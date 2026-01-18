# ASSUMPTIONS

> Living list of uncertainties that matter. Each must have a validation plan.
>
> **Last updated:** 2026-01-19

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
- **Status:** unverified

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
- **Status:** unverified

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
