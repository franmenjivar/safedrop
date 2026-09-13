"""Scenario definition: a fixed, reproducible dataset for one contingency event."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from app.models.enums import ContingencyAction, FailureFamily, ReasonCode
from app.models.landing_zone import LandingCandidate


class GeoPoint(BaseModel):
    name: str
    latitude: float
    longitude: float


class MissionState(BaseModel):
    """Where the aircraft came from and where it was going."""

    base: GeoPoint
    destination: GeoPoint
    alternate_hub: GeoPoint | None = None


class ScenarioFiles(BaseModel):
    """Paths are resolved relative to the scenario directory at load time."""

    telemetry_csv: Path
    known_elzs_geojson: Path | None = None
    map_features_geojson: Path | None = None
    obstacles_geojson: Path | None = None
    restrictions_geojson: Path | None = None
    traffic_csv: Path | None = None
    ground_state_json: Path | None = None
    image_directory: Path | None = None
    ground_truth_json: Path | None = None


class Scenario(BaseModel):
    id: str
    name: str
    description: str
    region: str = "Austin, Texas"
    context_mode: str = "offline"

    mission: MissionState
    files: ScenarioFiles
    directory: Path

    known_elzs: list[LandingCandidate] = Field(default_factory=list)

    #: The telemetry index at which the contingency should be evaluated.
    decision_step: int = -1


class GroundTruth(BaseModel):
    """Expected outcome, authored before the agents are run."""

    scenario_id: str
    failure_family: FailureFamily = FailureFamily.ENERGY
    expected_action: ContingencyAction
    expected_candidate_id: str | None = None
    acceptable_candidate_ids: list[str] = Field(default_factory=list)
    unsafe_candidates: dict[str, ReasonCode] = Field(default_factory=dict)
    expects_replan: bool = False
    expects_abstention: bool = False
    expected_reason_codes: list[ReasonCode] = Field(default_factory=list)
    notes: str = ""
