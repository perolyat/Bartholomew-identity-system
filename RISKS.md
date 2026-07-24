# RISKS

> Risk radar: security, privacy, reliability, maintainability, performance, tech debt.
>
> **Last updated:** 2026-07-24 (R1's red-team test suite added and independently re-verified —
> see below and MASTER_PLAN.md item 11.20; two tech debt items added earlier the same day from
> the scheduler/WAL concurrency fix found while merging item 11.17 — MASTER_PLAN.md item 11.18
> and DECISIONS.md's "Scheduler persistence moved off the event loop..." entry have the full
> writeup. R3/R5/R6 last independently re-verified 2026-07-21, not re-checked this pass; R2/R4
> and the pre-existing tech debt items likewise left as last recorded)

## Risk register (top)

### R1 — Consent bypass / privacy leakage
- **Category:** Privacy, Safety
- **What could go wrong:** A caller retrieves or surfaces a memory that should be excluded (never_store / ask_before_store / context_only).
- **Current controls:** ConsentGate applied by default at FTS/vector layers; memory rules engine; redaction; encryption.
- **Mitigation:** Add bypass-path red-team tests; audit any `apply_consent_gate=False` call sites; enforce “admin-only” paths.
- **Status:** Mitigated (2026-07-24) — the "no red-team test suite exists yet" gap is now closed:
  `tests/test_consent_bypass_redteam.py` (10 tests, see MASTER_PLAN.md item 11.20) drives content
  through the real `memories`/FTS/vector tables (bypassing `upsert_memory()`'s write-time guard,
  since `MemoryRulesEngine.should_store()` already hard-blocks `requires_consent` content there —
  the scenario worth red-teaming is content that reaches storage some other way: a rules.yaml
  reclassification after the fact, a migration, direct DB access) and proves it's never surfaced
  through every production retrieval surface: `get_retriever()`'s three modes, and
  `HybridRetriever`/`FTSOnlyRetriever` constructed directly with no `rules_engine` — the exact
  construction each class's own docstring usage example shows. That last case surfaced a real
  structural finding, not a bug: `HybridRetriever`/`FTSOnlyRetriever` skip their own optional
  rules-engine re-filtering entirely when constructed without one, but the retrieval-layer
  `ConsentGate` baked unconditionally into `FTSClient.search()`/`VectorStore.search()` (default
  `apply_consent_gate=True`) still holds regardless — confirmed by deliberately breaking
  `ConsentGate.filter_memory_ids()` and watching exactly those "no rules_engine" tests fail (and
  no others), proving the tests watch the right thing rather than passing vacuously. Also
  formalized the 2026-07-21 one-time grep audit into two permanent regression guards: an
  AST-based scan that no production call site ever passes `apply_consent_gate=False`, and a
  signature check that no public `.retrieve()` facade (`HybridRetriever`, `FTSOnlyRetriever`,
  `VectorRetrieverAdapter`) exposes a parameter capable of disabling the gate at all. One residual
  nuance, not a leak under current code: `get_retriever(mode="vector")` doesn't pass a
  `memory_store` through by default, so `Retriever`'s own second-layer rule check degrades to a
  content-less stub for that mode specifically — harmless today because the first-layer
  `ConsentGate` inside `VectorStore.search()` already gates unconditionally, but it means "vector"
  mode has one fewer redundant layer than "hybrid"/"fts" if that first layer were ever
  independently broken. Left as a documented, non-blocking observation rather than fixed in this
  pass — no test currently requires it and fixing it wasn't part of this session's scope. The
  earlier fail-closed bug (`Retriever`/`FTSOnlyRetriever`/`HybridRetriever` excluding every
  `requires_consent` memory unconditionally regardless of actual consent) stays fixed and covered
  by `tests/test_retrieval_consent_enforcement.py`, re-verified green as part of this pass.

### R2 — Over-automation / unsafe side effects
- **Category:** Safety
- **What could go wrong:** Skills/scheduler execute actions without explicit consent, or continue running when they shouldn’t.
- **Current controls:** Parking brake (scoped, persistent, fail-closed).
- **Mitigation:** Ensure every “Act” path checks brake; keep Stage 1 strictly read/ack/dismiss; add integration tests.
- **Status:** Controlled, and the "add integration tests" mitigation is well satisfied: `pytest
  -q tests/integration/test_parking_brake_integration.py tests/test_parking_brake_scoped_blocks.py
  tests/unit/safety/test_parking_brake.py` (17 tests, 2026-07-21) covers all five live scopes
  (`skills`, `scheduler`, `sight`, `voice`, `global`) both engaged and disengaged. See
  `COGNITIVE_RUNTIME.md`'s "Governance checkpoints" section for the current call-site list.
  Expansion risk (new "Act" paths forgetting the brake check) remains the open-ended part of
  this risk — not something a point-in-time audit closes permanently.

