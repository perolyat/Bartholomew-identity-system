"""Unit-level evidence integrity: the properties that must hold before a run.

Fast, no subprocesses, no server. The integration scenario in
`tests/integration/test_unattended_run_evidence.py` proves the same
properties against real processes; these pin the individual rules so that
when the scenario fails, it is obvious which rule broke.

The rules being pinned are all forms of one invariant: *inability to
determine something is never converted into success*.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone

import pytest

from bartholomew.runtime import evidence
from bartholomew.runtime.evidence import END_CLEAN, END_LOST, EvidenceStore
from bartholomew.runtime.evidence_report import build_report, digest, freeze


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "evidence.db")


@pytest.fixture(autouse=True)
def _clean_module_state():
    evidence._reset_for_tests()
    yield
    evidence._reset_for_tests()


# ---------------------------------------------------------------------------
# Run identity
# ---------------------------------------------------------------------------


def test_a_process_outside_a_run_records_nothing(db, monkeypatch):
    """The default posture. No env var, no writer, no new table."""
    monkeypatch.delenv(evidence.RUN_ID_ENV, raising=False)
    assert evidence.active_run_id() is None
    assert evidence.record_process_start(db, runtime_id="r1") is None
    evidence.record_process_stop(db)

    conn = sqlite3.connect(db)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert "unattended_run_incarnations" not in tables


def test_an_unusable_run_id_is_refused_rather_than_sanitised(monkeypatch):
    """A run id that quietly became something else would misattribute evidence."""
    monkeypatch.setenv(evidence.RUN_ID_ENV, "../../etc/passwd")
    with pytest.raises(ValueError):
        evidence.active_run_id()


def test_one_run_id_spans_many_incarnations(db):
    store = EvidenceStore(db)
    first = store.open_incarnation("run-x", runtime_id="rt-1", pid=101)
    store.close_incarnation(first, end_kind=END_CLEAN)
    second = store.open_incarnation("run-x", runtime_id="rt-2", pid=102)
    store.close_incarnation(second, end_kind=END_CLEAN)

    incs = store.incarnations("run-x")
    assert [i.runtime_id for i in incs] == ["rt-1", "rt-2"]
    assert {i.run_id for i in incs} == {"run-x"}


# ---------------------------------------------------------------------------
# Truthfulness of endings
# ---------------------------------------------------------------------------


def test_a_process_that_never_recorded_its_end_is_lost_not_clean(db):
    """The whole point: a killed process must not read as a clean stop."""
    store = EvidenceStore(db)
    store.open_incarnation("run-x", runtime_id="rt-1", pid=101)  # never closed
    store.open_incarnation("run-x", runtime_id="rt-2", pid=102)

    first, second = store.incarnations("run-x")
    assert first.end_kind == END_LOST
    assert first.inferred is True
    assert "not the moment the process actually died" in first.end_detail
    assert second.open is True


def test_an_inferred_lost_verdict_is_not_overwritten_by_a_late_clean_claim(db):
    """A row already written off stays written off.

    Otherwise a process that came back after being declared lost could erase
    the evidence that it had been unaccounted for.
    """
    store = EvidenceStore(db)
    first = store.open_incarnation("run-x", runtime_id="rt-1")
    store.open_incarnation("run-x", runtime_id="rt-2")

    store.close_incarnation(first, end_kind=END_CLEAN)

    assert store.incarnations("run-x")[0].end_kind == END_LOST


def test_closing_records_the_reason_for_a_failed_ending(db):
    store = EvidenceStore(db)
    inc = store.open_incarnation("run-x", runtime_id="rt-1")
    store.close_incarnation(inc, end_kind=evidence.END_FAILED, detail="scheduler died")

    only = store.incarnations("run-x")[0]
    assert only.end_kind == evidence.END_FAILED
    assert only.end_detail == "scheduler died"
    assert only.inferred is False


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def test_a_missing_source_is_unknown_and_carries_no_count(db):
    """`available: false` with no `count`, so "unknown" cannot be read as zero."""
    store = EvidenceStore(db)
    inc = store.open_incarnation("run-x", runtime_id="rt-1")
    store.close_incarnation(inc)

    record = build_report(db, "run-x")
    ticks = record["sources"]["scheduler_ticks"]
    assert ticks["available"] is False
    assert "count" not in ticks
    assert record["summary"]["scheduler_tick_count"] is None
    assert record["summary"]["complete"] is False
    assert "scheduler_ticks" in record["summary"]["unreadable_sources"]


def test_a_run_with_no_ledger_at_all_is_unknown_not_empty(db):
    EvidenceStore(db).ensure_schema()
    summary = build_report(db, "never-happened")["summary"]
    assert summary["complete"] is None
    assert "cannot distinguish" in summary["verdict"]


def test_a_lost_incarnation_makes_the_report_incomplete(db):
    store = EvidenceStore(db)
    store.open_incarnation("run-x", runtime_id="rt-1")  # lost
    second = store.open_incarnation("run-x", runtime_id="rt-2")
    store.close_incarnation(second)
    _make_all_sources_readable(db)

    summary = build_report(db, "run-x")["summary"]
    assert summary["complete"] is False
    assert summary["lost_endings"] == 1
    assert summary["clean_endings"] == 1
    assert "closed as lost" in summary["verdict"]


def test_a_fully_recorded_run_reports_complete(db):
    store = EvidenceStore(db)
    for rt in ("rt-1", "rt-2"):
        store.close_incarnation(store.open_incarnation("run-x", runtime_id=rt))
    _make_all_sources_readable(db)

    summary = build_report(db, "run-x")["summary"]
    assert summary["complete"] is True
    assert summary["unreadable_sources"] == []


def test_ticks_are_attributed_to_the_incarnation_that_was_running(db):
    """Pre-restart and post-restart activity must not be confused."""
    store = EvidenceStore(db)
    _make_all_sources_readable(db)

    first = store.open_incarnation("run-x", runtime_id="rt-1")
    bounds = _bounds(db)
    _insert_tick(db, "drive.a", bounds[first][0])
    store.close_incarnation(first)

    # A real second between the two, because attribution is at whole-second
    # resolution -- the resolution the `ticks` table itself stores.
    time.sleep(1.1)
    second = store.open_incarnation("run-x", runtime_id="rt-2")
    _insert_tick(db, "drive.b", _bounds(db)[second][0] + 1)
    store.close_incarnation(second)

    attributed = build_report(db, "run-x")["sources"]["scheduler_ticks"]["attributed"]
    assert attributed["per_incarnation"] == {str(first): 1, str(second): 1}
    assert attributed["unattributed"] == 0


def test_a_tick_from_before_the_run_is_unattributed_not_reassigned(db):
    store = EvidenceStore(db)
    _make_all_sources_readable(db)
    inc = store.open_incarnation("run-x", runtime_id="rt-1")
    _insert_tick(db, "drive.old", _bounds(db)[inc][0] - 3600)
    store.close_incarnation(inc)

    attributed = build_report(db, "run-x")["sources"]["scheduler_ticks"]["attributed"]
    assert attributed["unattributed"] == 1
    assert attributed["per_incarnation"] == {str(inc): 0}


def test_inbound_events_carry_the_tenant_runtime_id_under_its_own_name(db):
    """The column is the platform's per-user binding, not the incarnation id.

    Conflating the two would attribute events to the wrong process while
    looking precise about it, so the report renames it -- and attributes
    inbound events by their received time, like every other clock-attributed
    source.
    """
    store = EvidenceStore(db)
    _make_all_sources_readable(db)
    inc = store.open_incarnation("run-x", runtime_id="rt-1")
    started = _bounds(db)[inc][0]
    _insert_inbound(db, "e-during", runtime_id=None, received_ts=started)
    _insert_inbound(db, "e-before", runtime_id="tenant-7", received_ts=started - 3600)
    store.close_incarnation(inc)

    inbound = build_report(db, "run-x")["sources"]["inbound_events"]
    assert "received_at" in inbound["attribution"]
    by_event = {i["event_id"]: i for i in inbound["items"]}
    assert "runtime_id" not in by_event["e-before"]
    assert by_event["e-before"]["tenant_runtime_id"] == "tenant-7"
    assert by_event["e-during"]["tenant_runtime_id"] is None
    # Nothing leaks the internal epoch column into the frozen record.
    assert "_received_epoch" not in by_event["e-during"]

    assert inbound["attributed"]["per_incarnation"] == {str(inc): 1}
    assert inbound["attributed"]["unattributed"] == 1


def test_governed_skill_actions_are_attributed_by_their_text_timestamp(db):
    store = EvidenceStore(db)
    _make_all_sources_readable(db)
    inc = store.open_incarnation("run-x", runtime_id="rt-1")
    started = _bounds(db)[inc][0]
    _insert_skill_action(db, "sent", "ok", started + 1)
    _insert_skill_action(db, "earlier", "ok", started - 500)
    store.close_incarnation(inc)

    attributed = build_report(db, "run-x")["sources"]["governed_skill_actions"]["attributed"]
    assert attributed["per_incarnation"] == {str(inc): 1}
    assert attributed["unattributed"] == 1


def test_freezing_the_same_database_twice_yields_the_same_digest(db):
    store = EvidenceStore(db)
    store.close_incarnation(store.open_incarnation("run-x", runtime_id="rt-1"))
    _make_all_sources_readable(db)

    first = freeze(db, "run-x")
    second = freeze(db, "run-x")
    assert first["digest"] == second["digest"]
    assert first["record"] == second["record"]


def test_new_evidence_changes_the_digest(db):
    store = EvidenceStore(db)
    store.close_incarnation(store.open_incarnation("run-x", runtime_id="rt-1"))
    _make_all_sources_readable(db)
    before = digest(build_report(db, "run-x"))

    _insert_tick(db, "drive.a", _bounds(db)[store.incarnations("run-x")[0].id][0])

    assert digest(build_report(db, "run-x")) != before


def test_a_frozen_report_is_written_as_readable_json(db, tmp_path):
    from bartholomew.runtime.evidence_report import write_frozen_report

    store = EvidenceStore(db)
    store.close_incarnation(store.open_incarnation("run-x", runtime_id="rt-1"))
    out = tmp_path / "report.json"
    envelope = write_frozen_report(db, "run-x", str(out))

    on_disk = json.loads(out.read_text())
    assert on_disk["digest"] == envelope["digest"]
    assert on_disk["record"]["run_id"] == "run-x"


def test_observations_are_append_only_and_ordered(db):
    store = EvidenceStore(db)
    for i in range(3):
        store.record_observation("run-x", kind="health_sample", source="api", payload={"n": i})

    got = store.observations("run-x")
    assert [o["payload"]["n"] for o in got] == [0, 1, 2]
    assert {o["source"] for o in got} == {"api"}


# ---------------------------------------------------------------------------
# helpers -- minimal stand-ins for tables the runtime creates
# ---------------------------------------------------------------------------


def _make_all_sources_readable(db: str) -> None:
    """Create the runtime's evidence tables, empty.

    Empty-and-readable is a genuinely different state from missing, and every
    test that is about *counts* needs the former so it is not accidentally
    also testing the latter.
    """
    conn = sqlite3.connect(db)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ticks (
                id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                started_ts INTEGER NOT NULL, finished_ts INTEGER,
                success INTEGER NOT NULL DEFAULT 0, idempotency_key TEXT UNIQUE,
                result_meta TEXT);
            CREATE TABLE IF NOT EXISTS governance_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL,
                action TEXT NOT NULL, scopes TEXT NOT NULL, reason TEXT,
                revision INTEGER NOT NULL, actor TEXT);
            CREATE TABLE IF NOT EXISTS skill_action_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, skill_id TEXT NOT NULL,
                action TEXT NOT NULL, params_json TEXT, status TEXT NOT NULL,
                result_message TEXT, result_error TEXT, timestamp TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS startup_incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL,
                runtime_id TEXT, lifecycle_state_reached TEXT NOT NULL,
                exception_type TEXT, exception_message TEXT, traceback TEXT,
                resources_started TEXT NOT NULL, resources_not_started TEXT NOT NULL,
                previous_shutdown_clean INTEGER, integrity_checks_performed TEXT NOT NULL,
                recovery_actions_attempted TEXT NOT NULL, final_outcome TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS inbound_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL,
                event_id TEXT NOT NULL, event_type TEXT NOT NULL, occurred_at TEXT,
                received_at TEXT NOT NULL, payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL, outcome TEXT NOT NULL,
                governance_reason TEXT, verified_by TEXT NOT NULL, runtime_id TEXT,
                UNIQUE (source_id, event_id));
            """,
        )
        conn.commit()
    finally:
        conn.close()


