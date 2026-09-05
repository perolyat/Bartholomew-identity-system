"""The one adapter, and the event types it is registered for.

This is the whole of the backbone's connection to meaning, and it is
deliberately thin: it calls
`bartholomew.kernel.inbound_interpretation.interpret_captured_event()` --
the existing interpretation seam -- and translates that seam's answer into a
durable disposition. It does not classify anything itself, does not read the
payload for content, does not decide relevance, and does not write to any
store. Every one of those remains the property of an existing authority:

* what an event means                 -> `inbound_interpretation`
* whether a mutation is permitted     -> the Parking Brake / Identity policy,
                                         through `run_objective_through_
                                         runtime_contract`
* what an objective's history says    -> `objective_store`

What the backbone adds is only *when* that seam runs, *how many times*, and
*what is recorded about the outcome*. Before this package, `interpret_
captured_event()` had no caller in the running system at all: capture
returned, and something had to explicitly ask what the event meant. The
adapter is that caller, and the scheduler drive is what invokes it.

Registered types
----------------
Registration is a first-party code change, made here, and it is the only
place the backbone learns a type. The two below are the observation family:
a verified external source stating something that may bear on work the user
already has open. Both are handled identically, because the *handling* is
domain-blind -- the seam draws text from the payload's string leaves by
structure alone and matches it against live objectives -- and the two names
exist so a sender can say which of the two it is sending without the
processing path caring.

Adding a type means: a constant here, a parser, a registration, and a test.
There is no configuration, discovery or plugin path by which one can appear.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .envelope import CanonicalEvent
from .registry import (
    HandlerResult,
    PayloadValidationError,
    RegisteredEventType,
    register,
)
from .store import STATE_IRRELEVANT, STATE_PROCESSED, STATE_REFUSED

logger = logging.getLogger(__name__)

#: A verified source states something in prose.
OBSERVATION_NOTE = "observation.note"

#: A verified source reports the state of something.
OBSERVATION_STATUS = "observation.status"

#: Largest payload object this parser will accept, in JSON characters.
#: Capture already bounds the request body; this bounds what processing is
#: willing to walk, independently of how the row got there (a row restored
#: from a backup, say, never passed through the HTTP cap).
MAX_PAYLOAD_CHARS = 64 * 1024

#: How deep a payload may nest. Matches the interpretation seam's own bound,
#: so a payload this accepts is one that seam can actually read.
MAX_PAYLOAD_DEPTH = 6


class TransientProcessingError(RuntimeError):
    """The attempt failed for a reason that may not recur.

    Costs the event an attempt and returns it to the queue; enough of them in
    a row is what quarantine is for. Distinct from `PayloadValidationError`,
    which is deterministic and is refused outright rather than retried.
    """


class BrakeDeferredError(RuntimeError):
    """A Parking Brake or platform halt refused the governed write.

    Not a failure of the event and never counted as an attempt: the event
    goes back to the queue exactly as it was, so releasing the brake resumes
    a backlog that was preserved rather than eroded.
    """


@dataclass(frozen=True)
class ObservationPayload:
    """A parsed observation payload.

    Domain-blind by construction: no key is privileged, nothing is required
    to be present under a particular name, and `text` is derived through the
    interpretation seam's own public `candidate_text()` rather than by a
    second opinion about where a payload keeps its words.
    """

    raw: dict[str, Any]
    text: str

    @property
    def has_text(self) -> bool:
        return bool(self.text.strip())

    @classmethod
    def parse(cls, payload: Any) -> ObservationPayload:
        from bartholomew.kernel.inbound_interpretation import candidate_text

        if not isinstance(payload, dict):
            raise PayloadValidationError(
                f"an observation payload must be a JSON object, got {type(payload).__name__}",
            )
        _require_bounded(payload)
        return cls(raw=payload, text=candidate_text(payload))


def _require_bounded(payload: dict[str, Any], *, depth: int = 0) -> None:
    """Refuse a payload too deep or too large to interpret.

    A bound that is checked rather than assumed. The alternative -- letting a
    pathological payload through and relying on the interpretation seam's own
    truncation -- would silently interpret a *fragment* of an event as if it
    were the event.
    """
    import json

    if depth == 0:
        try:
            encoded = json.dumps(payload, default=str)
        except (TypeError, ValueError) as e:
            raise PayloadValidationError(f"payload is not JSON-encodable: {e}") from e
        if len(encoded) > MAX_PAYLOAD_CHARS:
            raise PayloadValidationError(
                f"payload is {len(encoded)} characters, over the "
                f"{MAX_PAYLOAD_CHARS}-character bound processing will read",
            )
    if depth > MAX_PAYLOAD_DEPTH:
        raise PayloadValidationError(
            f"payload nests deeper than {MAX_PAYLOAD_DEPTH} levels, which is "
            "deeper than interpretation reads",
        )
    values: Any
    if isinstance(payload, dict):
        values = payload.values()
    elif isinstance(payload, (list, tuple)):
        values = payload
    else:
        return
    for value in values:
        if isinstance(value, (dict, list, tuple)):
            _require_bounded(value, depth=depth + 1)


async def handle_observation(
    ctx: Any,
    event: CanonicalEvent,
    payload: ObservationPayload,
) -> HandlerResult:
    """Run one observation through the existing interpretation seam.

    The mapping from the seam's answer to a durable disposition is the only
    judgement this function makes, and each line of it is a deliberate
    position:

    ``relevant`` and recorded
        `processed`. One `objective_events` row of kind `fact` now exists,
        carrying the capture's own provenance.

    ``relevant`` and already recorded
        `processed`, with nothing written. A redelivery, a lease recovery or
        an operator requeue reaching an event whose evidence is already in
        the objective's history is a *success*, not a second write and not a
        failure -- which is what makes "attaches exactly once" hold across
        every path that can run interpretation twice.

    ``irrelevant``
        `irrelevant`. An explicit verdict: nothing in the event bears on
        anything Bartholomew is carrying. Terminal, and deliberately not
        `refused` -- the system reached an answer rather than declining to.

    ``uncertain``
        `refused`. Two plausible objectives, hedged language, a question, or
        an external party asking Bartholomew to *do* something. Nothing is
        recorded, on purpose, and calling that `irrelevant` would manufacture
        a verdict the seam explicitly declined to give.

    Identity-policy denial
        `refused`, terminal, with the policy's own reason. An operator who
        then allows the action can requeue it.

    Parking Brake
        `BrakeDeferredError`. Not a disposition at all: the event returns to the
        queue untouched.
    """
    from bartholomew.kernel.inbound_interpretation import (
        RELEVANCE_IRRELEVANT,
        RELEVANCE_UNCERTAIN,
        interpret_captured_event,
    )
    from bartholomew.kernel.objective_store import (
        InvalidTransitionError,
        ObjectiveNotFoundError,
    )

    try:
        result = await interpret_captured_event(ctx, stored=event, payload=payload.raw)
    except (ObjectiveNotFoundError, InvalidTransitionError) as e:
        # The matched objective reached a terminal state between the read and
        # the write. Retryable on purpose: the next pass will not see it in
        # the live set at all, and the event settles honestly as irrelevant.
        raise TransientProcessingError(
            f"the matched objective changed under the write: {type(e).__name__}: {e}",
        ) from e

    outcome = getattr(result, "outcome", None)
    interpretation = result.interpretation

    if outcome == "unavailable":
        raise TransientProcessingError(
            "no objective store is wired into this runtime; nothing was interpreted",
        )
    if outcome == "parking_brake_denied":
        raise BrakeDeferredError(getattr(result, "reason", None) or "parking brake engaged")
    if outcome == "error":
        raise TransientProcessingError(
            getattr(result, "reason", None) or "the objective seam reported an error",
        )
    if not getattr(result, "governance_allowed", True):
        return HandlerResult(
            disposition=STATE_REFUSED,
            reason="governance_denied",
            detail={"governance_reason": getattr(result, "reason", None)},
        )

    if result.already_recorded:
        return HandlerResult(
            disposition=STATE_PROCESSED,
            reason="already_recorded",
            detail={
                "objective_id": interpretation.objective_id,
                "recorded_now": False,
            },
        )

    if interpretation.relevance == RELEVANCE_IRRELEVANT:
        return HandlerResult(
            disposition=STATE_IRRELEVANT,
            reason=interpretation.reason,
            detail={"relevance": interpretation.relevance},
        )

    if interpretation.relevance == RELEVANCE_UNCERTAIN:
        return HandlerResult(
            disposition=STATE_REFUSED,
            reason=interpretation.reason,
            detail={
                "relevance": interpretation.relevance,
                "candidate_objective_ids": list(interpretation.candidate_objective_ids),
            },
        )

    if not result.recorded:
        # Relevant, permitted, not refused -- and yet nothing exists. Never
        # reported as processed: an unexplained non-write is a fault, and a
        # bounded retry is the right response to a fault.
        raise TransientProcessingError(
            f"interpretation reported {outcome!r} but wrote no objective event",
        )

    event_row = getattr(result, "event", None)
    return HandlerResult(
        disposition=STATE_PROCESSED,
        reason="evidence_recorded",
        detail={
            "objective_id": interpretation.objective_id,
            "objective_event_id": getattr(event_row, "id", None),
            "event_kind": getattr(event_row, "event_kind", None),
            "recorded_now": True,
            "provenance": result.provenance,
        },
    )


OBSERVATION_NOTE_SPEC = register(
    RegisteredEventType(
        event_type=OBSERVATION_NOTE,
        parse=ObservationPayload.parse,
        handler=handle_observation,
        description=(
            "A verified external source states something in prose. Interpreted "
            "against the objectives already open; attached as evidence only when "
            "exactly one is plainly the subject and the event asserts rather than asks."
        ),
    ),
)

OBSERVATION_STATUS_SPEC = register(
    RegisteredEventType(
        event_type=OBSERVATION_STATUS,
        parse=ObservationPayload.parse,
        handler=handle_observation,
        description=(
            "A verified external source reports the state of something. Handled "
            "identically to observation.note; the distinction is the sender's, "
            "not the processing path's."
        ),
    ),
)


__all__ = [
    "MAX_PAYLOAD_CHARS",
    "MAX_PAYLOAD_DEPTH",
    "OBSERVATION_NOTE",
    "OBSERVATION_NOTE_SPEC",
    "OBSERVATION_STATUS",
    "OBSERVATION_STATUS_SPEC",
    "BrakeDeferredError",
    "ObservationPayload",
    "TransientProcessingError",
    "handle_observation",
]
