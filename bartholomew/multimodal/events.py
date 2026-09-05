"""Logical canonical-event serializers, and the seam Session F connects.

Contract §7 deliverable 9: produce events that match the frozen canonical
envelope (§3.1) *logically*, without importing Session A's branch and without
becoming a second event authority. So this module:

* builds a dict with exactly §3.1's field names and vocabularies;
* imports nothing from any event package -- it has no dependency on A at all;
* **persists nothing, delivers nothing and consumes nothing.** There is no
  database call here, no HTTP call, no queue. `MultimodalEventSink` is a
  Protocol with one method, and the default `NullEventSink` drops what it is
  given. Session F supplies the real sink, which is A's ingress.

**What this module deliberately does not decide.** It does not assign
`captured_at` (§3.1: ingress assigns it, from trusted server-side context) and
it does not upgrade `source.verification` (a claimed device stays claimed --
invariant 15). It emits `captured_at: None` and lets A fill it, rather than
inventing a timestamp that would look like ingress had seen the event.

`event_id` is content-derived, so a retry of the same observation produces the
same id and collapses onto one logical event under A's
`(tenant_id, source_id, event_id)` rule -- the same idempotency discipline the
existing companion envelope already uses.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from .accessibility import AccessibilityObservation
from .microphone import MicrophoneObservation
from .privacy import Classification
from .screen import ScreenObservation
from .session import MultimodalSession
from .speech import SpeechOutcome

logger = logging.getLogger(__name__)

#: §3.1 schema version. Bumping this is a contract change, not a local edit.
SCHEMA_VERSION = 1

#: Namespaced event types this package produces. Registered with A by F; they
#: route to a handler and, per §3.1, authorise nothing by themselves.
EVENT_TYPE_TRANSCRIPT = "multimodal.microphone.transcript"
EVENT_TYPE_SCREEN = "multimodal.screen.observation"
EVENT_TYPE_ACCESSIBILITY = "multimodal.accessibility.observation"
EVENT_TYPE_SPEECH = "multimodal.spoken_output.utterance"
EVENT_TYPE_SESSION_STATE = "multimodal.session.state"

EVENT_TYPES: frozenset[str] = frozenset(
    {
        EVENT_TYPE_TRANSCRIPT,
        EVENT_TYPE_SCREEN,
        EVENT_TYPE_ACCESSIBILITY,
        EVENT_TYPE_SPEECH,
        EVENT_TYPE_SESSION_STATE,
    },
)

#: Hard ceiling on a serialized payload (§3.1: payload schemas are versioned
#: and size-bounded).
MAX_PAYLOAD_BYTES = 16_384


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _payload_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


def _derive_event_id(session_id: str, event_type: str, digest: str) -> str:
    """Content-derived, so a retry is a retry rather than a second event."""
    material = f"{session_id}|{event_type}|{digest}".encode()
    return f"multimodal:{hashlib.sha256(material).hexdigest()[:32]}"


@runtime_checkable
class MultimodalEventSink(Protocol):
    """Where a serialized event goes. Session F supplies A's ingress here."""

    def submit(self, envelope: dict[str, Any]) -> None: ...


class NullEventSink:
    """The default. Drops events, because C owns no delivery path.

    Not a silent failure: this is the honest state of the system until F wires
    A's ingress in. It logs at debug so an operator can see events being
    produced and discarded.
    """

    def submit(self, envelope: dict[str, Any]) -> None:
        logger.debug(
            "multimodal event %s produced with no sink configured (dropped)",
            envelope.get("event_type"),
        )


@dataclass
class CollectingEventSink:
    """An in-memory sink for tests and local inspection."""

    events: list[dict[str, Any]]

    def __init__(self) -> None:
        self.events = []

    def submit(self, envelope: dict[str, Any]) -> None:
        self.events.append(envelope)


def build_envelope(
    session: MultimodalSession,
    event_type: str,
    payload: dict[str, Any],
    classification: Classification,
) -> dict[str, Any]:
    """One §3.1-shaped envelope. Bounded, classified, provenance-complete.

    Every field a derived observation must carry per §7 gate 6 -- source,
    device, session, time, privacy and retention -- is present and comes from
    the session record rather than from the observation's own content.
    """
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unregistered multimodal event type: {event_type!r}")

    body = dict(payload)
    body["session_id"] = session.session_id
    body["modality"] = session.modality.value
    encoded = _canonical_json(body)
    if len(encoded.encode()) > MAX_PAYLOAD_BYTES:
        # Refuse rather than truncate a structured payload into something that
        # parses but means something else.
        raise ValueError(
            f"multimodal payload exceeds {MAX_PAYLOAD_BYTES} bytes; "
            f"bound the observation before serializing",
        )

    digest = _payload_digest(body)
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": _derive_event_id(session.session_id, event_type, digest),
        "event_type": event_type,
        "tenant_id": session.tenant_id,
        "source": {
            "source_id": f"multimodal.{session.modality.value}",
            "device_id": session.device_id,
            "principal_id": session.principal_id,
            # A device this package was handed is claimed, not verified.
            # Session E's registry is what can ever raise this, and it does so
            # on its own authority -- never here.
            "verification": "claimed",
        },
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        # §3.1: ingress assigns captured_at from trusted server-side context.
        # Emitting None is the truthful "not yet captured by ingress".
        "captured_at": None,
        "correlation_id": session.correlation_id,
        "causation_id": session.causation_id,
        "payload": body,
        "payload_sha256": digest,
        "privacy_class": classification.privacy_class.value,
        "retention_class": classification.retention_class.value,
    }