### R3 — SQLite / FTS feature variability causes false confidence
- **Category:** Reliability
- **What could go wrong:** Retrieval works on one platform/build and silently breaks on another (FTS5/matchinfo/bm25 behavior differences).
- **Current controls:** FTS fallback implementations exist; tests exercise some fallbacks.
- **Mitigation:** Linux CI baseline; explicit environment detection; fallback-path tests; consider bundling SQLite build if needed.
- **Status:** Largely mitigated on Linux (2026-07-20/21) — `docs/STATUS_2025-12-29.md` is stale
  and superseded on this topic (MASTER_PLAN.md flags it as such): two FTS5 bugs this doc
  attributed to Windows-only quirks were actually real logic bugs, reproduced identically on
  Linux and fixed (see MASTER_PLAN.md's "FTS5 external-content `upsert()` bug..." section); the
  full suite (`pytest -q`) is fully green on Linux as of 2026-07-20, and `tests/
  test_retrieval_fts5_fallback.py`/`tests/test_fts_schema_hygiene.py` pass locally as of
  2026-07-21. Cross-platform variability itself is an inherent, ongoing category (not a single
  bug to close permanently) — Windows behavior specifically is untested in this sandbox.

### R4 — Windows file locking causing flaky tests and masking real failures
- **Category:** Reliability, Maintainability
- **What could go wrong:** Temp DB cleanup fails; tests go red for non-product reasons; teams ignore failures.
- **Current controls:** Some retry cleanup patterns in fixtures.
- **Mitigation:** Close connections deterministically; tighten async fixtures; quarantine truly platform-only failures with markers.
- **Status:** Active — not re-verified this pass (this sandbox is Linux-only; no Windows CI run
  available to check against).

### R5 — Encryption envelope round-trip bugs
- **Category:** Security, Reliability
- **What could go wrong:** Data is encrypted but cannot be decrypted; summaries/values become unrecoverable; user trust destroyed.
- **Current controls:** EncryptionEngine + tests.
- **Mitigation:** Fix the failing integration tests; add property-based tests for envelope formats; version envelopes.
- **Status:** Mitigated — `pytest -q tests/test_phase2b_encryption.py` passes locally (2026-07-21,
  clean venv); MASTER_PLAN.md's P0 status (2026-07-20) records the full suite, including this
  file, at zero failures. No property-based envelope tests or explicit envelope versioning yet
  added — those two mitigation items remain open.

### R6 — Metrics duplication / cardinality blowups
- **Category:** Performance, Reliability
- **What could go wrong:** Re-registering Prometheus metrics causes runtime crashes; high-cardinality labels blow up memory.
- **Current controls:** Metrics registry guard tests exist.
- **Mitigation:** Make registry init idempotent; enforce label allowlists; add perf budget.
- **Status:** Mitigated — `pytest -q tests/test_metrics_labeled.py tests/test_metrics_labels.py
  tests/test_metrics_production_mode.py tests/test_metrics_registry_guard.py` passes locally
  (2026-07-21); MASTER_PLAN.md's P0 status lists metrics-registry idempotency among the items
  fixed 2026-07-20. Label allowlist enforcement and a dedicated perf budget check aren't
  independently confirmed this pass — see `PERF_BUDGETS.md`.

## Tech debt watchlist

- Legacy “implementation notes” docs are useful but currently compete with SSOT.
- ~~Retrieval mode factory mismatches (explicit mode returns wrong retriever).~~ Appears
  resolved: `tests/test_retrieval_factory.py` has explicit coverage for `fts`/`vector`/`hybrid`
  mode selection, invalid-mode handling, and env/config override precedence, all passing
  locally as of 2026-07-21. Left struck through rather than deleted since this wasn't
  independently re-derived from a bug report, only inferred from current test coverage.
- ~~Chunking engine exists but not wired; risk of architecture drift.~~ Stale as of
  2026-07-21: `bartholomew/kernel/chunking_engine.py`'s `ChunkingEngine` is wired into
  `memory_store.py`'s live `upsert_memory()` path (`chunking_engine.enabled` defaults to
  `True`), not a standalone/dormant module — `pytest -q -k chunk` (16 tests) passes locally.
  Left struck through rather than deleted for the same reason as the retrieval-mode item above.
- **(2026-07-24) Unresolved root cause: why a `TRUNCATE` checkpoint outlasted its own
  busy-timeout in CI.** `db_ctx.py`'s `wal_checkpoint()` gained temporary DEBUG-level
  instrumentation (start time, duration, thread, mode, label, the checkpoint's own result row,
  `in_transaction`) to help answer this — it's inert unless that logger's level is explicitly
  raised, so it carries no runtime cost today, but it has no removal date or owning
  investigation ticket. See MASTER_PLAN.md item 11.18 / DECISIONS.md's "Scheduler persistence
  moved off the event loop..." entry for the incident. Either resolve the question and remove
  the instrumentation, or turn it into permanent, deliberately-scoped observability — leaving it
  as unowned "temporary" code is the debt.
- **(2026-07-24) Mixed synchronous `sqlite3` and `aiosqlite` ownership of the same database
  file.** `memory_store.py` (aiosqlite), `scheduler/persistence.py` (sync, now behind
  `SchedulerStore`'s dedicated thread), and `persona_pack.py`/`narrator.py` (sync, still called
  directly from async methods, not audited or fixed by item 11.18) all read/write the same
  underlying db file with no single owner. Item 11.18 fixed the one call path proven to hang
  (the scheduler's own tick loop); it deliberately did not consolidate database ownership more
  broadly, and did not touch `bartholomew_api_bridge_v0_1/services/api/db_ctx.py` — a
  near-duplicate of `bartholomew/kernel/db_ctx.py` with the same per-call-checkpoint pattern
  still live in `liveness.py`/`db.py`'s hot paths. Same latent hazard class as item 11.18 fixed;
  not yet known to have caused a failure outside the one incident that prompted this fix.

## Red-team focus areas

1. Consent gate bypass paths (`apply_consent_gate=False`)
2. Parking-brake coverage of any new subsystem
3. Log redaction (ensure sensitive strings never hit logs)
4. Retrieval leakage via snippets/metadata


## Risk: LLM provider rate limits / prompt bloat (operational)
- **Why it matters:** Large “one-shot” prompts can exceed token-per-minute limits (as seen in Cline) and fail nondeterministically.
- **Mitigation:** Chunk/map-reduce processing; reference files instead of pasting; keep prompts under a hard cap; prefer local parsing for huge artifacts.
- **Test/Proof:** A scripted chunking run that produces stable intermediate artifacts + a final merge.
- **Status:** Active
