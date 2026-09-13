"""What crosses the External Capability Interface, and deliberately nothing more.

This module is the **transport-independent** half of the boundary. Every type
below is a frozen value object that knows how to check its own shape and
nothing else: there is no decision, no store, no authority and no I/O here. A
WebSocket, local-IPC or queue adapter added later carries these same values
without this file changing, which is what "transport-conscious but
transport-independent" has to mean if it is to mean anything.

The three things an endpoint may say, and the one thing it may hear
-------------------------------------------------------------------
An external endpoint -- an avatar or voice presence, a companion application,
a browser, a home-automation bridge, a sensor -- may submit an
`InboundExchange` of one of three kinds:

* `OBSERVATION` -- "this happened where I am."
* `REQUEST`     -- "the person asked for this." Still an observation about the
                   person; it is not an instruction to Bartholomew.
* `RESULT`      -- "the directive you gave me finished, and here is how."

It may hear exactly one thing back: an `ExchangeReceipt`, which says honestly
what became of the submission and *may* carry a `GovernedDirective` that
Bartholomew decided to issue. It hears nothing else, and there is no field on
any type below through which an endpoint can be told that it has acquired
authority, because there is no such thing to tell it.

Why a request is an observation
-------------------------------
`REQUEST` carries what a person said to an endpoint. It does not carry what
Bartholomew should therefore do, and the boundary has no field in which an
endpoint could put that. "Taylor asked me to open the roof quote" is an
observation about Taylor; whether anything follows from it is an executive
question, answered by Bartholomew against identity, memory and governance --
not by the endpoint that happened to be in the room. An endpoint that could
name the intent would be an executive with extra steps.

Content is data, never authority
--------------------------------
`payload` is opaque, exactly as `inbound_events` treats it: stored, digested,
carried, and never branched on to choose a code path. Text inside it --
whatever a web page, a tool response, a device or a person put there -- is
material Bartholomew may reason *about*. It is never material that can
instruct Bartholomew, and nothing in this package inspects it for instructions
to follow. `bartholomew/kernel/inbound_interpretation.py` records the same
rule for captured events and is the precedent this follows.

What this is NOT
----------------
`GovernedDirective` is **not** an `ActionRequest`. The governed Windows action
channel (`bartholomew/actuation/`) keeps its own envelope, its own
eleven-point admission, its own approvals and its own lease, and this package
neither replaces nor wraps it -- see `bartholomew/eci/__init__.py` for the
relationship. A directive is the general, portable shape in which *any*
endpoint is asked to perform *one* capability it already declared, for
capabilities whose governance is owned by the Bartholomew seam that issued it.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

#: The contract revision an endpoint speaks. Integer, not a semantic version
#: string, and compared exactly -- the same reasoning
#: `platform/device_capabilities.py` records for capability versions: an
#: unknown revision is an unknown contract, and acting on one is how a narrow
#: agreement quietly becomes a broad one. There is no downgrade path.
ECI_CONTRACT_VERSION = 1

#: Every contract revision this build can speak. A frozen set so that adding
#: one is a deliberate, reviewable act rather than an incidental edit.
SUPPORTED_CONTRACT_VERSIONS: frozenset[int] = frozenset({1})

#: How long a directive stays actionable when its issuer names no expiry.
#: Short on purpose, matching the actuation channel's reasoning: a directive
#: nobody carried out within two minutes is one whose moment has passed, and a
#: long-lived pending directive is a long-lived opportunity.
DEFAULT_DIRECTIVE_TTL_SECONDS = 120

#: The longest expiry any issuer may ask for. A directive cannot buy itself an
#: unbounded window by naming a distant date.
MAX_DIRECTIVE_TTL_SECONDS = 900

#: Largest payload this boundary will carry, in canonical-JSON characters.
#: The transport bounds the raw body as well; this bounds what the core is
#: willing to walk, independently of how a value reached it.
MAX_PAYLOAD_CHARS = 64 * 1024

#: Longest opaque identifier accepted from, or minted for, an endpoint.
MAX_IDENTIFIER_LENGTH = 128

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,127}$")


class ContractError(ValueError):
    """A value does not satisfy the boundary contract and is refused entire.

    Deliberately distinct from a governance refusal: this means *nothing
    coherent was said*, so there is nothing to govern. A malformed submission
    is never partially accepted -- there is no half-valid exchange that some
    later branch then repairs from context.
    """


class ExchangeKind(str, Enum):
    """What an endpoint is saying. A closed set; an unknown kind is refused."""

    OBSERVATION = "observation"
    REQUEST = "request"
    RESULT = "result"


class ExchangeOutcome(str, Enum):
    """What honestly became of one submission.

    Every member is a distinct, reportable state, and the set is deliberately
    wide rather than collapsed into success/failure: an endpoint that cannot
    tell a halted Bartholomew from a malformed message from an unauthorised
    capability will retry the wrong one. None of these may be reported as any
    of the others, and there is no member meaning "probably fine".
    """

    #: Durably recorded. NOT "acted upon" -- see `ExchangeReceipt.detail`.
    ACCEPTED = "accepted"
    #: This (endpoint, exchange_id) was already recorded. No second exchange exists.
    DUPLICATE = "duplicate"
    #: Bartholomew could not safely establish which endpoint or runtime this is.
    REFUSED_IDENTITY = "refused_identity"
    #: The envelope is not a coherent exchange. Nothing was recorded.
    REFUSED_MALFORMED = "refused_malformed"
    #: The capability named is unknown, unsupported, undeclared or unauthorised.
    REFUSED_CAPABILITY = "refused_capability"
    #: An Identity policy decision refused it. Recorded as refused.
    REFUSED_GOVERNANCE = "refused_governance"
    #: A Parking Brake or platform halt is engaged. Nothing was recorded.
    REFUSED_BRAKE = "refused_brake"
    #: A result arrived that names no directive this runtime issued, or names
    #: one belonging to another endpoint. It is NOT applied.
    UNCORRELATED = "uncorrelated"
    #: A result arrived for a directive whose window had already closed.
    EXPIRED = "expired"
    #: A result for this directive was already recorded. The first one stands
    #: and this one was NOT applied. Deliberately distinct from `DUPLICATE`,
    #: which means no second *exchange* was created: here the exchange is new
    #: and real, and it is the settlement that was refused.
    ALREADY_SETTLED = "already_settled"
    #: Persistence or the runtime could not answer. Nothing was recorded.
    UNAVAILABLE = "unavailable"


#: Outcomes in which nothing whatever was written. An endpoint seeing one of
#: these may retry the identical submission without creating a second logical
#: exchange. Held here rather than at each call site so the transport and the
#: core agree on what "nothing happened" means.
NOTHING_RECORDED: frozenset[ExchangeOutcome] = frozenset(
    {
        ExchangeOutcome.REFUSED_IDENTITY,
        ExchangeOutcome.REFUSED_MALFORMED,
        ExchangeOutcome.REFUSED_BRAKE,
        ExchangeOutcome.UNAVAILABLE,
    },
)


class DirectiveStatus(str, Enum):
    """How an endpoint says a directive ended.

    `UNKNOWN` is not a synonym for `FAILED` and must never be folded into it.
    It means the endpoint cannot tell whether the effect landed -- the process
    died mid-utterance, the transport dropped after dispatch -- and the honest
    answer is that uncertainty. `bartholomew/actuation/recovery.py` already
    holds this distinction for the Windows channel and is the authority on
    what follows from it; this boundary reports the same three states so that
    contract continues to apply rather than being contradicted by a second,
    looser one.
    """

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


def utc_now() -> datetime:
    """The clock this boundary reads. One place, so tests can freeze one."""
    return datetime.now(timezone.utc)


def to_iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(raw: str) -> datetime:
    """Parse an endpoint-supplied timestamp, or refuse it.

    Naive input is read as UTC rather than as local time: a boundary that
    guessed a sender's timezone would order events by a guess.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ContractError("timestamp must be a non-empty ISO-8601 string")
    text = raw.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ContractError(f"timestamp is not ISO-8601: {raw!r}") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def identifier(value: Any, name: str) -> str:
    """An opaque identifier, or a refusal. Never a repaired value."""
    if not isinstance(value, str) or not _ID_RE.match(value.strip()):
        raise ContractError(
            f"{name} must be 1-{MAX_IDENTIFIER_LENGTH} characters of letters, digits, "
            f"'.', ':', '-' or '_', starting with a letter or digit; got {value!r}",
        )
    return value.strip()


