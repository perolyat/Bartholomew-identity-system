"""Downstream interpretation of an already-captured inbound event.

Capture answers "did something arrive, and from where?". It stops there, and
`docs/D_ALWAYS_ON_AND_INBOUND.md` §5 is emphatic that it must: the
`inbound_events` row records *that something arrived*, never that Bartholomew
believes it. This module is the step after that, and only that step:

    captured inbound event
        -> downstream interpretation
        -> relevance assessment
        -> existing live objective match
        -> candidate evidence
        -> objective history

It is deliberately **not** wired into the ingress. Nothing in
`run_inbound_through_runtime_contract()`, the HTTP route or `inbound_store`
calls anything here, so the capture boundary is exactly where it was: a
caller that wants meaning asks for it explicitly, after capture has already
returned. That also keeps the acknowledgement honest -- a 202 still means
"captured, explicitly not processed".

Three outcomes, and the two that are not "relevant" both write nothing
----------------------------------------------------------------------
`RELEVANCE_IRRELEVANT`
    Nothing in the event bears on anything Bartholomew is carrying. No
    downstream objective mutation of any kind.

`RELEVANCE_RELEVANT`
    Exactly one live objective is plainly the subject, and the event states
    something rather than asking for something. It may be attached to *that*
    objective as `fact` evidence, carrying the capture's own provenance.

`RELEVANCE_UNCERTAIN`
    Something matched, but not well enough to be recorded as so: two
    plausible objectives, hedged or speculative language, or an external
    party telling Bartholomew to *do* something. This never silently becomes
    fact and is never attached to an arbitrary objective. It is reported to
    the caller and forgotten.

The asymmetry is the same one `objective_intents.relates_to()` records: a
missed attachment costs one piece of continuity, while a wrong or invented
one puts something into an objective's history that will later be read back
as if it belonged there.

What this module must never become
----------------------------------
* **Domain-aware.** `event_type` stays an opaque captured property. Nothing
  here branches on it -- no `if email`, no `if calendar`, no provider table.
  Text is drawn from the payload's string leaves wherever they happen to be,
  by structure alone.
* **A creator of objectives.** Only *existing live* objectives are matched.
  An unsolicited external event can never cause Bartholomew to take on new
  work, and `run_objective_through_runtime_contract` is only ever called here
  with the `record` transition.
* **An authority to act.** Noticing that something matters is not permission
  to do anything about it. No skill is invoked, nothing is scheduled, no
  message is sent, and no lifecycle transition (complete/block/abandon) is
  reachable from this module. An external sender writing "book the roofer"
  produces, at most, an UNCERTAIN with nothing recorded.
* **An extractor of personal memory.** Nothing here writes Memory. The only
  durable effect available is one `objective_events` row of kind `fact`, and
  only through the existing governed seam, which carries the Parking Brake
  and Identity policy gates with it.

Idempotency
-----------
Capture's `UNIQUE(source_id, event_id)` already makes a redelivery a
duplicate rather than a second logical event. This module honours that twice
over: a `StoredInboundEvent` marked `duplicate` is refused outright, and
before writing, the target objective's own history is checked for an event
already carrying this (`source_id`, `event_id`) provenance. The second check
is what makes re-running interpretation over the same captured row safe --
the first only covers the capture call itself.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from bartholomew.kernel import objective_intents, objective_store

logger = logging.getLogger(__name__)

#: Relevance verdicts. Exactly three, and only the middle one may write.
RELEVANCE_IRRELEVANT = "irrelevant"
RELEVANCE_RELEVANT = "relevant"
RELEVANCE_UNCERTAIN = "uncertain"

#: `provenance.source_kind` for evidence that reached an objective this way.
#: Distinct from chat's `external_capability`: this content was pushed at
#: Bartholomew by a third party rather than fetched by it, and a reader of the
#: history is entitled to know which.
SOURCE_KIND_INBOUND = "inbound_event"

#: The actor recorded against the objective event. Prefixed, like
#: `chat:forecast`, so an objective's history says which surface wrote a row.
ACTOR_PREFIX = "inbound"

#: Longest evidence summary written into an objective's history. The full
#: payload stays in `inbound_events`; the objective carries a readable line
#: and the provenance needed to go back to the original.
MAX_SUMMARY_CHARS = 300

#: How much payload text is considered at all. A pathological payload must
#: not turn interpretation into an unbounded scan.
MAX_TEXT_CHARS = 4000
MAX_TEXT_LEAVES = 200
MAX_DEPTH = 6

#: Language that makes an assertion something less than an assertion. Any of
#: these anywhere in the candidate text downgrades a match to UNCERTAIN
#: rather than recording it as fact.
_HEDGE_PATTERN = re.compile(
    r"\b(maybe|might|may|possibly|perhaps|probably|unsure|unclear|not sure|"
    r"we think|i think|could be|tentative|tentatively|provisional|"
    r"to be confirmed|tbc|tbd|if that works|hopefully|should be able)\b",
    re.IGNORECASE,
)

#: Language by which an external sender asks Bartholomew to *do* something.
#: Recognised in order to refuse it: a request is not evidence, and an
#: inbound event is never an instruction. Matched at the start of a sentence
#: *within a single payload string*, so that "we can confirm attendance" is
#: not read as a request and so that two unrelated fields concatenated do not
#: fabricate a sentence boundary that neither of them contains.
_DIRECTIVE_PATTERN = re.compile(
    r"(?:^|[.!?]\s+)\s*(please\b|can you\b|could you\b|would you\b|"
    r"book\b|cancel\b|pay\b|send\b|reply\b|forward\b|call\b|email\b|"
    r"schedule\b|approve\b|confirm\b|transfer\b|delete\b)",
    re.IGNORECASE,
)

#: A question is not a statement of fact either.
_QUESTION_PATTERN = re.compile(r"\?")


@dataclass(frozen=True)
class InboundInterpretation:
    """What one captured event was judged to mean. Pure data, no effect.

    `relevance` is the verdict; `reason` is why, in a stable machine-readable
    form so a caller can report it without re-deriving the judgement.
    `objective_id` is set only for RELEVANT -- an UNCERTAIN verdict names no
    objective, because naming one is halfway to attaching to it.
    """

    relevance: str
    reason: str
    summary: str | None = None
    objective_id: int | None = None
    objective_title: str | None = None
    #: Objectives that plausibly matched, for the caller's explanation only.
    candidate_objective_ids: tuple[int, ...] = ()

    @property
    def is_evidence(self) -> bool:
        return self.relevance == RELEVANCE_RELEVANT

    def to_dict(self) -> dict[str, Any]:
        return {
            "relevance": self.relevance,
            "reason": self.reason,
            "summary": self.summary,
            "objective_id": self.objective_id,
            "objective_title": self.objective_title,
            "candidate_objective_ids": list(self.candidate_objective_ids),
            "is_evidence": self.is_evidence,
        }


@dataclass(frozen=True)
class InboundInterpretationResult:
    """Outcome of interpreting one captured event, including what was written.

    `recorded` is true only when a new `objective_events` row of kind `fact`
    exists because of this call. An already-attached redelivery reports
    `recorded=False` with `already_recorded=True`: nothing new happened, and
    saying otherwise would make a retry look like fresh evidence.
    """

    interpretation: InboundInterpretation
    recorded: bool = False
    already_recorded: bool = False
    governance_allowed: bool = True
    outcome: str = "no_action"
    reason: str | None = None
    event: Any = None
    provenance: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------- the pure half


def _text_leaves(payload: Any, depth: int = 0) -> list[str]:
    """Every string in the payload, wherever it sits.

    Structural, not semantic: no key is privileged, so no provider's field
    naming becomes load-bearing and adding a new provider needs no change
    here. Bounded in depth and count so a hostile payload cannot make this
    expensive.
    """
    if depth > MAX_DEPTH:
        return []
    if isinstance(payload, str):
        text = payload.strip()
        return [text] if text else []
    out: list[str] = []
    if isinstance(payload, dict):
        for value in payload.values():
            out.extend(_text_leaves(value, depth + 1))
            if len(out) >= MAX_TEXT_LEAVES:
                break
    elif isinstance(payload, (list, tuple)):
        for value in payload:
            out.extend(_text_leaves(value, depth + 1))
            if len(out) >= MAX_TEXT_LEAVES:
                break
    return out[:MAX_TEXT_LEAVES]


def candidate_text(payload: Any) -> str:
    """The event's content as one bounded block of text.

    Deliberately includes nothing but the payload: `event_type` is an opaque
    captured property and feeding it into matching would let a sender steer
    the match by naming its event after an objective.
    """
    leaves = _text_leaves(payload)
    joined = " ".join(leaves)
    return joined[:MAX_TEXT_CHARS]


def _summary_of(payload: Any) -> str | None:
    """The single most informative line in the payload, bounded.

    The longest string leaf: the assertion a human would read, without
    guessing at which key a provider chose to put it under. The full payload
    remains in `inbound_events`, which the provenance points back to.
    """
    leaves = _text_leaves(payload)
    if not leaves:
        return None
    best = max(leaves, key=len).strip()
    if not best:
        return None
    return best[:MAX_SUMMARY_CHARS]


def interpret(payload: Any, live_objectives: list[Any]) -> InboundInterpretation:
    """Classify one captured event against the objectives already being carried.

    Pure: no I/O, no clock, no model call, no persistence -- the same
    discipline `objective_intents` holds to, and for the same reason. The
    governed half is `interpret_captured_event()`.

    `live_objectives` must already be the live set (`ObjectiveStore.list_live`).
    Nothing here filters terminal objectives, because nothing here should be
    handed one; a completed objective must be out of reach structurally,
    upstream, rather than by a filter this module could forget.
    """
    leaves = _text_leaves(payload)
    text = candidate_text(payload)
    if not text:
        # Nothing was asserted. Not ambiguous -- there is simply no claim in
        # the event to be relevant to anything.
        return InboundInterpretation(
            relevance=RELEVANCE_IRRELEVANT,
            reason="no_interpretable_content",
        )

    matched = [o for o in live_objectives if objective_intents.relates_to(o, text)]

    if not matched:
        return InboundInterpretation(
            relevance=RELEVANCE_IRRELEVANT,
            reason="no_matching_live_objective",
            summary=_summary_of(payload),
        )

    if len(matched) > 1:
        # Two plausible homes. Picking one would file the content against an
        # objective it may not belong to, where it is later read back as if
        # it did. Ambiguity is reported, never resolved by preference.
        return InboundInterpretation(
            relevance=RELEVANCE_UNCERTAIN,
            reason="ambiguous_objective_match",
            summary=_summary_of(payload),
            candidate_objective_ids=tuple(
                int(o.id) for o in matched if getattr(o, "id", None) is not None
            ),
        )

    objective = matched[0]
    candidates = (int(objective.id),) if getattr(objective, "id", None) is not None else ()

    # The sentence-shaped checks below run per payload string rather than
    # over the joined corpus: joining two fields invents a sentence boundary
    # that neither of them has, and both hides real cues and manufactures
    # false ones.
    if any(_DIRECTIVE_PATTERN.search(leaf) for leaf in leaves):
        # An external party asking Bartholomew to act. It is not evidence of
        # anything having happened, and it is emphatically not authority: an
        # inbound sender cannot reach Bartholomew's capabilities by phrasing
        # a payload as an instruction.
        return InboundInterpretation(
            relevance=RELEVANCE_UNCERTAIN,
            reason="external_directive_not_evidence",
            summary=_summary_of(payload),
            candidate_objective_ids=candidates,
        )

    if any(_QUESTION_PATTERN.search(leaf) for leaf in leaves):
        return InboundInterpretation(
            relevance=RELEVANCE_UNCERTAIN,
            reason="question_not_assertion",
            summary=_summary_of(payload),
            candidate_objective_ids=candidates,
        )

    if any(_HEDGE_PATTERN.search(leaf) for leaf in leaves):
        # Hedged content is exactly the case CRITICAL INVARIANT 2 exists for:
        # "the roofer might come Tuesday" must never be read back later as
        # "the roofer is coming Tuesday".
        return InboundInterpretation(
            relevance=RELEVANCE_UNCERTAIN,
            reason="hedged_or_speculative",
            summary=_summary_of(payload),
            candidate_objective_ids=candidates,
        )

    summary = _summary_of(payload)
    if not summary:  # pragma: no cover - text is non-empty, so a leaf exists
        return InboundInterpretation(
            relevance=RELEVANCE_IRRELEVANT,
            reason="no_interpretable_content",
        )

    return InboundInterpretation(
        relevance=RELEVANCE_RELEVANT,
        reason="single_live_objective_match",
        summary=summary,
        objective_id=int(objective.id),
        objective_title=getattr(objective, "title", None),
        candidate_objective_ids=candidates,
    )


def build_provenance(stored: Any) -> dict[str, Any]:
    """The capture's own provenance, carried intact onto the objective event.

    Nothing is invented and nothing is dropped: the objective's history can
    name where the claim came from, what verified the source, exactly which
    content was accepted (`payload_sha256`), and which `inbound_events` row
    to go back to. `evidence: True` is the same flag the forecast slice
    established -- this is an external assertion, not an established fact,
    and `objective_intents` renders it accordingly.
    """
    return {
        "source_kind": SOURCE_KIND_INBOUND,
        "inbound_row_id": getattr(stored, "row_id", None),
        "source_id": getattr(stored, "source_id", None),
        "event_id": getattr(stored, "event_id", None),
        "event_type": getattr(stored, "event_type", None),
        "occurred_at": getattr(stored, "occurred_at", None),
        "received_at": getattr(stored, "received_at", None),
        "payload_sha256": getattr(stored, "payload_sha256", None),
        "verified_by": getattr(stored, "verified_by", None),
        "evidence": True,
    }


def already_attached(events: list[Any], source_id: str, event_id: str) -> bool:
    """Whether this captured event is already in an objective's history.

    The structural half of "a redelivery produces no second piece of
    evidence". Capture's UNIQUE constraint covers the capture call; this
    covers interpretation being run again over a row that already exists,
    which is the case a caller is most likely to reach by accident.
    """
    for event in events:
        provenance = getattr(event, "provenance", None)
        if not isinstance(provenance, dict):
            continue
        if (
            provenance.get("source_kind") == SOURCE_KIND_INBOUND
            and provenance.get("source_id") == source_id
            and provenance.get("event_id") == event_id
        ):
            return True
    return False


# ------------------------------------------------------------ the governed half


async def interpret_captured_event(
    ctx: Any,
    *,
    stored: Any,
    payload: Any,
) -> InboundInterpretationResult:
    """Interpret one already-captured event, and attach it if it is evidence.

    Called *after* `run_inbound_through_runtime_contract()` has returned, by
    a caller that has decided to ask what the event meant. Capture never
    calls this itself.

    `ctx` is the same duck-typed context the objective seam takes:
    `.objective_store` and `.mem.db_path`, with `.blocking_executor`,
    `.governance_store` and `.identity_context` consulted by that seam.

    Refuses, writing nothing, when:

      * the event was not durably captured (`outcome != "captured"`) -- there
        is no provenance-bearing row to attach to;
      * the event is a redelivery (`duplicate`), or its evidence is already in
        the objective's history;
      * the verdict is IRRELEVANT or UNCERTAIN.

    The only write it can perform is `record` with `event_kind="fact"`
    through `run_objective_through_runtime_contract()`, so the Parking Brake
    and Identity policy gates apply exactly as they do to every other
    objective mutation -- this module adds no gate of its own and, more
    importantly, bypasses none. A braked Bartholomew records nothing here and
    says so.
    """
    from bartholomew.kernel.blocking_executor import run_off_loop
    from bartholomew.kernel.inbound_store import OUTCOME_CAPTURED
    from bartholomew.kernel.runtime_contract import run_objective_through_runtime_contract

    if getattr(stored, "outcome", None) != OUTCOME_CAPTURED:
        return InboundInterpretationResult(
            interpretation=InboundInterpretation(
                relevance=RELEVANCE_IRRELEVANT,
                reason="event_was_not_captured",
            ),
            outcome="not_captured",
            reason="the event was not durably captured, so there is nothing to interpret",
        )

    if getattr(stored, "duplicate", False):
        # The capture seam already decided this is a redelivery rather than a
        # second logical event. Interpreting it again would turn one external
        # retry into two pieces of evidence.
        return InboundInterpretationResult(
            interpretation=InboundInterpretation(
                relevance=RELEVANCE_IRRELEVANT,
                reason="duplicate_capture",
            ),
            already_recorded=True,
            outcome="duplicate",
            reason="this event was already captured; no second evidence is recorded",
        )

    store = getattr(ctx, "objective_store", None)
    if store is None:
        return InboundInterpretationResult(
            interpretation=InboundInterpretation(
                relevance=RELEVANCE_IRRELEVANT,
                reason="no_objective_store",
            ),
            outcome="unavailable",
            reason="no objective store is wired in",
        )
    executor = getattr(ctx, "blocking_executor", None)

    # Reading what Bartholomew is carrying is inspection, which a halt must
    # not hide (DECISIONS.md, 2026-08-18 clause (b)). The gate is on the
    # write, below, and it is the existing one.
    live = await run_off_loop(store.list_live, executor=executor)
    interpretation = interpret(payload, live)

    if not interpretation.is_evidence:
        # IRRELEVANT and UNCERTAIN both end here, and both end identically as
        # far as durable state is concerned: nothing happened.
        return InboundInterpretationResult(
            interpretation=interpretation,
            outcome="no_action",
            reason=interpretation.reason,
        )

    objective_id = interpretation.objective_id
    source_id = getattr(stored, "source_id", None)
    event_id = getattr(stored, "event_id", None)

    existing = await run_off_loop(store.events, objective_id, executor=executor)
    if already_attached(existing, source_id, event_id):
        return InboundInterpretationResult(
            interpretation=interpretation,
            already_recorded=True,
            outcome="already_recorded",
            reason="this captured event is already in the objective's history",
        )

    provenance = build_provenance(stored)
    result = await run_objective_through_runtime_contract(
        ctx,
        "record",
        objective_id=objective_id,
        event_kind=objective_store.EVENT_FACT,
        summary=interpretation.summary,
        provenance=provenance,
        actor=f"{ACTOR_PREFIX}:{source_id}",
    )

    if not getattr(result, "governance_allowed", True):
        return InboundInterpretationResult(
            interpretation=interpretation,
            governance_allowed=False,
            outcome=getattr(result, "outcome", "governance_denied"),
            reason=getattr(result, "reason", None),
            provenance=provenance,
        )

    return InboundInterpretationResult(
        interpretation=interpretation,
        recorded=getattr(result, "event", None) is not None,
        governance_allowed=True,
        outcome=getattr(result, "outcome", "recorded"),
        reason=None,
        event=getattr(result, "event", None),
        provenance=provenance,
    )
