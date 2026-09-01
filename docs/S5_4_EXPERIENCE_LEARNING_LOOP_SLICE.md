# S5.4 slice note — experience → candidate learning → review → consolidation

> **Status: non-authoritative slice note.** `ROADMAP.md`, `DECISIONS.md`, `CONSTITUTION.md`,
> `COGNITIVE_RUNTIME.md` and `docs/TILT.md` remain the authorities and are **not** amended by this
> document. Nothing here reclassifies S5.4 as complete: this is one narrow vertical slice of the
> loop `COGNITIVE_RUNTIME.md` describes, deliberately smaller than that sub-stage's full scope, and
> the rest of S5.4 (including the `ReflectionGenerator`/`NarratorEngine` ownership work
> `docs/S5_4_REFLECTION_OWNERSHIP.md` covers) is untouched and still open.

## What this slice proves

One complete governed loop, end to end, through existing repository authorities:

```
recorded objective outcome (ObjectiveStore, terminal + resolution)
  → its evidence events (fact / decision / action; `proposal` structurally excluded)
  → one candidate procedural lesson  [inference, proposed, not knowledge]
  → human review
     ├── reject → nothing consolidated, then or ever
     └── accept → a `competency_heuristic` record via S5.2's governed training write
                   → retrieved and used by S5.3's existing chat reasoning seam
```

The last arrow is the point. `tests/test_experience_learning_loop.py::TestAcceptanceScenario::
test_an_accepted_lesson_is_retrieved_and_used_later` runs an ordinary chat turn, touching none of
the learning code, and asserts the accepted lesson reaches the actual prompt — with a non-vacuity
control alongside it that pins the same question's prompt as *unchanged* while the candidate is
merely proposed.

## The four things kept apart

`bartholomew/kernel/candidate_learning.py` exists to stop these collapsing into one row:

| | Where it lives |
|---|---|
| what happened | `SourceExperience.observations` — verbatim evidence-event summaries, plus their ids |
| what Bartholomew infers | `inferred_rule`, with `epistemic_status = "inference"` (validation refuses `"observation"`) |
| what is proposed | `review_state = "proposed"`, stored under the `candidate_lesson` kind |
| what has been accepted | `review_state = "accepted"` — the only state `to_competency_heuristic()` will act on |

## Why rejection is real

Consolidation happens in exactly one function (`_consolidate_accepted_lesson`), reachable only from
the accept branch, and `to_competency_heuristic()` raises for any state but `accepted`. Review
decisions are terminal, so a rejected candidate cannot be accepted afterwards.

The candidate row itself survives a rejection — as an audit record of a lesson considered and
declined — but it is stored under `candidate_lesson`, which is **not** a member of
`competency.COMPETENCY_KINDS`. `runtime_contract._retrieve_memory_context()` filters retrieval to
`COMPETENCY_KINDS + PERSONAL_FACT_KINDS`, so no candidate is reachable by reasoning in any state.
That is a structural guarantee, not a filter a caller could forget; the `recall_policy:
context_only` entry added to `bartholomew/config/memory_rules.yaml` is for governance legibility on
top of it, not instead of it.

## Existing authorities reused, not duplicated

- **Objectives** — `ObjectiveStore`, unmodified. `evidence_events()` is what excludes `proposal`
  rows, so a lesson can never be inferred from something only contemplated.
- **Memory** — `MemoryStore.upsert_memory()`, the sole write authority. No new table, no migration.
- **Competency model** — S5.1's `CompetencyHeuristic`/`CompetencyEnvelope`/`Provenance`/
  `Supervision`. An accepted lesson is an S5.1 record, not a sixth competency kind.
- **Governed write** — S5.2's `run_training_through_runtime_contract()`, called with one new
  keyword. No second ingestion path.
- **Reflection** — `ActionReflection` / `record_action_reflection()`, surface `"learning"`.
- **Reasoning** — S5.3's `_retrieve_memory_context()` / `select_relevant()`, **unchanged**. The
  slice deliberately required no edit there; if it had, the lesson would not really be landing in
  the same substrate.
- **Governance** — the fail-closed Parking Brake on the existing `training` scope, plus Identity
  policy per action kind, in `evaluate_learning_admission()` (same two-gate shape and same result
  type as `evaluate_objective_admission()`).
- **Consent** — `privacy_guard` unchanged: a candidate whose content trips the sensitivity scan is
  queued for review rather than stored, and the seam reports `not_stored` rather than claiming a
  proposal exists.

## The one lift, and how narrow it is

S5.2 reserved the `experience` and `system_observation` provenance source types from the training
seam, anticipating that "when S5.4's consolidation path is designed and approved it may
deliberately lift this." This slice lifts exactly `experience`, via
`TrainingSubmission.validate(allow_consolidation_source=True)` and the matching keyword on
`run_training_through_runtime_contract()`, defaulting to `False`.

`system_observation` stays reserved unconditionally. Every user-facing surface — the HTTP training
route and the CLI — leaves the flag at its default, pinned by
`TestBoundaries::test_only_the_consolidation_seam_lifts_the_reservation`. `ALLOWED_TRAINING_SOURCE_TYPES`
is not widened.

## Deliberately not built

Autonomous or scheduled learning (nothing here runs on a tick or a drive); automatic
consolidation of any kind, including the "low-impact, high-confidence" branch `COGNITIVE_RUNTIME.md`
describes — `CandidateLesson.requires_review` is an unconditional property so there is no branch to
fall through; any movement between the personal / potentially-generalisable / system
classifications (the classification is copied and never inferred); cross-instance or cross-user
transport; model retraining; external knowledge ingestion; generalisation across more than the one
experience a candidate names; and any review UI or API route.

## Known limitations

- **Single-experience only.** Confidence is a flat `SINGLE_EXPERIENCE_CONFIDENCE = 0.4` and does
  not rise with corroboration, because nothing here aggregates outcomes. A second objective
  teaching the same lesson produces a second, unrelated candidate.
- **Objectives are the only experience source.** Skill outcomes, chat corrections and awaiting-response
  resolutions are all plausible sources and none is wired in.
- **The default inferred rule is mechanical**, not model-composed. Callers supply better wording;
  `propose_from_objective()`'s fallback deliberately states only what one occasion licenses.
- **Review has no surface.** Accept/reject are seam calls; there is no route, CLI verb or inbox
  view. A human is required, but has to be handed the call.
- **Re-proposing supersedes by key** without recording the superseded candidate's own history the
  way S5.2's training supersession does.
