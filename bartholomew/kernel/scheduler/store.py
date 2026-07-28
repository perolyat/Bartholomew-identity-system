"""
Async facade over scheduler/persistence.py's synchronous SQLite calls.

run_scheduler()'s tick loop and the drives it invokes (drive_self_check,
drive_reflection_micro) used to call db_ctx.wal_db()/persistence.py
directly -- plain, blocking sqlite3 code executed straight on the
asyncio event loop. Because run_scheduler() is a single always-on
background task sharing that event loop with everything else the daemon
does (kd.mem's aiosqlite calls, HTTP request handling in the live API),
a single slow or lock-contended call could freeze the whole process.
See DECISIONS.md's "scheduler persistence off the event loop" entry for
the incident that surfaced this.

SchedulerStore offloads those calls onto exactly one dedicated worker
thread (not asyncio.to_thread's shared default executor) so the event
loop is never blocked by scheduler DB I/O, and scheduler DB operations
stay strictly sequential -- matching the original synchronous loop's
ordering -- instead of racing multiple threads against the same file.

Ownership: whoever constructs a SchedulerStore is responsible for
closing it. KernelDaemon constructs one per instance in __init__ and
closes it in stop(). run_scheduler() uses ctx.scheduler_store when the
caller has provided one (the normal KernelDaemon-driven path) and does
not close it in that case; if ctx has no scheduler_store, run_scheduler()
constructs its own and closes it itself when the loop exits.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from . import persistence
from .health import get_system_metrics as _get_system_metrics

log = logging.getLogger(__name__)


class SchedulerStoreClosedError(RuntimeError):
    """Raised when a SchedulerStore operation is attempted after close()
    has been called. This is the one, consistent exception type for that
    condition -- callers should catch this specifically rather than a
    bare Exception/RuntimeError."""


class SchedulerStore:
    """One dedicated worker thread for this instance's entire lifetime.

    Construction is cheap and does no I/O -- the underlying
    ThreadPoolExecutor doesn't spawn its worker thread until the first
    call is submitted -- so it's safe to create unconditionally even for
    a KernelDaemon that's never start()ed.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scheduler-db")
        self._closed = False
        # Backpressure gate: at most one operation submitted to the
        # executor at a time, enforced at the asyncio layer -- not left
        # as an emergent property of the executor's own internal queue.
        # Released only by _finish_call (driven by the future's own
        # done-callback), never by the awaiting coroutine's own control
        # flow -- see _call()'s docstring.
        self._gate = asyncio.Lock()
        # The single outstanding concurrent.futures.Future, if any.
        self._current_future: Future | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        # Guards close()'s own body so concurrent/repeated close() calls
        # only ever do the drain-and-shutdown work once (idempotency).
        self._close_lock = asyncio.Lock()
        self._close_result: bool | None = None

    async def _call(self, fn, *args) -> Any:
        """Submit fn(*args) to the dedicated worker thread and await its
        result.

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
                raise SchedulerStoreClosedError(
                    f"SchedulerStore for {self.db_path!r} is closed; refusing to submit {fn.__name__}",
                )
            fut = self._executor.submit(fn, *args)
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

    # -- persistence.py mirror ----------------------------------------------

    async def ensure_schema(self) -> None:
        await self._call(persistence.ensure_schema, self.db_path)

    async def upsert_scheduled_tasks(self, tasks: dict[str, dict[str, Any]]) -> None:
        await self._call(persistence.upsert_scheduled_tasks, self.db_path, tasks)

    async def next_due_task(self, now_ts: int) -> dict[str, Any] | None:
        return await self._call(persistence.next_due_task, self.db_path, now_ts)

    async def tick_exists(self, idempotency_key: str) -> bool:
        return await self._call(persistence.tick_exists, self.db_path, idempotency_key)

    async def insert_tick(
        self,
        task_id: str,
        started_ts: int,
        finished_ts: int | None,
        success: int,
        idempotency_key: str,
        result_meta: dict[str, Any] | None = None,
    ) -> int:
        return await self._call(
            persistence.insert_tick,
            self.db_path,
            task_id,
            started_ts,
            finished_ts,
            success,
            idempotency_key,
            result_meta,
        )

    async def insert_nudge(
        self,
        kind: str,
        message: str,
        actions: list[dict[str, Any]],
        reason: str,
        created_ts: int,
    ) -> int:
        return await self._call(
            persistence.insert_nudge,
            self.db_path,
            kind,
            message,
            actions,
            reason,
            created_ts,
        )

    async def update_next_run(
        self,
        task_id: str,
        next_run_ts: int,
        last_run_ts: int,
        window_state: str | None = None,
    ) -> None:
        await self._call(
            persistence.update_next_run,
            self.db_path,
            task_id,
            next_run_ts,
            last_run_ts,
            window_state,
        )

    async def get_system_metrics(self) -> dict[str, Any]:
        return await self._call(_get_system_metrics, self.db_path)

    # -- lifecycle ------------------------------------------------------------

    async def close(self, timeout: float = 5.0) -> bool:
        """
        Stop accepting new work, bound-wait for the one outstanding
        operation (if any) to finish, then shut down without ever
        blocking the event loop.

        Idempotent: safe to call more than once (concurrently or in
        sequence) -- only the first call actually drains/shuts down;
        later calls return the same cached result immediately.

        Returns True if fully drained within `timeout`, False otherwise.
        A False return is never swallowed -- it's logged here and the
        caller (KernelDaemon.stop()) MUST check it before doing anything
        that assumes the underlying DB file is now quiescent (e.g. a
        shutdown-time TRUNCATE checkpoint). On a False return, the
        abandoned thread work may still be running in the background;
        this method does not and cannot forcibly kill it (concurrent
        .futures offers no thread-kill) -- it only stops SchedulerStore
        from waiting on or accepting more work.
        """
        # Set immediately and unconditionally (idempotent by nature --
        # setting True twice is harmless) so _call() starts rejecting new
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
                        "SchedulerStore.close(%s): pending work did not finish within "
                        "%.1fs; abandoning it (the thread may still be running) and "
                        "releasing anything queued behind it",
                        self.db_path,
                        timeout,
                    )
                    # Force the gate open so waiters behind a stuck
                    # operation get their SchedulerStoreClosedError
                    # promptly instead of hanging on a lock that may
                    # never release naturally.
                    if self._gate.locked():
                        self._gate.release()
                except Exception as e:
                    # The operation's own failure isn't close()'s
                    # concern, but don't hide that it happened.
                    log.debug(
                        "SchedulerStore.close(%s): pending operation raised during drain: %s",
                        self.db_path,
                        e,
                    )

            self._executor.shutdown(wait=False, cancel_futures=True)
            self._close_result = drained
            return drained
