import errno
import hashlib
import os
from contextlib import suppress
from pathlib import Path
from tempfile import NamedTemporaryFile

from pydantic import BaseModel, ConfigDict

from civiccharge.ingest.acquisition import DatasetSource
from civiccharge.ingest.dataset_validator import ValidatedDatasetArtifact
from civiccharge.ingest.http_downloader import DownloadedArtifact
from civiccharge.ingest.planning_data_models import CanonicalDatasetMetadata
from civiccharge.ingest.provenance import (
    SourceSnapshot,
    create_source_snapshot,
)


class SnapshotStoreError(RuntimeError):
    pass


class StoredSnapshot(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    snapshot: SourceSnapshot
    artifact_path: Path
    manifest_path: Path


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


def _snapshot_relative_path(
    source: DatasetSource,
    sha256: str,
) -> Path:
    return Path(
        "data",
        "raw",
        "snapshots",
        source.dataset.value,
        f"{sha256}.{source.source_format.value}",
    )


def _manifest_relative_path(
    snapshot: SourceSnapshot,
) -> Path:
    return Path(
        "manifests",
        "source-snapshots",
        snapshot.dataset.value,
        f"{snapshot.snapshot_id}.json",
    )


def _fsync_directory(
    directory: Path,
) -> None:
    descriptor = os.open(
        directory,
        os.O_RDONLY,
    )

    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _verify_file_matches(
    path: Path,
    *,
    expected_byte_size: int,
    expected_sha256: str,
    context: str,
) -> None:
    try:
        byte_size, sha256 = _calculate_file_evidence(path)
    except OSError as error:
        raise SnapshotStoreError(f"Unable to verify {context}: {error}") from error

    if byte_size != expected_byte_size:
        raise SnapshotStoreError(
            f"{context} byte size mismatch: expected {expected_byte_size}, observed {byte_size}"
        )

    if sha256 != expected_sha256:
        raise SnapshotStoreError(f"{context} SHA-256 mismatch")


def _validate_lineage(
    *,
    downloaded: DownloadedArtifact,
    validated: ValidatedDatasetArtifact,
    source: DatasetSource,
    metadata: CanonicalDatasetMetadata,
) -> None:
    if metadata.dataset != source.dataset:
        raise SnapshotStoreError("Dataset metadata does not match acquisition source")

    if downloaded.staging_path != validated.staging_path:
        raise SnapshotStoreError("Downloaded and validated artifacts have different paths")

    if downloaded.byte_size != validated.byte_size:
        raise SnapshotStoreError("Downloaded and validated artifacts have different byte sizes")

    if downloaded.sha256 != validated.sha256:
        raise SnapshotStoreError(
            "Downloaded and validated artifacts have different SHA-256 digests"
        )


def _promote_artifact(
    *,
    repository_root: Path,
    downloaded: DownloadedArtifact,
    validated: ValidatedDatasetArtifact,
    source: DatasetSource,
) -> Path:
    relative_path = _snapshot_relative_path(
        source,
        validated.sha256,
    )
    destination = repository_root / relative_path

    _verify_file_matches(
        validated.staging_path,
        expected_byte_size=validated.byte_size,
        expected_sha256=validated.sha256,
        context="staging artifact",
    )

    try:
        destination.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
    except OSError as error:
        raise SnapshotStoreError(f"Unable to create snapshot directory: {error}") from error

    if destination.exists():
        _verify_file_matches(
            destination,
            expected_byte_size=validated.byte_size,
            expected_sha256=validated.sha256,
            context="existing snapshot artifact",
        )

        if downloaded.staging_path != destination:
            try:
                downloaded.staging_path.unlink()
            except OSError as error:
                raise SnapshotStoreError(
                    f"Existing snapshot is valid but temporary artifact cleanup failed: {error}"
                ) from error

        return destination

    try:
        os.link(
            downloaded.staging_path,
            destination,
        )
    except FileExistsError:
        _verify_file_matches(
            destination,
            expected_byte_size=validated.byte_size,
            expected_sha256=validated.sha256,
            context="existing snapshot artifact",
        )

        if downloaded.staging_path != destination:
            try:
                downloaded.staging_path.unlink()
            except OSError as error:
                raise SnapshotStoreError(
                    f"Existing snapshot is valid but temporary artifact cleanup failed: {error}"
                ) from error

        return destination
    except OSError as error:
        if error.errno == errno.EXDEV:
            raise SnapshotStoreError(
                "Atomic snapshot promotion requires staging and "
                "destination to be on the same filesystem"
            ) from error

        raise SnapshotStoreError(f"Atomic snapshot promotion failed: {error}") from error

    try:
        _fsync_directory(destination.parent)
    except OSError as error:
        raise SnapshotStoreError(f"Snapshot directory fsync failed: {error}") from error

    try:
        _verify_file_matches(
            destination,
            expected_byte_size=validated.byte_size,
            expected_sha256=validated.sha256,
            context="promoted snapshot artifact",
        )
    except SnapshotStoreError:
        with suppress(OSError):
            destination.unlink(missing_ok=True)
        raise

    try:
        downloaded.staging_path.unlink()
    except OSError as error:
        raise SnapshotStoreError(
            f"Snapshot was published but temporary artifact cleanup failed: {error}"
        ) from error

    return destination


def _load_existing_manifest(
    manifest_path: Path,
) -> SourceSnapshot:
    try:
        payload = manifest_path.read_text(encoding="utf-8")
        return SourceSnapshot.model_validate_json(payload)
    except Exception as error:
        raise SnapshotStoreError(f"Existing snapshot manifest is invalid: {error}") from error


def _validate_existing_manifest(
    existing: SourceSnapshot,
    expected: SourceSnapshot,
) -> None:
    if existing.snapshot_id != expected.snapshot_id:
        raise SnapshotStoreError("Existing manifest snapshot ID does not match expected snapshot")

    if existing.dataset != expected.dataset:
        raise SnapshotStoreError("Existing manifest dataset does not match expected snapshot")

    if existing.sha256 != expected.sha256:
        raise SnapshotStoreError("Existing manifest SHA-256 does not match expected snapshot")

    if existing.byte_size != expected.byte_size:
        raise SnapshotStoreError("Existing manifest byte size does not match expected snapshot")

    if existing.relative_path != expected.relative_path:
        raise SnapshotStoreError("Existing manifest artifact path does not match expected snapshot")

    if existing.observed_entity_count != expected.observed_entity_count:
        raise SnapshotStoreError(
            "Existing manifest observed entity count does not match expected snapshot"
        )


def _publish_manifest(
    *,
    repository_root: Path,
    snapshot: SourceSnapshot,
) -> tuple[SourceSnapshot, Path]:
    relative_path = _manifest_relative_path(snapshot)
    manifest_path = repository_root / relative_path

    try:
        manifest_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
    except OSError as error:
        raise SnapshotStoreError(f"Unable to create manifest directory: {error}") from error

    if manifest_path.exists():
        existing = _load_existing_manifest(manifest_path)
        _validate_existing_manifest(
            existing,
            snapshot,
        )
        return existing, manifest_path

    temporary_path: Path | None = None

    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=manifest_path.parent,
            prefix=f".{snapshot.snapshot_id}-",
            suffix=".json.part",
            delete=False,
        ) as output:
            temporary_path = Path(output.name)
            output.write(
                snapshot.model_dump_json(
                    indent=2,
                )
            )
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())

        try:
            os.link(
                temporary_path,
                manifest_path,
            )
        except FileExistsError:
            existing = _load_existing_manifest(manifest_path)
            _validate_existing_manifest(
                existing,
                snapshot,
            )

            with suppress(OSError):
                temporary_path.unlink(missing_ok=True)

            return existing, manifest_path

        _fsync_directory(manifest_path.parent)

        temporary_path.unlink(missing_ok=True)

    except OSError as error:
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink(missing_ok=True)

        raise SnapshotStoreError(f"Atomic manifest publication failed: {error}") from error

    return snapshot, manifest_path


