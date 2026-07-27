# ROADMAP

> Milestones and stage gates with explicit exit criteria.
>
> **Last updated:** 2026-07-27 (planning-document reconciliation: Phase A recorded as merged;
> Phase B recorded as proposed-not-approved; S5.1 explicitly marked not started; the Stage 0.5
> "nothing catches undeclared imports" process gap closed. The "Last updated" line had read
> 2026-01-19 while stage sections were edited through July.)

## Engineering workstreams (cross-cutting; not stage gates)

Stage gates describe product capability. These describe the engineering foundation underneath
them, and are tracked here so a stage label is never read as a claim about verification quality.

### Phase A — Truthful cross-platform verification ✅ (Complete, merged 2026-07-27)

**Goal:** make verification of this repository automatic, cross-platform and trustworthy before
Stage 5 resumes — trusting no roadmap label, prior summary, comment or log unless confirmed
against current code and executable tests.

**Merged:** PR #26, merge commit **`8b96319c4059d9dfada2579ca5f6da22b34e1f31`**.
All 9 GitHub checks green on the merged head `e923fb9` (Quality; Tests + coverage on Ubuntu 3.10
and 3.11; Critical integration + lifecycle on 3.10 and 3.11; Windows 3.11; `lint-test` 3.10 and
3.11; `smoke`) — the Windows job was the first in this repository's history.

**Delivered:**
- `.github/workflows/ci.yml` — auto-run on every pull request, every push to `main`, and manual
  dispatch; four jobs across Ubuntu (3.10, 3.11) and Windows (3.11). See `CI.md` for the matrix.
- Packaging/dependency contract (`tests/smoke/test_packaging_contract.py`, 9 tests) that fails CI
  on an undeclared third-party runtime import, a first-party module that will not import, or a
  declared console script that will not run `--help`.
- Clean-start lifecycle characterisation (`tests/test_clean_start_lifecycle.py`, 6 tests),
  including the database-handle-release property that fails first on Windows.
- Coverage widened from one first-party package to all three, with the project's **pre-existing**
  declared 70% gate enforced (measured baseline 73.5%; the gate was not lowered).
- Two live production defects fixed, both found by disbelieving prior status: the sensitive-memory
  consent path (`asyncio.run()` called inside `async def`, always falling through to an
  undeclared `import nest_asyncio`) and the `bartholomew` console script (broken at import time).

**Deliberately not done (deferred, recorded not fixed):** persistence restructuring; the
intermittent concurrent-process WAL failure; findings F9–F11. See `RISKS.md`.

### Phase B — Persistence ownership stabilisation 📋 (Proposed; NOT approved for implementation)

**Status as of 2026-07-27:** no design, no branch, no code. This is the *proposed* next
engineering workstream and requires explicit approval before any implementation begins.

**Problem statement (characterised by Phase A, not fixed by it):** one SQLite file has no single
owner. `bartholomew/kernel/memory_store.py` uses `aiosqlite`;
`bartholomew/kernel/scheduler/persistence.py` uses synchronous `sqlite3` behind `SchedulerStore`'s
dedicated worker thread; `bartholomew/kernel/persona_pack.py` and `narrator.py` use synchronous
`sqlite3` called directly from async methods; and `bartholomew/kernel/db_ctx.py` and
`bartholomew_api_bridge_v0_1/services/api/db_ctx.py` are near-duplicate context modules with the
same WAL/checkpoint pattern, the latter still checkpointing per call in `liveness.py`/`db.py`.

**Evidence preserved for it:** `tests/test_sqlite_wal_concurrent_processes.py::
test_wal_cleanup_concurrent_processes` failed once under full-suite load and passed 3/3 in
isolation immediately afterwards; it was deliberately **not** retried, quarantined, re-marked or
given a longer timeout. The unresolved "why did a `TRUNCATE` checkpoint outlast its own
busy-timeout" question and its temporary DEBUG instrumentation are the likely same root cause.
See `RISKS.md`'s tech-debt watchlist.

---

## Guiding rule

