"""
Unit tests for parking brake persistence and state reload.
"""

import asyncio
import os
import tempfile

import pytest


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


def test_initial_state_is_disengaged(temp_db):
    """Test that parking brake starts disengaged."""
    from bartholomew.orchestrator.safety.parking_brake import BrakeStorage, ParkingBrake

    storage = BrakeStorage(temp_db)
    brake = ParkingBrake(storage)
    state = brake.state()

    assert state.engaged is False
    assert len(state.scopes) == 0


def test_engage_with_scopes(temp_db):
    """Test engaging brake with specific scopes."""
    from bartholomew.orchestrator.safety.parking_brake import BrakeStorage, ParkingBrake

    storage = BrakeStorage(temp_db)
    brake = ParkingBrake(storage)

    brake.engage("skills", "scheduler")
    state = brake.state()

    assert state.engaged is True
    assert state.scopes == {"skills", "scheduler"}


def test_engage_without_scopes_defaults_to_global(temp_db):
    """Test that engage without args defaults to global scope."""
    from bartholomew.orchestrator.safety.parking_brake import BrakeStorage, ParkingBrake

    storage = BrakeStorage(temp_db)
    brake = ParkingBrake(storage)

    brake.engage()
    state = brake.state()

    assert state.engaged is True
    assert state.scopes == {"global"}


def test_disengage_clears_all_scopes(temp_db):
    """Test that disengage clears engaged flag and scopes."""
    from bartholomew.orchestrator.safety.parking_brake import BrakeStorage, ParkingBrake

    storage = BrakeStorage(temp_db)
    brake = ParkingBrake(storage)

    brake.engage("skills", "voice")
    brake.disengage()
    state = brake.state()

    assert state.engaged is False
    assert len(state.scopes) == 0


def test_persistence_roundtrip(temp_db):
    """Test that state persists across instances."""
    from bartholomew.orchestrator.safety.parking_brake import BrakeStorage, ParkingBrake

    # First instance: engage with scopes
    storage1 = BrakeStorage(temp_db)
    brake1 = ParkingBrake(storage1)
    brake1.engage("skills", "sight", "scheduler")

    # Second instance: should load same state
    storage2 = BrakeStorage(temp_db)
    brake2 = ParkingBrake(storage2)
    state = brake2.state()

    assert state.engaged is True
    assert state.scopes == {"skills", "sight", "scheduler"}


def test_state_reloads_after_change(temp_db):
    """Test that state reloads correctly after changes."""
    from bartholomew.orchestrator.safety.parking_brake import BrakeStorage, ParkingBrake

    storage = BrakeStorage(temp_db)
    brake = ParkingBrake(storage)

    # Initial state
    assert brake.state().engaged is False

    # Engage
    brake.engage("voice")
    assert brake.state().engaged is True
    assert brake.state().scopes == {"voice"}

    # Change scopes
    brake.engage("global")
    assert brake.state().scopes == {"global"}

    # Disengage
    brake.disengage()
    assert brake.state().engaged is False
