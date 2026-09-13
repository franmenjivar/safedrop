r"""Predicted UAV separation along a diversion route.

Both aircraft are propagated forward in time and sampled:

.. math::
    D(t) = \lVert P_d(t) - P_o(t) \rVert, \qquad
    D_{min} = \min_t D(t) \ \ge\ D_{required}

The agent is given ``D_min`` and the verdict. It never computes geometry.
"""

from __future__ import annotations

import math

import pandas as pd

from app.config import SafeDropConfig
from app.models.enums import ReasonCode
from app.models.route import Route, SeparationCheck
from app.models.telemetry import OperationalState
from app.services.scenario_loader import ScenarioData
from app.tools.energy import ground_speed_mps
from app.tools.geometry import LocalFrame, bearing_deg, haversine_m
from app.tools.freshness import scenario_freshness, is_usable

SAMPLE_STEP_SEC = 1.0


def _own_track(
    route: Route, state: OperationalState, horizon_sec: float
) -> list[tuple[float, float, float, float]]:
    """Sample the own-ship trajectory as ``(t, lat, lon, alt)``."""
    legs: list[tuple[tuple[float, float, float], tuple[float, float, float]]] = []
    for a, b in zip(route.waypoints, route.waypoints[1:]):
        legs.append(
            ((a.latitude, a.longitude, a.altitude_m), (b.latitude, b.longitude, b.altitude_m))
        )

    track: list[tuple[float, float, float, float]] = []
    t = state.timestamp_s
    for (lat1, lon1, alt1), (lat2, lon2, alt2) in legs:
        leg_m = haversine_m(lat1, lon1, lat2, lon2)
        if leg_m < 1.0:
            # Pure vertical leg: descend at a nominal 2.5 m/s.
            duration = abs(alt1 - alt2) / 2.5 if alt1 != alt2 else 0.0
        else:
            course = bearing_deg(lat1, lon1, lat2, lon2)
            vg = ground_speed_mps(
                state.airspeed_mps, state.wind_speed_mps, state.wind_direction_deg, course
            )
            duration = leg_m / vg
        steps = max(1, int(duration / SAMPLE_STEP_SEC))
        for i in range(steps + 1):
            f = i / steps
            track.append(
                (
                    t + duration * f,
                    lat1 + (lat2 - lat1) * f,
                    lon1 + (lon2 - lon1) * f,
                    alt1 + (alt2 - alt1) * f,
                )
            )
        t += duration
        if t - state.timestamp_s > horizon_sec:
            break
    return track


def _traffic_position(
    frames: pd.DataFrame, t: float
) -> tuple[float, float, float] | None:
    """Linearly interpolate a traffic vehicle's position at time ``t``."""
    times = frames["timestamp_s"].to_numpy()
    if t < times[0] or t > times[-1]:
        return None
    idx = int((times <= t).sum()) - 1
    if idx >= len(times) - 1:
        row = frames.iloc[-1]
        return float(row["latitude"]), float(row["longitude"]), float(row["altitude_m"])
    a, b = frames.iloc[idx], frames.iloc[idx + 1]
    span = float(b["timestamp_s"]) - float(a["timestamp_s"])
    f = 0.0 if span == 0 else (t - float(a["timestamp_s"])) / span
    return (
        float(a["latitude"]) + (float(b["latitude"]) - float(a["latitude"])) * f,
        float(a["longitude"]) + (float(b["longitude"]) - float(a["longitude"])) * f,
        float(a["altitude_m"]) + (float(b["altitude_m"]) - float(a["altitude_m"])) * f,
    )


def calculate_minimum_separation(
    route: Route,
    state: OperationalState,
    scenario: ScenarioData,
    cfg: SafeDropConfig,
) -> SeparationCheck:
    """Minimum predicted 3-D separation against all scenario UAV traffic."""
    required = cfg.safety.minimum_separation_m

    if scenario.traffic is None or scenario.traffic.empty:
        # No traffic service result is not the same as "no traffic".
        service_status = scenario.ground_state.get("traffic_service_status", "NO_TRAFFIC")
        if service_status == "UNAVAILABLE":
            return SeparationCheck(
                route_id=route.route_id,
                required_separation_m=required,
                safe=False,
                data_available=False,
                reason_codes=[ReasonCode.TRAFFIC_VERIFICATION_UNAVAILABLE],
            )
        return SeparationCheck(
            route_id=route.route_id,
            required_separation_m=required,
            minimum_separation_m=None,
            safe=True,
            data_available=True,
        )

    freshness = scenario_freshness(
        "scenario_traffic_feed",
        scenario.ground_state.get("traffic_data_age_sec", 0.0),
        cfg.safety.traffic_data_max_age_sec,
    )
    if not is_usable(freshness):
        return SeparationCheck(
            route_id=route.route_id,
            required_separation_m=required,
            safe=False,
            data_available=False,
            reason_codes=[ReasonCode.AIRSPACE_DATA_STALE],
        )

    track = _own_track(route, state, horizon_sec=route.estimated_time_sec + 30.0)
    frame = LocalFrame(state.latitude, state.longitude)

    best: float | None = None
    conflict_id: str | None = None
    for vehicle_id, frames in scenario.traffic.groupby("vehicle_id"):
        frames = frames.sort_values("timestamp_s").reset_index(drop=True)
        for t, lat, lon, alt in track:
            other = _traffic_position(frames, t)
            if other is None:
                continue
            ox, oy = frame.to_xy(other[0], other[1])
            sx, sy = frame.to_xy(lat, lon)
            d = math.sqrt((ox - sx) ** 2 + (oy - sy) ** 2 + (other[2] - alt) ** 2)
            if best is None or d < best:
                best, conflict_id = d, str(vehicle_id)

    safe = best is None or best >= required
    return SeparationCheck(
        route_id=route.route_id,
        conflicting_vehicle_id=None if safe else conflict_id,
        minimum_separation_m=None if best is None else round(best, 1),
        required_separation_m=required,
        safe=safe,
        data_available=True,
        reason_codes=[] if safe else [ReasonCode.AIRSPACE_SEPARATION_VIOLATION],
    )
