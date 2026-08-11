# S5.2 Design — Training and Knowledge Acquisition

> **Authority note:** this document is the design `ROADMAP.md`'s S5.2 sub-stage would be
> implemented against. It is subordinate to `ROADMAP.md` (Stage 5's canonical exit criteria) and to
> `CONSTITUTION.md`'s "Training vs. configuration" / "Shared memory and transferable learning" /
> "Personal learning vs. potentially generalisable and system-level learning" sections and
> `COGNITIVE_RUNTIME.md`'s "Competency, Training, and Learning" section — those are the canonical
> requirements this design implements. It builds directly on S5.1's delivered data model
> (`bartholomew/kernel/competency.py`, `docs/S5_1_COMPETENCY_ARCHITECTURE_DESIGN.md`).
>
> **Status: design and Decisions A–E APPROVED 2026-08-11. Implementation NOT yet authorised.**
> Per `ROADMAP.md`'s Stage 5 approval model and S5.1's own precedent, the sequence is: this design
> approved in principle *(done — 2026-08-11)* → a separate implementation proposal approved
> *(proposed in §13; awaiting sign-off)* → implementation. Decisions A–E were approved as
> recommended, with **two future-facing constraints recorded in §9.1** that bind how this design
> may be read later. S5.1's completion does not authorise S5.2, and approval of this document does
> not authorise S5.3 or S5.4.
>
> **Explicitly excluded from S5.2:** the FTS5 migration/self-healing residual recorded in
> `RISKS.md` (2026-08-11) stays open and separate. No fix for it is authorised as part of this
> sub-stage, and §10 records only the (nil) interaction, not a remedy.

## 1. What this closes

`ROADMAP.md`'s S5.2 row requires: *"Define and implement how training material (formal reference
material, direct instruction, demonstration, correction, supervised-work outcomes) enters shared
Memory with provenance and consent, per `CONSTITUTION.md`'s 'Training vs. configuration.'"* Its
exit criterion is: *"Training material can enter shared Memory with provenance and consent, through
the existing Observation → Interpretation → Memory path, not a separate ingestion runtime."*

S5.1 delivered the *shape* competency knowledge takes. It deliberately delivered no way to put
anything into that shape from outside a test fixture: `competency.py` is pure data with no
persistence, retrieval, or I/O, and the only competency records that exist anywhere today are inert
worked-example fixtures. **S5.2 is the write path** — the governed route by which a user's manual,
instruction, demonstration, or correction becomes a stored, provenance-carrying,
consent-respecting competency record.

## 2. Proposed ownership

No new owner. Per `COGNITIVE_RUNTIME.md`'s "Training as Memory input, not a separate pipeline,"
training material enters *as Observations through the existing Runtime Contract*, and lands in
**MemoryStore** — the same single write authority every other memory uses. The ingestion seam
belongs in `bartholomew/kernel/runtime_contract.py` alongside the five surfaces already there
(chat, skills, drives, sight/voice, awaiting-response), not in a module of its own that could
become a second ingestion runtime.

## 3. Grounding in current code

Confirmed by direct reading on 2026-08-11, not assumed:

- **The seam pattern exists and is uniform.** `runtime_contract.py` defines frozen
  `Observation(source, raw_content, observed_at)` / `Interpretation(observation, prompt)` /
  `CandidateAction(kind, interpretation)` and one `run_*_through_runtime_contract()` entry point per
  surface. Each constructs the triple, calls `is_blocked_fail_closed_off_loop(<scope>, ...)`, and
  writes a Reflection. Existing brake scopes in live use are `"skills"` (chat), `"scheduler"`
  (drives), `"sight"`, `"voice"` — scopes are free strings matched against engaged scopes plus
  `"global"` (`parking_brake.py:193`), so a new `"training"` scope needs no schema change.
- **`MemoryStore.upsert_memory()` already applies the whole governance chain** regardless of
  `kind`: `never_store`/`ask_before_store` rule evaluation → redaction → summarisation → FTS
  indexing → encryption → sensitive-content consent gate → embedding. Competency kinds get all of
  it for free; S5.2 needs no new governance path, and must not add one.
