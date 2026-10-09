"""Unit tests for the measurement service (no HTTP involved)."""

import numpy as np
import pytest
import shapely
from pyproj import Transformer
from shapely.affinity import translate
from shapely.geometry import GeometryCollection, LineString, MultiPoint, MultiPolygon, Point, Polygon, box, mapping

from app.services.measurement import (
    ERROR,
    OK,
    SKIPPED,
    Measurer,
    geodesic_measure,
    utm_crs_for,
)

# UTM is conformal, not equal-area: areas are within ~0.1-0.2% of the true ellipsoidal value.
TOLERANCE = 3e-3
BENGALURU_BOX = box(77, 12, 78, 13)  # 1 deg x 1 deg


def measure(geom, crs="EPSG:4326"):
    return Measurer(crs).measure(mapping(geom))


def test_polygon_area_matches_geodesic_ground_truth():
    result = measure(BENGALURU_BOX)
    assert result.status == OK
    assert result.projected_crs == "EPSG:32643"  # UTM zone 43N
    assert result.area_m2 == pytest.approx(geodesic_measure(BENGALURU_BOX), rel=TOLERANCE)
    assert result.area_m2 == pytest.approx(12.02e9, rel=1e-2)  # ~12,025 km2
    assert result.length_m is None


def test_area_is_not_computed_in_degrees():
    assert BENGALURU_BOX.area == pytest.approx(1.0)  # what a naive .area gives: 1 "square degree"
    assert measure(BENGALURU_BOX).area_m2 > 1e9  # we return square metres


def test_same_box_is_smaller_at_high_latitude():
    north = translate(BENGALURU_BOX, yoff=48)  # lat 60-61
    result = measure(north)
    assert result.projected_crs == "EPSG:32643"  # same longitude, so same zone (43N)
    assert result.area_m2 == pytest.approx(geodesic_measure(north), rel=TOLERANCE)
    assert result.area_m2 < measure(BENGALURU_BOX).area_m2 * 0.6


def test_southern_hemisphere_uses_327xx_zones():
    sydney = box(151, -34, 151.1, -33.9)
    assert measure(sydney).projected_crs == "EPSG:32756"


def test_linestring_length_matches_geodesic():
    line = LineString([(77, 12), (77, 13)])
    result = measure(line)
    assert result.status == OK
    assert result.length_m == pytest.approx(geodesic_measure(line), rel=TOLERANCE)
    assert result.length_m == pytest.approx(110_700, rel=1e-2)
    assert result.area_m2 is None


def test_multipolygon_area_is_sum_of_parts():
    a, b = box(77, 12, 77.5, 12.5), box(77.6, 12.6, 78, 13)
    combined = measure(MultiPolygon([a, b])).area_m2
    assert combined == pytest.approx(measure(a).area_m2 + measure(b).area_m2, rel=TOLERANCE)


def test_polygon_hole_is_subtracted():
    hole = box(77.25, 12.25, 77.75, 12.75)
    holed = Polygon(BENGALURU_BOX.exterior.coords, [hole.exterior.coords])
    result = measure(holed).area_m2
    assert result == pytest.approx(measure(BENGALURU_BOX).area_m2 - measure(hole).area_m2, rel=TOLERANCE)
    assert result == pytest.approx(geodesic_measure(holed), rel=TOLERANCE)


def test_z_coordinates_are_ignored():
    flat = LineString([(77, 12), (77, 13)])
    tall = LineString([(77, 12, 0), (77, 13, 5000)])
    assert measure(tall).length_m == pytest.approx(measure(flat).length_m)


def test_projected_source_crs_is_handled():
    to_mercator = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True).transform
    mercator_box = shapely.transform(BENGALURU_BOX, lambda c: np.column_stack(to_mercator(c[:, 0], c[:, 1])))
    result = measure(mercator_box, crs="EPSG:3857")
    assert result.area_m2 == pytest.approx(geodesic_measure(BENGALURU_BOX), rel=TOLERANCE)
    # naive measurement in Web Mercator is badly inflated near the equator-ish latitudes
    assert mercator_box.area > result.area_m2 * 1.02


def test_points_are_skipped_not_errors():
    for geom in (Point(77.5, 12.5), MultiPoint([(77, 12), (78, 13)])):
        result = measure(geom)
        assert result.status == SKIPPED
        assert result.area_m2 is None and result.length_m is None


def test_unsupported_geometry_type_is_skipped():
    result = measure(GeometryCollection([Point(77, 12), LineString([(77, 12), (78, 13)])]))
    assert result.status == SKIPPED
    assert "Unsupported" in result.note


def test_missing_and_empty_geometry_are_skipped():
    assert Measurer("EPSG:4326").measure(None).status == SKIPPED
    assert measure(Polygon()).status == SKIPPED


def test_invalid_polygon_is_measured_with_a_warning():
    bowtie = Polygon([(77, 12), (78, 13), (78, 12), (77, 13)])
    result = measure(bowtie)
    assert result.status == OK
    assert "invalid" in result.note


def test_coordinates_outside_lonlat_range_are_an_error():
    projected_numbers_labelled_as_degrees = box(500_000, 1_400_000, 500_100, 1_400_100)
    result = measure(projected_numbers_labelled_as_degrees, crs="EPSG:4326")
    assert result.status == ERROR
    assert "CRS" in result.note


def test_polar_features_fall_back_to_geodesic():
    arctic = box(10, 85, 11, 86)
    result = measure(arctic)
    assert result.status == OK
    assert result.projected_crs is None
    assert result.area_m2 == pytest.approx(geodesic_measure(arctic))


@pytest.mark.parametrize(
    ("lon", "lat", "expected"),
    [
        (77.5, 12.5, "EPSG:32643"),
        (-122.4, 37.8, "EPSG:32610"),
        (151.2, -33.9, "EPSG:32756"),
        (180.0, 0.0, "EPSG:32660"),
        (-180.0, 0.0, "EPSG:32601"),
        (0.0, 85.0, None),
    ],
)
def test_utm_zone_selection(lon, lat, expected):
    assert utm_crs_for(lon, lat) == expected


def test_unexpected_exception_becomes_an_error_result(monkeypatch):
    measurer = Measurer("EPSG:4326")

    def boom(self, geom):
        raise RuntimeError("boom")

    monkeypatch.setattr(Measurer, "_measure", boom)
    result = measurer.measure(mapping(BENGALURU_BOX))
    assert result.status == ERROR
    assert "boom" not in result.note  # internal details are not leaked