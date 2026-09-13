"""Weather context.

Offline mode reads the scenario fixture. Live mode queries Open-Meteo, which
needs no API key. Either way the response carries provenance and freshness, and
a failure degrades to UNKNOWN rather than to an optimistic guess.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from app.config import SafeDropConfig
from app.models.ground_context import WeatherContext
from app.models.enums import FreshnessStatus
from app.models.ground_context import FreshnessInfo
from app.services.scenario_loader import ScenarioData
from app.tools.cache import CONTEXT_CACHE
from app.tools.freshness import fixture_freshness, live_freshness

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


def _from_scenario(scenario: ScenarioData, lat: float, lon: float) -> WeatherContext:
    fixture = scenario.ground_state.get("weather", {})
    row = scenario.telemetry.iloc[-1]
    return WeatherContext(
        latitude=lat,
        longitude=lon,
        wind_speed_mps=float(fixture.get("wind_speed_mps", row["wind_speed_mps"])),
        wind_direction_deg=float(fixture.get("wind_direction_deg", row["wind_direction_deg"])),
        wind_gust_mps=fixture.get("wind_gust_mps"),
        precipitation_mm=float(fixture.get("precipitation_mm", 0.0)),
        temperature_c=fixture.get("temperature_c"),
        freshness=fixture_freshness("scenario_weather_fixture"),
    )


async def get_weather_context(
    scenario: ScenarioData, lat: float, lon: float, cfg: SafeDropConfig
) -> WeatherContext:
    if cfg.context.default_mode == "offline" or not cfg.apis.open_meteo_enabled:
        return _from_scenario(scenario, lat, lon)

    key = f"weather:{lat:.3f},{lon:.3f}"
    if (cached := CONTEXT_CACHE.get(key)) is not None:
        return cached

    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            response = await client.get(
                OPEN_METEO_URL,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": "wind_speed_10m,wind_direction_10m,wind_gusts_10m,precipitation,temperature_2m",
                    "wind_speed_unit": "ms",
                },
            )
            response.raise_for_status()
            current = response.json()["current"]
    except Exception:
        fallback = _from_scenario(scenario, lat, lon)
        return fallback.model_copy(
            update={
                "freshness": FreshnessInfo(
                    source="open_meteo", freshness_status=FreshnessStatus.UNKNOWN
                )
            }
        )

    context = WeatherContext(
        latitude=lat,
        longitude=lon,
        wind_speed_mps=float(current["wind_speed_10m"]),
        wind_direction_deg=float(current["wind_direction_10m"]),
        wind_gust_mps=float(current.get("wind_gusts_10m", 0.0)),
        precipitation_mm=float(current.get("precipitation", 0.0)),
        temperature_c=float(current.get("temperature_2m", 0.0)),
        freshness=live_freshness("open_meteo", datetime.now(UTC), 900.0),
    )
    CONTEXT_CACHE.set(key, context)
    return context
