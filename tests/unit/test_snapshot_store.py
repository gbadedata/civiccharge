import errno
import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from civiccharge.ingest.acquisition import (
    DatasetSource,
    create_dataset_source,
)
from civiccharge.ingest.dataset_validator import (
    ValidatedDatasetArtifact,
)
from civiccharge.ingest.http_downloader import (
    DownloadedArtifact,
)
from civiccharge.ingest.planning_data_models import (
    CanonicalDatasetMetadata,
    PlanningDataset,
)
from civiccharge.ingest.provenance import (
    SourceFormat,
    SourceSnapshot,
)
from civiccharge.ingest.snapshot_store import (
    SnapshotStoreError,
    StoredSnapshot,
    store_validated_snapshot,
)

CONTENT = (
    b"dataset,entity,organisation-entity,reference,"
    b"name,document-url,documentation-url,description\n"
    b"article-4-direction,6100001,228,A4-1,"
    b"Example,http://example.org/a.pdf,"
    b"http://example.org/a,Description\n"
)


def _source() -> DatasetSource:
    return create_dataset_source(
        dataset=PlanningDataset.ARTICLE_4_DIRECTION,
        source_url=("https://files.planning.data.gov.uk/dataset/article-4-direction.csv"),
        source_format=SourceFormat.CSV,
    )


def _metadata(
    *,
    entity_count: int = 1,
) -> CanonicalDatasetMetadata:
    return CanonicalDatasetMetadata(
        dataset=PlanningDataset.ARTICLE_4_DIRECTION,
        name="Article 4 direction",
        collection="article-4-direction",
        phase="beta",
        licence="ogl3",
        licence_text="Open Government Licence",
        attribution="crown-copyright",
        attribution_text="Crown copyright",
        entity_count=entity_count,
        entity_minimum=6100000,
        entity_maximum=6199999,
        typology="legal-instrument",
        version=None,
    )


def _artifacts(
    tmp_path: Path,
    *,
    retrieved_at: datetime | None = None,
) -> tuple[
    DownloadedArtifact,
    ValidatedDatasetArtifact,
]:
    staging_path = tmp_path / "staging" / "article-4-direction.csv.part"
    staging_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    staging_path.write_bytes(CONTENT)

    sha256 = hashlib.sha256(CONTENT).hexdigest()

    downloaded = DownloadedArtifact(
        staging_path=staging_path,
        retrieved_at=(
            retrieved_at
            or datetime(
                2026,
                8,
                16,
                12,
                0,
                tzinfo=UTC,
            )
        ),
        attempts=1,
        status_code=200,
        byte_size=len(CONTENT),
        sha256=sha256,
        content_type="text/csv",
        response_url=("https://files.planning.data.gov.uk/dataset/article-4-direction.csv"),
    )

    validated = ValidatedDatasetArtifact(
        staging_path=staging_path,
        byte_size=len(CONTENT),
        sha256=sha256,
        observed_entity_count=1,
        columns=(
            "dataset",
            "entity",
            "organisation-entity",
            "reference",
            "name",
            "document-url",
            "documentation-url",
            "description",
        ),
    )

    return downloaded, validated


def test_snapshot_is_promoted_and_manifest_published(
    tmp_path: Path,
) -> None:
    downloaded, validated = _artifacts(tmp_path)

    result = store_validated_snapshot(
        repository_root=tmp_path,
        downloaded=downloaded,
        validated=validated,
        source=_source(),
        metadata=_metadata(),
    )

    assert isinstance(
        result,
        StoredSnapshot,
    )

    expected_artifact = (
        tmp_path / "data" / "raw" / "snapshots" / "article-4-direction" / f"{validated.sha256}.csv"
    )

    assert result.artifact_path == expected_artifact
    assert result.artifact_path.read_bytes() == CONTENT
    assert not downloaded.staging_path.exists()

    assert result.snapshot.sha256 == validated.sha256
    assert result.snapshot.retrieved_at == downloaded.retrieved_at
    assert result.snapshot.observed_entity_count == 1
    assert result.snapshot.declared_entity_count == 1
    assert result.snapshot.entity_count_matches_metadata

    assert result.snapshot.relative_path == (
        f"data/raw/snapshots/article-4-direction/{validated.sha256}.csv"
    )

    assert result.manifest_path.exists()

    loaded = SourceSnapshot.model_validate_json(result.manifest_path.read_text(encoding="utf-8"))

    assert loaded == result.snapshot
    assert list(result.manifest_path.parent.glob("*.part")) == []


