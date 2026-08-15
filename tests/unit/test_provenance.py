from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from civiccharge.ingest.planning_data_models import (
    CanonicalDatasetMetadata,
    PlanningDataset,
)
from civiccharge.ingest.provenance import (
    SourceFormat,
    SourceSnapshot,
    build_snapshot_id,
    create_source_snapshot,
)

SHA256 = "a" * 64


def _metadata() -> CanonicalDatasetMetadata:
    return CanonicalDatasetMetadata(
        dataset=PlanningDataset.ARTICLE_4_DIRECTION,
        name="Article 4 direction",
        collection="article-4-direction",
        phase="beta",
        licence="ogl3",
        licence_text="Open Government Licence",
        attribution="crown-copyright",
        attribution_text="Crown copyright",
        entity_count=3234,
        entity_minimum=6100000,
        entity_maximum=6199999,
        typology="legal-instrument",
        version=None,
    )


def _snapshot() -> SourceSnapshot:
    return create_source_snapshot(
        _metadata(),
        source_url=("https://www.planning.data.gov.uk/dataset/article-4-direction.json"),
        retrieved_at=datetime(
            2026,
            8,
            15,
            21,
            0,
            tzinfo=UTC,
        ),
        source_format=SourceFormat.JSON,
        sha256=SHA256,
        byte_size=1000,
        observed_entity_count=3234,
        relative_path=("data/raw/snapshots/article-4-direction.json"),
        upstream_api_version="0.1.0",
    )


def test_snapshot_id_is_deterministic() -> None:
    expected = "article-4-direction-" + SHA256

    assert (
        build_snapshot_id(
            PlanningDataset.ARTICLE_4_DIRECTION,
            SHA256,
        )
        == expected
    )

    assert _snapshot().snapshot_id == expected


def test_snapshot_normalises_timestamp_to_utc() -> None:
    offset = timezone(timedelta(hours=1))

    snapshot = create_source_snapshot(
        _metadata(),
        source_url="https://example.gov.uk/data.json",
        retrieved_at=datetime(
            2026,
            8,
            15,
            22,
            0,
            tzinfo=offset,
        ),
        source_format=SourceFormat.JSON,
        sha256=SHA256,
        byte_size=100,
        observed_entity_count=3234,
        relative_path="data/raw/snapshot.json",
    )

    assert snapshot.retrieved_at == datetime(
        2026,
        8,
        15,
        21,
        0,
        tzinfo=UTC,
    )


def test_snapshot_rejects_naive_timestamp() -> None:
    with pytest.raises(
        ValidationError,
        match="timezone-aware",
    ):
        create_source_snapshot(
            _metadata(),
            source_url="https://example.gov.uk/data.json",
            retrieved_at=datetime(2026, 8, 15, 21, 0),
            source_format=SourceFormat.JSON,
            sha256=SHA256,
            byte_size=100,
            observed_entity_count=3234,
            relative_path="data/raw/snapshot.json",
        )


def test_snapshot_rejects_invalid_sha256() -> None:
    with pytest.raises(ValidationError):
        create_source_snapshot(
            _metadata(),
            source_url="https://example.gov.uk/data.json",
            retrieved_at=datetime.now(UTC),
            source_format=SourceFormat.JSON,
            sha256="not-a-sha256",
            byte_size=100,
            observed_entity_count=3234,
            relative_path="data/raw/snapshot.json",
        )


def test_snapshot_exposes_entity_count_mismatch() -> None:
    snapshot = create_source_snapshot(
        _metadata(),
        source_url="https://example.gov.uk/data.json",
        retrieved_at=datetime.now(UTC),
        source_format=SourceFormat.JSON,
        sha256=SHA256,
        byte_size=100,
        observed_entity_count=3000,
        relative_path="data/raw/snapshot.json",
    )

    assert snapshot.declared_entity_count == 3234
    assert snapshot.observed_entity_count == 3000
    assert snapshot.entity_count_matches_metadata is False


@pytest.mark.parametrize(
    "relative_path",
    [
        "/tmp/snapshot.json",
        "../snapshot.json",
        "data/../../snapshot.json",
    ],
)
def test_snapshot_rejects_unsafe_paths(
    relative_path: str,
) -> None:
    with pytest.raises(ValidationError):
        create_source_snapshot(
            _metadata(),
            source_url="https://example.gov.uk/data.json",
            retrieved_at=datetime.now(UTC),
            source_format=SourceFormat.JSON,
            sha256=SHA256,
            byte_size=100,
            observed_entity_count=3234,
            relative_path=relative_path,
        )


def test_snapshot_rejects_wrong_snapshot_id() -> None:
    payload = _snapshot().model_dump(mode="json")
    payload["snapshot_id"] = "wrong-id"

    with pytest.raises(
        ValidationError,
        match="snapshot_id",
    ):
        SourceSnapshot.model_validate(payload)


def test_snapshot_rejects_unknown_internal_fields() -> None:
    payload = _snapshot().model_dump(mode="json")
    payload["unexpected-field"] = "value"

    with pytest.raises(ValidationError):
        SourceSnapshot.model_validate(payload)


def test_snapshot_rejects_blank_licence() -> None:
    payload = _snapshot().model_dump(mode="json")
    payload["licence"] = "   "

    with pytest.raises(ValidationError):
        SourceSnapshot.model_validate(payload)


def test_snapshot_rejects_invalid_source_url() -> None:
    with pytest.raises(ValidationError):
        create_source_snapshot(
            _metadata(),
            source_url="not-a-valid-http-url",
            retrieved_at=datetime.now(UTC),
            source_format=SourceFormat.JSON,
            sha256=SHA256,
            byte_size=100,
            observed_entity_count=3234,
            relative_path="data/raw/snapshot.json",
        )
