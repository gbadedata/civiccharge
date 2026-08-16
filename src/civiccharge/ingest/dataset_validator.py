import csv
import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from civiccharge.ingest.acquisition import (
    AcquisitionFailureReason,
    AcquisitionStatus,
    DatasetSource,
    FailedAcquisitionResult,
)
from civiccharge.ingest.http_downloader import DownloadedArtifact
from civiccharge.ingest.provenance import Sha256Digest, SourceFormat

COMMON_REQUIRED_COLUMNS = frozenset(
    {
        "dataset",
        "entity",
        "organisation-entity",
        "reference",
        "name",
        "document-url",
        "documentation-url",
    }
)

DATASET_REQUIRED_COLUMNS = {
    "article-4-direction": frozenset(
        {
            "description",
        }
    ),
    "conservation-area-document": frozenset(
        {
            "document-type",
            "conservation-area",
        }
    ),
}


class ValidatedDatasetArtifact(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    validation_status: Literal["validated"] = "validated"
    staging_path: Path
    byte_size: int = Field(gt=0)
    sha256: Sha256Digest
    observed_entity_count: int = Field(gt=0)
    columns: tuple[str, ...]


type DatasetValidationResult = ValidatedDatasetArtifact | FailedAcquisitionResult


def _quarantined(
    artifact: DownloadedArtifact,
    *,
    reason: AcquisitionFailureReason,
    detail: str,
    observed_entity_count: int | None = None,
) -> FailedAcquisitionResult:
    return FailedAcquisitionResult(
        status=AcquisitionStatus.QUARANTINED,
        attempts=artifact.attempts,
        failure_reason=reason,
        detail=detail,
        byte_size=artifact.byte_size,
        sha256=artifact.sha256,
        observed_entity_count=observed_entity_count,
    )


def _filesystem_failure(
    artifact: DownloadedArtifact,
    error: OSError,
) -> FailedAcquisitionResult:
    message = str(error).strip()
    detail = f"{type(error).__name__}: {message}" if message else type(error).__name__

    return FailedAcquisitionResult(
        status=AcquisitionStatus.REJECTED,
        attempts=artifact.attempts,
        failure_reason=AcquisitionFailureReason.FILESYSTEM_ERROR,
        detail=detail,
        byte_size=artifact.byte_size,
        sha256=artifact.sha256,
    )


def _calculate_file_evidence(
    path: Path,
) -> tuple[int, str]:
    digest = hashlib.sha256()
    byte_size = 0

    with path.open("rb") as source:
        while chunk := source.read(64 * 1024):
            digest.update(chunk)
            byte_size += len(chunk)

    return byte_size, digest.hexdigest()


def _required_columns(
    source: DatasetSource,
) -> frozenset[str]:
    dataset_specific = DATASET_REQUIRED_COLUMNS.get(
        source.dataset.value,
        frozenset(),
    )

    return COMMON_REQUIRED_COLUMNS | dataset_specific


def validate_csv_artifact(
    artifact: DownloadedArtifact,
    source: DatasetSource,
    *,
    expected_entity_count: int,
) -> DatasetValidationResult:
    if source.source_format != SourceFormat.CSV:
        raise ValueError("validate_csv_artifact only supports CSV sources")

    if expected_entity_count < 0:
        raise ValueError("expected_entity_count must not be negative")

    try:
        actual_byte_size, actual_sha256 = _calculate_file_evidence(artifact.staging_path)
    except OSError as error:
        return _filesystem_failure(
            artifact,
            error,
        )

    if actual_byte_size != artifact.byte_size:
        return _quarantined(
            artifact,
            reason=AcquisitionFailureReason.INVALID_CONTENT,
            detail=(
                "Artifact byte size changed after download: "
                f"expected {artifact.byte_size}, "
                f"observed {actual_byte_size}"
            ),
        )

    if actual_sha256 != artifact.sha256:
        return _quarantined(
            artifact,
            reason=AcquisitionFailureReason.INVALID_CONTENT,
            detail=("Artifact SHA-256 changed after download"),
        )

    observed_entity_count = 0
    seen_entities: set[int] = set()

    try:
        with artifact.staging_path.open(
            "r",
            encoding="utf-8-sig",
            newline="",
        ) as source_file:
            reader = csv.reader(
                source_file,
                strict=True,
            )

            try:
                header = next(reader)
            except StopIteration:
                return _quarantined(
                    artifact,
                    reason=(AcquisitionFailureReason.INVALID_CONTENT),
                    detail="CSV does not contain a header row",
                )

            columns = tuple(header)

            if any(not column or column != column.strip() for column in columns):
                return _quarantined(
                    artifact,
                    reason=(AcquisitionFailureReason.INVALID_CONTENT),
                    detail=("CSV contains blank or whitespace-padded column names"),
                )

            if len(set(columns)) != len(columns):
                return _quarantined(
                    artifact,
                    reason=(AcquisitionFailureReason.INVALID_CONTENT),
                    detail=("CSV contains duplicate column names"),
                )

            required = _required_columns(source)
            missing = sorted(required - set(columns))

            if missing:
                return _quarantined(
                    artifact,
                    reason=(AcquisitionFailureReason.INVALID_CONTENT),
                    detail=("CSV is missing required columns: " + ", ".join(missing)),
                )

            for row_number, values in enumerate(
                reader,
                start=2,
            ):
                observed_entity_count += 1

                if len(values) != len(columns):
                    return _quarantined(
                        artifact,
                        reason=(AcquisitionFailureReason.INVALID_CONTENT),
                        detail=(
                            f"Row {row_number} has {len(values)} fields; expected {len(columns)}"
                        ),
                        observed_entity_count=(observed_entity_count),
                    )

                row = dict(
                    zip(
                        columns,
                        values,
                        strict=True,
                    )
                )

                dataset_value = (row.get("dataset") or "").strip()

                if dataset_value != source.dataset.value:
                    return _quarantined(
                        artifact,
                        reason=(AcquisitionFailureReason.INVALID_CONTENT),
                        detail=(
                            f"Row {row_number} has dataset "
                            f"{dataset_value!r}; expected "
                            f"{source.dataset.value!r}"
                        ),
                        observed_entity_count=(observed_entity_count),
                    )

                entity_value = (row.get("entity") or "").strip()

                try:
                    entity = int(entity_value)
                except ValueError:
                    return _quarantined(
                        artifact,
                        reason=(AcquisitionFailureReason.INVALID_CONTENT),
                        detail=(f"Row {row_number} has invalid entity identifier {entity_value!r}"),
                        observed_entity_count=(observed_entity_count),
                    )

                if entity <= 0:
                    return _quarantined(
                        artifact,
                        reason=(AcquisitionFailureReason.INVALID_CONTENT),
                        detail=(f"Row {row_number} has non-positive entity identifier {entity}"),
                        observed_entity_count=(observed_entity_count),
                    )

                if entity in seen_entities:
                    return _quarantined(
                        artifact,
                        reason=(AcquisitionFailureReason.INVALID_CONTENT),
                        detail=(f"Duplicate entity identifier {entity} at row {row_number}"),
                        observed_entity_count=(observed_entity_count),
                    )

                seen_entities.add(entity)

    except (UnicodeDecodeError, csv.Error) as error:
        return _quarantined(
            artifact,
            reason=AcquisitionFailureReason.INVALID_CONTENT,
            detail=(f"{type(error).__name__}: {error}"),
            observed_entity_count=(observed_entity_count),
        )
    except OSError as error:
        return _filesystem_failure(
            artifact,
            error,
        )

    if observed_entity_count == 0:
        return _quarantined(
            artifact,
            reason=AcquisitionFailureReason.INVALID_CONTENT,
            detail="CSV contains a header but no data rows",
            observed_entity_count=0,
        )

    if observed_entity_count != expected_entity_count:
        return _quarantined(
            artifact,
            reason=(AcquisitionFailureReason.ENTITY_COUNT_MISMATCH),
            detail=(
                f"Expected {expected_entity_count} entities but observed {observed_entity_count}"
            ),
            observed_entity_count=(observed_entity_count),
        )

    return ValidatedDatasetArtifact(
        staging_path=artifact.staging_path,
        byte_size=artifact.byte_size,
        sha256=artifact.sha256,
        observed_entity_count=observed_entity_count,
        columns=columns,
    )
