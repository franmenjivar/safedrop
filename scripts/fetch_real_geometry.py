"""Fetch and cache real Austin-area open spaces from OpenStreetMap.

SafeDrop's landing candidates must sit on ground that actually exists and is
actually open. Hand-placed coordinates do not survive contact with a satellite
basemap — a "recovery yard" ends up on somebody's roof.

This script resolves real features through Nominatim, measures each one's
largest inscribed circle, and caches the result under ``data/geo_cache/``. The
cache is committed, so the benchmark stays reproducible offline and no run
depends on Nominatim being up.

    python -m scripts.fetch_real_geometry

Data © OpenStreetMap contributors, ODbL. Nominatim's usage policy caps this at
one request per second, which the delay below respects. Run it rarely.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

from app.config import DATA_DIR
from app.tools.geometry import inscribed_circle, polygon_area_m2

CACHE_DIR = DATA_DIR / "geo_cache"
NOMINATIM = "https://nominatim.openstreetmap.org"
HEADERS = {"User-Agent": "SafeDrop-hackathon/0.1 (research prototype)"}
DELAY_S = 1.2

#: Search term -> SafeDrop feature type.
SEARCHES = {
    "parking": "parking_lot",
    "park": "park",
    "recreation ground": "recreation_ground",
    "industrial": "industrial_lot",
    # Sensitive land use. Candidates near these are excluded deterministically,
    # before any agent sees them, so these must be real too.
    "school": "school",
    "hospital": "hospital",
    "playground": "playground",
}

# Note: unnamed land use (farmland, meadow, bare industrial parcels) cannot be
# found through Nominatim, which indexes named places. Those need an Overpass
# query — see app/tools/osm_overpass.py, which does exactly that when the
# Overpass API is reachable.

#: Scenario regions, as Nominatim viewboxes: left,top,right,bottom.
REGIONS = {
    "central_austin": "-97.800,30.285,-97.735,30.240",
    "north_austin": "-97.760,30.420,-97.690,30.375",
    "wells_branch": "-97.710,30.460,-97.640,30.415",
    "round_rock": "-97.720,30.560,-97.620,30.490",
    "pflugerville": "-97.660,30.470,-97.590,30.420",
}

MIN_AREA_M2 = 600.0
MIN_OPEN_RADIUS_M = 18.0


def _search(client: httpx.Client, term: str, viewbox: str, limit: int = 12) -> list[dict]:
    response = client.get(
        f"{NOMINATIM}/search",
        params={
            "q": term,
            "format": "jsonv2",
            "limit": limit,
            "viewbox": viewbox,
            "bounded": 1,
        },
    )
    response.raise_for_status()
    return response.json()


def _lookup(client: httpx.Client, hits: list[dict]) -> list[dict]:
    """Nominatim only returns geometry from the lookup endpoint."""
    ids = [f"{h['osm_type'][0].upper()}{h['osm_id']}" for h in hits][:40]
    if not ids:
        return []
    response = client.get(
        f"{NOMINATIM}/lookup",
        params={"osm_ids": ",".join(ids), "format": "json", "polygon_geojson": 1},
    )
    response.raise_for_status()
    return response.json()


def _largest_polygon(geometry: dict) -> dict | None:
    if geometry.get("type") == "Polygon":
        return geometry
    if geometry.get("type") == "MultiPolygon":
        rings = max(geometry["coordinates"], key=lambda p: len(p[0]))
        return {"type": "Polygon", "coordinates": rings}
    return None


def fetch_region(client: httpx.Client, region: str, viewbox: str) -> list[dict]:
    features: dict[str, dict] = {}
    for term, feature_type in SEARCHES.items():
        try:
            hits = _search(client, term, viewbox)
            time.sleep(DELAY_S)
            details = _lookup(client, hits)
            time.sleep(DELAY_S)
        except Exception as exc:
            print(f"    {region}/{term}: FAILED ({type(exc).__name__}: {exc})")
            continue

        kept = 0
        for detail in details:
            geometry = _largest_polygon(detail.get("geojson") or {})
            if geometry is None:
                continue
            try:
                area = polygon_area_m2(geometry)
                lat, lon, radius = inscribed_circle(geometry)
            except Exception:
                continue
            sensitive = feature_type in ("school", "hospital", "playground")
            if not sensitive and (area < MIN_AREA_M2 or radius < MIN_OPEN_RADIUS_M):
                continue

            key = f"{detail['osm_type']}/{detail['osm_id']}"
            features[key] = {
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "osm_id": detail["osm_id"],
                    "osm_type": detail["osm_type"],
                    "name": detail.get("name") or detail["display_name"].split(",")[0],
                    "display_name": detail["display_name"],
                    "feature_type": feature_type,
                    "region": region,
                    "area_m2": round(area, 1),
                    # Measured clearance: the largest circle that fits inside.
                    "open_lat": round(lat, 7),
                    "open_lon": round(lon, 7),
                    "open_radius_m": round(radius, 1),
                    "source": "openstreetmap_nominatim",
                    "licence": "Data (c) OpenStreetMap contributors, ODbL",
                },
            }
            kept += 1
        print(f"    {region}/{term}: {kept} usable")
    return list(features.values())


def main() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=45.0, headers=HEADERS, follow_redirects=True) as client:
        for region, viewbox in REGIONS.items():
            print(f"  {region}")
            features = fetch_region(client, region, viewbox)
            path = CACHE_DIR / f"{region}.geojson"
            path.write_text(
                json.dumps({"type": "FeatureCollection", "features": features}, indent=1) + "\n"
            )
            print(f"  -> {path.name}: {len(features)} features\n")


if __name__ == "__main__":
    main()
