import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from civiccharge.ingest.acquisition import (
    AcquisitionFailureReason,
    AcquisitionStatus,
    DatasetSource,
    FailedAcquisitionResult,
    create_dataset_source,
)
from civiccharge.ingest.dataset_validator import (
    ValidatedDatasetArtifact,
    validate_csv_artifact,
)
from civiccharge.ingest.http_downloader import (
    DownloadedArtifact,
)
from civiccharge.ingest.planning_data_models import (
    PlanningDataset,
)
from civiccharge.ingest.provenance import SourceFormat

HEADER = (
    "dataset,entity,organisation-entity,reference,name,document-url,documentation-url,description\n"
)


def _source() -> DatasetSource:
    return create_dataset_source(
        dataset=PlanningDataset.ARTICLE_4_DIRECTION,
        source_url=("https://files.planning.data.gov.uk/dataset/article-4-direction.csv"),
        source_format=SourceFormat.CSV,
    )


def _write_artifact(
    tmp_path: Path,
    content: bytes,
) -> DownloadedArtifact:
    path = tmp_path / "article-4-direction.csv.part"
    path.write_bytes(content)

    return DownloadedArtifact(
        staging_path=path,
        retrieved_at=datetime(
            2026,
            8,
            16,
            12,
            0,
            tzinfo=UTC,
        ),
        attempts=1,
        status_code=200,
        byte_size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        content_type="text/csv",
        response_url=("https://files.planning.data.gov.uk/dataset/article-4-direction.csv"),
    )


def test_valid_csv_is_structurally_validated(
    tmp_path: Path,
) -> None:
    content = (
        HEADER
        + (
            "article-4-direction,6100001,228,A4-1,"
            "Example,http://example.org/a.pdf,"
            "http://example.org/a,A direction\n"
        )
        + (
            "article-4-direction,6100002,228,A4-2,"
            "Second,http://example.org/b.pdf,"
            "http://example.org/b,Another direction\n"
        )
    ).encode()

    artifact = _write_artifact(
        tmp_path,
        content,
    )

    result = validate_csv_artifact(
        artifact,
        _source(),
        expected_entity_count=2,
    )

    assert isinstance(
        result,
        ValidatedDatasetArtifact,
    )
    assert result.observed_entity_count == 2
    assert result.byte_size == len(content)
    assert result.sha256 == artifact.sha256


def test_missing_required_column_is_quarantined(
    tmp_path: Path,
) -> None:
    content = b"dataset,entity\narticle-4-direction,6100001\n"

    result = validate_csv_artifact(
        _write_artifact(tmp_path, content),
        _source(),
        expected_entity_count=1,
    )

    assert isinstance(
        result,
        FailedAcquisitionResult,
    )
    assert result.status == AcquisitionStatus.QUARANTINED
    assert result.failure_reason == AcquisitionFailureReason.INVALID_CONTENT
    assert "missing required columns" in result.detail


def test_wrong_dataset_identity_is_quarantined(
    tmp_path: Path,
) -> None:
    content = (
        HEADER
        + (
            "conservation-area-document,6100001,228,"
            "A4-1,Wrong,http://example.org/a.pdf,"
            "http://example.org/a,Wrong dataset\n"
        )
    ).encode()

    result = validate_csv_artifact(
        _write_artifact(tmp_path, content),
        _source(),
        expected_entity_count=1,
    )

    assert isinstance(
        result,
        FailedAcquisitionResult,
    )
    assert "expected 'article-4-direction'" in result.detail


@pytest.mark.parametrize(
    "entity",
    [
        "",
        "abc",
        "0",
        "-1",
    ],
)
def test_invalid_entity_identifier_is_quarantined(
    tmp_path: Path,
    entity: str,
) -> None:
    content = (
        HEADER
        + (
            f"article-4-direction,{entity},228,A4-1,"
            "Example,http://example.org/a.pdf,"
            "http://example.org/a,Description\n"
        )
    ).encode()

    result = validate_csv_artifact(
        _write_artifact(tmp_path, content),
        _source(),
        expected_entity_count=1,
    )

    assert isinstance(
        result,
        FailedAcquisitionResult,
    )
    assert result.failure_reason == AcquisitionFailureReason.INVALID_CONTENT