**Ship by gates, not by vibes.** Each gate has:
- explicit scope
- acceptance criteria
- verification commands
- rollback notes

## Stage gates

### Stage 0 — Kernel alive, stable, dreaming ✅ (Complete)

**Goal:** A running kernel that can persist state, generate nudges, and produce daily/weekly reflections with governance constraints.

**Evidence:** `STAGE_0_COMPLETION.md`, `tests/test_stage0_alive.py`, exports under `exports/`.

**Exit criteria:**
- Kernel lifecycle start/stop cleanly.
- Water logging works.
- Nudge pipeline persists and respects cadence/quiet-hours.
- Daily + weekly reflection generation persists + exports.

**Verify:**
```bash
pytest -q -m smoke || pytest -q tests/test_stage0_alive.py
```

---

### Stage 0.5 — Packaging & Architecture Fixes ✅ (Complete 2026-07-20)

> **Source:** Cline audit 2026-01-22 verifying ChatGPT repo analysis

**Goal:** Ensure the package is installable, dependencies are canonical, and kernel runs headless without blocking on stdin.

**Scope:**
- Add missing `bartholomew/__init__.py` for package discoverability
- Consolidate dependencies in `pyproject.toml` (add `numpy`, `cryptography`, `typer`, `rich`)
- Fix malformed `safety.audit` rule in `memory_rules.yaml` to use `match:`/`metadata:` schema
- Refactor `input()` out of `bartholomew/kernel/memory/privacy_guard.py`

**Exit criteria:**
- `pip install -e .` succeeds; `python -c "import bartholomew"` works
- `pyproject.toml` contains all runtime deps; `requirements.txt` mirrors or is deprecated
- All memory_rules.yaml rules use consistent `match:`/`metadata:` schema
- No `input()` calls in kernel code; consent handled via event bus

**Verify:**
```bash
pip install -e .
python -c "import bartholomew"
grep -r "input(" bartholomew/kernel/ | grep -v test  # should be empty
pytest -q tests/test_memory_rules.py  # all rules parse
```

**Rollback:**
```bash
git checkout -- bartholomew/__init__.py pyproject.toml bartholomew/config/memory_rules.yaml bartholomew/kernel/memory/privacy_guard.py
```

**Evidence:** all four exit criteria verified against a clean venv on 2026-07-20 (see
`MASTER_PLAN.md` P0 items 0–3 for per-item notes). Two gaps found during verification,
carried forward rather than fixed here:
- The dependency audit that scoped this stage missed `jsonschema`, `requests`, and
  `pydantic[email]` — also added, but the underlying process gap (nothing catches
  undeclared imports until a fresh install fails) was unaddressed. **Closed by Phase A
  (2026-07-27, `8b96319`):** `tests/smoke/test_packaging_contract.py` now fails CI on any
  undeclared third-party runtime import, and `ci.yml`'s `quality` job installs from declared
  dependencies only, so a missing declaration fails a pull request rather than a user's first
  clean install. The gap had in fact bitten twice more in the interim — an undeclared
  `nest_asyncio` on the sensitive-memory write path and an undeclared `pytest-cov` that made
  the old `tests.yml` unable to pass at all.
- `identity_interpreter/adapters/consent_terminal.py` has the same blocking-`input()`
  shape as the fixed `privacy_guard.py`; out of this stage's stated scope
  (`bartholomew/kernel/` only) but should get the same fix.
