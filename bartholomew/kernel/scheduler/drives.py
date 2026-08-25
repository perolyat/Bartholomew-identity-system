"""
Drive functions and registry.

Each drive is a lightweight async function that performs a specific
autonomy task and optionally emits a Nudge.
"""

import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from bartholomew.kernel import schedule_noticing
from bartholomew.kernel.blocking_executor import run_off_loop

from . import containment, persistence
from .health import SELF_CHECK_DRIFT_REASON, check_drift
from .models import Nudge

log = logging.getLogger(__name__)

# Drive function signature
DriveFn = Callable[[Any], Awaitable[Nudge | None]]


async def drive_self_check(ctx: Any) -> Nudge | None:
    """
    Self-check drive: monitor system health and emit nudge if drift.

    Checks:
    - Database accessibility
    - Pending nudges accumulation
    - Stale daily reflections

    WP-A1 / B-F001. This drive's nudge lands in the very queue whose size
    it reports, which in Test #1 made the warning self-sustaining. Two
    things stop that now, neither of which touches this drive's cadence or
    threshold: `check_drift()` counts only pending items this drive did not
    itself create (see scheduler/health.py), and the persisted nudge
    carries a containment equivalence key so repeated executions above the
    threshold cannot add a second unresolved copy (see
    scheduler/containment.py). The condition itself stays reported.

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
            reason=SELF_CHECK_DRIFT_REASON,
            created_ts=int(time.time()),
        )

    return None


async def drive_curiosity_probe(ctx: Any) -> Nudge | None:
    """
    Curiosity probe drive: occasionally prompt reflection or exploration.

    Emits gentle nudges to encourage user engagement with memory/journal.

    WP-A1 / NUDGE-F001. The rotating prompt strings below are presentation,
    not distinct obligations: an unanswered invitation to reflect is one
    open item however it is worded. Persistence therefore keys every
    emission from this drive to a single equivalence key (see
    scheduler/containment.py), so an ordinary repeat firing cannot leave a
    second equivalent unresolved item behind while the first is still open,
    and answering or dismissing the open one frees the key for the next
    genuinely eligible occurrence.

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


SCHEDULE_REMINDER_DRIVE = "schedule_reminder_check"


def _local_today(ctx: Any):
    """The current local date, in the deployment's configured timezone.

    A reminder is a day-granular, human-calendar concept: "due on the 5th"
    means the 5th where the user lives, not the 5th in UTC. `ctx.tz` is the
    tz object KernelDaemon already built from `config/kernel.yaml`'s
    `timezone`; a duck-typed context without one falls back to UTC rather
    than to the host's local time, so a drive's behaviour never depends on
    an unstated machine setting.
    """
    tz = getattr(ctx, "tz", None) or timezone.utc
    return datetime.now(tz).date()


def _classify_delivery(result: Any) -> tuple[str, str | None]:
    """
    Turn one governed NotifySkill result into a delivery claim.

    Usable POC slice 2, approval point 8.4 option (b). Every branch below is
    an observation of what the result actually says -- none of them infers
    delivery from the absence of an error. The default is FAILED, so a result
    shape this function does not recognise is never reported as a delivery.
    """
    if result is None:
        return persistence.DELIVERY_FAILED, "notify returned no result"

    if not getattr(result, "success", False):
        detail = getattr(result, "error", None) or getattr(result, "message", None)
        return persistence.DELIVERY_FAILED, detail or "notify action did not succeed"

    data = getattr(result, "data", None)
    # Quiet hours / mute: NotifySkill queues rather than dropping (S1.3), and
    # returns the queued notification, whose status is "pending". The
    # obligation is intact; delivery has not happened yet, and saying it had
    # would be the exact falsehood 8.4 exists to prevent.
    if isinstance(data, dict) and data.get("status") == "pending":
        return persistence.DELIVERY_DEFERRED, "quiet hours or mute in force; queued for later"

    delivery = (getattr(result, "metadata", None) or {}).get("delivery")
    if not isinstance(delivery, dict):
        # An older/other notify implementation that does not report delivery.
        # Truthful answer: the send succeeded, and nothing here can claim the
        # notification reached anyone.
        return persistence.DELIVERY_SENT_LOCAL_ONLY, "notify reported no delivery outcome"

    if delivery.get("delivered"):
        return persistence.DELIVERY_DELIVERED, None
    if delivery.get("attempted"):
        return persistence.DELIVERY_FAILED, "outbound delivery was attempted and failed"
    return (
        persistence.DELIVERY_SENT_LOCAL_ONLY,
        "no outbound channel configured; the reminder did not leave this machine",
    )


