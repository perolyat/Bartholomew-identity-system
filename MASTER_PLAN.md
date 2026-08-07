# MASTER_PLAN

> **Single Source of Truth (SSOT)** for what Bartholomew is, what matters, where we are, and what we do next.
>
> **Last updated:** 2026-07-28 (documentation reconciliation pass 2: this document trimmed from a
> ~2,200-line engineering chronicle to an executive SSOT — items 11.1–11.22, the four bug-fix
> "rounds," and the "Experience Kernel MVP" write-up moved verbatim to
> [docs/archive/ENGINEERING_LOG_2026.md](docs/archive/ENGINEERING_LOG_2026.md), with a compact
> index and preserved item numbers left in place; the Echo Integration Roadmap moved to
> [docs/incubator/ECHO_IDEAS.md](docs/incubator/ECHO_IDEAS.md), non-canonical; "Next 3 Moves"
> updated for the corrected Stage 1-before-Stage-5 sequencing recorded in `ROADMAP.md`. See
> `DECISIONS.md`'s "Deployment architecture: hybrid local-first" entry for this pass's other major
> decision.
>
> **Previously (2026-07-27):** reconciliation against the repository state established by Phase A,
> merged as `8b96319`. The "Last updated" line had itself read 2026-01-19 while the body was
> edited repeatedly through July; "Stage gates / milestones", "Next 3 Moves" and the Approval
> Ledger were the stale sections and were corrected then.)

## Vision / North Star

Build a practical, privacy-preserving, consent-first cognitive architecture (“Bartholomew’s Brain”) that:

- Enforces identity, safety, and governance constraints from configuration (`Identity.yaml`, policy/memory rules).
- Maintains durable memory with redaction, encryption, consent gates, retention, and auditability.
- Implements an **Experience Kernel** (self-model + narrator) to maintain continuity and growth over time.
- Plans and nudges safely (fail-closed) and can later graduate into controlled “Act” capabilities.

## Non-negotiables

1. **Fail-closed governance**
   - No irreversible actions without an explicit gate.
   - Parking-brake semantics for subsystems (skills/sight/voice/scheduler/global).

2. **Privacy-first data handling**
   - Redaction before storage where required.
   - Encryption at rest for sensitive kinds/fields.
   - Consent gating for “ask before store” classes.
   - Retention/TTL rules must be enforceable and testable.

3. **Verification-first engineering**
   - If it can't be verified (tests/logs/replay), it's not shipped.
   - Changes that alter interfaces/governance must update docs + tests.

4. **No doc sprawl**
   - Canonical docs are the only SSOT: see links below.

## Doc Governance

All canonical documentation changes follow strict governance:

1. **User approval required**: No doc or code changes are committed without explicit user authorization.
2. **Change presentation**: Proposed changes must be shown via diff or summary before commit.
3. **Traceability**: Each commit must map to an approved task or explicit user request.
4. **Rollback readiness**: User can revert any change via `git checkout -- <files>` or `git revert <commit>`.

See [DECISIONS.md](DECISIONS.md) for the "User Approval Gate" decision and [CHECKLISTS.md](CHECKLISTS.md) for commit authorization checklist.

## Canonical docs

**13 documents.** This list is the registry; `DECISIONS.md`'s "Canonical SSOT docs" entry and
`CONSTITUTION.md`'s handover note describe the same set. (Corrected 2026-07-27: this list
previously omitted `CONSTITUTION.md`, contradicting `DECISIONS.md`'s "Adopt `CONSTITUTION.md`
as a canonical SSOT doc" entry, which explicitly puts the count at 13.)

- **MASTER_PLAN.md** (this doc)
- [CONSTITUTION.md](CONSTITUTION.md)
- [COGNITIVE_RUNTIME.md](COGNITIVE_RUNTIME.md)
- [ROADMAP.md](ROADMAP.md)
- [DECISIONS.md](DECISIONS.md)
- [RISKS.md](RISKS.md)
- [ASSUMPTIONS.md](ASSUMPTIONS.md)
- [INTERFACES.md](INTERFACES.md)
- [CHECKLISTS.md](CHECKLISTS.md)
- [REVIEWS.md](REVIEWS.md)
- [CI.md](CI.md)
- [TEST_MATRIX.md](TEST_MATRIX.md)
- [PERF_BUDGETS.md](PERF_BUDGETS.md)

