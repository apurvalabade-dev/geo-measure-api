"""Area and length measurement.

Strategy
--------
Degrees are not a unit of length, so we never measure in a geographic CRS.
For every feature we:

1. reproject the geometry from the file's CRS to WGS84 (lon/lat),
2. pick the UTM zone that contains the feature's centroid,
3. reproject to that UTM zone and measure there (area in m2, length in m).

Beyond the UTM limits (latitude above 84 or below -80) we fall back to
ellipsoidal (geodesic) measurement, which needs no projection at all.
The geodesic functions are also used in tests to cross-check the UTM results.
"""

import logging
import math
from dataclasses import dataclass

import numpy as np
import shapely
from pyproj import CRS, Geod, Transformer
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

logger = logging.getLogger(__name__)

OK = "OK"
SKIPPED = "SKIPPED"  # nothing to measure by design (points, unsupported types, no geometry)
ERROR = "ERROR"  # we tried and failed

WGS84 = "EPSG:4326"
GEOD = Geod(ellps="WGS84")

AREA_TYPES = {"Polygon", "MultiPolygon"}
LENGTH_TYPES = {"LineString", "MultiLineString"}
POINT_TYPES = {"Point", "MultiPoint"}

UTM_MIN_LAT, UTM_MAX_LAT = -80.0, 84.0


class MeasurementError(Exception):
    """A measurement failed for a reason that is safe to show to the client."""


@dataclass(frozen=True)
class Measurement:
    status: str
    note: str | None = None
    projected_crs: str | None = None
    area_m2: float | None = None
    length_m: float | None = None


class Measurer:
    """Measures GeoJSON geometries that are expressed in `source_crs`.

    One instance per uploaded file: it caches the pyproj transformers it creates
    (building a Transformer is slow), so it must not be shared between threads.
    """

    def __init__(self, source_crs: str) -> None:
        self._source_crs = source_crs
        self._transformers: dict[tuple[str, str], Transformer] = {}

    def measure(self, geometry: dict | None) -> Measurement:
        """Never raises: a problem with one feature must not affect the others."""
        if geometry is None:
            return Measurement(SKIPPED, "Feature has no geometry.")
        try:
            return self._measure(shape(geometry))
        except MeasurementError as exc:
            return Measurement(ERROR, str(exc))
        except Exception:
            logger.exception("Unexpected error while measuring a geometry")
            return Measurement(ERROR, "Measurement failed for this geometry.")

    def _measure(self, geom: BaseGeometry) -> Measurement:
        kind = geom.geom_type
        if geom.is_empty:
            return Measurement(SKIPPED, "Geometry is empty.")
        if kind in POINT_TYPES:
            return Measurement(SKIPPED, f"{kind} has no area or length.")
        if kind not in AREA_TYPES | LENGTH_TYPES:
            return Measurement(SKIPPED, f"Unsupported geometry type: {kind}.")

        geom = shapely.force_2d(geom)  # area and length are 2D; ignore altitude
        notes = []
        if not geom.is_valid:
            notes.append("Geometry is invalid (e.g. self-intersecting); the result may be unreliable.")

        lonlat = self._reproject(geom, self._source_crs, WGS84)
        centroid = lonlat.centroid
        _check_lonlat(centroid.x, centroid.y)

        utm_crs = utm_crs_for(centroid.x, centroid.y)
        if utm_crs is None:
            value = geodesic_measure(lonlat)
            notes.append("Outside the UTM latitude range; measured on the ellipsoid.")
        else:
            projected = self._reproject(lonlat, WGS84, utm_crs)
            value = projected.area if kind in AREA_TYPES else projected.length

        return Measurement(
            status=OK,
            note=" ".join(notes) or None,
            projected_crs=utm_crs,
            area_m2=value if kind in AREA_TYPES else None,
            length_m=value if kind in LENGTH_TYPES else None,
        )

    def _reproject(self, geom: BaseGeometry, source: str, target: str) -> BaseGeometry:
        if source == target:
            return geom
        key = (source, target)
        if key not in self._transformers:
            self._transformers[key] = Transformer.from_crs(
                CRS.from_user_input(source), CRS.from_user_input(target), always_xy=True
            )
        transformer = self._transformers[key]

        def apply(coords: np.ndarray) -> np.ndarray:
            x, y = transformer.transform(coords[:, 0], coords[:, 1])
            return np.column_stack((x, y))

        return shapely.transform(geom, apply)


def utm_crs_for(lon: float, lat: float) -> str | None:
    """EPSG code of the UTM zone containing (lon, lat), or None outside UTM's range."""
    if not (UTM_MIN_LAT <= lat <= UTM_MAX_LAT):
        return None
    zone = min(int((lon + 180) // 6) + 1, 60)  # lon == 180 belongs to zone 60
    base = 32600 if lat >= 0 else 32700  # 326xx north, 327xx south
    return f"EPSG:{base + zone}"


def geodesic_measure(lonlat_geom: BaseGeometry) -> float:
    """Ellipsoidal area (m2) or length (m) of a WGS84 lon/lat geometry."""
    parts = list(lonlat_geom.geoms) if hasattr(lonlat_geom, "geoms") else [lonlat_geom]
    if lonlat_geom.geom_type in AREA_TYPES:
        return sum(_polygon_area(part) for part in parts)
    return sum(GEOD.geometry_length(part) for part in parts)


def _polygon_area(polygon: BaseGeometry) -> float:
    # Holes are subtracted explicitly: summing signed ring areas depends on ring orientation.
    area = _ring_area(polygon.exterior)
    return area - sum(_ring_area(hole) for hole in polygon.interiors)


def _ring_area(ring: BaseGeometry) -> float:
    lons, lats = ring.xy
    return abs(GEOD.polygon_area_perimeter(lons, lats)[0])


def _check_lonlat(lon: float, lat: float) -> None:
    if not (math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90):
        raise MeasurementError(
            "Coordinates are outside the valid longitude/latitude range. "
            "The file's CRS may be declared incorrectly."
        )