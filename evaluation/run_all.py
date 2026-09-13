"""One-command benchmark: baseline vs SafeDrop over the fixed scenario set.

    python evaluation/run_all.py --mode offline

Writes ``results/baseline.json``, ``results/safedrop.json``,
``results/comparison.md``, and per-run trajectories under ``trajectories/``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import asyncio
import json
from datetime import UTC, datetime

from app.config import RESULTS_DIR, load_config
from app.services.scenario_loader import list_scenarios
from evaluation.metrics import comparison_table, summarise
from evaluation.run_baseline import run_all_baseline
from evaluation.run_safedrop import run_all_safedrop


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the SafeDrop benchmark.")
    parser.add_argument(
        "--mode",
        choices=["offline", "hybrid", "live"],
        default="offline",
        help="Context mode. Offline uses repository fixtures only and is the benchmark default.",
    )
    parser.add_argument("--scenarios", nargs="*", default=None, help="Scenario directory names.")
    parser.add_argument("--baseline-only", action="store_true")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument(
        "--raw-telemetry",
        action="store_true",
        help="Experiment: give Agent 1 raw telemetry rows instead of compressed state.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    cfg = load_config()
    cfg.context.default_mode = args.mode
    cfg.agents.raw_telemetry_mode = args.raw_telemetry
    if args.mode == "offline":
        cfg.apis.austin_gis_enabled = False
        cfg.apis.overpass_enabled = False
        cfg.apis.open_meteo_enabled = False

    dirs = args.scenarios or [meta["dir"] for meta in list_scenarios()]
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Running {len(dirs)} scenarios in {args.mode} mode.\n")

    baseline_scores = run_all_baseline(dirs, cfg)
    baseline = summarise("baseline", baseline_scores)
    _write(RESULTS_DIR / "baseline.json", baseline)
    for score in baseline_scores:
        print(f"  baseline  {score.scenario_id:12s} {str(score.actual_action):20s} safe={score.safe_resolution}")

    if args.baseline_only:
        print("\nBaseline only; skipping the agentic run.")
        return

    print()
    safedrop_scores = await run_all_safedrop(dirs, cfg, concurrency=args.concurrency)
    label = "safedrop-raw-telemetry" if args.raw_telemetry else "safedrop"
    safedrop = summarise(label, safedrop_scores)
    _write(RESULTS_DIR / f"{label}.json", safedrop)
    for score in safedrop_scores:
        print(f"  safedrop  {score.scenario_id:12s} {str(score.actual_action):20s} safe={score.safe_resolution}")

    table = comparison_table(baseline, safedrop)
    report = (
        f"# SafeDrop benchmark\n\n"
        f"- Generated: {datetime.now(UTC).isoformat()}\n"
        f"- Context mode: `{args.mode}`\n"
        f"- Agent model: `{cfg.agents.model}`\n"
        f"- Agent 1 input: {'raw telemetry rows' if args.raw_telemetry else 'compressed operational state'}\n"
        f"- Scenarios: {len(dirs)}\n\n"
        f"```\n{table}\n```\n\n"
        f"## Per scenario\n\n"
        f"| Scenario | Expected | Baseline | SafeDrop | Baseline site | SafeDrop site |\n"
        f"|---|---|---|---|---|---|\n"
    )
    by_id = {s.scenario_id: s for s in safedrop_scores}
    for score in baseline_scores:
        other = by_id.get(score.scenario_id)
        report += (
            f"| {score.scenario_id} | {score.expected_action} | "
            f"{'PASS' if score.safe_resolution else 'FAIL'} | "
            f"{'PASS' if other and other.safe_resolution else 'FAIL'} | "
            f"{score.actual_candidate_id or '—'} | {(other.actual_candidate_id if other else None) or '—'} |\n"
        )
    (RESULTS_DIR / f"comparison{'-raw' if args.raw_telemetry else ''}.md").write_text(report)

    print("\n" + table)
    print(f"\nWrote {RESULTS_DIR}/comparison{'-raw' if args.raw_telemetry else ''}.md")

    # A headline without its interval invites the wrong reading, so the
    # analysis is part of the benchmark rather than an optional extra.
    if not args.raw_telemetry:
        from evaluation.analysis import main as analyse

        print()
        analyse()


def _write(path, summary) -> None:
    path.write_text(json.dumps(summary.model_dump(mode="json"), indent=2) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
