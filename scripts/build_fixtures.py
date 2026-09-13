"""Generate every SafeDrop scenario dataset under ``data/scenarios/``.

Run with ``python -m scripts.build_fixtures``. Regenerating is deterministic:
same real features, same geometry, same telemetry, same images.

**Every landing candidate is anchored on a real place.** Coordinates come from
``data/geo_cache/`` (built by ``scripts.fetch_real_geometry`` from
OpenStreetMap), and each candidate sits at that feature's *pole of
inaccessibility* — the point furthest from any boundary — with its measured
clearance radius carried through as evidence. Hand-placed coordinates do not
survive contact with a satellite basemap; a synthetic "recovery yard" ends up
on somebody's roof.

The aircraft's own position is the one thing this simulator invents: it is
where the emergency happens to occur, not a claim about the ground.

All sites are labelled as *simulated* preplanned recovery sites or
*non-certified* off-nominal candidates. Nothing here asserts that a real park
or car park is an officially designated emergency landing zone.
"""

from __future__ import annotations

import math
import shutil
from pathlib import Path

import yaml

from app.config import DATA_DIR
from app.tools.geometry import bearing_deg, haversine_m
from scripts import site_images as img
from scripts.fixture_lib import (
    at,
    collection,
    feature,
    find,
    line,
    open_point,
    place_drone,
    rect,
    write_json,
    write_telemetry,
    write_traffic,
)

SCENARIOS_DIR = DATA_DIR / "scenarios"

PREPLANNED = {
    "tier": "PREPLANNED_SIMULATED_ELZ",
    "certification_status": "SIMULATED",
}


def real_elz(candidate_id: str, region: str, name: str, feature_type: str | None = None,
             image: str | None = None, obstacles: list[str] | None = None,
             label: str | None = None) -> dict:
    """A preplanned simulated recovery site anchored on a real open space."""
    real = find(region, name, feature_type)
    props = real["properties"]
    return feature(
        real["geometry"],
        candidate_id=candidate_id,
        name=label or f"{props['name']} (simulated recovery site)",
        feature_type=props["feature_type"],
        surface="grass" if props["feature_type"] in ("park", "recreation_ground") else "asphalt",
        static_obstacles=obstacles or [],
        image=image,
        area_m2=props["area_m2"],
        # Anchor on verified open ground, not the polygon centroid — and not
        # the raw inscribed-circle point either, which may sit on a structure
        # the boundary happens to enclose.
        anchor_lat=props["verified_lat"],
        anchor_lon=props["verified_lon"],
        open_radius_m=props["open_radius_m"],
        nearest_feature=props["nearest_feature"],
        anchor_clearance_score_m=props["anchor_clearance_score_m"],
        osm_id=props["osm_id"],
        osm_type=props["osm_type"],
        source=f"OpenStreetMap {props['osm_type']}/{props['osm_id']} (ODbL)",
        **PREPLANNED,
    )


def real_map_feature(region: str, name: str, feature_type: str | None = None,
                     candidate_id: str | None = None, image: str | None = None) -> dict:
    """A real feature offered to the off-nominal candidate generator."""
    real = find(region, name, feature_type)
    props = real["properties"]
    return feature(
        real["geometry"],
        feature_type=props["feature_type"],
        name=props["name"],
        candidate_id=candidate_id,
        image=image,
        area_m2=props["area_m2"],
        anchor_lat=props["verified_lat"],
        anchor_lon=props["verified_lon"],
        open_radius_m=props["open_radius_m"],
        nearest_feature=props["nearest_feature"],
        anchor_clearance_score_m=props["anchor_clearance_score_m"],
        surface="grass" if props["feature_type"] in ("park", "recreation_ground") else "asphalt",
        osm_id=props["osm_id"],
        source=f"OpenStreetMap {props['osm_type']}/{props['osm_id']} (ODbL)",
    )


