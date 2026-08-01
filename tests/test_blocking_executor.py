"""
Tests for bartholomew.kernel.blocking_executor.SingleWorkerExecutor.

Exercises the primitive directly with plain, non-persistence callables to
prove it is genuinely storage-agnostic -- SchedulerStore's own tests
(tests/test_scheduler_persistence_concurrency.py) already cover it via a
SQLite-backed facade; these tests deliberately do not touch SQLite at all.
"""

import asyncio
import threading
import time

import pytest

from bartholomew.kernel.blocking_executor import ExecutorClosedError, SingleWorkerExecutor


async def test_submit_returns_result():
    executor = SingleWorkerExecutor(label="test")
    try:
        result = await executor.submit(lambda x, y: x + y, 2, 3)
        assert result == 5
    finally:
        await executor.close()


async def test_submit_propagates_exceptions():
    executor = SingleWorkerExecutor(label="test")

    def boom():
        raise ValueError("kaboom")

    try:
        with pytest.raises(ValueError, match="kaboom"):
            await executor.submit(boom)
    finally:
        await executor.close()


async def test_submissions_are_strictly_sequential():
    """At most one submitted callable runs at a time, even under concurrent
    submission -- the defining property of this primitive."""
    executor = SingleWorkerExecutor(label="test")
    counter_lock = threading.Lock()
    current = 0
    peak = 0

    def stub_op(n):
        nonlocal current, peak
        with counter_lock:
            current += 1
            peak = max(peak, current)
        time.sleep(0.02)
        with counter_lock:
            current -= 1
        return n

    try:
        results = await asyncio.gather(*[executor.submit(stub_op, i) for i in range(10)])
    finally:
        await executor.close()

    assert peak == 1, f"peak concurrent execution was {peak}, expected exactly 1"
    assert sorted(results) == list(range(10))


async def test_submit_after_close_raises_default_exception():
    executor = SingleWorkerExecutor(label="test")
    await executor.close()
    with pytest.raises(ExecutorClosedError):
        await executor.submit(lambda: "should not run")


async def test_submit_after_close_raises_custom_exception_subclass():
    """Callers can supply their own closed-exception type (e.g.
    SchedulerStoreClosedError) while still being catchable via the base
    ExecutorClosedError."""

    class MyClosedError(ExecutorClosedError):
        pass

    executor = SingleWorkerExecutor(label="test", closed_exception_cls=MyClosedError)
    await executor.close()
    with pytest.raises(MyClosedError):
        await executor.submit(lambda: "should not run")
    # Also catchable generically.
    executor2 = SingleWorkerExecutor(label="test2", closed_exception_cls=MyClosedError)
    await executor2.close()
    with pytest.raises(ExecutorClosedError):
        await executor2.submit(lambda: "should not run")


async def test_close_is_idempotent():
    executor = SingleWorkerExecutor(label="test")
    first = await executor.close()
    second = await executor.close()
    assert first is True
    assert second is True


async def test_close_waits_for_pending_work_then_confirms_drained():
    """close() must not report success until the outstanding operation has
    actually finished, not merely been submitted."""
    executor = SingleWorkerExecutor(label="test")
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def blocking_op():
        started.set()
        release.wait(timeout=5)
        finished.set()
        return "done"

    task = asyncio.create_task(executor.submit(blocking_op))
    await asyncio.get_event_loop().run_in_executor(None, started.wait, 5)

    async def release_soon():
        await asyncio.sleep(0.05)
        release.set()

    release_task = asyncio.create_task(release_soon())
    drained = await executor.close(timeout=5.0)
    await release_task

    assert drained is True
    assert finished.is_set(), "close() reported drained before the worker actually finished"
    assert await task == "done"


async def test_close_times_out_and_reports_not_drained():
    """A pending operation that outlives close()'s timeout must make
    close() return False, not silently report success."""
    executor = SingleWorkerExecutor(label="test")
    release = threading.Event()

    def slow_op():
        release.wait(timeout=5)
        return "done"

    task = asyncio.create_task(executor.submit(slow_op))
    await asyncio.sleep(0.02)  # let the worker thread pick it up

    drained = await executor.close(timeout=0.05)
    assert drained is False

    release.set()
    await task  # let the background thread finish so it doesn't leak into other tests
