# S5.1 Design — Competency Architecture (Generic Data/Contract Model)

> **Authority note:** this document is the design `ROADMAP.md`'s S5.1 sub-stage would be
> implemented against. It is subordinate to `ROADMAP.md` (Stage 5's canonical exit criteria) and
> to `CONSTITUTION.md`'s "One Developing Digital Individual: Competencies and Training" /
> "Personal learning vs. potentially generalisable and system-level learning" sections and
> `COGNITIVE_RUNTIME.md`'s "Competency, Training, and Learning" section, which are the canonical
> requirements this design implements.
>
> **Status:** proposed 2026-08-08. Revised same day per reviewer feedback: `competency_procedure`
> and `competency_heuristic` kept as separate kinds (not consolidated); the `classification` field's
> `potentially_generalisable` value made explicit as informational-only, never a promotion trigger.
> **Approved for implementation 2026-08-09**, in the same sequence this document's own process
> requires: this design approved in principle, then a separate implementation proposal (scope,
> files, and §4.1's `summary` parameter option) approved, then the revised invariant-test approach
> (§12) approved, before any production code was touched. **Implementation complete and merged
> 2026-08-10** (`e1277b7`, PR #40) as `bartholomew/kernel/competency.py`, with 43 tests across the
> five files §13 anticipated; `ROADMAP.md`'s S5.1 row and Stage 5 completion note record the
> delivered result. See §12 for the non-negotiable invariants it holds to. *(Status line corrected
> 2026-08-11; it previously read "under final correction/review as of 2026-08-10.")*

## 1. What this closes

`ROADMAP.md`'s S5.1 row requires: *"Define and implement the smallest generic competency
data/contract model — knowledge areas, procedures, relevant capabilities, experience/evidence,
proficiency/confidence, supervision requirements — as structured content in the existing shared
Memory substrate, with no new memory authority, Executive, or Governance path,"* additionally
carrying *"the personal / potentially-generalisable / system-level classification and provenance"*
`CONSTITUTION.md` requires. Today, none of this exists: `Planner.decide()`
(`bartholomew/kernel/planner.py`) is a stub returning `None` unconditionally, `SkillManifest`
(`bartholomew/kernel/skill_manifest.py`) models only executable tools with no competency concept,
and `MemoryStore`'s `memories` table stores only `fact`/`event`/`preference`/... — no competency
representation of any kind. `COGNITIVE_RUNTIME.md`'s "Memory semantics this implies" section states
the mandate for this design precisely: *"new, well-understood kinds of content... Defining the
exact `kind` values... and any additional structured fields they need is deliberately left to a
separately authorised implementation pass"* — this document is that pass, for the data model only.

## 2. Proposed ownership

Per `COGNITIVE_RUNTIME.md`'s ownership table (already amended for this): **Memory Substrate**,
same authority as every other kind of memory content — "competencies are a structured description
held in the same substrate, not a separate store." No new store class, no new table. Application
of competency data (retrieval, reasoning) remains **Kernel Executive**'s job — that is S5.3, not
this document. This document defines the data shape only.

## 3. Grounding in current code

Confirmed by direct reading, not assumed:

- `bartholomew/kernel/memory_store.py`'s `memories` table is `(id, kind, key, value, summary, ts)`
  with a **unique index on `(kind, key)`** — upsert semantics (`ON CONFLICT(kind,key) DO UPDATE`),
  current-state-only, no built-in history. `kind` is a free string governed entirely by
  `memory_rules.yaml`, not a schema enum.
- Every write goes through `MemoryStore.upsert_memory()`, which already applies (in this order)
  `never_store`/`ask_before_store` rule evaluation → redaction → summarisation → FTS indexing →
  encryption → sensitive-content consent gate → embedding, regardless of `kind`. New competency
  `kind` values get all of this for free.
- **Gap found, not fixed here:** `upsert_memory()`'s internal `memory_dict` never includes a
  `tags` key, so `memory_rules.yaml`'s tag-matching rules are unreachable from the kernel's
  primary write path today. See §10.
- `bartholomew/kernel/retrieval.py`'s `get_retriever()` (hybrid/vector/fts) already supports
  `RetrievalFilters(kinds=[...])`, `ConsentGate`-filtered — the retrieval machinery
  `COGNITIVE_RUNTIME.md` says competency retrieval should reuse already exists and needs no
  changes for this design.
- `bartholomew/kernel/runtime_contract.py`'s `_build_interpretation()` already establishes the
  pattern competency retrieval (S5.3) would extend: reading Experience Kernel state and folding it
  into the `prompt` string as labelled lines. `Observation`/`Interpretation`/`CandidateAction` are
  frozen dataclasses with no fields to add for this design pass.
- `bartholomew/kernel/reflection.py`'s `ActionReflection` writes to `MemoryStore.reflections` — a
  **separate, append-only** table from `memories`. This is where "candidate learning" (S5.4)
  belongs; `memories` is for consolidated, current, retrievable competency state. This document
  only defines the `memories`-side shape.
- Skills are addressed by `skill_id` string, and Governance keys off exactly that string
  (`SkillRegistry.get_manifest(skill_id)`, `evaluate_tool_policy(identity_context, skill_id)`). A
  competency's "relevant capabilities" only need to be a list of `skill_id` strings.

## 4. Proposed data model

**No new tables, no new columns.** Competency data is structured JSON in `memories.value`, with a
plain-text `memories.summary` for FTS/embedding readability, using **five new `kind` values**.
Every record shares a common envelope; each `kind` adds its own fields.

### 4.1 Shared envelope (all five kinds)

```jsonc
{
  "competency_id": "estate_management",
  "classification": "personal",          // "personal" | "potentially_generalisable" | "system"
                                          // see §5 -- this is informational metadata ONLY
  "provenance": {
    "source_type": "user_instruction",   // formal_material | user_instruction | demonstration
                                          // | correction | experience | system_observation
    "detail": "user said to always check warranty before recommending replacement",
    "recorded_by": "user",               // "user" | "executive" | "reflection"
    "recorded_at": "2026-08-08T12:00:00Z"
  },
  "confidence": 0.8,                     // 0.0-1.0, nullable (unknown/not yet evidenced)
  "supervision": {                       // optional; per-record override of competency-level default
    "requires_review": false,
    "reason": null
  },
  "revision": 1,
  "updated_at": "2026-08-08T12:00:00Z"
}
```

### 4.2 `kind = "competency"` — one record per competency (index/definition)

`key = "<competency_id>"`.

```jsonc
{
  ...envelope...,
  "name": "Residential Estate Management",
  "status": "learning",                  // "learning" | "active" | "dormant"
  "description": "Free-text summary of what this competency covers.",
  "relevant_capabilities": ["documents_read", "web_search", "calendar_draft", "notify"],
  "proficiency": {
    "overall": 0.3,
    "by_area": {"maintenance_triage": 0.5, "quote_comparison": 0.6, "warranty_claims": 0.1}
  },
  "known_gaps": ["never handled a warranty dispute", "no experience with HOA rules"]
  // ...envelope's own "supervision": {"requires_review": ..., "reason": ...} (§4.1) IS the
  // competency-level default that child records' "per-record override" language refers to --
  // there is no separate "default_requires_review" field; the implementation (competency.py)
  // has exactly one `supervision` per record, always shaped `{requires_review, reason}`.
}
```

### 4.3 `kind = "competency_knowledge"` — domain knowledge

`key = "<competency_id>.<slug>"`. Adds: `topic`, `content` (the knowledge itself; `summary` should
render this in plain text for FTS/embedding).

### 4.4 `kind = "competency_procedure"` — procedures/workflows

`key = "<competency_id>.<slug>"`. Adds: `name`, `steps` (ordered list of strings), `when_to_use`,
`capability_refs` (skill_ids this procedure specifically uses; may be a subset of the competency's
list). **Kept distinct from `competency_heuristic`** — a procedure is a repeatable method/process,
not a rule-of-thumb. Per reviewer direction: revisit consolidating the two only if real
implementation evidence (not speculation) shows the distinction adds no value.

### 4.5 `kind = "competency_heuristic"` — rules of thumb

`key = "<competency_id>.<slug>"`. Adds: `rule` (the heuristic itself, plain text), `conditions`
(when it applies), `counterexamples` (known cases where it doesn't hold).

### 4.6 `kind = "competency_evidence"` — experience, cases, outcomes, corrections

`key = "<competency_id>.<slug-or-id>"`. Adds: `situation`, `action_taken`, `outcome`,
`judgement_was_correct` (bool, nullable), `lesson` (free text). Covers "prior cases, successful
interventions, mistakes and corrections, observed outcomes" per `ROADMAP.md`'s own Estate
Management acceptance-test wording, which already groups them together.

### 4.7 Relationships, without a schema change

`competency_id` in the envelope, plus `key = "<competency_id>.<slug>"`, is the only linking
mechanism — no foreign key. `WHERE kind = ? AND key LIKE 'estate_management.%'` uses the existing
`(kind, key)` unique index for an efficient prefix scan. Satisfies `COGNITIVE_RUNTIME.md`'s
explicit constraint ("does not require a new memory architecture or a new schema") at the cost of
relationships being convention-enforced, not DB-enforced — flagged as an open watch-item at the
generalisation test (§11).

### 4.8 Retrieval

Unchanged machinery, new `kinds` values passed to `RetrievalFilters` for relevance search (e.g.
`kinds=["competency", "competency_knowledge", "competency_procedure", "competency_heuristic",
"competency_evidence"]`); a direct `kind + key LIKE` query for exact "all records for this
competency" lookups, which are not relevance searches.

## 5. Classification semantics — `potentially_generalisable` is not a promotion mechanism

**This section exists because it is the most safety-critical part of this design and must not be
ambiguous.**

`classification` is a **descriptive label recorded at write time**, nothing more. Concretely:

- `personal` — this record belongs to this individual/instance. The default for anything about the
  user's specific life (this house, this contractor, this person).
- `potentially_generalisable` — this record's author (user, Executive, or a future reflection
  process) judged that its *content*, if properly de-identified and validated, *might* be useful
  beyond this individual. **This classification is a candidacy marker only.** It does **not**:
  - automatically promote the record anywhere;
  - automatically export, transmit, or copy the record outside this instance;
  - automatically incorporate the record into any future Bartholomew training, competency
    definition, procedure, default, or product release;
  - imply any code in this design (or S5.1's implementation) reads this field to *do* anything
    beyond store and later display it back to the user or Executive.
- `system` — this record is an observation about Bartholomew's own behaviour, not the user's
  world.

The actual generalisation pipeline this field is a prerequisite for —
`COGNITIVE_RUNTIME.md`'s "Personal, generalisable, and system-level learning classification"
section's `Individual experience -> Reflection -> Candidate learning -> Classification -> Privacy
and provenance evaluation -> De-identification where genuinely possible -> Consent/Governance as
required -> Validation -> Generalised lesson -> possible incorporation` — **is not designed, not
scoped, and not built by this document or by S5.1.** No de-identification mechanism, no
cross-instance transport, no validation process, and no product-level incorporation path exist
anywhere in this repository, and none is authorised here. Per `CONSTITUTION.md`: *"Removing a
person's name alone does not make information non-personal"* — when that future pipeline is
eventually designed, it must independently re-evaluate genuine independence-from-the-individual,
de-identifiability, provenance, consent, sensitivity, re-identification risk, and Governance before
anything crosses the instance boundary. **This design's `classification` field marking something
`potentially_generalisable` never substitutes for that future evaluation** — it only means "worth
that future process looking at," recorded now so the marking isn't lost, not "cleared to leave."

Personal information must never leak through a future generalisation process on the strength of
this field alone. Anyone implementing that future process must treat every
`potentially_generalisable` record as **still fully personal and fully governed** until that
process's own (separately designed) privacy/provenance/consent checks pass.

## 6. Worked example (Residential Estate Management, paper only)

To sanity-check the model without writing code: a `competency` record (`estate_management`,
proficiency ~0.3, capabilities = documents_read/web_search/calendar_draft/notify); a
`competency_procedure` (`quote_comparison`: get ≥3 quotes, compare scope/price/warranty,
classification=`potentially_generalisable`); a `competency_heuristic`
(`check_warranty_before_replace`, classification=`potentially_generalisable`); a
`competency_evidence` (a specific 2026 repair by a named contractor, classification=`personal`).
Every example `CONSTITUTION.md` and `ROADMAP.md` themselves give is covered by the envelope + five
kinds, without any Estate-only field anywhere in the model.

## 7. Non-goals

- No `EstateExecutive`/`EstatePlanner`/`EstateMemory`/`EstateGovernance`/`EstateLLM` or equivalent.
- No second Memory authority — everything lives in `MemoryStore`.
- No Executive integration (competency *retrieval and application* is S5.3).
- No training-ingestion mechanism (S5.2).
- No candidate-learning/consolidation loop (S5.4) — `reflections` already has the shape to hold it
  later; not touched here.
- No cross-instance/cross-user transport of any kind, for any classification value (§5).
- No real Estate Management skills or actions — this is a paper worked example only.

## 8. Provenance, confidence/evidence, privacy, consent/governance, and supervision — how each is preserved

Requested explicitly for this design; stated plainly:

- **Provenance:** every record's envelope carries `provenance.source_type` /
  `provenance.detail` / `provenance.recorded_by` / `provenance.recorded_at` — no competency record
  can be written without stating where it came from.
- **Confidence/evidence:** `confidence` (0.0–1.0, nullable) on every record; `competency_evidence`
  is a first-class kind specifically for the experience/outcome trail that justifies (or
  undermines) that confidence over time.
- **Privacy classification:** `classification` on every record (§5), independent of, and more
  granular than, the competency-level default — a competency can be mostly `personal` while one
  procedure within it is individually `potentially_generalisable`.
- **Consent/Governance requirements:** every competency-kind write goes through the *same*
  `MemoryStore.upsert_memory()` path as any other memory — `never_store`, `ask_before_store`,
  content-pattern redaction, encryption, and the sensitive-content consent gate all apply
  unchanged, automatically, regardless of `kind`. No competency-specific bypass exists or is
  proposed.
- **Supervision requirements:** `supervision.requires_review` (+ `reason`) at competency level
  (`competency` record's own `supervision` field, as a default) and per-record as an override —
  matches `COGNITIVE_RUNTIME.md`'s "Where a competency's own recorded supervision requirements
  call for it... the `CandidateAction` must reflect that need for review" (an S5.3 consumer of
  this field, not built here).

## 9. Governance/privacy implementation notes (recommended, not required for correctness)

The existing content-pattern rules in `memory_rules.yaml` (password/bank/health/personal-data
regexes) already apply regardless of `kind`, so sensitive competency content is already caught.
**Recommended at implementation time, not required:** explicit `always_keep`-style entries for the
five new kinds, so competency records don't rely on default (unset) `recall_policy` and are legible
to a human auditing `memory_rules.yaml` directly.

## 10. Flagged, not fixed here

`upsert_memory()`'s `memory_dict` doesn't pass through `tags`, so `memory_rules.yaml`'s
tag-matching is currently unreachable from the kernel's primary write path (§3). Not required for
this design (the envelope's own fields plus `kind` differentiation cover everything needed), but a
small, real, adjacent gap. Not touched by this document or by S5.1 — a separate, explicitly-scoped
decision if it's ever wanted.

## 11. Open questions for the generalisation test (`ASSUMPTIONS.md` A6)

- Does the `key`-prefix convention hold up once a second, and a structurally different third,
  competency exist and might need to reference records in *each other's* namespace (e.g. a Travel
  competency citing an Estate Management heuristic)? Best guess: yes, via a `related_to` field
  naming another `competency_id` — but this is exactly what A6 says must be tested, not assumed.
- Does keeping `competency_heuristic` and `competency_procedure` separate continue to earn its
  keep once real content exists in both? Per reviewer direction, deferred to real implementation
  evidence, not decided speculatively here.

## 12. Non-negotiable invariants

- **No implicit authority expansion.** Approving this document does not approve its
  implementation; approving implementation does not approve S5.2/S5.3/S5.4.
- **No competency-specific decision authority.** Applying competency data to a decision remains
  the Kernel Executive's job (S5.3) — this document defines data only, never a second reasoning
  path.
- **No cross-instance or cross-user transport exists or is created by this design**, for any
  classification value.
- **`potentially_generalisable` never triggers automatic promotion, export, or incorporation** —
  inert until a future, separately-designed, separately-approved governed process exists (§5).
- **No new memory schema or table.** Competency data lives entirely in `memories.value`/`summary`
  via existing governance/retrieval machinery.
- **No bypass of redaction/encryption/consent/audit.** Every competency-kind write goes through
  the same `upsert_memory()` path as any other memory.

## 13. Verify plan (once implementation is separately approved)

```bash
# Round-trip: each of the five kinds writes/reads through upsert_memory() identically to
# existing kinds (redaction, FTS indexing, consent gating all apply unchanged).
pytest -q tests/test_competency_memory_shapes.py   # new, proposed name

# Retrieval-filter behaviour: kinds=[...] returns exactly the competency records expected,
# ConsentGate-filtered the same as any other retrieval.
pytest -q tests/test_competency_retrieval.py        # new, proposed name

# The worked Estate Management example (§6) actually populated in a test DB end-to-end.
pytest -q tests/test_competency_worked_example.py   # new, proposed name

# Invariant test: no code path reads classification == "potentially_generalisable" and
# performs any export/transport/promotion action (grep-based structural test, mirroring the
# AST-based "placeholder capability never invoked outside the governed seam" precedent in
# tests/test_voice_sight_runtime_contract_seam.py).
pytest -q tests/test_competency_no_auto_promotion.py  # new, proposed name
```

Exact test file names/counts are implementation-time detail, not a design commitment — the four
verification *categories* above are the commitment.
