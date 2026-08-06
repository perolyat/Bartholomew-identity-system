"""
Drive functions and registry.

Each drive is a lightweight async function that performs a specific
autonomy task and optionally emits a Nudge.
"""

import time
from collections.abc import Awaitable, Callable
from typing import Any

from bartholomew.kernel.blocking_executor import run_off_loop

from .health import check_drift
from .models import Nudge

# Drive function signature
DriveFn = Callable[[Any], Awaitable[Nudge | None]]


async def drive_self_check(ctx: Any) -> Nudge | None:
    """
    Self-check drive: monitor system health and emit nudge if drift.

    Checks:
    - Database accessibility
    - Pending nudges accumulation
    - Stale daily reflections

    Args:
        ctx: Context object (typically KernelDaemon instance). Must have
            a scheduler_store (bartholomew.kernel.scheduler.store.
            SchedulerStore) -- no synchronous fallback is used here on
            purpose: a missing scheduler_store should fail loudly rather
            than silently reverting to a blocking sqlite3 call on the
            event loop.

    Returns:
        Nudge if system drift detected, None otherwise
    """
    metrics = await ctx.scheduler_store.get_system_metrics()
    drift = check_drift(metrics)

    if drift:
        return Nudge(
            kind="system_health",
            message=f"System drift detected: {drift}",
            actions=[],
            reason="self_check_drift",
            created_ts=int(time.time()),
        )

    return None


