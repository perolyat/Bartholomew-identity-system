"""
Clean-start persistence and lifecycle protection (Phase A, A4).

These tests do not restructure persistence (that is deferred to Phase B). They
pin the properties a fresh install must already have, so that a regression in
startup ordering, shutdown draining, or database-handle release fails CI
instead of surfacing as an environment-specific flake:

  - a brand-new temporary database gets the core schema *and* the scheduler
    schema, with the scheduler tables present before `start()` returns
    (the issue #24 / S5.0 invariant, asserted here from a product view);
  - the API bridge never serves a "missing table" window for scheduler-backed
    endpoints;
  - startup and shutdown each complete within a bounded time;
  - shutdown releases database handles well enough for the temporary directory
    to be deleted -- the property that fails first on Windows;
  - a daemon can be restarted against the same database;
  - shutdown leaves no scheduler worker threads or pending event-loop tasks.

Bounds are deliberately generous relative to observed local timings; they exist
to catch a hang, not to police performance. If one of these ever fails, the
correct response is to fix the lifecycle, not to raise the bound.
"""

from __future__ import annotations

import asyncio
import gc
import shutil
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

import pytest

# Generous upper bounds: a healthy local start/stop is well under a second.
# These catch hangs (the failure mode that matters), not slowness.
START_BUDGET_S = 60.0
STOP_BUDGET_S = 60.0

CORE_TABLES = {"memories", "nudges", "reflections", "memory_consent", "system_flags"}
SCHEDULER_TABLES = {"scheduled_tasks", "ticks"}


def _config_files(root: Path) -> dict[str, str]:
    (root / "kernel.yaml").write_text(
        'timezone: "Australia/Brisbane"\nloop_interval_seconds: 1\n',
        encoding="utf-8",
    )
    (root / "persona.yaml").write_text('name: "Test Bartholomew"\n', encoding="utf-8")
    (root / "policy.yaml").write_text("policies: []\n", encoding="utf-8")
    (root / "drives.yaml").write_text("drives: []\n", encoding="utf-8")
    return {
        "cfg_path": str(root / "kernel.yaml"),
        "db_path": str(root / "fresh.db"),
        "persona_path": str(root / "persona.yaml"),
        "policy_path": str(root / "policy.yaml"),
        "drives_path": str(root / "drives.yaml"),
    }


def _table_names(db_path: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def _scheduler_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name.startswith("scheduler-db")]


@pytest.fixture
def fresh_config(tmp_path):
    return _config_files(tmp_path)


def _make_daemon(config: dict[str, str]):
    from bartholomew.kernel.daemon import KernelDaemon

    return KernelDaemon(**config)


@pytest.mark.asyncio
async def test_fresh_database_gets_core_and_scheduler_schema(fresh_config) -> None:
    """A brand-new database has core *and* scheduler tables once start() returns."""
    db_path = fresh_config["db_path"]
    assert not Path(db_path).exists(), "precondition: database must not exist yet"

    daemon = _make_daemon(fresh_config)
    await daemon.start()
    try:
        tables = _table_names(db_path)
        missing_core = CORE_TABLES - tables
        missing_sched = SCHEDULER_TABLES - tables
        assert not missing_core, f"missing core tables after start(): {sorted(missing_core)}"
        assert not missing_sched, (
            f"missing scheduler tables after start(): {sorted(missing_sched)} "
            "(scheduler schema must be ready before start() returns)"
        )
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_startup_and_shutdown_are_bounded(fresh_config) -> None:
    """start() and stop() each complete within their budget (hang detector)."""
    daemon = _make_daemon(fresh_config)

    t0 = time.monotonic()
    await asyncio.wait_for(daemon.start(), timeout=START_BUDGET_S)
    start_elapsed = time.monotonic() - t0

    t1 = time.monotonic()
    await asyncio.wait_for(daemon.stop(), timeout=STOP_BUDGET_S)
    stop_elapsed = time.monotonic() - t1

    assert start_elapsed < START_BUDGET_S
    assert stop_elapsed < STOP_BUDGET_S


@pytest.mark.asyncio
async def test_shutdown_leaves_no_scheduler_threads_or_pending_tasks(fresh_config) -> None:
    """Shutdown drains the scheduler worker thread and its event-loop tasks."""
    threads_before = {t.ident for t in _scheduler_threads()}

    daemon = _make_daemon(fresh_config)
    await daemon.start()
    await daemon.stop()

    # Give cancelled tasks one loop iteration to finish unwinding.
    await asyncio.sleep(0)

    leaked = [t for t in _scheduler_threads() if t.ident not in threads_before and t.is_alive()]
    assert not leaked, f"scheduler worker thread(s) still alive after stop(): {leaked}"

    current = asyncio.current_task()
    pending = [
        t
        for t in asyncio.all_tasks()
        if t is not current and not t.done() and "KernelDaemon" in repr(t.get_coro())
    ]
    assert not pending, f"daemon tasks still pending after stop(): {pending}"

    for handle in (daemon._tick_task, daemon._consumer_task, daemon._dream_task):
        if handle is not None:
            assert handle.done(), f"background task not finished after stop(): {handle}"


@pytest.mark.asyncio
async def test_shutdown_releases_database_handles_for_tempdir_cleanup() -> None:
    """After stop(), the database directory can be deleted.

    This is the property that fails first on Windows, where an unreleased
    SQLite handle makes the file undeletable (PermissionError/WinError 32).
    Running it everywhere keeps the Linux CI leg honest about the same bug.
    """
    tmpdir = Path(tempfile.mkdtemp())
    try:
        daemon = _make_daemon(_config_files(tmpdir))
        await daemon.start()
        await daemon.stop()

        # Drop lingering references so CPython closes anything refcount-owned.
        del daemon
        gc.collect()

        shutil.rmtree(tmpdir)  # raises PermissionError on Windows if handles leak
        assert not tmpdir.exists()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.mark.asyncio
async def test_daemon_restarts_cleanly_against_same_database(fresh_config) -> None:
    """A second daemon can start against a database a previous run created."""
    first = _make_daemon(fresh_config)
    await first.start()
    await first.stop()

    second = _make_daemon(fresh_config)
    await asyncio.wait_for(second.start(), timeout=START_BUDGET_S)
    try:
        tables = _table_names(fresh_config["db_path"])
        assert CORE_TABLES <= tables
        assert SCHEDULER_TABLES <= tables
    finally:
        await asyncio.wait_for(second.stop(), timeout=STOP_BUDGET_S)


def test_api_has_no_missing_table_window_for_scheduler_endpoints(tmp_path, monkeypatch) -> None:
    """The API never serves a scheduler-backed endpoint before its tables exist.

    Drives the real FastAPI startup lifespan against a fresh database and hits
    the scheduler-backed liveness endpoints immediately -- the exact window that
    produced "no such table: ticks" (issue #24) before S5.0.
    """
    from fastapi.testclient import TestClient

    db_path = tmp_path / "api_fresh.db"
    monkeypatch.setenv("BARTH_DB_PATH", str(db_path))

    from bartholomew_api_bridge_v0_1.services.api.app import app

    with TestClient(app) as client:
        for endpoint in (
            "/api/liveness/ticks",
            "/api/liveness/nudges",
            "/api/liveness/reflections",
        ):
            response = client.get(f"{endpoint}?limit=5&offset=0")
            assert (
                response.status_code == 200
            ), f"{endpoint} returned {response.status_code}: {response.text[:200]}"
            assert isinstance(response.json(), list)
