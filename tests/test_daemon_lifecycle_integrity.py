"""
Phase B stage B5: KernelDaemon lifecycle state, startup unwind, write-fence/
clean-marker, and Startup Incident Log tests. See
docs/B5_STARTUP_SHUTDOWN_INTEGRITY.md.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from bartholomew.kernel.daemon import DaemonLifecycleState, KernelDaemon, UnsafeStartupError


@pytest.fixture
def config_files(tmp_path):
    (tmp_path / "kernel.yaml").write_text(
        'timezone: "Australia/Brisbane"\nloop_interval_seconds: 1\n',
    )
    (tmp_path / "persona.yaml").write_text('name: "Test Bartholomew"\n')
    (tmp_path / "policy.yaml").write_text("policies: []\n")
    (tmp_path / "drives.yaml").write_text("drives: []\n")
    return {
        "cfg_path": str(tmp_path / "kernel.yaml"),
        "db_path": str(tmp_path / "test.db"),
        "persona_path": str(tmp_path / "persona.yaml"),
        "policy_path": str(tmp_path / "policy.yaml"),
        "drives_path": str(tmp_path / "drives.yaml"),
    }


def _make_daemon(config_files):
    return KernelDaemon(**config_files)


def _incidents(db_path):
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT lifecycle_state_reached, exception_type, resources_started, "
            "resources_not_started, previous_shutdown_clean, integrity_checks_performed, "
            "recovery_actions_attempted, final_outcome FROM startup_incidents ORDER BY id",
        ).fetchall()
    finally:
        conn.close()
    return rows


# -- Lifecycle state transitions --------------------------------------------


async def test_fresh_daemon_starts_not_started(config_files):
    daemon = _make_daemon(config_files)
    assert daemon.lifecycle_state is DaemonLifecycleState.NOT_STARTED


async def test_successful_start_reaches_running(config_files):
    daemon = _make_daemon(config_files)
    await daemon.start()
    try:
        assert daemon.lifecycle_state is DaemonLifecycleState.RUNNING
    finally:
        await daemon.stop()


async def test_clean_stop_reaches_stopped(config_files):
    daemon = _make_daemon(config_files)
    await daemon.start()
    await daemon.stop()
    assert daemon.lifecycle_state is DaemonLifecycleState.STOPPED


async def test_failed_start_reaches_failed_not_not_started(config_files, monkeypatch):
    from bartholomew.kernel.scheduler import persistence

    def raising_ensure(db_path):
        raise RuntimeError("schema init boom")

    monkeypatch.setattr(persistence, "ensure_schema", raising_ensure)

    daemon = _make_daemon(config_files)
    with pytest.raises(RuntimeError, match="schema init boom"):
        await daemon.start()

    assert daemon.lifecycle_state is DaemonLifecycleState.FAILED


async def test_start_called_twice_raises_and_does_not_reinitialize(config_files):
    daemon = _make_daemon(config_files)
    await daemon.start()
    try:
        with pytest.raises(RuntimeError, match="can only be started once"):
            await daemon.start()
        assert daemon.lifecycle_state is DaemonLifecycleState.RUNNING
    finally:
        await daemon.stop()


async def test_start_after_failed_is_refused_not_reset(config_files, monkeypatch):
    """FAILED is terminal for this instance -- a retry must be a fresh
    KernelDaemon, not a mutation of this one back to NOT_STARTED."""
    from bartholomew.kernel.scheduler import persistence

    def raising_ensure(db_path):
        raise RuntimeError("schema init boom")

    monkeypatch.setattr(persistence, "ensure_schema", raising_ensure)

    daemon = _make_daemon(config_files)
    with pytest.raises(RuntimeError, match="schema init boom"):
        await daemon.start()
    assert daemon.lifecycle_state is DaemonLifecycleState.FAILED

    monkeypatch.undo()
    with pytest.raises(RuntimeError, match="can only be started once"):
        await daemon.start()
    assert daemon.lifecycle_state is DaemonLifecycleState.FAILED  # unchanged, not reset

    # A genuinely fresh instance against the same DB works fine.
    daemon2 = _make_daemon(config_files)
    await daemon2.start()
    await daemon2.stop()


async def test_stop_on_never_started_daemon_is_a_safe_noop(config_files):
    daemon = _make_daemon(config_files)
    await daemon.stop()  # must not raise
    assert daemon.lifecycle_state is DaemonLifecycleState.NOT_STARTED


async def test_stop_on_failed_daemon_is_a_safe_noop(config_files, monkeypatch):
    from bartholomew.kernel.scheduler import persistence

    monkeypatch.setattr(
        persistence,
        "ensure_schema",
        lambda db_path: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    daemon = _make_daemon(config_files)
    with pytest.raises(RuntimeError):
        await daemon.start()

    await daemon.stop()  # must not raise, must not re-attempt teardown
    assert daemon.lifecycle_state is DaemonLifecycleState.FAILED  # unchanged by stop()


# -- Startup unwind (producer tasks) -----------------------------------------


async def test_producer_tasks_are_cancelled_when_scheduler_task_creation_fails(
    config_files,
    monkeypatch,
):
    """Regression test for the B5-identified gap: a failure after the
    three producer tasks are created, but before/during scheduler_task
    creation, must cancel those tasks too -- not just close the two
    worker-thread resources."""

    def broken_run_scheduler(daemon_arg):
        raise RuntimeError("simulated scheduler_task creation failure")

    monkeypatch.setattr(
        "bartholomew.kernel.scheduler.loop.run_scheduler",
        broken_run_scheduler,
    )

    daemon = _make_daemon(config_files)
    with pytest.raises(RuntimeError, match="simulated scheduler_task creation failure"):
        await daemon.start()

    assert daemon.lifecycle_state is DaemonLifecycleState.FAILED
    assert daemon._tick_task is not None
    assert daemon._consumer_task is not None
    assert daemon._dream_task is not None
    assert daemon._scheduler_task is None

    for task in (daemon._tick_task, daemon._consumer_task, daemon._dream_task):
        assert task.cancelled() or task.done()


async def test_governance_store_construction_failure_still_unwinds_blocking_executor(
    config_files,
    monkeypatch,
):
    """Regression test for the B5-identified gap: governance_store's
    construction (which activates blocking_executor's worker thread) ran
    before the old protected region -- a failure there left
    blocking_executor un-unwound."""
    import bartholomew.orchestrator.safety.governance_store as governance_store_module

    def raising_ensure_schema(db_path):
        raise RuntimeError("governance schema boom")

    monkeypatch.setattr(governance_store_module, "ensure_schema", raising_ensure_schema)

    daemon = _make_daemon(config_files)
    with pytest.raises(RuntimeError, match="governance schema boom"):
        await daemon.start()

    assert daemon.lifecycle_state is DaemonLifecycleState.FAILED
    assert daemon.blocking_executor._closed is True
    assert daemon.scheduler_store._closed is True


# -- Write fence / clean marker ----------------------------------------------


async def test_clean_shutdown_marks_runtime_clean(config_files):
    daemon = _make_daemon(config_files)
    await daemon.start()
    await daemon.stop()

    marker = daemon.governance_store.runtime_marker()
    assert marker.clean is True


async def test_next_daemon_sees_previous_clean_shutdown(config_files):
    daemon = _make_daemon(config_files)
    await daemon.start()
    await daemon.stop()

    # Peek at the marker before a second start() overwrites it via its own
    # open_new_runtime() call -- this is what start() itself reads
    # internally before opening its own runtime.
    from bartholomew.orchestrator.safety.governance_store import GovernanceStore

    peek = GovernanceStore(config_files["db_path"])
    assert peek.previous_shutdown_was_clean() is True

    daemon2 = _make_daemon(config_files)
    await daemon2.start()
    try:
        assert daemon2.lifecycle_state.value == "running"
        # No incident should be recorded for a clean prior shutdown.
        assert _incidents(config_files["db_path"]) == []
    finally:
        await daemon2.stop()


async def test_write_fence_closed_after_stop_blocks_further_writes(config_files):
    daemon = _make_daemon(config_files)
    await daemon.start()
    store = daemon.governance_store
    await daemon.stop()

    from bartholomew.orchestrator.safety.governance_store import WriteFenceClosedError

    with pytest.raises(WriteFenceClosedError):
        store.engage("skills", reason="attempted after shutdown")


async def test_first_ever_run_has_no_previous_shutdown_opinion_and_no_incident(config_files):
    """A brand-new database has no prior runtime to distrust -- start()
    must not treat "no marker" as "unclean"."""
    daemon = _make_daemon(config_files)
    await daemon.start()
    try:
        assert daemon.lifecycle_state is DaemonLifecycleState.RUNNING
    finally:
        await daemon.stop()
    assert _incidents(config_files["db_path"]) == []


# -- Unclean-shutdown detection, integrity check, recovery -------------------


def _seed_unclean_runtime_marker(db_path: str) -> None:
    """Simulate a crashed prior process: open_new_runtime() ran (so a
    brake_runtime row with clean=0 exists) but nothing ever called
    mark_clean_shutdown()."""
    from bartholomew.orchestrator.safety.governance_store import GovernanceStore

    prior = GovernanceStore(db_path)
    prior.open_new_runtime()
    # No mark_clean_shutdown() call -- this is the crash simulation.


async def test_unclean_shutdown_triggers_integrity_check_and_recovery(config_files):
    _seed_unclean_runtime_marker(config_files["db_path"])

    daemon = _make_daemon(config_files)
    await daemon.start()
    try:
        assert daemon.lifecycle_state is DaemonLifecycleState.RUNNING
    finally:
        await daemon.stop()

    rows = _incidents(config_files["db_path"])
    assert len(rows) == 1
    (
        lifecycle_state,
        exc_type,
        started,
        not_started,
        prev_clean,
        checks,
        recovery,
        outcome,
    ) = rows[0]
    assert lifecycle_state == "running"
    assert exc_type is None
    assert prev_clean == 0
    assert any("quick_check" in c for c in json.loads(checks))
    assert "wal_checkpoint_truncate" in json.loads(recovery)
    assert outcome == "started_after_recovery"
    assert "mem" in json.loads(started)


async def test_failed_integrity_check_aborts_startup_as_unsafe(config_files, monkeypatch):
    _seed_unclean_runtime_marker(config_files["db_path"])

    import bartholomew.orchestrator.safety.governance_store as governance_store_module

    monkeypatch.setattr(
        governance_store_module,
        "run_quick_integrity_check",
        lambda db_path: (False, "row 3 missing from index idx_foo"),
    )

    daemon = _make_daemon(config_files)
    with pytest.raises(UnsafeStartupError, match="quick_check reported"):
        await daemon.start()

    assert daemon.lifecycle_state is DaemonLifecycleState.FAILED

    rows = _incidents(config_files["db_path"])
    assert len(rows) == 1
    (
        lifecycle_state,
        exc_type,
        started,
        not_started,
        prev_clean,
        checks,
        recovery,
        outcome,
    ) = rows[0]
    assert lifecycle_state == "failed"
    assert exc_type == "UnsafeStartupError"
    assert prev_clean == 0
    assert json.loads(recovery) == []  # repair never attempted -- check failed first
    assert outcome == "failed"
    # Confirms exactly how far startup got before aborting.
    assert set(json.loads(started)) == {
        "process_lock",
        "mem",
        "governance_store",
        "awaiting_response_store",
    }
    assert "scheduler_schema" in json.loads(not_started)


async def test_failed_startup_records_incident_with_accurate_progress(config_files, monkeypatch):
    from bartholomew.kernel.scheduler import persistence

    monkeypatch.setattr(
        persistence,
        "ensure_schema",
        lambda db_path: (_ for _ in ()).throw(RuntimeError("scheduler schema boom")),
    )

    daemon = _make_daemon(config_files)
    with pytest.raises(RuntimeError, match="scheduler schema boom"):
        await daemon.start()

    rows = _incidents(config_files["db_path"])
    assert len(rows) == 1
    (
        lifecycle_state,
        exc_type,
        started,
        not_started,
        prev_clean,
        checks,
        recovery,
        outcome,
    ) = rows[0]
    assert lifecycle_state == "failed"
    assert exc_type == "RuntimeError"
    assert prev_clean is None  # first-ever run, nothing to have an opinion about
    assert set(json.loads(started)) == {
        "process_lock",
        "mem",
        "governance_store",
        "awaiting_response_store",
    }
    assert "scheduler_schema" in json.loads(not_started)
    assert "skills" in json.loads(not_started)
    assert outcome == "failed"


# -- Process lock (Phase B stage B6) -----------------------------------------


async def test_second_daemon_cannot_start_while_first_is_running(config_files):
    """The process lock (Phase B stage B6) is the daemon's own
    single-instance guard: a second KernelDaemon against the same
    db_path must fail fast, not silently race the first."""
    from bartholomew.kernel.process_lock import ProcessLockHeldError

    daemon1 = _make_daemon(config_files)
    await daemon1.start()
    try:
        daemon2 = _make_daemon(config_files)
        with pytest.raises(ProcessLockHeldError):
            await daemon2.start()
        assert daemon2.lifecycle_state is DaemonLifecycleState.FAILED
    finally:
        await daemon1.stop()


async def test_process_lock_released_after_clean_stop_allows_next_start(config_files):
    daemon1 = _make_daemon(config_files)
    await daemon1.start()
    await daemon1.stop()

    daemon2 = _make_daemon(config_files)
    await daemon2.start()  # must not raise -- lock was released by stop()
    try:
        assert daemon2.lifecycle_state is DaemonLifecycleState.RUNNING
    finally:
        await daemon2.stop()


async def test_process_lock_released_after_failed_start_allows_next_start(
    config_files,
    monkeypatch,
):
    from bartholomew.kernel.scheduler import persistence

    monkeypatch.setattr(
        persistence,
        "ensure_schema",
        lambda db_path: (_ for _ in ()).throw(RuntimeError("schema init boom")),
    )
    daemon1 = _make_daemon(config_files)
    with pytest.raises(RuntimeError, match="schema init boom"):
        await daemon1.start()
    assert daemon1._process_lock.held is False

    monkeypatch.undo()
    daemon2 = _make_daemon(config_files)
    await daemon2.start()  # must not raise -- daemon1's failed start released the lock
    try:
        assert daemon2.lifecycle_state is DaemonLifecycleState.RUNNING
    finally:
        await daemon2.stop()