Every other `*.md` in the repository (implementation notes, `docs/*`, `STATUS_*`, `README`s)
is a **reference**, not an authority on project status. Where one contradicts a canonical doc,
the canonical doc wins. Two locations are explicitly and permanently non-authoritative by
design, not merely by omission: `docs/incubator/` (unapproved, individually-evaluated-only
ideas — see `docs/incubator/ECHO_IDEAS.md`) and `docs/archive/` (superseded historical material,
kept for record — see `docs/archive/ENGINEERING_LOG_2026.md` and the other archived files listed
in `RISKS.md`'s tech-debt watchlist).

## Current architecture

### Text diagram (high-level)

```
Identity.yaml + config/*.yaml
        |
        v
identity_interpreter/   (validation, normalization, policy engines)
        |
        v
bartholomew/kernel/     (daemon, planner, memory store, governance engines)
  |   |      |   |  \
  |   |      |   |   \
  |   |      |   |    +-- retrieval (FTS / vector / hybrid)
  |   |      |   +------- encryption / redaction / summarization
  |   |      +----------- consent gate + memory rules
  |   +------------------ event bus + metrics
  +---------------------- SQLite DB (data/barth.db)

bartholomew_api_bridge_v0_1/ (FastAPI surface over kernel + DB)

exports/ (audits, sessions)
logs/    (runtime logs)
```

### Key invariants

- **Identity.yaml is the governing config** for routing, safety, and persona/behavior
  constraints. *(Corrected 2026-07-21: confirmed true only for the chat path today —
  the autonomous kernel/scheduler/skill-execution path does not consult it at all. See
  "P2.5 — Runtime Convergence" in the backlog below for the fix.)*
- **Single SQLite DB** is the shared persistence backbone.
- **Consent + privacy gates** pre-filter retrieval results before they reach callers.
- **Parking brake** provides an emergency/operational kill-switch by scope.

## Stage gates / milestones

> **Rewritten 2026-07-27.** This section had gone stale: it listed only Stage 0 as complete,
> described Phases 2A–2D as "in progress ... known failing tests" citing the explicitly-stale
> `docs/archive/STATUS_2025-12-29.md`, and named "Green core on CI Linux" as the next gate — all of
> which the repository had long since overtaken. Statuses below are stated only where current
> code, a merge commit, or an executable test supports them.

**Complete**

| Stage | Status | Evidence |
|---|---|---|
| Stage 0 — Kernel alive/stable/dreaming | ✅ | `docs/archive/STAGE_0_COMPLETION.md`, `tests/test_stage0_alive.py` |
| Stage 0.5 — Packaging & architecture fixes | ✅ 2026-07-20 | P0 items 0–3 below; `ROADMAP.md` |
| Stage 2 — Governance hardening + memory stack (2A–2F) | ✅ P0 complete 2026-07-20 | `docs/archive/ENGINEERING_LOG_2026.md`'s "Full test suite investigation" (38 → 0) |
| Stage 3 — Unified Persona Core (Experience Kernel) | ✅ largely done, gaps closed 2026-07-20/21 | `ROADMAP.md` Stage 3; items 11.8–11.9 |
| Stage 4 — Skill registry + starter skills | ✅ 2026-07-21 | `ROADMAP.md` Stage 4 |
| Stage 4.5 — Runtime Convergence | ✅ 2026-07-24 | `COGNITIVE_RUNTIME.md` Exit Gate table (all 7 satisfied in scope); items 11.12–11.22 |

**Not started**

- **Stage 1 — Console/UI integration.** Stage 1 is a deferred console/UI product slice. It has
  not started and was never a prerequisite for Stages 2–4.5. No code evidence of work on it.
  Its historical numbering is retained deliberately; stages were sequenced by architectural
  dependency, not by number.
- **Stage 5 — Initiative engine.** Only its prerequisite **S5.0** has landed (see P3 below).
  **S5.1 has not begun.** No Stage 5 feature code exists.
- **Stages 6–7, Echo gates.** Future.

**Engineering workstreams (cross-cutting, not stage gates)**

- **Phase A — truthful cross-platform verification.** ✅ **Complete and merged 2026-07-27**,
  merge commit **`8b96319c4059d9dfada2579ca5f6da22b34e1f31`** (PR #26). Established
  `.github/workflows/ci.yml` (auto-run on every PR and push to `main`; Ubuntu 3.10 + 3.11 and
  Windows 3.11), a packaging/dependency contract, clean-start lifecycle tests, and coverage
  measured across all three first-party packages with the pre-existing 70% gate enforced. It
  also fixed two live production defects found by refusing to trust prior status labels: the
  sensitive-memory consent path (`asyncio.run()` inside `async def` → unguarded
  `import nest_asyncio`) and the broken `bartholomew` console script. All 9 GitHub checks were
  green on the merged head `e923fb9`, including the first Windows job in the repository's
  history. See `CI.md` and `RISKS.md`.
- **Phase B — persistence ownership stabilisation.** **Proposed next engineering work; NOT
  approved for implementation.** Scope is the mixed SQLite ownership Phase A deliberately
  characterised rather than restructured, and the intermittent concurrent-process WAL failure.
  See `RISKS.md`'s tech-debt watchlist for the evidence being preserved for it. No design,
  branch, or code exists.

**The "Green core on CI Linux" gate this section used to name is met** — and superseded by
Phase A, which made verification automatic and cross-platform rather than Linux-only and
partly manual. See [ROADMAP.md](ROADMAP.md) for per-stage exit criteria.

## Backlog (prioritized, smallest safe slices)

> **Rule:** every item must have acceptance criteria + verification steps before it is started.

### P0 — Packaging & Architecture (Pre-requisites)

> **Source:** Cline audit 2026-01-22 verifying ChatGPT repo analysis

0. ✅ **Missing package `__init__.py`** — Fixed 2026-07-20.
   - `bartholomew/` directory has no `__init__.py` file.
   - **Acceptance:** `bartholomew/__init__.py` exists; `pip install -e .` succeeds.
   - **Verify:** `python -c "import bartholomew"` works.
   - **DoD:** File created, editable install tested.
   - **Risk if skipped:** Package is not installable; imports fail.
   - **Note:** also added `bartholomew/kernel/memory/__init__.py`, which had the same
     implicit-namespace-package gap.

1. ✅ **Dependency consolidation to pyproject.toml** — Fixed 2026-07-20.
   - `pyproject.toml` missing runtime deps that exist in `requirements.txt`: `numpy`, `cryptography`.
   - `typer`, `rich` used in CLI but not declared.
   - **Acceptance:** `pyproject.toml` is single source of truth for all deps.
   - **Verify:** `pip install .` installs all deps; no manual `requirements.txt` needed.
   - **DoD:** All runtime deps in `[project.dependencies]`; `requirements.txt` mirrors or deprecated.
   - **Risk if skipped:** Dependency drift; CI/CD failures.
   - **Note:** verification (fresh venv install + test collection) also turned up two more
     undeclared runtime imports not in the original audit: `jsonschema` (used by
     `identity_interpreter/loader.py`) and `requests` (used by
     `identity_interpreter/adapters/llm_stub.py`), plus `pydantic`'s `EmailStr` needing the
     `email` extra (`identity_interpreter/models.py`). All four are now declared in both
     `pyproject.toml` and `requirements.txt`.
   - **Follow-up fixed 2026-07-20:** pinned `fastapi>=0.104,<0.121` in `pyproject.toml` and
     `requirements.txt` (that ceiling keeps `starlette` on the `0.4x` line; `fastapi>=0.121`
     pulls `starlette>=1.0`, which breaks `starlette.testclient`'s implicit `httpx`
     dependency — reproduced and confirmed on a clean venv). Also re-encoded
     `requirements.lock` from UTF-16 to UTF-8/LF so it's actually readable/usable, and added
     `httpx`/`freezegun` to `requirements-dev.txt` (both were imported by tests but
     undeclared, so a clean dev install couldn't even collect the test suite).
   - **New follow-up found while fixing this, root-caused and fixed 2026-07-20:** with the
     dependency set actually installing, `pytest -q -m smoke` (the full suite together, as
     CI's `lint-test` job runs it) hung rather than failed. `pytest-timeout` (`timeout = 120`,
     `timeout_method = "thread"` in `pyproject.toml`) was added first as a safety net so this
     fails fast with a clear traceback instead of hanging the CI job indefinitely.
     Root cause (confirmed via `faulthandler` thread dump): `bartholomew_api_bridge_v0_1/
     services/api/db.py` resolves `BARTH_DB_PATH` into a module-level `DB_PATH` constant the
     moment that module is first imported; later `os.environ["BARTH_DB_PATH"] = ...`
     assignments by other test modules (e.g. `tests/test_stage0_alive.py`'s own attempted
     override) do nothing, because Python caches the already-imported module. Since
     `tests/test_liveness_self.py` imports it first (alphabetically) without setting the env
     var at all, every test in the session that starts the API app's `KernelDaemon` — each
     with its own background scheduler thread — ended up sharing the real, git-tracked
     `data/barth.db`. Their scheduler threads then deadlocked on file locks against that
     shared (and, from repeated interrupted test runs, sometimes already-corrupted) file
     during `TestClient.__exit__`'s shutdown handshake. Fixed by setting
     `os.environ.setdefault("BARTH_DB_PATH", ...)` to a fresh temp path at the top of the
     root `conftest.py`, which pytest always imports before collecting any test module —
     guaranteeing the override is in place before `db.py` ever gets imported. Verified:
     the previously-hanging pair now runs in ~3.5s, the full smoke suite in ~4s, and
     `data/barth.db` is no longer touched by running the test suite at all.
   - **Follow-up noticed along the way, root-caused and fixed 2026-07-20 (see below):**
     `tests/test_consent_gates.py::test_fts_search_without_consent_gate` and all three tests
     in `tests/test_metrics_production_mode.py` were failing for two entirely unrelated
     reasons — see "FTS5 external-content `upsert()` bug" and "`sys.path` self-pollution"
     below.

## Fixed defects — full narrative moved to the archive (2026-07-28)

Three items previously detailed here in full — (1) the FTS5 external-content `upsert()` bug and
two `sys.path` self-pollution instances (fixed 2026-07-20), (2) the retrieval consent-enforcement
bug where `Retriever`/`FTSOnlyRetriever`/`HybridRetriever` excluded every `requires_consent`
memory unconditionally regardless of actual consent (fixed 2026-07-21), and (3) the RISKS.md R1
consent-bypass red-team test suite (added 2026-07-24) — are preserved verbatim in
[docs/archive/ENGINEERING_LOG_2026.md](docs/archive/ENGINEERING_LOG_2026.md) under their original
headings. All three remain fixed/landed; see `RISKS.md` R1 and `TEST_MATRIX.md` for their current
status and test references.

---

### P0 — Make the build trustworthy
5. **Canonical SSOT docs (done in this repo snapshot)**
   - **Acceptance:** canonical docs exist; cross-linked; "Next 3 Moves" current.
   - **Verify:** open markdown; links resolve.

6. **CI minimal gates (Linux)**
   - Make `pytest -q`, `ruff check .`, `black --check .` run in CI.
   - Quarantine platform-specific failures (Windows locking, SQLite build flags) into explicit markers.
   - **Acceptance:** CI green on Linux; quarantines documented.
   - **Verify:** GitHub Actions run; locally `ruff check . && black --check . && pytest -q`.

7. **Fix non-environmental failing tests** called out in `docs/archive/STATUS_2025-12-29.md`
   - Summarization truncation fallback.
   - Encryption round-trip for envelopes.
   - Embedding persist lifecycle (`persist_embeddings_for`, `embed_store` defaults).
   - Retrieval factory returning wrong retriever for explicit modes.
   - Metrics registry idempotency.
   - **Acceptance:** P0 failures are green on Linux CI; regressions covered by tests.
   - **Verify:** `pytest -q` on Linux CI; replay the failing cases.

### P1 — Unified Persona Core (Experience Kernel) + personality packs
8. ✅ **Experience Kernel MVP** (self-model + narrator) — mostly already built; gaps closed 2026-07-20.
   - **Correction to this doc (2026-07-20):** this item, `ROADMAP.md`'s "Stage 3", `INTERFACES.md`'s
     "Experience Kernel (proposed)" section, and `TEST_MATRIX.md` all describe this as
     future/not-started work. That was stale — the actual code already has a full implementation:
     `bartholomew/kernel/experience_kernel.py` (self-model: drives/affect/attention/goals/context +
     snapshot persistence), `bartholomew/kernel/narrator.py` (episodic memory + daily/weekly
     reflection narratives), `global_workspace.py`/`working_memory.py`/`persona_pack.py` (all wired
     together), `daemon.py` (instantiates all of it, runs a daily/weekly reflection scheduler), and a
     REST API (`/api/self`, `/api/episodes/*`, `/api/persona/*`). ~320 existing tests across 7 files
     (`test_experience_kernel.py`, `test_narrator.py`, `test_stage3_integration.py`,
     `test_reflection_generation.py`, `test_persona_pack.py`, `test_working_memory.py`,
     `test_global_workspace.py`) — confirmed all passing before touching anything.
   - **Acceptance:** kernel can produce a stable "about me" snapshot and a day/week reflection without leaking sensitive memory.
   - **Verify:** `pytest -q tests/test_experience_kernel.py` (already exists) + run a scenario replay
     (`tests/test_stage3_integration.py::TestFullLifecycle` is the closest existing precedent; a
     dedicated replay harness still doesn't exist as of 2026-07-20 -- see below).
   - **Gaps found and fixed 2026-07-20** (see `docs/archive/ENGINEERING_LOG_2026.md`'s
     "Experience Kernel MVP: bug fix + privacy gap" for full detail):
     1. A live, silently-swallowed `AttributeError` in `daemon.py`'s tick loop meant affect never
        decayed and persona auto-activation / the planner's `decide()` never ran, in production,
        since the moment Stage 3 landed.
     2. `INTERFACES.md`'s documented contract ("self_snapshot" = "safe-to-share description";
        retrieved memories must be "filtered by consent/privacy") wasn't actually implemented for
        this subsystem -- `episodic_entries`/`experience_snapshots` bypass `ConsentGate`/
        `memory_rules.py`/`redaction_engine.py` entirely, and `NarratorConfig.redact_personal_data`
        was declared in config but never checked anywhere.
   - **Not yet done, deliberately out of scope for this pass:** a dedicated "scenario replay" test
     harness (distinct from `TestFullLifecycle`) — later closed, see item 11.9 above; a date-range
     query against the `memories` table itself for narrator/reflection use (currently only
     `episodic_entries` supports this) — still open.
   - **Reflection-pipeline reconciliation — corrected 2026-07-28 (this bullet was stale, dated
     2026-07-20, and never updated after item 11.8 landed the next day):** `daemon.py`'s
     LLM+safety-checked `ReflectionGenerator` and `narrator.py`'s template-based
     `generate_*_reflection_narrative` are **not** simply "two non-unified pipelines" left
     untouched — item 11.8 (2026-07-21) changed `daemon.py` to call both and concatenate their
     output. That is the **current implementation**: two independently generated outputs are
     concatenated, not composed by a single authority. The **approved target architecture**
     (`DECISIONS.md`'s "Reflection ownership — target architecture" entry;
     `COGNITIVE_RUNTIME.md`'s "Reflection ownership" section) is that `ReflectionGenerator` owns
     final reflection composition, with `NarratorEngine` supplying supplementary episodic evidence
     to it, not standing as a second, independent pipeline. **The implementation remains
     incomplete relative to that target** — no code change has routed `NarratorEngine`'s narrative
     into `ReflectionGenerator` as an input, and no test verifies `ReflectionGenerator` as the sole
     point of final composition. Closing that gap is separately-authorised future work, not
     implied as done anywhere in this document.

9. ✅ **Persona / Mentor Mode packs (config-driven)** — verified against acceptance criteria and one
   real gap closed 2026-07-20.
   - System prompt packs (e.g., Calm Mentor / Coach / Gamer Ally) selectable via config/UI without code edits.
   - **Acceptance:** switching persona changes tone/constraints; logged in audit trail.
   - **Verify:** `pytest -q tests/test_persona_pack.py tests/test_narrator.py::TestPersonaNarrativeOverrides`
     (this doc previously cited a nonexistent `tests/test_persona_switching.py` -- corrected
     2026-07-20) + manual API smoke (done, see below).
   - **Verified independently against this item's specific acceptance criteria** (already-implemented
     in `bartholomew/kernel/persona_pack.py`/`PersonaPackManager`, wired into `daemon.py`;
     `tests/test_persona_pack.py` exists and passes -- but that alone doesn't prove the acceptance
     criteria hold, so checked each piece directly):
     - ✅ Config-driven, no-code-edit packs: `config/persona_packs/*.yaml` (default/caregiver/tactical).
     - ✅ Switch is logged to an audit trail: `persona_switch_log` SQLite table, retrievable via
       `GET /api/persona/history` -- confirmed live via a manual `TestClient` smoke test
       (`list`/`switch`/`current`/`history` all exercised end-to-end against the real FastAPI app).
     - ✅ Switching has a real behavioral effect on `ExperienceKernel`: `_apply_drive_boosts()`
       actually mutates drive `context_boost` values per the active pack's `drive_boosts`.
     - ❌ → ✅ **Switching changing "tone" was false until this fix.** `PersonaPack.narrative_overrides`/
       `tone`/`style` existed as rich, well-designed data (e.g. tactical: "Target acquired:
       {target}.", caregiver: "I noticed you might be feeling {emotion}. I'm here if you need
       me.") and `PersonaPackManager.get_narrative_templates()`/`get_style()`/`get_tone()` existed
       as accessors -- but `narrator.py` never called into `PersonaPackManager` anywhere. Proved
       directly: switching between "default" and "tactical" produced byte-identical narrative
       rotation, driven purely by an internal counter, completely independent of which persona was
       active. Fixed: `NarratorEngine` takes an optional `persona_manager` constructor arg (plus a
       `set_persona_manager()` setter for post-construction attachment) and a new `_get_templates()`
       helper that checks the active pack's `narrative_overrides` for the current
       `episode_type`/tone first, falling back to the existing static `NarrativeTemplates` when the
       persona has no override for that specific tone. Affect-driven tone selection
       (`determine_tone()`) is unchanged -- a persona only overrides which literal strings are used
       for a given tone, not which tone gets picked. `daemon.py` reordered to construct
       `persona_manager` before `narrator` (no dependency issue -- `PersonaPackManager` only needs
       `experience`/`workspace`, both already constructed earlier) so it can be passed straight in.
       Verified end-to-end: forcing the same NEUTRAL tone and switching from "default" to
       "tactical" changes `generate_attention_episode()`'s actual output text
       ("My attention shifted to..." → "Attention locked on...").
   - Added `tests/test_narrator.py::TestPersonaNarrativeOverrides` (5 tests) and one assertion in
     `tests/test_stage3_integration.py::TestDaemonIntegration::test_daemon_has_stage3_modules`
     confirming `daemon.narrator._persona_manager is daemon.persona_manager` (the wiring itself,
     not just the underlying mechanism).
   - Verified: full `pytest -q` remains fully green (0 failures). `ruff check` clean.

### P2 — Modularity: skill registry + a few safe starter skills
10. **Skill manifest + registry** (local "marketplace" later)
    - Standard manifest schema (id, purpose, permissions, data touched, risk class, tests).
    - **Acceptance:** skills discoverable, loadable, and permission-scoped.
    - **Verify:** `pytest -q tests/test_skill_registry.py`.

11. **Starter skills (safe + reversible)**
    - `tasks.basic` (add/list in SQLite)
    - `notify.*` (log fallback)
    - `calendar.draft_block` (draft-only; behind consent)
    - **Acceptance:** end-to-end: prompt → decide → tool call (with consent) → persisted + audited.
    - **Verify:** `pytest -q tests/test_end_to_end_tasks_and_audit.py`.

#### P2 investigation & wiring (2026-07-21)

Same pattern as P1's Experience Kernel: the manifest schema, `SkillRegistry`,
`SkillBase`, permission model, and all three starter skills (`tasks`,
`notify`, `calendar_draft`) already existed, fully built and unit-tested
(`tests/test_skill_registry.py`, 48 tests) -- but nothing in the live system
ever constructed a `SkillRegistry` or routed a request into it. `Planner`
was a 19-line stub whose `decide()` always returned `None`, and
`KernelDaemon` never imported `skill_registry` at all. The acceptance
criterion ("prompt → decide → tool call (with consent) → persisted +
audited") was unmet end-to-end even though every individual piece passed
its own tests in isolation. Fixed:

- **Planner**: added `Planner.handle_skill_request(skill_id, action, params)`
  -- validates the request names a real, loaded skill/action (the "decide"
  step), then delegates to `SkillRegistry.execute_action()` for consent
  resolution, execution, and audit. `set_skill_registry()` setter added
  since `KernelDaemon.__init__` constructs `Planner` before the Stage 3/4
  modules `SkillRegistry` depends on.
- **`daemon.py`**: constructs `SkillRegistry` and wires it into `Planner`;
  `start()` loads enabled skills (falling back to loading every discovered
  starter skill on a fresh database, so they work out of the box);
  `stop()` shuts the registry down.
- **Parking brake**: `SkillRegistry.execute_action()` now checks the global
  `ParkingBrake`'s `"skills"` scope before every execution and fails closed
  (blocks) if the check itself errors -- previously nothing in the skill
  system consulted the brake at all, despite `config/policy.yaml` already
  documenting a `"skills"` scope for it.
- **"ask" consent resolution**: `calendar_draft`'s manifest was `level:
  "auto"` (auto-granted, no consent) despite the backlog explicitly calling
  for it to be "draft-only; behind consent" -- changed to `level: "ask"`.
  Added `SkillRegistry._resolve_permissions()`, which resolves `"ask"`-level
  requirements via the same consent-handler mechanism already used for
  memory-write consent (`bartholomew.kernel.memory.privacy_guard`), rather
  than inventing a second one. Grants are session-scoped only. Fails closed
  (denies) with no handler registered.
- **Action audit trail**: added a `skill_action_audit` table (distinct from
  the existing `permission_audit`, which only logs permission checks) that
  records every `execute_action()` attempt -- success, failure, permission
  denial, or brake block -- with PII-redacted params, via a single
  `_finish()`/`_audit_execution()` choke-point.
- **Bug found and fixed along the way**: `SkillRegistry._setup_subscriptions()`
  passed its async event handler directly as `GlobalWorkspace.subscribe()`'s
  *sync* `callback` slot instead of `async_callback`. `GlobalWorkspace.publish()`
  (the sync path used throughout the kernel -- `daemon.py` startup/shutdown
  events, `skill_base.py`'s `_emit_event()`, `working_memory.py`) only ever
  invokes the sync `callback`, so it created the handler coroutine and
  immediately discarded it without running any of its body -- skill
  event-driven reactions (e.g. `calendar_draft` auto-creating a block from a
  `tasks.task_created` event) had silently never fired. Fixed by having the
  sync `callback` schedule the async handler via
  `asyncio.get_running_loop().create_task(...)` (failing safe -- logs and
  drops the event -- if there's no running loop), while `async_callback`
  continues to serve `publish_async()` directly.
- Added `tests/test_end_to_end_tasks_and_audit.py` (4 tests): an "auto"
  skill (`tasks.create`) persisting + auditing; an "ask" skill
  (`calendar_draft.create`) approved via a registered consent handler; the
  same "ask" flow denied with no handler registered (fail-closed); and the
  parking brake blocking then, after `disengage()`, allowing the same
  action.
- Verified: full `pytest -q` remains green. `ruff check` clean.

### P2.5 — Runtime Convergence (architectural prerequisite) ✅ Complete 2026-07-24 (item 11.22)

**Full narrative moved to the archive (2026-07-28).** The complete P2.5 write-up — the "two
brains" audit finding, Principle Zero / Principle One / the Architectural Invariant, the Runtime
Contract shape, and all 22 dated items (11.1–11.22) with their acceptance criteria and verify
commands — is preserved verbatim in
[docs/archive/ENGINEERING_LOG_2026.md](docs/archive/ENGINEERING_LOG_2026.md) under the same
heading and item numbers, so every existing cross-reference to "item 11.x" in `DECISIONS.md`,
`RISKS.md`, `INTERFACES.md`, `TEST_MATRIX.md`, and `ROADMAP.md` still resolves. Compact index of
what each item did:

| Item | One-line summary | Status |
|---|---|---|
| 11.1 | Authority ownership table for 4 duplicated concepts (model routing, persona, permission gates, kill-switch) | ✅ done 2026-07-21 |
| 11.2 | Identity Context → Executive → Policy Decision, for skill execution (scheduler-drive attempt reverted, see `DECISIONS.md`) | ✅ done 2026-07-21 |
| 11.3 | Runtime Contract as a code seam (`runtime_contract.py`), chat + skill-execution | ✅ done 2026-07-21 |
| 11.4 | Wire chat into the Experience Kernel (`/api/chat` routes through the seam) | ✅ done 2026-07-21 |
| 11.5 | Author `COGNITIVE_RUNTIME.md` | ✅ done 2026-07-21 |
| 11.6 | Wire chat's Governance stage into the Policy Decision check (`_CONVERSATIONAL_KINDS` exemption) | ✅ done 2026-07-21 |
| 11.7 | Wire recent conversation history into chat's Interpretation stage; found the 5th duplicated-memory-injection concept | ✅ done 2026-07-21 |
| 11.8 | Reflection pipelines appended (not unified) — see `COGNITIVE_RUNTIME.md`'s reflection-ownership section for the corrected framing | ⚠️ additive only, not architecturally unified (corrected 2026-07-28) |
| 11.9 | Scenario replay test harness; found a real restart-persistence bug in `ExperienceKernel` | ✅ done 2026-07-21 |
| 11.10 | Fixed five live 500s in the `self_state` API router; first HTTP-level test file for it | ✅ done 2026-07-21 |
| 11.11 | Wired `NarratorEngine.search_episodes()` into a real route | ✅ done 2026-07-21 |
| 11.12 | Retired the deprecated persona module; migrated 2 callers to `PersonaPackManager` | ✅ done 2026-07-22 |
| 11.13 | Deleted the deprecated kill-switch adapter (zero live callers) | ✅ done 2026-07-22 |
| 11.14 | Retired the deprecated tool-policy module; migrated 1 caller to `evaluate_tool_policy()` | ✅ done 2026-07-22 |
| 11.15 | Reclassified "model routing" as not a duplicate pair; un-deprecated `select_model` | ✅ done 2026-07-22 |
| 11.16 | Unified the two Reflection *shapes* (chat + skill execution) into one `ActionReflection`/sink | ✅ done 2026-07-23 |
| 11.17 | Scheduler-drive convergence — Observation/Governance for the scheduler surface | ✅ done 2026-07-23 |
| 11.18 | Scheduler persistence off the event loop; fixed a CI-caught deadlock hazard | ✅ done 2026-07-24 |
| 11.19 | Skill-execution convergence — Observation/CandidateAction for the skill surface | ✅ done 2026-07-24 |
| 11.20 | RISKS.md R1 red-team test suite (consent bypass / privacy leakage) | ✅ done 2026-07-24 |
| 11.21 | Voice/sight convergence — governed seam for the two remaining device surfaces | ✅ done 2026-07-24 |
| 11.22 | Reclassified Exit Gate Q7's voice/sight-persona residual to Stage 6; declared Stage 4.5 complete | ✅ done 2026-07-24 (docs only) |

**Runtime Convergence Exit Gate — status as of item 11.22 (2026-07-24): all seven questions
satisfied within Stage 4.5's scope; Stage 4.5 is complete.** See `COGNITIVE_RUNTIME.md`'s Exit
Gate table for the live, per-question evidence — that table, not this index, is the
continuously-updated scorecard.

### P3 — Initiative engine (proactive nudges) and workflows

**S5.0 — Deterministic scheduler-schema readiness at startup (prerequisite; closes issue #24).**
✅ implemented 2026-07-25 (separate narrow PR, landed before Stage 5 feature work). `KernelDaemon.
start()` now `await`s `scheduler_store.ensure_schema()` immediately after `MemoryStore.init()` and
before any side-effectful init or the scheduler task, so `scheduled_tasks`/`ticks` (and the
additive `nudges`/`reflections` integer columns) exist before `start()` returns. **Fail-closed:** a
schema-init error closes the scheduler store (no worker-thread leak) and propagates, so a
half-initialized daemon never comes up. Row-seeding stays in `run_scheduler()` (idempotent);
PR #23's endpoint tolerance is retained as defense in depth. Rationale, alternatives, and the four
locked sub-decisions (fail-closed / no-outer-timeout / schema-only / endpoint-tolerance) are in
DECISIONS.md's "Scheduler schema is created synchronously during KernelDaemon.start()..." entry.
**Verify:** `pytest -q tests/test_scheduler_startup_readiness.py` (**10 tests** — count corrected
2026-07-27 from "5", which was wrong when written: tables-exist-at-return; ordered-record +
asyncio-barrier proofs that schema readiness precedes scheduler-task creation and the loop's first
DB op; fail-closed cleanup, including that a failing cleanup does not mask the primary error;
cancellation and later-stage-failure cleanup; successful startup leaves the store open; idempotent
`ensure_schema`) — green on the 3.10 + 3.11 matrix; full `pytest -q` clean. Merged 2026-07-25 in
PR #25, merge commit `3496cfb`; **closes issue #24** (confirmed closed).

**S5.1 onwards — NOT STARTED (as of 2026-07-27).** No Stage 5 feature code exists: no typed
cadence, no proactive consent/mute, no quiet-hours defer, no dry-run, no rationale logging, no
`allow_proactive` governance category. S5.0 is a *prerequisite* that landed early; it is not
Stage 5 in progress. Beginning S5.1 requires separate explicit approval.

12. **Scheduler-driven check-ins + workflows** *(revised Stage 5 sequence — safety scaffolding
   before live proactivity: typed cadence → default-OFF consent + functional mute → quiet-hours
   defer-not-suppress → dry-run → rationale logging → then live check-in/weekly/next-best-action
   drives under a default-deny `allow_proactive` governance category)*
   - Morning/evening check-in; weekly review; “next best action” suggestion engine.
   - **Acceptance:** runs on schedule, respects quiet hours and parking brake; produces suggestions only (no Act).
   - **Verify:** `pytest -q tests/test_scheduler_checkins.py` + dry-run mode.

### P4 — Distributed being (cross-device) + voice adapters
13. **Cross-device thin client (PWA) + auth**
    - Token auth; shared session state; chat + timeline.
    - **Acceptance:** same state visible from two clients; no unauthenticated access.
    - **Verify:** integration tests + `curl` smoke.

14. **Voice adapters (optional / graceful unavailable)**
    - STT/TTS endpoints return "unavailable" when binaries missing.
    - **Acceptance:** voice endpoints fail gracefully; do not crash kernel.
    - **Verify:** `pytest -q tests/test_voice_adapters.py`.

### P5 — Embodiments (future)
15. **Mode system + signals** (Work/Life/Game/Car)
16. **Smart home integration** (read-only first)
17. **Gaming overlays** (separate surface; strict privacy + safety review)

---

## Archived engineering narrative (2026-07-28)

Two large historical sections previously lived here in full and have been moved, verbatim, to
[docs/archive/ENGINEERING_LOG_2026.md](docs/archive/ENGINEERING_LOG_2026.md):

- **"Full test suite investigation — 38 failures → 9 → 4 → 2 → 0"** (fixed 2026-07-20): 15 distinct
  root-caused bugs across 4 "rounds," ending in a fully green `pytest -q`.
- **"Experience Kernel MVP: bug fix + privacy gap"** (fixed 2026-07-20): the silently-swallowed
  `AttributeError` that had disabled the kernel's entire tick loop since Stage 3 landed, and the
  PII-redaction gap in `ExperienceKernel`/`NarratorEngine` free-text fields.

## Echo ideas — moved off the canonical plan (2026-07-28)

The brainstorm-derived "Echo Integration Roadmap" (45 features across 4 conceptual gates) that
previously lived here has been moved to
[docs/incubator/ECHO_IDEAS.md](docs/incubator/ECHO_IDEAS.md), which is explicitly non-canonical
and non-authoritative — embedding a second agent kernel, memory architecture, and permissions
system as canonical plan content conflicted with `CONSTITUTION.md`'s "one architectural authority
per concept" principle. See `ROADMAP.md`'s equivalent note for the full rationale.

## Risks summary

See [RISKS.md](RISKS.md) (privacy, consent bypass, platform-specific SQLite/FTS behavior, test flakiness, metrics duplication).

## Decisions summary

See [DECISIONS.md](DECISIONS.md) (SSOT docs, fail-closed governance, single DB, consent gates at lowest layer, etc.).

## Assumptions summary

See [ASSUMPTIONS.md](ASSUMPTIONS.md) (CI on Linux is the health baseline; Windows locking is noise; SQLite build features vary).

## Test expectations summary

See [TEST_MATRIX.md](TEST_MATRIX.md).

## Perf budgets summary

See [PERF_BUDGETS.md](PERF_BUDGETS.md).

## Next 3 Moves (always current)

> **Updated:** 2026-07-28 (documentation reconciliation pass 2 — approved sequencing). Supersedes
> the 2026-07-27 list below, which ordered Phase B, then Stage 5/S5.1, then Stage 1. See
> `ROADMAP.md`'s "Near-term milestone plan" for the full corrected sequence and rationale (Stage 1
> must precede Stage 5 because Stage 5's live proactivity needs a user-facing governance surface
> that only Stage 1 provides).

**The actual next moves, as of 2026-07-28 (each step below requires its own separate, explicit
approval before work begins — this list records sequencing, not authorisation):**

1. **Documentation reconciliation and the deployment-architecture decision** — this pass. The
   deployment decision (hybrid local-first) is now recorded in `DECISIONS.md`. The noncanonical-
   document cleanup pass (previously listed below as unscheduled) is substantially complete as of
   this pass — see the changed-file list presented alongside this update for what was
   updated/archived/merged/deleted.
2. **Design Phase B** — persistence-ownership stabilisation — against the approved hybrid
   local-first architecture. Design only; not started; not approved for implementation. Evidence
   base preserved in `RISKS.md`'s tech-debt watchlist (mixed `aiosqlite`/sync-`sqlite3`/
   scheduler-thread ownership of one file, two near-duplicate `db_ctx` modules, the unresolved
   `TRUNCATE`-outlasts-its-busy-timeout question, and the intermittent
   `test_wal_cleanup_concurrent_processes` failure under full-suite load).
3. **Build a minimal Stage 1 consumer web governance shell**, after separate approval of both
   this sequencing and the Stage 1 scope itself (parking-brake access, consent/approval inbox,
   notification/mute controls, awaiting-response queue, audit/provenance visibility, host-device
   onboarding — see `ROADMAP.md`'s Stage 1 section). **In progress as of 2026-08-01:** Phase B
   merged (PR #33); Stage 1 is now staged as sub-stages S1.0–S1.6 (`docs/STAGE_1_OVERVIEW.md`),
   with **S1.1 (Parking Brake API + UI), S1.3 (notification settings + mute/quiet-hours), and S1.5
   (governance audit/provenance view) implemented** — S1.4 and S1.6 remain scoped-only, each
   requiring its own separate approval. **2026-08-03:** a standalone, adjacent-but-not-S1.2 fix
   landed — sensitive-content memory writes are now queued for review instead of silently
   discarded when no consent handler is registered (see `docs/STAGE_1_OVERVIEW.md`'s "Standalone:
   consent-handler fix"). **2026-08-04: S1.2 (consent/approval inbox) implemented** — closes the
   separate `memory_rules.yaml`/`should_store()` `ask_before_store` gap, reusing the
   consent-handler fix's `pending_sensitive_writes` inbox rather than a parallel one.
4. **Stage 5 / S5.1 remains paused**, now sequenced *after* Stage 1 rather than before it, pending
   its own explicit approval. The locked internal sequence (safety scaffolding before live
   proactivity) is recorded under P3 above and in `ROADMAP.md`. Live proactive *reflection*
   behaviour specifically also remains blocked on the reflection-ownership implementation gap
   (see `COGNITIVE_RUNTIME.md`).

Also open but unscheduled (each requiring separate approval): issue #22 (forward `IdentityContext`
through the voice/sight compat wrappers), open and deferred to Stage 6; Phase A's deferred
findings F9–F11, recorded in `RISKS.md`; jurisdiction-aware capture/recording compliance work,
adaptive-notification/awaiting-response delivery beyond the Stage 1 baseline, and data-export/
portability delivery (all added to `ROADMAP.md`'s Stage 6 scope 2026-07-28); the future,
separately-authorised code-cleanup decision on whether to remove the legacy water-logging
endpoints/table/UI (see `RISKS.md`'s tech-debt watchlist).

**Historical (2026-07-20 list, all done):**

1. ✅ ~~Fix P0 packaging issues (items 0–1)~~ — done 2026-07-20.
2. ✅ ~~Fix malformed memory_rules.yaml + refactor `input()` out of kernel (items 2–3)~~ — done 2026-07-20.
3. ✅ ~~Pin the dependency set, fix the CI install step, fix the test DB-path hang~~ — done 2026-07-20.
4. ✅ ~~Fix `identity_interpreter/adapters/consent_terminal.py`'s blocking `input()`~~ — done 2026-07-20.
5. ✅ ~~Fix non-environmental failing tests from `docs/archive/STATUS_2025-12-29.md` (item 7)~~ — done
   2026-07-20; see `docs/archive/ENGINEERING_LOG_2026.md`'s "Full test suite investigation".
   `docs/archive/STATUS_2025-12-29.md` itself is now stale/superseded on this topic and shouldn't be
   treated as current status.
6. ✅ ~~CI minimal gates (Linux)~~ — done 2026-07-20. `pytest -q -m smoke`, `ruff check .`,
   `black --check .` were already running in CI (`.github/workflows/pre-commit.yml`, the
   latter two via the `pre-commit run --all-files` step); added a `Run full test suite`
   step running plain `pytest -q` right after the smoke step, in the same `lint-test` job
   (both Python 3.10 and 3.11). No quarantine/`xfail` markers needed — the full suite was
   already fully green locally (see `docs/archive/ENGINEERING_LOG_2026.md`'s "Full test suite
   investigation") by the time this landed, so there was nothing left to triage first.
   - **Verify:** GitHub Actions green on Linux (`lint-test` job, both matrix legs).

## P0 status: complete (as of 2026-07-20)

All P0 backlog items (0–7, "Packaging & Architecture" and "Make the build trustworthy") are
done: packaging/dependency fixes, config bugs, blocking-`input()` refactors, the full
38 → 0 test-failure sweep, and the full-suite CI gate above.

**Caveat resolved 2026-07-27:** this section previously said the "Green core on CI Linux" gate
was "met pending a real CI run confirming the new `Run full test suite` step passes on GitHub
Actions." That confirmation now exists — `pre-commit.yml`'s `lint-test` job (3.10 and 3.11) has
run green on merged pull requests, most recently on PR #26's head `e923fb9`, alongside Phase A's
`ci.yml`. The gate is met, not pending.

Remaining work moved to P1 (Experience Kernel MVP) and beyond — see the Backlog above.

## Pending Approvals

> **Status:** Tracks proposed changes through approval and commit lifecycle.
>
> **Process:** Agent proposes → User reviews → User approves → Commit is executed → Record in ledger
>
> **Rule:** Never mark anything as committed without a commit hash.

### Pending (awaiting user approval)
- 2026-07-27 — Planning-document reconciliation (documentation-only; no production code, tests,
  dependencies, workflows, configuration or schema touched) — **not yet committed**

### Approval Ledger
Record of approved changes with commit tracking (most recent 5):

> Populated 2026-07-27. This ledger read "*No entries yet*" (dated 2026-01-19) while five
> approved changes had in fact been merged to `main` — the ledger was unused, not empty by
> fact. Each entry below cites a real merge commit verified with `git log`; nothing here is
> recorded as committed without one.

- 2026-08-07 — S5.5: dry-run mode as a Governance/safety primitive — simulated
  propose/deliver transactions never write to real ground truth (`initiatives`,
  `initiative_audit`, the unified Reflection sink, `skill_action_audit`, Working Memory),
  recording to a separate `dry_run_results` table instead; a global `dry_run_state`/
  `dry_run_audit` switch OR-composed with the caller's own request (`effective_dry_run =
  caller_dry_run OR globally_engaged`, so a caller can never override an engaged switch); a
  `side_effect` skill-manifest flag (fail-closed default) gating whether `SkillRegistry`
  actually calls `execute()` under dry-run (`docs/S5_5_DRY_RUN_MODE_DESIGN.md`) — Approved by
  project owner in two steps: (1) design/technical-direction approval with one correction
  raised and resolved before implementation (`DryRunResult.approval_requirements` upgraded to
  carry real per-gate evidence — Parking Brake scope/checked/blocked, Identity Policy
  checked/allowed/reason, consent checked/consented, plus informational `category_mute` on
  deliver — rather than a coarse summary, without weakening or duplicating Governance's own
  allow/deny computation; and a dry-run-switch resolution failure on a live `propose`
  represented as an infrastructure/safety-resolution failure (`outcome="error"`) rather than a
  fabricated Governance denial, writing zero `initiatives`/`initiative_audit`/`dry_run_results`
  rows); (2) a further correction identified by the project owner after the first round — the
  same resolution-failure path was still falling through to a real unified-Reflection-sink
  write (`if dry_result is None:` alone doesn't distinguish "simulated" from "resolution
  failed," since `dry_result` is `None` in both cases), fixed with one added guard clause
  (`and not dry_run_resolution_failed`) and proven with tests that were confirmed to fail
  against the pre-fix code and pass after; (3) final commit approval ("approved") — Commit:
  `15ef139` (branch `claude/next-priorities-7qso6n`)
- 2026-08-07 — S5.4: quiet-hours defer with an extensible notification suppression-policy
  registry, per-initiative delivery_policy overrides, richer audit detail, and coalesced digest
  notifications (`docs/S5_4_QUIET_HOURS_DEFER_DESIGN.md`) — Approved by project owner, with an
  explicit confirmation that all delivery paths remain fully subject to the three Runtime
  Contract Governance gates (ParkingBrake, Identity Policy, per-category consent) and that
  category mute/consent are never bypassed by any delivery_policy value — Commit: `5498e95`
  (branch `claude/next-priorities-7qso6n`)
- **[RETROSPECTIVE BACKFILL, entered 2026-08-07 — this entry documents an approval that already
  occurred, it is not a new approval]** 2026-08-07 — S5.3: default-off per-category consent and
  functional mute, layering on the existing global NotifySkill mute/quiet-hours system rather
  than duplicating it, plus the `initiative_delivery_check` drive that enforces both
  (`docs/S5_3_DEFAULT_OFF_CONSENT_AND_MUTE_DESIGN.md`) — Approved by project owner in two steps,
  both on the record in this session's transcript: (1) technical-direction approval, explicitly
  endorsing muted→defer / revoked-consent→cancel and correcting process to require explicit
  approval before any future commit ("you have my approval to proceed with implementing S5.3
  exactly as proposed"); (2) post-implementation commit approval ("Approved. The implementation,
  testing and verification all look good ... Please commit this work") — Commit:
  `0901c10b0fdce9aebfbc0c3faa56dab0abbccb4e` (branch `claude/next-priorities-7qso6n`). This entry
  was missing from the ledger until identified as a gap and backfilled at the project owner's
  explicit request; no new approval was sought or given to create it.
- 2026-07-28 — Documentation reconciliation pass 2 — canonical-document audit and noncanonical
  documentation cleanup — Approved by project owner — Commit:
  `8df4efb7ad6a7cda3a8b2d5fd0a90533ace497c0` (recorded on
  `claude/bartholomew-docs-reconciliation-i18mlf`; merge status is evidenced by repository and
  PR history)
- 2026-07-27 — Phase A: truthful cross-platform verification (PR #26) — Approved by project
  owner — Commit: `8b96319c4059d9dfada2579ca5f6da22b34e1f31`
- 2026-07-25 — S5.0 scheduler startup readiness, closes issue #24 (PR #25) — Approved by
  project owner — Commit: `3496cfb8364b22c4df63f803d939df4883c52af3`
- 2026-07-25 — Items 11.19–11.22: skill + voice/sight runtime convergence, consent-bypass
  red-team suite (PR #21) — Approved by project owner — Commit: `187ef02`
- 2026-07-25 — `/api/liveness/ticks` missing-table tolerance (PR #23) — Approved by project
  owner — Commit: `cb98c65`
- 2026-07-24 — Item 11.18: scheduler persistence off the event loop; WAL checkpoint default
  — Approved by project owner — Commits: `bc5f24d`, `29d0ec9` (landed as direct commits on
  `main`, not via a merge commit; no PR number is recorded in either commit message)

**Ledger format:**
- YYYY-MM-DD — <short description> — Approved by <user> — Commit: <hash> (or **not yet committed**)

## Quality gates

- Governance invariants preserved (parking brake, consent gates, redaction/encryption rules).
- Unit + integration tests updated and passing (or explicitly quarantined with justification).
- Interfaces updated if contracts change.
- Risks/assumptions/decisions updated.

## Definition of Done (DoD)

A change is “done” only when:

- Implementation complete.
- Tests added/updated and passing (or explicit reason + quarantine).
- Lint/format/type checks pass (if enabled).
- Canonical docs updated if behavior/interfaces changed.
- Acceptance criteria verified.
- Governance not regressed (consent, parking brake, privacy rules).
- Rollback note included if risky.
- CI Gatekeeper satisfied (see [CI.md](CI.md)).
