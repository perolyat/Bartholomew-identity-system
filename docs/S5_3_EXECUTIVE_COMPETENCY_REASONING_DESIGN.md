# S5.3 Design — Executive Competency Reasoning

> **Authority note:** this document is the design `ROADMAP.md`'s S5.3 sub-stage would be
> implemented against. It is subordinate to `ROADMAP.md` (Stage 5's canonical exit criteria),
> `CONSTITUTION.md`'s "One Developing Digital Individual" section, and `COGNITIVE_RUNTIME.md`'s
> "Competency, Training, and Learning" section — in particular its Executive row and its "Transfer
> boundaries" subsection, which are the canonical requirements this design implements. It builds on
> S5.1's delivered data model (`competency.py`) and S5.2's delivered write path
> (`training.py` + `run_training_through_runtime_contract()`).
>
> **Status: design and Decisions A–E APPROVED 2026-08-11. Implementation NOT yet authorised.**
> Per Stage 5's approval model and the S5.1/S5.2 precedent, the sequence is: this design approved
> in principle *(done — 2026-08-11)* → a separate implementation proposal approved *(proposed in
> §11; awaiting sign-off)* → implementation. S5.2's completion does not authorise S5.3, and
> approval of this document does not authorise S5.4.
>
> **Revision 2026-08-11 (same day), after review:** Decision E was split into E.1 (no *automatic*
> exposure — a standing position) and E.2 (explicit user-requested decision explanation —
> **preserved as a future capability and deliberately not foreclosed**), because the original
> "audit trail only" wording risked reading as a permanent prohibition and would then have
> contradicted `CONSTITUTION.md`, which lists **explainability** among the things Governance owns
> and requires the system to "be explainable." E.2 places a positive obligation on this sub-stage:
> the recorded context must be explanation-grade. New §6.1 also states explicitly that S5.3 is
> **not** the final deliberative Executive reasoning architecture.
>
> **Reconnaissance basis:** every claim in §3 and §4 was verified by direct reading of the merged
> repository at `d3b0753`, not inferred from planning documents.

## 1. What this closes

`ROADMAP.md`'s S5.3 row requires: *"Extend `Planner.decide()` (today a stub returning `None`) so
the Executive retrieves relevant competencies/knowledge/procedures for a given situation and
constructs a `CandidateAction` informed by them and their confidence — through the existing
Governance path, not a new one."* Its exit criterion: *"The Executive (`Planner.decide()` **or its
successor**) retrieves and applies relevant competencies when constructing a `CandidateAction`,
still gated by the existing, single Governance path."*

S5.1 gave competencies a shape. S5.2 gave them a governed way to exist. **Nothing reads them
back for reasoning.** Competency records are, today, inert data that only a retrieval test touches.

## 2. Reconnaissance: what the repository actually looks like

Verified at `d3b0753`:

| Fact | Evidence |
|---|---|
| `Planner.decide()` is a stub returning `None` unconditionally | `planner.py:25–29` |
| **Its only caller is the periodic tick loop** | `daemon.py:860` — `action = await self.planner.decide(self.state)` inside `_tick_loop`, every `self.interval` seconds |
| That call site publishes straight to the event bus | `daemon.py:861–862` — `if action: await self.bus.publish("system", action)` |
| **The bus path is ungoverned** | `_system_consumer` (`daemon.py:870–886`) calls `self.mem.create_nudge(...)` directly. No brake check, no Runtime Contract, no `evaluate_tool_policy` — verified by direct read of the whole consumer |
| **Nudges created that way are user-visible** | `GET /api/nudges/pending` (`app.py:406`), plus the minimal UI's nudge panel (`index.html:1123`) |
| `WorldState` carries almost nothing | `state_model.py` — `now`, `last_water_ts`, `user_activity` |
| `CandidateAction` is where surfaces model a proposal | `runtime_contract.py:108–113`, frozen dataclass `(kind, interpretation)` |
| `_build_interpretation()` is the existing enrichment point | `runtime_contract.py:129` — folds active goals, active persona, and Working Memory context into the prompt |
| Competency retrieval already works unmodified | `RetrievalFilters(kinds=[...])` + `ConsentGate`; proven by S5.1's `tests/test_competency_retrieval.py` |
| **Retrieval is synchronous** | `Retriever.retrieve()` / `HybridRetriever.retrieve()` are `def`, not `async def` (`retrieval.py:497`, `:551`) |
| Governance is a single function | `policy_engine.evaluate_tool_policy(context, tool_name) -> PolicyDecision` |

### 2.1 The finding that shapes this whole design

**Implementing S5.3 by the literal instruction — "make `Planner.decide()` return actions" — would
ship ungoverned proactive behaviour.**

`decide()` is called on a timer, and anything it returns is published to a bus whose consumer
writes a user-visible nudge with **no governance whatsoever**. Today that path is inert only
because `decide()` always returns `None`. The stub *is* the safety mechanism.

Making it return a `CandidateAction` would therefore, in one change:
- create unsolicited proactive suggestions (S5.5–S5.7 scope, gated behind a default-deny
  `allow_proactive` category that does not exist yet, and behind Stage 1's shell being proven);
- deliver them through a path that answers "no" to Exit Gate questions #2 and #3 (does every
  proposed action pass through the Executive / the same Governance path);
- do so *before* the initiative safety scaffolding (typed cadence, default-OFF consent, functional
  mute, quiet-hours deferral) that S5.5 exists to build.

This is not a reason to stop. It is the reason S5.3 must attach competency reasoning to the
**request-driven** Executive path and leave `decide()`'s tick-loop return value alone (§5,
Decision A).

