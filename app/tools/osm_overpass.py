"""OpenStreetMap context via Overpass.

Raw Overpass output never reaches an agent — it is normalised into
:class:`GISFeature` first. Offline mode uses the scenario's map-feature fixture,
which is authored in the same normalised shape.
"""

from __future__ import annotations

import httpx

from app.config import SafeDropConfig
from app.models.ground_context import GISFeature
from app.services.scenario_loader import ScenarioData
from app.tools.cache import CONTEXT_CACHE
from app.tools.geometry import geometry_centroid, haversine_m

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

#: OSM tag -> SafeDrop feature type.
TAG_MAP: dict[tuple[str, str], str] = {
    ("amenity", "parking"): "parking_lot",
    ("leisure", "park"): "park",
    ("leisure", "pitch"): "sports_field",
    ("leisure", "recreation_ground"): "recreation_ground",
    ("leisure", "playground"): "playground",
    ("landuse", "grass"): "grass",
    ("landuse", "meadow"): "meadow",
    ("landuse", "farmland"): "farmland",
    ("landuse", "industrial"): "industrial_lot",
    ("amenity", "school"): "school",
    ("amenity", "hospital"): "hospital",
    ("amenity", "kindergarten"): "school",
}

OSM_FIXTURE_EXCLUDE = ("building",)


def _from_scenario(scenario: ScenarioData, lat: float, lon: float, radius_m: float) -> list[GISFeature]:
    out: list[GISFeature] = []
    for feature in scenario.map_features:
        props = feature.get("properties", {})
        feature_type = props.get("feature_type", "")
        if feature_type in OSM_FIXTURE_EXCLUDE:
            continue
        flat, flon = geometry_centroid(feature["geometry"])
        if haversine_m(lat, lon, flat, flon) > radius_m:
            continue
        out.append(
            GISFeature(
                source=props.get("source", "osm_fixture"),
                layer="overpass",
                feature_type=feature_type,
                name=props.get("name"),
                geometry=feature["geometry"],
                properties=props,
            )
        )
    return out


async def get_osm_context(
    scenario: ScenarioData,
    lat: float,
    lon: float,
    cfg: SafeDropConfig,
    radius_m: float = 1200.0,
) -> dict:
    if cfg.context.default_mode == "offline" or not cfg.apis.overpass_enabled:
        features = _from_scenario(scenario, lat, lon, radius_m)
        return {
            "status": "OK",
            "source": "osm_fixture",
            "mode": "offline",
            "features": [f.model_dump() for f in features],
        }

    key = f"osm:{lat:.3f},{lon:.3f},{radius_m}"
    if (cached := CONTEXT_CACHE.get(key)) is not None:
        return cached

    query = f"""
    [out:json][timeout:20];
    (
      way(around:{int(radius_m)},{lat},{lon})["amenity"~"parking|school|hospital|kindergarten"];
      way(around:{int(radius_m)},{lat},{lon})["leisure"~"park|pitch|recreation_ground|playground"];
      way(around:{int(radius_m)},{lat},{lon})["landuse"~"grass|meadow|farmland|industrial"];
    );
    out geom;
    """
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            response = await client.post(OVERPASS_URL, data={"data": query})
            response.raise_for_status()
            elements = response.json().get("elements", [])
    except Exception as exc:
        return {
            "status": "UNAVAILABLE",
            "source": "overpass",
            "reason": str(exc),
            "features": [f.model_dump() for f in _from_scenario(scenario, lat, lon, radius_m)],
        }

    features = _from_scenario(scenario, lat, lon, radius_m)
    for element in elements:
        tags = element.get("tags", {})
        feature_type = next(
            (v for k, v in TAG_MAP.items() if tags.get(k[0]) == k[1]), None
        )
        geometry = element.get("geometry")
        if feature_type is None or not geometry:
            continue
        ring = [[p["lon"], p["lat"]] for p in geometry]
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        features.append(
            GISFeature(
                source="osm_overpass",
                layer="overpass",
                feature_type=feature_type,
                name=tags.get("name"),
                geometry={"type": "Polygon", "coordinates": [ring]},
                properties={"osm_id": element.get("id"), **tags},
            )
        )

    payload = {
        "status": "OK",
        "source": "osm_overpass",
        "mode": cfg.context.default_mode,
        "features": [f.model_dump() for f in features],
    }
    CONTEXT_CACHE.set(key, payload)
    return payload