def _bounds(db: str) -> dict[int, tuple[int, int | None]]:
    conn = sqlite3.connect(db)
    try:
        return {
            int(r[0]): (int(r[1]), None if r[2] is None else int(r[2]))
            for r in conn.execute(
                "SELECT id, started_ts, ended_ts FROM unattended_run_incarnations",
            )
        }
    finally:
        conn.close()


def _insert_tick(db: str, task_id: str, ts: int) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO ticks (task_id, started_ts, finished_ts, success) VALUES (?, ?, ?, 1)",
            (task_id, ts, ts),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_inbound(db: str, event_id: str, *, runtime_id: str | None, received_ts: int) -> None:
    received_at = (
        datetime.fromtimestamp(received_ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    )
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO inbound_events (source_id, event_id, event_type, received_at, "
            "payload_json, payload_sha256, outcome, verified_by, runtime_id) "
            "VALUES ('src', ?, 'probe', ?, '{}', 'x', 'captured', 'test', ?)",
            (event_id, received_at, runtime_id),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_skill_action(db: str, action: str, status: str, ts: int) -> None:
    stamp = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO skill_action_audit (skill_id, action, status, timestamp) "
            "VALUES ('skill', ?, ?, ?)",
            (action, status, stamp),
        )
        conn.commit()
    finally:
        conn.close()
