"""The SafeDrop orchestrator.

Deliberately not an agent. It is a state machine that decides which agent runs
next, refuses invalid transitions, routes deterministic tool output between
stages, and stops the run when the evidence does not support going further.

Agents never call each other. Everything they produce lands in :class:`RunState`.
"""

from __future__ import annotations

import uuid

from app.agents import contingency_agent, diversion_agent, elz_context_agent, scripted
from app.agents.common import timed, usage_tokens
from app.agents.deps import AgentDeps
from app.config import SafeDropConfig, get_config
from app.models.enums import (
    ContingencyAction,
    GroundStatus,
    ReasonCode,
    VerificationStatus,
    WorkflowState,
)
from app.models.landing_zone import ElzContextResult
from app.models.route import DiversionVerificationResult
from app.models.state import RunState
from app.models.verification import EvidenceSource, Recommendation, RejectedOption
from app.services.scenario_loader import ScenarioData, load_scenario_cached
from app.services.trajectory_logger import TrajectoryLogger
from app.tools.candidate_generator import generate_off_nominal_candidates
from app.tools.energy import check_feasibility, estimate_energy
from app.tools.reachability import compute_reachability
from app.tools.state_preprocessing import build_operational_state, row_to_telemetry
from app.verification.briefing import build_briefing
from app.verification.safety_gateway import run_gateway

NON_LANDING_TARGETS = {
    ContingencyAction.CONTINUE: ("DESTINATION", ReasonCode.INSUFFICIENT_MISSION_ENERGY, ReasonCode.MISSION_ENERGY_SUFFICIENT),
    ContingencyAction.RETURN_TO_BASE: ("BASE", ReasonCode.INSUFFICIENT_RTB_ENERGY, ReasonCode.RTB_ENERGY_SUFFICIENT),
    ContingencyAction.DIVERT_TO_SAFE_HUB: ("ALTERNATE_HUB", ReasonCode.INSUFFICIENT_HUB_ENERGY, ReasonCode.HUB_ENERGY_SUFFICIENT),
}


async def run_workflow(
    scenario_id: str,
    run_id: str | None = None,
    cfg: SafeDropConfig | None = None,
    telemetry_index: int | None = None,
    state: RunState | None = None,
    scenario: ScenarioData | None = None,
) -> RunState:
    """Run the full contingency workflow for one scenario."""
    cfg = cfg or get_config()
    scenario = scenario or load_scenario_cached(scenario_id)
    run_id = run_id or f"{scenario.scenario.id}-{uuid.uuid4().hex[:8]}"
    state = state or RunState(run_id=run_id, scenario_id=scenario.scenario.id)
    logger = TrajectoryLogger(state.run_id, state.scenario_id)
    deps = AgentDeps(cfg=cfg, scenario=scenario, state=state, logger=logger)

    _prepare_state(state, scenario, cfg, telemetry_index)
    await _run_contingency_agent(deps)

    decision = state.contingency_decision
    assert decision is not None

    if decision.action is ContingencyAction.ESCALATE:
        state.workflow_state = WorkflowState.ESCALATED
        state.terminal_reason_codes = list(decision.reason_codes)
    elif decision.action in NON_LANDING_TARGETS:
        _verify_non_landing(deps, decision.action)
    else:
        await _run_forced_landing_branch(deps)

    _finalise(deps)
    return state


# --------------------------------------------------------------------------
# Stage 1 — deterministic preprocessing
# --------------------------------------------------------------------------


def _prepare_state(
    state: RunState,
    scenario: ScenarioData,
    cfg: SafeDropConfig,
    telemetry_index: int | None,
) -> None:
    index = telemetry_index if telemetry_index is not None else scenario.scenario.decision_step
    index = min(max(index, 0), len(scenario.telemetry) - 1)

    state.telemetry_index = index
    state.telemetry_total = len(scenario.telemetry)
    state.latest_telemetry = row_to_telemetry(scenario.telemetry.iloc[index])
    state.operational_state = build_operational_state(
        scenario.telemetry, index, scenario.scenario.mission
    )
    state.energy_estimate = estimate_energy(state.operational_state, cfg)
    state.workflow_state = WorkflowState.STATE_PREPARED
    state.add_trace(
        "preprocessor",
        f"Telemetry compressed: battery {state.operational_state.battery_state}, "
        f"motor {state.operational_state.motor_state}, "
        f"link {state.operational_state.communication_state}.",
    )
    state.add_trace(
        "energy_engine",
        f"{state.energy_estimate.usable_energy_wh:.1f} Wh usable above reserve — "
        f"{state.energy_estimate.remaining_flight_time_sec:.0f} s endurance.",
    )


