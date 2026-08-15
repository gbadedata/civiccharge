from datetime import date

import pytest
from pydantic import ValidationError

from civiccharge.ingest.planning_data_models import (
    Article4DirectionRecord,
    ConservationAreaDocumentRecord,
    PlanningDataEntityDTO,
    PlanningDataset,
    PlanningDatasetMetadataDTO,
    normalise_dataset_metadata,
    normalise_planning_entity,
)


def test_article_4_entity_is_normalised() -> None:
    dto = PlanningDataEntityDTO.model_validate(
        {
            "entity": 6100007,
            "dataset": "article-4-direction",
            "reference": "A4HMO1",
            "name": "Jesmond and Heaton",
            "description": "Example description",
            "document-url": "https://example.gov.uk/article4.pdf",
            "documentation-url": "https://example.gov.uk/article4",
            "organisation-entity": 228,
            "start-date": "2011-11-25",
            "end-date": "",
            "entry-date": "2023-02-17",
            "quality": "authoritative",
        }
    )

    record = normalise_planning_entity(dto)

    assert isinstance(record, Article4DirectionRecord)
    assert record.entity_id == 6100007
    assert record.reference == "A4HMO1"
    assert record.organisation_entity == 228
    assert record.start_date == date(2011, 11, 25)
    assert record.end_date is None
    assert record.entry_date == date(2023, 2, 17)


def test_external_entity_tolerates_string_organisation_id() -> None:
    dto = PlanningDataEntityDTO.model_validate(
        {
            "entity": 6309902,
            "dataset": "conservation-area-document",
            "reference": "D_TEST_CA_1",
            "name": "Example conservation area",
            "document-url": "https://example.gov.uk/example.pdf",
            "documentation-url": "https://example.gov.uk/conservation",
            "organisation-entity": "232",
            "start-date": "1979-01-01",
            "end-date": "",
            "entry-date": "2025-01-16",
            "quality": "some",
            "document-type": "area-map",
            "conservation-area": "TEST_CA_1",
        }
    )

    record = normalise_planning_entity(dto)

    assert isinstance(record, ConservationAreaDocumentRecord)
    assert record.organisation_entity == 232
    assert record.document_type == "area-map"
    assert record.conservation_area == "TEST_CA_1"


def test_external_entity_tolerates_additive_upstream_fields() -> None:
    dto = PlanningDataEntityDTO.model_validate(
        {
            "entity": 6100001,
            "dataset": "article-4-direction",
            "future-upstream-field": "new-value",
        }
    )

    assert dto.model_extra == {
        "future-upstream-field": "new-value",
    }


def test_empty_strings_are_normalised_to_none() -> None:
    dto = PlanningDataEntityDTO.model_validate(
        {
            "entity": 6100001,
            "dataset": "article-4-direction",
            "reference": "",
            "name": " ",
            "document-url": "",
            "documentation-url": "",
            "organisation-entity": "",
            "start-date": "",
            "end-date": "",
            "entry-date": "",
            "quality": "",
        }
    )

    record = normalise_planning_entity(dto)

    assert record.reference is None
    assert record.name is None
    assert record.document_url is None
    assert record.documentation_url is None
    assert record.organisation_entity is None
    assert record.start_date is None
    assert record.end_date is None
    assert record.entry_date is None
    assert record.quality is None


def test_invalid_date_is_rejected() -> None:
    dto = PlanningDataEntityDTO.model_validate(
        {
            "entity": 6100001,
            "dataset": "article-4-direction",
            "start-date": "not-a-date",
        }
    )

    with pytest.raises(
        ValueError,
        match="Invalid Planning Data start-date",
    ):
        normalise_planning_entity(dto)


def test_unsupported_dataset_is_rejected() -> None:
    dto = PlanningDataEntityDTO.model_validate(
        {
            "entity": 123,
            "dataset": "unexpected-dataset",
        }
    )

    with pytest.raises(
        ValueError,
        match="Unsupported Planning Data dataset",
    ):
        normalise_planning_entity(dto)


def test_dataset_metadata_is_normalised() -> None:
    dto = PlanningDatasetMetadataDTO.model_validate(
        {
            "dataset": "conservation-area-document",
            "name": "Conservation area document",
            "collection": "conservation-area",
            "phase": "beta",
            "licence": "ogl3",
            "licence-text": "Open Government Licence",
            "attribution": "crown-copyright",
            "attribution-text": "Crown copyright",
            "entity-count": 13950,
            "entity-minimum": 6300000,
            "entity-maximum": 6399999,
            "typology": "document",
            "version": "",
        }
    )

    metadata = normalise_dataset_metadata(dto)

    assert metadata.dataset is PlanningDataset.CONSERVATION_AREA_DOCUMENT
    assert metadata.entity_count == 13950
    assert metadata.entity_minimum == 6300000
    assert metadata.entity_maximum == 6399999
    assert metadata.version is None


def test_article_4_canonical_record_rejects_wrong_dataset() -> None:
    with pytest.raises(ValidationError):
        Article4DirectionRecord.model_validate(
            {
                "entity_id": 6100001,
                "dataset": "conservation-area-document",
                "reference": None,
                "name": None,
                "organisation_entity": None,
                "document_url": None,
                "documentation_url": None,
                "start_date": None,
                "end_date": None,
                "entry_date": None,
                "quality": None,
                "description": None,
            }
        )