- **The consent inbox S5.2 needs already exists.** `pending_sensitive_writes` (built by the
  2026-08-03 consent-handler fix, extended by S1.2) queues both `ask_before_store` rule matches
  (`requires_consent`) and sensitive-content writes with no registered handler, surfaced through
  `tests/test_consent_api.py`'s endpoints and Stage 1's consent/approval inbox. Training material
  that trips a consent rule routes here — no parallel review queue is needed or wanted.
- **S5.1's model is ready to be written.** `competency.py` provides `Provenance` (with
  `PROVENANCE_SOURCE_TYPES` = `formal_material`, `user_instruction`, `demonstration`, `correction`,
  `experience`, `system_observation`), `CompetencyEnvelope` (classification, confidence,
  supervision, revision), the five record classes, each with `to_dict()`/`validate()`/
  `to_summary_text()`, and `key_for(competency_id, slug)`. `to_summary_text()` exists precisely to
  feed `upsert_memory(summary=...)`, the additive parameter S5.1 added.
- **Gaps that constrain this design (all pre-existing, none fixed here):**
  - **No document-reading capability exists.** The only skills in the repository are
    `calendar_draft`, `notify`, `tasks`. S5.1's worked example cites `documents_read` and
    `web_search` as `relevant_capabilities`, but neither skill exists — those strings are
    aspirational. **S5.2 therefore cannot ingest a PDF/manual from disk**; see §6.
  - **`memory_chunks`/`chunk_fts` (the bulk-text path) is still trigger-synchronised**
    (`fts_client.py:300–329`) — structurally the same architecture PR #40 removed from `memory_fts`
    because it corrupted the database, and its own comment still refers to "memory_fts's triggers
    above," which no longer exist. Routing large training documents through chunking would land
    S5.2 on that hazard. A reason to keep bulk-document ingestion out of scope, not a reason to fix
    it here.
  - **`memory_rules.yaml` tag-matching is unreachable** from `upsert_memory()` (S5.1 design §10,
    flagged-not-fixed): `memory_dict` never carries `tags`. S5.2 must express governance rules via
    `kind` and content patterns only, and must not be designed around tags.
  - **`memory_rules.yaml` has no entries for the five competency kinds** (S5.1 design §9,
    "recommended, not required," not done). §7.2 proposes closing this within S5.2, which is where
    `COGNITIVE_RUNTIME.md` says governance-rule treatment belongs.

## 4. The boundary this design must hold: S5.2 vs. S5.4

`CONSTITUTION.md`'s training list spans both sub-stages, so the split must be stated explicitly or
S5.2 will drift into S5.4's work:

| Origin | Examples | Owner |
|---|---|---|
| **User-originated** — the user deliberately supplies material | formal reference material, direct instruction, demonstration, correction, the user's approval/correction of supervised work | **S5.2 (this document)** |
| **Bartholomew-originated** — derived from its own experience | independent experience, observed outcomes, candidate learning produced at the Reflection stage, consolidation that strengthens/weakens/qualifies prior learning | **S5.4** |

The distinction is *who authored the claim*, not which record kind results. Both can produce a
`competency_evidence` record; only S5.2's arrives because a human said so. Concretely: a user
typing "you were wrong to recommend replacing that unit — check the warranty first" is S5.2
(`source_type="correction"`, `recorded_by="user"`). Bartholomew noticing across three jobs that its
own quote comparisons ran long is S5.4 (`source_type="experience"`, `recorded_by="reflection"`).
**S5.2 must not implement any path where Bartholomew writes competency records about its own
experience without a user act** — that is S5.4, and building it here would pre-empt an unapproved
sub-stage.

## 5. Proposed design

### 5.1 One new seam function

```python
async def run_training_through_runtime_contract(
    daemon: KernelDaemon,
    submission: TrainingSubmission,
) -> TrainingRuntimeResult:
```

in `runtime_contract.py`, following the existing surfaces exactly:

1. **Observation** — `source="training"`, `raw_content` = the submitted material,
   `observed_at` = now. This is what satisfies Exit Gate question #1 for the training surface.
2. **Interpretation** — the submission given structure: which competency it targets, which record
   shapes it proposes, folded through `_build_interpretation()`'s existing enrichment so training
   sees the same Experience Kernel state every other surface does.
3. **CandidateAction** — `kind="training_ingest"`. A *proposal to write*, not a write.
4. **Governance** — `is_blocked_fail_closed_off_loop("training", ...)`, fail-closed, before any
   write. A new scope value; no new mechanism (§9, Decision D). Engaging the brake — globally or on
   `training` — must stop Bartholomew being trained, which is a property worth having explicitly.
