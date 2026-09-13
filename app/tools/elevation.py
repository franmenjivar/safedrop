"""Coarse terrain context.

Deliberately coarse: SafeDrop reports an elevation spread, not a certified
slope suitability judgement.
"""

from __future__ import annotations

import httpx

from app.config import SafeDropConfig
from app.tools.cache import CONTEXT_CACHE

OPEN_METEO_ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"


async def get_elevation_context(
    points: list[tuple[float, float]], cfg: SafeDropConfig
) -> dict:
    """Return elevations for a small set of ``(lat, lon)`` points."""
    if cfg.context.default_mode == "offline" or not cfg.apis.open_meteo_enabled or not points:
        return {"status": "SKIPPED", "source": "offline_mode", "elevations_m": []}

    key = "elev:" + ";".join(f"{a:.4f},{b:.4f}" for a, b in points)
    if (cached := CONTEXT_CACHE.get(key)) is not None:
        return cached
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            response = await client.get(
                OPEN_METEO_ELEVATION_URL,
                params={
                    "latitude": ",".join(f"{a:.5f}" for a, _ in points),
                    "longitude": ",".join(f"{b:.5f}" for _, b in points),
                },
            )
            response.raise_for_status()
            elevations = response.json()["elevation"]
    except Exception as exc:
        return {"status": "UNAVAILABLE", "source": "open_meteo", "reason": str(exc)}

    spread = max(elevations) - min(elevations) if elevations else 0.0
    payload = {
        "status": "OK",
        "source": "open_meteo_elevation",
        "elevations_m": elevations,
        "elevation_spread_m": round(spread, 1),
    }
    CONTEXT_CACHE.set(key, payload)
    return payload
