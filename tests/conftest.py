"""
Test fixtures and helpers for Bartholomew test suite.

Provides common fixtures for coverage-gated tests including time freezing,
parking brake helpers, and minimal storage shims for unit testing.
"""

import json
import platform
from contextlib import contextmanager
from dataclasses import dataclass

import pytest
from freezegun import freeze_time


# ============================================================================
# Windows FTS5/SQLite Skip Marker
# ============================================================================
# Windows Python builds often lack FTS5 with matchinfo() support.
# These tests require FTS5 features not available on standard Windows builds.

IS_WINDOWS = platform.system() == "Windows"
SKIP_WINDOWS_FTS = pytest.mark.skipif(
    IS_WINDOWS,
    reason="FTS5/matchinfo not available on Windows Python builds",
)


@pytest.fixture
def frozen_now():
    """
    Freeze time to a stable UTC timestamp for deterministic tests.

    Uses 2025-01-01T00:00:00Z as the frozen time.
    """
    with freeze_time("2025-01-01T00:00:00Z"):
        yield


# Minimal in-memory storage shim for ParkingBrake unit tests
@dataclass
class _MemStorage:
    """Fake in-memory storage for ParkingBrake tests (no DB required)."""

    value: str | None = None  # JSON string payload

    def fetch_flag(self, key: str) -> str | None:
        """Fetch stored flag value."""
        return self.value

    def upsert_flag(self, key: str, value: str, updated_at: int) -> None:
        """Store flag value."""
        self.value = value

    def append_memory(self, kind: str, value: dict) -> None:
        """No-op audit logging for unit tests."""
        pass


@contextmanager
def _engaged_brake(scopes=("skills",)):
    """
    Create and engage a ParkingBrake with specified scopes.

    Args:
        scopes: Tuple of scope names to engage (default: ("skills",))

    Yields:
        Engaged ParkingBrake instance
    """
    # Local import to avoid test import side effects
    from bartholomew.orchestrator.safety.parking_brake import ParkingBrake

    storage = _MemStorage(value=json.dumps({"engaged": False, "scopes": []}))
    brake = ParkingBrake(storage=storage)
    brake.engage(*scopes)
    try:
        yield brake
    finally:
        brake.disengage()


@pytest.fixture
def parking_brake_engaged():
    """
    Factory fixture for running tests with an engaged ParkingBrake.

    Usage:
        def test_something(parking_brake_engaged):
            with parking_brake_engaged(scopes=("skills",)) as brake:
                assert brake.is_blocked("skills")
    """
    return _engaged_brake