## 3. Dependency findings

### 3.1 The S5.4 reflection-ownership gap — **does not block S5.3**

Assessed specifically, as instructed. **There are two distinct reflection mechanisms in this
codebase, and the gap is in only one of them:**

| Mechanism | Where | Status |
|---|---|---|
| **Per-action reflection** — `ActionReflection` / `record_action_reflection()`, `REFLECTION_KIND = "action_reflection"` | `reflection.py:49–100`; written by every Runtime Contract surface including S5.2's training seam | **No ownership dispute.** Single function, single sink. |
| **Daily/weekly narrative reflection** — `ReflectionGenerator` (LLM, from `identity_interpreter`) **plus** `narrator.generate_daily_reflection_narrative()`, string-concatenated | `daemon.py:948` and `:1059`; concatenation at `daemon.py:1036` (`content = f"{content}\n\n---\n\n{episodic_narrative}"`) | **This is the gap.** Two pipelines run independently; neither is the enforced authority. |

`COGNITIVE_RUNTIME.md`'s "Reflection ownership" section states the binding consequence precisely:
*"live proactive **reflection** behaviour (`ROADMAP.md` Stage 5) remains blocked until this gap is
closed."* The blocked thing is **live proactive reflection behaviour** — S5.7, and the S5.4
consolidation loop that reads reflection output as its input.

**S5.3 as designed here touches neither mechanism.** It reads competency records from Memory and
enriches a `CandidateAction`; it composes no reflections, changes no reflection authority, and
reads no reflection output. The per-action reflection the Runtime Contract already writes at the
end of a request is the *first* mechanism, which has no ownership dispute.

**Conclusion: S5.3 can be designed and implemented cleanly while leaving the gap for S5.4.** No
prerequisite decision or work is required before S5.3 — **provided** Decision A holds. If S5.3 were
instead scoped to make `decide()` drive proactive behaviour, the gap *would* become relevant,
because that is the "live proactive" behaviour the consequence names. Recorded as a stop condition
(§9).

**This design does not touch, fix, or propose fixing the reflection gap.** It remains S5.4's.

### 3.2 The four deferred issues — none blocks S5.3

Checked against repository evidence rather than assumed:

| Deferred issue | Blocks S5.3? |
|---|---|
| FTS5 migration residual | **No.** It concerns pre-migration index content. S5.3 reads competency records written by S5.2 through the corrected single-writer path. Worth one verification test that competency retrieval returns what was written (§8), not a fix. |
| `data/memory.db` test mutation | **No.** Test hygiene only; no runtime coupling. |
| Stale `chunk_fts` documentation | **No.** S5.3 uses `memory_fts`/vector retrieval over `memories`, not the chunk path. |
| `privacy_guard` vs `memory_rules.yaml` vocabulary disagreement | **No.** Both fail closed, and both sit on the *write* path. S5.3 is a read path, gated by `ConsentGate` — a third, separate mechanism. |

One genuine adjacency worth stating and **not** acting on: S5.1 design §10's `tags` pass-through
gap means competency retrieval cannot be filtered by tag. This design uses `kind` and key-prefix
filtering, which is sufficient, so the gap stays deferred.

