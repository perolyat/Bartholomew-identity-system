"""What an action request is, and everything it must carry to be considered.

An `ActionRequest` is a complete, self-describing statement of one thing
Bartholomew has been asked to do on one enrolled device. Every field below is
required by the governance contract, and the dataclass refuses to exist without
them -- there is no partially-specified request that governance then has to
fill in from context, because a field filled in from context is a field nobody
approved.

**Nothing here decides anything.** This is a validated value object: it knows
how to check its own shape, how to fingerprint its parameters and whether it
has expired, and nothing else. Whether it may run is `seam.py`'s question,
answered against the repository's existing Governance authorities.

Identity fields, and where they come from
-----------------------------------------
`tenant_id`, `device_id` and `requested_by` are resolved by the API boundary
from the platform's own authority -- the verified principal and this process's
runtime binding -- and passed in. They are **never** read from a request body.
A caller that could name its own tenant would have a cross-tenant write
primitive; the reasoning is identical to `inbound_auth.resolved_runtime_id`'s,
and this module is written so that the honest values are the only ones a
constructor can be handed.

Repeatability
-------------
`Repeatability.NON_REPEATABLE` is the default and covers every capability that
does something once: launching an app twice is two apps, typing text twice is
the text twice. A non-repeatable action can be leased exactly once, and the
device keeps its own durable ledger of what it has executed, so a duplicate
delivery on either side is a no-op rather than a second execution.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from .capabilities import (
    IDEMPOTENT_ELIGIBLE,
    ApprovalRequirement,
    CapabilityKind,
    RiskClass,
    require_supported,
)
from .parameters import ValidatedParameters, ValidationContext, validate

#: How long a request may stay actionable if the caller names no expiry. Short
#: on purpose: an action nobody approved within fifteen minutes is an action
#: whose moment has passed, and a long-lived pending action is a long-lived
#: opportunity.
DEFAULT_TTL_SECONDS = 900

#: The longest expiry any caller may ask for. A request cannot buy itself an
#: unbounded window by naming a distant date.
MAX_TTL_SECONDS = 3600

#: Identifier shape for the caller-visible ids. Deliberately narrow so an id
#: can be used in a log line, a filename and a URL path segment without
#: escaping, and so nothing that looks like a path or a query can be smuggled
#: through one.
_ID_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


class RequestError(ValueError):
    """A request that cannot be considered at all. Always a refusal."""


class Repeatability(str, Enum):
    """Whether re-delivering this action may run it a second time."""

    #: Runs at most once, ever. Enforced on the server (one lease) and again
    #: on the device (a durable executed-ledger).
    NON_REPEATABLE = "non_repeatable"
    #: Running it twice has the same effect as running it once, so a redelivery
    #: after an ambiguous outcome is safe.
    IDEMPOTENT = "idempotent"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(moment: datetime) -> str:
    """Second-resolution UTC, matching the shape the rest of the repo stores."""
    return (
        moment.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace(
            "+00:00",
            "Z",
        )
    )


def parse_iso(raw: str) -> datetime:
    """Parse a stored timestamp, or refuse it.

    A timestamp that cannot be read is refused rather than defaulted: an
    unreadable expiry must not become "no expiry", which is the fail-open shape
    this whole module exists to avoid.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise RequestError("timestamp is missing")
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as e:
        raise RequestError(f"{raw!r} is not an ISO-8601 timestamp") from e
    if parsed.tzinfo is None:
        raise RequestError(
            f"{raw!r} has no timezone. A naive timestamp would be read as local time "
            "on whichever machine happened to read it.",
        )
    return parsed.astimezone(timezone.utc)


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _ID_RE.match(value.strip()):
        raise RequestError(
            f"{name} must be 1-128 characters of letters, digits, '.', ':', '-' or "
            f"'_', and must start with a letter or digit; got {value!r}",
        )
    return value.strip()


def new_action_id() -> str:
    """A fresh, unguessable action id.

    Server-minted by default. A caller may supply its own so a retry of the
    same logical request collapses onto the same action rather than creating a
    second one -- which is exactly how the idempotency contract is meant to be
    used, and why the id is part of what an approval binds to.
    """
    return f"act-{uuid.uuid4().hex}"


def new_correlation_id() -> str:
    return f"cor-{uuid.uuid4().hex}"


