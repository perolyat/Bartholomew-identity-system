"""Turning one leased action into one handler call. Four checks first, every time.

This is the device-side gate. Bartholomew's server has already run the eleven
governance checks, and this runs four more before anything reaches an operating
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
        )


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
    """The four device-side checks. Returns the capability, or raises a refusal.

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