def test_identical_snapshot_is_idempotent_and_keeps_first_manifest(
    tmp_path: Path,
) -> None:
    first_downloaded, first_validated = _artifacts(
        tmp_path,
        retrieved_at=datetime(
            2026,
            8,
            16,
            12,
            0,
            tzinfo=UTC,
        ),
    )

    first = store_validated_snapshot(
        repository_root=tmp_path,
        downloaded=first_downloaded,
        validated=first_validated,
        source=_source(),
        metadata=_metadata(),
    )

    second_downloaded, second_validated = _artifacts(
        tmp_path,
        retrieved_at=datetime(
            2026,
            8,
            17,
            12,
            0,
            tzinfo=UTC,
        ),
    )

    second = store_validated_snapshot(
        repository_root=tmp_path,
        downloaded=second_downloaded,
        validated=second_validated,
        source=_source(),
        metadata=_metadata(),
    )

    assert second.snapshot == first.snapshot
    assert second.snapshot.retrieved_at == first.snapshot.retrieved_at
    assert not second_downloaded.staging_path.exists()


def test_corrupt_existing_snapshot_is_rejected(
    tmp_path: Path,
) -> None:
    downloaded, validated = _artifacts(tmp_path)

    destination = (
        tmp_path / "data" / "raw" / "snapshots" / "article-4-direction" / f"{validated.sha256}.csv"
    )
    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    destination.write_bytes(b"corrupt")

    with pytest.raises(
        SnapshotStoreError,
        match="existing snapshot artifact",
    ):
        store_validated_snapshot(
            repository_root=tmp_path,
            downloaded=downloaded,
            validated=validated,
            source=_source(),
            metadata=_metadata(),
        )

    assert downloaded.staging_path.exists()


def test_staging_mutation_after_validation_is_rejected(
    tmp_path: Path,
) -> None:
    downloaded, validated = _artifacts(tmp_path)

    downloaded.staging_path.write_bytes(b"tampered-after-validation")

    with pytest.raises(
        SnapshotStoreError,
        match="staging artifact",
    ):
        store_validated_snapshot(
            repository_root=tmp_path,
            downloaded=downloaded,
            validated=validated,
            source=_source(),
            metadata=_metadata(),
        )

    destination = (
        tmp_path / "data" / "raw" / "snapshots" / "article-4-direction" / f"{validated.sha256}.csv"
    )

    assert not destination.exists()


def test_cross_filesystem_atomic_promotion_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    downloaded, validated = _artifacts(tmp_path)

    def fail_link(
        source: str | Path,
        destination: str | Path,
    ) -> None:
        raise OSError(
            errno.EXDEV,
            "Invalid cross-device link",
        )

    monkeypatch.setattr(
        "civiccharge.ingest.snapshot_store.os.link",
        fail_link,
    )

    with pytest.raises(
        SnapshotStoreError,
        match="same filesystem",
    ):
        store_validated_snapshot(
            repository_root=tmp_path,
            downloaded=downloaded,
            validated=validated,
            source=_source(),
            metadata=_metadata(),
        )

    assert downloaded.staging_path.exists()


