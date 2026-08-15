from enum import StrEnum
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

from civiccharge.ingest.planning_data_models import PlanningDataset
from civiccharge.ingest.provenance import Sha256Digest, SourceFormat

_HTTP_URL_ADAPTER = TypeAdapter(HttpUrl)


class AcquisitionStatus(StrEnum):
    ACCEPTED = "accepted"
    RETRYABLE_FAILURE = "retryable_failure"
    REJECTED = "rejected"
    QUARANTINED = "quarantined"


class AcquisitionFailureReason(StrEnum):
    TRANSPORT_ERROR = "transport_error"
    TIMEOUT = "timeout"
    HTTP_STATUS = "http_status"
    EMPTY_PAYLOAD = "empty_payload"
    INVALID_CONTENT = "invalid_content"
    ENTITY_COUNT_MISMATCH = "entity_count_mismatch"
    FILESYSTEM_ERROR = "filesystem_error"


class DatasetSource(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    dataset: PlanningDataset
    source_url: HttpUrl
    source_format: SourceFormat


def create_dataset_source(
    *,
    dataset: PlanningDataset,
    source_url: str,
    source_format: SourceFormat,
) -> DatasetSource:
    validated_url = _HTTP_URL_ADAPTER.validate_python(source_url)

    return DatasetSource(
        dataset=dataset,
        source_url=validated_url,
        source_format=source_format,
    )


class RetryPolicy(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    max_attempts: int = Field(default=4, ge=1, le=10)
    base_delay_seconds: float = Field(default=0.5, gt=0)
    max_delay_seconds: float = Field(default=8.0, gt=0)

    @model_validator(mode="after")
    def validate_delay_bounds(self) -> "RetryPolicy":
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError(
                "max_delay_seconds must be greater than or equal to base_delay_seconds"
            )

        return self

    def backoff_seconds(
        self,
        retry_number: int,
    ) -> float:
        if retry_number < 1:
            raise ValueError("retry_number must be at least 1")

        delay = self.base_delay_seconds * (2.0 ** (retry_number - 1))
        return min(delay, self.max_delay_seconds)


class AcquisitionResultBase(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    attempts: int = Field(ge=1)


class AcceptedAcquisitionResult(AcquisitionResultBase):
    status: Literal[AcquisitionStatus.ACCEPTED] = AcquisitionStatus.ACCEPTED
    byte_size: int = Field(gt=0)
    sha256: Sha256Digest
    observed_entity_count: int = Field(ge=0)


class FailedAcquisitionResult(AcquisitionResultBase):
    status: Literal[
        AcquisitionStatus.RETRYABLE_FAILURE,
        AcquisitionStatus.REJECTED,
        AcquisitionStatus.QUARANTINED,
    ]
    failure_reason: AcquisitionFailureReason
    detail: str
    byte_size: int = Field(default=0, ge=0)
    sha256: Sha256Digest | None = None
    observed_entity_count: int | None = Field(default=None, ge=0)

    @field_validator("detail")
    @classmethod
    def require_failure_detail(
        cls,
        value: str,
    ) -> str:
        cleaned = value.strip()

        if not cleaned:
            raise ValueError("detail must not be blank")

        return cleaned


type AcquisitionResult = Annotated[
    AcceptedAcquisitionResult | FailedAcquisitionResult,
    Field(discriminator="status"),
]


RETRYABLE_HTTP_STATUS_CODES = frozenset(
    {
        408,
        429,
        500,
        502,
        503,
        504,
    }
)


def classify_http_status(
    status_code: int,
) -> AcquisitionStatus:
    if not 100 <= status_code <= 599:
        raise ValueError("HTTP status code must be between 100 and 599")

    if 200 <= status_code <= 299:
        return AcquisitionStatus.ACCEPTED

    if status_code in RETRYABLE_HTTP_STATUS_CODES:
        return AcquisitionStatus.RETRYABLE_FAILURE

    return AcquisitionStatus.REJECTED
