"""One multimodal session: what it is bound to, and how it may change state.

**The session is the unit of permission.** Nothing in this package captures or
speaks outside one of these records. A session names -- and is refused if it
cannot name -- the tenant, the authenticated principal who asked, the resolved
device, the single modality, the bounded scope, the consent decision, the
governance decision, when it started, when it expires, and the correlation and
causation ids that let the whole chain be reconstructed later. A field left
`None` because something could not be resolved is a denial, not a default; the
resolution itself happens in `runtime.py`, which fails closed before a session
is ever constructed in an approved state.

**The state machine is validated, not documented.** `TRANSITIONS` below is the
complete set of legal edges. `MultimodalSession.transition()` refuses anything
else with `InvalidTransitionError` rather than logging and continuing, and appends
every accepted move to an in-record audit trail with a timestamp and a reason.
A session therefore cannot go from `stopped` back to `active`, cannot reach
`active` without passing through `approved`, and cannot leave a terminal state
at all.

The ten states:

* `requested`        -- a person asked; nothing has been resolved yet.
* `awaiting_approval`-- governance resolved; the consent decision is outstanding.
* `approved`         -- every gate passed; the adapter has not started yet.
* `active`           -- the adapter is running and the user-visible indicator is on.
* `stopping`         -- a stop was asked for; cleanup is in progress.
* `stopped`          -- terminal; cleaned up after a normal stop.
* `refused`          -- terminal; a gate said no (brake, policy, consent, scope).
* `failed`           -- terminal; something broke after approval.
* `unavailable`      -- terminal; the hardware or OS permission is genuinely not
                        there. Distinct from `failed` and distinct from `refused`
                        because "you have no microphone" is a different truth
                        from "the microphone broke" and from "you may not".
* `expired`          -- terminal; the maximum duration elapsed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from .modality import CaptureScope, Modality

#: A session is bounded by construction. Nothing may request an unbounded one:
#: `MAX_SESSION_SECONDS` is the ceiling `validate_duration()` enforces, so an
#: "until I say stop" session is not expressible.
DEFAULT_SESSION_SECONDS = 120
MAX_SESSION_SECONDS = 900


class SessionState(str, Enum):
    REQUESTED = "requested"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    ACTIVE = "active"
    STOPPING = "stopping"
    STOPPED = "stopped"
    REFUSED = "refused"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    EXPIRED = "expired"


#: States from which nothing further may happen. Checked by `is_terminal`.
TERMINAL_STATES: frozenset[SessionState] = frozenset(
    {
        SessionState.STOPPED,
        SessionState.REFUSED,
        SessionState.FAILED,
        SessionState.UNAVAILABLE,
        SessionState.EXPIRED,
    },
)

#: States in which capture or output is actually happening. The visible status
#: surface reports "listening"/"observing"/"speaking" for exactly these.
LIVE_STATES: frozenset[SessionState] = frozenset(
    {SessionState.ACTIVE, SessionState.STOPPING},
)

#: The complete legal edge set. Anything absent here is refused.
#:
#: Note what is deliberately missing: no edge back out of any terminal state,
#: no `requested -> active` shortcut past approval, and no
#: `stopped -> requested` restart (a new session is a new record with a new
#: consent decision, which is what forbids automatic session restart).
TRANSITIONS: dict[SessionState, frozenset[SessionState]] = {
    SessionState.REQUESTED: frozenset(
        {
            SessionState.AWAITING_APPROVAL,
            SessionState.REFUSED,
            SessionState.UNAVAILABLE,
            SessionState.FAILED,
        },
    ),
    SessionState.AWAITING_APPROVAL: frozenset(
        {
            SessionState.APPROVED,
            SessionState.REFUSED,
            SessionState.UNAVAILABLE,
            SessionState.FAILED,
            SessionState.EXPIRED,
        },
    ),
    SessionState.APPROVED: frozenset(
        {
            SessionState.ACTIVE,
            SessionState.REFUSED,
            SessionState.UNAVAILABLE,
            SessionState.FAILED,
            SessionState.EXPIRED,
            SessionState.STOPPING,
        },
    ),
    SessionState.ACTIVE: frozenset(
        {
            SessionState.STOPPING,
            SessionState.STOPPED,
            SessionState.FAILED,
            SessionState.EXPIRED,
            SessionState.UNAVAILABLE,
        },
    ),
    SessionState.STOPPING: frozenset(
        {SessionState.STOPPED, SessionState.FAILED, SessionState.EXPIRED},
    ),
    SessionState.STOPPED: frozenset(),
    SessionState.REFUSED: frozenset(),
    SessionState.FAILED: frozenset(),
    SessionState.UNAVAILABLE: frozenset(),
    SessionState.EXPIRED: frozenset(),
}


class InvalidTransitionError(RuntimeError):
    """An illegal state move was attempted. Never swallowed."""


def validate_duration(seconds: int | None) -> int:
    """The one place a session duration is bounded.

    Refuses non-positive and over-ceiling values rather than clamping them: a
    caller asking for an eight-hour listening session has asked for something
    this package does not do, and silently giving them fifteen minutes would
    hide that.
    """
    if seconds is None:
        return DEFAULT_SESSION_SECONDS
    if not isinstance(seconds, int) or isinstance(seconds, bool):
        raise ValueError("session duration must be an integer number of seconds")
    if seconds <= 0:
        raise ValueError("session duration must be positive")
    if seconds > MAX_SESSION_SECONDS:
        raise ValueError(
            f"session duration {seconds}s exceeds maximum {MAX_SESSION_SECONDS}s",
        )
    return seconds


@dataclass(frozen=True)
class StateChange:
    """One accepted move, kept for audit."""

    from_state: SessionState
    to_state: SessionState
    at: str
    reason: str | None = None


@dataclass
class MultimodalSession:
    """One explicit, bounded, inspectable multimodal session."""

    tenant_id: str
    principal_id: str
    device_id: str
    modality: Modality
    correlation_id: str
    #: Immediate parent (the request that caused this session), or None.
    causation_id: str | None = None
    #: Required for SCREEN. Meaningless and left None for the other two.
    scope: CaptureScope | None = None
    session_id: str = field(default_factory=lambda: f"mms_{uuid.uuid4().hex}")
    state: SessionState = SessionState.REQUESTED
    max_duration_seconds: int = DEFAULT_SESSION_SECONDS

    #: Recorded decisions. `None` means "not yet decided"; a session may only
    #: become APPROVED when both are truthy, which `runtime.py` enforces and
    #: `approve()` re-checks here so the invariant does not live in one place.
    consent_decision: bool | None = None
    governance_decision: bool | None = None

    #: RFC3339 UTC. `started_at` is set on entry to ACTIVE, not at request.
    requested_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
    started_at: str | None = None
    ended_at: str | None = None
    expires_at: str | None = None

    #: Why the session ended, in the user's words where possible.
    outcome_reason: str | None = None
    #: Ordered audit of every accepted transition.
    history: list[StateChange] = field(default_factory=list)
    #: The process that owns this session, for restart reconciliation.
    owner_pid: int | None = None

    def __post_init__(self) -> None:
        self.max_duration_seconds = validate_duration(self.max_duration_seconds)
        if self.modality is Modality.SCREEN and self.scope is None:
            raise ValueError("a screen session must name exactly one capture scope")
        if self.modality is not Modality.SCREEN and self.scope is not None:
            raise ValueError(f"{self.modality.value} session cannot carry a capture scope")
        for field_name in ("tenant_id", "principal_id", "device_id", "correlation_id"):
            if not getattr(self, field_name):
                raise ValueError(f"a multimodal session requires {field_name}")

    # -- state ---------------------------------------------------------------

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def is_live(self) -> bool:
        """Whether capture or output is genuinely happening right now."""
        return self.state in LIVE_STATES

    def transition(self, to_state: SessionState, reason: str | None = None) -> None:
        """Move to `to_state`, or refuse. The only way `state` ever changes."""
        allowed = TRANSITIONS.get(self.state, frozenset())
        if to_state not in allowed:
            raise InvalidTransitionError(
                f"session {self.session_id}: {self.state.value} -> {to_state.value} "
                f"is not a legal transition",
            )
        now = datetime.now(timezone.utc)
        self.history.append(
            StateChange(
                from_state=self.state,
                to_state=to_state,
                at=now.isoformat(),
                reason=reason,
            ),
        )
        self.state = to_state
        if reason:
            self.outcome_reason = reason
        if to_state is SessionState.ACTIVE:
            self.started_at = now.isoformat()
            self.expires_at = (now + timedelta(seconds=self.max_duration_seconds)).isoformat()
        if to_state in TERMINAL_STATES:
            self.ended_at = now.isoformat()

    def approve(self, reason: str | None = None) -> None:
        """Enter APPROVED, re-checking that both decisions genuinely passed.

        The gate order lives in `runtime.py`; this is the belt-and-braces
        check that a session can never be marked approved without both a
        governance pass and an explicit consent grant recorded on the record
        itself. A caller that forgot to record one gets a refusal here.
        """
        if self.governance_decision is not True:
            raise InvalidTransitionError("cannot approve without a recorded governance pass")
        if self.consent_decision is not True:
            raise InvalidTransitionError("cannot approve without recorded explicit consent")
        self.transition(SessionState.APPROVED, reason)

    def seconds_remaining(self, now: datetime | None = None) -> float | None:
        """Seconds until expiry, or None when not started. Never negative."""
        if not self.expires_at:
            return None
        moment = now or datetime.now(timezone.utc)
        remaining = (datetime.fromisoformat(self.expires_at) - moment).total_seconds()
        return max(0.0, remaining)

    def is_expired(self, now: datetime | None = None) -> bool:
        if not self.expires_at:
            return False
        moment = now or datetime.now(timezone.utc)
        return datetime.fromisoformat(self.expires_at) <= moment

    def snapshot(self) -> dict[str, Any]:
        """The inspectable view. Used by the status API and the audit record."""
        return {
            "session_id": self.session_id,
            "tenant_id": self.tenant_id,
            "principal_id": self.principal_id,
            "device_id": self.device_id,
            "modality": self.modality.value,
            "state": self.state.value,
            "is_live": self.is_live,
            "scope": self.scope.describe() if self.scope else None,
            "requested_at": self.requested_at,
            "started_at": self.started_at,
            "expires_at": self.expires_at,
            "ended_at": self.ended_at,
            "seconds_remaining": self.seconds_remaining(),
            "max_duration_seconds": self.max_duration_seconds,
            "consent_decision": self.consent_decision,
            "governance_decision": self.governance_decision,
            "outcome_reason": self.outcome_reason,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "history": [
                {
                    "from": change.from_state.value,
                    "to": change.to_state.value,
                    "at": change.at,
                    "reason": change.reason,
                }
                for change in self.history
            ],
        }
