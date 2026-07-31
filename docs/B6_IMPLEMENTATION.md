# B6 Implementation — External Governance Control and CLI Safety

> Phase B, stage B6. Per `docs/PHASE_B_OVERVIEW.md`'s B6 scope: prevent CLI/maintenance tools from
> racing the running daemon, introduce the process lock bound to B5's lifecycle-terminal-state
> conditions, and — per the safety decision recorded in `docs/B4_IMPLEMENTATION.md` §1 — perform the
> B3→live schema swap that B4 deliberately deferred to land here, together with CLI's migration, so
> daemon reads and CLI writes are never split across two schemas at once.

## 1. A precondition this stage's own exit condition depends on: CLI's db path default

Before touching the schema or the lock, checking `bartholomew/cli.py`'s `brake on/off/status`
commands against `docs/B0_BASELINE_REPORT.md` §1 confirmed the divergence that report flagged as an
open question: the CLI defaulted to `data/bartholomew.db`, a **different filename** than the daemon's
own default (`data/barth.db`, via `_default_db_path()`/`BARTH_DB_PATH`). Left unfixed, this stage's
entire purpose would be structurally unsatisfiable — a process lock and a shared schema are both
meaningless if the CLI is, by default, operating on a file the daemon never touches. `_resolve_brake_db_path()`
(new, in `bartholomew/cli.py`) now resolves the same way the daemon does (`bartholomew.kernel.daemon._default_db_path()`)
unless `--db` is passed explicitly. `embeddings_stats`/`embeddings_rebuild_vss` were left untouched —
unrelated to Governance, out of this stage's actual scope.

## 2. The schema swap

`check_scope_blocked()` (`bartholomew/orchestrator/safety/parking_brake.py`, used by all 6 live-daemon
read sites since B4) and `bartholomew/cli.py`'s `brake_on`/`brake_off`/`brake_status` now both use
`bartholomew.kernel.governance.brake_store.GovernanceBrakeStore` (B3's schema) instead of the legacy
`ParkingBrake`/`BrakeStorage`/`system_flags` path — landing in the same commit, per the B4 safety
decision. The legacy classes themselves are untouched and still exist (still exercised by their own
unit tests, `tests/unit/safety/test_parking_brake.py` and `tests/test_parking_brake_scoped_blocks.py`)
— nothing currently reaches them from a live code path, but per this repository's "deprecate before
deleting duplicates" convention (`DECISIONS.md`), they are not deleted in this stage.

**A real behavior change, not just a backend swap:** `GovernanceBrakeStore.engage()` unions scopes
(B3's fix for the legacy class's replace-based bug) rather than replacing them. `bartholomew brake on
--scope skills` run after `bartholomew brake on --scope global` now keeps `"global"` blocked too,
instead of silently narrowing to only `"skills"` — documented in the CLI's own `--help` text and
pinned by `tests/test_cli_brake_commands.py::test_brake_on_unions_scopes_across_calls`.
`GovernanceBrakeStore.disengage()` requires `confirm=True`; the CLI command itself supplies it —
running `bartholomew brake off` is the explicit, confirmed action B3's invariant requires.

**Audit parity, closed as a side effect:** the legacy `BrakeStorage.append_memory()` audit call was
**always a silent no-op** from the CLI — it requires a `memory_store=` argument none of the 9
construction sites (6 live, 3 CLI) ever provided (confirmed by grep during B4's investigation).
`GovernanceBrakeStore`'s `governance_audit` table is unconditional and atomic with the state write —
every CLI-issued `engage()`/`disengage()` now produces a real, queryable audit row with
`actor="cli"`, pinned by `test_brake_commands_produce_real_audit_rows`.

**Performance trade-off, recorded rather than silently optimized around:** `GovernanceBrakeStore.__init__()`
runs `schema.ensure_schema()` (idempotent `CREATE TABLE IF NOT EXISTS` × 3 + one migration-guard
`SELECT`) on every construction, and `check_scope_blocked()` constructs a fresh store on every call —
so every live Governance check now does this idempotent schema-ensure work, not just a single
`SELECT` the way the legacy `ParkingBrake.__init__()` did. This is deliberate: `check_scope_blocked()`'s
`identity_interpreter/orchestrator/orchestrator.py` caller can run with no `KernelDaemon` ever having
started in-process (the `_kernel is None` fallback in `app.py`), so nothing can safely assume the
schema already exists without ensuring it. No evidence from `docs/B0_BASELINE_REPORT.md` or this
stage's own investigation suggests this is a measured hot-path problem; optimizing it without that
evidence would be premature. Recorded here as a known, deliberate trade-off for a future stage to
revisit with actual profiling data if it ever becomes one.

## 3. Process lock (`bartholomew/kernel/process_lock.py`)

A small `ProcessLock` class: non-blocking exclusive OS lock (`fcntl.flock` on POSIX, `msvcrt.locking`
on Windows) on `"<db_path>.lock"`, one lock file per database (sibling to SQLite's own `-wal`/`-shm`
files). Advisory, not a substitute for SQLite's own WAL-mode concurrency handling (see the module's
own docstring) — it exists for the failure mode SQLite's concurrency model does not cover: two
`KernelDaemon` *processes* independently believing they own the one scheduler loop, one set of
background tasks, one in-process Governance check path.

- `KernelDaemon.start()` acquires the lock as its very first action (before even `mem.init()`). If
  another process already holds it, `start()` raises immediately with a clear message naming the
  owner's PID when known — fail closed, matching this codebase's existing posture — and does **not**
  poison the instance (nothing else was touched yet; a caller retrying later, e.g. once the other
  process has exited, retries against a genuinely untouched instance).
- `_unwind_after_failed_start()` (B5) now also releases the lock, as its last step — a startup
  failure occurring *after* the lock was acquired must not permanently lock out every future attempt
  against that database.
- `stop()` releases the lock as its very last step, after the B5 clean-shutdown marker write — bound
  to B5's lifecycle-terminal-state conditions, so a caller observing the lock as free has a genuine
  guarantee every other resource has already been torn down, not merely that `stop()` was called.

Crash-safety is inherent to the mechanism, not a separate feature: `fcntl`/`msvcrt` locks are held by
the OS against an open file descriptor and are automatically released when the owning process exits
for any reason (including `kill -9`), so an unclean daemon exit cannot leave a database permanently
locked out — the next `start()` attempt (from any process) succeeds.

## 4. Deliberately not write-fenced

The overview's scope lists "write fencing only where repository evidence shows it is necessary."
`brake on`/`brake off` are **deliberately not blocked** by the process lock, even while a daemon holds
it: `GovernanceBrakeStore`'s transitions are already safe under concurrent access (atomic
transactions, revision-tracked ordering — B3), so there is no correctness reason to fence them, and a
real reason not to — `brake on` is frequently used specifically to stop something a *live* daemon is
doing right now; a lock that blocked it would turn an emergency stop into something that could itself
be blocked by the very process it needs to stop. Instead, all three commands report (informationally
only, via `ProcessLock.is_held_by_other()`/`owner_pid()`) whether a live daemon appears to be running
against the target database, so an operator has situational awareness without the write being gated
on it. `brake_status`'s report is read-only by nature and carries no risk either way.

