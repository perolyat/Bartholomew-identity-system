"""Turning one leased action into one handler call. Five checks first, every time.

This is the device-side gate. Bartholomew's server has already run the eleven
governance checks, and this runs five more before anything reaches an operating
system -- not because the server is doubted, but because a client that trusts
whatever a server tells it is a client that a compromised or impersonated
server can drive:

1. **Device targeting.** The action must name *this* device. An action for
   another machine that arrived here is refused, not executed.
2. **Capability kind and version.** The kind must be one this build implements
   *and* one this install was configured to offer, at the exact version. An
   unknown kind or version is refused; it is never run at the nearest version.
3. **Expiry.** An action past its expiry is refused. The clocks are checked
   independently here rather than trusting the server's view of the time.
4. **Replay state.** An action id in this machine's durable executed-ledger is
   never run again; what it did before is reported instead.
5. **Abort clearance.** A lease carries a deadline by which this companion must
   have re-read the server's abort signal. Past it, nothing runs. This is the
   device half of stop-after-lease: the server can move a leased row to
   `aborted_by_brake`, but only the device can decline to act on it.

**Dispatch is a literal table lookup on a closed enum.** `handlers.HANDLERS`
maps `CapabilityKind` to a named function. There is no `getattr` on a
server-supplied string, no `globals()`, no `importlib`, no entry point and no
`eval`: a capability name that is not a member of the enum cannot become a key,
and a key with no entry cannot become a call.
`tests/test_windows_action_prohibitions.py` asserts each of those absences over
this module's source.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from bartholomew.actuation.capabilities import (
    CURRENT_CAPABILITY_VERSION,
    CapabilityKind,
    UnsupportedCapabilityError,
    require_supported,
)
from bartholomew.actuation.result import ActionResultStatus, ErrorCategory, HandlerOutcome

from .handlers import HANDLERS, HandlerContext
from .state import ActionCompanionState

logger = logging.getLogger(__name__)

#: Most action ids this module will read out of one abort response. Matches the
#: server's own `store.MAX_ABORT_POLL_IDS` bound, applied again here because a
#: client that trusts a server's list length is a client an unbounded list can
#: exhaust.
MAX_ABORT_IDS = 50


class DispatchRefusedError(Exception):
    """This device refused an action before touching anything.

    Carries the category and detail the channel reports back, so a refusal is
    as legible in the audit as a failure.
    """

    def __init__(self, category: ErrorCategory, detail: str):
        super().__init__(detail)
        self.category = category
        self.detail = detail


@dataclass(frozen=True)
class LeasedAction:
    """One action as it arrived over the action channel, before validation.

    Deliberately holds the raw wire values rather than parsed ones: parsing is
    what `validate()` does below, and a type that could only be constructed
    from valid data would move the refusal to the constructor where the caller
    could not report it properly.
    """

    action_id: str
    tenant_id: str
    device_id: str
    capability: str
    capability_version: Any
    parameters: Any
    expires_at: str
    repeatability: str = "non_repeatable"
    correlation_id: str = ""
    #: When this lease's abort clearance goes stale, as the **server** measured
    #: it. Empty when the server did not send one, which an older server will
    #: not -- `check()` treats an absent deadline as an expired one, so the
    #: companion refuses to act rather than falling back to acting freely.
    abort_deadline: str = ""

    @classmethod
    def from_wire(cls, raw: Any) -> LeasedAction:
        """Build one from a channel response entry, or refuse it.

        Every field is required. A leased action missing any of them is
        malformed, and a malformed action is refused rather than defaulted --
        a missing `device_id` defaulted to this device would be exactly the
        targeting failure check 1 exists to catch.
        """
        if not isinstance(raw, dict):
            raise DispatchRefusedError(
                ErrorCategory.PARAMETERS_INVALID,
                "a leased action must be a JSON object",
            )
        missing = [
            key
            for key in (
                "action_id",
                "tenant_id",
                "device_id",
                "capability",
                "capability_version",
                "expires_at",
            )
            if raw.get(key) in (None, "")
        ]
        if missing:
            raise DispatchRefusedError(
                ErrorCategory.PARAMETERS_INVALID,
                f"the leased action is missing {sorted(missing)}",
            )
        return cls(
            action_id=str(raw["action_id"]),
            tenant_id=str(raw["tenant_id"]),
            device_id=str(raw["device_id"]),
            capability=str(raw["capability"]),
            capability_version=raw["capability_version"],
            parameters=raw.get("parameters") or {},
            expires_at=str(raw["expires_at"]),
            repeatability=str(raw.get("repeatability") or "non_repeatable"),
            correlation_id=str(raw.get("correlation_id") or ""),
            abort_deadline=str(raw.get("abort_deadline") or ""),
        )


@dataclass(frozen=True)
class AbortSignal:
    """The server's answer to "may the actions I am holding still run?".

    **Unreadable is stop.** `readable=False` is what a transport failure, an
    unauthenticated channel, a malformed body and an older server that has no
    such endpoint all produce, and `may_run()` returns False for every one of
    them. The alternative -- carrying on when the abort signal cannot be
    reached -- would make the whole mechanism a control that works exactly when
    nothing is wrong.

    `halted` and `aborted` are kept apart for the reason the server keeps them
    apart: a halt is about the channel and means stop polling too, while an
    aborted id is about one action and means the others are still fine.
    """

    readable: bool
    halted: bool = False
    aborted: frozenset[str] = frozenset()
    running: frozenset[str] = frozenset()
    reason: str = ""
    #: When this clearance goes stale, as the **server** measured it on this
    #: read. Empty when the server did not send one, which is treated as
    #: already stale -- the same reading `abort_deadline_passed` gives every
    #: other unusable deadline.
    clearance_deadline: str = ""

    @classmethod
    def unreadable(cls, reason: str) -> AbortSignal:
        return cls(readable=False, halted=False, reason=str(reason or "")[:300])

    @classmethod
    def from_wire(cls, raw: Any) -> AbortSignal:
        """Parse the abort response, or return an unreadable signal.

        Never raises. A malformed body is not an error to handle somewhere
        else; it is an answer that failed to establish clearance, which is
        exactly what `unreadable` means.
        """
        if not isinstance(raw, dict):
            return cls.unreadable("the abort response was not a JSON object")
        running = raw.get("running")
        aborted = raw.get("aborted")
        if not isinstance(running, list) or not isinstance(aborted, list):
            return cls.unreadable("the abort response named no running/aborted lists")
        return cls(
            readable=True,
            halted=bool(raw.get("halted")),
            clearance_deadline=str(raw.get("clearance_deadline") or ""),
            aborted=frozenset(str(a) for a in aborted[:MAX_ABORT_IDS]),
            running=frozenset(str(a) for a in running[:MAX_ABORT_IDS]),
            reason=str(raw.get("reason") or "")[:300],
        )

    def may_run(self, action_id: str) -> bool:
        """Whether one action is established as still runnable. Fail-closed.

        Affirmative only: the id has to be *in* `running`. Absence is not
        permission, so an id the server did not mention -- because it was
        cancelled, expired, swept, or never that device's -- stops.
        """
        return self.readable and not self.halted and action_id in self.running


def abort_deadline_passed(raw: str, *, now: datetime | None = None) -> bool:
    """Whether a lease's abort clearance has gone stale. Missing is stale.

    The bound the W03-C contract asks the *lease response* to carry, honoured
    here on the device. An empty or unparseable deadline returns True, so a
    server that sent no bound gets the conservative reading rather than an
    unbounded one -- the same treatment `_parse_expiry` gives an unreadable
    expiry, and for the same reason.
    """
    text = str(raw or "").strip()
    if not text:
        return True
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return True
    if parsed.tzinfo is None:
        return True
    return (now or datetime.now(timezone.utc)) >= parsed.astimezone(timezone.utc)


def _parse_expiry(raw: str) -> datetime:
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as e:
        raise DispatchRefusedError(
            ErrorCategory.EXPIRED,
            f"the action's expiry {raw!r} could not be read, so it is treated as "
            "expired rather than as unbounded",
        ) from e
    if parsed.tzinfo is None:
        raise DispatchRefusedError(
            ErrorCategory.EXPIRED,
            "the action's expiry has no timezone and is treated as expired",
        )
    return parsed.astimezone(timezone.utc)


def check(
    action: LeasedAction,
    ctx: HandlerContext,
    state: ActionCompanionState,
    *,
    now: datetime | None = None,
) -> CapabilityKind:
    """The five device-side checks. Returns the capability, or raises a refusal.

    Ordered cheapest-and-most-fundamental first: an action for another device
    is refused before its capability is even parsed, because nothing about it
    is this machine's business.
    """
    # 1. Device targeting.
    if action.device_id != ctx.config.device_id:
        raise DispatchRefusedError(
            ErrorCategory.DEVICE_NOT_ENROLLED,
            f"this action targets device {action.device_id!r} and this companion is "
            f"{ctx.config.device_id!r}; it will not be run here",
        )

    # 2. Capability kind and version, twice: what this build implements, and
    #    what this install was configured to offer.
    try:
        descriptor = require_supported(action.capability, action.capability_version)
    except UnsupportedCapabilityError as e:
        raise DispatchRefusedError(ErrorCategory.CAPABILITY_UNSUPPORTED, str(e)) from e
    if descriptor.version != CURRENT_CAPABILITY_VERSION:  # pragma: no cover - belt and braces
        raise DispatchRefusedError(
            ErrorCategory.CAPABILITY_UNSUPPORTED,
            f"{descriptor.kind.value} version {descriptor.version} is not this build's",
        )
    if not ctx.config.supports(descriptor.kind):
        raise DispatchRefusedError(
            ErrorCategory.CAPABILITY_NOT_DECLARED,
            f"{descriptor.kind.value} is not enabled on this device. This install "
            f"offers {[k.value for k in ctx.config.capabilities]}.",
        )
    if descriptor.kind not in HANDLERS:  # pragma: no cover - the table is complete
        raise DispatchRefusedError(
            ErrorCategory.CAPABILITY_UNSUPPORTED,
            f"{descriptor.kind.value} has no handler in this build",
        )

    # 3. Expiry, judged against this machine's own clock.
    if (now or datetime.now(timezone.utc)) >= _parse_expiry(action.expires_at):
        raise DispatchRefusedError(
            ErrorCategory.EXPIRED,
            f"the action expired at {action.expires_at} and will not be run",
        )

    # 4. Replay state, from the durable ledger.
    previous = state.executed.get(action.action_id)
    if previous is not None and action.repeatability != "idempotent":
        raise DispatchRefusedError(
            ErrorCategory.REPLAY_REFUSED,
            f"this device already ran action {action.action_id} and observed "
            f"{previous.status!r}; a duplicate delivery does not run it again",
        )

    # 5. The abort clearance, which is the newest of the five and the only one
    #    that is about *now* rather than about the action. A lease is not a
    #    standing permission: the server stamped a deadline on it, and past
    #    that deadline this companion has not established that the action may
    #    still run. The runner re-reads the abort signal to refresh it; this is
    #    the check that makes failing to do so stop the action rather than
    #    silently widen the window.
    if abort_deadline_passed(action.abort_deadline, now=now):
        raise DispatchRefusedError(
            ErrorCategory.PARKING_BRAKE,
            f"the abort clearance for action {action.action_id} expired at "
            f"{action.abort_deadline or '<unset>'}; this companion has not "
            "established that it may still run, so it will not run it",
        )
    return descriptor.kind


def dispatch(
    action: LeasedAction,
    ctx: HandlerContext,
    state: ActionCompanionState,
    *,
    now: datetime | None = None,
) -> HandlerOutcome:
    """Run one leased action, or report why it was refused. Never raises.

    A handler that raises is a bug in the handler, and a bug in a handler must
    not take the companion down or -- worse -- leave the action silently
    unreported. It becomes an `unknown` outcome naming the exception type,
    because a handler that crashed part-way genuinely may or may not have had
    an effect, and that is what `unknown` is for.
    """
    try:
        kind = check(action, ctx, state, now=now)
    except DispatchRefusedError as refusal:
        if refusal.category is ErrorCategory.PARKING_BRAKE:
            # The abort-clearance refusal, and it is an abort rather than a
            # failure. Nothing else in `check()` raises this category, and
            # recording "a halt stopped this" as `failed` would put it in the
            # same column as a window that would not focus -- which is the one
            # distinction an operator auditing a safety control needs.
            return HandlerOutcome.aborted_by_brake(refusal.detail, steps_completed=0)
        return HandlerOutcome(
            ActionResultStatus.FAILED,
            refusal.category,
            refusal.detail,
        )

    handler = HANDLERS[kind]
    try:
        outcome = handler(action.parameters, ctx)
    except Exception as e:  # noqa: BLE001 - a crashed handler is an unknown outcome
        logger.exception("The %s handler raised", kind.value)
        return HandlerOutcome.unverifiable(
            f"the {kind.value} handler raised {type(e).__name__} part-way through, so "
            "whether it had any effect is not known",
        )
    if not isinstance(outcome, HandlerOutcome):  # pragma: no cover - defensive
        return HandlerOutcome.unverifiable(
            f"the {kind.value} handler did not return an outcome",
        )
    return outcome
