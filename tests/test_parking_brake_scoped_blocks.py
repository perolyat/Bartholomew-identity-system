"""
Unit tests for parking brake scoped blocking logic.
"""
import pytest
import tempfile
import os
import asyncio


@pytest.fixture
def temp_db():
    """Temporary database for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    
    # Initialize schema
    from bartholomew.kernel.memory_store import MemoryStore
    store = MemoryStore(path)
    asyncio.run(store.init())
    
    yield path
    
    # Cleanup
    try:
        os.unlink(path)
    except Exception:
        pass


def test_is_blocked_when_disengaged(temp_db):
    """Test that all scopes are allowed when brake is disengaged."""
    from bartholomew.orchestrator.safety.parking_brake import (
        ParkingBrake, BrakeStorage
    )
    
    storage = BrakeStorage(temp_db)
    brake = ParkingBrake(storage)
    
    assert brake.is_blocked("skills") is False
    assert brake.is_blocked("sight") is False
    assert brake.is_blocked("voice") is False
    assert brake.is_blocked("scheduler") is False
    assert brake.is_blocked("anything") is False


def test_is_blocked_with_global_scope(temp_db):
    """Test that global scope blocks all components."""
    from bartholomew.orchestrator.safety.parking_brake import (
        ParkingBrake, BrakeStorage
    )
    
    storage = BrakeStorage(temp_db)
    brake = ParkingBrake(storage)
    brake.engage("global")
    
    assert brake.is_blocked("skills") is True
    assert brake.is_blocked("sight") is True
    assert brake.is_blocked("voice") is True
    assert brake.is_blocked("scheduler") is True
    assert brake.is_blocked("anything") is True


def test_is_blocked_with_specific_scopes(temp_db):
    """Test that specific scopes only block named components."""
    from bartholomew.orchestrator.safety.parking_brake import (
        ParkingBrake, BrakeStorage
    )
    
    storage = BrakeStorage(temp_db)
    brake = ParkingBrake(storage)
    brake.engage("skills", "scheduler")
    
    # Blocked scopes
    assert brake.is_blocked("skills") is True
    assert brake.is_blocked("scheduler") is True
    
    # Allowed scopes
    assert brake.is_blocked("sight") is False
    assert brake.is_blocked("voice") is False
    assert brake.is_blocked("other") is False


def test_global_supersedes_specific_scopes(temp_db):
    """Test that global scope supersedes any specific scopes."""
    from bartholomew.orchestrator.safety.parking_brake import (
        ParkingBrake, BrakeStorage
    )
    
    storage = BrakeStorage(temp_db)
    brake = ParkingBrake(storage)
    brake.engage("global", "skills")  # global + specific
    
    # Everything should be blocked due to global
    assert brake.is_blocked("skills") is True
    assert brake.is_blocked("sight") is True
    assert brake.is_blocked("voice") is True
    assert brake.is_blocked("scheduler") is True


def test_all_four_component_scopes(temp_db):
    """Test blocking each of the four main component scopes."""
    from bartholomew.orchestrator.safety.parking_brake import (
        ParkingBrake, BrakeStorage
    )
    
    storage = BrakeStorage(temp_db)
    brake = ParkingBrake(storage)
    
    # Test skills scope
    brake.engage("skills")
    assert brake.is_blocked("skills") is True
    assert brake.is_blocked("scheduler") is False
    
    # Test sight scope
    brake.engage("sight")
    assert brake.is_blocked("sight") is True
    assert brake.is_blocked("skills") is False
    
    # Test voice scope
    brake.engage("voice")
    assert brake.is_blocked("voice") is True
    assert brake.is_blocked("sight") is False
    
    # Test scheduler scope
    brake.engage("scheduler")
    assert brake.is_blocked("scheduler") is True
    assert brake.is_blocked("voice") is False
