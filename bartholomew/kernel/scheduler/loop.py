"""
Main scheduler loop.

Continuously runs scheduled drives, persists ticks and outputs,
and updates next-run timestamps based on cadence rules.
"""

import asyncio
import logging
import os
import time
from typing import Any

from . import cadence as cadence_module
from . import drives
from .store import SchedulerStore

log = logging.getLogger(__name__)
DRIVE_TIMEOUT = float(os.getenv("BARTH_DRIVE_TIMEOUT", "5.0"))

# Pause between consecutive due-task iterations, so the scheduler's writes
# reach the shared database as a paced stream rather than a solid burst.
#
# Why this exists (RISKS.md, "(2026-08-22) Startup-window governed actions can
# fail with a raw `database is locked`"): a fresh database makes every
# registered drive due at the same instant (persistence.upsert_scheduled_tasks
# seeds next_run_ts = now), and a restart after downtime leaves them all
# overdue. Either way this loop previously ran them back to back with no yield
# at all -- each iteration's nudge, tick and next-run writes following the last
# with no gap. SQLite allows one writer at a time, so a *sustained* stream of
# scheduler write transactions can starve a concurrent user-facing governed
# write for longer than its effective 5s busy timeout, surfacing to the user as
# HTTP 400 `database is locked`. Measured during WP-A2 at 3-4 failures per 1200
# quiet-hours updates, always within the first ~8 requests after process start.
#
# A short pause between iterations leaves a clear window in which a competing
# writer can take the lock well inside its budget. It is deliberately a pacing
# change and nothing more: no next_run_ts is altered, no cadence is rescheduled,
# no drive is skipped, and no work is dropped -- the same drives run, spaced.
# Against cadences measured in minutes to days, the added catch-up latency is
# immaterial.
#
# Deliberately NOT changed here: the effective 5s `PRAGMA busy_timeout` and the
# dead 30s connection parameter (RISKS.md, "(2026-08-22) The effective SQLite
# lock timeout is 5 seconds..."). Taylor retained that behaviour on 2026-08-22
# and that entry records that changing either value is a repository-wide
# behaviour change requiring its own decision.
#
# Set BARTH_DRIVE_PACE_S=0 to disable (tests that drive the loop against a
# virtual clock, or any caller wanting the previous back-to-back behaviour).
DRIVE_PACE_S = max(0.0, float(os.getenv("BARTH_DRIVE_PACE_S", "0.5")))


async def _run_drive(ctx: Any, task_id: str, fn):
    """
    Execute a drive function with timeout and exception handling, routed
    through the Runtime Contract seam (Observation -> Interpretation ->
    Executive -> Governance -> Capability -> Execution -> Reflection ->
    Memory). See `runtime_contract.run_drive_through_runtime_contract()`
    for what each stage means for a scheduler drive specifically -- item
    11.17, closing Exit Gate questions #1-3 for this surface (see
    COGNITIVE_RUNTIME.md's Exit Gate status table).

    Args:
        ctx: Context object (typically KernelDaemon instance)
        task_id: ID of the drive task
        fn: Async function to execute

    Returns:
        Tuple of (nudge_or_none, success_flag)
    """
    from bartholomew.kernel.runtime_contract import run_drive_through_runtime_contract

    return await run_drive_through_runtime_contract(ctx, task_id, fn, timeout=DRIVE_TIMEOUT)


def resolve_cadences(ctx: Any) -> dict:
    """
    Resolve cadence overrides from env > config > registry defaults.

    Iterates `drives.resolve_registry(ctx)`, not `drives.REGISTRY`, so an
    optional drive whose consent flag is off is not merely left at its
    default cadence -- it is absent, and therefore never scheduled at all
    (Usable POC slice 2's default-OFF requirement).

    Note the two `config/kernel.yaml` blocks are different things and are not
    interchangeable: `drives:` is a **cadence-override map** consulted below,
    while registration is decided by `resolve_registry()` from the
    `proactive:` block. Omitting a drive from `drives:` disables nothing.

    Args:
        ctx: Context object (KernelDaemon instance)

    Returns:
        Dict mapping task_id to resolved cadence string
    """
    resolved = {}

    for task_id, config in drives.resolve_registry(ctx).items():
        # Start with registry default
        resolved_cadence = config["cadence"]

        # Check kernel.yaml config for overrides
        if hasattr(ctx, "cfg"):
            cfg_drives = ctx.cfg.get("drives", {})
            if task_id in cfg_drives:
                resolved_cadence = cfg_drives[task_id]

        # Check environment variable overrides
        env_key = f"DRIVE_{task_id.upper()}"
        env_value = os.getenv(env_key)
        if env_value:
            resolved_cadence = env_value

        resolved[task_id] = resolved_cadence

    return resolved


