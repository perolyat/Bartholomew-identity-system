"""Starting a session: resolve, gate, bind, and only then touch a device.

This is the one door. Every capture and every utterance in Package C comes
through `start_session()`, and `start_session()` cannot be reached with a
half-resolved caller: `SessionRequest` is refused at construction if it
cannot name a tenant, a principal, a device and a modality, and the governed
seam is consulted before any adapter object is created.

**Order matters and is fixed.** Resolve tenant and principal, resolve the
device's declared capability (§3.3), then hand all of it to
`runtime_contract.run_multimodal_session_through_runtime_contract()`, which
runs capability -> brake -> Identity policy -> explicit consent. Only a
`governance_allowed` result reaches the adapter. Each denial produces a
terminal session in the state that tells the truth about *why*: `refused` for
a gate that said no, `unavailable` for hardware that is not there.

**Nothing here can be triggered by content.** `start_session()` takes a
`SessionRequest` built from an authenticated principal's explicit ask. It has
no code path that reads a model response, an inbound event payload, a
companion observation or any other untrusted text -- and
`tests/test_multimodal_no_autonomous_start.py` asserts that the API route and
the seam refuse a request whose principal is a model or an event. Contract §7:
"No model response can start capture directly."

**Denials are recorded before they are returned.** The governed seam writes
one `ActionReflection` for every outcome through the existing sink, so a
refused session is as durably recorded as a granted one.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .devices import DeviceCapabilityResolver, resolve_modality_capability
from .microphone import (
    MicrophoneCaptureFailedError,
    MicrophoneSessionAdapter,
    MicrophoneUnavailableError,
)
from .modality import CaptureScope, Modality
from .session import (
    MultimodalSession,
    SessionState,
    validate_duration,
)
from .store import SessionStore

logger = logging.getLogger(__name__)

#: Principal kinds that may never start a capture session, whatever else is
#: true. A model's own output and a captured event are content, not people:
#: letting either occupy the `principal_id` of a session would make capture
#: reachable from text (invariant 5, contract §7). Checked in
#: `SessionRequest.__post_init__`, so such a request cannot even be built.
FORBIDDEN_PRINCIPAL_PREFIXES: tuple[str, ...] = (
    "model:",
    "assistant:",
    "event:",
    "inbound:",
    "companion:",
    "system:",
)


class AutonomousStartRefusedError(PermissionError):
    """Something that is not an authenticated human tried to start capture."""


@dataclass
class SessionRequest:
    """An explicit, fully-resolved ask for one multimodal session."""

    tenant_id: str
    principal_id: str
    device_id: str
    modality: Modality
    correlation_id: str
    causation_id: str | None = None
    scope: CaptureScope | None = None
    max_duration_seconds: int | None = None
    #: A separate decision from "may observe the screen at all". Approving
    #: screen observation does not approve pixels; this must be asked for.
    allow_screenshot_fallback: bool = False

    def __post_init__(self) -> None:
        for name in ("tenant_id", "principal_id", "device_id", "correlation_id"):
            if not getattr(self, name):
                raise ValueError(f"a multimodal session request requires {name}")
        lowered = self.principal_id.strip().lower()
        if lowered.startswith(FORBIDDEN_PRINCIPAL_PREFIXES):
            raise AutonomousStartRefusedError(
                f"principal {self.principal_id!r} is not an authenticated human "
                f"principal; a model response, inbound event or companion "
                f"observation cannot start a capture session",
            )
        self.max_duration_seconds = validate_duration(self.max_duration_seconds)
        if self.modality is Modality.SCREEN and self.scope is None:
            raise ValueError("a screen session must name exactly one capture scope")
        if self.modality is not Modality.SCREEN and self.scope is not None:
            raise ValueError(f"{self.modality.value} sessions do not take a capture scope")
        if self.allow_screenshot_fallback and self.modality is not Modality.SCREEN:
            raise ValueError("the screenshot fallback only applies to screen sessions")


@dataclass
class SessionStartResult:
    """What became of one start attempt. Always carries the session record."""

    session: MultimodalSession
    allowed: bool
    outcome: str
    reason: str | None = None
    #: True when the governed seam could not durably record its decision.
    provenance_degraded: bool = False
    provenance_error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "outcome": self.outcome,
            "reason": self.reason,
            "provenance_degraded": self.provenance_degraded,
            "session": self.session.snapshot(),
            **self.extra,
        }


async def start_session(
    request: SessionRequest,
    *,
    store: SessionStore,
    capability_resolver: DeviceCapabilityResolver | None = None,
    db_path: str | None = None,
    identity_context: Any | None = None,
    blocking_executor: Any | None = None,
    microphone_backend: Any | None = None,
    seam: Callable[..., Any] | None = None,
) -> SessionStartResult:
    """Resolve, gate and (only if every gate passed) start one session.

    Returns rather than raises for every denial: a refused session is a normal,
    inspectable outcome that the status surface must be able to show, not an
    exception for a caller to swallow.
    """
    session = MultimodalSession(
        tenant_id=request.tenant_id,
        principal_id=request.principal_id,
        device_id=request.device_id,
        modality=request.modality,
        correlation_id=request.correlation_id,
        causation_id=request.causation_id,
        scope=request.scope,
        max_duration_seconds=request.max_duration_seconds or 0,
    )
    store.add(session)
    session.transition(SessionState.AWAITING_APPROVAL, "resolving governance")

    # Gate input 1: the device's declared capability (§3.3). Resolved here
    # because Package C consumes the declaration; it does not own the registry.
    #
    # An explicit argument always wins. Only an unset one falls back to what
    # Session F installed, so a production caller reaches Session E's registry
    # while a caller that named its own resolver is never overridden.
    if capability_resolver is None:
        capability_resolver = get_capability_resolver()
    capability = resolve_modality_capability(
        capability_resolver,
        request.device_id,
        request.modality,
    )

    if seam is None:
        from bartholomew.kernel.runtime_contract import (
            run_multimodal_session_through_runtime_contract as seam,
        )

    result = await seam(
        request.modality.value,
        db_path=db_path,
        identity_context=identity_context,
        capability_supported=capability.supported,
        capability_reason=capability.reason,
        blocking_executor=blocking_executor,
        # So the person asked can tell whose machine is asking. Context for
        # the consent channel; never a substitute for the modality prompt.
        consent_context={
            "tenant_id": request.tenant_id,
            "principal_id": request.principal_id,
            "device_id": request.device_id,
            "modality": request.modality.value,
            "correlation_id": request.correlation_id,
            "session_id": session.session_id,
        },
    )

    session.governance_decision = bool(result.governance_allowed)
    session.consent_decision = result.outcome == "started" or None
    if result.outcome == "consent_denied":
        session.consent_decision = False

    if not result.governance_allowed:
        session.transition(
            SessionState.REFUSED,
            result.reason or f"refused: {result.outcome}",
        )
        return SessionStartResult(
            session=session,
            allowed=False,
            outcome=result.outcome,
            reason=result.reason,
            provenance_degraded=result.provenance_degraded,
            provenance_error=result.provenance_error,
        )

    session.consent_decision = True
    session.approve("every governance gate passed")

    # Only now may a device be touched.
    if request.modality is Modality.MICROPHONE:
        return _start_microphone(
            session,
            store,
            backend=microphone_backend,
            provenance_degraded=result.provenance_degraded,
            provenance_error=result.provenance_error,
        )

    # Screen and spoken-output sessions become ACTIVE and are then driven by
    # their own bounded calls (`screen.capture_with_fallback`,
    # `speech.speak_with_handle`), which the caller makes while the session is
    # live and which the store can stop at any moment.
    session.transition(SessionState.ACTIVE, "session started")
    return SessionStartResult(
        session=session,
        allowed=True,
        outcome="started",
        provenance_degraded=result.provenance_degraded,
        provenance_error=result.provenance_error,
        extra={"allow_screenshot_fallback": request.allow_screenshot_fallback},
    )


def _start_microphone(
    session: MultimodalSession,
    store: SessionStore,
    *,
    backend: Any | None,
    provenance_degraded: bool,
    provenance_error: str | None,
) -> SessionStartResult:
    """Probe, then run the listening session on its own thread.

    The probe happens before ACTIVE so that a machine with no microphone
    produces a visible `unavailable` session -- never an `active` one that is
    hearing nothing. `unavailable` is a different terminal state from `failed`
    because "you have no microphone" and "the microphone broke" need different
    things from the user.
    """
    adapter = MicrophoneSessionAdapter(backend)
    status = adapter.probe()
    if not status.usable:
        session.transition(
            SessionState.UNAVAILABLE,
            f"microphone unavailable ({status.availability.value}): {status.detail}",
        )
        return SessionStartResult(
            session=session,
            allowed=True,
            outcome="unavailable",
            reason=status.detail,
            provenance_degraded=provenance_degraded,
            provenance_error=provenance_error,
            extra={"microphone": status.as_dict()},
        )

    store.add(session, stopper=adapter.stop)
    session.transition(SessionState.ACTIVE, "listening")
    observation_holder: dict[str, Any] = {}

    def _run() -> None:
        try:
            observation_holder["observation"] = adapter.start(
                float(session.max_duration_seconds),
            )
        except MicrophoneUnavailableError as exc:
            # The device went away mid-session. Truthfully unavailable, not a
            # session that quietly kept "listening".
            store.terminate(
                session.session_id,
                SessionState.UNAVAILABLE,
                f"microphone lost: {exc.status.detail}",
            )
        except MicrophoneCaptureFailedError as exc:
            store.terminate(session.session_id, SessionState.FAILED, str(exc))
        except Exception as exc:
            logger.exception("Microphone session failed")
            store.terminate(session.session_id, SessionState.FAILED, str(exc))
        else:
            store.stop(session.session_id, "listening session finished")

    thread = threading.Thread(
        target=_run,
        name=f"multimodal-mic-{session.session_id}",
        daemon=True,
    )
    thread.start()

    return SessionStartResult(
        session=session,
        allowed=True,
        outcome="started",
        provenance_degraded=provenance_degraded,
        provenance_error=provenance_error,
        extra={
            "microphone": status.as_dict(),
            "_thread": thread,
            "_observation": observation_holder,
        },
    )


# ---------------------------------------------------------------------------
# The installed capability resolver (Session F)
# ---------------------------------------------------------------------------
# `start_session` keeps its explicit `capability_resolver` parameter, and an
# explicit argument still wins: a caller that passes one is never overridden
# by what happens to be installed. This holder is only what an *unset*
# argument now falls back to, so a production caller gets Session E's registry
# instead of the fail-closed None, and every existing test that passes its own
# resolver behaves exactly as it did.

_INSTALLED_RESOLVER: dict[str, DeviceCapabilityResolver | None] = {"resolver": None}


def get_capability_resolver() -> DeviceCapabilityResolver | None:
    """The installed resolver, or None -- which `resolve_modality_capability` denies."""
    return _INSTALLED_RESOLVER["resolver"]


def install_capability_resolver(resolver: DeviceCapabilityResolver | None) -> None:
    """Install the registry-backed resolver. None restores the fail-closed default."""
    _INSTALLED_RESOLVER["resolver"] = resolver
    logger.info(
        "Multimodal capability resolver installed: %s",
        type(resolver).__name__ if resolver is not None else "none (fail-closed)",
    )
