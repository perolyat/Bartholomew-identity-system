"""
Drive functions and registry.

Each drive is a lightweight async function that performs a specific
autonomy task and optionally emits a Nudge.
"""

import calendar
import functools
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timezone
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
    # The same timezone `_local_today()` uses, handed to the noticing so a
    # relative form resolves against the local date the fact was captured on
    # rather than against its UTC date. The two must not drift apart: a
    # reminder anchored in one zone and windowed in another is off by a day
    # for part of every day.
    tz = getattr(ctx, "tz", None) or timezone.utc
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
        tz=tz,
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


OBJECTIVE_CONTINUITY_DRIVE = "objective_continuity_check"

#: Kind and reason for the nudges this drive raises.
OBJECTIVE_NUDGE_KIND = "objective"
OBJECTIVE_NUDGE_REASON = "objective_reengagement"

#: How long an objective stays quiet after being surfaced before it may be
#: raised again. Scaffolding tuned from real use, not a frozen boundary --
#: the same posture the schedule-reminder constants carry. Deliberately
#: generous: the failure this slice exists to remove is Bartholomew adding
#: burden, and an assistant that raises the same objective daily is adding
#: burden however good its record-keeping is.
DEFAULT_QUIET_INTERVAL_S = 3 * 24 * 3600

#: How near a horizon has to be before an objective is raised regardless of
#: the quiet interval.
DEFAULT_HORIZON_WINDOW_DAYS = 3

#: Most objectives raised in one tick.
DEFAULT_MAX_OBJECTIVES_PER_TICK = 2


def _objective_is_due(
    objective: Any,
    now_ts: int,
    today,
    *,
    quiet_s: int,
    window_days: int,
) -> bool:
    """Whether this objective has earned a re-engagement right now.

    Two independent reasons, and nothing else:

      1. its horizon is inside the look-ahead window -- the deadline is
         approaching and the user would want to know;
      2. it has gone quiet for longer than the quiet interval -- nobody has
         mentioned it and it is at risk of being forgotten, which is the
         thing Bartholomew is supposed to prevent.

    An objective never surfaced at all is due on the second ground, measured
    from when it was opened: a brand-new objective is not raised back at the
    user in the same breath they established it.

    Note what is absent: there is no "surface it because we have new
    evidence" ground. Evidence arriving is not by itself a reason to
    interrupt someone.
    """
    from bartholomew.kernel import objective_store

    horizon_kind = getattr(objective, "horizon_kind", None)
    horizon_date = getattr(objective, "horizon_date", None)
    if horizon_kind == objective_store.HORIZON_BY_DATE and horizon_date:
        try:
            due = date.fromisoformat(horizon_date)
        except ValueError:
            due = None
        if due is not None and (due - today).days <= window_days:
            return True

    anchor = getattr(objective, "last_surfaced_at", None) or getattr(objective, "opened_at", None)
    anchor_ts = _parse_iso_ts(anchor)
    if anchor_ts is None:
        # No usable timestamp: not raised. Without an anchor there is no
        # honest answer to "has this gone quiet", and guessing produces
        # exactly the unpredictable interruptions this drive must not make.
        return False
    return (now_ts - anchor_ts) >= quiet_s


