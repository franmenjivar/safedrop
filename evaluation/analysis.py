"""Critical analysis of the benchmark result.

A headline of 100% invites exactly one question: *on how many scenarios, chosen
by whom?* This module answers it in the report rather than waiting to be asked.

It computes:

* **Confidence intervals.** 6/6 and 15/15 are very different claims, and neither
  is "100%". Wilson score intervals make the sample size visible.
* **Per-family coverage.** A benchmark weighted toward one failure family
  measures one thing well and nothing else.
* **Structural advantage.** How much of the baseline's loss is a capability gap
  versus a scenario mix that the baseline cannot win by construction.
* **Known threats to validity**, stated rather than left for a reader to find.

    python evaluation/analysis.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import math

from app.config import RESULTS_DIR
from evaluation.metrics import MetricSummary


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval — behaves sensibly at 0/n and n/n, unlike normal approximation."""
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def load(name: str) -> MetricSummary | None:
    path = RESULTS_DIR / f"{name}.json"
    if not path.exists():
        return None
    return MetricSummary.model_validate_json(path.read_text())


THREATS = [
    (
        "The scenarios and the system were authored by the same people",
        "Every scenario was written alongside the implementation, so the benchmark tests "
        "paths we knew existed. It cannot detect a failure mode we never imagined. An "
        "independent scenario author would be the single highest-value change to this "
        "evaluation.",
    ),
    (
        "Ground truth encodes our own judgement of 'correct'",
        "SCENARIO-21 says the right answer is DIVERT rather than RETURN. That is a defensible "
        "call, not a fact. Where the correct action is genuinely arguable, scoring rewards "
        "agreement with us.",
    ),
    (
        "The baseline's configuration is ours too",
        "We chose its thresholds, its site-priority rule, and which checks it carries. We tried "
        "to make it a fair representative of documented practice — static geofencing and "
        "obstacle avoidance included — but a vendor would tune it differently.",
    ),
    (
        "Scenario mix drives the headline gap",
        "The baseline cannot re-check ground conditions by construction, so every landing "
        "scenario is one it structurally cannot win. The proportion of landing scenarios "
        "therefore sets the size of the gap. Read the per-family table, not the headline.",
    ),
    (
        "Ground conditions are authored, not observed",
        "Pedestrian and vehicle counts come from scenario files we wrote. Real ground feeds are "
        "noisy, late, and sometimes wrong in ways these fixtures are not. The freshness and "
        "corroboration rules exist because of that, but they have not been tested against real "
        "sensor noise.",
    ),
    (
        "Vision accuracy is measured against its own fixture",
        "Image ground truth is emitted by the same renderer that draws the image. That is a fair "
        "test of the vision model reading a synthetic scene, and no evidence at all about "
        "reading a real photograph.",
    ),
    (
        "Latency excludes everything except agent time",
        "The figure quoted above is model round-trips only. It does not include telemetry "
        "ingest, real GIS calls, or operator reading time, and it is measured on a benchmark "
        "with no contention and no rate limiting.",
    ),
]