def nearby_sensitive(region: str, centre: tuple[float, float], radius_m: float = 1600.0) -> list[dict]:
    """Real schools, hospitals, and playgrounds near a scenario's operating area."""
    from scripts.fixture_lib import load_region

    out = []
    for f in load_region(region):
        props = f["properties"]
        if props["feature_type"] not in ("school", "hospital", "playground"):
            continue
        if haversine_m(*centre, props["open_lat"], props["open_lon"]) > radius_m:
            continue
        out.append(
            feature(
                f["geometry"],
                feature_type=props["feature_type"],
                name=props["name"],
                source=f"OpenStreetMap {props['osm_type']}/{props['osm_id']} (ODbL)",
            )
        )
    return out


def write_scenario(directory: Path, manifest: dict, **fixtures) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "scenario.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False))
    for filename, payload in fixtures.items():
        if payload is not None:
            write_json(directory / filename, payload)


def ground(candidate_id, **kwargs) -> tuple[str, dict]:
    return candidate_id, {"data_age_sec": 8, **kwargs}


def fresh_images(directory: Path) -> Path:
    images = directory / "images"
    if images.exists():
        shutil.rmtree(images)
    return images


def report(name: str, drone, sites: list[tuple[str, tuple[float, float]]]) -> None:
    print(f"  {name}: drone {drone[0]:.5f},{drone[1]:.5f}")
    for label, point in sites:
        print(
            f"      {label:16s} {haversine_m(*drone, *point):6.0f} m "
            f"@ {bearing_deg(*drone, *point):3.0f}deg   {point[0]:.5f},{point[1]:.5f}"
        )


# ==========================================================================
# SCENARIO 01 — the correct answer is CONTINUE
# ==========================================================================


def scenario_01() -> None:
    d = SCENARIOS_DIR / "scenario_01"
    region = "north_austin"
    elz_a = real_elz("AUS-ELZ-001", region, "Domain Central Park", "park")
    elz_b = real_elz("AUS-ELZ-002", region, "Balcones District Park", "park")
    sites = [open_point(f) for f in (elz_a, elz_b)]

    drone = place_drone(sites, bearing_deg=250, standoff_m=900)
    base = at(drone, 240, 1500)
    destination = at(drone, 60, 1200)
    report("SCENARIO-01", drone, [("Quarries", sites[0]), ("Balcones", sites[1])])

    write_telemetry(
        d / "telemetry.csv",
        end_point=drone, heading_deg=60, seconds=120, airspeed_mps=13.0, altitude_m=88,
        soc_end=44.0, soc_drop_pct_min=0.9, temp_end=52.0, temp_rise_c_min=1.4,
        motor_end=0.95, motor_drift=0.004, payload_kg=2.0,
        wind_speed_mps=4.0, wind_direction_deg=200, link_end=0.97, link_drift=0.0, seed=101,
    )
    write_scenario(
        d,
        {
            "id": "SCENARIO-01",
            "name": "Battery pack warming, mission energy ample",
            "description": (
                "Pack temperature is trending up and triggers an advisory, but state of charge "
                "and wind leave ample margin to the delivery destination. The correct action is "
                "to continue; a system that forces a landing here has over-reacted."
            ),
            "region": "North Austin, Texas",
            "context_mode": "offline",
            "decision_step": -1,
            "mission": {
                "base": {"name": "Domain Fulfilment Hub", "latitude": base[0], "longitude": base[1]},
                "destination": {"name": "Kramer Lane Customer", "latitude": destination[0], "longitude": destination[1]},
            },
            "files": {
                "telemetry_csv": "telemetry.csv",
                "known_elzs_geojson": "known_elzs.geojson",
                "ground_state_json": "ground_state.json",
                "ground_truth_json": "ground_truth.json",
            },
        },
        **{
            "known_elzs.geojson": collection([elz_a, elz_b]),
            "ground_state.json": {"default_age_sec": 8, "candidates": {}},
            "ground_truth.json": {
                "scenario_id": "SCENARIO-01",
                "expected_action": "CONTINUE",
                "expected_reason_codes": ["MISSION_ENERGY_SUFFICIENT"],
                "notes": "Tests that a thermal advisory alone does not trigger a forced landing.",
            },
        },
    )


