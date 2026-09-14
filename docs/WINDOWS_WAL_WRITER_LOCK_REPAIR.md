# Windows Writer-Lock / WAL Reliability Repair

> **Status:** Work-package record (2026-09-14; **PR #110, not merged — awaiting Taylor's User
> Approval Gate**), step 2 of the sequence Taylor approved at the User Approval Gate
> (`DECISIONS.md`, "The approved sequence"). **Non-canonical** — a document
> under `docs/`; `RISKS.md` carries the risk disposition, `DECISIONS.md` the rule this repair
> establishes, and `START_HERE.md` the current-state snapshot. Where this note and a canonical
> document disagree, the canonical document wins.
>
> **Scope, as approved:** this reliability class only. Not general database cleanup, not
> unrelated refactoring, not EXEC-02, not Band 0. Findings outside the class are recorded in §8
> and left unabsorbed.

## 0. The defect, in one paragraph

Bartholomew's `MemoryStore` writes through **aiosqlite**, which runs each call on a worker
thread and returns the result through the event loop. A write is therefore two round trips:
`await db.execute("INSERT …")` — after which the worker thread holds SQLite's single write
lock — and `await db.commit()`, which releases it. Between those two awaits the lock is held
by a thread whose *only* way to release it is for the event loop to run the coroutine one
more step. Every scheduler drive records a Reflection this way, several times a minute. A
**synchronous `sqlite3` write made on the event-loop thread** inside that window blocks the
loop while it waits for the lock; the holder cannot commit, because committing needs the
loop that is blocked; so the writer waits its whole `busy_timeout` (5 s, set by
`db_ctx.set_wal_pragmas`) and fails with `database is locked`. Once the window is hit the
failure is certain, not probabilistic, which is why the observed failures are always exactly
one `busy_timeout` long. On a loaded Windows runner (four xdist workers, slower file and thread
operations) the window is hit often; nothing about the mechanism is Windows-specific.

The repair is the discipline the repository already adopted in Phase B stage B2
(`docs/B2_EVENT_LOOP_ISOLATION.md`): **a statement that can wait for the write lock never runs
on the event-loop thread.** The production paths that still did are moved off the loop through
the existing `run_off_loop()` primitive. Off the loop, the same write waits the few milliseconds
until the commit lands.

A second, independent defect of the same class was found and repaired while establishing the
first: two connections racing to convert a **fresh** database file to WAL mode, where SQLite
refuses to invoke the busy handler for the loser (§3).

## 1. How the diagnosis was established

Nothing here inherits the 2026-09-09 hypothesis in `RISKS.md`; it was re-derived and then
either confirmed or refuted by direct evidence.

