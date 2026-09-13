"""Safety Verification Gateway.

Nothing reaches the operator without passing through here. The gateway is not
an agent and contains no model call: it re-reads the deterministic tool results
already stored in :class:`RunState` and decides whether the plan the agents
assembled is actually backed by evidence.

The failure mode it exists to prevent is a fluent recommendation whose
supporting checks never passed.
"""

from __future__ import annotations

from app.config import SafeDropConfig
from app.models.enums import (
    ContingencyAction,
    GroundStatus,
    ReasonCode,
    VerificationStatus,
)
from app.models.state import RunState
from app.models.verification import CheckResult, VerificationResult
from app.tools.freshness import is_usable

#: Actions that do not open the forced-landing branch.
NON_LANDING_ACTIONS = {
    ContingencyAction.CONTINUE,
    ContingencyAction.RETURN_TO_BASE,
    ContingencyAction.DIVERT_TO_SAFE_HUB,
}


def run_gateway(state: RunState, cfg: SafeDropConfig) -> VerificationResult:
    checks: list[CheckResult] = []
    reasons: list[ReasonCode] = []

    decision = state.contingency_decision
    checks.append(
        CheckResult(
            name="contingency_action",
            passed=decision is not None,
            detail=f"Agent 1 selected {decision.action}." if decision else "No contingency decision.",
        )
    )
    if decision is None:
        reasons.append(ReasonCode.AGENT_FAILURE)
        return _finalise(checks, reasons)

    if decision.action is ContingencyAction.ESCALATE:
        checks.append(
            CheckResult(
                name="escalation",
                passed=False,
                detail="Agent 1 escalated: evidence insufficient for an autonomous branch.",
            )
        )
        return _finalise(checks, reasons + decision.reason_codes)

    if decision.action in NON_LANDING_ACTIONS:
        return _verify_non_landing(state, decision.action, checks, reasons)

    return _verify_forced_landing(state, cfg, checks, reasons)


def _verify_non_landing(
    state: RunState,
    action: ContingencyAction,
    checks: list[CheckResult],
    reasons: list[ReasonCode],
) -> VerificationResult:
    """CONTINUE / RETURN_TO_BASE / DIVERT are verified against the feasibility tools."""
    target = {
        ContingencyAction.CONTINUE: "DESTINATION",
        ContingencyAction.RETURN_TO_BASE: "BASE",
        ContingencyAction.DIVERT_TO_SAFE_HUB: "ALTERNATE_HUB",
    }[action]
    match = next((c for c in state.feasibility_checks if c.target == target), None)
    checks.append(
        CheckResult(
            name="energy_margin",
            passed=bool(match and match.feasible),
            detail=(
                f"{target}: {match.energy_margin_wh:+.1f} Wh margin over reserve "
                f"at {match.ground_speed_mps:.1f} m/s ground speed."
                if match
                else f"No deterministic feasibility check for {target}."
            ),
        )
    )
    if match is None or not match.feasible:
        reasons.append(ReasonCode.INSUFFICIENT_MISSION_ENERGY)
    checks.append(
        CheckResult(
            name="operator_review_flagged",
            passed=True,
            detail="Recommendation is advisory; no flight command is issued.",
        )
    )
    return _finalise(checks, reasons)