# ==========================================================================
# SCENARIO 02 — RETURN_TO_BASE
# ==========================================================================


def scenario_02() -> None:
    d = SCENARIOS_DIR / "scenario_02"
    region = "pflugerville"
    elz_a = real_elz("AUS-ELZ-003", region, "Creekside Park", "park")
    elz_b = real_elz("AUS-ELZ-004", region, "Bohls Park", "park")
    sites = [open_point(f) for f in (elz_a, elz_b)]

    drone = place_drone(sites, bearing_deg=200, standoff_m=800)
    base = at(drone, 225, 1400)
    destination = at(drone, 45, 5200)
    report("SCENARIO-02", drone, [("Creekside", sites[0]), ("Bohls", sites[1])])

    write_telemetry(
        d / "telemetry.csv",
        end_point=drone, heading_deg=45, seconds=120, airspeed_mps=13.0, altitude_m=92,
        soc_end=16.0, soc_drop_pct_min=2.2, temp_end=47.0, temp_rise_c_min=0.8,
        motor_end=0.90, motor_drift=0.010, payload_kg=2.2,
        wind_speed_mps=6.0, wind_direction_deg=45, link_end=0.93, link_drift=0.0, seed=202,
    )
    write_scenario(
        d,
        {
            "id": "SCENARIO-02",
            "name": "State of charge insufficient for delivery, base upwind and close",
            "description": (
                "Rapid state-of-charge loss with a headwind toward the destination. The delivery "
                "cannot be completed, but the launch base is close and downwind. The correct action "
                "is a return to base, not a forced landing."
            ),
            "region": "Pflugerville, Texas",
            "context_mode": "offline",
            "decision_step": -1,
            "mission": {
                "base": {"name": "Pflugerville Depot", "latitude": base[0], "longitude": base[1]},
                "destination": {"name": "Hutto Medical Clinic", "latitude": destination[0], "longitude": destination[1]},
            },
            "files": {
                "telemetry_csv": "telemetry.csv",
                "known_elzs_geojson": "known_elzs.geojson",
                "ground_state_json": "ground_state.json",
                "ground_truth_json": "ground_truth.json",
            },
        },
        **{
            "known_elzs.geojson": collection([elz_a, elz_b]),
            "ground_state.json": {"default_age_sec": 8, "candidates": {}},
            "ground_truth.json": {
                "scenario_id": "SCENARIO-02",
                "expected_action": "RETURN_TO_BASE",
                "expected_reason_codes": ["INSUFFICIENT_MISSION_ENERGY", "RTB_ENERGY_SUFFICIENT"],
                "notes": "Tests that a feasible return is preferred over a forced landing.",
            },
        },
    )


# ==========================================================================
# SCENARIO 04 — forced landing; nearest site under vehicle occupancy
# ==========================================================================


