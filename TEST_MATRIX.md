# TEST_MATRIX

> Minimum test coverage by subsystem. Add to this when adding a subsystem.
>
> **Last updated:** 2026-01-19

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
- **Unit:** narrator formatting, self_snapshot schema validation, fallback paths.
- **Integration:** scenario replay producing daily/weekly reflection without leaking gated memories.
- **E2E:** prompt → retrieve → kernel decide → produce safe reflection + audit trail entry.
