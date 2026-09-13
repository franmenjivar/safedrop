"""Ground-condition evidence: scenario feeds, image observations, freshness."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.enums import FreshnessStatus


class FreshnessInfo(BaseModel):
    """Every context response carries provenance and age."""

    source: str
    retrieved_at: str | None = None
    age_seconds: float | None = None
    threshold_seconds: float | None = None
    freshness_status: FreshnessStatus = FreshnessStatus.UNKNOWN


class GroundState(BaseModel):
    """Current ground occupancy at a candidate, from the scenario ground feed."""

    candidate_id: str
    pedestrian_count: int | None = None
    vehicle_count: int | None = None
    animal_count: int | None = None
    construction_active: bool | None = None
    clear_area_percent: float | None = None
    surface_condition: str | None = None
    temporary_restriction_active: bool = False
    freshness: FreshnessInfo


class LandingZoneImageObservation(BaseModel):
    """Structured output of the vision tool.

    The vision model reports observable facts only. It is never asked
    "is this safe to land?" — that judgement is made by deterministic rules
    in :mod:`app.tools.image_context`.
    """

    candidate_id: str

    people_count: int = 0
    vehicle_count: int = 0
    animal_count: int = 0

    large_obstacle_count: int = 0
    construction_equipment_present: bool = False
    standing_water_visible: bool = False

    visible_clear_area_percent: float | None = None

    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source: str = "unknown"
    description: str = ""


class WeatherContext(BaseModel):
    latitude: float
    longitude: float
    wind_speed_mps: float
    wind_direction_deg: float
    wind_gust_mps: float | None = None
    precipitation_mm: float = 0.0
    temperature_c: float | None = None
    freshness: FreshnessInfo


class GISFeature(BaseModel):
    """Normalised geospatial feature from Austin GIS or OpenStreetMap."""

    source: str
    layer: str
    feature_type: str
    name: str | None = None
    geometry: dict = Field(default_factory=dict)
    properties: dict = Field(default_factory=dict)
