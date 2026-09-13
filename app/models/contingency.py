"""Agent 1 output and the deterministic feasibility evidence behind it."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.enums import ContingencyAction, ReasonCode, Severity


class EnergyEstimate(BaseModel):
    """Result of the deterministic energy engine. Agents read; never compute."""

    remaining_energy_wh: float
    reserve_energy_wh: float
    usable_energy_wh: float
    estimated_power_w: float
    remaining_flight_time_sec: float


class FeasibilityCheck(BaseModel):
    """Whether a named destination is reachable on remaining usable energy."""

    target: str
    distance_m: float
    ground_speed_mps: float
    required_time_sec: float
    required_energy_wh: float
    energy_margin_wh: float
    feasible: bool
    reason_codes: list[ReasonCode] = Field(default_factory=list)


class ReachabilityResult(BaseModel):
    """Deterministic reachability envelope and which known ELZs fall inside it."""

    max_reachable_distance_m: float
    remaining_flight_time_sec: float
    usable_energy_wh: float
    reserve_energy_wh: float
    ground_speed_mps: float
    envelope_polygon: list[tuple[float, float]] = Field(
        default_factory=list,
        description="Wind-distorted reachable envelope as (lat, lon) vertices.",
    )
    reachable_candidate_ids: list[str] = Field(default_factory=list)
    unreachable_candidate_ids: list[str] = Field(default_factory=list)
    status: str = "OK"


class ContingencyDecision(BaseModel):
    """Structured output of Agent 1 — the Contingency Decision Agent."""

    action: ContingencyAction
    severity: Severity
    emergency_type: str | None = None

    mission_completion_possible: bool
    return_to_base_possible: bool
    alternate_hub_possible: bool
    forced_landing_required: bool

    reason_codes: list[ReasonCode] = Field(default_factory=list)
    rationale: str = Field(
        default="",
        description="Two or three sentences citing the tool results that drove the decision.",
    )
