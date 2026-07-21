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


## Experience Kernel — implemented (this section previously said "proposed"; corrected 2026-07-20)

**Correction (2026-07-20):** implemented in `bartholomew/kernel/experience_kernel.py` (self-model)
and `bartholomew/kernel/narrator.py` (narration/reflections), wired into `daemon.py`. The
"Retrieved memories (filtered by consent/privacy)" input below was not actually true of the
implementation until this date — episode/self-model free-text fields (attention targets, goal
descriptions, affect labels, observation/reflection content) had no redaction of their own. Fixed:
see `MASTER_PLAN.md`'s "Experience Kernel MVP: bug fix + privacy gap" section. Narration doesn't
read from the `memories` table directly (confirmed: it's built from `GlobalWorkspace` event
payloads and template strings, not raw stored memory content) — the risk was specifically the
caller-supplied free-text fields listed above, not a memories-table leak.

**Purpose:** Maintain continuity of self (state), narrate experience, and provide reflection summaries.

**Inputs:**
- Recent events (structured)
- Retrieved memories (filtered by consent/privacy) — N/A in the current implementation; narration
  is built from GlobalWorkspace event payloads, not a direct memories-table read
- Current persona pack id

**Outputs:**
- `self_snapshot` (safe-to-share description + current goals) — free-text fields now redacted for
  concrete PII shapes (email/phone/SSN) before entering the snapshot; see correction above
- `narration` (short narrative of recent events)
- `reflections` (daily/weekly)

**Error modes:**
- Missing inputs → degrade to empty snapshot; never fabricate sensitive facts.
- Summarizer failures → fallback to truncated safe summary.

**Performance:**
- Must complete within planner loop budget (see PERF_BUDGETS).

## Skill manifest — implemented (this section previously said "proposed"; corrected 2026-07-21)

**Purpose:** Make skills modular, permission-scoped, and testable.

**Fields:** `config/skills/*.yaml` (`skill_id`, `version`, `description`,
`entry_module`/`entry_class`, `permissions.level` (`auto`/`ask`/`never`) +
`permissions.requires`, `subscriptions`, `emits`, `actions`). See
`bartholomew/kernel/skill_manifest.py` for the full schema.

**Contract:** `bartholomew.kernel.skill_base.SkillBase` — skills subclass it
and implement `initialize()`/`shutdown()`/`execute(action, params)`, returning
a `SkillResult` (`ok`/`fail`/`denied`).

**Wiring (`bartholomew/kernel/skill_registry.py`, `daemon.py`,
`planner.py`):** `KernelDaemon` constructs one `SkillRegistry`, wired into
`Planner.handle_skill_request()` — the "prompt → decide → tool call →
persisted + audited" path. `SkillRegistry.execute_action()` is the single
choke-point every skill execution flows through: checks the global
`ParkingBrake`'s `"skills"` scope (fails closed on error), resolves
`"ask"`-level permissions via the same consent-handler mechanism used for
memory-write consent (`bartholomew.kernel.memory.privacy_guard`,
session-scoped grants only), executes the skill, then writes an audit
record to `skill_action_audit` regardless of outcome (success, failure,
permission denial, or brake block). Starter skills: `tasks` (auto),
`notify` (auto), `calendar_draft` (ask — draft-only, no external calendar
integration). See `tests/test_skill_registry.py` and
`tests/test_end_to_end_tasks_and_audit.py`.

## Identity Context / Policy Decision contract (planned)

**Purpose:** Close the gap the 2026-07-21 architectural audit found — `Identity.yaml`
currently governs only the chat path (via `identity_interpreter`'s pipeline); the autonomous
kernel/scheduler/skill-execution path never consults it. See MASTER_PLAN.md's "P2.5 — Runtime
Convergence" and DECISIONS.md's "Identity publishes a declarative Identity Context..." entry
for the full rationale. **Nothing described below exists yet** — this section sketches the
target shape for that milestone.

**Identity Context** — produced by `identity_interpreter`, declarative only (answers "who is
Bartholomew," never "what should I do right now"): values, red lines, behavioral constraints,
preferences, communication style, risk profile, decision heuristics, goals. Extends/replaces
the role `identity_interpreter.models.Decision` partially plays today.

**Policy Decision** — constructed by the Executive (`bartholomew/kernel/daemon.py`,
`planner.py`, `scheduler/loop.py`) from an Identity Context, not by Identity itself and not by
the Kernel parsing `Identity.yaml` directly.

**Consumers (planned):** `SkillRegistry.execute_action()`, `scheduler/loop.py`'s
`_run_drive()`, and the chat pipeline all consult the same Policy Decision uniformly — today
only the chat pipeline (`identity_interpreter/orchestrator/orchestrator.py`) does.