def new_exchange_id() -> str:
    return f"exc-{uuid.uuid4().hex}"


def new_directive_id() -> str:
    return f"dir-{uuid.uuid4().hex}"


def new_correlation_id() -> str:
    """A fresh correlation id.

    Prefixed distinctly from the actuation channel's `cor-` ids so that a
    directive correlation and an action correlation can never be mistaken for
    one another in a log, a row or a bug report. They are different channels
    with different governance, and an id that reads the same in both would
    invite the assumption that they interchange.
    """
    return f"eci-{uuid.uuid4().hex}"


def canonical_json(payload: Any) -> str:
    """The exact bytes digested and stored. Sorted, so a digest is stable."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def payload_digest(payload: Any) -> str:
    """SHA-256 of the canonical payload.

    The digest, never the content, is what provenance records carry. It proves
    *which* material was accepted without copying a third party's data into a
    second store -- the posture `runtime_contract._record_inbound_reflection`
    already takes for captured events.
    """
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _validated_payload(payload: Any, *, field_name: str = "payload") -> dict[str, Any]:
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ContractError(f"{field_name} must be a JSON object")
    try:
        encoded = canonical_json(payload)
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{field_name} is not JSON-serialisable: {exc}") from exc
    if len(encoded) > MAX_PAYLOAD_CHARS:
        raise ContractError(
            f"{field_name} is {len(encoded)} characters; the boundary carries at most "
            f"{MAX_PAYLOAD_CHARS}",
        )
    return payload


@dataclass(frozen=True)
class EndpointIdentity:
    """Which external endpoint this is, and whose Bartholomew it belongs to.

    **Every field here is established by the platform, and none of them may be
    read from a submission.** `endpoint_id` comes from the verified device
    credential the platform issued and can revoke; `user_id` is the owning
    tenant recorded at enrolment; `verified_by` names what actually verified
    the caller and is recorded verbatim.

    There is no anonymous or provisional endpoint identity, on the same
    discipline as `platform.principal.Principal` and
    `platform.devices.VerifiedDevice`: "we could not tell which endpoint this
    is" cannot be represented in this type, so it cannot leak into a
    downstream `if` as a permissive default. A boundary that cannot say who is
    speaking refuses, and `ExchangeOutcome.REFUSED_IDENTITY` is how it says so.

    An endpoint cannot choose its runtime. `user_id` is the enrolment's, and
    `bartholomew/platform/device_inbound.py` records why at length: verifying
    *that a device is genuinely Taylor's laptop* says nothing about whose
    Bartholomew a message belongs in, and a claimed runtime would be a
    cross-user write primitive dressed as provenance.
    """

    endpoint_id: str
    user_id: str
    verified_by: str
    #: Free-text provenance about the machine, recorded verbatim, never an
    #: authorisation axis. A kind is not a permission.
    endpoint_kind: str = "unspecified"

    def __post_init__(self) -> None:
        object.__setattr__(self, "endpoint_id", identifier(self.endpoint_id, "endpoint_id"))
        object.__setattr__(self, "user_id", identifier(self.user_id, "user_id"))
        if not isinstance(self.verified_by, str) or not self.verified_by.strip():
            raise ContractError("verified_by must be a non-empty string")
        object.__setattr__(self, "verified_by", self.verified_by.strip())

    @property
    def source_id(self) -> str:
        """How this endpoint appears as a provenance source on captured rows.

        Prefixed so an ECI-originated row is legible without a join, and so an
        endpoint source can never collide with a webhook source id somebody
        configures by hand -- the same reasoning as
        `device_inbound.DEVICE_SOURCE_PREFIX`.
        """
        return f"eci:{self.endpoint_id}"


@dataclass(frozen=True)
class CapabilityRef:
    """One capability, named exactly: a kind and an integer contract version.

    Refused, never approximated. `multimodal.spoken_output` at version 2 is
    not version 1 with extras; it is a contract this build has not agreed to.
    """

    kind: str
    version: int

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not self.kind.strip():
            raise ContractError("capability kind must be a non-empty string")
        object.__setattr__(self, "kind", self.kind.strip())
        if isinstance(self.version, bool) or not isinstance(self.version, int):
            raise ContractError("capability version must be an integer")
        if self.version < 1:
            raise ContractError("capability version must be 1 or greater")

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "version": self.version}

    def __str__(self) -> str:  # pragma: no cover - operator display only
        return f"{self.kind} v{self.version}"


@dataclass(frozen=True)
class InboundExchange:
    """One thing an endpoint submitted, validated and ready to be governed.

    Frozen: an exchange is a statement of what an endpoint said at a moment,
    and nothing downstream may edit it before or after it is recorded.

    `endpoint` is *not* taken from the submission -- the transport resolves it
    from the verified credential and passes it in, so the honest value is the
    only one a constructor can be handed.
    """

    endpoint: EndpointIdentity
    exchange_id: str
    kind: ExchangeKind
    payload: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime | None = None
    #: Set only on `RESULT`: which directive this is the result of.
    correlation_id: str | None = None
    #: Set only on `RESULT`: how the directive ended.
    status: DirectiveStatus | None = None
    contract_version: int = ECI_CONTRACT_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "exchange_id", identifier(self.exchange_id, "exchange_id"))
        if not isinstance(self.kind, ExchangeKind):
            raise ContractError(f"kind must be an ExchangeKind; got {self.kind!r}")
        if self.contract_version not in SUPPORTED_CONTRACT_VERSIONS:
            raise ContractError(
                f"contract version {self.contract_version!r} is not supported by this build "
                f"(supported: {sorted(SUPPORTED_CONTRACT_VERSIONS)}). It is refused rather "
                "than downgraded.",
            )
        object.__setattr__(self, "payload", _validated_payload(self.payload))

        if self.kind is ExchangeKind.RESULT:
            if self.correlation_id is None:
                raise ContractError(
                    "a result must name the directive it is the result of "
                    "(correlation_id); an uncorrelatable result is refused, never "
                    "attributed to a plausible directive",
                )
            object.__setattr__(
                self,
                "correlation_id",
                identifier(self.correlation_id, "correlation_id"),
            )
            if not isinstance(self.status, DirectiveStatus):
                raise ContractError(
                    "a result must carry a DirectiveStatus of succeeded, failed or "
                    "unknown; 'unknown' is a real answer and is never folded into "
                    "'failed'",
                )
        else:
            if self.correlation_id is not None:
                raise ContractError(
                    f"correlation_id is meaningful only on a {ExchangeKind.RESULT.value!r} "
                    f"exchange; a {self.kind.value!r} may not claim to be the result of "
                    "anything",
                )
            if self.status is not None:
                raise ContractError(
                    f"status is meaningful only on a {ExchangeKind.RESULT.value!r} exchange",
                )

    @property
    def payload_sha256(self) -> str:
        return payload_digest(self.payload)

    @property
    def occurred_at_iso(self) -> str | None:
        return to_iso(self.occurred_at) if self.occurred_at is not None else None


@dataclass(frozen=True)
class GovernedDirective:
    """One thing Bartholomew has decided an endpoint should do.

    **Nothing in this package creates one on its own judgement.** A directive
    is produced by a Bartholomew-owned seam that has already run its own
    governance -- for the reference path, `runtime_contract.
    run_spoken_output_through_runtime_contract()`, whose enablement, Parking
    Brake and Identity-policy gates all pass before it calls out. This
    boundary carries the directive; it does not author it, and
    `boundary.py`'s responder seam is the only way one can enter.

    Frozen, expiring, and bound to one endpoint. It names a capability the
    endpoint has already declared and is authorised for -- checked before the
    directive is issued, not after -- and carries typed parameters, never a
    command, script, shell or free-form instruction field. There is no value
    of this type that means "do whatever the payload says".
    """

    directive_id: str
    correlation_id: str
    endpoint_id: str
    capability: CapabilityRef
    parameters: dict[str, Any] = field(default_factory=dict)
    issued_at: datetime = field(default_factory=utc_now)
    expires_at: datetime | None = None
    #: Which Bartholomew seam decided this. Recorded verbatim on the audit
    #: trail so a directive can always be traced to the authority that issued
    #: it rather than to the boundary that carried it.
    issued_by: str = "unattributed"

    def __post_init__(self) -> None:
        object.__setattr__(self, "directive_id", identifier(self.directive_id, "directive_id"))
        object.__setattr__(
            self,
            "correlation_id",
            identifier(self.correlation_id, "correlation_id"),
        )
        object.__setattr__(self, "endpoint_id", identifier(self.endpoint_id, "endpoint_id"))
        if not isinstance(self.capability, CapabilityRef):
            raise ContractError("capability must be a CapabilityRef")
        object.__setattr__(
            self,
            "parameters",
            _validated_payload(self.parameters, field_name="parameters"),
        )
        if not isinstance(self.issued_by, str) or not self.issued_by.strip():
            raise ContractError("issued_by must name the Bartholomew seam that decided this")
        object.__setattr__(self, "issued_by", self.issued_by.strip())

        if self.expires_at is None:
            object.__setattr__(
                self,
                "expires_at",
                self.issued_at + timedelta(seconds=DEFAULT_DIRECTIVE_TTL_SECONDS),
            )
        window = (self.expires_at - self.issued_at).total_seconds()
        if window <= 0:
            raise ContractError("a directive must expire after it was issued, not before")
        if window > MAX_DIRECTIVE_TTL_SECONDS:
            raise ContractError(
                f"a directive may stay actionable for at most {MAX_DIRECTIVE_TTL_SECONDS}s; "
                f"{window:.0f}s was asked for",
            )

    def has_expired(self, *, now: datetime | None = None) -> bool:
        return (now or utc_now()) >= self.expires_at

    def as_dict(self) -> dict[str, Any]:
        """The wire shape. What an endpoint is handed, and all it is handed."""
        return {
            "directive_id": self.directive_id,
            "correlation_id": self.correlation_id,
            "capability": self.capability.as_dict(),
            "parameters": dict(self.parameters),
            "issued_at": to_iso(self.issued_at),
            "expires_at": to_iso(self.expires_at),
            "contract_version": ECI_CONTRACT_VERSION,
        }


@dataclass(frozen=True)
class ExchangeReceipt:
    """What the endpoint is told, and the only thing it is told.

    `detail` is required to be plain about what did and did not happen.
    Acknowledgement never outruns the facts: an accepted observation has been
    *recorded*, not understood, believed or acted upon, and this type carries
    no field that could say otherwise. The same rule `routes/inbound.py`
    states for its 202 holds here, for the same reason.
    """

    outcome: ExchangeOutcome
    exchange_id: str
    detail: str
    reason: str | None = None
    #: Present only when a Bartholomew seam decided to issue one.
    directive: GovernedDirective | None = None
    #: True when the primary record was written but its provenance record was
    #: not. The exchange is not re-run for it and is not reported as failed --
    #: but it must not present as fully recorded either. WP-A2b's posture,
    #: which the inbound and device seams already hold to.
    provenance_degraded: bool = False
    provenance_error: str | None = None

    @property
    def recorded(self) -> bool:
        """Whether anything was durably written for this submission."""
        return self.outcome not in NOTHING_RECORDED

    def as_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "outcome": self.outcome.value,
            "exchange_id": self.exchange_id,
            "recorded": self.recorded,
            "detail": self.detail,
            "contract_version": ECI_CONTRACT_VERSION,
        }
        if self.reason:
            body["reason"] = self.reason
        if self.directive is not None:
            body["directive"] = self.directive.as_dict()
        if self.provenance_degraded:
            body["provenance_degraded"] = True
            body["provenance_error"] = self.provenance_error
        return body


__all__ = [
    "DEFAULT_DIRECTIVE_TTL_SECONDS",
    "ECI_CONTRACT_VERSION",
    "MAX_DIRECTIVE_TTL_SECONDS",
    "MAX_IDENTIFIER_LENGTH",
    "MAX_PAYLOAD_CHARS",
    "NOTHING_RECORDED",
    "SUPPORTED_CONTRACT_VERSIONS",
    "CapabilityRef",
    "ContractError",
    "DirectiveStatus",
    "EndpointIdentity",
    "ExchangeKind",
    "ExchangeOutcome",
    "ExchangeReceipt",
    "GovernedDirective",
    "InboundExchange",
    "canonical_json",
    "identifier",
    "new_correlation_id",
    "new_directive_id",
    "new_exchange_id",
    "parse_iso",
    "payload_digest",
    "to_iso",
    "utc_now",
]
