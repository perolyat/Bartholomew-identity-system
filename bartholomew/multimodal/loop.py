"""The active-session observation loop: the Observe leg, running.

Before this module a screen session reached `ACTIVE` and then nothing
happened: no production code called `capture_with_fallback`, serialized an
observation or submitted it to the sink (W03-PREP assessment, "Windows
perception"). This is the missing caller. It is started by
`runtime.start_session()` **only after** every governance gate passed and the
session entered `ACTIVE`, and it runs on its own daemon thread for as long as
the session is live and no longer.

Each wake of the loop, in a fixed order:

1. **Re-read governance.** The session must still be `ACTIVE`; its expiry must
   not have passed; and the Parking Brake -- global or the modality's own scope
   -- must not be engaged. The brake is read through the same
   `GovernanceStore` every other seam reads, fail-closed: an engaged brake ends
   the session through `SessionStore.stop_all_for_brake` (this is that
   function's production caller), and an *unreadable* brake ends it too,
   because an observation the safety gate cannot vouch for is one that must not
   be taken. The wake interval is the bound on how long an engaged brake can
   go unnoticed (`DEFAULT_POLL_SECONDS`).
2. **Capture.** On the capture cadence, read the approved scope through
   `screen.capture_with_fallback` -- accessibility first, pixels only if the
   session authorised the fallback -- exactly as the wave-two package intended
   and never did. Nothing here can widen the scope: the requested scope *is*
   the approved scope.
3. **Classify and separate.** Turn the reading into an `ObservationEvent`:
   the observed facts (bounded elements, focus, idle duration) in
   `observed_event`, and any inference in its own marked fields with a
   confidence and competing explanations.
4. **Emit.** Serialize and hand the envelope to the one installed
   `MultimodalEventSink`. A refused emit (an envelope whose tenant does not
   match the process binding, an unregistered type, a failed write) ends the
   session as `failed` with the reason: the loop does not keep observing
   things it cannot record.

What it never does: start on its own (there is no code path here that begins
without an `ACTIVE`, consented session), restart a finished session, capture
outside the approved scope, retain an image, or act on what it sees.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .accessibility import AccessibilityProvider
from .events import (
    EVENT_TYPE_ACCESSIBILITY,
    EVENT_TYPE_SCREEN,
    MultimodalEventSink,
    get_event_sink,
    serialize_observation_event,
    serialize_session_state,
)
from .inference import Inferencer, NullInferencer, default_inferencer
from .modality import BRAKE_SCOPE, CaptureScope, Modality
from .observation import (
    MAX_FACT_ELEMENTS,
    MAX_SUMMARY_CHARS,
    OBSERVED_UNAVAILABLE,
    OBSERVED_WINDOW_STATE,
    ObservationEvent,
    ObservedEvent,
    make_provenance,
)
from .privacy import Classification, bound_text
from .screen import CaptureRefusedError, ScreenBackend, ScreenObservation, capture_with_fallback
from .session import MultimodalSession, SessionState
from .store import SessionStore

logger = logging.getLogger(__name__)

#: How often the loop wakes to re-read the brake and the session state. This
#: is the bound on how long an engaged brake can go unnoticed by a live
#: session, plus one brake read.
DEFAULT_POLL_SECONDS = 1.0
#: How often an observation is actually captured while the session is live.
DEFAULT_CAPTURE_INTERVAL_SECONDS = 5.0
#: A caller may tighten either interval, never loosen past these.
MAX_POLL_SECONDS = 5.0
MAX_CAPTURE_INTERVAL_SECONDS = 60.0

#: Provenance `source` values the loop stamps.
SOURCE_ACCESSIBILITY = "multimodal.accessibility"
SOURCE_SCREEN = "multimodal.screen"
SOURCE_READ_BACK = "multimodal.read_back"


class BrakeUnreadableError(RuntimeError):
    """The Parking Brake could not be read. Fail-closed: the session ends."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp(value: float | None, default: float, ceiling: float) -> float:
    if value is None:
        return default
    seconds = float(value)
    if seconds <= 0:
        raise ValueError("an observation-loop interval must be positive")
    return min(seconds, ceiling)