# --------------------------------------------------------------------------
# Stage 2 — Agent 1
# --------------------------------------------------------------------------


async def _run_contingency_agent(deps: AgentDeps) -> None:
    state, cfg = deps.state, deps.cfg
    assert state.operational_state is not None
    if cfg.agents.raw_telemetry_mode:
        prompt = contingency_agent.build_raw_prompt(
            deps.scenario.telemetry,
            state.telemetry_index,
            deps.scenario.scenario.mission,
            deps.scenario.scenario.name,
            cfg.agents.raw_telemetry_rows,
        )
    else:
        prompt = contingency_agent.build_prompt(
            state.operational_state, deps.scenario.scenario.name
        )

    deps.logger.log_prompt(contingency_agent.AGENT_NAME, prompt)
    try:
        agent = contingency_agent.build_agent(cfg)
        if scripted.is_scripted(cfg):
            scripted.apply(
                agent,
                scripted.contingency_model(
                    battery_state=state.operational_state.battery_state.value
                ),
            )
        with timed() as t:
            result = await agent.run(prompt, deps=deps)
        decision = result.output
        tokens_in, tokens_out = usage_tokens(result)
        deps.logger.log_agent(
            contingency_agent.AGENT_NAME, decision, t.ms, tokens_in, tokens_out
        )
    except Exception as exc:
        decision = contingency_agent.fallback_decision(str(exc))
        deps.logger.log_agent(
            contingency_agent.AGENT_NAME, decision, 0.0, error=str(exc)
        )

    state.contingency_decision = decision
    state.workflow_state = WorkflowState.CONTINGENCY_CLASSIFIED
    state.add_trace(
        "agent_1",
        f"{decision.action} ({decision.severity}) — {decision.rationale or 'no rationale given'}",
        reason_codes=[c.value for c in decision.reason_codes],
    )


# --------------------------------------------------------------------------
# Non-landing branches
# --------------------------------------------------------------------------


def _verify_non_landing(deps: AgentDeps, action: ContingencyAction) -> None:
    """CONTINUE / RETURN / DIVERT still need a deterministic feasibility check on record."""
    state, cfg, scenario = deps.state, deps.cfg, deps.scenario
    target, insufficient, sufficient = NON_LANDING_TARGETS[action]
    assert state.operational_state is not None and state.energy_estimate is not None

    if not any(c.target == target for c in state.feasibility_checks):
        mission = scenario.scenario.mission
        point = {
            "DESTINATION": mission.destination,
            "BASE": mission.base,
            "ALTERNATE_HUB": mission.alternate_hub,
        }[target]
        if point is not None:
            check = check_feasibility(
                target,
                point.latitude,
                point.longitude,
                state.operational_state,
                state.energy_estimate,
                cfg,
                insufficient,
                sufficient,
            )
            state.feasibility_checks.append(check)
            deps.logger.log_tool(
                "orchestrator", f"verify_{target.lower()}", {"target": target}, check, 0.0
            )

    state.workflow_state = WorkflowState.SAFETY_VERIFIED
    state.add_trace("orchestrator", f"{action} branch verified against deterministic feasibility.")


# --------------------------------------------------------------------------
# Forced-landing branch
# --------------------------------------------------------------------------


