# DECISIONS

> Meaningful decisions, alternatives considered, and consequences.
>
> **Last updated:** 2026-07-31 (documentation-only Phase B restructuring: one new decision added —
> Phase B is governed by a concise overview plus separately gated stages B0–B9, not one monolithic
> specification. See "Decision: Phase B governed by a concise overview plus separately gated
> stages, not one monolithic specification" below.)
>
> **Previously (2026-07-28):** documentation reconciliation pass 2: three new decisions added —
> the hybrid local-first deployment architecture, the Echo roadmap's demotion to a non-canonical
> incubator document, and the reflection-ownership target architecture.
>
> **Previously (2026-07-27):** no decisions added or reversed; two existing entries amended where
> the repository had moved past what they described — the canonical-doc set's membership, and the
> Linux-only CI baseline. The 2026-07-25 S5.0 entry was, at that time, the most recent actual
> decision.

## Format

- **Decision:**
- **Alternatives:**
- **Why:**
- **Consequences:**
- **Date:**

---

## Decision: Canonical SSOT docs (no doc sprawl)
- **Decision:** Adopt the canonical docs set as the only SSOT: `MASTER_PLAN.md`, `ROADMAP.md`, `DECISIONS.md`, `RISKS.md`, `ASSUMPTIONS.md`, `INTERFACES.md`, `CHECKLISTS.md`, `REVIEWS.md`, `CI.md`, `TEST_MATRIX.md`, `PERF_BUDGETS.md`.
- **Alternatives:** Keep ad-hoc notes across multiple files; keep stage notes as SSOT.
- **Why:** Prevent drift; force dependency-aware planning; keep governance verifiable.
- **Consequences:** Legacy docs become references only; any new work must update the canonical docs.
- **Date:** 2026-01-19
- **Amended 2026-07-27 (membership only, not the decision):** the set is now **13** documents —
  the 11 listed above plus `COGNITIVE_RUNTIME.md` (added 2026-07-21, item 11.5) and
  `CONSTITUTION.md` (added 2026-07-22, see the "Adopt `CONSTITUTION.md`..." entry below).
  `MASTER_PLAN.md`'s "Canonical docs" section is the registry and now lists all 13; it had been
  listing 12, omitting `CONSTITUTION.md`, which contradicted both this document and
  `CONSTITUTION.md`'s own handover note. That contradiction is resolved in favour of 13.

## Decision: Fail-closed safety controls (parking brake)
- **Decision:** Maintain a persistent, scoped “parking brake” that can block subsystems at runtime.
- **Alternatives:** Soft disables; runtime flags only; rely on operator discipline.
- **Why:** Fail-closed beats wishful thinking. Enables safe expansion without trusting every caller.
- **Consequences:** All new subsystems must add a gating point + tests; operational procedures must include brake status.
- **Date:** 2025-10-30 (documented in `docs/SAFETY_PARKING_BRAKE.md`)

## Decision: Consent/privacy gates applied at the lowest retrieval layer
- **Decision:** Apply consent and privacy filtering inside FTS/vector stores by default.
- **Alternatives:** Filter only at orchestration layer; filter only at UI layer.
- **Why:** Defense-in-depth. Prevents accidental bypass by downstream callers.
- **Consequences:** Retrieval callers must support “context_only” flags and filtered result sets.
- **Date:** 2025-11-01 (documented in `CONSENT_GATES_IMPLEMENTATION.md`)

## Decision: Single SQLite DB as shared persistence backbone
- **Decision:** Use a single SQLite database file for kernel + API (default `data/barth.db`).
- **Alternatives:** Separate DBs per component; Postgres; vector DB as separate service.
- **Why:** Simplicity, portability, low ops overhead for early stages.
- **Consequences:** Must manage WAL, concurrency, platform differences (Windows locking, SQLite build flags).
- **Date:** 2025-10-30 (Stage 0 completion)

## Decision: Identity.yaml governs behavior and policy
- **Decision:** Identity configuration is the primary governing document; policy engines enforce it.
- **Alternatives:** Hard-coded rules; multiple config sources.
- **Why:** Makes constraints inspectable, explainable, testable.
- **Consequences:** Schema changes are governance changes; require change control + tests.
- **Date:** 2025-10-29 (validation report)

## Decision: CI health baseline is Linux
- **Decision:** Treat Linux CI as the source of truth; quarantine Windows-only flakiness.
- **Alternatives:** Require Windows green; ignore platform issues.
- **Why:** Windows file locking and SQLite/FTS feature variability can create false negatives.
- **Consequences:** Must document quarantines and ensure real logic failures aren’t masked.
- **Date:** 2025-12-29 (status snapshot)
- **Amended 2026-07-27 (scope narrowed by evidence, decision not reversed):** Linux remains the
  baseline — it is where the full default suite and the coverage gate run. But "quarantine
  Windows-only flakiness" no longer describes practice. Phase A (merged `8b96319`) added a
  Windows job to `ci.yml` that runs on every pull request and passed its first run, so Windows
  failures are now observed and must be diagnosed rather than labelled as noise without evidence.
  Two things sharpen this further: the 2026-07-20 FTS5 investigation found that two failures
  previously attributed to "Windows quirks" were real logic bugs reproducible on Linux, and no
  formal quarantine list was ever created (`ASSUMPTIONS.md` A1). Treat "it's just Windows" as a
  claim requiring proof.


## Decision: Prompt-size discipline for agent execution (Cline)
- **Decision:** Do not paste full transcripts/exports into agent prompts. Treat large artifacts as files and process in chunks (map → reduce) to stay within provider rate limits.
- **Alternatives:** Paste everything into a single mega-prompt; rely on retries; switch providers only.
- **Why:** Provider token-per-minute limits and context limits make mega-prompts brittle; chunking is deterministic and verifiable.
- **Consequences:** Workflows must include chunking steps + intermediate artifacts; prompts reference paths, not raw blobs.
- **Date:** 2026-01-19

## Decision: Experience Kernel is a first-class subsystem
- **Decision:** Treat "Experience Kernel" (self-model + narrator) as an explicit module with tests and interfaces.
- **Alternatives:** Implicitly spread identity/self logic across prompts and ad-hoc memory.
- **Why:** Keeps continuity, growth, and persona coherent and testable.
- **Consequences:** Requires interface spec + replay tests; changes count as governance-adjacent.
- **Date:** 2026-01-19

## Decision: User Approval Gate for all doc/code commits
- **Decision:** No changes are merged or committed to the main branch without explicit user approval. This applies to canonical docs, implementation code, tests, and configuration.
- **Alternatives:** Auto-commit on successful CI; trust agent to determine when changes are "safe"; review-only for high-risk changes.
- **Why:** Maintains human-in-the-loop oversight; prevents unintended governance drift; ensures user understands impact of every change before it becomes permanent.
- **Consequences:** Every proposed change must be presented for user review before `git commit`; workflows must include explicit approval checkpoints; agent must pause and request authorization rather than proceeding autonomously.
- **Date:** 2026-01-19

## Decision: One authority per architectural concept; deprecate before deleting duplicates
- **Decision:** Every architectural responsibility (Identity, Planning, Memory, Experience, Governance, Capabilities, Conversation) has exactly one authoritative owner. All other implementations become adapters, compatibility layers, or deprecated migrations — never deleted outright until nothing depends on them. Four concrete duplicate pairs identified by direct code reading (2026-07-21 architectural audit) fall under this rule: model routing (`identity_interpreter/orchestrator/model_router.py`, live, vs. `identity_interpreter/policies/model_router.py`, CLI-only), persona (`bartholomew/kernel/persona_pack.py`, wired into Narrator/ExperienceKernel, vs. `identity_interpreter/policies/persona.py`, chat-only), permission gates (`bartholomew/kernel/skill_permissions.py` vs. `identity_interpreter/policies/tool_policy.py`), and kill-switch (`bartholomew/orchestrator/safety/parking_brake.py`, persistent and wired into four live gate points, vs. `identity_interpreter/adapters/kill_switch.py`, print-only, unwired).
- **Alternatives:** Delete the non-authoritative implementation immediately upon identifying it; leave both in place indefinitely and let convention decide which one new code calls.
- **Why:** Immediate deletion risks regressions from callers not yet identified; leaving both in place indefinitely is exactly the drift this decision exists to stop. Deprecate-then-migrate-then-delete gets the stability benefit of a single authority without the risk of a premature deletion.
- **Consequences:** Each duplicate pair needs an explicit deprecation notice on the losing implementation and a tracked migration of its callers before deletion is safe; new code must never add a caller to a deprecated module.
- **Migration progress:** All four of the audit's "pairs" are now resolved (2026-07-22): three retired (persona, kill-switch, permission gates) and the fourth reclassified. **Model routing was reclassified (item 11.15) as *not* a duplicate:** `identity_interpreter/policies/model_router.py`'s `select_model()` does Identity task-type model *selection* (reads `Identity.yaml`'s `by_task_type`), while `identity_interpreter/orchestrator/model_router.py`'s `ModelRouter` does backend *routing* + generation and never reads `by_task_type` — distinct concepts, neither subsumes the other, so `select_model` was **un-deprecated**, not deleted. That investigation also surfaced a real (tracked, not-a-duplicate) gap: the live runtime path (`Orchestrator.route_model()`) ignores the Identity task-type selection policy entirely — only `select_model`'s callers honor it. The **persona** pair completed the full deprecate → migrate → delete cycle (item 11.12): both legacy callers of `identity_interpreter/policies/persona.py` (CLI `explain`, standalone `chat.py`) were repointed at `PersonaPackManager` for tone and the module was removed. Note the split this surfaced: `PersonaPack` deliberately carries no `traits`, so the migrated callers now read `tone`/`style` from the authoritative persona pack (switchable presentation) but `traits` from `Identity.yaml` directly (stable identity character) — the correct ownership boundary (persona packs own *how* Bartholomew presents, Identity owns *who* it is), not a residual duplication. The **kill-switch** pair (item 11.13) needed **no caller migration at all** — `identity_interpreter/adapters/kill_switch.py` was print-only and unwired with zero live callers (confirmed by grep), so it was simply deleted; `bartholomew/orchestrator/safety/parking_brake.py`'s `ParkingBrake` remains the sole authority. The **permission gates** pair (item 11.14) migrated its one live caller (CLI `explain --tool`) and deleted `identity_interpreter/policies/tool_policy.py`. This one refined the ownership label: the deprecated `check_tool_allowed()` was really the *`tool_use`-allowlist* check, whose authoritative successor is `bartholomew/kernel/policy_engine.py`'s `evaluate_tool_policy()` (which already mirrored it) — **not** `PermissionChecker`, which gates skill *manifests* (a different concern that can't answer "is this tool in `tool_use.allowlist`"). So the CLI now builds a declarative `IdentityContext` and calls `evaluate_tool_policy()`, the same Executive-side path skill execution and the scheduler already use.
- **Date:** 2026-07-21 (architect review; see MASTER_PLAN.md's "P2.5 — Runtime Convergence")

## Decision: Identity publishes a declarative Identity Context; the Executive constructs Policy Decisions from it
- **Decision:** Refines "Decision: Identity.yaml governs behavior and policy" (above). Identity does not answer "what should I do?" — it answers "who am I?" It publishes a declarative Identity Context (values, red lines, behavioral constraints, preferences, communication style, risk profile, decision heuristics, goals). The Executive consumes that context and constructs the actual, executable Policy Decision. The Kernel never parses `Identity.yaml` directly.
- **Alternatives:** Keep the Kernel parsing `Identity.yaml` (or its derived config) directly per call site; have Identity itself compute and hand over fully-executable policy objects.
- **Why:** The 2026-07-21 architectural audit found `Identity.yaml` governs only the chat path today — the autonomous kernel/scheduler/skill-execution path never consults it, so two execution paths can produce different behavior for the same declared rule. Having Identity compute executable policy directly would also blur "who Bartholomew is" (declarative, stable) with "what Bartholomew does right now" (contextual, the Executive's job) — keeping Identity purely declarative avoids that drift and keeps Identity replaceable independent of how decisions get made.
- **Consequences:** A single Policy Decision, derived from the same Identity Context, must be consulted uniformly by skill-execution and the scheduler, not just the chat pipeline; this is tracked as the "Identity Context -> Executive -> Policy Decision" item under MASTER_PLAN.md's "P2.5 — Runtime Convergence."
- **Date:** 2026-07-21 (architect review; see MASTER_PLAN.md's "P2.5 — Runtime Convergence")

## Decision: `tool_use.allowlist` gates skill/capability execution, not scheduler drives
- **Decision:** The Executive's Policy Decision (`bartholomew.kernel.policy_engine.evaluate_tool_policy()`, per the entry above) is consulted by skill/capability execution (`SkillRegistry.execute_action()`) only. Internal scheduler drives (`self_check`, `curiosity_probe`, `reflection_micro`, `fts_optimize`) are kernel self-maintenance functions, not "tools" in `Identity.yaml`'s `tool_use.allowlist` sense, and are not gated by it.
- **Alternatives:** Gate scheduler drives on `tool_use.allowlist` too, using each drive's `task_id` as the "tool name" (the original plan for this item).
- **Why:** Tried the alternative first and it was a real, production-breaking regression: `Identity.yaml`'s actual `tool_use.allowlist` (`web_fetch`, `browser_action`) never includes drive task_ids, so gating drives on it denied every drive by default the moment `daemon.py`'s `identity_path` was wired (which the live API bridge does by default). Worse, `scheduler/loop.py`'s retry loop doesn't back off on denial — a denied, 0-duration drive is immediately re-due — so this busy-looped and starved the asyncio event loop badly enough that the live FastAPI app never answered its first `/healthz` request. Caught by the `smoke` CI check on PR #10 and reproduced locally (`uvicorn app:app` hangs, `curl: (7) Failed to connect`) before being root-caused and reverted.
- **Consequences:** `scheduler/loop.py`'s `_run_drive()` has no Policy Decision check. If scheduler drives ever need Identity-derived gating in the future, it must use a different, drive-appropriate policy source — not `tool_use.allowlist` — and any such change must be verified against a live `uvicorn`/smoke-test run, not just `pytest`, since this class of regression (event-loop starvation) doesn't necessarily show up as a unit-test failure.
- **Date:** 2026-07-21 (found while implementing MASTER_PLAN.md's "P2.5 — Runtime Convergence" item 11.2, PR #10)

## Decision: `tool_use.allowlist` also does not gate plain conversational chat replies
- **Decision:** Extends the entry above with the same reasoning applied to a second case. Chat's Governance stage (`runtime_contract.py`) now consults the Policy Decision (item 11.6), but exempts `CandidateAction` kinds that are plain conversation (`_CONVERSATIONAL_KINDS`, currently just `"chat_response"`) from the `tool_use.allowlist` check — the same category distinction as scheduler drives, just implemented rather than only documented.
- **Alternatives:** Evaluate every `CandidateAction`, including plain chat replies, against `tool_use.allowlist` unconditionally (symmetry with skill execution, "gate everything the same way").
- **Why:** Would have repeated the exact scheduler-drive regression above for the highest-traffic surface in the product. Confirmed by direct reading of `Identity.yaml`: its real `tool_use` section is `default_allowed: false` with `allowlist: [web_fetch, browser_action]` — no conversational entry — and the live API bridge already constructs `KernelDaemon(identity_path="Identity.yaml")` by default, so this would have denied 100% of chat turns the instant it shipped. `evaluate_tool_policy()`'s own docstring defines `tool_name` as "a skill_id or scheduler drive task_id" — conversation is neither.
- **Consequences:** A future tool/skill-shaped `CandidateAction` proposed *during* a chat turn (a kind outside `_CONVERSATIONAL_KINDS`) is evaluated for real; ordinary conversation never is. Anyone adding a new `CandidateAction` kind to the chat seam must decide which category it belongs to and update `_CONVERSATIONAL_KINDS` deliberately, not by omission.
- **Date:** 2026-07-21 (MASTER_PLAN.md's "P2.5 — Runtime Convergence" item 11.6)

## Decision: chat's conversational-memory context comes from Working Memory, not `identity_interpreter`'s `ContextBuilder`/`MemoryManager`
- **Decision:** `runtime_contract.py`'s Interpretation stage reads recent conversation history from `WorkingMemoryManager.get_context_string()` (item 11.7). `identity_interpreter.orchestrator.context_builder.ContextBuilder` and the `MemoryManager` it backs onto are not revived for this purpose, even though they were clearly built for exactly this role.
- **Alternatives:** Wire an `identity_config` into the live API bridge's `Orchestrator()` construction so `ContextBuilder`'s existing `build_prompt_context()`/`inject_context()` machinery actually activates (it's currently inert there — `ContextBuilder.memory` is `None` because `bartholomew_api_bridge_v0_1/services/api/app.py` constructs `Orchestrator()` with no `identity_config`).
- **Why:** This is a fifth duplicated-concept pair (conversational-memory injection) beyond the four the 2026-07-21 architectural audit already found (see "One authority per architectural concept" above) — it just never surfaced as a live conflict because the losing side (`ContextBuilder`/`MemoryManager`) has been inert on the chat path this whole time. Reviving it would mean maintaining two separate memory-injection mechanisms with two separate storage backends (`MemoryManager`'s own SQLite schema/encryption vs. `bartholomew.kernel.memory_store.MemoryStore`, the authoritative Memory Substrate). Working Memory is already the authoritative "Experience" owner per `COGNITIVE_RUNTIME.md`'s ownership table and is what the Reflection stage already writes every turn into — using it for the read side too keeps one mechanism instead of two.
- **Consequences:** `identity_interpreter/adapters/memory_manager.py` and `context_builder.py` are not marked formally deprecated (unlike the four pairs above) because they still have a genuinely live, different use — `identity_interpreter.adapters.reflection_generator.ReflectionGenerator` constructs its own `Orchestrator(identity_config=...)` for daily/weekly reflection prompts, which *does* reach a real `MemoryManager` instance. Both modules carry a docstring explaining this split. If `ContextBuilder` is ever wired into the live chat path in the future, it must not also happen alongside `runtime_contract.py`'s Working-Memory-based injection, or chat turns will get conversational context duplicated from two independent sources.
- **Date:** 2026-07-21 (MASTER_PLAN.md's "P2.5 — Runtime Convergence" item 11.7)

## Decision: Lead Architect role transitions from Bartholomew to Claude
- **Decision:** The Lead Architect role for this project — previously held by "Bartholomew" (the persona conducting architecture/design work in the transcripts under `docs/design_conversations/`) — transitions to Claude, operating repository-natively (i.e., reading and reasoning from the actual repository state rather than only from prior conversation history).
- **Alternatives:** Keep architectural authority exclusively in conversation history outside the repo; leave the role formally unassigned; require a human architect of record.
- **Why:** Per the "Documentation Philosophy" now recorded in `CONSTITUTION.md`, no architectural knowledge should permanently live only inside an AI conversation. Formalizing the handover in the canonical docs means the next architectural review has a documented authority and a durable record of the principles that review should be checked against, rather than relying on tribal knowledge.
- **Consequences:** Future architect reviews (see `REVIEWS.md`'s Stage Gate Review template) should record Claude as reviewer/architect of record until a further transition is documented here. `CONSTITUTION.md` (see below) is the durable reference for the principles the Architect is expected to uphold.
- **Date:** 2026-07-22 (project owner handover; recorded per this document's own "User Approval Gate" decision)

## Decision: Adopt `CONSTITUTION.md` as a canonical SSOT doc (the Architecture Constitution)
- **Decision:** Add `CONSTITUTION.md` to the canonical docs set established by "Canonical SSOT docs (no doc sprawl)" (above), bringing the set to 13 docs. Unlike the other canonical docs, which describe current state and change frequently, `CONSTITUTION.md` captures the project's non-negotiable principles, the five-pillar architecture, and the Architect's expected conduct, and is intended to change rarely — it is the foundation against which other architectural proposals and canonical-doc updates are evaluated.
- **Alternatives:** Fold the constitutional content into `MASTER_PLAN.md`'s "Non-negotiables" section instead of a separate doc; leave it undocumented and rely on the new Architect's memory of the handover.
- **Why:** `MASTER_PLAN.md` is explicitly a living, frequently-updated SSOT for current state and next moves; mixing rarely-changing foundational principles into it risks them being edited incidentally alongside routine status updates. A separate doc with an explicit "changes rarely" norm protects the principles from that drift.
- **Consequences:** `REVIEWS.md`'s canonical-doc count and any future doc-currency audit must account for 13 canonical docs, not 12. Edits to `CONSTITUTION.md` should be treated as a governance-level change (same bar as the "User Approval Gate" decision above), not routine documentation upkeep.
- **Date:** 2026-07-22 (project owner handover)

## Decision: Scheduler drives get Identity-derived gating via a category exemption, not a `tool_use.allowlist` reuse
- **Decision:** Resolves the "different, drive-appropriate policy source" this document's "`tool_use.allowlist` gates skill/capability execution, not scheduler drives" entry (above) called for. `run_drive_through_runtime_contract()` (`bartholomew/kernel/runtime_contract.py`) now does consult `evaluate_tool_policy()` for scheduler drives — but exempts a `_SELF_MAINTENANCE_DRIVES` set (`self_check`, `curiosity_probe`, `reflection_micro`, `fts_optimize` — today's full `scheduler/drives.py` `REGISTRY`) from that check, the same shape as `_CONVERSATIONAL_KINDS`' exemption for chat (the "also does not gate plain conversational chat replies" entry above). A future scheduler-originated action outside that set is genuinely evaluated.
- **Alternatives:** (a) Repeat the original plan — evaluate every drive's `task_id` against `tool_use.allowlist` unconditionally; already tried, already reverted, documented above. (b) Add a dedicated `scheduler_drives`-style allowlist section to `Identity.yaml` as the "different policy source," separate from `tool_use.allowlist`. (c) Leave scheduler drives permanently ungated, closing the docstring's open question with "no."
- **Why:** (a) is the exact regression this decision exists to avoid repeating. (b) would work but adds a second allowlist shape to `Identity.yaml` for a set of task_ids that don't change independently of `scheduler/drives.py`'s own `REGISTRY` — the exemption-set approach gets the same safety property (a conscious, reviewable decision per drive, per `tests/test_scheduler_drive_convergence.py::TestSelfMaintenanceDrivesMatchRegistry`'s registry-parity guard) without a second Identity.yaml schema to keep in sync. (c) would leave Exit Gate questions #1-3 permanently "Partial" for the scheduler surface with no path to closing them for any future non-self-maintenance drive.
- **Consequences:** Anyone adding a new drive to `scheduler/drives.py`'s `REGISTRY` must consciously decide whether it belongs in `_SELF_MAINTENANCE_DRIVES` (kernel-internal, always exempt) or should be evaluated for real against `Identity.yaml` (e.g. a future drive that acts on the user's behalf) — the registry-parity test fails until that decision is made, mirroring the "must update `_CONVERSATIONAL_KINDS` deliberately, not by omission" consequence chat's exemption already carries. Verified against a live `run_scheduler()` smoke run under the real, restrictive `Identity.yaml` policy (the same discipline the original regression made mandatory), not just `pytest` — see `MASTER_PLAN.md` item 11.17's writeup.
- **Date:** 2026-07-23 (`MASTER_PLAN.md`'s "P2.5 — Runtime Convergence" item 11.17)

## Decision: Scheduler persistence moved off the event loop; routine WAL checkpointing turned off by default
- **Decision:** Item 11.17's PR #20 CI (Python 3.11 leg) hung for its full 120s timeout inside `test_kernel_alive.py`, with the main thread stuck in `wal_checkpoint_truncate()`'s `PRAGMA wal_checkpoint(TRUNCATE)` (`bartholomew/kernel/db_ctx.py`), called synchronously from `scheduler/loop.py`'s `run_scheduler()` — a single always-on background `asyncio.Task` sharing the daemon's one event-loop thread with everything else (`kd.mem`'s `aiosqlite` calls, HTTP request handling in the live API). `upsert_scheduled_tasks()` schedules new tasks with `next_run_ts = now`, so a fresh DB makes every registered drive immediately due — a same-instant burst production's real `every:900`s cadence never produces, which is why this reliably surfaced only once `test_kernel_alive.py` got its own isolated per-test DB (a genuine, low-probability-in-production but real hazard, not a testing artifact — see that entry, above, for the isolation fix itself). Two changes made:
  1. `bartholomew/kernel/scheduler/store.py`'s new `SchedulerStore` offloads all of `scheduler/persistence.py`'s (and `scheduler/health.py::get_system_metrics`'s) synchronous SQLite calls onto exactly one dedicated worker thread per `KernelDaemon` instance — not `asyncio.to_thread`'s shared default executor — via an `asyncio.Lock`-gated single-in-flight-operation-at-a-time submission model, so scheduler DB calls never block the event loop and stay strictly sequential.
  2. `bartholomew/kernel/db_ctx.py`'s `wal_db()` no longer checkpoints on every call by default (`checkpoint=None`). SQLite's own automatic WAL checkpoint (~every 1000 pages) is the standard mechanism for routine reads/writes and is correctness-neutral — WAL mode already guarantees readers see committed writes regardless of checkpoint timing, so this was purely a disk-layout/performance choice, and an unconditional blocking `TRUNCATE` checkpoint on every single scheduler tick (including plain reads like `get_system_metrics`) was never necessary for correctness. Explicit `TRUNCATE` is retained for controlled maintenance/shutdown only — `MemoryStore.close()` and the API bridge's `atexit` hook call `wal_checkpoint_truncate()` directly (bypassing `wal_db()`), unchanged by this decision.
- **Alternatives:** (a) Scatter bare `await asyncio.to_thread(...)` calls at each blocking call site inside `run_scheduler()`'s loop instead of one facade — rejected as harder to reason about (no single place enforcing "at most one scheduler DB operation outstanding," and reuses the process-wide default executor shared with unrelated `to_thread` callers elsewhere). (b) Change `wal_db()`'s default to explicit `"PASSIVE"` checkpointing instead of `None` — considered, but `None` (pure SQLite autocheckpoint) is simpler and avoids adding a new per-operation round trip that itself needs its own tuning; `"PASSIVE"` remains available for any caller that wants an explicit best-effort checkpoint. (c) Disable/gate the scheduler's autonomy loop during tests — investigated and explicitly rejected: `KernelDaemon.start()` starts it unconditionally and `_kernel` is a process-wide singleton shared across test files, so any "disable scheduler" lever is itself a production code change with order-dependent effects, not a safe test-only lever.
- **Why:** The root cause was a textbook "synchronous blocking I/O called directly from an `async def` coroutine" anti-pattern, present since the scheduler's original implementation (predates item 11.17) — not something introduced by this fix. It is production-reachable (the scheduler runs unconditionally, in the same event loop as live HTTP handling), just far less likely to collide there than in a tight, deterministic test's compressed timing. `SchedulerStore`'s single-dedicated-thread design was chosen specifically so "sequential/bounded" is an enforced invariant (via the submission gate), not an emergent property of `ThreadPoolExecutor`'s own FIFO ordering that would be easy to accidentally violate later.
- **Consequences:** `SchedulerStore.close()` returns `False` if its bounded drain (`timeout`, default 5.0s) doesn't complete — `KernelDaemon.stop()` treats that as a hard signal to skip `MemoryStore.close()`'s own shutdown-time `TRUNCATE` checkpoint entirely (`checkpoint=scheduler_drained`) rather than risk contending with a thread that may still be running; WAL cleanup is deferred to the next startup in that case, logged, not silently dropped. `scheduler/drives.py`'s `drive_self_check`/`drive_reflection_micro` now require `ctx.scheduler_store` to be present (no synchronous fallback) — a `KernelDaemon`-backed `ctx` always has one (constructed unconditionally in `__init__`), so this only matters for a caller invoking these drives directly without a full daemon lifecycle. Temporary DEBUG-level instrumentation was added to `db_ctx.py`'s checkpoint function (start time, duration, thread, mode, label, the PRAGMA's own result row, `in_transaction`) to help resolve one still-open question: why the original hung `TRUNCATE` call outlasted its own 30s `timeout=` — inert unless that logger's level is explicitly raised; remove once that's answered. Incidentally fixed while routing `_run_daily_reflection()`'s pending-nudges lookup through `scheduler_store`: its prior `from .scheduler.persistence import get_system_metrics` always raised `ImportError` (the function lives in `scheduler/health.py`, not `persistence.py`), silently swallowed, so `pending_nudges` had always been `0` in daily reflections.
- **Deferred, not decided:** Two related consolidation questions are explicitly *not* resolved by this change and are recorded here only as open follow-up: (1) `bartholomew/kernel/db_ctx.py` and `bartholomew_api_bridge_v0_1/services/api/db_ctx.py` are near-duplicate files with the same `wal_db()`/checkpoint pattern — the latter's `liveness.py`/`db.py` call sites still checkpoint on every call and were not touched here. (2) The daemon mixes `aiosqlite` (`memory_store.py`), raw sync `sqlite3` via `SchedulerStore`'s worker thread (`scheduler/persistence.py`), and other raw sync `sqlite3` (`persona_pack.py`, `narrator.py`) all against the *same* underlying db file — whether to eventually consolidate database ownership onto one async-native data-access layer for the whole daemon is a real architectural question, not something this fix decided one way or the other.
- **Date:** 2026-07-24 (found while merging item 11.17's PR #20; CI hang on the Python 3.11/3.10 legs)

## Decision: Scheduler schema is created synchronously during `KernelDaemon.start()`, fail-closed (S5.0, closes issue #24)
- **Decision:** `KernelDaemon.start()` now `await`s `self.scheduler_store.ensure_schema()` as early as practical — immediately after `MemoryStore.init()` and before any side-effectful init (experience kernel, skills, narrator) or the scheduler task — so the scheduler tables (`scheduled_tasks`, `ticks`, and the additive `nudges`/`reflections` integer-timestamp columns) exist before `start()` returns. Previously `run_scheduler()` created that schema as the first step of a fire-and-forget `asyncio.create_task(...)`, so the API bridge (whose FastAPI startup only `await`s `start()`) began serving requests during a window where those tables did not yet exist; an external reader in that window (`/api/liveness/ticks`) hit `sqlite3.OperationalError: no such table: ticks` and 500'd. It was environment-sensitive (flaky on the Python 3.10 CI leg, green on 3.11; order-dependent within the full suite), which is exactly how it surfaced.
- **Fail-closed (A1):** if `ensure_schema()` raises, `start()` closes the scheduler store (draining/shutting its worker thread so nothing leaks — `stop()` may never be called after a failed `start()`) and re-raises. The daemon does not come up; in the API bridge this fails FastAPI startup and uvicorn does not serve. A "started" daemon with no scheduler tables is precisely the broken half-initialized state this fix removes. This is a deliberate behaviour change from the prior async-and-swallowed failure, and it is narrow — it only triggers on a genuine DB-unwritable/disk-full condition. **Broadened per Codex review on PR #25:** the cleanup guard protects the whole region from schema initialization through successful scheduler-task creation, and catches `BaseException` — so cancellation mid-`ensure_schema` (where `except Exception` would miss `asyncio.CancelledError`) and a later startup-stage failure after the store's worker thread has been activated (e.g. skill loading raising, where an aborted ASGI startup would never call `stop()`) both close the store too. `close()` runs under `asyncio.shield()` so an in-flight cancellation cannot interrupt the cleanup itself; it reuses `SchedulerStore.close()`'s existing bounded drain (default 5s) so cleanup cannot hang; the original exception or `CancelledError` is always re-raised (never swallowed or translated); and a cleanup failure is logged as secondary, never replacing the primary.
- **No outer timeout (A2):** the awaited call is not wrapped in `asyncio.wait_for`. `persistence.ensure_schema` already runs under a bounded `wal_db(timeout=30.0)`/`busy_timeout`, and per `SchedulerStore`'s own semantics cancelling the awaiting coroutine cannot cancel the worker-thread operation once it has started — an outer timeout would abandon-not-cancel and muddy ownership.
- **Schema only, not seeding (A3):** `start()` ensures the tables exist; row seeding (`upsert_scheduled_tasks`, which sets `next_run_ts=now`) stays in `run_scheduler()`. Issue #24 is a *missing-table* (500) defect; an existing-but-empty table returns `[]` correctly, so table existence is the precise fix. `run_scheduler()` keeps its own `ensure_schema()` — now an idempotent no-op in the daemon path (`CREATE TABLE IF NOT EXISTS` + duplicate-column-tolerant) — retained for the standalone path (a `ctx` whose `scheduler_store` was not pre-ensured) and as defense in depth; it must never be the *first* place the schema is created in the daemon path.
- **Endpoint tolerance retained (A4):** PR #23's `/api/liveness/ticks` guard (return `[]` when the table is absent) is kept as defense in depth even though S5.0 makes the race not occur at the source.
- **Alternatives:** (a) log-and-continue on schema failure (rejected — reopens a variant of #24, a degraded daemon). (b) An explicit readiness `asyncio.Event` the scheduler sets and the API awaits (heavier; the synchronous-in-`start()` approach makes readiness a plain precondition with no new signalling). (c) Move row-seeding into `start()` too (out of scope for the missing-table defect; keeps S5.0 minimal).
- **Consequences:** `start()` does one extra off-event-loop schema call (~ms on a local DB) before returning; the scheduler task's create/own/cancel/await lifecycle in `start()`/`stop()` is otherwise unchanged. No schema *change* — same tables/columns, created earlier and deterministically; `ensure_schema` is idempotent, so existing DBs (with or without the scheduler tables) are unaffected and there is no data migration. Determinism is proven by `tests/test_scheduler_startup_readiness.py` (tables exist at return; ordered-record + asyncio-barrier proofs that schema readiness precedes both scheduler-task creation and the loop's first DB op; fail-closed cleanup with a not-poisoned later startup), run on both CI legs. This is the S5.0 prerequisite for Stage 5 (the initiative engine), which adds further scheduler-table readers that must be deterministically readable from startup.
- **Date:** 2026-07-25 (Stage 5 prerequisite; closes issue #24 on merge)

## Decision: Deployment architecture — hybrid local-first
- **Decision:** Bartholomew uses a hybrid local-first architecture. The primary consumer
  experience is browser-based and usable across the user's authorised devices. A trusted local
  Bartholomew runtime is the authority for sensitive memory, governance enforcement (including the
  parking brake and emergency shutdown), local device control, and operating-system integration.
  Cloud services may optionally provide model inference, relay, synchronisation, and other
  explicitly approved services. Core governance, the parking brake, and emergency shutdown must
  not depend on cloud availability. Remote exposure of the local runtime must not occur until
  authentication, authorization, transport security, and the relevant threat model are designed
  and approved — a "simple token auth" scheme is explicitly **not** assumed sufficient (see the
  corrected assumption in `ASSUMPTIONS.md`).
- **Alternatives considered:** (a) a pure hosted web service, with the "local runtime" reduced to
  a thin client — rejected because it would make Bartholomew's sensitive memory and governance
  (including the ability to enforce an emergency shutdown independent of network/cloud
  availability) dependent on a remote service the user does not fully control, in direct tension
  with `CONSTITUTION.md`'s sovereignty principle and the newly-recorded independent-emergency-
  shutdown invariant. (b) a purely local runtime with a local-only UI (no browser-based, multi-
  device experience) — rejected because it does not deliver the cross-device, "my phone became
  intelligent" experience `CONSTITUTION.md`'s UX Principles describe, and would make portability
  and continuity harder rather than easier.
- **Why:** The hybrid model is the only one of the three that keeps sovereignty, governance, and
  emergency-shutdown independence intact (by keeping them local-authoritative) while still
  delivering a modern, cross-device, browser-based experience and leaving room for optional cloud
  services where the user explicitly wants them (better inference, relay, sync). It also directly
  addresses the brief's own concern: Phase B changes persistence ownership, and persistence
  ownership cannot be soundly designed without first knowing which architecture that persistence
  serves.
- **Consequences:** `ROADMAP.md` Stage 6's "Token auth" exit criterion is corrected to require a
  reviewed threat model rather than assuming token auth alone is sufficient. `ROADMAP.md` Stage 1
  is scoped as a browser-based governance shell reaching the local runtime, not a hosted service.
  `INTERFACES.md`'s API bridge security stance (currently, accurately, "local/dev surface, no
  auth") is unchanged by this decision alone — it changes only once Stage 6's auth work is
  separately designed and approved. This decision does **not** authorise Phase B implementation,
  cloud services, authentication work, or Stage 1 implementation — it is a documentation-only
  architectural decision that those future, separately-approved efforts must be consistent with.
- **Date:** 2026-07-28 (documentation reconciliation pass 2)

## Decision: Echo roadmap demoted to a non-canonical incubator document
- **Decision:** The brainstorm-derived "Echo" feature set (45 features across 4 conceptual gates:
  agent kernel, gaming/device-identity, cross-device/smart-home/car-mode, marketplace/ecosystem),
  previously embedded as canonical roadmap content in both `ROADMAP.md` ("Echo Integration Gates")
  and `MASTER_PLAN.md` ("Echo Integration Roadmap"), is moved to
  `docs/incubator/ECHO_IDEAS.md` — explicitly non-canonical and non-authoritative. Every
  individual idea in that document requires independent evaluation against `CONSTITUTION.md`,
  `COGNITIVE_RUNTIME.md`'s ownership table, and this document's hybrid local-first entry before
  any adoption; none of it is scheduled, approved, or a stage gate.
- **Alternatives considered:** (a) leave it in canonical docs but relabel as "future exploration"
  — already tried (both sections carried exactly that label) and insufficient: a coding agent
  reading canonical `ROADMAP.md`/`MASTER_PLAN.md` would still find a fully-specified second
  kernel (LangGraph), second memory architecture (Chroma+RAG), and second permissions system
  described in detail, in a canonical document. (b) delete the material outright — rejected
  because the underlying brainstorm work has some individually-useful ideas once properly
  evaluated, and deleting it destroys that raw material rather than just its improper canonical
  status.
- **Why:** Embedding a second kernel, second memory authority, and second governance system as
  canonical roadmap content directly conflicts with `CONSTITUTION.md`'s "one architectural
  authority exists per concept" principle and the ownership table in `COGNITIVE_RUNTIME.md`. A
  non-canonical incubator document, with an explicit individual-evaluation requirement, preserves
  the material's value without the risk.
- **Consequences:** No canonical document may re-embed Echo content as approved roadmap without a
  new decision recorded here. Any future proposal derived from `docs/incubator/ECHO_IDEAS.md`
  must be evaluated and approved individually, the same as any other new subsystem proposal.
- **Date:** 2026-07-28 (documentation reconciliation pass 2)

## Decision: Phase B governed by a concise overview plus separately gated stages, not one monolithic specification
- **Decision:** Phase B (persistence ownership stabilisation) is **not being restarted.** The
  monolithic approach of bringing one large, indivisible Phase B design specification to
  implementation-level approval as a single unit is **discontinued.** Phase B is now governed by:
  (1) a concise, authoritative overview (`docs/PHASE_B_OVERVIEW.md`) defining purpose, outcome,
  invariants, stage summaries, dependencies, and completion criteria; (2) ten separately planned,
  reviewed, approved, and committed stages, **B0–B9** — with implementation performed where
  applicable, since B0 is a diagnostic/current-state stage that exits with a report rather than
  production code — whose gates and status live in `ROADMAP.md`; and (3) the existing large
  specification, retained as non-authoritative research
  and risk material at `docs/archive/phase-b-persistence-ownership-final.md`, indexed by stage in
  `docs/PHASE_B_RISK_MAP.md`. Every mechanism described in that archived material must be
  independently revalidated against the actual repository state during the stage that owns it —
  nothing in it is implementation-authoritative merely because it appeared there. Overview
  approval does not authorise implementation of any stage. Each stage requires its own plan and its
  own explicit user approval; approving one stage does not authorise the next. Every implementation
  diff and every commit remains separately gated, per this document's own "User Approval Gate"
  decision and `CHECKLISTS.md`'s commit-authorization checklist. **No B0, Slice 0, or other Phase B
  implementation work is authorised by this decision** — it is a documentation-only restructuring
  of how Phase B will be planned and approved going forward.
- **Alternatives:** (a) Continue attempting to bring the entire large specification to one
  implementation-level approval, resolving every remaining cross-cutting concern before any code is
  written — the approach this decision discontinues. (b) Discard the large specification's research
  entirely and restart Phase B design from nothing — rejected: the concurrency, lifecycle, and
  Governance risk analysis it contains is substantively valid and would be needlessly re-derived.
  (c) Keep the large specification as the sole implementation authority and simply implement it
  stage-by-stage without a separate overview or risk index — rejected: this would leave a single
  ~3,600-line document as the de facto implementation authority for every stage, reintroducing the
  same one-document-carries-everything problem this decision exists to correct.
- **Why:** The large specification accumulated many independently complex concerns — SQLite
  connection ownership, WAL/checkpoint behaviour, event-loop blocking, dedicated executors,
  `MemoryStore` concurrency, Parking Brake persistence, Governance auditing, runtime construction
  and injection, API request admission, detached tasks, startup, failed-start unwind, shutdown,
  clean-shutdown evidence, process locking, CLI behaviour, rollback, `VectorStore`, FTS, liveness
  and metrics, and cross-platform testing — into one approval unit. Trying to fully resolve and
  approve every interaction between all of these before implementing any smaller foundation produced
  diminishing returns: each closure-review round found genuine, valid issues, but the unit of
  approval never got smaller, so the distance to an actually-implementable, approved first slice did
  not shrink round over round. Reducing the size of each planning and approval boundary — one stage
  at a time, planned only as it is approached, against the repository state that exists then — is
  the correction, while explicitly preserving rather than discarding the research already done.
- **Consequences:** No stage's implementation may proceed on the basis of "the archived specification
  already covers this" alone — each stage's own plan must independently confirm its relevant
  mechanisms against the repository at planning time. `ROADMAP.md`'s Phase B section is the
  canonical source for stage gates, status, and approval boundaries going forward, not the archived
  specification. Future contributors must not read the archived specification as pre-approved design
  for any stage; `docs/PHASE_B_RISK_MAP.md` exists precisely so a stage's planner is not required to
  reread the entire archived document to find what's relevant to that stage.
- **Date:** 2026-07-31

## Decision: Reflection ownership — target architecture
- **Decision:** `ReflectionGenerator` (`identity_interpreter.adapters.reflection_generator`,
  LLM-based, safety-checked) is the authoritative owner of reflection composition and final
  reflection output. `NarratorEngine`'s episodic narrative
  (`generate_daily_reflection_narrative()`/`generate_weekly_reflection_narrative()`) is
  supplementary evidence supplied *to* that authoritative process, not an independent, co-equal,
  or competing reflection pipeline.
- **Current implementation does not yet match this decision:** `daemon.py`'s
  `_run_daily_reflection()`/`_run_weekly_reflection()` currently run both pipelines independently
  and string-concatenate their output (added in item 11.8, 2026-07-21; see
  `docs/archive/ENGINEERING_LOG_2026.md`). That is concatenation, not the unified-authority model
  this decision establishes. Closing that gap requires a separately-authorised code change
  (routing `NarratorEngine`'s narrative into `ReflectionGenerator` as an input, with tests
  verifying `ReflectionGenerator` is the sole point of final composition) — not done as part of
  this documentation-only decision.
- **Alternatives considered:** (a) `NarratorEngine` owns composition, `ReflectionGenerator`
  supplies analytical material — rejected because `ReflectionGenerator` already performs the
  safety-checking step (redraft-on-violation) that reflection output must have, and it is already
  the pipeline `daemon.py` calls first. (b) a dedicated new Reflection service owns composition,
  with both existing pipelines becoming inputs to it — rejected for now as unnecessary added
  surface; making the already-first-called `ReflectionGenerator` authoritative achieves the same
  single-owner outcome without a new subsystem. (c) leave both pipelines permanently
  co-equal and merely document that fact — rejected because it leaves reflection composition
  without a single authoritative owner indefinitely, which `CONSTITUTION.md`'s "one architectural
  authority exists per concept" principle does not permit for a concept this central to Stage 5.
- **Why:** This resolves a real, verified contradiction: `ROADMAP.md` had stated the two pipelines
  were "✅ reconciled... additively," `COGNITIVE_RUNTIME.md` had stated they "remain unreconciled,"
  and each pointed at the other for the authoritative answer — a genuine cross-document
  contradiction, not a documentation nitpick. This decision gives both documents (and
  `MASTER_PLAN.md`) one, single, consistent answer going forward.
- **Consequences:** Stage 5 (`ROADMAP.md`) live proactive *reflection* behaviour remains blocked
  until the implementation gap above is closed by separately-authorised code plus verifying tests
  — concatenation of two independently-running pipelines is not an acceptable foundation for new
  proactive behaviour built on reflection output. No reflection code was modified as part of this
  decision; this is a documentation-only architectural decision recording the target, not the
  implementation of it.
- **Date:** 2026-07-28 (documentation reconciliation pass 2)

## Decision: Phase B stage B2 generalizes `SchedulerStore`'s pattern into a storage-agnostic shared executor
- **Decision:** `bartholomew/kernel/blocking_executor.py`'s `SingleWorkerExecutor` (one dedicated
  worker thread, a submission gate limiting it to one in-flight operation, and a `close()` that
  bound-waits for confirmed termination rather than assuming it) is the one, reusable mechanism for
  offloading blocking, non-async-safe work off the event loop — not a second SQLite-specific
  executor. `scheduler/store.py`'s `SchedulerStore`, the original instance of this pattern, was
  refactored to delegate to it (a thin `persistence.py`-specific facade) rather than left as an
  independent duplicate. `KernelDaemon` owns one shared instance (`self.blocking_executor`), closed
  before `MemoryStore`'s final checkpoint alongside `scheduler_store`, folded into the same
  drain-confirmation gate. All 5 event-loop-blocking caller groups
  `docs/B1_SHARED_CONNECTION_POLICY.md` §2 assigned to B2 (FTS startup schema init,
  `SkillRegistry.load_enabled_skills()`, `ParkingBrake` construction across 4
  `runtime_contract.py` functions plus skill execution, persona/narrator calls, memory
  chunking/re-embedding) were migrated onto it via a small `run_off_loop(fn, *args, executor=None,
  **kwargs)` helper that falls back to a one-off `asyncio.to_thread()` for call sites with no
  owning daemon instance (`run_sight_through_runtime_contract`/`run_voice_through_runtime_contract`,
  neither of which take a daemon/ctx parameter).
- **Alternatives:** (a) Leave `SchedulerStore` as-is and build a second, independent
  SQLite-specific `DedicatedDbExecutor` per the archived design's original two-lane proposal —
  rejected: `docs/B0_PERSISTENCE_BASELINE.md` §2 found no such class exists, and building a second
  bespoke mechanism would repeat exactly the kind of duplicate-implementation problem B1 had just
  finished resolving for `db_ctx.py`. (b) One dedicated `SingleWorkerExecutor` per subsystem
  (persona, narrator, parking brake, memory chunking) rather than one shared instance — rejected
  as unnecessary added complexity for this pass: SQLite's own file-level locking already serializes
  writes regardless of how many Python threads submit them, and none of B1's 5 assigned groups
  have a documented cross-group ordering requirement (unlike the scheduler's own tick-loop
  ordering, which is why `SchedulerStore` correctly keeps its own separate lane rather than sharing
  this one). (c) Scatter bare `asyncio.to_thread()` calls at every one of the 9 call sites instead
  of building a reusable primitive — rejected for the same reason `DECISIONS.md`'s prior
  "Scheduler persistence moved off the event loop" entry already rejected it: no enforced
  backpressure/sequential-submission invariant, reusing the shared default executor unconditionally
  even where a dedicated lane is cheap and available. `run_off_loop()`'s fallback to
  `asyncio.to_thread()` for the two daemon-less sight/voice call sites is a narrow, deliberate
  exception to this, not a reversal of it.
- **Why:** `docs/B0_PERSISTENCE_BASELINE.md` §3 (as corrected during PR #33 review) confirmed FTS
  startup schema init and `SkillRegistry.load_enabled_skills()` run synchronously during
  `KernelDaemon.start()`'s first and fourth steps respectively, directly on the event loop; §3 also
  confirmed `ParkingBrake.__init__()` itself performs the blocking SQLite read (`is_blocked()`
  afterward only reads the in-memory cache that construction populated), and that construction is
  reachable, unwrapped, from 4 `runtime_contract.py` functions plus skill execution. Every one of
  these sits on a live, frequently-exercised path (daemon startup, every chat message, every
  scheduler drive, every skill action) sharing the same event loop as HTTP request handling.
- **Consequences:** `MemoryStore.__init__()` and `SkillRegistry.__init__()` both gained an optional
  `blocking_executor` constructor argument (default `None`, fully backward compatible — existing
  callers/tests that don't pass one get `run_off_loop()`'s `asyncio.to_thread()` fallback, not a
  new required dependency). `skill_registry.py`'s `_is_blocked_by_brake()` is now `async def`
  (single call site, `execute_action()`, updated to `await` it). A new shared helper,
  `bartholomew.orchestrator.safety.parking_brake.construct_parking_brake_off_loop()`, is the one
  place all 5 `ParkingBrake` construction-and-check call sites route through — a future change to
  how that construction is offloaded only needs to change one function. Verified against the full
  governance/runtime-contract test set including `test_chat_returns_503_when_parking_brake_engaged`
  (the fail-closed path this change touches most directly), plus the full non-integration/non-slow
  suite — see `docs/B2_EVENT_LOOP_ISOLATION.md` §4 for the complete verification record, including
  the one pre-existing, already-documented flaky test this pass re-confirmed but did not touch
  (`tests/test_sqlite_wal_concurrent_processes.py::test_wal_cleanup_concurrent_processes`).
- **Date:** 2026-07-31 (Phase B stage B2)

## Decision: Phase B stage B3 builds a new governance schema/store alongside, not in place of, `ParkingBrake`
- **Decision:** `bartholomew/orchestrator/safety/governance_store.py` introduces a governance-owned
  schema (`parking_brake_state`, `governance_audit` — narrow, separate from `MemoryStore`'s schema)
  and a `GovernanceStore` class, tested in isolation, built as a new module alongside the current
  `ParkingBrake`/`BrakeStorage` rather than modifying them in place. `engage()` keeps simple replace
  semantics (not union), version-guarded only on the loosening side: `disengage()` defaults its
  `expected_revision` check to the calling instance's own last-loaded revision, raising
  `StaleGovernanceWriteError` instead of silently regressing a more-recent, more-restrictive state;
  `engage()` (tightening) is never refused regardless of staleness. State and its audit record are
  written in one transaction, closing the gap where `BrakeStorage.append_memory()`'s audit trail was
  silently dead code in production (no real construction site ever passes a `memory_store`). The
  archived design's third table, `brake_runtime`, is deferred entirely to B5, where it's actually
  consumed (it is the write-fence/clean-shutdown-marker mechanism, not part of B3's own scope).
- **Alternatives:** (a) Modify `parking_brake.py`'s existing `BrakeStorage`/`ParkingBrake` in place
  — rejected: B3's isolated tests could then accidentally exercise or destabilize the still-live,
  wired-in code path before B4 has done the runtime-integration work that's supposed to gate that
  transition. (b) Union/monotonic-widening `engage()` semantics, per the archived design's original
  proposal — rejected for this stage: `docs/B0_PERSISTENCE_BASELINE.md`/B3's own re-grounding found
  exactly one real production caller of `engage()` today (`bartholomew/cli.py`'s `brake on`), so
  building concurrency-safe union semantics now is complexity for a caller that doesn't exist yet;
  revisit only if B4's live-daemon re-inventory finds a genuine concurrent caller. (c) Guard `engage()`
  with the same revision check as `disengage()` — rejected: the non-negotiable invariant is that
  tightening is never refused, only loosening requires confirmation: guarding `engage()` too would
  let a stale reader block a legitimate emergency tightening. (d) Build `brake_runtime` now for
  schema-ownership completeness even though nothing consumes it yet — rejected as the same kind of
  disconnected, untested proposal B0/B1 have been correcting elsewhere in this phase.
- **Why:** `docs/B0_PERSISTENCE_BASELINE.md`'s re-verification (independently repeated at B3 plan
  start) found governance state currently lives inside `MemoryStore`'s own `system_flags` table —
  a real schema-ownership violation of this phase's "one authoritative schema per governance table"
  invariant — with no version guard of any kind and a completely non-functional audit mechanism.
- **Consequences:** `ParkingBrake`/`BrakeStorage` are functionally unchanged by this stage; the
  live runtime still reads/writes `system_flags` exactly as before. Wiring `GovernanceStore` in as
  the runtime's shared instance, migrating the 9 real construction sites `docs/B0_PERSISTENCE
  _BASELINE.md` §5 found, and revisiting the replace-vs-union question against real live-daemon
  callers, are all B4's work, not resolved here. Verified with 19 isolated tests, including two
  genuine crash-consistency tests: a real SQLite `BEFORE INSERT` trigger injects a failure between
  the state `UPDATE` and the audit `INSERT` (Python's `sqlite3.Connection` is a C type and can't be
  monkeypatched, so a DB-level fault injection was used instead of a mock), and a successful write
  is checked for survival across a full connection/instance drop-and-reopen — see
  `docs/B3_GOVERNANCE_PERSISTENCE.md` §3 for the complete record.
- **Date:** 2026-07-31 (Phase B stage B3)

## Decision: Phase B stage B4 bridges the live daemon and the legacy CLI with a temporary fail-closed dual-check
- **Decision:** `KernelDaemon` now owns one shared `GovernanceStore` instance (B3's schema),
  constructed off the event loop in `start()`, wired into every real live-daemon Parking Brake
  construction site: `skill_registry.py`'s `_is_blocked_by_brake`, `runtime_contract.py`'s chat and
  drive Governance gates, and `Orchestrator.handle_input()`'s mainline path (via a new
  `skip_governance_check` parameter, set by `app.py`'s `_respond()` closure since
  `run_chat_through_runtime_contract` already gates it). Standalone CLI construction sites
  (`bartholomew/cli.py`) are untouched, per `docs/PHASE_B_OVERVIEW.md`'s explicit B4 exit
  condition — B6's responsibility. Because the CLI keeps writing only the legacy `system_flags`
  value until B6, a new, deliberately temporary module,
  `bartholomew/orchestrator/safety/governance_bridge.py`, makes every live check consult *both*
  sources, blocked if either says blocked — so the CLI kill switch keeps affecting the running
  daemon through the B4-to-B6 migration window. Its own docstring, and
  `tests/test_governance_bridge_dual_check.py`'s, both state plainly that both files must be
  deleted in the same B6 change that migrates the CLI off `system_flags`.
- **Alternatives:** (a) Accept the gap and document it — rejected: a real, avoidable operator-facing
  safety regression (the emergency kill switch silently stops working against the live daemon)
  for however long B6 takes is not an acceptable cost merely to keep B4's diff smaller. (b) Have B4
  also switch the CLI to write through the new schema now — rejected as a genuine scope violation:
  B6 owns CLI treatment specifically because of process-lock/race-safety design it hasn't done yet,
  and a rushed partial CLI migration here risks introducing a worse race than the one being
  guarded against. (c) Cache Governance state in the shared instance rather than refreshing on every
  check — rejected: today's behavior (every check re-reads from disk) is what makes "CLI writes,
  daemon picks it up on the very next check" work at all; a cache would have made the split-brain
  window *worse*, not better, since even the new-schema side would go stale between refreshes.
- **Why:** `docs/B0_PERSISTENCE_BASELINE.md`'s inventory, re-verified at this stage's plan start,
  confirmed `bartholomew/cli.py`'s `brake on`/`brake off` are still the only real write path for
  Parking Brake state; consolidating the daemon's reads onto B3's new schema without also
  addressing that write-path gap would have silently broken CLI-driven emergency control the moment
  this stage merged — the exact kind of "Independent emergency control" invariant
  `CONSTITUTION.md`'s safety checklist exists to catch.
- **Consequences:** Every live Governance check now does two reads (new schema + legacy
  `system_flags`) instead of one until B6 lands — an accepted, temporary, and explicitly-documented
  cost. A first version of the bridge (plain `store.is_blocked() or legacy_is_blocked()`, read-only)
  turned out to be genuinely broken and was caught before merge by
  `tests/test_end_to_end_tasks_and_audit.py::test_parking_brake_blocks_then_disengage_allows`:
  `GovernanceStore.__init__()` imports the legacy value exactly once, and since nothing calls
  `engage()`/`disengage()` on the shared instance directly, that one-time snapshot never re-synced —
  a later legacy `disengage()` was invisible to the bridge, which would have kept reporting
  "blocked" forever. Fixed by having every check mirror the current legacy value into the store via
  a real, audited write whenever they disagree, but only when the store's own latest transition
  isn't already a genuine (non-mirror, non-`"migrated"`) engagement — so a mirror can tighten or
  match, never silently loosen, a real engagement the store holds independently. See
  `docs/B4_GOVERNANCE_RUNTIME_INTEGRATION.md` §2 for the full mechanism and why each direction is
  safe. Also fixed while re-inventorying construction sites for this stage: `Orchestrator
  .handle_input()`'s own Parking Brake check was textually synchronous, so B0's/B2's "sync call
  inside `async def`" search missed that it was reachable on the event loop on every chat message
  via `app.py`'s `async def _respond(...)` closure, and was entirely redundant with
  `run_chat_through_runtime_contract`'s own gate on that path; the `_kernel is None` fallback path
  (the one case where `handle_input()`'s check genuinely is the sole gate) is now wrapped in
  `asyncio.to_thread(...)` at its one call site rather than needing every internal blocking call
  individually off-loaded. Verified against the full governance/runtime-contract/scheduler/
  lifecycle test set (211 tests) plus the complete non-integration/non-slow suite, both clean, and
  a temporary regression suite (8 tests) covering the bridge's four originally-required scenarios,
  scope-specificity, `global`-scope behavior, and the staleness bug above — see
  `docs/B4_GOVERNANCE_RUNTIME_INTEGRATION.md` §4 for the complete record.
- **Date:** 2026-07-31 (Phase B stage B4)

## Decision: Phase B stage B5 keeps the write-fence/clean-marker and Startup Incident Log under Governance's authority
- **Decision:** `KernelDaemon` gains explicit `DaemonLifecycleState` tracking (`NOT_STARTED ->
  STARTING -> RUNNING -> STOPPING -> STOPPED`, with `STARTING -> FAILED` as the other branch).
  `FAILED` is terminal for a given instance -- `start()` refuses to run again on an instance past
  `NOT_STARTED`; a retry is expected to be a fresh process with a fresh instance, never a mutation
  back to `NOT_STARTED`. The write-fence/clean-marker (`brake_runtime`) and an append-only Startup
  Incident Log (`startup_incidents`) both live in `bartholomew/orchestrator/safety
  /governance_store.py`, not a separate daemon-lifecycle module -- both are fundamentally a
  Governance concern (a trust assertion about whether the previous runtime completed cleanly), and
  Governance remains this project's single authority for runtime integrity state, not something
  split across persistence systems. `start()`'s failure-unwind now covers every resource it
  actually activates, not a fixed pair. Unclean-shutdown recovery is conservative but
  non-blocking: detect, log prominently, run a lightweight `PRAGMA quick_check`, repair the
  deferred WAL checkpoint if it passes, and continue -- aborting (a new `UnsafeStartupError`) only
  when the check itself reports a real problem, never merely because the last shutdown wasn't
  confirmed clean.
- **Alternatives:** (a) A separate general daemon-lifecycle-marker module, independent of
  Governance -- rejected per the explicit direction that runtime integrity state should not be
  split across persistence systems. (b) Treat an unclean prior shutdown as itself sufficient
  reason to refuse startup (fail closed on the marker alone) -- rejected: an ordinary power loss or
  OS crash must not permanently prevent Bartholomew from starting; only actual evidence of
  unsafety (a failed integrity check) should abort. (c) On a poisoned-instance violation (`start()`
  called twice, or after `FAILED`), silently no-op and return the existing state -- rejected: that
  would hide a real caller bug rather than surface it, and "keeping the process in `FAILED` makes
  debugging easier" was the explicit direction. (d) Run the full, expensive `PRAGMA
  integrity_check` rather than `quick_check` -- rejected as disproportionate to "lightweight
  verification."
- **Why:** Re-grounding this stage against the current `start()`/`stop()` (not assumed from B0,
  which predates B2's `blocking_executor` and B4's `governance_store`) found two real,
  currently-untested unwind gaps: `governance_store` construction activated `blocking_executor`'s
  worker thread *before* the old protected region began, and producer tasks created before
  `scheduler_task` were never cancelled if that last step failed. B4 also made this stage's
  "Governance write freeze" concern concretely real for the first time -- its dual-check bridge
  writes to `governance_store` on ordinary reads, where nothing wrote there before.
- **Consequences:** `stop()` now closes the write fence *before* any other teardown step (freeze
  before drain) and marks the shutdown clean only if producer tasks were *confirmed* terminal (a
  `wait_for` timeout is no longer silently swallowed) *and* both worker-thread resources drained --
  an honest marker, not an assumed one. Two real bugs surfaced by the new test suite before merge,
  both fixed: the failure-path incident write originally tried to route through
  `blocking_executor` via `run_off_loop()` after that executor was already closed as part of the
  same failure's unwind (fixed by making incident recording a direct call, the same precedent
  `mark_clean_shutdown()`'s own tail-of-shutdown call already set); and an early version of the
  "next daemon sees previous clean shutdown" test checked the marker *after* the second daemon's
  own `start()` had already overwritten it via its own `open_new_runtime()` call. Verified against
  the full governance/runtime-contract/scheduler/lifecycle test set (261 tests) plus the complete
  non-integration/non-slow suite, both clean, plus 32 new tests split across
  `tests/test_governance_runtime_lifecycle.py` (fence/incident-log persistence in isolation) and
  `tests/test_daemon_lifecycle_integrity.py` (lifecycle transitions, both regression cases, and the
  full unclean-shutdown/integrity-check/recovery path) -- see
  `docs/B5_STARTUP_SHUTDOWN_INTEGRITY.md` §4 for the complete record.
- **Date:** 2026-07-31 (Phase B stage B5)