- No automated "all `memory_rules.yaml` rules parse" test was added (the stated `pytest -q
  tests/test_memory_rules.py` verify command references a test file that doesn't exist);
  verified instead with a one-off script.

---

### Stage 1 — Console/UI integration 📋 (Deferred product slice; NOT STARTED)

**Status:** Stage 1 is a deferred console/UI product slice. It has not started and was never a
prerequisite for Stages 2–4.5. Its historical numbering is retained deliberately — the later
stages were sequenced by architectural dependency, not by stage number, so a lower number here
does not imply it blocked anything.

**Goal:** A minimal user-facing console or UI on top of the API bridge that can:
- display current state (nudges, last reflections)
- acknowledge/dismiss nudges
- trigger reflections (dev/testing)

**Constraints:**
- Must honor parking brake and consent gates.
- Must not widen tool surface without governance review.

**Exit criteria:**
- API endpoints stable and documented.
- Basic UI/console can safely perform: list/ack/dismiss nudges; fetch latest reflections.
- No “Act” capability beyond these actions.

**Verify:**
```bash
pytest -q tests/test_orchestration_integration.py
pytest -q bartholomew_api_bridge_v0_1/tests/test_sqlite_wal_api.py
# optional smoke
bash bartholomew_api_bridge_v0_1/scripts/curl_smoke.sh
```

---

### Stage 2 — Governance hardening + memory stack (Phases 2A–2D)

**Goal:** Redaction, encryption, summarization, embeddings, consent gates, retention, and retrieval modes are reliable and testable.

**Sub-gates:**
- **2A** Redaction correctness
- **2B** Encryption envelope round-trip + key handling
- **2C** Summarization (fallbacks, truncation, sensitive handling)
- **2D** Embeddings lifecycle + vector store + retrieval integration
- **2E** FTS + hybrid retrieval (with graceful fallbacks)
- **2F** Chunking (ingest + retrieval + snippet assembly)

**Exit criteria (minimum):**
- P0 failing tests identified in `docs/STATUS_2025-12-29.md` are green on Linux CI.
- Explicit retriever modes behave correctly (`vector`, `fts`, `hybrid`).
- Consent gates applied by default at the lowest layer.
- Metrics registry is idempotent.

**Verify (Linux CI baseline):**
```bash
ruff check .
black --check .
pytest -q
```

---

### Stage 3 — Unified Persona Core (Experience Kernel) — largely done; gaps closed 2026-07-20

**Correction (2026-07-20):** this section previously described Stage 3 as future/not-started. It
was stale — `bartholomew/kernel/experience_kernel.py`, `narrator.py`, `global_workspace.py`,
`working_memory.py`, `persona_pack.py` are already implemented and wired into `daemon.py`, with
~320 existing tests across 7 test files (all passing). See `MASTER_PLAN.md`'s "Experience Kernel
MVP: bug fix + privacy gap" section for what was actually found and fixed this round: a
silently-swallowed `AttributeError` that disabled the tick loop's affect decay / persona
auto-activation / planner calls since Stage 3 landed, and a privacy gap (this section's own
"must preserve consent gates, privacy redaction" constraint below wasn't actually implemented for
this subsystem — episodic entries and self-model snapshots bypassed `ConsentGate`/
`memory_rules.py`/`redaction_engine.py` entirely).

**Goal:** Bartholomew behaves like one continuous “self” with an Experience Kernel (self-model + narrator) and configurable persona packs, without expanding the action surface.

**Constraints:**
- No new real-world “Act” powers.
- Must preserve consent gates, privacy redaction/encryption, and auditability.

**Exit criteria:**
- Experience Kernel MVP wired into the loop (self snapshot + narrator reflections). ✅
- Persona packs switchable via config/UI and recorded in audit logs. ✅ (`persona_pack.py`,
  `PersonaPackManager`; not independently re-verified against this exact criterion this round)
- New unit + integration tests for kernel/persona. ✅ (already exist, see correction above)
- A dedicated "scenario replay" test — ✅ added 2026-07-21 (`tests/test_scenario_replay.py`;
  see `MASTER_PLAN.md` item 11.9). Found and fixed a real restart-persistence bug in the
  process: `ExperienceKernel` state (goals/affect/attention/drives) was never actually
  restored on daemon restart despite a log line claiming it was.
- The two non-unified reflection pipelines (`daemon.py`'s `ReflectionGenerator` vs.
  `narrator.py`'s episodic-narrative generators) — ✅ reconciled 2026-07-21, additively:
  `daemon.py`'s daily/weekly reflection generation now appends `narrator.py`'s real
  episodic-narrative output alongside `ReflectionGenerator`'s own content, rather than either
  replacing the other. See `MASTER_PLAN.md` item 11.8.

**Verify:**
```bash
pytest -q tests/test_experience_kernel.py
pytest -q tests/test_persona_pack.py   # this doc previously cited a nonexistent
                                        # tests/test_persona_switching.py -- corrected 2026-07-20
```

---

### Stage 4 — Modularity: Skill registry + starter skills — done (2026-07-21)

**Goal:** Standardize skills as installable modules with explicit manifests, permissions, and test expectations.

**Exit criteria:**
- Skill manifest schema defined + enforced.
- Registry can list/load skills; permission model applied.
- Starter skills working end-to-end: tasks + notify + calendar draft.

The registry, manifests, and starter skills were already fully built and
unit-tested, but disconnected from the live daemon (`Planner` never called
into `SkillRegistry`, `KernelDaemon` never constructed one). Wired up, plus
a parking-brake check, "ask"-consent resolution for `calendar_draft`, and
a dedicated `skill_action_audit` trail -- see MASTER_PLAN.md's "P2
investigation & wiring" write-up for details.

**Verify:**
```bash
pytest -q tests/test_skill_registry.py
pytest -q tests/test_end_to_end_tasks_and_audit.py
```

---

### Stage 4.5 — Runtime Convergence (architectural prerequisite) ✅ (Complete 2026-07-24)

**Goal:** Close the gap a grounded architectural audit found (2026-07-21): the project
effectively has "two brains" (`bartholomew/kernel` and `identity_interpreter/`) with four
duplicated concepts (model routing, persona, permission gates, kill-switch), `Identity.yaml`
governing only the chat path, and a fully-built Experience Kernel/Narrator/Working Memory
stack ("Living Device" continuity) that chat never reaches. See MASTER_PLAN.md's "P2.5 —
Runtime Convergence" for the full narrative, governing principles (Principle Zero, Principle
One — Uniform Cognition, the Architectural Invariant), and the Runtime Contract.

**Exit criteria (the Runtime Convergence Exit Gate — all seven must be "yes"):**
- Can every input source create an Observation?
- Does every proposed action pass through the Executive?
- Does every execution pass through the same Governance path?
- Does every completed action produce a Reflection?
- Does every Reflection update Memory?
- Does every conversation see the Experience Kernel?
- Does every interface expose the same personality?

**Scope:**
- One authoritative owner per architectural concept; the four duplicate pairs marked
  deprecated (not deleted) and routed through the winner.
- Identity Context -> Executive -> Policy Decision (Identity stays declarative; the Executive
  constructs the executable decision).
- The Runtime Contract's pipeline (Observation -> Interpretation -> Executive -> Governance ->
  Capability -> Execution -> Reflection -> Memory) becomes a real code seam for chat +
  skill-execution.
