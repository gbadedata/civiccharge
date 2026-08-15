# CivicCharge

## Assured Multimodal Intelligence for Public Planning Records

CivicCharge is a production-shaped Document AI system for converting
heterogeneous historic English local-authority planning documents into
validated, traceable structured records.

The project investigates one central question:

> Can multimodal AI transform inconsistent public planning documents into
> structured data while knowing when its output is safe to accept?

CivicCharge combines:

- document discovery and ingestion;
- native PDF text extraction;
- OCR for scanned or degraded documents;
- document classification;
- structured information extraction;
- multimodal document understanding;
- authoritative-data enrichment;
- field-level evidence and provenance;
- confidence calibration;
- abstention;
- human review.

## Initial document families

The MVP focuses on:

- Article 4 Directions;
- Conservation Area documents.

## Model ladder

CivicCharge will compare increasingly complex approaches rather than assuming
the largest model is automatically best:

1. deterministic extraction rules;
2. text-only transformer;
3. LayoutLMv3 multimodal transformer;
4. FLAML AutoML document classifier;
5. optional generative/VLM challenger after v1.0.

## Core engineering principles

1. Evidence before prediction.
2. Null over invention.
3. Entire local authorities are held out during primary evaluation.
4. Simple baselines must exist before complex models.
5. Accepted fields must be traceable to source evidence.
6. Model uncertainty must change system behaviour.
7. Public availability does not imply unrestricted redistribution.
8. Human review remains part of the system.

## Assurance policy

Each processed record ultimately receives one of three outcomes:

- `ACCEPT`
- `REVIEW`
- `REJECT`

Automatic acceptance will only be enabled where measured precision and
evidence-validation requirements are satisfied.

## Repository status

Development started: 15 August 2026.

Current milestone:

`v0.1-data-baseline`
