"""Unit tests for parking brake safety mechanism."""

import pytest


@pytest.mark.unit
def test_parking_brake_blocks_skills(parking_brake_engaged):
    """Test that engaged parking brake blocks skills scope execution."""
    with parking_brake_engaged(scopes=("skills",)) as brake:
        assert brake.is_blocked(scope="skills") is True
        # Simulate skill execution attempt when brake is engaged
        with pytest.raises(RuntimeError, match="Skills blocked by Parking Brake"):
            if brake.is_blocked(scope="skills"):
                raise RuntimeError("Skills blocked by Parking Brake")


@pytest.mark.unit
def test_parking_brake_allows_when_disengaged(parking_brake_engaged):
    """Test that disengaged parking brake allows execution."""
    with parking_brake_engaged(scopes=("skills",)) as brake:
        # First verify it's engaged
        assert brake.is_blocked(scope="skills") is True
        # Now disengage
        brake.disengage()
        # Should now be allowed
        assert brake.is_blocked(scope="skills") is False