- Chat wired into the Experience Kernel.
- `COGNITIVE_RUNTIME.md` authored as the canonical "how does Bartholomew think" document. —
  ✅ done 2026-07-21. Its own "Exit Gate status" table is the honest, continuously-updated
  scorecard. As of item 11.21 (2026-07-24), **all five live surfaces** — chat, skill execution,
  scheduler drives, *and* voice/sight — construct an Observation/CandidateAction that genuinely
  drives the Governance decision (not just constructed and discarded — proven per-surface, and for
  voice/sight by `tests/test_voice_sight_runtime_contract_seam.py` including deliberate
  gate-neutralisation non-vacuity controls) and pass through the shared Governance path. Voice/
  sight additionally require a fail-closed device consent gate; their capture/stream capability
  stays an inert Stage 6 placeholder reachable only through the governed seam. See
  `COGNITIVE_RUNTIME.md`'s Exit Gate table for the live, per-question status rather than restating
  it here.

**Status (2026-07-24): complete.** All seven Exit Gate questions are satisfied within Stage 4.5's
scope. Questions **#1–#6 are "yes"** for every surface that exists today (item 11.21 closed the
last current-production governance gap, voice/sight). Question **#7 (personality uniformity) is
"yes" within Stage 4.5's scope**: every personality-bearing interface (chat, CLI `explain`,
`chat.py`) sources persona from the single authority (`PersonaPackManager`); the `Identity.yaml`
`traits` split is deliberate-by-design, not a convergence gap. Q7's only residual — voice/sight
consulting persona — was **formally reclassified to Stage 6** (item 11.22, 2026-07-24), because a
surface producing no persona-bearing output cannot expose a personality until Stage 6 builds that
output; it is a Stage 6 dependency, not a Stage 4.5 deliverable left undone. No official exit
criterion is left partial. Real voice/sight functionality (capture, streaming, transcription,
sessions, persona output) remains Stage 6.

