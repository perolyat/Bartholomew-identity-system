"""Read-back: what does that window say *now*? -- the Verify leg's read.

W03-C's Act -> Verify step needs to read a field or a window back after an
action rather than assume the action succeeded. This is that read. It is the
`observation-event` shared contract's read query, and it is deliberately
**not a second screen reader**: it observes through the same
`ObservationLoop` machinery an active session already runs, under the same
consent, scope, brake and identity governance, and it emits what it read
onto the same canonical backbone as any other observation, so a Verify
verdict has durable evidence behind it.

Governance, in order, before anything is touched:

1. **Who is asking.** A `requested_by` principal that is a model, an event,
   a companion or the system is refused outright -- the same rule that stops
   content from starting capture stops content from reading a screen.
2. **A consented, live session must already cover the target.** Read-back
   never starts a session. If no ACTIVE screen session for this tenant and
   device has an approved scope that `covers()` the requested target, the
   answer is `unavailable` with the reason, and the caller (or the person)
   starts one through the ordinary consented path. There is no code here
   that could observe without an existing human "yes".
3. **The Parking Brake is re-read at the moment of the read**, fail-closed.
   Engaged: the covering session is ended through `stop_all_for_brake` and
   the read is unavailable. Unreadable: unavailable.
4. **Scope cannot widen.** The requested target is checked against the
   approved scope by `capture_with_fallback`; a wider or different target is
   refused, not approximated.

What comes back is an `ObservationEvent` of kind `read_back` **with no
inference**: a read-back is evidence for a verdict, and a verdict built on a
guess would be verifying against the wrong thing. `text` is the observed
content as one bounded block (`observation.observed_text`). An honest
`unavailable` is the answer whenever any of the above says no, whenever the
accessibility provider cannot read the window, and whenever the screenshot
fallback would be needed but was not authorised for the session.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .inference import NullInferencer
from .loop import BrakeUnreadableError, ObservationLoop, default_brake_check
from .modality import BRAKE_SCOPE, CaptureScope, Modality
from .observation import OBSERVED_READ_BACK, ObservationEvent, observed_text
from .screen import CaptureRefusedError
from .session import MultimodalSession, SessionState
from .store import SessionStore, default_store

logger = logging.getLogger(__name__)

#: Machine-readable reasons a read-back is unavailable. Consumers (W03-C)
#: branch on these; the `reason` text beside them is for people.
UNAVAILABLE_REQUESTER_REFUSED = "requester_refused"
UNAVAILABLE_NO_CONSENTED_SESSION = "no_consented_session"
UNAVAILABLE_OUTSIDE_SCOPE = "outside_consented_scope"
UNAVAILABLE_BRAKE_ENGAGED = "parking_brake_engaged"
UNAVAILABLE_BRAKE_UNREADABLE = "parking_brake_unreadable"
UNAVAILABLE_PROVIDER = "provider_unavailable"
UNAVAILABLE_READ_FAILED = "read_failed"


@dataclass
class ReadBackResult:
    """The answer to "what does it say now?", or an honest "cannot say"."""

    available: bool
    text: str | None = None
    #: One of the UNAVAILABLE_* codes when `available` is False; else None.
    code: str | None = None
    reason: str | None = None
    event: ObservationEvent | None = field(default=None, repr=False)
    session_id: str | None = None
    #: The backbone event id the read-back was recorded under, when it was.
    event_id: str | None = None
    #: True when the read succeeded but could not be durably recorded.
    provenance_degraded: bool = False
    provenance_error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "text": self.text,
            "code": self.code,
            "reason": self.reason,
            "session_id": self.session_id,
            "event_id": self.event_id,
            "provenance_degraded": self.provenance_degraded,
            "provenance_error": self.provenance_error,
            "observation": self.event.as_dict() if self.event is not None else None,
        }


def _unavailable(code: str, reason: str, **extra: Any) -> ReadBackResult:
    return ReadBackResult(available=False, code=code, reason=reason, **extra)


def _covering_session(
    store: SessionStore,
    *,
    tenant_id: str,
    device_id: str,
    target: CaptureScope | None,
) -> tuple[MultimodalSession | None, bool]:
    """(the first ACTIVE screen session whose scope covers `target`, any_live).

    `any_live` distinguishes "no session at all" from "a session, but not for
    that target", so the reason a caller gets names the actual gap.
    """
    any_live = False
    for session in store.live(tenant_id):
        if session.modality is not Modality.SCREEN or session.state is not SessionState.ACTIVE:
            continue
        if session.device_id != device_id or session.scope is None:
            continue
        any_live = True
        if target is None or session.scope.covers(target):
            return session, True
    return None, any_live


def read_back(
    *,
    tenant_id: str,
    device_id: str,
    target: CaptureScope | None = None,
    requested_by: str | None = None,
    store: SessionStore | None = None,
    db_path: str | None = None,
    brake_check: Callable[[], bool] | None = None,
    accessibility_provider: Any | None = None,
    screen_backend: Any | None = None,
    sink: Any | None = None,
    emit: bool = True,
) -> ReadBackResult:
    """Read the current UI state of a consented target, or say why not.

    `target` names the window/display/region to read; None means "whatever
    the live session is consented to observe". `requested_by` is the
    principal on whose behalf the read happens (W03-C passes the action's
    principal); content principals are refused. `store` defaults to the
    process-wide registry. The provider/backend/sink default to those of the
    covering session's own loop, so a read-back sees exactly what the
    session sees.
    """
    from .runtime import FORBIDDEN_PRINCIPAL_PREFIXES

    if requested_by is not None:
        lowered = str(requested_by).strip().lower()
        if lowered.startswith(FORBIDDEN_PRINCIPAL_PREFIXES):
            return _unavailable(
                UNAVAILABLE_REQUESTER_REFUSED,
                f"principal {requested_by!r} is not an authenticated human principal; a model "
                "response, inbound event or companion observation cannot read a screen",
            )

    registry = store if store is not None else default_store()
    session, any_live = _covering_session(
        registry,
        tenant_id=tenant_id,
        device_id=device_id,
        target=target,
    )
    if session is None:
        if any_live:
            return _unavailable(
                UNAVAILABLE_OUTSIDE_SCOPE,
                "the live observation session's consented scope does not cover the requested "
                "target; a wider or different scope needs a new session and a new consent decision",
            )
        return _unavailable(
            UNAVAILABLE_NO_CONSENTED_SESSION,
            "no active, consented screen observation session exists for this device; read-back "
            "never starts one -- start a session through the ordinary consented path first",
        )

    # The session's own loop, so the read uses the same governed backends. A
    # session driven without a loop (tests, a caller driving ticks itself)
    # gets a loop-shaped reader over the same session and store.
    loop = registry.observer(session.session_id)
    if not isinstance(loop, ObservationLoop):
        loop = ObservationLoop(
            session,
            registry,
            allow_screenshot_fallback=False,
            accessibility_provider=accessibility_provider,
            screen_backend=screen_backend,
            sink=sink,
            db_path=db_path,
            brake_check=brake_check,
        )
    elif accessibility_provider is not None or screen_backend is not None or sink is not None:
        # An explicit backend for this read wins, for the same reason an
        # explicit resolver wins in `start_session`: a caller that named one
        # is never overridden by what happens to be installed.
        loop = ObservationLoop(
            session,
            registry,
            allow_screenshot_fallback=loop.allow_screenshot_fallback,
            accessibility_provider=accessibility_provider or loop.accessibility_provider,
            screen_backend=screen_backend or loop.screen_backend,
            sink=sink if sink is not None else loop.sink,
            db_path=db_path,
            brake_check=brake_check or loop.brake_check,
            config=loop.config,
        )

    # Governance re-read at the moment of the read: the brake, fail-closed.
    scope = BRAKE_SCOPE[session.modality]
    check = brake_check or (loop.brake_check if isinstance(loop, ObservationLoop) else None)
    if check is None:  # pragma: no cover - the loop always has one
        check = default_brake_check(scope, db_path)
    try:
        engaged = bool(check())
    except BrakeUnreadableError as exc:
        return _unavailable(
            UNAVAILABLE_BRAKE_UNREADABLE,
            f"the parking brake could not be read ({exc}); refusing to read the screen",
            session_id=session.session_id,
        )
    except Exception as exc:
        return _unavailable(
            UNAVAILABLE_BRAKE_UNREADABLE,
            f"the parking brake check errored ({type(exc).__name__}: {exc}); refusing to read",
            session_id=session.session_id,
        )
    if engaged:
        registry.stop_all_for_brake(scope)
        return _unavailable(
            UNAVAILABLE_BRAKE_ENGAGED,
            f"the parking brake is engaged (scope={scope}); the observation session was stopped",
            session_id=session.session_id,
        )

    try:
        event = loop.observe_once(
            requested_scope=target,
            kind=OBSERVED_READ_BACK,
            inferencer=NullInferencer(),
        )
    except CaptureRefusedError as exc:
        return _unavailable(UNAVAILABLE_OUTSIDE_SCOPE, str(exc), session_id=session.session_id)
    except Exception as exc:
        logger.exception("read-back failed")
        return _unavailable(
            UNAVAILABLE_READ_FAILED,
            f"{type(exc).__name__}: {exc}",
            session_id=session.session_id,
        )

    facts = event.observed_event.facts
    readable = bool(facts.get("available")) or bool(facts.get("used_screenshot"))

    event_id: str | None = None
    degraded = False
    degraded_error: str | None = None
    if emit:
        try:
            _event_type, event_id = loop.emit(event)
        except Exception as exc:
            # The read stands; the missing durable record is reported, the
            # way a seam reports a reflection write it could not make.
            degraded, degraded_error = True, f"{type(exc).__name__}: {exc}"
            logger.warning("read-back observation not recorded: %s", exc)

    if not readable:
        return ReadBackResult(
            available=False,
            code=UNAVAILABLE_PROVIDER,
            reason=event.observed_event.summary,
            event=event,
            session_id=session.session_id,
            event_id=event_id,
            provenance_degraded=degraded,
            provenance_error=degraded_error,
        )
    return ReadBackResult(
        available=True,
        text=observed_text(event),
        event=event,
        session_id=session.session_id,
        event_id=event_id,
        provenance_degraded=degraded,
        provenance_error=degraded_error,
    )


__all__ = [
    "UNAVAILABLE_BRAKE_ENGAGED",
    "UNAVAILABLE_BRAKE_UNREADABLE",
    "UNAVAILABLE_NO_CONSENTED_SESSION",
    "UNAVAILABLE_OUTSIDE_SCOPE",
    "UNAVAILABLE_PROVIDER",
    "UNAVAILABLE_READ_FAILED",
    "UNAVAILABLE_REQUESTER_REFUSED",
    "ReadBackResult",
    "read_back",
]
