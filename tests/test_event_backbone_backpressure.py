"""Backlog limits and backpressure at the capture door (Package A).

A door that keeps accepting into a queue nothing is draining fills the disk
silently. The honest alternative is to refuse, retryably, and say why -- which
is what the inbound seam does once the backbone's non-terminal backlog reaches
its configured limit.

Two things are asserted here that are easy to get wrong in opposite
directions: the refusal must write nothing at all, and an *unreadable* backlog
must not become an outage.
"""

from __future__ import annotations

import pytest

from bartholomew.kernel import inbound_store
from bartholomew.kernel.event_processing import store
from bartholomew.kernel.event_processing.adapters import OBSERVATION_NOTE
from bartholomew.kernel.event_processing.config import DEFAULT_BACKLOG_MAX, resolve_settings
from bartholomew.kernel.runtime_contract import (
    EventBacklogFullError,
    run_inbound_through_runtime_contract,
)
from bartholomew.orchestrator.safety import governance_store as gs


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "backpressure.db")
    gs.ensure_schema(path)
    inbound_store.ensure_schema(path)
    store.ensure_schema(path)
    return path


async def capture(db_path, event_id, *, cfg=None):
    return await run_inbound_through_runtime_contract(
        db_path=db_path,
        source_id="src",
        event_id=event_id,
        event_type=OBSERVATION_NOTE,
        payload={"body": f"content {event_id}"},
        verified_by="test",
        runtime_cfg=cfg,
    )


def fill_backlog(db_path, count):
    for i in range(count):
        inbound_store.capture_event(
            db_path,
            source_id="filler",
            event_id=f"f{i}",
            event_type=OBSERVATION_NOTE,
            occurred_at=None,
            payload={"body": "backlog"},
            outcome=inbound_store.OUTCOME_CAPTURED,
            governance_reason=None,
            verified_by="test",
            runtime_id=None,
        )
    store.sweep_captured(db_path, limit=count + 10)


@pytest.mark.asyncio
async def test_capture_is_refused_once_the_backlog_reaches_its_limit(db):
    cfg = {"event_processing": {"backlog_max": 3}}
    fill_backlog(db, 3)

    with pytest.raises(EventBacklogFullError) as excinfo:
        await capture(db, "over-the-line", cfg=cfg)

    assert excinfo.value.backlog == 3
    assert excinfo.value.limit == 3
    assert "retry" in str(excinfo.value)
    # Nothing was written: the refusal is not a capture, and the sender's
    # retry must be able to deliver the same event later.
    assert inbound_store.get_event(db, "src", "over-the-line") is None


@pytest.mark.asyncio
async def test_capture_resumes_once_the_backlog_drains(db):
    cfg = {"event_processing": {"backlog_max": 2}}
    fill_backlog(db, 2)
    with pytest.raises(EventBacklogFullError):
        await capture(db, "blocked", cfg=cfg)

    # Settle one event, exactly as a processing pass would.
    claimed = store.claim_batch(db, runtime_id=None, limit=1, lease_seconds=60, max_attempts=3)[0]
    store.settle(
        db,
        claimed.row_id,
        claimed.claim_token,
        state=store.STATE_PROCESSED,
        reason="ok",
    )

    result = await capture(db, "blocked", cfg=cfg)
    assert result.captured is True


@pytest.mark.asyncio
async def test_terminal_events_do_not_count_against_the_backlog(db):
    cfg = {"event_processing": {"backlog_max": 2}}
    fill_backlog(db, 2)
    for _ in range(2):
        claimed = store.claim_batch(
            db,
            runtime_id=None,
            limit=1,
            lease_seconds=60,
            max_attempts=3,
        )[0]
        store.settle(
            db,
            claimed.row_id,
            claimed.claim_token,
            state=store.STATE_IRRELEVANT,
            reason="no_matching_live_objective",
        )
    assert store.pending_count(db) == 0
    assert (await capture(db, "fine", cfg=cfg)).captured is True


@pytest.mark.asyncio
async def test_an_unreadable_backlog_does_not_refuse_capture(db, monkeypatch):
    """A capacity gate, not a safety gate.

    A brake must refuse when it cannot be read. This must not: refusing every
    event because a count failed would turn a reporting problem into an
    outage, and the disk is what this protects, not the user.
    """
    from bartholomew.kernel.event_processing import store as store_module

    def explode(_db_path):
        raise store_module.EventProcessingStateError("the table is on fire")

    monkeypatch.setattr(store_module, "pending_count", explode)
    result = await capture(db, "still-captured")
    assert result.captured is True


@pytest.mark.asyncio
async def test_a_database_with_no_processing_table_reads_as_an_empty_backlog(tmp_path):
    """Truthful: a database that has never processed an event has none queued."""
    path = str(tmp_path / "bare.db")
    gs.ensure_schema(path)
    inbound_store.ensure_schema(path)
    assert store.pending_count(path) == 0
    assert (await capture(path, "first")).captured is True


def test_the_default_limit_is_generous_enough_not_to_bite_in_normal_use():
    assert resolve_settings().backlog_max == DEFAULT_BACKLOG_MAX
    assert DEFAULT_BACKLOG_MAX >= 1000
