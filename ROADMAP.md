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

### Stage 3 — Unified Persona Core (Experience Kernel) (next after Stage 2)

**Goal:** Bartholomew behaves like one continuous “self” with an Experience Kernel (self-model + narrator) and configurable persona packs, without expanding the action surface.

**Constraints:**
- No new real-world “Act” powers.
- Must preserve consent gates, privacy redaction/encryption, and auditability.

**Exit criteria:**
- Experience Kernel MVP wired into the loop (self snapshot + narrator reflections).
- Persona packs switchable via config/UI and recorded in audit logs.
- New unit + integration tests for kernel/persona.

**Verify:**
```bash
pytest -q tests/test_experience_kernel.py
pytest -q tests/test_persona_switching.py
```

---

### Stage 4 — Modularity: Skill registry + starter skills

**Goal:** Standardize skills as installable modules with explicit manifests, permissions, and test expectations.

**Exit criteria:**
- Skill manifest schema defined + enforced.
- Registry can list/load skills; permission model applied.
- Starter skills working end-to-end: tasks + notify + calendar draft.

**Verify:**
```bash
pytest -q tests/test_skill_registry.py
pytest -q tests/test_end_to_end_tasks_and_audit.py
```

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

1. **Canonical docs landed (SSOT)**
2. **Linux CI green for P0 core**
3. **Fix P0 logic bugs (summarization/encryption/embeddings/retrieval factory/metrics idempotency)**
4. **Quarantine or parameterize platform-specific tests** (Windows file locking; SQLite/FTS limitations)
5. **Stage 1 UI/console slice**

## What we will not do yet

- Expand automation/tooling surface without governance + test coverage.
- “Act” features without parking-brake, consent, audit, and rollback.
