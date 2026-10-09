import pytest

from tests.factories import (
    kml_line,
    kml_point,
    kml_polygon,
    make_kml,
    make_shapefile_zip,
    zip_of,
)


def upload(client, name: str, content: bytes):
    return client.post("/api/files/", files={"file": (name, content)})


def test_upload_kml_returns_file_info(client):
    response = upload(client, "survey.kml", make_kml(kml_polygon(), kml_line(), kml_point()))
    assert response.status_code == 201
    body = response.json()
    assert body["filename"] == "survey.kml"
    assert body["file_type"] == "kml"
    assert body["feature_count"] == 3
    assert body["crs"] == "EPSG:4326"
    assert body["status"] == "COMPLETED"
    assert body["id"]


def test_upload_kml_reads_features_from_every_folder(client):
    kml = make_kml(kml_polygon("loose"), folders={"A": [kml_line("a1")], "B": [kml_point("b1"), kml_point("b2")]})
    response = upload(client, "folders.kml", kml)
    assert response.status_code == 201
    assert response.json()["feature_count"] == 4


def test_upload_zipped_shapefile(client):
    response = upload(client, "survey.zip", make_shapefile_zip())
    assert response.status_code == 201
    body = response.json()
    assert body["file_type"] == "shapefile"
    assert body["feature_count"] == 3
    assert body["crs"] == "EPSG:4326"


def test_get_file_info(client):
    file_id = upload(client, "survey.kml", make_kml(kml_polygon())).json()["id"]
    response = client.get(f"/api/files/{file_id}/")
    assert response.status_code == 200
    assert response.json()["id"] == file_id
    assert response.json()["feature_count"] == 1


def test_get_unknown_file_returns_404(client):
    assert client.get("/api/files/doesnotexist/").status_code == 404


def test_rejects_unsupported_extension(client):
    response = upload(client, "notes.txt", b"hello")
    assert response.status_code == 400


def test_rejects_empty_file(client):
    assert upload(client, "empty.kml", b"").status_code == 422


def test_rejects_invalid_kml(client):
    assert upload(client, "bad.kml", b"this is not xml").status_code == 422


def test_rejects_kml_without_features(client):
    assert upload(client, "none.kml", make_kml()).status_code == 422


def test_rejects_corrupt_zip(client):
    assert upload(client, "bad.zip", b"definitely not a zip").status_code == 422


def test_rejects_zip_without_shp(client):
    response = upload(client, "nope.zip", zip_of({"readme.txt": b"hi"}))
    assert response.status_code == 422
    assert ".shp" in response.json()["detail"]


def test_rejects_shapefile_without_prj(client):
    response = upload(client, "noprj.zip", make_shapefile_zip(include_prj=False))
    assert response.status_code == 422
    assert "coordinate reference system" in response.json()["detail"]


@pytest.mark.parametrize("missing", [".shx", ".dbf"])
def test_rejects_incomplete_shapefile(client, missing):
    response = upload(client, "partial.zip", make_shapefile_zip(drop=(missing,)))
    assert response.status_code == 422


def test_rejects_zip_slip(client):
    response = upload(client, "evil.zip", zip_of({"../../evil.shp": b"x"}))
    assert response.status_code == 422


def test_failed_upload_is_not_stored(client):
    upload(client, "bad.kml", b"this is not xml")
    from app.config import UPLOAD_DIR

    leftovers = [p for p in UPLOAD_DIR.iterdir() if not any(p.iterdir())]
    assert leftovers == []