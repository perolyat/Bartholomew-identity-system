# S1.4 Design — The `awaiting_response` Obligation Queue

> **Authority note:** this document is the design this sub-stage was implemented against. It is
> subordinate to `ROADMAP.md` (Stage 1's canonical exit criteria) and `docs/STAGE_1_OVERVIEW.md`
> (S1.4's implemented-scope record), and to `COGNITIVE_RUNTIME.md`'s `awaiting_response` section,
> which is the canonical requirement this design implements.
>
> **Status:** proposed 2026-08-05, approved and implemented 2026-08-05. See
> `docs/STAGE_1_OVERVIEW.md`'s S1.4 section for what was actually built, including the one
> implementation-time judgment call not fully resolved here (the `awaiting_response_check`
> scheduler drive's own `Identity.yaml` allowlist entry — see `DECISIONS.md`'s corresponding
> 2026-08-05 entry).

## 1. What this closes

`COGNITIVE_RUNTIME.md`'s `awaiting_response` section states plainly: *"this state does not exist
in code today."* Five required properties are recorded there (obligation stays visible/open, not
archived; resumes automatically on reply; escalates/reminds when overdue under existing
notification governance; every transition is auditable; creating/escalating/resolving an entry
traverses the full Runtime Contract, not a side channel). `ROADMAP.md` calls S1.4 *"the largest,
most novel remaining sub-stage; needs its own design pass, not just CRUD"* — this is that pass.

## 2. Proposed ownership

`COGNITIVE_RUNTIME.md`'s ownership table has no row for this concept yet. It isn't Memory (not
memory content), isn't Governance (it's an action *subject to* governance, not a governance
mechanism itself), and isn't Experience's Working Memory (a short-term conversational buffer, not
a durable obligation ledger with its own lifecycle/escalation state). Proposed: a new store,
`bartholomew/kernel/awaiting_response_store.py`, sibling to `governance_store.py` and
`scheduler/persistence.py` — same shape (isolated class, `ensure_schema()`, one schema, tested in
isolation before wiring), owned by the Kernel Executive row (`daemon.py` / `planner.py` /
`scheduler/*`) alongside goals/nudges, since an obligation is a planning concept: something the
Executive must keep track of and eventually act on, not something Memory or Experience decide
about on their own.

## 3. Proposed schema

```sql
CREATE TABLE IF NOT EXISTS awaiting_response (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT NOT NULL,              -- short human-readable description of the obligation
    origin_surface TEXT NOT NULL,       -- 'chat' | 'scheduler' | 'skill' | 'voice' | 'sight'
    context_ref TEXT,                   -- working-memory item id / memory id the obligation traces back to
    status TEXT NOT NULL DEFAULT 'open',-- 'open' | 'reminded' | 'escalated' | 'resolved'
    opened_at TEXT NOT NULL,
    due_at TEXT,                        -- nullable; when overdue-escalation should start considering this entry
    reminder_count INTEGER NOT NULL DEFAULT 0,
    last_reminded_at TEXT,
    escalated_at TEXT,
    resolved_at TEXT,
    resolution TEXT,                    -- 'reply_received' | 'user_dismissed' | 'manual' | NULL
    actor TEXT                          -- who/what last transitioned this entry (mirrors governance_audit.actor, S1.1)
);

CREATE INDEX IF NOT EXISTS idx_awaiting_response_status
    ON awaiting_response(status, due_at);

CREATE TABLE IF NOT EXISTS awaiting_response_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES awaiting_response(id),
    ts TEXT NOT NULL,
    transition TEXT NOT NULL,           -- 'opened' | 'reminded' | 'escalated' | 'resolved'
    actor TEXT,
    detail TEXT
);
```

Mirrors two precedents already merged: `governance_audit`'s state+audit-row-in-one-transaction
shape (S1.1/`governance_store.py`), and `pending_sensitive_writes`'s single-table
queue-with-status-column shape (S1.2/the consent-handler fix). No new persistence pattern is being
invented here.

## 4. State machine

```
opened --(reminder due)--> reminded --(still overdue, N reminders elapsed)--> escalated
   \                           \                                                  \
    \                           \--------------------(reply received)------------> resolved
     \-----------------------(reply received or explicit resolve)----------------> resolved
```

