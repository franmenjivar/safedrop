"""Scripted stand-ins for the three agents.

These replay a fixed, rule-driven tool sequence in place of a language model.
They exist for two reasons:

* **Testing.** The orchestrator, tool plumbing, reconciliation, gateway, and
  briefing can be exercised in CI with no API key and no network.
* **Demo rehearsal.** Setting ``SAFEDROP_AGENTS=scripted`` runs the whole
  console end to end without spending tokens or depending on a model being up.

**They fail SCENARIO-18, and that is the useful part.** That scenario needs the
inference that disagreeing heading sources make every *downstream tool output*
suspect, because the tools were fed a position that may be wrong. Threshold
rules cannot express "the numbers I was given are not trustworthy"; they can
only compare those numbers to limits. The stand-ins confidently answer
CONTINUE. It is the clearest single demonstration of what the agentic layer is
for.

They are emphatically *not* agents. They call every tool in a predetermined
order and branch on the results with hard-coded rules. Anything a real agent
contributes — deciding which evidence is worth gathering, weighing a degraded
subsystem against a feasible alternative, recognising that a failed separation
check means replan rather than abandon — is absent here. Runs made in scripted
mode are labelled as such and must never be reported as agent performance.
"""

from __future__ import annotations

from typing import Callable

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel


def _tool_returns(messages: list[ModelMessage]) -> dict[str, list[dict]]:
    """Collect tool results seen so far, keyed by tool name."""
    seen: dict[str, list[dict]] = {}
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if isinstance(part, ToolReturnPart):
                content = part.content
                if hasattr(content, "model_dump"):
                    content = content.model_dump(mode="json")
                seen.setdefault(part.tool_name, []).append(
                    content if isinstance(content, dict) else {"value": content}
                )
    return seen


def _output_tool(info: AgentInfo) -> str:
    return info.output_tools[0].name


def scripted(steps: Callable[[dict[str, list[dict]], AgentInfo], list[ToolCallPart]]) -> FunctionModel:
    """Build a FunctionModel from a function that picks the next tool calls."""

    def run(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=steps(_tool_returns(messages), info))

    return FunctionModel(run)


# --------------------------------------------------------------------------


def _rule_based_action(
    destination: dict, base: dict, hub: dict, battery_state: str | None
) -> str:
    """The threshold logic a scripted stand-in uses in place of judgement."""
    if destination.get("feasible") and battery_state != "CRITICAL":
        return "CONTINUE"
    if base.get("feasible"):
        return "RETURN_TO_BASE"
    if hub.get("feasible"):
        return "DIVERT_TO_SAFE_HUB"
    return "LAND_IMMEDIATELY"


def contingency_model(
    action: str | None = None, battery_state: str | None = None
) -> FunctionModel:
    """Agent 1 stand-in: check energy, test every destination, then apply rules.

    ``action`` pins the outcome (used by tests). Left as ``None``, the action is
    derived from the deterministic feasibility results.
    """

    def steps(seen: dict[str, list[dict]], info: AgentInfo) -> list[ToolCallPart]:
        if "calculate_remaining_energy" not in seen:
            return [ToolCallPart("calculate_remaining_energy", {})]
        for tool in (
            "check_destination_feasibility",
            "check_return_to_base_feasibility",
            "check_alternate_hub_feasibility",
        ):
            if tool not in seen and any(t.name == tool for t in info.function_tools):
                return [ToolCallPart(tool, {})]
        if "calculate_reachability_envelope" not in seen:
            return [ToolCallPart("calculate_reachability_envelope", {})]

        destination = seen.get("check_destination_feasibility", [{}])[0]
        base = seen.get("check_return_to_base_feasibility", [{}])[0]
        hub = seen.get("check_alternate_hub_feasibility", [{}])[0]
        chosen = action or _rule_based_action(destination, base, hub, battery_state)
        return [
            ToolCallPart(
                _output_tool(info),
                {
                    "action": chosen,
                    "severity": "CRITICAL" if chosen == "LAND_IMMEDIATELY" else "CAUTION",
                    "emergency_type": "BATTERY_DEGRADATION",
                    "mission_completion_possible": bool(destination.get("feasible")),
                    "return_to_base_possible": bool(base.get("feasible")),
                    "alternate_hub_possible": bool(hub.get("feasible")),
                    "forced_landing_required": chosen == "LAND_IMMEDIATELY",
                    "reason_codes": _reason_codes(chosen, destination, base),
                    "rationale": "Scripted stand-in: threshold rules over deterministic feasibility results.",
                },
            )
        ]

    return scripted(steps)


def _reason_codes(action: str, destination: dict, base: dict) -> list[str]:
    if action == "CONTINUE":
        return ["MISSION_ENERGY_SUFFICIENT"]
    codes = ["INSUFFICIENT_MISSION_ENERGY"] if not destination.get("feasible") else []
    if action == "RETURN_TO_BASE":
        return codes + ["RTB_ENERGY_SUFFICIENT"]
    if action == "DIVERT_TO_SAFE_HUB":
        return codes + ["HUB_ENERGY_SUFFICIENT"]
    return codes + (["INSUFFICIENT_RTB_ENERGY"] if not base.get("feasible") else [])


