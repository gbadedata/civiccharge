# ADR 0002: Python Dependency Management

**Status:** Accepted
**Date:** 2026-08-15

## Context

CivicCharge requires reproducible development, CI and deployment environments.

A `pip freeze` snapshot records packages installed in one specific environment.
It is therefore unsuitable as the canonical dependency-management strategy for
a project that must be reproducible across development machines, CI runners and
container builds.

Development dependencies such as pytest, Ruff and MyPy are internal engineering
tools rather than optional runtime features of the CivicCharge package.

## Decision

CivicCharge uses:

- `pyproject.toml` as the declarative project and dependency definition;
- dependency groups for development tooling;
- `uv.lock` as the committed dependency lockfile;
- `uv sync --locked` for reproducible environment creation;
- `uv run --locked` for execution against the locked environment.

The repository does not use a manually maintained `requirements.txt` or
`pip freeze` output as its canonical dependency source.

Generated requirements files may be exported from the lockfile only where an
external deployment platform explicitly requires that format.

## Dependency classes

Runtime dependencies are declared under `[project].dependencies`.

Development dependencies are declared under `[dependency-groups].dev`.

Future specialist dependency groups may be added only when justified by an
implemented subsystem. Examples may include `ml`, `ocr`, `serving`, and `docs`.

Dependencies must not be added to the core runtime environment merely because
they may be useful later.

## Lockfile policy

`uv.lock` is committed to version control.

Changes to declared dependencies must update the lockfile in the same change.

CI and deployment workflows must use locked operations and reject stale or
inconsistent lockfiles.

The lockfile must never be edited manually.

## Reproducibility policy

A clean checkout must be able to reconstruct the supported Python environment
using `uv sync --locked`.

Quality gates are executed through the locked environment:

- `uv run --locked pytest`
- `uv run --locked ruff check .`
- `uv run --locked ruff format --check .`
- `uv run --locked mypy`

Commands must not depend on packages installed globally on the host machine.

## Upgrade policy

Dependency upgrades must be intentional.

An upgrade should:

1. modify the declared dependency constraint where necessary;
2. update `uv.lock`;
3. run the complete test, lint, formatting and type-checking gates;
4. review material dependency changes before commit;
5. keep declaration and lockfile changes in the same commit.

## Rationale

This approach separates human-maintained dependency intent from
machine-resolved exact versions while preserving deterministic installation
and an auditable dependency graph.

It reduces environment drift, prevents accidental dependence on globally
installed packages and gives development, CI and deployment a common
reproducibility mechanism.
