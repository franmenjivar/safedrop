"""Deterministic diversion-route planning and route-level safety checks.

The planner is intentionally simple: a direct leg, then a family of
perpendicular-offset detours. Agent 3 does not build routes — it asks for
variant *n* and reads the verdict. That is what makes "request a replan" a
meaningful agent decision rather than an LLM drawing waypoints.
"""

from __future__ import annotations

from shapely.geometry import Point

from app.config import SafeDropConfig
from app.models.contingency import EnergyEstimate
from app.models.landing_zone import LandingCandidate
from app.models.route import (
    DescentCorridorCheck,
    ObstacleCheck,
    RestrictionCheck,
    Route,
    RouteEnergyCheck,
    Waypoint,
)
from app.models.telemetry import OperationalState
from app.services.scenario_loader import ScenarioData
from app.tools.energy import energy_for_flight_time_wh, ground_speed_mps
from app.tools.geometry import (
    LocalFrame,
    bearing_deg,
    haversine_m,
    offset_latlon,
)

#: Lateral offsets (metres) applied to the midpoint for each route variant.
DETOUR_OFFSETS_M = [0.0, 220.0, -220.0, 420.0, -420.0, 650.0, -650.0]
MAX_VARIANTS = len(DETOUR_OFFSETS_M)


def plan_route(
    state: OperationalState,
    candidate: LandingCandidate,
    variant: int = 1,
) -> Route:
    """Plan route variant ``variant`` (1 = direct) to a candidate."""
    variant = max(1, min(variant, MAX_VARIANTS))
    offset_m = DETOUR_OFFSETS_M[variant - 1]

    start = (state.latitude, state.longitude)
    end = (candidate.latitude, candidate.longitude)
    course = bearing_deg(*start, *end)
    direct_m = haversine_m(*start, *end)

    waypoints = [Waypoint(latitude=start[0], longitude=start[1], altitude_m=state.altitude_m)]
    distance_m = direct_m
    strategy = "direct"

    if offset_m != 0.0:
        mid = (
            (start[0] + end[0]) / 2.0,
            (start[1] + end[1]) / 2.0,
        )
        detour = offset_latlon(mid[0], mid[1], (course + 90.0) % 360.0, offset_m)
        waypoints.append(
            Waypoint(latitude=detour[0], longitude=detour[1], altitude_m=state.altitude_m * 0.8)
        )
        distance_m = haversine_m(*start, *detour) + haversine_m(*detour, *end)
        strategy = f"lateral_offset_{offset_m:+.0f}m"

    waypoints.append(
        Waypoint(latitude=end[0], longitude=end[1], altitude_m=state.altitude_m * 0.35)
    )
    waypoints.append(Waypoint(latitude=end[0], longitude=end[1], altitude_m=0.0))

    ground_speed = ground_speed_mps(
        state.airspeed_mps, state.wind_speed_mps, state.wind_direction_deg, course
    )
    descent_sec = state.altitude_m / 2.5  # nominal 2.5 m/s descent
    cruise_sec = distance_m / ground_speed
    return Route(
        route_id=f"ROUTE-{candidate.candidate_id}-{variant}",
        candidate_id=candidate.candidate_id,
        variant=variant,
        distance_m=round(distance_m, 1),
        estimated_time_sec=round(cruise_sec + descent_sec, 1),
        cruise_time_sec=round(cruise_sec, 1),
        waypoints=waypoints,
        strategy=strategy,
    )


def route_latlon_path(route: Route) -> list[tuple[float, float]]:
    """Deduplicated horizontal path of a route."""
    path: list[tuple[float, float]] = []
    for wp in route.waypoints:
        point = (wp.latitude, wp.longitude)
        if not path or path[-1] != point:
            path.append(point)
    return path


def calculate_route_energy(
    route: Route,
    state: OperationalState,
    estimate: EnergyEstimate,
    cfg: SafeDropConfig,
) -> RouteEnergyCheck:
    r"""Pass condition: :math:`E_{route} + E_{reserve} \le E_{remaining}`.

    Only the horizontal transit is charged here — the touchdown hover budget is
    already held inside ``reserve_energy_wh``, so charging it twice would make
    every route look infeasible.
    """
    route_energy = energy_for_flight_time_wh(estimate.estimated_power_w, route.cruise_time_sec)
    margin = estimate.remaining_energy_wh - route_energy - estimate.reserve_energy_wh
    return RouteEnergyCheck(
        route_id=route.route_id,
        remaining_energy_wh=estimate.remaining_energy_wh,
        route_energy_wh=round(route_energy, 2),
        reserve_energy_wh=estimate.reserve_energy_wh,
        energy_margin_wh=round(margin, 2),
        safe=margin >= 0.0,
    )


def check_route_obstacles(
    route: Route, scenario: ScenarioData, cfg: SafeDropConfig
) -> ObstacleCheck:
    """Intersect the horizontal route with buffered obstacle footprints."""
    path = route_latlon_path(route)
    frame = LocalFrame(*path[0])
    line = frame.line(path)
    buffer_m = cfg.candidate_generation.building_buffer_m

    hits: list[str] = []
    clearance: float | None = None
    for feature in scenario.obstacles:
        props = feature.get("properties", {})
        name = props.get("name") or props.get("id") or "obstacle"
        try:
            geom = frame.geometry(feature["geometry"])
        except Exception:
            continue
        d = float(line.distance(geom))
        clearance = d if clearance is None else min(clearance, d)
        if d < buffer_m:
            hits.append(str(name))
    return ObstacleCheck(
        route_id=route.route_id,
        intersecting_obstacles=hits,
        minimum_clearance_m=None if clearance is None else round(clearance, 1),
        safe=not hits,
    )


def check_restricted_airspace(route: Route, scenario: ScenarioData) -> RestrictionCheck:
    """Intersect the horizontal route with active restriction polygons."""
    path = route_latlon_path(route)
    frame = LocalFrame(*path[0])
    line = frame.line(path)

    hits: list[str] = []
    for feature in scenario.restrictions:
        props = feature.get("properties", {})
        if not props.get("active", True):
            continue
        name = props.get("name") or props.get("id") or "restricted_area"
        try:
            geom = frame.geometry(feature["geometry"])
        except Exception:
            continue
        if line.intersects(geom):
            hits.append(str(name))
    return RestrictionCheck(
        route_id=route.route_id, intersecting_restrictions=hits, safe=not hits
    )


def validate_descent_corridor(
    candidate: LandingCandidate, scenario: ScenarioData, cfg: SafeDropConfig
) -> DescentCorridorCheck:
    """A vertical cylinder over the touchdown point must be clear of structures."""
    radius = cfg.safety.descent_corridor_radius_m
    frame = LocalFrame(candidate.latitude, candidate.longitude)
    cylinder = Point(0.0, 0.0).buffer(radius)

    obstructions: list[str] = []
    for feature in scenario.obstacles:
        props = feature.get("properties", {})
        name = props.get("name") or props.get("id") or "obstacle"
        try:
            geom = frame.geometry(feature["geometry"])
        except Exception:
            continue
        if cylinder.intersects(geom):
            obstructions.append(str(name))
    return DescentCorridorCheck(
        candidate_id=candidate.candidate_id,
        corridor_radius_m=radius,
        obstructions=obstructions,
        safe=not obstructions,
    )
