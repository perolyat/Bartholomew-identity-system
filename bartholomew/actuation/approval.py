"""One approval authorises one action, and the list of what it cannot is longer.

Bartholomew may conclude that an action *would* be useful. It may not conclude
that the action is *authorised*. This module is the data authority for the
second half of that sentence, and it follows the precedent already recorded for
learning acceptance (`bartholomew/kernel/learning_authorization.py`): an
approval is a single record naming who approved what, when, and on the strength
of exactly which content -- not a role, not a session, not a standing grant.

Why this is not a permission
----------------------------
`Identity.yaml`'s `tool_use.allowlist` is a standing grant: an entry says "this
kind of action is permitted from now on". That is the right shape for
*requesting* an action, which creates a pending record that does nothing. It is
the wrong shape for *dispatching* one, so `windows_action_dispatch` is
deliberately absent from the allowlist and adding it would not make dispatch
reachable: `seam.evaluate_action_admission()` requires an
`ActionApproval` bound to the exact request regardless of what the allowlist
says. There is no "actuation enabled" switch to find.

What an approval binds to, and therefore what it cannot authorise
-----------------------------------------------------------------
Six facts, checked together and independently reported on failure:

======================  ================================================
bound to                so it cannot authorise
======================  ================================================
`action_id`             another action, including a re-request
`tenant_id`             another tenant
`device_id`             another device
`capability`            another capability
`capability_version`    the same capability under a different contract
`parameter_fingerprint` the same action with any parameter changed
======================  ================================================

plus `expires_at`, so it cannot authorise execution after it lapses, and
`approver`, so authorisation is never anonymous.

**An approval is not a brake override.** `seam.py` re-reads the Parking Brake
immediately before dispatch and refuses on an engagement regardless of any
approval. That ordering is the contract, and it is asserted in
`tests/test_windows_action_governance.py` rather than left to this paragraph.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from bartholomew.kernel.memory.privacy_guard import register_structural_schema

from .capabilities import CapabilityKind, parse_kind
from .request import ActionRequest, parse_iso, to_iso, utc_now

#: The `MemoryStore` kind an action approval is stored under.
#:
#: Like `learning_authorization.KIND`, deliberately absent from the competency
#: kinds: an approval is a governance record, never reasoning material, and
#: must never be retrievable as knowledge.
KIND: str = "windows_action_approval"

#: The longest an approval may outlive its grant. An approval is a decision
#: about a moment, and a moment does not last an hour.
MAX_APPROVAL_TTL_SECONDS = 900


class ApprovalError(ValueError):
    """An approval that cannot be granted. Always a refusal."""


@dataclass(frozen=True)
class ApprovalCheck:
    """Whether one approval authorises one request, and why not if not.

    `reason` is always populated on refusal and is written verbatim into the
    refusal's Reflection, so an audit can tell "nobody approved this" apart
    from "the parameters changed after it was approved" apart from "that
    approval was for a different device".
    """

    allowed: bool
    reason: str | None = None
    #: A short, stable token for the audit row, so refusals can be counted by
    #: cause without parsing prose.
    code: str | None = None


@dataclass(frozen=True)
class ActionApproval:
    """One explicit authorisation to perform one specific action, once."""

    action_id: str
    tenant_id: str
    device_id: str
    capability: CapabilityKind
    capability_version: int
    parameter_fingerprint: str
    #: Who approved it. Never inferred, never defaulted, never "system".
    approver: str
    granted_at: str
    expires_at: str
    note: str | None = None

    # -- persistence ------------------------------------------------------

    def key(self) -> str:
        """One live approval per action, keyed by tenant and action.

        Tenant-qualified so two tenants' approvals can never share a row even
        if an action id were somehow reused across them.
        """
        return f"{self.tenant_id}::{self.action_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "tenant_id": self.tenant_id,
            "device_id": self.device_id,
            "capability": self.capability.value,
            "capability_version": self.capability_version,
            "parameter_fingerprint": self.parameter_fingerprint,
            "approver": self.approver,
            "granted_at": self.granted_at,
            "expires_at": self.expires_at,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActionApproval:
        return cls(
            action_id=str(data["action_id"]),
            tenant_id=str(data["tenant_id"]),
            device_id=str(data["device_id"]),
            capability=parse_kind(str(data["capability"])),
            capability_version=int(data["capability_version"]),
            parameter_fingerprint=str(data["parameter_fingerprint"]),
            approver=str(data["approver"]),
            granted_at=str(data["granted_at"]),
            expires_at=str(data["expires_at"]),
            note=data.get("note"),
        )

    def to_summary_text(self) -> str:
        return (
            f"Action approval for {self.capability.value} v{self.capability_version} "
            f"on device {self.device_id} granted by {self.approver} at {self.granted_at}"
        )

    # -- the decision -----------------------------------------------------

    def authorizes(
        self,
        request: ActionRequest | None,
        *,
        now: datetime | None = None,
    ) -> ApprovalCheck:
        """Whether this approval authorises `request`, right now.

        Every mismatch is reported with its own code rather than a shared
        "invalid approval", because the three interesting failures -- wrong
        action, changed parameters, lapsed -- mean very different things to
        whoever reads the audit.
        """
        if request is None:
            return ApprovalCheck(
                False,
                "an approval must be bound to an action request",
                "approval_unbound",
            )
        moment = now or utc_now()

        if self.action_id != request.action_id:
            return ApprovalCheck(
                False,
                f"this approval authorises action {self.action_id!r}, not "
                f"{request.action_id!r}",
                "approval_wrong_action",
            )
        if self.tenant_id != request.tenant_id:
            return ApprovalCheck(
                False,
                "this approval was granted in a different tenant",
                "approval_wrong_tenant",
            )
        if self.device_id != request.device_id:
            return ApprovalCheck(
                False,
                f"this approval authorises device {self.device_id!r}, not "
                f"{request.device_id!r}",
                "approval_wrong_device",
            )
        if self.capability is not request.capability:
            return ApprovalCheck(
                False,
                f"this approval authorises {self.capability.value}, not "
                f"{request.capability.value}",
                "approval_wrong_capability",
            )
        if self.capability_version != request.capability_version:
            return ApprovalCheck(
                False,
                f"this approval authorises {self.capability.value} version "
                f"{self.capability_version}, not version {request.capability_version}",
                "approval_wrong_capability_version",
            )
        if self.parameter_fingerprint != request.parameter_fingerprint:
            return ApprovalCheck(
                False,
                "the action's parameters have changed since it was approved by "
                f"{self.approver!r}; a new approval is required",
                "approval_parameters_changed",
            )

        try:
            approval_expiry = parse_iso(self.expires_at)
        except ValueError:
            # An unreadable expiry is treated as lapsed. An approval whose
            # window cannot be read is not evidence of an open window.
            return ApprovalCheck(
                False,
                "this approval's expiry could not be read, so it is treated as lapsed",
                "approval_expiry_unreadable",
            )
        if moment >= approval_expiry:
            return ApprovalCheck(
                False,
                f"this approval lapsed at {self.expires_at}",
                "approval_expired",
            )

        if request.has_expired(now=moment):
            return ApprovalCheck(
                False,
                f"the action itself expired at {request.expires_at}",
                "action_expired",
            )
        return ApprovalCheck(True)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.action_id:
            errors.append("action_id is required")
        if not self.tenant_id:
            errors.append("tenant_id is required")
        if not self.device_id:
            errors.append("device_id is required")
        if not self.parameter_fingerprint:
            errors.append(
                "parameter_fingerprint is required -- an approval must bind to content",
            )
        if not self.approver:
            errors.append("approver is required -- authorisation is never anonymous")
        return errors


def build_approval(
    request: ActionRequest,
    *,
    approver: str,
    note: str | None = None,
    ttl_seconds: int | None = None,
    now: datetime | None = None,
) -> ActionApproval:
    """Mint an approval bound to `request` as it stands at this moment.

    Built *from* the request rather than from caller-supplied fields, so an
    approval cannot be minted for parameters the approver never saw. The only
    thing the caller contributes is who they are and how long they mean it for.

    The approval never outlives the action: an approval window longer than the
    action's own would be a window with nothing behind it.
    """
    if not isinstance(approver, str) or not approver.strip():
        raise ApprovalError(
            "an approval must name its approver; authorisation is never anonymous",
        )
    moment = now or utc_now()
    if request.has_expired(now=moment):
        raise ApprovalError(
            f"action {request.action_id} expired at {request.expires_at} and can no "
            "longer be approved",
        )

    ttl = MAX_APPROVAL_TTL_SECONDS if ttl_seconds is None else int(ttl_seconds)
    if not (1 <= ttl <= MAX_APPROVAL_TTL_SECONDS):
        raise ApprovalError(
            f"an approval's ttl_seconds must be between 1 and {MAX_APPROVAL_TTL_SECONDS}",
        )
    expiry = min(moment + timedelta(seconds=ttl), request.expiry())

    approval = ActionApproval(
        action_id=request.action_id,
        tenant_id=request.tenant_id,
        device_id=request.device_id,
        capability=request.capability,
        capability_version=request.capability_version,
        parameter_fingerprint=request.parameter_fingerprint,
        approver=approver.strip(),
        granted_at=to_iso(moment),
        expires_at=to_iso(expiry),
        note=(note.strip()[:280] if isinstance(note, str) and note.strip() else None),
    )
    problems = approval.validate()
    if problems:  # pragma: no cover - build_approval fills every field above
        raise ApprovalError("; ".join(problems))
    return approval


APPROVAL_SCHEMA_KEYS: frozenset[str] = frozenset(
    ActionApproval(
        action_id="a",
        tenant_id="t",
        device_id="d",
        capability=CapabilityKind.FOCUS_WINDOW,
        capability_version=1,
        parameter_fingerprint="f",
        approver="p",
        granted_at="1970-01-01T00:00:00Z",
        expires_at="1970-01-01T00:00:00Z",
    )
    .to_dict()
    .keys(),
)

register_structural_schema(KIND, APPROVAL_SCHEMA_KEYS)
