import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import ALLOWED_EXTENSIONS, MAX_UPLOAD_BYTES, UPLOAD_DIR
from app.db import get_db
from app.models import Feature, UploadedFile, new_id
from app.schemas import (
    FeatureMeasurementOut,
    FeatureOut,
    FileOut,
    MeasurementsOut,
    MeasurementSummary,
)
from app.services.measurement import ERROR, OK, SKIPPED, Measurer
from app.services.parser import ParsedFeature, ParseError, parse_file

router = APIRouter(prefix="/api/files", tags=["files"])


@router.post("/", response_model=FileOut, status_code=201)
def upload_file(file: UploadFile, db: Session = Depends(get_db)) -> UploadedFile:
    """Upload a `.kml` file or a `.zip` containing a Shapefile and extract its features."""
    filename = Path(file.filename or "").name
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, "Unsupported file type. Upload a .kml or a .zip containing a shapefile.")

    file_id = new_id()
    # The stored name never comes from the client, so it cannot escape UPLOAD_DIR.
    upload_dir = UPLOAD_DIR / file_id
    upload_dir.mkdir(parents=True)
    stored_path = upload_dir / f"original{extension}"

    try:
        _save_upload(file, stored_path)
        parsed = parse_file(stored_path, extension)
        measurer = Measurer(parsed.crs)

        record = UploadedFile(
            id=file_id,
            filename=filename[:255],
            file_type=parsed.file_type,
            status="COMPLETED",
            crs=parsed.crs,
            feature_count=len(parsed.features),
            features=[_build_feature(feature, measurer) for feature in parsed.features],
        )
        db.add(record)
        db.commit()
    except ParseError as exc:
        shutil.rmtree(upload_dir, ignore_errors=True)
        raise HTTPException(422, str(exc)) from exc
    except Exception:
        shutil.rmtree(upload_dir, ignore_errors=True)
        db.rollback()
        raise

    return record


@router.get("/{file_id}/", response_model=FileOut)
def get_file(file_id: str, db: Session = Depends(get_db)) -> UploadedFile:
    """Return information about an uploaded file."""
    return _get_file_or_404(db, file_id)


@router.get("/{file_id}/features/", response_model=list[FeatureOut])
def list_features(
    file_id: str,
    limit: int = Query(1000, ge=1, le=10_000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[FeatureOut]:
    """Return the extracted features: index, geometry type, geometry, CRS and properties."""
    record = _get_file_or_404(db, file_id)
    rows = db.scalars(_features_query(file_id, limit, offset)).all()
    return [
        FeatureOut(
            feature_index=row.feature_index,
            geometry_type=row.geometry_type,
            geometry=row.geometry,
            crs=record.crs,
            properties=row.properties,
        )
        for row in rows
    ]


@router.get("/{file_id}/measurements/", response_model=MeasurementsOut)
def get_measurements(
    file_id: str,
    limit: int = Query(1000, ge=1, le=10_000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> MeasurementsOut:
    """Return the area / length of every feature, plus totals for the whole file."""
    record = _get_file_or_404(db, file_id)

    counts = dict(
        db.execute(
            select(Feature.measurement_status, func.count()).where(Feature.file_id == file_id).group_by(Feature.measurement_status)
        ).all()
    )
    total_area, total_length = db.execute(
        select(func.coalesce(func.sum(Feature.area_m2), 0.0), func.coalesce(func.sum(Feature.length_m), 0.0)).where(
            Feature.file_id == file_id
        )
    ).one()

    rows = db.scalars(_features_query(file_id, limit, offset)).all()
    return MeasurementsOut(
        file_id=record.id,
        source_crs=record.crs,
        feature_count=record.feature_count,
        summary=MeasurementSummary(
            measured=counts.get(OK, 0),
            skipped=counts.get(SKIPPED, 0),
            errors=counts.get(ERROR, 0),
            total_area_m2=total_area,
            total_length_m=total_length,
        ),
        limit=limit,
        offset=offset,
        features=[
            FeatureMeasurementOut(
                feature_index=row.feature_index,
                geometry_type=row.geometry_type,
                status=row.measurement_status or SKIPPED,
                note=row.measurement_note,
                projected_crs=row.projected_crs,
                area_m2=row.area_m2,
                length_m=row.length_m,
            )
            for row in rows
        ],
    )


def _get_file_or_404(db: Session, file_id: str) -> UploadedFile:
    record = db.get(UploadedFile, file_id)
    if record is None:
        raise HTTPException(404, "File not found.")
    return record


def _features_query(file_id: str, limit: int, offset: int):
    return select(Feature).where(Feature.file_id == file_id).order_by(Feature.feature_index).limit(limit).offset(offset)


def _build_feature(feature: ParsedFeature, measurer: Measurer) -> Feature:
    measurement = measurer.measure(feature.geometry)
    return Feature(
        feature_index=feature.index,
        geometry_type=feature.geometry_type,
        geometry=feature.geometry,
        properties=feature.properties,
        measurement_status=measurement.status,
        measurement_note=measurement.note,
        projected_crs=measurement.projected_crs,
        area_m2=measurement.area_m2,
        length_m=measurement.length_m,
    )


def _save_upload(upload: UploadFile, destination: Path) -> None:
    """Stream the upload to disk, enforcing the size limit as we go."""
    size = 0
    with destination.open("wb") as out:
        while chunk := upload.file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                raise HTTPException(413, f"File too large (maximum {MAX_UPLOAD_BYTES // (1024 * 1024)} MB).")
            out.write(chunk)
    if size == 0:
        raise ParseError("The uploaded file is empty.")