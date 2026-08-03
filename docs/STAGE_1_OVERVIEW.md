# Stage 1 Overview — Minimal Consumer Web Governance Shell

> **Authority note:** this document is the concise explanation of the *approved Stage 1 direction
> and stage structure*, mirroring `docs/PHASE_B_OVERVIEW.md`'s shape and role. It is subordinate to
> and linked from `ROADMAP.md`, which remains the canonical source for Stage 1's exit criteria and
> approval boundaries. This overview does not itself authorise implementation of any sub-stage
> beyond S1.1, S1.3, and S1.5 (already implemented, see below) — each of S1.2, S1.4, and S1.6 needs
> its own separate approval before implementation begins.
>
> **Last updated:** 2026-08-01 (S1.3 notification settings + mute/quiet-hours implemented,
> following S1.1 Parking Brake API + UI and S1.5 governance audit/provenance view).

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
- Notification settings and mute/quiet-hours controls — no schema, no route — **now closed by
  S1.3, below.**
- The `awaiting_response` obligation state — `COGNITIVE_RUNTIME.md`'s own words: *"this state does
  not exist in code today."* A full runtime-lifecycle concept (must traverse Observation →
  Interpretation → Executive → Governance → Capability → Execution → Reflection → Memory like any
  other action), not a UI-only add.
- An audit/provenance view — the data exists in `governance_audit` (see S1.1) but nothing reads it
  back out yet — **now closed by S1.5, below.**
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

### S1.3 — Notification settings + mute/quiet-hours ✅ (Implemented 2026-08-01)
**Purpose:** user-controlled notification preferences and quiet-hours windows.
**Found already built** (`bartholomew/skills/notify.py`, an `enabled: true` Stage 4 starter
skill, previously with no test file at all): a full `NotifySkill` whose quiet-hours logic already
gated notification delivery — but quiet hours were hardcoded (`DEFAULT_QUIET_HOURS_START/END`,
reset on every `initialize()`) with no `set_quiet_hours` action, and there was no mute concept
anywhere in the file.
**Scope implemented:**
- A `notification_settings` singleton-row table persists quiet hours and mute state; `initialize()`
  loads it (seeding class defaults on first run) instead of hardcoding.
- New skill actions: `set_quiet_hours`, `mute` (optional `until`), `unmute`,
  `get_notification_settings` — `get_quiet_hours`'s existing response shape is untouched.
  `_is_muted()` lazily clears (and persists the clear) an expired `muted_until`.
- `_action_send()`'s gate and `_process_queue()`'s delivery-bypass condition both now also check
  mute, treated the same as an always-on quiet-hours window for non-`URGENT` notifications.
- `GET/PUT/POST /api/notifications/{settings,quiet-hours,mute,unmute}`
  (`routes/notifications.py`) — each a direct `await kernel.skill_registry.execute_action("notify",
  ...)`, the same single, already-governed choke-point every skill execution goes through (no new
  pattern introduced).
- A "🔔 Notifications" UI card, following S1.1's `r.ok`-checking convention.
- Tests: `tests/test_notify_skill_settings.py` (new — first test file for this skill at all) and
  `tests/test_notifications_api.py` (HTTP-level, same pattern as `test_governance_api.py`).
**Blocking discovery, resolved:** `kernel.skill_registry.execute_action()` denied *every* skill
call outright in the default config — `Identity.yaml`'s `tool_use.allowlist` contained no skill_id
at all (only `web_fetch`/`browser_action`), and `evaluate_tool_policy()` has no skill-level
exemption (unlike scheduler drives' `_SELF_MAINTENANCE_DRIVES`). This blocked the whole feature,
not something specific to this route. Fixed by adding `"notify"` to `Identity.yaml`'s
`tool_use.allowlist` — a small, explicitly user-approved change (flagged before committing, since
`Identity.yaml` names explicit approvers in its own `governance.change_control` section) for an
already-shipped, already-enabled, `permissions.level: "auto"` (low-risk) skill that was simply
unreachable via the governed HTTP path until now.
**Exit condition met:** quiet hours and mute set via the API/UI persist across a real page reload
(not just in-memory), verified with a real browser (Playwright + the pre-installed headless
Chromium).

### S1.4 — `awaiting_response` queue (scoped only, not implemented)
**Purpose:** implement the obligation-state concept `COGNITIVE_RUNTIME.md` documents but that does
not exist in code — opened/reminded/escalated/resolved, traversing Governance like any other
action. The largest, most novel remaining sub-stage; needs its own design pass, not just CRUD.
**Deferred until its own separate approval.**

### S1.5 — Audit / provenance view ✅ (Implemented 2026-08-01)
**Purpose:** read `governance_audit` (now including `actor`, per S1.1) back out through an API +
UI view.
**Scope implemented:**
- `GovernanceStore.list_audit(limit: int = 50) -> list[dict]`
  (`bartholomew/orchestrator/safety/governance_store.py`) — a read-only method returning the most
  recent `governance_audit` rows newest-first, decoding the persisted `scopes` JSON string into a
  list per entry.
- `GET /api/governance/audit` (`routes/governance.py`, `limit` query param, validated 1–100,
  mirroring `routes/liveness.py`'s existing bounds-check convention) → `await
  run_off_loop(kernel.governance_store.list_audit, ...)`, returning `{"entries": [...], "count":
  N}` — the same list-endpoint shape as this API's other list routes (e.g. `/api/episodes/recent`).
- A "📜 Governance Audit" card in `ui/minimal/index.html`, placed directly after the Parking Brake
  card, reusing the existing `.nudge-item`/`.nudge-kind`/`.nudge-msg`/`.nudge-ts` CSS (no new
  styles needed). `refreshAudit()` follows S1.1's `r.ok`-checking convention (explicit error state,
  never silently renders a failed fetch as empty). Runs on initial load, its own 30s interval, and
  immediately after a successful engage/disengage so new entries appear without a manual refresh.
- Tests: `list_audit()` unit tests (ordering, `scopes` decoding, `limit`, empty-store case) in
  `tests/test_governance_store.py`; HTTP-level tests (`/api/governance/audit` reflects
  engage/disengage, `count` matches `len(entries)`, `limit` respected, out-of-bounds `limit` → 400)
  in `tests/test_governance_api.py`.
**Explicitly out of scope (per this stage's own plan review):** `startup_incidents` (a different
concern — runtime integrity diagnostics, not action audit) and any future S1.2/S1.4 provenance
sources — those remain deferred to their own sub-stages, unchanged by this one.
**Exit condition met:** engage/disengage produce audit entries visible via `GET
/api/governance/audit` and the minimal UI without a manual refresh, verified with a real browser
(Playwright + the pre-installed headless Chromium).

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