def scenario_04() -> None:
    d = SCENARIOS_DIR / "scenario_04"
    images = fresh_images(d)
    region = "round_rock"

    lot = real_elz("AUS-ELZ-011", region, "Kalahari Boulevard", "parking_lot",
                   image="elz_011_current.png", obstacles=["light_poles"])
    park = real_elz("AUS-ELZ-012", region, "Chandler Creek Park", "park",
                    image="elz_012_current.png")
    far = real_elz("AUS-ELZ-013", region, "Jester Farms Park", "park")

    lot_p, park_p, far_p = (open_point(f) for f in (lot, park, far))
    drone = place_drone([lot_p, park_p], bearing_deg=150, standoff_m=250)
    base = at(drone, 200, 4200)
    destination = at(drone, 20, 4800)
    report("SCENARIO-04", drone, [("Kalahari lot", lot_p), ("Chandler Ck", park_p), ("Jester Farms", far_p)])

    truth = {
        "elz_011_current.png": img.parking_lot(images / "elz_011_current.png", vehicles=4, seed=41),
        "elz_012_current.png": img.sports_field(images / "elz_012_current.png", people=0, seed=42),
    }
    write_json(images / "image_ground_truth.json", truth)

    write_telemetry(
        d / "telemetry.csv",
        end_point=drone, heading_deg=20, seconds=120, airspeed_mps=13.0, altitude_m=78,
        soc_end=17.5, soc_drop_pct_min=3.4, temp_end=58.0, temp_rise_c_min=2.2,
        motor_end=0.86, motor_drift=0.020, payload_kg=2.0,
        wind_speed_mps=5.0, wind_direction_deg=200, link_end=0.91, link_drift=0.0, seed=404,
    )
    write_scenario(
        d,
        {
            "id": "SCENARIO-04",
            "name": "Forced landing with the nearest site under vehicle occupancy",
            "description": (
                "A forced landing is unavoidable. The nearest preplanned site is a car park that "
                "is currently occupied by vehicles; a park a similar distance away is clear. "
                "Distance alone would pick the wrong site."
            ),
            "region": "Round Rock, Texas",
            "context_mode": "offline",
            "decision_step": -1,
            "mission": {
                "base": {"name": "Round Rock Depot", "latitude": base[0], "longitude": base[1]},
                "destination": {"name": "Teravista Customer", "latitude": destination[0], "longitude": destination[1]},
            },
            "files": {
                "telemetry_csv": "telemetry.csv",
                "known_elzs_geojson": "known_elzs.geojson",
                "ground_state_json": "ground_state.json",
                "image_directory": "images",
                "ground_truth_json": "ground_truth.json",
            },
        },
        **{
            "known_elzs.geojson": collection([lot, park, far]),
            "ground_state.json": {
                "default_age_sec": 8,
                "candidates": dict([
                    ground("AUS-ELZ-011", pedestrian_count=0, vehicle_count=4, animal_count=0,
                           construction_active=False, clear_area_percent=62, surface_condition="dry"),
                    ground("AUS-ELZ-012", pedestrian_count=0, vehicle_count=0, animal_count=0,
                           construction_active=False, clear_area_percent=94, surface_condition="dry"),
                ]),
            },
            "ground_truth.json": {
                "scenario_id": "SCENARIO-04",
                "expected_action": "LAND_IMMEDIATELY",
                "expected_candidate_id": "AUS-ELZ-012",
                "unsafe_candidates": {"AUS-ELZ-011": "VEHICLE_OCCUPANCY"},
                "notes": "Tests dynamic ground re-evaluation of a preplanned site.",
            },
        },
    )


# ==========================================================================
# SCENARIO 09 — ground hazards, a traffic conflict, and a replan
# ==========================================================================


