# PERF_BUDGETS

> Rough performance budgets for core loops. These are guardrails, not trophies.
>
> **Last updated:** 2026-07-27 (planning-document reconciliation)
>
> ⚠️ **None of the budgets below are currently measured or enforced.** There is no benchmark
> suite, no timing harness, and no CI job that checks any figure on this page — the "How to
> measure" section describes an intention, not an existing artifact. Treat every number here as
> a target proposed on 2026-01-19, not as an observed characteristic of the system. The only
> timing assertions that actually run are the clean-start lifecycle bounds in
> `tests/test_clean_start_lifecycle.py`, and those are deliberately generous **hang detectors**
> (60s), not performance policy. Stated here rather than left implicit so a green CI is not
> mistaken for a met performance budget.

## Measurement principles

- Measure on Linux CI runners first (baseline), then on dev machines.
- Report p50 and p95 where possible.
- Any regression >20% p95 requires explicit justification or fix.

## Budgets (initial)

### Kernel loop
- **Heartbeat / tick:** p95 < 50ms excluding sleep
- **Planner step:** p95 < 150ms (no LLM)

### Memory ingestion
- **Upsert (no encryption + no embeddings):** p95 < 40ms
- **Upsert (with encryption):** p95 < 80ms
- **Upsert (with embeddings compute+store):** p95 < 250ms (depends on backend)

### Retrieval
- **Vector search top_k=10:** p95 < 120ms
- **FTS search top_k=10:** p95 < 80ms
- **Hybrid retrieval (fusion + gating):** p95 < 200ms

### Summarization
- **Fallback/truncation summarization:** p95 < 30ms
- **LLM summarization (future):** budget separately by model/provider

### Encryption
- **Envelope encrypt/decrypt (small payload):** p95 < 10ms

### WAL / DB maintenance
- **Checkpoint on shutdown:** < 500ms typical; log if >2s. **Contradicted by observed
  behaviour (noted 2026-07-27, budget deliberately not rewritten):** a `PRAGMA
  wal_checkpoint(TRUNCATE)` once outlasted its own 30s timeout in CI, which is what prompted
  item 11.18. Shutdown checkpointing is also no longer unconditional — `KernelDaemon.stop()`
  skips it if `SchedulerStore`'s bounded drain does not complete. Why this budget was missed by
  three orders of magnitude is an open question owned by the proposed Phase B workstream; see
  `RISKS.md`'s "unresolved root cause" entry. The figure is left as written rather than raised
  to match the failure, because the behaviour is not proven correct.

## How to measure (pragmatic)

- Use pytest benchmarks where helpful (add `-k perf` style markers).
- Add lightweight internal timing logs around:
  - upsert path
  - retrieval path
  - consent gate filtering
- Treat any perf log that includes sensitive content as a bug (redact).

## Regression response

If a budget is exceeded:
1. Identify which sub-step regressed (profile/log timings).
2. Add/adjust tests to prevent repeat.
3. If the budget is no longer realistic, record a decision in `DECISIONS.md` with justification.


### Experience Kernel (persona + narrator)
- **Self snapshot compute (no LLM):** p95 < 50ms
- **Daily reflection assembly (no LLM):** p95 < 100ms
- **LLM-backed narration/reflection (future):** budget separately by provider/model

### Scheduler (check-ins)
- **Schedule evaluation + job dispatch (no LLM):** p95 < 20ms

### API bridge (Stage 1 baseline)
- **GET /health:** p95 < 30ms
- **GET /nudges (list):** p95 < 60ms
- **POST /nudges/{id}/ack|dismiss:** p95 < 60ms
