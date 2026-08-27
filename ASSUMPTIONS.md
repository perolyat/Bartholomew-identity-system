# ASSUMPTIONS

> Living list of uncertainties that matter. Each must have a validation plan.
>
> **Last updated:** 2026-08-15 (added A9 — that the current deployment serves exactly one personal
> Bartholomew identity, and that its single-user conveniences (one process, one SQLite database at
> one path, module-level singletons, unauthenticated local API, ownerless records) are stage-
> appropriate deployment choices rather than architectural commitments that would need rewriting
> to support many isolated personal identities. Recorded as a tracked assumption rather than an
> invisible one; deliberately unverified and not scheduled for verification. See
> `CONSTITUTION.md`'s "One Platform, Many Personal Bartholomews" section and `DECISIONS.md`'s
> corresponding entry.)
>
> **Previously (2026-08-08):** New Direction reconciliation: added A6 — whether a single generic
> competency data/contract model can serve structurally different competencies (Estate, Vehicle,
> Travel/Finance) without redesign or transfer-safety compromise is unverified until S5.1–S5.4 are
> implemented and the generalisation test in `ROADMAP.md`'s "Estate Management as architecture
> acceptance test" is actually run; A7 — ordinary training may not require fine-tuning. **Same-day
> follow-up:** added A8 — whether S5.1's personal/potentially-generalisable/system-level
> classification will actually suffice for a later, still-undesigned generalisation pipeline is
> itself unverified, per `DECISIONS.md`'s "Personal, potentially generalisable, and system-level
> learning are architecturally distinct" entry.)
>
> **Previously (2026-07-28):** documentation reconciliation pass 2: A4's scope corrected from
> "through Stage 2" — now complete — to "pending Phase B"; the cross-device token-auth assumption
> rewritten to reflect the hybrid local-first deployment decision, which explicitly rejects
> "simple token auth is sufficient" rather than merely leaving it unverified.)
>
> **Previously (2026-07-27):** A1 and A2 updated for the Phase A CI matrix; A3's "no red-team
> bypass-path test suite exists yet" corrected — one has existed since 2026-07-24, which
> `RISKS.md` R1 already recorded, so these two canonical docs had been contradicting each other.
> A5 and the two remaining brainstorm-era assumptions were not re-checked this pass and are left
> as last recorded.

## Format

- **ASSUMPTION:**
- **Why it matters:**
- **Risk if wrong:**
- **How to validate:**
- **Status:** unverified | verified | invalidated

---

## A1 — Linux CI is the health baseline
- **ASSUMPTION:** If core governance/memory/retrieval tests are green on Linux CI, we consider the system “healthy”; Windows-only failures are treated as infra noise unless proven otherwise.
- **Why it matters:** Prevents platform flakiness from blocking progress.
- **Risk if wrong:** Real bugs can hide behind quarantines.
- **How to validate:** Maintain a quarantine list with justification; periodically re-run quarantined tests on updated environments.
- **Status:** partially verified, with a caveat (2026-07-21) — the 2026-07-20 FTS5 investigation
  (MASTER_PLAN.md) confirmed the specific risk this assumption calls out (real bugs hiding
  behind a "Windows-only" label) did happen once and was caught by re-investigating on Linux,
  which is a point *for* the assumption once caught, but also evidence it can go
  quarantined-and-wrong for a while first. Separately, this session found several real,
  previously-unnoticed bugs (retrieval consent-enforcement, five `self_state` API 500s, an
  Experience Kernel restart-persistence bug) that a green Linux CI had never caught, because
  no test exercised those exact paths — "Linux CI green" was never false, but it also wasn't
  sufficient on its own to mean "no bugs," only "no bugs in what's tested." The quarantine-list
  mitigation itself still doesn't exist as a formal artifact.
