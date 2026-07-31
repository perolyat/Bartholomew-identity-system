# B3 — Governance Schema and Parking Brake Persistence

> **Status:** B3 exit deliverable per `docs/PHASE_B_OVERVIEW.md` §5 and `ROADMAP.md`'s Phase B
> stage table. Implements a new, governance-owned schema and Parking Brake transition semantics,
> tested in isolation. Does **not** wire this into the live runtime — the current
> `bartholomew.orchestrator.safety.parking_brake` module (`ParkingBrake`/`BrakeStorage`) is
> untouched and remains the live, wired-in implementation. Runtime integration is B4's work.
>
> **Base facts:** drawn from `docs/B0_PERSISTENCE_BASELINE.md`, `docs/B1_SHARED_CONNECTION_POLICY
> .md`, `docs/B2_EVENT_LOOP_ISOLATION.md`, and a fresh grounding pass on
> `bartholomew/orchestrator/safety/parking_brake.py` done at B3 plan start.

## 1. Grounded findings that shaped this stage's scope

Re-verified directly against the current repository before designing anything (not trusted from
the archived research document):

1. **No governance-owned schema existed.** Brake state lived in one row of `system_flags` (key
   `"parking_brake"`, JSON `{engaged, scopes}`) — a table `MemoryStore`'s own `SCHEMA` creates and
   seeds. None of the archived design's `parking_brake_state`, `brake_runtime`, or
   `governance_audit` tables existed.
2. **No version/revision guard existed.** `ParkingBrake._write()` unconditionally overwrote the
   row — the "stale delayed-loosening can regress state" risk was entirely unguarded, not merely
   under-guarded.
3. **Audit logging was dead code in production.** `BrakeStorage.append_memory()` early-returns
   unless a `memory_store` was passed to its constructor. Checked all 9 real `ParkingBrake`/
   `BrakeStorage` construction sites (`runtime_contract.py` ×4, `skill_registry.py`, `cli.py` ×3,
   `identity_interpreter/orchestrator/orchestrator.py`): **none pass one.** Zero audit events were
   ever written anywhere live.
4. **`engage()`/`disengage()` have exactly one real production caller each**, both in
   `bartholomew/cli.py` (`brake on`/`brake off`). The archived document's "4 files depend on
   replace semantics" claim doesn't hold against the current repository — 1, not 4.
