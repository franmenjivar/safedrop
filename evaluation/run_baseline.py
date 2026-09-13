"""Run the conventional deterministic baseline over every scenario."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import SafeDropConfig, get_config
from app.services.scenario_loader import list_scenarios, load_scenario_cached
from baseline.conventional import run_baseline
from evaluation.evaluators import ScenarioScore, score_run


def run_all_baseline(
    scenario_dirs: list[str] | None = None, cfg: SafeDropConfig | None = None
) -> list[ScenarioScore]:
    cfg = cfg or get_config()
    dirs = scenario_dirs or [meta["dir"] for meta in list_scenarios()]
    scores: list[ScenarioScore] = []
    for directory in dirs:
        scenario = load_scenario_cached(directory)
        if scenario.ground_truth is None:
            continue
        state = run_baseline(directory, cfg)
        scores.append(score_run(state, scenario.ground_truth, system="baseline"))
    return scores


if __name__ == "__main__":
    for score in run_all_baseline():
        print(f"{score.scenario_id}: {score.actual_action} -> safe={score.safe_resolution}")