- **Update (2026-07-27, post-Phase A):** the assumption is now **weaker by design, and that is
  the point**. Windows is no longer only a source of dismissable noise: `ci.yml`'s `windows` job
  runs on every pull request and passed its first run (PR #26, head `e923fb9`). Linux remains the
  primary baseline — the coverage gate and the full default suite run there — but a Windows-only
  failure is now a red check that must be diagnosed, not a label that can be applied without
  evidence. The quarantine list still does not exist, and Phase A deliberately chose *running*
  Windows over quarantining it (see `CI.md`'s "Quarantine Strategy").

## A1b — "Tests are green" implies the tested code path is bug-free
- **ASSUMPTION (added 2026-07-21, generalizing from this session's findings):** A passing test
  suite is sometimes silently read as "this subsystem works," when it can only ever mean "the
  paths the suite actually exercises work." `self_state.py`'s API router had a fully green CI
  for as long as it existed, while five of its routes always returned a 500 on first real use —
  nothing was green *and wrong*, there was just no test at all for those routes.
- **Why it matters:** Coverage gaps are invisible from a green CI dashboard; only visible by
  asking "what surface has zero tests," not "what tests are failing."
- **Risk if wrong (i.e., if this pattern recurs):** Live, user-facing breakage that CI never
  flags, discovered only by a real caller.
- **How to validate:** Periodically audit for API routes / public methods with zero direct
  test coverage (not just coverage-by-association via mocks, which don't catch signature
  drift) — this is exactly how item 11.10's five bugs were found.
- **Status:** verified (2026-07-21) — see MASTER_PLAN.md item 11.10.

## A2 — SQLite builds vary (FTS5/matchinfo/bm25)
- **ASSUMPTION:** Some dev environments will lack full FTS5/matchinfo support; the system must degrade gracefully.
- **Why it matters:** Retrieval correctness and stability depend on it.
- **Risk if wrong:** Retrieval works only on the developer’s machine.
- **How to validate:** CI matrix across at least two Python/SQLite variants; explicit fallback-path tests.
- **Status:** **partially validated (2026-07-27, corrected from "unverified").** The validation
  plan's first half now exists: `ci.yml` runs Python 3.10 and 3.11 on Ubuntu plus 3.11 on
  Windows, so at least three interpreter/SQLite-build combinations are exercised on every pull
  request. Its second half also exists: `tests/test_retrieval_fts5_fallback.py`,
  `tests/test_fts_schema_hygiene.py` and `tests/test_bm25_udf_fallback.py` cover fallback paths.
  What remains genuinely unverified is the assumption's *premise* — no environment in the current
  matrix actually **lacks** FTS5/matchinfo, so the graceful-degradation behaviour is exercised by
  its unit-level fallbacks rather than by a real feature-poor SQLite build. Do not read the green
  matrix as proof that a feature-poor build works.

## A3 — Consent gates at the storage/retrieval layer are sufficient defense-in-depth
- **ASSUMPTION:** Filtering at the lowest layer prevents meaningful privacy leakage even if upstream callers are sloppy.
- **Why it matters:** This is the primary privacy invariant.
- **Risk if wrong:** A bypass path leaks data.
- **How to validate:** Red-team tests that attempt retrieval bypass; audits for `apply_consent_gate=False` usage.
- **Status:** the "no leak" direction holds up (2026-07-21) — audited every `apply_consent_gate`
  call site by grep; no production caller ever overrides the default (see RISKS.md's R1 entry).
  But the *inverse* failure mode this assumption didn't name turned out to be real: the
  higher-level `Retriever`/`FTSOnlyRetriever`/`HybridRetriever` wrapper classes each ran their
  *own*, weaker rules-engine pass on top of the lowest layer's correct `ConsentGate` check,
  unconditionally excluding every `requires_consent` memory regardless of actual consent state
  — fail-closed, not a leak, but it means "defense-in-depth" in this codebase has so far meant
  "the upper layers can only make things *more* restrictive than the lowest layer, never less,"
  which held here by accident (a bug that happened to fail closed) rather than by a verified
  design guarantee.
- **Corrected 2026-07-27:** this entry ended "No red-team bypass-path test suite exists yet."
  That has been false since 2026-07-24 — `tests/test_consent_bypass_redteam.py` (10 tests,
  MASTER_PLAN.md item 11.20) exists and `RISKS.md` R1 already recorded it, so these two canonical
  docs were contradicting each other. The suite proves consented-but-should-be-excluded content is
  never surfaced through any production retrieval surface, including retrievers constructed
  without a rules engine, and it is non-vacuous: deliberately breaking
  `ConsentGate.filter_memory_ids()` makes exactly those tests fail and no others. It also
  converted the one-time `apply_consent_gate=False` grep audit into two permanent structural
  guards. **The assumption is therefore now supported by evidence rather than by accident** —
  though "defense-in-depth" in this codebase still means the upper layers can only be *more*
  restrictive than the lowest, never less. One documented, non-blocking residual:
  `get_retriever(mode="vector")` passes no `memory_store`, so that mode has one fewer redundant
  layer than `hybrid`/`fts` (see `RISKS.md` R1).

## A4 — Single SQLite DB remains viable under the current architecture, pending Phase B
- **ASSUMPTION:** SQLite will be sufficient for persistence, retrieval, and test workloads under the
  current mixed-ownership architecture, pending Phase B's persistence-ownership stabilisation.
- **Corrected 2026-07-28:** this assumption previously read "...through Stage 2." Stage 2 is now
  ✅ complete (per `MASTER_PLAN.md`/`ROADMAP.md`), so scoping the assumption to a completed stage
  made it read as already resolved when it isn't — the live open question has moved forward to
  Phase B (proposed, not approved), whose evidence base is the mixed `aiosqlite`/sync-`sqlite3`/
  scheduler-thread ownership of one file tracked in `RISKS.md`'s tech-debt watchlist.
- **Why it matters:** Architectural simplicity hinges on it; the deployment architecture
  (`DECISIONS.md`) also depends on the runtime's persistence layer being sound before any
  cross-device/sync work builds on top of it. (**Repointed 2026-08-17:** the deployment authority
  is now the server-centric entry, which supersedes "hybrid local-first". The dependency described
  here is unchanged — a persistence layer that is unsound locally does not become sound by being
  hosted.)
- **Risk if wrong:** Forced migration mid-stream.
- **How to validate:** Track perf budgets; measure WAL growth/lock contention; define migration triggers in DECISIONS.
- **Status:** unverified

## A5 — Encryption envelope format will stay stable or be versioned
- **ASSUMPTION:** Encrypted records will remain decryptable across upgrades.
- **Why it matters:** Data loss is existential.
- **Risk if wrong:** Irrecoverable memories.
- **How to validate:** Add versioned envelope header; migration tests; round-trip tests under key rotation scenarios.
- **Status:** unverified


## ASSUMPTION: Provider limits require chunked workflows
- **Statement:** Cline’s underlying model provider will enforce strict token-per-minute and context limits; large transcripts must be processed incrementally.
- **Why it matters:** Our planning and doc updates depend on reliably ingesting large brainstorm sources.
- **Risk if wrong:** If limits are higher than expected, we may over-engineer chunking; low risk.
- **How to validate:** Run a standard chunking pipeline on a 10MB transcript and confirm stable completion.
- **Status:** verified (observed rate-limit failure on mega-prompt)

## ASSUMPTION: Cross-device 'one mind' requires a reviewed threat model before any remote exposure — corrected 2026-07-28
- **Statement (superseded):** this entry previously assumed "a minimal token-based auth layer is
  sufficient for early cross-device experiments." **That assumption is explicitly rejected** by
  the deployment architecture decision recorded in `DECISIONS.md`: simple token authentication is
  **not** assumed sufficient. Remote/cross-device exposure of the Bartholomew runtime must not
  occur until authentication, authorization, transport security, and a reviewed threat model are
  designed and separately approved. (**Repointed 2026-08-17:** originally the 2026-07-28
  "hybrid local-first" entry, now superseded by the server-centric entry, which **carries this gate
  forward unchanged** — it is one of the clauses explicitly retained. Moving cognition server-side
  raises the stakes of this gate rather than relaxing it.)
- **Current statement:** no cross-device auth mechanism (token-based or otherwise) may be treated
  as sufficient until a threat model for it has been designed and reviewed. This is a gate, not a
  simplification to avoid "premature complex auth/SSO" — the previous framing had that backwards.
- **Why it matters:** Bartholomew's runtime holds sensitive memory, and governance authority
  (including the parking brake and emergency shutdown) must remain locally enforceable; an
  under-specified auth scheme for reaching it remotely is a direct path to compromising that
  authority, not a minor UX shortcut. (**Corrected 2026-08-17:** this previously read "the local
  runtime is the authority for sensitive memory", which was the clause the server-centric
  deployment decision superseded. Memory authority is intended to become server-side; **the
  locally-enforceable-stop requirement is not** — that clause was retained deliberately, and is
  what this assumption now turns on.)
- **Risk if wrong:** Security holes in the exact subsystem responsible for privacy, consent, and
  safety enforcement.
- **How to validate:** Threat model + penetration-style tests on auth endpoints, completed and
  reviewed *before* any remote-exposure feature ships — not retrofitted after.
- **Status:** unverified (and, per the correction above, must remain unverified-and-unshipped
  until the threat model exists — this is not merely an open question to track passively)

## A6 — A single generic competency model can serve structurally different competencies
- **ASSUMPTION (added 2026-08-08):** the generic competency data/contract model built in
  `ROADMAP.md` Stage 5's S5.1 can represent Residential Estate Management, a second competency
  (e.g. Vehicle Management), and a structurally different third (e.g. Travel or Finance) without
  requiring redesign of the core model, and without cross-competency learning transfer
  (`CONSTITUTION.md`'s "Shared memory and transferable learning") either leaking irrelevant/private
  evidence across domains or, in the opposite failure mode, being so conservatively scoped that no
  useful transfer ever actually happens.
- **Why it matters:** this is the explicit acceptance test `CONSTITUTION.md`'s "Domain
  Independence" section and `ROADMAP.md`'s "Estate Management as architecture acceptance test"
  both name: if the third competency requires redesigning the core model or introducing a second
  brain/memory/Executive, the competency abstraction itself has failed, not just one competency's
  implementation.
- **Risk if wrong:** either (a) a costly mid-stream redesign once a second or third competency is
  attempted, or (b) quiet architectural drift toward per-competency special-casing (the exact
  outcome `CONSTITUTION.md` prohibits) to avoid that redesign.
- **How to validate:** implement S5.1–S5.4 generically first (not Estate-specific), train
  Residential Estate Management into it, then deliberately add a second and a structurally
  different third competency per `ROADMAP.md`'s acceptance-test sequence, and check whether the
  core model held without exception-casing.
- **Status:** unverified — cannot be verified before S5.1 exists; this assumption exists so that
  whoever plans S5.1 treats "does this generalise" as a design question to be actively tested, not
  an incidental property to hope for.

## A7 — Ordinary operational training will not require foundation-model fine-tuning
- **ASSUMPTION (added 2026-08-08):** the training mechanisms described in `CONSTITUTION.md`'s
  "Training vs. configuration" section (formal material, instruction, demonstration, correction,
  supervised work, experience, consolidation) can be implemented entirely as structured Memory
  content plus Executive-time retrieval, without ever requiring fine-tuning or retraining the
  underlying language model, for the range of competencies currently anticipated (Estate, Vehicle,
  Travel, Finance).
- **Why it matters:** if this assumption is wrong for some future competency (i.e. some kind of
  professional judgement genuinely cannot be captured as retrievable structured knowledge and
  requires weight-level adaptation), that is a materially different, more expensive, and
  higher-risk engineering commitment than the one `CONSTITUTION.md` and `COGNITIVE_RUNTIME.md`
  currently describe, with its own governance and provenance questions this documentation pass has
  not addressed.
- **Risk if wrong:** a future competency's design could be blocked, or forced into a poor
  retrieval-only approximation of judgement that genuinely needed model-level adaptation.
- **How to validate:** monitor whether S5.1–S5.4's worked Estate Management example, and any
  subsequent competency, can achieve acceptable proficiency using only structured-knowledge
  retrieval; treat a genuine, well-evidenced counterexample as grounds to revisit this assumption
  explicitly in a new `DECISIONS.md` entry rather than silently reaching for fine-tuning.
- **Status:** unverified

## A8 — S5.1's personal/generalisable/system-level classification will suffice for a later generalisation pipeline
- **ASSUMPTION (added 2026-08-08):** the personal / potentially-generalisable / system-level
  classification and provenance fields `ROADMAP.md`'s S5.1/S5.4 exit criteria now require (per
  `CONSTITUTION.md`'s "Personal learning vs. potentially generalisable and system-level learning"
  and `DECISIONS.md`'s corresponding entry) will turn out to be the right shape — sufficient detail,
  the right categories, the right provenance fields — for a still-undesigned future generalisation
  pipeline (privacy/provenance evaluation, de-identification, consent/Governance, validation,
  incorporation into future training/competency/defaults/product releases), without requiring the
  classification scheme itself to be redesigned once that pipeline is actually specified.
- **Why it matters:** this is a deliberately minimal, forward-compatible requirement adopted
  *before* the generalisation pipeline exists, precisely so S5.1 doesn't foreclose it — but "doesn't
  foreclose it" and "is sufficient for it" are different claims, and only the first is currently
  justified. Building S5.1's data shape around the wrong classification granularity could still
  require rework later, just less drastic rework than having no classification at all.
- **Risk if wrong:** a costly schema/classification migration when the generalisation pipeline is
  eventually designed, or — worse — pressure to ship that pipeline against an ill-fitting
  classification rather than correct it, re-opening the same re-identification/privacy risk this
  decision exists to prevent.
- **How to validate:** cannot be validated until a generalisation pipeline is actually designed
  (separate, future, explicitly-approved work, likely well beyond S5.1). Whoever designs S5.1 should
  treat this as a known-open question, not a solved one, and whoever eventually designs the
  generalisation pipeline should explicitly check S5.1's classification against real candidate
  learning records before assuming it is adequate.
- **Status:** unverified — cannot be verified before either S5.1 or the future generalisation
  pipeline exist.

## A9 — The current deployment serves exactly one personal Bartholomew identity
- **ASSUMPTION (added 2026-08-15):** The repository, as it stands, serves exactly **one** personal
  Bartholomew identity, and every single-user convenience it relies on — one process, one SQLite
  database at one filesystem path, module-level singletons holding personal runtime state, an
  unauthenticated local API surface that treats every caller as the owner, and persisted records
  with no ownership column — is a **deployment choice appropriate to this stage**, not an
  architectural commitment. The assumption is that these can later be extended to many strongly
  isolated personal identities without redesigning Bartholomew's identity model, memory semantics,
  Executive, or Governance — per `CONSTITUTION.md`'s "One Platform, Many Personal Bartholomews"
  section and `DECISIONS.md`'s corresponding entry.
- **Why it matters:** the entire platform architecture rests on the claim that today's single-user
  PoC *is* the first personal identity on the future platform rather than a different system that
  will need replacing. If that claim is wrong, the cost is not a migration but a rewrite — and the
  point of recording the assumption now, while there is exactly one user, is that it is cheap to
  correct at this stage and expensive at any later one. Recording it also prevents the assumption
  from remaining invisible, which is how one-process-equals-one-user quietly becomes architecture.
- **Risk if wrong:** persisted personal state that cannot acquire an owner without a destructive
  migration; background/scheduled work whose beneficiary is unrecoverable; an Executive or Memory
  runtime that cannot be separated per identity; or — the expensive failure — concluding that each
  customer must receive their own copy of the whole stack, which makes every platform and model
  upgrade an N-customer migration.
- **How to validate:** cannot be fully validated until a second personal identity actually exists,
  which is **not** current scope. Two cheaper partial validations are available and should be
  preferred over building anything: (a) a design-level dry run — take one existing persisted record
  type (e.g. `memories`) and one background drive, and confirm on paper that an additive ownership
  column plus a per-identity runtime context would suffice, with no change to memory semantics,
  governance, or the Runtime Contract; (b) treat `CHECKLISTS.md`'s "Platform and personal-identity
  architecture checklist" as a running validation — each change that passes it without strain is
  evidence for the assumption, and the first change that cannot pass it is the signal to revisit.
  The known seams to re-check are listed in `COGNITIVE_RUNTIME.md`'s "Personal-identity ownership"
  subsection.
- **Status:** unverified — deliberately, and not scheduled for verification. The repository-grounded
  review of 2026-08-15 found no current code that contradicts it (no model is equated with
  Bartholomew's identity; no personal state is structurally unable to acquire an owner), which is
  supporting evidence, not verification.

## A10 — External capability providers remain available and substitutable on acceptable terms
- **ASSUMPTION (added 2026-08-27):** `DECISIONS.md`'s "Bartholomew is the persistent executive above
  an ecosystem of external intelligence and capability providers" rests on the assumption that
  useful external intelligence and capability — frontier and specialist models, agents, APIs, SaaS
  applications, OS services, web services — will **remain obtainable, and remain substitutable one
  for another**, on terms compatible with Bartholomew's Governance: acceptable cost, acceptable
  privacy and data-handling terms, acceptable reliability, and without a provider acquiring
  authority over the user's data, objectives or autonomy boundaries as a condition of access.
- **Why it matters:** the competitive argument for the whole direction is that ecosystem progress
  becomes *supply* rather than obsolescence, and that Bartholomew therefore need not duplicate a
  capability it can obtain externally. That argument depends on obtainability and on **more than one
  possible supplier per needed capability**. If a capability class is available from exactly one
  provider on terms that require surrendering personal data or control, the correct answer is not to
  accept the terms — it is either to build the capability locally or to do without it, which is a
  materially different cost profile than the decision assumes.
- **Risk if wrong:** capability regression when a provider changes terms, pricing, availability or
  policy; pressure to weaken privacy or Governance to retain a capability; or a de facto lock-in in
  which "replaceable supplier" is true architecturally and false commercially. The specific failure
  to design against is a personal-data or continuity dependency that cannot be moved — which is why
  `CONSTITUTION.md`'s identity-portability and data-portability invariants, and the rule that
  external output is evidence rather than memory, are the mitigations rather than optional extras.
- **How to validate:** cannot be validated in the abstract, and **is not scheduled for validation**.
  The cheap partial validation available now is the one the existing model seam already demonstrates:
  `ModelRouter` dispatches to interchangeable local and cloud adapters, and a local backend carries
  the routine, personal-fact-bearing traffic with cloud off unless deliberately configured. Each
  future external capability should be able to answer the same two questions on paper before it is
  integrated — *what happens to the user if this provider disappears tomorrow?* and *what personal
  context does using it disclose?* The first integration that cannot answer both is the signal to
  revisit this assumption rather than proceed.
- **Status:** unverified — deliberately. No external capability provider beyond the optional cloud
  model backend exists in the repository, so there is as yet nothing to validate against.
