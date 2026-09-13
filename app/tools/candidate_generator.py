"""Deterministic off-nominal landing-candidate generation.

Used only when no preplanned simulated ELZ is reachable. The generator scans
the scenario's geospatial fixture inside the reachability envelope, applies
hard exclusion buffers, and emits candidates with explicit provenance. The
language model never invents a site — it only evaluates what this produces.
"""

from __future__ import annotations

from app.config import SafeDropConfig
from app.models.contingency import EnergyEstimate
from app.models.enums import (
    CandidateTier,
    CertificationStatus,
    GroundStatus,
    ReasonCode,
)
from app.models.landing_zone import (
    CandidateEvaluation,
    CandidateRiskFeatures,
    LandingCandidate,
)
from app.models.telemetry import OperationalState
from app.services.scenario_loader import ScenarioData
from app.tools.geometry import (
    bearing_deg,
    geometry_centroid,
    haversine_m,
    point_to_features_min_distance,
    polygon_area_m2,
)
from app.tools.reachability import reachable_radius_m

#: Feature types that may become a candidate, and the tier they land in.
CANDIDATE_TIERS: dict[str, CandidateTier] = {
    "parking_lot": CandidateTier.OFF_NOMINAL_LOWER_EXPOSURE,
    "sports_field": CandidateTier.OFF_NOMINAL_LOWER_EXPOSURE,
    "recreation_ground": CandidateTier.OFF_NOMINAL_LOWER_EXPOSURE,
    "industrial_lot": CandidateTier.OFF_NOMINAL_LOWER_EXPOSURE,
    "open_lot": CandidateTier.OFF_NOMINAL_LOWER_EXPOSURE,
    "park": CandidateTier.OFF_NOMINAL_LAST_RESORT,
    "farmland": CandidateTier.OFF_NOMINAL_LAST_RESORT,
    "meadow": CandidateTier.OFF_NOMINAL_LAST_RESORT,
    "grass": CandidateTier.OFF_NOMINAL_LAST_RESORT,
}

#: Sensitive feature type -> (config buffer attribute, rejection reason code).
EXCLUSION_BUFFERS: dict[str, tuple[str, ReasonCode]] = {
    "school": ("school_buffer_m", ReasonCode.SCHOOL_PROXIMITY),
    "hospital": ("hospital_buffer_m", ReasonCode.HOSPITAL_PROXIMITY),
    "playground": ("playground_buffer_m", ReasonCode.PLAYGROUND_PROXIMITY),
    "major_road": ("major_road_buffer_m", ReasonCode.ROAD_CONFLICT),
    "railway": ("railway_buffer_m", ReasonCode.RAILWAY_PROXIMITY),
    "power_line": ("power_line_buffer_m", ReasonCode.POWER_INFRASTRUCTURE_PROXIMITY),
    "building": ("building_buffer_m", ReasonCode.BUILDING_CONFLICT),
}


def _density(lat: float, lon: float, features: list[dict], radius_m: float = 100.0) -> float:
    """Fraction of a 100 m-radius disc covered by the given feature type."""
    from math import pi

    from app.tools.geometry import LocalFrame
    from shapely.geometry import Point

    frame = LocalFrame(lat, lon)
    disc = Point(0.0, 0.0).buffer(radius_m)
    covered = 0.0
    for feature in features:
        try:
            covered += frame.geometry(feature["geometry"]).intersection(disc).area
        except Exception:
            continue
    return round(min(1.0, covered / (pi * radius_m**2)), 3)


