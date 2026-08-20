# MASTER_PLAN

> **Single Source of Truth (SSOT)** for what Bartholomew is, what matters, where we are, and what we do next.
>
> **Last updated:** 2026-08-20 — **Real-World Test #1 is complete and its Decision Register is
> approved.** Taylor approved **Post-Test #1 Decision Register v2.2** on 2026-08-20 as the
> authoritative Post-Test #1 Decision Register, establishing decisions D1–D15, safety gates S1–S11,
> product gates P1–P9, readiness Bands 0/A/B/C/D and implementation Tracks 1–7 as project authority.
> The Test #1 evidence freeze is commit **`854a8da7fd107db33a933c4bdb01bf3fd7eb69bd`** (merge commit
> for PR #58) — **not** `main`. The register is preserved at `docs/evidence/test-1/`; the decisions
> are in `DECISIONS.md`; the bands are in `ROADMAP.md`. **This is documentation-only and authorises
> no implementation:** the resulting doc changes are listed under "Pending (awaiting user approval)"
> and must clear the Approval Gate before any implementation work package is proposed. The
> Parking Brake scope count is corrected repository-wide from five to six (`training`), and
> `RISKS.md`'s hydration entry is amended per D4 rather than duplicated. "Next 3 Moves" item 4 is
> updated from "put slice 1 into real-world use" (now done) to the Approval-Gate review.
>
> **Previously (2026-08-17):** **P0–P6 stabilisation merged (PR #56)** and the **server-centric
> deployment architecture recorded** in `DECISIONS.md`, superseding "hybrid local-first". The
> stabilisation repaired the reflection model path (no reflection had ever been model-composed),
> untracked two runtime databases the test suite was mutating, separated cloud *configured* from
> cloud *ready*, and made `/api/health` report model reachability distinctly from model selection;
> `docs/FIRST_REAL_WORLD_TEST.md` is the controlled-test procedure. The architecture entry is
> **documentation-only and authorises no implementation** — no multi-tenancy, cloud infrastructure,
> device agent or authentication exists. **The Usable POC / time-to-real-use priority and
> `docs/TILT.md` sequencing are unchanged**, and "Next 3 Moves" below still stands.
>
> **Previously (2026-08-15):** **platform/personal-identity architecture recorded**
> (documentation-only). Bartholomew is architecturally **one shared platform serving many strongly
> isolated personal Bartholomew identities**; a new user never receives a duplicated copy of the
> stack or model; **Bartholomew is not the LLM**; personal identity/state must survive changes of
> device, backend, database, AI provider and model generation; a lightweight client is the long-term
> direction, but local Governance authority (parking brake above all) must never become
> cloud-dependent. `CONSTITUTION.md` carries the enduring principle and a **binding
> conflict-surfacing rule**; `DECISIONS.md` carries the decision and rationale. **Nothing is
> implemented and no scope changed** — this deployment remains a single-user PoC, that remains
> correct, and `docs/TILT.md`'s real-world-use priority is unchanged. "Next 3 Moves" below is
> **unchanged**: putting slice 1 into real-world use is still the next move.
>
> **Previously (2026-08-14):** **Usable POC slice 1 (Personal Memory Capture and Recall) is
> implemented and approved** (`2d443a9`). Ordinary conversation now produces durable, retrievable
> memory through the existing governed write path; chat retrieval sees it; and the `notify` skill
> has a real outbound delivery channel. This closes the three gaps the 2026-08-12 assessment named
> as the reason none of the built machinery had generated real feedback. **"Next 3 Moves" item 4 is
> now the next move: put slice 1 into real-world use** — not further slice-1 engineering, per
> `docs/TILT.md`. Slice 1's completion does not authorise slice 2. See `DECISIONS.md`'s "Usable POC
> slice 1 implementation approved" entry.
>
> **Previously (2026-08-12):** Usable POC / time-to-real-use prioritisation approved: a
> repository-grounded assessment found that persistence, governance, and competency-retrieval
> machinery are well-built and well-tested, but ordinary use of Bartholomew has not yet generated
> real feedback — most notably, ordinary conversation writes nothing durable and retrievable. New
> canonical document `docs/TILT.md` formalises the resulting execution-sequencing principle:
> real-world testing of a sufficiently-functional vertical slice now takes priority over further
> polish/hardening of that slice, except where safety, governance, privacy, data integrity,
> architectural validity, or experiment validity are at stake. "Next 3 Moves" below is rewritten
> accordingly. This is a **documentation-only** pass — no code, tests, or configuration changed.
> See `DECISIONS.md`'s "Usable POC / time-to-real-use prioritisation" entry for full rationale,
> alternatives considered, and the conflict this resolves with `CONSTITUTION.md`'s "Development
> Philosophy" section.)
>
> **Previously (2026-08-08):** New Direction reconciliation: P3 restructured to cover the
> competency/training/learning architecture (`ROADMAP.md` Stage 5 S5.1–S5.4) ahead of the
> pre-existing initiative-engine scope (now S5.5–S5.7, preserved unchanged in substance); "Next 3
> Moves" updated to insert this work between Stage 1 and live proactivity. See `CONSTITUTION.md`'s
> "One Developing Digital Individual" section and `DECISIONS.md`'s "One developing digital
> individual — competency and training architecture" / "Stage 5 restructured around competency
> and training before live initiative" entries. No implementation authorised by this pass.)
>
> **Previously (2026-07-28):** documentation reconciliation pass 2: this document trimmed from a
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
   - Parking-brake semantics for subsystems (global/skills/sight/voice/scheduler/training).
     *Corrected 2026-08-20: this line omitted `training`. `COGNITIVE_RUNTIME.md`'s "The kill-switch:
     `ParkingBrake`" section is the canonical authority for the scope list and its semantics.*

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

**14 documents.** This list is the registry; `DECISIONS.md`'s "Canonical SSOT docs" entry and
`CONSTITUTION.md`'s handover note describe the same set. (Corrected 2026-07-27: this list
previously omitted `CONSTITUTION.md`, contradicting `DECISIONS.md`'s "Adopt `CONSTITUTION.md`
as a canonical SSOT doc" entry, which explicitly puts the count at 13. **Amended 2026-08-12:**
`docs/TILT.md` added as the 14th — a deliberate, narrow exception to the "everything under
`docs/` is a reference" rule below, since it governs binding near-term execution sequencing the
same way `ROADMAP.md` and `DECISIONS.md` do. See `DECISIONS.md`'s "Usable POC / time-to-real-use
prioritisation" entry.)

- **MASTER_PLAN.md** (this doc)
- [CONSTITUTION.md](CONSTITUTION.md)
- [COGNITIVE_RUNTIME.md](COGNITIVE_RUNTIME.md)
- [ROADMAP.md](ROADMAP.md)
- [docs/TILT.md](docs/TILT.md) — current execution-sequencing priority (Usable POC / time-to-real-use)
- [DECISIONS.md](DECISIONS.md)
- [RISKS.md](RISKS.md)
- [ASSUMPTIONS.md](ASSUMPTIONS.md)
- [INTERFACES.md](INTERFACES.md)
- [CHECKLISTS.md](CHECKLISTS.md)
- [REVIEWS.md](REVIEWS.md)
- [CI.md](CI.md)
- [TEST_MATRIX.md](TEST_MATRIX.md)
- [PERF_BUDGETS.md](PERF_BUDGETS.md)

**Not canonical, but load-bearing (added 2026-08-20):** `docs/evidence/test-1/` is the stable,
referenceable **evidence location** for Real-World Test #1 — the preserved Post-Test #1 Decision
Register v2.2, SHA-256 checksums, the Test #1 commit-provenance record, and an explicit inventory of
the raw artifacts that are **absent**. **It is a location, not an authority**: the count above stays
at 14, the decisions live in `DECISIONS.md`, and the readiness bands live in `ROADMAP.md`.

Every other `*.md` in the repository (implementation notes, the rest of `docs/*`, `STATUS_*`,
`README`s) is a **reference**, not an authority on project status. Where one contradicts a
canonical doc, the canonical doc wins. Two locations are explicitly and permanently
non-authoritative by design, not merely by omission: `docs/incubator/` (unapproved,
individually-evaluated-only ideas — see `docs/incubator/ECHO_IDEAS.md`) and `docs/archive/`
(superseded historical material, kept for record — see `docs/archive/ENGINEERING_LOG_2026.md` and
the other archived files listed in `RISKS.md`'s tech-debt watchlist).

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
- **This deployment serves exactly one personal Bartholomew identity** *(added 2026-08-15)*. The
  diagram above is a **single-user PoC deployment**, and that is correct for this stage — it is
  **not** evidence that multi-user or cloud infrastructure exists, because none does. Architecturally
  this runtime is the *first personal Bartholomew identity on an early deployment of a platform that
  will later serve many strongly isolated personal identities*; the shared platform, the replaceable
  underlying models, and a user's persistent personal Bartholomew are three distinct layers, and
  **Bartholomew is not the LLM**. See `CONSTITUTION.md`'s "One Platform, Many Personal Bartholomews"
  section (the authority), `DECISIONS.md`'s corresponding entry, `COGNITIVE_RUNTIME.md`'s
  "Personal-identity ownership" subsection (what the code assumes today), and `ASSUMPTIONS.md` A9.
  Building any of that platform infrastructure is **not current scope** — see `ROADMAP.md`'s "What
  we will not do yet".
- **The intended destination is a server-centric platform with local/edge capability agents**
  *(added 2026-08-17)*. `DECISIONS.md`'s "Deployment architecture — server-centric Bartholomew with
  local/edge capability agents" — which **supersedes** the 2026-07-28 "hybrid local-first" entry —
  makes core cognition and personal memory server-centric by default, reached through a web
  application, with optional native applications acting as governed capability bridges rather than
  independent brains. **This changes nothing about the current system or the current plan.** It is
  TARGET architecture: no multi-tenancy, cloud infrastructure, device agent, capability protocol or
  authentication exists, and the four-state reading of this document is unchanged —
  **CURRENT** is the single-user local prototype described above; **NEAR-TERM** is the first
  controlled real-world test (`docs/FIRST_REAL_WORLD_TEST.md`); **TARGET** is that decision;
  **FUTURE** is `ROADMAP.md`'s "What we will not do yet". The "Next 3 Moves" below are unaffected:
  real-world use of slice 1 is still next, per `docs/TILT.md`.

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
| Stage 1 — Console/UI integration (sub-stages S1.0–S1.6) | ✅ 2026-08-05 *(this table corrected 2026-08-08 — it previously listed Stage 1 as "not started" below, stale since S1.1–S1.6 landed 2026-08-01–2026-08-05)* | `ROADMAP.md` Stage 1; `docs/STAGE_1_OVERVIEW.md` |

**Not started**

- **Stage 5 — Developing Agency (competency, training, learning, then initiative)** *(renamed
  2026-08-08 from "Initiative engine" — see `ROADMAP.md`'s restructured Stage 5 section)*. Only its
  prerequisite **S5.0** has landed (see P3 below). **S5.1 (competency architecture) has not begun.**
  No Stage 5 feature code exists.
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

> **Identifier note (added 2026-08-20):** the `P0`–`P5` headings in this backlog are **priority
> tiers** and are unrelated to the Post-Test #1 **product gates P1–P9** established by the register
> approved 2026-08-20 (summarised in `ROADMAP.md`'s "Post-Test #1 readiness bands"). A bare `P3`
> here means the priority tier, never the product gate. Qualify post-test references explicitly.

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
     dependency — reproduced and confirmed on a clean venv).
     **Superseded 2026-08-17:** that ceiling is no longer what the project pins. `requirements.txt`
     and `requirements.in` now carry `fastapi>=0.134,<0.141`, raised for CVE-2026-54283 — the floor
     was lifted because `fastapi<0.133` cannot resolve any patched Starlette. The `0.104,<0.121`
     text above is preserved as the historical record of the 2026-07-20 fix; **the live constraint
     is whatever `requirements.txt` says**, and it is not this. Also re-encoded
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
     to it, not standing as a second, independent pipeline. **Closed 2026-08-17** (change landed
     `8d87258`, 2026-08-16): `daemon.py` collects the narrator's episodic material first and passes
     it in as `episodic_evidence`, and `tests/test_reflection_ownership.py` /
     `tests/test_reflection_narrative_integration.py` verify `ReflectionGenerator` as the sole
     point of final composition. The sentence above beginning "The implementation remains
     incomplete" described the state before that commit and is retained only for traceability.
     A second, separate defect — that no *model* had ever composed a reflection, because
     `daemon.py` pinned `backend="stub"` and `ReflectionGenerator` could not be constructed
     headless — was repaired 2026-08-17 (`tests/test_reflection_model_path.py`). Closing the
     remaining S5.4 loop is separately-authorised future work, not
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

### P3 — Developing Agency: competency/training/learning, then initiative engine

**Restructured 2026-08-08** (New Direction reconciliation — see `DECISIONS.md`'s "Stage 5
restructured around competency and training before live initiative" entry and `ROADMAP.md`'s
Stage 5 section, the canonical source for this workstream's sub-stage detail going forward). This
backlog item previously covered only the initiative engine (scheduled check-ins/workflows). It now
also covers the generic competency/training/learning architecture (`ROADMAP.md` Stage 5's
S5.1–S5.4) that must exist first, per `CONSTITUTION.md`'s "One Developing Digital Individual"
section — the Executive (`Planner.decide()`) has no machinery to retrieve or apply competencies
today, so scheduling proactive behaviour is premature ahead of the Executive having anything
competent to be proactive about. The pre-existing initiative-engine scope (item 12 below) is
preserved unchanged in substance as S5.5–S5.7, resequenced after the new S5.1–S5.4 work.

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

**S5.1 onwards — NOT STARTED (as of 2026-08-08).** No Stage 5 feature code exists: no competency
data model, no training/knowledge-acquisition path, no Executive competency reasoning, no
experience→learning loop, no typed cadence, no proactive consent/mute, no quiet-hours defer, no
dry-run, no rationale logging, no `allow_proactive` governance category. S5.0 is a *prerequisite*
that landed early; it is not Stage 5 in progress. Beginning S5.1 requires separate explicit
approval. **Under the restructured numbering (2026-08-08), S5.1 is competency architecture, not
the initiative engine** — see `ROADMAP.md`'s Stage 5 section for the current S5.1–S5.7 sequence.

11a. **Competency architecture, training, and the experience→learning loop** (`ROADMAP.md` Stage 5
   S5.1–S5.4, added 2026-08-08) — the generic competency data/contract model, training/knowledge
   acquisition with provenance and consent, Executive competency reasoning (extending
   `Planner.decide()`), and the governed Experience → Reflection → candidate learning →
   consolidation loop. Worked through Residential Estate Management as the first proving ground —
   see `ROADMAP.md`'s "Estate Management as architecture acceptance test." **Not started; not
   authorised by this documentation pass.**

12. **Scheduler-driven check-ins + workflows** (`ROADMAP.md` Stage 5 S5.5–S5.7, preserved unchanged
   in substance, resequenced after item 11a above) *(sequence: safety scaffolding before live
   proactivity: typed cadence → default-OFF consent + functional mute → quiet-hours
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

> **Updated 2026-08-12** (Usable POC / time-to-real-use prioritisation — see `docs/TILT.md` and
> `DECISIONS.md`'s "Usable POC / time-to-real-use prioritisation" entry): the list below is
> rewritten around the new priority. Items 1–5 of the prior (2026-08-08) list are now complete —
> Phase B (all of B0–B9), Stage 1 (all of S1.0–S1.6), and Stage 5 S5.1–S5.3 all shipped between
> 2026-08-01 and 2026-08-12; see `ROADMAP.md` for each stage's exit evidence. The next move is no
> longer "continue down the pre-existing Stage 5 sequence" — it is the first Usable POC vertical
> slice, per `docs/TILT.md`.
>
> **Previously (2026-08-08, New Direction reconciliation):** step 4 inserted the competency/
> training/learning architecture between Stage 1 and Stage 5's live-proactivity work — see
> `ROADMAP.md`'s restructured Stage 5 section and `DECISIONS.md`'s "Stage 5 restructured around
> competency and training before live initiative" entry. Superseded above, not reversed: that
> sequencing is why S5.1–S5.3 were the right things to build before this pivot, and S5.4–S5.7
> remain real, deferred (not abandoned) work — see `docs/TILT.md`'s "What is deferred" section.

**The actual next moves, as of 2026-08-12 (each step below requires its own separate, explicit
approval before work begins — this list records sequencing, not authorisation):**

1. ✅ **Done.** Documentation reconciliation, the hybrid local-first deployment decision, Phase B
   (persistence-ownership stabilisation, B0–B9), and Stage 1 (the consumer web governance shell,
   S1.0–S1.6: parking-brake access, consent/approval inbox, notification/mute controls,
   awaiting-response queue, audit/provenance visibility, host-device onboarding). See `ROADMAP.md`
   for exit evidence and merge commits.
2. ✅ **Done.** Stage 5 S5.1–S5.3: the competency data/contract model, training/knowledge
   acquisition with provenance/consent, and Executive competency reasoning (relevance-gated
   retrieval and selection, wired into chat via `run_chat_through_runtime_contract`). See
   `ROADMAP.md`'s Stage 5 section for exit evidence and merge commits.
3. ✅ **Done 2026-08-14** (`2d443a9`). **The first Usable POC vertical slice: Personal Memory
   Capture and Recall.** Planning note approved in `4de2962`, implementation approved separately
   and explicitly, then delivered. Extends the existing consent-gated write path and the existing
   competency-retrieval seam to ordinary conversational facts, plus one real notification delivery
   channel (a provider-agnostic outbound webhook). See `ROADMAP.md`'s "Usable POC" section for the
   completion record, `docs/POC_SLICE_1_MEMORY_CAPTURE_RECALL.md` for the as-implemented detail and
   known limitations, and `DECISIONS.md`'s "Usable POC slice 1 implementation approved" entry.
   **This is the first slice of the Usable POC, not its full boundary** — see item 5 below.
4. ✅ **Done 2026-08-19/20. Real-World Test #1 was run.** Slice 1 went into real-world use, and
   this step is complete. **Evidence freeze: commit `854a8da7fd107db33a933c4bdb01bf3fd7eb69bd`**
   (the merge commit for PR #58) — **not** `main`, which was 25 commits behind at
   `d0c202f7b39f9244417f1954629f64f68dfbb341` and did not contain the tested implementation. The
   procedure is `docs/FIRST_REAL_WORLD_TEST.md`; the evidence location, provenance record and
   absence inventory are `docs/evidence/test-1/`.
   **Outcome:** 38 historical evidence items, adjudicated in **Post-Test #1 Decision Register
   v2.2**, approved by Taylor 2026-08-20 — establishing decisions D1–D15, safety gates S1–S11,
   product gates P1–P9, readiness Bands 0/A/B/C/D and implementation Tracks 1–7. The decisions are
   in `DECISIONS.md`; the bands and gate summaries are in `ROADMAP.md`'s "Post-Test #1 readiness
   bands"; the approval is in the ledger below.
   **Headline finding, recorded because it governs what comes next:** the tester's own assessment
   was that the current functionality was practically useless and the qualitative burden sat below
   break-even — a burden finding, not a feature gap. That is what decision D1 and product gate P2
   now exist to answer.

4a. **← NEXT: Taylor reviews the Post-Test #1 documentation propagation under the Approval Gate.**
   The documentation-only pass carrying the approved register into the canonical docs is listed
   under "Pending (awaiting user approval)" below. **No implementation work package may be proposed
   until that review completes** — that is the register's own approval sequence, not an extra step.
   After it completes, work is proposed slice by slice as usual, sequenced by the readiness bands:
   Band A before any further unattended testing, Band B before real ambient sensing, Band C before
   full Real-World Test #2, with Band 0 attended localhost checkpoints and Band D safe parallel
   prototyping available in the meantime. **Listing a band is not authorisation to start work in
   it.**
5. **Subsequent Usable POC vertical slices** (scope deliberately not fixed yet — see
   `docs/TILT.md`'s "Direction for later slices"): progressing toward proactive surfacing of
   something Bartholomew noticed, and at least one genuine governed action with a visible
   real-world result. Draws on the real, already-considered material in `ROADMAP.md`'s S5.4
   (experience → learning/consolidation loop) and S5.5–S5.7 (initiative safety scaffolding,
   dry-run, controlled live initiative) — deferred, not discarded, and to be scoped from slice 1's
   real feedback rather than designed ahead of it.
6. **Everything else previously sequenced here** — the pre-2026-08-08 Stage 5 initiative-engine
   work (now S5.5–S5.7) and the reflection-ownership implementation gap S5.4 was to close — remains
   real and remains on the roadmap. It follows the Usable POC slices above rather than preceding
   them; see `docs/TILT.md`'s "What is deferred" section for the full list and reasons.

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
- 2026-08-20 — **Post-Test #1 documentation propagation** (documentation-only; no production code,
  tests, schemas, migrations, runtime configuration, workflows or CI touched) — the canonical-doc
  changes that carry the approved Post-Test #1 Decision Register v2.2 into this repository:
  `DECISIONS.md` (sixteen new entries), `RISKS.md` (hydration entry amended per D4, scope-count
  correction, three new watchlist entries), `ROADMAP.md` (new "Post-Test #1 readiness bands"
  section), `docs/TILT.md` (D7 reconciliation), `docs/SAFETY_PARKING_BRAKE.md` (five→six scopes,
  with caveats), `COGNITIVE_RUNTIME.md`, `INTERFACES.md`, `CHECKLISTS.md`,
  `docs/FIRST_REAL_WORLD_TEST.md`, `docs/S1_4_AWAITING_RESPONSE_DESIGN.md`, this document, and the
  new `docs/evidence/test-1/` evidence location — **awaiting Taylor's review under the Approval
  Gate.** No implementation work package may be proposed until that review completes.
  **Not yet committed.**
- 2026-07-27 — Planning-document reconciliation (documentation-only; no production code, tests,
  dependencies, workflows, configuration or schema touched) — **not yet committed**

### Approval Ledger
Record of approved changes with commit tracking (most recent 5):

- 2026-08-20 — **Post-Test #1 Decision Register v2.2 approved as the authoritative Post-Test #1
  Decision Register** — Approved by Taylor — Commit: **not yet committed** (approval of an
  interpretation artifact, not of a diff; the artifact is preserved unmodified at
  `docs/evidence/test-1/interpretation/BARTHOLOMEW_POST_TEST_1_DECISION_REGISTER_v2_2_FINAL_APPROVAL_CANDIDATE.md`,
  SHA-256 `ebc282bbdc19123310a070a6cd41a27d2c0bbd4cd7e0b323d5d0245ca24aa798`).
  **What the approval establishes as project authority:** decisions **D1–D15**; safety gates
  **S1–S11**; product gates **P1–P9**; readiness **Bands 0/A/B/C/D**; implementation **Tracks 1–7**;
  the register's §5 evidence register and §6 finding→band map.
  **Independent correction review:** confirmed that v2.2 resolved the issues raised against v2.1
  without introducing new factual, arithmetic, provenance, classification, cross-reference or
  internal-consistency errors, and without substantively changing D1–D15.
  **Evidence-access limitation, still in force:** restored Test #1 case IDs and timestamps were
  verified for **internal consistency only**, not independently against the raw Test #1 artifacts.
  **The register's own front matter still reads "NOT YET APPROVED" and its filename still carries
  `FINAL_APPROVAL_CANDIDATE`; both are deliberately unchanged.** The artifact is preserved
  unmodified and this ledger entry — not an edit to the artifact — is the approval record. This
  repository has no established approved-artifact renaming process that would require otherwise.
  **The approval authorises documentation propagation only.** It does not authorise implementation
  of any D1–D15 decision or any safety/product gate.

- 2026-08-14 — AI-assisted development provenance & IP governance (`DECISIONS.md` new decision
  entry; `RISKS.md` three new tech-debt watchlist entries; `CHECKLISTS.md` one new PR-checklist
  line; documentation-only, no production code/tests/dependencies/workflows/configuration/schema
  touched) — Approved by project owner — Commit: `5a5e08eeb49ecc4f88fd7231dde4e37500b54bff`

> Populated 2026-07-27. This ledger read "*No entries yet*" (dated 2026-01-19) while five
> approved changes had in fact been merged to `main` — the ledger was unused, not empty by
> fact. Each entry below cites a real merge commit verified with `git log`; nothing here is
> recorded as committed without one.

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
