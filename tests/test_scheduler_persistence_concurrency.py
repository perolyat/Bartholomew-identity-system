"""
Regression tests for the scheduler-persistence-off-the-event-loop fix.

See DECISIONS.md's "Scheduler persistence moved off the event loop;
routine WAL checkpointing turned off by default" entry for the incident
these guard against: PR #20's CI hung for its full 120s pytest-timeout
inside test_kernel_alive.py, with the main thread stuck in a synchronous
PRAGMA wal_checkpoint(TRUNCATE) call made directly from
scheduler/loop.py's run_scheduler() -- a single always-on background
asyncio.Task sharing the daemon's one event-loop thread with everything
else. bartholomew.kernel.scheduler.store.SchedulerStore offloads that
work onto one dedicated worker thread; bartholomew.kernel.db_ctx.wal_db()
no longer checkpoints on every call by default.
"""

from __future__ import annotations

import asyncio
import contextlib
import multiprocessing as mp
import sqlite3
import threading
import time

import pytest

from bartholomew.kernel.db_ctx import wal_checkpoint
from bartholomew.kernel.scheduler.store import SchedulerStore, SchedulerStoreClosedError


@pytest.fixture
def mock_config_files(tmp_path):
    """Create mock config files for daemon (mirrors test_scheduler_drive_convergence.py)."""
    cfg_path = tmp_path / "kernel.yaml"
    cfg_path.write_text(
        """
timezone: "Australia/Brisbane"
loop_interval_seconds: 1
quiet_hours:
  start: "23:00"
  end: "06:00"
dreaming:
  nightly_window: "21:00-23:00"
  weekly:
    weekday: "Sun"
    time: "21:30"
""",
    )

    persona_path = tmp_path / "persona.yaml"
    persona_path.write_text('name: "Test Bartholomew"\n')

    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text("policies: []\n")

    drives_path = tmp_path / "drives.yaml"
    drives_path.write_text("drives: []\n")

    db_path = tmp_path / "test.db"

    return {
        "cfg_path": str(cfg_path),
        "db_path": str(db_path),
        "persona_path": str(persona_path),
        "policy_path": str(policy_path),
        "drives_path": str(drives_path),
    }


