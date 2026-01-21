# MASTER_PLAN

> **Single Source of Truth (SSOT)** for what Bartholomew is, what matters, where we are, and what we do next.
>
> **Last updated:** 2026-01-19 (Australia/Sydney)

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

- **MASTER_PLAN.md** (this doc)
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

- **Identity.yaml is the governing config** for routing, safety, and persona/behavior constraints.
- **Single SQLite DB** is the shared persistence backbone.
- **Consent + privacy gates** pre-filter retrieval results before they reach callers.
- **Parking brake** provides an emergency/operational kill-switch by scope.

## Stage gates / milestones

**Completed**
- **Stage 0: Kernel alive + stable + dreaming** (see `STAGE_0_COMPLETION.md`).

**In progress (engineering reality)**
- **Governance hardening (Phase 2A–2D)**: redaction, encryption, summarization, embeddings, vector store, FTS + hybrid retrieval.
  - Current snapshot includes known failing tests and platform variability (see `docs/STATUS_2025-12-29.md`).

**Next gate (the next thing that should be “done” end-to-end)**
- **Gate: “Green core on CI Linux”**
  - A minimal, reproducible CI run that is green on Linux for the core governance + memory + retrieval path.
  - Windows-only flakiness is tolerated only if clearly quarantined.

See [ROADMAP.md](ROADMAP.md) for concrete exit criteria.

## Backlog (prioritized, smallest safe slices)

> **Rule:** every item must have acceptance criteria + verification steps before it is started.

### P0 — Packaging & Architecture (Pre-requisites)

> **Source:** Cline audit 2026-01-22 verifying ChatGPT repo analysis

0. **Missing package `__init__.py`**
   - `bartholomew/` directory has no `__init__.py` file.
   - **Acceptance:** `bartholomew/__init__.py` exists; `pip install -e .` succeeds.
   - **Verify:** `python -c "import bartholomew"` works.
   - **DoD:** File created, editable install tested.
   - **Risk if skipped:** Package is not installable; imports fail.

1. **Dependency consolidation to pyproject.toml**
   - `pyproject.toml` missing runtime deps that exist in `requirements.txt`: `numpy`, `cryptography`.
   - `typer`, `rich` used in CLI but not declared.
   - **Acceptance:** `pyproject.toml` is single source of truth for all deps.
   - **Verify:** `pip install .` installs all deps; no manual `requirements.txt` needed.
   - **DoD:** All runtime deps in `[project.dependencies]`; `requirements.txt` mirrors or deprecated.
   - **Risk if skipped:** Dependency drift; CI/CD failures.

2. **Fix malformed memory_rules.yaml rule**
   - The `safety.audit` rule in `always_keep` section lacks `match:`/`metadata:` structure:
     ```yaml
     # WRONG (current):
     - kind: safety.audit
       summarize: false
     # CORRECT:
     - match:
         kind: safety.audit
       metadata:
         summarize: false
     ```
   - **Acceptance:** All rules use consistent `match:`/`metadata:` schema.
   - **Verify:** Unit test confirms all rules are parsed by engine.
   - **DoD:** Rule fixed; test added.
   - **Risk if skipped:** Silent rule failures; safety.audit memories not governed.

3. **Refactor `input()` out of kernel**
   - `bartholomew/kernel/memory/privacy_guard.py` calls `input()` blocking on stdin.
   - **Acceptance:** Kernel emits consent event via event bus; never calls `input()`.
   - **Verify:** `grep -r "input(" bartholomew/kernel/` returns no matches (excluding tests).
   - **DoD:** Consent flow uses event bus; UI/CLI handles user prompt.
   - **Risk if skipped:** Headless/API deployments hang indefinitely.

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

7. **Fix non-environmental failing tests** called out in `docs/STATUS_2025-12-29.md`
   - Summarization truncation fallback.
   - Encryption round-trip for envelopes.
   - Embedding persist lifecycle (`persist_embeddings_for`, `embed_store` defaults).
   - Retrieval factory returning wrong retriever for explicit modes.
   - Metrics registry idempotency.
   - **Acceptance:** P0 failures are green on Linux CI; regressions covered by tests.
   - **Verify:** `pytest -q` on Linux CI; replay the failing cases.

### P1 — Unified Persona Core (Experience Kernel) + personality packs
8. **Experience Kernel MVP** (self-model + narrator)
   - Minimal "self" state, narrator summarization, and reflection hooks wired into the kernel loop.
   - **Acceptance:** kernel can produce a stable "about me" snapshot and a day/week reflection without leaking sensitive memory.
   - **Verify:** `pytest -q tests/test_experience_kernel.py` (to be added) + run a scenario replay.

9. **Persona / Mentor Mode packs (config-driven)**
   - System prompt packs (e.g., Calm Mentor / Coach / Gamer Ally) selectable via config/UI without code edits.
   - **Acceptance:** switching persona changes tone/constraints; logged in audit trail.
   - **Verify:** `pytest -q tests/test_persona_switching.py` + manual API smoke.

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

