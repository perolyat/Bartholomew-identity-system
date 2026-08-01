"""
Single dedicated worker thread for offloading blocking calls off the
asyncio event loop, with confirmed -- not merely submitted -- termination.

Storage-agnostic: this module knows nothing about SQLite, or any other
persistence mechanism. It offloads an arbitrary blocking callable onto one
dedicated worker thread (not asyncio.to_thread's shared default executor),
keeps submitted work strictly sequential (at most one operation in flight
at a time, enforced at the asyncio layer), and close() bound-waits for the
one outstanding operation before shutting down rather than merely
requesting shutdown and assuming it happened.

This generalizes the pattern bartholomew/kernel/scheduler/store.py's
SchedulerStore introduced for scheduler persistence (Phase B stage B2; see
docs/B2_EVENT_LOOP_ISOLATION.md) into a reusable primitive any caller with
blocking, non-async-safe work can use -- SQLite-backed or not.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

log = logging.getLogger(__name__)


class ExecutorClosedError(RuntimeError):
    """Raised when submit() is called after close(). Callers should catch
    this (or a caller-supplied subclass -- see SingleWorkerExecutor's
    closed_exception_cls) specifically rather than a bare
    Exception/RuntimeError."""


class SingleWorkerExecutor:
    """One dedicated worker thread for this instance's entire lifetime.

    Construction is cheap and does no I/O -- the underlying
    ThreadPoolExecutor doesn't spawn its worker thread until the first
    call is submitted -- so it's safe to create unconditionally.
    """

    def __init__(
        self,
        *,
        thread_name_prefix: str = "single-worker",
        label: str = "",
        closed_exception_cls: type[ExecutorClosedError] = ExecutorClosedError,
    ):
        self.label = label
        self._closed_exception_cls = closed_exception_cls
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=thread_name_prefix)
        self._closed = False
        # Backpressure gate: at most one operation submitted to the
        # executor at a time, enforced at the asyncio layer -- not left
        # as an emergent property of the executor's own internal queue.
        # Released only by _finish_call (driven by the future's own
        # done-callback), never by the awaiting coroutine's own control
        # flow -- see submit()'s docstring.
        self._gate = asyncio.Lock()
        # The single outstanding concurrent.futures.Future, if any.
        self._current_future: Future | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        # Guards close()'s own body so concurrent/repeated close() calls
        # only ever do the drain-and-shutdown work once (idempotency).
        self._close_lock = asyncio.Lock()
        self._close_result: bool | None = None

    async def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Submit fn(*args, **kwargs) to the dedicated worker thread and
        await its result.

        Cancelling the coroutine awaiting this call does NOT cancel or
        lose tracking of the underlying worker future: asyncio.wrap_future
        propagates cancellation to the concurrent.futures.Future, but
        that's a no-op once the thread has actually started running (the
        stdlib only allows cancelling futures that haven't started yet),
        so the thread keeps executing to completion regardless. The gate
        is released, and self._current_future cleared, only by
        _finish_call -- driven by the future's own done-callback -- so
        close() can still find and drain it even if the original caller
        gave up waiting.
        """
        self._loop = self._loop or asyncio.get_running_loop()
        await self._gate.acquire()
        try:
            if self._closed:
                raise self._closed_exception_cls(
                    f"SingleWorkerExecutor({self.label!r}) is closed; refusing to submit {fn!r}",
                )
            fut = self._executor.submit(fn, *args, **kwargs)
        except BaseException:
            self._gate.release()
            raise
        self._current_future = fut
        fut.add_done_callback(self._on_future_done)
        return await asyncio.wrap_future(fut)

    def _on_future_done(self, fut: Future) -> None:
        # Runs on the executor's worker thread -- hop back to the event
        # loop before touching asyncio-only state (the Lock).
        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(self._finish_call, fut)

    def _finish_call(self, fut: Future) -> None:
        if self._current_future is fut:
            self._current_future = None
        if self._gate.locked():
            self._gate.release()

    async def close(self, timeout: float = 5.0) -> bool:
        """
        Stop accepting new work, bound-wait for the one outstanding
        operation (if any) to finish, then shut down without ever
        blocking the event loop.

        Idempotent: safe to call more than once (concurrently or in
        sequence) -- only the first call actually drains/shuts down;
        later calls return the same cached result immediately.

        Returns True if fully drained within `timeout`, False otherwise.
        A False return must never be swallowed by the caller -- it means
        the abandoned thread work may still be running in the background;
        this method does not and cannot forcibly kill it (concurrent
        .futures offers no thread-kill), it only stops this executor from
        waiting on or accepting more work. Callers that assume the
        underlying resource is now quiescent (e.g. a shutdown-time
        checkpoint) must check this return value first.
        """
        # Set immediately and unconditionally (idempotent by nature --
        # setting True twice is harmless) so submit() starts rejecting new
        # submissions right away, even while a concurrent close() call is
        # still queued behind _close_lock below.
        self._closed = True

        async with self._close_lock:
            if self._close_result is not None:
                return self._close_result

            # No `await` between here and self._closed = True above, so
            # this read is atomic relative to every other coroutine on
            # this event loop -- `pending` is exactly "whatever is
            # genuinely outstanding right now, or None", never stale.
            pending = self._current_future

            drained = True
            if pending is not None and not pending.done():
                try:
                    await asyncio.wait_for(asyncio.wrap_future(pending), timeout=timeout)
                except asyncio.TimeoutError:
                    drained = False
                    log.warning(
                        "SingleWorkerExecutor(%s).close(): pending work did not finish "
                        "within %.1fs; abandoning it (the thread may still be running) "
                        "and releasing anything queued behind it",
                        self.label,
                        timeout,
                    )
                    # Force the gate open so waiters behind a stuck
                    # operation get their closed-exception promptly
                    # instead of hanging on a lock that may never
                    # release naturally.
                    if self._gate.locked():
                        self._gate.release()
                except Exception as e:
                    # The operation's own failure isn't close()'s
                    # concern, but don't hide that it happened.
                    log.debug(
                        "SingleWorkerExecutor(%s).close(): pending operation raised "
                        "during drain: %s",
                        self.label,
                        e,
                    )

            self._executor.shutdown(wait=False, cancel_futures=True)
            self._close_result = drained
            return drained


async def run_off_loop(
    fn: Callable[..., Any],
    *args: Any,
    executor: SingleWorkerExecutor | None = None,
    **kwargs: Any,
) -> Any:
    """
    Run fn(*args, **kwargs) off the event loop.

    Uses `executor` (a shared, owned SingleWorkerExecutor) when the caller
    has one available -- the common case for anything reachable from a
    KernelDaemon instance. Falls back to asyncio.to_thread() as a one-off
    when no dedicated executor is available (e.g. a call site with no
    owning daemon instance, or a duck-typed test context) -- still off the
    event loop, still confirmed-complete by the time this returns, just
    without the dedicated-thread/backpressure/close() lifecycle a shared
    executor provides for repeated use.
    """
    if executor is not None:
        return await executor.submit(fn, *args, **kwargs)
    return await asyncio.to_thread(fn, *args, **kwargs)