async def _run_forced_landing_branch(deps: AgentDeps) -> None:
    state, cfg, scenario = deps.state, deps.cfg, deps.scenario
    assert state.operational_state is not None and state.energy_estimate is not None

    reachability = compute_reachability(
        state.operational_state, state.energy_estimate, scenario.known_elzs, cfg
    )
    state.reachability = reachability
    state.workflow_state = WorkflowState.REACHABILITY_READY
    deps.logger.log_tool("orchestrator", "compute_reachability", {}, reachability, 0.0)

    reachable_known = [
        c for c in scenario.known_elzs if c.candidate_id in reachability.reachable_candidate_ids
    ]
    state.candidates = list(reachable_known)

    if reachable_known:
        state.add_trace(
            "reachability",
            f"{len(reachable_known)} preplanned simulated ELZ(s) inside the "
            f"{reachability.max_reachable_distance_m:.0f} m envelope.",
        )
    else:
        state.add_trace(
            "reachability",
            "No preplanned simulated ELZ is reachable — generating off-nominal candidates.",
            reason_codes=[ReasonCode.NO_REACHABLE_PREMAPPED_ELZ.value],
        )
        generated, risk, rejected = generate_off_nominal_candidates(
            scenario, state.operational_state, state.energy_estimate, cfg
        )
        state.candidates = generated
        state.candidate_risk_features = risk
        state.prefilter_rejections = rejected
        state.generated_off_nominal = True
        deps.logger.log_tool(
            "orchestrator",
            "generate_off_nominal_candidates",
            {},
            {
                "generated": [c.candidate_id for c in generated],
                "deterministically_rejected": [
                    {"candidate_id": r.candidate_id, "reason_codes": [c.value for c in r.reason_codes]}
                    for r in rejected
                ],
            },
            0.0,
        )
        for rejection in rejected:
            state.add_trace(
                "candidate_generator",
                f"{rejection.candidate_id} excluded: "
                + ", ".join(c.value for c in rejection.reason_codes),
            )
        state.add_trace(
            "candidate_generator",
            f"{len(generated)} non-certified off-nominal candidate(s) generated.",
        )

    if not state.candidates:
        state.workflow_state = WorkflowState.ESCALATED
        state.terminal_reason_codes = [
            ReasonCode.NO_REACHABLE_PREMAPPED_ELZ
            if not state.generated_off_nominal
            else ReasonCode.NO_CANDIDATE_GENERATED
        ]
        state.add_trace("orchestrator", "No landing candidate available. Escalating.")
        return

    await _run_elz_agent(deps)
    valid = state.elz_context.valid_candidates if state.elz_context else []
    if not valid:
        state.workflow_state = WorkflowState.ESCALATED
        state.terminal_reason_codes = [ReasonCode.NO_GROUND_SAFE_ELZ]
        state.add_trace(
            "orchestrator",
            "No candidate is ground-safe on current evidence. Escalating to the operator.",
        )
        return

    await _run_diversion_agent(deps)
    result = state.diversion_result
    if result is None or not result.verified:
        state.workflow_state = WorkflowState.ESCALATED
        state.terminal_reason_codes = (
            result.reason_codes if result else [ReasonCode.NO_VERIFIED_ROUTE]
        ) or [ReasonCode.NO_VERIFIED_ROUTE]
        state.add_trace("orchestrator", "No verified route to any ground-safe candidate.")
        return

    state.workflow_state = WorkflowState.ROUTE_VERIFIED


async def _run_elz_agent(deps: AgentDeps) -> None:
    state, cfg = deps.state, deps.cfg
    note = (
        "No preplanned site is reachable, so these candidates were generated from "
        "geospatial context and are NOT certified landing zones."
        if state.generated_off_nominal
        else "These are preplanned simulated recovery sites inside the reachable envelope."
    )
    prompt = elz_context_agent.build_prompt(state.candidates, note)

    deps.logger.log_prompt(elz_context_agent.AGENT_NAME, prompt)
    try:
        agent = elz_context_agent.build_agent(cfg)
        if scripted.is_scripted(cfg):
            scripted.apply(
                agent,
                scripted.elz_model([c.candidate_id for c in state.candidates]),
            )
        with timed() as t:
            result = await agent.run(prompt, deps=deps)
        output: ElzContextResult = result.output
        tokens_in, tokens_out = usage_tokens(result)
        deps.logger.log_agent(elz_context_agent.AGENT_NAME, output, t.ms, tokens_in, tokens_out)
        output = _reconcile_elz_output(deps, output)
    except Exception as exc:
        deps.logger.log_agent(elz_context_agent.AGENT_NAME, None, 0.0, error=str(exc))
        output = _deterministic_elz_fallback(deps)

    state.elz_context = output
    state.workflow_state = WorkflowState.ELZ_CONTEXT_CHECKED
    for evaluation in output.all_evaluations():
        codes = ", ".join(c.value for c in evaluation.reason_codes) or "no hard constraint fired"
        state.add_trace(
            "agent_2",
            f"{evaluation.candidate_id}: {evaluation.status} — {codes}",
            evidence=evaluation.evidence,
        )


