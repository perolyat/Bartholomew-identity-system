# Stage 1 Overview — Minimal Consumer Web Governance Shell

> **Authority note:** this document is the concise explanation of the *approved Stage 1 direction
> and stage structure*, mirroring `docs/PHASE_B_OVERVIEW.md`'s shape and role. It is subordinate to
> and linked from `ROADMAP.md`, which remains the canonical source for Stage 1's exit criteria and
> approval boundaries. This overview does not itself authorise implementation of any sub-stage
> beyond S1.1 (already implemented, see below) — each of S1.2–S1.6 needs its own separate approval
> before implementation begins.
>
> **Last updated:** 2026-08-01 (initial version; S1.1 Parking Brake API + UI implemented).

## 1. Purpose

`ROADMAP.md`'s "Near-term milestone plan" sequences Stage 1 — a minimal consumer web governance
shell on top of `bartholomew_api_bridge_v0_1` — immediately after Phase B (merged as PR #33).
Stage 5's live proactive behaviour needs a real, user-facing governance surface first; Stage 1 is
that surface. Its exit criteria span six distinct capabilities plus host-device onboarding
guidance — comparable in scope to Phase B, which the project deliberately staged as B0–B9 rather
than one large diff (`DECISIONS.md`'s "no implicit authority expansion" / "user approval gate"
decisions). This document applies that same discipline to Stage 1.

## 2. Current state (baseline, as found 2026-08-01)

**Already shipped**, in `bartholomew_api_bridge_v0_1/`:
- `services/api/app.py` — nudges (list/ack/dismiss), reflections (latest/trigger), health, chat.
- `services/api/routes/self_state.py` — affect/attention/drives/goals, episodes, persona (Stage 3;
  not a Stage 1 exit-criterion item, but already live).
- `ui/minimal/index.html` — zero-dependency UI covering all of the above, with auto-refresh.

**Genuinely unbuilt**, confirmed by direct code reading, not assumed:
- Parking Brake control had no HTTP route at all (the daemon's shared `kernel.governance_store`
  and the CLI's `brake on/off/status` were the only callers) — **now closed by S1.1, below.**
- A consent/approval-request inbox for general "ask"-level capability requests. The existing
  `ConsentGate`/`memory_consent` (`bartholomew/kernel/consent_gate.py`) only gates memory
  *retrieval*, a different concern.
- Notification settings and mute/quiet-hours controls — no schema, no route.
- The `awaiting_response` obligation state — `COGNITIVE_RUNTIME.md`'s own words: *"this state does
  not exist in code today."* A full runtime-lifecycle concept (must traverse Observation →
  Interpretation → Executive → Governance → Capability → Execution → Reflection → Memory like any
  other action), not a UI-only add.
- An audit/provenance view — the data exists in `governance_audit` (see S1.1) but nothing reads it
  back out yet.
- Host-device onboarding guidance content.

## 3. Sub-stages S1.0–S1.6

### S1.0 — Baseline
**Purpose:** establish the current-state facts the later sub-stages depend on (Section 2 above).
**Exit condition:** this document. No production implementation.

### S1.1 — Parking Brake API + UI ✅ (Implemented 2026-08-01)
**Purpose:** surface the existing, live, tested `GovernanceStore` (Phase B stages B3/B4/B6) through
the API bridge and minimal UI — pure additive wiring, no new governance semantics.
**Scope implemented:**
- `bartholomew_api_bridge_v0_1/services/api/routes/governance.py` — `GET /api/governance/brake`,
  `POST /api/governance/brake/engage`, `POST /api/governance/brake/disengage`, registered in
  `services/api/app.py`. Engage/disengage go through `run_off_loop()` against
  `kernel.governance_store` (Phase B stage B2's event-loop-isolation pattern, same as every other
  kernel-hitting route in `routes/self_state.py`). `StaleGovernanceWriteError` → HTTP 409;
  `WriteFenceClosedError` → HTTP 503.
- A "🅿️ Parking Brake" card in `ui/minimal/index.html`: status badge, scope checkboxes for the five
  known scopes, Engage/Disengage buttons, auto-refreshing every 15s.
- `governance_audit`'s new `actor` column (`bartholomew/orchestrator/safety/governance_store.py`),
  backfilled additively for pre-existing databases, threaded through `engage()`/`disengage()`, and
  populated by both the CLI (`actor="cli"`) and the new API route (`actor` from the request body,
  default `"user"`) — added during this stage's plan review so the audit trail records who/what
  requested each transition from the start, not just what changed.
- Tests: `tests/test_governance_api.py` (HTTP-level, real `TestClient` + real kernel), plus new
  actor-column cases in `tests/test_governance_store.py`.
**Design decisions carried from plan review (2026-08-01), recorded for continuity:**
- **Crash/restart persistence:** already fail-safe by construction — `GovernanceStore._write()`
  commits state and its audit row atomically, and every construction (including after a crash)
  reloads from the persisted row; already covered by
  `test_successful_write_survives_connection_reopen_with_both_rows_present` in
  `tests/test_governance_store.py`. No brake state is ever held only in memory.
- **Emergency override:** while the kernel is `RUNNING`, `engage()` (tightening) is never refused
  regardless of staleness — only `disengage()` (loosening) is revision-guarded. The only refusal
  path, `WriteFenceClosedError`, fires only once shutdown's write fence closes, by which point the
  API's admission middleware has already been returning 503 to every route (not just this one)
  since `lifecycle_state` left `RUNNING` — see `daemon.py`'s `stop()`, which sets `STOPPING` before
  closing the write fence.
- **Authentication:** deliberately *not* added in this stage. The API bridge has no authentication
  on any route today (chat, nudges, reflections, etc. are equally open); `ROADMAP.md`'s Stage 1
  section explicitly defers real auth to a separate future project ("a Stage 1 shell is not itself
  an authentication project"). Singling out the brake route for one-off auth would be inconsistent
  and give false assurance about the rest of the surface. The `actor` field is caller-declared, not
  verified — audit-trail structure, not authorization. Revisit when the separate auth project lands.
**Exit condition met:** engage/disengage/status reachable over HTTP and from the minimal UI,
verified with a real browser (Playwright + the pre-installed headless Chromium) clicking through
engage → status update → disengage, not just a curl round-trip.

### S1.2 — Consent / approval inbox (scoped only, not implemented)
**Purpose:** a pending "ask"-level permission-request queue distinct from `ConsentGate`'s existing
memory-retrieval consent.
**Deferred until its own separate approval.**

### S1.3 — Notification settings + mute/quiet-hours (scoped only, not implemented)
**Purpose:** user-controlled notification preferences and quiet-hours windows.
**Deferred until its own separate approval.**

### S1.4 — `awaiting_response` queue (scoped only, not implemented)
**Purpose:** implement the obligation-state concept `COGNITIVE_RUNTIME.md` documents but that does
not exist in code — opened/reminded/escalated/resolved, traversing Governance like any other
action. The largest, most novel remaining sub-stage; needs its own design pass, not just CRUD.
**Deferred until its own separate approval.**

### S1.5 — Audit / provenance view (scoped only, not implemented)
**Purpose:** read `governance_audit` (now including `actor`, per S1.1) back out through an API +
UI view, plus any other action-provenance sources S1.2/S1.4 introduce.
**Deferred until its own separate approval.**

### S1.6 — Host-device onboarding guidance (scoped only, not implemented)
**Purpose:** onboarding content presenting the realistic trade-offs of each supported deployment
target, consistent with `DECISIONS.md`'s hybrid local-first deployment-architecture entry.
**Deferred until its own separate approval.**

## 4. Non-negotiable invariants (mirrors Phase B's overview)

- **No implicit authority expansion.** Approving this overview (and S1.1's implementation) does not
  approve S1.2–S1.6's implementation; approving one sub-stage does not approve the next.
- **User approval gate unchanged.** Every sub-stage's plan and every implementation diff remains
  separately and explicitly gated, per `DECISIONS.md`.
- **No auth-by-omission drift.** No future Stage 1 route may be shipped under the assumption that
  "the brake route already sets the precedent" — the no-auth posture is a documented, deliberate,
  whole-surface deferral, not a one-off decision that individual routes silently inherit forever.