## 4. Proposed ownership

No new owner. Per `COGNITIVE_RUNTIME.md`'s ownership table, applying competency data is the
**Kernel Executive**'s job. In this codebase the Executive is split across two places, which the
design must respect rather than unify:

- `Planner` — the class named Executive, today doing routing/validation (`handle_skill_request`)
  and nothing else;
- `runtime_contract.py`'s per-surface seams — where `Interpretation` and `CandidateAction` are
  actually constructed.

Competency **retrieval** belongs in Interpretation (per `COGNITIVE_RUNTIME.md`'s table row:
*"Interpretation is where relevant competency and knowledge retrieval happens"*). Competency
**application** belongs in `CandidateAction` construction. Both already exist; S5.3 enriches them.

## 5. Proposed design

### 5.1 A competency reasoning module

New `bartholomew/kernel/competency_reasoning.py` — pure logic, no I/O of its own, mirroring the
discipline `competency.py` and `training.py` hold to:

- `select_relevant(records, situation, *, limit, min_confidence) -> CompetencySelection` — ranks
  and filters already-retrieved records.
- `CompetencyContext` — the structured result: which competencies were consulted, their
  confidence, which procedures/heuristics/knowledge were selected, and the aggregate supervision
  requirement.

  **This structure must be explanation-grade** (Decision E.2): per applied record it carries the
  record's identity (`kind` + `key`), its provenance, its classification, and its confidence — not
  a flattened summary or a count. S5.3 exposes none of this to the user automatically, but a
  future "why did you recommend that?" capability can only be built on a record that kept this
  detail. Storing less would foreclose that capability by omission, since a decision cannot be
  reconstructed after the fact.
- `render_for_prompt(context) -> str` — the plain-text rendering folded into the Interpretation
  prompt, in the same labelled-lines style `_build_interpretation()` already uses.

Retrieval itself (the I/O) stays with the existing retrieval layer, called from the seam.

### 5.2 Retrieval, off the event loop

`Retriever.retrieve()` is synchronous. Per the B2/B8 discipline this repository already enforces,
competency retrieval must be routed through `run_off_loop()` with the daemon's
`blocking_executor` — not called directly from the async seam. Getting this wrong would put a
per-request blocking DB read on the event loop, which is exactly the class of defect Phase B spent
nine stages removing.

### 5.3 Where it attaches

```
Observation
  -> Interpretation           <-- competency retrieval + selection happens here
       (existing goals/persona/working-memory enrichment, PLUS a rendered
        competency context block)
  -> CandidateAction          <-- carries the CompetencyContext, including any
       supervision requirement, so Governance can be asked the right question
  -> Governance               <-- unchanged authority: brake + evaluate_tool_policy
  -> Capability / Execution   <-- unchanged
  -> Reflection               <-- unchanged mechanism; records which competencies
                                  were consulted, for auditability
```

`CandidateAction` gains one optional field (`competency_context`). It is a frozen dataclass; adding
an optional field is additive and breaks no existing construction site.

### 5.4 Supervision propagation

`COGNITIVE_RUNTIME.md` requires: *"Where a competency's own recorded supervision requirements call
for it… the `CandidateAction` must reflect that need for review; Governance still makes the actual
admission decision."*

S5.1's envelope already carries `supervision.requires_review` + `reason`. S5.3 propagates it: if
any applied record requires review, the `CandidateAction` says so. **Governance's authority is not
delegated** — the competency cannot approve anything, and a competency saying "no review needed"
never relaxes a gate. It can only ever *add* a review requirement, never remove one. This
asymmetry is a non-negotiable invariant (§7).

### 5.5 Transfer boundaries

`COGNITIVE_RUNTIME.md`'s "Transfer boundaries" section requires relevance, provenance, confidence,
privacy, Governance, and **domain boundaries** — with the explicit example that *"a plumbing-
contractor heuristic does not transfer to travel booking just because both involve comparing vendor
quotes."* Indiscriminate transfer is an explicit non-goal.

Concretely for S5.3: retrieval is `ConsentGate`-filtered (privacy), records below a confidence
floor are excluded (confidence), each applied record's provenance is carried into the context
(provenance), and **cross-competency application is off by default** (domain boundaries) — see
Decision C.

## 6. Scope: what "reasoning" means in S5.3

Deliberately narrow. S5.3 **retrieves, selects, renders, and propagates supervision**. It does
**not** introduce model-driven planning — the Executive does not gain an LLM call that invents
courses of action.

