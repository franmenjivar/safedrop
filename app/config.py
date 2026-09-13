"""Configuration loading for SafeDrop.

All safety thresholds and experiment parameters live in ``config/default.yaml``
so that experiments can be re-run with different weights without touching code.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parent.parent

# Credentials live in .env (gitignored), never in the repository or in config.
load_dotenv(REPO_ROOT / ".env")
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "default.yaml"
DATA_DIR = REPO_ROOT / "data"
RESULTS_DIR = REPO_ROOT / "results"
TRAJECTORY_DIR = REPO_ROOT / "trajectories"

ContextMode = Literal["offline", "live", "hybrid"]


class SafetyConfig(BaseModel):
    battery_reserve_percent: float = 5.0
    minimum_separation_m: float = 30.0
    minimum_landing_area_m2: float = 120.0
    minimum_visible_clear_area_percent: float = 70.0
    ground_data_max_age_sec: float = 30.0
    traffic_data_max_age_sec: float = 15.0
    max_allowed_vehicles: int = 0
    max_allowed_people: int = 0
    max_allowed_animals: int = 0
    descent_corridor_radius_m: float = 15.0


class EnergyConfig(BaseModel):
    battery_capacity_wh: float = 446.0
    hover_power_w: float = 1400.0
    cruise_power_w: float = 900.0
    payload_power_w_per_kg: float = 45.0
    motor_degradation_power_factor: float = 1.6
    reserve_landing_sec: float = 25.0


class CandidateGenerationConfig(BaseModel):
    school_buffer_m: float = 100.0
    hospital_buffer_m: float = 100.0
    playground_buffer_m: float = 80.0
    major_road_buffer_m: float = 30.0
    building_buffer_m: float = 20.0
    railway_buffer_m: float = 40.0
    power_line_buffer_m: float = 40.0
    max_candidates: int = 8


class RankingConfig(BaseModel):
    w_energy: float = 0.30
    w_ground: float = 0.20
    w_airspace: float = 0.20
    w_distance: float = 0.15
    w_complexity: float = 0.15


class ContextConfig(BaseModel):
    default_mode: ContextMode = "offline"


class ApiConfig(BaseModel):
    austin_gis_enabled: bool = False
    overpass_enabled: bool = False
    open_meteo_enabled: bool = False
    #: Optional Google Maps Embed API key. When set, candidate cards can render
    #: Street View inline instead of linking out. Embed keys are client-visible
    #: by design and should be restricted by HTTP referrer. Env-only — never in
    #: config/default.yaml, which is committed.
    google_maps_embed_key: str | None = None


AgentMode = Literal["live", "scripted"]


class AgentConfig(BaseModel):
    #: "live" runs the real agents. "scripted" swaps in rule-driven stand-ins
    #: (app/agents/scripted.py) so the console runs end to end with no model.
    #: Scripted runs are NOT agent performance and are labelled as such.
    mode: AgentMode = "live"
    model: str = "google-gla:gemini-2.5-flash"
    vision_model: str = "google-gla:gemini-2.5-flash"
    retries: int = 2
    temperature: float = 0.0
    #: Experiment switch: feed Agent 1 raw telemetry rows instead of the
    #: compressed operational state, to measure what the preprocessor buys.
    raw_telemetry_mode: bool = False
    raw_telemetry_rows: int = 60


class SimulationConfig(BaseModel):
    playback_ms: int = 250


class SafeDropConfig(BaseModel):
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    energy: EnergyConfig = Field(default_factory=EnergyConfig)
    candidate_generation: CandidateGenerationConfig = Field(
        default_factory=CandidateGenerationConfig
    )
    ranking: RankingConfig = Field(default_factory=RankingConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    apis: ApiConfig = Field(default_factory=ApiConfig)
    agents: AgentConfig = Field(default_factory=AgentConfig)
    simulation: SimulationConfig = Field(default_factory=SimulationConfig)


def load_config(path: Path | None = None) -> SafeDropConfig:
    path = path or DEFAULT_CONFIG_PATH
    raw: dict[str, Any] = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text()) or {}
    config = SafeDropConfig.model_validate(raw)

    # Environment overrides keep the demo switchable without editing YAML.
    if mode := os.getenv("SAFEDROP_CONTEXT_MODE"):
        config.context.default_mode = mode  # type: ignore[assignment]
    if agents := os.getenv("SAFEDROP_AGENTS"):
        config.agents.mode = agents.strip().lower()  # type: ignore[assignment]
    if model := os.getenv("SAFEDROP_MODEL"):
        config.agents.model = model
        config.agents.vision_model = model

    # pydantic-ai's Google provider reads GOOGLE_API_KEY; accept the Gemini
    # name too, since that is what AI Studio hands you.
    if not os.getenv("GOOGLE_API_KEY") and (key := os.getenv("GEMINI_API_KEY")):
        os.environ["GOOGLE_API_KEY"] = key

    if embed_key := os.getenv("GOOGLE_MAPS_EMBED_KEY"):
        config.apis.google_maps_embed_key = embed_key
    return config


@lru_cache(maxsize=1)
def get_config() -> SafeDropConfig:
    return load_config()
