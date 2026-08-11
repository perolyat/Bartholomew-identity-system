# S5.2 Design — Training and Knowledge Acquisition

> **Authority note:** this document is the design `ROADMAP.md`'s S5.2 sub-stage would be
> implemented against. It is subordinate to `ROADMAP.md` (Stage 5's canonical exit criteria) and to
> `CONSTITUTION.md`'s "Training vs. configuration" / "Shared memory and transferable learning" /
> "Personal learning vs. potentially generalisable and system-level learning" sections and
> `COGNITIVE_RUNTIME.md`'s "Competency, Training, and Learning" section — those are the canonical
> requirements this design implements. It builds directly on S5.1's delivered data model
> (`bartholomew/kernel/competency.py`, `docs/S5_1_COMPETENCY_ARCHITECTURE_DESIGN.md`).
>
> **Status: PROPOSED 2026-08-11. NOT APPROVED. No implementation is authorised by this document.**
> Per `ROADMAP.md`'s Stage 5 approval model and S5.1's own precedent, the sequence is: this design
> approved in principle → a separate implementation proposal (scope, files, decisions in §9)
> approved → implementation. S5.1's completion does not authorise S5.2, and approving this
> document would not authorise S5.3 or S5.4.

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

## 9. Decisions required before an implementation proposal

**Decision A — structured-only ingestion, or model-assisted extraction?**
*Recommendation: structured-only for S5.2.* The caller supplies already-shaped S5.1 records; S5.2
governs and persists them. Model-assisted extraction (paste a manual, have Bartholomew propose
`competency_procedure` records from it) is genuinely useful and probably wanted eventually, but it
concentrates the governance risk: a model inventing procedures *and their provenance* is precisely
what `CONSTITUTION.md`'s "training must never bypass provenance" guards against, and it needs a
review-before-consolidation step that is structurally S5.4's machinery. Recommend deferring it and
keeping the seam shaped so extraction can later feed the same governed write.

**Decision B — what surfaces the training path?**
*Recommendation: an API endpoint plus CLI, no new UI in S5.2.* Stage 1's governance shell is
complete and already owns consent/audit surfaces; a training authoring UI is a Stage 1-shaped
product decision that should be taken on its own merits, not smuggled in through a Stage 5
sub-stage.

**Decision C — partial-submission semantics.**
A submission of five records where one trips `ask_before_store` cannot be atomic without a new
transaction authority spanning the consent queue. *Recommendation: per-record independence* — each
record is stored, queued for consent, or rejected on its own, and `TrainingRuntimeResult` reports
per-record outcomes. All-or-nothing would require exactly the kind of new write authority this
sub-stage is forbidden to add.

**Decision D — brake scope name.** *Recommendation: a new `"training"` scope*, so training can be
halted without halting chat or skills. The alternative (reusing `"skills"`) would couple two
unrelated capabilities.

**Decision E — does raw source material need retaining?**
When a procedure is distilled from a manual, is the manual's text kept? *Recommendation: no new
kind in S5.2* — `Provenance.detail` carries the citation, which is enough to satisfy "with
provenance." Introducing a `competency_source` kind is a real data-model change and should be its
own decision if §6's document ingestion is ever built.

## 10. Risks and dependencies

- **`chunk_fts` trigger hazard** (§3) — not triggered by S5.2 as scoped, but the reason §6's
  deferral exists. If Decision A or §6 is overridden, this becomes a live dependency.
- **FTS migration residual** (`RISKS.md`, 2026-08-11) — competency records written by S5.2 are
  indexed through the corrected single-writer path, so new writes are unaffected; the residual
  concerns pre-migration content only. Noted so the interaction is checked, not assumed.
- **Unreachable tag rules** (§3) — if governance for training content ever *needs* tag matching,
  that deferred gap becomes blocking. It does not for this design.
- **`upsert_memory()`'s `(kind, key)` upsert semantics are current-state-only** — re-training the
  same `competency_id.slug` overwrites rather than versions it. S5.1's envelope carries `revision`,
  but nothing increments it automatically. Whether re-training should preserve history is an S5.4
  consolidation question; S5.2 should at minimum increment `revision` and not silently lose the
  prior claim's provenance. Flagged for the implementation proposal.

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