def test_mismatched_metadata_dataset_is_rejected(
    tmp_path: Path,
) -> None:
    downloaded, validated = _artifacts(tmp_path)

    metadata = _metadata().model_copy(
        update={"dataset": (PlanningDataset.CONSERVATION_AREA_DOCUMENT)}
    )

    with pytest.raises(
        SnapshotStoreError,
        match="metadata does not match",
    ):
        store_validated_snapshot(
            repository_root=tmp_path,
            downloaded=downloaded,
            validated=validated,
            source=_source(),
            metadata=metadata,
        )


def test_mismatched_validated_digest_is_rejected(
    tmp_path: Path,
) -> None:
    downloaded, validated = _artifacts(tmp_path)

    different_digest = "0" * 64

    mismatched = validated.model_copy(
        update={
            "sha256": different_digest,
        }
    )

    with pytest.raises(
        SnapshotStoreError,
        match="different SHA-256",
    ):
        store_validated_snapshot(
            repository_root=tmp_path,
            downloaded=downloaded,
            validated=mismatched,
            source=_source(),
            metadata=_metadata(),
        )


def test_invalid_existing_manifest_is_rejected(
    tmp_path: Path,
) -> None:
    downloaded, validated = _artifacts(tmp_path)

    first = store_validated_snapshot(
        repository_root=tmp_path,
        downloaded=downloaded,
        validated=validated,
        source=_source(),
        metadata=_metadata(),
    )

    first.manifest_path.write_text(
        "{not-json",
        encoding="utf-8",
    )

    second_downloaded, second_validated = _artifacts(tmp_path)

    with pytest.raises(
        SnapshotStoreError,
        match="manifest is invalid",
    ):
        store_validated_snapshot(
            repository_root=tmp_path,
            downloaded=second_downloaded,
            validated=second_validated,
            source=_source(),
            metadata=_metadata(),
        )


def test_manifest_preserves_count_discrepancy(
    tmp_path: Path,
) -> None:
    downloaded, validated = _artifacts(tmp_path)

    result = store_validated_snapshot(
        repository_root=tmp_path,
        downloaded=downloaded,
        validated=validated,
        source=_source(),
        metadata=_metadata(
            entity_count=2,
        ),
    )

    assert result.snapshot.declared_entity_count == 2
    assert result.snapshot.observed_entity_count == 1
    assert not result.snapshot.entity_count_matches_metadata


def test_manifest_publication_failure_is_recoverable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    downloaded, validated = _artifacts(tmp_path)

    real_link = os.link
    link_calls = 0

    def fail_manifest_link(
        source: str | Path,
        destination: str | Path,
    ) -> None:
        nonlocal link_calls
        link_calls += 1

        if link_calls == 2:
            raise OSError(
                errno.EIO,
                "simulated manifest publication failure",
            )

        real_link(
            source,
            destination,
        )

    monkeypatch.setattr(
        "civiccharge.ingest.snapshot_store.os.link",
        fail_manifest_link,
    )

    with pytest.raises(
        SnapshotStoreError,
        match="Atomic manifest publication failed",
    ):
        store_validated_snapshot(
            repository_root=tmp_path,
            downloaded=downloaded,
            validated=validated,
            source=_source(),
            metadata=_metadata(),
        )

    artifact_path = (
        tmp_path / "data" / "raw" / "snapshots" / "article-4-direction" / f"{validated.sha256}.csv"
    )

    assert artifact_path.exists()

    manifest_directory = tmp_path / "manifests" / "source-snapshots" / "article-4-direction"

    assert list(manifest_directory.glob("*.part")) == []

    monkeypatch.setattr(
        "civiccharge.ingest.snapshot_store.os.link",
        real_link,
    )

    retry_downloaded, retry_validated = _artifacts(tmp_path)

    result = store_validated_snapshot(
        repository_root=tmp_path,
        downloaded=retry_downloaded,
        validated=retry_validated,
        source=_source(),
        metadata=_metadata(),
    )

    assert result.artifact_path == artifact_path
    assert result.manifest_path.exists()
