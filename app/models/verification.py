"""Safety Verification Gateway output and the final operator-facing package."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.enums import (
    CandidateTier,
    CertificationStatus,
    ContingencyAction,
    ReasonCode,
    VerificationStatus,
)


class CheckResult(BaseModel):
    """One mandatory check. ``passed=None`` means the check does not apply."""

    name: str
    passed: bool | None = None
    detail: str = ""

    @property
    def applicable(self) -> bool:
        return self.passed is not None


class VerificationResult(BaseModel):
    """Output of the Safety Verification Gateway.

    A PASS requires every *applicable* check to be explicitly true. A check
    that could not be evaluated is not a pass.
    """

    verification_status: VerificationStatus = VerificationStatus.NOT_RUN
    checks: list[CheckResult] = Field(default_factory=list)
    reason_codes: list[ReasonCode] = Field(default_factory=list)

    def applicable_checks(self) -> list[CheckResult]:
        return [c for c in self.checks if c.applicable]

    def failed_checks(self) -> list[CheckResult]:
        return [c for c in self.checks if c.passed is False]

    def all_pass(self) -> bool:
        applicable = self.applicable_checks()
        return bool(applicable) and all(c.passed for c in applicable)


class RejectedOption(BaseModel):
    candidate_id: str
    name: str
    reason_codes: list[ReasonCode] = Field(default_factory=list)
    detail: str = ""


class EvidenceSource(BaseModel):
    type: str
    source: str


class Recommendation(BaseModel):
    """The complete, evidence-backed package presented to the remote operator."""

    scenario_id: str
    run_id: str
    contingency_action: ContingencyAction
    recommended_candidate_id: str | None = None
    recommended_candidate_name: str | None = None
    candidate_tier: CandidateTier | None = None
    certification_status: CertificationStatus | None = None
    recommended_route_id: str | None = None

    verification: VerificationResult = Field(default_factory=VerificationResult)
    evidence: dict = Field(default_factory=dict)
    sources: list[EvidenceSource] = Field(default_factory=list)
    rejected_options: list[RejectedOption] = Field(default_factory=list)

    briefing: str = Field(default="", description="Deterministically templated operator briefing.")
    reason_codes: list[ReasonCode] = Field(default_factory=list)

    operator_authorization_required: bool = True
    recommendation_ready: bool = False
    simulation_only: bool = True
    flight_command_sent: bool = False
