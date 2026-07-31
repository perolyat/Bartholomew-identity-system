"""
Generalized dedicated-worker-thread executor for synchronous SQLite work.

Phase B, stage B2. `bartholomew/kernel/scheduler/store.py`'s `SchedulerStore`
established the pattern this module generalizes: run all synchronous SQLite
calls for one logical owner on exactly one dedicated worker thread (not
`asyncio.to_thread`'s shared default executor), gated so at most one
operation is ever outstanding at a time, with confirmed -- not merely
submitted-and-assumed -- termination on close(). See
`docs/PHASE_B_OVERVIEW.md`'s B2 scope and `docs/B0_BASELINE_REPORT.md` section
3 for the call sites this exists to fix: `MemoryStore._handle_chunking()`/
`reembed_memory()`, `SkillRegistry`'s connection-opening helpers, the three
`*Skill` classes' database methods, and `PersonaPackManager`/`NarratorEngine`'s
synchronous methods reached directly from `KernelDaemon.start()`.

Each owner (e.g. one `MemoryStore` instance, one `SkillRegistry` instance)
should construct and hold exactly one `DedicatedDbExecutor` for its own
lifetime and be responsible for closing it -- the same ownership contract
`SchedulerStore` already uses.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")


class DbExecutorClosedError(RuntimeError):
    """Raised when a call is submitted after close() has been called. The
    one, consistent exception type for that condition -- callers should
    catch this specifically rather than a bare Exception/RuntimeError."""


class DedicatedDbExecutor:
    """One dedicated worker thread for this instance's entire lifetime.

    Construction is cheap and does no I/O -- the underlying
    ThreadPoolExecutor doesn't spawn its worker thread until the first call
    is submitted -- so it's safe to create unconditionally.

    Not itself SQLite-specific: `call()` runs any synchronous callable on the
    dedicated thread. The SQLite framing is in this module's docstring and
    naming only, matching how `SchedulerStore` is actually used.
    """

    def __init__(self, name: str, *, thread_name_prefix: str | None = None):
        self.name = name
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=thread_name_prefix or f"db-exec-{name}",
        )
        self._closed = False
        # Backpressure gate: at most one operation submitted to the executor
        # at a time, enforced at the asyncio layer -- not left as an
        # emergent property of the executor's own internal queue. Released
        # only by _finish_call (driven by the future's own done-callback),
        # never by the awaiting coroutine's own control flow -- see call()'s
        # docstring.
        self._gate = asyncio.Lock()
        # The single outstanding concurrent.futures.Future, if any.
        self._current_future: Future | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        # Guards close()'s own body so concurrent/repeated close() calls
        # only ever do the drain-and-shutdown work once (idempotency).
        self._close_lock = asyncio.Lock()
        self._close_result: bool | None = None

    async def call(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Submit fn(*args, **kwargs) to the dedicated worker thread and
        await its result.

        Cancelling the coroutine awaiting this call does NOT cancel or lose
        tracking of the underlying worker future: asyncio.wrap_future
        propagates cancellation to the concurrent.futures.Future, but that's
        a no-op once the thread has actually started running (the stdlib
        only allows cancelling futures that haven't started yet), so the
        thread keeps executing to completion regardless. The gate is
        released, and self._current_future cleared, only by _finish_call --
        driven by the future's own done-callback -- so close() can still
        find and drain it even if the original caller gave up waiting.

        Raises DbExecutorClosedError if close() has already been called --
        this is an "accepted vs. not submitted" outcome distinction: a call
        either gets a Future submitted to the executor (accepted, and will
        run to completion even under cancellation, per above) or raises
        before any submission happens (not submitted at all).
        """
        self._loop = self._loop or asyncio.get_running_loop()
        await self._gate.acquire()
        try:
            if self._closed:
                raise DbExecutorClosedError(
                    f"DedicatedDbExecutor {self.name!r} is closed; refusing to submit {fn.__name__}",
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
        operation (if any) to finish, then shut down without ever blocking
        the event loop.

        Idempotent: safe to call more than once (concurrently or in
        sequence) -- only the first call actually drains/shuts down; later
        calls return the same cached result immediately.

        Returns True if fully drained within `timeout`, False otherwise. A
        False return is never swallowed -- it's logged here and the caller
        MUST check it before doing anything that assumes the underlying
        resource (e.g. the DB file) is now quiescent. On a False return, the
        abandoned thread work may still be running in the background; this
        method does not and cannot forcibly kill it (concurrent.futures
        offers no thread-kill) -- it only stops the executor from waiting on
        or accepting more work. This is the "confirmed, not merely
        submitted-and-assumed" termination semantic B2 requires.
        """
        # Set immediately and unconditionally (idempotent by nature --
        # setting True twice is harmless) so call() starts rejecting new
        # submissions right away, even while a concurrent close() call is
        # still queued behind _close_lock below.
        self._closed = True

        async with self._close_lock:
            if self._close_result is not None:
                return self._close_result

            # No `await` between here and self._closed = True above, so
            # this read is atomic relative to every other coroutine on this
            # event loop -- `pending` is exactly "whatever is genuinely
            # outstanding right now, or None", never stale.
            pending = self._current_future

            drained = True
            if pending is not None and not pending.done():
                try:
                    await asyncio.wait_for(asyncio.wrap_future(pending), timeout=timeout)
                except asyncio.TimeoutError:
                    drained = False
                    log.warning(
                        "DedicatedDbExecutor(%s).close(): pending work did not finish "
                        "within %.1fs; abandoning it (the thread may still be running) "
                        "and releasing anything queued behind it",
                        self.name,
                        timeout,
                    )
                    # Force the gate open so waiters behind a stuck
                    # operation get their DbExecutorClosedError promptly
                    # instead of hanging on a lock that may never release
                    # naturally.
                    if self._gate.locked():
                        self._gate.release()
                except Exception as e:
                    # The operation's own failure isn't close()'s concern,
                    # but don't hide that it happened.
                    log.debug(
                        "DedicatedDbExecutor(%s).close(): pending operation raised during "
                        "drain: %s",
                        self.name,
                        e,
                    )

            self._executor.shutdown(wait=False, cancel_futures=True)
            self._close_result = drained
            return drained