## 5. Windows and POSIX behaviour

`process_lock.py`'s OS-conditional branches (`sys.platform == "win32"` for `msvcrt`, `fcntl`
otherwise) were written but only the POSIX path was exercised directly in this environment (Linux).
`tests/test_process_lock.py::test_lock_is_exclusive_across_real_processes` uses a real
`multiprocessing.Process` (not just multiple in-process instances) specifically because `flock`/
`msvcrt.locking` semantics are per-open-file-description, not per-process — a same-process-only test
would not have caught a bug where two file descriptors in one process fail to conflict correctly. CI's
existing `windows` job (`CI.md`) will be the actual cross-platform proof for the Windows branch, since
this pass could not execute it directly.

## 6. Rerunning B5's lifecycle tests with the lock in place

`tests/test_daemon_lifecycle_b5.py`'s full suite passes unmodified except for one necessary fixture
fix: `test_unclean_previous_run_is_detected_and_logged` simulates a crash by draining a daemon's
executors directly without calling `stop()` — it now also calls `first.process_lock.release()`,
matching what a real crash does automatically (the OS reclaims the lock on process exit). Without that
addition, the test's second daemon would correctly — but for this test's purposes, inconveniently —
be refused start() by the still-held lock, since the test process never actually exited. This is the
direct evidence for the overview's "B5's lifecycle integration tests pass again with the process lock
added" exit condition. `tests/test_daemon_lifecycle_b6.py` adds the process-lock-specific scenarios
B5's own suite didn't cover (second-daemon refusal, refusal-then-retry-after-stop, lock release on a
failed start, lock held-then-released across a real `start()`/`stop()` cycle).

## 7. Verification

New: `tests/test_process_lock.py` (13 tests, including one real cross-process test),
`tests/test_daemon_lifecycle_b6.py` (4 tests), `tests/test_cli_brake_commands.py` (9 tests). Updated
for the schema swap: `tests/test_end_to_end_tasks_and_audit.py`, `tests/test_api_chat_runtime_contract.py`,
`tests/test_voice_sight_runtime_contract_seam.py` (including its `TestMutationNonVacuity` class, whose
`monkeypatch.setattr(ParkingBrake, "is_blocked", ...)` no longer had any effect once `check_scope_blocked()`
stopped calling `ParkingBrake` at all — retargeted to `GovernanceBrakeState.is_blocked`),
`tests/test_scenario_replay.py`. All pass, alongside the full pre-existing suite. `black`/`ruff`/`mypy`
clean (the process-lock module's inline OS-conditional imports needed a top-level move in
`parking_brake.py` to satisfy this repository's `PLC0415` lazy-import lint scoping, since
`bartholomew/orchestrator/safety/**` isn't in `pyproject.toml`'s per-file-ignores list the way
`bartholomew/kernel/**` is).