5. **Memory** — each proposed record validated (`record.validate()`), then written via
   `MemoryStore.upsert_memory(kind, key, json.dumps(record.to_dict()), ts, summary=record.to_summary_text())`.
   No new write path; the existing consent/redaction/encryption chain applies unchanged.
6. **Reflection** — one reflection per submission recording what was ingested, accepted, queued for
   consent, or rejected, matching every other surface's Reflection write.

### 5.2 The submission type

```python
@dataclass(frozen=True)
class TrainingSubmission:
    competency_id: str
    source_type: str          # restricted to the S5.2 subset -- see §5.4
    source_detail: str        # free text: "Bosch WAT28401AU manual, sec. 4"; "user said in chat"
    records: list[CompetencyRecordUnion]   # structured, caller-supplied -- see Decision A
    classification: str = "personal"       # default; see §7.3
    confidence: float | None = None
    supervision: Supervision | None = None
```

`records` carries already-structured S5.1 objects. The submission supplies the *material and its
provenance*; S5.2 supplies the *governed write*.

### 5.3 Provenance integrity — the one genuinely new safety rule

`CONSTITUTION.md` requires training never bypass provenance. S5.1's `Provenance` has four fields,
and a caller could otherwise assert all of them. This design proposes **splitting them by who may
set them**:

| Field | Set by | Rationale |
|---|---|---|
| `source_type` | caller | Only the caller knows whether this is a manual or a correction. Validated against the allowed subset (§5.4). |
| `detail` | caller | Free-text description of the material. |
| `recorded_by` | **the seam, never the caller** | Derived from the authenticated ingestion route. A caller must not be able to claim material came from `"user"` when it came from `"executive"`. |
| `recorded_at` | **the seam, never the caller** | Server clock at ingestion. Prevents backdated provenance. |

This is a constraint on the S5.2 write path, not a change to S5.1's data model — `competency.py`
stays untouched.

### 5.4 Source types S5.2 accepts

Of S5.1's six `PROVENANCE_SOURCE_TYPES`, S5.2 accepts exactly the four user-originated ones:
`formal_material`, `user_instruction`, `demonstration`, `correction`. It **rejects** `experience`
and `system_observation`, which are S5.4's to write (§4). Enforcing this in the seam is what keeps
the sub-stage boundary real rather than documentary.

**This restriction is a sub-stage boundary marker, not a permanent property of the seam.** When
S5.4's candidate-learning/consolidation path is designed and approved, it may deliberately lift the
restriction so Bartholomew-originated learning flows through this same governed write rather than a
second one — which is the outcome `COGNITIVE_RUNTIME.md`'s "no second Memory authority" rule wants.
What must never happen is the restriction being lifted *incidentally*, as a side effect of some
other change, rather than by an explicit S5.4 decision.

### 5.5 Layering: why this seam is extension-ready (per §9.1, Constraint 1)

The seam's input is **structured, provenance-bearing records — not keystrokes.** Nothing in it
assumes a human at a keyboard; it assumes records that validate and carry provenance. That single
property is what makes the layering below possible, and it is a requirement of this design, not an
incidental consequence:

| Layer | Responsibility | Status |
|---|---|---|
| **Layer 0 — Interpretation/extraction** | Turn prose, conversation, or a document into *candidate* structured records | **Not built. Out of S5.2.** Future conversational / model-assisted / document extraction lives here. |
| **Layer 1 — Governed write seam** (`run_training_through_runtime_contract`) | Validate, govern (brake + consent + redaction), persist, report per-record outcomes | **S5.2 builds this.** |

Any future Layer 0 feeds Layer 1; it never writes to Memory itself, and it never gets its own
governance path. Structured manual submission is therefore **the first client of this seam, not the
intended final user experience** — natural-language training remains an intended future capability
(§9.1).

**Forward-compatibility note (recorded, not resolved here).** S5.1's envelope has one
`recorded_by` field (`user` / `executive` / `reflection`), which conflates *who supplied the
material* with *who authored the structured claim*. For S5.2 these are always the same (the user
does both), so nothing is lost. Under a future Layer 0 they diverge: a model extracting a procedure
from a user-supplied manual means material-from-`user`, claim-from-`executive`. Whoever designs
extraction must resolve this — most likely by adding a field, which is a change to S5.1's approved
model and therefore its own decision. Recording it here so the ambiguity is a known, deliberate
open question rather than something discovered mid-implementation.