async def drive_curiosity_probe(ctx: Any) -> Nudge | None:
    """
    Curiosity probe drive: occasionally prompt reflection or exploration.

    Emits gentle nudges to encourage user engagement with memory/journal.

    Args:
        ctx: Context object (typically KernelDaemon instance)

    Returns:
        Nudge with curiosity prompt, or None
    """
    # Simple curiosity nudge every so often
    # In a more advanced implementation, could analyze recent activity
    # and tailor the question

    prompts = [
        "What's one thing you learned today?",
        "How are you feeling right now?",
        "Any highlights from today worth remembering?",
    ]

    # For now, just cycle through prompts based on time
    prompt_idx = (int(time.time()) // 3600) % len(prompts)

    return Nudge(
        kind="curiosity",
        message=prompts[prompt_idx],
        actions=[{"label": "Reflect", "cmd": "open_journal"}, {"label": "Later", "cmd": "dismiss"}],
        reason="curiosity_probe",
        created_ts=int(time.time()),
    )


async def drive_reflection_micro(ctx: Any) -> Nudge | None:
    """
    Micro-reflection drive: insert small reflective moments.

    Creates lightweight reflection entries to track system state over time.
    Does not emit nudges by default.

    Args:
        ctx: Context object (typically KernelDaemon instance). Must have
            a scheduler_store -- see drive_self_check()'s docstring for
            why there's no synchronous fallback.

    Returns:
        None (reflections are inserted directly, no nudge needed)
    """
    # Get system metrics for reflection content
    metrics = await ctx.scheduler_store.get_system_metrics()

    # Build micro-reflection content
    content = f"""# Micro-Reflection

System health snapshot:
- Database: {"OK" if metrics["db_ok"] else "Error"}
- Pending nudges: {metrics["pending_nudges"]}
- Last daily reflection: {metrics["last_daily_reflection_ts"] or "None"}

Status: Autonomy loop active
"""

    # Insert reflection via MemoryStore
    try:
        await ctx.mem.insert_reflection(
            kind="micro_reflection",
            content=content,
            meta=metrics,
            ts=str(int(time.time())),  # MemoryStore expects string
            pinned=False,
        )
    except Exception as e:
        print(f"[Scheduler] Error inserting micro-reflection: {e}")

    # No nudge emitted for micro-reflections
    return None


async def drive_fts_optimize(ctx: Any) -> Nudge | None:
    """
    FTS optimize drive: run weekly FTS index optimization.

    Runs INSERT INTO memory_fts(memory_fts) VALUES('optimize') to merge
    FTS segments and reduce fragmentation for better search performance.

    Args:
        ctx: Context object (typically KernelDaemon instance)

    Returns:
        None (optimization runs silently)
    """
    db_path = ctx.mem.db_path

    try:
        from bartholomew.kernel.fts_client import FTSClient

        fts = FTSClient(db_path)
        fts.optimize()
        print("[Scheduler] FTS index optimized")
    except Exception as e:
        print(f"[Scheduler] Error optimizing FTS index: {e}")

    # No nudge emitted for maintenance tasks
    return None


async def drive_awaiting_response_check(ctx: Any) -> Nudge | None:
    """
    Awaiting-response check drive (Stage 1, S1.4; see
    docs/S1_4_AWAITING_RESPONSE_DESIGN.md Sec 6): scan the
    awaiting_response obligation queue for entries due their next
    reminder/escalation, and drive each through the governed
    awaiting_response seam individually -- a denial/failure on one entry
    must not affect any other (mirrors NotifySkill._process_queue()'s own
    per-notification loop).

    Deliberately NOT in _SELF_MAINTENANCE_DRIVES
    (bartholomew.kernel.runtime_contract): unlike self_check/
    curiosity_probe/reflection_micro/fts_optimize, this drive's whole
    purpose is triggering genuine outbound contact (a reminder/escalation
    notification) about specific user content, not kernel-internal
    housekeeping -- it is evaluated for real by the Identity Policy check
    the same as the per-entry remind/escalate/resolve transitions it
    dispatches, and Identity.yaml's tool_use.allowlist has been extended
    with this task_id accordingly (governance.change_control-flagged, per
    the design doc's Sec 5 precedent).

    Args:
        ctx: Context object (typically KernelDaemon instance). Must have an
            awaiting_response_store
            (bartholomew.kernel.awaiting_response_store
            .AwaitingResponseStore) -- a missing store (pre-S1.4-wiring
            callers, e.g. existing scheduler tests) is treated as "nothing
            to scan yet", not an error.

    Returns:
        None always -- delivery for a due entry happens inside the
        per-entry seam call (via NotifySkill), not via a scheduler Nudge.
    """
    store = getattr(ctx, "awaiting_response_store", None)
    if store is None:
        return None

    from bartholomew.kernel.runtime_contract import (
        run_awaiting_response_through_runtime_contract,
    )

    now_ts = int(time.time())
    executor = getattr(ctx, "blocking_executor", None)
    due_entries = await run_off_loop(store.list_due_for_transition, now_ts, executor=executor)

    for entry in due_entries:
        transition = store.next_transition_for(entry, now_ts)
        try:
            await run_awaiting_response_through_runtime_contract(
                ctx,
                transition,
                entry_id=entry.id,
                actor="scheduler:awaiting_response_check",
            )
        except Exception as e:
            print(f"[Scheduler] Error advancing awaiting_response entry {entry.id}: {e}")

    return None


async def drive_initiative_sweep(ctx: Any) -> Nudge | None:
    """
    Initiative sweep drive (Stage 5, S5.2; see
    docs/S5_2_TYPED_CADENCE_DESIGN.md Sec 6): scan the Initiative store for
    non-terminal initiatives whose expires_at has passed, and drive each
    through the governed `expire` transition individually -- a denial/
    failure on one entry must not affect any other (mirrors
    `drive_awaiting_response_check`'s own per-entry loop).

    This is the first production code exercising `initiative_store.py` /
    `run_initiative_through_runtime_contract()`, but only the `expire`
    transition -- `propose`/`defer`/`deliver`/`resolve`/`cancel`/
    `supersede` remain untested against a real drive until S5.7 designs
    one (design doc Sec 6's own honest scope note).

    Deliberately IS in _SELF_MAINTENANCE_DRIVES
    (bartholomew.kernel.runtime_contract): unlike `awaiting_response_check`,
    this drive never proposes new outbound contact, only closes out rows
    already past their TTL. The `expire` transition it dispatches has its
    own, separate exemption from Identity Policy and consent inside
    run_initiative_through_runtime_contract()
    (_SELF_MAINTENANCE_INITIATIVE_TRANSITIONS) -- design doc Sec 7,
    approved by the project owner 2026-08-06.

    Args:
        ctx: Context object (typically KernelDaemon instance). Must have an
            initiative_store (bartholomew.kernel.initiative_store.
            InitiativeStore) -- a missing store (pre-S5.2-wiring callers,
            e.g. existing scheduler tests) is treated as "nothing to sweep
            yet", not an error.

    Returns:
        None always -- expiry has no user-facing delivery of its own.
    """
    store = getattr(ctx, "initiative_store", None)
    if store is None:
        return None

    from bartholomew.kernel.runtime_contract import run_initiative_through_runtime_contract

    now_ts = int(time.time())
    executor = getattr(ctx, "blocking_executor", None)
    expiring = await run_off_loop(store.list_expiring, now_ts, executor=executor)

    for initiative in expiring:
        try:
            await run_initiative_through_runtime_contract(
                ctx,
                "expire",
                initiative_id=initiative.id,
                actor="scheduler:initiative_sweep",
            )
        except Exception as e:
            print(f"[Scheduler] Error expiring initiative {initiative.id}: {e}")

    return None


# Drive registry with default cadences
REGISTRY: dict[str, dict[str, Any]] = {
    "self_check": {
        "fn": drive_self_check,
        "cadence": "every:900",  # Every 15 minutes
    },
    "curiosity_probe": {
        "fn": drive_curiosity_probe,
        "cadence": "window:3600:2",  # 2 times per hour
    },
    "reflection_micro": {
        "fn": drive_reflection_micro,
        "cadence": "every:7200",  # Every 2 hours
    },
    "fts_optimize": {
        "fn": drive_fts_optimize,
        "cadence": "every:604800",  # Every 7 days (weekly)
    },
    "awaiting_response_check": {
        "fn": drive_awaiting_response_check,
        "cadence": "every:900",  # Every 15 minutes (design doc Sec 6)
    },
    "initiative_sweep": {
        "fn": drive_initiative_sweep,
        "cadence": "every:900",  # Every 15 minutes -- matches awaiting_response_check
    },
}