def _make_table(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE t(x)")
    conn.commit()
    conn.close()


class _LockHolder:
    """Holds a write lock on db_path from a background thread until released."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.started = threading.Event()
        self.release = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO t VALUES (1)")
        self.started.set()
        self.release.wait(timeout=10)
        conn.commit()
        conn.close()

    def __enter__(self) -> _LockHolder:
        self._thread.start()
        assert self.started.wait(timeout=5), "lock holder never acquired the write lock"
        return self

    def __exit__(self, *exc) -> None:
        self.release.set()
        self._thread.join(timeout=5)


# -- 1. Event loop heartbeat continues under contention -----------------------


async def test_event_loop_heartbeat_continues_during_checkpoint_contention(tmp_path):
    db_path = str(tmp_path / "test.db")
    _make_table(db_path)

    heartbeat = 0
    stop = asyncio.Event()

    async def beat():
        nonlocal heartbeat
        while not stop.is_set():
            heartbeat += 1
            await asyncio.sleep(0.01)

    heartbeat_task = asyncio.create_task(beat())
    store = SchedulerStore(db_path)

    def contended_write():
        # Real sqlite3 write contending against the held lock; whichever
        # way it resolves (succeeds once the lock clears, or raises) is
        # irrelevant to this test -- what matters is that the event loop
        # kept ticking while it was in flight.
        conn = sqlite3.connect(db_path, timeout=3.0)
        conn.execute("INSERT INTO t VALUES (2)")
        conn.commit()
        conn.close()

    with _LockHolder(db_path) as holder:
        contended_task = asyncio.create_task(store._call(contended_write))
        await asyncio.sleep(0.3)
        beats_during_contention = heartbeat
        assert beats_during_contention > 5, (
            f"event loop only ticked {beats_during_contention} times in 0.3s while "
            "a DB call was contended -- it was blocked"
        )
        holder.release.set()

    with contextlib.suppress(Exception):
        await contended_task
    stop.set()
    await heartbeat_task
    await store.close()


# -- 2. Fresh-DB scheduler startup cannot hang ---------------------------------


async def test_fresh_database_scheduler_startup_does_not_hang(mock_config_files):
    """Direct regression test for the CI failure: a fresh DB makes every
    registered drive immediately due (upsert_scheduled_tasks sets
    next_run_ts = now), producing the exact same-instant burst that hung
    test_kernel_alive.py. This must complete within a small bound."""
    from bartholomew.kernel.daemon import KernelDaemon
    from bartholomew.kernel.scheduler.loop import run_scheduler

    daemon = KernelDaemon(**mock_config_files)
    task = asyncio.create_task(run_scheduler(daemon))

    async def run_briefly_then_cancel():
        await asyncio.sleep(2.0)  # let the immediate all-drives-due burst run
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    await asyncio.wait_for(run_briefly_then_cancel(), timeout=15.0)

    drained = await daemon.scheduler_store.close()
    assert drained is True


# -- 3. Checkpoint contention degrades safely ----------------------------------


def test_passive_checkpoint_does_not_block_under_contention(tmp_path):
    db_path = str(tmp_path / "test.db")
    _make_table(db_path)

    with _LockHolder(db_path):
        start = time.monotonic()
        row = wal_checkpoint(db_path, timeout=5.0, mode="PASSIVE", label="test-passive")
        duration = time.monotonic() - start

    assert duration < 2.0, f"PASSIVE checkpoint blocked for {duration:.2f}s under contention"
    assert row is not None


def _hold_lock_then_attempt_truncate(db_path: str, result_queue, checkpoint_timeout: float) -> None:
    # Runs in a subprocess. Holds a write lock for its entire lifetime
    # (never commits/closes), then attempts a blocking TRUNCATE checkpoint
    # against it -- exercising the exact call shape that hung in CI.
    holder = sqlite3.connect(db_path, timeout=1.0)
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("INSERT INTO t VALUES (1)")
    try:
        row = wal_checkpoint(
            db_path,
            timeout=checkpoint_timeout,
            mode="TRUNCATE",
            label="test-contention",
        )
        result_queue.put(("returned", row))
    except Exception as e:
        result_queue.put(("raised", type(e).__name__, str(e)))


def test_truncate_checkpoint_under_contention_is_subprocess_isolated(tmp_path):
    """
    A held write lock + PRAGMA wal_checkpoint(TRUNCATE) is run inside a
    subprocess with a hard parent-enforced timeout, so if TRUNCATE's
    blocking behavior under contention genuinely exceeds its own
    busy-timeout (the still-open question in DECISIONS.md's
    scheduler-persistence entry), it cannot hang this test suite --
    it can only fail this one test, loudly, with the subprocess reaped.
    """
    db_path = str(tmp_path / "test.db")
    _make_table(db_path)

    result_queue: mp.Queue = mp.Queue()
    checkpoint_timeout = 2.0
    hard_bound = checkpoint_timeout + 10.0  # generous safety margin

    proc = mp.Process(
        target=_hold_lock_then_attempt_truncate,
        args=(db_path, result_queue, checkpoint_timeout),
    )
    proc.start()
    proc.join(timeout=hard_bound)

    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=5)
        pytest.fail(
            f"TRUNCATE checkpoint under contention did not complete within "
            f"the {hard_bound}s hard bound -- reproduces the unresolved "
            f"question in DECISIONS.md's scheduler-persistence entry (why "
            f"a TRUNCATE checkpoint outlasted its own busy-timeout in CI). "
            f"The subprocess was terminated; this test suite was not blocked.",
        )

    outcome = result_queue.get(timeout=5)
    print(f"[test] TRUNCATE-under-contention outcome: {outcome}")
    assert outcome[0] in ("returned", "raised")


# -- 4. Scheduler persistence stays sequential/bounded -------------------------


async def test_scheduler_store_serializes_calls(tmp_path):
    db_path = str(tmp_path / "test.db")
    store = SchedulerStore(db_path)

    peak = 0
    current = 0
    counter_lock = threading.Lock()

    def stub_op(n):
        nonlocal peak, current
        with counter_lock:
            current += 1
            peak = max(peak, current)
        time.sleep(0.02)
        with counter_lock:
            current -= 1
        return n

    try:
        results = await asyncio.gather(*[store._call(stub_op, i) for i in range(10)])
    finally:
        await store.close()

    assert peak == 1, f"peak concurrent execution was {peak}, expected exactly 1"
    assert sorted(results) == list(range(10))
    assert store._worker._executor._max_workers == 1


# -- 5 & 6. SchedulerStore.close() lifecycle -----------------------------------


async def test_close_rejects_queued_callers_once_closed(tmp_path):
    """Start one blocked operation, queue several more behind the gate,
    close() mid-flight, release the blocker, and verify nothing queued
    gets submitted after closure -- every caller resolves predictably."""
    store = SchedulerStore(str(tmp_path / "test.db"))
    release_event = threading.Event()
    started_event = threading.Event()

    def blocking_op():
        started_event.set()
        release_event.wait(timeout=5)
        return "done"

    blocked_task = asyncio.create_task(store._call(blocking_op))
    await asyncio.get_event_loop().run_in_executor(None, started_event.wait, 5)

    queued_tasks = [asyncio.create_task(store._call(blocking_op)) for _ in range(3)]
    await asyncio.sleep(0.05)  # let them queue on the gate

    close_task = asyncio.create_task(store.close(timeout=5.0))
    await asyncio.sleep(0.05)
    release_event.set()

    assert await close_task is True
    assert await blocked_task == "done"
    for t in queued_tasks:
        with pytest.raises(SchedulerStoreClosedError):
            await t


async def test_close_drains_future_after_awaiting_coroutine_cancelled(tmp_path):
    """Cancel the coroutine awaiting an already-running store operation;
    verify close() still detects and drains the underlying worker future
    rather than believing the store is idle."""
    store = SchedulerStore(str(tmp_path / "test.db"))
    release_event = threading.Event()
    started_event = threading.Event()
    completed = threading.Event()

    def blocking_op():
        started_event.set()
        release_event.wait(timeout=5)
        completed.set()
        return "done"

    task = asyncio.create_task(store._call(blocking_op))
    await asyncio.get_event_loop().run_in_executor(None, started_event.wait, 5)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    close_task = asyncio.create_task(store.close(timeout=5.0))
    await asyncio.sleep(0.05)
    assert not completed.is_set()  # close() is genuinely still waiting

    release_event.set()
    assert await close_task is True
    assert completed.is_set()


async def test_close_is_idempotent(tmp_path):
    store = SchedulerStore(str(tmp_path / "test.db"))
    await store._call(lambda: "warm up")

    first = await store.close()
    second = await store.close()
    third = await store.close()

    assert first == second == third is True

    with pytest.raises(SchedulerStoreClosedError):
        await store._call(lambda: "should not run")