## 6. Scope: what "formal reference material" means in S5.2

`CONSTITUTION.md` lists "manuals, procedures, regulatory material" as training material, which
invites reading a PDF off disk. **That is out of scope for S5.2**, for two grounded reasons (§3):
no document-reading capability exists in the repository, and the bulk-text path
(`memory_chunks`/`chunk_fts`) still carries the trigger-based FTS architecture PR #40 removed from
`memory_fts` for corrupting the database.

S5.2's scope is therefore **text training material submitted directly** — pasted, typed, or posted
through the API — which fully satisfies the exit criterion ("training material can enter shared
Memory with provenance and consent"). Document ingestion (file upload, PDF/DOCX parsing, chunked
storage, citation back to a source document) is deferred as separate, later, separately-approved
work that should be sequenced *after* the `chunk_fts` trigger hazard is addressed. Recorded here so
the deferral is a decision, not an omission.

**When that work is designed it must also settle Constraint 2 (§9.1):** whether sufficient source
material is retained or otherwise recoverable so provenance can be **independently re-evaluated**,
not merely asserted through a citation. Decision E's citation-only answer is scoped to S5.2 and is
explicitly not the permanent position.

## 7. Provenance, consent, governance, privacy — how each is preserved

### 7.1 Consent
Training material takes the same route as any memory: `upsert_memory()` evaluates
`memory_rules.yaml`, and content matching `never_store` is refused while `ask_before_store`
(`requires_consent`) is queued into `pending_sensitive_writes` and surfaced in Stage 1's existing
consent/approval inbox. **No training-specific consent mechanism is proposed**; a user training
Bartholomew on something containing a password or bank detail hits exactly the gate that already
exists.

### 7.2 Governance-rule legibility (proposed, in scope)
Add explicit `memory_rules.yaml` entries for the five competency kinds — the item S5.1's design §9
recommended and left undone. `COGNITIVE_RUNTIME.md` states that defining these kinds' "governance-
rule treatment (`memory_rules.yaml`)" belongs to a separately authorised implementation pass, and
S5.2 — the pass that first writes them for real — is that pass. Without it, competency records rely
on unset/default `recall_policy` and are invisible to anyone auditing the rules file. Must use
`kind`/content matching, never tags (§3).

### 7.3 Classification defaults
`classification` defaults to `personal`, and the seam must never infer
`potentially_generalisable` on its own — only an explicit caller act sets it. Per S5.1 §5 and
`CONSTITUTION.md`, the value remains **informational metadata only**: no promotion, export,
transport, de-identification, or incorporation mechanism exists in this repository, and S5.2
introduces none. S5.1's structural test
(`tests/test_competency_no_auto_promotion.py`) must be extended to cover the new write path, so
"no promotion mechanism was introduced" stays an enforced property rather than a claim.

### 7.4 Audit
Governance decisions on training already land in `governance_audit` via the existing
`GovernanceStore` path. The per-submission Reflection (§5.1 step 6) provides the "what was
ingested" trail.

## 8. Non-goals

- **No separate training-ingestion runtime**, pipeline, daemon, or queue —
  `COGNITIVE_RUNTIME.md` forbids it explicitly.
- **No foundation-model fine-tuning.** Per `CONSTITUTION.md`, ordinary operational training updates
  Memory content, not weights.
- **No Executive integration.** `Planner.decide()` still returns `None`; retrieving and applying
  competencies is S5.3.
- **No candidate-learning or consolidation loop**, and no Bartholomew-originated competency writes
  (§4) — S5.4.
- **No generalisation/de-identification/cross-instance transport**, for any classification value.
- **No document/file ingestion** (§6).
- **No Estate Management production functionality** — Estate remains a worked example.
- **No fixes to the deferred tech-debt items** named in §3 (tags pass-through, `chunk_fts`
  triggers). They are recorded as constraints and dependencies only.

## 9. Decisions — APPROVED 2026-08-11

All five were approved as recommended. Recorded here as decided, not proposed.

