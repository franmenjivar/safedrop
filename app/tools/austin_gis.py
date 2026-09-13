"""City of Austin open GIS context.

Offline mode serves the scenario's normalised fixture. Live mode queries the
public ArcGIS REST endpoints. Note the framing carefully: a park polygon means
*open geometry exists here*, never *this is an approved landing area*.
"""

from __future__ import annotations

import httpx

from app.config import SafeDropConfig
from app.models.ground_context import GISFeature
from app.services.scenario_loader import ScenarioData
from app.tools.cache import CONTEXT_CACHE
from app.tools.geometry import haversine_m, geometry_centroid

#: Publicly documented City of Austin ArcGIS layers used for context only.
AUSTIN_LAYERS: dict[str, str] = {
    "parks": "https://services.arcgis.com/0L95CJ0VTaxqcmjU/arcgis/rest/services/PARKS_Park_Boundaries/FeatureServer/0/query",
}

#: Fixture feature types treated as Austin-GIS-sourced context.
GIS_FIXTURE_TYPES = ("park", "school", "hospital", "playground")


def _from_scenario(scenario: ScenarioData, lat: float, lon: float, radius_m: float) -> list[GISFeature]:
    out: list[GISFeature] = []
    for feature in scenario.map_features:
        props = feature.get("properties", {})
        if props.get("feature_type") not in GIS_FIXTURE_TYPES:
            continue
        flat, flon = geometry_centroid(feature["geometry"])
        if haversine_m(lat, lon, flat, flon) > radius_m:
            continue
        out.append(
            GISFeature(
                source=props.get("source", "austin_gis_fixture"),
                layer=props.get("layer", props["feature_type"]),
                feature_type=props["feature_type"],
                name=props.get("name"),
                geometry=feature["geometry"],
                properties=props,
            )
        )
    return out


async def get_austin_gis_context(
    scenario: ScenarioData,
    lat: float,
    lon: float,
    cfg: SafeDropConfig,
    radius_m: float = 1500.0,
) -> dict:
    """Return normalised GIS context around a point."""
    if cfg.context.default_mode == "offline" or not cfg.apis.austin_gis_enabled:
        features = _from_scenario(scenario, lat, lon, radius_m)
        return {
            "status": "OK",
            "source": "austin_gis_fixture",
            "mode": "offline",
            "features": [f.model_dump() for f in features],
        }

    key = f"austin_gis:{lat:.3f},{lon:.3f},{radius_m}"
    if (cached := CONTEXT_CACHE.get(key)) is not None:
        return cached

    features: list[GISFeature] = list(_from_scenario(scenario, lat, lon, radius_m))
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            for layer, url in AUSTIN_LAYERS.items():
                response = await client.get(
                    url,
                    params={
                        "geometry": f"{lon},{lat}",
                        "geometryType": "esriGeometryPoint",
                        "inSR": 4326,
                        "distance": radius_m,
                        "units": "esriSRUnit_Meter",
                        "spatialRel": "esriSpatialRelIntersects",
                        "outFields": "*",
                        "returnGeometry": "true",
                        "outSR": 4326,
                        "f": "geojson",
                    },
                )
                response.raise_for_status()
                for feature in response.json().get("features", []):
                    props = feature.get("properties", {})
                    features.append(
                        GISFeature(
                            source="austin_gis",
                            layer=layer,
                            feature_type="park",
                            name=props.get("PARK_NAME") or props.get("NAME"),
                            geometry=feature["geometry"],
                            properties=props,
                        )
                    )
    except Exception as exc:
        return {
            "status": "PARTIAL",
            "source": "austin_gis",
            "reason": str(exc),
            "features": [f.model_dump() for f in features],
        }

    payload = {
        "status": "OK",
        "source": "austin_gis",
        "mode": cfg.context.default_mode,
        "features": [f.model_dump() for f in features],
    }
    CONTEXT_CACHE.set(key, payload)
    return payload