def _reconcile_elz_output(deps: AgentDeps, output: ElzContextResult) -> ElzContextResult:
    """Re-apply the deterministic rules over the agent's classification.

    The agent decides what evidence to gather and how to summarise it. It does
    not get the final say on whether a hard constraint fired: any candidate the
    agent marked VALID is re-checked here, and demoted if the rules disagree.
    """
    from pydantic_ai import RunContext  # local import to avoid a cycle at module load

    ctx = _FakeCtx(deps)
    reconciled = ElzContextResult(summary=output.summary)
    seen: set[str] = set()

    for evaluation in output.all_evaluations():
        if evaluation.candidate_id in seen:
            continue
        seen.add(evaluation.candidate_id)
        if not any(c.candidate_id == evaluation.candidate_id for c in deps.state.candidates):
            continue  # the agent hallucinated a candidate id; drop it
        truth = elz_context_agent.evaluate_hard_constraints(ctx, evaluation.candidate_id)
        rule_status = GroundStatus(truth["verdict"])
        if rule_status is not evaluation.status:
            evaluation = evaluation.model_copy(
                update={
                    "status": rule_status,
                    "reason_codes": [
                        ReasonCode(c)
                        for c in truth["reason_codes"] + truth["unverified_reason_codes"]
                    ],
                    "evidence": evaluation.evidence
                    + [f"Deterministic reconciliation set status to {rule_status}."],
                }
            )
        elz_context_agent._append(reconciled, evaluation)

    # Any candidate the agent silently dropped is still evaluated.
    for candidate in deps.state.candidates:
        if candidate.candidate_id in seen:
            continue
        truth = elz_context_agent.evaluate_hard_constraints(ctx, candidate.candidate_id)
        from app.models.landing_zone import CandidateEvaluation

        elz_context_agent._append(
            reconciled,
            CandidateEvaluation(
                candidate_id=candidate.candidate_id,
                status=GroundStatus(truth["verdict"]),
                reason_codes=[
                    ReasonCode(c) for c in truth["reason_codes"] + truth["unverified_reason_codes"]
                ],
                evidence=truth["evidence"] + ["Candidate omitted by the agent; evaluated by rules."],
                ground_confidence=truth["ground_confidence"],
            ),
        )
    return reconciled


def _deterministic_elz_fallback(deps: AgentDeps) -> ElzContextResult:
    return elz_context_agent.deterministic_fallback(deps.state.candidates, _FakeCtx(deps))


class _FakeCtx:
    """Minimal stand-in for ``RunContext`` so rule functions can run outside an agent."""

    def __init__(self, deps: AgentDeps) -> None:
        self.deps = deps


async def _run_diversion_agent(deps: AgentDeps) -> None:
    state, cfg = deps.state, deps.cfg
    assert state.elz_context is not None
    valid_ids = [e.candidate_id for e in state.elz_context.valid_candidates]
    candidates = [c for c in state.candidates if c.candidate_id in valid_ids]

    prompt = diversion_agent.build_prompt(candidates, cfg.safety.minimum_separation_m)

    deps.logger.log_prompt(diversion_agent.AGENT_NAME, prompt)
    try:
        agent = diversion_agent.build_agent(cfg)
        if scripted.is_scripted(cfg):
            scripted.apply(
                agent,
                scripted.diversion_model([c.candidate_id for c in candidates]),
            )
        with timed() as t:
            result = await agent.run(prompt, deps=deps)
        output: DiversionVerificationResult = result.output
        tokens_in, tokens_out = usage_tokens(result)
        deps.logger.log_agent(diversion_agent.AGENT_NAME, output, t.ms, tokens_in, tokens_out)
    except Exception as exc:
        deps.logger.log_agent(diversion_agent.AGENT_NAME, None, 0.0, error=str(exc))
        output = DiversionVerificationResult(
            verified=False,
            reason_codes=[ReasonCode.AGENT_FAILURE],
            rationale=f"Diversion agent unavailable: {exc}",
        )

    output = _reconcile_diversion_output(deps, output)
    state.diversion_result = output

    for evaluation in state.route_evaluations:
        detail = []
        if evaluation.separation and evaluation.separation.minimum_separation_m is not None:
            detail.append(
                f"separation {evaluation.separation.minimum_separation_m:.0f} m / "
                f"{evaluation.separation.required_separation_m:.0f} m required"
            )
        if evaluation.energy:
            detail.append(f"energy margin {evaluation.energy.energy_margin_wh:+.1f} Wh")
        state.add_trace(
            "agent_3",
            f"{evaluation.route.route_id} ({evaluation.route.strategy}): "
            f"{'VERIFIED' if evaluation.passed else 'REJECTED'}"
            + (f" — {', '.join(detail)}" if detail else ""),
            reason_codes=[c.value for c in evaluation.reason_codes],
        )
    if output.replan_count:
        state.add_trace("agent_3", f"{output.replan_count} replan(s) requested.")