### P3 — Initiative engine (proactive nudges) and workflows
12. **Scheduler-driven check-ins + workflows**
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

## Echo Integration Roadmap (Brainstorm-Derived Features)

> **Source:** Extracted from 81 design conversations (logs/brainstorm/)
> **Status:** Future exploration—45 features identified across 4 stage gates
> **Note:** These represent a companion AI agent concept with gaming, smart home, and cross-device capabilities

### Echo Foundation (Gate 0) — 5 features
- LangGraph Agent Kernel (perceive → retrieve → decide → act → learn)
- Episodic and Semantic Memory (SQLite + Chroma + RAG)
- Permissions System (YAML-based ask/auto/never policies)
- Tauri + Python Architecture (desktop-first, offline)
- Code Signing and Runtime Attestation

### Echo Core (Gate 1) — 16 features
- Gaming Mode with Session Detection, Build Guidance, Inventory Coaching
- Permissions-Aware Memory, Modular Skill Manifests
- Context-Aware Modes (In-Game, Life, Work, Focus, Car)
- Scheduled Check-ins (APScheduler with mode-aware quieting)
- Device Identity Binding (EDID with TPM/Secure Enclave)
- Mutual TLS Device Pairing, Multi-Factor Authentication Gates
- Tamper-Evident Action Logging (ed25519 signatures)
- Device Bridge Services (Rust/Go for USB/Bluetooth/mDNS)
- Game Session Awareness, Contextual Help Adaptation
- Echo Organic Immune System (EOIS) - three-layer defense

### Echo Advanced (Gate 2) — 21 features
- Smart Home Integration (Matter/Home Assistant with scenes)
- Android Auto Car Mode (PTT, <6s replies, safety constraints)
- Cross-Device Sync (desktop/mobile/car with real-time updates)
- Personality Packs (switchable personas: Coach, Gamer Ally, Calm Mentor)
- Audit Trail (human-readable action logs with rationale)
- Shadow + Smoke UI Theme (futuristic glass/neon aesthetic)
- Voice I/O (Vosk + Piper/Coqui for local STT/TTS)
- USB PC Rescue Mode, Smart TV Voice Remote
- Device Troubleshooting Knowledge Base, Trusted Device List
- IoT Protocol Adapters (DLNA, WebOS, Tizen, Chromecast, HDMI-CEC)
- Offline Voice Processing, Cross-Domain Maturation
- Behavioral Baseline Detection, Canary Tokens
- Encrypted Quarantine Store, Network Isolation Controls
- Restore Points and Rollback, Forensics Incident Export
- Binary Watermarking

### Echo Ecosystem (Gate 3) — 3 features
- Local Skill Marketplace (install/remove without restart)
- Skill Marketplace Vetting (static analysis + signatures)
- Opt-in Differential Privacy Telemetry

**Acceptance for Echo exploration:**
- Features mapped to dependencies, constraints, and evidence
- Each feature has rationale and suggested stage gate
- Full feature JSON available at: `logs/brainstorm/merged/features_master.json`

**Verification:**
```bash
# View extracted features
cat logs/brainstorm/merged/features_master.json | python -m json.tool | head -50
# Check feature breakdown by gate
python -c "import json; from pathlib import Path; features = json.loads(Path('logs/brainstorm/merged/features_master.json').read_text()); gates = {}; [gates.setdefault(f['suggested_stage_gate'], []).append(f['feature']) for f in features]; [print(f'{gate}: {len(feats)} features') for gate, feats in sorted(gates.items())]"
```

---

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

> **Updated:** 2026-01-22 based on Cline audit

1. **Fix P0 packaging issues** (items 0–1)
   - Add `bartholomew/__init__.py`
   - Consolidate deps in `pyproject.toml` (add `numpy`, `cryptography`)
   - **Verify:** `pip install -e . && python -c "import bartholomew"`

2. **Fix malformed memory_rules.yaml + refactor `input()` out of kernel** (items 2–3)
   - Fix `safety.audit` rule to use `match:`/`metadata:` schema
   - Refactor `privacy_guard.py` to emit events instead of blocking on stdin
   - **Verify:** grep for `input(` returns nothing; unit test confirms all rules parsed

3. **CI minimal gates (Linux)** (items 5–7)
   - Make `pytest -q`, `ruff check .`, `black --check .` run in CI
   - Fix non-environmental failing tests from `docs/STATUS_2025-12-29.md`
   - **Verify:** GitHub Actions green on Linux

## Pending Approvals

> **Status:** Tracks proposed changes through approval and commit lifecycle.
>
> **Process:** Agent proposes → User reviews → User approves → Commit is executed → Record in ledger
>
> **Rule:** Never mark anything as committed without a commit hash.

### Pending (awaiting user approval)
- *None* (last updated: 2026-01-19)

### Approval Ledger
Record of approved changes with commit tracking (max 5 entries):
- *No entries yet*

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