- **opened**: created when a matter needing an external reply is recognised. Visible to the
  Executive as an open obligation (`COGNITIVE_RUNTIME.md`'s first required property) — it must
  never be silently dropped or archived by anything other than the `resolved` transition.
- **reminded**: `due_at` has passed with no resolution; a reminder was sent (see §6). Multiple
  reminders increment `reminder_count`/`last_reminded_at` without changing status away from
  `reminded`.
- **escalated**: overdue past a configurable reminder threshold. Distinguishes "gently follow up"
  from "this genuinely needs attention" for UI/notification-priority purposes — an escalated entry
  is a stronger notification-priority signal than a reminded one, not a different governance path.
- **resolved**: terminal. `resolution` records why (`reply_received` = the automatic path in §7,
  `user_dismissed`/`manual` = explicit UI/API action). Reachable directly from any prior state —
  a reply can arrive before any reminder ever fires.

## 5. Runtime Contract integration

Per `COGNITIVE_RUNTIME.md`'s explicit requirement, every transition is a governed action, not a
bare store write. Proposed: a new seam function in `runtime_contract.py`,
`run_awaiting_response_through_runtime_contract()`, mirroring `run_drive_through_runtime_contract`'s
shape (this is a kernel-internal/scheduler-adjacent surface, not a user-facing chat/skill call):

- **Stage 1 (Observation):** `source="awaiting_response"`, `raw_content=<transition>:<entry_id>`.
- **Stage 2 (Interpretation):** the entry's `subject`.
- **Stage 3 (Executive):** `CandidateAction` kind — one of `awaiting_response_open`,
  `awaiting_response_remind`, `awaiting_response_escalate`, `awaiting_response_resolve`.
- **Stage 4 (Governance):** ParkingBrake(`"skills"` scope — no new brake scope; see §8 open
  question), then the standard additive Identity Policy check against the `CandidateAction.kind`
  itself (`evaluate_tool_policy(identity_context, candidate_action.kind)`, same call chat/drives/
  device seams already make). **Not** added to `_SELF_MAINTENANCE_DRIVES`-equivalent exemption:
  unlike `self_check`/`curiosity_probe`, a reminder/escalation is genuine outbound contact about
  specific user content, so it must be evaluated for real, same posture the module docstring
  already applies to any drive outside that frozen exempt set.
  **Known dependency, not yet satisfied (flagged in review, 2026-08-05):** because it's evaluated
  for real, `evaluate_tool_policy()` checks the *seam's own* four kinds
  (`awaiting_response_open/remind/escalate/resolve`) against `Identity.yaml`'s `tool_use.allowlist`
  — a separate check from whatever `NotifySkill`'s delegated call underneath it is evaluated
  against (see Stage 5+6 below). Production `Identity.yaml` today has `default_allowed: false` and
  allowlists only `web_fetch`, `browser_action`, and `notify` (S1.3's addition) — none of the four
  new kinds. Unless added, every transition would be denied by the Governance stage before the
  delegated `notify.send` call is ever reached, regardless of `notify` already being allowlisted.
  This mirrors S1.3's own "blocking discovery" exactly (see `docs/STAGE_1_OVERVIEW.md`'s S1.3
  section) and must be resolved the same way: implementation adds all four kinds to
  `Identity.yaml`'s `tool_use.allowlist`, flagged before committing per `Identity.yaml`'s own
  `governance.change_control` section (the same explicit-approval treatment S1.3's `"notify"`
  addition got) — not a silent default-allow, and not deferred as an open question, since the
  answer here is unambiguous.
- **Stage 5+6 (Capability + Execution):** the store mutation itself (open/remind/escalate/resolve
  on `awaiting_response_store.py`). For `remind`/`escalate` specifically, delivery is **delegated
  to the existing governed `NotifySkill` path** (`run_skill_through_runtime_contract(registry,
  "notify", "send", {...})`) rather than a second notification mechanism — reusing exactly the
  machinery S1.3 built (quiet-hours/mute already enforced there), per this project's "one authority
  per architectural concept" rule. `SkillRegistry.execute_action()` runs its *own* independent
  Governance pass on `skill_id="notify"` (brake + `skill_permissions.py` + Identity Policy on
  `"notify"` itself) — this is the check S1.3's allowlist addition satisfies, and it remains a
  satisfied dependency for that inner call specifically. It does not substitute for the outer
  seam's own kind-based check above; both must independently allow the transition.
- **Stage 7+8 (Reflection + Memory):** one `ActionReflection` per transition into the existing
  shared sink (`record_action_reflection`), same as every other seam — closes the "every state
  transition... remain auditable" requirement without a bespoke audit mechanism competing with the
  one Reflection already provides. The `awaiting_response_audit` table (§3) is the queryable,
  per-entry detail view; Reflection is the cross-surface unified stream. Both cite the same
  transition, no divergence.

## 6. Escalation/reminder trigger

A new scheduler drive, `awaiting_response_check`, registered in `drives.py`'s `REGISTRY` with a
proposed cadence of `every:900` (15 min, matching `self_check`'s cadence — frequent enough that a
due obligation isn't stuck for hours before its first reminder, without hammering the DB). Each
tick: scan `awaiting_response` for `status IN ('open','reminded')` rows whose `due_at` has passed
(or whose last reminder is stale past a reminder-interval default), and drive each eligible entry
through `run_awaiting_response_through_runtime_contract()` for the `remind` or `escalate`
transition, per entry — not a bulk operation, so a denial/failure on one entry doesn't affect
others (mirrors `_process_queue()`'s per-notification loop in `notify.py`).

**Explicitly not decided here** (approval-time/implementation-time choice, not a design blocker):
the exact default `due_at` offset when none is supplied at creation, the reminder-to-escalation
threshold (count vs. elapsed time), and whether these are global constants or per-entry
overridable. Proposed starting defaults for discussion: `due_at` defaults to +24h from `opened_at`
if not specified by the creator; escalate after 3 reminders or 72h overdue, whichever comes first.

## 7. Resolution paths

Two paths, deliberately asymmetric in confidence:

- **Explicit (MVP, required):** a `resolve` API/UI action, `resolution='user_dismissed'` or
  `'manual'`. Always available regardless of correlation confidence.
- **Automatic on reply (`resolution='reply_received'`) — narrow MVP scope, not the general
  case:** `COGNITIVE_RUNTIME.md` requires resumption "without the user having to re-raise it," but
  correctly correlating an arbitrary inbound message to a specific open obligation is a hard,
  product-judgement-laden problem (mismatched correlation would silently resolve the *wrong*
  obligation — worse than not automating at all). Proposed narrow rule for S1.4: only auto-resolve
  when the reply arrives as the **next chat turn in the same session/thread** that opened the
  entry (a same-`context_ref`/adjacent-turn heuristic via `working_memory`), and only when exactly
  one open entry exists for that thread — an ambiguous match (multiple open entries, or no
  session/thread correlation available) falls back to staying open, never guesses. Anything
  smarter (topic/subject matching across threads, cross-surface correlation) is genuinely the
  "genuinely adaptive" behaviour `ROADMAP.md`'s Stage 6 scope already reserves — out of scope here,
  not an oversight.

## 8. Open design questions for approval time

1. **ParkingBrake scope:** reuse `"skills"` (delivery already gates through NotifySkill's own
   `"skills"`-scoped check via `run_skill_through_runtime_contract`) vs. add a dedicated
   `"awaiting_response"` scope for the store-mutation stage itself. Recommendation: reuse
   `"skills"` — scopes are free-form strings in `GovernanceStore` (no schema change either way),
   but a new scope is only justified if there's a real reason to brake obligation-tracking
   independently of skill execution, which nothing here currently needs.
2. **Reminder/escalation defaults** (§6) — needs a product decision, not an engineering one.
3. Whether `awaiting_response` entries can be created by surfaces other than chat at all in this
   sub-stage (voice/sight are Stage 6 scope per `COGNITIVE_RUNTIME.md`'s device-surface notes) —
   proposed: chat and scheduler-drive origins only for S1.4; `origin_surface`'s schema already
   allows the rest without a migration when that expands later.

## 9. API + UI shape (mirrors `routes/consent.py` / `routes/governance.py` conventions)

- `GET /api/awaiting-response` — list, `status` filter, same `limit` bounds-check convention as
  `/api/governance/audit`.
- `POST /api/awaiting-response/{id}/resolve` — explicit resolution.
- `GET /api/awaiting-response/{id}/audit` — the per-entry `awaiting_response_audit` trail.
- UI: a "⏳ Awaiting Response" card in `ui/minimal/index.html`, same auto-refresh/`r.ok`-checking
  convention as the Parking Brake and Audit cards.

## 10. Non-negotiable invariants (mirrors Phase B's and S1's overview docs)

- No implicit authority expansion: approving this design document does not approve its
  implementation.
- Creating/reminding/escalating/resolving an entry always traverses the full Runtime Contract seam
  (§5) — no direct `awaiting_response_store` write from a route or skill bypasses it.
- Reminder/escalation delivery reuses `NotifySkill`, never a second notification mechanism.
- An ambiguous auto-resolution match always fails closed to "stays open," never guesses (§7).

## 11. Verify plan (once implementation is separately approved)

```bash
pytest -q tests/test_awaiting_response_store.py       # schema, state machine, audit rows
pytest -q tests/test_runtime_contract_awaiting_response.py  # governance gates, Reflection emission
pytest -q tests/test_awaiting_response_api.py          # HTTP-level, mirrors test_consent_api.py
pytest -q tests/test_scheduler_drive_convergence.py    # awaiting_response_check NOT in _SELF_MAINTENANCE_DRIVES
```