async def _gated_schedule_rows(ctx: Any) -> list[dict[str, Any]]:
    """
    Read the stored date-bearing facts this drive may look at.

    Two existing authorities, reused, neither reimplemented:
      * `MemoryStore.list_memories_by_kind()` -- the single memory authority's
        own read path. Facts held in `pending_sensitive_writes` were never
        written to `memories`, so consent-gated content is structurally out of
        reach here, exactly as slice 1 established.
      * `ConsentGate.filter_memory_ids()` -- the same privacy filter the
        retrieval layer applies, consulted here because this drive decides
        what to put in an outbound notification. A row the gate excludes, or
        marks `context_only` (recallable in context, not to be surfaced), is
        dropped.

    The gate read is synchronous sqlite3, so it goes through `run_off_loop()`
    -- the B2/B8 discipline. A failure raises: this drive must not fall back
    to unfiltered rows, and must not silently report "nothing due" when what
    actually happened is that the privacy filter could not be read.
    """
    mem = getattr(ctx, "mem", None)
    if mem is None:
        return []

    rows = await mem.list_memories_by_kind(list(schedule_noticing.NOTICED_KINDS), limit=500)
    candidates = [
        row for row in rows if schedule_noticing.is_noticeable_row(row.get("kind"), row.get("key"))
    ]
    if not candidates:
        return []

    memory_ids = [row["id"] for row in candidates if row.get("id") is not None]
    if not memory_ids:
        return []

    from bartholomew.kernel.consent_gate import ConsentGate

    db_path = mem.db_path
    executor = getattr(ctx, "blocking_executor", None)

    def _filter():
        return ConsentGate(db_path).filter_memory_ids(memory_ids)

    policy = await run_off_loop(_filter, executor=executor)

    permitted = []
    for row in candidates:
        verdict = policy.get(row.get("id")) or {}
        if verdict.get("include") and not verdict.get("context_only"):
            permitted.append(row)
    return permitted


async def _deliver_reminder(ctx: Any, reminder: Any) -> tuple[str, str | None]:
    """
    Send one reminder through the existing governed notification path, and
    report truthfully what became of it.

    `run_skill_through_runtime_contract(registry, "notify", "send", ...)` is
    byte-for-byte the shape `_notify_fact_captured()` and
    `_notify_awaiting_response()` already use: `SkillRegistry.execute_action()`
    runs its own independent Governance pass (parking brake on the `skills`
    scope, `nudge.create` permission, Identity `tool_use.allowlist` on
    `"notify"`), and `NotifySkill._action_send()` applies S1.3's quiet-hours
    and mute rules. No new notification mechanism, no second Governance path.

    Never raises. Approval point 8.4: a delivery failure is *recorded*, not
    propagated -- a webhook that is down must not be able to destabilise the
    scheduler loop. It is also not retried: the failure record is the remedy
    (see the drive's docstring).
    """
    registry = getattr(ctx, "skill_registry", None)
    if registry is None:
        return persistence.DELIVERY_NOT_ATTEMPTED, "no skill registry on the scheduler context"

    from bartholomew.kernel.runtime_contract import run_skill_through_runtime_contract

    try:
        result = await run_skill_through_runtime_contract(
            registry,
            "notify",
            "send",
            {
                "message": reminder.message,
                "title": reminder.title,
                "priority": "normal",
            },
        )
    except Exception as e:
        log.exception("Schedule reminder delivery raised for %s", reminder.identity)
        return persistence.DELIVERY_FAILED, f"{type(e).__name__}: {e}"

    return _classify_delivery(result)


