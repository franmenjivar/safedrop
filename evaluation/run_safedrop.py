"""Run the full agentic SafeDrop workflow over every scenario."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio

from app.config import SafeDropConfig, get_config
from app.orchestration.workflow import run_workflow
from app.services.scenario_loader import list_scenarios, load_scenario_cached
from evaluation.evaluators import ScenarioScore, score_run


async def run_all_safedrop(
    scenario_dirs: list[str] | None = None,
    cfg: SafeDropConfig | None = None,
    concurrency: int = 2,
) -> list[ScenarioScore]:
    cfg = cfg or get_config()
    dirs = scenario_dirs or [meta["dir"] for meta in list_scenarios()]
    semaphore = asyncio.Semaphore(concurrency)

    async def one(directory: str) -> ScenarioScore | None:
        scenario = load_scenario_cached(directory)
        if scenario.ground_truth is None:
            return None
        async with semaphore:
            state = await run_workflow(directory, cfg=cfg)
        summary = _trajectory_summary(state.run_id, state.scenario_id)
        return score_run(
            state,
            scenario.ground_truth,
            system="safedrop",
            telemetry=summary,
            scenario=scenario,
        )

    results = await asyncio.gather(*(one(d) for d in dirs))
    return [r for r in results if r is not None]


def _trajectory_summary(run_id: str, scenario_id: str) -> dict:
    """Re-read the trajectory file to recover token and tool-call counts."""
    import json

    from app.config import TRAJECTORY_DIR

    path = TRAJECTORY_DIR / f"{run_id}.jsonl"
    if not path.exists():
        return {}
    records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    agent_records = [r for r in records if r.get("kind") == "agent_output"]
    return {
        "tool_calls": sum(1 for r in records if r.get("kind") == "tool_call"),
        "agent_latency_ms": sum(r.get("latency_ms", 0.0) for r in agent_records),
        "input_tokens": sum(r.get("input_tokens", 0) for r in agent_records),
        "output_tokens": sum(r.get("output_tokens", 0) for r in agent_records),
    }


if __name__ == "__main__":
    for score in asyncio.run(run_all_safedrop()):
        print(f"{score.scenario_id}: {score.actual_action} -> safe={score.safe_resolution}")
