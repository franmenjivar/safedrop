"""Landing candidates: preplanned simulated ELZs and off-nominal candidates.

Terminology is deliberate (coding instructions §6). Nothing in this project
claims that a park, field, or parking lot is an officially designated
emergency landing zone.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.enums import CandidateTier, CertificationStatus, GroundStatus, ReasonCode


class LandingCandidate(BaseModel):
    """A place the aircraft could potentially be set down."""

    candidate_id: str
    name: str
    tier: CandidateTier
    certification_status: CertificationStatus
    feature_type: str = Field(description="parking_lot | sports_field | park | field | ...")
    source: str = Field(description="Where the geometry came from.")

    latitude: float
    longitude: float
    area_m2: float
    surface: str = "unknown"
    static_obstacles: list[str] = Field(default_factory=list)

    image_path: str | None = None
    operator_review_required: bool = True

    @property
    def is_off_nominal(self) -> bool:
        return self.tier is not CandidateTier.PREPLANNED_SIMULATED_ELZ


class CandidateRiskFeatures(BaseModel):
    """Deterministic geospatial risk features (coding instructions §25)."""

    candidate_id: str
    distance_from_drone_m: float
    distance_to_major_road_m: float | None = None
    distance_to_school_m: float | None = None
    distance_to_hospital_m: float | None = None
    distance_to_playground_m: float | None = None
    distance_to_railway_m: float | None = None
    distance_to_power_infrastructure_m: float | None = None
    building_density_100m: float | None = None
    road_density_100m: float | None = None
    terrain_slope_estimate_deg: float | None = None


class CandidateEvaluation(BaseModel):
    """Agent 2's verdict on a single candidate, with its supporting evidence."""

    candidate_id: str
    status: GroundStatus
    reason_codes: list[ReasonCode] = Field(default_factory=list)
    evidence: list[str] = Field(
        default_factory=list,
        description="Short human-readable evidence lines, each traceable to a tool result.",
    )
    ground_confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Confidence that ground state is as reported."
    )


class ElzContextResult(BaseModel):
    """Structured output of Agent 2."""

    valid_candidates: list[CandidateEvaluation] = Field(default_factory=list)
    rejected_candidates: list[CandidateEvaluation] = Field(default_factory=list)
    unverified_candidates: list[CandidateEvaluation] = Field(default_factory=list)
    summary: str = ""

    def all_evaluations(self) -> list[CandidateEvaluation]:
        return [
            *self.valid_candidates,
            *self.rejected_candidates,
            *self.unverified_candidates,
        ]
