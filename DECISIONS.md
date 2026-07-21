# DECISIONS

> Meaningful decisions, alternatives considered, and consequences.
>
> **Last updated:** 2026-01-19

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
