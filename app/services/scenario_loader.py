"""Loading fixed scenario datasets from ``data/scenarios/<id>/``.

A scenario is entirely file-backed: CSV telemetry, GeoJSON geography, JSON
ground state, JPEG site imagery, JSON ground truth. Nothing is fetched at load
time, so offline evaluation is byte-for-byte reproducible.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import pandas as pd
import yaml

from app.config import DATA_DIR
from app.models.enums import CandidateTier, CertificationStatus
from app.models.landing_zone import LandingCandidate
from app.models.scenario import GroundTruth, Scenario
from app.tools.geometry import geometry_centroid, polygon_area_m2

SCENARIO_DIR = DATA_DIR / "scenarios"


@dataclass
class ScenarioData:
    """All fixture content for one scenario, loaded once and reused."""

    scenario: Scenario
    telemetry: pd.DataFrame
    known_elzs: list[LandingCandidate] = field(default_factory=list)
    map_features: list[dict] = field(default_factory=list)
    obstacles: list[dict] = field(default_factory=list)
    restrictions: list[dict] = field(default_factory=list)
    traffic: pd.DataFrame | None = None
    ground_state: dict = field(default_factory=dict)
    ground_truth: GroundTruth | None = None
    image_dir: Path | None = None

    def features_of_type(self, *types: str) -> list[dict]:
        return [f for f in self.map_features if f["properties"].get("feature_type") in types]


def _read_geojson(path: Path | None) -> list[dict]:
    if path is None or not path.exists():
        return []
    payload = json.loads(path.read_text())
    if payload.get("type") == "FeatureCollection":
        return payload.get("features", [])
    return [payload]


def _candidate_from_feature(feature: dict) -> LandingCandidate:
    props = feature["properties"]
    # Prefer the measured pole of inaccessibility over the polygon centroid.
    # For an L-shaped or crescent park the centroid can fall outside the
    # polygon entirely — on a road, a building, or a lake.
    if "anchor_lat" in props:
        lat, lon = props["anchor_lat"], props["anchor_lon"]
    else:
        lat, lon = geometry_centroid(feature["geometry"])
    area = props.get("area_m2") or round(polygon_area_m2(feature["geometry"]), 1)
    tier = CandidateTier(props.get("tier", CandidateTier.PREPLANNED_SIMULATED_ELZ))
    return LandingCandidate(
        candidate_id=props["candidate_id"],
        name=props.get("name", props["candidate_id"]),
        tier=tier,
        certification_status=CertificationStatus(
            props.get(
                "certification_status",
                CertificationStatus.SIMULATED
                if tier is CandidateTier.PREPLANNED_SIMULATED_ELZ
                else CertificationStatus.NON_CERTIFIED,
            )
        ),
        feature_type=props.get("feature_type", "unknown"),
        source=props.get("source", "SafeDrop scenario dataset"),
        latitude=lat,
        longitude=lon,
        area_m2=area,
        surface=props.get("surface", "unknown"),
        static_obstacles=props.get("static_obstacles", []),
        image_path=props.get("image"),
        operator_review_required=tier is not CandidateTier.PREPLANNED_SIMULATED_ELZ,
    )


def load_scenario(scenario_id: str) -> ScenarioData:
    """Load one scenario by directory name or scenario id."""
    directory = SCENARIO_DIR / scenario_id
    if not directory.exists():
        directory = _find_by_id(scenario_id)
    manifest = yaml.safe_load((directory / "scenario.yaml").read_text())

    files = manifest.get("files", {})
    resolved = {k: (directory / v) for k, v in files.items() if v}
    scenario = Scenario.model_validate(
        {**manifest, "files": resolved, "directory": directory}
    )

    telemetry = pd.read_csv(scenario.files.telemetry_csv)
    known = [_candidate_from_feature(f) for f in _read_geojson(scenario.files.known_elzs_geojson)]
    scenario.known_elzs = known
    if scenario.decision_step < 0:
        scenario.decision_step = len(telemetry) - 1

    traffic = (
        pd.read_csv(scenario.files.traffic_csv)
        if scenario.files.traffic_csv and scenario.files.traffic_csv.exists()
        else None
    )
    ground_state = (
        json.loads(scenario.files.ground_state_json.read_text())
        if scenario.files.ground_state_json and scenario.files.ground_state_json.exists()
        else {}
    )
    ground_truth = (
        GroundTruth.model_validate_json(scenario.files.ground_truth_json.read_text())
        if scenario.files.ground_truth_json and scenario.files.ground_truth_json.exists()
        else None
    )
    image_dir = scenario.files.image_directory
    if image_dir is not None and not image_dir.exists():
        image_dir = None

    return ScenarioData(
        scenario=scenario,
        telemetry=telemetry,
        known_elzs=known,
        map_features=_read_geojson(scenario.files.map_features_geojson),
        obstacles=_read_geojson(scenario.files.obstacles_geojson),
        restrictions=_read_geojson(scenario.files.restrictions_geojson),
        traffic=traffic,
        ground_state=ground_state,
        ground_truth=ground_truth,
        image_dir=image_dir,
    )


def _find_by_id(scenario_id: str) -> Path:
    for directory in sorted(SCENARIO_DIR.iterdir()):
        manifest = directory / "scenario.yaml"
        if manifest.exists():
            data = yaml.safe_load(manifest.read_text())
            if data.get("id") == scenario_id:
                return directory
    raise FileNotFoundError(f"Unknown scenario: {scenario_id}")


@lru_cache(maxsize=32)
def load_scenario_cached(scenario_id: str) -> ScenarioData:
    return load_scenario(scenario_id)


def list_scenarios() -> list[dict]:
    """Manifest summaries for the scenario picker, without loading fixtures."""
    out: list[dict] = []
    if not SCENARIO_DIR.exists():
        return out
    for directory in sorted(SCENARIO_DIR.iterdir()):
        manifest = directory / "scenario.yaml"
        if not manifest.exists():
            continue
        data = yaml.safe_load(manifest.read_text())
        out.append(
            {
                "id": data["id"],
                "dir": directory.name,
                "name": data.get("name", data["id"]),
                "description": data.get("description", ""),
                "region": data.get("region", ""),
            }
        )
    return out
