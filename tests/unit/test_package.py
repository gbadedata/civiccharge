"""Tests for the CivicCharge package."""

import civiccharge


def test_package_version() -> None:
    """The package exposes its current semantic version."""
    assert civiccharge.__version__ == "0.1.0"