def default_brake_check(scope: str, db_path: str | None) -> Callable[[], bool]:
    """A fail-closed brake read against the one GovernanceStore.

    `db_path` is resolved the way the device seams resolve theirs when unset
    (the deployment's one kernel database), so a loop started without an
    explicit path reads the same brake the server does. Any error reading it
    raises `BrakeUnreadableError`, which the loop treats as engaged.
    """

    def _check() -> bool:
        try:
            from bartholomew.orchestrator.safety.governance_store import is_blocked_fail_closed

            path = db_path
            if not path:
                from bartholomew.kernel.db_paths import resolve_kernel_db_path

                path = resolve_kernel_db_path(None)
            return bool(is_blocked_fail_closed(scope, path))
        except Exception as exc:
            raise BrakeUnreadableError(f"{type(exc).__name__}: {exc}") from exc

    return _check


def _read_idle_seconds(provider: Any) -> float | None:
    """The provider's idle duration, if it offers one. Never raises."""
    reader = getattr(provider, "read_idle_seconds", None)
    if not callable(reader):
        return None
    try:
        value = reader()
    except Exception:
        logger.debug("idle-time read failed", exc_info=True)
        return None
    if value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(seconds):
        return None
    return max(0.0, seconds)


def build_observation_event(
    observation: ScreenObservation,
    *,
    session: MultimodalSession,
    idle_seconds: float | None = None,
    inferencer: Inferencer | None = None,
    kind: str = OBSERVED_WINDOW_STATE,
    occurred_at: str | None = None,
    captured_at: str | None = None,
) -> ObservationEvent:
    """Turn one screen reading into an observed-vs-inferred record.

    The facts carry the bounded *content* -- the elements, not merely their
    count -- with element truncation recorded in the classification. The
    inference, if any, is produced from structural facts only and lands in
    its own fields.
    """
    a11y = observation.accessibility
    classification = Classification(
        privacy_class=observation.classification.privacy_class,
        retention_class=observation.classification.retention_class,
        redactions=list(observation.classification.redactions),
        truncated=observation.classification.truncated,
    )
    # The accessibility reading's own redaction trail is part of the content's
    # history even when the screenshot path's classification was chosen.
    for record in a11y.classification.redactions:
        if record not in classification.redactions:
            classification.redactions.append(record)
    classification.truncated = classification.truncated or a11y.classification.truncated
    classification.escalate(a11y.classification.privacy_class)

    elements = [e.as_dict() for e in a11y.elements[:MAX_FACT_ELEMENTS]]
    if len(a11y.elements) > MAX_FACT_ELEMENTS:
        classification.truncated = True
        classification.note("observation.elements", "event_element_bound")

    unreadable = not a11y.available and not observation.used_screenshot
    if unreadable:
        # A reading that could not be made is recorded as such -- never as a
        # blank window. A tick's window_state becomes `unavailable`; a
        # read-back keeps its kind (the Verify leg asked a question and gets
        # "cannot say" as the answer to *that* question) with the same
        # honest summary.
        if kind == OBSERVED_WINDOW_STATE:
            kind = OBSERVED_UNAVAILABLE
        source = SOURCE_ACCESSIBILITY
    else:
        source = SOURCE_SCREEN if observation.used_screenshot else SOURCE_ACCESSIBILITY

    facts: dict[str, Any] = {
        "scope": observation.scope_description,
        "available": a11y.available,
        "complete": a11y.complete,
        "application": a11y.application,
        "window_id": a11y.window_id,
        "window_title": a11y.window_title,
        "elements": elements,
        "element_count": len(a11y.elements),
        "elements_truncated": len(a11y.elements) > MAX_FACT_ELEMENTS,
        "focused_element": a11y.focused_element.as_dict() if a11y.focused_element else None,
        "omitted_secret_fields": a11y.omitted_secret_fields,
        "accessibility_detail": a11y.detail,
        "used_screenshot": observation.used_screenshot,
        "screen_description": observation.description,
        "fallback_reason": observation.fallback_reason,
        "fallback_refused_reason": observation.fallback_refused_reason,
        "evidence_reference": observation.evidence_reference,
        "idle_seconds": round(idle_seconds, 1) if idle_seconds is not None else None,
    }

    if unreadable:
        reason = a11y.insufficiency_reason() or a11y.detail or "no reading"
        if observation.fallback_refused_reason:
            reason = f"{reason}; {observation.fallback_refused_reason}"
        unavailable = ObservedEvent.unavailable(reason, facts=facts)
        observed = (
            unavailable
            if kind == OBSERVED_UNAVAILABLE
            else ObservedEvent(kind=kind, summary=unavailable.summary, facts=facts)
        )
    else:
        observed = ObservedEvent(
            kind=kind,
            summary=_summarise(observation, idle_seconds, classification),
            facts=facts,
        )

    provenance = make_provenance(
        observed,
        source=source,
        session_id=session.session_id,
        device_id=session.device_id,
        occurred_at=occurred_at,
        captured_at=captured_at,
    )
    inference = (inferencer or NullInferencer()).infer(observed)
    if inference is None:
        return ObservationEvent(
            observed_event=observed,
            provenance=provenance,
            classification=classification,
        )
    return ObservationEvent(
        observed_event=observed,
        provenance=provenance,
        inferred_state=inference.state,
        confidence=inference.confidence,
        competing_explanations=list(inference.competing_explanations),
        classification=classification,
    )