def main() -> None:
    baseline = load("baseline")
    safedrop = load("safedrop")
    if baseline is None or safedrop is None:
        print("Run `python evaluation/run_all.py --mode offline` first.")
        return

    n = safedrop.scenarios
    lines: list[str] = ["# Benchmark analysis", ""]
    lines.append(
        f"Read this before quoting any number below as a capability claim. "
        f"The sample is **{n} scenarios**."
    )
    lines.append("")

    # ---- interval on the headline ------------------------------------
    lines.append("## What 100% actually means at this sample size")
    lines.append("")
    lines.append("| System | SCRR | 95% Wilson interval | Reading |")
    lines.append("|---|---:|---|---|")
    for summary in (baseline, safedrop):
        rate = summary.safe_contingency_resolution_rate
        successes = round(rate * summary.scenarios)
        lo, hi = wilson(successes, summary.scenarios)
        reading = (
            f"consistent with anything from {lo:.0%} to {hi:.0%}"
            if hi - lo > 0.15
            else "reasonably tight"
        )
        lines.append(
            f"| {summary.system} | {rate:.0%} ({successes}/{summary.scenarios}) "
            f"| {lo:.0%} – {hi:.0%} | {reading} |"
        )
    lines.append("")
    lo, hi = wilson(round(safedrop.safe_contingency_resolution_rate * n), n)
    lines.append(
        f"A perfect score on {n} scenarios is **not** evidence of a {safedrop.safe_contingency_resolution_rate:.0%} "
        f"success rate. It is evidence that the true rate is unlikely to be below **{lo:.0%}**. "
        "Reporting the interval rather than the point estimate is the honest form of this claim."
    )
    lines.append("")

    # ---- family coverage ---------------------------------------------
    lines.append("## Coverage by failure family")
    lines.append("")
    lines.append("| Family | Scenarios | Baseline SCRR | SafeDrop SCRR |")
    lines.append("|---|---:|---:|---:|")
    base_by_family = dict(baseline.family_scrr)
    for family, count in safedrop.family_coverage.items():
        b = base_by_family.get(family)
        lines.append(
            f"| {family.replace('_', ' ').title()} | {count} "
            f"| {b:.0%} | {safedrop.family_scrr[family]:.0%} |"
            if b is not None
            else f"| {family.replace('_', ' ').title()} | {count} | — | {safedrop.family_scrr[family]:.0%} |"
        )
    lines.append("")
    thin = [f for f, c in safedrop.family_coverage.items() if c < 3]
    if thin:
        lines.append(
            "Families with fewer than three scenarios carry almost no statistical weight: "
            + ", ".join(f"`{f}`" for f in thin)
            + ". Treat their rates as anecdotes."
        )
        lines.append("")

    # ---- abstention ---------------------------------------------------
    lines.append("## Abstention")
    lines.append("")
    if safedrop.abstention_cases:
        lines.append(
            f"{safedrop.abstention_correct} of {safedrop.abstention_cases} scenarios where the "
            "correct output was *no recommendation* were handled correctly. This is the metric "
            "most likely to be flattered by a benchmark: a system tuned to always answer scores "
            "well everywhere else and fails only here."
        )
    else:
        lines.append(
            "**No abstention cases in this run.** Every scenario has a correct answer to find, "
            "so nothing tests whether the system knows when to stop. This is a gap, not a result."
        )
    lines.append("")

    # ---- structural advantage -----------------------------------------
    landing = sum(
        1 for s in safedrop.per_scenario if s.expected_action == "LAND_IMMEDIATELY"
    )
    lines.append("## How much of the gap is structural")
    lines.append("")
    lines.append(
        f"{landing} of {n} scenarios ({landing / n:.0%}) require a forced landing. The baseline "
        "has no dynamic ground re-evaluation *by construction*, so it cannot win those. Change "
        "the mix and the headline moves without either system changing at all."
    )
    lines.append("")
    lines.append(
        "The gap is real — it is the capability being tested — but its *size* is a property of "
        "the benchmark. The defensible claim is the per-family table above and the mechanism, "
        "not the headline delta."
    )
    lines.append("")

    # ---- what did not improve -----------------------------------------
    lines.append("## What did not improve")
    lines.append("")
    lines.append(
        f"Contingency action accuracy is {baseline.contingency_action_accuracy:.0%} for the "
        f"baseline and {safedrop.contingency_action_accuracy:.0%} for SafeDrop. Both systems pick "
        "the right branch. The agentic layer adds nothing to *that* decision, and saying so is "
        "part of the result."
    )
    lines.append("")
    if safedrop.mean_latency_ms:
        lines.append(
            f"SafeDrop costs roughly {safedrop.mean_latency_ms / 1000:.0f} s and "
            f"{(safedrop.total_input_tokens + safedrop.total_output_tokens) / max(1, n):,.0f} tokens "
            f"per scenario. The baseline is effectively instant and free. For a contingency with "
            "seconds of endurance remaining, that latency is a real cost, not a footnote."
        )
        lines.append("")

    # ---- threats -------------------------------------------------------
    lines.append("## Threats to validity")
    lines.append("")
    for title, body in THREATS:
        lines.append(f"**{title}.** {body}")
        lines.append("")

    lines.append(
        "---\n\nRegenerate with `python evaluation/run_all.py --mode offline && "
        "python evaluation/analysis.py`."
    )

    report = "\n".join(lines)
    (RESULTS_DIR / "analysis.md").write_text(report)
    print(report)


if __name__ == "__main__":
    main()
