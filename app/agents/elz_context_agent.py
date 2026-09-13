"""Agent 2 — ELZ Context Evaluation Agent.

Given reachable candidates, decide which are suitable *right now*. This is the
agentic core of SafeDrop: the deterministic layer can tell you a 900 m² asphalt
rectangle exists, but only current ground and visual evidence tells you whether
three delivery vans are parked on it.

The agent orchestrates evidence gathering. The rejection rules themselves are
deterministic and live in :func:`check_hard_constraints`, which the agent cannot
override.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_ai import Agent, RunContext

from app.agents.common import agent_model, load_prompt, model_settings, timed
from app.agents.deps import AgentDeps
from app.config import SafeDropConfig
from app.models.enums import CandidateTier, FreshnessStatus, GroundStatus, ReasonCode
from app.models.ground_context import GroundState, LandingZoneImageObservation
from app.models.landing_zone import (
    CandidateEvaluation,
    ElzContextResult,
    LandingCandidate,
)
from app.tools.austin_gis import get_austin_gis_context
from app.tools.freshness import is_usable
from app.tools.ground_occupancy import (
    apply_ground_rules,
    get_ground_state,
    has_any_observation,
)
from app.tools.image_context import apply_image_safety_rules, observe_landing_zone_image
from app.tools.osm_overpass import get_osm_context
from app.tools.weather import get_weather_context

AGENT_NAME = "elz_context_agent"


def build_agent(cfg: SafeDropConfig) -> Agent[AgentDeps, ElzContextResult]:
    agent = Agent(
        agent_model(cfg),
        deps_type=AgentDeps,
        output_type=ElzContextResult,
        instructions=load_prompt("elz_context_agent"),
        name=AGENT_NAME,
        retries=cfg.agents.retries,
        model_settings=model_settings(cfg),
    )

    @agent.tool
    def get_candidate_risk_features(ctx: RunContext[AgentDeps], candidate_id: str) -> dict:
        """Static geometry and land-use proximity for one candidate."""
        candidate = _candidate(ctx, candidate_id)
        risk = next(
            (r for r in ctx.deps.state.candidate_risk_features if r.candidate_id == candidate_id),
            None,
        )
        payload = {
            "candidate_id": candidate_id,
            "name": candidate.name,
            "tier": candidate.tier,
            "certification_status": candidate.certification_status,
            "feature_type": candidate.feature_type,
            "area_m2": candidate.area_m2,
            "surface": candidate.surface,
            "static_obstacles": candidate.static_obstacles,
            "minimum_required_area_m2": ctx.deps.cfg.safety.minimum_landing_area_m2,
            "has_current_image": bool(_image_path(ctx, candidate)),
            "risk_features": risk.model_dump() if risk else None,
        }
        ctx.deps.logger.log_tool(AGENT_NAME, "get_candidate_risk_features", {"candidate_id": candidate_id}, payload, 0.0)
        return payload

    @agent.tool
    def get_scenario_ground_state(ctx: RunContext[AgentDeps], candidate_id: str) -> GroundState:
        """Current ground-occupancy feed for one candidate, with data age."""
        with timed() as t:
            ground = get_ground_state(ctx.deps.scenario, candidate_id, ctx.deps.cfg)
        _store_ground(ctx, ground)
        ctx.deps.logger.log_tool(
            AGENT_NAME,
            "get_scenario_ground_state",
            {"candidate_id": candidate_id},
            ground,
            t.ms,
            reason_code=ground.freshness.freshness_status,
        )
        return ground

    @agent.tool
    async def inspect_landing_zone_image(
        ctx: RunContext[AgentDeps], candidate_id: str
    ) -> LandingZoneImageObservation | dict:
        """Observe the candidate's current site image. Returns counts, not a verdict."""
        candidate = _candidate(ctx, candidate_id)
        path = _image_path(ctx, candidate)
        if path is None:
            payload = {
                "candidate_id": candidate_id,
                "status": "NO_CURRENT_VISUAL_CONFIRMATION",
                "detail": "No current site image is available for this candidate.",
            }
            ctx.deps.logger.log_tool(
                AGENT_NAME,
                "inspect_landing_zone_image",
                {"candidate_id": candidate_id},
                payload,
                0.0,
                reason_code=ReasonCode.NO_CURRENT_VISUAL_CONFIRMATION,
            )
            return payload

        with timed() as t:
            observation = await observe_landing_zone_image(candidate_id, path, ctx.deps.cfg)
        if observation is None:
            payload = {
                "candidate_id": candidate_id,
                "status": "IMAGE_INSPECTION_UNAVAILABLE",
                "detail": "The vision tool could not produce observations.",
            }
            ctx.deps.logger.log_tool(
                AGENT_NAME, "inspect_landing_zone_image", {"candidate_id": candidate_id}, payload, t.ms
            )
            return payload

        _store_observation(ctx, observation)
        ctx.deps.logger.log_tool(
            AGENT_NAME, "inspect_landing_zone_image", {"candidate_id": candidate_id}, observation, t.ms
        )
        return observation

    @agent.tool
    def check_hard_constraints(ctx: RunContext[AgentDeps], candidate_id: str) -> dict:
        """Run the deterministic safety rules over everything gathered for a candidate.

        This is authoritative. If it returns reason codes, the candidate is
        rejected regardless of any other consideration.

        Call this only after gathering the candidate's evidence — it refuses to
        return a verdict while a source it depends on is still outstanding.
        """
        missing = _missing_evidence(ctx, candidate_id)
        if missing:
            payload = {
                "candidate_id": candidate_id,
                "verdict": "PREREQUISITES_NOT_MET",
                "missing_evidence": missing,
                "note": (
                    "No verdict was produced. Call "
                    + " and ".join(missing)
                    + f" for {candidate_id} first, then call check_hard_constraints again."
                ),
            }
            ctx.deps.logger.log_tool(
                AGENT_NAME,
                "check_hard_constraints",
                {"candidate_id": candidate_id},
                payload,
                0.0,
                decision="PREREQUISITES_NOT_MET",
            )
            return payload

        with timed() as t:
            result = evaluate_hard_constraints(ctx, candidate_id)
        ctx.deps.logger.log_tool(
            AGENT_NAME,
            "check_hard_constraints",
            {"candidate_id": candidate_id},
            result,
            t.ms,
            decision=result["verdict"],
            reason_code=result["reason_codes"][0] if result["reason_codes"] else None,
        )
        return result

    @agent.tool
    def check_data_freshness(ctx: RunContext[AgentDeps], candidate_id: str) -> dict:
        """Age and status of the evidence held for a candidate."""
        ground = next(
            (g for g in ctx.deps.state.ground_states if g.candidate_id == candidate_id), None
        )
        observation = next(
            (o for o in ctx.deps.state.image_observations if o.candidate_id == candidate_id), None
        )
        payload = {
            "candidate_id": candidate_id,
            "ground_feed": ground.freshness.model_dump() if ground else None,
            "image_observation_source": observation.source if observation else None,
            "threshold_sec": ctx.deps.cfg.safety.ground_data_max_age_sec,
        }
        ctx.deps.logger.log_tool(AGENT_NAME, "check_data_freshness", {"candidate_id": candidate_id}, payload, 0.0)
        return payload

    @agent.tool
    async def get_austin_gis_context_tool(ctx: RunContext[AgentDeps], candidate_id: str) -> dict:
        """City of Austin GIS context near a candidate. Context only, never approval."""
        candidate = _candidate(ctx, candidate_id)
        with timed() as t:
            payload = await get_austin_gis_context(
                ctx.deps.scenario, candidate.latitude, candidate.longitude, ctx.deps.cfg, 800.0
            )
        summary = _summarise_features(payload)
        ctx.deps.logger.log_tool(AGENT_NAME, "get_austin_gis_context", {"candidate_id": candidate_id}, summary, t.ms)
        return summary

    @agent.tool
    async def get_osm_context_tool(ctx: RunContext[AgentDeps], candidate_id: str) -> dict:
        """OpenStreetMap land-use context near a candidate, already normalised."""
        candidate = _candidate(ctx, candidate_id)
        with timed() as t:
            payload = await get_osm_context(
                ctx.deps.scenario, candidate.latitude, candidate.longitude, ctx.deps.cfg, 600.0
            )
        summary = _summarise_features(payload)
        ctx.deps.logger.log_tool(AGENT_NAME, "get_osm_context", {"candidate_id": candidate_id}, summary, t.ms)
        return summary

    @agent.tool
    async def get_weather_context_tool(ctx: RunContext[AgentDeps]) -> dict:
        """Current wind and precipitation over the operating area."""
        state = ctx.deps.state.operational_state
        assert state is not None
        with timed() as t:
            weather = await get_weather_context(
                ctx.deps.scenario, state.latitude, state.longitude, ctx.deps.cfg
            )
        ctx.deps.state.weather = weather
        ctx.deps.logger.log_tool(AGENT_NAME, "get_weather_context", {}, weather, t.ms)
        return weather.model_dump()

    return agent


