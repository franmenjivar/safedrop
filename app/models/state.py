"""The shared, inspectable workflow state.

Agents do not talk to each other. They read from and write to this object via
the deterministic orchestrator, which is what makes runs replayable.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app.models.contingency import (
    ContingencyDecision,
    EnergyEstimate,
    FeasibilityCheck,
    ReachabilityResult,
)
from app.models.enums import OperatorDecision, ReasonCode, WorkflowState
from app.models.ground_context import (
    GroundState,
    LandingZoneImageObservation,
    WeatherContext,
)
from app.models.landing_zone import (
    CandidateEvaluation,
    CandidateRiskFeatures,
    ElzContextResult,
    LandingCandidate,
)
from app.models.route import DiversionVerificationResult, RouteEvaluation
from app.models.telemetry import OperationalState, TelemetryRow
from app.models.verification import Recommendation, VerificationResult


class RunState(BaseModel):
    """Everything one SafeDrop run knows. Serialisable end to end."""

    run_id: str
    scenario_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    workflow_state: WorkflowState = WorkflowState.START

    # Telemetry replay
    telemetry_index: int = 0
    telemetry_total: int = 0
    playing: bool = False
    latest_telemetry: TelemetryRow | None = None

    # Stage 1 — deterministic preprocessing
    operational_state: OperationalState | None = None
    energy_estimate: EnergyEstimate | None = None
    feasibility_checks: list[FeasibilityCheck] = Field(default_factory=list)

    # Stage 2 — Agent 1
    contingency_decision: ContingencyDecision | None = None

    # Stage 3 — deterministic reachability
    reachability: ReachabilityResult | None = None
    candidates: list[LandingCandidate] = Field(default_factory=list)
    candidate_risk_features: list[CandidateRiskFeatures] = Field(default_factory=list)
    generated_off_nominal: bool = False
    prefilter_rejections: list[CandidateEvaluation] = Field(default_factory=list)

    # Stage 4 — Agent 2 evidence and verdict
    ground_states: list[GroundState] = Field(default_factory=list)
    image_observations: list[LandingZoneImageObservation] = Field(default_factory=list)
    weather: WeatherContext | None = None
    elz_context: ElzContextResult | None = None

    # Stage 5 — Agent 3
    route_evaluations: list[RouteEvaluation] = Field(default_factory=list)
    diversion_result: DiversionVerificationResult | None = None

    # Stage 6 — gateway and operator
    verification: VerificationResult | None = None
    recommendation: Recommendation | None = None
    operator_decision: OperatorDecision | None = None

    terminal_reason_codes: list[ReasonCode] = Field(default_factory=list)
    trace: list[dict] = Field(default_factory=list)

    simulation_only: bool = True
    flight_command_sent: bool = False

    def add_trace(self, source: str, message: str, **extra: object) -> None:
        """Append a human-readable line to the operator-facing decision trace."""
        self.trace.append(
            {
                "t": round(self.latest_telemetry.timestamp_s, 1)
                if self.latest_telemetry
                else 0.0,
                "source": source,
                "message": message,
                **extra,
            }
        )
