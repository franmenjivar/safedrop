"""Produce a trajectory that captures a tool refusing and the agent correcting.

In normal runs this does not happen any more: the prompt tells Agent 2 to work
one candidate at a time, so it never asks for a verdict before its evidence has
arrived. That is the right outcome and it leaves the trajectories with no
example of the guard doing its job.

This script forces the situation deliberately. A scripted Agent 2 calls
``check_hard_constraints`` first, before any evidence exists. The tool refuses
and names what is missing; the scripted model reads that refusal, gathers the
named evidence, and calls the checker again — which is exactly the feedback
loop the guard exists to create.

The output is clearly labelled as a constructed demonstration, not a live agent
run, and is written to a separate run id so it cannot be confused with
benchmark evidence.

    python -m scripts.demo_feedback_trajectory
"""

from __future__ import annotations

import asyncio

from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

import app.agents.scripted as scripted
from app.agents.scripted import _output_tool, _tool_returns
from app.config import load_config
from app.orchestration.workflow import run_workflow

RUN_ID = "DEMO-FEEDBACK-LOOP"


def premature_model(candidate_ids: list[str]) -> FunctionModel:
    """Ask for a verdict first, then do what the refusal tells you."""

    def run(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen = _tool_returns(messages)
        checks = seen.get("check_hard_constraints", [])
        judged = {c["candidate_id"] for c in checks if c.get("verdict") != "PREREQUISITES_NOT_MET"}

        for cid in candidate_ids:
            if cid in judged:
                continue

            refusal = next(
                (c for c in checks
                 if c["candidate_id"] == cid and c.get("verdict") == "PREREQUISITES_NOT_MET"),
                None,
            )
            if refusal is None:
                # Step 1: ask for the verdict with no evidence gathered at all.
                return ModelResponse(
                    parts=[ToolCallPart("check_hard_constraints", {"candidate_id": cid})]
                )

            # Step 2: the tool refused and named what it needs. Fetch that.
            for tool in refusal["missing_evidence"]:
                already = {r.get("candidate_id") for r in seen.get(tool, [])}
                if cid not in already:
                    return ModelResponse(parts=[ToolCallPart(tool, {"candidate_id": cid})])

            # Step 3: everything it named is now present. Ask again.
            return ModelResponse(
                parts=[ToolCallPart("check_hard_constraints", {"candidate_id": cid})]
            )

        buckets: dict[str, list[dict]] = {"VALID": [], "REJECTED": [], "UNVERIFIED": []}
        for result in checks:
            if result["verdict"] not in buckets:
                continue
            buckets[result["verdict"]].append({
                "candidate_id": result["candidate_id"],
                "status": result["verdict"],
                "reason_codes": result["reason_codes"] + result["unverified_reason_codes"],
                "evidence": result["evidence"],
                "ground_confidence": result["ground_confidence"],
            })
        return ModelResponse(parts=[ToolCallPart(_output_tool(info), {
            "valid_candidates": buckets["VALID"],
            "rejected_candidates": buckets["REJECTED"],
            "unverified_candidates": buckets["UNVERIFIED"],
            "summary": "Constructed demonstration of the prerequisite guard.",
        })])

    return FunctionModel(run)


def main() -> None:
    cfg = load_config()
    cfg.agents.mode = "scripted"

    # Scripted mode swaps in stand-ins for all three agents at the orchestrator
    # seam, so that is where Agent 2's stand-in has to be replaced — patching
    # build_agent would just be overridden a moment later.
    original = scripted.elz_model
    scripted.elz_model = lambda candidate_ids: premature_model(candidate_ids)
    try:
        state = asyncio.run(run_workflow("scenario_09", run_id=RUN_ID, cfg=cfg))
    finally:
        scripted.elz_model = original

    print(f"wrote trajectories/{RUN_ID}.jsonl")
    print(f"  workflow: {state.workflow_state}")
    for e in state.elz_context.all_evaluations():
        print(f"  {e.candidate_id}: {e.status}")


if __name__ == "__main__":
    main()
