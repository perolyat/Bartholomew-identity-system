"""
Tests for bartholomew.kernel.awaiting_response_store (Stage 1, S1.4). Isolated
tests against the new awaiting_response/awaiting_response_audit schema -- see
docs/S1_4_AWAITING_RESPONSE_DESIGN.md for the design this implements.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from bartholomew.kernel.awaiting_response_store import (
    AwaitingResponseNotFoundError,
    AwaitingResponseStore,
    InvalidTransitionError,
)


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "awaiting_response.db")


def test_fresh_schema_is_empty(db_path):
    store = AwaitingResponseStore(db_path)
    assert store.list() == []


def test_open_creates_entry_with_default_due_at(db_path):
    store = AwaitingResponseStore(db_path)
    entry = store.open(subject="Reply about the trip", origin_surface="chat", actor="chat")

    assert entry.status == "open"
    assert entry.subject == "Reply about the trip"
    assert entry.origin_surface == "chat"
    assert entry.due_at is not None
    assert entry.reminder_count == 0
    assert entry.resolution is None


def test_open_writes_an_audit_row(db_path):
    store = AwaitingResponseStore(db_path)
    entry = store.open(subject="x", origin_surface="chat")

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT transition FROM awaiting_response_audit WHERE entry_id = ?",
            (entry.id,),
        ).fetchall()
    finally:
        conn.close()
    assert [r[0] for r in rows] == ["opened"]


def test_remind_increments_reminder_count_and_sets_status(db_path):
    store = AwaitingResponseStore(db_path)
    entry = store.open(subject="x", origin_surface="chat")

    reminded = store.remind(entry.id, actor="scheduler")
    assert reminded.status == "reminded"
    assert reminded.reminder_count == 1
    assert reminded.last_reminded_at is not None

    reminded_again = store.remind(entry.id, actor="scheduler")
    assert reminded_again.reminder_count == 2


def test_escalate_sets_status_and_timestamp(db_path):
    store = AwaitingResponseStore(db_path)
    entry = store.open(subject="x", origin_surface="chat")
    store.remind(entry.id)

    escalated = store.escalate(entry.id, actor="scheduler")
    assert escalated.status == "escalated"
    assert escalated.escalated_at is not None


def test_resolve_is_terminal(db_path):
    store = AwaitingResponseStore(db_path)
    entry = store.open(subject="x", origin_surface="chat")

    resolved = store.resolve(entry.id, resolution="user_dismissed", actor="user")
    assert resolved.status == "resolved"
    assert resolved.resolution == "user_dismissed"
    assert resolved.resolved_at is not None


def test_transition_after_resolve_raises(db_path):
    store = AwaitingResponseStore(db_path)
    entry = store.open(subject="x", origin_surface="chat")
    store.resolve(entry.id, resolution="manual")

    with pytest.raises(InvalidTransitionError):
        store.remind(entry.id)
    with pytest.raises(InvalidTransitionError):
        store.escalate(entry.id)
    with pytest.raises(InvalidTransitionError):
        store.resolve(entry.id, resolution="manual")


def test_transition_on_unknown_entry_raises(db_path):
    store = AwaitingResponseStore(db_path)
    with pytest.raises(AwaitingResponseNotFoundError):
        store.remind(999)
    with pytest.raises(AwaitingResponseNotFoundError):
        store.resolve(999, resolution="manual")


def test_get_returns_none_for_unknown_entry(db_path):
    store = AwaitingResponseStore(db_path)
    assert store.get(999) is None


def test_list_filters_by_status(db_path):
    store = AwaitingResponseStore(db_path)
    a = store.open(subject="a", origin_surface="chat")
    b = store.open(subject="b", origin_surface="chat")
    store.resolve(b.id, resolution="manual")

    open_entries = store.list(status="open")
    resolved_entries = store.list(status="resolved")
    assert [e.id for e in open_entries] == [a.id]
    assert [e.id for e in resolved_entries] == [b.id]


def test_list_open_by_origin_excludes_resolved(db_path):
    store = AwaitingResponseStore(db_path)
    a = store.open(subject="a", origin_surface="chat")
    b = store.open(subject="b", origin_surface="chat")
    store.resolve(b.id, resolution="manual")
    store.open(subject="c", origin_surface="scheduler")

    chat_open = store.list_open_by_origin("chat")
    assert [e.id for e in chat_open] == [a.id]


def test_list_due_for_transition_only_includes_overdue_entries(db_path):
    store = AwaitingResponseStore(db_path)
    now_ts = int(time.time())
    past = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ts - 3600))
    future = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ts + 3600))

    overdue = store.open(subject="overdue", origin_surface="chat", due_at=past)
    store.open(subject="not yet due", origin_surface="chat", due_at=future)

    due = store.list_due_for_transition(now_ts)
    assert [e.id for e in due] == [overdue.id]


def test_list_due_for_transition_respects_reminder_interval(db_path):
    """An entry already reminded recently isn't due again immediately --
    REMINDER_INTERVAL_S paces repeated reminders."""
    store = AwaitingResponseStore(db_path)
    now_ts = int(time.time())
    past = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ts - 3600))
    entry = store.open(subject="x", origin_surface="chat", due_at=past)
    store.remind(entry.id)

    due = store.list_due_for_transition(now_ts)
    assert due == []


def test_next_transition_for_escalates_after_reminder_threshold(db_path):
    from bartholomew.kernel.awaiting_response_store import ESCALATE_AFTER_REMINDERS

    store = AwaitingResponseStore(db_path)
    entry = store.open(subject="x", origin_surface="chat")
    for _ in range(ESCALATE_AFTER_REMINDERS):
        entry = store.remind(entry.id)

    now_ts = int(time.time())
    assert store.next_transition_for(entry, now_ts) == "escalate"


def test_next_transition_for_reminds_below_threshold(db_path):
    store = AwaitingResponseStore(db_path)
    entry = store.open(subject="x", origin_surface="chat")

    now_ts = int(time.time())
    assert store.next_transition_for(entry, now_ts) == "remind"


def test_list_audit_returns_entries_newest_first(db_path):
    store = AwaitingResponseStore(db_path)
    entry = store.open(subject="x", origin_surface="chat")
    store.remind(entry.id)
    store.resolve(entry.id, resolution="manual")

    audit = store.list_audit(entry.id)
    assert [row["transition"] for row in audit] == ["resolved", "reminded", "opened"]