def generate_off_nominal_candidates(
    scenario: ScenarioData,
    state: OperationalState,
    estimate: EnergyEstimate,
    cfg: SafeDropConfig,
) -> tuple[list[LandingCandidate], list[CandidateRiskFeatures], list[CandidateEvaluation]]:
    """Return ``(candidates, risk_features, deterministic_rejections)``."""
    buildings = [f for f in scenario.map_features if f["properties"].get("feature_type") == "building"]
    roads = [f for f in scenario.map_features if f["properties"].get("feature_type") in ("major_road", "road")]

    sensitive: dict[str, list[dict]] = {
        key: [f["geometry"] for f in scenario.map_features if f["properties"].get("feature_type") == key]
        for key in EXCLUSION_BUFFERS
    }

    kept: list[tuple[float, LandingCandidate, CandidateRiskFeatures]] = []
    rejected: list[CandidateEvaluation] = []

    for feature in scenario.map_features:
        props = feature["properties"]
        feature_type = props.get("feature_type", "")
        tier = CANDIDATE_TIERS.get(feature_type)
        if tier is None:
            continue

        if "anchor_lat" in props:
            lat, lon = props["anchor_lat"], props["anchor_lon"]
        else:
            lat, lon = geometry_centroid(feature["geometry"])
        candidate_id = props.get("candidate_id") or props.get("osm_id") or f"AUTO-{feature_type.upper()}"
        name = props.get("name", candidate_id)
        area = props.get("area_m2") or round(polygon_area_m2(feature["geometry"]), 1)
        distance = haversine_m(state.latitude, state.longitude, lat, lon)
        heading = bearing_deg(state.latitude, state.longitude, lat, lon)

        def reject(code: ReasonCode, detail: str) -> None:
            rejected.append(
                CandidateEvaluation(
                    candidate_id=candidate_id,
                    status=GroundStatus.REJECTED,
                    reason_codes=[code],
                    evidence=[detail],
                    ground_confidence=1.0,
                )
            )

        if distance > reachable_radius_m(state, estimate, heading):
            continue  # outside the envelope; not a rejection, just not offered

        if area < cfg.safety.minimum_landing_area_m2:
            reject(
                ReasonCode.INSUFFICIENT_AREA,
                f"{name}: {area:.0f} m2 < {cfg.safety.minimum_landing_area_m2:.0f} m2 minimum.",
            )
            continue

        # Hard exclusion buffers around sensitive infrastructure.
        distances: dict[str, float | None] = {}
        excluded = False
        for key, (attr, code) in EXCLUSION_BUFFERS.items():
            d = point_to_features_min_distance(lat, lon, sensitive[key])
            distances[key] = d
            buffer_m = getattr(cfg.candidate_generation, attr)
            if d is not None and d < buffer_m:
                reject(code, f"{name}: {d:.0f} m from nearest {key} (buffer {buffer_m:.0f} m).")
                excluded = True
                break
        if excluded:
            continue

        candidate = LandingCandidate(
            candidate_id=candidate_id,
            name=name,
            tier=tier,
            certification_status=CertificationStatus.NON_CERTIFIED,
            feature_type=feature_type,
            source=props.get("source", "AUSTIN_GIS_OSM_FIXTURE"),
            latitude=lat,
            longitude=lon,
            area_m2=area,
            surface=props.get("surface", "unknown"),
            static_obstacles=props.get("static_obstacles", []),
            image_path=props.get("image"),
            operator_review_required=True,
        )
        risk = CandidateRiskFeatures(
            candidate_id=candidate_id,
            distance_from_drone_m=round(distance, 1),
            distance_to_major_road_m=_r(distances.get("major_road")),
            distance_to_school_m=_r(distances.get("school")),
            distance_to_hospital_m=_r(distances.get("hospital")),
            distance_to_playground_m=_r(distances.get("playground")),
            distance_to_railway_m=_r(distances.get("railway")),
            distance_to_power_infrastructure_m=_r(distances.get("power_line")),
            building_density_100m=_density(lat, lon, buildings),
            road_density_100m=_density(lat, lon, roads),
            terrain_slope_estimate_deg=props.get("terrain_slope_estimate_deg"),
        )
        kept.append((distance, candidate, risk))

    # Prefer lower-exposure tiers, then proximity.
    kept.sort(key=lambda item: (item[1].tier is CandidateTier.OFF_NOMINAL_LAST_RESORT, item[0]))
    kept = kept[: cfg.candidate_generation.max_candidates]
    return [c for _, c, _ in kept], [r for _, _, r in kept], rejected


def _r(value: float | None) -> float | None:
    return None if value is None else round(value, 1)
