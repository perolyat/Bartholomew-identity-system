"""
Drive functions and registry.

Each drive is a lightweight async function that performs a specific
autonomy task and optionally emits a Nudge.
"""

import time
import uuid
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


def summarize_batch(initiatives: list[Any]) -> tuple[str, str]:
    """Deterministic grouped-by-category digest for a coalesced delivery
    (S5.4 design doc Sec 5) -- grouping rather than a flat concatenation of
    every rationale in arrival order, so a burst of unrelated initiatives
    reads as one organized update instead of a noisy list. Isolated on
    purpose: a smarter/LLM-based summarizer can replace this function's
    body later without any change to the drive's control flow. Not called
    for a batch of size 1 -- that path keeps today's single-item message.
    """
    by_category: dict[str, list[Any]] = {}
    for initiative in initiatives:
        by_category.setdefault(initiative.category, []).append(initiative)

    category_counts = ", ".join(
        f"{len(items)} {category}" for category, items in sorted(by_category.items())
    )
    title = f"Bartholomew: {len(initiatives)} updates ({category_counts})"

    lines: list[str] = []
    for category, items in sorted(by_category.items()):
        lines.append(f"{category.replace('_', ' ').title()} ({len(items)}):")
        lines.extend(f"- {initiative.rationale}" for initiative in items)
    message = "\n".join(lines)
    return title, message


