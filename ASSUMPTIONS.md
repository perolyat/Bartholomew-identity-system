# ASSUMPTIONS

> Living list of uncertainties that matter. Each must have a validation plan.
>
> **Last updated:** 2026-07-28 (documentation reconciliation pass 2: A4's scope corrected from
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
- **Why it matters:** Architectural simplicity hinges on it; the hybrid local-first deployment
  architecture (`DECISIONS.md`) also depends on the local runtime's persistence layer being sound
  before any cross-device/sync work builds on top of it.
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
  the hybrid local-first deployment architecture decision recorded in `DECISIONS.md`
  (2026-07-28): simple token authentication is **not** assumed sufficient. Remote/cross-device
  exposure of the trusted local Bartholomew runtime must not occur until authentication,
  authorization, transport security, and a reviewed threat model are designed and separately
  approved.
- **Current statement:** no cross-device auth mechanism (token-based or otherwise) may be treated
  as sufficient until a threat model for it has been designed and reviewed. This is a gate, not a
  simplification to avoid "premature complex auth/SSO" — the previous framing had that backwards.
- **Why it matters:** Bartholomew's local runtime is the authority for sensitive memory and
  governance (including the parking brake and emergency shutdown); an under-specified auth scheme
  for reaching it remotely is a direct path to compromising that authority, not a minor UX
  shortcut.
- **Risk if wrong:** Security holes in the exact subsystem responsible for privacy, consent, and
  safety enforcement.
- **How to validate:** Threat model + penetration-style tests on auth endpoints, completed and
  reviewed *before* any remote-exposure feature ships — not retrofitted after.
- **Status:** unverified (and, per the correction above, must remain unverified-and-unshipped
  until the threat model exists — this is not merely an open question to track passively)
