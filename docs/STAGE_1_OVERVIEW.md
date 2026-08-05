# Stage 1 Overview — Minimal Consumer Web Governance Shell

> **Authority note:** this document is the concise explanation of the *approved Stage 1 direction
> and stage structure*, mirroring `docs/PHASE_B_OVERVIEW.md`'s shape and role. It is subordinate to
> and linked from `ROADMAP.md`, which remains the canonical source for Stage 1's exit criteria and
> approval boundaries. This overview does not itself authorise implementation of any sub-stage
> beyond S1.1, S1.2, S1.3, and S1.5 (already implemented, see below) — each of S1.4 and S1.6 needs
> its own separate approval before implementation begins.
>
> **Last updated:** 2026-08-05 (S1.4 design proposed — see `docs/S1_4_AWAITING_RESPONSE_DESIGN.md`;
> design-only, not approved for implementation. Previously: 2026-08-04, S1.2 implemented — see
> below; builds the promotion path the standalone consent-handler fix's `pending_sensitive_writes`
> inbox always anticipated. Previously: 2026-08-03, standalone consent-handler fix implemented —
> see "Standalone: consent-handler fix"
> below S1.6, not a Stage 1 sub-stage. 2026-08-01: S1.3 notification settings + mute/quiet-hours
> implemented, following S1.1 Parking Brake API + UI and S1.5 governance audit/provenance view).

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

