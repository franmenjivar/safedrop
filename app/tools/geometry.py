"""Deterministic geometry helpers.

Everything geographic in SafeDrop reduces to a local equirectangular
projection around a reference point. At the scale of a forced-landing
diversion (< 3 km) the distortion is negligible, and it lets us use plain
Shapely in metres.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

from shapely.geometry import LineString, Point, shape
from shapely.geometry.base import BaseGeometry

EARTH_RADIUS_M = 6_371_008.8


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing from point 1 to point 2, degrees clockwise from north."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    y = math.sin(dlambda) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlambda)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


class LocalFrame:
    """Equirectangular metre frame anchored at a reference lat/lon."""

    def __init__(self, ref_lat: float, ref_lon: float) -> None:
        self.ref_lat = ref_lat
        self.ref_lon = ref_lon
        self._m_per_deg_lat = 111_132.0
        self._m_per_deg_lon = 111_320.0 * math.cos(math.radians(ref_lat))

    def to_xy(self, lat: float, lon: float) -> tuple[float, float]:
        return (
            (lon - self.ref_lon) * self._m_per_deg_lon,
            (lat - self.ref_lat) * self._m_per_deg_lat,
        )

    def to_latlon(self, x: float, y: float) -> tuple[float, float]:
        return (
            self.ref_lat + y / self._m_per_deg_lat,
            self.ref_lon + x / self._m_per_deg_lon,
        )

    def point(self, lat: float, lon: float) -> Point:
        return Point(*self.to_xy(lat, lon))

    def line(self, coords: Sequence[tuple[float, float]]) -> LineString:
        """Build a metre-frame line from ``(lat, lon)`` pairs."""
        return LineString([self.to_xy(lat, lon) for lat, lon in coords])

    def geometry(self, geojson_geometry: dict) -> BaseGeometry:
        """Project a GeoJSON geometry (lon/lat order) into the metre frame."""
        geom = shape(geojson_geometry)
        return _map_coords(geom, lambda x, y: self.to_xy(y, x))


def _map_coords(geom: BaseGeometry, fn) -> BaseGeometry:
    from shapely.ops import transform

    return transform(lambda x, y, z=None: fn(x, y), geom)


def inscribed_circle(geojson_geometry: dict) -> tuple[float, float, float]:
    """The most interior point of a polygon and its distance to the boundary.

    Returns ``(lat, lon, radius_m)``. This is how SafeDrop turns "there is an
    open area here" into "there is *this much* open area, centred *here*" — the
    pole of inaccessibility sits as far from every edge as the shape allows, so
    a candidate anchored on it is genuinely inside open ground rather than on a
    boundary shared with a road, a building, or a treeline.

    The radius is a measured clearance, not an assumption, and is reported to
    the operator as such.
    """
    from shapely.ops import polylabel

    lat, lon = geometry_centroid(geojson_geometry)
    frame = LocalFrame(lat, lon)
    polygon = frame.geometry(geojson_geometry)
    if polygon.geom_type == "MultiPolygon":
        polygon = max(polygon.geoms, key=lambda g: g.area)
    if polygon.is_empty or polygon.area <= 0:
        return lat, lon, 0.0

    point = polylabel(polygon, tolerance=1.0)
    radius = float(polygon.exterior.distance(point))
    for interior in polygon.interiors:
        radius = min(radius, float(interior.distance(point)))
    plat, plon = frame.to_latlon(point.x, point.y)
    return plat, plon, radius


def polygon_area_m2(geojson_geometry: dict) -> float:
    """Area of a GeoJSON polygon in square metres."""
    geom = shape(geojson_geometry)
    centroid = geom.centroid
    frame = LocalFrame(centroid.y, centroid.x)
    return float(frame.geometry(geojson_geometry).area)


def geometry_centroid(geojson_geometry: dict) -> tuple[float, float]:
    """Centroid of a GeoJSON geometry as ``(lat, lon)``."""
    c = shape(geojson_geometry).centroid
    return (c.y, c.x)


def point_to_features_min_distance(
    lat: float,
    lon: float,
    features: Iterable[dict],
) -> float | None:
    """Distance from a point to the nearest of a set of GeoJSON geometries."""
    frame = LocalFrame(lat, lon)
    origin = Point(0.0, 0.0)
    best: float | None = None
    for geometry in features:
        try:
            d = float(frame.geometry(geometry).distance(origin))
        except Exception:  # malformed fixture geometry must not kill a run
            continue
        best = d if best is None else min(best, d)
    return best


def offset_latlon(lat: float, lon: float, bearing: float, distance_m: float) -> tuple[float, float]:
    """Move ``distance_m`` along ``bearing`` from a lat/lon point."""
    frame = LocalFrame(lat, lon)
    rad = math.radians(bearing)
    return frame.to_latlon(distance_m * math.sin(rad), distance_m * math.cos(rad))
