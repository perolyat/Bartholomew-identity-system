# ROADMAP

> Milestones and stage gates with explicit exit criteria.
>
> **Last updated:** 2026-01-19

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
  undeclared imports until a fresh install fails) is unaddressed.
- `identity_interpreter/adapters/consent_terminal.py` has the same blocking-`input()`
  shape as the fixed `privacy_guard.py`; out of this stage's stated scope
  (`bartholomew/kernel/` only) but should get the same fix.
- No automated "all `memory_rules.yaml` rules parse" test was added (the stated `pytest -q
  tests/test_memory_rules.py` verify command references a test file that doesn't exist);
  verified instead with a one-off script.

---

### Stage 1 — Console/UI integration (Next product-facing slice)

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
- **Still open:** a dedicated "scenario replay" test (see `MASTER_PLAN.md`).
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

### Stage 4.5 — Runtime Convergence (architectural prerequisite; recommended, pending sign-off)

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
  ✅ done 2026-07-21. Its own "Exit Gate status" table is the honest scorecard: most of the
  seven questions above are "partial," not "yes" (e.g. chat's Governance stage doesn't yet
  consult the Identity Context → Policy Decision path item 11.2 wired for skills; scheduler
  drives and voice/sight adapters don't construct an Observation/CandidateAction at all).
  Writing the doc doesn't close the gate by itself — see `COGNITIVE_RUNTIME.md` for the
  concrete remaining gaps.

**Note:** Stage 5 (below) is recommended to wait until this stage's exit gate is fully green —
that sequencing is a recommendation, not yet a binding decision; it requires explicit
user sign-off before treated as blocking.

---

### Stage 5 — Initiative engine (scheduled check-ins + workflows)

**Goal:** Proactive suggestions and check-ins that are safe, useful, and not naggy.

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

> **Updated:** 2026-01-22 based on Cline audit

1. **Stage 0.5: Packaging & Architecture Fixes** (NEW - immediate priority)
   - Add `bartholomew/__init__.py`
   - Consolidate deps in `pyproject.toml`
   - Fix `memory_rules.yaml` malformed rule
   - Refactor `input()` out of kernel
2. **Linux CI green for P0 core**
3. **Fix P0 logic bugs** (summarization/encryption/embeddings/retrieval factory/metrics idempotency)
4. **Quarantine or parameterize platform-specific tests** (Windows file locking; SQLite/FTS limitations)
5. **Stage 1 UI/console slice**

## What we will not do yet

- Expand automation/tooling surface without governance + test coverage.
- “Act” features without parking-brake, consent, audit, and rollback.