def _parse_iso_ts(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return calendar.timegm(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, TypeError):
        return None


async def _deliver_objective_reengagement(ctx: Any, title: str, message: str):
    """Send one re-engagement through the existing governed notification path.

    Byte-for-byte the shape `_deliver_reminder()` uses, and for the same
    reason: `run_skill_through_runtime_contract(registry, "notify", "send")`
    runs `SkillRegistry.execute_action()`'s own independent Governance pass
    and `NotifySkill`'s quiet-hours and mute rules. No second notification
    mechanism exists, and this slice does not add one.

    Never raises: a delivery failure is recorded, not propagated.
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
            {"message": message, "title": title, "priority": "normal"},
        )
    except Exception as e:
        log.exception("Objective re-engagement delivery raised for %s", title)
        return persistence.DELIVERY_FAILED, f"{type(e).__name__}: {e}"

    return _classify_delivery(result)


async def drive_objective_continuity_check(ctx: Any) -> Nudge | None:
    """
    Proactive objective re-engagement (Golden Path slice 2).

    Scans the objectives Bartholomew is still carrying and, for each one that
    has earned it, surfaces exactly one re-engagement: a WP-A1-contained
    nudge in the existing queue *and* one governed notification through the
    existing NotifySkill path -- carrying the continuity summary derived from
    that objective's own events, so the user is told what has changed rather
    than being asked to reconstruct it.

    **This drive records and reports. It advances nothing.** It contacts
    nobody except the user, through the one notification path that already
    existed. An objective saying "get the roof repaired" does not authorise
    ringing a roofer, and there is no code path here by which it could.

    **Registration is conditional and default OFF** (`config/kernel.yaml`'s
    `proactive.objective_continuity`). When the flag is off this function is
    never registered, so there are no ticks, no queue impact and no behaviour
    change of any kind -- see `resolve_registry()`.

    **A terminal objective is unreachable from here.** `list_live()` cannot
    return one. That is the third of the three independent stops behind the
    promise that a completed objective goes quiet permanently -- the others
    being the store's terminal-transition refusal and the chat
    interpretation block's live-only listing. Three, because a completed
    objective that keeps resurfacing is the worst outcome this slice can
    produce, and one filter that someone later forgets is not enough.

    Returns None always, like the two drives it follows: a tick can produce
    several re-engagements, each with its own nudge and delivery outcome,
    which the loop's single-Nudge return cannot represent.

    Failure posture, matching `drive_schedule_reminder_check` exactly: a
    delivery failure is recorded on the nudge and never raised; a
    nudge-persistence failure is logged per objective, the remaining
    objectives are still attempted, and the drive then raises so the tick is
    recorded as a failure. Nothing infers that an item which failed to
    persist is disposable.
    """
    from bartholomew.kernel import objective_intents
    from bartholomew.kernel.runtime_contract import (
        evaluate_objective_admission,
        run_objective_through_runtime_contract,
    )

    store = getattr(ctx, "objective_store", None)
    scheduler_store = getattr(ctx, "scheduler_store", None)
    if store is None or scheduler_store is None:
        return None

    cfg = getattr(ctx, "cfg", None) or {}
    proactive_cfg = cfg.get("proactive") or {}
    quiet_s = int(proactive_cfg.get("objective_quiet_interval_s", DEFAULT_QUIET_INTERVAL_S))
    window_days = int(
        proactive_cfg.get("objective_horizon_window_days", DEFAULT_HORIZON_WINDOW_DAYS),
    )
    per_tick = int(
        proactive_cfg.get("objective_max_per_tick", DEFAULT_MAX_OBJECTIVES_PER_TICK),
    )

    executor = getattr(ctx, "blocking_executor", None)
    objectives = await run_off_loop(store.list_live, executor=executor)
    now_ts = int(time.time())
    today = _local_today(ctx)

    due = [
        objective
        for objective in objectives
        if _objective_is_due(
            objective,
            now_ts,
            today,
            quiet_s=quiet_s,
            window_days=window_days,
        )
    ][:per_tick]
    if not due:
        return None

    persistence_failures: list[str] = []

    for objective in due:
        # The continuity summary, derived from this objective's own events
        # since it was last raised. Never stored: a kept summary is a
        # fabrication the moment the events move on.
        try:
            events = await run_off_loop(
                functools.partial(
                    store.evidence_events,
                    objective.id,
                    after_event_id=objective.last_surfaced_event_id,
                ),
                executor=executor,
            )
        except Exception as e:
            persistence_failures.append(f"objective {objective.id}: history read failed: {e}")
            log.error(
                "[Scheduler] Objective %s history read FAILED: %s -- not raised, "
                "and no continuity is assumed",
                objective.id,
                e,
            )
            continue

        message = objective_intents.render_reengagement(objective, events)
        title = objective_intents.reengagement_title(objective)

        # Identity is the objective plus the *round* -- the surfacing it
        # follows -- supplied explicitly, because the message is a rendering
        # of a history that changes and keying on it would let one objective
        # occupy unbounded queue slots.
        #
        # The round matters, and this is where an objective differs from a
        # schedule reminder. A reminder's obligation is "(this fact, this due
        # date)": inherently one-shot, so `nudge_exists_for_dedup_key()`'s
        # any-row-ever semantics are exactly right for it. An objective is
        # not one-shot -- re-engaging after the quiet interval is the entire
        # point of the drive -- so a fixed per-objective key would silently
        # mean Bartholomew raised each objective exactly once, ever, and then
        # went quiet on work that was still live.
        #
        # Keying on the last surfacing gives both properties: two ticks
        # inside one round collapse to a single unresolved item (containment
        # intact, NUDGE-F001 prevented), while the next round -- reached only
        # by actually surfacing, which advances the id -- is a genuinely new
        # obligation.
        round_marker = objective.last_surfaced_event_id or "first"
        identity = f"objective:{objective.id}:{round_marker}"
        dedup_key = containment.dedup_key_for(
            OBJECTIVE_NUDGE_KIND,
            message,
            OBJECTIVE_NUDGE_REASON,
            None,
            identity,
        )
        if dedup_key is None:
            persistence_failures.append(
                f"objective {objective.id}: reason {OBJECTIVE_NUDGE_REASON!r} "
                "is not containment-eligible",
            )
            log.error(
                "[Scheduler] Objective re-engagement reason %r is not "
                "containment-eligible; nothing was raised",
                OBJECTIVE_NUDGE_REASON,
            )
            continue

        try:
            if await scheduler_store.nudge_exists_for_dedup_key(dedup_key):
                continue
        except Exception as e:
            persistence_failures.append(f"objective {objective.id}: dedup read failed: {e}")
            log.error(
                "[Scheduler] Objective re-engagement dedup read FAILED for %s: %s -- "
                "nothing was raised and none is assumed to exist",
                objective.id,
                e,
            )
            continue

        # Governance BEFORE any mutation of our own.
        #
        # The nudge insert below is a governed write on the user's queue, and
        # the delivery after it is outbound contact. Neither may happen
        # unless the `objective_surface` candidate action is admitted, so
        # admission is evaluated here, first, through the one shared
        # authority `run_objective_through_runtime_contract()` itself uses
        # (`evaluate_objective_admission`) -- consulted twice, never
        # reimplemented, so the two cannot drift apart.
        #
        # This read is not a second Governance decision and does not weaken
        # the first: the seam re-evaluates it at the moment it writes, so the
        # mutation is still gated at its own execution boundary. Evaluating
        # admission without mutating is inspection, which
        # `DECISIONS.md`'s "inspect, but do not mutate" explicitly permits.
        admission = await evaluate_objective_admission(ctx, "objective_surface")
        if not admission.allowed:
            # Refused. Nothing is written, nothing is queued, nothing is
            # sent -- there is no partial artefact of a re-engagement that
            # governance did not permit.
            log.info(
                "[Scheduler] Objective %s re-engagement not admitted: %s",
                objective.id,
                admission.reason,
            )
            continue

        try:
            outcome = await scheduler_store.insert_nudge_contained(
                OBJECTIVE_NUDGE_KIND,
                message,
                [{"label": "Still going", "cmd": "ack"}, {"label": "Dismiss", "cmd": "dismiss"}],
                OBJECTIVE_NUDGE_REASON,
                now_ts,
                None,
                identity,
            )
        except Exception as e:
            persistence_failures.append(f"objective {objective.id}: nudge insert failed: {e}")
            log.error(
                "[Scheduler] Objective re-engagement nudge persistence FAILED for %s: "
                "%s -- nothing was raised, the objective is NOT recorded as surfaced, "
                "and it stays due so the next tick can try again",
                objective.id,
                e,
            )
            continue

        nudge_id = outcome.get("nudge_id")
        if nudge_id is None:
            # Suppressed: an equivalent unresolved re-engagement provably
            # exists. A second copy is the nagging containment prevents.
            continue

        status, detail = await _deliver_objective_reengagement(ctx, title, message)

        try:
            await scheduler_store.record_nudge_delivery(nudge_id, status, detail, now_ts)
        except Exception as e:
            persistence_failures.append(f"objective {objective.id}: delivery record failed: {e}")
            log.error(
                "[Scheduler] Objective re-engagement delivery outcome NOT RECORDED "
                "for %s (status=%s): %s",
                objective.id,
                status,
                e,
            )

        # Mark it surfaced LAST, and only now.
        #
        # `surfaced` means "this was actually put in front of the user", and
        # the queued nudge is what makes that true -- it is visible in the
        # UI whether or not the outbound notification also got through, which
        # is why a delivery failure (recorded on the nudge above) still
        # counts as surfaced while a failed nudge insert does not.
        #
        # Ordering it last is what keeps that honest. Marking it first, as
        # this originally did, meant a failed insert left an objective
        # claiming it had been raised when the user had seen nothing -- and
        # because the re-engagement window advances on surfacing, it would
        # then have gone quiet on an obligation that was never delivered.
        # Failing before this point instead leaves the objective untouched
        # and still due, so the next tick retries it.
        try:
            surfaced = await run_objective_through_runtime_contract(
                ctx,
                "surface",
                objective_id=objective.id,
                actor="scheduler:objective_continuity",
            )
        except Exception as e:
            persistence_failures.append(f"objective {objective.id}: surface failed: {e}")
            log.error(
                "[Scheduler] Objective %s was raised with the user but could NOT be "
                "recorded as surfaced: %s -- the queue holds the nudge; the objective "
                "is not assumed to have been raised",
                objective.id,
                e,
            )
            continue

        if not surfaced.governance_allowed:
            # The brake was engaged, or policy changed, between admission and
            # this write. The seam refused, which is correct and fail-closed;
            # record it rather than letting the objective silently look
            # raised.
            persistence_failures.append(
                f"objective {objective.id}: surface refused by governance after "
                f"admission: {surfaced.reason}",
            )
            log.error(
                "[Scheduler] Objective %s surface refused at the write boundary: %s",
                objective.id,
                surfaced.reason,
            )

    if persistence_failures:
        raise RuntimeError(
            "objective_continuity_check could not durably record "
            f"{len(persistence_failures)} item(s): {'; '.join(persistence_failures)}",
        )

    return None


# =============================================================================
# Package A: the canonical event backbone's one drive.
# =============================================================================
#
# Everything about processing captured inbound events lives in
# bartholomew.kernel.event_processing. This is the whole of its connection to
# the scheduler: one registered drive that runs one bounded pass. There is no
# second loop, no worker process, no broker and no thread -- the autonomy loop
# that already exists is the only thing that runs it.

#: The drive's task_id. Also its `tool_use.allowlist` entry in Identity.yaml:
#: this drive is deliberately NOT in `_SELF_MAINTENANCE_DRIVES`, because
#: processing third-party events into a user's objectives is specific user
#: content, not kernel housekeeping -- the same reasoning
#: `drive_awaiting_response_check` records for itself.
INBOUND_EVENT_PROCESSING_DRIVE = "inbound_event_processing"


async def drive_inbound_event_processing(ctx: Any) -> Nudge | None:
    """Run one governed pass of the event-processing backbone.

    Thin on purpose. Every decision -- whether a halt is in force, which
    events to claim, what each one means, whether the write is permitted,
    what to record -- belongs to `event_processing.processor.process_batch()`
    and the authorities it consults. This function exists to be the thing the
    scheduler can call, and to say afterwards what happened.

    **Emits no nudge, ever.** Processing an inbound event is not a reason to
    interrupt the user: the evidence lands in the objective's own history,
    where it is read when the objective is next surfaced by the drive whose
    job that is. A backbone that queued a notification per event would turn
    every verified webhook into a demand for attention, which is precisely
    the shape `docs/POC_SLICE_2_PROACTIVE_REMINDERS.md` requires an explicit
    consent flag for.

    Failures are visible without one: a genuine fault raises, so the tick is
    recorded as `success=0` against this task_id in `ticks`, and the backlog,
    retry and quarantine counters are on the health surface either way.
    """
    from bartholomew.kernel.event_processing.processor import process_batch

    outcome = await process_batch(ctx)

    if outcome.deferred and not outcome.claimed:
        # Nothing was claimed at all: the pass declined before touching the
        # queue. Deliberately conditioned on `claimed` rather than on
        # `deferred` alone, because a brake that lands *mid*-batch also sets
        # `deferred` -- and reporting "no events were changed" for a pass that
        # had already processed four of them would be untrue.
        log.info(
            "[Scheduler] inbound_event_processing: no events were claimed or changed (%s)",
            outcome.deferred,
        )
        return None

    if outcome.errors:
        # Not swallowed and not converted into a nudge. Raising marks the tick
        # a failure, which is the honest record of a pass that could not do
        # what it claimed -- and the per-event state (attempts, quarantine)
        # is already durable, so nothing is retried by raising here.
        log.error(
            "[Scheduler] inbound_event_processing: %s event(s) failed this pass: %s",
            len(outcome.errors),
            "; ".join(outcome.errors[:5]),
        )
        raise RuntimeError(
            f"event processing had {len(outcome.errors)} failing event(s): "
            f"{'; '.join(outcome.errors[:5])}",
        )

    if outcome.claimed or outcome.swept:
        log.info(
            "[Scheduler] inbound_event_processing: swept=%s claimed=%s processed=%s "
            "irrelevant=%s refused=%s quarantined=%s released=%s",
            outcome.swept,
            outcome.claimed,
            outcome.processed,
            outcome.irrelevant,
            outcome.refused,
            outcome.quarantined,
            outcome.released,
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
    OBJECTIVE_CONTINUITY_DRIVE: {
        "fn": drive_objective_continuity_check,
        # Coarser still: the quiet interval is measured in days, so a
        # tighter cadence would only re-check what it already declined to
        # raise.
        "cadence": "every:10800",  # Every 3 hours
    },
    INBOUND_EVENT_PROCESSING_DRIVE: {
        "fn": drive_inbound_event_processing,
        # Tight, because this one is latency-bearing in a way the others are
        # not: an event that arrived is already late by the time it is
        # captured, and the whole point of the backbone is that it does not
        # sit unlooked-at. Cheap when idle -- two indexed reads against an
        # empty queue -- and bounded when busy by `event_processing`'s own
        # batch limit and deadline.
        "cadence": "every:15",
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


def proactive_objective_continuity_enabled(ctx: Any) -> bool:
    """
    Whether the operator has turned proactive objective re-engagement on.

    One authority: `config/kernel.yaml`'s `proactive.objective_continuity`,
    default `false`, and deliberately no environment-variable override --
    same single-deliberate-act reasoning as
    `proactive_schedule_reminders_enabled()` immediately above.
    """
    cfg = getattr(ctx, "cfg", None) or {}
    proactive = cfg.get("proactive") or {}
    return bool(proactive.get("objective_continuity", False))


def inbound_event_processing_enabled(ctx: Any) -> bool:
    """Whether this context runs the event-processing backbone.

    Default **ON**, unlike the two proactive drives above, and the difference
    is not an oversight. Those two decide whether Bartholomew may contact the
    user unprompted, which is a consent question and therefore has exactly one
    deliberate authority. This one decides whether Bartholomew looks at events
    a verified source already delivered to it: it sends nothing, contacts
    nobody, and its only durable effect is a `fact` row on an objective the
    user opened -- through the same governed seam, behind the same Parking
    Brake and the same Identity policy, that every other objective write uses.
    Capturing events and never reading them is the surprising behaviour, not
    the safe one.

    Two switches, and they are not symmetrical:

    * `config/kernel.yaml`'s `event_processing.enabled` is the only thing that
      can turn it **on**.
    * `BARTH_EVENT_PROCESSING_ENABLED=0` can only turn it **off**.

    The environment variable is a kill switch, not a second authority: an
    operator dealing with an incident should not have to edit a file to stop
    processing, and being able to stop something is never the risk that
    needing one deliberate act to start it is guarding against.
    """
    from bartholomew.kernel.event_processing.config import resolve_settings

    return resolve_settings(getattr(ctx, "cfg", None)).enabled


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
    if proactive_objective_continuity_enabled(ctx):
        resolved[OBJECTIVE_CONTINUITY_DRIVE] = OPTIONAL_REGISTRY[OBJECTIVE_CONTINUITY_DRIVE]
    if inbound_event_processing_enabled(ctx):
        resolved[INBOUND_EVENT_PROCESSING_DRIVE] = OPTIONAL_REGISTRY[INBOUND_EVENT_PROCESSING_DRIVE]
    return resolved
