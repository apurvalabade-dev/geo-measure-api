"""Read a KML file or a zipped Shapefile into plain Python objects.

Nothing in here knows about HTTP or the database, which keeps it easy to test.
"""

import json
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import shapely
from pyproj import CRS

from app.config import MAX_FEATURES, MAX_UNCOMPRESSED_BYTES


class ParseError(Exception):
    """The uploaded file is invalid. The message is safe to show to the client."""


@dataclass(frozen=True)
class ParsedFeature:
    index: int
    geometry_type: str | None
    geometry: dict[str, Any] | None  # GeoJSON, in the file's own CRS
    properties: dict[str, Any]


@dataclass(frozen=True)
class ParsedFile:
    file_type: str  # "kml" | "shapefile"
    crs: str
    features: list[ParsedFeature]


def parse_file(path: Path, extension: str) -> ParsedFile:
    """Parse an uploaded file. `extension` is the lower-case suffix, e.g. ".kml"."""
    if extension == ".kml":
        return _to_parsed(_read_kml(path), "kml")
    if extension == ".zip":
        return _to_parsed(_read_shapefile_zip(path), "shapefile")
    raise ParseError(f"Unsupported file type: {extension}")


# --------------------------------------------------------------------- KML


def _read_kml(path: Path) -> gpd.GeoDataFrame:
    # GDAL exposes every KML <Folder> as a separate layer, and read_file only
    # reads the first one, so we read all layers and combine them.
    try:
        layer_names = [name for name, _ in pyogrio.list_layers(path)]
        frames = [gpd.read_file(path, layer=name) for name in layer_names]
    except Exception as exc:
        raise ParseError("Could not read the KML file: it is invalid or corrupt.") from exc

    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        raise ParseError("The KML file contains no features.")

    gdf = pd.concat(frames, ignore_index=True)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")  # KML is always WGS84 by specification
    return gdf


# --------------------------------------------------------------- Shapefile


def _read_shapefile_zip(path: Path) -> gpd.GeoDataFrame:
    if not zipfile.is_zipfile(path):
        raise ParseError("The uploaded file is not a valid zip archive.")

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp).resolve()
        _safe_extract(path, dest)
        shp = _find_shapefile(dest)

        try:
            gdf = gpd.read_file(shp)
        except Exception as exc:
            raise ParseError("Could not read the shapefile: it is invalid or corrupt.") from exc

    if gdf.crs is None:
        raise ParseError(
            "The shapefile has no coordinate reference system. "
            "Include the .prj file in the zip."
        )
    return gdf


def _safe_extract(zip_path: Path, dest: Path) -> None:
    try:
        with zipfile.ZipFile(zip_path) as archive:
            members = archive.infolist()
            if sum(member.file_size for member in members) > MAX_UNCOMPRESSED_BYTES:
                raise ParseError("The zip archive is too large once extracted.")
            for member in members:  # zip-slip: no member may escape `dest`
                if not (dest / member.filename).resolve().is_relative_to(dest):
                    raise ParseError("The zip archive contains unsafe file paths.")
            archive.extractall(dest)
    except zipfile.BadZipFile as exc:
        raise ParseError("The zip archive is corrupt.") from exc
    except (RuntimeError, NotImplementedError) as exc:  # encrypted / exotic compression
        raise ParseError("The zip archive is encrypted or uses unsupported compression.") from exc


def _find_shapefile(root: Path) -> Path:
    candidates = [
        p
        for p in root.rglob("*")
        if p.suffix.lower() == ".shp"
        and "__MACOSX" not in p.parts  # macOS adds junk "._file.shp" entries
        and not p.name.startswith("._")
    ]
    if not candidates:
        raise ParseError("The zip archive does not contain a .shp file.")
    if len(candidates) > 1:
        raise ParseError("The zip contains more than one shapefile. Upload one per zip.")

    shp = candidates[0]
    siblings = {p.name.lower() for p in shp.parent.iterdir()}
    for ext in (".shx", ".dbf"):
        if f"{shp.stem.lower()}{ext}" not in siblings:
            raise ParseError(f"The shapefile is incomplete: the {ext} file is missing.")
    return shp


# ------------------------------------------------------------ Conversion


def _to_parsed(gdf: gpd.GeoDataFrame, file_type: str) -> ParsedFile:
    if gdf.empty:
        raise ParseError("The file contains no features.")
    if len(gdf) > MAX_FEATURES:
        raise ParseError(f"The file has too many features (maximum {MAX_FEATURES}).")

    geometries = list(gdf.geometry)
    attributes = gdf.drop(columns=gdf.geometry.name).to_dict("records")

    features = [
        ParsedFeature(
            index=index,
            geometry_type=geom.geom_type if geom is not None else None,
            geometry=_to_geojson(geom),
            properties=_clean_properties(attrs),
        )
        for index, (geom, attrs) in enumerate(zip(geometries, attributes))
    ]
    return ParsedFile(file_type=file_type, crs=_crs_string(gdf.crs), features=features)


def _clean_properties(attributes: dict[str, Any]) -> dict[str, Any]:
    cleaned = {key: _clean_value(value) for key, value in attributes.items()}
    return {key: value for key, value in cleaned.items() if value is not None}


def _to_geojson(geom: Any) -> dict[str, Any] | None:
    if geom is None or geom.is_empty:
        return None
    return json.loads(shapely.to_geojson(geom))


def _crs_string(crs: CRS) -> str:
    epsg = crs.to_epsg()
    return f"EPSG:{epsg}" if epsg else crs.to_wkt()


def _clean_value(value: Any) -> Any:
    """Make an attribute JSON-safe and turn empty values (NaN, '', NaT) into None."""
    if isinstance(value, np.generic):
        value = value.item()
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, float) and value != value:  # NaN
        return None
    if isinstance(value, str):
        return value if value.strip() else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value