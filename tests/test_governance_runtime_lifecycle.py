"""
Tests for Phase B stage B5's additions to
bartholomew.orchestrator.safety.governance_store: the write-fence/
clean-marker (brake_runtime) and the Startup Incident Log
(startup_incidents). Isolated from KernelDaemon -- daemon-level
orchestration (when to call these, what counts as "obviously
recoverable") is tested separately in tests/test_daemon_lifecycle.py.
"""

import json
import sqlite3

import pytest

from bartholomew.orchestrator.safety.governance_store import (
    GovernanceStore,
    WriteFenceClosedError,
    run_quick_integrity_check,
)


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "governance.db")


# -- brake_runtime / write fence --------------------------------------------


def test_no_runtime_marker_before_open_new_runtime(db_path):
    store = GovernanceStore(db_path)
    assert store.runtime_marker() is None
    assert store.previous_shutdown_was_clean() is None


def test_open_new_runtime_sets_unclean_default_and_open_fence(db_path):
    store = GovernanceStore(db_path)
    runtime_id = store.open_new_runtime()

    marker = store.runtime_marker()
    assert marker is not None
    assert marker.runtime_id == runtime_id
    assert marker.clean is False
    assert marker.write_fence_open is True


def test_open_new_runtime_generates_distinct_ids(db_path):
    store = GovernanceStore(db_path)
    first = store.open_new_runtime()
    second = store.open_new_runtime()
    assert first != second


def test_mark_clean_shutdown_then_next_runtime_sees_it(db_path):
    store = GovernanceStore(db_path)
    store.open_new_runtime()
    store.mark_clean_shutdown()

    assert store.runtime_marker().clean is True

    # A "next process" constructing its own store against the same file
    # sees the previous runtime's clean marker before overwriting it.
    next_store = GovernanceStore(db_path)
    assert next_store.previous_shutdown_was_clean() is True

    next_store.open_new_runtime()
    assert next_store.previous_shutdown_was_clean() is False  # reset by open_new_runtime


def test_unclean_shutdown_is_the_honest_default(db_path):
    """open_new_runtime() marks clean=0 immediately -- if the process
    dies before mark_clean_shutdown() ever runs, the next runtime finds
    clean=0, the honest (not optimistic) default."""
    store = GovernanceStore(db_path)
    store.open_new_runtime()
    # Simulate a crash: no mark_clean_shutdown() call, no close_write_fence().

    next_store = GovernanceStore(db_path)
    assert next_store.previous_shutdown_was_clean() is False


def test_close_write_fence_blocks_engage_and_disengage(db_path):
    store = GovernanceStore(db_path)
    store.open_new_runtime()
    store.close_write_fence()

    with pytest.raises(WriteFenceClosedError):
        store.engage("skills", reason="should be refused")

    with pytest.raises(WriteFenceClosedError):
        store.disengage(reason="should also be refused")


def test_close_write_fence_does_not_block_reads(db_path):
    store = GovernanceStore(db_path)
    store.engage("skills", reason="before fence closes")
    store.open_new_runtime()
    store.close_write_fence()

    # Reads (is_blocked/state/refresh) are unaffected by the fence -- only
    # writes are gated.
    assert store.is_blocked("skills") is True
    assert store.refresh().engaged is True


def test_missing_brake_runtime_row_defaults_to_fence_open(db_path):
    """A GovernanceStore that never calls open_new_runtime() (e.g. a
    standalone test instance, or the B4 bridge's temporary fallback) must
    not be silently gated by a fence nobody ever opened for it."""
    store = GovernanceStore(db_path)
    assert store.runtime_marker() is None
    # No WriteFenceClosedError -- missing row means permissive default.
    state = store.engage("skills", reason="no runtime marker exists yet")
    assert state.engaged is True


def test_close_write_fence_is_idempotent(db_path):
    store = GovernanceStore(db_path)
    store.open_new_runtime()
    store.close_write_fence()
    store.close_write_fence()  # must not raise
    assert store.runtime_marker().write_fence_open is False


