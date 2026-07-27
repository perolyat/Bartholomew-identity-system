# TEST_MATRIX

> Minimum test coverage by subsystem. Add to this when adding a subsystem.
>
> **Last updated:** 2026-07-27 (planning-document reconciliation: added the subsystems whose
> tests landed between 2026-07-23 and Phase A — runtime-contract seams for skills and
> voice/sight, consent-bypass red team, scheduler startup readiness, packaging contract,
> clean-start lifecycle, sensitive-memory consent. This matrix had recorded none of them, so a
> reader would have concluded those subsystems were untested.)
>
> **Counts below are from `pytest --collect-only` on 2026-07-27, not from memory.** Total suite:
> 915 collected; `pytest -q` runs 912 (3 `integration`/`slow` tests are deselected by
> `addopts`). See [CI.md](CI.md).

## Legend

- **Unit:** isolated pure/module tests
- **Integration:** crosses module boundaries (DB, retrieval, policies)
- **E2E:** start services and exercise real flows

## Matrix

### Identity interpreter (config + policies)
- **Unit:**
  - `tests/test_loader.py`, `tests/test_policies.py`
- **Integration:**
  - `tests/test_orchestration_integration.py` (routing + adapters)
- **E2E:**
  - CLI smoke: `python -m identity_interpreter.cli lint Identity.yaml`

### Kernel lifecycle (Stage 0)
- **Integration/E2E:**
  - `tests/test_stage0_alive.py`
  - `tests/test_sqlite_wal.py`, `tests/test_sqlite_wal_concurrent_processes.py`

### Consent gates + governance
- **Unit/Integration:**
  - `tests/test_consent_gates.py`
  - `tests/test_indexing_policy_guard.py`
  - `tests/test_kernel_privacy_guard.py`

### Redaction (Phase 2A)
- **Unit:**
  - `tests/test_phase2a_redaction.py`

### Encryption (Phase 2B)
- **Unit/Integration:**
  - `tests/test_phase2b_encryption.py`

### Summarization (Phase 2C)
- **Unit/Integration:**
  - `tests/test_phase2c_summarization.py`

### Embeddings + vector store (Phase 2D)
- **Unit/Integration:**
  - `tests/test_phase2d_compute_only.py`
  - `tests/test_phase2d_embeddings.py`
  - `tests/test_phase2d_fixpack_v3.py`

### FTS + hybrid retrieval
- **Unit:**
  - `tests/test_fts_schema_hygiene.py`
  - `tests/test_retrieval_factory.py`
  - `tests/test_hybrid_fusion_math.py`, `tests/test_hybrid_rrf.py`, `tests/test_hybrid_recency.py`, `tests/test_hybrid_tiebreakers.py`
- **Integration:**
  - `tests/test_fts_search.py`, `tests/test_fts_snippet_smoke.py`
  - `tests/test_bm25_udf_fallback.py`, `tests/test_retrieval_fts5_fallback.py`

### Metrics
- **Unit/Integration:**
  - `tests/test_metrics_labels.py`, `tests/test_metrics_labeled.py`, `tests/test_metrics_registry_guard.py`, `tests/test_metrics_production_mode.py`

### Parking brake
- **Unit/Integration:**
  - `tests/test_parking_brake_persistence_roundtrip.py`
  - `tests/test_parking_brake_scoped_blocks.py`

### Runtime Contract seams (Stage 4.5 convergence)

Each live surface must construct an `Observation`/`CandidateAction` that is *genuinely consumed*
by Governance, and emit exactly one `ActionReflection` per attempt.

- **Unit/Integration:**
  - `tests/test_runtime_contract_chat_seam.py` (chat), `tests/test_api_chat_runtime_contract.py`
  - `tests/test_scheduler_drive_convergence.py` (drives; includes the item-11.2 regression guard
    and the `_SELF_MAINTENANCE_DRIVES` registry-parity check)
  - `tests/test_skill_runtime_contract_seam.py` — **15 tests** (item 11.19)
  - `tests/test_voice_sight_runtime_contract_seam.py` — **45 tests** (item 11.21)
  - `tests/test_runtime_convergence_policy.py`
- **Non-vacuity requirement:** these suites include deliberate gate-neutralisation controls —
  breaking a single gate must make exactly that gate's denial tests fail. A new seam test that
  cannot fail when its gate is removed is not coverage.

### Consent-bypass red team (RISKS.md R1)

- **Integration:** `tests/test_consent_bypass_redteam.py` — **10 tests** (item 11.20). Drives
  content through the real `memories`/FTS/vector tables and proves it is never surfaced by any
  production retrieval surface, including retrievers constructed with no rules engine. Includes
  permanent structural guards: an AST scan that no production call site passes
  `apply_consent_gate=False`, and a signature check that no public `.retrieve()` facade exposes a
  parameter capable of disabling the gate.

### Scheduler startup readiness (S5.0, issue #24)

- **Integration:** `tests/test_scheduler_startup_readiness.py` — **10 tests**. Tables exist when
  `start()` returns; ordered-record and `asyncio` barrier proofs that schema readiness precedes
  scheduler-task creation and the loop's first DB operation; fail-closed cleanup, including that a
  failing cleanup does not mask the primary exception; cancellation and later-stage failure;
  idempotent `ensure_schema`.

