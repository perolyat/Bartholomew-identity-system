"""What an endpoint may be asked to do -- and the three separate facts that decide it.

`INTERFACES.md`'s device-capability entry states the properties this module
implements, and two of them are the whole content here:

* **Declared, not assumed.** An endpoint states what it offers. The platform
  never infers a capability from the endpoint's *kind*. There is no branch
  below that reads "this is a Windows machine, so it can open a URL".
* **Availability is distinct from declaration.** A declared capability that
  cannot currently be served must be reportable as such. The named precedent
  is `cloud_llm.readiness()` / `/api/health`'s `model_status`, which separate
  *configured* from *reachable* precisely because conflating them produced a
  surface that claimed a readiness it had never checked.

Declaring is not authorising, and authorising is not availability
-----------------------------------------------------------------
Three facts, checked separately, and all three must hold before a directive
may name a capability:

1. **This deployment understands it.** `platform/device_capabilities.py` holds
   the frozen vocabulary and answers this. An unknown kind, or a known kind at
   an unknown version, is *unsupported* -- refused, never approximated.
2. **The endpoint declared it and the operator admitted it.**
   `platform.devices.VerifiedDevice.authorizes()` already composes those two:
   the manifest is the endpoint's own claim, and the approval ceiling is what
   an operator actually agreed to. This module does not re-implement either;
   it calls that method.
3. **The endpoint says it can serve it right now.** This is the fact nothing
   in the repository held before, and the one this module adds.

**None of the three is authority to decide when the capability should be
used.** An endpoint advertising `multimodal.spoken_output` has said "I am able
to speak"; whether Bartholomew should speak, and what, is an executive
question answered elsewhere. Capability and authority are separate concepts,
and nothing in this module widens one into the other.

Why silence is not availability
-------------------------------
`UNREPORTED` is a distinct standing from `UNAVAILABLE`, and neither may be
directed. An endpoint that has never reported is not "probably fine": issuing
a directive on a declaration alone would claim exactly the readiness
`cloud_llm.readiness()` exists to stop us claiming. Reporting is cheap -- one
submission on connect -- and the cost of the strict reading is one round trip,
against a failure mode where Bartholomew speaks into a room with nothing
listening and records that it spoke.

Why a report goes stale
-----------------------
An availability report is a statement about a moment, not a standing promise,
and an endpoint that has vanished cannot withdraw one. Rather than invent a
heartbeat protocol, a report simply expires: past `AVAILABILITY_TTL_SECONDS`
it reads as `UNREPORTED` again. That is the honest handling of *endpoint
disappears* for a boundary whose first transport has no connection to lose,
and it is a bound, not a liveness claim -- a report inside its window still
only means the endpoint said so then.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from .contract import CapabilityRef, ContractError, to_iso, utc_now

#: How long an availability report is believed. Deliberately short relative to
#: a session and long relative to a round trip: it bounds the window in which
#: a departed endpoint still reads as present, without requiring a heartbeat.
AVAILABILITY_TTL_SECONDS = 300

#: Longest detail string recorded alongside a report. A registry row is not a
#: place to put arbitrary endpoint-supplied text.
MAX_DETAIL_LENGTH = 200


class CapabilityStanding(str, Enum):
    """Where one capability stands for one endpoint, right now.

    A closed set with no "probably" member. Each value is a different, honest
    reason, and they are kept apart so that an operator reading a refusal
    learns which of five things to fix rather than being told "no".
    """

    #: All three facts hold. A directive may name this capability.
    AVAILABLE = "available"
    #: This deployment does not understand the kind, or not at that version.
    UNSUPPORTED = "unsupported"
    #: The endpoint never declared it in its manifest.
    UNDECLARED = "undeclared"
    #: Declared, but outside the operator's approved ceiling for this endpoint.
    UNAUTHORISED = "unauthorised"
    #: Authorised, but the endpoint has never said it can serve it -- or last
    #: said so longer ago than a report is believed.
    UNREPORTED = "unreported"
    #: Authorised, and the endpoint has explicitly said it cannot serve it now.
    UNAVAILABLE = "unavailable"


#: The one standing in which a directive may be issued. A frozenset of one,
#: written as a set so that a future second member is a deliberate edit here
#: rather than a loosened comparison at some call site.
DIRECTABLE: frozenset[CapabilityStanding] = frozenset({CapabilityStanding.AVAILABLE})


@dataclass(frozen=True)
class AvailabilityReport:
    """An endpoint's own statement that it can, or cannot, serve a capability.

    Provenance about the endpoint's condition, never an authorisation. A
    report saying `available=True` for a capability the endpoint never
    declared authorises nothing and changes no standing: `resolve_standing()`
    checks the declaration first and never consults a report it has already
    refused on.
    """

    capability: CapabilityRef
    available: bool
    reported_at: datetime
    detail: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.capability, CapabilityRef):
            raise ContractError("capability must be a CapabilityRef")
        if not isinstance(self.available, bool):
            raise ContractError("available must be a boolean")
        if not isinstance(self.reported_at, datetime):
            raise ContractError("reported_at must be a datetime")
        detail = (self.detail or "").strip()
        if len(detail) > MAX_DETAIL_LENGTH:
            detail = detail[:MAX_DETAIL_LENGTH]
        object.__setattr__(self, "detail", detail)

    def is_stale(self, *, now: datetime | None = None) -> bool:
        moment = now or utc_now()
        return moment - self.reported_at > timedelta(seconds=AVAILABILITY_TTL_SECONDS)

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability.as_dict(),
            "available": self.available,
            "reported_at": to_iso(self.reported_at),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class CapabilityDecision:
    """Whether this endpoint may be directed to do this, and why not if not."""

    capability: CapabilityRef
    standing: CapabilityStanding
    reason: str

    @property
    def directable(self) -> bool:
        return self.standing in DIRECTABLE

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability.as_dict(),
            "standing": self.standing.value,
            "directable": self.directable,
            "reason": self.reason,
        }


def resolve_standing(
    device: Any,
    capability: CapabilityRef,
    report: AvailabilityReport | None,
    *,
    now: datetime | None = None,
) -> CapabilityDecision:
    """Where `capability` stands for `device`. A pure function; it reads nothing.

    `device` is a `platform.devices.VerifiedDevice`. It is typed loosely on
    purpose so that this module stays importable without pulling the control
    plane in behind it -- the same lazy-boundary discipline
    `device_inbound.install_device_resolver` keeps -- and because the only
    thing asked of it is the `authorizes()` method the platform already owns.

    The order is the contract. Support is checked before declaration so that
    an endpoint declaring a capability this build has never heard of is told
    *unsupported* rather than *undeclared*; declaration and the operator
    ceiling are checked before availability so that an endpoint cannot make an
    undeclared capability interesting by reporting itself ready for it.

    Fails closed on every uncertainty: a registry that raises while being
    asked is not a permission.
    """
    if not isinstance(capability, CapabilityRef):
        raise ContractError("capability must be a CapabilityRef")

    from bartholomew.platform.device_capabilities import supports

    if not supports(capability.kind, capability.version):
        return CapabilityDecision(
            capability=capability,
            standing=CapabilityStanding.UNSUPPORTED,
            reason=(
                f"This deployment does not understand {capability}. An unknown "
                "capability kind or version is refused, never mapped onto the "
                "nearest thing that looks similar."
            ),
        )

    try:
        authorized = bool(device.authorizes(capability.kind, capability.version))
    except Exception as exc:  # noqa: BLE001 - an unreadable registry is not a permission
        return CapabilityDecision(
            capability=capability,
            standing=CapabilityStanding.UNAUTHORISED,
            reason=(
                f"The endpoint's capability registration could not be read "
                f"({type(exc).__name__}); refusing rather than proceeding on an "
                "unknown authorisation."
            ),
        )

    if not authorized:
        declared = _declares(device, capability)
        if declared:
            return CapabilityDecision(
                capability=capability,
                standing=CapabilityStanding.UNAUTHORISED,
                reason=(
                    f"The endpoint declared {capability}, but it is outside the "
                    "operator's approved capability set for this endpoint. Declaring "
                    "a capability is not being granted it."
                ),
            )
        return CapabilityDecision(
            capability=capability,
            standing=CapabilityStanding.UNDECLARED,
            reason=(
                f"The endpoint has not declared {capability} in its registered "
                "manifest. Bartholomew never infers a capability from an endpoint's "
                "kind or platform."
            ),
        )

    if report is None:
        return CapabilityDecision(
            capability=capability,
            standing=CapabilityStanding.UNREPORTED,
            reason=(
                f"The endpoint is authorised for {capability} but has not reported "
                "whether it can currently serve it. Declaration is not availability, "
                "and a directive is not issued on an unchecked readiness."
            ),
        )

    if report.is_stale(now=now):
        return CapabilityDecision(
            capability=capability,
            standing=CapabilityStanding.UNREPORTED,
            reason=(
                f"The endpoint's last availability report for {capability} is older "
                f"than {AVAILABILITY_TTL_SECONDS}s and is no longer believed. An "
                "endpoint that has gone away cannot withdraw its own report."
            ),
        )

    if not report.available:
        detail = f" ({report.detail})" if report.detail else ""
        return CapabilityDecision(
            capability=capability,
            standing=CapabilityStanding.UNAVAILABLE,
            reason=(f"The endpoint reports that it cannot currently serve {capability}{detail}."),
        )

    return CapabilityDecision(
        capability=capability,
        standing=CapabilityStanding.AVAILABLE,
        reason=(
            f"{capability} is understood by this deployment, declared by the endpoint, "
            "within the operator's approved set, and reported as currently servable."
        ),
    )


def _declares(device: Any, capability: CapabilityRef) -> bool:
    """Whether the endpoint's manifest names this, ignoring the ceiling.

    Used only to tell an *unauthorised* refusal from an *undeclared* one, so
    that an operator reading the reason learns whether to widen an approval or
    to fix the endpoint. Never consulted as a permission.
    """
    manifest = getattr(device, "manifest", None)
    if manifest is None:
        return False
    try:
        return bool(manifest.declares(capability.kind, capability.version))
    except Exception:  # noqa: BLE001 - unreadable reads as "not declared"
        return False


__all__ = [
    "AVAILABILITY_TTL_SECONDS",
    "DIRECTABLE",
    "MAX_DETAIL_LENGTH",
    "AvailabilityReport",
    "CapabilityDecision",
    "CapabilityStanding",
    "resolve_standing",
]
