# BARTHOLOMEW W03-D HANDOFF — Governed Memory & Learning Consolidation

> Written by **W03-D — Governed Memory & Learning Consolidation** at completion.
> Authoritative contract: `docs/waves/W03/W03_D_CONTRACT.md`.
> Read `docs/waves/W03/README.md` and `W03_MANIFEST.yaml` first.

| | |
|---|---|
| Session | **W03-D — Governed Memory & Learning Consolidation** |
| Immutable id | `W03-D` |
| Manifest branch name | `wave/w03-d-governed-memory-learning` |
| Branch actually pushed | `claude/w03-d-urzyeg` (see **Branch naming** below) |
| Pull request | `[W03-D] Governed Memory & Learning Consolidation` |
| Baseline | `main` @ `e96e6a6dfc3f71a68d44010c9954bb4e7a0c5195` (W03-PREP closeout SHA, confirmed present in ancestry) |
| Required CI tier | Integration |
| Status | see **Status** below |

## Branch naming — read this before integrating

The manifest prescribes `wave/w03-d-governed-memory-learning`, and
`tests/test_wave_manifest.py` enforces that pattern for every builder, so the
manifest entry is **unchanged**. The commits, however, were pushed to
`claude/w03-d-urzyeg`, the branch this session was assigned by its execution
harness — the same divergence W03-PREP itself has, which is why its manifest
entry records `claude/wave-3-prep-ci-6g1u86` (the manifest's branch-pattern
rule applies only to `kind: builder`/`integration`, so W03-PREP could record
its real branch and W03-D cannot).

An identical ref was also pushed to `wave/w03-d-governed-memory-learning` so
the contract-prescribed name resolves. **Both point at the same frozen SHA**,
recorded in **Status** below. W03-F should integrate that SHA, by either name.

## What this package changed, and why

The wave-two learning loop already enforced manual acceptance structurally:
candidate-bound approval (PR #83), a shadow-only policy, and the
`candidate_lesson` kind's exclusion from the retrieval allowlist. All of that
is untouched and still passes.

What was missing was governance on the **read** side. Before W03-D a
`memories` row said what it claimed and when it was written, and nothing else.
Nothing recorded where a claim came from, who asserted it, how confident
anybody was, when it stopped being true, or that it had been withdrawn; no
retrieval path checked authority, provenance, staleness or revocation;
recalled text was concatenated into the prompt verbatim, indistinguishable
from the user's own words; revocation was a hard delete that left no trace, so
the next capture could silently recreate exactly what a person had removed;
and `memory_rules.yaml`'s `auto_expire`/`expires_in` had been declared since
rules v1.0 and **read by no code path at all**.

That is tolerable while recalled memory only tints a sentence. It is not
tolerable once recalled memory reaches a decision that can move a mouse on
somebody's PC, which is what the Wave 3 loop does.

### The shape of the fix

| Concern | Where it lives |
|---|---|
| Provenance / confidence / validity window / supersession fields, and the single writer of them | `bartholomew/kernel/memory_store.py` |
| What an `expires_in` rule *means* | `bartholomew/kernel/memory_rules.py` |
| The retrieval-side validity verdict, and the instruction/data frame | `bartholomew/kernel/consent_gate.py` |
| Honouring the verdict (relevance logic untouched) | `retrieval.py`, `hybrid_retriever.py` |
| Framing recalled blocks in the prompt | `runtime_contract._build_interpretation()` |
| Seeing and lifting a revocation | `routes/memory.py` + `platform/route_policy.py` |

One governed write path. One retrieval governance point. No new authority.

## Acceptance criteria — results

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | Provenance & validity fields, written only through `upsert_memory`, surfaced in retrieved items | **Met** | `tests/test_w03d_memory_provenance.py` — fresh + migrated schema, the AST guard that no module outside `memory_store.py` writes the columns, `RetrievedItem.provenance` |
| 2 | Retrieval verdict honoured by all retrievers and the chat/executive path; AST bypass guard extended | **Met** | `test_w03d_memory_provenance.py::TestValidityVerdict`; `test_w03d_memory_poisoning.py::TestStaleAuthority`, `::TestChatSeamHonoursTheVerdict`; `tests/test_consent_bypass_redteam.py::TestNoGateBypassKnob` |
| 3 | Instruction/data boundary; stored imperative text changes no `CandidateAction` kind or actuation proposal | **Met** | `tests/test_w03d_instruction_data_boundary.py` (structural) + `test_w03d_memory_poisoning.py::TestEmbeddedInstructions` (behavioural, 4 payloads × 2 properties) |
| 4 | Supersession first-class; currently-valid wins; history preserved (`key@rN` extended to facts/preferences) | **Met** | `tests/test_w03d_supersession_and_tombstone.py::TestSupersessionScenarios`, `::TestAuditHistory` |
| 5 | Revocation tombstone; a revoked `(kind, key)` cannot be silently recreated | **Met** | `::TestRevocationTombstone` — re-learning, re-training and personal-fact capture vectors all refused |
| 6 | `auto_expire` / `expires_in` enforced fail-closed at read time | **Met** | `::TestExpiryArithmetic`, `::TestValidityVerdict::test_a_stale_observation_reads_expired` and the legacy-row variant |
| 7 | Poisoning resistance (embedded instructions, stale authority, cross-user/domain) | **Met** | `tests/test_w03d_memory_poisoning.py` (27 tests) |
| 8 | Permissive policy + allowlisted `learning_accept` still consolidates nothing | **Met** | `tests/test_w03d_manual_acceptance_authoritative.py::TestPermissivePolicyStillConsolidatesNothing` |

`W03_TEST_CONTRACTS.md` Sec.6's **mechanism** half (a correction supersedes and
changes a *later* task; an unapproved one changes nothing) is covered by
`test_w03d_manual_acceptance_authoritative.py::TestCorrectionChangesALaterTask`.
Its **scenario** half (Golden Path 4, live on a real desktop) remains W03-E's
and W03-F's.