| | Decision | Approved outcome |
|---|---|---|
| **A** | Who shapes raw material into structured records | **Structured-only ingestion.** The caller supplies already-shaped S5.1 records; S5.2 governs and persists them. Model-assisted extraction is **not** built here. Bound by Constraint 1 (§9.1). |
| **B** | What surfaces the training path | **API + CLI. No new UI in S5.2.** A training-authoring UI is a Stage 1-shaped product decision to be taken on its own merits. |
| **C** | Partial-submission semantics | **Per-record independence.** Each record is stored, queued for consent, or rejected on its own; `TrainingRuntimeResult` reports per-record outcomes. All-or-nothing would need a new write authority spanning Memory and the consent queue, which this sub-stage may not add. |
| **D** | Brake scope | **A dedicated `"training"` governance scope**, so training can be halted without halting chat or skills. Requires registering it in the Stage 1 governance API's `VALID_SCOPES` allowlist — see §10. |
| **E** | Retaining raw source material | **Citation-only for S5.2.** `Provenance.detail` carries the citation; no sixth `competency_source` kind is introduced. Bound by Constraint 2 (§9.1). |

### 9.1 Future-facing constraints on Decisions A and E (recorded 2026-08-11)

These two constraints were attached to the approval and bind how this design may be read later.
Neither expands S5.2's scope; both prevent a scope decision hardening into a permanent
architectural position.

**Constraint 1 — Decision A must not establish structured manual submission as the intended final
user experience.** S5.2's job is to provide the **governed canonical ingestion seam** that future
conversational, model-assisted, and document-extraction paths feed into. Natural-language training
remains an intended future capability of Bartholomew; extraction and interpretation are simply
outside this sub-stage. §5.5 records the layering this requires and the concrete property that
makes it real — the seam consumes *structured, provenance-bearing records*, never keystrokes, so
any future producer of such records can feed it without a second write path or a second governance
path. **Anything that would make this seam assume a human author is a violation of this
constraint**, not merely a stylistic preference.

**Constraint 2 — Decision E is an S5.2 scope decision, not a permanent rejection of retained or
verifiable source material.** When document ingestion is designed (§6), it must revisit whether
sufficient source material has to be retained, or otherwise recoverable, so that **provenance can
be independently re-evaluated** rather than merely asserted. This matters most for the
generalisation pipeline `CONSTITUTION.md` describes: that process is required to re-evaluate
privacy, provenance, and de-identifiability before anything could ever leave the instance, and it
cannot re-evaluate source text that was never kept. A citation alone is sufficient for S5.2's
scope and insufficient as a permanent answer.

## 10. Risks and dependencies

- **`chunk_fts` trigger hazard** (§3) — not triggered by S5.2 as scoped, but the reason §6's
  deferral exists. If Decision A or §6 is overridden, this becomes a live dependency.
- **FTS migration residual** (`RISKS.md`, 2026-08-11) — **explicitly out of S5.2's scope and
  remains open; no fix is authorised as part of this sub-stage.** The interaction is nil by
  construction, not by luck: competency records written by S5.2 go through the corrected
  single-writer path, and the residual concerns only content indexed *before* that migration.
  Recorded so the interaction is verified rather than assumed, and so this sub-stage is never
  mistaken for having addressed it.
- **Unreachable tag rules** (§3) — if governance for training content ever *needs* tag matching,
  that deferred gap becomes blocking. It does not for this design.