@dataclass(frozen=True)
class ActionRequest:
    """One governed request to act on one enrolled Windows device.

    Frozen. An action is a statement of what was asked for at a moment, and
    nothing downstream may edit it -- least of all after it has been approved,
    since the approval binds to its parameter fingerprint.
    """

    action_id: str
    tenant_id: str
    device_id: str
    capability: CapabilityKind
    capability_version: int
    parameters: ValidatedParameters
    correlation_id: str
    requested_by: str
    risk_class: RiskClass
    approval_requirement: ApprovalRequirement
    issued_at: str
    expires_at: str
    repeatability: Repeatability = Repeatability.NON_REPEATABLE
    #: The action or event this one follows from, when there is one. Never
    #: invented: None means "nothing caused this but the request itself".
    causation_id: str | None = None
    errors: tuple[str, ...] = field(default_factory=tuple)

    # -- derived ---------------------------------------------------------

    @property
    def parameter_fingerprint(self) -> str:
        """The digest an approval binds to."""
        return self.parameters.fingerprint()

    def expiry(self) -> datetime:
        return parse_iso(self.expires_at)

    def has_expired(self, *, now: datetime | None = None) -> bool:
        return (now or utc_now()) >= self.expiry()

    @property
    def requires_approval(self) -> bool:
        """Whether an approval is required *ignoring* any trusted autonomy.

        Always True in this build for every capability. Trusted autonomy is
        resolved per device in `seam.py`, against the enrolment, and can only
        ever apply to the three eligible kinds.
        """
        return True

    def binding(self) -> dict[str, Any]:
        """Exactly the facts an approval must match. One place, so two cannot drift."""
        return {
            "action_id": self.action_id,
            "tenant_id": self.tenant_id,
            "device_id": self.device_id,
            "capability": self.capability.value,
            "capability_version": self.capability_version,
            "parameter_fingerprint": self.parameter_fingerprint,
        }

    def to_dict(self, *, redacted: bool = True) -> dict[str, Any]:
        """The wire/storage form.

        `redacted=True` -- the default -- is what a list endpoint, a Reflection
        and an evidence row get: sensitive parameter values are replaced by
        their digest and length. `redacted=False` is reached only by the code
        that must actually hand the parameters to the device, and by the
        approval surface, which shows the approver what they are approving.
        """
        return {
            "action_id": self.action_id,
            "tenant_id": self.tenant_id,
            "device_id": self.device_id,
            "capability": self.capability.value,
            "capability_version": self.capability_version,
            "parameters": (
                dict(self.parameters.redacted) if redacted else dict(self.parameters.canonical)
            ),
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "requested_by": self.requested_by,
            "risk_class": self.risk_class.value,
            "approval_requirement": self.approval_requirement.value,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "repeatability": self.repeatability.value,
            "parameter_fingerprint": self.parameter_fingerprint,
        }


