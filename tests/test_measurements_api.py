import pytest
from pyproj import Geod
from shapely.geometry import box

from app.services.measurement import Measurer
from tests.factories import kml_line, kml_point, kml_polygon, make_kml, make_shapefile_zip


def upload(client, name: str, content: bytes) -> str:
    response = client.post("/api/files/", files={"file": (name, content)})
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.fixture
def mixed_file_id(client) -> str:
    return upload(client, "mixed.kml", make_kml(kml_polygon("box"), kml_line("road"), kml_point("pin")))


def test_measurements_for_mixed_geometry_file(client, mixed_file_id):
    response = client.get(f"/api/files/{mixed_file_id}/measurements/")
    assert response.status_code == 200
    body = response.json()

    assert body["source_crs"] == "EPSG:4326"
    assert body["feature_count"] == 3
    assert body["summary"]["measured"] == 2
    assert body["summary"]["skipped"] == 1
    assert body["summary"]["errors"] == 0

    polygon, line, point = body["features"]
    expected_area = abs(Geod(ellps="WGS84").geometry_area_perimeter(box(77, 12, 78, 13))[0])
    assert polygon["geometry_type"] == "Polygon"
    assert polygon["area_m2"] == pytest.approx(expected_area, rel=3e-3)
    assert polygon["length_m"] is None
    assert polygon["projected_crs"] == "EPSG:32643"
    assert line["geometry_type"] == "LineString"
    assert line["length_m"] == pytest.approx(110_700, rel=1e-2)
    assert line["area_m2"] is None
    assert point["geometry_type"] == "Point"
    assert point["status"] == "SKIPPED"
    assert point["area_m2"] is None and point["length_m"] is None

    assert body["summary"]["total_area_m2"] == pytest.approx(polygon["area_m2"])
    assert body["summary"]["total_length_m"] == pytest.approx(line["length_m"])


def test_measurements_for_shapefile(client):
    file_id = upload(client, "survey.zip", make_shapefile_zip())
    body = client.get(f"/api/files/{file_id}/measurements/").json()
    assert body["summary"]["measured"] == 3
    assert all(f["area_m2"] == pytest.approx(f["area_m2"]) and f["area_m2"] > 1e9 for f in body["features"])


def test_measurements_pagination(client, mixed_file_id):
    body = client.get(f"/api/files/{mixed_file_id}/measurements/", params={"limit": 1, "offset": 1}).json()
    assert [f["feature_index"] for f in body["features"]] == [1]
    assert body["feature_count"] == 3  # totals ignore paging
    assert body["summary"]["measured"] == 2


def test_measurements_rejects_bad_paging(client, mixed_file_id):
    assert client.get(f"/api/files/{mixed_file_id}/measurements/", params={"limit": 0}).status_code == 422


def test_measurements_for_unknown_file_returns_404(client):
    assert client.get("/api/files/nope/measurements/").status_code == 404


def test_one_failing_feature_does_not_fail_the_upload(client, monkeypatch):
    original = Measurer._measure

    def fail_on_lines(self, geom):
        if geom.geom_type == "LineString":
            raise RuntimeError("simulated failure")
        return original(self, geom)

    monkeypatch.setattr(Measurer, "_measure", fail_on_lines)
    file_id = upload(client, "partial.kml", make_kml(kml_polygon("box"), kml_line("road")))

    body = client.get(f"/api/files/{file_id}/measurements/").json()
    polygon, line = body["features"]
    assert polygon["status"] == "OK" and polygon["area_m2"] > 0
    assert line["status"] == "ERROR" and line["length_m"] is None
    assert body["summary"]["measured"] == 1 and body["summary"]["errors"] == 1


def test_features_endpoint_exposes_geometry_crs_and_properties(client, mixed_file_id):
    response = client.get(f"/api/files/{mixed_file_id}/features/")
    assert response.status_code == 200
    polygon, line, point = response.json()
    assert polygon["feature_index"] == 0
    assert polygon["geometry_type"] == "Polygon"
    assert polygon["geometry"]["type"] == "Polygon"
    assert polygon["crs"] == "EPSG:4326"
    assert polygon["properties"]["Name"] == "box"
    assert point["geometry"]["coordinates"][:2] == [77.5, 12.5]


def test_features_for_unknown_file_returns_404(client):
    assert client.get("/api/files/nope/features/").status_code == 404