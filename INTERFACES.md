# INTERFACES

> Contracts between core modules. If a contract changes, update this doc and add/adjust tests.
>
> **Last updated:** 2026-01-19

## 1) Identity configuration

### Identity.yaml
- **Purpose:** Primary governing config for routing, safety, persona, and policy.
- **Producer:** Human authoring + linting.
- **Consumer:** `identity_interpreter` loader + policies; kernel components reading normalized config.

**Key operations:**
- Lint/validate: `python -m identity_interpreter.cli lint Identity.yaml`
- Explain a decision trace: `python -m identity_interpreter.cli explain Identity.yaml ...`

**Error modes:**
- Schema invalid → hard fail (no runtime).
- Normalization error → hard fail.

---

## 2) Kernel DB interface (SQLite)

### DB path resolution
- `BARTH_DB_PATH` env var wins; otherwise default `data/barth.db`.

### Tables (high-level)
- `memories` + related governance metadata
- `memory_consent`
- `nudges`
- `reflections`
- vector/fts tables as implemented

**Invariants:**
- Timestamps stored as UTC.
- WAL mode with clean checkpoint on shutdown.

---

## 3) Memory ingestion

### `MemoryStore.upsert_memory(...)` (conceptual)
- **Input:** kind, content/value, metadata (speaker, privacy markers, etc.)
- **Output:** memory_id + stored metadata (including policy flags)

**Governance pipeline (must occur in this order):**
1. Apply memory rules → determine allow_store / requires_consent / recall_policy / summarize / encrypt / embed.
2. Redact sensitive spans (if required by rule).
3. Summarize (if enabled by rule; must handle fallbacks deterministically).
4. Encrypt at rest (if required) using envelope format.
5. Persist to DB.
6. Index: FTS/vector depending on policy.

**Error modes:**
- If encryption fails → fail the write (no partial storage).
- If summarization fails → store redacted content and mark summary as missing; never crash the kernel loop.

---

## 4) Retrieval

### Retriever modes
- **vector**: semantic vector search
- **fts**: keyword search
- **hybrid**: fusion of vector + fts with recency shaping

### Consent gate
- Must be applied by default at the lowest layer.
- Bypass (`apply_consent_gate=False`) is **admin-only** and must never be used in user-facing flows.

**Output contract:**
- Returns ordered results with:
  - `memory_id`
  - score fields (vector/fts/fused)
  - `context_only` flag where relevant
  - snippet/preview if safe

---

## 5) Parking brake

### Scope checks
- Components must check before executing side effects.
- Supported scopes: `global`, `skills`, `sight`, `voice`, `scheduler`.

**Failure mode:**
- If engaged → fail closed (raise / early-return) with a structured error.

---

## 6) API bridge (FastAPI)

### Minimal endpoints (Stage 0/1)
- Nudges: list pending, ack, dismiss
- Reflections: fetch latest daily/weekly; manual trigger (dev/testing)
- Health: kernel online + last beat + db path + counts

**Security stance (today):**
- Treat as local/dev surface until auth is introduced.

---

## 7) Logging / audit

**Logging expectations:**
- Structured logs for orchestrator/kernel loops.
- Sensitive content must be redacted before logging.

**Audit expectations:**
- Safety-relevant events (brake toggles, consent decisions) must be recorded.

---

## 8) Performance expectations

See [PERF_BUDGETS.md](PERF_BUDGETS.md) for budgets and measurement method.


## Experience Kernel (proposed)

**Purpose:** Maintain continuity of self (state), narrate experience, and provide reflection summaries.

**Inputs:**
- Recent events (structured)
- Retrieved memories (filtered by consent/privacy)
- Current persona pack id

**Outputs:**
- `self_snapshot` (safe-to-share description + current goals)
- `narration` (short narrative of recent events)
- `reflections` (daily/weekly)

**Error modes:**
- Missing inputs → degrade to empty snapshot; never fabricate sensitive facts.
- Summarizer failures → fallback to truncated safe summary.

**Performance:**
- Must complete within planner loop budget (see PERF_BUDGETS).

## Skill manifest (proposed)

**Purpose:** Make skills modular, permission-scoped, and testable.

**Fields (minimum):**
- `id`, `version`, `description`
- `risk_class` (low/med/high)
- `permissions_required` (ask/auto/never)
- `data_touched` (kinds)
- `tests` (paths)

**Contract:** Skill functions must declare inputs/outputs, side-effects, and rollback notes.