async def drive_schedule_reminder_check(ctx: Any) -> Nudge | None:
    """
    Proactive schedule reminders (Usable POC slice 2 --
    `docs/POC_SLICE_2_PROACTIVE_REMINDERS.md`).

    Scans stored date-bearing facts for items falling due inside the
    look-ahead window and, for each, surfaces exactly one reminder: a
    WP-A1-contained nudge in the existing queue *and* one governed
    notification through the existing NotifySkill path. That delivery is this
    slice's governed action with a visible real-world result.

    **Registration is conditional and default OFF** (`config/kernel.yaml`'s
    `proactive.schedule_reminders`). When the flag is off this function is
    never registered, so there are no ticks, no queue impact and no behaviour
    change of any kind -- see `resolve_registry()`.

    Deliberately NOT in `_SELF_MAINTENANCE_DRIVES`
    (`bartholomew.kernel.runtime_contract`), following
    `drive_awaiting_response_check`'s recorded precedent exactly: this drive's
    whole purpose is genuine outbound contact about specific user content, not
    kernel-internal housekeeping, so it is evaluated for real by the Identity
    Policy check and `Identity.yaml`'s `tool_use.allowlist` carries its
    task_id.

    Returns None always, like `drive_awaiting_response_check`: a tick can
    produce several reminders, each with its own nudge and its own delivery
    outcome, which the loop's single-Nudge return cannot represent. The nudge
    writes therefore happen here, through the same contained insert
    (`insert_nudge_contained`) the loop uses -- not a second write path.

    What this drive does and does not do on failure:
      * **A delivery failure is recorded on the nudge and never raised**
        (approval point 8.4 option (b)). The nudge row stands, so no
        obligation is lost, and `delivery_status` says the reminder did not
        arrive rather than leaving the queue implying it did. There is
        deliberately **no retry-until-delivered**: §4's after-ack rule would
        suppress it anyway, and an unbounded retry against a permanently
        unreachable webhook is explicitly out of scope. A bounded retry policy
        can be designed later from real-use evidence.
      * **A nudge-persistence failure is loud.** It is logged at ERROR per
        reminder, the remaining reminders are still attempted, and the drive
        then raises so the tick is recorded as a failure. Nothing infers that
        an item which failed to persist was a duplicate or is disposable --
        the WP-A1 requirement E posture, applied here.

    Args:
        ctx: Context object (typically KernelDaemon instance). Needs `mem`,
            `scheduler_store` and, to deliver anything, `skill_registry`. A
            context missing `scheduler_store` is treated as "not wired for
            this yet" and does nothing, matching
            `drive_awaiting_response_check`'s handling of a missing store.

    Returns:
        None -- see above.
    """
    store = getattr(ctx, "scheduler_store", None)
    if store is None or getattr(ctx, "mem", None) is None:
        return None

    today = _local_today(ctx)
    cfg = getattr(ctx, "cfg", None) or {}
    proactive_cfg = cfg.get("proactive") or {}
    look_ahead = int(
        proactive_cfg.get("look_ahead_days", schedule_noticing.DEFAULT_LOOK_AHEAD_DAYS),
    )
    per_tick = int(
        proactive_cfg.get("max_per_tick", schedule_noticing.DEFAULT_MAX_REMINDERS_PER_TICK),
    )

    rows = await _gated_schedule_rows(ctx)
    reminders = schedule_noticing.select_due(
        rows,
        today,
        look_ahead_days=look_ahead,
        limit=per_tick,
    )
    if not reminders:
        return None

    persistence_failures: list[str] = []

    for reminder in reminders:
        # Asked of the containment policy rather than formatted here, so this
        # read and the key `insert_nudge_contained()` will write are computed
        # by the same authority. Hand-building the format would mean a later
        # change to it (it has already grown an escalation suffix once)
        # silently stops the after-ack check from matching -- and "silently
        # stops matching" here means re-reminding the user about things they
        # have already dealt with, which is exactly the nagging Sec 4 exists
        # to prevent.
        dedup_key = containment.dedup_key_for(
            schedule_noticing.REMINDER_KIND,
            reminder.message,
            schedule_noticing.REMINDER_REASON,
            None,
            reminder.identity,
        )
        if dedup_key is None:
            # Only reachable if the reason were removed from the containment
            # allowlist. Unkeyed reminders would accumulate one row per tick,
            # so refuse rather than flood the queue.
            persistence_failures.append(
                f"{reminder.identity}: reason {schedule_noticing.REMINDER_REASON!r} "
                "is not containment-eligible",
            )
            log.error(
                "[Scheduler] Schedule reminder reason %r is not containment-eligible; "
                "no reminder was raised (an unkeyed reminder would accumulate one "
                "unresolved row per tick)",
                schedule_noticing.REMINDER_REASON,
            )
            continue

        # §4 after-ack courtesy: one reminder per (fact, due date), not
        # re-raised once the user has acted on it. Read-only, and safe in the
        # direction it can fail -- see nudge_exists_for_dedup_key().
        try:
            if await store.nudge_exists_for_dedup_key(dedup_key):
                continue
        except Exception as e:
            persistence_failures.append(f"{reminder.identity}: dedup read failed: {e}")
            log.error(
                "[Scheduler] Schedule reminder dedup read FAILED for %s: %s -- "
                "no reminder was raised and none is assumed to exist",
                reminder.identity,
                e,
            )
            continue

        try:
            outcome = await store.insert_nudge_contained(
                schedule_noticing.REMINDER_KIND,
                reminder.message,
                [{"label": "Got it", "cmd": "ack"}, {"label": "Dismiss", "cmd": "dismiss"}],
                schedule_noticing.REMINDER_REASON,
                int(time.time()),
                None,
                reminder.identity,
            )
        except Exception as e:
            persistence_failures.append(f"{reminder.identity}: nudge insert failed: {e}")
            log.error(
                "[Scheduler] Schedule reminder nudge persistence FAILED for %s: "
                "%s -- the reminder was NOT queued and is not assumed to be "
                "represented",
                reminder.identity,
                e,
            )
            continue

        nudge_id = outcome.get("nudge_id")
        if nudge_id is None:
            # Suppressed: an equivalent unresolved reminder provably exists
            # right now (the dedup read above lost a race with another
            # writer). It has already been delivered or has its own recorded
            # delivery outcome; sending a second copy would be the nagging
            # §4 exists to prevent.
            continue

        status, detail = await _deliver_reminder(ctx, reminder)

        try:
            await store.record_nudge_delivery(nudge_id, status, detail, int(time.time()))
        except Exception as e:
            persistence_failures.append(f"{reminder.identity}: delivery record failed: {e}")
            log.error(
                "[Scheduler] Schedule reminder delivery outcome NOT RECORDED for "
                "%s (status=%s): %s -- the queue cannot say whether this "
                "reminder arrived",
                reminder.identity,
                status,
                e,
            )

    if persistence_failures:
        raise RuntimeError(
            "schedule_reminder_check could not durably record "
            f"{len(persistence_failures)} item(s): {'; '.join(persistence_failures)}",
        )

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
}

