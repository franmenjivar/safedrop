"""Agent 3 — Diversion Verification Agent.

Takes ground-valid candidates and establishes whether the aircraft can actually
reach one. The interesting behaviour here is not route-finding — the planner is
deterministic — but recognising that a failed separation check means *replan the
path*, while a failed energy check means *give up on this candidate*.
"""

from __future__ import annotations

from pydantic_ai import Agent, RunContext

from app.agents.common import agent_model, load_prompt, model_settings, timed
from app.agents.deps import AgentDeps
from app.config import SafeDropConfig
from app.models.enums import ReasonCode
from app.models.landing_zone import LandingCandidate
from app.models.route import (
    DiversionVerificationResult,
    Route,
    RouteEvaluation,
)
from app.tools.route_planner import (
    MAX_VARIANTS,
    calculate_route_energy,
    check_restricted_airspace,
    check_route_obstacles,
    plan_route,
    validate_descent_corridor,
)
from app.tools.separation import calculate_minimum_separation

AGENT_NAME = "diversion_verification_agent"


def build_agent(cfg: SafeDropConfig) -> Agent[AgentDeps, DiversionVerificationResult]:
    agent = Agent(
        agent_model(cfg),
        deps_type=AgentDeps,
        output_type=DiversionVerificationResult,
        instructions=load_prompt("diversion_agent"),
        name=AGENT_NAME,
        retries=cfg.agents.retries,
        model_settings=model_settings(cfg),
    )

    @agent.tool
    def plan_diversion_route(
        ctx: RunContext[AgentDeps], candidate_id: str, variant: int = 1
    ) -> Route:
        """Ask the deterministic planner for route variant ``variant`` to a candidate.

        Variant 1 is the direct path. Higher variants are lateral detours, used
        when a route-level check fails but the candidate itself is still good.
        """
        candidate = _candidate(ctx, candidate_id)
        state = _state(ctx)
        if variant > MAX_VARIANTS:
            raise ValueError(f"No route variants beyond {MAX_VARIANTS} exist for {candidate_id}.")
        with timed() as t:
            route = plan_route(state, candidate, variant)
        _store(ctx, RouteEvaluation(route=route))
        ctx.deps.logger.log_tool(
            AGENT_NAME,
            "plan_diversion_route",
            {"candidate_id": candidate_id, "variant": variant},
            route,
            t.ms,
            decision="REPLAN_ROUTE" if variant > 1 else "PLAN_ROUTE",
        )
        return route

    @agent.tool
    def calculate_route_energy_requirement(ctx: RunContext[AgentDeps], route_id: str) -> dict:
        """Energy required for a route versus remaining energy above reserve."""
        evaluation = _evaluation(ctx, route_id)
        state = _state(ctx)
        estimate = ctx.deps.state.energy_estimate
        assert estimate is not None
        with timed() as t:
            check = calculate_route_energy(evaluation.route, state, estimate, ctx.deps.cfg)
        evaluation.energy = check
        ctx.deps.logger.log_tool(
            AGENT_NAME,
            "calculate_route_energy_requirement",
            {"route_id": route_id},
            check,
            t.ms,
            decision="PASS" if check.safe else "FAIL",
            reason_code=None if check.safe else ReasonCode.INSUFFICIENT_ROUTE_ENERGY,
        )
        return check.model_dump()

    @agent.tool
    def check_route_obstacle_clearance(ctx: RunContext[AgentDeps], route_id: str) -> dict:
        """Clearance between the route and known structures."""
        evaluation = _evaluation(ctx, route_id)
        with timed() as t:
            check = check_route_obstacles(evaluation.route, ctx.deps.scenario, ctx.deps.cfg)
        evaluation.obstacles = check
        ctx.deps.logger.log_tool(
            AGENT_NAME,
            "check_route_obstacle_clearance",
            {"route_id": route_id},
            check,
            t.ms,
            decision="PASS" if check.safe else "FAIL",
            reason_code=None if check.safe else ReasonCode.ROUTE_OBSTACLE_INTERSECTION,
        )
        return check.model_dump()

    @agent.tool
    def check_route_restricted_airspace(ctx: RunContext[AgentDeps], route_id: str) -> dict:
        """Whether the route crosses an active restricted area."""
        evaluation = _evaluation(ctx, route_id)
        with timed() as t:
            check = check_restricted_airspace(evaluation.route, ctx.deps.scenario)
        evaluation.restrictions = check
        ctx.deps.logger.log_tool(
            AGENT_NAME,
            "check_route_restricted_airspace",
            {"route_id": route_id},
            check,
            t.ms,
            decision="PASS" if check.safe else "FAIL",
            reason_code=None if check.safe else ReasonCode.RESTRICTED_AIRSPACE_INTERSECTION,
        )
        return check.model_dump()

    @agent.tool
    def calculate_minimum_uav_separation(ctx: RunContext[AgentDeps], route_id: str) -> dict:
        """Minimum predicted separation against nearby UAV traffic along a route."""
        evaluation = _evaluation(ctx, route_id)
        with timed() as t:
            check = calculate_minimum_separation(
                evaluation.route, _state(ctx), ctx.deps.scenario, ctx.deps.cfg
            )
        evaluation.separation = check
        decision = "PASS" if check.safe else ("REPLAN_ROUTE" if check.data_available else "ESCALATE")
        ctx.deps.logger.log_tool(
            AGENT_NAME,
            "calculate_minimum_separation",
            {"route_id": route_id},
            check,
            t.ms,
            decision=decision,
            reason_code=check.reason_codes[0] if check.reason_codes else None,
        )
        return check.model_dump()

    @agent.tool
    def validate_candidate_descent_corridor(ctx: RunContext[AgentDeps], route_id: str) -> dict:
        """Whether the vertical corridor over the touchdown point is clear."""
        evaluation = _evaluation(ctx, route_id)
        candidate = _candidate(ctx, evaluation.route.candidate_id)
        with timed() as t:
            check = validate_descent_corridor(candidate, ctx.deps.scenario, ctx.deps.cfg)
        evaluation.descent = check
        _finalise(evaluation)
        ctx.deps.logger.log_tool(
            AGENT_NAME,
            "validate_descent_corridor",
            {"route_id": route_id},
            check,
            t.ms,
            decision="PASS" if check.safe else "FAIL",
            reason_code=None if check.safe else ReasonCode.DESCENT_CORRIDOR_BLOCKED,
        )
        # Keyed by route as well as candidate so a caller tracking routes can
        # match this result to the route it just checked.
        return {**check.model_dump(), "route_id": route_id}

    @agent.tool
    def get_route_verification_summary(ctx: RunContext[AgentDeps], route_id: str) -> dict:
        """Which of the six mandatory checks have run for a route, and their verdicts."""
        evaluation = _evaluation(ctx, route_id)
        _finalise(evaluation)
        return {
            "route_id": route_id,
            "candidate_id": evaluation.route.candidate_id,
            "variant": evaluation.route.variant,
            "energy": evaluation.energy.safe if evaluation.energy else None,
            "obstacles": evaluation.obstacles.safe if evaluation.obstacles else None,
            "restricted_airspace": evaluation.restrictions.safe if evaluation.restrictions else None,
            "separation": evaluation.separation.safe if evaluation.separation else None,
            "descent_corridor": evaluation.descent.safe if evaluation.descent else None,
            "all_checks_passed": evaluation.passed,
            "reason_codes": [c.value for c in evaluation.reason_codes],
        }

    return agent


