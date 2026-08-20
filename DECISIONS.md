# DECISIONS

> Meaningful decisions, alternatives considered, and consequences.
>
> **Last updated:** 2026-08-20 (Post-Test #1 documentation propagation — sixteen new entries added
> under the new "Post-Test #1 decisions (approved 2026-08-20)" heading at the end of this document:
> the fifteen decisions Taylor approved on 2026-08-20 as part of **Post-Test #1 Decision Register
> v2.2**, plus one entry recording the register's §14 items that remain deliberately unresolved.
> `D1`–`D15` are cross-reference identifiers only; each decision uses this document's existing
> `## Decision: <title>` convention and names its register source in the body. **No production code,
> tests, schemas, migrations or runtime configuration changed**, and **no decision below authorises
> implementation**; work packages are proposed and approved separately under this document's "User
> Approval Gate" entry.
>
> **One CI-configuration change, separately approved — corrected 2026-08-20 after independent
> review.** This note first said the pass changed no "CI/workflows". That became false: commit
> `279635f` adds `exclude: ^docs/evidence/` to the `end-of-file-fixer` and `trailing-whitespace`
> hooks in `.pre-commit-config.yaml`. The two hooks were rewriting the preserved Post-Test #1
> register — 17 markdown hard line breaks original to the approved document — which corrupts an
> artifact this pass exists to preserve byte-for-byte and breaks its recorded SHA-256. Taylor
> approved it separately, on its own merits, as a standing rule: **evidence intended to be immutable
> is not touched by auto-formatters.** It is a two-hook, one-path-prefix exception; `black`, `ruff`,
> `detect-private-key`, `check-yaml` and `check-added-large-files` are untouched, as is every path
> outside `docs/evidence/`. It is **not** an implementation change and authorises nothing below. The approved register is preserved at
> `docs/evidence/test-1/interpretation/`; the readiness bands, safety gates and product gates it
> establishes are propagated to `ROADMAP.md`, `docs/TILT.md`, `RISKS.md`, `CHECKLISTS.md`,
> `INTERFACES.md`, `MASTER_PLAN.md`, `COGNITIVE_RUNTIME.md` and `docs/SAFETY_PARKING_BRAKE.md`.)
>
> **Previously (2026-08-15, second pass):** (one new decision added — "Parking Brake authority
> tiers — Personal/User and Platform/Admin." A targeted follow-up to the same-day platform/personal-
> identity decision: an independently enforceable per-user brake that never halts other users, plus
> a separate higher-scope platform-wide brake a user cannot override and that does not require
> disabling users individually. Records precedence, the orthogonality of tiers to the existing
> subsystem `scopes` axis, and that brake scope is Governance authority enforced below the
> presentation layer rather than a UI feature. Documentation-only — no code change authorised or
> required; the current single-user brake conceptually *is* the Personal/User tier.
> `COGNITIVE_RUNTIME.md`'s "Authority tiers" subsection is the canonical authority for the
> semantics.)
>
> **Previously (2026-08-15, first pass):** one new decision added — "One shared Bartholomew platform; many
> strongly isolated personal Bartholomew identities." Records the foundational distinction between
> the shared platform, the replaceable underlying intelligence/models, and a user's persistent
> personal Bartholomew; establishes that Bartholomew is not the LLM, that identity is portable
> across infrastructure, that the client may eventually be lightweight while local Governance
> authority stays local, and that isolation between personal identities is non-negotiable. Extends
> rather than replaces the 2026-07-28 "Deployment architecture — hybrid local-first" entry.
> Documentation-only — no implementation, schema, or PoC-scope change authorised. See
> `CONSTITUTION.md`'s new "One Platform, Many Personal Bartholomews" section, `COGNITIVE_RUNTIME.md`'s
> new "Personal-identity ownership" subsection, `CHECKLISTS.md`'s new checklist, `RISKS.md`'s three
> new tech-debt entries, and `ASSUMPTIONS.md` A9.)
>
> **Previously (2026-08-14, second pass):** one new decision added — "AI-assisted development is
> governed by the existing Architect/User-Approval framework — provenance, IP, and third-party-
> licensing risk made explicit." Prompted by Anthropic's introduction of machine-readable
> provenance/watermarking for Claude-generated content; a repository-grounded governance review,
> not legal advice. Documentation-only — no implementation, dependency, workflow, or CI change
> authorised. See `RISKS.md`'s three new tech-debt watchlist entries and `CHECKLISTS.md`'s one new
> PR-checklist line, added in the same pass.)
>
> **Corrected 2026-08-14** (same day, following an automated review comment): the "AI-assisted
> development is governed by..." entry's introductory paragraph is corrected to distinguish AI
> tools with actual repository-evidenced use (Claude, Cline) from GitHub Copilot, which this
> repository is configured to support (`.github/copilot-instructions.md`) but has no proven
> authorship record for. Wording-only; the decision's substance is unchanged.
>
> **Previously (2026-08-14, first pass):** one new decision added — "Usable POC slice 1
> implementation approved" — recording the separate, explicit implementation approval the prior
> entry required, the one design deviation accepted at review, and the acceptance-bar wording
> clarification. Slice 1 is implemented in `2d443a9`.)
>
> **Previously (2026-08-12):** one new decision added — "Usable POC / time-to-real-use
> prioritisation" — approving a repository-grounded assessment's finding that development had
> drifted toward polish/hardening ahead of real use, and formalising the resulting
> execution-sequencing principle in new canonical document `docs/TILT.md`. Personal Memory Capture
> and Recall is approved as the Usable POC's first vertical slice, explicitly not its boundary.
> The "Canonical SSOT docs" entry below is amended (membership only) to add `docs/TILT.md` as the
> 14th canonical document. Documentation-only: no code, tests, or configuration changed.)
>
> **Previously (2026-08-11):** one new decision added — "Schema keys of registered structured
> kinds are structural metadata, not user content," approved as part of S5.2's final implementation
> review and implemented in `7bfab09`, merged via PR #43 / `5dacb52`. Records the measured
> rejection of the values-only-scanning alternative, which would have been a real consent bypass.
> The separate `privacy_guard` vs `memory_rules.yaml` sensitivity-vocabulary disagreement is
> recorded in `RISKS.md` and deliberately **not** resolved.)
>
> **Previously (2026-08-08):** New Direction reconciliation: three new decisions added — "One
> developing digital individual — competency and training architecture," "Stage 5 restructured
> around competency and training before live initiative," and (same-day follow-up) "Personal,
> potentially generalisable, and system-level learning are architecturally distinct." All three are
> documentation-only; no implementation authorised. See `CONSTITUTION.md`, `COGNITIVE_RUNTIME.md`,
> `ROADMAP.md`, and `ASSUMPTIONS.md` for the corresponding documentation changes.)
>
> **Previously (2026-07-31):** documentation-only Phase B restructuring: one new decision added —
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
- **Amended 2026-08-12 (membership only, not the decision):** the set is now **14** documents —
  the 13 above plus `docs/TILT.md`, the current Usable POC / time-to-real-use execution-priority
  document (see the "Usable POC / time-to-real-use prioritisation" entry below). This is a
  deliberate, narrow exception to the general rule that everything under `docs/` is a
  non-authoritative reference: `docs/TILT.md` governs binding near-term sequencing the same way
  `ROADMAP.md` does, so reference status would misstate its authority. `MASTER_PLAN.md`'s
  "Canonical docs" section and `README.md`'s doc-count reference are both updated to match.

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

## Decision: Schema keys of registered structured kinds are structural metadata, not user content
- **Decision:** `privacy_guard.is_sensitive()` may exclude the *schema-defined key names* of
  explicitly **registered** structured record kinds from content sensitivity scanning. **All values
  are always scanned in full**, as are any keys not part of the registered schema. Unknown or
  unregistered structured data retains conservative key-and-value scanning. Registration is opt-in
  per kind, and a kind whose schema legitimately contains a sensitivity-indicating key (a literal
  `password` field, say) must not be registered at all.
- **Alternatives considered:**
  - *Scan JSON values only, never keys.* **Rejected on measured evidence:** `{"password":
    "hunter2"}` and `{"email": "bob@example.com"}` are sensitive solely by virtue of their key, so
    this would have been a real consent bypass. `tests/test_privacy_guard_structural_scanning.py`
    pins that failure mode so the approach cannot be reintroduced by accident.
  - *Special-case competency records.* Rejected: the defect is generic to any caller storing
    schema-structured JSON, so a per-caller exemption would leave the same trap for the next one.
  - *Narrow the sensitivity vocabulary (e.g. drop `name`).* Rejected here: that changes what the
    gate catches globally and is a policy question, not a correctness fix.
  - *Accept the behaviour.* Rejected: every `competency`/`competency_procedure` record was being
    queued for consent on its schema shape rather than its content, which both degrades the
    training experience and dilutes the consent inbox's signal.
- **Why:** The gate exists to catch sensitive *content*. A field *named* `name` is schema; the
  person's name that might sit in a value is content. Conflating them flags records on their shape.
  The rule is deliberately opt-in and fail-safe: a missing registration degrades to conservative
  scanning, never to permissive, so forgetting to register can only over-trigger consent.
- **Consequences:** A registered kind's schema must contain no key that is itself the only signal
  that its value is sensitive — enforced by a schema-key inventory guard asserting exactly which
  registered keys collide with the sensitivity vocabulary (today: `name` alone, reviewed and
  approved). **If a future schema change adds such a key, the correct response is to de-register
  that kind or treat the key as content — not to extend the allowlist to make the test pass.**
  Registration lives with the module owning the schema (`competency.py` registers its own five
  kinds, deriving the key set from the dataclasses so it cannot drift), because an existing S5.1
  invariant forbids the shared Memory path from importing the competency module.
- **Date:** 2026-08-11 (approved as part of S5.2's final implementation review; implemented in
  `7bfab09`, merged via PR #43, merge commit `5dacb52`. See
  `docs/S5_2_TRAINING_KNOWLEDGE_ACQUISITION_DESIGN.md` and `RISKS.md`'s separately-recorded,
  deliberately-unresolved `privacy_guard` vs `memory_rules.yaml` vocabulary disagreement.)

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

## Decision: AI-assisted development is governed by the existing Architect/User-Approval framework — provenance, IP, and third-party-licensing risk made explicit
- **Decision:** Bartholomew's development process, including work performed by Claude (Lead
  Architect, per the 2026-07-22 handover decision above) and Cline (repository evidence of actual
  use — see "Decision: Prompt-size discipline for agent execution (Cline)" above, 2026-01-19), is
  governed by the existing `DECISIONS.md`/`CHECKLISTS.md` framework, not a separate AI-specific
  approval process. `.github/copilot-instructions.md` configures this repository to support GitHub
  Copilot; that file demonstrates configuration, not proof that Copilot has produced any repository
  contribution — no Copilot-attributed commit or trailer exists in git history today. Should that
  change, this same framework governs Copilot's use too, without requiring a separate decision.
  This entry makes explicit six things that were already true in practice but not previously
  stated as policy:
  1. AI coding assistance is permitted and is already this repository's primary development
     mechanism; nothing about that changes.
  2. An AI-generated proposal, plan, or code change carries no special trust merely because a
     model produced it. The existing User Approval Gate ("Decision: User Approval Gate for all
     doc/code commits," above), the Commit authorization checklist, and the PR checklist's
     testing/review requirements (`CHECKLISTS.md`) apply identically regardless of whether a human
     or an AI agent authored the diff.
  3. Git, PR, decision, and approval history must remain a truthful and commercially
     reconstructable record of material authorship, AI assistance, review, and human approval.
     Existing Claude-authored/Co-Authored-By attribution and historical records are retained and
     must not be rewritten merely to conceal AI involvement. Future workflows may legitimately use
     human commits, AI-agent commits, co-authorship trailers, reviewed AI-generated patches, or
     other mechanisms, provided they do not fabricate or deliberately misrepresent material
     provenance. No particular AI provider, Git author identity, or attribution mechanism is
     permanently required.
  4. Bartholomew (the product) must not become architecturally dependent on Anthropic, Claude,
     Claude-specific APIs, or Claude-specific provenance/watermarking mechanisms. This restates
     `CONSTITUTION.md`'s "one architectural authority per concept" and this project's own prior
     tool/architect transitions (Cline → Claude Code as coding tool; the Lead Architect role
     itself moving from a persona to Claude without loss of continuity, per `CONSTITUTION.md`'s
     "Documentation Philosophy") — applied here specifically to development tooling, alongside the
     runtime model-routing abstraction already in `ORCHESTRATION_INTEGRATION.md`. Claude Code is a
     development tool, not part of Bartholomew's runtime architecture.
  5. A known machine-readable AI provenance/watermark signal (e.g. Anthropic's provenance
     mechanism for Claude-generated content) must not be intentionally stripped, defeated,
     obfuscated, or laundered through another model for the purpose of concealing AI involvement.
     Conversely: the signal's presence is not itself evidence of defective, non-original, or
     infringing code; its absence is not itself evidence of human authorship; and normal code
     review, testing, and the third-party-licence diligence in item 6 remain the actual engineering
     controls — not watermark presence or absence. No mass rewrite or paraphrase of existing
     AI-assisted code or documentation is required or authorised by this entry.
  6. AI-generated code is not assumed original or licence-clean merely because a model produced
     it. Ordinary third-party dependency/licence compliance (already governed by
     `pyproject.toml`/`requirements*.txt`) remains mandatory for anything a contributor — human or
     AI — adds. A substantial, suspiciously close match between generated code and a known
     third-party implementation is treated as requiring investigation before merge, not automatic
     acceptance — this restates ordinary code-review diligence for a generated-code failure mode,
     not a new mechanism.
- **Alternatives considered:**
  - *Do nothing; rely on existing unstated practice.* Rejected: the practice is sound (truthful
    git authorship, a real User Approval Gate, a provider-agnostic architecture), but a future
    investor/acquirer/customer diligence process should not have to reverse-engineer this
    project's AI-governance posture from `git log` and scattered decisions. Recording it once
    costs nothing and closes that gap.
  - *Create a new standalone canonical document (e.g. `AI_GOVERNANCE.md`).* Rejected under this
    repository's own "one authority per concept, no doc sprawl" principle: this is a single
    decision, of a kind `DECISIONS.md` already exists to hold — it extends the "Lead Architect
    role transitions from Bartholomew to Claude" entry above rather than duplicating it — and the
    specific open risks it identifies belong in `RISKS.md`'s existing tech-debt watchlist, not a
    new document.
  - *Build technical controls now (SBOM, dependency-licence scanning, provenance/attestation
    tooling) as part of this pass.* Rejected for now, not permanently: per `docs/TILT.md`'s
    time-to-real-use principle, these are external-beta/commercial-release-maturity controls, not
    blockers for a single-developer Usable POC with no external distribution yet. Recorded in
    `RISKS.md` so they are not lost, not implemented ahead of need.
  - *Mandate per-line or per-commit AI-authorship labelling as a permanently required mechanism.*
    Rejected: today's mix of mechanisms — git commit authorship where used, PR descriptions,
    decision-log entries, and the review/merge trail — already reconstructs requirement → design →
    AI-assisted implementation → review → human approval → merge without new bureaucracy; per-line
    labelling would be exactly the kind of useless per-line provenance bureaucracy this review was
    asked to avoid. Fixing one specific mechanism (e.g. always attributing commits to a particular
    AI-agent Git identity) as a *permanent* requirement would also contradict item 3's own
    principle that no particular attribution mechanism is permanently required — future workflows
    must remain free to use whichever truthful mechanism fits, provided material provenance is
    never fabricated or misrepresented.
- **Why:** A commercially defensible, diligence-ready development process needs "how was AI used,
  under what controls, and is the IP/licensing position clean" to be reconstructable from the
  repository, not institutional memory. This repository's actual practice already produces most of
  that evidence; the gap was that none of it had been stated as a decision a diligence reviewer,
  new contributor, or future coding agent could find. Two genuinely open risks surfaced during this
  review — an unresolved LICENSE inconsistency, and the complete absence of dependency-licence/SBOM
  tooling — are real and are recorded in `RISKS.md`, not silently accepted.
- **Consequences:** No change to how development is done — this entry describes and extends
  existing practice. The only new obligations on future work are: (i) don't defeat/strip
  provenance signals to conceal AI involvement, (ii) don't fabricate human authorship or rewrite
  history to remove AI evidence, (iii) treat a suspicious generated-code match as an investigation
  trigger, and (iv) keep the architecture provider-agnostic — all four of which current practice
  already satisfies. Three items move into `RISKS.md`'s tech-debt watchlist (LICENSE
  inconsistency; no dependency-licence/SBOM tooling; no AI-provider-terms record), explicitly
  framed as not blocking the Usable POC, per `docs/TILT.md`. `CHECKLISTS.md` gains one line
  extending the existing secrets/confidential-data discipline explicitly to AI coding-agent
  sessions (already true in practice; now stated). `MASTER_PLAN.md`'s Approval Ledger records this
  entry once committed, per that document's own "never mark anything as committed without a commit
  hash" rule — see `MASTER_PLAN.md`'s corresponding update, below. No implementation, dependency,
  workflow, or CI change is authorised by this entry.
