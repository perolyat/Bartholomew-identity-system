# ASSUMPTIONS

> Living list of uncertainties that matter. Each must have a validation plan.
>
> **Last updated:** 2026-07-21 (A1, A3 re-verified against this session's findings; A1b added;
> A2/A4/A5 and the three brainstorm-era assumptions below not re-checked this pass)

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
- **Status:** unverified

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
  design guarantee. No red-team bypass-path test suite exists yet.

## A4 — Single SQLite DB remains viable through Stage 2
- **ASSUMPTION:** SQLite will be sufficient for persistence, retrieval, and test workloads through Stage 2.
- **Why it matters:** Architectural simplicity hinges on it.
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

## ASSUMPTION: Cross-device ‘one mind’ is achievable with simple token auth first
- **Statement:** A minimal token-based auth layer is sufficient for early cross-device experiments.
- **Why it matters:** Avoid premature complex auth/SSO.
- **Risk if wrong:** Security holes; we must upgrade to OAuth/SSO sooner.
- **How to validate:** Threat model + penetration-style tests on auth endpoints.
- **Status:** unverified
