"""Aggregate metrics across scored scenarios."""

from __future__ import annotations

import statistics

from pydantic import BaseModel, Field

from app.models.enums import ContingencyAction
from evaluation.evaluators import ScenarioScore


class MetricSummary(BaseModel):
    system: str
    scenarios: int

    safe_contingency_resolution_rate: float
    contingency_action_accuracy: float
    ground_hazard_avoidance_rate: float | None = None
    airspace_conflict_avoidance_rate: float | None = None
    false_safe_recommendation_rate: float
    correct_abstention_rate: float | None = None
    replanning_success_rate: float | None = None
    energy_feasibility_accuracy: float | None = None

    mean_latency_ms: float = 0.0
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    mean_tool_calls: float = 0.0

    #: Scenario count and SCRR per failure family — a benchmark weighted toward
    #: one family measures one thing well and everything else not at all.
    family_coverage: dict[str, int] = Field(default_factory=dict)
    family_scrr: dict[str, float] = Field(default_factory=dict)
    abstention_cases: int = 0
    abstention_correct: int = 0

    image_observation_accuracy: float | None = None
    image_hazard_false_negative_rate: float | None = None
    vision_source: str | None = None

    per_scenario: list[ScenarioScore] = Field(default_factory=list)


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))
    return round(ordered[idx], 1)


def summarise(system: str, scores: list[ScenarioScore]) -> MetricSummary:
    n = len(scores)
    landing = [s for s in scores if s.expected_action is ContingencyAction.LAND_IMMEDIATELY]
    with_site = [s for s in landing if s.actual_candidate_id is not None]
    with_separation = [s for s in landing if s.separation_verified is not None]
    replan = [s for s in scores if s.replan_expected]
    should_abstain = [s for s in scores if s.expects_abstention]
    latencies = [s.latency_ms for s in scores]
    images = sum(s.images_scored for s in scores)
    vision_sources = {s.vision_source for s in scores if s.vision_source}

    families: dict[str, list] = {}
    for s in scores:
        families.setdefault(s.failure_family, []).append(s)
    abstain_cases = should_abstain

    return MetricSummary(
        system=system,
        scenarios=n,
        safe_contingency_resolution_rate=_rate(sum(s.safe_resolution for s in scores), n) or 0.0,
        contingency_action_accuracy=_rate(sum(s.action_correct for s in scores), n) or 0.0,
        ground_hazard_avoidance_rate=_rate(
            sum(not s.selected_known_unsafe for s in with_site), len(with_site)
        ),
        airspace_conflict_avoidance_rate=_rate(
            sum(bool(s.separation_verified) for s in with_separation), len(with_separation)
        ),
        false_safe_recommendation_rate=_rate(sum(s.false_safe for s in scores), n) or 0.0,
        correct_abstention_rate=_rate(
            sum(s.safe_resolution for s in should_abstain), len(should_abstain)
        ),
        replanning_success_rate=_rate(
            sum(bool(s.replan_successful) for s in replan), len(replan)
        ),
        energy_feasibility_accuracy=_rate(
            sum(bool(s.energy_verified) for s in with_site), len(with_site)
        ),
        mean_latency_ms=round(statistics.fmean(latencies), 1) if latencies else 0.0,
        p50_latency_ms=_percentile(latencies, 0.5),
        p95_latency_ms=_percentile(latencies, 0.95),
        max_latency_ms=round(max(latencies), 1) if latencies else 0.0,
        total_input_tokens=sum(s.input_tokens for s in scores),
        total_output_tokens=sum(s.output_tokens for s in scores),
        mean_tool_calls=round(statistics.fmean([s.tool_calls for s in scores]), 2) if scores else 0.0,
        family_coverage={k: len(v) for k, v in sorted(families.items())},
        family_scrr={
            k: round(sum(x.safe_resolution for x in v) / len(v), 4)
            for k, v in sorted(families.items())
        },
        abstention_cases=len(abstain_cases),
        abstention_correct=sum(s.safe_resolution for s in abstain_cases),
        image_observation_accuracy=_rate(sum(s.image_counts_exact for s in scores), images),
        image_hazard_false_negative_rate=_rate(
            sum(s.image_hazard_false_negatives for s in scores), images
        ),
        vision_source=", ".join(sorted(vision_sources)) or None,
        per_scenario=scores,
    )


#: (label, attribute, kind) — "rate" renders as a percentage, "number" as-is.
COMPARISON_ROWS = [
    ("Safe Contingency Resolution Rate", "safe_contingency_resolution_rate", "rate"),
    ("Contingency Action Accuracy", "contingency_action_accuracy", "rate"),
    ("Ground Hazard Avoidance Rate", "ground_hazard_avoidance_rate", "rate"),
    ("Airspace Conflict Avoidance Rate", "airspace_conflict_avoidance_rate", "rate"),
    ("Replanning Success Rate", "replanning_success_rate", "rate"),
    ("Correct Abstention Rate", "correct_abstention_rate", "rate"),
    ("False-Safe Recommendation Rate", "false_safe_recommendation_rate", "rate"),
    ("Image Observation Accuracy", "image_observation_accuracy", "rate"),
    ("Image Hazard False-Negative Rate", "image_hazard_false_negative_rate", "rate"),
    ("Mean latency (ms)", "mean_latency_ms", "number"),
    ("P95 latency (ms)", "p95_latency_ms", "number"),
    ("Total input tokens", "total_input_tokens", "number"),
    ("Total output tokens", "total_output_tokens", "number"),
    ("Mean tool calls / scenario", "mean_tool_calls", "number"),
]


def comparison_table(baseline: MetricSummary, safedrop: MetricSummary) -> str:
    def fmt(value, kind: str) -> str:
        if value is None:
            return "n/a"
        if kind == "rate":
            return f"{value:.0%}"
        if isinstance(value, float):
            return f"{value:,.1f}"
        return f"{value:,}"

    width = max(len(label) for label, _, _ in COMPARISON_ROWS) + 2
    right = max(12, len(safedrop.system) + 2)
    lines = [f"{'Metric'.ljust(width)}{'Baseline'.rjust(right)}{'SafeDrop'.rjust(right)}"]
    lines.append("-" * (width + right * 2))
    for label, attr, kind in COMPARISON_ROWS:
        lines.append(
            f"{label.ljust(width)}{fmt(getattr(baseline, attr), kind).rjust(right)}"
            f"{fmt(getattr(safedrop, attr), kind).rjust(right)}"
        )
    return "\n".join(lines)