def build_request(
    *,
    tenant_id: str,
    device_id: str,
    requested_by: str,
    capability: str,
    capability_version: Any,
    parameters: Any,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    action_id: str | None = None,
    repeatability: str | None = None,
    ttl_seconds: int | None = None,
    context: ValidationContext | None = None,
    now: datetime | None = None,
) -> ActionRequest:
    """Build a validated `ActionRequest`, or refuse the whole thing.

    Every refusal below happens *before* any state is written and long before
    any device sees anything. The order is deliberate: the capability and
    version are resolved first, because an unknown capability has no parameter
    contract to validate against and refusing it early keeps an unknown kind
    from reaching a validator that might be permissive.

    `tenant_id`, `device_id` and `requested_by` are required positional-by-name
    arguments with no defaults, so a call site cannot forget one and get an
    anonymous action.
    """
    descriptor = require_supported(capability, capability_version)

    tenant = _identifier(tenant_id, "tenant_id")
    device = _identifier(device_id, "device_id")
    requester = _identifier(requested_by, "requested_by")
    action = _identifier(action_id, "action_id") if action_id else new_action_id()
    correlation = (
        _identifier(correlation_id, "correlation_id") if correlation_id else new_correlation_id()
    )
    causation = _identifier(causation_id, "causation_id") if causation_id else None

    try:
        mode = Repeatability(repeatability) if repeatability else Repeatability.NON_REPEATABLE
    except ValueError as e:
        raise RequestError(
            f"{repeatability!r} is not a repeatability mode; the permitted modes are "
            f"{[m.value for m in Repeatability]}",
        ) from e
    if mode is Repeatability.IDEMPOTENT and descriptor.kind not in IDEMPOTENT_ELIGIBLE:
        # Refused, not quietly downgraded. `idempotent` relaxes the server's
        # one-lease guard *and* is what the device's durable ledger checks
        # before refusing a repeat, so a caller who could set it on
        # `windows.type_text` could have one human approval spend itself twice.
        # A caller that asked for it on an ineligible capability has
        # misunderstood something, and should be told so rather than served a
        # different action than the one it asked for.
        raise RequestError(
            f"{descriptor.kind.value} may not be declared idempotent: performing it "
            "twice is not the same as performing it once. Only "
            f"{sorted(k.value for k in IDEMPOTENT_ELIGIBLE)} may be, and everything "
            "else runs at most once.",
        )

    ttl = DEFAULT_TTL_SECONDS if ttl_seconds is None else int(ttl_seconds)
    if not (1 <= ttl <= MAX_TTL_SECONDS):
        raise RequestError(
            f"ttl_seconds must be between 1 and {MAX_TTL_SECONDS}; got {ttl}. An "
            "action that stays actionable indefinitely is an indefinite opportunity.",
        )

    validated = validate(descriptor.kind, parameters, context)

    issued = now or utc_now()
    return ActionRequest(
        action_id=action,
        tenant_id=tenant,
        device_id=device,
        capability=descriptor.kind,
        capability_version=descriptor.version,
        parameters=validated,
        correlation_id=correlation,
        causation_id=causation,
        requested_by=requester,
        risk_class=descriptor.risk,
        approval_requirement=descriptor.approval,
        issued_at=to_iso(issued),
        expires_at=to_iso(issued + timedelta(seconds=ttl)),
        repeatability=mode,
    )


def rebuild_request(
    stored: dict[str, Any],
    *,
    context: ValidationContext | None = None,
) -> ActionRequest:
    """Reconstruct a request from its stored form, re-validating as it goes.

    Used when an action is read back to be approved, leased or dispatched.
    Deliberately re-runs the full validator rather than trusting the row:
    the allowlists may have been tightened since the request was written, and
    a request that would no longer be accepted must no longer be executable.
    A row that fails re-validation raises, which every caller turns into a
    refusal.
    """
    descriptor = require_supported(
        stored.get("capability"),
        stored.get("capability_version"),
    )
    validated = validate(descriptor.kind, stored.get("parameters") or {}, context)
    expires_at = stored.get("expires_at")
    issued_at = stored.get("issued_at")
    parse_iso(expires_at)  # refuse an unreadable expiry rather than defaulting one
    parse_iso(issued_at)
    try:
        mode = Repeatability(stored.get("repeatability") or Repeatability.NON_REPEATABLE.value)
    except ValueError as e:
        raise RequestError(
            f"stored repeatability {stored.get('repeatability')!r} is not a mode",
        ) from e
    if mode is Repeatability.IDEMPOTENT and descriptor.kind not in IDEMPOTENT_ELIGIBLE:
        # A stored row claiming idempotence for a capability that is not
        # eligible predates the rule, or was written by something that should
        # not have. Either way it does not get the relaxed lease guard: the
        # row is read back as what it actually is.
        raise RequestError(
            f"the stored action declares {descriptor.kind.value} idempotent, which is "
            "not permitted; it will not be dispatched under a relaxed replay guard",
        )
    return ActionRequest(
        action_id=_identifier(stored.get("action_id"), "action_id"),
        tenant_id=_identifier(stored.get("tenant_id"), "tenant_id"),
        device_id=_identifier(stored.get("device_id"), "device_id"),
        capability=descriptor.kind,
        capability_version=descriptor.version,
        parameters=validated,
        correlation_id=_identifier(stored.get("correlation_id"), "correlation_id"),
        causation_id=(
            _identifier(stored["causation_id"], "causation_id")
            if stored.get("causation_id")
            else None
        ),
        requested_by=_identifier(stored.get("requested_by"), "requested_by"),
        risk_class=descriptor.risk,
        approval_requirement=descriptor.approval,
        issued_at=issued_at,
        expires_at=expires_at,
        repeatability=mode,
    )