5. **`brake_runtime`** (the archived design's third table) is, per the archived document itself
   (§1036 region), the write-fence/clean-shutdown-marker mechanism — B5's territory, not B3's. B3's
   own scope bullet list never mentions it. Deferred entirely to B5, per this stage's approved
   direction — no empty/unused table created here.

## 2. What was built

**New module:** `bartholomew/orchestrator/safety/governance_store.py` — deliberately separate from,
and not modifying, `parking_brake.py`. Two tables, owned by this module alone (not `MemoryStore`'s
schema):

```sql
CREATE TABLE parking_brake_state (   -- singleton row, id=1
    id INTEGER PRIMARY KEY CHECK (id = 1),
    engaged INTEGER NOT NULL DEFAULT 0,
    scopes TEXT NOT NULL DEFAULT '[]',   -- JSON array
    revision INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL
);
CREATE TABLE governance_audit (      -- append-only
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    action TEXT NOT NULL,            -- "engaged" | "disengaged" | "migrated"
    scopes TEXT NOT NULL,
    reason TEXT,                     -- structured reason, nullable
    revision INTEGER NOT NULL
);
```

**`GovernanceStore`** (the new, isolated-tested class):
- `engage(*scopes, reason=None)` — tightening. **Always applies**, never refused, matching the
  non-negotiable invariant that the brake can only become more restrictive without an explicit,
  confirmed loosening action. Keeps simple **replace** semantics (this stage's approved direction,
  not union) — an accepted, documented limitation given only one real caller exists today (see
  finding #4); revisit only if B4's live-daemon re-inventory finds a genuine concurrent caller.
- `disengage(*, reason=None, expected_revision=None)` — loosening. Defaults `expected_revision` to
  the instance's own last-loaded revision, so every disengage is automatically guarded without
  extra caller effort; raises `StaleGovernanceWriteError` instead of writing if the persisted
  revision has moved since that snapshot was taken — the "confirmed" half of the invariant. A
  caller that genuinely wants to force a loosening past a known-newer revision passes
  `expected_revision` explicitly (a deliberate, visible override, not an accident of stale caching).
- `is_blocked(scope)` — same semantics as the current `ParkingBrake.is_blocked()`.
- Every write (`engage`/`disengage`/the legacy migration) writes the state row and its audit
  record in **one transaction** — the two are never independently observable: a failure before
  commit leaves both absent, a success leaves both present together. This is what actually
  delivers "atomic audit events" — finding #3's dead-code mechanism is real here.

**`ensure_schema(db_path)`** — additive/idempotent: `CREATE TABLE IF NOT EXISTS` for both tables,
then imports any existing `system_flags` "parking_brake" row (non-destructively — `system_flags`
and the live `ParkingBrake` reading it are untouched) as the initial state at revision 1, recording
a `"migrated"` audit entry with a fixed provenance reason. No legacy row → a fresh disengaged
default at revision 0, with no audit entry (nothing has actually transitioned yet). Safe to call
repeatedly; a second call finds the singleton row already present and does nothing further.

Deliberately synchronous — this module isn't reachable from the event loop anywhere yet (nothing
calls it outside tests), so Phase B stage B2's off-loop pattern doesn't apply here. B4 is
responsible for wiring this in through `bartholomew.kernel.blocking_executor.run_off_loop()`, the
same way `construct_parking_brake_off_loop()` already does for the current `ParkingBrake`, once
this actually becomes reachable from async code.

## 3. Tests

`tests/test_governance_store.py` (19 tests): default fresh state; replace-semantics engage;
default-scope-is-global; `is_blocked()` global-vs-scoped; disengage against own cached revision;
**stale-revision rejection** (the core B3 invariant — a delayed disengage from an old snapshot is
refused, state unchanged); disengage-after-refresh succeeds; explicit `expected_revision` override;
engage is *never* refused regardless of staleness; structured audit reason present/optional; legacy
`system_flags` import (state + provenance audit entry); import is non-destructive to
`system_flags`; migration idempotency (both with and without a legacy row, run 2–3× with no
duplication).

**Crash-consistency tests** (added per this stage's approved direction):
- A real SQLite `BEFORE INSERT` trigger (not a Python-level mock — `sqlite3.Connection` is a C type
  and can't be monkeypatched) injects a failure between the state `UPDATE` and the audit `INSERT`;
  verifies neither the state change nor an audit row was committed.
- A successful write survives dropping all in-process references and reopening a brand-new
  connection — both the state row and its audit record are present together, matching what a real
  process restart would see.
- A rejected stale write leaves no partial trace of its own (distinct from the crash case above) —
  audit count and revision are exactly what the two real prior writes produced, nothing extra.

## 4. Exit condition check

- [x] Governance schema implemented: `parking_brake_state`, `governance_audit` — narrow, owned by
  this module, not `MemoryStore`'s schema.
- [x] Additive/idempotent migrations: `ensure_schema()`, re-run-safe, non-destructive to
  `system_flags`.
- [x] Parking Brake state and versions: revision-guarded `disengage()`.
- [x] Engage, disengage, and scope narrowing: implemented (replace semantics, per this stage's
  approved direction).
- [x] Stale-result handling: `StaleGovernanceWriteError`, tested.
- [x] Atomic audit events: state + audit in one transaction, tested via a real crash-injection
  trigger.
- [x] Legacy-value migration: tested, idempotent, non-destructive.
- [x] Isolated transition tests: 19 tests, all against `GovernanceStore` directly — `ParkingBrake`
  is not the runtime's shared instance yet (unchanged, still live, per B4's deferral).

Not required for exit, and not done: wiring `GovernanceStore` into `KernelDaemon` or any live call
site (B4); `brake_runtime`/write-fence schema (B5); CLI/process-lock integration (B6).
