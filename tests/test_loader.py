"""
Tests for identity loader
"""

from pathlib import Path

import pytest

from identity_interpreter.loader import IdentityLoadError, lint_identity, load_identity


def test_load_identity():
    """Test loading Identity.yaml"""
    identity_path = Path("Identity.yaml")

    if not identity_path.exists():
        pytest.skip("Identity.yaml not found")

    identity = load_identity(identity_path)

    # Basic assertions
    assert identity.meta.name == "Bartholomew"
    assert identity.meta.version == "1.2.1"
    assert len(identity.red_lines) > 0
    assert len(identity.values_and_principles.core_values) > 0


def test_lint_identity():
    """Test linting Identity.yaml"""
    identity_path = Path("Identity.yaml")

    if not identity_path.exists():
        pytest.skip("Identity.yaml not found")

    warnings = lint_identity(identity_path)

    # Should return a list (may be empty)
    assert isinstance(warnings, list)


def test_invalid_file():
    """Test loading non-existent file"""
    with pytest.raises(IdentityLoadError):
        load_identity("nonexistent.yaml")