def _summarise(
    observation: ScreenObservation,
    idle_seconds: float | None,
    classification: Classification,
) -> str:
    """One plain sentence of facts. Bounded, and the bound is recorded."""
    a11y = observation.accessibility
    parts: list[str] = []
    if a11y.available:
        title = f"{a11y.window_title!r}" if a11y.window_title else "an untitled window"
        app = f" ({a11y.application})" if a11y.application else ""
        parts.append(f"active window {title}{app}: {len(a11y.elements)} readable control(s)")
        if a11y.focused_element is not None:
            parts.append(f"focus on {a11y.focused_element.role} {a11y.focused_element.name!r}")
        if a11y.omitted_secret_fields:
            parts.append(f"{a11y.omitted_secret_fields} secret field(s) omitted")
        if not a11y.complete:
            parts.append("partial read")
    if observation.used_screenshot:
        parts.append(f"screenshot fallback used: {observation.description or 'no description'}")
    if idle_seconds is not None:
        minutes = int(idle_seconds // 60)
        parts.append(
            (
                f"no keyboard or mouse input for {minutes} minute(s)"
                if minutes >= 1
                else f"input within the last {int(idle_seconds)} second(s)"
            ),
        )
    text, truncated = bound_text("; ".join(parts) or "nothing readable", MAX_SUMMARY_CHARS - 4)
    if truncated:
        classification.truncated = True
        classification.note("observation.summary", "length_bound")
    return text


@dataclass
class TickResult:
    """What one wake of the loop did. Data for tests and the status surface."""

    session_id: str
    at: str
    ended: bool = False
    end_reason: str | None = None
    observed_kind: str | None = None
    event_type: str | None = None
    event_id: str | None = None
    emitted: bool = False
    error: str | None = None
    event: ObservationEvent | None = field(default=None, repr=False)


@dataclass
class ObservationLoopConfig:
    poll_seconds: float = DEFAULT_POLL_SECONDS
    capture_interval_seconds: float = DEFAULT_CAPTURE_INTERVAL_SECONDS

    def __post_init__(self) -> None:
        self.poll_seconds = _clamp(self.poll_seconds, DEFAULT_POLL_SECONDS, MAX_POLL_SECONDS)
        self.capture_interval_seconds = _clamp(
            self.capture_interval_seconds,
            DEFAULT_CAPTURE_INTERVAL_SECONDS,
            MAX_CAPTURE_INTERVAL_SECONDS,
        )


class ObservationLoop:
    """The running Observe leg for one ACTIVE screen session."""

    def __init__(
        self,
        session: MultimodalSession,
        store: SessionStore,
        *,
        allow_screenshot_fallback: bool = False,
        accessibility_provider: AccessibilityProvider | None = None,
        screen_backend: ScreenBackend | None = None,
        sink: MultimodalEventSink | None = None,
        inferencer: Inferencer | None = None,
        db_path: str | None = None,
        brake_check: Callable[[], bool] | None = None,
        config: ObservationLoopConfig | None = None,
    ) -> None:
        if session.modality is not Modality.SCREEN or session.scope is None:
            raise ValueError("the observation loop runs screen sessions only")
        self.session = session
        self.store = store
        self.allow_screenshot_fallback = bool(allow_screenshot_fallback)
        self.accessibility_provider = accessibility_provider
        self.screen_backend = screen_backend
        self._sink = sink
        self.inferencer = inferencer if inferencer is not None else default_inferencer()
        self.config = config or ObservationLoopConfig()
        self.brake_scope = BRAKE_SCOPE[session.modality]
        self.brake_check = brake_check or default_brake_check(self.brake_scope, db_path)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        # Counters for the status surface. Never content.
        self.ticks = 0
        self.emitted = 0
        self.last_tick_at: str | None = None
        self.last_event_id: str | None = None
        self.last_observed_kind: str | None = None
        self.last_error: str | None = None
        self.ended_reason: str | None = None

    # -- the sink, resolved late so an install after construction is seen ----

    @property
    def sink(self) -> MultimodalEventSink:
        return self._sink if self._sink is not None else get_event_sink()

    # -- control --------------------------------------------------------------

    def stop(self) -> None:
        """The store's stopper: wake the thread so it ends promptly."""
        self._stop.set()

    @property
    def stop_requested(self) -> bool:
        return self._stop.is_set()

    def stats(self) -> dict[str, Any]:
        return {
            "running": self._thread is not None and self._thread.is_alive(),
            "ticks": self.ticks,
            "observations_emitted": self.emitted,
            "last_tick_at": self.last_tick_at,
            "last_event_id": self.last_event_id,
            "last_observed_kind": self.last_observed_kind,
            "last_error": self.last_error,
            "ended_reason": self.ended_reason,
            "poll_seconds": self.config.poll_seconds,
            "capture_interval_seconds": self.config.capture_interval_seconds,
            "brake_scope": self.brake_scope,
        }

    # -- governance -----------------------------------------------------------

    def check_governance(self) -> str | None:
        """End the session if it may no longer observe. Returns the reason, or None.

        Order: the record's own state, expiry, then the brake. The brake is
        read last because it is the one that costs a database read, and a
        session that has already ended needs no read to know it.
        """
        session = self.session
        if session.state is not SessionState.ACTIVE:
            return f"session is {session.state.value}"
        if session.is_expired():
            self.store.terminate(
                session.session_id,
                SessionState.EXPIRED,
                "maximum session duration reached",
            )
            return "maximum session duration reached"
        try:
            engaged = bool(self.brake_check())
        except BrakeUnreadableError as exc:
            reason = f"parking brake unreadable ({exc}); stopped fail-closed"
            self.store.terminate(session.session_id, SessionState.FAILED, reason)
            return reason
        except Exception as exc:
            reason = (
                f"parking brake check errored ({type(exc).__name__}: {exc}); stopped fail-closed"
            )
            self.store.terminate(session.session_id, SessionState.FAILED, reason)
            return reason
        if engaged:
            # The production caller of `stop_all_for_brake`: every live
            # session the scope covers ends, this one included.
            self.store.stop_all_for_brake(self.brake_scope)
            if session.state is SessionState.ACTIVE:
                # Not covered by the scope we read? Then it should not have
                # read as engaged for us. Close it anyway rather than observe
                # under an engaged brake.
                self.store.terminate(
                    session.session_id,
                    SessionState.STOPPED,
                    f"stopped by parking brake (scope={self.brake_scope})",
                )
            return session.outcome_reason or f"stopped by parking brake (scope={self.brake_scope})"
        return None

    # -- observing ------------------------------------------------------------

    def observe_once(
        self,
        *,
        requested_scope: CaptureScope | None = None,
        kind: str = OBSERVED_WINDOW_STATE,
        inferencer: Inferencer | None = None,
    ) -> ObservationEvent:
        """One governed reading of the approved scope, as an observation event.

        Does not emit and does not re-read the brake: `tick()` and
        `readback.read_back()` do both around this call. A `requested_scope`
        outside the approved one raises `CaptureRefusedError`, unchanged.
        """
        assert self.session.scope is not None  # noqa: S101 - checked in __init__
        captured_at = _now_iso()
        observation = capture_with_fallback(
            approved_scope=self.session.scope,
            requested_scope=requested_scope,
            allow_screenshot_fallback=self.allow_screenshot_fallback,
            accessibility_provider=self.accessibility_provider,
            screen_backend=self.screen_backend,
        )
        idle = _read_idle_seconds(self.accessibility_provider)
        return build_observation_event(
            observation,
            session=self.session,
            idle_seconds=idle,
            inferencer=self.inferencer if inferencer is None else inferencer,
            kind=kind,
            captured_at=captured_at,
        )

    def emit(self, event: ObservationEvent) -> tuple[str, str]:
        """Serialize and submit. Returns (event_type, event_id). Raises on refusal."""
        event_type = (
            EVENT_TYPE_SCREEN
            if event.observed_event.facts.get("used_screenshot")
            else EVENT_TYPE_ACCESSIBILITY
        )
        envelope = serialize_observation_event(self.session, event, event_type=event_type)
        self.sink.submit(envelope)
        return event_type, str(envelope["event_id"])

    def tick(self) -> TickResult:
        """One complete wake: governance, capture, classify, emit."""
        result = TickResult(session_id=self.session.session_id, at=_now_iso())
        with self._lock:
            self.ticks += 1
            self.last_tick_at = result.at
        reason = self.check_governance()
        if reason is not None:
            result.ended, result.end_reason = True, reason
            self.ended_reason = reason
            return result
        try:
            event = self.observe_once()
        except CaptureRefusedError as exc:
            # Cannot happen for the approved scope; recorded honestly if it does.
            reason = f"capture refused: {exc}"
            self.store.terminate(self.session.session_id, SessionState.FAILED, reason)
            result.ended, result.end_reason, result.error = True, reason, str(exc)
            self.ended_reason = reason
            return result
        except Exception as exc:
            logger.exception("Observation tick failed")
            reason = f"observation failed: {type(exc).__name__}: {exc}"
            self.store.terminate(self.session.session_id, SessionState.FAILED, reason)
            result.ended, result.end_reason, result.error = True, reason, str(exc)
            self.ended_reason = reason
            return result

        result.event = event
        result.observed_kind = event.observed_event.kind
        self.last_observed_kind = event.observed_event.kind
        try:
            result.event_type, result.event_id = self.emit(event)
        except Exception as exc:
            # A refused or failed record ends the session: observing what
            # cannot be recorded is exactly the silent path this loop closes.
            reason = f"observation could not be recorded: {type(exc).__name__}: {exc}"
            logger.warning("%s (session %s)", reason, self.session.session_id)
            self.last_error = str(exc)
            self.store.terminate(self.session.session_id, SessionState.FAILED, reason)
            result.ended, result.end_reason, result.error = True, reason, str(exc)
            self.ended_reason = reason
            return result
        result.emitted = True
        with self._lock:
            self.emitted += 1
            self.last_event_id = result.event_id
        return result

    # -- the thread -----------------------------------------------------------

    def _emit_session_state(self) -> None:
        """Lifecycle audit, best-effort: never masks why the loop ended."""
        try:
            self.sink.submit(serialize_session_state(self.session))
        except Exception as exc:
            logger.warning(
                "session-state audit event not recorded for %s: %s",
                self.session.session_id,
                exc,
            )

    def run(self) -> None:
        """The thread body. Returns when the session is no longer live."""
        self._emit_session_state()
        next_capture = time.monotonic()
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                if now >= next_capture:
                    outcome = self.tick()
                    if outcome.ended:
                        break
                    next_capture = time.monotonic() + self.config.capture_interval_seconds
                else:
                    reason = self.check_governance()
                    if reason is not None:
                        self.ended_reason = reason
                        break
                remaining = max(0.0, next_capture - time.monotonic())
                self._stop.wait(
                    min(self.config.poll_seconds, remaining) or self.config.poll_seconds,
                )
        finally:
            if (
                self.session.state is SessionState.ACTIVE
                or self.session.state is SessionState.STOPPING
            ):
                # The thread is ending, so observation has ended; the record
                # must say so even if nothing else closed it.
                self.store.terminate(
                    self.session.session_id,
                    SessionState.STOPPED,
                    self.ended_reason or "observation loop ended",
                )
            # The record's own reason is the truth about why observation
            # ended (a person's stop, the brake, expiry, a failure); the
            # loop's note is only what it noticed on the way out.
            self.ended_reason = self.session.outcome_reason or self.ended_reason
            self._emit_session_state()

    def start_thread(self) -> threading.Thread:
        thread = threading.Thread(
            target=self.run,
            name=f"multimodal-observe-{self.session.session_id}",
            daemon=True,
        )
        self._thread = thread
        thread.start()
        return thread

    @property
    def thread(self) -> threading.Thread | None:
        return self._thread


__all__ = [
    "DEFAULT_CAPTURE_INTERVAL_SECONDS",
    "DEFAULT_POLL_SECONDS",
    "SOURCE_ACCESSIBILITY",
    "SOURCE_READ_BACK",
    "SOURCE_SCREEN",
    "BrakeUnreadableError",
    "ObservationLoop",
    "ObservationLoopConfig",
    "TickResult",
    "build_observation_event",
    "default_brake_check",
]
