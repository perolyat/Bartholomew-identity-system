"""
Tests for bartholomew.kernel.db_executor.DedicatedDbExecutor.

Phase B, stage B2. This module generalizes the pattern
tests/test_scheduler_persistence_concurrency.py already exercises for
SchedulerStore -- these tests mirror that file's shape (serialization,
close() drain/idempotency/cancellation-safety) against the generalized
executor directly, independent of any particular caller.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from bartholomew.kernel.db_executor import DbExecutorClosedError, DedicatedDbExecutor


async def test_call_runs_on_dedicated_thread_and_returns_result():
    executor = DedicatedDbExecutor("test")
    try:
        result = await executor.call(lambda x: x * 2, 21)
        assert result == 42
    finally:
        await executor.close()


async def test_call_accepts_keyword_arguments():
    executor = DedicatedDbExecutor("test")
    try:
        result = await executor.call(lambda a, b, c=0: a + b + c, 1, 2, c=39)
        assert result == 42
    finally:
        await executor.close()


async def test_call_propagates_exceptions():
    executor = DedicatedDbExecutor("test")

    def boom():
        raise ValueError("db exploded")

    try:
        with pytest.raises(ValueError, match="db exploded"):
            await executor.call(boom)
    finally:
        await executor.close()


async def test_calls_serialize_never_run_concurrently():
    executor = DedicatedDbExecutor("test")

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
        results = await asyncio.gather(*[executor.call(stub_op, i) for i in range(10)])
    finally:
        await executor.close()

    assert peak == 1, f"peak concurrent execution was {peak}, expected exactly 1"
    assert sorted(results) == list(range(10))
    assert executor._executor._max_workers == 1


async def test_call_does_not_block_event_loop():
    executor = DedicatedDbExecutor("test")
    heartbeat = 0
    stop = asyncio.Event()

    async def beat():
        nonlocal heartbeat
        while not stop.is_set():
            heartbeat += 1
            await asyncio.sleep(0.01)

    heartbeat_task = asyncio.create_task(beat())

    def slow_op():
        time.sleep(0.3)
        return "done"

    result = await executor.call(slow_op)

    stop.set()
    await heartbeat_task
    await executor.close()

    assert result == "done"
    assert heartbeat > 5, f"event loop only ticked {heartbeat} times during a 0.3s call"


async def test_close_rejects_new_calls_after_closing():
    executor = DedicatedDbExecutor("test")
    await executor.call(lambda: "warm up")
    assert await executor.close() is True

    with pytest.raises(DbExecutorClosedError):
        await executor.call(lambda: "should not run")


async def test_close_rejects_queued_callers_once_closed():
    """Start one blocked operation, queue several more behind the gate,
    close() mid-flight, release the blocker, and verify nothing queued gets
    submitted after closure -- every caller resolves predictably."""
    executor = DedicatedDbExecutor("test")
    release_event = threading.Event()
    started_event = threading.Event()

    def blocking_op():
        started_event.set()
        release_event.wait(timeout=5)
        return "done"

    blocked_task = asyncio.create_task(executor.call(blocking_op))
    await asyncio.get_event_loop().run_in_executor(None, started_event.wait, 5)

    queued_tasks = [asyncio.create_task(executor.call(blocking_op)) for _ in range(3)]
    await asyncio.sleep(0.05)  # let them queue on the gate

    close_task = asyncio.create_task(executor.close(timeout=5.0))
    await asyncio.sleep(0.05)
    release_event.set()

    assert await close_task is True
    assert await blocked_task == "done"
    for t in queued_tasks:
        with pytest.raises(DbExecutorClosedError):
            await t


async def test_close_drains_future_after_awaiting_coroutine_cancelled():
    """Cancel the coroutine awaiting an already-running call; verify close()
    still detects and drains the underlying worker future rather than
    believing the executor is idle -- confirmed, not assumed, termination."""
    executor = DedicatedDbExecutor("test")
    release_event = threading.Event()
    started_event = threading.Event()
    completed = threading.Event()

    def blocking_op():
        started_event.set()
        release_event.wait(timeout=5)
        completed.set()
        return "done"

    task = asyncio.create_task(executor.call(blocking_op))
    await asyncio.get_event_loop().run_in_executor(None, started_event.wait, 5)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    close_task = asyncio.create_task(executor.close(timeout=5.0))
    await asyncio.sleep(0.05)
    assert not completed.is_set()  # close() is genuinely still waiting

    release_event.set()
    assert await close_task is True
    assert completed.is_set()


async def test_close_times_out_and_reports_undrained():
    """A call that outlives close()'s timeout must produce drained=False,
    not a silently optimistic True -- the caller has to be able to tell
    the two apart before doing anything that assumes quiescence."""
    executor = DedicatedDbExecutor("test")
    release_event = threading.Event()
    started_event = threading.Event()

    def blocking_op():
        started_event.set()
        release_event.wait(timeout=5)
        return "done"

    task = asyncio.create_task(executor.call(blocking_op))
    await asyncio.get_event_loop().run_in_executor(None, started_event.wait, 5)

    drained = await executor.close(timeout=0.1)
    assert drained is False

    release_event.set()
    # The abandoned thread keeps running to completion regardless of
    # close() giving up on waiting for it (concurrent.futures offers no
    # thread-kill) -- await it out so it doesn't leak into other tests.
    assert await task == "done"


async def test_close_is_idempotent():
    executor = DedicatedDbExecutor("test")
    await executor.call(lambda: "warm up")

    first = await executor.close()
    second = await executor.close()
    third = await executor.close()

    assert first == second == third is True


async def test_close_with_no_calls_ever_made():
    executor = DedicatedDbExecutor("test")
    assert await executor.close() is True