def _finalise(evaluation: RouteEvaluation) -> None:
    """Recompute a route's overall verdict from whichever checks have run."""
    checks = [
        (evaluation.energy, ReasonCode.INSUFFICIENT_ROUTE_ENERGY),
        (evaluation.obstacles, ReasonCode.ROUTE_OBSTACLE_INTERSECTION),
        (evaluation.restrictions, ReasonCode.RESTRICTED_AIRSPACE_INTERSECTION),
        (evaluation.separation, None),
        (evaluation.descent, ReasonCode.DESCENT_CORRIDOR_BLOCKED),
    ]
    codes: list[ReasonCode] = []
    complete = True
    for check, code in checks:
        if check is None:
            complete = False
            continue
        if not check.safe:
            if code is not None:
                codes.append(code)
            else:  # separation carries its own reason codes
                codes.extend(getattr(check, "reason_codes", []))
    evaluation.reason_codes = codes
    evaluation.passed = complete and not codes


def _store(ctx: RunContext[AgentDeps], evaluation: RouteEvaluation) -> None:
    ctx.deps.state.route_evaluations = [
        e for e in ctx.deps.state.route_evaluations if e.route.route_id != evaluation.route.route_id
    ] + [evaluation]


def _evaluation(ctx: RunContext[AgentDeps], route_id: str) -> RouteEvaluation:
    for evaluation in ctx.deps.state.route_evaluations:
        if evaluation.route.route_id == route_id:
            return evaluation
    known = [e.route.route_id for e in ctx.deps.state.route_evaluations]
    raise ValueError(f"Unknown route {route_id!r}. Plan a route first. Known routes: {known}")


def _candidate(ctx: RunContext[AgentDeps], candidate_id: str) -> LandingCandidate:
    for candidate in ctx.deps.state.candidates:
        if candidate.candidate_id == candidate_id:
            return candidate
    raise ValueError(f"Unknown candidate {candidate_id!r}.")


def _state(ctx: RunContext[AgentDeps]):
    state = ctx.deps.state.operational_state
    if state is None:
        raise RuntimeError("Operational state has not been prepared.")
    return state


def build_prompt(candidates: list[LandingCandidate], required_separation_m: float) -> str:
    lines = [
        f"- {c.candidate_id} | {c.name} | {c.feature_type} | {c.tier}" for c in candidates
    ]
    return f"""\
Agent 2 cleared the following candidates on current ground conditions. Verify whether
the aircraft can safely reach one of them.

GROUND-VALID CANDIDATES ({len(candidates)}):
{chr(10).join(lines)}

Required minimum UAV separation: {required_separation_m:.0f} m.

Work through the candidates in the order given. For each, plan a route and run all six
checks. Replan the path when separation, obstacles, or restricted airspace fail; move
to the next candidate when energy fails.
"""
