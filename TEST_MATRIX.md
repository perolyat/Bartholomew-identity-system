# TEST_MATRIX

> Minimum test coverage by subsystem. Add to this when adding a subsystem.
>
> **Last updated:** 2026-07-21 (Experience Kernel section's "not yet implemented" scenario
> replay note corrected; added sections for the self-state API bridge and retrieval consent
> enforcement, both new this session)

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
