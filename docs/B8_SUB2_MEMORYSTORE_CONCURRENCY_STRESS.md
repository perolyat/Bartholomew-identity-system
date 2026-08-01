# B8 (sub-stage 2) — MemoryStore concurrency stress test

> **Status:** second split sub-stage of B8. Closes `docs/PHASE_B_RISK_MAP.md`'s remaining named B8
> row not addressed by sub-stage 1: "`MemoryStore` concurrency and its own serialization cost under
> the new executor model... Stress-harness test analogous to archived Test 51."

## 1. Scope

A concurrency stress test against a real `MemoryStore` sharing one `SingleWorkerExecutor` — the
shape a busy daemon actually subjects it to, now that sub-stage 1 routes every `VectorStore` call
through that same executor too, not just SQLite writes. Not a redesign or new concurrency
mechanism: the goal was to validate the *existing* architecture (aiosqlite for `MemoryStore`'s own
tables, the shared executor for `VectorStore`'s synchronous calls) under real concurrent load, and
fix anything a stress test surfaced.

## 2. What was built

`tests/test_memory_store_concurrency_stress.py` (3 tests):

- `test_concurrent_upserts_lose_no_writes` — 25 concurrent `upsert_memory()` calls against one
  `MemoryStore`; every call succeeds and every memory gets a distinct id (no lost or merged
  writes).
- `test_concurrent_upserts_with_embeddings_do_not_cross_contaminate` — 10 concurrent
  `upsert_memory()` calls with `BARTHO_EMBED_ENABLED=1`; verifies every embedding row found under
  each resulting `memory_id` genuinely belongs to it — the real regression target, since all of
  these calls now compete for the same single-worker executor sub-stage 1 introduced for
  `VectorStore` access, and a race there could in principle file one memory's vector under another's
  id.
- `test_concurrent_reembed_calls_on_different_memories_do_not_interfere` — 5 memories re-embedded
  concurrently via `reembed_memory()`, each producing a positive embedding count.

## 3. Result

All three passed against the current implementation on first run — **no bug found**. This is a
legitimate, valuable outcome in its own right: it's new regression coverage confirming
`SingleWorkerExecutor`'s strict-sequential-submission guarantee (documented in
`bartholomew/kernel/blocking_executor.py`) genuinely holds under concurrent `MemoryStore` usage,
including through sub-stage 1's newly-added `VectorStore` off-loop calls, and closes the risk map's
named B8 row rather than leaving it as an open, untested concern.

## 4. Exit condition check

- [x] `docs/PHASE_B_RISK_MAP.md`'s "MemoryStore concurrency... under the new executor model" B8 row
  has a stress-harness test, per that row's own required-outcome column.
- [x] No cross-module schema consolidation attempted; no changes to `MemoryStore`'s or
  `VectorStore`'s own implementation were needed.

With this, every B8 candidate named in `docs/PHASE_B_RISK_MAP.md`'s B8 section is now either fixed
(sub-stage 1), tested and confirmed sound (this sub-stage), or confirmed not applicable to the
current repository (sub-stage 1's liveness/metrics/hybrid_retriever findings). The one remaining
named-but-deferred item (`SkillRegistry.__init__()`'s constructor-time blocking I/O, sub-stage 1
§1 finding 6) is a construction-site reorganization outside this stage's "migrate remaining
consumers" scope, not a migration target B8 itself owns.