def test_reopening_runtime_reopens_the_fence(db_path):
    """A fresh open_new_runtime() call (new process, new attempt) reopens
    a fence a prior runtime closed -- the fence is per-runtime, not
    permanently latched."""
    store = GovernanceStore(db_path)
    store.open_new_runtime()
    store.close_write_fence()
    assert store.runtime_marker().write_fence_open is False

    store.open_new_runtime()
    assert store.runtime_marker().write_fence_open is True
    # Writes work again.
    state = store.engage("skills", reason="fence reopened")
    assert state.engaged is True


# -- Startup Incident Log ----------------------------------------------------


def test_record_startup_incident_persists_all_fields(db_path):
    store = GovernanceStore(db_path)
    runtime_id = store.open_new_runtime()

    store.record_startup_incident(
        runtime_id=runtime_id,
        lifecycle_state_reached="FAILED",
        exception_type="RuntimeError",
        exception_message="scheduler schema creation failed",
        traceback="Traceback (most recent call last):\n  ...",
        resources_started=["mem", "governance_store"],
        resources_not_started=["scheduler_schema", "experience_kernel", "skills"],
        previous_shutdown_clean=False,
        integrity_checks_performed=["quick_check"],
        recovery_actions_attempted=["wal_checkpoint_truncate"],
        final_outcome="failed",
    )

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT runtime_id, lifecycle_state_reached, exception_type, exception_message, "
            "traceback, resources_started, resources_not_started, previous_shutdown_clean, "
            "integrity_checks_performed, recovery_actions_attempted, final_outcome "
            "FROM startup_incidents",
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    (
        db_runtime_id,
        lifecycle_state,
        exc_type,
        exc_message,
        tb,
        started,
        not_started,
        prev_clean,
        checks,
        recovery,
        outcome,
    ) = row
    assert db_runtime_id == runtime_id
    assert lifecycle_state == "FAILED"
    assert exc_type == "RuntimeError"
    assert exc_message == "scheduler schema creation failed"
    assert "Traceback" in tb
    assert json.loads(started) == ["mem", "governance_store"]
    assert json.loads(not_started) == ["scheduler_schema", "experience_kernel", "skills"]
    assert prev_clean == 0
    assert json.loads(checks) == ["quick_check"]
    assert json.loads(recovery) == ["wal_checkpoint_truncate"]
    assert outcome == "failed"


def test_record_startup_incident_allows_null_previous_shutdown_clean(db_path):
    """previous_shutdown_clean is nullable -- the first-ever runtime has
    no prior shutdown to have an opinion about."""
    store = GovernanceStore(db_path)

    store.record_startup_incident(
        runtime_id=None,
        lifecycle_state_reached="RUNNING",
        resources_started=["mem"],
        resources_not_started=[],
        previous_shutdown_clean=None,
        integrity_checks_performed=[],
        recovery_actions_attempted=[],
        final_outcome="started_clean",
    )

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT previous_shutdown_clean FROM startup_incidents").fetchone()
    finally:
        conn.close()
    assert row[0] is None


def test_startup_incidents_are_append_only_across_multiple_records(db_path):
    store = GovernanceStore(db_path)
    for i in range(3):
        store.record_startup_incident(
            runtime_id=f"runtime-{i}",
            lifecycle_state_reached="FAILED",
            resources_started=[],
            resources_not_started=["mem"],
            previous_shutdown_clean=None,
            integrity_checks_performed=[],
            recovery_actions_attempted=[],
            final_outcome="failed",
        )

    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM startup_incidents").fetchone()[0]
        ids = [
            row[0] for row in conn.execute("SELECT runtime_id FROM startup_incidents ORDER BY id")
        ]
    finally:
        conn.close()
    assert count == 3
    assert ids == ["runtime-0", "runtime-1", "runtime-2"]


# -- Integrity check ----------------------------------------------------------


def test_quick_integrity_check_passes_on_healthy_db(db_path):
    GovernanceStore(db_path)  # ensures schema exists
    ok, result = run_quick_integrity_check(db_path)
    assert ok is True
    assert result == "ok"


def test_quick_integrity_check_on_missing_file_creates_empty_db(db_path):
    """sqlite3.connect() on a nonexistent path creates an empty (valid)
    database file rather than raising -- quick_check on it should still
    report "ok", not be mistaken for corruption."""
    ok, result = run_quick_integrity_check(db_path)
    assert ok is True
