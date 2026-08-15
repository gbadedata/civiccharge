import pytest
from pydantic import ValidationError

from civiccharge.ingest.acquisition import (
    AcceptedAcquisitionResult,
    AcquisitionFailureReason,
    AcquisitionStatus,
    FailedAcquisitionResult,
    RetryPolicy,
    classify_http_status,
    create_dataset_source,
)
from civiccharge.ingest.planning_data_models import PlanningDataset
from civiccharge.ingest.provenance import SourceFormat

SHA256 = "a" * 64


def test_dataset_source_validates_http_url() -> None:
    source = create_dataset_source(
        dataset=PlanningDataset.ARTICLE_4_DIRECTION,
        source_url=("https://files.planning.data.gov.uk/dataset/article-4-direction.csv"),
        source_format=SourceFormat.CSV,
    )

    assert source.dataset == PlanningDataset.ARTICLE_4_DIRECTION
    assert source.source_format == SourceFormat.CSV
    assert str(source.source_url).startswith("https://")


def test_dataset_source_rejects_invalid_url() -> None:
    with pytest.raises(ValidationError):
        create_dataset_source(
            dataset=PlanningDataset.ARTICLE_4_DIRECTION,
            source_url="not-a-url",
            source_format=SourceFormat.CSV,
        )


def test_retry_policy_has_bounded_exponential_backoff() -> None:
    policy = RetryPolicy(
        max_attempts=5,
        base_delay_seconds=0.5,
        max_delay_seconds=2.0,
    )

    assert policy.backoff_seconds(1) == 0.5
    assert policy.backoff_seconds(2) == 1.0
    assert policy.backoff_seconds(3) == 2.0
    assert policy.backoff_seconds(4) == 2.0


def test_retry_policy_rejects_invalid_retry_number() -> None:
    policy = RetryPolicy()

    with pytest.raises(
        ValueError,
        match="retry_number",
    ):
        policy.backoff_seconds(0)


def test_retry_policy_rejects_inverted_delay_bounds() -> None:
    with pytest.raises(
        ValidationError,
        match="max_delay_seconds",
    ):
        RetryPolicy(
            base_delay_seconds=5.0,
            max_delay_seconds=1.0,
        )


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (200, AcquisitionStatus.ACCEPTED),
        (204, AcquisitionStatus.ACCEPTED),
        (408, AcquisitionStatus.RETRYABLE_FAILURE),
        (429, AcquisitionStatus.RETRYABLE_FAILURE),
        (500, AcquisitionStatus.RETRYABLE_FAILURE),
        (502, AcquisitionStatus.RETRYABLE_FAILURE),
        (503, AcquisitionStatus.RETRYABLE_FAILURE),
        (504, AcquisitionStatus.RETRYABLE_FAILURE),
        (400, AcquisitionStatus.REJECTED),
        (401, AcquisitionStatus.REJECTED),
        (403, AcquisitionStatus.REJECTED),
        (404, AcquisitionStatus.REJECTED),
        (501, AcquisitionStatus.REJECTED),
    ],
)
def test_http_status_classification(
    status_code: int,
    expected: AcquisitionStatus,
) -> None:
    assert classify_http_status(status_code) == expected


@pytest.mark.parametrize(
    "status_code",
    [
        0,
        99,
        600,
        999,
    ],
)
def test_http_status_classification_rejects_invalid_codes(
    status_code: int,
) -> None:
    with pytest.raises(
        ValueError,
        match="HTTP status code",
    ):
        classify_http_status(status_code)


def test_accepted_result_requires_verified_artifact_metadata() -> None:
    result = AcceptedAcquisitionResult(
        attempts=1,
        byte_size=4096,
        sha256=SHA256,
        observed_entity_count=3234,
    )

    assert result.status == AcquisitionStatus.ACCEPTED
    assert result.byte_size == 4096
    assert result.observed_entity_count == 3234


def test_accepted_result_rejects_empty_artifact() -> None:
    with pytest.raises(ValidationError):
        AcceptedAcquisitionResult(
            attempts=1,
            byte_size=0,
            sha256=SHA256,
            observed_entity_count=3234,
        )


def test_failed_result_preserves_partial_evidence() -> None:
    result = FailedAcquisitionResult(
        status=AcquisitionStatus.QUARANTINED,
        attempts=1,
        failure_reason=AcquisitionFailureReason.ENTITY_COUNT_MISMATCH,
        detail="Expected 3234 entities but observed 3200.",
        byte_size=5000,
        sha256=SHA256,
        observed_entity_count=3200,
    )

    assert result.status == AcquisitionStatus.QUARANTINED
    assert result.byte_size == 5000
    assert result.sha256 == SHA256
    assert result.observed_entity_count == 3200


def test_failed_result_requires_failure_detail() -> None:
    with pytest.raises(
        ValidationError,
        match="detail must not be blank",
    ):
        FailedAcquisitionResult(
            status=AcquisitionStatus.REJECTED,
            attempts=1,
            failure_reason=AcquisitionFailureReason.HTTP_STATUS,
            detail="   ",
        )


def test_failed_result_cannot_be_accepted() -> None:
    with pytest.raises(ValidationError):
        FailedAcquisitionResult.model_validate(
            {
                "status": "accepted",
                "attempts": 1,
                "failure_reason": "http_status",
                "detail": "Invalid state",
            }
        )


def test_internal_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        RetryPolicy.model_validate(
            {
                "max_attempts": 4,
                "base_delay_seconds": 0.5,
                "max_delay_seconds": 8.0,
                "unexpected": True,
            }
        )


@pytest.mark.parametrize(
    "max_attempts",
    [
        0,
        11,
    ],
)
def test_retry_policy_rejects_out_of_range_attempts(
    max_attempts: int,
) -> None:
    with pytest.raises(ValidationError):
        RetryPolicy(max_attempts=max_attempts)