Rationale, in the same spirit as S5.2's Decision A: a model inventing a plan *and* citing
competencies as its justification concentrates exactly the risk the Constitution guards against,
and it would need a review-before-action loop that is structurally S5.4/S5.5 machinery. The
selection logic in S5.3 is deterministic and testable; making the Executive genuinely deliberative
is a much larger step that deserves its own approval.

### 6.1 S5.3 is not the final Executive reasoning architecture

**Stated explicitly so this sub-stage's narrowness is never mistaken for a completed design of
Bartholomew's judgement.** S5.3 delivers exactly three things: competency **retrieval**,
**selection**, and **supervision propagation**. It is the first step in which the Executive
consults learned competence at all — not the finished shape of how Bartholomew reasons.

Deliberately **not** decided, foreclosed, or implied by this sub-stage:

- **Model-driven planning and prospective reasoning** — the Executive weighing options, projecting
  consequences, or composing multi-step plans. `COGNITIVE_RUNTIME.md`'s Executive row anticipates
  richer construction ("combining retrieved knowledge/procedures/heuristics… current goals and
  context, and available capabilities into a proposed action"); S5.3 implements the *retrieval and
  supervision* half of that sentence and leaves the *combining* to later, separately-approved work.
- **Deliberation over competing competencies or conflicting heuristics** — S5.3 selects by
  relevance and confidence; it does not adjudicate between records that disagree.
- **Learning from whether an applied competency helped** — that is S5.4's experience loop.
- **Any change to where reasoning happens.** A future deliberative Executive may well need a
  different home than today's split between `Planner` and the seams (§4). S5.3 does not settle
  that question, and its narrowness must not be cited later as precedent that the split is correct.

The design constraint this places on the implementation: **the seam must remain a place richer
reasoning can be added, not a hardcoded lookup that a later architecture must tear out.** Selection
is a named, replaceable function; the context it produces is structured data, not a pre-rendered
string baked into a prompt.

## 7. Non-negotiable invariants

- **No new Governance path.** Brake + `evaluate_tool_policy` remain the sole admission authority.
- **Competency data never approves anything.** Supervision can only *add* a review requirement,
  never remove or relax one.
- **No second Memory authority and no new retrieval path** — the existing
  `ConsentGate`-filtered retrieval layer, unchanged.
- **No proactive behaviour.** S5.3 introduces no unsolicited output; `decide()`'s tick-loop return
  contract is unchanged (Decision A).
- **No blocking I/O on the event loop** — retrieval routed through `run_off_loop()`.
- **No writes.** S5.3 is a read path; it creates and modifies no competency records. Learning from
  outcomes is S5.4.
- **No reflection-ownership change** — the S5.4 gap is untouched (§3.1).
- **`classification` remains inert** — reasoning never branches on
  `potentially_generalisable` to promote, export, or transport anything. S5.1's structural test is
  extended to cover the reasoning module.
- **No automatic exposure of competency context** in ordinary responses (Decision E.1) — and,
  equally binding in the other direction, **the recorded context must stay explanation-grade**
  (Decision E.2) so a future user-requested decision-explanation capability is not foreclosed by
  omission. Both halves are testable (§8).
- **No chain-of-thought is produced or stored.** What is recorded is which stored, governed
  records were retrieved and applied — never a model reasoning trace.

## 8. Verify plan (once implementation is separately approved)

```bash
# Selection logic: relevance, confidence floor, domain scoping, ordering.
pytest -q tests/test_competency_reasoning_selection.py

# The seam: retrieved competencies genuinely reach the CandidateAction and the
# prompt -- with a non-vacuity control proving the enrichment is consumed, not
# constructed and discarded (the precedent this repo already sets).
pytest -q tests/test_competency_reasoning_seam.py

# Supervision propagation, including the asymmetry: a competency can add a
# review requirement but can never relax one.
pytest -q tests/test_competency_supervision_propagation.py

# Governance unchanged: brake engaged still denies; competency context never
# widens what Identity policy permits.
pytest -q tests/test_competency_reasoning_governance.py

# Consent: never-consented competency content is not retrievable into a
# CandidateAction (ConsentGate still applies on the read path).
pytest -q tests/test_competency_reasoning_consent.py

# Off-loop discipline: retrieval does not run on the event-loop thread
# (thread-identity spy, matching the B8 precedent).
pytest -q tests/test_competency_reasoning_off_loop.py

# Regression: decide() still returns None; no proactive nudge is produced.
pytest -q tests/test_planner_decide_remains_inert.py

# Decision E, both halves: no competency context leaks into an ordinary
# response (E.1), AND the recorded context is explanation-grade -- per-record
# identity, provenance, classification and confidence survive, so a future
# user-requested explanation capability is not foreclosed by omission (E.2).
pytest -q tests/test_competency_context_exposure_boundary.py

# Extended S5.1 invariant: still no promotion/export/transport mechanism.
pytest -q tests/test_competency_no_auto_promotion.py
```

## 9. Stop conditions

If any of these is hit, **stop and return for review** rather than working around it:

1. **Competency reasoning cannot be attached without changing `decide()`'s tick-loop return
   contract.** That would mean shipping proactive behaviour, which is S5.5–S5.7 and unauthorised —
   and at that point the reflection-ownership gap (§3.1) becomes a live blocker rather than a
   deferred one.
2. **Supervision propagation turns out to require Governance to consult competency data to decide.**
   That would be delegating admission authority to competency records, which §7 forbids.
3. **The selection logic cannot be made deterministic** without a model call — that is §6's
   boundary, and crossing it needs its own approval.
4. **Retrieval cannot be made `ConsentGate`-correct for competency kinds** — a privacy regression
   on the read path, which must be raised, not worked around.
5. **`CandidateAction` cannot carry competency context without changes rippling into unrelated
   surfaces** (voice/sight/awaiting-response), indicating the field belongs somewhere else.
6. **Explanation-grade recording (Decision E.2) turns out to conflict with consent or redaction** —
   e.g. retaining per-record provenance in the reflection would persist content the consent gate
   excluded. That is a genuine privacy/explainability tension and must be raised for decision, not
   resolved unilaterally in either direction.

## 10. Decisions required before an implementation proposal

**Decision A — where competency reasoning attaches.** *Recommendation: the request-driven path
(Interpretation + `CandidateAction`), leaving `Planner.decide()` returning `None`.* This satisfies
the exit criterion as written ("`Planner.decide()` **or its successor**") without shipping the
ungoverned proactivity §2.1 documents. The alternative — implementing `decide()` for the tick loop
— requires first bringing the tick→bus→nudge path through the Runtime Contract, which is S5.5–S5.7
work and is not authorised. *(A third option, implementing `decide()` but logging instead of
publishing, is dry-run proactive reasoning, i.e. S5.6 — also not authorised.)*

**Decision B — which surfaces get competency reasoning in S5.3.** *Recommendation: chat only.*
Chat is the surface where a user actually asks something a competency could inform. Skill execution
already has its own governed path and no natural "what should I do here" moment; drives are
proactive. Starting with one surface keeps the blast radius small and the non-vacuity test
meaningful. Extending to other surfaces later is additive.

**Decision C — cross-competency transfer.** *Recommendation: off by default in S5.3 — retrieve
within the competencies relevant to the situation, not across all of them.* `CONSTITUTION.md`
explicitly wants transfer to be possible eventually (the contractor-quote example), but
`COGNITIVE_RUNTIME.md` equally explicitly forbids it being indiscriminate, and the machinery for
judging domain-appropriateness does not exist. Recommend recording transfer as a deliberate S5.3
non-goal with the boundary stated, rather than implementing a weak version of it.

**Decision D — what happens when confidence is low or no competency matches.** *Recommendation:
the Executive proposes nothing extra and the request proceeds exactly as it does today.*
`COGNITIVE_RUNTIME.md` allows the Executive to construct "an explicit 'I'm not confident enough
here'", but making Bartholomew *say* that is user-visible behaviour change on the chat surface.
Recommend S5.3 stays silent-when-unsure, and that surfacing uncertainty to the user be its own
decision.

**Decision E — exposure of competency context.** *(Revised 2026-08-11 after review. The earlier
wording — "not user-visible… audit trail only" — was too blunt and risked reading as a permanent
prohibition on user-facing decision explanation. It is replaced by the two-part boundary below,
because a flat prohibition would contradict `CONSTITUTION.md`, which lists **explainability** among
the things Governance owns and requires the system to "**be explainable**".)*

*Recommendation: separate **automatic exposure** from **explicit user-requested explanation**, and
decide them differently.*

**E.1 — Automatic exposure: NO, and not merely "not yet."** Competency context must not be
appended to, injected into, or narrated within ordinary responses. Bartholomew does not volunteer
"I consulted these three competencies" alongside an answer the user simply asked for. This is a
standing design position, not a sequencing artefact: unsolicited internal detail is noise, it
degrades the response, and it invites users to treat retrieved records as authority. **S5.3
implements no automatic exposure.**

**E.2 — Explicit user-requested decision explanation: PRESERVED as a future capability, not built
in S5.3, and explicitly not foreclosed.** A user asking "why did you recommend that?" and getting
a concise account of the knowledge, procedure, or heuristic relied upon — with its provenance and
confidence — is a legitimate and constitutionally-supported capability. Two things make it
tractable and safe, and both are properties of *this* design:

- **What S5.3 records is not chain-of-thought.** It is a structured list of *which stored, governed
  competency records were retrieved and applied*, each already carrying provenance
  (`source_type`/`detail`/`recorded_by`/`recorded_at`), classification, and confidence from S5.1's
  envelope. That is factual, inspectable, user-owned data — categorically different from a model's
  internal reasoning trace, which this design neither produces nor stores.
- **The repository already does this shape of thing.** Stage 1's S1.5 shipped a user-facing
  governance audit/provenance view ("who/what approved a given action and when"). Decision
  provenance for competency-informed responses is the same principle extended to a new subsystem,
  not a new category of disclosure.

**The obligation E.2 places on S5.3 — this is the actionable part.** The competency context S5.3
records must be **explanation-grade**: structured, per-record, and carrying each applied record's
identity, provenance, and confidence, rather than a flattened summary string or a bare count. If
S5.3 recorded only "3 competencies used," a future explanation feature would have nothing to render
and would have to re-derive the decision — which is impossible after the fact. **Recording it
poorly would foreclose E.2 by omission**, which is exactly what this revision exists to prevent.

Designing the explanation surface itself — how it is asked for, how it reads, how much it says,
and how it respects consent/redaction on the records it cites — remains separate, later,
Stage 1-shaped work requiring its own approval. S5.3 makes it *possible*, and does not make it.

---

## 11. Proposed implementation plan (PROPOSED 2026-08-11 — NOT AUTHORISED)

Written against Decisions A–E as approved. **No code has been written.** This section is the
"separate implementation proposal" the status note requires and needs its own sign-off.

### 11.1 Staging — core, then wiring, then boundary verification

| Step | Contents | Why here |
|---|---|---|
| **1 — Reasoning core** | `bartholomew/kernel/competency_reasoning.py`: selection, `CompetencyContext`, prompt rendering. Pure data/logic, no I/O, not yet wired to anything | Fully testable in isolation, and reviewable on its selection semantics alone before it can affect a single live request |
| **2 — Seam integration (chat only)** | Off-loop retrieval in `run_chat_through_runtime_contract`, optional `_build_interpretation` parameter, `CandidateAction.competency_context`, supervision propagation, explanation-grade recording in the Reflection | The substance. Where the governance, consent, and off-loop tests live |
| **3 — Boundary verification + demonstration** | E.1/E.2 exposure-boundary tests, `decide()`-inertness regression, and an Estate Management end-to-end demonstration: a competency trained via S5.2 actually informs a chat turn | Proves the sub-stage's claims end-to-end, and pins the two boundaries most likely to erode later |

**Note on ordering, since it differs from S5.2 deliberately.** S5.2 put governance first because it
introduced a *write* path that needed a brake before it could act. S5.3 introduces **no new
capability, no new governance surface, and no new brake scope** — chat's existing `"skills"` scope
(`runtime_contract.py:247`) already gates this path, and a read that changes nothing needs no new
control. So the natural order here is core → wiring → verification. If implementation reveals that
S5.3 *does* need its own scope, that is a signal the sub-stage has grown beyond a read path and
should stop for review.

### 11.2 Files

**New:**
- `bartholomew/kernel/competency_reasoning.py` — pure logic; a structural test asserts it imports
  no I/O machinery, matching the discipline `competency.py` and `training.py` hold to.
- The eight test files named in §8.

**Edited — `bartholomew/kernel/runtime_contract.py` only:**
- `CandidateAction` gains `competency_context: CompetencyContext | None = None`. It is a frozen
  dataclass; an optional field with a default is additive and changes no existing construction
  site (chat, skills, drives, sight, voice, awaiting-response all keep working unchanged).
- `_build_interpretation()` gains an optional pre-rendered competency block parameter (§11.3).
- `run_chat_through_runtime_contract()` performs the retrieval and records the context.

**Not edited, deliberately:**
- **`bartholomew/kernel/planner.py`** — Decision A. `decide()` keeps returning `None`, and the
  tick→bus→nudge path (§2.1) is untouched. The roadmap row names `Planner.decide()`, so this is
  worth stating plainly: the exit criterion's "**or its successor**" is what makes this correct,
  and a regression test pins the inertness.
- `competency.py` — S5.1's model needs no change to be read.
- `memory_store.py`, `retrieval.py`, `consent_gate.py` — the existing read path is used as-is.
- `daemon.py` — no lifecycle change.

### 11.3 Concrete mechanics (each grounded in a verified repository fact)

1. **Retrieval must be awaited, but `_build_interpretation()` is synchronous**
   (`runtime_contract.py:129`, a plain `def`). Rather than make it async — which would touch every
   surface that calls it — the seam retrieves *first* (awaited, off-loop) and passes the rendered
   block in as an optional argument. The sync function stays sync; the `await` stays in the async
   caller.
2. **Off the event loop.** `Retriever.retrieve()`/`HybridRetriever.retrieve()` are synchronous
   (`retrieval.py:497`, `:551`), so retrieval goes through
   `run_off_loop(..., executor=getattr(daemon, "blocking_executor", None))` — the same
   `getattr` fallback the drive seam already uses for duck-typed contexts.
3. **Failure isolation.** Competency retrieval must never break a chat turn. `_build_interpretation`
   already establishes this pattern — each enrichment is individually try/except'd and falls back
   to the raw observation. Competency enrichment follows it exactly: on any failure, log and
   proceed with no competency context, never raise.
4. **Explanation-grade recording** (Decision E.2) extends chat's existing reflection `details`
   dict (`runtime_contract.py:295–301`), which today carries `{"response_preview": ...}`. The
   context is added there — per-record `kind`, `key`, provenance, classification, confidence — so
   it lands in the same single shared reflections sink every surface already writes to. No new
   store, no new table.
5. **Supervision propagation is additive-only.** The aggregate `requires_review` is the OR of the
   applied records'. There is no code path by which a competency record can clear a review
   requirement or relax a gate (§7).

### 11.4 Implementation-time details, with proposed defaults

These are tuning choices, not governance decisions; recorded so they are chosen deliberately
rather than by accident:

- **Confidence floor.** Exclude records whose `confidence` is explicitly below a floor (proposed
  default **0.3**). `confidence is None` means *unknown*, not *low* — include, but rank below
  evidenced records. A floor of `None`/0 disables filtering.
- **Hard cap on records folded into the prompt** (proposed default **5**). This is not arbitrary:
  `RISKS.md` carries a standing operational risk entry for **prompt bloat / provider rate limits**,
  and competency retrieval is exactly the kind of feature that grows a prompt silently. The cap is
  enforced in selection, and a test asserts it holds when many records match.
- **Kinds retrieved.** `competency_knowledge`, `competency_procedure`, `competency_heuristic` for
  guidance; `competency_evidence` for prior cases; the `competency` index record for proficiency
  and the supervision default. All five, filtered by relevance — with Decision C's scoping.

### 11.5 Explicitly not included

No model-driven planning or prospective reasoning (§6.1). No deliberation between conflicting
competencies. No cross-competency transfer (Decision C). No automatic exposure in responses
(Decision E.1) and no explanation surface (Decision E.2 — preserved, not built). No proactive
behaviour, and no change to `decide()` (Decision A). No surfaces beyond chat (Decision B). No
writes, and no learning from outcomes (S5.4). **No fix to the reflection-ownership gap** (§3.1) or
to any of the four deferred issues (§3.2).

### 11.6 What would signal this plan is wrong

§9's six stop conditions apply in full. Two are most likely to fire during implementation:

- If competency context cannot be carried on `CandidateAction` without rippling into unrelated
  surfaces (§9.5), the field belongs elsewhere and the design needs revisiting, not a workaround.
- If explanation-grade recording collides with consent or redaction (§9.6) — e.g. retaining
  per-record provenance would persist content the consent gate excluded — that is a real
  privacy/explainability tension to raise for decision, not to resolve unilaterally in either
  direction.
