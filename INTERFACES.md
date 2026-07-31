# INTERFACES

> Contracts between core modules. If a contract changes, update this doc and add/adjust tests.
>
> **Last updated:** 2026-07-28 (documentation reconciliation pass 2: §6's security stance updated
> for the hybrid local-first deployment decision; a new "Proposed contracts" subsection added
> under §6 for emergency-shutdown, capture-control, data-export, notification, and
> awaiting-response interfaces — all explicitly unimplemented and unapproved for implementation.)
>
> **Previously (2026-07-27):** the header had read 2026-01-19 while sections were appended
> through July. §2's table list and WAL-checkpoint invariant, §4's consent-gate bypass wording and
> §6's endpoint list were corrected against current code; the per-subsystem sections appended
> 2026-07-20/21/23 were already accurate and unchanged.

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

*(Corrected 2026-07-27 — enumerated from a fresh database created by a real `KernelDaemon`
start/stop cycle, not from memory. The previous list named four tables and "vector/fts tables as
implemented"; a fresh database actually contains 37.)*

- **Memory substrate:** `memories`, `memory_consent`, `memory_chunks`, `memory_fts*`,
  `chunk_fts*`, `episode_fts*` (FTS5 shadow tables)
- **Kernel output:** `nudges`, `reflections`
- **Experience Kernel:** `experience_snapshots`, `working_memory_snapshots`, `episodic_entries`,
  `persona_switch_log`
- **Scheduler:** `scheduled_tasks`, `ticks` — created synchronously during `KernelDaemon.start()`
  and guaranteed to exist before it returns (S5.0; see DECISIONS.md and issue #24)
- **Governance / skills:** `system_flags` (parking-brake state), `skill_permissions`,
  `permission_audit`, `skill_action_audit`, `skill_registry_state`, plus per-skill tables
  (`skill_tasks`, `skill_notifications`, `skill_calendar_events`)

**Invariants:**
- Timestamps stored as UTC.
- WAL mode. **Checkpointing is not per-call** — corrected 2026-07-27: `wal_db()` defaults to
  `checkpoint=None` since item 11.18, relying on SQLite's own automatic WAL checkpoint, because
  an unconditional blocking `TRUNCATE` on every scheduler tick deadlocked the event loop. WAL
  mode already guarantees readers see committed writes regardless of checkpoint timing, so this
  is a disk-layout choice, not a correctness one. Explicit `TRUNCATE` is retained for controlled
  maintenance and shutdown (`MemoryStore.close()`, the API bridge's `atexit` hook).
  **Applies uniformly since Phase B stage B1** (`docs/B1_SHARED_CONNECTION_POLICY.md`):
  `bartholomew_api_bridge_v0_1/services/api/db_ctx.py` had independently re-diverged (its own
  `wal_db()` unconditionally checkpointed on every call, including three read-only liveness GET
  routes) until B1 made it re-export `bartholomew.kernel.db_ctx` directly.
- **Shutdown checkpointing is conditional, not guaranteed.** If `SchedulerStore.close()`'s
  bounded drain (default 5s) does not complete, `KernelDaemon.stop()` deliberately skips the
  shutdown `TRUNCATE` rather than contend with a possibly-live worker thread; WAL cleanup is
  deferred to the next startup and logged. The former "WAL mode with clean checkpoint on
  shutdown" invariant overstated this.
- **No single owner.** `aiosqlite`, synchronous `sqlite3`, and `SchedulerStore`'s dedicated
  worker thread all access this one file. This is characterised, not resolved — see `RISKS.md`
  and `ROADMAP.md`'s Phase B workstream (proposed, not approved).

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
- Must be applied by default at the lowest layer (`FTSClient.search()` / `VectorStore.search()`,
  `apply_consent_gate=True` by default).
- Bypass (`apply_consent_gate=False`) is **admin-only** and must never be used in user-facing
  flows. **Enforced, not merely stated (2026-07-24, item 11.20):** an AST-based regression test
  asserts no production call site ever passes `apply_consent_gate=False`, and a signature check
  asserts no public `.retrieve()` facade (`HybridRetriever`, `FTSOnlyRetriever`,
  `VectorRetrieverAdapter`) even exposes a parameter capable of disabling the gate. See
  `tests/test_consent_bypass_redteam.py` and `RISKS.md` R1.

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

**Also live (added 2026-07-27; this list had not been updated since 2026-01-19):**
- Liveness: `/api/liveness/ticks`, `/api/liveness/nudges`, `/api/liveness/reflections`.
  `/ticks` returns `[]` rather than 500 when the `ticks` table is absent — retained as defense in
  depth even though S5.0 removed the startup window that made it reachable (issue #24, PR #23).
- Chat: `/api/chat`, routed through `run_chat_through_runtime_contract()` when the kernel is
  running (item 11.4).
- Self-state: self-snapshot, goals, persona, drives, attention and episode routers
  (`tests/test_self_state_api.py`).

**Security stance (today):**
- Treat as local/dev surface until auth is introduced. Unchanged: there is still no
  authentication. Auth is Stage 6 work, and is now explicitly scoped by the hybrid local-first
  deployment architecture (`DECISIONS.md`, 2026-07-28): remote/cross-device exposure of this API
  must not occur until authentication, authorization, transport security, and a reviewed threat
  model are designed and separately approved — a simple token-auth scheme is explicitly **not**
  assumed sufficient (see `ASSUMPTIONS.md`).

### Proposed contracts — NOT implemented, NOT approved for implementation (added 2026-07-28)

The following interfaces are recorded here only so that a future, separately-approved
implementation has a single agreed shape to build against. None of them exist in code today.
Listing them here does not authorise building them.

- **Emergency shutdown (out-of-process).** A control path independent of Bartholomew's own
  application code, per `CONSTITUTION.md`'s independent-emergency-shutdown invariant. Proposed
  shape: unspecified pending design; must not depend on Bartholomew's own process, UI, or network
  stack being responsive.
- **Capture-control (start/stop/teardown).** Per `COGNITIVE_RUNTIME.md`'s "Device surfaces" section,
  the existing governed seam (`run_voice_/run_sight_through_runtime_contract()`) already covers a
  single *start* attempt. A proposed *teardown* interface must never itself require passing the
  same consent/policy gates a *start* does (teardown is not a governed "start").
  Jurisdiction-aware capture/recording compliance (`CONSTITUTION.md`) is design scope for whatever
  real capture capability Stage 6 eventually builds.
- **Data export / portability.** Per `CONSTITUTION.md`'s data-portability invariant: memories,
  preferences, personal model, identity/governance settings, provenance, approvals/audit history,
  and active goals/unresolved matters. Proposed shape: unspecified pending design (see `ROADMAP.md`
  Stage 6).
- **Notification preferences / adaptive notifications.** Mute, quiet-hours, and per-category
  preferences (minimum viable version scoped to Stage 1's governance shell — see `ROADMAP.md`);
  genuinely adaptive behaviour (per `CONSTITUTION.md`'s adaptive-notifications invariant) is
  Stage 6 design scope.
- **`awaiting_response` queue.** Per `COGNITIVE_RUNTIME.md`'s `awaiting_response` obligation-state
  section: list/resolve open obligations, minimum viable version scoped to Stage 1.

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

## Identity Context / Policy Decision contract — implemented for skill execution 2026-07-21

**Purpose:** Close the gap the 2026-07-21 architectural audit found — `Identity.yaml`
governed only the chat path (via `identity_interpreter`'s pipeline); the autonomous
kernel/skill-execution path never consulted it. See MASTER_PLAN.md's "P2.5 — Runtime
Convergence" and DECISIONS.md's "Identity publishes a declarative Identity Context..." entry
for the full rationale.

**Identity Context** (`identity_interpreter/identity_context.py`) — produced by
`identity_interpreter`, declarative only (answers "who is Bartholomew," never "what should I
do right now"): `core_values`, `red_lines`, `tool_use_default_allowed`,
`tool_use_allowlist`, `tool_use_consent_prompts`.

**Policy Decision** (`bartholomew/kernel/policy_engine.py`) — constructed by the Executive via
`evaluate_tool_policy(context, tool_name)` from an Identity Context, not by Identity itself
and not by the Kernel parsing `Identity.yaml` directly.

**Consumers:** `SkillRegistry.execute_action()` consults `evaluate_tool_policy()` via an
optional `identity_context` constructor param (`None` by default — no behavior change unless
wired). `daemon.py`'s optional `identity_path` param loads `Identity.yaml` once via
`identity_interpreter.loader.load_identity()` and shares the built context; the live API
bridge (`bartholomew_api_bridge_v0_1/services/api/app.py`) passes
`identity_path="Identity.yaml"` by default.

**Chat, updated 2026-07-21 (item 11.6):** `bartholomew/kernel/runtime_contract.py`'s Governance
stage — the seam `/api/chat` routes through when the kernel is running (item 11.4) — now also
consults `evaluate_tool_policy(daemon.identity_context, candidate_action.kind)`, with one
exemption: `CandidateAction` kinds in `_CONVERSATIONAL_KINDS` (currently just
`"chat_response"`, the only kind the chat seam produces today) skip the check, since plain
conversation isn't a "tool" in `tool_use.allowlist`'s sense. A future tool/skill-shaped
candidate action proposed *during* a chat turn (a kind outside that exempt set) would be
evaluated for real. `identity_interpreter/orchestrator/orchestrator.py`'s own `handle_input()`
(used directly as `respond_fn`'s backend, and also as the fallback path when the kernel isn't
running) still has only its separate, parking-brake-only check — it does not consult the shared
Policy Decision itself; only the Runtime Contract seam wrapping it does.

**Scheduler drives, updated 2026-07-23 (item 11.17):** `scheduler/loop.py`'s `_run_drive()` now
delegates to `bartholomew.kernel.runtime_contract.run_drive_through_runtime_contract()`, which
also consults `evaluate_tool_policy(ctx.identity_context, task_id)` — with the same shape of
exemption as chat's: task_ids in `_SELF_MAINTENANCE_DRIVES` (`self_check`, `curiosity_probe`,
`reflection_micro`, `fts_optimize` — today's full `scheduler/drives.py` `REGISTRY`) skip the
check, since these are kernel self-maintenance functions, not "tools" in
`tool_use.allowlist`'s sense. An *earlier* attempt (before this exemption existed) wired every
drive's `task_id` into the check unconditionally and it was a real production regression
(denied every drive by default, busy-looped the scheduler, and starved the event loop badly
enough that the live app never answered `/healthz`) — see DECISIONS.md's "`tool_use.allowlist`
gates skill/capability execution, not scheduler drives" entry for that incident, and "Scheduler
drives get Identity-derived gating via a category exemption..." for how it was corrected. A
future scheduler-originated action outside `_SELF_MAINTENANCE_DRIVES` (e.g. a drive that acts
on the user's behalf) would be evaluated for real.

See `tests/test_runtime_convergence_policy.py`,
`tests/test_runtime_contract_chat_seam.py::TestChatGovernanceConsultsPolicyDecision`, and
`tests/test_scheduler_drive_convergence.py`.
