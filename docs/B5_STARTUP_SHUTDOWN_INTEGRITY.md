# B5 — Startup and Shutdown Integrity

> **Status:** B5 exit deliverable per `docs/PHASE_B_OVERVIEW.md` §5 and `ROADMAP.md`'s Phase B
> stage table. Defines and proves in-process lifecycle-terminal-state conditions for the resources
> B1–B4 introduced. Does **not** cover externally admitted work (B7 hasn't introduced request
> admission yet) or the process lock (B6) — this stage's clean marker reflects confirmed
> termination of the resources it owns, not the complete §4 shutdown invariant.
>
> **Base facts:** drawn from a fresh re-read of `daemon.py`'s current `start()`/`stop()` at plan
> start (changed twice since B0's original inventory — B2 added `blocking_executor`, B4 added
> `governance_store`), not assumed from B0.

## 1. Grounded findings that shaped this stage's scope

Re-verified directly against the current repository before designing anything:

1. **A real startup-unwind gap, introduced by B4.** `governance_store` construction ran before
   the protected `try`/`except` region, but already activated `blocking_executor`'s worker thread
   — a failure there left it un-unwound. Confirmed absent from existing test coverage (`tests/
   test_scheduler_startup_readiness.py`'s failure-injection tests all start from `scheduler_store
   .ensure_schema()` onward).
2. **A producer-task unwind gap.** `_tick_task`/`_consumer_task`/`_dream_task` were created before
   `_scheduler_task`; a failure creating the latter never cancelled the former three.
3. **No clean-marker or write-fence existed anywhere** — re-confirmed; B3 had explicitly deferred
   `brake_runtime` here.
4. **No Governance write freeze during shutdown — newly relevant because of B4.** Before B4,
   nothing wrote to `governance_store` on the hot path; B4's dual-check bridge mirror logic now
   does, on ordinary reads.
5. **No poisoned-instance guard** — confirmed by direct search: nothing prevented calling `start()`
   twice, restarting after a failure, or calling `stop()` before `start()`.
6. **No unclean-start detection** — `start()` behaved identically regardless of how the previous
   `stop()` went.

## 2. Architectural decisions (as directed)

- **Write-fence/clean-marker lives in `governance_store.py`**, not a separate daemon-lifecycle
  module — it is fundamentally a Governance concern (a trust assertion about the previous
  runtime), and Governance remains this project's single authority for runtime integrity state.
- **Unclean-shutdown recovery is conservative but non-blocking**: detect, log prominently, run a
  lightweight integrity check, repair what's obviously recoverable, continue unless there's actual
  evidence continuing would be unsafe. An unexpected power loss must not permanently prevent
  startup; only real evidence of a problem should.
- **Failed startup goes to a terminal `FAILED` state**, never silently reset to `NOT_STARTED`. A
  retry is expected to be a fresh process with a fresh `KernelDaemon` instance.
- **A Startup Incident Log** (`startup_incidents`, append-only, in `governance_store.py`) records
  every unclean-shutdown detection and every startup failure, as a separate operational audit
  trail from normal runtime logging.

## 3. What was built

### `bartholomew/orchestrator/safety/governance_store.py`

Two new tables:

```sql
CREATE TABLE brake_runtime (         -- singleton row, id=1
    id INTEGER PRIMARY KEY CHECK (id = 1),
    runtime_id TEXT NOT NULL,
    started_at INTEGER NOT NULL,
    clean INTEGER NOT NULL DEFAULT 0,
    write_fence_open INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE startup_incidents (     -- append-only
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    runtime_id TEXT,
    lifecycle_state_reached TEXT NOT NULL,
    exception_type TEXT, exception_message TEXT, traceback TEXT,
    resources_started TEXT NOT NULL,       -- JSON list
    resources_not_started TEXT NOT NULL,   -- JSON list
    previous_shutdown_clean INTEGER,       -- nullable: no prior runtime = NULL
    integrity_checks_performed TEXT NOT NULL,   -- JSON list
    recovery_actions_attempted TEXT NOT NULL,   -- JSON list
    final_outcome TEXT NOT NULL
);
```

New `GovernanceStore` methods: `runtime_marker()`, `previous_shutdown_was_clean()` (`None` for a
first-ever run — nothing to distrust), `open_new_runtime()` (fresh `runtime_id`, fence reopened,
`clean=0` — the honest default until proven otherwise), `close_write_fence()`,
`mark_clean_shutdown()` (deliberately the last Governance write of a clean shutdown, per the
"clean-marker honesty" invariant), `record_startup_incident(...)`. New module-level
`run_quick_integrity_check(db_path)` — `PRAGMA quick_check`, not the much more expensive `PRAGMA
integrity_check`, per the "lightweight" direction.

`_write()` (the shared path for `engage()`/`disengage()`) now refuses with a new
`WriteFenceClosedError` if `brake_runtime.write_fence_open` is `0` — applies uniformly to real
writes and B4's bridge mirror writes alike. A missing `brake_runtime` row (no `KernelDaemon`
lifecycle has opened one against this db_path — standalone tests, B4's bridge fallback instances)
defaults to fence-open, matching this class's existing permissive-default pattern for other
optional machinery.

### `bartholomew/kernel/daemon.py`

New `DaemonLifecycleState` enum: `NOT_STARTED → STARTING → RUNNING → STOPPING → STOPPED`, with
`STARTING → FAILED` as the other branch. `FAILED` is terminal — `start()` refuses to run again on
an instance already past `NOT_STARTED`.

**`start()`**, restructured as one protected region covering *every* resource activated (fixing
findings #1–#2): `mem.init()` → construct `governance_store` → read
`previous_shutdown_was_clean()` → `open_new_runtime()` (in that order — reading must precede the
overwrite) → if the previous shutdown wasn't clean, run `run_quick_integrity_check()`; a failed
check raises the new `UnsafeStartupError` and aborts (evidence continuing would be unsafe); a
passed check runs `db_ctx.wal_checkpoint_truncate()` as the "obviously recoverable" repair (the
WAL cleanup B2's own "deferred to next startup" decision anticipated) → the existing
scheduler-schema/experience-kernel/skills/narrator/producer-task/scheduler-task sequence, now each
step appending to a `resources_started` list as it completes. On any failure: `FAILED`, cancel
every producer task actually created, close both worker-thread resources, then record a Startup
Incident with exactly how far startup got. On success where an unclean shutdown was detected but
recovered from: `RUNNING`, *and* a Startup Incident is still recorded (`final_outcome
="started_after_recovery"`) — the Incident Log's trigger is "unclean shutdown detected OR startup
failed," not just the latter.

**`stop()`**: closes the write fence first (before any other teardown step — freeze before drain),
then the existing snapshot-persist/skill-shutdown/task-cancellation/worker-close sequence, now
tracking whether every producer task was *confirmed* terminal (not just asked to cancel — a
`wait_for` timeout is no longer silently swallowed). `mark_clean_shutdown()` is called only if
every tracked resource (`producer_tasks_terminal and scheduler_drained and blocking_drained`) was
confirmed terminal — an honest marker, not an assumed one. Lifecycle guard: `NOT_STARTED`/`FAILED`/
`STOPPING`/`STOPPED` are safe no-ops (only `RUNNING` has real teardown work); ends at `STOPPED`.

Two direct (not off-loop) calls, both deliberate: `mark_clean_shutdown()` (after
`blocking_executor` is already closed — nothing left to submit to) and `record_startup_incident()`
in the failure path (same reason — the failure path closes the executor *before* recording the
incident, so it can't route through it either). Both are one-off diagnostic/marker writes on rare
paths, the same tradeoff `mem.close()`'s own tail-of-shutdown checkpoint call already made.

## 4. Tests

`tests/test_governance_runtime_lifecycle.py` (15 tests): `brake_runtime`/fence behavior in
isolation — no marker before `open_new_runtime()`, unclean-by-default, clean-marks-persist,
fence blocks writes but not reads, missing-row permissive default, fence idempotency and
reopening; Startup Incident Log field persistence, nullable `previous_shutdown_clean`,
append-only-across-multiple-records; `run_quick_integrity_check()` on a healthy DB and a
just-created-empty one.

`tests/test_daemon_lifecycle_integrity.py` (17 tests): lifecycle-state transitions (fresh →
`NOT_STARTED`, successful start → `RUNNING`, failed start → `FAILED` not `NOT_STARTED`, clean
stop → `STOPPED`); `start()` called twice raises without reinitializing; `start()` after `FAILED`
is refused, not reset (a fresh instance works fine); `stop()` on never-started or failed instances
is a safe no-op; the two regression tests for findings #1–#2 (producer tasks confirmed cancelled
when scheduler-task creation fails; `blocking_executor` confirmed closed when `governance_store`
construction itself fails); clean-shutdown marking and the next runtime seeing it; write fence
blocking a direct `engage()` after `stop()`; a first-ever run recording no incident; the full
unclean-shutdown → integrity-check → WAL-repair → `started_after_recovery` incident path; a failed
integrity check raising `UnsafeStartupError` and recording `final_outcome="failed"` with the
repair step never attempted; an ordinary startup failure's incident recording the exact
resources-started/not-started split.

Two real bugs were caught by this suite before merge, both fixed:
- The incident-recording call in the failure path originally tried to route through
  `blocking_executor` via `run_off_loop()` — but the failure path closes that executor *before*
  recording the incident, so every failure-path incident write itself failed (masked, logged only
  as a secondary error, silently losing the diagnostic record). Fixed by making
  `record_startup_incident()` a direct call, matching the same precedent `mark_clean_shutdown()`
  already established.
- An early version of `test_next_daemon_sees_previous_clean_shutdown` checked
  `previous_shutdown_was_clean()` *after* the second daemon's own `start()` had already run —
  which overwrites the marker via its own `open_new_runtime()` call. Fixed by peeking with a
  separate `GovernanceStore` instance before the second `start()`.

Re-ran the full governance/runtime-contract/scheduler/lifecycle test set (261 tests) and the
complete non-integration/non-slow suite — both clean.

## 5. Exit condition check

- [x] Startup and shutdown sequences verified against the concrete B1–B4 runtime (not a
  hypothetical one) — every test above runs a real `KernelDaemon` against a real SQLite file.
- [x] Tests prove *confirmed* (not assumed) termination: producer-task confirmation replaces the
  previous silent-timeout-swallow; both worker-thread resources' existing bounded `close()` calls
  are honored; the clean marker is gated on all three.
- [x] Expressed as lifecycle-terminal-state conditions B6 can bind process-lock behavior to later
  (`DaemonLifecycleState`, the write fence, the clean marker) — none of this assumes the process
  lock exists.
- [x] Does not claim to cover externally admitted work — the clean marker's scope is explicitly
  the resources B1–B4 introduced, per this stage's own exit condition.

Not required for exit, and not done: the process lock itself, CLI-vs-daemon race safety (B6);
external request admission's contribution to clean-shutdown evidence (B7).
