"""Data-freshness accounting.

SafeDrop's whole claim is about *current* conditions, so every context response
carries an age and is explicitly classified. Stale evidence can never produce a
PASS — at best it produces UNVERIFIED.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.models.ground_context import FreshnessInfo
from app.models.enums import FreshnessStatus


def fixture_freshness(source: str) -> FreshnessInfo:
    """Offline fixtures are fixed scenario truth, not live observations."""
    return FreshnessInfo(source=source, freshness_status=FreshnessStatus.FIXED_SCENARIO)


def scenario_freshness(
    source: str, age_seconds: float | None, threshold_seconds: float
) -> FreshnessInfo:
    """A scenario feed that carries an explicit simulated age."""
    if age_seconds is None:
        return FreshnessInfo(
            source=source,
            threshold_seconds=threshold_seconds,
            freshness_status=FreshnessStatus.UNKNOWN,
        )
    status = (
        FreshnessStatus.CURRENT
        if age_seconds <= threshold_seconds
        else FreshnessStatus.STALE
    )
    return FreshnessInfo(
        source=source,
        age_seconds=age_seconds,
        threshold_seconds=threshold_seconds,
        freshness_status=status,
    )


def live_freshness(source: str, retrieved_at: datetime, threshold_seconds: float) -> FreshnessInfo:
    age = (datetime.now(UTC) - retrieved_at).total_seconds()
    return scenario_freshness(source, age, threshold_seconds).model_copy(
        update={"retrieved_at": retrieved_at.isoformat()}
    )


def is_usable(info: FreshnessInfo) -> bool:
    """Fixed scenario data and current live data may support a PASS; nothing else."""
    return info.freshness_status in (
        FreshnessStatus.FIXED_SCENARIO,
        FreshnessStatus.CURRENT,
    )
