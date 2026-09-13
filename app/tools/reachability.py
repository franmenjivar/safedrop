"""Deterministic reachability envelope.

The envelope is wind-distorted: ground speed differs by heading, so the set of
reachable points is an oval displaced downwind, not a circle. Candidates are
tested against the radius at their own bearing, which keeps the map overlay and
the reachability verdict consistent.
"""

from __future__ import annotations

from app.config import SafeDropConfig
from app.models.contingency import EnergyEstimate, ReachabilityResult
from app.models.landing_zone import LandingCandidate
from app.models.telemetry import OperationalState
from app.tools.energy import ground_speed_mps
from app.tools.geometry import bearing_deg, haversine_m, offset_latlon

ENVELOPE_SEGMENTS = 72


def reachable_radius_m(
    state: OperationalState, estimate: EnergyEstimate, heading_deg: float
) -> float:
    r""":math:`D_r(\psi) = V_g(\psi) \, T_r`"""
    vg = ground_speed_mps(
        state.airspeed_mps, state.wind_speed_mps, state.wind_direction_deg, heading_deg
    )
    return vg * estimate.remaining_flight_time_sec


def compute_reachability(
    state: OperationalState,
    estimate: EnergyEstimate,
    candidates: list[LandingCandidate],
    cfg: SafeDropConfig,
) -> ReachabilityResult:
    envelope: list[tuple[float, float]] = []
    radii: list[float] = []
    for i in range(ENVELOPE_SEGMENTS):
        heading = 360.0 * i / ENVELOPE_SEGMENTS
        r = reachable_radius_m(state, estimate, heading)
        radii.append(r)
        envelope.append(offset_latlon(state.latitude, state.longitude, heading, r))

    reachable: list[str] = []
    unreachable: list[str] = []
    for candidate in candidates:
        distance = haversine_m(
            state.latitude, state.longitude, candidate.latitude, candidate.longitude
        )
        heading = bearing_deg(
            state.latitude, state.longitude, candidate.latitude, candidate.longitude
        )
        if distance <= reachable_radius_m(state, estimate, heading):
            reachable.append(candidate.candidate_id)
        else:
            unreachable.append(candidate.candidate_id)

    nominal_heading = bearing_deg(
        state.latitude,
        state.longitude,
        state.latitude,
        state.longitude + 0.001,
    )
    return ReachabilityResult(
        max_reachable_distance_m=round(max(radii), 1),
        remaining_flight_time_sec=estimate.remaining_flight_time_sec,
        usable_energy_wh=estimate.usable_energy_wh,
        reserve_energy_wh=estimate.reserve_energy_wh,
        ground_speed_mps=round(
            ground_speed_mps(
                state.airspeed_mps,
                state.wind_speed_mps,
                state.wind_direction_deg,
                nominal_heading,
            ),
            2,
        ),
        envelope_polygon=envelope,
        reachable_candidate_ids=reachable,
        unreachable_candidate_ids=unreachable,
        status="OK" if reachable else "NO_REACHABLE_PREMAPPED_ELZ",
    )
