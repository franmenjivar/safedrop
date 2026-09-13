"""Conventional deterministic contingency baseline.

This is the comparison SafeDrop has to beat, and it is deliberately *not* a
straw man. It reflects what commercial and regulatory practice already
documents: predefined thresholds, a deterministic reachability calculation, a
preconfigured recovery-site list, nearest-feasible selection, and a direct
route, with a remote pilot supervising.

What it does not do is the thing SafeDrop is testing:

* it does not re-check whether a preplanned site is *currently* occupied,
* it does not look at site imagery,
* it does not generate off-nominal candidates when nothing preplanned is reachable,
* it does not verify traffic separation along the chosen route,
* it does not replan after a failed verification.
"""

from __future__ import annotations

import uuid

from app.config import SafeDropConfig, get_config
from app.models.contingency import ContingencyDecision
from app.models.enums import (
    ContingencyAction,
    GroundStatus,
    ReasonCode,
    Severity,
    SubsystemState,
    WorkflowState,
)
from app.models.landing_zone import CandidateEvaluation, ElzContextResult
from app.models.route import DiversionVerificationResult, RouteEvaluation
from app.models.state import RunState
from app.services.scenario_loader import ScenarioData, load_scenario_cached
from app.tools.energy import check_feasibility, estimate_energy
from app.tools.geometry import haversine_m
from app.tools.reachability import compute_reachability
from app.tools.route_planner import (
    calculate_route_energy,
    check_restricted_airspace,
    check_route_obstacles,
    plan_route,
    validate_descent_corridor,
)
from app.tools.state_preprocessing import build_operational_state, row_to_telemetry
from app.verification.briefing import build_briefing
from app.verification.safety_gateway import run_gateway
from app.models.verification import EvidenceSource, Recommendation, RejectedOption

# Predefined contingency thresholds, of the kind a conventional failsafe carries.
SOC_LAND_IMMEDIATELY = 12.0
SOC_RETURN_TO_BASE = 25.0
BATTERY_TEMP_LAND = 70.0
MOTOR_HEALTH_LAND = 0.65
LINK_QUALITY_LOST = 0.25


def run_baseline(
    scenario_id: str,
    cfg: SafeDropConfig | None = None,
    run_id: str | None = None,
) -> RunState:
    cfg = cfg or get_config()
    scenario = load_scenario_cached(scenario_id)
    state = RunState(
        run_id=run_id or f"BASELINE-{scenario.scenario.id}-{uuid.uuid4().hex[:6]}",
        scenario_id=scenario.scenario.id,
    )

    index = scenario.scenario.decision_step
    state.telemetry_index = index
    state.telemetry_total = len(scenario.telemetry)
    state.latest_telemetry = row_to_telemetry(scenario.telemetry.iloc[index])
    operational = build_operational_state(scenario.telemetry, index, scenario.scenario.mission)
    state.operational_state = operational
    estimate = estimate_energy(operational, cfg)
    state.energy_estimate = estimate
    state.workflow_state = WorkflowState.STATE_PREPARED

    decision = _threshold_decision(state, scenario, cfg)
    state.contingency_decision = decision
    state.workflow_state = WorkflowState.CONTINGENCY_CLASSIFIED
    state.add_trace("baseline_failsafe", f"Threshold rules selected {decision.action}.")

    if decision.action is ContingencyAction.LAND_IMMEDIATELY:
        _baseline_landing(state, scenario, cfg)

    _finalise_baseline(state, scenario, cfg)
    return state


