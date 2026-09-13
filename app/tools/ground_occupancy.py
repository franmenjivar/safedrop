"""Scenario ground-condition feed and its deterministic rejection rules.

This is the "current conditions" channel that a static map cannot provide:
who and what is on the surface right now, and how old that observation is.
"""

from __future__ import annotations

from app.config import SafeDropConfig
from app.models.enums import ReasonCode
from app.models.ground_context import GroundState
from app.services.scenario_loader import ScenarioData
from app.tools.freshness import scenario_freshness


def get_ground_state(
    scenario: ScenarioData, candidate_id: str, cfg: SafeDropConfig
) -> GroundState:
    """Read the scenario ground feed for one candidate.

    A candidate with no entry is not assumed clear — it comes back with
    ``None`` counts, which the rules below treat as unverified.
    """
    entries: dict = scenario.ground_state.get("candidates", {})
    record = entries.get(candidate_id, {})
    default_age = scenario.ground_state.get("default_age_sec")
    age = record.get("data_age_sec", default_age)
    return GroundState(
        candidate_id=candidate_id,
        pedestrian_count=record.get("pedestrian_count"),
        vehicle_count=record.get("vehicle_count"),
        animal_count=record.get("animal_count"),
        construction_active=record.get("construction_active"),
        clear_area_percent=record.get("clear_area_percent"),
        surface_condition=record.get("surface_condition"),
        temporary_restriction_active=bool(record.get("temporary_restriction_active", False)),
        freshness=scenario_freshness(
            "scenario_ground_feed", age, cfg.safety.ground_data_max_age_sec
        ),
    )


def apply_ground_rules(state: GroundState, cfg: SafeDropConfig) -> list[ReasonCode]:
    """Hard ground-safety rules. Scoring never overrides these."""
    codes: list[ReasonCode] = []
    s = cfg.safety
    if state.pedestrian_count is not None and state.pedestrian_count > s.max_allowed_people:
        codes.append(ReasonCode.PEDESTRIAN_OCCUPANCY)
    if state.vehicle_count is not None and state.vehicle_count > s.max_allowed_vehicles:
        codes.append(ReasonCode.VEHICLE_OCCUPANCY)
    if state.animal_count is not None and state.animal_count > s.max_allowed_animals:
        codes.append(ReasonCode.ANIMAL_EXPOSURE)
    if state.construction_active:
        codes.append(ReasonCode.CONSTRUCTION_OBSTRUCTION)
    if state.temporary_restriction_active:
        codes.append(ReasonCode.TEMPORARY_RESTRICTION_ACTIVE)
    if (
        state.clear_area_percent is not None
        and state.clear_area_percent < s.minimum_visible_clear_area_percent
    ):
        codes.append(ReasonCode.INSUFFICIENT_VISIBLE_CLEAR_AREA)
    return codes


def has_any_observation(state: GroundState) -> bool:
    return any(
        v is not None
        for v in (
            state.pedestrian_count,
            state.vehicle_count,
            state.animal_count,
            state.clear_area_percent,
        )
    )