def _reconcile_diversion_output(
    deps: AgentDeps, output: DiversionVerificationResult
) -> DiversionVerificationResult:
    """Only a route whose stored checks all passed may be reported as verified."""
    state = deps.state
    for evaluation in state.route_evaluations:
        diversion_agent._finalise(evaluation)

    # A passing route only counts if it leads to a candidate Agent 2 cleared on
    # the ground. Verifying a path to an occupied site proves nothing.
    ground_valid = {
        e.candidate_id for e in (state.elz_context.valid_candidates if state.elz_context else [])
    }
    passing = [
        e for e in state.route_evaluations if e.passed and e.route.candidate_id in ground_valid
    ]
    attempted = [e.route.route_id for e in state.route_evaluations]
    replans = sum(1 for e in state.route_evaluations if e.route.variant > 1)

    claimed = next(
        (e for e in passing if e.route.route_id == output.selected_route_id), None
    )
    chosen = claimed or (passing[0] if passing else None)

    if chosen is None:
        codes = [c for e in state.route_evaluations for c in e.reason_codes]
        return output.model_copy(
            update={
                "verified": False,
                "selected_candidate_id": None,
                "selected_route_id": None,
                "attempted_route_ids": attempted,
                "replan_count": replans,
                "reason_codes": codes or [ReasonCode.NO_VERIFIED_ROUTE],
            }
        )

    return output.model_copy(
        update={
            "verified": True,
            "selected_candidate_id": chosen.route.candidate_id,
            "selected_route_id": chosen.route.route_id,
            "attempted_route_ids": attempted,
            "replan_count": replans,
            "reason_codes": [ReasonCode.ROUTE_VERIFIED],
        }
    )


# --------------------------------------------------------------------------
# Stage 6 — gateway, evidence package, operator hand-off
# --------------------------------------------------------------------------


def _finalise(deps: AgentDeps) -> None:
    state, cfg = deps.state, deps.cfg
    verification = run_gateway(state, cfg)
    state.verification = verification
    deps.logger.log_tool("safety_gateway", "run_gateway", {}, verification, 0.0)

    decision = state.contingency_decision
    assert decision is not None

    candidate = None
    if state.diversion_result and state.diversion_result.selected_candidate_id:
        candidate = next(
            (
                c
                for c in state.candidates
                if c.candidate_id == state.diversion_result.selected_candidate_id
            ),
            None,
        )

    recommendation = Recommendation(
        scenario_id=state.scenario_id,
        run_id=state.run_id,
        contingency_action=decision.action,
        recommended_candidate_id=candidate.candidate_id if candidate else None,
        recommended_candidate_name=candidate.name if candidate else None,
        candidate_tier=candidate.tier if candidate else None,
        certification_status=candidate.certification_status if candidate else None,
        recommended_route_id=state.diversion_result.selected_route_id
        if state.diversion_result
        else None,
        verification=verification,
        evidence=_build_evidence(state),
        sources=_build_sources(state, deps.scenario),
        rejected_options=_build_rejected(state),
        reason_codes=list(decision.reason_codes) + list(state.terminal_reason_codes),
        operator_authorization_required=True,
        recommendation_ready=verification.verification_status is VerificationStatus.PASS,
        simulation_only=True,
        flight_command_sent=False,
    )
    recommendation.briefing = build_briefing(state, recommendation)
    state.recommendation = recommendation

    if verification.verification_status is VerificationStatus.PASS:
        state.workflow_state = WorkflowState.OPERATOR_PENDING
        state.add_trace("safety_gateway", "All mandatory checks passed. Awaiting operator decision.")
    else:
        state.workflow_state = WorkflowState.ESCALATED
        failed = ", ".join(c.name for c in verification.failed_checks())
        state.add_trace("safety_gateway", f"Verification FAILED ({failed}). No plan released.")

    deps.logger.log_event(
        "orchestrator",
        "run_complete",
        workflow_state=state.workflow_state.value,
        verification=verification.verification_status.value,
        summary=deps.logger.summary(),
    )


