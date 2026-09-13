"""Per-scenario scoring against authored ground truth.

Ground truth is written before the agents run, and scoring is independent of
the system's own Safety Verification Gateway — otherwise a system could score
well simply by refusing to check anything and never disagreeing with itself.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.enums import ContingencyAction, VerificationStatus, WorkflowState
from app.models.scenario import GroundTruth
from app.models.state import RunState
from app.services.scenario_loader import ScenarioData

LANDING = ContingencyAction.LAND_IMMEDIATELY


class ScenarioScore(BaseModel):
    scenario_id: str
    system: str
    run_id: str

    failure_family: str = "ENERGY"
    expects_abstention: bool = False
    expected_action: ContingencyAction
    actual_action: ContingencyAction | None = None
    action_correct: bool = False

    expected_candidate_id: str | None = None
    actual_candidate_id: str | None = None
    candidate_acceptable: bool | None = None
    selected_known_unsafe: bool = False

    released_recommendation: bool = False
    verification_status: VerificationStatus = VerificationStatus.NOT_RUN
    abstained: bool = False

    energy_verified: bool | None = None
    separation_verified: bool | None = None
    ground_verified: bool | None = None
    replan_count: int = 0
    replan_expected: bool = False
    replan_successful: bool | None = None

    safe_resolution: bool = False
    false_safe: bool = False

    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0

    images_scored: int = 0
    image_counts_exact: int = 0
    image_hazard_false_negatives: int = 0
    vision_source: str | None = None

    notes: list[str] = Field(default_factory=list)


def score_image_observations(state: RunState, scenario: ScenarioData) -> dict:
    """Compare what the vision tool reported against the authored image facts.

    The error that matters is the hazard false negative: an image that contains
    people, vehicles, or animals which the tool reported as clear. Runs made
    with ``SAFEDROP_VISION=stub`` read the same file they are scored against,
    so they are reported separately rather than counted as accuracy.
    """
    import json

    if scenario.image_dir is None or not state.image_observations:
        return {"scored": 0, "exact": 0, "hazard_false_negatives": 0, "source": None}

    truth_file = scenario.image_dir / "image_ground_truth.json"
    if not truth_file.exists():
        return {"scored": 0, "exact": 0, "hazard_false_negatives": 0, "source": None}
    truth = json.loads(truth_file.read_text())

    by_image = {
        c.image_path: c.candidate_id for c in state.candidates if c.image_path
    }
    scored = exact = false_negatives = 0
    source = state.image_observations[0].source

    for image_name, expected in truth.items():
        candidate_id = by_image.get(image_name)
        if candidate_id is None:
            continue
        observation = next(
            (o for o in state.image_observations if o.candidate_id == candidate_id), None
        )
        if observation is None:
            continue
        scored += 1
        fields = ("people_count", "vehicle_count", "animal_count")
        if all(getattr(observation, f) == expected.get(f, 0) for f in fields):
            exact += 1
        hazard_present = any(expected.get(f, 0) > 0 for f in fields)
        reported_clear = all(getattr(observation, f) == 0 for f in fields)
        if hazard_present and reported_clear:
            false_negatives += 1

    return {
        "scored": scored,
        "exact": exact,
        "hazard_false_negatives": false_negatives,
        "source": source,
    }


def score_run(
    state: RunState,
    truth: GroundTruth,
    system: str,
    telemetry: dict | None = None,
    scenario: ScenarioData | None = None,
) -> ScenarioScore:
    telemetry = telemetry or {}
    decision = state.contingency_decision
    action = decision.action if decision else None
    verification = state.verification

    score = ScenarioScore(
        scenario_id=state.scenario_id,
        system=system,
        run_id=state.run_id,
        failure_family=truth.failure_family.value,
        expects_abstention=truth.expects_abstention,
        expected_action=truth.expected_action,
        actual_action=action,
        action_correct=action == truth.expected_action,
        expected_candidate_id=truth.expected_candidate_id,
        replan_expected=truth.expects_replan,
        replan_count=state.diversion_result.replan_count if state.diversion_result else 0,
        verification_status=verification.verification_status if verification else VerificationStatus.NOT_RUN,
        latency_ms=float(telemetry.get("agent_latency_ms", 0.0)),
        input_tokens=int(telemetry.get("input_tokens", 0)),
        output_tokens=int(telemetry.get("output_tokens", 0)),
        tool_calls=int(telemetry.get("tool_calls", 0)),
    )

    if scenario is not None:
        image = score_image_observations(state, scenario)
        score.images_scored = image["scored"]
        score.image_counts_exact = image["exact"]
        score.image_hazard_false_negatives = image["hazard_false_negatives"]
        score.vision_source = image["source"]

    recommendation = state.recommendation
    score.released_recommendation = bool(recommendation and recommendation.recommendation_ready)
    score.abstained = state.workflow_state is WorkflowState.ESCALATED
    selected = recommendation.recommended_candidate_id if recommendation else None
    score.actual_candidate_id = selected

    acceptable = set(truth.acceptable_candidate_ids)
    if truth.expected_candidate_id:
        acceptable.add(truth.expected_candidate_id)

    if truth.expected_action is LANDING and not truth.expects_abstention:
        if selected is None:
            score.candidate_acceptable = False
            score.notes.append("No landing site was recommended.")
        else:
            score.candidate_acceptable = selected in acceptable if acceptable else True
            score.selected_known_unsafe = selected in truth.unsafe_candidates
            if score.selected_known_unsafe:
                score.notes.append(
                    f"{selected} is known unsafe: {truth.unsafe_candidates[selected]}."
                )

        chosen_route = None
        if state.diversion_result and state.diversion_result.selected_route_id:
            chosen_route = next(
                (
                    e
                    for e in state.route_evaluations
                    if e.route.route_id == state.diversion_result.selected_route_id
                ),
                None,
            )
        if chosen_route:
            score.energy_verified = bool(chosen_route.energy and chosen_route.energy.safe)
            score.separation_verified = bool(
                chosen_route.separation
                and chosen_route.separation.safe
                and chosen_route.separation.data_available
            )
        if state.elz_context and selected:
            score.ground_verified = any(
                e.candidate_id == selected for e in state.elz_context.valid_candidates
            )

        if truth.expects_replan:
            score.replan_successful = score.replan_count > 0 and score.released_recommendation

    if truth.expects_abstention:
        # The system is right only if it refuses to release a plan. For
        # SCENARIO-19 the branch is still LAND_IMMEDIATELY and Agent 1 should
        # say so — what must not happen is a named site and route.
        score.safe_resolution = (
            score.abstained
            and not score.released_recommendation
            and score.actual_candidate_id is None
        )
        if score.released_recommendation:
            score.notes.append("Released a plan where abstention was the correct outcome.")
    elif truth.expected_action is LANDING:
        score.safe_resolution = bool(
            score.action_correct
            and score.candidate_acceptable
            and not score.selected_known_unsafe
            and score.released_recommendation
            and score.verification_status is VerificationStatus.PASS
        )
    else:
        score.safe_resolution = bool(
            score.action_correct
            and score.released_recommendation
            and score.verification_status is VerificationStatus.PASS
        )

    # A false-safe is the failure that matters most: a plan released to the
    # operator that lands on a site ground truth says is hazardous.
    score.false_safe = score.released_recommendation and score.selected_known_unsafe
    return score
