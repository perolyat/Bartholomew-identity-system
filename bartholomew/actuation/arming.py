"""The arming window: a bounded, revocable permission for the channel to run.

Package B's contract already separates *asking* for an action from
*authorising* one. This adds a third, coarser thing in front of both: whether
the machine's action channel is open at all right now.

Arming is not approval, and the distinction is the point
--------------------------------------------------------
An armed channel authorises **nothing**. Every action still travels the whole
of B's governed path -- validated, admitted, and bound to an explicit approval
that names that exact action. Arming only says "for the next few minutes, this
one device may carry out actions the person separately approves". Disarmed, an
approved action still does not run; armed, an unapproved one still does not.
Both halves are required, and neither substitutes for the other.

Deliberately in-process, deliberately not persisted
---------------------------------------------------
The window lives in this process and nowhere else, so a restart cannot leave a
machine armed. That is not a limitation to be fixed later: an arming window is
a statement about what a person is doing *right now*, at the keyboard, and a
statement like that does not survive the process that heard it. Recovering it
from disk would mean a crash at minute two of fifteen silently handing the
next process thirteen minutes of authority nobody re-granted.

Bound narrowly
--------------
One window at a time, per tenant, naming exactly one device. Arming a second
device replaces the first rather than accumulating: two open channels is not a
state a person asked for. A window is checked against the tenant *and* the
device, so arming the desk PC does not arm the laptop.

The Parking Brake is not consulted here
---------------------------------------
`evaluate_dispatch_admission` reads the brake immediately before this check and
denies first, so an engaged brake closes the channel however much time is left
on the window -- and it does so without this module needing to know the brake
exists. The arming surface reads the brake too, so it can refuse to arm and
report honestly, but the enforcement that matters is B's, in the order B
already established: brake, then everything else.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

#: How long one arming window lasts. Short on purpose: long enough to carry out
#: a task a person is watching, short enough that walking away closes it.
DEFAULT_ARM_SECONDS = 15 * 60

#: The longest window this module will mint, whatever a caller asks for. A
#: ceiling rather than a suggestion, so a caller cannot arm for a day.
MAX_ARM_SECONDS = 15 * 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ArmWindow:
    """One open window. Frozen: a window is not extended in place."""

    tenant_id: str
    device_id: str
    armed_at: datetime
    expires_at: datetime
    #: Who asked. Server-derived, never read from a request body.
    armed_by: str
    #: Free-text note from the operator, bounded, for the audit surface.
    reason: str | None = None

    def expired(self, *, now: datetime | None = None) -> bool:
        return (now or _now()) >= self.expires_at

    def seconds_remaining(self, *, now: datetime | None = None) -> int:
        delta = (self.expires_at - (now or _now())).total_seconds()
        return max(0, int(delta))

    def describe(self) -> dict[str, Any]:
        """Non-sensitive summary for the status surface."""
        return {
            "armed": not self.expired(),
            "device_id": self.device_id,
            "armed_at": self.armed_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "seconds_remaining": self.seconds_remaining(),
            "armed_by": self.armed_by,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ArmCheck:
    """Whether the channel is open for one (tenant, device) right now."""

    allowed: bool
    reason: str | None = None

    @classmethod
    def open(cls) -> ArmCheck:
        return cls(True)

    @classmethod
    def closed(cls, reason: str) -> ArmCheck:
        return cls(False, reason)


#: One window per tenant, in a holder rather than a bare module global for the
#: reason `actuation.devices._INSTALLED` gives: a rebound attribute is
#: invisible to a module that already imported the name.
_WINDOWS: dict[str, ArmWindow] = {}
_LOCK = threading.Lock()


def arm(
    *,
    tenant_id: str,
    device_id: str,
    armed_by: str,
    seconds: int | None = None,
    reason: str | None = None,
    now: datetime | None = None,
) -> ArmWindow:
    """Open a window for one device. Replaces any window this tenant had.

    Callers are responsible for having established, before calling: an
    authenticated enrolled device, a server-derived tenant that owns it, the
    Windows actuation capability, and a clear Parking Brake. This function
    records the decision; it does not make it.
    """
    tenant = str(tenant_id or "").strip()
    device = str(device_id or "").strip()
    if not tenant or not device:
        raise ValueError("an arming window must name both a tenant and a device")

    span = DEFAULT_ARM_SECONDS if seconds is None else int(seconds)
    if span <= 0:
        raise ValueError("an arming window must be a positive number of seconds")
    span = min(span, MAX_ARM_SECONDS)

    moment = now or _now()
    window = ArmWindow(
        tenant_id=tenant,
        device_id=device,
        armed_at=moment,
        expires_at=moment + timedelta(seconds=span),
        armed_by=str(armed_by or "unknown"),
        reason=(reason or None),
    )
    with _LOCK:
        _WINDOWS[tenant] = window
    logger.info(
        "Windows action channel armed for device %s until %s",
        device,
        window.expires_at.isoformat(),
    )
    return window


def disarm(*, tenant_id: str) -> ArmWindow | None:
    """Close this tenant's window immediately. Returns the window that was open.

    Idempotent: disarming a channel that is already closed is not a fault, and
    returns None.
    """
    tenant = str(tenant_id or "").strip()
    with _LOCK:
        window = _WINDOWS.pop(tenant, None)
    if window is not None:
        logger.info("Windows action channel disarmed for device %s", window.device_id)
    return window


def current(*, tenant_id: str, now: datetime | None = None) -> ArmWindow | None:
    """This tenant's live window, or None. An expired window is not live.

    Expiry is evaluated on read rather than swept on a timer, so a window is
    never briefly usable after its expiry because a sweep had not run yet.
    """
    tenant = str(tenant_id or "").strip()
    with _LOCK:
        window = _WINDOWS.get(tenant)
        if window is None:
            return None
        if window.expired(now=now):
            # Drop it as we pass, so status surfaces do not keep reporting a
            # dead window and the dict does not grow.
            _WINDOWS.pop(tenant, None)
            return None
    return window


def check(*, tenant_id: str, device_id: str, now: datetime | None = None) -> ArmCheck:
    """Whether this exact device may act for this tenant right now.

    The check B's dispatch admission runs. Closed is the default in every
    unclear case: no window, an expired one, or one naming a different device.
    """
    window = current(tenant_id=tenant_id, now=now)
    if window is None:
        return ArmCheck.closed(
            "the Windows action channel is not armed; arm it explicitly before "
            "an approved action can be carried out",
        )
    if window.device_id != str(device_id or "").strip():
        return ArmCheck.closed(
            f"the Windows action channel is armed for device {window.device_id}, "
            "not for this one",
        )
    return ArmCheck.open()


def describe(*, tenant_id: str, now: datetime | None = None) -> dict[str, Any]:
    """What the status surface says about this tenant's channel."""
    window = current(tenant_id=tenant_id, now=now)
    if window is None:
        return {
            "armed": False,
            "device_id": None,
            "seconds_remaining": 0,
            "detail": (
                "The Windows action channel is disarmed. Nothing can be carried "
                "out on this machine, including actions that are already approved."
            ),
        }
    described = window.describe()
    described["detail"] = (
        f"Armed for device {window.device_id} for another "
        f"{window.seconds_remaining()}s. An approved action may be carried out; "
        "an unapproved one still may not."
    )
    return described


def reset_for_tests() -> None:
    """Drop every window. Used by test fixtures on teardown."""
    with _LOCK:
        _WINDOWS.clear()


__all__ = [
    "DEFAULT_ARM_SECONDS",
    "MAX_ARM_SECONDS",
    "ArmCheck",
    "ArmWindow",
    "arm",
    "check",
    "current",
    "describe",
    "disarm",
    "reset_for_tests",
]
