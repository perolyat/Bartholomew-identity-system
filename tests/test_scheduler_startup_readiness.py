"""
S5.0 (closes issue #24): deterministic scheduler-schema readiness at startup.

`KernelDaemon.start()` now ensures the scheduler schema (`scheduled_tasks`,
`ticks`, and the nudges/reflections integer-timestamp columns) synchronously,
as early as practical -- immediately after `MemoryStore.init()` and before any
side-effectful init or the scheduler task -- and fails closed (cleaning up the
scheduler store) if that init raises. Previously the schema was created by the
fire-and-forget `run_scheduler()` task, so the API bridge could serve a request
during a window where `ticks`/`scheduled_tasks` did not exist yet ("no such
table" -> 500).

These tests are deterministic: they use ordered call records / asyncio barriers
and direct schema assertions, never a reliance on winning or losing the
original race, so they behave identically on Python 3.10 and 3.11 (the exact
version-sensitivity that produced #24).
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from bartholomew.kernel.scheduler import persistence
from bartholomew.kernel.scheduler.store import SchedulerStore


def _table_names(db_path: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def _column_names(db_path: str, table: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


@pytest.fixture
def config_files(tmp_path):
    """Minimal daemon config (mirrors tests/test_stage3_integration.py)."""
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
    from bartholomew.kernel.daemon import KernelDaemon

    return KernelDaemon(**config_files)


# =============================================================================
# T1 -- tables exist immediately after start() returns, and it is start()
#       itself (not the scheduler loop) that created them.
# =============================================================================


@pytest.mark.asyncio
async def test_scheduler_tables_exist_immediately_after_start(config_files, monkeypatch):
    """`ticks` and `scheduled_tasks` (and the additive nudge/reflection integer
    columns) exist the instant start() returns. run_scheduler is stubbed to a
    no-op so this proves start() -- not the loop -- established readiness."""
    monkeypatch.setattr(
        "bartholomew.kernel.scheduler.loop.run_scheduler",
        lambda ctx: asyncio.sleep(0),
    )
    daemon = _make_daemon(config_files)
    await daemon.start()
    try:
        db = config_files["db_path"]
        tables = _table_names(db)
        assert "ticks" in tables
        assert "scheduled_tasks" in tables
        # additive integer-timestamp columns from the scheduler schema
        assert "created_ts_s" in _column_names(db, "nudges")
        assert "ts_s" in _column_names(db, "reflections")
    finally:
        await daemon.stop()


# =============================================================================
# T2a -- ordering: schema readiness completes strictly BEFORE the scheduler
#        task is created. Proven with an ordered call record; run_scheduler is
#        replaced by a spy that records the exact moment start() constructs the
#        scheduler coroutine (`run_scheduler(self)` is evaluated at the
#        create_task call site).
# =============================================================================


@pytest.mark.asyncio
async def test_schema_ready_before_scheduler_task_created(config_files, monkeypatch):
    events: list[str] = []

    real_ensure = SchedulerStore.ensure_schema

    async def spy_ensure(self):
        await real_ensure(self)
        events.append("schema_ready")

    monkeypatch.setattr(SchedulerStore, "ensure_schema", spy_ensure)

    def spy_run_scheduler(ctx):
        # Evaluated at start()'s `asyncio.create_task(run_scheduler(self))`
        # line -- i.e. exactly at scheduler-task creation.
        events.append("scheduler_task_created")
        return asyncio.sleep(0)

    monkeypatch.setattr(
        "bartholomew.kernel.scheduler.loop.run_scheduler",
        spy_run_scheduler,
    )

    daemon = _make_daemon(config_files)
    await daemon.start()
    try:
        # With run_scheduler stubbed, its own (idempotent) ensure_schema never
        # runs, so exactly one schema_ready is recorded -- from start().
        assert events == ["schema_ready", "scheduler_task_created"]
    finally:
        await daemon.stop()


# =============================================================================
# T2b -- ordering: schema readiness completes BEFORE the scheduler loop's first
#        database operation, and that op always sees the schema present. Uses
#        the REAL run_scheduler and an asyncio barrier (no timing snapshot).
# =============================================================================


@pytest.mark.asyncio
async def test_schema_ready_before_scheduler_first_db_op(config_files, monkeypatch):
    events: list[str] = []
    tables_at_first_db_op: dict[str, bool] = {}
    loop = asyncio.get_running_loop()
    first_db_op = asyncio.Event()

    real_ensure = SchedulerStore.ensure_schema

    async def spy_ensure(self):
        await real_ensure(self)
        if "schema_ready" not in events:  # record only the first (start()'s)
            events.append("schema_ready")

    monkeypatch.setattr(SchedulerStore, "ensure_schema", spy_ensure)

    real_next_due = persistence.next_due_task

    def spy_next_due(db_path, now_ts):
        # Runs on the scheduler worker thread; this is the loop's first DB op.
        if not first_db_op.is_set():
            names = _table_names(db_path)
            tables_at_first_db_op["ticks"] = "ticks" in names
            tables_at_first_db_op["scheduled_tasks"] = "scheduled_tasks" in names
            events.append("first_db_op")
            loop.call_soon_threadsafe(first_db_op.set)
        return real_next_due(db_path, now_ts)

    monkeypatch.setattr(persistence, "next_due_task", spy_next_due)

    daemon = _make_daemon(config_files)
    await daemon.start()
    try:
        # No `await` between start() returning and this assertion, so the
        # scheduler task cannot have stepped yet: schema is ready, no DB op.
        assert events == ["schema_ready"]

        # Now let the loop run; wait deterministically for its first DB op.
        await asyncio.wait_for(first_db_op.wait(), timeout=5.0)

        assert events[0] == "schema_ready"
        assert "first_db_op" in events
        assert events.index("schema_ready") < events.index("first_db_op")
        # The loop never queries before the schema exists.
        assert tables_at_first_db_op == {"ticks": True, "scheduled_tasks": True}
    finally:
        await daemon.stop()


# =============================================================================
# T3 -- fail-closed + cleanup: a schema-init exception propagates out of
#       start(); no scheduler (or other background) task is created; the store
#       worker is closed; the daemon is left cleanly stopped; and a later
#       startup against the same DB is not poisoned by the failed attempt.
# =============================================================================


@pytest.mark.asyncio
async def test_start_fails_closed_and_cleans_up_on_schema_error(config_files, monkeypatch):
    boom = RuntimeError("schema init boom")

    def raising_ensure(db_path):
        raise boom

    # Patch the worker-thread function so the real SchedulerStore path runs
    # (thread spawned, gate acquired) and the failure propagates through it.
    monkeypatch.setattr(persistence, "ensure_schema", raising_ensure)

    daemon = _make_daemon(config_files)
    with pytest.raises(RuntimeError, match="schema init boom"):
        await daemon.start()

    # Fail closed: no scheduler task, and none of the other background tasks
    # either (ensure_schema runs before any of them are created).
    assert daemon._scheduler_task is None
    assert daemon._tick_task is None
    assert daemon._consumer_task is None
    assert daemon._dream_task is None
    # The store worker was cleaned up, not left running.
    assert daemon.scheduler_store._closed is True

    # A later startup (fresh daemon, same DB) is not poisoned by the failure.
    monkeypatch.undo()  # restore the real ensure_schema
    daemon2 = _make_daemon(config_files)
    await daemon2.start()
    try:
        assert {"ticks", "scheduled_tasks"} <= _table_names(config_files["db_path"])
    finally:
        await daemon2.stop()


# =============================================================================
# T3b -- a failing cleanup must not mask the primary schema-init error: when
#        ensure_schema() raises AND close() also raises, the *original*
#        schema-init error propagates, no scheduler task is created, and the
#        cleanup failure is observable (logged).
# =============================================================================


@pytest.mark.asyncio
async def test_primary_schema_error_survives_a_failing_cleanup(config_files, monkeypatch, caplog):
    import logging

    primary = RuntimeError("schema init boom")

    def raising_ensure(db_path):
        raise primary

    monkeypatch.setattr(persistence, "ensure_schema", raising_ensure)

    daemon = _make_daemon(config_files)

    async def raising_close(*args, **kwargs):
        raise ValueError("cleanup boom")

    monkeypatch.setattr(daemon.scheduler_store, "close", raising_close)

    try:
        with caplog.at_level(logging.ERROR):
            with pytest.raises(RuntimeError) as excinfo:
                await daemon.start()

        # The PRIMARY schema-init error is what propagated -- not the cleanup
        # error, and not a chained replacement of it.
        assert excinfo.value is primary
        assert "cleanup boom" not in str(excinfo.value)

        # Still fail-closed: no scheduler task was created.
        assert daemon._scheduler_task is None

        # The cleanup failure is observable: logged at ERROR with its traceback.
        assert "Scheduler store cleanup failed" in caplog.text
        assert any(rec.exc_info for rec in caplog.records)
    finally:
        # Restore the real close() and actually shut the worker thread down,
        # since the monkeypatched close() above skipped the real cleanup.
        monkeypatch.undo()
        await daemon.scheduler_store.close()


# =============================================================================
# T4 -- the retained run_scheduler-side ensure_schema is idempotent, so calling
#       it after start() already ensured the schema is a harmless no-op.
# =============================================================================


@pytest.mark.asyncio
async def test_ensure_schema_is_idempotent(config_files):
    daemon = _make_daemon(config_files)
    await daemon.mem.init()
    try:
        await daemon.scheduler_store.ensure_schema()
        await daemon.scheduler_store.ensure_schema()  # must not raise
        assert {"ticks", "scheduled_tasks"} <= _table_names(config_files["db_path"])
    finally:
        await daemon.scheduler_store.close()
