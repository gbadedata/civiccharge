from datetime import date
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PlanningDataset(StrEnum):
    ARTICLE_4_DIRECTION = "article-4-direction"
    CONSERVATION_AREA_DOCUMENT = "conservation-area-document"


class ExternalPlanningDataModel(BaseModel):
    # The upstream Planning Data service is beta. Additive upstream fields
    # must not break ingestion before they have been assessed.
    model_config = ConfigDict(
        extra="allow",
        populate_by_name=True,
    )


class PlanningDataEntityDTO(ExternalPlanningDataModel):
    entity: int | str
    dataset: str
    reference: str = ""
    name: str = ""
    description: str = ""
    document_url: str = Field(default="", alias="document-url")
    documentation_url: str = Field(default="", alias="documentation-url")
    organisation_entity: int | str | None = Field(
        default=None,
        alias="organisation-entity",
    )
    start_date: str = Field(default="", alias="start-date")
    end_date: str = Field(default="", alias="end-date")
    entry_date: str = Field(default="", alias="entry-date")
    quality: str = ""
    typology: str = ""
    document_type: str = Field(default="", alias="document-type")
    conservation_area: str = Field(default="", alias="conservation-area")


class PlanningDataLinksDTO(ExternalPlanningDataModel):
    first: str | None = None
    last: str | None = None
    next: str | None = None
    prev: str | None = None


class PlanningDataEntityPageDTO(ExternalPlanningDataModel):
    entities: list[PlanningDataEntityDTO]
    links: PlanningDataLinksDTO
    count: int


class PlanningDatasetMetadataDTO(ExternalPlanningDataModel):
    dataset: str
    name: str = ""
    collection: str = ""
    phase: str = ""
    licence: str = ""
    licence_text: str = Field(default="", alias="licence-text")
    attribution: str = ""
    attribution_text: str = Field(default="", alias="attribution-text")
    entity_count: int | str = Field(alias="entity-count")
    entity_minimum: int | str = Field(alias="entity-minimum")
    entity_maximum: int | str = Field(alias="entity-maximum")
    typology: str = ""
    version: str = ""


class CanonicalPlanningRecordBase(BaseModel):
    # Internal CivicCharge contracts are deliberately strict.
    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_id: int = Field(gt=0)
    reference: str | None
    name: str | None
    organisation_entity: int | None
    document_url: str | None
    documentation_url: str | None
    start_date: date | None
    end_date: date | None
    entry_date: date | None
    quality: str | None


class Article4DirectionRecord(CanonicalPlanningRecordBase):
    dataset: Literal[PlanningDataset.ARTICLE_4_DIRECTION] = PlanningDataset.ARTICLE_4_DIRECTION
    description: str | None


class ConservationAreaDocumentRecord(CanonicalPlanningRecordBase):
    dataset: Literal[PlanningDataset.CONSERVATION_AREA_DOCUMENT] = (
        PlanningDataset.CONSERVATION_AREA_DOCUMENT
    )
    document_type: str | None
    conservation_area: str | None


type CanonicalPlanningRecord = Annotated[
    Article4DirectionRecord | ConservationAreaDocumentRecord,
    Field(discriminator="dataset"),
]


class CanonicalDatasetMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset: PlanningDataset
    name: str
    collection: str
    phase: str
    licence: str
    licence_text: str
    attribution: str
    attribution_text: str
    entity_count: int = Field(ge=0)
    entity_minimum: int = Field(gt=0)
    entity_maximum: int = Field(gt=0)
    typology: str
    version: str | None


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None

    cleaned = value.strip()
    return cleaned or None


def _parse_optional_date(value: str, field_name: str) -> date | None:
    cleaned = value.strip()

    if not cleaned:
        return None

    try:
        return date.fromisoformat(cleaned)
    except ValueError as exc:
        raise ValueError(f"Invalid Planning Data {field_name}: {value!r}") from exc


def _parse_optional_integer(
    value: int | str | None,
    field_name: str,
) -> int | None:
    if value is None:
        return None

    if isinstance(value, int):
        return value

    cleaned = value.strip()

    if not cleaned:
        return None

    try:
        return int(cleaned)
    except ValueError as exc:
        raise ValueError(f"Invalid Planning Data {field_name}: {value!r}") from exc


def _parse_required_integer(
    value: int | str,
    field_name: str,
) -> int:
    parsed = _parse_optional_integer(value, field_name)

    if parsed is None:
        raise ValueError(f"Planning Data {field_name} must not be empty")

    return parsed


def normalise_planning_entity(
    entity: PlanningDataEntityDTO,
) -> CanonicalPlanningRecord:
    common: dict[str, Any] = {
        "entity_id": _parse_required_integer(entity.entity, "entity"),
        "reference": _blank_to_none(entity.reference),
        "name": _blank_to_none(entity.name),
        "organisation_entity": _parse_optional_integer(
            entity.organisation_entity,
            "organisation-entity",
        ),
        "document_url": _blank_to_none(entity.document_url),
        "documentation_url": _blank_to_none(entity.documentation_url),
        "start_date": _parse_optional_date(
            entity.start_date,
            "start-date",
        ),
        "end_date": _parse_optional_date(
            entity.end_date,
            "end-date",
        ),
        "entry_date": _parse_optional_date(
            entity.entry_date,
            "entry-date",
        ),
        "quality": _blank_to_none(entity.quality),
    }

    if entity.dataset == PlanningDataset.ARTICLE_4_DIRECTION:
        return Article4DirectionRecord(
            **common,
            description=_blank_to_none(entity.description),
        )

    if entity.dataset == PlanningDataset.CONSERVATION_AREA_DOCUMENT:
        return ConservationAreaDocumentRecord(
            **common,
            document_type=_blank_to_none(entity.document_type),
            conservation_area=_blank_to_none(entity.conservation_area),
        )

    raise ValueError(f"Unsupported Planning Data dataset: {entity.dataset!r}")


def normalise_dataset_metadata(
    metadata: PlanningDatasetMetadataDTO,
) -> CanonicalDatasetMetadata:
    try:
        dataset = PlanningDataset(metadata.dataset)
    except ValueError as exc:
        raise ValueError(f"Unsupported Planning Data dataset: {metadata.dataset!r}") from exc

    return CanonicalDatasetMetadata(
        dataset=dataset,
        name=metadata.name,
        collection=metadata.collection,
        phase=metadata.phase,
        licence=metadata.licence,
        licence_text=metadata.licence_text,
        attribution=metadata.attribution,
        attribution_text=metadata.attribution_text,
        entity_count=_parse_required_integer(
            metadata.entity_count,
            "entity-count",
        ),
        entity_minimum=_parse_required_integer(
            metadata.entity_minimum,
            "entity-minimum",
        ),
        entity_maximum=_parse_required_integer(
            metadata.entity_maximum,
            "entity-maximum",
        ),
        typology=metadata.typology,
        version=_blank_to_none(metadata.version),
    )