def scenario_09() -> None:
    d = SCENARIOS_DIR / "scenario_09"
    images = fresh_images(d)
    region = "north_austin"

    elz_a = real_elz("ELZ-A", region, "Lot F - Patient", "parking_lot",
                     image="elz_a_current.png", obstacles=["light_poles"])
    elz_b = real_elz("ELZ-B", region, "Alderbrook Pocket Park", "park",
                     image="elz_b_current.png", obstacles=["trees"])
    elz_c = real_elz("ELZ-C", region, "Walnut Creek Greenbelt", "park",
                     image="elz_c_current.png")

    a_p, b_p, c_p = (open_point(f) for f in (elz_a, elz_b, elz_c))
    # Stand off north-east so the sites lie at increasing range on a similar
    # bearing — which is what makes one traffic conflict meaningful.
    drone = place_drone([a_p, b_p], bearing_deg=35, standoff_m=700)
    base = at(drone, 300, 3600)
    destination = at(drone, 120, 3100)
    hub = at(drone, 150, 3300)
    report("SCENARIO-09", drone, [("ELZ-A lot", a_p), ("ELZ-B park", b_p), ("ELZ-C greenbelt", c_p)])

    seconds = 120
    write_telemetry(
        d / "telemetry.csv",
        end_point=drone, heading_deg=215, seconds=seconds, airspeed_mps=13.0, altitude_m=75,
        soc_end=15.5, soc_drop_pct_min=4.8, temp_end=61.0, temp_rise_c_min=3.0,
        motor_end=0.71, motor_drift=0.035, payload_kg=2.5,
        wind_speed_mps=8.0, wind_direction_deg=35, link_end=0.85, link_drift=0.0, seed=909,
    )

    truth = {
        "elz_a_current.png": img.parking_lot(images / "elz_a_current.png", vehicles=3, seed=91),
        "elz_b_current.png": img.urban_park(images / "elz_b_current.png", people=8, seed=92),
        "elz_c_current.png": img.industrial_yard(images / "elz_c_current.png", seed=93, clear=True),
    }
    write_json(images / "image_ground_truth.json", truth)

    # DRONE-207 is timed to cross the direct route to ELZ-C at close range, so
    # the first route fails on separation and the agent must ask for another.
    # Time the conflict from the geometry the aircraft will actually fly: own
    # ground speed on the course to ELZ-C, half way along the direct leg.
    decision_t = seconds - 1
    course_to_c = bearing_deg(*drone, *c_p)
    range_to_c = haversine_m(*drone, *c_p)
    own_ground_speed = max(2.0, 13.0 - 8.0 * math.cos(math.radians(course_to_c - 35.0)))
    conflict_t = decision_t + 0.5 * range_to_c / own_ground_speed
    conflict_point = at(drone, course_to_c, range_to_c * 0.5)
    own_altitude_at_conflict = 75.0 + (0.35 * 75.0 - 75.0) * 0.5

    lead_s = 40.0
    intruder_speed = 12.0
    traffic = [
        {
            "vehicle_id": "DRONE-207",
            "start": at(conflict_point, (course_to_c + 270) % 360, intruder_speed * lead_s),
            "heading_deg": (course_to_c + 90) % 360,
            "speed_mps": intruder_speed,
            # Offset vertically so the closest approach lands just inside the
            # 30 m minimum rather than being a direct hit.
            "altitude_m": round(own_altitude_at_conflict - 11.0, 1),
            "t0": round(conflict_t - lead_s),
            "t1": round(conflict_t + 90),
        }
    ]
    write_traffic(d / "traffic.csv", traffic)

    obstacles = collection([
        feature(rect(*at(drone, 60, 700), 55, 40), id="OBS-901", name="Distribution warehouse", height_m=14),
        feature(rect(*at(drone, 180, 600), 30, 30), id="OBS-902", name="Water tower compound", height_m=38),
    ])
    restrictions = collection([
        feature(rect(*at(drone, 20, 2200), 400, 400), id="TFR-901", name="Temporary event restriction",
                type="TEMPORARY_FLIGHT_RESTRICTION", active=True),
    ])

    write_scenario(
        d,
        {
            "id": "SCENARIO-09",
            "name": "Battery and motor degradation with ground hazards and a traffic conflict",
            "description": (
                "The primary demonstration. State of charge is critical and the motor is degraded; "
                "neither the destination, the base, nor the alternate hub is reachable. Two of the "
                "three reachable recovery sites are occupied on the ground, and the direct route to "
                "the third violates minimum separation against DRONE-207, forcing a replan."
            ),
            "region": "North Austin, Texas",
            "context_mode": "offline",
            "decision_step": -1,
            "mission": {
                "base": {"name": "North Austin Depot", "latitude": base[0], "longitude": base[1]},
                "destination": {"name": "Pflugerville Customer", "latitude": destination[0], "longitude": destination[1]},
                "alternate_hub": {"name": "Dessau Alternate Hub", "latitude": hub[0], "longitude": hub[1]},
            },
            "files": {
                "telemetry_csv": "telemetry.csv",
                "known_elzs_geojson": "known_elzs.geojson",
                "obstacles_geojson": "obstacles.geojson",
                "restrictions_geojson": "restrictions.geojson",
                "traffic_csv": "traffic.csv",
                "ground_state_json": "ground_state.json",
                "image_directory": "images",
                "ground_truth_json": "ground_truth.json",
            },
        },
        **{
            "known_elzs.geojson": collection([elz_a, elz_b, elz_c]),
            "obstacles.geojson": obstacles,
            "restrictions.geojson": restrictions,
            "ground_state.json": {
                "default_age_sec": 8,
                "traffic_data_age_sec": 4,
                "candidates": dict([
                    ground("ELZ-A", pedestrian_count=0, vehicle_count=3, animal_count=0,
                           construction_active=False, clear_area_percent=74, surface_condition="dry"),
                    ground("ELZ-B", pedestrian_count=8, vehicle_count=0, animal_count=0,
                           construction_active=False, clear_area_percent=46, surface_condition="dry"),
                    ground("ELZ-C", pedestrian_count=0, vehicle_count=0, animal_count=0,
                           construction_active=False, clear_area_percent=93, surface_condition="dry"),
                ]),
            },
            "ground_truth.json": {
                "scenario_id": "SCENARIO-09",
                "expected_action": "LAND_IMMEDIATELY",
                "expected_candidate_id": "ELZ-C",
                "unsafe_candidates": {"ELZ-A": "VEHICLE_OCCUPANCY", "ELZ-B": "PEDESTRIAN_OCCUPANCY"},
                "expects_replan": True,
                "notes": "Direct route to ELZ-C must fail on UAV separation and be replanned.",
            },
        },
    )


