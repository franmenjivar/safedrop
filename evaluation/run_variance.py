"""Repeat the SafeDrop benchmark N times and report run-to-run variance.

Agent and vision behaviour are non-deterministic. A single run is an anecdote —
this is what lets a number be quoted honestly, with a spread rather than a
point estimate.

    python evaluation/run_variance.py --runs 3
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import asyncio
import json
import statistics
from datetime import UTC, datetime

from app.config import RESULTS_DIR, load_config
from app.services.scenario_loader import list_scenarios
from evaluation.metrics import summarise
from evaluation.run_safedrop import run_all_safedrop

TRACKED = [
    ("safe_contingency_resolution_rate", "SCRR", "rate"),
    ("contingency_action_accuracy", "Action accuracy", "rate"),
    ("ground_hazard_avoidance_rate", "Ground hazard avoidance", "rate"),
    ("airspace_conflict_avoidance_rate", "Airspace conflict avoidance", "rate"),
    ("replanning_success_rate", "Replanning success", "rate"),
    ("correct_abstention_rate", "Correct abstention", "rate"),
    ("false_safe_recommendation_rate", "False-safe rate", "rate"),
    ("image_observation_accuracy", "Image observation accuracy", "rate"),
    ("image_hazard_false_negative_rate", "Image hazard false-negative rate", "rate"),
    ("mean_latency_ms", "Mean latency (ms)", "number"),
    ("total_input_tokens", "Input tokens", "number"),
    ("total_output_tokens", "Output tokens", "number"),
]


async def main() -> None:
    parser = argparse.ArgumentParser(description="Repeat the benchmark and report variance.")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--scenarios", nargs="*", default=None)
    args = parser.parse_args()

    cfg = load_config()
    cfg.context.default_mode = "offline"
    cfg.apis.austin_gis_enabled = False
    cfg.apis.overpass_enabled = False
    cfg.apis.open_meteo_enabled = False

    dirs = args.scenarios or [meta["dir"] for meta in list_scenarios()]
    summaries = []
    per_scenario_failures: dict[str, int] = {}
    vision_misses: list[str] = []

    for i in range(args.runs):
        print(f"--- run {i + 1} / {args.runs} ---", flush=True)
        scores = await run_all_safedrop(dirs, cfg, concurrency=args.concurrency)
        summary = summarise(f"safedrop-run-{i + 1}", scores)
        summaries.append(summary)
        for s in scores:
            if not s.safe_resolution:
                per_scenario_failures[s.scenario_id] = per_scenario_failures.get(s.scenario_id, 0) + 1
                print(
                    f"      ^ {s.scenario_id} [{s.failure_family}] "
                    f"expected {s.expected_action}, got {s.actual_action}"
                    + (f", site {s.actual_candidate_id}" if s.actual_candidate_id else "")
                    + (f" — {'; '.join(s.notes)}" if s.notes else ""),
                    flush=True,
                )
            if s.image_hazard_false_negatives:
                vision_misses.append(f"run {i + 1}: {s.scenario_id}")
            print(f"    {s.scenario_id:12s} {str(s.actual_action):20s} safe={s.safe_resolution}", flush=True)

    lines = [f"# SafeDrop benchmark variance — {args.runs} runs", ""]
    lines.append(f"- Generated: {datetime.now(UTC).isoformat()}")
    lines.append(f"- Model: `{cfg.agents.model}`")
    lines.append(f"- Scenarios per run: {len(dirs)}")
    lines.append("")
    # Pooled interval across all runs — the strongest honest statement available.
    from evaluation.analysis import wilson

    total = sum(s.scenarios for s in summaries)
    passed = sum(round(s.safe_contingency_resolution_rate * s.scenarios) for s in summaries)
    lo, hi = wilson(passed, total)
    lines.append(
        f"**Pooled across all runs: {passed}/{total} scenario executions resolved safely — "
        f"95% Wilson interval {lo:.0%} to {hi:.0%}.** A single run of {summaries[0].scenarios} "
        "cannot separate a very good system from a lucky one; repetition is what narrows that."
    )
    lines.append("")
    lines.append("| Metric | Min | Median | Max |")
    lines.append("|---|---:|---:|---:|")

    for attr, label, kind in TRACKED:
        values = [getattr(s, attr) for s in summaries if getattr(s, attr) is not None]
        if not values:
            lines.append(f"| {label} | n/a | n/a | n/a |")
            continue
        fmt = (lambda v: f"{v:.0%}") if kind == "rate" else (lambda v: f"{v:,.0f}")
        lines.append(
            f"| {label} | {fmt(min(values))} | {fmt(statistics.median(values))} | {fmt(max(values))} |"
        )

    # Per-family stability across runs: a family that is only ever right by
    # luck shows up here as a rate that moves between runs.
    families = sorted({f for s in summaries for f in s.family_coverage})
    if families:
        lines.append("## SCRR by family, per run")
        lines.append("")
        lines.append("| Family | n | " + " | ".join(f"run {i+1}" for i in range(len(summaries))) + " |")
        lines.append("|---|---:|" + "---:|" * len(summaries))
        for family in families:
            n = summaries[0].family_coverage.get(family, 0)
            cells = " | ".join(f"{s.family_scrr.get(family, 0):.0%}" for s in summaries)
            lines.append(f"| {family.replace('_', ' ').title()} | {n} | {cells} |")
        lines.append("")

    lines.append("")
    if per_scenario_failures:
        lines.append("## Scenarios that failed at least once")
        lines.append("")
        for scenario_id, count in sorted(per_scenario_failures.items()):
            lines.append(f"- `{scenario_id}` — failed {count} / {args.runs} runs")
    else:
        lines.append(f"All {len(dirs)} scenarios resolved safely in all {args.runs} runs.")
    lines.append("")

    lines.append("## Vision hazard false negatives")
    lines.append("")
    lines.extend([f"- {m}" for m in vision_misses] or ["- None observed."])
    lines.append("")
    lines.append("")
    lines.append(
        "Rates that move between runs are the ones to distrust. A metric that is stable at "
        "100% across every run is either genuinely solid or the scenario never exercised the "
        "path — the per-family table above and `results/analysis.md` say which."
    )
    lines.append("")
    lines.append(
        "A hazard false negative is an image containing people, vehicles, or animals that "
        "the vision tool reported as clear. It is the visual error that matters, and it is "
        "tracked separately from count accuracy because being off by one person is not the "
        "same failure as missing all of them."
    )

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "variance.md").write_text("\n".join(lines) + "\n")
    (RESULTS_DIR / "variance.json").write_text(
        json.dumps([s.model_dump(mode="json") for s in summaries], indent=2) + "\n"
    )
    print("\n" + "\n".join(lines))


if __name__ == "__main__":
    asyncio.run(main())
