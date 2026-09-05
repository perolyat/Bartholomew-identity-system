"""One governed processing pass over the claimed backlog.

Everything the backbone actually *does* happens here, in a fixed order that
each step depends on:

1. **Schema**, idempotently, so a pass that runs before startup finished (or
   against a database that predates this feature) works rather than 500s.
2. **The Parking Brake**, engaged at all, fail-closed. Nothing after this
   point runs while a halt is in force -- not the sweep, not claiming, not a
   single write. That is what makes "the brake preserves the backlog" a
   structural property rather than a hope: a halted pass leaves the table
   byte-identical, so releasing the brake resumes exactly the queue that
   existed when it was engaged.
3. **Identity policy**, for this pass's own action kind. Also before
   claiming, for the same reason -- a denied pass must not spend attempts.
4. **The sweep**, turning newly captured rows into processing state.
5. **Claim, process, settle**, one event at a time, each bounded.

The two gates are the *existing* ones, read through the same helpers every
other governed surface uses. This module adds no gate of its own and, much
more importantly, bypasses none: the write that actually matters -- attaching
evidence to an objective -- goes through
`run_objective_through_runtime_contract`, which checks both again at the
moment of writing.

Why the gates are checked twice
------------------------------
Not redundancy. The pass-level check is about the *backlog*: it decides
whether to disturb the queue at all. The seam-level check is about the
*write*: it is the authority, and it is what catches a brake engaged in the
middle of a batch. The pass-level check cannot be relied on for correctness
(state changes under it) and the seam-level check cannot preserve the backlog
on its own (by the time it runs, an attempt has been spent). Both are needed
and neither substitutes for the other.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from . import store
from .adapters import BrakeDeferredError, TransientProcessingError
from .config import EventProcessingSettings, resolve_settings
from .envelope import ENVELOPE_VERSION, CanonicalEvent, payload_matches_digest
from .registry import HandlerResult, PayloadValidationError, lookup

logger = logging.getLogger(__name__)

#: The Executive-facing kind for a processing pass, evaluated against
#: `Identity.yaml`'s `tool_use.allowlist`. One kind, not one per event type --
#: a governance taxonomy of domains is exactly what the domain-blind capture
#: boundary exists to prevent, and it must not grow back here.
EVENT_PROCESS_KIND = "inbound_event_process"

#: Why a pass did nothing. Reported, logged, and never silently swallowed.
DEFERRED_DISABLED = "disabled"
DEFERRED_PARKING_BRAKE = "parking_brake_engaged"
DEFERRED_POLICY = "identity_policy_denied"

#: Terminal reasons this module (rather than a handler) assigns.
REASON_UNKNOWN_TYPE = "unknown_event_type"
REASON_INVALID_PAYLOAD = "invalid_payload"
REASON_MISSING_INBOUND_ROW = "inbound_record_missing"
REASON_PAYLOAD_UNREADABLE = "payload_unreadable"
REASON_DIGEST_MISMATCH = "payload_digest_mismatch"
REASON_NOT_CAPTURED = "inbound_record_not_captured"
REASON_TENANT_MISMATCH = "tenant_mismatch"


@dataclass
class ProcessingPassResult:
    """What one pass did. Counts only -- no third-party content."""

    swept: int = 0
    claimed: int = 0
    processed: int = 0
    irrelevant: int = 0
    refused: int = 0
    quarantined: int = 0
    retried: int = 0
    released: int = 0
    deferred: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def settled(self) -> int:
        return self.processed + self.irrelevant + self.refused + self.quarantined

    def as_dict(self) -> dict[str, Any]:
        return {
            "swept": self.swept,
            "claimed": self.claimed,
            "processed": self.processed,
            "irrelevant": self.irrelevant,
            "refused": self.refused,
            "quarantined": self.quarantined,
            "retried": self.retried,
            "released": self.released,
            "deferred": self.deferred,
            "errors": list(self.errors),
        }


def _db_path(ctx: Any) -> str:
    path = getattr(getattr(ctx, "mem", None), "db_path", None)
    if not path:
        raise ValueError("the processing pass needs a context with mem.db_path")
    return str(path)


async def _brake_engaged(ctx: Any, db_path: str) -> tuple[bool, str | None]:
    """Whether a halt is in force. Fail-closed: an unreadable gate is a halt.

    Reads `engaged_state_fail_closed_off_loop`, the same composed read the
    memory-mutation gate, the objective seam and inbound capture use -- so a
    Platform/Admin halt stops processing through exactly one implementation,
    not a second opinion that could drift from it.
    """
    from bartholomew.orchestrator.safety.governance_store import (
        engaged_state_fail_closed_off_loop,
    )

    try:
        state = await engaged_state_fail_closed_off_loop(
            db_path,
            governance_store=getattr(ctx, "governance_store", None),
            executor=getattr(ctx, "blocking_executor", None),
        )
    except Exception as e:
        logger.exception("Event processing: governance state unreadable; failing closed")
        return True, f"governance state unreadable: {type(e).__name__}: {e}"
    if state.engaged:
        scopes = ", ".join(sorted(state.scopes)) or "global"
        return True, f"parking brake engaged (scopes={scopes})"
    return False, None


def _policy_denial(ctx: Any) -> str | None:
    """The Identity-policy reason this pass may not run, or None.

    Skipped entirely when no IdentityContext is wired in -- additive, and the
    same posture every other seam in this repository takes.
    """
    identity_context = getattr(ctx, "identity_context", None)
    if identity_context is None:
        return None
    from bartholomew.kernel import policy_engine

    decision = policy_engine.evaluate_tool_policy(identity_context, EVENT_PROCESS_KIND)
    return None if decision.allowed else (decision.reason or "denied by Identity policy")


def resolve_runtime_id() -> str | None:
    """Whose events this process may claim.

    The platform's binding and nothing else -- never a value read from an
    event, a payload or a header. On an unbound single-user deployment this is
    None, which claims exactly the events captured with no binding.
    """
    from bartholomew.platform.runtime_registry import bound_runtime_user_id

    return bound_runtime_user_id()


def _load_event(db_path: str, record: store.ProcessingRecord) -> CanonicalEvent:
    """Build the canonical envelope for one claimed row.

    Reads the capture record rather than trusting the processing row's copy of
    it: `inbound_events` is the authority on what arrived, and provenance
    written onto an objective must come from there.
    """
    from bartholomew.kernel.inbound_store import (
        OUTCOME_CAPTURED,
        get_event,
        get_event_payload,
    )

    stored = get_event(db_path, record.source_id, record.event_id)
    if stored is None:
        raise _TerminalDispositionError(
            REASON_MISSING_INBOUND_ROW,
            "the captured row no longer exists",
        )
    if stored.outcome != OUTCOME_CAPTURED:
        raise _TerminalDispositionError(
            REASON_NOT_CAPTURED,
            f"the inbound record's outcome is {stored.outcome!r}, not a capture",
        )
    payload = get_event_payload(db_path, record.source_id, record.event_id)
    if payload is None:
        raise _TerminalDispositionError(
            REASON_PAYLOAD_UNREADABLE,
            "the stored payload could not be read back as JSON",
        )
    event = CanonicalEvent.from_inbound_row(
        stored,
        payload,
        envelope_version=record.envelope_version or ENVELOPE_VERSION,
    )
    if not payload_matches_digest(event):
        raise _TerminalDispositionError(
            REASON_DIGEST_MISMATCH,
            "the stored payload no longer matches the digest capture recorded",
        )
    return event


class _TerminalDispositionError(Exception):
    """An internal signal: settle this event `refused` with a stated reason.

    Refused rather than failed, because every condition raising it is
    deterministic -- a missing row, an unreadable payload, a type nothing
    handles. Retrying would produce the same answer three times and then call
    a settled question a fault.
    """

    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


async def _process_one(
    ctx: Any,
    db_path: str,
    record: store.ProcessingRecord,
    *,
    runtime_id: str | None,
    settings: EventProcessingSettings,
    result: ProcessingPassResult,
) -> None:
    """Take one claimed event to a disposition, or put it back."""
    from bartholomew.kernel.blocking_executor import run_off_loop

    executor = getattr(ctx, "blocking_executor", None)

    # Tenant re-check, after the claim already filtered on it. Defence in
    # depth on the property that matters most here: an event may only ever be
    # processed by the runtime it was captured for, and a single filter in a
    # single query is one refactor away from not being there.
    if record.runtime_id != runtime_id:
        await run_off_loop(
            store.settle,
            db_path,
            record.row_id,
            record.claim_token,
            state=store.STATE_REFUSED,
            reason=REASON_TENANT_MISMATCH,
            result={"claimed_by_runtime": runtime_id, "event_runtime": record.runtime_id},
            executor=executor,
        )
        result.refused += 1
        logger.error(
            "Event processing: refused %s/%s -- captured for runtime %r but claimed "
            "by %r. No evidence was attached.",
            record.source_id,
            record.event_id,
            record.runtime_id,
            runtime_id,
        )
        return

    try:
        event = await run_off_loop(_load_event, db_path, record, executor=executor)
        registered = lookup(event.event_type)
        if registered is None:
            raise _TerminalDispositionError(
                REASON_UNKNOWN_TYPE,
                f"no handler is registered for event type {event.event_type!r}",
            )
        try:
            payload = registered.parse(event.payload)
        except PayloadValidationError as e:
            raise _TerminalDispositionError(REASON_INVALID_PAYLOAD, str(e)) from e

        handler_result: HandlerResult = await registered.handler(ctx, event, payload)

    except _TerminalDispositionError as terminal:
        await run_off_loop(
            store.settle,
            db_path,
            record.row_id,
            record.claim_token,
            state=store.STATE_REFUSED,
            reason=terminal.reason,
            result={"detail": terminal.detail, "event_type": record.event_type},
            executor=executor,
        )
        result.refused += 1
        logger.info(
            "Event processing: refused %s/%s (%s): %s",
            record.source_id,
            record.event_id,
            terminal.reason,
            terminal.detail,
        )
        return

    except BrakeDeferredError as deferred:
        await run_off_loop(
            store.release,
            db_path,
            record.row_id,
            record.claim_token,
            reason=f"deferred: {deferred}",
            refund_attempt=True,
            executor=executor,
        )
        result.released += 1
        result.deferred = result.deferred or DEFERRED_PARKING_BRAKE
        logger.info(
            "Event processing: %s/%s returned to the backlog unchanged (%s)",
            record.source_id,
            record.event_id,
            deferred,
        )
        return

    except Exception as e:
        # `asyncio.CancelledError` is a BaseException and is deliberately not
        # caught here: a cancelled pass is a shutdown, and charging the event
        # an attempt for the process going away would be wrong. Its claim
        # lease expires and a later pass recovers it untouched.
        error = f"{type(e).__name__}: {e}"
        if not isinstance(e, TransientProcessingError):
            logger.exception(
                "Event processing: handler crashed on %s/%s",
                record.source_id,
                record.event_id,
            )
        new_state = await run_off_loop(
            store.fail,
            db_path,
            record.row_id,
            record.claim_token,
            error=error,
            max_attempts=settings.max_attempts,
            executor=executor,
        )
        if new_state == store.STATE_QUARANTINED:
            result.quarantined += 1
            logger.error(
                "Event processing: quarantined %s/%s after %s attempt(s): %s",
                record.source_id,
                record.event_id,
                record.attempts,
                error,
            )
        elif new_state == store.STATE_CAPTURED:
            result.retried += 1
        result.errors.append(f"{record.source_id}/{record.event_id}: {error}")
        return

    settled = await run_off_loop(
        store.settle,
        db_path,
        record.row_id,
        record.claim_token,
        state=handler_result.disposition,
        reason=handler_result.reason,
        result=handler_result.detail,
        executor=executor,
    )
    if not settled:
        # The lease expired mid-flight and another pass owns this event now.
        # The handler's effect is already idempotent (evidence attachment
        # checks the objective's own history), so the other pass will settle
        # it as `already_recorded` -- nothing is lost and nothing is doubled.
        logger.warning(
            "Event processing: %s/%s could not be settled -- its claim had "
            "already been recovered. The disposition is another pass's to record.",
            record.source_id,
            record.event_id,
        )
        result.errors.append(f"{record.source_id}/{record.event_id}: claim lost before settle")
        return

    if handler_result.disposition == store.STATE_PROCESSED:
        result.processed += 1
    elif handler_result.disposition == store.STATE_IRRELEVANT:
        result.irrelevant += 1
    else:
        result.refused += 1


async def process_batch(
    ctx: Any,
    *,
    db_path: str | None = None,
    settings: EventProcessingSettings | None = None,
    runtime_id: str | None = None,
    resolve_runtime: bool = True,
    now_ts: int | None = None,
) -> ProcessingPassResult:
    """Run one bounded, governed processing pass.

    `ctx` is the same duck-typed context every other seam takes: `.mem.db_path`
    is required; `.objective_store`, `.blocking_executor`, `.governance_store`
    and `.identity_context` are consulted through `getattr`.

    Returns counts. Never raises for an individual event's failure -- that is
    what attempts and quarantine are for -- but does propagate a failure of
    the durable state itself, because a pass that cannot record what it did
    must not report that it did it.
    """
    from bartholomew.kernel.blocking_executor import run_off_loop

    executor = getattr(ctx, "blocking_executor", None)
    path = db_path or _db_path(ctx)
    resolved = settings or resolve_settings(getattr(ctx, "cfg", None))
    result = ProcessingPassResult()

    if not resolved.enabled:
        result.deferred = DEFERRED_DISABLED
        return result

    await run_off_loop(store.ensure_schema, path, executor=executor)

    engaged, brake_reason = await _brake_engaged(ctx, path)
    if engaged:
        result.deferred = DEFERRED_PARKING_BRAKE
        logger.info(
            "Event processing: no events were claimed or changed -- %s. "
            "The backlog is preserved exactly as it was.",
            brake_reason,
        )
        return result

    denial = _policy_denial(ctx)
    if denial is not None:
        result.deferred = DEFERRED_POLICY
        logger.warning(
            "Event processing: pass denied by Identity policy (%s). The backlog "
            "is preserved and no attempt was spent.",
            denial,
        )
        return result

    result.swept = await run_off_loop(
        store.sweep_captured,
        path,
        limit=resolved.sweep_limit,
        envelope_version=ENVELOPE_VERSION,
        executor=executor,
    )

    claim_runtime = resolve_runtime_id() if (resolve_runtime and runtime_id is None) else runtime_id
    claimed = await run_off_loop(
        store.claim_batch,
        path,
        runtime_id=claim_runtime,
        limit=resolved.batch_limit,
        lease_seconds=resolved.lease_seconds,
        max_attempts=resolved.max_attempts,
        now_ts=now_ts,
        executor=executor,
    )
    result.claimed = len(claimed)
    if not claimed:
        return result

    deadline = time.monotonic() + resolved.deadline_seconds
    for index, record in enumerate(claimed):
        if index and time.monotonic() >= deadline:
            # Out of budget. Everything still held goes back untouched, with
            # its attempt refunded: running out of time is the pass's problem,
            # never the event's, and it must never bring one nearer quarantine.
            await run_off_loop(
                store.release,
                path,
                record.row_id,
                record.claim_token,
                reason="batch deadline reached before this event was processed",
                refund_attempt=True,
                executor=executor,
            )
            result.released += 1
            continue
        await _process_one(
            ctx,
            path,
            record,
            runtime_id=claim_runtime,
            settings=resolved,
            result=result,
        )

    return result


__all__ = [
    "DEFERRED_DISABLED",
    "DEFERRED_PARKING_BRAKE",
    "DEFERRED_POLICY",
    "EVENT_PROCESS_KIND",
    "REASON_DIGEST_MISMATCH",
    "REASON_INVALID_PAYLOAD",
    "REASON_MISSING_INBOUND_ROW",
    "REASON_NOT_CAPTURED",
    "REASON_PAYLOAD_UNREADABLE",
    "REASON_TENANT_MISMATCH",
    "REASON_UNKNOWN_TYPE",
    "ProcessingPassResult",
    "process_batch",
    "resolve_runtime_id",
]
