# B6 — External Governance control and CLI safety

> **Status:** B6 exit deliverable per `docs/PHASE_B_OVERVIEW.md` §5 and `ROADMAP.md`'s Phase B
> stage table. Retires `bartholomew/cli.py`'s last legacy Governance write path (the three
> `ParkingBrake`/`BrakeStorage` construction sites B4 explicitly deferred here), which in turn
> retires B4's temporary dual-check bridge, and introduces the cross-process lock B5's overview
> anticipated ("B6 can later bind process-lock behaviour to" its lifecycle-terminal-state
> conditions).
>
> **Base facts:** drawn from a fresh re-read of `bartholomew/cli.py`, `governance_bridge.py`, and
> `daemon.py`'s current `start()`/`stop()` at plan start, not assumed from B0/B4/B5's own research.

## 1. Grounded findings that shaped this stage's scope

1. **Exactly three legacy CLI construction sites**, confirmed by direct read: `cli.py`'s
   `brake on`/`brake off`/`brake status` (formerly lines 261, 277, 291) each independently
   construct `ParkingBrake(BrakeStorage(db))` and read/write only the legacy `system_flags`
   value — the CLI-process sites `docs/PHASE_B_RISK_MAP.md` and B4's own docs named as B6's
   territory, not a gap B4 left open.
2. **Exactly four call sites depended on B4's dual-check bridge**
   (`bartholomew.orchestrator.safety.governance_bridge`): `skill_registry.py`'s
   `_is_blocked_by_brake()`, `runtime_contract.py`'s chat and scheduler-drive Governance gates,
   and `identity_interpreter/orchestrator/orchestrator.py`'s `handle_input()`. B4's own docs
   already named this bridge (and `tests/test_governance_bridge_dual_check.py`) as temporary,
   "delete ... in the same B6 change" that retires the CLI's legacy write path.
3. **`brake on`/`brake off`/`brake status` are not a real conflict path for the process lock.**
   `docs/PHASE_B_RISK_MAP.md`'s B6 rows call for "write fencing only where repository evidence
   shows it is necessary" — these commands are the intended mechanism for controlling a *running*
   daemon, not an offline maintenance operation assuming exclusive file access. GovernanceStore's
   own write fence (B5) and revision-guarded `disengage()` (B3) already protect this path; adding
   lock-based exclusion here would make the kill switch unusable against a live daemon.
4. **`embeddings rebuild-vss` is the one real "offline" conflict path**, confirmed by direct read:
   it drops and rebuilds `memory_embeddings_vss` and its triggers wholesale, with no revision
   guarding of its own, assuming exclusive access to the database file.
5. **`bartholomew/kernel/runtime_contract.py`'s sight/voice Governance gates remain legacy
   `ParkingBrake`/`BrakeStorage` calls**, confirmed unaffected: B4's own docs already found these
   paths "still unreachable" (no live caller reaches them) and explicitly deferred their
   consolidation — out of B6's CLI-safety scope, not reopened here.
6. **No process lock or PID-file mechanism existed anywhere in the repository** before this stage
   (confirmed by direct search) — a clean slate, not a migration of an existing mechanism.

## 2. What was built

### `bartholomew/kernel/process_lock.py` (new)

`ProcessLock`: a single-attempt, non-blocking, cross-platform advisory file lock, one per
`db_path` (`<db_path>.lock`, sitting alongside the database file rather than a separate registry
that could itself go stale). POSIX: `fcntl.flock(LOCK_EX | LOCK_NB)`. Windows: `msvcrt.locking()`
with `LK_NBLCK` over a fixed 1-byte region at offset 0, per the risk map's "fixed-byte seeking"
note. `acquire()` raises `ProcessLockHeldError` — an actionable, operator-facing message naming
the db path and lock file, not a bare OS error — on conflict; `release()` is idempotent and safe
to call whether or not the lock was ever held. Context-manager support (`with ProcessLock(path):`)
for the CLI's single-command usage.

This is a plain OS-level mutual-exclusion primitive, deliberately separate from GovernanceStore's
write fence: the fence governs *Governance writes specifically* during one daemon's own
startup/shutdown window; this lock governs *whole-process* exclusivity for operations that assume
they own the database file outright.

### `bartholomew/orchestrator/safety/governance_store.py`

Two new module-level functions, `is_blocked_fail_closed()` / `is_blocked_fail_closed_off_loop()`,
deliberately named and shaped as drop-in replacements for the retired
`governance_bridge` functions of the same names — a plain `refresh()` + `is_blocked()` read, no
dual-check, now that nothing writes the legacy value. The four call sites (§1.2) each changed by a
one-line import swap.

### `bartholomew/orchestrator/safety/governance_bridge.py` — deleted

Along with `tests/test_governance_bridge_dual_check.py` (8 tests), per B4's own docs' explicit
instruction. No functional replacement needed beyond §2's plain read — the bridge's entire reason
to exist was the CLI's legacy write path, which no longer exists after this stage.

### `bartholomew/cli.py`

`brake on`/`brake off`/`brake status` now construct a `GovernanceStore(db)` and call
`engage()`/`disengage()`/`state()` directly, tagging every write's audit `reason` with a
`"CLI: ..."` prefix (audit parity: an operator reading `governance_audit` can now tell a CLI-issued
transition from a runtime-issued one). `WriteFenceClosedError` (an operator ran `brake` during a
daemon's startup/shutdown window) and `StaleGovernanceWriteError` (state changed since it was last
read) are both caught and surface a clear, actionable message and exit code 1, instead of an
unhandled traceback.

