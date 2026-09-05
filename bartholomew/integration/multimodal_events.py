"""Package C's multimodal events enter Package A's canonical ingress.

C builds §3.1-shaped envelopes and hands them to a `MultimodalEventSink`,
whose default (`NullEventSink`) drops them. That was the honest state until
this module: C owns no delivery path and deliberately imports nothing from any
event package.

This module supplies the real sink, and it is A's ingress -- `inbound_events`,
the same table the HTTP capture route writes and the same table A's sweep
reads. **There is no second event bus.** A multimodal transcript and an
externally-POSTed observation become rows in one table, are swept by one
processor, are governed by one Parking Brake and settle in one state machine.

What the sink does and does not decide
--------------------------------------
* **`captured_at` is assigned by ingress, not by C.** C emits `captured_at:
  None` on purpose. `inbound_store.capture_event` stamps `received_at` from
  trusted server-side context, which *is* the captured-at, and this sink does
  not invent one on the way past.
* **`occurred_at` is preserved** exactly as C recorded it, so the distance
  between when a thing happened and when Bartholomew captured it stays
  visible.
* **Correlation and causation survive.** `inbound_events` has no column for
  either, so they travel in the stored payload -- which is the full envelope,
  byte-for-byte what C produced. `payload_sha256` therefore covers them, and
  the handlers below read them back out and put them on every disposition.
* **Privacy metadata survives** the same way: `privacy_class` and
  `retention_class` are C's classification of the material and are stored with
  it rather than being recomputed by anything downstream.
* **Idempotency is C's, unchanged.** `event_id` is content-derived, and
  `inbound_events` is UNIQUE on `(source_id, event_id)`, so a retried
  observation collapses onto one logical row rather than becoming a second
  event. The sink reports a duplicate as a duplicate and writes nothing.
* **The sink never reports success it did not get.** A failed write raises;
  it does not log-and-continue. C's callers already treat a raising sink as a
  failure of that observation, which is the correct direction.

Interpretation: reuse, never a second path
------------------------------------------
The three *observational* types -- a microphone transcript, a screen
observation, an accessibility observation -- are external material about the
person's world, which is exactly what A's `handle_observation` already
interprets. They are registered to it directly. They therefore travel the same
Identity policy, the same Parking Brake deferral and the same
`interpret_captured_event` seam as every other observation, and this module
adds no interpretation logic of its own.

The two *non-observational* types are registered to a handler that records and
settles them without interpretation, because neither is a statement about the
world:

* `multimodal.spoken_output.utterance` is a record of what Bartholomew itself
  said. Interpreting Bartholomew's own speech as evidence about the person
  would let the system treat its own output as an observation of the world.
* `multimodal.session.state` is capture-session lifecycle -- started, stopped,
  denied, unavailable. It is audit, and it is the record that keeps C's
  distinction between *unavailable*, *broken* and *permission denied*
  durable; it is not evidence.

Both settle as `irrelevant`, which is A's "the system reached an answer"
verdict rather than "the system declined to answer". The row itself remains in
`inbound_events` in full, so nothing is lost by not interpreting it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from bartholomew.kernel.event_processing.adapters import ObservationPayload, handle_observation
from bartholomew.kernel.event_processing.registry import (
    HandlerResult,
    PayloadValidationError,
    RegisteredEventType,
    register,
)
from bartholomew.kernel.event_processing.store import STATE_IRRELEVANT
from bartholomew.kernel.inbound_store import OUTCOME_CAPTURED, capture_event
from bartholomew.multimodal.events import (
    EVENT_TYPE_ACCESSIBILITY,
    EVENT_TYPE_SCREEN,
    EVENT_TYPE_SESSION_STATE,
    EVENT_TYPE_SPEECH,
    EVENT_TYPE_TRANSCRIPT,
    EVENT_TYPES,
)

logger = logging.getLogger(__name__)

#: What `verified_by` records for a multimodal event. These events are not
#: signed by a remote sender: they are produced in-process by a capture
#: session that the person themself started, under a device whose capability
#: was resolved through Session E's registry. Naming that specifically -- and
#: not reusing an HMAC source's marker -- keeps the audit honest about what
#: kind of assurance stands behind the row.
VERIFIED_BY_MULTIMODAL = "multimodal_session"


class MultimodalIngressError(RuntimeError):
    """The event could not be captured. Never swallowed: see the docstring."""


@dataclass
class CanonicalIngressSink:
    """Package C's `MultimodalEventSink`, writing into Package A's ingress.

    Structural, like every other seam here: C's Protocol is one method,
    `submit`, and C imports nothing from this module.

    `runtime_id` is the bound runtime's identity, supplied at install time
    from the resolved runtime -- never from an envelope, and never from a
    request. A's sweep matches `event_processing.runtime_id` against the
    process's own binding, so an event captured under the wrong id would not
    be claimed; taking it from the caller's own binding is what keeps tenant
    attribution out of reach of the event's content.
    """

    db_path: str
    runtime_id: str | None = None

    #: Set by `submit` on each call, for tests and local inspection. Holds the
    #: last stored row, never a payload.
    last_row_id: int | None = None

    def submit(self, envelope: dict[str, Any]) -> None:
        event_type = str(envelope.get("event_type") or "")
        if event_type not in EVENT_TYPES:
            # A type this sink is not registered for must not enter the one
            # ingress table under a multimodal source id.
            raise MultimodalIngressError(
                f"refusing to capture unregistered multimodal event type {event_type!r}",
            )
        source = envelope.get("source") or {}
        source_id = str(source.get("source_id") or "")
        event_id = str(envelope.get("event_id") or "")
        if not source_id or not event_id:
            raise MultimodalIngressError(
                "a multimodal event must carry both a source_id and an event_id",
            )

        tenant = str(envelope.get("tenant_id") or "").strip() or None
        if tenant and self.runtime_id and tenant != self.runtime_id:
            # The envelope's tenant and the process's binding disagree. This
            # is refused rather than reconciled: writing the row under either
            # id would attribute one person's observation using the other's
            # claim, and the envelope is not an authority on whose it is.
            raise MultimodalIngressError(
                "the multimodal session's tenant does not match this runtime's "
                "binding; refusing to capture rather than re-attribute it",
            )

        try:
            stored = capture_event(
                self.db_path,
                source_id=source_id,
                event_id=event_id,
                event_type=event_type,
                # C's own record of when the thing happened, preserved.
                occurred_at=envelope.get("occurred_at"),
                # The whole envelope is the payload, so correlation_id,
                # causation_id, privacy_class, retention_class and the source
                # block all survive and are covered by the digest.
                payload=envelope,
                outcome=OUTCOME_CAPTURED,
                governance_reason=None,
                verified_by=VERIFIED_BY_MULTIMODAL,
                runtime_id=self.runtime_id or tenant,
            )
        except Exception as e:  # noqa: BLE001 - re-raised, never swallowed
            raise MultimodalIngressError(
                f"the multimodal event could not be captured ({type(e).__name__}: {e})",
            ) from e

        self.last_row_id = getattr(stored, "row_id", None)
        if getattr(stored, "duplicate", False):
            logger.debug(
                "multimodal event %s was already captured; not a second event",
                event_id,
            )


# ---------------------------------------------------------------------------
# Registration with Session A's event-type table
# ---------------------------------------------------------------------------


def _envelope_provenance(payload: Any) -> dict[str, Any]:
    """Correlation, causation and privacy, read back off the stored envelope."""
    if not isinstance(payload, dict):
        return {}
    source = payload.get("source") or {}
    return {
        "correlation_id": payload.get("correlation_id"),
        "causation_id": payload.get("causation_id"),
        "privacy_class": payload.get("privacy_class"),
        "retention_class": payload.get("retention_class"),
        "session_id": (
            (payload.get("payload") or {}).get("session_id")
            if isinstance(payload.get("payload"), dict)
            else None
        ),
        "modality": (
            (payload.get("payload") or {}).get("modality")
            if isinstance(payload.get("payload"), dict)
            else None
        ),
        "device_id": source.get("device_id") if isinstance(source, dict) else None,
        "verification": source.get("verification") if isinstance(source, dict) else None,
    }


@dataclass(frozen=True)
class MultimodalObservationPayload:
    """A C envelope, presented to A's observation handler as an observation.

    The envelope's *inner* payload is what carries the observed material, so
    that is what interpretation reads. The outer envelope is kept alongside it
    so a disposition can report the provenance without interpretation having
    to know about envelopes at all.
    """

    envelope: dict[str, Any]
    observation: ObservationPayload

    @property
    def raw(self) -> dict[str, Any]:
        return self.observation.raw

    @property
    def text(self) -> str:
        return self.observation.text

    @classmethod
    def parse(cls, payload: Any) -> MultimodalObservationPayload:
        if not isinstance(payload, dict):
            raise PayloadValidationError(
                f"a multimodal event payload must be a JSON object, got {type(payload).__name__}",
            )
        body = payload.get("payload")
        if not isinstance(body, dict):
            raise PayloadValidationError(
                "a multimodal envelope must carry an object payload",
            )
        # A's own bound and text derivation, reused rather than restated.
        return cls(envelope=payload, observation=ObservationPayload.parse(body))


async def handle_multimodal_observation(
    ctx: Any,
    event: Any,
    payload: MultimodalObservationPayload,
) -> HandlerResult:
    """Interpret one multimodal observation through A's existing handler.

    Delegates in full: the Parking Brake deferral, the Identity-policy
    refusal, the uncertain/irrelevant/recorded verdicts and the transient
    retries are all A's, unchanged. The only thing added here is the
    envelope's provenance on the resulting detail, so an audit reading a
    disposition can see which capture session and which correlation the
    evidence came from without re-reading the payload.
    """
    result = await handle_observation(ctx, event, payload.observation)
    detail = dict(result.detail)
    detail["multimodal"] = _envelope_provenance(payload.envelope)
    return HandlerResult(
        disposition=result.disposition,
        reason=result.reason,
        detail=detail,
    )


async def handle_multimodal_record(
    ctx: Any,  # noqa: ARG001 - the record is the whole of the handling
    event: Any,  # noqa: ARG001
    payload: MultimodalObservationPayload,
) -> HandlerResult:
    """Settle a non-observational multimodal event without interpreting it.

    Bartholomew's own utterances and a capture session's lifecycle are audit,
    not evidence about the person's world. The durable record is the
    `inbound_events` row, which already exists by the time a handler runs; the
    right processing disposition is a definite `irrelevant`, not a `refused`
    that would read as "we could not decide".
    """
    return HandlerResult(
        disposition=STATE_IRRELEVANT,
        reason="multimodal_record_not_evidence",
        detail={
            "recorded": True,
            "multimodal": _envelope_provenance(payload.envelope),
        },
    )


MULTIMODAL_TRANSCRIPT_SPEC = register(
    RegisteredEventType(
        event_type=EVENT_TYPE_TRANSCRIPT,
        parse=MultimodalObservationPayload.parse,
        handler=handle_multimodal_observation,
        description=(
            "A bounded, redacted transcript from an explicitly-started microphone "
            "session. Interpreted against open objectives exactly as any other "
            "observation is; carries no audio, because none is retained."
        ),
    ),
)

MULTIMODAL_SCREEN_SPEC = register(
    RegisteredEventType(
        event_type=EVENT_TYPE_SCREEN,
        parse=MultimodalObservationPayload.parse,
        handler=handle_multimodal_observation,
        description=(
            "A bounded observation of one permitted screen region or window. "
            "Interpreted as an observation; the privacy classification C assigned "
            "travels with it and is never recomputed downstream."
        ),
    ),
)

MULTIMODAL_ACCESSIBILITY_SPEC = register(
    RegisteredEventType(
        event_type=EVENT_TYPE_ACCESSIBILITY,
        parse=MultimodalObservationPayload.parse,
        handler=handle_multimodal_observation,
        description=(
            "A structured UI-tree observation with secret fields already omitted "
            "at capture. Interpreted as an observation."
        ),
    ),
)

MULTIMODAL_SPEECH_SPEC = register(
    RegisteredEventType(
        event_type=EVENT_TYPE_SPEECH,
        parse=MultimodalObservationPayload.parse,
        handler=handle_multimodal_record,
        description=(
            "A record of something Bartholomew said aloud. Recorded and settled "
            "without interpretation: the system's own output is not an "
            "observation of the person's world."
        ),
    ),
)

MULTIMODAL_SESSION_STATE_SPEC = register(
    RegisteredEventType(
        event_type=EVENT_TYPE_SESSION_STATE,
        parse=MultimodalObservationPayload.parse,
        handler=handle_multimodal_record,
        description=(
            "Capture-session lifecycle -- started, stopped, unavailable, broken, "
            "permission denied. Audit, recorded and settled without "
            "interpretation; it is what keeps those states distinguishable."
        ),
    ),
)


__all__ = [
    "VERIFIED_BY_MULTIMODAL",
    "CanonicalIngressSink",
    "MultimodalIngressError",
    "MultimodalObservationPayload",
    "handle_multimodal_observation",
    "handle_multimodal_record",
]