def _build_evidence(state: RunState) -> dict:
    evidence: dict = {}
    if state.energy_estimate:
        evidence["energy"] = state.energy_estimate.model_dump()
    if state.reachability:
        evidence["reachability"] = {
            "max_reachable_distance_m": state.reachability.max_reachable_distance_m,
            "remaining_flight_time_sec": state.reachability.remaining_flight_time_sec,
            "reachable_preplanned_elz_ids": state.reachability.reachable_candidate_ids,
        }
    if state.diversion_result and state.diversion_result.selected_route_id:
        chosen = next(
            (
                e
                for e in state.route_evaluations
                if e.route.route_id == state.diversion_result.selected_route_id
            ),
            None,
        )
        if chosen:
            evidence["route"] = {
                "route_id": chosen.route.route_id,
                "distance_m": chosen.route.distance_m,
                "strategy": chosen.route.strategy,
                "energy_margin_wh": chosen.energy.energy_margin_wh if chosen.energy else None,
                "minimum_uav_separation_m": chosen.separation.minimum_separation_m
                if chosen.separation
                else None,
                "required_uav_separation_m": chosen.separation.required_separation_m
                if chosen.separation
                else None,
            }
        evidence["replan_count"] = state.diversion_result.replan_count
    if state.feasibility_checks:
        evidence["feasibility"] = [c.model_dump() for c in state.feasibility_checks]
    return evidence


def _build_sources(state: RunState, scenario: ScenarioData) -> list[EvidenceSource]:
    sources = [EvidenceSource(type="telemetry", source="scenario_csv")]
    if state.candidates:
        sources.append(
            EvidenceSource(
                type="geospatial",
                source="austin_gis_osm_fixture"
                if state.generated_off_nominal
                else "scenario_known_elzs",
            )
        )
    if state.image_observations:
        sources.append(
            EvidenceSource(
                type="visual_ground_check", source=state.image_observations[0].source
            )
        )
    if state.ground_states:
        sources.append(EvidenceSource(type="ground_occupancy", source="scenario_ground_feed"))
    if scenario.traffic is not None:
        sources.append(EvidenceSource(type="traffic", source="scenario_csv"))
    if state.weather:
        sources.append(EvidenceSource(type="weather", source=state.weather.freshness.source))
    return sources


def _build_rejected(state: RunState) -> list[RejectedOption]:
    rejected: list[RejectedOption] = []
    by_id = {c.candidate_id: c for c in state.candidates}

    for evaluation in state.prefilter_rejections:
        rejected.append(
            RejectedOption(
                candidate_id=evaluation.candidate_id,
                name=evaluation.candidate_id,
                reason_codes=evaluation.reason_codes,
                detail="; ".join(evaluation.evidence),
            )
        )
    if state.elz_context:
        for evaluation in state.elz_context.rejected_candidates + state.elz_context.unverified_candidates:
            candidate = by_id.get(evaluation.candidate_id)
            rejected.append(
                RejectedOption(
                    candidate_id=evaluation.candidate_id,
                    name=candidate.name if candidate else evaluation.candidate_id,
                    reason_codes=evaluation.reason_codes,
                    detail="; ".join(evaluation.evidence),
                )
            )
    selected_route = state.diversion_result.selected_route_id if state.diversion_result else None
    for evaluation in state.route_evaluations:
        if evaluation.passed or evaluation.route.route_id == selected_route:
            continue
        candidate = by_id.get(evaluation.route.candidate_id)
        detail = []
        if evaluation.separation and evaluation.separation.minimum_separation_m is not None:
            detail.append(
                f"predicted separation {evaluation.separation.minimum_separation_m:.0f} m "
                f"against {evaluation.separation.conflicting_vehicle_id or 'traffic'}, "
                f"{evaluation.separation.required_separation_m:.0f} m required"
            )
        if evaluation.obstacles and evaluation.obstacles.intersecting_obstacles:
            detail.append("intersects " + ", ".join(evaluation.obstacles.intersecting_obstacles))
        if evaluation.energy and not evaluation.energy.safe:
            detail.append(f"energy margin {evaluation.energy.energy_margin_wh:+.1f} Wh")
        rejected.append(
            RejectedOption(
                candidate_id=evaluation.route.route_id,
                name=f"{candidate.name if candidate else evaluation.route.candidate_id} via {evaluation.route.strategy}",
                reason_codes=evaluation.reason_codes,
                detail="; ".join(detail),
            )
        )
    return rejected


def apply_operator_decision(state: RunState, approve: bool) -> RunState:
    """Record the human decision. No flight command is ever emitted."""
    from app.models.enums import OperatorDecision

    state.operator_decision = OperatorDecision.APPROVE if approve else OperatorDecision.REJECT
    state.workflow_state = WorkflowState.APPROVED if approve else WorkflowState.REJECTED
    state.flight_command_sent = False
    state.add_trace(
        "operator",
        f"Operator {'APPROVED' if approve else 'REJECTED'} the recommendation. "
        "Simulation only — no command was sent to any aircraft.",
    )
    return state