# --------------------------------------------------------------------------
# Deterministic constraint evaluation — the part the agent cannot argue with.
# --------------------------------------------------------------------------


def _missing_evidence(ctx: RunContext[AgentDeps], candidate_id: str) -> list[str]:
    """Evidence sources that exist for this candidate but have not been read yet.

    Language models issue tool calls in parallel, so without this a constraint
    check can land before the imagery it depends on and produce a verdict on
    evidence that had not arrived.
    """
    candidate = _candidate(ctx, candidate_id)
    missing: list[str] = []
    if not any(g.candidate_id == candidate_id for g in ctx.deps.state.ground_states):
        missing.append("get_scenario_ground_state")
    if _image_path(ctx, candidate) is not None and not any(
        o.candidate_id == candidate_id for o in ctx.deps.state.image_observations
    ):
        missing.append("inspect_landing_zone_image")
    return missing


def evaluate_hard_constraints(ctx: RunContext[AgentDeps], candidate_id: str) -> dict:
    candidate = _candidate(ctx, candidate_id)
    cfg = ctx.deps.cfg
    codes: list[ReasonCode] = []
    evidence: list[str] = []

    if candidate.area_m2 < cfg.safety.minimum_landing_area_m2:
        codes.append(ReasonCode.INSUFFICIENT_AREA)
        evidence.append(
            f"Area {candidate.area_m2:.0f} m2 below the {cfg.safety.minimum_landing_area_m2:.0f} m2 minimum."
        )

    ground = next((g for g in ctx.deps.state.ground_states if g.candidate_id == candidate_id), None)
    if ground is None:
        ground = get_ground_state(ctx.deps.scenario, candidate_id, cfg)
        _store_ground(ctx, ground)

    ground_codes = apply_ground_rules(ground, cfg)
    codes.extend(ground_codes)
    if has_any_observation(ground):
        evidence.append(
            f"Ground feed: {ground.pedestrian_count} people, {ground.vehicle_count} vehicles, "
            f"{ground.animal_count} animals, clear area {ground.clear_area_percent}%."
        )

    observation = next(
        (o for o in ctx.deps.state.image_observations if o.candidate_id == candidate_id), None
    )
    if observation is not None:
        image_codes = apply_image_safety_rules(observation, cfg)
        codes.extend(c for c in image_codes if c not in codes)
        evidence.append(
            f"Image ({observation.source}): {observation.people_count} people, "
            f"{observation.vehicle_count} vehicles, {observation.animal_count} animals, "
            f"{observation.visible_clear_area_percent}% visibly clear."
        )

    # Evidence sufficiency. Off-nominal candidates need a current image when one exists.
    ground_current = is_usable(ground.freshness) and has_any_observation(ground)
    has_image_path = _image_path(ctx, candidate) is not None
    image_current = observation is not None

    unverified_reasons: list[ReasonCode] = []
    if ground.freshness.freshness_status is FreshnessStatus.STALE:
        unverified_reasons.append(ReasonCode.GROUND_DATA_STALE)
        evidence.append(
            f"Ground feed is STALE ({ground.freshness.age_seconds:.0f}s > "
            f"{cfg.safety.ground_data_max_age_sec:.0f}s threshold)."
        )
    if has_image_path and not image_current:
        unverified_reasons.append(ReasonCode.NO_CURRENT_VISUAL_CONFIRMATION)
    if candidate.is_off_nominal and not has_image_path and not ground_current:
        unverified_reasons.append(ReasonCode.NO_CURRENT_VISUAL_CONFIRMATION)
        evidence.append("Off-nominal candidate with no current visual or ground evidence.")
    if not ground_current and not image_current:
        if ReasonCode.NO_CURRENT_VISUAL_CONFIRMATION not in unverified_reasons:
            unverified_reasons.append(ReasonCode.NO_CURRENT_VISUAL_CONFIRMATION)

    # Last-resort sites — parks, farmland, general open land — carry the highest
    # occupancy uncertainty, and a single clear observation is the easiest thing
    # for a detector to get wrong. Certifying one requires corroboration: both a
    # current ground feed and a current image must agree it is clear.
    if candidate.tier is CandidateTier.OFF_NOMINAL_LAST_RESORT and not (
        ground_current and image_current
    ):
        unverified_reasons.append(ReasonCode.UNCORROBORATED_LAST_RESORT_SITE)
        evidence.append(
            f"Last-resort site ({candidate.feature_type}) has a single evidence source; "
            "certifying one requires both a current ground feed and a current image."
        )

    if codes:
        verdict = GroundStatus.REJECTED
    elif unverified_reasons:
        verdict = GroundStatus.UNVERIFIED
    else:
        verdict = GroundStatus.VALID
        evidence.append("All hard ground constraints passed on current evidence.")

    confidence = 1.0 if verdict is not GroundStatus.UNVERIFIED else 0.4
    return {
        "candidate_id": candidate_id,
        "verdict": verdict.value,
        "reason_codes": [c.value for c in codes],
        "unverified_reason_codes": [c.value for c in unverified_reasons],
        "evidence": evidence,
        "ground_confidence": confidence,
        "note": "This result is deterministic and cannot be overridden.",
    }