# ==========================================================================
# SCENARIO 16 — no preplanned site reachable; off-nominal generation
# ==========================================================================


def scenario_16() -> None:
    d = SCENARIOS_DIR / "scenario_16"
    images = fresh_images(d)
    region = "pflugerville"

    # Preplanned sites, deliberately placed out of reach.
    far_a = real_elz("AUS-ELZ-031", region, "Town Center Drive", "parking_lot")
    far_b = real_elz("AUS-ELZ-032", region, "Wells Point Sports Park", "recreation_ground")

    winner = real_map_feature(region, "Swenson Park", "park",
                              candidate_id="AUTO-PARK-SWENSON", image="auto_field_01.png")
    # Both of these sit inside the playground buffer of a real, mapped
    # playground, so they are excluded before any agent is involved.
    near_playground_a = real_map_feature(region, "Black Locust Park", "park",
                                         candidate_id="AUTO-PARK-BLACKLOCUST")
    near_playground_b = real_map_feature(region, "Heatherwilde", "park",
                                         candidate_id="AUTO-PARK-HEATHERWILDE")

    w, a, b = (open_point(f) for f in (winner, near_playground_a, near_playground_b))
    drone = place_drone([w, a, b], bearing_deg=200, standoff_m=120)
    base = at(drone, 20, 4200)
    destination = at(drone, 200, 4600)
    report("SCENARIO-16", drone, [
        ("Swenson", w), ("Black Locust", a), ("Heatherwilde", b),
        ("Town Center (far)", open_point(far_a)), ("Wells Point (far)", open_point(far_b)),
    ])

    truth = {
        "auto_field_01.png": img.sports_field(images / "auto_field_01.png", people=0, seed=62),
    }
    write_json(images / "image_ground_truth.json", truth)

    write_telemetry(
        d / "telemetry.csv",
        end_point=drone, heading_deg=200, seconds=120, airspeed_mps=13.0, altitude_m=70,
        soc_end=15.0, soc_drop_pct_min=5.6, temp_end=64.0, temp_rise_c_min=3.4,
        motor_end=0.78, motor_drift=0.028, payload_kg=1.8,
        wind_speed_mps=5.0, wind_direction_deg=110, link_end=0.88, link_drift=0.0, seed=1616,
    )

    features = [winner, near_playground_a, near_playground_b, *nearby_sensitive(region, w, 1800.0)]

    write_scenario(
        d,
        {
            "id": "SCENARIO-16",
            "name": "No preplanned recovery site is reachable",
            "description": (
                "Severe battery degradation leaves every preplanned simulated recovery site outside "
                "the reachable envelope. SafeDrop generates off-nominal candidates from real "
                "geospatial context. Two of the three sit inside the buffer of a real mapped "
                "playground and are excluded deterministically, before any agent sees them. What "
                "survives is verified against current imagery and offered as NON-CERTIFIED."
            ),
            "region": "Pflugerville, Texas",
            "context_mode": "offline",
            "decision_step": -1,
            "mission": {
                "base": {"name": "Pflugerville Depot", "latitude": base[0], "longitude": base[1]},
                "destination": {"name": "Wells Branch Customer", "latitude": destination[0], "longitude": destination[1]},
            },
            "files": {
                "telemetry_csv": "telemetry.csv",
                "known_elzs_geojson": "known_elzs.geojson",
                "map_features_geojson": "map_features.geojson",
                "ground_state_json": "ground_state.json",
                "image_directory": "images",
                "ground_truth_json": "ground_truth.json",
            },
        },
        **{
            "known_elzs.geojson": collection([far_a, far_b]),
            "map_features.geojson": collection(features),
            # Off-nominal parks are last-resort tier, so a clear image alone
            # cannot certify them — the ground feed has to agree.
            "ground_state.json": {
                "default_age_sec": 6,
                "candidates": dict([
                    ground("AUTO-PARK-SWENSON", pedestrian_count=0, vehicle_count=0, animal_count=0,
                           construction_active=False, clear_area_percent=92, surface_condition="dry"),
                ]),
            },
            "ground_truth.json": {
                "scenario_id": "SCENARIO-16",
                "expected_action": "LAND_IMMEDIATELY",
                "expected_candidate_id": "AUTO-PARK-SWENSON",
                "unsafe_candidates": {
                    "AUTO-PARK-BLACKLOCUST": "PLAYGROUND_PROXIMITY",
                    "AUTO-PARK-HEATHERWILDE": "PLAYGROUND_PROXIMITY",
                },
                "notes": "Tests off-nominal generation and real deterministic exclusion buffers.",
            },
        },
    )


