"""The files in samples/ are documented in the README; this keeps the two honest."""

from pathlib import Path

import pytest

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def upload(client, name: str):
    return client.post("/api/files/", files={"file": (name, (SAMPLES / name).read_bytes())})


def measurements(client, name: str) -> dict:
    response = upload(client, name)
    assert response.status_code == 201, response.text
    return client.get(f"/api/files/{response.json()['id']}/measurements/").json()


def test_survey_kml(client):
    body = measurements(client, "survey.kml")
    assert body["summary"]["measured"] == 2 and body["summary"]["skipped"] == 1
    assert body["summary"]["total_area_m2"] == pytest.approx(12_037_295_141, rel=1e-6)
    assert body["summary"]["total_length_m"] == pytest.approx(110_646.7, rel=1e-5)


def test_folders_kml_reads_every_folder_and_subtracts_holes(client):
    body = measurements(client, "folders.kml")
    assert body["feature_count"] == 5
    assert body["summary"]["skipped"] == 2
    plot_b, holed, road, *_ = body["features"]
    assert plot_b["projected_crs"] == "EPSG:32644"  # lon 79-79.5 is UTM zone 44
    assert holed["area_m2"] == pytest.approx(9_027_990_921, rel=1e-6)
    assert road["projected_crs"] == "EPSG:32643"


def test_plots_shapefile(client):
    body = measurements(client, "plots.zip")
    assert body["feature_count"] == 3 and body["summary"]["measured"] == 3
    assert all(f["area_m2"] == pytest.approx(120.5e6, rel=2e-3) for f in body["features"])


def test_already_projected_shapefile(client):
    response = upload(client, "roads_utm.zip")
    assert response.json()["crs"] == "EPSG:32643"
    body = client.get(f"/api/files/{response.json()['id']}/measurements/").json()
    assert [f["length_m"] for f in body["features"]] == [pytest.approx(1000.0, abs=1e-3)] * 2


@pytest.mark.parametrize(
    ("name", "expected_detail"),
    [
        ("no_prj.zip", "coordinate reference system"),
        ("no_shp.zip", ".shp"),
        ("not_a_zip.zip", "not a valid zip"),
    ],
)
def test_invalid_samples_are_rejected(client, name, expected_detail):
    response = upload(client, name)
    assert response.status_code == 422
    assert expected_detail in response.json()["detail"]