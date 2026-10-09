"""Helpers that build small KML and shapefile fixtures in memory."""

import io
import shutil
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
from shapely.affinity import translate
from shapely.geometry import Polygon

# A shapefile holds ONE geometry type, so the default fixture is all polygons.
BOX = Polygon([(77, 12), (78, 12), (78, 13), (77, 13)])  # 1 deg x 1 deg near Bengaluru


def kml_polygon(name: str = "box", coords: str = "77,12,0 78,12,0 78,13,0 77,13,0 77,12,0") -> str:
    return (
        f"<Placemark><name>{name}</name><Polygon><outerBoundaryIs><LinearRing>"
        f"<coordinates>{coords}</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>"
    )


def kml_line(name: str = "road", coords: str = "77,12,0 77,13,0") -> str:
    return f"<Placemark><name>{name}</name><LineString><coordinates>{coords}</coordinates></LineString></Placemark>"


def kml_point(name: str = "pin", coords: str = "77.5,12.5,0") -> str:
    return f"<Placemark><name>{name}</name><Point><coordinates>{coords}</coordinates></Point></Placemark>"


def make_kml(*placemarks: str, folders: dict[str, list[str]] | None = None) -> bytes:
    body = "".join(placemarks)
    for folder_name, items in (folders or {}).items():
        body += f"<Folder><name>{folder_name}</name>{''.join(items)}</Folder>"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<kml xmlns="http://www.opengis.net/kml/2.2"><Document>{body}</Document></kml>'
    ).encode()


def make_shapefile_zip(gdf: gpd.GeoDataFrame | None = None, *, include_prj: bool = True, drop: tuple[str, ...] = ()) -> bytes:
    if gdf is None:
        gdf = gpd.GeoDataFrame(
            {"name": ["box", "box2", "box3"]},
            geometry=[BOX, translate(BOX, xoff=2), translate(BOX, xoff=4)],
            crs="EPSG:4326",
        )
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "shp"
        folder.mkdir()
        gdf.to_file(folder / "data.shp")
        if not include_prj:
            (folder / "data.prj").unlink()
        for ext in drop:
            (folder / f"data{ext}").unlink()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for path in folder.iterdir():
                archive.write(path, path.name)
        return buffer.getvalue()


def zip_of(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()