async def run_scheduler(ctx: Any) -> None:
    """
    Main scheduler loop.

    Runs continuously until cancelled, executing drives on their
    cadences and persisting all activity.

    All persistence goes through a SchedulerStore (bartholomew.kernel.
    scheduler.store), which offloads the underlying synchronous sqlite3
    calls onto one dedicated worker thread so this loop never blocks the
    event loop on DB I/O. Ownership: if ctx already has a
    `scheduler_store` (the normal path -- KernelDaemon constructs one in
    __init__ and closes it in stop()), this function uses it and does
    NOT close it. If ctx has none, this function constructs its own and
    closes it itself when the loop exits -- a fallback for callers that
    invoke run_scheduler() directly without a full KernelDaemon lifecycle.

    Args:
        ctx: Context object (typically KernelDaemon instance)
            Must have: mem.db_path, cfg (optional), tz
    """
    store = getattr(ctx, "scheduler_store", None)
    owns_store = store is None
    if owns_store:
        store = SchedulerStore(ctx.mem.db_path)
        ctx.scheduler_store = store

    try:
        # Ensure schema exists. As of S5.0 (issue #24), the normal
        # KernelDaemon-driven path already ensured this synchronously in
        # KernelDaemon.start() before this task was ever created, so here it is
        # an idempotent no-op (CREATE TABLE IF NOT EXISTS + duplicate-column-
        # tolerant). It is retained unconditionally for the standalone path --
        # a caller that runs run_scheduler() against a ctx whose scheduler_store
        # was not pre-ensured (including the owns_store branch above) -- and as
        # defense in depth. It must never be the *first* place the schema is
        # created in the daemon path.
        print("[Scheduler] Initializing schema...")
        await store.ensure_schema()

        # Resolve which drives this context runs at all (always-on plus any
        # optional drive whose consent flag is on), then their cadences.
        # Resolved ONCE here, so one loop iteration cannot see a different
        # registry from the next.
        registry = drives.resolve_registry(ctx)
        resolved_cadences = resolve_cadences(ctx)
        print(f"[Scheduler] Resolved cadences: {resolved_cadences}")

        # Log resolved cadences
        print("[Scheduler] Resolved cadences:")
        for task_id, cadence_str in resolved_cadences.items():
            print(f"  {task_id}: {cadence_str}")

        # Build tasks dict with resolved cadences
        tasks_config = {}
        for task_id in registry:
            tasks_config[task_id] = {"cadence": resolved_cadences[task_id]}

        # Upsert scheduled tasks
        await store.upsert_scheduled_tasks(tasks_config)

        print("[Scheduler] Autonomy loop started")

        # Main loop
        while True:
            try:
                now_ts = int(time.time())

                # Get next due task
                due_task = await store.next_due_task(now_ts)

                if not due_task:
                    # No tasks due, sleep briefly
                    await asyncio.sleep(5)
                    continue

                # Pace the write stream (see DRIVE_PACE_S above). Applied
                # once per due-task iteration, before any of this iteration's
                # writes, so consecutive iterations cannot chain into an
                # unbroken burst -- and so the first drive after startup does
                # not begin writing in the same instant the process does.
                if DRIVE_PACE_S:
                    await asyncio.sleep(DRIVE_PACE_S)

                task_id = due_task["id"]
                scheduled_ts = due_task["next_run_ts"]
                cadence_str = due_task["cadence"]

                # Build idempotency key using scheduled time
                idempotency_key = f"{task_id}:{scheduled_ts}"

                # Check if this tick already exists (restart protection)
                try:
                    if await store.tick_exists(idempotency_key):
                        # Already ran, just update next_run and continue
                        next_ts, new_window_state = cadence_module.compute_next_run(
                            last_run_ts=scheduled_ts,
                            scheduled_ts=scheduled_ts,
                            cadence_str=cadence_str,
                            now_ts=now_ts,
                            window_state=due_task["window_state"],
                        )
                        await store.update_next_run(
                            task_id,
                            next_ts,
                            scheduled_ts,
                            new_window_state,
                        )
                        continue
                except Exception:
                    # If check fails, proceed anyway (idempotency in INSERT)
                    pass

                # A scheduled_tasks row can outlive its registration: an
                # optional drive that was turned on, ran, and was then turned
                # off again leaves its row behind (nothing here deletes
                # scheduler state). Skip it and push its next run forward, so
                # a de-registered drive neither executes nor spins the loop.
                # Deliberately not a deletion: the row is a record of what was
                # scheduled, and turning the flag back on should resume it.
                if task_id not in registry:
                    log.info(
                        "[Scheduler] Skipping %s: scheduled but not registered "
                        "for this context (its consent flag is off)",
                        task_id,
                    )
                    next_ts, new_window_state = cadence_module.compute_next_run(
                        last_run_ts=scheduled_ts,
                        scheduled_ts=scheduled_ts,
                        cadence_str=cadence_str,
                        now_ts=now_ts,
                        window_state=due_task["window_state"],
                    )
                    await store.update_next_run(
                        task_id,
                        next_ts,
                        scheduled_ts,
                        new_window_state,
                    )
                    continue

                # Record tick start
                started_ts = int(time.time())

                # Execute drive with timeout and exception guard
                drive_fn = registry[task_id]["fn"]
                nudge, success = await _run_drive(ctx, task_id, drive_fn)
                result_meta: dict[str, Any] = {}

                # Persist the nudge (if any) BEFORE the tick, so the tick can
                # record truthfully what became of it. WP-A1 requirement E:
                # this used to be wrapped in contextlib.suppress(Exception),
                # which meant a locked or failing database discarded a queued
                # item while the run still reported success -- indistinguish-
                # able from "there was nothing to persist". A dropped
                # obligation nobody can detect is exactly what D2 forbids,
                # and it makes the S1 queue-integrity invariant untestable.
                if nudge:
                    try:
                        outcome = await store.insert_nudge_contained(
                            nudge.kind,
                            nudge.message,
                            nudge.actions,
                            nudge.reason,
                            nudge.created_ts,
                            getattr(nudge, "escalation", None),
                            getattr(nudge, "dedup_identity", None),
                        )
                        result_meta["nudge"] = outcome
                    except Exception as e:
                        # Visible and safe: the tick is recorded as a FAILURE
                        # carrying the error, and the failure is logged at
                        # ERROR. Nothing here infers that the item was a
                        # duplicate, was already represented, or is
                        # disposable -- the only claim made is that
                        # persistence did not happen.
                        success = 0
                        result_meta["nudge"] = {
                            "outcome": "persistence_failed",
                            "kind": nudge.kind,
                            "reason": nudge.reason,
                            "error": f"{type(e).__name__}: {e}",
                        }
                        log.error(
                            "[Scheduler] Nudge persistence FAILED for %s "
                            "(kind=%s reason=%s): %s -- the emitted item was "
                            "NOT queued and is not assumed to be represented",
                            task_id,
                            nudge.kind,
                            nudge.reason,
                            e,
                        )
                        print(f"[Scheduler] Nudge persistence FAILED for {task_id}: {e}")

                finished_ts = int(time.time())
                dur_ms = (finished_ts - started_ts) * 1000

                # Persist tick
                try:
                    await store.insert_tick(
                        task_id,
                        started_ts,
                        finished_ts,
                        success,
                        idempotency_key,
                        result_meta,
                    )
                except Exception as e:
                    # If insert fails due to duplicate key, that's OK
                    if "unique" not in str(e).lower():
                        print(f"[Scheduler] Error inserting tick for {task_id}: {e}")

                # Compute next run time
                next_ts, new_window_state = cadence_module.compute_next_run(
                    last_run_ts=scheduled_ts,
                    scheduled_ts=scheduled_ts,
                    cadence_str=cadence_str,
                    now_ts=now_ts,
                    window_state=due_task["window_state"],
                )

                # Update scheduled task
                await store.update_next_run(task_id, next_ts, scheduled_ts, new_window_state)

                # Log tick execution
                print(f"[Scheduler] tick={task_id} ok={success} dur_ms={dur_ms} next={next_ts}")

            except asyncio.CancelledError:
                print("[Scheduler] Shutdown requested")
                break
            except Exception as e:
                print(f"[Scheduler] Unexpected error in loop: {e}")
                await asyncio.sleep(5)

        print("[Scheduler] Autonomy loop stopped")
    finally:
        if owns_store:
            await store.close()