- **Brake scope registration is a real, three-place change** (Decision D). Verified 2026-08-11:
  `parking_brake.py:193` matches scopes as free strings, but the Stage 1 governance API holds a
  hardcoded allowlist — `VALID_SCOPES = frozenset({"global", "skills", "sight", "voice",
  "scheduler"})` in `bartholomew_api_bridge_v0_1/services/api/routes/governance.py:35` — and
  **rejects unknown scopes**. Registering `"training"` there (plus `cli.py`'s help text and
  `parking_brake.py`'s module docstring) is required, or the scope would be enforceable internally
  yet impossible to engage from the UI or API — a governance control that exists but cannot be
  operated. This corrects an earlier statement in this design that no registration was needed.
- **`upsert_memory()`'s `(kind, key)` upsert semantics are current-state-only** — re-training the
  same `competency_id.slug` overwrites rather than versions it. **Resolved for S5.2** (§13.4): the
  seam reads the existing record, increments `revision`, and records the supersession — including
  the superseded claim's provenance — in the per-submission Reflection. `reflections` is a
  separate, append-only table, so history exists there without inventing a new store or a second
  write authority. Whether superseded competency state should additionally be *retrievable* as
  memory (rather than only auditable via reflections) stays an S5.4 consolidation question.

## 11. Non-negotiable invariants

- **No implicit authority expansion.** Approving this document does not approve its implementation;
  approving implementation does not approve S5.3 or S5.4.
- **No second Memory authority.** Every training write goes through `MemoryStore.upsert_memory()`.
- **No second Governance path.** Training is gated by the existing fail-closed brake/policy check.
- **No second reasoning path.** S5.2 writes; it never decides. `Planner.decide()` is untouched.
- **No Bartholomew-originated competency writes** — `experience`/`system_observation` source types
  are rejected by the seam (§5.4).
- **Provenance is never fully caller-asserted** — `recorded_by`/`recorded_at` are seam-derived
  (§5.3).
- **`potentially_generalisable` remains inert** — no promotion, export, or transport mechanism is
  introduced, enforced by extending S5.1's structural test (§7.3).
- **No bypass of redaction/encryption/consent/audit**, for any training source type.
- **The seam consumes structured records, never keystrokes** (§5.5, Constraint 1). It must not
  acquire any structural assumption that a human authored the submission — that is what keeps it
  the canonical seam future conversational/model-assisted/document extraction feeds into, rather
  than a manual-entry path that would have to be replaced.
- **No fix to the FTS5 migration/self-healing residual** is made or implied by this sub-stage
  (§10). It stays open and separately tracked in `RISKS.md`.

## 12. Verify plan (once implementation is separately approved)

```bash
# The seam: Observation/Interpretation/CandidateAction constructed and genuinely driving the
# Governance decision (including a non-vacuity control, per the precedent in
# tests/test_voice_sight_runtime_contract_seam.py).
pytest -q tests/test_training_runtime_contract_seam.py

# Governance: brake engaged (scope "training" and "global") blocks ingestion, fail-closed.
pytest -q tests/test_training_governance.py

# Consent: ask_before_store training material lands in pending_sensitive_writes and is
# resolvable through the existing S1.2 inbox, not a parallel queue.
pytest -q tests/test_training_consent.py

# Provenance integrity: caller cannot set recorded_by/recorded_at; experience/
# system_observation source types are rejected (the S5.2/S5.4 boundary).
pytest -q tests/test_training_provenance_integrity.py

# Round-trip: submitted records read back as valid S5.1 objects, retrievable by kind/key
# prefix, ConsentGate-filtered as any other memory.
pytest -q tests/test_training_ingestion_round_trip.py

# Extended S5.1 invariant: still no promotion/export/transport mechanism, now including the
# new write path.
pytest -q tests/test_competency_no_auto_promotion.py
```

Exact file names and counts are implementation-time detail. The six verification *categories* are
the commitment.

---

## 13. Proposed implementation plan (PROPOSED 2026-08-11 — NOT AUTHORISED)

Written against Decisions A–E as approved (§9) and Constraints 1–2 (§9.1). **No code has been
written.** This section is the "separate implementation proposal" the status note requires; it
needs its own sign-off before any production file is touched.

### 13.1 Staging — governance controls land before the thing they govern

Three separately reviewable steps. The ordering is deliberate and is the one safety-relevant
sequencing choice in this plan:

| Step | Contents | Why here |
|---|---|---|
| **1 — Governance foundation** | Register the `"training"` brake scope (§10's three places); add `memory_rules.yaml` entries for the five competency kinds (§7.2) | The control that stops training must exist **before** the path that performs it. Landing the seam first would leave a window where training is enforceable only via `global`, and not engageable per-scope from the UI/API at all. |
| **2 — The governed write seam** | `bartholomew/kernel/training.py` + `run_training_through_runtime_contract()` in `runtime_contract.py` | The substance of S5.2. Kernel-only; no external surface yet, so it can be reviewed on its governance properties alone. |
| **3 — Surfaces** | API route + CLI command (Decision B), plus the Estate Management end-to-end demonstration | Exposure comes last, once the governed path underneath it is reviewed and tested. |

Each step is independently mergeable and independently testable. Approving one does not approve
the next.

### 13.2 Files

**New:**
- `bartholomew/kernel/training.py` — `TrainingSubmission`, `TrainingRecordOutcome`,
  `TrainingRuntimeResult`, `ALLOWED_TRAINING_SOURCE_TYPES` (the four of §5.4), submission
  validation, and provenance stamping (§5.3). **Pure data and validation: no persistence, no
  retrieval, no I/O** — the same discipline `competency.py` holds to, and enforced the same way, by
  a structural test asserting the module imports no database machinery.
- `bartholomew_api_bridge_v0_1/services/api/routes/training.py` — the submission endpoint (Step 3).
- Six test files per §12, plus the Estate demonstration test.

**Edited:**
- `bartholomew/kernel/runtime_contract.py` — the seam function, `_TRAINING_KIND = "training_ingest"`,
  brake scope `"training"`. Additive; no existing surface changes.
- `bartholomew/config/memory_rules.yaml` — explicit entries for the five competency kinds.
- `bartholomew_api_bridge_v0_1/services/api/routes/governance.py` — `VALID_SCOPES` gains
  `"training"`.
- `bartholomew/cli.py` — brake scope help text; new `train` command (Step 3).
- `bartholomew/orchestrator/safety/parking_brake.py` — module docstring's scope list.

**Not edited, deliberately:** `bartholomew/kernel/competency.py` (S5.1's approved model stands
unchanged), `bartholomew/kernel/memory_store.py` (no new write path — the seam calls the existing
`upsert_memory()`), `bartholomew/kernel/planner.py` (S5.3).

### 13.3 The seam, concretely

```
TrainingSubmission
  -> validate submission (competency_id, source_type in the S5.4 subset, records non-empty)
  -> Observation(source="training", raw_content=<submitted material>)
  -> Interpretation (via existing _build_interpretation enrichment)
  -> CandidateAction(kind="training_ingest")
  -> GOVERNANCE: is_blocked_fail_closed_off_loop("training", ...)   # fail-closed, before any write
  -> for each record (per-record independence, Decision C):
       validate() -> stamp provenance (recorded_by/recorded_at seam-derived, §5.3)
       -> revision handling (§13.4)
       -> MemoryStore.upsert_memory(kind, key, json, ts, summary=record.to_summary_text())
       -> outcome: stored | queued_for_consent | rejected(reason)
  -> Reflection: one per submission, recording per-record outcomes and any supersession
  -> TrainingRuntimeResult(per-record outcomes)
```

### 13.4 Resolved implementation details

- **Revision/supersession (§10).** Before writing a record whose `(kind, key)` already exists, the
  seam reads the current record, sets `revision = prior.revision + 1`, and records the supersession
  — including the superseded claim's provenance — in the per-submission Reflection. `reflections`
  is separate and append-only, so prior claims remain auditable without a new store, a new table,
  or a second write authority. The `memories` row itself remains current-state-only, unchanged.
- **Consent outcomes.** A record routed to `pending_sensitive_writes` reports
  `queued_for_consent`, not `stored`. The distinction must be visible in the result and in the CLI
  output, or a user will believe training landed when it is awaiting their approval.
- **Fail-closed ordering.** The brake check precedes all record processing, so a blocked brake
  results in zero writes and zero consent-queue entries.

### 13.5 Explicitly not included

No extraction or interpretation of prose (Constraint 1 — Layer 0, §5.5). No document/file
ingestion (§6). No UI (Decision B). No sixth record kind (Decision E). No Executive integration
(S5.3). No candidate-learning or consolidation loop, and no Bartholomew-originated competency
writes (S5.4). No Estate Management production functionality — the Step 3 demonstration trains the
worked example through the real seam and stops there. **No fix to the FTS5 migration/self-healing
residual** (§10) — it stays open in `RISKS.md`.

### 13.6 What would signal this plan is wrong

Recorded so the implementation can fail honestly rather than be forced through:
- If the seam cannot be written without assuming a human author, Constraint 1 is being violated and
  the design needs revisiting, not a workaround.
- If per-record independence (Decision C) turns out to require a transaction spanning Memory and
  the consent queue, that is a new write authority and must stop for re-approval.
- If `memory_rules.yaml` cannot express the needed governance for competency kinds without tag
  matching, the deferred tags gap (§3) has become blocking and must be raised, not worked around.
