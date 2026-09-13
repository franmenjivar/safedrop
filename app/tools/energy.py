"""Deterministic energy and feasibility engine.

This module is the reason SafeDrop's agents are allowed to be agents: every
number an agent quotes about range, endurance, or feasibility is produced
here, not by the language model.
"""

from __future__ import annotations

import math

from app.config import EnergyConfig, SafeDropConfig
from app.models.contingency import EnergyEstimate, FeasibilityCheck
from app.models.enums import ReasonCode
from app.models.telemetry import OperationalState
from app.tools.geometry import bearing_deg, haversine_m

MIN_GROUND_SPEED_MPS = 2.0


def estimated_power_w(
    payload_kg: float, motor_health: float, energy_cfg: EnergyConfig
) -> float:
    """Cruise power adjusted for payload mass and motor degradation."""
    health = min(max(motor_health, 0.0), 1.0)
    degradation = 1.0 + (1.0 - health) * (energy_cfg.motor_degradation_power_factor - 1.0)
    return energy_cfg.cruise_power_w * degradation + energy_cfg.payload_power_w_per_kg * payload_kg


def reserve_energy_wh(cfg: SafeDropConfig) -> float:
    """Reserve = a percentage of pack capacity plus a hover budget for touchdown."""
    pack_reserve = cfg.energy.battery_capacity_wh * cfg.safety.battery_reserve_percent / 100.0
    landing_reserve = cfg.energy.hover_power_w * cfg.energy.reserve_landing_sec / 3600.0
    return pack_reserve + landing_reserve


def estimate_energy(state: OperationalState, cfg: SafeDropConfig) -> EnergyEstimate:
    r"""Compute the remaining-energy picture.

    .. math::
        E_u = E_r - E_{reserve}, \qquad T_r = \frac{E_u}{P_f}
    """
    remaining = cfg.energy.battery_capacity_wh * state.battery_soc / 100.0
    reserve = reserve_energy_wh(cfg)
    usable = max(0.0, remaining - reserve)
    power = estimated_power_w(state.payload_kg, state.motor_health, cfg.energy)
    flight_time = (usable / power) * 3600.0 if power > 0 else 0.0
    return EnergyEstimate(
        remaining_energy_wh=round(remaining, 2),
        reserve_energy_wh=round(reserve, 2),
        usable_energy_wh=round(usable, 2),
        estimated_power_w=round(power, 1),
        remaining_flight_time_sec=round(flight_time, 1),
    )


def ground_speed_mps(
    airspeed_mps: float, wind_speed_mps: float, wind_from_deg: float, heading_deg: float
) -> float:
    r"""Wind-adjusted ground speed.

    ``wind_from_deg`` follows the meteorological convention (the direction the
    wind blows *from*), so flying on that heading is a pure headwind:

    .. math:: V_g = V_a - V_w \cos(\psi - \psi_{wind})
    """
    component = wind_speed_mps * math.cos(math.radians(heading_deg - wind_from_deg))
    return max(MIN_GROUND_SPEED_MPS, airspeed_mps - component)


def energy_for_flight_time_wh(power_w: float, seconds: float) -> float:
    return power_w * seconds / 3600.0


def check_feasibility(
    target_name: str,
    target_lat: float,
    target_lon: float,
    state: OperationalState,
    estimate: EnergyEstimate,
    cfg: SafeDropConfig,
    insufficient_code: ReasonCode,
    sufficient_code: ReasonCode,
    distance_m: float | None = None,
) -> FeasibilityCheck:
    """Can the aircraft reach ``target`` on usable energy, given the wind?"""
    if distance_m is None:
        distance_m = haversine_m(state.latitude, state.longitude, target_lat, target_lon)
    heading = bearing_deg(state.latitude, state.longitude, target_lat, target_lon)
    vg = ground_speed_mps(
        state.airspeed_mps, state.wind_speed_mps, state.wind_direction_deg, heading
    )
    time_sec = distance_m / vg
    required = energy_for_flight_time_wh(estimate.estimated_power_w, time_sec)
    margin = estimate.usable_energy_wh - required
    feasible = margin >= 0.0
    return FeasibilityCheck(
        target=target_name,
        distance_m=round(distance_m, 1),
        ground_speed_mps=round(vg, 2),
        required_time_sec=round(time_sec, 1),
        required_energy_wh=round(required, 2),
        energy_margin_wh=round(margin, 2),
        feasible=feasible,
        reason_codes=[sufficient_code if feasible else insufficient_code],
    )
