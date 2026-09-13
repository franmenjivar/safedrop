"""Verify that a landing anchor is actually on open ground.

The inscribed-circle anchor guarantees a point is deep inside a *polygon*. It
does not guarantee the polygon is clear. OpenStreetMap "park" boundaries
routinely enclose houses, service roads, playscapes and pavilions, and a
subdivision named "Creekside Park" is not a park at all.

So each anchor is checked against reverse geocoding: what is the nearest mapped
thing to this point, and how far away is it? A point whose nearest neighbour is
a building six metres away is not a landing site, however deep inside the
boundary it sits.

Candidate points are sampled across the polygon interior and the best-scoring
one wins. Results are written back into ``data/geo_cache/`` as
``verified_lat`` / ``verified_lon`` / ``nearest_feature_m``.

    python -m scripts.verify_anchors

Nominatim's usage policy caps this at one request per second. Run it rarely.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

import httpx
from shapely.geometry import Point

from app.config import DATA_DIR
from app.tools.geometry import LocalFrame, haversine_m

CACHE_DIR = DATA_DIR / "geo_cache"
NOMINATIM = "https://nominatim.openstreetmap.org/reverse"
HEADERS = {"User-Agent": "SafeDrop-hackathon/0.1 (research prototype)"}
DELAY_S = 1.1

#: Things you must not put an aircraft on top of, and the clearance each needs.
HARD_BLOCKERS = {
    "building": 35.0,
    "place": 35.0,          # house, address points
    "man_made": 25.0,
    "power": 40.0,
}
#: Things that are tolerable nearby but not underneath.
SOFT_BLOCKERS = {
    "highway": 20.0,        # paths and service roads
    "leisure": 15.0,        # playgrounds, pitches, benches
    "amenity": 10.0,
}

SAMPLES_PER_FEATURE = 5
MIN_ACCEPTABLE_SCORE = 20.0


def _sample_points(geometry: dict, anchor: tuple[float, float], n: int) -> list[tuple[float, float]]:
    """The inscribed-circle point plus a ring of interior alternatives."""
    frame = LocalFrame(*anchor)
    polygon = frame.geometry(geometry)
    if polygon.geom_type == "MultiPolygon":
        polygon = max(polygon.geoms, key=lambda g: g.area)

    points = [anchor]
    radius = polygon.exterior.distance(Point(0.0, 0.0))
    for i in range(n - 1):
        angle = 2 * math.pi * i / max(1, n - 1)
        for fraction in (0.55, 0.35):
            candidate = Point(
                radius * fraction * math.cos(angle), radius * fraction * math.sin(angle)
            )
            if polygon.contains(candidate):
                points.append(frame.to_latlon(candidate.x, candidate.y))
                break
    return points[:n]


def _score(client: httpx.Client, lat: float, lon: float) -> tuple[float, str]:
    """Distance in metres to the nearest mapped feature, and what it is.

    A large number means open ground. The category determines how much
    clearance that particular kind of feature demands.
    """
    try:
        response = client.get(
            NOMINATIM,
            params={"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 18},
        )
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        return 0.0, f"lookup failed: {exc}"

    category = data.get("category", "")
    kind = f"{category}/{data.get('type')}"
    name = data.get("name") or data.get("display_name", "")[:40]
    try:
        distance = haversine_m(lat, lon, float(data["lat"]), float(data["lon"]))
    except Exception:
        return 0.0, f"{kind} (no position)"

    required = HARD_BLOCKERS.get(category) or SOFT_BLOCKERS.get(category)
    if required is None:
        # Boundaries, landuse, natural features: not an obstruction.
        return 999.0, f"{kind} {name}"
    # Score is how much clearance we have beyond what this feature type needs.
    return distance - required, f"{kind} {name} at {distance:.0f} m"


def verify_region(client: httpx.Client, path: Path) -> tuple[int, int]:
    payload = json.loads(path.read_text())
    ok = flagged = 0

    for feature in payload["features"]:
        props = feature["properties"]
        if props["feature_type"] in ("school", "hospital", "playground"):
            continue  # sensitive land use, never a candidate

        anchor = (props["open_lat"], props["open_lon"])
        best = None
        for lat, lon in _sample_points(feature["geometry"], anchor, SAMPLES_PER_FEATURE):
            score, detail = _score(client, lat, lon)
            time.sleep(DELAY_S)
            if best is None or score > best[0]:
                best = (score, lat, lon, detail)
            if score >= 200.0:
                break  # comfortably open; no need to keep sampling

        score, lat, lon, detail = best
        props["verified_lat"] = round(lat, 7)
        props["verified_lon"] = round(lon, 7)
        props["nearest_feature"] = detail
        props["anchor_clearance_score_m"] = round(score, 1)
        props["open_ground_verified"] = score >= MIN_ACCEPTABLE_SCORE

        if props["open_ground_verified"]:
            ok += 1
        else:
            flagged += 1
            print(f"    REJECT {props['name'][:40]:42s} {detail}")

    path.write_text(json.dumps(payload, indent=1) + "\n")
    return ok, flagged


def main() -> None:
    with httpx.Client(timeout=30.0, headers=HEADERS, follow_redirects=True) as client:
        for path in sorted(CACHE_DIR.glob("*.geojson")):
            print(f"  {path.stem}")
            ok, flagged = verify_region(client, path)
            print(f"  -> {ok} verified open, {flagged} rejected\n")


if __name__ == "__main__":
    main()