def deterministic_fallback(
    candidates: list[LandingCandidate], ctx: RunContext[AgentDeps]
) -> ElzContextResult:
    """Rule-only evaluation, used when the agent call itself fails."""
    result = ElzContextResult(summary="Agent unavailable; deterministic rules applied.")
    for candidate in candidates:
        outcome = evaluate_hard_constraints(ctx, candidate.candidate_id)
        evaluation = CandidateEvaluation(
            candidate_id=candidate.candidate_id,
            status=GroundStatus(outcome["verdict"]),
            reason_codes=[ReasonCode(c) for c in outcome["reason_codes"] + outcome["unverified_reason_codes"]],
            evidence=outcome["evidence"],
            ground_confidence=outcome["ground_confidence"],
        )
        _append(result, evaluation)
    return result


def _append(result: ElzContextResult, evaluation: CandidateEvaluation) -> None:
    if evaluation.status is GroundStatus.VALID:
        result.valid_candidates.append(evaluation)
    elif evaluation.status is GroundStatus.REJECTED:
        result.rejected_candidates.append(evaluation)
    else:
        result.unverified_candidates.append(evaluation)


# --------------------------------------------------------------------------


def _candidate(ctx: RunContext[AgentDeps], candidate_id: str) -> LandingCandidate:
    for candidate in ctx.deps.state.candidates:
        if candidate.candidate_id == candidate_id:
            return candidate
    raise ValueError(
        f"Unknown candidate {candidate_id!r}. Valid ids: "
        f"{[c.candidate_id for c in ctx.deps.state.candidates]}"
    )


