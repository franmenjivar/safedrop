"""Helpers for generating SafeDrop scenario fixtures.

Everything under ``data/scenarios/`` is produced by :mod:`scripts.build_fixtures`
so the whole benchmark is regenerable from source rather than hand-edited.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

from app.tools.geometry import offset_latlon


def rect(lat: float, lon: float, width_m: float, height_m: float, rotation_deg: float = 0.0) -> dict:
    """Axis-oriented rectangle polygon centred on a point, as GeoJSON (lon, lat)."""
    hw, hh = width_m / 2.0, height_m / 2.0
    corners = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh), (-hw, -hh)]
    theta = math.radians(rotation_deg)
    ring = []
    for dx, dy in corners:
        rx = dx * math.cos(theta) - dy * math.sin(theta)
        ry = dx * math.sin(theta) + dy * math.cos(theta)
        bearing = (math.degrees(math.atan2(rx, ry))) % 360.0
        distance = math.hypot(rx, ry)
        plat, plon = offset_latlon(lat, lon, bearing, distance)
        ring.append([round(plon, 7), round(plat, 7)])
    return {"type": "Polygon", "coordinates": [ring]}


def line(points: list[tuple[float, float]]) -> dict:
    return {"type": "LineString", "coordinates": [[round(lon, 7), round(lat, 7)] for lat, lon in points]}


def at(origin: tuple[float, float], bearing_deg: float, distance_m: float) -> tuple[float, float]:
    return offset_latlon(origin[0], origin[1], bearing_deg, distance_m)


def feature(geometry: dict, **properties) -> dict:
    return {"type": "Feature", "geometry": geometry, "properties": properties}


def collection(features: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": features}


def write_json(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


# --------------------------------------------------------------------------
# Real geometry
# --------------------------------------------------------------------------


def load_region(region: str) -> list[dict]:
    """Load the cached real OpenStreetMap features for a region."""
    from app.config import DATA_DIR

    path = DATA_DIR / "geo_cache" / f"{region}.geojson"
    if not path.exists():
        raise FileNotFoundError(
            f"Missing {path}. Run `python -m scripts.fetch_real_geometry` first."
        )
    return json.loads(path.read_text())["features"]


def verified(region: str) -> list[dict]:
    """Only features whose anchor was confirmed to be on open ground.

    ``scripts.verify_anchors`` reverse-geocodes each candidate point and
    measures how far the nearest mapped structure is. A polygon tagged "park"
    that encloses a house, a service road, or a playscape does not qualify.
    """
    out = [f for f in load_region(region) if f["properties"].get("open_ground_verified")]
    if not out:
        raise RuntimeError(
            f"No verified-open features in {region}. Run `python -m scripts.verify_anchors`."
        )
    return out


def find(region: str, name: str, feature_type: str | None = None) -> dict:
    """Find one verified-open real feature by name.

    Scenarios name the exact real place they use, so fixtures are stable across
    rebuilds and every coordinate can be checked against a map. Naming a site
    that failed open-ground verification is an error, not a silent downgrade.
    """
    pool = verified(region)
    matches = [
        f
        for f in pool
        if name.lower() in f["properties"]["name"].lower()
        and (feature_type is None or f["properties"]["feature_type"] == feature_type)
    ]
    if not matches:
        rejected = [
            f["properties"]["name"]
            for f in load_region(region)
            if name.lower() in f["properties"]["name"].lower()
        ]
        if rejected:
            raise LookupError(
                f"{name!r} exists in {region} but failed open-ground verification: {rejected}. "
                "Pick a different site."
            )
        available = sorted({f["properties"]["name"] for f in pool})
        raise LookupError(f"No verified feature matching {name!r} in {region}. Available: {available}")
    return max(matches, key=lambda f: f["properties"]["anchor_clearance_score_m"])


def open_point(feature: dict) -> tuple[float, float]:
    """The verified open-ground point of a real feature.

    Prefers the reverse-geocode-verified point over the raw inscribed-circle
    point, since verification may have moved the anchor away from a structure
    the polygon happened to enclose.
    """
    p = feature["properties"]
    if "verified_lat" in p:
        return (p["verified_lat"], p["verified_lon"])
    if "open_lat" in p:
        return (p["open_lat"], p["open_lon"])
    return (p["anchor_lat"], p["anchor_lon"])


def sensitive_features(region: str, types=("school", "hospital", "playground")) -> list[dict]:
    return [f for f in load_region(region) if f["properties"]["feature_type"] in types]


def place_drone(points: list[tuple[float, float]], bearing_deg: float, standoff_m: float):
    """Put the aircraft ``standoff_m`` from the centroid of the candidate set.

    The aircraft position is the one thing a simulator legitimately invents —
    it is where the emergency happens to occur, not a claim about the ground.
    """
    lat = sum(p[0] for p in points) / len(points)
    lon = sum(p[1] for p in points) / len(points)
    return at((lat, lon), bearing_deg, standoff_m)


TELEMETRY_COLUMNS = [
    "timestamp_s",
    "latitude",
    "longitude",
    "altitude_m",
    "battery_soc",
    "battery_temp_c",
    "battery_drop_rate_pct_min",
    "motor_health",
    "airspeed_mps",
    "payload_kg",
    "wind_speed_mps",
    "wind_direction_deg",
    "link_quality",
    # Channels for the propulsion, navigation, environment and power families.
    "power_draw_w",
    "vibration_g",
    "esc_temp_c",
    "gps_satellites",
    "gps_hdop",
    "heading_disagreement_deg",
    "wind_gust_mps",
]


def write_telemetry(
    path: Path,
    *,
    end_point: tuple[float, float],
    heading_deg: float,
    seconds: int,
    airspeed_mps: float,
    altitude_m: float,
    soc_end: float,
    soc_drop_pct_min: float,
    temp_end: float,
    temp_rise_c_min: float,
    motor_end: float,
    motor_drift: float,
    payload_kg: float,
    wind_speed_mps: float,
    wind_direction_deg: float,
    link_end: float,
    link_drift: float,
    gps_jitter_m: float = 0.6,
    seed: int = 7,
    power_draw_w: float | None = None,
    vibration_g: float = 0.18,
    vibration_rise_per_min: float = 0.0,
    esc_temp_c: float = 52.0,
    esc_rise_c_min: float = 0.0,
    gps_satellites: int = 14,
    gps_hdop: float = 0.8,
    heading_disagreement_deg: float = 1.5,
    wind_gust_mps: float | None = None,
) -> None:
    """Write a telemetry CSV whose final row is the decision point.

    Earlier rows are back-projected along the reversed heading, so the track
    arrives at ``end_point`` travelling on ``heading_deg``.
    """
    rng = random.Random(seed)
    rows: list[list[str]] = []
    back_bearing = (heading_deg + 180.0) % 360.0

    for i in range(seconds):
        t = float(i)
        behind_m = (seconds - 1 - i) * airspeed_mps
        lat, lon = at(end_point, back_bearing, behind_m)
        if gps_jitter_m:
            jitter_bearing = rng.uniform(0, 360)
            jitter = abs(rng.gauss(0, gps_jitter_m))
            lat, lon = at((lat, lon), jitter_bearing, jitter)

        minutes_before_end = (seconds - 1 - i) / 60.0
        soc = soc_end + soc_drop_pct_min * minutes_before_end
        temp = temp_end - temp_rise_c_min * minutes_before_end
        motor = motor_end + motor_drift * minutes_before_end
        link = min(1.0, max(0.0, link_end + link_drift * minutes_before_end))
        alt = altitude_m + rng.uniform(-0.6, 0.6)

        vib = max(0.0, vibration_g - vibration_rise_per_min * minutes_before_end)
        esc = esc_temp_c - esc_rise_c_min * minutes_before_end
        gust = (wind_gust_mps if wind_gust_mps is not None else wind_speed_mps + 1.5)
        draw = power_draw_w if power_draw_w is not None else ""

        rows.append(
            [
                f"{t:.0f}",
                f"{lat:.6f}",
                f"{lon:.6f}",
                f"{alt:.1f}",
                f"{soc:.2f}",
                f"{temp:.1f}",
                f"{soc_drop_pct_min:.2f}",
                f"{min(1.0, motor):.3f}",
                f"{airspeed_mps + rng.uniform(-0.3, 0.3):.1f}",
                f"{payload_kg:.1f}",
                f"{wind_speed_mps + rng.uniform(-0.3, 0.3):.1f}",
                f"{wind_direction_deg:.0f}",
                f"{link:.2f}",
                f"{draw + rng.uniform(-12, 12):.0f}" if draw != "" else "",
                f"{vib + rng.uniform(-0.02, 0.02):.3f}",
                f"{esc + rng.uniform(-0.8, 0.8):.1f}",
                f"{gps_satellites}",
                f"{gps_hdop + rng.uniform(-0.05, 0.05):.2f}",
                f"{heading_disagreement_deg + rng.uniform(-0.8, 0.8):.1f}",
                f"{gust + rng.uniform(-0.4, 0.4):.1f}",
            ]
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(",".join(TELEMETRY_COLUMNS) + "\n" + "\n".join(",".join(r) for r in rows) + "\n")


TRAFFIC_COLUMNS = ["timestamp_s", "vehicle_id", "latitude", "longitude", "altitude_m"]


def write_traffic(path: Path, tracks: list[dict]) -> None:
    """Write synthetic UAV traffic.

    Each track: ``{"vehicle_id", "start", "heading_deg", "speed_mps",
    "altitude_m", "t0", "t1"}`` with ``start`` the position at ``t0``.
    """
    rows: list[list[str]] = []
    for track in tracks:
        lat, lon = track["start"]
        for t in range(int(track["t0"]), int(track["t1"]) + 1):
            elapsed = t - track["t0"]
            plat, plon = at((lat, lon), track["heading_deg"], track["speed_mps"] * elapsed)
            alt = track["altitude_m"] + track.get("climb_mps", 0.0) * elapsed
            rows.append(
                [f"{t}", track["vehicle_id"], f"{plat:.6f}", f"{plon:.6f}", f"{alt:.1f}"]
            )
    rows.sort(key=lambda r: (float(r[0]), r[1]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(",".join(TRAFFIC_COLUMNS) + "\n" + "\n".join(",".join(r) for r in rows) + "\n")