def elz_model(candidate_ids: list[str]) -> FunctionModel:
    """Agent 2: gather ground state and imagery per candidate, then classify."""

    def steps(seen: dict[str, list[dict]], info: AgentInfo) -> list[ToolCallPart]:
        done = {
            tool: {r.get("candidate_id") for r in results}
            for tool, results in seen.items()
        }
        for tool in (
            "get_scenario_ground_state",
            "inspect_landing_zone_image",
            "check_hard_constraints",
        ):
            for candidate_id in candidate_ids:
                if candidate_id not in done.get(tool, set()):
                    return [ToolCallPart(tool, {"candidate_id": candidate_id})]

        buckets: dict[str, list[dict]] = {"VALID": [], "REJECTED": [], "UNVERIFIED": []}
        for result in seen.get("check_hard_constraints", []):
            if result["verdict"] not in buckets:
                continue  # PREREQUISITES_NOT_MET carries no verdict
            buckets[result["verdict"]].append(
                {
                    "candidate_id": result["candidate_id"],
                    "status": result["verdict"],
                    "reason_codes": result["reason_codes"] + result["unverified_reason_codes"],
                    "evidence": result["evidence"],
                    "ground_confidence": result["ground_confidence"],
                }
            )
        return [
            ToolCallPart(
                _output_tool(info),
                {
                    "valid_candidates": buckets["VALID"],
                    "rejected_candidates": buckets["REJECTED"],
                    "unverified_candidates": buckets["UNVERIFIED"],
                    "summary": "Scripted stand-in: deterministic constraint results, no interpretation.",
                },
            )
        ]

    return scripted(steps)


ROUTE_CHECKS = [
    "calculate_route_energy_requirement",
    "check_route_obstacle_clearance",
    "check_route_restricted_airspace",
    "calculate_minimum_uav_separation",
    "validate_candidate_descent_corridor",
]


def diversion_model(candidate_ids: list[str], max_variant: int = 5) -> FunctionModel:
    """Agent 3: plan, verify, and replan on a route-level failure."""

    def steps(seen: dict[str, list[dict]], info: AgentInfo) -> list[ToolCallPart]:
        planned = seen.get("plan_diversion_route", [])
        if not planned:
            return [ToolCallPart("plan_diversion_route", {"candidate_id": candidate_ids[0], "variant": 1})]

        current = planned[-1]
        route_id = current["route_id"]
        results = {
            tool: {r["route_id"]: r for r in seen.get(tool, []) if "route_id" in r}
            for tool in ROUTE_CHECKS
        }
        for tool in ROUTE_CHECKS:
            if route_id not in results[tool]:
                return [ToolCallPart(tool, {"route_id": route_id})]

        energy_ok = results["calculate_route_energy_requirement"][route_id]["safe"]
        path_failures = [
            tool
            for tool in (
                "check_route_obstacle_clearance",
                "check_route_restricted_airspace",
                "calculate_minimum_uav_separation",
            )
            if not results[tool][route_id]["safe"]
        ]
        descent_ok = results["validate_candidate_descent_corridor"][route_id]["safe"]

        if energy_ok and not path_failures and descent_ok:
            return [
                ToolCallPart(
                    _output_tool(info),
                    {
                        "selected_candidate_id": current["candidate_id"],
                        "selected_route_id": route_id,
                        "verified": True,
                        "attempted_route_ids": [p["route_id"] for p in planned],
                        "replan_count": sum(1 for p in planned if p["variant"] > 1),
                        "reason_codes": ["ROUTE_VERIFIED"],
                        "rationale": "Scripted stand-in: first route passing all six checks.",
                    },
                )
            ]

        # A path-level failure is worth replanning; an energy failure is not.
        if path_failures and energy_ok and current["variant"] < max_variant:
            return [
                ToolCallPart(
                    "plan_diversion_route",
                    {"candidate_id": current["candidate_id"], "variant": current["variant"] + 1},
                )
            ]

        remaining = [c for c in candidate_ids if c not in {p["candidate_id"] for p in planned}]
        if remaining:
            return [ToolCallPart("plan_diversion_route", {"candidate_id": remaining[0], "variant": 1})]

        return [
            ToolCallPart(
                _output_tool(info),
                {
                    "selected_candidate_id": None,
                    "selected_route_id": None,
                    "verified": False,
                    "attempted_route_ids": [p["route_id"] for p in planned],
                    "replan_count": sum(1 for p in planned if p["variant"] > 1),
                    "reason_codes": ["NO_VERIFIED_ROUTE"],
                    "rationale": "Scripted stand-in: no route passed every check.",
                },
            )
        ]

    return scripted(steps)


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------


def is_scripted(cfg) -> bool:
    return cfg.agents.mode == "scripted"


def apply(agent, model: FunctionModel):
    """Point an already-built agent at a scripted model, keeping its tools."""
    agent.model = model
    return agent
