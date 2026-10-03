# SQLite connection-lifecycle repair — the record

**Package:** SQLite connection-lifecycle foundation repair.
**Charter:** `DECISIONS.md`, "SQLite connection-lifetime repair is a separate package, scoped to
reuse and never a process-wide pool" (2026-09-17).
**Status:** implemented; awaiting Taylor's User Approval Gate at the merge checkpoint.
**Preceding record:** `docs/WINDOWS_TEST_EXECUTION_CONTRACT.md` (PR #112, merged as `9ca2d4a`).

---

## 1. What the previous session claimed, and what was verified

| Claim (from `RISKS.md` / `DECISIONS.md`, 2026-09-17) | Verified? | Evidence |
| --- | --- | --- |
| PR #112 merged cleanly | Yes | `origin/main` head `9ca2d4a` is the merge commit for `claude/windows-runtime-execution-bnpcw0`. |
| Gate 2 / Band 0 blocked solely by the SQLite connection lifecycle | Consistent with the record; not independently re-measured on Windows | `RISKS.md` 2026-09-17 amendment; no separate blocker found in this package's scope. |
| Heavy-burst test ~108 s against a 120 s per-test timeout | **Confirmed on Windows, and now repaired** | §4a: the base branch `9ca2d4a` lost a worker to it the same day (run 35301649186); on this branch it leaves the cost table entirely (run 35311115266). |
| ~98 % of the test is the per-operation connection lifecycle, dominated by `close` at ~70 ms | **Confirmed in structure, and the mechanism identified** | §2. |
| The remedy is scoped reuse, never a process-wide pool | Adopted unchanged | §3. |
| `RISKS.md` still says PR #112 is awaiting Taylor's gate | **Confirmed stale, corrected** | §6. |

The one place the previous diagnosis was incomplete rather than wrong: it named `close` as the
dominant term but not *why* a close costs 70 ms. That mattered, because "closing a handle is slow
on Windows" and "closing the last handle runs a full WAL checkpoint and unlinks two files" lead to
different repairs. It is the second.

## 2. Verified root cause

Every persistence helper in this repository has the same shape: it takes a `db_path`, opens a
connection through `db_ctx.wal_db()`, does one small thing, and closes. **The ownership boundary
is the individual statement, not the unit of work.** Between calls there is therefore no
connection to the database at all, which means every `wal_db()` exit is the *last* close.

The last close of a WAL database is not a handle release. SQLite checkpoints the entire WAL into
the main database file and then unlinks the `-wal` and `-shm` files; the next call has to recreate
them, re-establish the shared-memory index, and re-fsync. That is the 70 ms — and the commits are
expensive for the same reason, because each commit is writing into a WAL file that was created
moments earlier.

Measured on the Linux developer runner, 300 single-row writes through `wal_db()`, milliseconds per
operation:

| | connect | commit | close |
| --- | --- | --- | --- |
| sole connection (today's behaviour) | 0.407 | 1.199 | 1.150 |
| another connection open on the same file | 0.156 | 0.021 | 0.022 |

Same code, same work, same statements. The only difference is that closing is no longer the last
close, so no checkpoint-and-unlink happens — and commit and close both collapse by roughly 50x.
This reproduces the recorded Windows profile exactly in shape (`close` dominant, `connect` small,
`commit` second), at Linux's much lower file-operation cost.

End-to-end, the containment burst (1000 emissions through `insert_nudge_contained`):

| | wall time |
| --- | --- |
| unscoped (per-operation connections) | **4.54 s** |
| inside one `db_session()` | **0.105 s** |

## 3. Architecture before → after

**Before.** One connection per persistence call. Correct, and it satisfies the handle-release
contract that `tests/test_vector_store_handle_lifetime.py` and `tests/test_sqlite_wal_cleanup.py`
exist to defend — but it makes a unit of work that touches the database *n* times pay *n* full WAL
teardowns and rebuilds.

**After.** The same default, plus an explicit, bounded scope a caller can declare when it is about
to do several operations as one unit of work:

```python
with db_ctx.db_session(db_path, label="tick:self_check"):
    persistence.insert_nudge_contained(db_path, ...)
    persistence.insert_tick(db_path, ...)
    persistence.update_next_run(db_path, ...)
```

Inside the scope, `wal_db()` borrows the session's connection. Outside it, nothing changes at all.

### The lifecycle contract after the change

1. **A scope owns exactly one connection, for exactly the scope.** It is closed in `finally`, on
   the success path and the exception path alike. Nothing survives the `with`.
2. **Handles are released at scope exit, not at process exit.** The Windows handle-release
   contract is preserved: the number of release points drops, the guarantee does not.
3. **It is not a pool.** Nothing is cached; leaving and re-entering a scope opens a new
   connection; there is no module-level registry of live connections. Pinned by
   `TestItIsAScopeNotAPool`.
4. **The binding is thread-local, never a `ContextVar`.** A `sqlite3.Connection` belongs to the
   thread allowed to use it; a context variable would follow an `await` onto an executor thread
   and hand it a connection it must not touch. A second thread inside someone else's scope opens
   its own connection as usual.
5. **Transaction semantics are unchanged.** A borrowed `wal_db()` rolls back any transaction still
   open when the call returns — which is precisely what closing its own connection used to do. A
   caller that writes and does not commit still does not persist, and a caller that raises
   mid-write cannot have its partial work committed by the *next* borrower.
6. **No lock is held while the scope is idle.** Between operations the connection has no open
   transaction, so a long scope does not block another writer.
7. **Scopes nest.** An inner scope for the same database on the same thread reuses the outer
   connection; the outermost scope owns the close.
8. **Scope identity is the database file.** Keyed on `realpath`, so the same file named two ways
   shares one scope; URIs and `:memory:` are keyed verbatim, because their string *is* what
   determines which database is opened.

### Where the scope is used

- **`SchedulerStore.unit_of_work()`** — the production consumer. `SchedulerStore` already runs
  every persistence call on one dedicated worker thread, so a scope entered on that thread covers
  all of them. It is entered and left on that worker, and the exit is shielded so a cancellation
  delivered to the caller cannot leave the worker holding a connection.
- **`scheduler/loop.py`** — one executed tick is one unit of work. The nudge write, the tick
  record and the next-run update share one connection instead of three. The scope is the tick, and
  deliberately does not span the drive execution or the idle sleep.
- **`tests/test_scheduler_queue_containment.py`** — the four heavy emission bursts declare
  themselves as one unit of work. No assertion changed; every assertion still reads the database
  through its own independent connection.

### What was deliberately not done

- No process-wide pool, and no permanently open connection.
- No change to `busy_timeout`, WAL mode, `synchronous`, or any timeout anywhere.
- No test skipped, xfailed, relaxed or special-cased; no per-test timeout raised.
- No second database architecture: `wal_db()` remains the single choke point every store already
  goes through, so `MemoryStore`, the vector store, the executive, actuation, ECI, inbound and
  event-processing stores all become scope-capable without touching their code.

## 4. Evidence

- `tests/test_db_session_lifecycle.py` — 34 deterministic tests over eight properties: reuse
  inside the scope, release at scope end (including a `/proc/self/fd` handle enumeration with the
  cyclic collector disabled, the same probe the vector-store handle-lifetime test uses),
  unchanged transaction semantics, nesting, thread confinement, scope-not-pool, the mechanism
  (one connection per burst; the WAL file's inode unchanged across a scope), and the
  `SchedulerStore` unit of work.
- The unscoped path is asserted alongside each scoped assertion, so the comparison is a comparison.
- Regression: `tests/test_vector_store_handle_lifetime.py`, `tests/test_sqlite_wal.py`,
  `test_sqlite_wal_cleanup.py`, `tests/test_sqlite_event_loop_convoy.py`,
  `tests/test_sqlite_wal_concurrent_processes.py`, `tests/test_no_raw_sqlite_connect_in_api.py`,
  `tests/test_fts_single_writer_architecture.py`, `tests/test_clean_start_lifecycle.py`,
  `tests/test_daemon_lifecycle_integrity.py` — 64 passed. Scheduler suites
  (`test_scheduler_queue_containment`, `test_scheduler_persistence_concurrency`,
  `test_scheduler_startup_readiness`, `test_scheduler_queue_soak`) — 49 passed. Full `tests/` run
  recorded in the pull request.
- Heavy-burst containment test on Linux: **2.23 s → 0.20 s**.

### 4a. Windows evidence (the decisive measurement)

Merge Candidate run **35311115266**, head `6750227`, "Windows full default suite + actuation
(py3.11)": **16:30 wall, 5098 passed, 1 failed, 82 skipped.** No worker lost. No stall recorded.
All four workers reached `session_finish=yes process_exit=yes`.

`test_a_heavy_system_generated_burst_leaves_every_genuine_row_untouched` **does not appear in
that run's slowest-reports table or in its SQLite cost table at all** — both are top-15 lists
whose last entry is 1.3 s. Its recorded profile before this package was:

```
1036 opens | 0.4s connect | 33.8s commit | 72.3s close | 108.4s wall
```

The control is same-day and on the base branch: the Merge Candidate for `main` at `9ca2d4a`
(run **35301649186**, 03:22 the same morning, the post-#112 push) failed with

```
WORKER LOST  gw3  died on tests/test_scheduler_queue_containment.py::
  TestContainmentNeverDestroysAnObligation::
  test_a_heavy_system_generated_burst_leaves_every_genuine_row_untouched
```

— the blocker exactly, on the same runner image and the same execution machinery. The comparison
is therefore direct, and the one thing that changed between them is this package.

**Three Windows Merge Candidate runs on this branch, against one on `main`:**

| Run | Head | Result |
| --- | --- | --- |
| 35301649186 | `main` @ `9ca2d4a` (control, same morning) | **red** — `WORKER LOST gw3` on the heavy-burst test |
| 35311115266 | `6750227` | 5098 passed, 1 failed (pre-existing device-consent TTL race, §below); burst test absent from the cost table |
| 35312520515 | `350479e` | **fully green, all seven jobs** |
| 35319175282 | `091567c` | **fully green, all seven jobs** |

Two consecutive fully green Windows Merge Candidates, and the one run that was
not green failed on a pre-existing class this package does not touch. The record
before this package, on the windows-runtime branch, was two green and three red
across four heads with **every red the heavy-burst worker kill**.

**A second negative control, unplanned and therefore worth more than the first.**
PR #114 (`claude/event-lease-truncation-race`) is cut from `main` and deliberately
does not carry this package. Its Windows Merge Candidate the same morning
(run 35319767756, head `81d0128`) ended with

```
WORKER LOST  gw3  died on tests/test_scheduler_queue_containment.py::
  TestContainmentNeverDestroysAnObligation::
  test_a_heavy_system_generated_burst_leaves_every_genuine_row_untouched
```

and its cost table shows all three containment bursts back at the per-operation
profile:

```
opens  connect  commits   commit    close     wall  nodeid
 1037     0.2s     1032    15.1s    31.6s    54.3s  ...heavy_system_generated_burst...
  812     0.2s      805     7.5s    15.0s    23.7s  ...open_obligations_are_bit_for_bit_unchanged
  303     0.1s      301     3.1s     5.8s     9.5s  ...capacity_is_bounded_by_identity...
```

On this branch, none of those three appears in that table at all. Same morning,
same runner image, same execution machinery; the difference is this package.
Two independent branches off `main` now show the blocker, and every run carrying
the repair does not.

**Second Windows run, 35312520515, head `350479e`: fully green — all seven jobs.** The Windows
suite's own trace closes with `nothing: no worker crashed and no phase reported a bad outcome`,
and the heavy-burst test is again absent from the SQLite cost table (last entry 1.1 s). The
device-consent failure below did not recur, which confirms it as the intermittent timing race it
is described as rather than a deterministic break.

**The one failure in run 35311115266 is a different, pre-existing class**:
`tests/test_device_consent_channel.py::test_a_late_answer_after_expiry_cannot_resurrect_the_start`
("the ask never appeared"). It sets `ttl_seconds=1` and polls for the ask to be *pending*; the
run's own trace shows it at `253 opens | 3 commits | 4.0s commit | 14.3s wall`, so one commit took
seconds and the ask expired before it was observable. `docs/WINDOWS_WAL_WRITER_LOCK_REPAIR.md`
already names this file, with `test_event_backbone_processing.py::
test_a_crash_after_claiming_loses_nothing_and_duplicates_nothing`, as "time-budget assertions on
per-operation SQLite paths", failing on `main` before this package existed. `db_session()` changes
behaviour only inside a scope and neither path opens one, so there is no mechanism by which this
repair causes it; what changed is exposure, since the heavy-burst test no longer kills a worker and
requeues its work. Recorded as its own `RISKS.md` entry rather than absorbed here — see §5.

## 5. Limitations and remaining risk

- **Three runs is a reasonable basis for repeatability, not a proof of it.** Two consecutive
  fully green Windows Merge Candidates (35312520515, 35319175282) and one whose only failure was
  a pre-existing class this package does not touch, against a same-morning base-branch control
  that lost a worker to the blocker. That is a real change from two green / three red across four
  heads. It is still three runs: a reassessment may reasonably want more, and they are cheap —
  the `ci:merge-candidate` label runs the Windows suite on this PR.
- **A separate, pre-existing Windows failure is still live** (§4a and `RISKS.md`,
  2026-09-18): time-budget assertions on per-operation SQLite paths in
  `test_device_consent_channel.py` and `test_event_backbone_processing.py`. Deliberately not
  absorbed — repairing them means touching those subsystems' own semantics. It is a real obstacle
  to repeatable Windows green and should be tracked as its own item, not counted against this
  package.
- **The scope is opt-in.** Only the scheduler tick and the containment bursts declare one today.
  Other hot paths (MemoryStore ingestion, retrieval, the API bridge's request handling) still pay
  the per-operation cost and can adopt `db_session()` incrementally. That is deliberate: each
  adoption is a statement about where a unit of work begins and ends, and should be made by
  someone who knows.
- **A scope held too long would be a defect.** It holds no lock, but it does hold file handles,
  which on Windows blocks deletion of the database file. `SchedulerStore.unit_of_work()`'s
  docstring says so, and `test_it_is_not_held_across_the_store_s_lifetime` asserts it for the one
  place that could plausibly drift that way.
- **Not in scope, recorded not absorbed:** the per-operation cost in `MemoryStore`'s aiosqlite
  path has a different shape (one long-lived connection per store instance already) and was not
  examined here.

  > **Corrected 2026-09-25.** The parenthesis above is wrong: `MemoryStore` has no long-lived
  > connection. Every method opened its own (`async with aiosqlite.connect(self.db_path)`, 36
  > sites, plus two synchronous ones), so it has exactly the per-operation shape this package
  > measured. Those connections also ran on SQLite's defaults — `synchronous=FULL`,
  > `foreign_keys=OFF`, 5 s at setup — rather than the shared policy. The Windows reliability
  > incident package routes all of them through one seam that applies the shared authority's
  > lifecycle (`docs/WINDOWS_RELIABILITY_INCIDENT_2026_09_24.md` §3, A1). Ownership is
  > unchanged: still one connection per unit of work, still closed when it ends, still not a
  > pool.

## 6. Documentation corrected

- `RISKS.md` said PR #112 was "NOT MERGED, awaiting Taylor's User Approval Gate". It merged on
  2026-09-18 as `9ca2d4a`. Corrected.

## 7. What a Gate 2 / Band 0 reassessment should use

This package does **not** declare Gate 2 / Band 0 complete. The evidence it provides for that
reassessment is:

1. The root cause above, measured rather than asserted, with the earlier "the cost is the open"
   claim withdrawn and the replacement ("the cost is the last close's checkpoint-and-unlink")
   demonstrated by a controlled A/B on the same code path.
2. The lifecycle contract in §3, pinned by 34 deterministic tests, including explicit proof that
   the handle-release guarantee the Windows tests defend is preserved.
3. The Linux before/after figures in §2 and §4.
4. **The Windows measurements in §4a**, which are the decisive ones: runs 35311115266 and
   35312520515 on this branch against run 35301649186 on `main` the same morning, which lost a
   worker to the blocker. This is what closes the connection-lifecycle blocker specifically.
   35312520515 is additionally a **fully green Windows Merge Candidate**, all seven jobs.
5. **Repeatability, as far as three runs can show it:** 35312520515 and 35319175282 both fully
   green, back to back; 35311115266's only failure was a pre-existing class this package does not
   touch. A reassessment may want more runs, and should treat the wall-clock test-defect class
   (device-consent TTL, the event-processing lease clock — repaired separately in PR #114 — and
   the forecast seam's substring leak check) as its own open item. Note that two of those three
   were latent for months and became visible only because this package stopped a larger failure
   from masking them.

The correct statement is that **the SQLite connection-lifecycle blocker is resolved, measured on
Windows** — and that Band 0 is *not* thereby clear, because its condition is repeatable all-green
completion and a separate failure class is still live.