- **Date:** 2026-08-14

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
> **⚠️ SUPERSEDED 2026-08-17** by "Deployment architecture — server-centric Bartholomew with
> local/edge capability agents" (end of this document). Preserved unchanged as the record of a
> decision correctly made on the evidence then available.
>
> **What changed:** this entry's clause making a local runtime "the authority for **sensitive
> memory**", and its rejection of alternative (a) (a hosted service with a thin local client). Core
> cognition and personal memory are now intended to be server-centric.
>
> **What still stands, and is carried forward by the superseding entry:** governance enforcement,
> the parking brake and emergency shutdown must not depend on cloud availability; and no
> remote/cross-device exposure before authentication, authorization, transport security and a
> reviewed threat model are designed and separately approved — "simple token auth" is explicitly not
> assumed sufficient. Documents citing this entry *for those points* remain correct.

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
  `COGNITIVE_RUNTIME.md`'s ownership table, and this document's current deployment-architecture
  entry (the server-centric entry, which supersedes "hybrid local-first") before any adoption; none
  of it is scheduled, approved, or a stage gate.
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

## Decision: Phase B stage B6 retires the CLI's legacy Governance write path instead of extending the dual-check bridge, and scopes the process lock to genuine offline-conflict operations only
- **Decision:** `bartholomew/cli.py`'s `brake on`/`brake off`/`brake status` -- the three
  standalone `ParkingBrake`/`BrakeStorage` construction sites B4 explicitly left untouched -- now
  construct a `GovernanceStore(db)` and call `engage()`/`disengage()`/`state()` directly, tagging
  every write's audit reason with a `"CLI: ..."` prefix. This retires the CLI's last legacy
  `system_flags` write path, which in turn retires B4's temporary dual-check bridge
  (`governance_bridge.py` and its 8-test file, deleted per B4's own docs' explicit instruction) --
  the four call sites that depended on it (`skill_registry.py`, `runtime_contract.py`'s chat and
  scheduler gates, `identity_interpreter/orchestrator/orchestrator.py`) now call plain
  drop-in-shaped `is_blocked_fail_closed()`/`is_blocked_fail_closed_off_loop()` functions added to
  `governance_store.py` itself. A new cross-platform `ProcessLock`
  (`bartholomew/kernel/process_lock.py`; POSIX `fcntl.flock`, Windows `msvcrt.locking` over a fixed
  1-byte region) is acquired first in `KernelDaemon.start()` and released last in `stop()` -- both
  the daemon's own single-instance guard and the concrete anchor for B5's lifecycle-terminal-state
  conditions -- and additionally by `bartholomew/cli.py`'s `embeddings rebuild-vss`. `brake
  on`/`brake off`/`brake status` deliberately do **not** take this lock.
- **Alternatives:** (a) Extend the dual-check bridge indefinitely instead of migrating the CLI off
  the legacy path -- rejected: B4's own docs already named this bridge temporary, and keeping two
  parallel Governance write paths alive permanently is exactly the ownership fragmentation Phase B
  exists to close. (b) Have `brake on/off/status` also acquire the process lock, on the theory that
  any CLI-vs-daemon interaction should be lock-gated uniformly -- rejected: `docs/
  PHASE_B_RISK_MAP.md`'s B6 rows explicitly call for "write fencing only where repository evidence
  shows it is necessary," and these commands are the intended mechanism for controlling a *running*
  daemon (the entire point of a remote kill switch); GovernanceStore's own write fence and
  revision-guarded `disengage()` already protect them, and lock-gating them too would make the kill
  switch unusable against a live daemon, defeating its purpose. (c) Consolidate
  `runtime_contract.py`'s sight/voice Governance gates onto `GovernanceStore` in the same pass,
  since they're still on the legacy `ParkingBrake` path -- rejected: B4 already confirmed these
  paths unreachable (no live caller) and explicitly deferred them; B6's scope is CLI safety, not
  reopening an already-decided deferral.
- **Why:** Re-grounding this stage against the current repository (not assumed from B0/B4's own
  research) confirmed exactly three legacy CLI construction sites, exactly four bridge-dependent
  call sites, and exactly one CLI command (`rebuild-vss`) that actually assumes exclusive database
  access with no revision guarding of its own -- `brake on/off/status` are not a real conflict
  path, so treating them as one would be scope creep beyond what the evidence supports.
- **Consequences:** Retiring `governance_bridge.py` broke three pre-existing tests
  (`tests/test_api_chat_runtime_contract.py`, `tests/test_end_to_end_tasks_and_audit.py`,
  `tests/test_scenario_replay.py`) that constructed a standalone legacy `ParkingBrake` to simulate
  an operator engaging the brake, relying on the bridge to make that visible to the runtime's
  check -- each now engages `GovernanceStore` directly instead, the same shape `cli.py`'s own
  migration took. `tests/test_daemon_lifecycle_integrity.py`'s two exact-`resources_started`-set
  assertions needed `"process_lock"` added, since it's now the first resource `start()` activates.
  Verified against the full governance/runtime-contract/scheduler/lifecycle/CLI test set (76 tests
  across the four affected/new files) plus the complete non-integration/non-slow suite, both clean,
  plus 23 new tests split across `tests/test_process_lock.py` (the lock primitive in isolation) and
  `tests/test_cli_governance_and_lock.py` (CLI commands against real `GovernanceStore`/`ProcessLock`
  instances) -- see `docs/B6_EXTERNAL_GOVERNANCE_CLI_SAFETY.md` §3-4 for the complete record.
- **Date:** 2026-07-31 (Phase B stage B6)

## Decision: Phase B stage B7 gates external admission with one HTTP middleware chokepoint, not a per-route migration, and does not build detached-task admission propagation
- **Decision:** A new identity-bound `RequestAdmission` primitive
  (`bartholomew/kernel/request_admission.py`) -- `try_admit() -> token | None`,
  `release(token)` (a no-op for a foreign/duplicate/unknown token, not a bare counter decrement),
  `close()`, `drain(timeout) -> bool` -- is owned by `KernelDaemon`, constructed alongside
  `process_lock`/`blocking_executor` in `__init__`. `stop()`'s very first action after entering
  `STOPPING` is now `admission.close()` followed by `await admission.drain(...)`, ahead of even the
  Governance write-fence close -- in-flight external requests finish against resources that are
  still fully intact, not ones being torn down underneath them. A single new
  `@app.middleware("http")` in `bartholomew_api_bridge_v0_1/services/api/app.py` is the one
  chokepoint that admits or refuses every HTTP request, checking `_kernel.lifecycle_state is
  RUNNING` (not just `_kernel is not None`) with an explicit exemption list for health/liveness/
  metrics/docs endpoints. No detached-task/child-work admission propagation was built.
- **Alternatives:** (a) A `Depends()`-based per-route migration across the ~35 kernel-touching
  routes, the shape the archived design and B6's own CLI migration both used elsewhere -- rejected
  here specifically: unlike B6's three CLI commands, ~35 routes is enough surface that a per-route
  opt-in could silently miss a future route that forgets to add the dependency, where a single
  ASGI-layer middleware structurally cannot be bypassed by a new route. (b) Build the archived
  design's full `AdmissionToken`/`_AdmissionScope`/`spawn_detached_governed_task` propagation
  machinery for child/detached work -- rejected: a direct repository search found no
  `asyncio.create_task()` (or equivalent) call anywhere that spawns work outliving its own
  request/response cycle; building propagation machinery for a pattern that doesn't exist in this
  codebase would be speculative, not evidence-grounded. (c) Trust `_kernel is not None` alone as
  the admission gate, matching the pre-existing per-route checks -- rejected: confirmed by direct
  read that `app.py`'s `startup()` assigns the `_kernel` global before `await _kernel.start()`
  completes, and `shutdown()` never resets it to `None` after `stop()` -- a bare presence check
  admits requests through the entire `STARTING` window and the entire `STOPPING`/`STOPPED` window
  alike.
- **Why:** Re-grounding this stage against the current repository (not the archived design, not
  B0's own route count) found the real gap was narrower and different in shape than assumed: no
  detached work to propagate tokens through, but a real, previously-unguarded lifecycle-state race
  at both ends of the daemon's life, on a governed surface large enough that per-route discipline
  is the wrong reliability bet.
- **Consequences:** Every governed route now pays one `try_admit()`/`release()` pair per request,
  invisible in normal operation (a few microseconds of `set` membership work) but present in
  request latency measurements. `stop()`'s shutdown sequence gained a new drain phase (default 10s
  timeout) ahead of the existing write-fence/producer-task/executor teardown; a stuck or lost
  admission that never releases now correctly prevents the shutdown from being marked clean, the
  same "confirmed, not assumed" honesty B5 already required of every other tracked resource. No
  pre-existing test needed modification -- unlike B6's bridge deletion, this stage added a new gate
  rather than retiring an old path, and every pre-existing test already ran with the kernel
  `RUNNING`. Verified against 30 new tests split across `tests/test_request_admission.py` (the
  primitive in isolation, including the identity-bound-release regression),
  `tests/test_daemon_admission_drain.py` (the close-then-drain sequence in `stop()`, including a
  provable "stop() waited" test and a timeout-doesn't-mark-clean regression), and
  `tests/test_api_admission_gate.py` (the live middleware against a real `TestClient` + running
  daemon) -- plus the complete non-integration/non-slow suite, both clean -- see
  `docs/B7_EXTERNAL_REQUEST_ADMISSION.md` §3-4 for the complete record.
- **Date:** 2026-08-01 (Phase B stage B7)

## Decision: Phase B stage B8's first split sub-stage fixes callers, not the four classes those callers touch
- **Decision:** `ExperienceKernel`, `WorkingMemoryManager`, `PersonaPackManager`, and
  `VectorStore` are confirmed (by direct read) to have zero `async def` methods among them --
  every one of their methods is plain, synchronous, and does real `sqlite3` I/O where relevant.
  Rather than adding async wrappers or an async API surface to any of these four classes, this
  sub-stage fixes the actual gap: four call sites (`daemon.py`'s `start()`/`stop()`,
  `memory_store.py`'s three embedding-pipeline methods, `skill_registry.py`'s `load_skill()`/
  `_finish()`) that called these classes' methods directly from `async def` context instead of
  through `bartholomew.kernel.blocking_executor.run_off_loop()`, the same off-loop pattern B2
  already established. No new mechanism, no new class, no signature change to any of the four
  target classes.
- **Alternatives:** (a) Convert `ExperienceKernel`/`WorkingMemoryManager`/`PersonaPackManager`/
  `VectorStore` to `async def` methods (using `aiosqlite` internally, matching `MemoryStore`'s own
  pattern) -- rejected: a much larger, riskier rewrite of four classes with many callers each
  (including synchronous, non-daemon callers like `bartholomew/cli.py`) to fix what is, on
  inspection, entirely a caller-discipline problem, not a class-design problem; B2 already proved
  the wrap-the-caller pattern works cleanly for exactly this shape of gap. (b) Also fix
  `SkillRegistry.__init__()`'s own constructor-time blocking I/O (`_init_database()`, reachable
  from the event loop since `KernelDaemon.__init__()` runs inside `app.py`'s `async def startup()`
  before `start()` is awaited) -- rejected for this sub-stage specifically: unlike the four target
  call sites (already-isolated calls that just needed wrapping), fixing this would mean
  restructuring *when* `SkillRegistry` itself is constructed relative to the rest of
  `KernelDaemon.__init__()`'s wiring, a construction-site reorganization closer to B4's scope than
  "wrap an already-isolated call" -- named as an honest, deferred limitation instead of silently
  left out. (c) Also migrate `hybrid_retriever.py`'s FTS/vector search calls -- rejected: confirmed
  by direct search that nothing outside `retrieval.py`/`hybrid_retriever.py`/`types.py` itself
  calls `get_retriever()` or `.retrieve()` anywhere in the live codebase; there is no event loop to
  block today.
- **Why:** Re-auditing the repository specifically for B8's "remaining persistence consumers"
  scope (not re-reading B0's original inventory, which predates B2/B4's own migrations and was
  route-focused rather than internal-call-site-focused) found that the API layer's own
  `narrator.py`/`persona_pack.py` calls were *already* correctly wrapped -- the real gaps were all
  internal, non-HTTP call sites B0's route-focused audit never covered, plus a genuinely
  inconsistent partial migration within `reembed_memory()` and `load_enabled_skills()` themselves
  (one call in each method already correctly off-loop, a sibling call in the same method not).
- **Consequences:** `daemon.py`'s `_init_experience_kernel()` is now `async def`, awaited from
  `start()` -- two call sites needed a one-line `await` added
  (`daemon.py` itself, `tests/test_scenario_replay.py`'s `_boot()` helper); one pre-existing
  monkeypatch-based test needed no change, since Python evaluates a call expression (and any
  exception it raises) before the `await` keyword is ever reached. `memory_store.py`'s embedding
  pipeline and `skill_registry.py`'s audit/state persistence now route through the caller's own
  `blocking_executor`/`_blocking_executor`, consistent with every other daemon-owned blocking call.
  Verified against 6 new tests in `tests/test_b8_event_loop_isolation.py` that spy on thread
  identity to prove the calls actually moved off the event-loop thread (not merely that behavior
  was preserved, which the existing test suites already covered and continue to cover) -- plus the
  complete non-integration/non-slow suite, both clean -- see
  `docs/B8_SUB1_STARTUP_SHUTDOWN_VECTORSTORE_SKILLS_OFF_LOOP.md` §3-4 for the complete record. B8
  as a whole remains open; this decision covers only its first split sub-stage.
- **Date:** 2026-08-01 (Phase B stage B8, sub-stage 1)

## Decision: Phase B stage B8's second split sub-stage validates rather than changes MemoryStore's concurrency model
- **Decision:** A stress test (`tests/test_memory_store_concurrency_stress.py`) fires many
  concurrent `upsert_memory()`/`reembed_memory()` calls against one `MemoryStore` sharing one
  `SingleWorkerExecutor`, closing `docs/PHASE_B_RISK_MAP.md`'s remaining named B8 candidate. No
  code changes to `MemoryStore`, `VectorStore`, or `SingleWorkerExecutor` were made.
- **Why:** All three tests passed against the current implementation on first run. Given that
  result, changing the concurrency model to fix a problem that doesn't reproduce would be
  unjustified churn; the correct action is to add the coverage as a permanent regression test and
  record the outcome honestly, which is what this sub-stage does.
- **Consequences:** `SingleWorkerExecutor`'s strict-sequential-submission guarantee is now proven,
  not just documented, under concurrent `MemoryStore` usage -- including through sub-stage 1's
  newly-added `VectorStore` off-loop calls, where a race could in principle have cross-filed one
  memory's embedding under another's `memory_id`. With this, every B8 risk-map candidate is fixed,
  tested-and-confirmed-sound, or confirmed not applicable (sub-stage 1); B8 as a whole is complete.
  See `docs/B8_SUB2_MEMORYSTORE_CONCURRENCY_STRESS.md` for the complete record.
- **Date:** 2026-08-01 (Phase B stage B8, sub-stage 2)

## Decision: Phase B stage B9 validates with real adversarial conditions, not monkeypatched simulations, and reports the archived rollback mechanism as never built rather than partially formalising it
- **Decision:** B9's tests deliberately avoid monkeypatching the specific function under test where
  a real adversarial condition can be produced instead: real bytes are overwritten in a real SQLite
  file rather than mocking `run_quick_integrity_check`'s return value; two `daemon.start()` calls
  race via `asyncio.gather` rather than being called sequentially; `ProcessLock`/`GovernanceStore`
  contention is exercised via real OS threads (20-way) rather than sequential calls asserting on
  order. Separately: a direct repository search for the archived design's
  `rollback_clear_maintenance()` found no such mechanism (or any maintenance-mode/rollback marker
  of any kind) anywhere in the current codebase. Rather than building a new rollback mechanism
  under B9's banner (which is a validation stage, not a feature stage) or writing tests against a
  mechanism that doesn't exist, this is documented as an honest, direct finding: no rollback
  capability exists to formalise.
- **Alternatives:** (a) Monkeypatch every B9 scenario the same way B5's existing suite does (e.g.
  `run_quick_integrity_check` returning a canned failure) -- rejected: B9's entire purpose is
  validating the *integrated, real* system, and a monkeypatched integrity-check failure cannot
  catch a bug in the integrity-check function itself, which is exactly what the real-corruption
  test found. (b) Build a new maintenance-mode/rollback mechanism now, using the archived design as
  a starting point, so B9 has something concrete to "formalise" -- rejected: that would be new
  feature work smuggled into a validation stage, not a decision this stage's own approval scope
  covers, and risks inventing a mechanism nobody has actually needed for the ~9 stages of Phase B
  completed so far.
- **Why:** A stage whose stated purpose is "adversarial validation against the integrated system"
  should actually attack the integrated system, not a mocked stand-in for it -- the real-corruption
  test proving this in practice (finding a bug the monkeypatched equivalent test, still passing
  unchanged, did not and could not find) is the strongest evidence for this choice available.
- **Consequences:** `governance_store.run_quick_integrity_check()` now catches
  `sqlite3.DatabaseError` from `PRAGMA quick_check` itself, restoring the intended
  `UnsafeStartupError` path for severe corruption instead of an undiagnosed raw exception; startup
  safety was never actually compromised (the exception still correctly aborted startup either way),
  only the diagnostic quality B5's Startup Incident Log exists to provide. `ROADMAP.md`'s Phase B
  section is now marked complete (B0-B9 all done) rather than carrying forward an unresolved
  "rollback procedures" promise the codebase was never going to fulfil without new feature work.
  Verified against 7 new tests across `tests/test_b9_adversarial_startup_shutdown.py` and
  `tests/test_b9_concurrent_cli_daemon.py`, plus the complete non-integration/non-slow suite, both
  clean -- see `docs/B9_RECOVERY_ROLLBACK_ADVERSARIAL_VALIDATION.md` for the complete record. This
  is Phase B's final stage; approval of B9 authorises no further Phase B work.
- **Date:** 2026-08-01 (Phase B stage B9)

## Decision: S1.4's `awaiting_response_check` scheduler drive is deliberately not self-maintenance-exempt, requiring its own `Identity.yaml` allowlist entry beyond the design doc's four named seam kinds
- **Decision:** implemented `docs/S1_4_AWAITING_RESPONSE_DESIGN.md` as approved, with one
  implementation-time judgment call the design document itself did not fully resolve: whether the
  new scheduler drive that scans for due reminders/escalations (`awaiting_response_check`) should be
  added to `runtime_contract._SELF_MAINTENANCE_DRIVES` (like `self_check`/`curiosity_probe`/
  `reflection_micro`/`fts_optimize`) or evaluated for real by the Identity Policy check. The design
  doc's own Sec 11 verify-plan comment ("`awaiting_response_check` NOT in `_SELF_MAINTENANCE_DRIVES`")
  settled the *which*, but its Sec 5 "known dependency" only named the four seam kinds
  (`awaiting_response_open/remind/escalate/resolve`) as needing `Identity.yaml` allowlist additions
  -- not the scheduler task_id itself. Taken literally, that would leave the outer scan drive
  registered but permanently denied under real production `Identity.yaml` (`default_allowed: false`,
  and no entry named `awaiting_response_check`), making the feature dead on arrival: reminders and
  escalations would never fire because the scan that discovers due entries never runs.
- **Resolution:** added `awaiting_response_check` as a fifth `tool_use.allowlist` entry, alongside
  the four seam kinds design doc Sec 5 already named, flagged here per `Identity.yaml`'s own
  `governance.change_control` section (same explicit-flagging treatment S1.3's `"notify"` addition
  and S1.4's own four seam-kind additions got).
- **Alternatives:** (a) Add `awaiting_response_check` to `_SELF_MAINTENANCE_DRIVES` instead --
  rejected: it contradicts the design doc's own explicit verify-plan statement, and while the outer
  scan itself contacts no one directly, treating it as exempt would mean the *decision* of whether to
  remind/escalate a specific entry is made under a scheduler-cadence exemption even though the actual
  outbound contact a moment later is correctly gated for real -- the design's stated intent was for
  this surface's entire path, not just its terminal step, to be a conscious inclusion rather than a
  default carve-out. (b) Leave the drive registered without the allowlist addition, accepting it
  never fires until a human explicitly allowlists it later -- rejected: nothing in the design or in
  `ROADMAP.md`'s Stage 1 exit criteria calls for shipping the queue as an inert placeholder, and an
  always-denied registered drive is indistinguishable from a bug to anyone reading production logs.
- **Why:** the feature's own purpose (obligations "aren't silently forgotten," per
  `COGNITIVE_RUNTIME.md`) is not met if the mechanism that notices overdue obligations never runs.
  Functional correctness under real `Identity.yaml` was treated as a stronger constraint than a
  literal reading of which specific allowlist entries the design prose enumerated by name.
- **Consequences:** `tests/test_scheduler_drive_convergence.py`'s `_SELF_MAINTENANCE_DRIVES`
  exemption-equality test was updated (not weakened) to name `awaiting_response_check` as a
  conscious, recorded exception rather than silently breaking; new tests prove it is genuinely denied
  without the allowlist entry and genuinely allowed with it, mirroring the existing
  `TestPolicyDecisionIsRealForNonExemptDrives` pattern for an arbitrary non-exempt drive kind.
  `tests/test_awaiting_response_api.py` runs its assertions against the real app and real
  `Identity.yaml` (not a permissive test `IdentityContext`), which is what actually caught and
  confirmed this dependency was resolved correctly, not merely asserted. See
  `docs/STAGE_1_OVERVIEW.md`'s S1.4 section for the complete implementation record.
- **Date:** 2026-08-05 (Stage 1, S1.4)

## Decision: PR #38's CI flake root cause is a pre-existing frozen-DB_PATH test-isolation defect, not a defect in S1.4/S1.6 feature logic -- fixed by resolving BARTH_DB_PATH fresh at daemon-startup time
- **Decision:** After three CI failures on PR #38 (S1.4 + S1.6), each a different specific test
  in `tests/test_notifications_api.py` failing with `sqlite3.OperationalError: database is
  locked`, investigation was escalated per explicit user direction: isolate the exact root cause
  rather than retry or work around symptoms, and restore previous scheduler behaviour before
  merging. Root cause, confirmed by direct code reading and a targeted reproduction (not
  speculation): `bartholomew_api_bridge_v0_1/services/api/db.py`'s `DB_PATH` was a module-level
  constant, resolved from `BARTH_DB_PATH` exactly once at this module's first import. Because
  pytest imports (collects) every test module before running any test, and roughly a dozen test
  files each set `os.environ["BARTH_DB_PATH"]` to their own private tempdir immediately before
  importing `app.py` -- each expecting an isolated database -- only whichever file's import
  happened to run last during collection actually determined the value every later `from .db
  import DB_PATH` (in `app.py`, in `routes/liveness.py`) would ever see; a small standalone
  reproduction (two dummy test files, each printing `os.environ["PROBE"]` at import time and at
  test-run time) confirmed every test in the session observed whichever file was collected *last*,
  not its own file's assignment. Every one of these test files' `KernelDaemon` -- including its
  live background scheduler -- ended up silently sharing ONE physical SQLite file instead of each
  getting its own, so one file's scheduler ticks and another, entirely unrelated file's
  foreground HTTP-triggered writes (`test_notifications_api.py`'s notify-skill settings save) were
  genuinely racing each other for the same file under SQLite's finite busy-timeout. This defect
  predates PR #38 entirely (`tests/test_governance_api.py`'s own `_live_db_path()` helper already
  carried a code comment describing and working around it) and is not a logic bug in the
  `awaiting_response_check` scheduler drive or any other S1.4/S1.6 code. S1.4's addition of a
  fifth, unconditionally-registered scheduler drive (running inside every `KernelDaemon`
  system-wide, not just S1.4-relevant ones) measurably raised the number of concurrent
  scheduler-vs-foreground contention windows across the whole test suite, which is what tipped
  this pre-existing, latent architectural fragility into visibly, repeatedly failing CI, without
  itself being the underlying defect.
- **Fix:** `db.py` gains `resolve_db_path()`, which reads `BARTH_DB_PATH` fresh on every call
  instead of caching it; `app.py`'s `startup()` now calls it at `KernelDaemon` construction time
  instead of importing the frozen `DB_PATH` constant. Because pytest's collect-everything-first
  behaviour means even a "fresh" read at fixture-setup time can still observe whichever file was
  *collected* last rather than the currently-running file's own assignment, every affected test
  file's `client` fixture (`test_api_admission_gate.py`, `test_api_chat_runtime_contract.py`,
  `test_awaiting_response_api.py`, `test_consent_api.py`, `test_governance_api.py`,
  `test_notifications_api.py`, `test_onboarding_api.py`, `test_self_state_api.py`,
  `test_stage0_alive.py`) now re-asserts its own `BARTH_DB_PATH` immediately before constructing
  its `TestClient`, guaranteeing genuine per-file isolation regardless of collection order.
  `test_governance_api.py`'s `_live_db_path()` workaround (which read the now-decoupled frozen
  `DB_PATH` constant specifically because that used to reliably equal whatever file was actually
  live) is simplified to return the module's own `_DB_PATH` directly, since the two are correctly
  the same value again. No change to `awaiting_response_check`'s registration, cadence, or any
  other S1.4/S1.6 approved-design element; `db.DB_PATH` remains for the one remaining consumer
  (`routes/liveness.py`) and the `atexit` checkpoint hook, both out of scope (neither implicated
  in the observed failures, and both behave identically to before in a real single-process
  deployment, where `BARTH_DB_PATH` is set once before the process starts and never changes).
- **Alternatives considered:** (a) Add application-level retry/backoff around the specific writes
  that failed (`notify.py`'s `_save_settings()`) -- rejected: doesn't address the root cause (two
  daemons genuinely sharing one file), only masks its symptom, and the busy-timeout window it
  would need to outlast is unbounded under this defect (two independent daemons' schedulers can
  contend indefinitely, not just briefly). (b) Remove `awaiting_response_check` from the drive
  registry or make it conditional -- rejected without a separate approval: this is part of S1.4's
  already-approved design (`docs/S1_4_AWAITING_RESPONSE_DESIGN.md` Sec 6), and investigation found
  no implementation defect in the drive itself to justify changing it; the drive was only ever the
  trigger that made a pre-existing, unrelated defect visible more often, not the defect itself.
  (c) A broader rewrite of the shared-test-database architecture (e.g. per-test-function isolation,
  a conftest-level fixture consolidating all ~9 files) -- rejected as larger than necessary: fixing
  the one proven, narrow defect (frozen constant + no re-assertion at the point of use) fully
  restores isolation without restructuring how these tests are written.
- **Why:** The user's explicit direction was to isolate the exact root cause rather than work
  around symptoms, and to restore previous (i.e., correctly isolated) scheduler behaviour without
  changing S1.4/S1.6's approved scope. A verifiable, minimal fix to an objectively-broken caching
  assumption satisfies both: it is provably correct (confirmed by direct reproduction), narrowly
  scoped (two production call sites plus nine test fixtures, each a small and obviously-correct
  change), and requires no revision to either sub-stage's approved design.
- **Consequences:** New regression coverage: `tests/test_api_db_path_isolation.py` proves
  `resolve_db_path()` reflects the current env var (not a cached value), creates its parent
  directory, falls back to the default when unset, and that two sequential `TestClient` lifecycles
  with different `BARTH_DB_PATH` values get genuinely isolated `KernelDaemon` instances. The
  combined previously-failing test set (`test_notifications_api.py` plus every other file sharing
  this pattern) was run repeatedly clean after the fix, where it had failed on 3 of 3 prior CI
  attempts. PR #38 remains unmerged pending this fix's own CI verification, per explicit user
  instruction not to merge while the regression exists.
- **Follow-up correction (same day):** the first push of this fix only updated `app.py`'s
  `startup()` to resolve fresh; `routes/liveness.py` still imported the frozen `DB_PATH` constant
  for its three `/api/liveness/*` routes. Once `app.py`'s daemon and `liveness.py`'s routes could
  resolve to *different* files (each test file's daemon now correctly isolated, but liveness still
  reading whichever file happened to freeze `DB_PATH` at first import across the *entire* test
  suite -- not the 9-file subset this was verified against locally), CI caught a real second-order
  bug this fix introduced: `tests/test_stage0_alive.py::test_liveness_endpoints` failing with
  `sqlite3.OperationalError: no such table: nudges` (reading a file no daemon had ever
  initialized). This was missed locally because manually-composed local test runs happened to list
  files in an order where the frozen constant coincidentally pointed at *some* already-initialized
  daemon's file, masking the divergence; CI's full-suite run (true collection order across the
  whole codebase) did not get that same lucky coincidence. Fixed by making `liveness.py` call
  `resolve_db_path()` too, so its routes read the same file the live daemon actually uses.
  `db.get_conn()`/`init_db()` (unused dead code, confirmed by search) and `app.py`'s `atexit`
  checkpoint hook (fires once at interpreter shutdown, not implicated in any observed failure)
  were deliberately left on the frozen constant.
- **Date:** 2026-08-06 (PR #38 follow-up: scheduler/database test-isolation fix)

## Decision: One developing digital individual — competency and training architecture
- **Decision:** Adopt the direction recorded in an architecture-review handoff ("Bartholomew
  Project — New Direction & Repository Handoff") as canonical, after reconciling it against the
  current repository rather than accepting it uncritically: **Bartholomew is one developing
  digital individual**, not a collection of independent domain-specific applications or "Manager"
  brains. Areas of professional or practical responsibility — Estate Management, Finance, Travel,
  Vehicle Management, and any future one — are **competencies this one Bartholomew learns**,
  acquired the way a human employee develops competence: through training, instruction,
  correction, supervised work, experience, reflection, and accumulated judgement. A competency is
  learned judgement (knowledge, procedures, heuristics, relevant capabilities, experience/evidence,
  proficiency/confidence, supervision requirements) that informs the one Executive; it is sharply
  distinct from a skill/capability (a relatively dumb, executable tool), and it must never become a
  second Executive, Memory authority, or Governance path. This is now recorded in
  `CONSTITUTION.md`'s "One Developing Digital Individual: Competencies and Training" section and
  conceptually extended into the Runtime Contract in `COGNITIVE_RUNTIME.md`'s "Competency,
  Training, and Learning" section.
- **Alternatives considered:** (a) Build Residential Estate Management first, as a working
  vertical slice, and generalise into a competency architecture afterward — rejected: the handoff
  and this reconciliation agree this inverts the correct order and risks locking in Estate-specific
  shortcuts (an `EstateExecutive`, `EstateMemory`, or similar) that would then need to be
  unwound, repeating the exact "two brains" mistake `MASTER_PLAN.md`'s 2026-07-21 architectural
  audit already found and fixed once (Stage 4.5, Runtime Convergence) for chat vs. skills. (b)
  Model competencies as a new kind of Skill/plugin inside the existing Skill Registry — rejected:
  a competency is learned judgement, not an executable tool; conflating the two would either weaken
  the Skill Registry's deliberately simple execution-mechanism contract or smuggle domain judgement
  into skill implementations, both of which `CONSTITUTION.md`'s Capability pillar already argues
  against. (c) Give each competency its own memory store/database for isolation and simplicity —
  rejected: this directly reproduces the "second memory architecture" problem `DECISIONS.md`'s
  Echo-roadmap-demotion entry already rejected for a different reason, and blocks the
  transferable-learning requirement (evidence from one competency legitimately informing judgement
  in another) that a walled-off store cannot support. (d) Accept the handoff's specific S5.1–S5.7
  numbering and stage content verbatim without checking it against the repository — rejected: this
  reconciliation independently verified the handoff's specific claims (`Planner.decide()` returns
  `None`; `SkillManifest` models executable tools with no competency concept;
  `memory_store.py`'s `memories` table already uses an open-ended `kind` string, not a fixed enum,
  governed by `memory_rules.yaml`) before adopting them, and adjusted stage numbering to fit
  existing, already-shipped Stage 5 history (S5.0) rather than renumbering it away.
- **Why:** `CONSTITUTION.md` already established, before this decision, that there is exactly one
  Executive, one Memory substrate ("the long-term competitive moat... decades of accumulated
  understanding of the user"), and cross-cutting Governance above every subsystem — and that
  "nothing should be designed specifically for bills," with the explicit generalisation test "if
  adding the third domain requires schema redesign, the architecture has failed." The handoff's
  "one developing digital individual" principle is not a new architecture; it is the direct,
  previously-unstated consequence of principles this repository already committed to, made
  explicit specifically for *learned competence* rather than only for capabilities/tools. Direct
  repository verification confirmed the underlying gap the handoff names is real: `Planner.decide()`
  (`bartholomew/kernel/planner.py`) is a stub that always returns `None` — there is no Executive
  reasoning path today that could apply a competency even if one existed — and `SkillManifest`
  (`bartholomew/kernel/skill_manifest.py`) models exactly what `CONSTITUTION.md`'s Capability
  pillar already says a skill should be: an executable action with permissions, not a carrier of
  domain judgement.
- **Consequences:** `CONSTITUTION.md` is amended (see its 2026-08-08 entry). `COGNITIVE_RUNTIME.md`
  gains a conceptual, not-yet-implemented "Competency, Training, and Learning" section. `ROADMAP.md`
  Stage 5 is restructured (see the paired decision below) around this principle. This decision does
  **not** authorise implementing any competency runtime, training pipeline, memory-kind change, or
  Estate Management feature — it is a documentation-only architectural decision that future,
  separately-approved implementation work (`ROADMAP.md` Stage 5 S5.1 onward) must be consistent
  with, the same status the hybrid-local-first deployment decision has for Stage 6.
- **Date:** 2026-08-08

## Decision: Stage 5 restructured around competency and training before live initiative
- **Decision:** `ROADMAP.md`'s Stage 5 is renamed from "Initiative engine (scheduled check-ins +
  workflows)" to "Developing Agency (competency, training, learning, then initiative)" and
  resequenced: **S5.1 — Competency architecture, S5.2 — Training and knowledge acquisition, S5.3 —
  Executive competency reasoning, S5.4 — Experience → learning/consolidation loop** now precede
  what was previously S5.1 onward (typed cadence, default-off consent/mute, quiet-hours defer,
  dry-run, rationale logging, live check-in/weekly-review/next-best-action drives), which is
  preserved unchanged in substance as **S5.5 — Initiative safety scaffolding, S5.6 — Dry-run
  proactive reasoning, S5.7 — Controlled live initiative**. S5.0 (deterministic scheduler-schema
  readiness, already shipped 2026-07-25) is unchanged. Residential Estate Management is recorded as
  the first competency to be trained into the S5.1 architecture, worked as the "architecture
  acceptance test" (`ROADMAP.md`'s new subsection of that name) rather than as production
  functionality to build ahead of the architecture.
- **Alternatives considered:** (a) Leave Stage 5 as the initiative engine and treat competency
  architecture as an entirely separate, unnumbered workstream — rejected: `ROADMAP.md`'s own
  "Sequencing (locked)" principle for Stage 5 already established that safety scaffolding must
  precede live proactivity; the same reasoning applies one level earlier — competent reasoning must
  precede scheduling *when* to act on it, so treating them as unrelated tracks would obscure a real
  dependency. (b) Insert competency work as new sub-stages numbered S5.8+ after the existing
  initiative-engine sub-stages, leaving S5.1–S5.7's existing content untouched — rejected: this
  would let live initiative (S5.7 under that numbering) ship before the Executive has any
  competency machinery to reason with, which is the exact ordering problem this decision exists to
  fix; numbering competency work *after* initiative in the sequence would misstate the true
  dependency order even if the labels were technically distinct. (c) Adopt the handoff's suggested
  S5.0–S5.7 labels without adjustment — rejected as this repository's actual S5.0 already has
  different, shipped content (scheduler-schema readiness, not "runtime prerequisites" in the
  handoff's generic sense); the labels are kept but their content is grounded in this repository's
  real history, not copied verbatim.
- **Why:** `Planner.decide()` (`bartholomew/kernel/planner.py`) returns `None` unconditionally
  today — confirmed by direct reading, not assumed from the handoff. There is no Executive
  reasoning path an initiative/proactivity feature could meaningfully build on without first
  building the competency-retrieval-and-application machinery S5.1–S5.4 describe. Building
  proactive scheduling (the old S5.1 onward) ahead of that would produce a scheduler that can
  decide *when* to speak but still has nothing genuinely competent to say — the reverse of the
  actual dependency.
- **Consequences:** No Stage 5 implementation work of any kind has started; this decision changes
  only sequencing and documentation, matching the "documentation-only, no implementation
  authorised" status every other Stage 5 planning decision in this document already carries. Live
  proactive Stage 5 behaviour (S5.7) still additionally requires Stage 1's user-facing governance
  shell, per the existing 2026-07-28 decision recorded in `ROADMAP.md`'s "Near-term milestone
  plan" — this decision does not change that requirement, it only inserts S5.1–S5.4 earlier in the
  overall sequence. The pre-existing reflection-ownership implementation gap
  (`COGNITIVE_RUNTIME.md`'s "Reflection ownership" section; this document's "Reflection ownership —
  target architecture" entry above) is now explicitly S5.4's responsibility to close, since the
  experience→learning loop cannot have a single authoritative composition step while two reflection
  pipelines run independently. `MASTER_PLAN.md`'s P3 backlog item and "Next 3 Moves" section are
  updated to match.
- **Date:** 2026-08-08

## Decision: Personal, potentially generalisable, and system-level learning are architecturally distinct
- **Decision:** The Bartholomew currently being developed is a predecessor of the Bartholomew
  system eventually released to customers. During development, testing, and later real-world use,
  an individual Bartholomew accumulates substantial personal knowledge, experience, corrections,
  procedures, heuristics, preferences, outcomes, and context. Three categories of what gets learned
  must remain architecturally distinguishable from the moment candidate learning is produced:
  **personal learning** (belongs to a particular user/instance — preferences, routines,
  relationships, household/property information, personal history, trusted contractors, private
  documents, and similar; stays within that individual's governed memory unless an explicit,
  appropriate mechanism permits otherwise; never automatically becomes global/shared/product-level
  knowledge); **potentially generalisable learning** (a candidate lesson that might improve future
  Bartholomew versions — a heuristic, an improved procedure, a recurring failure mode, a
  correction to an incorrect assumption, etc. — that must never be automatically promoted); and
  **system/product learning** (an observation about Bartholomew itself — excessive false positives,
  a reasoning strategy that repeatedly fails, a safety check needed earlier, a missing procedure, a
  consistently inappropriate default — distinguishable from both personal memory and ordinary
  competency knowledge). A future, entirely conceptual generalisation pipeline is recorded (not
  built): individual experience → reflection → candidate learning → classification → privacy and
  provenance evaluation → de-identification where genuinely possible → consent/Governance as
  required → validation → generalised lesson → possible incorporation into future Bartholomew
  training, competency definitions, procedures, defaults, or product releases. **Removing a
  person's name alone does not make information non-personal** — genuine de-identification,
  provenance, consent, sensitivity, re-identification risk, confidence, validation, Governance, and
  auditability must all be considered before any generalisation, and where safe generalisation
  cannot be established, the learning remains individual. This is now recorded in
  `CONSTITUTION.md`'s "Personal learning vs. potentially generalisable and system-level learning"
  section and `COGNITIVE_RUNTIME.md`'s "Personal, generalisable, and system-level learning
  classification" section.
- **Alternatives considered:** (a) Treat all candidate learning as eligible for generalisation once
  PII is scrubbed — rejected: this document's own text above states plainly that name-removal alone
  is insufficient, and treating scrubbing as sufficient is exactly the failure mode (re-identification
  risk, context-based identifiability) this decision exists to prevent. (b) Treat all learning as
  permanently individual, with no future generalisation path even conceptually reserved — rejected:
  this would foreclose legitimate future product improvement (a corrected procedure, a safer
  workflow, a fixed competency gap) that a properly governed process could validly surface, and the
  requesting instruction was explicit that the future distinction must remain *possible*, not that
  it must be permanently prevented. (c) Build the cross-instance/product-level generalisation
  pipeline now, as part of this documentation pass — rejected and explicitly out of scope: S5.1
  (the first competency-architecture implementation stage) has not started, there is no cross-user
  infrastructure of any kind in this repository, and building transport/de-identification/validation
  machinery ahead of the single-instance competency architecture it would eventually feed from
  would repeat the exact "build the specific thing before the general architecture" mistake the
  Estate Management acceptance test (`ROADMAP.md`) already exists to avoid, one level up.
- **Why:** This does not conflict with any existing architectural invariant — it extends ones
  already established. `CONSTITUTION.md`'s Sovereign Principle ("the user is always the final
  authority... optimise for maximum trust"), the data-portability invariant ("trust may be a
  product advantage; lock-in must not be"), and the hybrid-local-first deployment decision (the
  trusted local runtime is authoritative for sensitive memory, not dependent on cloud availability)
  already establish that an individual's data belongs to that individual by default. This decision
  makes explicit a dimension those invariants did not previously name directly: protection against
  an individual's personal experience silently becoming *another user's* or *the product's*
  knowledge, not just protection against external/cloud access. It is the direct architectural
  consequence of treating "one developing digital individual" (the same-day "One developing digital
  individual — competency and training architecture" decision above) as *this* individual, not an
  interchangeable instance whose memory is fungible with any other Bartholomew's.
- **Consequences:** `ROADMAP.md`'s Stage 5 S5.1 (competency architecture) and S5.4 (experience →
  learning loop) exit criteria now require that competency/candidate-learning records carry this
  classification and sufficient provenance — a data-shape requirement on the architecture, not an
  implementation of any cross-instance mechanism. No code, schema, de-identification pipeline,
  consent flow, or transport mechanism is implemented by this decision. `ASSUMPTIONS.md` gains a
  new entry (A8) recording that whether S5.1's classification will actually prove sufficient for a
  later, still-undesigned generalisation pipeline is itself unverified. This decision does **not**
  authorise designing or implementing cross-user/global learning infrastructure — that remains
  separate, future, and its own explicitly-approved work, likely well beyond S5.1.
- **Date:** 2026-08-08 (same-day follow-up to the two entries above)

## Decision: Usable POC / time-to-real-use prioritisation
- **Decision:** A repository-grounded assessment (2026-08-12) is approved in principle, with one
  modification. The assessment found: Bartholomew's persistence (Phase B), governance (Stage 1),
  and competency-retrieval (Stage 5 S5.1–S5.3) machinery are real, well-built, and well-tested, but
  none of it has generated real-world usage feedback, because ordinary conversation writes nothing
  durable and retrievable — chat only touches short-term Working Memory and an audit-shaped
  `action_reflection` record. S5.3's relevance-gated retrieval seam (`competency_reasoning.py`,
  merged the same day as this decision) is reachable only through S5.2's formal training-ingestion
  API, not through anything an ordinary conversation would trigger. The `notify` skill has no
  delivery channel outside the browser tab. `sentence-transformers` is commented out in
  `requirements.txt`, so retrieval runs on a deterministic fallback embedder whose scores S5.3's
  own design doc found anti-correlated with relevance. **Root cause identified as sequencing, not
  architecture**: the five-pillar architecture appears capable of the intended end-to-end loop;
  what actually produced this outcome is `CONSTITUTION.md`'s "Development Philosophy" section,
  followed faithfully, which states the project deliberately spends more time designing than
  coding and that correct architecture outweighs rapid feature delivery.

  The requested modification: **Personal Memory Capture and Recall is approved as the Usable POC's
  first vertical slice, not as the definition or boundary of the Usable POC.** The broader Usable
  POC objective is a progressive, multi-slice demonstration of: real-world input → Observation/
  Interpretation → persistent memory → retrieval/reasoning → Recommendation/proactive surfacing →
  user approval where required → at least one real governed Action → visible real-world result.
  Later slices are expected to expand toward proactive surfacing and at least one genuine governed
  action, not stop at memory/retrieval.

  The following principle is formalised as binding for near-term work, recorded in new canonical
  document `docs/TILT.md` (see the "Canonical SSOT docs" amendment above):

  > Once a vertical slice is sufficiently functional to generate meaningful real-user feedback,
  > real-world testing takes priority over additional polish or hardening — unless a defect
  > threatens safety, governance, privacy, data integrity, architectural validity, or the validity
  > of the experiment itself.

  And the prioritisation test: **"What real Bartholomew capability does this unlock for the
  tester?"** — infrastructure work without a strong answer is generally deferred until real usage
  demonstrates the need for it.

- **Alternatives considered:**
  (a) Continue the pre-existing Stage 5 sequence (S5.4 → S5.5 → S5.6 → S5.7) to completion before
  any real-world testing begins — rejected: this is the exact pattern the assessment identified as
  the problem, and would produce a fully-built initiative/proactivity system with nothing real yet
  proven to be proactive *about*, since S5.4–S5.7 as previously scoped still assume competency
  material enters via formal training, not organic use.
  (b) Adopt Personal Memory Capture and Recall as the complete definition of the Usable POC —
  explicitly rejected by the requesting instruction: memory/retrieval alone would leave the loop's
  Recommendation/Action/visible-result stages undemonstrated, and the architecture would still be
  unproven on the harder half of the loop (governed real-world action).
  (c) Rewrite `CONSTITUTION.md`'s "Development Philosophy" section directly, in this same pass —
  initially rejected in favour of a new, narrower canonical document (`docs/TILT.md`) taking
  explicit, temporary precedence over that one section for sequencing only, on the reasoning that
  the requesting instruction's document list did not include `CONSTITUTION.md`. **Superseded the
  same day, on explicit user request, by the amendment below:** `CONSTITUTION.md`'s "Development
  Philosophy" section is reconciled directly rather than left as a standing, if bounded,
  contradiction — see the "Amended 2026-08-12 (same day, reconciliation)" entry below.
  (d) Treat this as a full architecture reassessment or redesign — explicitly out of scope per the
  requesting instruction ("we are correcting the project's execution priorities, not redesigning
  Bartholomew"); no five-pillar architecture change is made or proposed by this decision.
  (e) Create `docs/CURRENT_STATE.md` as a new current-state document, as tentatively listed in the
  requesting instruction ("if necessary") — evaluated and **not created**: `MASTER_PLAN.md`
  already serves as the current-state SSOT per its own "Single Source of Truth" framing and
  README's description of it, and this pass corrects the one material staleness found (Stage 5
  S5.3's status, which read "not started" after implementation had already merged) directly in
  `MASTER_PLAN.md` and `ROADMAP.md`. A second, competing current-state document would itself be
  the kind of infrastructure-without-demonstrated-need this decision exists to discourage, and
  would conflict with `MASTER_PLAN.md`'s own "No doc sprawl" non-negotiable.

- **Why:** The five-pillar architecture (Governance, Executive, Memory, Capability, Experience) is
  not the bottleneck — extending S5.3's already-generic retrieval/selection seam to a new memory
  kind is additive, not a redesign. The bottleneck is that engineering effort has been driven by
  hypothetical future requirements and internal correctness rather than by observed real-world
  need, exactly as `CONSTITUTION.md`'s own pre-existing "Consumer-value gate" (§6) warns against
  but has not, in practice, been applied as an actual gate to recent Stage 5 sequencing. Making
  real-world testing the default once a slice is minimally functional — rather than the reward for
  finishing a fully-hardened stage — directly targets that.

- **Consequences:**
  - `ROADMAP.md` gains a new "Usable POC — progressive vertical slices" section, ahead of the
    Stage gates, and its Stage 5 table/preamble/"Near-term milestone plan" are updated: S5.3 is
    corrected from a stale "not started" to "done" (merged `a4f094b`), and S5.4–S5.7 are marked
    **deferred, not abandoned** — expected to be informed by Usable POC slice feedback rather than
    built ahead of it.
  - `MASTER_PLAN.md`'s "Canonical docs" list grows to 14 (adds `docs/TILT.md`) and its "Next 3
    Moves" section is rewritten around the new sequence; `README.md`'s "13 canonical documents"
    reference is corrected to 14.
  - This decision authorises planning/documentation only. It does not authorise implementation of
    Personal Memory Capture and Recall or any other vertical slice — each still requires its own
    separate, explicit approval, per `MASTER_PLAN.md`'s Doc Governance section and the User
    Approval Gate below.
  - No code, tests, or configuration are changed by this decision.
- **Date:** 2026-08-12
- **Amended 2026-08-12 (same day, reconciliation — not a new substantive decision):** the initial
  version of this decision resolved the conflict with `CONSTITUTION.md`'s "Development Philosophy"
  section via alternative (c) above — `docs/TILT.md` taking explicit, temporary precedence over
  that one section, with `CONSTITUTION.md`'s own text left unedited and a sunset condition
  requiring later reconciliation. On review, that was corrected in favour of reconciling
  immediately rather than leaving a documented, if bounded, contradiction in place: `CONSTITUTION.md`'s
  "Development Philosophy" section is now **amended directly** (2026-08-12, per that document's own
  governance rule — see its amendment log) so that its architecture-first default no longer implies
  holding back a vertical slice that is already sufficiently safe and functional to generate
  meaningful real-world feedback, while every other engineering standard in that section and
  document is preserved unchanged. `docs/TILT.md` is correspondingly reworded from "temporarily
  supersedes `CONSTITUTION.md`" to "is the tactical detail underneath a principle `CONSTITUTION.md`
  now states directly" — its "What this does NOT change" and "Sunset condition" sections no longer
  describe an unresolved precedence conflict, because none remains. This amendment changes no
  substantive decision beyond the already-approved time-to-real-use principle; it only removes a
  self-imposed, no-longer-necessary temporary contradiction between two canonical documents.
- **Date (amendment):** 2026-08-12

## Decision: Usable POC slice 1 implementation approved
- **Decision:** Implementation of the Usable POC's first vertical slice (Personal Memory Capture
  and Recall) is **approved and delivered** — commit `2d443a9`, approved on independent review.
  This is the separate, explicit implementation approval the "Usable POC / time-to-real-use
  prioritisation" entry above required and deliberately withheld; the planning note
  (`docs/POC_SLICE_1_MEMORY_CAPTURE_RECALL.md`, approved 2026-08-14 in `4de2962`) is its scope.

  Three sub-decisions were fixed at approval:

  **(a) The notification channel is a provider-agnostic outbound webhook.** A configurable-URL
  JSON HTTP POST, with no provider SDK, payload shape, or code branch. `ntfy` is the intended
  first real-world test endpoint, supplied as configuration — explicitly *not* an architectural
  dependency. Chosen over SMTP/email because a topic-style webhook needs no stored credential at
  all, so the slice adds no credential-storage surface. Pinned by a test asserting no
  provider-specific identifier appears in the delivery code.

  **(b) The fact extractor is POC scaffolding, not the long-term boundary of memory capture.** Its
  pattern set is deliberately narrow and provisional. Broadening what counts as a capturable fact
  is real, expected future work informed by real usage — not scoped or designed here. Recorded
  because a narrow extractor is only acceptable *as* scaffolding; treating it as the definition of
  "a personal fact" would be a misreading with real product consequences.

  **(c) Two selection passes, not one merged call — a deviation from the planning note, accepted.**
  The note said the retrieval filter widens to cover personal-fact kinds; it does. But
  `competency_reasoning.select_relevant()` commits to a single domain per selection (S5.3 Decision
  C, no cross-competency transfer), so passing facts and competencies through one call would let a
  recalled personal fact evict an applicable competency, or vice versa — a silent S5.3 regression.
  The implementation therefore runs two independent selections over the same retrieved candidates.
  The relevance gate itself is reused byte-for-byte in both; nothing about it was generalised,
  copied, or parameterised.

- **Alternatives considered:**
  (a) SMTP/email as the notification channel — rejected: needs stored credentials, adding a
  credential-management surface to a slice whose whole purpose is reaching real use quickly.
  (b) A bundled notification provider integration — rejected by explicit approval clarification:
  it would make a configuration choice into an architectural dependency.
  (c) One merged `select_relevant()` call, as the planning note's wording implied — rejected on
  the mechanism above; it would have regressed S5.3 silently, which is exactly the class of defect
  the relevance gate was added to prevent.
  (d) A new memory kind for personal facts — rejected: `memory_rules.yaml` already governs
  `user_profile`/`user_schedule` with `always_keep` rules, and a new kind would have needed new
  governance rules, i.e. new architecture, which the slice explicitly excludes.
  (e) Generalising `select_relevant()` to handle both record families natively — rejected as
  premature: it would modify a mechanism S5.3 shipped with measured, characterised behaviour,
  ahead of any evidence that the adapter approach is insufficient.

- **Why:** The slice closes the three concrete gaps the 2026-08-12 assessment identified — ordinary
  conversation wrote nothing durable and retrievable, the one working retrieval seam saw only
  formally-trained material, and the one notification mechanism had no channel outside the browser
  tab — without introducing a new memory kind, consent gate, governance category, or write path.
  Every capture is a *proposal* to the existing governed write path; nothing in the new code
  decides whether content may be stored.

- **Consequences:**
  - `ROADMAP.md`'s "Usable POC — progressive vertical slices" section records slice 1 as done with
    a completion record; its "Not yet approved for implementation" line is removed as now false.
  - `docs/TILT.md`'s "First vertical slice" section is marked implemented, and its **acceptance-bar
    wording is clarified** (wording only, no behaviour change): it previously read "recalled,
    unprompted, in a later *unrelated* conversation," which read literally asks for a memory to
    surface in a conversation it has nothing to do with — the opposite of correct behaviour, and
    precisely what the relevance gate prevents. It now reads: *a fact stated in one conversation
    can be relevantly recalled in a later separate conversation without the user restating the
    fact.* The same clarification is carried in the planning note.
  - One new runtime configuration value exists: `BARTHOLOMEW_NOTIFY_WEBHOOK_URL`. Unset (the
    default) preserves the previous log-only behaviour exactly, so this is additive.
  - **Slice 1's completion does not authorise slice 2.** Per `docs/TILT.md`, the next step is real
    use of this slice; slice 2 is scoped from that feedback and needs its own approval. Stage 5
    S5.4–S5.7 remain deferred, unchanged.
  - Open issues #42 and #22 are untouched and remain open.
- **Date:** 2026-08-14

## Decision: One shared Bartholomew platform; many strongly isolated personal Bartholomew identities
- **Decision:** Bartholomew is architecturally **one shared platform serving many strongly
  isolated personal Bartholomew identities**, not a product of which each user receives a
  duplicated copy. Three layers are permanently distinguished: (1) the **Bartholomew platform** —
  shared software, common Executive and Governance mechanisms, capability infrastructure, model
  access, updates; (2) **underlying intelligence/resources** — LLMs, multimodal models, specialist
  AI services, reasoning engines, which Bartholomew *uses* and which are replaceable; and (3) a
  **user's personal Bartholomew** — the persistent, isolated state carrying that individual's
  memories, preferences, goals, relationships, permissions, history, learned understanding,
  trust/autonomy configuration and world model. Individuality comes from layer 3, never from
  duplicating layers 1 and 2. Four consequences are binding architectural direction:
  **(a) Bartholomew is not the LLM** — the model layer is a cognitive resource, and models must be
  upgradable or replaceable without destroying a user's Bartholomew identity;
  **(b) identity is portable** — logically independent of the device, client, server, database,
  AI provider, model generation and deployment topology currently serving it, subject to
  Governance, security, privacy and user control; **(c) the client may eventually be lightweight**
  — a downloaded application need not contain Bartholomew's whole intelligence, provided local
  Governance authority (parking brake, local device permissions, credential boundaries, safe
  degradation, loss-of-connectivity behaviour) remains locally enforceable, so that a cloud outage
  can never leave a user unable to locally stop Bartholomew acting on their devices;
  **(d) isolation between personal identities is non-negotiable** — user ownership/tenancy becomes
  a first-class concept wherever persistence or execution eventually requires it, and personal
  learning never becomes shared platform knowledge except through the explicit, governed,
  not-yet-built generalisation process this document's "Personal, potentially generalisable, and
  system-level learning are architecturally distinct" entry already governs.
  The current single-user deployment is conceptually **the first personal Bartholomew identity
  running on an early deployment of that platform** — not a different, throwaway system.
  This entry is **documentation-only and authorises no implementation.**
- **Alternatives considered:** (a) **Leave it undocumented** and decide at commercialisation time —
  rejected: the repository-grounded review that produced this entry found the canonical set
  effectively silent on how many users exist, with a single incidental mention of "single-user"
  across fourteen documents. Silence is not neutral; it lets each future session assume whichever
  model is locally convenient, which is exactly how one-process-equals-one-user hardens into
  architecture. (b) **Fix it in code now** — add ownership columns, a tenancy dimension, an
  authenticated identity, per-user runtime state — rejected as a direct violation of
  `docs/TILT.md` and `CONSTITUTION.md`'s revised Development Philosophy: it would convert a
  documentation decision into a large infrastructure project ahead of any real-world use of
  slice 1, and would be premature abstraction against requirements no user has yet exercised.
  (c) **Record it as a per-user deployment model instead** (each customer gets their own copy of
  the whole stack) — rejected: it makes model/infrastructure upgrades a per-customer migration,
  makes shared platform learning structurally impossible, and contradicts the product intent that
  a user's Bartholomew persists across devices and infrastructure rather than being pinned to one
  installation. (d) **Fold it into the existing hybrid local-first entry** — rejected as
  conflating two genuinely different questions; see "relationship to existing decisions" below.
- **Why:** The individuality of a person's Bartholomew is a product characteristic, and the review
  found nothing in the canonical documentation that prevented a future session from implementing
  it the expensive and fragile way — by duplicating the stack per user, or by equating Bartholomew
  with whichever model it currently calls. Both would be extremely difficult to reverse later: the
  first turns every platform improvement into an N-customer migration, and the second makes a
  model-generation change an identity-destroying event. Writing the distinction down now costs a
  documentation pass; discovering it after customers exist costs a rewrite. The same argument
  applies to isolation: retrofitting ownership onto persisted state and background execution after
  a second user exists is materially harder than reserving the concept while there is exactly one.
- **Relationship to existing decisions:** This entry **extends, and does not replace,**
  "Deployment architecture — hybrid local-first" (2026-07-28) — **superseded 2026-08-17** by the
  server-centric entry, which restates this relationship in its clause (f). That entry answers *where authority
  and compute sit* for one user (local-authoritative for sensitive memory, governance and
  emergency shutdown; optional cloud services). This entry answers *how many users there are, what
  a personal Bartholomew is, and what must remain replaceable underneath it*. The two are
  consistent: the lightweight-client direction recorded here is explicitly bounded by hybrid
  local-first's requirement that governance and emergency shutdown never depend on cloud
  availability. It also **builds on** "Personal, potentially generalisable, and system-level
  learning are architecturally distinct" (2026-08-08), which already established that personal
  learning is never auto-promoted; this entry supplies the platform context that made that
  distinction necessary, and does not restate or weaken it. It is constrained by "Usable POC /
  time-to-real-use prioritisation" (2026-08-12), which is why nothing here is implementation.
- **Consequences:**
  - `CONSTITUTION.md` gains a "One Platform, Many Personal Bartholomews" section — the enduring
    authority for the three-layer distinction, Bartholomew-is-not-the-LLM, identity portability,
    the client/Bartholomew boundary, local Governance authority, and isolation. It ends with a
    **binding conflict-surfacing rule** naming eight properties a future proposal may not silently
    trade away.
  - `COGNITIVE_RUNTIME.md` gains a "Personal-identity ownership" subsection under its ownership
    table, plus one ownership-table row, recording what the runtime assumes today and classifying
    each single-user assumption as acceptable-for-PoC, a documented migration seam, or a trap.
  - `CHECKLISTS.md` gains a "Platform and personal-identity architecture checklist" making the
    conflict-surfacing rule operational at change time.
  - `RISKS.md` gains three tech-debt watchlist entries for the concrete seams found in code.
  - `ASSUMPTIONS.md` gains A9 — that the current deployment serves exactly one personal identity,
    recorded as a deliberate, tracked assumption rather than an invisible one.
  - `ROADMAP.md`'s "What we will not do yet" gains an explicit line: multi-user/tenancy
    infrastructure is not current PoC scope. **No stage's scope grows as a result of this entry.**
  - **No code change is authorised by this entry.** The review deliberately found no defect
    requiring correction now: the single-user assumptions in the code are all either appropriate
    for the PoC or cheap to correct later, and the one candidate worth flagging (the globally
    unique `memories(kind, key)` index) is a documented seam, correctable by ordinary additive
    migration if and when ownership becomes real.
  - Future platform work — actual multi-user infrastructure, production tenancy, authentication,
    distributed services, client/server split, migration systems — remains **FUTURE PLATFORM
    WORK**, requiring its own proposal and approval. It must not be pulled into the current PoC
    roadmap.
- **Date:** 2026-08-15

## Decision: Parking Brake authority tiers — Personal/User and Platform/Admin
- **Decision:** The Parking Brake has **two distinct authority tiers**, and they are a MUST-HAVE
  pair. A **Personal/User Parking Brake** is independently enforceable per personal Bartholomew: it
  halts relevant execution — autonomous actions, scheduled execution, capability execution,
  external side effects, device/environment control — for that user's Bartholomew only, and the
  user remains the ultimate authority over execution performed on their behalf. A separate,
  higher-scope **Platform/Admin Parking Brake** allows authorised platform administration/
  governance to halt relevant execution across the entire platform in a serious safety, security,
  governance, systemic-defect, critical-operational or other platform-wide emergency, **without
  requiring every user's Personal brake to be activated individually.** Precedence is unambiguous:
  an active Platform/Admin halt **overrides** subordinate personal autonomy permissions, trust
  levels, approvals and execution authority, and **a user must not be able to override it** through
  personal settings; conversely one user's Personal brake **must never** halt, degrade, or alter
  the authority or state of another user's Bartholomew. The tiers compose **restrictively** —
  execution proceeds only if neither blocks it, and disengaging one never implies disengaging the
  other. The tiers are **orthogonal to the existing subsystem `scopes` axis** (`global`, `skills`,
  `sight`, `voice`, `scheduler`, `training`), which answers *what* is halted rather than *whose
  execution stops on whose authority*. Brake scope is **Governance authority, not a UI feature**:
  enforcement sits below the presentation layer at the execution boundary, and a client crashing,
  disconnecting, or being bypassed must not by itself invalidate the halt state. The Platform tier
  **does not replace local user authority**: wherever Bartholomew can act on a user's local devices
  or environment, that user must retain a locally enforceable means of stopping their own
  Bartholomew even when central services are unavailable, connectivity is lost, or the platform is
  malfunctioning — a platform outage must never leave local autonomous execution unstoppable.
  `COGNITIVE_RUNTIME.md`'s "The kill-switch: `ParkingBrake`" → "Authority tiers" subsection is the
  canonical authority for these semantics. This entry is **documentation-only and authorises no
  implementation.**
- **Alternatives considered:** (a) **Add `"platform"` as another string in the existing `scopes`
  set** — rejected, and specifically called out as a category error in the canonical text because
  it is the most likely wrong turn: scopes are cleared by the same ordinary `disengage()` any user
  can call, so a platform-wide safety halt expressed as a scope would be **user-overridable**,
  which is precisely the property this decision exists to guarantee against. It would also
  conflate "what class of execution is halted" with "on whose authority" — two genuinely different
  axes. (b) **Rely on a single global brake and activate it per user at incident time** — rejected:
  it makes a platform-wide emergency halt an O(users) operation performed under incident
  conditions, which is exactly when it will be slowest and least reliable, and it provides no
  mechanism a user cannot simply undo. (c) **Put the platform halt in the client or admin UI** —
  rejected: a halt that a client crash, disconnection, or bypass can invalidate is not a safety
  control. (d) **Build the two tiers now** — rejected as premature platform work: with one
  deployment and one user there is no platform to halt and no administrator distinct from the user,
  so the tier would be untestable against any real requirement and would violate `docs/TILT.md`.
  (e) **Design the narrower scoped-suspension system now** (disable one defective capability
  platform-wide, suspend an integration, allow read-only cognition, isolate a compromised
  subsystem) — rejected as explicitly out of scope; recognised as possible future extensibility
  only, not a MUST-HAVE, and not to be designed or implemented now.
- **Why:** The move to one shared platform serving many personal Bartholomews creates a governance
  question the single-user brake never had to answer: *whose execution stops, and who may stop it?*
  Left unstated, the most natural reading of the existing implementation — one persisted brake row,
  one `is_blocked(scope)` check — is that "Parking Brake" means one undifferentiated switch shared
  by everyone. That reading is cheap to prevent now with documentation and expensive to reverse
  after a platform exists, because both failure directions are severe: a global brake that any user
  can trip halts unrelated people's Bartholomews, and a brake with no platform tier leaves no way
  to stop a systemic defect except user-by-user. Recording precedence explicitly also settles the
  case that would otherwise be argued at incident time — whether accumulated personal trust or
  autonomy level can outrank a platform safety halt. It cannot.
- **Relationship to existing decisions:** **Extends** "One shared Bartholomew platform; many
  strongly isolated personal Bartholomew identities" (2026-08-15) — this is the Governance-authority
  consequence of that decision, and property 9 of its conflict-surfacing rule. **Bounded by**
  "Deployment architecture — hybrid local-first" (2026-07-28) — **superseded 2026-08-17**, but the
  clause relied on here was retained verbatim by the server-centric entry — whose requirement that governance
  and emergency shutdown never depend on cloud availability is what forbids reading the
  Platform/Admin tier as central-only control. **Consistent with** `CONSTITUTION.md`'s
  independent-emergency-shutdown invariant (§1) and with `GovernanceStore`'s existing
  revision-guarded `disengage()` invariant ("the brake can only become more restrictive without an
  explicit, confirmed loosening action"), which the restrictive-composition rule preserves rather
  than replaces.
- **Consequences:**
  - `COGNITIVE_RUNTIME.md`'s kill-switch section becomes the explicit canonical authority for brake
    scope, tiers and precedence, and gains an "Authority tiers" subsection. Its stale claim that
    brake state lives in `system_flags` is corrected: since Phase B stage B6 the write authority is
    `GovernanceStore` (`parking_brake_state`).
  - `CONSTITUTION.md` states the enduring two-tier requirement concisely and adds a **ninth
    property** to the conflict-surfacing rule; it does not restate the mechanics.
  - `CHECKLISTS.md`'s platform/personal-identity checklist gains one brake-tier item.
  - `RISKS.md` gains one tech-debt entry sharpening an **already-known, already-deferred** finding:
    the `sight`/`voice` seams still read the legacy `system_flags`-backed `ParkingBrake` while the
    write authority is `GovernanceStore` (found by B4, re-confirmed and deferred by B6 §1 finding
    5). Not a live safety hole — the capability behind those seams is inert, and no live caller
    reaches them — but under the tier model it acquires a new consequence: **tier awareness must be
    added once, in `GovernanceStore`,** so those seams should be consolidated onto it *before*
    tiers are introduced, or they would silently not honour them.
  - **No code change is authorised by this entry**, and none is required. The current single-user
    brake conceptually *is* the Personal/User tier and is sufficient for this stage; it is global
    only because there is exactly one user for it to be global over.
  - The Platform/Admin tier, tenancy-aware brake state, admin services, and any scoped-suspension
    system remain **FUTURE PLATFORM WORK** under `ROADMAP.md`'s "What we will not do yet". No
    stage's scope changed.
- **Date:** 2026-08-15

## Decision: Deployment architecture — server-centric Bartholomew with local/edge capability agents (supersedes "hybrid local-first")
- **Decision:** Bartholomew's core cognition and platform are **server-centric by default**. The
  persistent thing that is a user's Bartholomew — Executive/orchestration, model access, memory,
  reasoning, background cognition, learning and continuity — is intended to run as shared platform
  infrastructure rather than as a full runtime installed on each end-user device. The user's primary
  interface is a **web application** reachable from PC, phone or tablet. Optional native companion
  applications for platforms such as Windows, macOS, Android and iOS are **capability bridges / edge
  components, not independent Bartholomew brains**: they expose permitted device-local capabilities
  — files, applications, notifications, sensors, camera/microphone, screen/context, peripherals,
  local-network resources and other OS facilities — to the user's server-side Bartholomew. The model
  is a **"server brain + local nervous system"** hybrid. Eight clauses are binding:

  **(a) This is NOT a mandate to become cloud-only.** It defines where cognition runs *by default*,
  not that everything must move. Local or edge execution remains appropriate — and in some cases
  mandatory — where device access, privacy, latency, resilience, security, offline/degraded
  operation or platform restriction justify it. `CONSTITUTION.md`'s "Client versus Bartholomew"
  constraint ("this is not a licence to make Bartholomew cloud-only") is **not weakened** by this
  entry; this entry is the decision about the default, not permission to ignore that constraint.

  **(b) Locally enforceable stop authority survives, unconditionally.** Wherever Bartholomew can act
  on a user's devices or environment, that user must retain a locally enforceable means of stopping
  their own Bartholomew even when central services are unavailable, connectivity is lost, or the
  platform is malfunctioning. `CONSTITUTION.md`'s "central infrastructure must never become the only
  authority capable of stopping or constraining the system", its independent-emergency-shutdown
  invariant, and this document's "Parking Brake authority tiers" entry all remain in force and are
  **constraints on this decision**, not casualties of it. A platform outage must never leave local
  autonomous execution unstoppable.

  **(c) Core cognition is independent of any particular UI or end-user device.** No device, client
  or presentation surface may become a required component of cognition. The web application is a
  client, not the system.

  **(d) Device capabilities are explicitly discoverable and governed.** A device/agent declares what
  it can do; capabilities are not assumed. Windows, macOS, Android and iOS expose materially
  different facilities and must not be modelled as interchangeable. Every capability is subject to
  permission and Governance constraint, and capability availability is a runtime fact to be
  discovered and reported truthfully, never inferred.

  **(e) Physical embodiments attach through the same capability/device model.** Home hubs, robots,
  drones, vehicles and smart-home systems are, by default, capability-bearing devices attached to a
  user's Bartholomew — not independent full Bartholomew brains. A future requirement may justify an
  exception; it would need its own decision.

  **(f) Commercial multi-user operation uses shared platform/runtime infrastructure with strict
  per-user isolation** of identity, memory, permissions, devices, history, trust state and personal
  model. This restates rather than replaces this document's "One shared Bartholomew platform; many
  strongly isolated personal Bartholomew identities" entry, which remains the authority on the
  three-layer model, identity portability and isolation being non-negotiable.

  **(g) Global/platform administrative stop controls and individual-user Parking Brake controls are
  distinct concepts and must eventually be represented separately.** The authority on their
  semantics remains this document's "Parking Brake authority tiers — Personal/User and
  Platform/Admin" entry and `COGNITIVE_RUNTIME.md`'s "Authority tiers" subsection.

  **(h) This is TARGET architecture.** It is **not** a claim that production multi-tenancy, cloud
  infrastructure, native device agents, a device-capability protocol, or authentication currently
  exist. **None of them do.** The current system remains a single-user local development prototype,
  and that prototype is conceptually the first personal Bartholomew identity on an early deployment
  of this platform, not a throwaway. **This entry is documentation-only and authorises no
  implementation.**

- **Supersedes:** this document's **"Deployment architecture — hybrid local-first" (2026-07-28)**,
  which is marked superseded in place. Precisely what changes: that entry made *"a trusted local
  Bartholomew runtime … the authority for sensitive memory"* and rejected its own alternative (a),
  "a pure hosted web service, with the local runtime reduced to a thin client". **That clause is
  reversed for cognition and memory**: personal memory and core cognition are now intended to live
  server-side. What is **retained unchanged** from that entry: governance enforcement, the parking
  brake and emergency shutdown must not depend on cloud availability; and remote exposure of the
  runtime must not occur until authentication, authorization, transport security and a reviewed
  threat model are designed and separately approved, with "simple token auth" explicitly not assumed
  sufficient. Two competing deployment authorities are not left standing.

- **Alternatives considered:** (a) **Leave "hybrid local-first" in place and record the new
  direction elsewhere** — rejected: it would leave two canonical entries giving opposite answers to
  "where does sensitive memory live?", which is exactly the failure mode this document's "One
  authority per architectural concept" entry exists to prevent, and which the 2026-07-28
  reconciliation pass had to repair once already for reflection ownership. (b) **Amend the
  2026-07-28 entry in place** — rejected: it would rewrite the record of a decision that was
  correctly made on the evidence available then, and destroy the traceability of *why* the direction
  changed. Superseding preserves both. (c) **Create a numbered ADR file or an `adr/` directory** —
  rejected: the repository has no such convention, `DECISIONS.md` *is* the ADR store, and creating a
  parallel one would violate "Canonical SSOT docs (no doc sprawl)" (reasoning already recorded in
  `docs/PLATFORM_IDENTITY_ARCHITECTURE_REVIEW.md` §4). (d) **Defer the decision until the platform
  is actually built** — rejected for the reason the platform-identity entry gives: silence is not
  neutral, and each session otherwise assumes whichever topology is locally convenient.

- **Why:** The 2026-07-28 entry's local-authoritative-memory clause was written to protect
  sovereignty, and sovereignty is better served by portability and isolation than by physical
  locality: `CONSTITUTION.md` already makes identity "logically independent of the device, client,
  server, database, AI provider, model generation and deployment topology currently serving it".
  Requiring every user to host a complete Bartholomew also contradicts the product thesis — an
  assistant available to an ordinary individual, reachable from a phone — and would make the model
  layer, which `CONSTITUTION.md` insists is a replaceable supplier, into a per-device installation
  burden. Most of this direction was already permitted: `CONSTITUTION.md`'s "Client versus
  Bartholomew" section already anticipates a lightweight client, device-specific capability adapters
  and remote "background cognition"; the platform-identity entry already anticipates a client that
  "need not contain Bartholomew's whole intelligence". What was missing was a decision on the
  **default**, and the resolution of the one clause that genuinely conflicted. The sovereignty
  concern that produced the original rejection is answered by clause (b), which keeps the ability to
  stop Bartholomew local and unconditional — that, not the physical location of a database, is what
  makes the user the final authority.

- **Consequences:**
  - `DECISIONS.md`'s "Deployment architecture — hybrid local-first" carries a superseded banner
    pointing here. No other entry is retired: "One shared Bartholomew platform…", "Parking Brake
    authority tiers…", and "Usable POC / time-to-real-use prioritisation" all stand.
  - `CONSTITUTION.md`, `ASSUMPTIONS.md`, `COGNITIVE_RUNTIME.md`, `INTERFACES.md`, `ROADMAP.md`,
    `MASTER_PLAN.md` and `RISKS.md` take **pointer/consequential amendments only**. The decision is
    not restated in any of them.
  - **A new risk is created and recorded** (`RISKS.md`): server-centric cognition increases
    dependence on connectivity. Defined loss-of-connectivity behaviour and degraded-mode operation
    become requirements rather than nice-to-haves — they were already constitutionally required and
    remain unbuilt.
  - **`ROADMAP.md`'s and `MASTER_PLAN.md`'s current priority does not change.** The usable-PoC /
    time-to-real-use prioritisation and `docs/TILT.md` sequencing continue to govern what is worked
    on next. This entry describes a destination, and explicitly does not schedule the journey.
  - It remains true, and is unaffected by this entry, that the current API has **no authentication**
    and must not be exposed beyond localhost (`INTERFACES.md`, security stance).
- **Date:** 2026-08-17

## Decision: Parking Brake means inspect, but do not mutate
- **Decision:** While a user's Parking Brake is engaged, governed state may be **read** but must
  not be **changed**. Inspection is never blocked — a halt that hides what Bartholomew was about to
  do defeats the purpose of halting, because the user can no longer see what they are deciding
  about. Mutation is blocked until the brake is released, **including mutation the user requests
  through a legitimate surface**. The concrete case this was decided for is **pending consent
  resolution**: listing the inbox is allowed; approving is refused; denying is refused; the pending
  request stays `pending` and remains resolvable once the brake is disengaged. Four clauses are
  binding:
  **(a) Denial is refused too, despite looking like the safe direction.** It is not safe: denial
  marks the row denied *and clears its payload*, irreversibly. A halted system must not destroy the
  evidence of what it was asked to decide. The brake **defers** the decision; it does not make one.
  **(b) The brake's own operations are exempt.** Engaging, maintaining, auditing, inspecting and
  disengaging the brake are how the halt is controlled; gating them on the halt would make it
  impossible to release. Enforcement covers governed *work*, not governance itself.
  **(c) Enforcement sits at the execution boundary, not in the API.** `MemoryStore` raises
  `ParkingBrakeEngagedError`; the route only translates it to a 503. This follows this document's
  "Parking Brake authority tiers" entry — brake scope is Governance authority, not a UI feature —
  so a bypassed, crashed or replaced client cannot get around the halt.
  **(d) Gated on the brake being engaged at all, not on one scope.** Resolving consent mutates the
  user's memory, which belongs to none of the existing subsystem scopes (`skills`, `sight`,
  `voice`, `scheduler`, `training`); gating it on any single one would be arbitrary. A brake
  engaged for `voice` alone still blocks consent resolution. **This is the one interpretive choice
  in this entry** — the decision as given says "the Parking Brake is engaged" without qualifying by
  scope, and this implements that literally. If a scope-sensitive reading is wanted instead, that
  is a small follow-up against this entry, not a re-decision of the principle.
  `COGNITIVE_RUNTIME.md`'s "The kill-switch: `ParkingBrake`" → "Inspect, but do not mutate" is the
  canonical authority for these semantics; this entry records the decision and its reasoning, and
  does not restate them elsewhere.
- **Alternatives considered:** (a) **Leave the previous behaviour** — approving a queued write
  succeeded while braked, on the reading that the brake halts *Bartholomew* acting rather than *the
  user* deciding. Rejected: it produced an incoherent pair, where submitting new material was
  refused (`blocked_by_governance`) but the same content already queued could be committed. The
  front door was gated and the queued back door was not. (b) **Block approve but allow deny**, on
  the intuition that refusing is the conservative direction. Rejected for the reason in clause (a)
  above: denial is destructive and irreversible, so allowing it during a halt destroys state the
  user may want to review once they understand why they halted. (c) **Add a `memory` brake scope**
  and gate on that. Rejected as scope expansion: it changes the scope axis, `VALID_SCOPES`,
  validation and documentation, to express something the engaged flag already expresses. It remains
  available later if the scope axis grows for independent reasons.
- **Why:** The prior behaviour was found by a test written specifically to pin it *without*
  endorsing it, precisely so that changing it would have to be a recorded decision rather than
  silent drift. That worked as intended. The principle chosen is the one that makes a halt mean
  something a non-engineer can predict: **while the brake is on, Bartholomew shows you everything
  and changes nothing.** A rule that admitted exceptions for "safe-looking" mutations would be
  unpredictable at exactly the moment the user most needs predictability — they engaged the brake
  because something was wrong.
- **Consequences:**
  - `COGNITIVE_RUNTIME.md`'s Parking Brake section gains an "Inspect, but do not mutate"
    subsection. No other document restates the rule.
  - `bartholomew/orchestrator/safety/governance_store.py` gains `ParkingBrakeEngagedError` and
    `engaged_state_fail_closed()` / `..._off_loop()` — a brake-level read, alongside the existing
    scope-level `is_blocked_fail_closed()`.
  - `MemoryStore.approve_pending_sensitive_write()` / `deny_pending_sensitive_write()` refuse while
    braked; `list_pending_sensitive_writes()` is untouched.
  - `tests/test_consent_api.py::TestConsentResolutionUnderTheParkingBrake` is **rewritten** — it
    previously asserted the opposite for approve. That is the intended effect of a pinning test.
  - **No other call site was changed.** The rule is stated generally because it is general, but
    only consent resolution is enforced today; extending it to a further mutation is a small,
    separate change against this entry rather than a new decision.
  - No new brake scope, no change to the authority tiers, no change to `VALID_SCOPES`.
- **Date:** 2026-08-18

---

# Post-Test #1 decisions (approved 2026-08-20)

> The fifteen entries below record the decisions Taylor approved on 2026-08-20 as part of
> **Post-Test #1 Decision Register v2.2**
> (`docs/evidence/test-1/interpretation/BARTHOLOMEW_POST_TEST_1_DECISION_REGISTER_v2_2_FINAL_APPROVAL_CANDIDATE.md`).
>
> **`D1`–`D15` are cross-reference identifiers only, not a heading format.** Each decision is
> recorded here under this document's existing `## Decision: <title>` convention, with the register
> cross-reference stated in the body. The register remains the authoritative *post-test decision
> record*; this document is the canonical decision log, and the two are cross-referenced rather than
> duplicated. Where the register's own wording is the substance of a decision, it is carried through
> here rather than paraphrased into something narrower, broader, or softer.
>
> **Authority ordering (from the register §1, unchanged):** `CONSTITUTION.md` → canonical SSOT docs →
> approved implemented subsystem designs → direct Test #1 evidence → approved post-test
> interpretation/decisions → independent-review recommendations. No entry below silently narrows a
> constitutional or canonical invariant.
>
> **These decisions do not authorise implementation.** They establish authority for what must be
> true before particular classes of work proceed. Implementation work packages are proposed and
> approved separately, slice by slice, under this document's existing "User Approval Gate" decision.
> The readiness bands that sequence them are in `ROADMAP.md`'s "Post-Test #1 readiness bands"
> section; the safety gates (S1–S11) and product gates (P1–P9) they reference are summarised in the
> same place and defined in full in the register's §9 and §10.

## Decision: Burden Reduction Principle
- **Decision:** Bartholomew must reduce unnecessary cognitive, administrative, interaction,
  supervision, cleanup, and recovery burden. Deliberate sovereignty-preserving burden — meaningful
  consent, consequential approval, necessary safety/privacy decisions — is allowed, but must remain
  proportionate and comprehensible. **ABR (the burden-reduction ratio) remains conceptual until
  burden measurement is defined**; no numeric threshold is set by this entry, and none may be
  assumed. Source: Post-Test #1 Decision Register v2.2, D1.
- **Alternatives:** Treat burden as a UX quality attribute rather than a governing principle
  (rejected: Test #1's Phase B finding was that the system's *net* effect on the tester was
  negative, which a quality attribute would not have made decidable); set a numeric ABR target now
  (rejected: a threshold without a defined measurement is a number that cannot be honestly
  evaluated — see the deliberately-unresolved list at the end of this section).
- **Why:** Test #1 produced two independent burden findings (B-F002, "practically useless" /
  qualitative burden below break-even; B-F001, a queue whose health warnings grew the queue). Both
  are burden failures rather than feature gaps, and neither is visible to a roadmap organised only
  around capability.
- **Consequences:** Burden instrumentation is a **Band C** prerequisite for full Real-World Test #2.
  Deliberate burden must be justifiable as sovereignty-preserving, not merely traditional. A future
  slice that adds capability while increasing net user management is a regression under this entry
  even if every test passes.
- **Date:** 2026-08-20

## Decision: Internal triage does not justify interruption, and obligations are never silently lost
- **Decision:** Internal activity does not automatically justify interrupting the user. Triage
  evaluates: user need; whether consent, judgement, or action is actually required; urgency/risk;
  confidence; duplicate/stale status; internal resolvability; interruption cost; quiet hours and
  context. **Genuine obligations must never be silently dropped.** Deferred, consolidated, or
  suppressed items remain durably represented and auditable where appropriate until resolved.
  `awaiting_response` remains the canonical durable external-reply obligation mechanism — its
  existing approved role in `docs/S1_4_AWAITING_RESPONSE_DESIGN.md` is preserved, not replaced.
  Source: Post-Test #1 Decision Register v2.2, D2.
- **Alternatives:** Suppress low-value items outright (rejected: silent suppression of a genuine
  obligation is exactly the failure mode this entry exists to forbid — the burden reduction would be
  bought with lost commitments); keep surfacing everything and rely on the user to filter (rejected:
  that is the Test #1 behaviour, and B-F001/NUDGE-F001 are what it produced).
- **Why:** Test #1 showed both halves of the problem at once: a curiosity prompt fired immediately
  on startup with no user need behind it (START-N001), equivalent curiosity prompts persisted as
  separate unresolved items (NUDGE-F001), and queue-health warnings themselves became queue items
  (B-F001). The corrective for that must not become an excuse to drop things.
- **Consequences:** Triage behaviour is gated by P6, whose evaluation set and pass target are frozen
  before implementation. Suppression must be auditable. Cap-shedding may affect only policy-eligible
  system-generated items and never a genuine obligation; `awaiting_response` is exempt from silent
  expiry and cap shedding (see S1). Triage containment is a **Band A** prerequisite for further
  unattended testing.
- **Date:** 2026-08-20

## Decision: Ordinary-user and diagnostic information architecture are separated
- **Decision:** Separate the ordinary-user information architecture from the developer/admin
  diagnostic one. **Safety-critical controls remain directly available to the ordinary user** — this
  separation hides diagnostics, never safety. **The exact physical surface count and form remain
  open** and are not decided by this entry. Source: Post-Test #1 Decision Register v2.2, D3.
- **Alternatives:** One surface for everyone with progressive disclosure (not rejected — it remains
  a permissible implementation of this entry; what is decided is the *separation of concerns*, not
  the number of surfaces); simplify by removing diagnostic capability (rejected explicitly under P4:
  simplicity may not be achieved by deleting useful capability).
- **Why:** Test #1's ordinary-user findings cluster on diagnostic material leaking into the ordinary
  surface — raw governance revision metadata shown without explanation (HU-F008), Valence/Arousal/
  Top Drives/Attention presented in internal vocabulary (B-F003), Parking Brake scope semantics
  unpredictable before operating them (HU-F006, HU-F007) — alongside a first-use finding that the
  tester did not know where to begin (HU-F001).
- **Consequences:** Ordinary/admin separation is a **Band C** prerequisite for full Test #2, gated
  by P4 and, for the safety-control half, P1. Low-risk surface *concepts* may be prototyped under
  Band D. The number and form of user/admin/expressive surfaces stays on the unresolved list below.
- **Date:** 2026-08-20

## Decision: Water/hydration is out of current ordinary-user product scope
- **Decision:** Remove Water/hydration from the ordinary active product and UI. Implementation must
  **amend and reconcile the existing canonical hydration/water entry in `RISKS.md`, not create a
  competing authority**. Legacy data and model disposition is a **separate** decision and **must not
  silently delete historical data**. Source: Post-Test #1 Decision Register v2.2, D4.
- **Alternatives:** Fix the Water panel so it works (rejected: it is not in the product's current
  scope, so making it work would be building the wrong thing correctly); delete the `water_logs`
  table and models along with the UI (rejected: that conflates removing a feature from the ordinary
  surface with disposing of a user's historical data, which is a governed decision in its own right).
- **Why:** Test #1 FUNC-017 found enabled Water controls calling routes that do not exist, rendering
  `undefined ml` (MF-F003), and the tester independently said Water should not be in the ordinary UI
  (B-F005). `RISKS.md` has carried a hydration/water cleanup entry since 2026-07-28, corrected
  2026-08-17; that entry is the existing authority and is amended rather than duplicated.
- **Consequences:** `RISKS.md`'s hydration entry is amended to record the scope decision and the
  data-disposition boundary. Removal of the ordinary UI panel is **Band D** work. No canonical
  document may reintroduce hydration as an active current product feature. Whether the residual
  `water_logs` table and models are ever removed remains a separate governed decision, unmade.
- **Date:** 2026-08-20

## Decision: Memory agency — inspect, correct, forget, export, with truthful derived-data accounting
- **Decision:** Provide human-readable controls to inspect, correct, forget, export, and set
  retention on stored personal understanding, with provenance where appropriate. **Correction does
  not rewrite audit history.** **"Forget" must account truthfully for embeddings, FTS entries,
  summaries, reflections, caches, and sensor-derived data**; residual limitations that cannot be
  fully honoured must be **disclosed**, not quietly tolerated. Source: Post-Test #1 Decision Register
  v2.2, D5.
- **Alternatives:** Offer deletion that removes the primary record only (rejected: it would report a
  forget that did not happen, which is the specific dishonesty this entry forbids); defer memory
  agency until after Test #2 (rejected: see P5 — this is required *for* full Test #2, with no "if
  durable memory participates" loophole).
- **Why:** Test #1's B-F004: the tester expected to be able to inspect and edit what Bartholomew had
  stored about them, and could not. `CONSTITUTION.md`'s data-portability invariant already requires
  export; this entry makes the rest of the agency set explicit and ties truthful deletion to the
  derived artifacts that make naive deletion a lie.
- **Consequences:** Memory agency is gated by P5 and is a **Band C** requirement for full Test #2.
  Memory UI may be prototyped against synthetic memories under Band D. Truthful derived-artifact
  deletion also appears as an S4 requirement for sensitive content and a P9 requirement for
  post-test data agency; those are the same obligation seen from three scopes, not three policies.
- **Date:** 2026-08-20

## Decision: Sleep/consolidation is approved as investigation and controlled prototype only
- **Decision:** Approve **architecture investigation and controlled prototype only** — **not**
  production sleep behaviour. Any investigation must define job eligibility, quiet-hours preference,
  batching, resource use, interruption/resumption, crash safety, observability, availability,
  Governance/Parking-Brake semantics, and the relationship to triage (D2). **Sleep cannot become a
  suppression, capture, action, or notification bypass.** Source: Post-Test #1 Decision Register
  v2.2, D6 — recorded there as **APPROVE AS INVESTIGATION**.
- **Alternatives:** Approve sleep/consolidation as a production behaviour (rejected: nothing in Test
  #1 evidences that it is safe to run unattended work on that basis, and P7 records that sleep is
  not required at all); defer the investigation entirely (rejected: the architecture question is
  real and Band D can carry it safely on test data).
- **Why:** Phase B Vision Finding 009 raised consolidation as a direction. It is genuinely
  interesting and genuinely capable of becoming an unattended-action path that bypasses triage and
  governance if approved as behaviour rather than as investigation.
- **Consequences:** Sleep architecture work on test data is **Band D**. **This entry does not
  authorise any production sleep behaviour, any unattended consolidation job, or any change to
  notification/suppression behaviour.** P7 is explicit that sleep is not required; if it is ever
  enabled it must be interruptible, available, observable, crash-safe, governed, and must not create
  a hidden backlog or dispose of obligations — otherwise it stays disabled. Sleep job algorithms
  remain on the unresolved list below.
- **Date:** 2026-08-20

## Decision: Real-world testing standard — TILT reconciled, not weakened and not abandoned
- **Decision:** Two clauses, both binding. **(a) Full Real-World Test #2 begins only when the
  applicable safety and product gates pass.** **(b) Intermediate real-use checkpoints — attended,
  localhost, text-only or similarly narrow slices — remain allowed and encouraged under
  `docs/TILT.md` once a slice is "done enough to test", provided that specific slice has no
  unresolved safety, governance, privacy, data-integrity, architectural-validity, or
  experiment-validity blocker.** TILT is therefore **not** "finish everything before testing" and
  **not** "test regardless of unresolved safety blockers". Source: Post-Test #1 Decision Register
  v2.2, D7 (D7a, D7b).
- **Recorded as post-test judgement, not as historical fact:** Taylor's post-Test #1 direction is
  that continuing to operate the same low-capability experience is unlikely to yield proportionate
  value, and that the next meaningful real-use checkpoint should unlock a **new useful slice**
  rather than polish the existing one indefinitely.
- **Alternatives:** Suspend real-world testing until Band C (rejected: it would repeat the
  pre-2026-08-12 sequencing failure `docs/TILT.md` exists to correct); keep testing under TILT with
  no additional gating (rejected: Test #1 surfaced S0-severity findings — OP-W004, NUDGE-F001,
  B-F001, SEC-F002a/b, UI-SYNC001b — that make unattended and ambient testing a different risk class
  from an attended localhost checkpoint).
- **Why:** TILT's six exceptions were already the right rule; what Test #1 showed is that they need
  to be applied *per test class*, because "the slice is safe enough to try attended on localhost"
  and "the system is safe enough to run unattended with real sensors" are different questions with
  different answers.
- **Consequences:** `docs/TILT.md` gains the D7 reconciliation and cross-references the readiness
  bands; `ROADMAP.md` gains the bands themselves. Band 0 is the operational form of clause (b).
  Nothing in this entry lowers `docs/TILT.md`'s six exceptions or lets a checkpoint proceed past one.
- **Date:** 2026-08-20

## Decision: Ambient-sensing readiness — no real passive capture until the applicable gates pass
- **Decision:** No real ambient or passive camera, microphone, or wearable capture until the
  applicable gates pass. This decision is **subordinate to `CONSTITUTION.md`**, not an alternative to
  it. The gate set includes: key lifecycle; audit integrity; privacy/retention; consent and
  revocation; bystanders; jurisdiction; **direct** Parking Brake enforcement; independent emergency
  shutdown; truthful state; teardown; sensitive-context handling; queue containment; and
  authentication **if** the capability is remote. **Controlled synthetic bench development is
  allowed** only where it captures no non-consenting person and performs no unattended real capture,
  and where Governance is developed **alongside** the adapter rather than after it. Source:
  Post-Test #1 Decision Register v2.2, D8.
- **Alternatives:** Build the sensor adapters first and add Governance once they work (rejected
  explicitly: it is how a capability arrives before the ability to stop it); block synthetic
  development too (rejected: synthetic benches are how the adapters get built safely, and Band D
  exists for exactly this).
- **Why:** Ambient capture is the point at which Bartholomew starts taking in more of the world than
  the user is present to supervise, and Test #1's configuration-state matrix did **not**
  independently prove direct enforcement of every `sight`, `voice`, `scheduler`, or `training` case.
- **Consequences:** Real ambient sensing is a **Band B** gate: everything applicable from Band A,
  plus D8, D11, D12, D13, safety gates S3–S7 and S9–S11, the `sight`/`voice` brake-authority
  consolidation, and consent/revocation/retention with no silent restart-resume. Synthetic and
  pre-recorded work is Band D. Wearable-camera inclusion remains unresolved.
- **Date:** 2026-08-20

## Decision: Formal evidence standard for findings
- **Decision:** Every formal finding carries: a stable ID; class; severity; band; status; phase/case/
  step; timestamp or event; the exact artifact; the expected result **or** an explicit "no oracle
  existed"; the actual result; the direct evidence; any inference recorded **separately** from the
  evidence; impact; a closure criterion; and verification evidence. Evidence is stored with stable
  paths and checksums where practical. Reclassifications and status/severity changes are recorded
  rather than applied silently. Source: Post-Test #1 Decision Register v2.2, D9.
- **Alternatives:** Keep findings as prose observations (rejected: the v1→v2.2 correction cycle
  depended on being able to check arithmetic, provenance and classification, which prose does not
  support); require the full field set retroactively for historical items (rejected: it would mean
  editing historical evidence, which is forbidden — the register instead marks post-test
  formalisations as `[POST-TEST]` and keeps the historical accounting visible).
- **Why:** Test #1's own findings could only be corrected across register versions because they had
  stable IDs and separable evidence-versus-inference. The one place this bit hardest — the absence of
  preserved raw artifacts — is precisely the checksum clause.
- **Consequences:** `docs/evidence/test-1/` is created as the stable evidence location, with SHA-256
  checksums for what is present and an explicit absence inventory for what is not. Compact
  presentation of E5/E6/E7 rows does not waive this standard: for E1–E3 findings the full field set
  remains binding, and any compactly omitted field must be restored from the underlying source record
  before closure evidence is accepted.
- **Date:** 2026-08-20

## Decision: Authentication / network exposure boundary, and consequential local device agency
- **Decision:** The unauthenticated local proof-of-concept must not be silently extended to remote or
  cross-device use. Before any real PC/phone remote, LAN, or non-localhost use: a reviewed threat
  boundary, authentication and authorisation, device/session ownership, and brake/emergency
  semantics. Local non-actuating prototypes may proceed. **Additionally, and independently of network
  locality: any local adapter that can control keyboard, mouse, screen, or browser, launch actions,
  or otherwise exercise consequential device agency also requires independent emergency shutdown
  (D11) and its verification gate (S9).** Source: Post-Test #1 Decision Register v2.2, D10.
- **Alternatives:** Treat "it's only localhost" as sufficient for device-control prototypes
  (rejected: locality bounds *who can reach it*, not *what it can do to the machine* — a local
  adapter with keyboard control needs a stop that works when the UI does not); accept simple token
  auth for remote use (rejected already by the existing deployment-architecture entry, and carried
  forward unchanged here).
- **Why:** `docs/FIRST_REAL_WORLD_TEST.md` §0 records that at the tested implementation an
  unauthenticated `curl` could engage **and disengage** the Parking Brake. Test #1 was run bound to
  `127.0.0.1` for that reason. That containment is a test condition, not an architecture.
- **Consequences:** Authentication (S8) becomes applicable at **Band B only when remote phone,
  wearable, or non-localhost capability is involved** — it is **not** mandatory for a purely local
  Band B scenario, and must not be recorded as if it were. The D11/S9 requirement for consequential
  local device agency is **not** conditional on network exposure. Remote phone capability sits behind
  D10/S8 in the implementation tracks. Production authentication architecture remains unresolved.
- **Date:** 2026-08-20

## Decision: Independent emergency shutdown before unattended sensing or device agency
- **Decision:** Before unattended real sensing or consequential device agency, provide an
  **independently enforceable emergency stop that works outside Bartholomew's ordinary UI and
  without its in-process cooperation.** Source: Post-Test #1 Decision Register v2.2, D11; carries
  forward the register's C1 constraint and `CONSTITUTION.md`'s locally-enforceable-halt requirement.
- **Alternatives:** Rely on the existing Parking Brake UI and API (rejected: both are in-process and
  reachable only while the ordinary surface works — an emergency stop that requires the system to
  cooperate is not an emergency stop); rely on killing the process (not sufficient on its own: a
  restart must not silently resume capture, which is why S9 requires the restart-after-emergency to
  be deliberate).
- **Why:** The Parking Brake is a *governance* control that the running system enforces. Unattended
  sensing and device agency are exactly the conditions under which the running system may be the
  thing that needs stopping.
- **Consequences:** Gated by S9: outside the ordinary UI; works with the ordinary UI unavailable;
  works despite relevant normal-surface control or interference where applicable; terminates capture,
  output, and device agency; independent of in-process goodwill; restart after an emergency stop is
  deliberate. Required at **Band B** for ambient sensing, and required for consequential local device
  agency under D10 regardless of network exposure. **This entry does not authorise building it now.**
- **Date:** 2026-08-20

## Decision: Test #2 personal-data handling must be defined before any real personal-data capture
- **Decision:** Before real personal-data capture, define: the captured data classes; storage;
  raw-data retention; retention duration; derived artifacts; consent; deletion and revocation;
  encryption class; post-test retention; and the distinction between test-created data and durable
  memory. **S11 verifies this policy rather than restating a separate one** — there is one policy,
  with a verification gate, not two policies that can drift apart. Source: Post-Test #1 Decision
  Register v2.2, D12.
- **Alternatives:** Define data handling during the test (rejected: retention and consent decisions
  made while data is already being captured are not decisions, they are ratifications); write a
  second policy inside the safety gate (rejected explicitly — hence S11's verification-only framing).
- **Why:** Test #1 ran with ephemeral dev keys for both STANDARD and STRONG classes (OP-W001/002),
  and sensitive-formatted values reached ordinary working context and output (SEC-F002a/b) with no
  echo oracle defined. Both are the shape of "we will work out the data rules later".
- **Consequences:** A **Band B** prerequisite for ambient sensing and part of the **Band C** Test #2
  package. S11 passes only when every D12 field is instantiated for the selected Test #2 scenario and
  capabilities, storage locations are enumerated, test-created data is distinguishable from durable
  memory, a post-test inventory can be generated, the user can inspect retained data, deletion and
  retention execution is recordable, and retained *evidence* is distinguished from retained *personal
  content*. Test #1's own ephemeral-key data disposition is recorded under S3.
- **Date:** 2026-08-20

## Decision: Bystander and third-party privacy before shared-home or public ambient capture
- **Decision:** Before shared-home or public ambient capture, define: bystander/household policy;
  jurisdiction, notice, and consent implications; private and sensitive zones and their suppression;
  truthful capture indicators; and stop/revocation. **Primary-user consent does not automatically
  authorise capturing everyone nearby.** Source: Post-Test #1 Decision Register v2.2, D13; carries
  forward the register's C2 constraint and `CONSTITUTION.md`'s capture-and-recording-safety
  invariant.
- **Alternatives:** Treat the primary user's consent as covering the household (rejected in the
  decision itself: the people captured are not the people consenting); defer bystander policy to a
  later compliance workstream (rejected: the capture happens at Band B, so the policy is needed at
  Band B).
- **Why:** `RISKS.md` has recorded since 2026-07-28 that no jurisdictional analysis exists for any
  future microphone or camera capability. Ambient capture in a shared home is the first point at
  which that gap becomes other people's problem rather than the project's.
- **Consequences:** A **Band B** prerequisite, verified through S6 (per-sensor opt-in; truthful
  state; OS/hardware-level indicator where available, with availability assessed and recorded per
  sensor before the test; immediate revocation; brake terminates capture and prevents new capture;
  ungated teardown; fail-safe retention defaults; captured-data deletion and revocation treatment;
  bystander/household policy; jurisdiction and private-versus-public assessment; and no silent resume
  on reconnect or restart). A global legal-compliance system is explicitly **not** in scope and
  remains unresolved.
- **Date:** 2026-08-20

## Decision: Tester and usability validation — Taylor is primary, independence must be labelled
- **Decision:** Taylor remains the primary tester and product authority. **Strong independent
  first-use discoverability claims require a naive or proxy tester.** **Band C may proceed with
  Taylor-only P1 evidence if a naive tester is genuinely unavailable — but that result must be
  labelled designer-tested / not independently validated, and may not later be cited as independent
  discoverability proof.** Source: Post-Test #1 Decision Register v2.2, D14.
- **Alternatives:** Require a naive tester as a hard Band C blocker (rejected: it would stall Test #2
  on tester availability rather than on readiness); accept designer testing as equivalent to
  independent validation (rejected: the designer cannot un-know where the controls are, which is the
  entire content of a first-use finding).
- **Why:** HU-F001 — "I don't even know where to begin" — is a genuine first-use finding precisely
  because it was spontaneous. That property does not survive a second exposure to the same surface.
- **Consequences:** P1's five clauses must all pass, and the record must state which tester class
  produced the evidence. A designer-tested P1 result is a valid Band C input and an invalid citation
  for independent discoverability. Time-to-first-useful-action is recorded either way.
- **Date:** 2026-08-20

## Decision: Abort criteria, evidence freeze, and closure are predefined for Test #2
- **Decision:** Predefine, before Test #2 runs: abort criteria; the exact evidence freeze covering
  repository, configuration, model, data, capability, consent, and brake state; and closure —
  orderly shutdown, a final data inventory, the unresolved findings, post-test data disposition, and
  an explicit phase boundary. Source: Post-Test #1 Decision Register v2.2, D15.
- **Alternatives:** Decide when to abort while the test is running (rejected: an abort criterion
  invented mid-test is indistinguishable from a rationalisation); record the freeze as a branch name
  (rejected on direct Test #1 evidence — PR #58's head branch no longer resolves, and only the commit
  hash survived).
- **Why:** Test #1's own freeze had to be reconstructed after the fact, and the reconstruction is why
  the register carries a corrected provenance section at all. Test #1 also ended with a logging gap
  at shutdown (OP-W005) that had to be substituted with process/port/state evidence.
- **Consequences:** A **Band C** prerequisite and Track 7 content. The evidence freeze is recorded as
  a **commit hash**, never as a branch name. Closure is gated jointly with P9's post-test data
  agency. `docs/evidence/test-1/` is the pattern the Test #2 freeze should follow.
- **Date:** 2026-08-20

## Decision: Post-Test #1 items that remain deliberately unresolved
- **Decision:** The following are **not** decided by the Post-Test #1 approval, and must not be
  frozen by documentation, implementation planning, or an agent acting on the register: the number
  and form of user/admin/expressive surfaces; expressive avatar form; the customer-facing label and
  location for Awaiting Response; customer terminology for Affect/Attention/Drives; exact nudge caps
  and rates; exact critical-state freshness values; final P3 and P6 numeric targets before the
  scenario freeze; sleep job algorithms; wearable-camera inclusion; production authentication
  architecture; a global legal-compliance system; the numeric ABR threshold; and the final
  multi-user/server implementation. Source: Post-Test #1 Decision Register v2.2, §14.
- **Alternatives:** Resolve them now to unblock planning (rejected: several of them — the numeric
  targets in particular — are only meaningful once the scenarios they measure are frozen, and
  deciding them early would set a bar to be tuned toward rather than met); leave them unlisted
  (rejected: an unlisted open question is one an implementer resolves by accident).
- **Why:** Recording them as a decision is what makes premature closure visible. If a future
  document or diff appears to settle one of these, that is a change against this entry and needs
  Taylor, not an inference.
- **Consequences:** Each item stays open until decided explicitly. Where existing documentation
  appears to freeze one prematurely, it is flagged for Taylor rather than reinterpreted. As at
  2026-08-20 this documentation pass found **no** canonical document that prematurely freezes any of
  the thirteen; the one adjacent case is noted in `RISKS.md`'s Parking Brake configuration entry
  (`config/policy.yaml`'s `affected_components` list, a runtime configuration file this
  documentation-only pass did not change).
- **Date:** 2026-08-20
