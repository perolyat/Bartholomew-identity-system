# Inbound Event → Meaning — Slice Note (Session B)

**Status: IMPLEMENTED (this branch). Not merged.**

> A right-sized slice note per `docs/TILT.md` — not a design document and not a second
> authority on anything. Where this note and a canonical document disagree, the canonical
> document wins. Authority above this note: `CONSTITUTION.md`, `DECISIONS.md`,
> `COGNITIVE_RUNTIME.md`, `INTERFACES.md`, and — for everything upstream of capture —
> `docs/D_ALWAYS_ON_AND_INBOUND.md`.

## 0. The slice in one paragraph

Inbound capture stops, deliberately, at a provenance-bearing durable row: something arrived,
here is where from, and Bartholomew does not claim to believe it. That boundary is correct and
this slice does not move it. What it adds is the *next* step, invoked separately and later: an
already-captured event can be assessed for relevance to an objective Bartholomew is **already**
carrying, and — only when exactly one live objective plainly fits and the event actually asserts
something — attached to that objective's history as `fact` evidence, carrying the capture's own
provenance intact. Everything else produces no durable effect at all.

## 1. The progression

```
captured inbound event          (unchanged; run_inbound_through_runtime_contract)
  -> downstream interpretation  (inbound_interpretation.interpret — pure)
  -> relevance assessment       (irrelevant / relevant / uncertain)
  -> existing live objective    (ObjectiveStore.list_live only)
  -> candidate evidence         (event_kind="fact", provenance carried)
  -> objective history          (via run_objective_through_runtime_contract "record")
```

## 2. Three verdicts, and only one of them writes

| Verdict | When | Effect |
|---|---|---|
| `irrelevant` | no interpretable text, or no live objective matches | none |
| `relevant` | exactly one live objective matches **and** the content asserts something | one `objective_events` row, kind `fact` |
| `uncertain` | two plausible objectives; hedged/speculative wording; a question; or an external party asking Bartholomew to *do* something | none |

`uncertain` writes nothing — not even a `proposal`. Parking an unverified external assertion in
an objective's history under any kind is still putting it in the history, where a later reader
has no way to tell it from something that happened.

## 3. The invariants, and where each is held

**Capture stays domain-blind.** Nothing in the ingress changed. `event_type` remains an opaque
captured property and is deliberately excluded from the matching corpus entirely — otherwise a
sender could steer the match by naming its event after an objective. Payload text is drawn from
string leaves *wherever they sit*, by structure alone: no key is privileged, so no provider's
field naming is load-bearing and no `if email` / `if calendar` branch can appear.

**External content is evidence, never automatic truth.** Attachment is always
`event_kind="fact"` with `provenance.evidence = True` — the same posture the forecast slice
established — so `objective_intents` renders it as an outside claim rather than a settled one.
Hedged and interrogative content never reaches that path at all.

**No automatic action.** The only durable effect available from this module is one `record`
transition. No skill is invoked, nothing is scheduled, no message is sent, no objective is
created, and no lifecycle transition (complete/block/abandon) is reachable. An inbound payload
phrased as an instruction is classified `uncertain` and dropped: noticing that something matters
is not authority to act on it, and an external sender cannot reach Bartholomew's capabilities.

**Governance stays where it is.** The write goes through the existing objective seam, so the
Parking Brake (fail-closed, engaged-at-all) and the Identity policy gate on `objective_record`
apply unchanged. This slice adds no gate of its own and — the part that matters — bypasses none.
No new allowlist entry was needed. Reading the live objective set is not gated, because
inspection is what a halt must not hide (`DECISIONS.md`, 2026-08-18, clause (b)).

**Idempotency, at both layers.** Capture's `UNIQUE(source_id, event_id)` makes a redelivery a
duplicate rather than a second logical event, and a `duplicate` row is refused outright here.
Independently, the target objective's history is checked for an event already carrying this
`(source_id, event_id)` provenance — which is what makes re-running interpretation over the same
captured row safe, the case a caller is most likely to reach by accident.

## 4. Not wired into the ingress, on purpose

Nothing in `run_inbound_through_runtime_contract()`, the HTTP route or `inbound_store` calls this
module. A 202 still means "captured, explicitly not processed", and the capture boundary is
exactly where Session D left it. Interpretation is invoked by a caller that has decided to ask
what an event meant. Choosing *when* that happens — a drive, a turn, an operator action — is a
wiring decision for cross-stream integration, not for this slice.

## 5. Deliberately not done

* No queue, scheduler, or event-processing framework. One function, invoked by its caller.
* No provider adapters, and no provider-specific ingestion of any kind.
* No creation of objectives from unsolicited events, ever.
* No personal-memory extraction from inbound content. Nothing here writes Memory.
* No model-based extraction. The same reasoning `objective_intents` records: a model asked "is
  this relevant?" says yes far too readily, and the cost of a false positive is a durable false
  record, while the cost of a false negative is one missed piece of continuity.
* No notification behaviour, no experience-to-learning, no companion protocol.

## 6. Evidence

| Claim | Where |
|---|---|
| Full acceptance scenario: captured → provenance survives → interpreted downstream → matched → evidence in history → readable by later reasoning | `tests/test_inbound_interpretation.py::test_the_acceptance_scenario` |
| An unrelated event mutates nothing | `test_unrelated_event_does_not_mutate_the_objective` |
| Hedged / interrogative / imperative content never becomes fact, nor a proposal | `test_ambiguous_events_never_become_fact` |
| Two plausible objectives → no attachment to either | `test_two_plausible_objectives_produce_no_attachment` |
| No skill execution, no new objective, no lifecycle change, no memory/nudge | `test_an_event_cannot_cause_skill_execution_or_new_objectives` |
| Redelivery and interpretation replay both produce exactly one piece of evidence | `test_duplicate_capture_produces_no_second_evidence` |
| Parking Brake refuses the attachment and does not consume the event | `test_parking_brake_refuses_the_attachment` |
| Identity policy denial refuses the attachment | `test_identity_policy_denial_refuses_the_attachment` |
| A completed objective is structurally out of reach | `test_a_terminal_objective_is_out_of_reach` |
| `event_type` is never matched on; text found wherever it sits; bounds honoured | `TestPureInterpretation` |

## 7. Known limitations

* Matching is word-overlap against objective titles (`objective_intents.relates_to`), reused
  rather than reinvented. It is conservative and will miss paraphrases that share no title word
  ("the tiler is coming Tuesday" against "Get the roof repaired"). A miss costs one piece of
  continuity; a wrong attachment costs trust, and the two are not symmetric.
* The hedge/directive/question cues are English-language regexes. They are a floor on
  over-claiming, not a comprehension layer, and they will not catch every evasive phrasing.
* The evidence summary is the longest payload string, bounded. The full payload stays in
  `inbound_events`, which the provenance points back to.
