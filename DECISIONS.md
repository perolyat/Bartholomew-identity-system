# DECISIONS

> Meaningful decisions, alternatives considered, and consequences.
>
> **Last updated:** 2026-07-22 (two new entries added: the Lead Architect transition from
> Bartholomew to Claude, and the adoption of `CONSTITUTION.md` as a 13th canonical SSOT doc)

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
- **Migration progress:** The **persona** pair completed the full deprecate → migrate → delete cycle on 2026-07-22 (item 11.12): both legacy callers of `identity_interpreter/policies/persona.py` (CLI `explain`, standalone `chat.py`) were repointed at `PersonaPackManager` for tone and the module was removed — the first of the four pairs fully retired. Note the split this surfaced: `PersonaPack` deliberately carries no `traits`, so the migrated callers now read `tone`/`style` from the authoritative persona pack (switchable presentation) but `traits` from `Identity.yaml` directly (stable identity character). That is the correct ownership boundary — persona packs own *how* Bartholomew presents, Identity owns *who* it is — not a residual duplication.
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