def _threshold_decision(
    state: RunState, scenario: ScenarioData, cfg: SafeDropConfig
) -> ContingencyDecision:
    operational = state.operational_state
    estimate = state.energy_estimate
    assert operational is not None and estimate is not None
    mission = scenario.scenario.mission

    destination = check_feasibility(
        "DESTINATION", mission.destination.latitude, mission.destination.longitude,
        operational, estimate, cfg,
        ReasonCode.INSUFFICIENT_MISSION_ENERGY, ReasonCode.MISSION_ENERGY_SUFFICIENT,
        operational.distance_to_destination_m,
    )
    base = check_feasibility(
        "BASE", mission.base.latitude, mission.base.longitude,
        operational, estimate, cfg,
        ReasonCode.INSUFFICIENT_RTB_ENERGY, ReasonCode.RTB_ENERGY_SUFFICIENT,
        operational.distance_to_base_m,
    )
    state.feasibility_checks = [destination, base]

    hub = None
    if mission.alternate_hub is not None:
        hub = check_feasibility(
            "ALTERNATE_HUB", mission.alternate_hub.latitude, mission.alternate_hub.longitude,
            operational, estimate, cfg,
            ReasonCode.INSUFFICIENT_HUB_ENERGY, ReasonCode.HUB_ENERGY_SUFFICIENT,
            operational.distance_to_hub_m,
        )
        state.feasibility_checks.append(hub)

    reasons: list[ReasonCode] = []
    land_now = (
        operational.battery_soc <= SOC_LAND_IMMEDIATELY
        or operational.battery_temperature_c >= BATTERY_TEMP_LAND
        or operational.motor_health <= MOTOR_HEALTH_LAND
    )
    if operational.battery_soc <= SOC_LAND_IMMEDIATELY:
        reasons.append(ReasonCode.LOW_STATE_OF_CHARGE)
    if operational.battery_temperature_c >= BATTERY_TEMP_LAND:
        reasons.append(ReasonCode.BATTERY_OVERHEAT)
    if operational.motor_health <= MOTOR_HEALTH_LAND:
        reasons.append(ReasonCode.MOTOR_DEGRADATION)

    if land_now or not (destination.feasible or base.feasible or (hub and hub.feasible)):
        action = ContingencyAction.LAND_IMMEDIATELY
        severity = Severity.CRITICAL
        if not reasons:
            reasons.append(ReasonCode.INSUFFICIENT_RTB_ENERGY)
    elif destination.feasible and operational.battery_soc > SOC_RETURN_TO_BASE:
        action = ContingencyAction.CONTINUE
        severity = (
            Severity.ADVISORY
            if operational.battery_state is SubsystemState.NOMINAL
            else Severity.CAUTION
        )
        reasons.append(ReasonCode.MISSION_ENERGY_SUFFICIENT)
    elif base.feasible:
        action = ContingencyAction.RETURN_TO_BASE
        severity = Severity.WARNING
        reasons.extend([ReasonCode.INSUFFICIENT_MISSION_ENERGY, ReasonCode.RTB_ENERGY_SUFFICIENT])
    elif hub and hub.feasible:
        action = ContingencyAction.DIVERT_TO_SAFE_HUB
        severity = Severity.WARNING
        reasons.append(ReasonCode.HUB_ENERGY_SUFFICIENT)
    else:
        action = ContingencyAction.LAND_IMMEDIATELY
        severity = Severity.CRITICAL
        reasons.append(ReasonCode.INSUFFICIENT_RTB_ENERGY)

    return ContingencyDecision(
        action=action,
        severity=severity,
        emergency_type="THRESHOLD_RULE",
        mission_completion_possible=destination.feasible,
        return_to_base_possible=base.feasible,
        alternate_hub_possible=bool(hub and hub.feasible),
        forced_landing_required=action is ContingencyAction.LAND_IMMEDIATELY,
        reason_codes=reasons,
        rationale="Predefined contingency thresholds applied to deterministic feasibility results.",
    )