## Behaviour changes downstream sessions must know about

1. **`forget_memory()` now lays a revocation tombstone.** Content is still
   erased — the Memory Agency promise is unchanged — but the *identity* is
   recorded as withdrawn and `upsert_memory()` refuses it afterwards with
   `outcome="refused_revoked"`. Any test or flow that deletes a `(kind, key)`
   and then re-writes the same one must call
   `reinstate_memory(kind, key, reinstated_by=...)` first. One existing
   fixture needed this (`tests/test_memory_agency.py::_seed_memory`); nothing
   else in 4,204 tests did.
   `delete_memory()`, the mechanical primitive, is unaffected and lays no
   tombstone, so internal maintenance paths keep working unchanged.

2. **`correct_memory()` now archives before it writes.** Same conditional-write
   semantics, same outcomes; it additionally leaves a `memory_revision` row at
   `"<kind>/<key>@rN"`. A store that has been corrected N times has N archive
   rows, which is worth knowing if you assert on row counts.

3. **`StoreResult.outcome` has a new value**, `refused_revoked`, alongside
   `stored` / `queued_for_consent` / `refused` / `precondition_failed`.
   `runtime_contract.FACT_OUTCOME_REVOKED` is its personal-fact-capture
   counterpart.

4. **`RetrievedItem` gained `verdict` and `provenance`.** Every item returned
   is `currently_valid`; the fields are carried so a consumer can state
   honestly what it applied and where it came from, not as something to
   re-filter on. Both are keyword-defaulted, so constructing a `RetrievedItem`
   positionally is unaffected.

5. **Recalled memory reaches the prompt inside a frame.** Anything asserting on
   the exact prompt string around competency guidance or personal facts will
   see `<<<RECALLED_MEMORY>>>` … `<<<END_RECALLED_MEMORY>>>` around them. A
   turn that recalled nothing is byte-for-byte unchanged.

## Notes for the concurrent builders

- **W03-A (observation events).** `MEMORY_SOURCE_TYPES` carries `observation`
  and `inference` as distinct values, so an observation event that becomes a
  memory does not lose the separation on the way in. Write observation-derived
  memories with `MemoryProvenance(source_type="observation", confidence=...)`
  and inferences with `source_type="inference"`; the retrieval verdict and the
  competency kinds are unchanged, so observed-vs-inferred kinds stay out of the
  retrieval competency kinds exactly as before.
  `config/memory_rules.yaml` already declares `environment_observation` with
  `expires_in: 7d`, and that declaration is now *enforced* — a Windows
  observation stops being recalled after its window. If W03-A wants a
  different window for a new observation kind, add an `auto_expire` rule; do
  not set `valid_to` past what a rule declares, because the write takes the
  earlier of the two.

- **W03-B (executive).** Retrieval is unchanged in shape. Consume
  `RetrievedItem.verdict`/`.provenance` if you want to explain what you
  applied. Do not re-filter on validity — the gate already did, and a second
  filter would be a second governance authority.

