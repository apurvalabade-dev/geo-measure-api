# Geospatial File Measurement API

A FastAPI service that accepts a **KML file** or a **zipped Shapefile**, extracts every feature,
and returns **area (polygons)** and **length (lines)** measurements in metres.

Measurements are never taken in degrees: each geometry is reprojected to the UTM zone of its
centroid first (see [CRS handling](#crs-handling)).

- Python / FastAPI / SQLAlchemy / SQLite
- geopandas (pyogrio), shapely, pyproj for the geospatial work
- 53 automated tests (pytest)

---

## Setup

Developed and tested on **Python 3.14** (GitHub Codespaces). Other versions are untested.

```bash
git clone https://github.com/<your-username>/geo-measure-api.git
cd geo-measure-api

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

## Run

```bash
uvicorn app.main:app --reload
```

- API: http://127.0.0.1:8000
- Interactive docs (Swagger UI): http://127.0.0.1:8000/docs

The SQLite database (`geo.db`) and the `uploads/` folder are created automatically on first start.
Both can be moved with environment variables: `DATABASE_URL` and `UPLOAD_DIR`.

## Run the tests

```bash
pytest
```

Tests use a temporary database and upload folder, so they never touch your real data.

---

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/files/` | Upload a `.kml` or a `.zip` containing a Shapefile; parses and measures it |
| `GET` | `/api/files/{id}/` | File information |
| `GET` | `/api/files/{id}/measurements/` | Area / length per feature, plus totals |
| `GET` | `/api/files/{id}/features/` | Extracted features: index, geometry type, geometry, CRS, properties |
| `GET` | `/health` | Liveness check |

Both list endpoints accept `limit` (default 1000, max 10000) and `offset` (default 0).

### Upload a file

```bash
curl -F "file=@survey.kml" http://127.0.0.1:8000/api/files/
```

`201 Created`
```json
{
  "id": "db18276dc59e45ae822f22af84576465",
  "filename": "survey.kml",
  "file_type": "kml",
  "feature_count": 3,
  "crs": "EPSG:4326",
  "status": "COMPLETED"
}
```

A zipped Shapefile is uploaded the same way (`-F "file=@survey.zip"`). The zip must contain the
`.shp`, `.shx`, `.dbf` and `.prj` files.

### File information

```bash
curl http://127.0.0.1:8000/api/files/db18276dc59e45ae822f22af84576465/
```

Returns the same body as the upload response.

### Measurements

```bash
curl http://127.0.0.1:8000/api/files/db18276dc59e45ae822f22af84576465/measurements/
```

`200 OK` (a KML with one polygon, one line and one point)
```json
{
  "file_id": "db18276dc59e45ae822f22af84576465",
  "source_crs": "EPSG:4326",
  "feature_count": 3,
  "summary": {
    "measured": 2,
    "skipped": 1,
    "errors": 0,
    "total_area_m2": 12037295141.475117,
    "total_length_m": 110646.74044832421
  },
  "limit": 1000,
  "offset": 0,
  "features": [
    {
      "feature_index": 0,
      "geometry_type": "Polygon",
      "status": "OK",
      "note": null,
      "projected_crs": "EPSG:32643",
      "area_m2": 12037295141.475117,
      "length_m": null
    },
    {
      "feature_index": 1,
      "geometry_type": "LineString",
      "status": "OK",
      "note": null,
      "projected_crs": "EPSG:32643",
      "area_m2": null,
      "length_m": 110646.74044832421
    },
    {
      "feature_index": 2,
      "geometry_type": "Point",
      "status": "SKIPPED",
      "note": "Point has no area or length.",
      "projected_crs": null,
      "area_m2": null,
      "length_m": null
    }
  ]
}
```

Feature `status` values:

| Status | Meaning |
|---|---|
| `OK` | Measured. `note` may carry a warning (e.g. the polygon is invalid) |
| `SKIPPED` | Nothing to measure by design: points, empty or missing geometry, unsupported types such as `GeometryCollection` |
| `ERROR` | Measurement was attempted and failed; the rest of the file is unaffected |

`summary` totals cover the whole file, not just the current page.

### Features

```bash
curl "http://127.0.0.1:8000/api/files/db18276dc59e45ae822f22af84576465/features/?limit=1"
```

```json
[
  {
    "feature_index": 0,
    "geometry_type": "Polygon",
    "geometry": {
      "type": "Polygon",
      "coordinates": [[[77.0, 12.0, 0.0], [78.0, 12.0, 0.0], [78.0, 13.0, 0.0], [77.0, 13.0, 0.0], [77.0, 12.0, 0.0]]]
    },
    "crs": "EPSG:4326",
    "properties": { "Name": "box", "tessellate": -1, "extrude": 0, "visibility": -1 }
  }
]
```

Geometry is GeoJSON in the file's **original** CRS. Empty attribute values are dropped.

### Errors

| Code | When |
|---|---|
| `400` | Extension is not `.kml` or `.zip` |
| `404` | Unknown file id |
| `413` | Upload is larger than 50 MB |
| `422` | The file is invalid: corrupt zip, no `.shp` inside, missing `.shx`/`.dbf`, **missing `.prj`**, unreadable or empty KML, no features, too many features, or more than one shapefile in the zip |

Errors look like `{"detail": "The zip archive does not contain a .shp file."}`.
Rejected uploads store nothing and leave no files behind.

---

## Try it with the sample files

The `samples/` folder has ready-made inputs. With the server running:

```bash
curl -F "file=@samples/survey.kml" http://127.0.0.1:8000/api/files/
# then: curl http://127.0.0.1:8000/api/files/<id>/measurements/
```

Or run them all at once:

```bash
for f in samples/*; do echo "== $f"; curl -s -F "file=@$f" http://127.0.0.1:8000/api/files/; echo; done
```

| File | What it tests | Expected result |
|---|---|---|
| `survey.kml` | Mixed geometry in one KML | `201`, 3 features. Polygon ≈ 12,037,295,141 m² (UTM 43N), line ≈ 110,646.7 m, point `SKIPPED` |
| `folders.kml` | Features spread over several KML folders, a polygon with a hole, features in different UTM zones | `201`, 5 features. Polygon with hole ≈ 9,027,990,921 m² (outer box minus the hole). `plot-B` is measured in UTM 44N (`EPSG:32644`), the rest in 43N. Two points `SKIPPED` |
| `plots.zip` | Zipped Shapefile in EPSG:4326 | `201`, `file_type: shapefile`, 3 polygons of about 120.5 km² each |
| `roads_utm.zip` | Shapefile that is **already projected** (EPSG:32643) | `201`, `crs: EPSG:32643`, two lines of exactly 1000.0 m |
| `no_prj.zip` | Shapefile without a `.prj` | `422`: no coordinate reference system |
| `no_shp.zip` | Zip with no `.shp` inside | `422`: archive does not contain a `.shp` file |
| `not_a_zip.zip` | A text file pretending to be a zip | `422`: not a valid zip archive |

Other checks you can do by hand:

```bash
curl -s -F "file=@samples/survey.kml;filename=notes.txt" http://127.0.0.1:8000/api/files/   # 400 unsupported type
curl -s http://127.0.0.1:8000/api/files/does-not-exist/                                      # 404
curl -s "http://127.0.0.1:8000/api/files/<id>/measurements/?limit=1&offset=1"                # paging
```

---

## Architecture

### Structure

```
app/
├── main.py                 FastAPI app, router registration, startup (creates tables)
├── config.py               Paths, upload limits, env overrides
├── db.py                   SQLAlchemy engine, session dependency
├── models.py               UploadedFile, Feature
├── schemas.py              Pydantic response models
├── routers/files.py        HTTP layer: validation, orchestration, status codes
└── services/
    ├── parser.py           KML / zipped Shapefile  ->  plain Python objects (no HTTP, no DB)
    └── measurement.py      GeoJSON geometry        ->  area / length (no HTTP, no DB)
tests/                      parser, measurement and API tests
samples/                    example KML / Shapefile inputs, including invalid ones
```

The HTTP layer only orchestrates. Parsing and measuring are plain functions/classes, which keeps
them easy to unit test.

### File-processing flow

1. `POST /api/files/` checks the extension.
2. The upload is streamed to `uploads/<random id>/original.<ext>` in 1 MB chunks, enforcing the size
   limit while streaming. The client-supplied filename is never used as a path.
3. **KML:** every layer is read and combined (GDAL treats each `<Folder>` as a layer).
   **Shapefile:** the zip is checked and extracted to a temp directory (zip-slip and size guards), exactly
   one `.shp` plus its `.shx` and `.dbf` is required, and a missing `.prj` is rejected.
4. For each feature the parser records index, geometry type, GeoJSON geometry and cleaned properties.
5. Each feature is measured (below); the file, its features and their measurements are saved in one
   transaction.
6. On any failure the saved upload is deleted and nothing is written to the database.

### Measurement flow

```
GeoJSON geometry (file CRS)
  -> skip points / empty / unsupported types
  -> drop Z values
  -> reproject to WGS84 lon/lat
  -> pick UTM zone from the centroid
  -> reproject to that UTM zone
  -> Polygon/MultiPolygon: .area (m²)    LineString/MultiLineString: .length (m)
```

Results are computed once at upload and stored; `GET .../measurements/` only reads them.

### CRS handling

- **Why not measure in EPSG:4326:** the units are degrees. One degree of longitude is about 111 km at
  the equator and shrinks to zero at the poles, so a "square degree" has no fixed area.
- **Strategy:** UTM zone chosen from the centroid. Zone = `floor((lon + 180) / 6) + 1`, EPSG `326xx`
  for the northern hemisphere and `327xx` for the southern. Bengaluru (77.5°E) falls in zone 43N,
  `EPSG:32643`.
- **Projected input files** are also reprojected through WGS84 to UTM. Measuring directly in, say, Web
  Mercator would inflate areas away from the equator.
- **Beyond UTM's range** (latitude above 84° or below -80°) the service measures on the WGS84 ellipsoid
  with `pyproj.Geod` instead, and says so in `note`.
- **Cross-check:** the tests compare UTM results with ellipsoidal (geodesic) results from
  `pyproj.Geod`.

---

## Design decisions

| Decision | Chosen | Alternatives considered | Why |
|---|---|---|---|
| Processing | **Synchronous**, with limits (50 MB, 50,000 features) | Background tasks / Celery + Redis | The work is bounded and fast at these limits, and the spec has no status polling. A queue would add infrastructure that I could not justify. The cost: a very large file blocks one worker for its duration. |
| Invalid files | **Reject with 4xx, store nothing** | Store a `FAILED` record | The client gets an immediate, specific error. `status` is always `COMPLETED` for stored files today; `PROCESSING` / `FAILED` would become meaningful if processing became asynchronous. |
| Missing `.prj` | **Reject** | Assume EPSG:4326 | A wrong guess produces wrong areas with no error. |
| Projection | **UTM from centroid** | Local equal-area projection; geodesic for everything | UTM is the familiar metre-based standard and conformal (good for lengths). It is not equal-area, so areas carry roughly 0.1% error (the sample above: 12,037 km² vs 12,025 km² geodesic). A local Lambert azimuthal equal-area projection would give exact areas but distorts lengths and needs custom PROJ strings. |
| When to measure | **At upload**, stored | On every read | Reads are cheap and consistent. Re-measuring would be needed only if the algorithm changes. |
| Geometry storage | GeoJSON in a JSON column | PostGIS | Keeps setup to one `pip install`. No spatial queries are needed here. |
| Database | SQLite via SQLAlchemy | PostgreSQL | Zero setup for reviewers. `DATABASE_URL` can point elsewhere (not tested on other databases). |
| Multiple shapefiles in one zip | Reject | Merge them | Ambiguous: layers may differ in CRS and fields. |
| Invalid polygons | Measure, add a warning | Reject or auto-repair | Auto-repair silently changes the geometry; rejecting loses a feature that may still be useful. |
| Measurer lifetime | One per upload | Global cache | pyproj transformers are slow to build, so they are cached, but not shared across requests/threads. |
| `def` vs `async def` endpoints | Plain `def` | `async def` | Parsing and projection are blocking CPU/disk work; FastAPI runs `def` endpoints in a threadpool so the event loop is not blocked. |

## Limitations

- UTM area error of about 0.1%, larger near zone edges.
- Features that cross a UTM zone boundary or the antimeridian are measured in the centroid's zone.
- Large files are held in memory and processed in one request.
- Original uploads are kept in `uploads/` for traceability and are never cleaned up.
- Tables are created with `create_all`; there are no migrations.
- No authentication, rate limiting or delete endpoint.
- KMZ, network links and ground overlays are not supported.
- KML features include GDAL's rendering fields (`tessellate`, `extrude`, `visibility`) in `properties`.

---

## Learnings

- **KML folders are separate layers in GDAL.** Reading a KML the obvious way returns only the first folder
  and silently drops the rest. I found this while designing the parser, and a test now covers it.
- **A Shapefile holds exactly one geometry type.** My first test fixture mixed points, lines and polygons
  and could not even be written. Mixed geometries in practice come from KML.
- **macOS zips contain junk entries** like `__MACOSX/._file.shp` that look like a second shapefile.
- **UTM is conformal, not equal-area.** Comparing against `pyproj.Geod` showed a 0.1% area difference
  that my first, tighter test tolerance did not allow for. That is what led me to document it.
- **Python as a second backend language.** *(Rewrite this in your own words: what was different coming
  from Java / Node.js, and what you would do differently next time.)*

## Future scope

- Background processing with a job queue and `PROCESSING` / `FAILED` states for large files.
- PostGIS for spatial queries (features within a bounding box, intersections).
- Choose the measurement method per request (UTM, equal-area, geodesic).
- Object storage and a retention policy for uploads; delete endpoint.
- Database migrations (Alembic) and PostgreSQL in CI.
- KMZ and GeoJSON input, and shapefile encoding (`.cpg`) handling.
- Authentication and per-user file ownership.