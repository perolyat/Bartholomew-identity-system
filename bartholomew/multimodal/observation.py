"""The observation event: what was seen, kept apart from what it might mean.

**Shared contract `observation-event` (owner W03-A).** W03-B, W03-D, W03-E
and W03-F consume this shape; only W03-A changes it. It is the record an
active capture session emits on every tick, the record the read-back
primitive returns to the Verify leg, and the payload that travels into the
canonical backbone (`inbound_events` + `event_processing`) under the existing
`multimodal.accessibility.observation` / `multimodal.screen.observation` event
types. No new event bus and no new event type: the shape rides the five
registered multimodal types unchanged.

Why the separation is structural, not editorial
-----------------------------------------------
The research brief behind Wave 3 requires that Bartholomew can represent
*"the user has been inactive for 20 minutes"* **without** asserting *"the user
is overwhelmed and requires intervention"*. Before this module, nothing in the
repository could: an observation and its interpretation were one string, and a
consumer had no way to tell evidence from guess. So the record carries five
**distinct** fields, and the constructor refuses a record that blurs them:

* `observed_event` -- what the sensor actually read: a kind, a bounded set of
  facts, and a one-line plain statement of those facts. Never an inference.
* `inferred_state` -- a clearly-marked guess about the person or their work,
  or `None` when nothing is inferred. It is *never required*: an observation
  with no inference is a complete, valid record.
* `confidence` -- present exactly when `inferred_state` is, and strictly
  below `1.0`. A certain thing is an observation, not an inference; a record
  claiming a certain inference is refused.
* `competing_explanations` -- non-empty whenever an inference is present, so
  the record itself preserves the uncertainty a downstream decision must
  honour (W03-B's "seek clarification, do not intervene on weak inference").
* `provenance` -- source, `occurred_at`, `captured_at`, and a content digest,
  so the record can be traced to the session that produced it and proven
  unchanged.

The privacy/retention classification of the *content* travels with the
record too, because raw material never does: the facts are already bounded,
redacted and truncation-marked by `privacy.sanitise` before they reach here.

What this module does not do
----------------------------
It does not decide. Nothing here proposes an action, opens an objective,
writes memory or grants anything -- an `ObservationEvent` is evidence, and the
kernel's existing handlers interpret it the way they interpret any other
observation. It also does not persist anything: it is a value type, serialized
by `events.serialize_observation_event` and delivered through the one
installed `MultimodalEventSink`.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .privacy import MAX_OBSERVATION_CHARS, Classification, bound_text

#: Shape version of the observation event. Bumping it is a shared-contract
#: change, made through W03-PREP, never a local edit.
OBSERVATION_EVENT_VERSION = 1

#: Observed-event kinds this package produces. Consumers branch on these;
#: anything else is a contract change.
OBSERVED_WINDOW_STATE = "window_state"
OBSERVED_INACTIVITY = "inactivity"
OBSERVED_UNAVAILABLE = "unavailable"
OBSERVED_READ_BACK = "read_back"

OBSERVED_KINDS: frozenset[str] = frozenset(
    {OBSERVED_WINDOW_STATE, OBSERVED_INACTIVITY, OBSERVED_UNAVAILABLE, OBSERVED_READ_BACK},
)

#: Bounds on what one observed event may carry. Tighter than the envelope's
#: 16 KiB ceiling on purpose, so an ordinary tick never approaches it and a
#: pathological accessibility tree is bounded *here*, with the truncation
#: recorded, rather than refused downstream.
MAX_FACT_ELEMENTS = 24
MAX_FACTS_JSON_CHARS = 12_000
MAX_SUMMARY_CHARS = 300
MAX_COMPETING_EXPLANATIONS = 8
MAX_INFERRED_STATE_CHARS = 120


class ObservationContractError(ValueError):
    """The record would blur observation and inference, or is unbounded."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> str:
    """Strict canonical JSON: a fact that is not plain JSON is refused, not coerced."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode()).hexdigest()


@dataclass(frozen=True)
class ObservedEvent:
    """What was actually read. Facts only.

    `facts` is a bounded, JSON-encodable mapping of what the sensor reported
    -- application, window title, the bounded elements, an idle duration.
    `summary` is one plain sentence stating those facts and nothing more:
    it is what a person or the kernel reads, and it must not contain a
    conclusion about the person's state.
    """

    kind: str
    summary: str
    facts: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in OBSERVED_KINDS:
            raise ObservationContractError(
                f"unknown observed-event kind {self.kind!r}; the vocabulary is "
                f"{sorted(OBSERVED_KINDS)}",
            )
        summary = (self.summary or "").strip()
        if not summary:
            raise ObservationContractError("an observed event needs a plain-language summary")
        if len(summary) > MAX_SUMMARY_CHARS:
            raise ObservationContractError(
                f"observed-event summary is {len(summary)} characters, over the "
                f"{MAX_SUMMARY_CHARS} bound; bound it (and record the bound) before "
                "building the event",
            )
        if not isinstance(self.facts, dict):
            raise ObservationContractError("observed-event facts must be a mapping")
        try:
            encoded = _canonical(self.facts)
        except (TypeError, ValueError) as exc:
            raise ObservationContractError(
                f"observed-event facts are not JSON-encodable: {exc}",
            ) from exc
        if len(encoded) > MAX_FACTS_JSON_CHARS:
            raise ObservationContractError(
                f"observed-event facts are {len(encoded)} characters, over the "
                f"{MAX_FACTS_JSON_CHARS} bound; bound the observation first",
            )
        object.__setattr__(self, "summary", summary)

    def digest(self) -> str:
        return _digest({"kind": self.kind, "summary": self.summary, "facts": self.facts})

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "summary": self.summary, "facts": dict(self.facts)}

    # -- constructors for the kinds this package produces --------------------

    @classmethod
    def inactivity(
        cls,
        idle_seconds: float,
        *,
        facts: dict[str, Any] | None = None,
    ) -> ObservedEvent:
        """`the user has been inactive for N minutes` -- and nothing more.

        This is the canonical non-collapse case: the record states a duration
        with no input, and *does not* say what the person is doing or feeling.
        """
        if idle_seconds is None or (isinstance(idle_seconds, float) and math.isnan(idle_seconds)):
            raise ObservationContractError("inactivity needs a numeric idle duration")
        seconds = max(0.0, float(idle_seconds))
        minutes = int(seconds // 60)
        if minutes >= 1:
            summary = (
                f"no keyboard or mouse input for {minutes} minute{'s' if minutes != 1 else ''}"
            )
        else:
            summary = f"no keyboard or mouse input for {int(seconds)} seconds"
        body = {"idle_seconds": round(seconds, 1)}
        if facts:
            body.update(facts)
        return cls(kind=OBSERVED_INACTIVITY, summary=summary, facts=body)

    @classmethod
    def unavailable(cls, reason: str, *, facts: dict[str, Any] | None = None) -> ObservedEvent:
        """The sensor could not read. Recorded as such, never as a blank screen."""
        summary, _ = bound_text(
            f"observation unavailable: {reason or 'unknown reason'}",
            MAX_SUMMARY_CHARS,
        )
        return cls(kind=OBSERVED_UNAVAILABLE, summary=summary, facts=dict(facts or {}))


@dataclass(frozen=True)
class ObservationProvenance:
    """Where and when the observation came from, and a digest to prove it."""

    #: `multimodal.accessibility` / `multimodal.screen` / `multimodal.read_back`.
    source: str
    #: When the observed state held (for inactivity: now; the idle duration
    #: says how long it has held). RFC3339 UTC.
    occurred_at: str
    #: When the capture session read it. This is the *sensor's* capture
    #: moment, stamped in-process by the session that owns the device. The
    #: envelope-level `captured_at` remains ingress's to assign (§3.1) and is
    #: emitted as None; the two are different facts and both are kept.
    captured_at: str
    #: `sha256:` digest of the observed event (kind, summary, facts).
    digest: str
    #: The session that produced it, so a consumer can find the consent record.
    session_id: str | None = None
    device_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("source", "occurred_at", "captured_at", "digest"):
            if not getattr(self, name):
                raise ObservationContractError(f"observation provenance requires {name}")
        if not self.digest.startswith("sha256:"):
            raise ObservationContractError("the provenance digest must be a sha256: digest")

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "occurred_at": self.occurred_at,
            "captured_at": self.captured_at,
            "digest": self.digest,
            "session_id": self.session_id,
            "device_id": self.device_id,
        }


@dataclass
class ObservationEvent:
    """One observation, with any inference held separately and marked.

    The constructor is the contract. It refuses:

    * an inference without a confidence, or a confidence without an inference;
    * a confidence outside `[0.0, 1.0)` -- an inference is never certain;
    * an inference with no competing explanation -- the record must preserve
      the uncertainty, not merely label it;
    * competing explanations on a record that infers nothing (there is
      nothing for them to compete with);
    * a provenance digest that does not match the observed event.
    """

    observed_event: ObservedEvent
    provenance: ObservationProvenance
    inferred_state: str | None = None
    confidence: float | None = None
    competing_explanations: list[str] = field(default_factory=list)
    #: The privacy/retention classification of the *content*, with the
    #: redaction and truncation trail. Never recomputed downstream.
    classification: Classification = field(default_factory=Classification)

    def __post_init__(self) -> None:
        if self.inferred_state is not None:
            state = str(self.inferred_state).strip()
            if not state:
                raise ObservationContractError("an inferred state cannot be blank; use None")
            if len(state) > MAX_INFERRED_STATE_CHARS:
                raise ObservationContractError("inferred_state is over its length bound")
            self.inferred_state = state
            if self.confidence is None:
                raise ObservationContractError(
                    "an inference must carry a confidence; an unmarked inference "
                    "is indistinguishable from an observation",
                )
            if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
                raise ObservationContractError("confidence must be a number")
            value = float(self.confidence)
            if math.isnan(value) or value < 0.0 or value >= 1.0:
                raise ObservationContractError(
                    f"confidence {value!r} is outside [0.0, 1.0); an inference is never "
                    "certain -- a certain thing is an observed event",
                )
            self.confidence = value
            if not self.competing_explanations:
                raise ObservationContractError(
                    "an inference must name at least one competing explanation so the "
                    "record preserves its own uncertainty",
                )
        else:
            if self.confidence is not None:
                raise ObservationContractError(
                    "confidence without an inferred state is meaningless",
                )
            if self.competing_explanations:
                raise ObservationContractError(
                    "competing explanations without an inferred state have nothing to compete with",
                )
        explanations: list[str] = []
        for item in self.competing_explanations:
            text = str(item).strip()
            if text:
                explanations.append(text[:MAX_SUMMARY_CHARS])
        if len(explanations) > MAX_COMPETING_EXPLANATIONS:
            raise ObservationContractError("too many competing explanations; bound them")
        self.competing_explanations = explanations
        if self.provenance.digest != self.observed_event.digest():
            raise ObservationContractError(
                "the provenance digest does not match the observed event; the record "
                "was altered after capture or built from a different observation",
            )

    @property
    def has_inference(self) -> bool:
        return self.inferred_state is not None

    def as_dict(self) -> dict[str, Any]:
        """The serialized shape. Field names here are the contract."""
        return {
            "observation_event_version": OBSERVATION_EVENT_VERSION,
            "observed_event": self.observed_event.as_dict(),
            "inferred_state": self.inferred_state,
            "confidence": self.confidence,
            "competing_explanations": list(self.competing_explanations),
            "provenance": self.provenance.as_dict(),
            "classification": self.classification.as_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ObservationEvent:
        """Rebuild from the serialized shape. Re-runs every contract check."""
        if not isinstance(data, dict):
            raise ObservationContractError("an observation event is a mapping")
        if data.get("observation_event_version") != OBSERVATION_EVENT_VERSION:
            raise ObservationContractError(
                f"unsupported observation_event_version {data.get('observation_event_version')!r}",
            )
        observed_raw = data.get("observed_event") or {}
        observed = ObservedEvent(
            kind=str(observed_raw.get("kind") or ""),
            summary=str(observed_raw.get("summary") or ""),
            facts=dict(observed_raw.get("facts") or {}),
        )
        prov_raw = data.get("provenance") or {}
        provenance = ObservationProvenance(
            source=str(prov_raw.get("source") or ""),
            occurred_at=str(prov_raw.get("occurred_at") or ""),
            captured_at=str(prov_raw.get("captured_at") or ""),
            digest=str(prov_raw.get("digest") or ""),
            session_id=prov_raw.get("session_id"),
            device_id=prov_raw.get("device_id"),
        )
        classification = Classification()
        cls_raw = data.get("classification") or {}
        try:
            from .privacy import PrivacyClass, RedactionRecord, RetentionClass

            if cls_raw.get("privacy_class"):
                classification.privacy_class = PrivacyClass(cls_raw["privacy_class"])
            if cls_raw.get("retention_class"):
                classification.retention_class = RetentionClass(cls_raw["retention_class"])
            classification.truncated = bool(cls_raw.get("truncated"))
            classification.redactions = [
                RedactionRecord(where=str(r.get("where")), rule=str(r.get("rule")))
                for r in (cls_raw.get("redactions") or [])
                if isinstance(r, dict)
            ]
        except ValueError as exc:
            raise ObservationContractError(f"unknown classification vocabulary: {exc}") from exc
        return cls(
            observed_event=observed,
            provenance=provenance,
            inferred_state=data.get("inferred_state"),
            confidence=data.get("confidence"),
            competing_explanations=list(data.get("competing_explanations") or []),
            classification=classification,
        )


def make_provenance(
    observed: ObservedEvent,
    *,
    source: str,
    session_id: str | None,
    device_id: str | None,
    occurred_at: str | None = None,
    captured_at: str | None = None,
) -> ObservationProvenance:
    """Provenance for `observed`, digest computed here so it cannot be wrong."""
    now = captured_at or _now_iso()
    return ObservationProvenance(
        source=source,
        occurred_at=occurred_at or now,
        captured_at=now,
        digest=observed.digest(),
        session_id=session_id,
        device_id=device_id,
    )


def observed_text(event: ObservationEvent) -> str:
    """The observed content as one bounded block of text -- for read-back.

    Deliberately reads only `observed_event`: the inference is not "the
    current UI state", and a Verify step that read an inference back as
    evidence would be verifying against a guess.
    """
    parts: list[str] = [event.observed_event.summary]
    facts = event.observed_event.facts
    for element in facts.get("elements") or []:
        if not isinstance(element, dict):
            continue
        role = str(element.get("role") or "").strip()
        name = str(element.get("name") or "").strip()
        value = element.get("value")
        line = f"{role}: {name}".strip(": ")
        if value not in (None, ""):
            line += f" = {value}"
        if element.get("focused"):
            line += " [focused]"
        if line:
            parts.append(line)
    text, _ = bound_text("\n".join(parts), MAX_OBSERVATION_CHARS)
    return text


__all__ = [
    "MAX_FACT_ELEMENTS",
    "OBSERVATION_EVENT_VERSION",
    "OBSERVED_INACTIVITY",
    "OBSERVED_KINDS",
    "OBSERVED_READ_BACK",
    "OBSERVED_UNAVAILABLE",
    "OBSERVED_WINDOW_STATE",
    "ObservationContractError",
    "ObservationEvent",
    "ObservationProvenance",
    "ObservedEvent",
    "make_provenance",
    "observed_text",
]
