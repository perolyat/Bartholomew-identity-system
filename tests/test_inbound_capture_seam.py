"""Unit-level evidence for the governed inbound capture seam (Session D).

Exercises `run_inbound_through_runtime_contract()` and the store directly,
against real SQLite files -- the boundary being proven is persistence and
governance, so neither is mocked. The HTTP boundary has its own file
(`tests/integration/test_inbound_http.py`), which runs a real server.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from bartholomew.kernel import inbound_store
from bartholomew.kernel.runtime_contract import run_inbound_through_runtime_contract
from bartholomew.orchestrator.safety.governance_store import (
    GovernanceStore,
    ParkingBrakeEngagedError,
)


@pytest.fixture
def db_path(tmp_path):
    """A real database with the memory + governance schema the seam needs.

    Real schema, real file: these tests are about what actually lands on
    disk, so nothing here is a stand-in for the database.
    """
    import asyncio

    from bartholomew.kernel.memory_store import MemoryStore
    from bartholomew.orchestrator.safety import governance_store as gs

    path = str(tmp_path / "inbound.db")
    asyncio.run(MemoryStore(path).init())
    gs.ensure_schema(path)
    inbound_store.ensure_schema(path)
    return path


async def _capture(db_path, **overrides):
    kwargs = {
        "db_path": db_path,
        "source_id": "src-a",
        "event_id": "evt-1",
        "event_type": "generic.thing.happened",
        "payload": {"anything": "at all"},
        "verified_by": "test",
    }
    kwargs.update(overrides)
    return await run_inbound_through_runtime_contract(**kwargs)


def _rows(db_path, table="inbound_events"):
    with sqlite3.connect(db_path) as conn:
        try:
            return conn.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608
        except sqlite3.OperationalError:
            return []


@pytest.mark.asyncio
async def test_valid_event_is_captured_with_provenance(db_path):
    result = await _capture(db_path)

    assert result.captured is True
    assert result.duplicate is False
    assert result.outcome == inbound_store.OUTCOME_CAPTURED

    stored = result.stored
    # Every provenance question the design requires an answer to.
    assert stored.source_id == "src-a"  # where did it come from
    assert stored.verified_by == "test"  # what verified it
    assert stored.event_type == "generic.thing.happened"
    assert stored.received_at.endswith("Z")  # when we received it
    assert len(stored.payload_sha256) == 64  # what content was accepted
    assert stored.outcome == "captured"  # what happened to it


@pytest.mark.asyncio
async def test_capture_writes_a_reflection_but_not_memory(db_path):
    """Capture is a record that something arrived, never a belief about it."""
    await _capture(db_path)

    with sqlite3.connect(db_path) as conn:
        reflections = conn.execute(
            "SELECT content FROM reflections WHERE kind = 'action_reflection'",
        ).fetchall()
        memories = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        nudges = conn.execute("SELECT COUNT(*) FROM nudges").fetchone()[0]

    assert len(reflections) == 1
    assert "inbound" in reflections[0][0].lower()
    # Ingestion did not decide what the event means.
    assert memories == 0
    assert nudges == 0


@pytest.mark.asyncio
async def test_payload_is_stored_verbatim_and_never_interpreted(db_path):
    """Domain blindness: an unknown event_type and arbitrary payload capture fine."""
    payload = {"nested": {"a": [1, 2, 3]}, "unicode": "café", "unknown_field": None}
    result = await _capture(
        db_path,
        event_type="some.provider.we.have.never.heard.of",
        payload=payload,
    )
    assert result.captured is True

    with sqlite3.connect(db_path) as conn:
        raw = conn.execute(
            "SELECT payload_json FROM inbound_events WHERE event_id = 'evt-1'",
        ).fetchone()[0]
    assert json.loads(raw) == payload


@pytest.mark.asyncio
async def test_duplicate_delivery_does_not_create_a_second_event(db_path):
    first = await _capture(db_path)
    second = await _capture(db_path)
    # Same event_id, different JSON key order -- still one logical event.
    third = await _capture(db_path, payload={"anything": "at all", "extra": None})

    assert first.duplicate is False
    assert second.duplicate is True
    assert third.duplicate is True
    assert second.stored.row_id == first.stored.row_id
    assert len(_rows(db_path)) == 1

    # And no second Reflection either: a retry produced no new capture.
    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM reflections WHERE kind = 'action_reflection'",
        ).fetchone()[0]
    assert count == 1


@pytest.mark.asyncio
async def test_same_event_id_from_a_different_source_is_a_different_event(db_path):
    """Idempotency is scoped to (source, event) -- sources do not share an id space."""
    await _capture(db_path, source_id="src-a", event_id="1")
    second = await _capture(db_path, source_id="src-b", event_id="1")

    assert second.duplicate is False
    assert len(_rows(db_path)) == 2


@pytest.mark.asyncio
async def test_parking_brake_refuses_and_mutates_nothing(db_path):
    """ "Inspect, but do not mutate": a braked request writes NOTHING.

    Not even a "received and refused" row -- recording the refusal would
    itself be a governed-state mutation performed while the user has halted
    mutation, which is the side door the brake exists to close.
    """
    GovernanceStore(db_path).engage("global")

    before_events = _rows(db_path)
    before_reflections = _rows(db_path, "reflections")

    with pytest.raises(ParkingBrakeEngagedError):
        await _capture(db_path)

    assert _rows(db_path) == before_events
    assert _rows(db_path, "reflections") == before_reflections


@pytest.mark.asyncio
async def test_capture_resumes_after_the_brake_is_released(db_path):
    """The refusal is retryable: nothing was consumed by the braked attempt."""
    store = GovernanceStore(db_path)
    store.engage("global")
    with pytest.raises(ParkingBrakeEngagedError):
        await _capture(db_path)

    store.refresh()
    store.disengage()

    result = await _capture(db_path)
    assert result.captured is True
    assert result.duplicate is False


@pytest.mark.asyncio
async def test_unreadable_governance_fails_closed(db_path, monkeypatch):
    """An unreadable safety gate refuses; it never waves the event through."""
    import bartholomew.orchestrator.safety.governance_store as gs

    async def boom(*a, **k):
        raise sqlite3.OperationalError("governance unreadable")

    monkeypatch.setattr(gs, "engaged_state_fail_closed_off_loop", boom)

    with pytest.raises(sqlite3.OperationalError):
        await _capture(db_path)

    assert _rows(db_path) == []


@pytest.mark.asyncio
async def test_persistence_failure_is_reported_never_fabricated(db_path, monkeypatch):
    """A failed write raises `InboundPersistenceError`.

    The real `capture_event` is exercised -- only the database access under it
    fails -- so this proves the seam's own error translation, not a stub's.
    Nothing that failed to persist may return a successful-looking result.
    """
    from bartholomew.kernel import inbound_store as store_module

    def boom(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(store_module, "wal_db", boom)

    with pytest.raises(store_module.InboundPersistenceError) as excinfo:
        await _capture(db_path)

    # The message says plainly that nothing was persisted.
    assert "NOT persisted" in str(excinfo.value)


def test_recent_events_on_a_database_without_the_table(tmp_path):
    """An empty list is the truthful answer before anything has been captured."""
    path = str(tmp_path / "fresh.db")
    sqlite3.connect(path).close()
    assert inbound_store.recent_events(path) == []