def test_duplicate_entity_is_quarantined(
    tmp_path: Path,
) -> None:
    row = (
        "article-4-direction,6100001,228,A4-1,"
        "Example,http://example.org/a.pdf,"
        "http://example.org/a,Description\n"
    )
    content = (HEADER + row + row).encode()

    result = validate_csv_artifact(
        _write_artifact(tmp_path, content),
        _source(),
        expected_entity_count=2,
    )

    assert isinstance(
        result,
        FailedAcquisitionResult,
    )
    assert "Duplicate entity identifier" in result.detail


def test_entity_count_mismatch_preserves_evidence(
    tmp_path: Path,
) -> None:
    content = (
        HEADER
        + (
            "article-4-direction,6100001,228,A4-1,"
            "Example,http://example.org/a.pdf,"
            "http://example.org/a,Description\n"
        )
    ).encode()

    artifact = _write_artifact(
        tmp_path,
        content,
    )

    result = validate_csv_artifact(
        artifact,
        _source(),
        expected_entity_count=2,
    )

    assert isinstance(
        result,
        FailedAcquisitionResult,
    )
    assert result.failure_reason == AcquisitionFailureReason.ENTITY_COUNT_MISMATCH
    assert result.observed_entity_count == 1
    assert result.sha256 == artifact.sha256
    assert result.byte_size == artifact.byte_size


def test_modified_artifact_size_is_quarantined(
    tmp_path: Path,
) -> None:
    artifact = _write_artifact(
        tmp_path,
        (
            HEADER
            + (
                "article-4-direction,6100001,228,A4-1,"
                "Example,http://example.org/a.pdf,"
                "http://example.org/a,Description\n"
            )
        ).encode(),
    )

    artifact.staging_path.write_bytes(b"tampered")

    result = validate_csv_artifact(
        artifact,
        _source(),
        expected_entity_count=1,
    )

    assert isinstance(
        result,
        FailedAcquisitionResult,
    )
    assert "byte size changed" in result.detail


def test_modified_artifact_hash_is_quarantined(
    tmp_path: Path,
) -> None:
    original = b"1234567890"
    artifact = _write_artifact(
        tmp_path,
        original,
    )

    artifact.staging_path.write_bytes(b"abcdefghij")

    result = validate_csv_artifact(
        artifact,
        _source(),
        expected_entity_count=1,
    )

    assert isinstance(
        result,
        FailedAcquisitionResult,
    )
    assert "SHA-256 changed" in result.detail


def test_header_only_csv_is_quarantined(
    tmp_path: Path,
) -> None:
    content = HEADER.encode()

    result = validate_csv_artifact(
        _write_artifact(tmp_path, content),
        _source(),
        expected_entity_count=0,
    )

    assert isinstance(
        result,
        FailedAcquisitionResult,
    )
    assert "no data rows" in result.detail


def test_missing_staging_file_is_filesystem_failure(
    tmp_path: Path,
) -> None:
    content = b"placeholder"
    artifact = _write_artifact(
        tmp_path,
        content,
    )
    artifact.staging_path.unlink()

    result = validate_csv_artifact(
        artifact,
        _source(),
        expected_entity_count=1,
    )

    assert isinstance(
        result,
        FailedAcquisitionResult,
    )
    assert result.status == AcquisitionStatus.REJECTED
    assert result.failure_reason == AcquisitionFailureReason.FILESYSTEM_ERROR


def test_non_csv_source_is_rejected_by_programming_contract(
    tmp_path: Path,
) -> None:
    artifact = _write_artifact(
        tmp_path,
        b"content",
    )

    source = create_dataset_source(
        dataset=PlanningDataset.ARTICLE_4_DIRECTION,
        source_url="https://example.org/data.json",
        source_format=SourceFormat.JSON,
    )

    with pytest.raises(
        ValueError,
        match="only supports CSV",
    ):
        validate_csv_artifact(
            artifact,
            source,
            expected_entity_count=1,
        )