def store_validated_snapshot(
    *,
    repository_root: Path,
    downloaded: DownloadedArtifact,
    validated: ValidatedDatasetArtifact,
    source: DatasetSource,
    metadata: CanonicalDatasetMetadata,
    upstream_api_version: str | None = None,
    upstream_spec_version: str | None = None,
) -> StoredSnapshot:
    _validate_lineage(
        downloaded=downloaded,
        validated=validated,
        source=source,
        metadata=metadata,
    )

    artifact_path = _promote_artifact(
        repository_root=repository_root,
        downloaded=downloaded,
        validated=validated,
        source=source,
    )

    relative_artifact_path = artifact_path.relative_to(repository_root).as_posix()

    candidate_snapshot = create_source_snapshot(
        metadata,
        source_url=str(source.source_url),
        retrieved_at=downloaded.retrieved_at,
        source_format=source.source_format,
        sha256=validated.sha256,
        byte_size=validated.byte_size,
        observed_entity_count=(validated.observed_entity_count),
        relative_path=relative_artifact_path,
        upstream_api_version=upstream_api_version,
        upstream_spec_version=upstream_spec_version,
    )

    snapshot, manifest_path = _publish_manifest(
        repository_root=repository_root,
        snapshot=candidate_snapshot,
    )

    return StoredSnapshot(
        snapshot=snapshot,
        artifact_path=artifact_path,
        manifest_path=manifest_path,
    )