# ==========================================================================
# SCENARIO 17 — the largest, flattest site is the one full of people
# ==========================================================================


def scenario_17() -> None:
    d = SCENARIOS_DIR / "scenario_17"
    images = fresh_images(d)
    region = "central_austin"

    # The most-interior point of the Norwood Tract polygon lands inside Norwood
    # Estate Dog Park — an off-leash area. Large, flat, fenced, and full of
    # loose animals. This is precisely the hazard a map cannot warn you about.
    big_park = real_elz("AUS-ELZ-021", region, "Norwood Tract", "park",
                        image="elz_021_current.png", obstacles=["trees", "perimeter_fence"],
                        label="Norwood Tract / Norwood Estate Dog Park (simulated recovery site)")
    winner = real_elz("AUS-ELZ-022", region, "Little Stacy", "park",
                      image="elz_022_current.png")
    lot = real_elz("AUS-ELZ-023", region, "HACA Parking", "parking_lot",
                   image="elz_023_current.png", obstacles=["light_poles"])

    big_p, win_p, lot_p = (open_point(f) for f in (big_park, winner, lot))
    drone = place_drone([big_p, lot_p], bearing_deg=70, standoff_m=520)
    base = at(drone, 70, 4100)
    destination = at(drone, 250, 3800)
    report("SCENARIO-17", drone, [("Norwood park", big_p), ("Little Stacy", win_p), ("HACA lot", lot_p)])

    truth = {
        "elz_021_current.png": img.dog_park(images / "elz_021_current.png", people=4, dogs=6, seed=71),
        "elz_022_current.png": img.sports_field(images / "elz_022_current.png", people=0, seed=72, goals=False),
        "elz_023_current.png": img.parking_lot(images / "elz_023_current.png", vehicles=6, seed=73),
    }
    write_json(images / "image_ground_truth.json", truth)

    write_telemetry(
        d / "telemetry.csv",
        end_point=drone, heading_deg=250, seconds=120, airspeed_mps=13.0, altitude_m=82,
        soc_end=12.0, soc_drop_pct_min=4.1, temp_end=56.0, temp_rise_c_min=2.0,
        motor_end=0.85, motor_drift=0.018, payload_kg=2.2,
        wind_speed_mps=6.0, wind_direction_deg=70, link_end=0.94, link_drift=0.0, seed=1717,
    )
    obstacles = collection([
        feature(rect(*at(drone, 280, 500), 40, 34), id="OBS-1701", name="Riverside office block", height_m=22),
    ])
    write_scenario(
        d,
        {
            "id": "SCENARIO-17",
            "name": "The largest, flattest site is an off-leash dog park",
            "description": (
                "A forced landing over central Austin. The best site by geometry is a large open "
                "lawn beside Lady Bird Lake — which is in fact an off-leash dog park, currently "
                "holding four people and six loose animals. The nearest car park is full. The "
                "verified answer is a smaller neighbourhood park further away. Map geometry is "
                "not ground suitability, and a polygon labelled 'park' can be the worst option "
                "on the board."
            ),
            "region": "Central Austin, Texas",
            "context_mode": "offline",
            "decision_step": -1,
            "mission": {
                "base": {"name": "East Austin Depot", "latitude": base[0], "longitude": base[1]},
                "destination": {"name": "Barton Hills Customer", "latitude": destination[0], "longitude": destination[1]},
            },
            "files": {
                "telemetry_csv": "telemetry.csv",
                "known_elzs_geojson": "known_elzs.geojson",
                "obstacles_geojson": "obstacles.geojson",
                "ground_state_json": "ground_state.json",
                "image_directory": "images",
                "ground_truth_json": "ground_truth.json",
            },
        },
        **{
            "known_elzs.geojson": collection([big_park, winner, lot]),
            "obstacles.geojson": obstacles,
            "ground_state.json": {
                "default_age_sec": 9,
                "candidates": dict([
                    ground("AUS-ELZ-021", pedestrian_count=4, vehicle_count=0, animal_count=6,
                           construction_active=False, clear_area_percent=55, surface_condition="dry"),
                    ground("AUS-ELZ-022", pedestrian_count=0, vehicle_count=0, animal_count=0,
                           construction_active=False, clear_area_percent=93, surface_condition="dry"),
                    ground("AUS-ELZ-023", pedestrian_count=0, vehicle_count=6, animal_count=0,
                           construction_active=False, clear_area_percent=48, surface_condition="dry"),
                ]),
            },
            "ground_truth.json": {
                "scenario_id": "SCENARIO-17",
                "expected_action": "LAND_IMMEDIATELY",
                "expected_candidate_id": "AUS-ELZ-022",
                "unsafe_candidates": {
                    "AUS-ELZ-021": "ANIMAL_EXPOSURE",
                    "AUS-ELZ-023": "VEHICLE_OCCUPANCY",
                },
                "notes": (
                    "Tests that open geometry is never treated as current safety. The largest, "
                    "flattest reachable site is a real off-leash dog park."
                ),
            },
        },
    )


BUILDERS = [scenario_01, scenario_02, scenario_04, scenario_09, scenario_16, scenario_17]


def main() -> None:
    SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)
    for builder in BUILDERS:
        builder()
    print("\nAll six scenarios rebuilt on real OpenStreetMap geometry.")


if __name__ == "__main__":
    main()
