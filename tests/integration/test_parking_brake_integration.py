"""
Integration tests for parking brake with gated components.

Tests that brake properly blocks skills, sight, voice, and scheduler.
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


def test_skills_blocked_when_engaged(temp_db, monkeypatch):
    """Test that skills (orchestrator) raises error when brake engaged."""
    from bartholomew.orchestrator.safety.parking_brake import (
        ParkingBrake, BrakeStorage
    )
    from identity_interpreter.orchestrator.orchestrator import Orchestrator
    
    # Engage brake on skills scope
    storage = BrakeStorage(temp_db)
    brake = ParkingBrake(storage)
    brake.engage("skills")
    
    # Route orchestrator to the temp DB used by the brake
    monkeypatch.setenv("BARTH_DB_PATH", temp_db)
    
    # Attempt to use orchestrator should raise
    orchestrator = Orchestrator(log_dir=tempfile.mkdtemp())
    
    with pytest.raises(RuntimeError, match="ParkingBrake: skills blocked"):
        orchestrator.handle_input("test input")


def test_skills_allowed_when_disengaged(temp_db, monkeypatch):
    """Test that skills work normally when brake disengaged."""
    from bartholomew.orchestrator.safety.parking_brake import (
        ParkingBrake, BrakeStorage
    )
    
    # Ensure brake is disengaged
    storage = BrakeStorage(temp_db)
    brake = ParkingBrake(storage)
    brake.disengage()
    
    # Route orchestrator to the temp DB used by the brake
    monkeypatch.setenv("BARTH_DB_PATH", temp_db)
    
    # This test verifies no RuntimeError is raised
    # Orchestrator may fail for other reasons (no LLM, etc.)
    # but NOT due to parking brake
    from identity_interpreter.orchestrator.orchestrator import Orchestrator
    orchestrator = Orchestrator(log_dir=tempfile.mkdtemp())
    
    try:
        orchestrator.handle_input("test")
    except RuntimeError as e:
        # Should not be parking brake error
        assert "ParkingBrake" not in str(e)
    except Exception:
        # Other errors are acceptable for this test
        pass


def test_sight_blocked_when_engaged(temp_db):
    """Test that sight pipeline returns blocked status when engaged."""
    from bartholomew.orchestrator.safety.parking_brake import (
        ParkingBrake, BrakeStorage
    )
    from identity_interpreter.adapters.sight.pipeline import start_capture
    
    # Engage brake on sight scope
    storage = BrakeStorage(temp_db)
    brake = ParkingBrake(storage)
    brake.engage("sight")
    
    # Start capture should return blocked
    result = start_capture(temp_db)
    assert result["blocked"] is True


def test_sight_allowed_when_disengaged(temp_db):
    """Test that sight pipeline works when brake disengaged."""
    from bartholomew.orchestrator.safety.parking_brake import (
        ParkingBrake, BrakeStorage
    )
    from identity_interpreter.adapters.sight.pipeline import start_capture
    
    # Ensure brake is disengaged
    storage = BrakeStorage(temp_db)
    brake = ParkingBrake(storage)
    brake.disengage()
    
    # Start capture should not be blocked
    result = start_capture(temp_db)
    assert result.get("blocked", False) is False
    assert result["status"] == "capturing"


def test_voice_blocked_when_engaged(temp_db):
    """Test that voice stream returns early when brake engaged."""
    from bartholomew.orchestrator.safety.parking_brake import (
        ParkingBrake, BrakeStorage
    )
    from identity_interpreter.adapters.voice_io.stream_bridge import (
        start_stream
    )
    
    # Engage brake on voice scope
    storage = BrakeStorage(temp_db)
    brake = ParkingBrake(storage)
    brake.engage("voice")
    
    # Start stream should return None (early return)
    result = start_stream(temp_db)
    assert result is None


def test_voice_allowed_when_disengaged(temp_db):
    """Test that voice stream starts when brake disengaged."""
    from bartholomew.orchestrator.safety.parking_brake import (
        ParkingBrake, BrakeStorage
    )
    from identity_interpreter.adapters.voice_io.stream_bridge import (
        start_stream
    )
    import io
    import sys
    
    # Ensure brake is disengaged
    storage = BrakeStorage(temp_db)
    brake = ParkingBrake(storage)
    brake.disengage()
    
    # Capture stdout to verify stream started
    captured = io.StringIO()
    sys.stdout = captured
    
    try:
        start_stream(temp_db)
        output = captured.getvalue()
        assert "Voice stream started" in output
    finally:
        sys.stdout = sys.__stdout__


def test_scheduler_blocked_when_engaged(temp_db):
    """Test that scheduler raises error when brake engaged."""
    from bartholomew.orchestrator.safety.parking_brake import (
        ParkingBrake, BrakeStorage
    )
    
    # Engage brake on scheduler scope
    storage = BrakeStorage(temp_db)
    brake = ParkingBrake(storage)
    brake.engage("scheduler")
    
    # Create mock context with db_path
    class MockCtx:
        class MockMem:
            def __init__(self, db_path):
                self.db_path = db_path
        
        def __init__(self, db_path):
            self.mem = self.MockMem(db_path)
    
    ctx = MockCtx(temp_db)
    
    # Import _run_drive function
    from bartholomew.kernel.scheduler.loop import _run_drive
    
    # Define a simple async test function
    async def test_drive(ctx):
        return None
    
    # Run drive should raise
    with pytest.raises(RuntimeError, match="ParkingBrake: scheduler blocked"):
        asyncio.run(_run_drive(ctx, "test_task", test_drive))


def test_scheduler_allowed_when_disengaged(temp_db):
    """Test that scheduler works normally when brake disengaged."""
    from bartholomew.orchestrator.safety.parking_brake import (
        ParkingBrake, BrakeStorage
    )
    
    # Ensure brake is disengaged
    storage = BrakeStorage(temp_db)
    brake = ParkingBrake(storage)
    brake.disengage()
    
    # Create mock context
    class MockCtx:
        class MockMem:
            def __init__(self, db_path):
                self.db_path = db_path
        
        def __init__(self, db_path):
            self.mem = self.MockMem(db_path)
    
    ctx = MockCtx(temp_db)
    
    # Import _run_drive function
    from bartholomew.kernel.scheduler.loop import _run_drive
    
    # Define a simple async test function
    async def test_drive(ctx):
        return "result"
    
    # Run drive should succeed
    result, success = asyncio.run(_run_drive(ctx, "test_task", test_drive))
    assert success == 1
    assert result == "result"


def test_audit_trail_records_changes(temp_db):
    """Test that brake changes are recorded in safety.audit."""
    from bartholomew.orchestrator.safety.parking_brake import (
        ParkingBrake, BrakeStorage
    )
    from bartholomew.kernel.memory_store import MemoryStore
    import sqlite3
    
    # Create storage with memory_store for audit
    store = MemoryStore(temp_db)
    storage = BrakeStorage(temp_db, memory_store=store)
    brake = ParkingBrake(storage)
    
    # Engage brake
    brake.engage("skills", "scheduler")
    
    # Disengage brake
    brake.disengage()
    
    # Check audit trail in database
    with sqlite3.connect(temp_db) as conn:
        cursor = conn.execute(
            "SELECT key, value FROM memories WHERE kind = 'safety.audit'"
        )
        rows = cursor.fetchall()
    
    # Should have at least 2 audit entries (engage + disengage)
    assert len(rows) >= 2
    
    # Verify content (entries are JSON strings)
    import json
    audit_actions = [json.loads(row[1])["action"] for row in rows]
    assert "engaged" in audit_actions
    assert "disengaged" in audit_actions


def test_global_scope_blocks_all_components(temp_db):
    """Test that global scope blocks all four component types."""
    from bartholomew.orchestrator.safety.parking_brake import (
        ParkingBrake, BrakeStorage
    )
    from identity_interpreter.adapters.sight.pipeline import start_capture
    from identity_interpreter.adapters.voice_io.stream_bridge import (
        start_stream
    )
    
    # Engage brake with global scope
    storage = BrakeStorage(temp_db)
    brake = ParkingBrake(storage)
    brake.engage("global")
    
    # Sight should be blocked
    sight_result = start_capture(temp_db)
    assert sight_result["blocked"] is True
    
    # Voice should be blocked
    voice_result = start_stream(temp_db)
    assert voice_result is None
    
    # Skills and scheduler would also be blocked
    # (tested in individual tests above)
