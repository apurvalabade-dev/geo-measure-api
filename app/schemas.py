from typing import Any

from pydantic import BaseModel, ConfigDict


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    file_type: str
    feature_count: int
    crs: str | None
    status: str


class FeatureOut(BaseModel):
    feature_index: int
    geometry_type: str | None
    geometry: dict[str, Any] | None
    crs: str | None
    properties: dict[str, Any]


class FeatureMeasurementOut(BaseModel):
    feature_index: int
    geometry_type: str | None
    status: str
    note: str | None = None
    projected_crs: str | None = None
    area_m2: float | None = None
    length_m: float | None = None


class MeasurementSummary(BaseModel):
    measured: int
    skipped: int
    errors: int
    total_area_m2: float
    total_length_m: float


class MeasurementsOut(BaseModel):
    file_id: str
    source_crs: str | None
    feature_count: int
    summary: MeasurementSummary
    limit: int
    offset: int
    features: list[FeatureMeasurementOut]