### S1.2 — Consent / approval inbox ✅ (Implemented 2026-08-04)
**Purpose:** a pending "ask"-level permission-request queue for `memory_rules.yaml`'s
`ask_before_store` category (`requires_consent: true`), distinct from `ConsentGate`'s existing
memory-*retrieval* consent (`memory_consent` table).
**Found:** a first attempt (built earlier in this stage) was based on a disproven assumption that
`ask_before_store` content was already stored in some gated state; it wasn't — `should_store()`
discarded it identically to `never_store` content, with nothing persisted and no record anywhere.
That attempt was stashed, then dropped, once the assumption was disproven by direct testing.
**Scope implemented** (`bartholomew/kernel/memory_store.py`):
- `upsert_memory()`'s single `should_store()` check is now two explicit checks: `allow_store:
  false` (`never_store`) stays an unconditional hard block, no promotion path, ever — unchanged.
  `requires_consent: true` (`ask_before_store`) now queues the write instead of discarding it,
  reusing the standalone consent-handler fix's `pending_sensitive_writes` inbox (below) rather than
  building a parallel one — extended with a `reason` column (`'privacy_guard'` |
  `'rule_consent'`) and a nullable `privacy_class` column, both additive/idempotent migrations.
- New `skip_rule_consent` keyword-only param (sibling to `skip_privacy_guard`), used only by the
  approval flow so re-running the pipeline doesn't re-trip the gate and re-queue itself.
- `approve_pending_sensitive_write()` now also inserts a `memory_consent` row when resolving a
  `reason='rule_consent'` entry — required because `ConsentGate`/`Retriever` re-evaluate
  `requires_consent` at *retrieval* time too, and only include a memory with a real `memory_consent`
  row (`bartholomew/kernel/consent_gate.py`, `tests/test_retrieval_consent_enforcement.py`).
  Without this, an approved memory would be stored but permanently unretrievable. This is the
  concrete "separate promotion path" `should_store()`'s docstring always described.
- `routes/consent.py` and the "🔏 Pending Memory Consent" UI card needed **no new endpoints** —
  the existing pending-writes inbox from the consent-handler fix already generalizes over
  `pending_id`; the UI now shows a `reason`/`privacy_class` badge per entry.
**Not the same gap as the standalone consent-handler fix** (2026-08, below S1.6) — that fix
addressed a *different*, already-live gate (`privacy_guard.is_sensitive()`); this closes the
`memory_rules.yaml` gate, reusing the same inbox mechanism for both.
**Exit condition met:** `ask_before_store` content is queued (not discarded); approval stores it
under its original kind/key/ts *and* makes it actually retrievable (verified via
`ConsentGate.get_memory_policy()`, not just a `memories` row); denial leaves nothing stored;
`never_store` content remains an unconditional hard block with no promotion path.

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

### S1.4 — `awaiting_response` queue (design proposed 2026-08-05, not approved, not implemented)
**Purpose:** implement the obligation-state concept `COGNITIVE_RUNTIME.md` documents but that does
not exist in code — opened/reminded/escalated/resolved, traversing Governance like any other
action. The largest, most novel remaining sub-stage; needed its own design pass, not just CRUD.
**Design pass complete:** see `docs/S1_4_AWAITING_RESPONSE_DESIGN.md` for the proposed schema
(new `awaiting_response_store.py`), state machine, Runtime Contract integration (a new
`run_awaiting_response_through_runtime_contract()` seam, reusing `NotifySkill` for delivery rather
than a second notification path), the escalation-drive trigger, and the narrow, fail-closed
auto-resolution rule. **Deferred until its own separate approval to implement** — the design
document itself is not that approval.

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
concern — runtime integrity diagnostics, not action audit) and any future S1.4 provenance
sources — those remain deferred to their own sub-stages, unchanged by this one.
**Exit condition met:** engage/disengage produce audit entries visible via `GET
/api/governance/audit` and the minimal UI without a manual refresh, verified with a real browser
(Playwright + the pre-installed headless Chromium).

### S1.6 — Host-device onboarding guidance (scoped only, not implemented)
**Purpose:** onboarding content presenting the realistic trade-offs of each supported deployment
target, consistent with `DECISIONS.md`'s hybrid local-first deployment-architecture entry.
**Deferred until its own separate approval.**

### Standalone: consent-handler fix ✅ (Implemented 2026-08-03)
**Not a Stage 1 sub-stage** — a standalone fix found while investigating S1.2, kept separate from
Stage 1's numbering since it isn't part of the original six-capability scope. Recorded here because
it's directly adjacent to (and easily confused with) S1.2.
**Purpose:** `MemoryStore.upsert_memory()` has a keyword-based sensitivity gate
(`bartholomew.kernel.memory.privacy_guard.is_sensitive()`) distinct from `memory_rules.yaml`'s
`ask_before_store` rules (S1.2's gap, above — unfixed at the time this fix landed; both now share
the same `pending_sensitive_writes` inbox). It already had live handler-based
consent plumbing — `chat.py` registers a real terminal prompt for interactive CLI use, by design
("headless callers... leave this unset and fail closed instead"). That default is correct for a
synchronous stdin prompt, but its practical effect was that any sensitive-content write from the
live API/daemon was silently and permanently discarded, with zero record anywhere.
**Scope implemented:**
- New `pending_sensitive_writes` table (`bartholomew/kernel/memory_store.py`). When
  `is_sensitive(value)` is true and `get_consent_handler() is None` (the genuine headless case —
  never an interactive handler's explicit decline, which is never second-guessed or re-queued),
  the full write request is preserved instead of discarded.
- `upsert_memory(..., skip_privacy_guard=True)` (mirrors `identity_interpreter`'s existing
  `skip_governance_check` pattern) so an approved item can be stored for real without re-tripping
  the same gate.
- `list/approve/deny_pending_sensitive_write()` on `MemoryStore`; `approve` re-stores through
  `upsert_memory()` itself with the *original* kind/key/ts, not a reimplementation of its
  redaction/encryption/summarization logic.
- `GET/POST /api/consent/pending-writes/...` (`routes/consent.py`), direct `kernel.mem.*()` calls
  matching how every other route already calls `MemoryStore` (no runtime-contract seam involved —
  that's specific to skill execution, per the S1.3 CI lesson).
- A "🔏 Pending Memory Consent" UI card.
- Tests: extended `tests/test_memory_store_sensitive_consent.py` (queuing, explicit-decline is
  never queued, `skip_privacy_guard`, approve/deny, unknown/already-resolved ids) and new
  `tests/test_consent_api.py`; existing consent-security suites
  (`test_consent_gates.py`/`test_consent_bypass_redteam.py`/`test_retrieval_consent_enforcement.py`)
  re-verified with no regression.
**Exit condition met:** a sensitive-content write with no handler registered is queued, not lost;
approving it via the API/UI stores it under its original kind/key, verified with a real browser
(Playwright + the pre-installed headless Chromium) from a seeded pending item through to a
confirmed row in `memories`.

## 4. Non-negotiable invariants (mirrors Phase B's overview)

- **No implicit authority expansion.** Approving this overview (and S1.1's implementation) does not
  approve S1.2–S1.6's implementation; approving one sub-stage does not approve the next.
- **User approval gate unchanged.** Every sub-stage's plan and every implementation diff remains
  separately and explicitly gated, per `DECISIONS.md`.
- **No auth-by-omission drift.** No future Stage 1 route may be shipped under the assumption that
  "the brake route already sets the precedent" — the no-auth posture is a documented, deliberate,
  whole-surface deferral, not a one-off decision that individual routes silently inherit forever.
