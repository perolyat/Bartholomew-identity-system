"""Smoke tests for basic import health checks."""

import pytest


@pytest.mark.smoke
def test_imports_smoke():
    """Test that bartholomew package imports successfully."""
    import bartholomew  # noqa: F401

    # If we have a __version__, optionally assert it's non-empty
    try:
        from bartholomew import __version__

        assert isinstance(__version__, str) and len(__version__) > 0
    except (ImportError, AttributeError):
        # __version__ may not be defined yet, that's okay for smoke test
        pass