async def drive_initiative_delivery_check(ctx: Any) -> Nudge | None:
    """
    Initiative delivery-eligibility-check drive (Stage 5, S5.3/S5.4; see
    docs/S5_3_DEFAULT_OFF_CONSENT_AND_MUTE_DESIGN.md Sec 4 and
    docs/S5_4_QUIET_HOURS_DEFER_DESIGN.md): the drive S5.2's own design doc
    (Sec 11 item 5) named as still missing -- without it, an `approved`
    initiative had no path to `delivered` in production at all. Scans the
    Initiative store for initiatives in `approved`/`deferred`/`snoozed`
    status whose `due_at` has passed (or is unset, treated as immediately
    due), and for each, in order:

    1. Consent revoked since approval (`is_category_consented()` now
       False) -> `cancel`. A stronger, more deliberate signal than a
       mute -- matches `cancel`'s existing S5.1 semantics ("the condition
       that motivated it no longer holds"), approved by the project owner
       2026-08-06. Never bypassed by `delivery_policy` (S5.4 design doc
       Sec 2's invariant).
    2. Category currently muted (`is_category_muted()`) -> `defer` with
       `reason="muted"`. Temporary and re-checked on a later tick, once
       the mute is lifted -- distinct from consent revocation. Also never
       bypassed by `delivery_policy`.
    3. `delivery_policy in ("immediate", "critical_override")` -> `deliver`
       right away, bypassing the suppression-policy registry entirely, and
       forcing NotifySkill's own `priority="urgent"` so NotifySkill's
       independent quiet-hours/mute gate can't silently queue it either
       (S5.4 design doc Sec 3).
    4. Otherwise, check `notification_suppression.active_suppression_
       reason()` (quiet hours, NotifySkill's own manual mute, and any
       future policy registered there):
       - Active and `delivery_policy != "silent"` -> `defer` with
         `reason=<policy name>`.
       - Active and `delivery_policy == "silent"` -> `deliver` right away
         with `notify_overrides={"sound": False}`.
       - Not active -> collected as a coalescing candidate (below), not
         delivered inside this loop iteration.

    After the loop, every collected `standard`-policy candidate is
    delivered: individually (unchanged, pre-S5.4 behaviour) if there's
    exactly one, or as a single coalesced digest (S5.4 design doc Sec 5)
    via `summarize_batch()` if there's more than one -- each initiative
    still transitions to `delivered` individually
    (`suppress_notification=True` skips only its own auto-notify), mark-
    then-notify order, and only one `notify.send` covers the whole batch.

    One entry's failure never affects another's (mirrors
    `drive_initiative_sweep`'s and `drive_awaiting_response_check`'s own
    per-entry try/except loops); the same isolation applies to the
    post-loop per-batch-member delivery pass.

    Deliberately IS in _SELF_MAINTENANCE_DRIVES at the drive-tick level
    (bartholomew.kernel.runtime_contract) -- deciding *whether to check*
    delivery eligibility is not itself outbound contact, the same
    reasoning `initiative_sweep`'s own drive-tick exemption uses. This is
    NOT the same question as whether the `deliver` transition it
    dispatches is exempt from Governance -- it is not, and stays fully
    gated (only `expire`/`cancel` have that exemption, and only at the
    transition level, per _SELF_MAINTENANCE_INITIATIVE_TRANSITIONS).

    Args:
        ctx: Context object (typically KernelDaemon instance). Must have an
            initiative_store (bartholomew.kernel.initiative_store.
            InitiativeStore) -- a missing store (pre-S5.3-wiring callers,
            e.g. existing scheduler tests) is treated as "nothing to check
            yet", not an error.

    Returns:
        None always -- delivery for a due entry happens inside the
        per-entry seam call(s), not via a scheduler Nudge.
    """
    store = getattr(ctx, "initiative_store", None)
    if store is None:
        return None

    from bartholomew.kernel.notification_suppression import active_suppression_reason
    from bartholomew.kernel.runtime_contract import (
        run_initiative_through_runtime_contract,
        run_skill_through_runtime_contract,
    )

    now_ts = int(time.time())
    executor = getattr(ctx, "blocking_executor", None)
    due = await run_off_loop(store.list_due_for_delivery, now_ts, executor=executor)

    suppression_cache: dict[str, Any] = {}
    to_coalesce: list[Any] = []

    for initiative in due:
        try:
            consented = await run_off_loop(
                store.is_category_consented,
                initiative.category,
                executor=executor,
            )
            if not consented:
                await run_initiative_through_runtime_contract(
                    ctx,
                    "cancel",
                    initiative_id=initiative.id,
                    actor="scheduler:initiative_delivery_check",
                )
                continue

            muted = await run_off_loop(
                store.is_category_muted,
                initiative.category,
                executor=executor,
            )
            if muted:
                await run_initiative_through_runtime_contract(
                    ctx,
                    "defer",
                    initiative_id=initiative.id,
                    reason="muted",
                    actor="scheduler:initiative_delivery_check",
                )
                continue

            if initiative.delivery_policy in ("immediate", "critical_override"):
                await run_initiative_through_runtime_contract(
                    ctx,
                    "deliver",
                    initiative_id=initiative.id,
                    actor="scheduler:initiative_delivery_check",
                    notify_overrides={"priority": "urgent"},
                )
                continue

            reason = await active_suppression_reason(ctx, suppression_cache)
            if reason and initiative.delivery_policy != "silent":
                await run_initiative_through_runtime_contract(
                    ctx,
                    "defer",
                    initiative_id=initiative.id,
                    reason=reason,
                    actor="scheduler:initiative_delivery_check",
                )
                continue

            if reason and initiative.delivery_policy == "silent":
                await run_initiative_through_runtime_contract(
                    ctx,
                    "deliver",
                    initiative_id=initiative.id,
                    actor="scheduler:initiative_delivery_check",
                    notify_overrides={"sound": False},
                )
                continue

            to_coalesce.append(initiative)
        except Exception as e:
            print(f"[Scheduler] Error checking delivery for initiative {initiative.id}: {e}")

    if not to_coalesce:
        return None

    if len(to_coalesce) == 1:
        initiative = to_coalesce[0]
        try:
            await run_initiative_through_runtime_contract(
                ctx,
                "deliver",
                initiative_id=initiative.id,
                actor="scheduler:initiative_delivery_check",
            )
        except Exception as e:
            print(f"[Scheduler] Error delivering initiative {initiative.id}: {e}")
        return None

    # Coalesced path (S5.4 design doc Sec 5/9): mark every batch member
    # delivered first, notify once after -- minimizes duplicate-send risk
    # (a failure between the two steps leaves an initiative delivered-but-
    # unseen, never seen twice) and matches _deliver_initiative_
    # notification's existing best-effort contract.
    batch_id = str(uuid.uuid4())
    delivered = []
    for initiative in to_coalesce:
        try:
            await run_initiative_through_runtime_contract(
                ctx,
                "deliver",
                initiative_id=initiative.id,
                actor="scheduler:initiative_delivery_check",
                suppress_notification=True,
                coalesced=True,
                batch_id=batch_id,
                batch_size=len(to_coalesce),
            )
            delivered.append(initiative)
        except Exception as e:
            print(
                f"[Scheduler] Error delivering initiative {initiative.id} in batch {batch_id}: {e}"
            )

    if delivered and getattr(ctx, "skill_registry", None) is not None:
        title, message = summarize_batch(delivered)
        try:
            await run_skill_through_runtime_contract(
                ctx.skill_registry,
                "notify",
                "send",
                {"message": message, "title": title, "priority": "normal"},
            )
        except Exception as e:
            print(f"[Scheduler] Error sending coalesced notification for batch {batch_id}: {e}")

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
    "initiative_delivery_check": {
        "fn": drive_initiative_delivery_check,
        "cadence": "every:900",  # Every 15 minutes -- matches initiative_sweep (S5.3)
    },
}