- **W03-C (actuation).** Nothing in W03-D reaches actuation. The poisoning
  suite asserts, from the chat seam, that no recalled text produces an action
  proposal; if the envelope's surface changes, that assertion
  (`test_w03d_memory_poisoning.py::test_stored_imperative_text_produces_no_actuation_proposal`)
  is the one to re-point.

- **W03-E (golden path).** The mechanism for "a correction changes a later
  task" is in place and tested; the scenario is yours. `GET
  /api/memory/revocations` and `POST /api/memory/{kind}/{key}/reinstate` exist
  if the operator surface wants to show what is withheld.

## Residual risks and integration notes

1. **The frame is a statement to a model, not a security boundary.** It makes
   the model's input honest about what is instruction and what is recalled
   data; it does not make the model obedient to that distinction. What
   actually prevents a stored imperative becoming an action is structural — a
   `CandidateAction`'s kind is chosen by the surface that built it, and W03-C's
   envelope is the only route to Windows. Both halves are tested; only the
   second is a guarantee. Stated plainly so nobody reads the frame as more
   than it is.

2. **Cross-user isolation is per-store and was not widened.** The verdict is
   computed only from the database the `ConsentGate` was constructed with; it
   never reads another user's rows to decide anything. This was an explicit
   escalation boundary in the contract and was not approached.

3. **Legacy rows fall back to rule-derived expiry, not to exclusion.** A
   database predating the W03-D columns has no recorded provenance. Excluding
   every memory on such a database would be an outage rather than a safety
   property, so the verdict falls back to the `auto_expire` rule plus any
   tombstone. Rows of kinds with no `auto_expire` rule and no recorded window
   therefore read `currently_valid` on an unmigrated store — which is what
   they were before W03-D. `init()` migrates on the next start.

4. **No backfill of provenance.** Existing rows keep NULL columns, meaning
   *not recorded*. Inventing an origin for them would be a fabricated
   provenance in exactly the material a verdict relies on. New writes from the
   personal-fact capture path carry provenance; other write sites (training,
   consolidation, adoption) still write NULL and can adopt `MemoryProvenance`
   incrementally without any change here.

5. **`memory_revision` rows accumulate.** One per correction, unbounded. They
   are small, unindexed for retrieval, and invisible to the retrieval seam, but
   there is no retention policy for them yet. Flagged for the deferral register
   rather than solved here — a retention rule is a governance decision, not a
   builder's.

6. **Two `route_policy.py` entries were added** for the new routes, because
   `tests/test_s8_route_policy_coverage.py` correctly refuses to let an
   unclassified route ship. That file is not in any W03 session's `owns` list;
   the change is two lines and is the required registration for routes W03-D
   owns.

7. **`runtime_contract.py` was touched** (six edits: the `consent_gate` import,
   the frame in `_build_interpretation`, provenance on the personal-fact write,
   and the `FACT_OUTCOME_REVOKED` outcome). It is owned by no W03 session and
   the contract's acceptance criteria 2 and 3 name the chat retrieval path and
   the prompt rendering explicitly, so the changes were unavoidable and were
   kept minimal. W03-B should expect them.

## Verification performed

Run in a clean Python 3.11 virtualenv against this branch's head, mirroring the
tier definitions in `.github/workflows/`:

| Check | Command | Result |
|---|---|---|
| PR Fast — default suite | `pytest -n auto --dist loadfile` (COLUMNS=200) | see PR/Status |
| Integration — integration/slow | `pytest -m "integration or slow"` | see PR/Status |
| Integration — coverage gate | `pytest -n auto --dist loadfile --cov=... --cov-fail-under=70` | see PR/Status |
| Lint / format | `ruff check` + `black` on every changed file | clean |
| Governance suites | consent-bypass red team, shadow policy, acceptance authorization, route policy, s8 governance | pass |

The two `test_declared_console_script_runs_help` failures seen when pytest is
invoked as `.venv/bin/python -m pytest` without the venv's `bin` on `PATH` are
the local artifact `W03_CI_BASELINE.md` §6 already records; they pass with
`PATH` set, and pass in CI.

## Status

- **W03-D status:** recorded in `W03_MANIFEST.yaml` (`sessions[].status`).
- **Frozen head SHA:** recorded in the pull request and in the manifest update
  commit message.
- **Freeze means freeze:** nothing is pushed to this branch after the head is
  declared frozen without telling W03-F.
