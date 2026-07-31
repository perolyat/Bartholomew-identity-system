# B3 Implementation — Governance Schema and Parking Brake Persistence

> Phase B, stage B3. Per `docs/PHASE_B_OVERVIEW.md`'s B3 scope and exit condition: "governance
> schema and Parking Brake transition semantics implemented and tested in isolation, **without yet
> being the runtime's shared instance**." This stage does not touch
> `bartholomew/orchestrator/safety/parking_brake.py` or any of the 9 real `ParkingBrake` construction
> sites `docs/B0_BASELINE_REPORT.md` §5 found — that swap is B4's job.

## 1. Two real defects found while designing this

Reading the live implementation (`bartholomew/orchestrator/safety/parking_brake.py`) before writing
the new schema surfaced two concrete bugs, not just the "candidate risks" `docs/PHASE_B_RISK_MAP.md`
carried forward from the archived research:

1. **Non-monotonic `engage()`.** The live `ParkingBrake._write()` *replaces* the persisted `scopes`
   set on every call. `engage("global")` followed later by `engage("skills")` leaves the brake
   persisted as `{"skills"}` only — `"global"` is silently dropped. That's a direct violation of
   `docs/PHASE_B_OVERVIEW.md` §4's "the Parking Brake can only become *more* restrictive without an
   explicit, confirmed loosening action" invariant: a second, unrelated `engage()` call can
   *loosen* the brake as a side effect.
2. **No confirmed-loosening distinction.** The live `disengage()` takes no arguments and
   unconditionally clears all scopes — any caller with a brake reference can fully loosen it with one
   call, with nothing distinguishing an intentional loosening from an accidental one.

`bartholomew/kernel/governance/brake_store.py`'s `GovernanceBrakeStore` fixes both: `engage()` unions
into the existing scopes rather than replacing them, and `disengage()`/`narrow_scopes()` both require
`confirm=True`, raising `ValueError` otherwise.

## 2. Schema (`bartholomew/kernel/governance/schema.py`)

Three tables, `CREATE TABLE IF NOT EXISTS` only (additive, idempotent):

- **`parking_brake_state`** — singleton row (`id=1`). Adds `revision` (bumped on every transition)
  and `last_loosen_revision` (the revision at which the most recent loosening was applied) — neither
  exists in the legacy `system_flags`-backed storage, and both are required for the ordering-safety
  fix in §3.
- **`brake_runtime`** — a second singleton row, deliberately separate from the durable state above:
  process/runtime-local binding info (`owner_label`, `pid`, `started_at`, `last_seen_at`) for B6's
  external-control/CLI-safety work to later detect a CLI operation racing a live daemon. Schema only
  in this stage — nothing writes to it yet, since there's no live runtime instance to bind.
- **`governance_audit`** — append-only. Every row is written in the *same transaction* as the
  `parking_brake_state` write it describes (one connection, one commit) — unlike the legacy
  `BrakeStorage.append_memory()`, which fires an **unawaited** `asyncio.create_task()` against
  `MemoryStore` and is not atomic with (or even guaranteed to happen after) the state write it's
  meant to describe.

`schema.ensure_schema(db_path)` also migrates a pre-existing legacy `system_flags` `"parking_brake"`
JSON value into the new table, exactly once (only if `parking_brake_state` has no row yet — checked
before every migration attempt, so a second `ensure_schema()` call, e.g. on next process start,
cannot re-migrate and clobber transitions that happened since). A malformed legacy value fails
closed (`engaged=True, scopes=["global"]`) rather than defaulting to an unengaged brake, per §4's
fail-closed invariant. `system_flags`'s row itself is never written or deleted by this migration —
the legacy `BrakeStorage` keeps reading/writing it unchanged until B4 actually swaps the runtime
over.

## 3. Transition semantics (`bartholomew/kernel/governance/brake_store.py`)

- **`engage(*scopes, actor=None, reason=None, expected_revision=None)`** — unions `scopes` (default
  `{"global"}`) into the current set; always sets `engaged=True`.
- **`disengage(*, confirm, actor=None, reason=None, expected_revision=None)`** — full loosening,
  requires `confirm=True`.
- **`narrow_scopes(scopes_to_remove, *, confirm, ...)`** — partial loosening; if the remaining scope
  set becomes empty, this becomes a full disengage (an engaged brake with no scopes blocks nothing,
  per `is_blocked()`'s semantics, so that's not a meaningfully distinct state). Requires
  `confirm=True`.
- **Stale-transition rejection**: every transition accepts an optional `expected_revision`. If given
  and it doesn't match the persisted revision, `StaleBrakeTransitionError` is raised and **nothing is
  written** (verified by `test_rejected_stale_transition_writes_no_audit_row`) — the direct fix for
  `docs/PHASE_B_RISK_MAP.md`'s B3 row: "a delayed loosening whose `revision` check still passes can
  regress `persisted_version` and reapply stale scopes." `test_stale_revision_check_prevents_
  delayed_loosen_from_regressing_a_newer_engage` reproduces that exact scenario: a caller reads state
  at revision 1, a different actor tightens to revision 2 before the first caller's now-stale loosen
  request is applied, and the stale request is rejected rather than silently regressing the
  in-between tightening.

## 4. Deliberately not built here

- **No runtime wiring.** Nothing in `bartholomew/orchestrator/`, `bartholomew/kernel/daemon.py`, or
  any of the 9 real `ParkingBrake` construction sites was touched. `GovernanceBrakeStore` is
  synchronous, matching the legacy `BrakeStorage`/`ParkingBrake` shape it will eventually replace —
  not yet routed through a B2-style `DedicatedDbExecutor`, since it isn't reachable from the event
  loop at all yet; how the live daemon's async call sites should reach it is an explicit B4 decision,
  not assumed here.
- **No caller migration.** `docs/PHASE_B_RISK_MAP.md`'s "replacement-vs-union compatibility" row
  names four repository files that depend on the live `engage()`'s replace behavior and would need
  rewriting once the runtime moves to a monotonic `engage()` — that rewrite is explicitly B4's
  responsibility (per that row's own "B3 plan start (schema/semantics), B4 plan start (callers)"
  split), not this stage's.
- **No process-lock / CLI integration.** `brake_runtime`'s schema exists; nothing populates or reads
  it yet. That's B6.

## 5. Verification

`tests/test_governance_brake_store.py` (26 tests, all isolated — no daemon, no live `ParkingBrake`
involved): schema idempotency, legacy migration (present, absent, malformed, and
migrate-then-transition-then-re-ensure-schema doesn't clobber), monotonic `engage()` (the direct
regression test for defect #1 above), confirmed `disengage()`/`narrow_scopes()` (defect #2),
stale-revision rejection (including the exact delayed-loosen-vs-newer-engage scenario from the risk
map), atomic audit-row content and ordering, and `is_blocked()` semantics. `black --check`,
`ruff check`, and `mypy` all pass clean on the new module.
