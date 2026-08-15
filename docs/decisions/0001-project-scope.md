# ADR 0001: CivicCharge MVP Scope

**Status:** Accepted  
**Date:** 2026-08-15

## Context

Historic English local-authority planning information exists across native
PDFs, scanned PDFs, image-heavy documents, tables, maps and inconsistent
authority-specific templates.

The target output is structured and standardised, but a useful public-sector
Document AI system must also preserve evidence, provenance and uncertainty.

## Decision

CivicCharge v1.0 will focus on extracting and assuring structured information
from:

- Article 4 Direction documents;
- Conservation Area documents.

The MVP will include:

- public-data discovery;
- source provenance and checksums;
- document acquisition;
- PDF inspection;
- native-text versus OCR routing;
- OCR evaluation;
- deterministic extraction baselines;
- weak supervision;
- text-only modelling;
- LayoutLMv3 multimodal modelling;
- AutoML document classification;
- local-authority-held-out evaluation;
- evidence validation;
- confidence calibration and abstention;
- PostgreSQL persistence;
- FastAPI serving;
- Docker;
- automated testing;
- responsible-AI documentation.

## Explicitly excluded from v1.0

The following are not part of the MVP:

- scraping every UK local authority;
- legal or planning decision-making;
- full map polygon extraction;
- automatic georeferencing;
- Kubernetes;
- graph databases;
- vector databases without a demonstrated requirement;
- foundation-model training;
- autonomous agents.

## Rationale

The strongest portfolio evidence comes from rigorous data engineering,
evaluation, provenance, model assurance and reproducibility.

Adding unrelated infrastructure would increase implementation risk without
strengthening the central technical argument.