def _image_path(ctx: RunContext[AgentDeps], candidate: LandingCandidate) -> Path | None:
    if not candidate.image_path:
        return None
    image_dir = ctx.deps.scenario.image_dir
    if image_dir is None:
        return None
    path = image_dir / candidate.image_path
    return path if path.exists() else None


def _store_ground(ctx: RunContext[AgentDeps], ground: GroundState) -> None:
    ctx.deps.state.ground_states = [
        g for g in ctx.deps.state.ground_states if g.candidate_id != ground.candidate_id
    ] + [ground]


def _store_observation(ctx: RunContext[AgentDeps], observation: LandingZoneImageObservation) -> None:
    ctx.deps.state.image_observations = [
        o for o in ctx.deps.state.image_observations if o.candidate_id != observation.candidate_id
    ] + [observation]


def _summarise_features(payload: dict) -> dict:
    features = payload.get("features", [])
    counts: dict[str, int] = {}
    names: list[str] = []
    for feature in features:
        counts[feature["feature_type"]] = counts.get(feature["feature_type"], 0) + 1
        if feature.get("name"):
            names.append(f"{feature['name']} ({feature['feature_type']})")
    return {
        "status": payload.get("status"),
        "source": payload.get("source"),
        "feature_counts": counts,
        "named_features": names[:12],
    }


def build_prompt(candidates: list[LandingCandidate], reachability_note: str) -> str:
    lines = []
    for c in candidates:
        lines.append(
            f"- {c.candidate_id} | {c.name} | {c.feature_type} | {c.area_m2:.0f} m2 | "
            f"{c.tier} | {c.certification_status}"
        )
    return f"""\
A forced landing has been called. {reachability_note}

REACHABLE CANDIDATES ({len(candidates)}):
{chr(10).join(lines)}

Evaluate every candidate above. Gather current evidence, then call
check_hard_constraints for each one before you classify it. Return all
{len(candidates)} candidates across valid / rejected / unverified.
"""