**Note:** Stage 5 (below) was recommended to wait until this stage's exit gate is fully green.
With all seven questions now satisfied within scope, that prerequisite is met — though pausing/
resuming P3 (Stage 5) still requires separate, explicit user sign-off, which this completion does
not itself grant.

---

### Stage 5 — Initiative engine (scheduled check-ins + workflows) 📋 NOT STARTED

**Status as of 2026-07-27:** **S5.1 has not begun.** Only the prerequisite S5.0 has landed. No
Stage 5 feature code exists — no typed cadence, no proactive consent or mute, no quiet-hours
defer, no dry-run mode, no structured rationale logging, and no `allow_proactive` governance
category. Resuming Stage 5 requires separate explicit approval; S5.0 landing early does not
constitute Stage 5 being in progress.

**Goal:** Proactive suggestions and check-ins that are safe, useful, and not naggy.

**Prerequisite — S5.0 (closes issue #24):** deterministic scheduler-schema readiness at startup —
`KernelDaemon.start()` ensures the scheduler tables synchronously (fail-closed) before returning,
so Stage 5's proactive drives and their user-visible state are not built on nondeterministic
scheduler initialization. ✅ merged 2026-07-25, PR #25, merge commit `3496cfb`; issue #24 is
confirmed closed. Proven by `tests/test_scheduler_startup_readiness.py` (10 tests) on the
3.10 + 3.11 matrix. See MASTER_PLAN.md's P3 "S5.0" note and DECISIONS.md.

**Sequencing (locked):** safety scaffolding lands before any live proactivity — typed cadence
(interval / daily / weekly wall-clock) → **default-OFF** consent + **functional mute** →
quiet-hours *defer* (not suppress, with coalescing/expiry) → dry-run → structured rationale
logging → *then* live check-in / weekly-review / next-best-action drives under a default-deny
`allow_proactive` governance category (suggestion-only, brake-blocked, excluded from `tool_use`,
no self-maintenance exemption). Default-off consent and working mute are prerequisites for live
delivery, not later enhancements.

**Exit criteria:**
- Scheduler runs check-ins (morning/evening) and weekly review in dry-run + live.
- Quiet-hours respected; parking brake scope coverage tested.
- Suggestions logged with rationale; user can mute/adjust cadence.

**Verify:**
```bash
pytest -q tests/test_scheduler_checkins.py
```

---

### Stage 6 — Distributed being (cross-device) + voice adapters

**Goal:** Same Bartholomew across devices with minimal, secure auth and optional voice.

**Exit criteria:**
- Token auth; cross-device client shows same timeline/state.
- Voice endpoints degrade gracefully when binaries missing.

**Carried-forward requirements from item 11.21 (voice/sight governance seam):**
- Real capture/streaming/transcription/computer-vision/device-driver work slots *into* the
  existing governed seams (`run_voice_/run_sight_through_runtime_contract()`), which already
  construct the Observation/CandidateAction and run brake + Identity Policy + fail-closed device
  consent. Do not add a second, ungoverned capture path — the inert `_perform_stream`/
  `_perform_capture` placeholders are the slot.
- Governance approval at the seam authorizes a **single start attempt** only. Continuous sessions,
  consent renewal, and revocation are new mechanisms to design here — not an extension of the
  single-start grant.
- **Safety invariant:** safely stopping or tearing down an active capture session must NEVER
  depend on obtaining permission to *continue* capturing. Teardown is not a governed "start" and
  must not be gated as one (a stuck consent/policy path must not be able to trap the device "on").
- **Personality uniformity for voice/sight (reclassified here from Stage 4.5 Exit Gate question
  #7, item 11.22, 2026-07-24):** once voice/sight produce persona-bearing output, that output must
  source persona from the single authority (`PersonaPackManager`'s active pack), the same way the
  text interfaces already do — so Bartholomew presents "one personality, not one per interface"
  across voice/sight too. This could not be done in Stage 4.5: a surface with no persona-bearing
  output has no personality to converge. Satisfying it is part of building real voice/sight
  functionality here; it closes the last residual of Exit Gate question #7.

**Verify:**
```bash
pytest -q tests/test_cross_device_auth.py
pytest -q tests/test_voice_adapters.py
```

---

### Stage 7 — Embodiments (future)

**Goal:** Car mode, gaming overlays, smart home control — strictly gated, privacy-reviewed, and incrementally enabled.

**Exit criteria:**
- Each embodiment has: interface spec, threat model, consent model, and replay tests.

---

## Echo Integration Gates (Brainstorm-Derived, Future Exploration)

> **Source:** 45 features extracted from 81 design conversations
> **Status:** Conceptual roadmap for companion AI agent with multi-domain capabilities
> **Prerequisites:** Bartholomew Stages 0-3 complete; governance + consent framework mature

### Echo Gate 0 — Foundation (5 features)

**Goal:** Establish core agent architecture with local-first execution and security baseline.

**Scope:**
- LangGraph kernel implementing full perceive→retrieve→decide→act→learn loop
- Episodic (SQLite) + semantic (Chroma) memory with RAG
- YAML-based permissions system (ask/auto/never)
- Tauri + Python architecture for desktop-first offline operation
- Code signing + runtime attestation for supply chain integrity

**Exit criteria:**
- Agent kernel can complete full loop with capped steps/timeouts
- Memory stores persist and retrieve with consent gates
- All binaries signed; verification on startup
- Permissions enforced for all actions

**Verify:**
```bash
pytest -q tests/test_echo_kernel_loop.py
pytest -q tests/test_echo_permissions.py
```

---

### Echo Gate 1 — Core Capabilities (16 features)

**Goal:** Add gaming mentor, device identity, and organic immune system (EOIS) foundation.

**Scope:**
- Gaming: session detection, build guidance, inventory coaching
- Permissions-aware memory with context metadata
- Modular skill manifests (hot-load/unload)
- Context-aware modes (In-Game, Life, Work, Focus, Car)
- Device Identity (EDID) with TPM/Secure Enclave binding
- Mutual TLS pairing, MFA gates for sensitive operations
- Tamper-evident logging (ed25519 signatures)
- Device bridge services (Rust) for USB/Bluetooth/mDNS
- EOIS three-layer defense (Border/Detection/Containment)

**Exit criteria:**
- Gaming mentor provides build advice without external wikis
- Each device has cryptographic identity; pairing is secure
- All privileged actions logged with signatures
- EOIS detects and contains basic threats (signature + baseline)

**Verify:**
```bash
pytest -q tests/test_echo_gaming_mentor.py
pytest -q tests/test_echo_edid_pairing.py
pytest -q tests/test_echo_eois_detection.py
```

---

### Echo Gate 2 — Advanced Integration (21 features)

**Goal:** Cross-device sync, smart home, car mode, and full EOIS with quarantine/forensics.

**Scope:**
- Smart home (Matter/Home Assistant) with scenes
- Android Auto car mode (PTT, <6s replies, safety constraints)
- Real-time cross-device sync (desktop/mobile/car)
- Personality packs (Coach, Gamer Ally, Calm Mentor)
- Human-readable audit trail with rationale
- Shadow + Smoke UI theme (Bartholomew-inspired)
- Local voice I/O (Vosk STT, Piper/Coqui TTS)
- USB PC rescue mode, Smart TV voice remote
- Device troubleshooting KB, trusted device whitelist
- IoT protocol adapters (DLNA, WebOS, Tizen, Chromecast, HDMI-CEC)
- Behavioral baseline detection, canary tokens, honey traps
- Encrypted quarantine, network isolation, restore points
- Forensics export, binary watermarking

**Exit criteria:**
- Tasks sync instantly across all devices
- Car mode enforces safety constraints (<6s, no risky tools)
- Smart home scenes execute with consent gates
- EOIS quarantines threats and exports forensics bundles
- All actions reversible via restore points

**Verify:**
```bash
pytest -q tests/test_echo_cross_device_sync.py
pytest -q tests/test_echo_car_mode_safety.py
pytest -q tests/test_echo_smart_home_consent.py
pytest -q tests/test_echo_eois_quarantine.py
```

---

### Echo Gate 3 — Ecosystem (3 features)

**Goal:** Community extensibility with security vetting and privacy-preserving intelligence.

**Scope:**
- Local skill marketplace (install/remove live, no restart)
- Skill vetting (static analysis + author signatures)
- Opt-in differential privacy telemetry for threat intelligence

**Exit criteria:**
- Community skills installable from UI with vetting
- Marketplace prevents malicious skill distribution
- Telemetry aggregation mathematically preserves privacy

**Verify:**
```bash
pytest -q tests/test_echo_marketplace_vetting.py
pytest -q tests/test_echo_differential_privacy.py
```

---

### Echo Integration Notes

**Constraints:**
- Must inherit all Bartholomew governance (parking brake, consent gates, redaction/encryption)
- No Echo features ship without: threat model, acceptance criteria, tests, rollback plan
- Privacy-first: local execution default; cloud features strictly opt-in

**Feature manifest location:**
- Full JSON: `logs/brainstorm/merged/features_master.json`
- Per-chunk JSONs: `logs/brainstorm/extracted/features_chunk_*.json`
- Verbatim source: `logs/brainstorm/BARTHOLOMEW_BRAINSTORM_NOTES_VERBATIM.md`

**Verification:**
```bash
# View all features
cat logs/brainstorm/merged/features_master.json | python -m json.tool
# Feature count by gate
python -c "import json; from pathlib import Path; f = json.loads(Path('logs/brainstorm/merged/features_master.json').read_text()); g = {}; [g.setdefault(x['suggested_stage_gate'], []).append(x['feature']) for x in f]; [print(f'{k}: {len(v)}') for k, v in sorted(g.items())]"
```

---

## Near-term milestone plan (recommended)

> **Updated:** 2026-07-27. The 2026-01-22 Cline-audit plan that stood here was entirely
> completed or superseded and is kept below as history.

**Current (nothing is in flight; each item needs explicit approval to start):**

1. **Phase B — persistence ownership stabilisation.** Proposed next engineering work; not
   approved. See the workstream section at the top of this document.
2. **Stage 5 / S5.1 — initiative engine.** Paused. The locked sequence (safety scaffolding
   before live proactivity) is recorded in the Stage 5 section above.
3. **Stage 1 — console/UI slice.** A deferred product slice: not started, and never a
   prerequisite for Stages 2–4.5.

**Historical (2026-01-22 Cline audit plan — all items done or superseded):**

1. ~~**Stage 0.5: Packaging & Architecture Fixes**~~ — done 2026-07-20.
2. ~~**Linux CI green for P0 core**~~ — done; superseded by Phase A's automatic, cross-platform CI.
3. ~~**Fix P0 logic bugs**~~ (summarization/encryption/embeddings/retrieval factory/metrics
   idempotency) — done 2026-07-20 (38 → 0 sweep, MASTER_PLAN.md).
4. **Quarantine or parameterize platform-specific tests** — *not done, and deliberately not done
   that way.* Phase A took the opposite approach: rather than quarantining Windows behaviour, it
   added a Windows CI job and a test asserting the exact handle-release property that fails first
   under Windows locking. No quarantine list was ever created (see `ASSUMPTIONS.md` A1).
5. **Stage 1 UI/console slice** — a deferred product slice; not started, and never a
   prerequisite for Stages 2–4.5 (carried forward above).

## What we will not do yet

- Expand automation/tooling surface without governance + test coverage.
- “Act” features without parking-brake, consent, audit, and rollback.
