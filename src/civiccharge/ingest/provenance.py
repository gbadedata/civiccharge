from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    TypeAdapter,
    field_validator,
    model_validator,
)

from civiccharge.ingest.planning_data_models import (
    CanonicalDatasetMetadata,
    PlanningDataset,
)


class SourceFormat(StrEnum):
    JSON = "json"
    CSV = "csv"
    GEOJSON = "geojson"
    PARQUET = "parquet"


Sha256Digest = Annotated[
    str,
    Field(pattern=r"^[0-9a-f]{64}$"),
]

_HTTP_URL_ADAPTER = TypeAdapter(HttpUrl)


def build_snapshot_id(
    dataset: PlanningDataset,
    sha256: str,
) -> str:
    return f"{dataset.value}-{sha256}"


class SourceSnapshot(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    snapshot_id: str
    dataset: PlanningDataset
    source_url: HttpUrl
    retrieved_at: datetime
    source_format: SourceFormat
    sha256: Sha256Digest
    byte_size: int = Field(gt=0)

    declared_entity_count: int = Field(ge=0)
    observed_entity_count: int = Field(ge=0)

    licence: str
    attribution: str
    phase: str

    upstream_api_version: str | None = None
    upstream_spec_version: str | None = None

    relative_path: str

    manifest_schema_version: Literal["1.0"] = "1.0"

    @field_validator("retrieved_at")
    @classmethod
    def normalise_retrieved_at(
        cls,
        value: datetime,
    ) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware")

        return value.astimezone(UTC)

    @field_validator("licence", "attribution", "phase")
    @classmethod
    def require_non_empty_text(
        cls,
        value: str,
    ) -> str:
        cleaned = value.strip()

        if not cleaned:
            raise ValueError("value must not be blank")

        return cleaned

    @field_validator(
        "upstream_api_version",
        "upstream_spec_version",
    )
    @classmethod
    def normalise_optional_text(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        cleaned = value.strip()
        return cleaned or None

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(
        cls,
        value: str,
    ) -> str:
        cleaned = value.strip()

        if not cleaned:
            raise ValueError("relative_path must not be blank")

        if "\\" in cleaned:
            raise ValueError("relative_path must use POSIX separators")

        path = PurePosixPath(cleaned)

        if path.is_absolute():
            raise ValueError("relative_path must not be absolute")

        if ".." in path.parts:
            raise ValueError("relative_path must not contain parent traversal")

        return path.as_posix()

    @model_validator(mode="after")
    def validate_snapshot_integrity(
        self,
    ) -> "SourceSnapshot":
        expected_snapshot_id = build_snapshot_id(
            self.dataset,
            self.sha256,
        )

        if self.snapshot_id != expected_snapshot_id:
            raise ValueError("snapshot_id does not match dataset and sha256")

        return self

    @property
    def entity_count_matches_metadata(self) -> bool:
        return self.declared_entity_count == self.observed_entity_count


def create_source_snapshot(
    metadata: CanonicalDatasetMetadata,
    *,
    source_url: str,
    retrieved_at: datetime,
    source_format: SourceFormat,
    sha256: str,
    byte_size: int,
    observed_entity_count: int,
    relative_path: str,
    upstream_api_version: str | None = None,
    upstream_spec_version: str | None = None,
) -> SourceSnapshot:
    validated_source_url = _HTTP_URL_ADAPTER.validate_python(source_url)

    return SourceSnapshot(
        snapshot_id=build_snapshot_id(
            metadata.dataset,
            sha256,
        ),
        dataset=metadata.dataset,
        source_url=validated_source_url,
        retrieved_at=retrieved_at,
        source_format=source_format,
        sha256=sha256,
        byte_size=byte_size,
        declared_entity_count=metadata.entity_count,
        observed_entity_count=observed_entity_count,
        licence=metadata.licence,
        attribution=metadata.attribution,
        phase=metadata.phase,
        upstream_api_version=upstream_api_version,
        upstream_spec_version=upstream_spec_version,
        relative_path=relative_path,
    )
