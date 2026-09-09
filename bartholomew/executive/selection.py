"""Capability selection: what the device actually declares, and nothing else.

Selection is deliberately the least clever module in the package. It takes the
capability the person's own words named (`intent.py`), and it answers one
question against the device's enrolment: *may this device be asked for this?*

It does not choose between capabilities, rank them, substitute a near neighbour
for one that is unavailable, or read anything from memory. Those are exactly
the behaviours that turn "I could not focus the window" into "so I synthesised
a keystroke instead", and the envelope's own refusals say the same thing about
their own layer: ambiguity is a refusal, not a guess.

The authority here is `bartholomew.actuation.capabilities` (the closed
vocabulary and its risk/approval facts) and `EnrolledDevice.declares()` (what
this machine was enrolled for). Both are W03-C's; this module reads them and
adds no third notion of what a capability is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bartholomew.actuation.capabilities import (
    ApprovalRequirement,
    CapabilityKind,
    RiskClass,
    UnsupportedCapabilityError,
    describe,
)

#: Why a step cannot be proposed on this device. Distinct codes, because an
#: audit needs to tell "this build has no such capability" apart from "this
#: machine was not enrolled for it".
NOT_IN_VOCABULARY = "not_in_vocabulary"
NOT_DECLARED = "not_declared_by_device"
VERSION_MISMATCH = "version_not_declared"


@dataclass(frozen=True)
class CapabilitySelection:
    """The verdict on one step's capability against one device."""

    capability: str
    available: bool
    version: int | None = None
    risk: str | None = None
    approval_requirement: str | None = None
    #: True when this device's enrolment grants configured trusted autonomy for
    #: this kind. Read for the *explanation* only: whether an approval is
    #: needed is decided by the envelope at dispatch, not here, and this
    #: package never acts on the answer.
    device_autonomous: bool = False
    refusal_code: str | None = None
    refusal_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "available": self.available,
            "version": self.version,
            "risk": self.risk,
            "approval_requirement": self.approval_requirement,
            "device_autonomous": self.device_autonomous,
            "refusal_code": self.refusal_code,
            "refusal_reason": self.refusal_reason,
        }


def select_capability(capability: str, device: Any) -> CapabilitySelection:
    """Whether `device` may be asked for `capability`, and what that would cost.

    `device` is an `EnrolledDevice` (or anything with the same `declares` /
    `declared_version` / `autonomous_for` surface). `None` is not treated as
    permissive: with no device there is nothing that declared anything, and the
    selection refuses.
    """
    try:
        kind = CapabilityKind(capability)
        descriptor = describe(kind)
    except (ValueError, UnsupportedCapabilityError):
        return CapabilitySelection(
            capability=capability,
            available=False,
            refusal_code=NOT_IN_VOCABULARY,
            refusal_reason=(
                f"{capability!r} is not a capability this build implements. It is not "
                "unimplemented, it is inexpressible: there is no value of the "
                "capability vocabulary that means it."
            ),
        )

    risk = descriptor.risk.value if isinstance(descriptor.risk, RiskClass) else str(descriptor.risk)
    approval = (
        descriptor.approval.value
        if isinstance(descriptor.approval, ApprovalRequirement)
        else str(descriptor.approval)
    )

    if device is None or not getattr(device, "declares", None):
        return CapabilitySelection(
            capability=kind.value,
            available=False,
            version=descriptor.version,
            risk=risk,
            approval_requirement=approval,
            refusal_code=NOT_DECLARED,
            refusal_reason=(
                "no enrolled device was resolved, so nothing declares this capability. "
                "An unknown device is refused, never assumed capable."
            ),
        )

    if not device.declares(kind):
        return CapabilitySelection(
            capability=kind.value,
            available=False,
            version=descriptor.version,
            risk=risk,
            approval_requirement=approval,
            refusal_code=NOT_DECLARED,
            refusal_reason=(
                f"this device is not enrolled for {kind.value}. The executive does not "
                "substitute a capability the device does have."
            ),
        )

    declared_version = device.declared_version(kind)
    if declared_version != descriptor.version:
        return CapabilitySelection(
            capability=kind.value,
            available=False,
            version=descriptor.version,
            risk=risk,
            approval_requirement=approval,
            refusal_code=VERSION_MISMATCH,
            refusal_reason=(
                f"this device declares {kind.value} at version {declared_version!r}, and "
                f"this build implements version {descriptor.version}. A version is "
                "refused, never downgraded to one that should be close enough."
            ),
        )

    return CapabilitySelection(
        capability=kind.value,
        available=True,
        version=descriptor.version,
        risk=risk,
        approval_requirement=approval,
        device_autonomous=bool(getattr(device, "autonomous_for", lambda _k: False)(kind)),
    )


__all__ = [
    "NOT_DECLARED",
    "NOT_IN_VOCABULARY",
    "VERSION_MISMATCH",
    "CapabilitySelection",
    "select_capability",
]