# Drives that are NOT registered unless an operator has explicitly turned them
# on. Kept out of REGISTRY entirely rather than registered-and-inert, because
# "default OFF" for a proactive outbound capability has to mean zero ticks and
# zero queue impact, not a drive that fires and decides to do nothing.
#
# Note this is a *registration* list, which `config/kernel.yaml`'s `drives:`
# block is not: that block is a cadence-override map (it names 3 of the 5
# always-on drives), so omitting a drive from it changes no registration and
# would disable nothing.
OPTIONAL_REGISTRY: dict[str, dict[str, Any]] = {
    SCHEDULE_REMINDER_DRIVE: {
        "fn": drive_schedule_reminder_check,
        # Coarse on purpose: reminders are day-granular (planning note §7).
        "cadence": "every:3600",  # Hourly
    },
}


def proactive_schedule_reminders_enabled(ctx: Any) -> bool:
    """
    Whether the operator has turned proactive schedule reminders on.

    One authority: `config/kernel.yaml`'s `proactive.schedule_reminders`,
    default `false`. Deliberately no environment-variable override -- a second
    way to switch on a proactive outbound capability is a second authority
    over consent-to-be-proactive, and the point of this flag is that turning
    it on is one deliberate, visible operator act.
    """
    cfg = getattr(ctx, "cfg", None) or {}
    proactive = cfg.get("proactive") or {}
    return bool(proactive.get("schedule_reminders", False))


def resolve_registry(ctx: Any) -> dict[str, dict[str, Any]]:
    """
    The drives this context actually runs: the always-on REGISTRY, plus any
    optional drive whose consent flag is on.

    This is the real registration path -- `scheduler/loop.py` resolves
    cadences from it, upserts scheduled tasks from it, and looks drive
    functions up in it. A drive absent from the returned mapping is never
    scheduled and never executed.
    """
    resolved = dict(REGISTRY)
    if proactive_schedule_reminders_enabled(ctx):
        resolved[SCHEDULE_REMINDER_DRIVE] = OPTIONAL_REGISTRY[SCHEDULE_REMINDER_DRIVE]
    return resolved