def test_negative_expected_count_is_rejected(
    tmp_path: Path,
) -> None:
    artifact = _write_artifact(
        tmp_path,
        b"content",
    )

    with pytest.raises(
        ValueError,
        match="must not be negative",
    ):
        validate_csv_artifact(
            artifact,
            _source(),
            expected_entity_count=-1,
        )


def _conservation_source() -> DatasetSource:
    return create_dataset_source(
        dataset=(PlanningDataset.CONSERVATION_AREA_DOCUMENT),
        source_url=("https://files.planning.data.gov.uk/dataset/conservation-area-document.csv"),
        source_format=SourceFormat.CSV,
    )


def test_conservation_area_document_is_structurally_validated(
    tmp_path: Path,
) -> None:
    content = (
        b"dataset,entity,organisation-entity,reference,"
        b"name,document-url,documentation-url,"
        b"document-type,conservation-area\n"
        b"conservation-area-document,6300001,228,CA-1,"
        b"Example,http://example.org/a.pdf,"
        b"http://example.org/a,area-appraisal,"
        b"228_CA1\n"
    )

    result = validate_csv_artifact(
        _write_artifact(tmp_path, content),
        _conservation_source(),
        expected_entity_count=1,
    )

    assert isinstance(
        result,
        ValidatedDatasetArtifact,
    )
    assert result.observed_entity_count == 1


def test_conservation_area_document_requires_specific_columns(
    tmp_path: Path,
) -> None:
    content = (
        b"dataset,entity,organisation-entity,reference,"
        b"name,document-url,documentation-url\n"
        b"conservation-area-document,6300001,228,CA-1,"
        b"Example,http://example.org/a.pdf,"
        b"http://example.org/a\n"
    )

    result = validate_csv_artifact(
        _write_artifact(tmp_path, content),
        _conservation_source(),
        expected_entity_count=1,
    )

    assert isinstance(
        result,
        FailedAcquisitionResult,
    )
    assert result.failure_reason == AcquisitionFailureReason.INVALID_CONTENT
    assert "conservation-area" in result.detail
    assert "document-type" in result.detail


@pytest.mark.parametrize(
    "row",
    [
        (
            "article-4-direction,6100001,228,A4-1,"
            "Example,http://example.org/a.pdf,"
            "http://example.org/a\n"
        ),
        (
            "article-4-direction,6100001,228,A4-1,"
            "Example,http://example.org/a.pdf,"
            "http://example.org/a,Description,"
            "unexpected\n"
        ),
    ],
)
def test_ragged_csv_row_is_quarantined(
    tmp_path: Path,
    row: str,
) -> None:
    content = (HEADER + row).encode()

    result = validate_csv_artifact(
        _write_artifact(tmp_path, content),
        _source(),
        expected_entity_count=1,
    )

    assert isinstance(
        result,
        FailedAcquisitionResult,
    )
    assert result.failure_reason == AcquisitionFailureReason.INVALID_CONTENT
    assert "fields; expected" in result.detail


def test_duplicate_header_names_are_quarantined(
    tmp_path: Path,
) -> None:
    content = (
        b"dataset,entity,entity,organisation-entity,"
        b"reference,name,document-url,"
        b"documentation-url,description\n"
        b"article-4-direction,6100001,6100002,228,"
        b"A4-1,Example,http://example.org/a.pdf,"
        b"http://example.org/a,Description\n"
    )

    result = validate_csv_artifact(
        _write_artifact(tmp_path, content),
        _source(),
        expected_entity_count=1,
    )

    assert isinstance(
        result,
        FailedAcquisitionResult,
    )
    assert result.failure_reason == AcquisitionFailureReason.INVALID_CONTENT
    assert "duplicate column names" in result.detail


def test_invalid_utf8_is_quarantined(
    tmp_path: Path,
) -> None:
    content = b"dataset,entity\n\xff\xfe\xff"

    result = validate_csv_artifact(
        _write_artifact(tmp_path, content),
        _source(),
        expected_entity_count=1,
    )

    assert isinstance(
        result,
        FailedAcquisitionResult,
    )
    assert result.failure_reason == AcquisitionFailureReason.INVALID_CONTENT
    assert "UnicodeDecodeError" in result.detail