def _verify_forced_landing(
    state: RunState,
    cfg: SafeDropConfig,
    checks: list[CheckResult],
    reasons: list[ReasonCode],
) -> VerificationResult:
    diversion = state.diversion_result
    candidate_id = diversion.selected_candidate_id if diversion else None
    route_id = diversion.selected_route_id if diversion else None

    if candidate_id is None or route_id is None:
        checks.append(
            CheckResult(
                name="verified_plan_exists",
                passed=False,
                detail="No candidate/route pair survived verification.",
            )
        )
        reasons.extend(diversion.reason_codes if diversion else [ReasonCode.NO_VERIFIED_ROUTE])
        return _finalise(checks, reasons)

    candidate = next((c for c in state.candidates if c.candidate_id == candidate_id), None)
    evaluation = None
    if state.elz_context:
        evaluation = next(
            (e for e in state.elz_context.all_evaluations() if e.candidate_id == candidate_id),
            None,
        )
    route_eval = next(
        (r for r in state.route_evaluations if r.route.route_id == route_id), None
    )

    # --- Ground safety -----------------------------------------------------
    ground_ok = evaluation is not None and evaluation.status is GroundStatus.VALID
    checks.append(
        CheckResult(
            name="ground_safety",
            passed=ground_ok,
            detail=(
                f"{candidate_id} ground status {evaluation.status}."
                if evaluation
                else f"{candidate_id} has no ground evaluation."
            ),
        )
    )
    if not ground_ok:
        reasons.append(ReasonCode.NO_GROUND_SAFE_ELZ)

    if route_eval is None:
        checks.append(
            CheckResult(name="route_verified", passed=False, detail=f"{route_id} was not evaluated.")
        )
        reasons.append(ReasonCode.NO_VERIFIED_ROUTE)
        return _finalise(checks, reasons)

    # --- Route checks ------------------------------------------------------
    energy = route_eval.energy
    checks.append(
        CheckResult(
            name="energy_margin",
            passed=bool(energy and energy.safe),
            detail=(
                f"{energy.energy_margin_wh:+.1f} Wh over reserve "
                f"({energy.route_energy_wh:.1f} Wh required)."
                if energy
                else "Route energy not calculated."
            ),
        )
    )
    if not (energy and energy.safe):
        reasons.append(ReasonCode.INSUFFICIENT_ROUTE_ENERGY)

    obstacles = route_eval.obstacles
    checks.append(
        CheckResult(
            name="route_obstacle_clearance",
            passed=bool(obstacles and obstacles.safe),
            detail=(
                f"Nearest structure {obstacles.minimum_clearance_m} m."
                if obstacles and obstacles.minimum_clearance_m is not None
                else (
                    f"Intersects {', '.join(obstacles.intersecting_obstacles)}."
                    if obstacles and obstacles.intersecting_obstacles
                    else "Obstacle check not run."
                )
            ),
        )
    )
    if not (obstacles and obstacles.safe):
        reasons.append(ReasonCode.ROUTE_OBSTACLE_INTERSECTION)

    restrictions = route_eval.restrictions
    checks.append(
        CheckResult(
            name="restricted_airspace",
            passed=bool(restrictions and restrictions.safe),
            detail=(
                "Clear of active restrictions."
                if restrictions and restrictions.safe
                else "Restriction check failed or not run."
            ),
        )
    )
    if not (restrictions and restrictions.safe):
        reasons.append(ReasonCode.RESTRICTED_AIRSPACE_INTERSECTION)

    separation = route_eval.separation
    sep_ok = bool(separation and separation.safe and separation.data_available)
    checks.append(
        CheckResult(
            name="uav_separation",
            passed=sep_ok,
            detail=(
                (
                    f"Minimum predicted separation {separation.minimum_separation_m} m "
                    f"(required {separation.required_separation_m:.0f} m)."
                    if separation.minimum_separation_m is not None
                    else "No conflicting traffic in the scenario feed."
                )
                if separation
                else "Separation not calculated."
            ),
        )
    )
    if not sep_ok:
        reasons.extend(separation.reason_codes if separation else [ReasonCode.TRAFFIC_VERIFICATION_UNAVAILABLE])

    descent = route_eval.descent
    checks.append(
        CheckResult(
            name="descent_corridor",
            passed=bool(descent and descent.safe),
            detail=(
                f"{descent.corridor_radius_m:.0f} m corridor clear."
                if descent and descent.safe
                else "Descent corridor obstructed or not checked."
            ),
        )
    )
    if not (descent and descent.safe):
        reasons.append(ReasonCode.DESCENT_CORRIDOR_BLOCKED)

    # --- Freshness ---------------------------------------------------------
    used_ground = next((g for g in state.ground_states if g.candidate_id == candidate_id), None)
    fresh_ok = used_ground is None or is_usable(used_ground.freshness)
    checks.append(
        CheckResult(
            name="data_freshness",
            passed=fresh_ok,
            detail=(
                f"Ground feed {used_ground.freshness.freshness_status}"
                + (
                    f", age {used_ground.freshness.age_seconds:.0f}s."
                    if used_ground.freshness.age_seconds is not None
                    else "."
                )
                if used_ground
                else "No timed ground feed for this candidate."
            ),
        )
    )
    if not fresh_ok:
        reasons.append(ReasonCode.GROUND_DATA_STALE)

    # --- Off-nominal candidates carry an extra obligation ------------------
    if candidate is not None and candidate.is_off_nominal:
        flagged = (
            candidate.operator_review_required
            and candidate.certification_status.value == "NON_CERTIFIED"
        )
        checks.append(
            CheckResult(
                name="operator_review_flagged",
                passed=flagged,
                detail="Off-nominal candidate is marked NON_CERTIFIED and requires operator review.",
            )
        )
    else:
        checks.append(
            CheckResult(
                name="operator_review_flagged",
                passed=True,
                detail="Preplanned simulated ELZ; operator authorisation still required.",
            )
        )

    return _finalise(checks, reasons)


def _finalise(checks: list[CheckResult], reasons: list[ReasonCode]) -> VerificationResult:
    result = VerificationResult(checks=checks, reason_codes=_dedupe(reasons))
    result.verification_status = (
        VerificationStatus.PASS if result.all_pass() else VerificationStatus.FAIL
    )
    return result


def _dedupe(codes: list[ReasonCode]) -> list[ReasonCode]:
    seen: dict[ReasonCode, None] = {}
    for code in codes:
        seen.setdefault(code, None)
    return list(seen)
