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

Since Phase B stage B2, the worker-thread/gate/confirmed-drain mechanism
itself lives in bartholomew/kernel/blocking_executor.py's
SingleWorkerExecutor (storage-agnostic, reused by other blocking callers
-- see docs/B2_EVENT_LOOP_ISOLATION.md); SchedulerStore is now a thin,
persistence.py-specific facade over one SingleWorkerExecutor instance,
not a second independent implementation of the same pattern.

Ownership: whoever constructs a SchedulerStore is responsible for
closing it. KernelDaemon constructs one per instance in __init__ and
closes it in stop(). run_scheduler() uses ctx.scheduler_store when the
caller has provided one (the normal KernelDaemon-driven path) and does
not close it in that case; if ctx has no scheduler_store, run_scheduler()
constructs its own and closes it itself when the loop exits.
"""

from __future__ import annotations

import logging
from typing import Any

from ..blocking_executor import ExecutorClosedError, SingleWorkerExecutor
from . import persistence
from .health import get_system_metrics as _get_system_metrics

log = logging.getLogger(__name__)


class SchedulerStoreClosedError(ExecutorClosedError):
    """Raised when a SchedulerStore operation is attempted after close()
    has been called. This is the one, consistent exception type for that
    condition -- callers should catch this specifically rather than a
    bare Exception/RuntimeError."""


class SchedulerStore:
    """One dedicated worker thread for this instance's entire lifetime.

    Construction is cheap and does no I/O -- the underlying worker
    executor doesn't spawn its worker thread until the first call is
    submitted -- so it's safe to create unconditionally even for a
    KernelDaemon that's never start()ed.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._worker = SingleWorkerExecutor(
            thread_name_prefix="scheduler-db",
            label=db_path,
            closed_exception_cls=SchedulerStoreClosedError,
        )

    @property
    def _closed(self) -> bool:
        """Proxies the delegate's closed flag -- kept as a property (not a
        plain attribute move) so existing introspection (tests, callers)
        checking `store._closed` doesn't need to reach through `._worker`
        to observe this instance's own closed state."""
        return self._worker._closed

    async def _call(self, fn, *args) -> Any:
        return await self._worker.submit(fn, *args)

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
        blocking the event loop. Delegates to SingleWorkerExecutor.close()
        -- see its docstring for the full drain/idempotency contract.

        Returns True if fully drained within `timeout`, False otherwise.
        A False return is never swallowed -- it's logged (by the
        delegate) and the caller (KernelDaemon.stop()) MUST check it
        before doing anything that assumes the underlying DB file is now
        quiescent (e.g. a shutdown-time TRUNCATE checkpoint).
        """
        return await self._worker.close(timeout=timeout)
