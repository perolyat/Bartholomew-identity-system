"""Unit tests for time_utils module."""

import re

import pytest

from bartholomew.kernel.time_utils import utc_now_iso


@pytest.mark.unit
def test_utc_now_iso_format(frozen_now):
    """Test that utc_now_iso returns properly formatted ISO string with UTC timezone."""
    s = utc_now_iso()
    # Expect RFC3339-like with UTC offset or 'Z'
    assert s.endswith("Z") or s.endswith("+00:00"), f"Expected UTC timezone marker, got: {s}"
    # Ensure it matches ISO format: YYYY-MM-DDTHH:MM:SS
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", s), f"Invalid ISO format: {s}"