def _baseline_landing(state: RunState, scenario: ScenarioData, cfg: SafeDropConfig) -> None:
    """Nearest feasible preconfigured recovery site, direct route, no re-checks."""
    operational = state.operational_state
    estimate = state.energy_estimate
    assert operational is not None and estimate is not None

    reachability = compute_reachability(operational, estimate, scenario.known_elzs, cfg)
    state.reachability = reachability
    state.workflow_state = WorkflowState.REACHABILITY_READY

    reachable = [
        c for c in scenario.known_elzs if c.candidate_id in reachability.reachable_candidate_ids
    ]
    state.candidates = reachable
    if not reachable:
        # A conventional system has no preconfigured site to fall back to.
        state.terminal_reason_codes = [ReasonCode.NO_REACHABLE_PREMAPPED_ELZ]
        state.workflow_state = WorkflowState.ESCALATED
        state.add_trace("baseline_selector", "No preconfigured recovery site is reachable.")
        return

    reachable.sort(
        key=lambda c: haversine_m(operational.latitude, operational.longitude, c.latitude, c.longitude)
    )
    chosen = reachable[0]

    # The baseline accepts the site on its static record alone.
    state.elz_context = ElzContextResult(
        valid_candidates=[
            CandidateEvaluation(
                candidate_id=chosen.candidate_id,
                status=GroundStatus.VALID,
                evidence=["Preconfigured recovery site accepted from its static record."],
                ground_confidence=1.0,
            )
        ],
        summary="Nearest feasible preconfigured recovery site selected.",
    )
    state.workflow_state = WorkflowState.ELZ_CONTEXT_CHECKED
    state.add_trace("baseline_selector", f"Nearest feasible preconfigured site: {chosen.candidate_id}.")

    # A conventional planner does carry static-map geofencing and obstacle
    # avoidance, so the baseline gets those. What it does not carry is dynamic
    # traffic separation verification or any replanning on failure.
    route = plan_route(operational, chosen, variant=1)
    energy = calculate_route_energy(route, operational, estimate, cfg)
    obstacles = check_route_obstacles(route, scenario, cfg)
    restrictions = check_restricted_airspace(route, scenario)
    descent = validate_descent_corridor(chosen, scenario, cfg)
    passed = energy.safe and obstacles.safe and restrictions.safe and descent.safe

    evaluation = RouteEvaluation(
        route=route,
        energy=energy,
        obstacles=obstacles,
        restrictions=restrictions,
        separation=None,  # never verified by the baseline
        descent=descent,
        passed=passed,
    )
    state.route_evaluations = [evaluation]
    state.diversion_result = DiversionVerificationResult(
        selected_candidate_id=chosen.candidate_id,
        selected_route_id=route.route_id,
        verified=passed,
        attempted_route_ids=[route.route_id],
        replan_count=0,
        reason_codes=[ReasonCode.ROUTE_VERIFIED] if passed else [ReasonCode.INSUFFICIENT_ROUTE_ENERGY],
        rationale="Direct route generated with static obstacle and geofence checks only.",
    )
    state.workflow_state = WorkflowState.ROUTE_VERIFIED
    state.add_trace("baseline_router", f"Direct route {route.route_id} generated ({route.distance_m:.0f} m).")


def _finalise_baseline(state: RunState, scenario: ScenarioData, cfg: SafeDropConfig) -> None:
    """Score the baseline through the same gateway, so the comparison is fair.

    The gateway is unchanged. Checks the baseline never ran simply have no
    result, and an unevaluated check is not a pass — which is precisely the
    difference the experiment is trying to measure.
    """
    verification = run_gateway(state, cfg)
    state.verification = verification
    decision = state.contingency_decision
    assert decision is not None

    candidate = None
    if state.diversion_result and state.diversion_result.selected_candidate_id:
        candidate = next(
            (c for c in state.candidates if c.candidate_id == state.diversion_result.selected_candidate_id),
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
        recommended_route_id=state.diversion_result.selected_route_id if state.diversion_result else None,
        verification=verification,
        sources=[EvidenceSource(type="telemetry", source="scenario_csv")],
        rejected_options=[
            RejectedOption(candidate_id=c.candidate_id, name=c.name, detail="Not selected by nearest-feasible ordering.")
            for c in state.candidates
            if candidate and c.candidate_id != candidate.candidate_id
        ],
        reason_codes=list(decision.reason_codes) + list(state.terminal_reason_codes),
        recommendation_ready=verification.all_pass(),
        operator_authorization_required=True,
    )
    recommendation.briefing = build_briefing(state, recommendation)
    state.recommendation = recommendation
    state.workflow_state = (
        WorkflowState.OPERATOR_PENDING if verification.all_pass() else WorkflowState.ESCALATED
    )
