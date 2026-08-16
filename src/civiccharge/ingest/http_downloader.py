import hashlib
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import sleep

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from civiccharge.ingest.acquisition import (
    AcquisitionFailureReason,
    AcquisitionStatus,
    DatasetSource,
    FailedAcquisitionResult,
    RetryPolicy,
    classify_http_status,
)
from civiccharge.ingest.provenance import Sha256Digest


class HttpDownloadConfig(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    user_agent: str = "CivicCharge/0.1"
    connect_timeout_seconds: float = Field(default=10.0, gt=0)
    read_timeout_seconds: float = Field(default=30.0, gt=0)
    write_timeout_seconds: float = Field(default=10.0, gt=0)
    pool_timeout_seconds: float = Field(default=10.0, gt=0)
    chunk_size_bytes: int = Field(
        default=64 * 1024,
        ge=1024,
        le=8 * 1024 * 1024,
    )

    @field_validator("user_agent")
    @classmethod
    def require_user_agent(
        cls,
        value: str,
    ) -> str:
        cleaned = value.strip()

        if not cleaned:
            raise ValueError("user_agent must not be blank")

        return cleaned

    def to_httpx_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self.connect_timeout_seconds,
            read=self.read_timeout_seconds,
            write=self.write_timeout_seconds,
            pool=self.pool_timeout_seconds,
        )


class DownloadedArtifact(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    staging_path: Path
    retrieved_at: datetime
    attempts: int = Field(ge=1)
    status_code: int = Field(ge=200, le=299)
    byte_size: int = Field(gt=0)
    sha256: Sha256Digest
    content_type: str | None = None
    response_url: str

    @field_validator("retrieved_at")
    @classmethod
    def normalise_retrieved_at(
        cls,
        value: datetime,
    ) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware")

        return value.astimezone(UTC)


type DownloadExecutionResult = DownloadedArtifact | FailedAcquisitionResult


class _EmptyPayloadError(Exception):
    pass


def _exception_detail(
    error: BaseException,
) -> str:
    message = str(error).strip()

    if message:
        return f"{type(error).__name__}: {message}"

    return type(error).__name__


def _remove_if_present(
    path: Path | None,
) -> None:
    if path is None:
        return

    path.unlink(missing_ok=True)


def _stream_response_to_staging(
    response: httpx.Response,
    *,
    source: DatasetSource,
    staging_dir: Path,
    config: HttpDownloadConfig,
    attempts: int,
) -> DownloadedArtifact:
    staging_path: Path | None = None

    try:
        with NamedTemporaryFile(
            mode="wb",
            dir=staging_dir,
            prefix=f".{source.dataset.value}-",
            suffix=f".{source.source_format.value}.part",
            delete=False,
        ) as output:
            staging_path = Path(output.name)
            digest = hashlib.sha256()
            byte_size = 0

            for chunk in response.iter_bytes(chunk_size=config.chunk_size_bytes):
                if not chunk:
                    continue

                output.write(chunk)
                digest.update(chunk)
                byte_size += len(chunk)

            output.flush()
            os.fsync(output.fileno())

        if byte_size == 0:
            raise _EmptyPayloadError("HTTP response body contained zero bytes")

        return DownloadedArtifact(
            staging_path=staging_path,
            retrieved_at=datetime.now(UTC),
            attempts=attempts,
            status_code=response.status_code,
            byte_size=byte_size,
            sha256=digest.hexdigest(),
            content_type=response.headers.get("content-type"),
            response_url=str(response.url),
        )
    except Exception:
        _remove_if_present(staging_path)
        raise


def download_with_retries(
    source: DatasetSource,
    staging_dir: Path,
    *,
    client: httpx.Client,
    retry_policy: RetryPolicy,
    config: HttpDownloadConfig,
    sleeper: Callable[[float], None] = sleep,
) -> DownloadExecutionResult:
    try:
        staging_dir.mkdir(
            parents=True,
            exist_ok=True,
        )
    except OSError as error:
        return FailedAcquisitionResult(
            status=AcquisitionStatus.REJECTED,
            attempts=1,
            failure_reason=AcquisitionFailureReason.FILESYSTEM_ERROR,
            detail=_exception_detail(error),
        )

    for attempt in range(
        1,
        retry_policy.max_attempts + 1,
    ):
        try:
            with client.stream(
                "GET",
                str(source.source_url),
                headers={
                    "User-Agent": config.user_agent,
                },
                timeout=config.to_httpx_timeout(),
                follow_redirects=True,
            ) as response:
                status = classify_http_status(response.status_code)

                if status == AcquisitionStatus.REJECTED:
                    return FailedAcquisitionResult(
                        status=AcquisitionStatus.REJECTED,
                        attempts=attempt,
                        failure_reason=(AcquisitionFailureReason.HTTP_STATUS),
                        detail=(f"HTTP {response.status_code} from {response.url}"),
                    )

                if status == AcquisitionStatus.RETRYABLE_FAILURE:
                    if attempt < retry_policy.max_attempts:
                        sleeper(retry_policy.backoff_seconds(attempt))
                        continue

                    return FailedAcquisitionResult(
                        status=(AcquisitionStatus.RETRYABLE_FAILURE),
                        attempts=attempt,
                        failure_reason=(AcquisitionFailureReason.HTTP_STATUS),
                        detail=(f"HTTP {response.status_code} after retry exhaustion"),
                    )

                try:
                    return _stream_response_to_staging(
                        response,
                        source=source,
                        staging_dir=staging_dir,
                        config=config,
                        attempts=attempt,
                    )
                except _EmptyPayloadError as error:
                    return FailedAcquisitionResult(
                        status=AcquisitionStatus.QUARANTINED,
                        attempts=attempt,
                        failure_reason=(AcquisitionFailureReason.EMPTY_PAYLOAD),
                        detail=_exception_detail(error),
                    )

        except httpx.TimeoutException as error:
            if attempt < retry_policy.max_attempts:
                sleeper(retry_policy.backoff_seconds(attempt))
                continue

            return FailedAcquisitionResult(
                status=AcquisitionStatus.RETRYABLE_FAILURE,
                attempts=attempt,
                failure_reason=(AcquisitionFailureReason.TIMEOUT),
                detail=_exception_detail(error),
            )

        except httpx.TransportError as error:
            if attempt < retry_policy.max_attempts:
                sleeper(retry_policy.backoff_seconds(attempt))
                continue

            return FailedAcquisitionResult(
                status=AcquisitionStatus.RETRYABLE_FAILURE,
                attempts=attempt,
                failure_reason=(AcquisitionFailureReason.TRANSPORT_ERROR),
                detail=_exception_detail(error),
            )

        except OSError as error:
            return FailedAcquisitionResult(
                status=AcquisitionStatus.REJECTED,
                attempts=attempt,
                failure_reason=(AcquisitionFailureReason.FILESYSTEM_ERROR),
                detail=_exception_detail(error),
            )

    raise RuntimeError("retry loop terminated without producing a result")
