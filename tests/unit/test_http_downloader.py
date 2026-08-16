import hashlib
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from civiccharge.ingest.acquisition import (
    AcquisitionFailureReason,
    AcquisitionStatus,
    DatasetSource,
    FailedAcquisitionResult,
    RetryPolicy,
    create_dataset_source,
)
from civiccharge.ingest.http_downloader import (
    DownloadedArtifact,
    HttpDownloadConfig,
    download_with_retries,
)
from civiccharge.ingest.planning_data_models import (
    PlanningDataset,
)
from civiccharge.ingest.provenance import SourceFormat

URL = "https://files.planning.data.gov.uk/dataset/article-4-direction.csv"


def _source() -> DatasetSource:
    return create_dataset_source(
        dataset=PlanningDataset.ARTICLE_4_DIRECTION,
        source_url=URL,
        source_format=SourceFormat.CSV,
    )


def _config() -> HttpDownloadConfig:
    return HttpDownloadConfig(
        user_agent="CivicCharge-test/0.1",
        connect_timeout_seconds=1.0,
        read_timeout_seconds=1.0,
        write_timeout_seconds=1.0,
        pool_timeout_seconds=1.0,
        chunk_size_bytes=1024,
    )


def test_successful_download_streams_to_staging(
    tmp_path: Path,
) -> None:
    payload = b"entity,name\n6100001,Example\n"

    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        assert request.headers["user-agent"] == "CivicCharge-test/0.1"

        return httpx.Response(
            200,
            content=payload,
            headers={"content-type": "text/csv"},
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = download_with_retries(
            _source(),
            tmp_path,
            client=client,
            retry_policy=RetryPolicy(max_attempts=3),
            config=_config(),
            sleeper=lambda _: None,
        )

    assert isinstance(result, DownloadedArtifact)
    assert result.attempts == 1
    assert result.byte_size == len(payload)
    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    assert result.content_type == "text/csv"
    assert result.staging_path.read_bytes() == payload
    assert result.staging_path.suffix == ".part"


def test_redirect_is_followed_within_one_attempt(
    tmp_path: Path,
) -> None:
    requests: list[str] = []

    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        requests.append(str(request.url))

        if len(requests) == 1:
            return httpx.Response(
                302,
                headers={"location": ("https://files.planning.data.gov.uk/redirected.csv")},
                request=request,
            )

        return httpx.Response(
            200,
            content=b"entity\n1\n",
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = download_with_retries(
            _source(),
            tmp_path,
            client=client,
            retry_policy=RetryPolicy(),
            config=_config(),
            sleeper=lambda _: None,
        )

    assert isinstance(result, DownloadedArtifact)
    assert result.attempts == 1
    assert len(requests) == 2
    assert result.response_url.endswith("/redirected.csv")


def test_permanent_http_failure_is_not_retried(
    tmp_path: Path,
) -> None:
    calls = 0

    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            404,
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = download_with_retries(
            _source(),
            tmp_path,
            client=client,
            retry_policy=RetryPolicy(max_attempts=4),
            config=_config(),
            sleeper=lambda _: None,
        )

    assert isinstance(result, FailedAcquisitionResult)
    assert result.status == AcquisitionStatus.REJECTED
    assert result.failure_reason == AcquisitionFailureReason.HTTP_STATUS
    assert result.attempts == 1
    assert calls == 1


def test_retryable_http_failure_can_recover(
    tmp_path: Path,
) -> None:
    calls = 0
    delays: list[float] = []

    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        nonlocal calls
        calls += 1

        if calls == 1:
            return httpx.Response(
                429,
                request=request,
            )

        return httpx.Response(
            200,
            content=b"entity\n1\n",
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = download_with_retries(
            _source(),
            tmp_path,
            client=client,
            retry_policy=RetryPolicy(
                max_attempts=3,
                base_delay_seconds=0.5,
                max_delay_seconds=2.0,
            ),
            config=_config(),
            sleeper=delays.append,
        )

    assert isinstance(result, DownloadedArtifact)
    assert result.attempts == 2
    assert calls == 2
    assert delays == [0.5]


def test_retryable_http_failure_exhausts_budget(
    tmp_path: Path,
) -> None:
    calls = 0
    delays: list[float] = []

    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            503,
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = download_with_retries(
            _source(),
            tmp_path,
            client=client,
            retry_policy=RetryPolicy(
                max_attempts=3,
                base_delay_seconds=0.5,
                max_delay_seconds=2.0,
            ),
            config=_config(),
            sleeper=delays.append,
        )

    assert isinstance(result, FailedAcquisitionResult)
    assert result.status == AcquisitionStatus.RETRYABLE_FAILURE
    assert result.attempts == 3
    assert calls == 3
    assert delays == [0.5, 1.0]


def test_timeout_is_retried_and_reported(
    tmp_path: Path,
) -> None:
    calls = 0

    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout(
            "read timed out",
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = download_with_retries(
            _source(),
            tmp_path,
            client=client,
            retry_policy=RetryPolicy(max_attempts=2),
            config=_config(),
            sleeper=lambda _: None,
        )

    assert isinstance(result, FailedAcquisitionResult)
    assert result.failure_reason == AcquisitionFailureReason.TIMEOUT
    assert result.attempts == 2
    assert calls == 2


def test_transport_error_is_retried_and_reported(
    tmp_path: Path,
) -> None:
    calls = 0

    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError(
            "connection failed",
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = download_with_retries(
            _source(),
            tmp_path,
            client=client,
            retry_policy=RetryPolicy(max_attempts=2),
            config=_config(),
            sleeper=lambda _: None,
        )

    assert isinstance(result, FailedAcquisitionResult)
    assert result.failure_reason == AcquisitionFailureReason.TRANSPORT_ERROR
    assert result.attempts == 2
    assert calls == 2


def test_empty_success_response_is_quarantined(
    tmp_path: Path,
) -> None:
    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"",
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = download_with_retries(
            _source(),
            tmp_path,
            client=client,
            retry_policy=RetryPolicy(),
            config=_config(),
            sleeper=lambda _: None,
        )

    assert isinstance(result, FailedAcquisitionResult)
    assert result.status == AcquisitionStatus.QUARANTINED
    assert result.failure_reason == AcquisitionFailureReason.EMPTY_PAYLOAD
    assert list(tmp_path.glob("*.part")) == []


def test_filesystem_failure_is_reported(
    tmp_path: Path,
) -> None:
    not_a_directory = tmp_path / "staging"
    not_a_directory.write_text("occupied")

    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=b"data",
                request=request,
            )
        )
    ) as client:
        result = download_with_retries(
            _source(),
            not_a_directory,
            client=client,
            retry_policy=RetryPolicy(),
            config=_config(),
            sleeper=lambda _: None,
        )

    assert isinstance(result, FailedAcquisitionResult)
    assert result.status == AcquisitionStatus.REJECTED
    assert result.failure_reason == AcquisitionFailureReason.FILESYSTEM_ERROR


def test_download_config_rejects_blank_user_agent() -> None:
    with pytest.raises(
        ValidationError,
        match="user_agent",
    ):
        HttpDownloadConfig(user_agent="   ")


@pytest.mark.parametrize(
    "chunk_size",
    [
        0,
        1023,
        8 * 1024 * 1024 + 1,
    ],
)
def test_download_config_rejects_invalid_chunk_size(
    chunk_size: int,
) -> None:
    with pytest.raises(ValidationError):
        HttpDownloadConfig(chunk_size_bytes=chunk_size)


class _FailingStream(httpx.SyncByteStream):
    def __iter__(self) -> Iterator[bytes]:
        yield b"partial-data"
        raise httpx.ReadError("stream interrupted")


def test_partial_staging_file_is_removed_on_stream_failure(
    tmp_path: Path,
) -> None:
    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        return httpx.Response(
            200,
            stream=_FailingStream(),
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = download_with_retries(
            _source(),
            tmp_path,
            client=client,
            retry_policy=RetryPolicy(max_attempts=1),
            config=_config(),
            sleeper=lambda _: None,
        )

    assert isinstance(result, FailedAcquisitionResult)
    assert result.failure_reason == AcquisitionFailureReason.TRANSPORT_ERROR
    assert result.attempts == 1
    assert list(tmp_path.glob("*.part")) == []
