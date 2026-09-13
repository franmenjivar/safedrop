"""Still-image inspection of a candidate landing zone.

Two responsibilities, kept strictly apart:

1. :func:`observe_landing_zone_image` asks a vision model for *observable
   facts* — how many people, vehicles, animals, obstacles are visible. It is
   never asked whether landing is safe.
2. :func:`apply_image_safety_rules` converts those facts into reason codes
   using deterministic thresholds from config. A language model cannot
   override these.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from pydantic_ai import Agent, BinaryContent

from app.config import SafeDropConfig
from app.models.enums import ReasonCode
from app.models.ground_context import LandingZoneImageObservation

VISION_INSTRUCTIONS = """\
You are an image observation tool for a drone operations console. You are shown an
overhead view of a possible emergency landing area.

Report only what is directly visible. Count objects. Do not judge whether landing is
safe, do not speculate about what might be outside the frame, and do not guess at
intent.

Definitions:
- people_count: human figures visible anywhere in the frame.
- vehicle_count: cars, trucks, vans, buses, trailers, or construction vehicles.
- animal_count: livestock, dogs, or other visible animals.
- large_obstacle_count: poles, towers, goalposts, dumpsters, large equipment, or
  structures inside the open area.
- visible_clear_area_percent: the share of the frame that is flat, open, and
  unobstructed, as a whole number 0-100.
- confidence: your confidence in these counts, 0.0 to 1.0.

If the image is too unclear to count reliably, report low confidence rather than
guessing high counts.
"""

MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def vision_enabled() -> bool:
    """Vision runs when a Gemini key is present and it has not been disabled."""
    if os.getenv("SAFEDROP_VISION", "").lower() in ("0", "off", "stub", "false"):
        return False
    return bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))


@lru_cache(maxsize=1)
def _vision_agent(model: str) -> Agent[None, LandingZoneImageObservation]:
    return Agent(
        model,
        output_type=LandingZoneImageObservation,
        instructions=VISION_INSTRUCTIONS,
        name="landing_zone_image_observer",
        retries=2,
    )


def _fixture_observation(
    candidate_id: str, image_path: Path
) -> LandingZoneImageObservation | None:
    """Fall back to the scenario's authored image facts when vision is off.

    This keeps the whole pipeline runnable with no API key. Runs made this way
    are labelled ``scenario_fixture`` so evaluation can exclude them from
    vision-accuracy metrics.
    """
    truth_file = image_path.parent / "image_ground_truth.json"
    if not truth_file.exists():
        return None
    truth = json.loads(truth_file.read_text()).get(image_path.name)
    if truth is None:
        return None
    return LandingZoneImageObservation(
        candidate_id=candidate_id,
        source="scenario_fixture",
        confidence=1.0,
        **{k: v for k, v in truth.items() if k != "candidate_clear"},
    )


async def observe_landing_zone_image(
    candidate_id: str, image_path: Path, cfg: SafeDropConfig
) -> LandingZoneImageObservation | None:
    """Return structured observations for a candidate's current site image."""
    if not image_path.exists():
        return None

    # Scripted mode never spends a model call, key present or not.
    if cfg.agents.mode == "scripted" or not vision_enabled():
        return _fixture_observation(candidate_id, image_path)

    media_type = MEDIA_TYPES.get(image_path.suffix.lower(), "image/png")
    agent = _vision_agent(cfg.agents.vision_model)
    try:
        result = await agent.run(
            [
                f"Overhead view of candidate landing area {candidate_id}. "
                "Report the visible counts.",
                BinaryContent(data=image_path.read_bytes(), media_type=media_type),
            ]
        )
    except Exception as exc:  # a vision failure must degrade, not crash the run
        fallback = _fixture_observation(candidate_id, image_path)
        if fallback is not None:
            return fallback.model_copy(update={"description": f"vision unavailable: {exc}"})
        return None

    return result.output.model_copy(
        update={"candidate_id": candidate_id, "source": cfg.agents.vision_model}
    )


def apply_image_safety_rules(
    observation: LandingZoneImageObservation, cfg: SafeDropConfig
) -> list[ReasonCode]:
    """Hard rejection rules over image observations (coding instructions §30)."""
    codes: list[ReasonCode] = []
    s = cfg.safety
    if observation.people_count > s.max_allowed_people:
        codes.append(ReasonCode.PEOPLE_PRESENT)
    if observation.vehicle_count > s.max_allowed_vehicles:
        codes.append(ReasonCode.VEHICLE_OCCUPANCY)
    if observation.animal_count > s.max_allowed_animals:
        codes.append(ReasonCode.ANIMAL_EXPOSURE)
    if observation.construction_equipment_present:
        codes.append(ReasonCode.CONSTRUCTION_OBSTRUCTION)
    if observation.standing_water_visible:
        codes.append(ReasonCode.STANDING_WATER)
    if (
        observation.visible_clear_area_percent is not None
        and observation.visible_clear_area_percent < s.minimum_visible_clear_area_percent
    ):
        codes.append(ReasonCode.INSUFFICIENT_VISIBLE_CLEAR_AREA)
    return codes