### Packaging / dependency contract (Phase A)

- **Smoke:** `tests/smoke/test_packaging_contract.py` — **9 tests**. Every first-party package and
  submodule imports; every declared console script runs `--help`; the API app imports by its
  canonical path; no undeclared third-party runtime import; and every entry claimed to be a
  "guarded optional" import really is inside a `try`/`except` (so the allow-list cannot be used to
  silence a real undeclared dependency).
- **Why it exists:** a green suite never caught that the `bartholomew` console script was broken at
  import time, because no test imported `bartholomew.cli`. See `ASSUMPTIONS.md` A1b.

### Clean-start persistence + lifecycle (Phase A)

- **Integration/E2E:** `tests/test_clean_start_lifecycle.py` — **6 tests**. Fresh database gets
  core *and* scheduler schema; bounded start/stop (hang detectors, not performance policy);
  no leaked scheduler threads or pending tasks; database handles released well enough to delete
  the temp directory (the property that fails first on Windows); clean restart against the same
  database; no "missing table" window for scheduler-backed API endpoints.
- **Note:** these *characterise* current persistence behaviour. They do not restructure it —
  that is Phase B, which is not approved.

### Sensitive-memory consent (Phase A regression)

- **Unit/Integration:** `tests/test_memory_store_sensitive_consent.py` — **5 tests**. Regression
  cover for the defect where `MemoryStore.upsert_memory()` called `asyncio.run()` from inside an
  `async def`, always raising `RuntimeError` and falling through to an undeclared
  `import nest_asyncio` — so approved sensitive content raised `ModuleNotFoundError` instead of
  being stored. Consent remains fail-closed with no handler registered.

## When adding a new subsystem

You must add:
- at least one unit test
- at least one integration test crossing the brittle boundary (DB + governance + retrieval)
- update `INTERFACES.md` if any contract changes



## Experience Kernel

**Status (2026-07-20):** unit coverage already exists and passes (`tests/test_experience_kernel.py`,
`tests/test_narrator.py`, `tests/test_reflection_generation.py`, `tests/test_persona_pack.py`,
`tests/test_working_memory.py`, `tests/test_global_workspace.py`), including new PII-redaction
tests (`TestPIIRedaction` in `test_narrator.py`, `TestExperienceKernelPIIRedaction` in
`test_experience_kernel.py`) added when that gap was found and closed — see `MASTER_PLAN.md`.
`tests/test_stage3_integration.py::TestFullLifecycle` is cross-module integration coverage.

- **Unit:** narrator formatting, self_snapshot schema validation, fallback paths.
- **Integration:** `tests/test_stage3_integration.py::TestFullLifecycle` (hand-wires individual
  kernel modules together, not the real `KernelDaemon`).
- **E2E:** `tests/test_scenario_replay.py` — ✅ implemented 2026-07-21 (see MASTER_PLAN.md item
  11.9). Drives a real `KernelDaemon` through `run_chat_through_runtime_contract()` across one
  continuous multi-turn session (chat → goal added → later turn referencing both the goal and
  prior turn content → persona switch → parking-brake block/recover → daily reflection
  capturing the real episode → simulated restart). Found and fixed a real bug in the process
  (`ExperienceKernel` state wasn't actually restored on daemon restart despite a log line
  claiming it was).

## API bridge — self-state / persona / episodes (`bartholomew_api_bridge_v0_1`)

**Status (2026-07-21):** had **zero** HTTP-level tests before this date — everything else
touching this surface called `ExperienceKernel`/`PersonaPackManager` methods directly, never
through the actual routes. `tests/test_self_state_api.py` is the first such file; writing it
found five previously-live 500s (parameter-name drift between routes and the kernel methods
they call) and one always-null response field — see MASTER_PLAN.md items 11.10/11.11.

- **Integration (HTTP, via `fastapi.testclient.TestClient` against the real app):**
  `tests/test_self_state_api.py` — self-snapshot, goals (add/get/complete), persona
  (list/switch/current), drives (list/top/activate/satisfy), attention (set/clear), episode
  search and by-type lookup.
- Note: `app_module._kernel` is a process-wide singleton shared with
  `tests/test_api_chat_runtime_contract.py` when both run in the same pytest process — any new
  test here that mutates goals/persona must clean up after itself (see this file's own
  docstring for the leak this caused and how it was fixed).

## Retrieval consent enforcement

**Status (2026-07-21):** `tests/test_retrieval_consent_enforcement.py` — added when a real bug
was found: `Retriever`/`FTSOnlyRetriever`/`HybridRetriever` each excluded every
`requires_consent` memory unconditionally, ignoring the `memory_consent` table `ConsentGate`
already reads correctly one layer down. See MASTER_PLAN.md's "Retrieval consent-enforcement
bug" section.

- **Unit:** direct tests of `_should_include()`/`_evaluate_rules()` with a mocked rules engine,
  isolating the consented-ids wiring from `memory_rules.yaml` pattern-matching.
- **Integration:** one test against a real `memory_consent` row via `ConsentGate` itself.