1. **Windows CI log, `main` at `a64f5af` (Merge Candidate run 34775856466).** The FND-04
   vertical-slice failure's captured log shows the causal chain verbatim:
   `eci/store.py:record_directive` → `sqlite3.OperationalError: database is locked`, raised
   from `eci_responder.py:speak_fn` inside
   `runtime_contract.py:run_spoken_output_through_runtime_contract` — a synchronous write on
   the event-loop thread — while the same test's captured stdout shows the scheduler's
   `self_check` drive in flight with `dur_ms=5000`: a drive that was mid-Reflection-write for
   exactly the writer's `busy_timeout`. The test then failed on `'eci.result' not in
   ['eci.request']` because no directive was issued, so no result could be reported.
2. **Deterministic reproduction on Linux** (`scratchpad/convoy_repro.py`, now
   `tests/test_sqlite_event_loop_convoy.py::test_the_mechanism…`): an aiosqlite connection
   with an executed-but-uncommitted `INSERT`, then the repository's own `wal_db()` writer on
   the loop thread with `busy_timeout = 1000` — **fails after 1.00 s, every time**; the same
   writer through `asyncio.to_thread` with the loop free — **succeeds in 54 ms**.
3. **Suite-wide enumeration** of every SQLite statement that can wait for a lock and that
   executed on a thread running an event loop, by running the whole default suite under an
   instrumented `sqlite3.connect` (the detector now lives in
   `tests/helpers/event_loop_sqlite.py`). 905 distinct call chains; 504 after excluding
   `PRAGMA journal_mode` (a read on an already-WAL database). Classified by outermost
   production frame into: writers reachable **during operation** concurrently with the
   scheduler's aiosqlite Reflection writes (the defect class, §2); writers that run only at
   daemon construction, start or stop, when no aiosqlite transaction can be in flight (out of
   scope, §8); writers on the **platform** database, a separate file no aiosqlite connection
   ever opens, which therefore cannot convoy (§8); and stores that tests call directly on the
   loop, which is the test's business rather than the product's.
4. **Reproduction of each repaired production path** against a real in-flight `MemoryStore`
   transaction, before and after the repair (§4).

## 2. What was repaired, and why each change is the smallest correct one

Every change routes an existing synchronous `sqlite3` write through the existing
`bartholomew.kernel.blocking_executor.run_off_loop()`. No timeout was changed, no retry was
added to a write, no test was skipped, quarantined or loosened, WAL stays on, and the
`busy_timeout` decision of 2026-08-22 is untouched.

| Path (during operation) | Writer that ran on the loop | Change |
|---|---|---|
| Skill actions: `notify` (`set_quiet_hours`, `mute`, `unmute`, `send`, `queue`, `cancel`, queue processing, the lazy mute-expiry clear), `tasks` (`create`, `complete`, `update`, `delete`), `calendar_draft` (`create`, `update`, `delete`), and each skill's `initialize()` schema/settings writes | `_save_settings`, `_save_notification`, `_save_task`, `_delete_task`, `_save_event`, `_delete_event`, `_init_database`, `_load_settings` | Each call site awaits the new `SkillBase._run_off_loop()`; `NotifySkill._is_muted()` is now awaitable because its expiry clear persists |
| Every skill's own permission self-check (`SkillBase._require_permission`, 19 call sites across four skills) | `PermissionChecker.check()` → `_log_audit` (the required `permission_audit` row) and `_check_db_grant` | `_require_permission` is now `async`, runs the check off the loop **under a copy of the task's context** so WP-A2's `ContextVar` collector of failed audit writes still receives a failure recorded on the worker thread |
| The registry's own pre-action gate, `SkillRegistry._resolve_permissions` | `PermissionChecker.check()` / `grant_session()` | New `_permission_call()` helper, same context-preserving off-loop pattern; the registry now hands its shared `SingleWorkerExecutor` to skills through `SkillContext.blocking_executor` so skill writes join the daemon's confirmed drain at shutdown |
| The `fts_optimize` scheduler drive | `FTSClient.optimize()` | `run_off_loop(fts.optimize, executor=ctx.blocking_executor)` |
| The External Capability Interface: `boundary.submit()` (schema), `boundary._settle()` (result correlation), `DirectiveIssuer.issue()` reached through the production responder's `speak_fn`, and the `/api/eci/availability` route | `eci/store.py`'s `ensure_schema`, `settle_directive`, `record_directive`, `record_availability` | Off-loop at each async seam; `speak_fn` is now a coroutine (the spoken-output seam already awaited an awaitable result) |

**Independent defect, same class — the fresh-file WAL conversion race (`db_ctx.set_wal_pragmas`).**
On a database that is not yet in WAL mode, `PRAGMA journal_mode = WAL` rewrites the header by
escalating its own read lock to a write lock. When two connections race to convert the same
fresh file, SQLite deliberately does **not** invoke the busy handler for the loser of that
escalation (its documented deadlock-avoidance rule) and returns `SQLITE_BUSY` at once, whatever
`busy_timeout` or the connection's `timeout=` says — so `database is locked` is raised on the
very first pragma of a brand-new database. Reproduced through the repository's own `wal_db()`:
two spawned processes failed on **19 of 40** barrier-synchronised attempts on Linux; two threads
on **9 of 60**. This is the mechanism behind
`tests/test_sqlite_wal_concurrent_processes.py`'s intermittent `Worker 0 failed: database is
locked` recorded on `main` since 2026-08-15. `set_wal_pragmas()` now retries that one pragma
for up to five seconds with a short backoff — the winner's conversion takes milliseconds, after
which the pragma is a read. Nothing else about the connection authority changed. **0 of 60**
attempts fail after the repair.

## 3. Why this is the defect and not a symptom

- The failure signature is *exactly* one `busy_timeout` long, in every observation: the RISKS
  probe of 2026-09-09 (three writers each waiting exactly 5 s), the `dur_ms=5000` tick in the
  FND-04 log, the 1.00 s failure in the deterministic reproduction. Ordinary lock contention
  produces a distribution of waits; a convoy produces the timeout value.
- The 2026-08-27 scheduler pacing (`DRIVE_PACE_S`) did not change the measured rate — as that
  entry recorded. Pacing spreads writes; it cannot help a writer that is blocking the only
  thread able to release the lock it wants.
- The repair removes the *dependency* (a loop-thread wait on a loop-dependent holder), not the
  contention. The same contention still occurs; it now resolves in milliseconds, which is what
  the "repaired" reproductions measure.
- Widening `busy_timeout` would have lengthened the stall, not removed it; disabling WAL would
  not have changed the two-round-trip aiosqlite transaction at all.

## 4. Regression protection

| Test | What it detects |
|---|---|
| `tests/test_sqlite_event_loop_convoy.py::test_the_mechanism…` | The convoy itself, executable: a loop-thread writer through `wal_db()` fails after its `busy_timeout` against an in-flight aiosqlite commit; the same writer off the loop succeeds. |
| `…::test_*_completes_while_a_memory_write_is_in_flight` (notify quiet-hours; notify send + mute; tasks create; calendar create; a skill's permission-audit write; ECI directive issue; the `fts_optimize` drive) | Each repaired production entry point, run against a real held `MemoryStore` transaction that only the loop can release. **On `main` at `57f86f8` all of them fail** — `database is locked`, "no directive was issued", "took 5.33 s" (verified by running the new tests against an unmodified worktree of `main`). |
| `…::test_the_repaired_paths_write_nothing_to_sqlite_on_the_event_loop_thread` | The structural invariant over every repaired path, with no lock holder: no lock-waiting statement executes on a loop thread from production code. Guards against the defect returning one call site at a time. |
| `tests/test_fnd04_eci_vertical_slice.py::TestTheLoopStaysOffTheEventLoop` | The same invariant through the real app, the real `/api/eci` routes and the production responder — the exact path that failed in CI. |
| `tests/test_sqlite_wal_concurrent_processes.py::test_two_connections_converting_a_fresh_database_to_wal_both_succeed` | The fresh-file conversion race, 40 barrier-synchronised attempts. **Fails on `main`** (`fresh-database WAL conversion lost the race`). |
| `tests/helpers/event_loop_sqlite.py` | The detector the two invariant tests use, available to any future suite. |

### `tests/test_event_backbone_drive.py` — reclassified, and its two failing tests corrected

The two members of this file named in `RISKS.md` as writer-lock failures are not: the
`a64f5af` log shows no lock error in either. Both tests waited for the **first** durable
effect of a processing pass and then asserted a **later** one without waiting for it: the
handler attaches the evidence, a separate transaction settles the processing row to
`processed`, and the scheduler records the drive's tick only after the drive returns. A poll
returning on the evidence can read the processing row a few milliseconds before settle lands
(`assert 'claimed' == 'processed'`); a poll returning on the terminal state can read `ticks`
before the tick is written (`the scheduler recorded no tick`). Injecting a 0.5 s delay
between effect and record makes the **unmodified** tests fail on every run, with exactly the
CI messages; the corrected tests, which wait for the state each assertion is about, pass under
the same injected delay and in repeated plain runs. The assertions are unchanged; only the
wait condition now matches what the design guarantees.

## 5. Validation

Linux (this session, `scratchpad` virtualenv, Python 3.11.15, SQLite 3.45.1, aiosqlite 0.22.1):

- Focused suites for every touched area: green once the two callers of `notify._is_muted()`
  awaited the now-async method (§4); the full-suite run in §5.1 supersedes them.
- New tests against unmodified `main`: 9 failures, as designed (§4).
- `tests/test_event_backbone_drive.py`: passes under the injected delay and in three plain runs.

### 5.1 Linux full default suite

Code head `ac8eea9` (the later `5fb9c88` changes only the two Windows CI invocations, §5.2):

    venv/bin/python -m pytest -p no:cacheprovider -n auto --dist loadfile

5080 outcomes to 100 %, no `F` or `E` in the progress record, exit status 0 — the same invocation
shape as the Windows Merge Candidate job. Python 3.11.15, SQLite 3.45.1, aiosqlite 0.22.1,
pytest 9.1.1. The new tests of §4 were run 5 × 19 on the repaired tree (all pass) and once against
unmodified `main` in a worktree (9 failures, as designed).

### 5.2 Windows Merge Candidate runs

`workflow_dispatch` of `merge-candidate.yml`; the Windows job runs the whole default suite with
`-n auto --dist loadfile` under a 40-minute cap.

| Run (UTC) | Head | Windows full default suite | Other six jobs |
|---|---|---|---|
| 34850155554 (13:35) | `57f86f8`, unmodified `main` | **cancelled at the cap**: `[gw2] node down: Not properly terminated` at ~65 %, 99 % reached at 13:56, nothing further, junit never written | all green |
| 34857413080 (14:43) | `ac8eea9`, this branch | **cancelled at the cap**: `[gw0] node down: Not properly terminated` at ~67 % (14:54), 99 % reached at 15:02, seven more tests by the 15:23 cap, junit never written | all green |
| 34866265459 (16:04) | `5fb9c88`, this branch, `-vv` | _in flight when this was written_ | |

What the two completed runs establish:

- **No writer-lock failure appears in either Windows log.** The `database is locked` signature that
  named this class (§0) is absent from both; on this branch the repaired paths ran under `-n auto`
  contention without it.
- **The "stalled tail" is not this class and not this repair's effect.** It reproduces with the same
  shape on unmodified `main` and on the repaired branch: one worker dies silently mid-run
  (pytest-timeout's thread method ends a worker with `os._exit`, so a 120 s test timeout inside an
  xdist worker leaves only `node down`), the suite reaches 99 %, and the last few dozen tests never
  complete while no further timeout fires. The dotted output cannot name those tests; `5fb9c88` runs
  both Windows jobs `-vv` so the next occurrence can be attributed to a worker and a test id.
- **The Windows baseline is therefore not yet trustworthy on this evidence**, for a reason
  independent of the writer lock. §8 item 1 carries it; §9 draws the consequence.

### 5.3 Nightly Windows serial runs (context, not the baseline)

`nightly.yml` "Windows, every marker" runs the whole collection serially, integration and slow
included, under a 120-minute cap. On `main` (run 34825458349, `a64f5af`, 08:58) both jobs complete
red — py3.11: `12 failed, 5305 passed, 80 skipped, 11 errors in 1:39:26`. On this branch (run
34857416478, `ac8eea9`, 14:43) both jobs were instead **ended by the 120 s per-test timeout**:
py3.11 at 35 %, inside `tests/test_memory_agency_review_fixes.py::test_filtered_pagination_addresses_the_filtered_set`
(a `slow` test: 900 upserts through `MemoryStore`, which opens a new aiosqlite connection and worker
thread per operation — the dump shows that thread inside `sqlite3.connect`; the test takes 10.6 s on
this Linux machine); py3.12 at 60 %, with `scheduler-db_0` inside `insert_tick`'s `conn.commit()`.
Neither stack is in a repaired path and neither is a lock wait (`busy_timeout` raises within 3–5 s;
these waited 120 s). Whether this is runner I/O slowness on a `slow` test or a real difference is
being measured by same-time runs of `nightly.yml` on this branch (34866268731, `-vv`) and on `main`
(34866271890, control).

## 6. FND-04 classification

**A. SAME ROOT CAUSE — supported by evidence.** The failing assertion
(`'eci.result' not in ['eci.request']`) is the *consequence*; the *cause* is in the same test's
captured log: `record_directive` on the event-loop thread failing with `database is locked`
after its full `busy_timeout`, while a scheduler drive's Reflection write was in flight
(`self_check … dur_ms=5000`). No directive was issued, so the reference endpoint had no result
to report, so the second `inbound_events` row never existed. That is the convoy of §0 on the
ECI's own writer, and the ECI writers are repaired and covered by the invariant test through
the real routes. The classification is from the log evidence, not from co-occurrence in the same
Windows run.

## 7. The Ubuntu critical-integration timeout

**NO CAUSAL RELATIONSHIP ESTABLISHED.** The cancelled PR #109 job (run 34842391085, attempt 2)
shows the 25-minute cap consumed by the two WP-A1 soak tests: 56 % reached at 12:52, the first
soak's progress line at 13:00:32 after ~8 minutes, the second's at 13:05:53, then no output until
the cancellation at 13:10:23. Those soaks are CPU-bound simulated-clock loops (17,663 iterations),
and the identical code passed in 16m13s on the next head. No lock error and no `busy_timeout`-shaped
stall appears in the log. The variance is consistent with runner performance, not with this
defect; it is preserved as a separate reliability concern.

## 8. Findings outside this package (recorded, not absorbed)

1. **Windows Merge Candidate "stalled tail".** The push of `57f86f8` (unmodified `main`, run
   34850155554) reached 99 % of the Windows default suite at 13:56 and then produced no output
   until the 40-minute cap at 14:15; earlier in the same run one xdist worker died
   (`[gw2] node down: Not properly terminated`). This is a hang, not a lock error, and it is what
   `RISKS.md` records as the job-budget symptom. **It recurred on this branch** (§5.2, run
   34857413080) with the same shape, so it is independent of this repair and of the writer lock.
   The dotted output cannot name the tests involved; `5fb9c88` runs the Windows jobs `-vv` so the
   next occurrence can be attributed. Not this class; not absorbed.
2. **Nightly serial Windows job** (`nightly.yml`, run 34825458349 on `a64f5af`): 12 failed,
   11 errors in 1h39m, including at least one cp1252 console-encoding failure in a CLI test.
   On this branch the same jobs were ended earlier by a per-test timeout (§5.3). Separate tier, not
   the Merge Candidate baseline; not classified here beyond §5.3.
3. **Cancellation swallowed by `asyncio.wait_for` (Python 3.11).**
   `run_drive_through_runtime_contract` awaits `asyncio.wait_for(drive_fn(ctx), timeout)`; in
   Python 3.11 `wait_for` returns the inner result instead of raising `CancelledError` when the
   inner future completes in the same loop iteration as the cancellation. The `a64f5af` log's
   second backbone-drive failure shows it: twelve more one-second ticks *after* the fixture's
   `task.cancel()`, until its 10 s `wait_for` timed out and cancelled again. Cost: up to the
   waiter's timeout of extra shutdown latency (5 s in `KernelDaemon.stop()`), never a lost write.
   Not this class.
4. **Operator self-state routes** (`routes/self_state.py`: `update_affect`, `set_attention`,
   `activate_drive`, `satisfy_drive`, `add_goal`, `complete_goal`) reach `narrator.py`'s
   episode writes synchronously through `global_workspace.publish` on the loop thread. Same
   shape as this class, operator-only, not observed failing, and offloading them would move
   `ExperienceKernel`'s in-memory mutations onto a worker thread — a separate decision.
5. **Platform-database writes on the loop** (`endpoint_auth.resolve` stamping `last_seen_at`,
   `auth.py` login/logout sessions, `device_inbound.resolve`). A separate file that aiosqlite never
   opens, so they cannot convoy; they block the loop only for ordinary contention. Recorded for
   completeness.
6. **Construction/start/stop-time writes on the loop** (`daemon.__init__`'s component schema
   scripts, skill `initialize()` before this repair, `_record_startup_incident`,
   `mark_clean_shutdown`, `MemoryStore.close()`'s TRUNCATE checkpoint). No aiosqlite transaction
   can be in flight at those points, so they are not members of this class; the skill
   `initialize()` writes were moved anyway because the same helpers serve runtime actions.
7. **`MemoryStore` opens a connection, and an aiosqlite worker thread, per operation.**
   `bartholomew/kernel/memory_store.py` uses `async with aiosqlite.connect(self.db_path)` at every
   method (twenty sites); the nightly dump names that worker `Thread-14051` at 35 % of the run.
   Each open pays file open, WAL pragma and shm mapping, which is where Windows is slowest. Not a
   correctness defect and not this class; it sets the Windows cost of the `slow` memory tests
   (§5.3).

## 9. Band 0 readiness

**NOT READY** on the evidence to date (2026-09-14; PR #110 open, not merged).

- The writer-lock class is root-caused, repaired and protected (§§0–4), and the Linux suite is green
  on the repaired head (§5.1). That part of the pre-Band-0 requirement is met.
- The Windows Merge Candidate baseline is not yet trustworthy, for a reason outside this class: the
  "stalled tail" (§8 item 1) ended both Windows full-suite runs today, on unmodified `main` and on
  this branch alike. A baseline that cannot finish cannot be called green, and Band 0 was gated on a
  trustworthy Windows baseline.
- The runs dispatched at 16:04 (§5.2 row 3, §5.3) are the next evidence; this section is updated
  from them.