def serialize_microphone(
    session: MultimodalSession,
    observation: MicrophoneObservation,
) -> dict[str, Any]:
    """A bounded transcript event. Never raw audio -- there is none to carry."""
    return build_envelope(
        session,
        EVENT_TYPE_TRANSCRIPT,
        {
            "text": observation.text,
            "listened_seconds": observation.listened_seconds,
            "redactions": [r.as_dict() for r in observation.classification.redactions],
            "truncated": observation.classification.truncated,
        },
        observation.classification,
    )


def serialize_accessibility(
    session: MultimodalSession,
    observation: AccessibilityObservation,
) -> dict[str, Any]:
    """A structured UI observation event."""
    return build_envelope(
        session,
        EVENT_TYPE_ACCESSIBILITY,
        {
            "available": observation.available,
            "complete": observation.complete,
            "application": observation.application,
            "window_id": observation.window_id,
            "window_title": observation.window_title,
            "element_count": len(observation.elements),
            "focused_element": (
                observation.focused_element.as_dict() if observation.focused_element else None
            ),
            "omitted_secret_fields": observation.omitted_secret_fields,
            "detail": observation.detail,
        },
        observation.classification,
    )


def serialize_screen(
    session: MultimodalSession,
    observation: ScreenObservation,
) -> dict[str, Any]:
    """A derived screen observation event.

    Carries the derived description, which screen/window/region it came from,
    whether the screenshot fallback was used and *why* it was required -- the
    provenance §7 requires -- and an evidence reference. It carries no image
    and no image path, because `ScreenObservation` has neither.
    """
    return build_envelope(
        session,
        EVENT_TYPE_SCREEN,
        {
            "scope": observation.scope_description,
            "used_screenshot": observation.used_screenshot,
            "description": observation.description,
            "fallback_reason": observation.fallback_reason,
            "fallback_refused_reason": observation.fallback_refused_reason,
            "evidence_reference": observation.evidence_reference,
            "accessibility_available": observation.accessibility.available,
            "accessibility_sufficient": observation.accessibility.sufficient_for(),
            "omitted_secret_fields": observation.accessibility.omitted_secret_fields,
        },
        observation.classification,
    )


def serialize_speech(session: MultimodalSession, outcome: SpeechOutcome) -> dict[str, Any]:
    """An utterance event. `spoken` is only true when sound was made."""
    return build_envelope(
        session,
        EVENT_TYPE_SPEECH,
        {
            "spoken": outcome.spoken,
            "detail": outcome.detail,
            "engine": outcome.engine,
            "cancelled_before_speaking": outcome.cancelled_before_speaking,
        },
        outcome.classification or Classification(),
    )


def serialize_session_state(session: MultimodalSession) -> dict[str, Any]:
    """A state-change event, so the whole session is reconstructable later."""
    snapshot = session.snapshot()
    classification = Classification()
    # A session's own lifecycle is operational metadata about the system, not
    # captured content about the user -- so it is the one event here that is
    # context_only/audit rather than sensitive/ephemeral.
    from .privacy import PrivacyClass, RetentionClass

    classification.privacy_class = PrivacyClass.CONTEXT_ONLY
    classification.retention_class = RetentionClass.AUDIT
    return build_envelope(
        session,
        EVENT_TYPE_SESSION_STATE,
        {
            "state": snapshot["state"],
            "scope": snapshot["scope"],
            "started_at": snapshot["started_at"],
            "expires_at": snapshot["expires_at"],
            "ended_at": snapshot["ended_at"],
            "outcome_reason": snapshot["outcome_reason"],
            "history": snapshot["history"],
        },
        classification,
    )


# ---------------------------------------------------------------------------
# The installed sink (Session F)
# ---------------------------------------------------------------------------
# A holder rather than a bare module global, for the reason
# `actuation.devices._INSTALLED` gives: a rebound module attribute is invisible
# to a module that already imported the name.
#
# The default is unchanged -- `NullEventSink`, which drops what it is given.
# This adds a place for Session F to put A's ingress; it does not add a
# delivery path that exists when nobody installed one.

_INSTALLED_SINK: dict[str, MultimodalEventSink | None] = {"sink": None}


def get_event_sink() -> MultimodalEventSink:
    """The installed sink, defaulting to the one that drops events.

    Never returns None: "no sink" would be an unanswered question at a call
    site that has an event in its hand, and the honest answer with nothing
    installed is a sink that discards and says so.
    """
    sink = _INSTALLED_SINK["sink"]
    return sink if sink is not None else NullEventSink()


def install_event_sink(sink: MultimodalEventSink | None) -> None:
    """Install the delivery path. Passing None restores the dropping default."""
    _INSTALLED_SINK["sink"] = sink
    logger.info(
        "Multimodal event sink installed: %s",
        type(sink).__name__ if sink is not None else "NullEventSink (default)",
    )
