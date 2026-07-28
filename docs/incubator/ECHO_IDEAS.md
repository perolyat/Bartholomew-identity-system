# Echo Ideas (Brainstorm-Derived) — Incubator Only

> **This document is explicitly NON-CANONICAL, NON-AUTHORITATIVE, and NOT an approved roadmap.**
>
> It previously existed as "Echo Integration Gates" in `ROADMAP.md` and "Echo Integration Roadmap"
> in `MASTER_PLAN.md`. It has been moved here (2026-07-28, planning-document reconciliation) because
> embedding it in either canonical document instructed a future agent to build a second kernel, a
> second memory architecture, and a second governance/permissions system — in direct conflict with
> `CONSTITUTION.md`'s "one architectural authority exists per concept" principle and the existing
> ownership table in `COGNITIVE_RUNTIME.md`.
>
> **Rule for using this document:** every individual idea below requires independent evaluation
> against `CONSTITUTION.md`, `COGNITIVE_RUNTIME.md`, and the current authoritative owner of the
> relevant concept (see `COGNITIVE_RUNTIME.md`'s ownership table) **before** any adoption. Nothing
> here is pre-approved. Nothing here may be implemented, scheduled, or referenced as an existing
> stage gate. In particular:
>
> - No item below may propose a second kernel, second memory authority, or second governance system
>   as though it were already approved — any such idea must instead be evaluated as a change to the
>   *existing* authoritative kernel (`bartholomew/kernel`), Memory Substrate, or Governance
>   (`ParkingBrake`/`policy_engine.py`), never as a parallel implementation.
> - No item below may propose a competing deployment architecture as though already approved — see
>   `DECISIONS.md`'s "Deployment architecture: hybrid local-first" entry for the actual approved
>   direction any device/sync/cloud idea here must be evaluated against.
>
> **Source:** extracted from 81 design conversations under `docs/brainstorm/` / `logs/brainstorm/`.
> Full feature JSON: `logs/brainstorm/merged/features_master.json`. Per-chunk JSONs:
> `logs/brainstorm/extracted/features_chunk_*.json`. Verbatim source:
> `logs/brainstorm/BARTHOLOMEW_BRAINSTORM_NOTES_VERBATIM.md`.

---

## Echo Gate 0 — Foundation (5 features)

**Concept goal (as originally brainstormed):** establish a core agent architecture with local-first
execution and a security baseline.

**Originally proposed scope:**
- A LangGraph-based agent kernel implementing a full perceive → retrieve → decide → act → learn loop
- Episodic (SQLite) + semantic (Chroma) memory with RAG
- A YAML-based permissions system (ask/auto/never)
- A Tauri + Python architecture for desktop-first offline operation
- Code signing + runtime attestation for supply-chain integrity

**Why this needs individual re-evaluation, not adoption as written:** Bartholomew already has an
authoritative kernel (`bartholomew/kernel`), an authoritative Memory Substrate (SQLite, with FTS +
vector retrieval), and an authoritative governance/permissions mechanism (`ParkingBrake` +
`policy_engine.py` + the skill-manifest permission model) — see `COGNITIVE_RUNTIME.md`'s ownership
table. Any of the above ideas (e.g., a perceive→decide→act loop shape, code signing) would need to be
evaluated as a possible *enhancement* to those existing authorities, never as a second implementation
of the same concept.

**Originally proposed exit criteria (as brainstormed, not approved):** agent kernel completes a full
loop with capped steps/timeouts; memory stores persist/retrieve with consent gates; all binaries
signed with startup verification; permissions enforced for all actions.

---

## Echo Gate 1 — Core Capabilities (16 features)

**Concept goal:** gaming mentor, device identity, and an "organic immune system" (EOIS) foundation.

**Originally proposed scope:**
- Gaming: session detection, build guidance, inventory coaching, contextual help adaptation
- Permissions-aware memory with context metadata
- Modular skill manifests (hot-load/unload)
- Context-aware modes (In-Game, Life, Work, Focus, Car)
- Scheduled check-ins (APScheduler with mode-aware quieting)
- Device Identity (EDID) with TPM/Secure Enclave binding
- Mutual TLS device pairing, multi-factor authentication gates for sensitive operations
- Tamper-evident action logging (ed25519 signatures)
- Device bridge services (Rust/Go) for USB/Bluetooth/mDNS
- Echo Organic Immune System (EOIS) three-layer defense (Border/Detection/Containment)

**Originally proposed exit criteria (as brainstormed, not approved):** gaming mentor gives build advice
without external wikis; each device has cryptographic identity with secure pairing; all privileged
actions logged with signatures; EOIS detects and contains basic threats via signature + baseline.

---

## Echo Gate 2 — Advanced Integration (21 features)

**Concept goal:** cross-device sync, smart home, car mode, and a full EOIS with quarantine/forensics.

**Originally proposed scope:**
- Smart home (Matter/Home Assistant) with scenes
- Android Auto car mode (push-to-talk, <6s replies, safety constraints)
- Real-time cross-device sync (desktop/mobile/car)
- Personality packs (Coach, Gamer Ally, Calm Mentor — switchable personas)
- Human-readable audit trail with rationale
- "Shadow + Smoke" UI theme (futuristic glass/neon aesthetic)
- Local voice I/O (Vosk STT, Piper/Coqui TTS), offline voice processing
- USB PC rescue mode, Smart TV voice remote
- Device troubleshooting knowledge base, trusted device whitelist
- IoT protocol adapters (DLNA, WebOS, Tizen, Chromecast, HDMI-CEC)
- Cross-domain maturation, behavioral baseline detection, canary tokens, honey traps
- Encrypted quarantine store, network isolation controls
- Restore points and rollback, forensics incident export, binary watermarking

**Note on personality packs specifically:** Bartholomew already has an authoritative persona-pack
mechanism (`bartholomew/kernel/persona_pack.py`'s `PersonaPackManager`, per `DECISIONS.md` item
11.12 and the personality/Constitution split recorded 2026-07-28). Any "Coach/Gamer Ally/Calm Mentor"
idea from this list should be evaluated as *additional packs* under that existing authority, not a
new mechanism.

**Originally proposed exit criteria (as brainstormed, not approved):** tasks sync instantly across
devices; car mode enforces safety constraints; smart home scenes execute with consent gates; EOIS
quarantines threats and exports forensics bundles; all actions reversible via restore points.

---

## Echo Gate 3 — Ecosystem (3 features)

**Concept goal:** community extensibility with security vetting and privacy-preserving intelligence.

**Originally proposed scope:**
- Local skill marketplace (install/remove live, no restart)
- Skill vetting (static analysis + author signatures)
- Opt-in differential-privacy telemetry for threat intelligence

**Originally proposed exit criteria (as brainstormed, not approved):** community skills installable
from a UI with vetting; marketplace prevents malicious skill distribution; telemetry aggregation
mathematically preserves privacy.

---

## Constraints on anything adopted from this document

Carried forward from the original brainstorm notes, and still binding on any idea evaluated out of
this document:

- Must inherit all existing Bartholomew governance (parking brake, consent gates,
  redaction/encryption) — never a parallel governance path.
- No Echo-derived feature ships without: a threat model, acceptance criteria, tests, and a rollback
  plan, evaluated the same way any other proposed change to this repository is evaluated.
- Privacy-first: local execution by default; cloud features strictly opt-in, consistent with the
  hybrid local-first deployment architecture recorded in `DECISIONS.md`.

## Re-evaluation checklist for any single idea pulled from this document

Before any idea above is proposed for actual implementation:

1. Which existing canonical concept/owner does it touch (per `COGNITIVE_RUNTIME.md`'s ownership
   table)? State it explicitly — "none" is not an acceptable answer for any idea that involves
   perception, memory, decision-making, or permissions.
2. Does it conflict with `CONSTITUTION.md`'s five pillars or any stated invariant? If so, the
   conflict must be resolved explicitly (update `CONSTITUTION.md` with rationale, or reject the
   idea) — never silently adopted.
3. Does it conflict with the hybrid local-first deployment architecture (`DECISIONS.md`)?
4. Does it require a threat model (device pairing, MFA, remote access, telemetry)? If so, that threat
   model must exist and be reviewed before implementation, not after.
5. Is it consistent with the consumer-value gate (`CONSTITUTION.md`) — does it materially reduce
   cognitive burden, reduce life administration, prevent forgotten matters, improve outcomes, or
   preserve trust? Architectural sophistication alone does not justify inclusion.

## Verification (reference only — not implemented, not a canonical exit criterion)

```bash
# View extracted features
cat logs/brainstorm/merged/features_master.json | python -m json.tool | head -50
# Feature count by gate
python -c "import json; from pathlib import Path; features = json.loads(Path('logs/brainstorm/merged/features_master.json').read_text()); gates = {}; [gates.setdefault(f['suggested_stage_gate'], []).append(f['feature']) for f in features]; [print(f'{gate}: {len(feats)} features') for gate, feats in sorted(gates.items())]"
```