`embeddings rebuild-vss` acquires the process lock (§2, `ProcessLock`) immediately after
confirming the database file exists and before opening any SQLite connection to it; a held lock
(daemon running, or another maintenance command in progress) is refused with the same actionable
message, before any write is attempted. The lock is released via `finally`, so it cannot leak even
when the command fails for an unrelated reason (e.g. the `sqlite-vss` extension not being
installed).

### `bartholomew/kernel/daemon.py`

`KernelDaemon.__init__()` constructs a `ProcessLock(db_path)` (cheap — no I/O until `acquire()`,
matching this class's existing pattern for `blocking_executor`). `"process_lock"` is now the first
entry in `_STARTUP_RESOURCE_ORDER`.

**`start()`**: acquires the lock as the very first step inside the protected region, off the event
loop like every other blocking call in this method. A conflict (`ProcessLockHeldError`) is raised
before anything else is touched — `mem.init()` hasn't run yet, so there is nothing to unwind beyond
the lock-release-and-incident-record path every other startup failure already takes. This is both
the daemon's own single-instance guard (a second `KernelDaemon` against the same `db_path` fails
fast rather than racing the first) and the concrete anchor for `docs/PHASE_B_OVERVIEW.md`'s "the
process lock ... bound to the lifecycle-terminal-state conditions B5 established."

**Failure-path unwind**: releases the process lock (off-loop, before `blocking_executor.close()`
so there's still an executor to submit it to) alongside the existing scheduler-store/
blocking-executor cleanup — a no-op if the lock was never successfully acquired
(`ProcessLock.release()`'s own guard), so this is safe to call unconditionally.

**`stop()`**: releases the lock last, after the write-fence close, the clean-marker write, and
everything else — a direct (not off-loop) call, matching the same tail-of-shutdown precedent
`mark_clean_shutdown()` and `mem.close()`'s checkpoint call already set (`blocking_executor` is
already closed by this point).

## 3. Tests

`tests/test_process_lock.py` (10 tests): acquire/release in isolation — held-state tracking,
lock-file creation, a second independently-constructed `ProcessLock` on the same path conflicting
(flock/msvcrt lock semantics bind to the open file description, so two separately-opened file
descriptors conflict even within one test process, a valid same-process proxy for the real
cross-process case), idempotent `release()`, double-`acquire()` on the same instance refused,
context-manager acquire/release including on exception, a lock properly freed by one holder being
cleanly acquirable by the next, and the error message's content.

`tests/test_cli_governance_and_lock.py` (13 tests): `brake on/off/status` against a real
`GovernanceStore(db)` (default scope, explicit scopes, CLI-tagged audit reason, fresh-DB default
state), `WriteFenceClosedError` and `StaleGovernanceWriteError` surfaced as actionable CLI messages
with exit code 1, `rebuild-vss` refused while the lock is held, and the lock provably not leaking
even when the command fails for an unrelated reason.

`tests/test_daemon_lifecycle_integrity.py` (+3 tests, 20 total): a second `KernelDaemon` against
the same `db_path` fails with `ProcessLockHeldError` while the first is `RUNNING`; the lock
releases after a clean `stop()`, letting a second daemon then start; the lock releases after a
failed `start()`'s unwind, letting a second daemon then start. The two existing incident-log tests
asserting an exact `resources_started` set were updated to include `"process_lock"`. Rerun in full
(20 tests) — this is B6's own required re-verification of B5's lifecycle-integration suite "with
the process lock now in place," per this stage's exit condition.

**Pre-existing tests updated as a direct consequence of retiring `governance_bridge.py`** (not new
functionality, but breakage this stage's deletion caused and this stage fixes): three tests
(`tests/test_api_chat_runtime_contract.py::test_chat_returns_503_when_parking_brake_engaged`,
`tests/test_end_to_end_tasks_and_audit.py::test_parking_brake_blocks_then_disengage_allows`,
`tests/test_scenario_replay.py::test_full_multi_turn_session_coheres`) constructed a standalone
legacy `ParkingBrake(BrakeStorage(db))` to simulate an operator engaging the brake, relying on the
now-deleted bridge to make that visible to the runtime's Governance check. Each now engages
`GovernanceStore` directly (the live daemon's own shared instance where one exists, a fresh
instance against the same `db_path` where it doesn't) — the same shape `cli.py`'s own migration
took.

Full non-integration/non-slow suite re-run clean after this change.

## 4. Exit condition check

- [x] CLI/maintenance operations cannot silently race a running daemon: `embeddings rebuild-vss`
  (the one real offline-conflict operation, per §1 finding 4) is lock-gated; `brake on/off/status`
  are protected by GovernanceStore's own write fence and revision guarding, the correct layer for
  operations designed to control a live daemon (§1 finding 3).
- [x] Proven on both POSIX and Windows: `ProcessLock` branches on `sys.platform`, using each
  platform's real locking primitive (not a POSIX-only mechanism with a Windows no-op); the existing
  Windows CI leg (`ci.yml`) runs this stage's test files unmodified.
- [x] B5's lifecycle integration tests pass again with the process lock now in place: rerun in
  full (20 tests, §3), plus 3 new lock-specific regression tests added to the same file.
- [x] Audit parity: CLI-issued Governance writes are tagged (`"CLI: ..."`) in `governance_audit`,
  distinguishable from runtime-issued ones.
- [x] Operational failure messages: both `ProcessLockHeldError` and the CLI's
  `WriteFenceClosedError`/`StaleGovernanceWriteError` handling produce actionable text, not bare
  tracebacks.

Not required for exit, and not done: sight/voice Governance-gate consolidation (§1 finding 5,
already confirmed unreachable and explicitly deferred by B4, not reopened here); external request
admission and its inclusion in clean-shutdown evidence (B7).
