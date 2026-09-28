# Windows SQLite headroom repair — the record

**Package:** Windows SQLite headroom repair, a bounded follow-up to the Windows reliability incident.
**Branch:** `claude/windows-sqlite-headroom-repair-ywxir4`, cut from PR #124's exact head
`1adf2510f2819e3ac5d06c7598028758638993a3`. It has its own stacked draft PR, **#125**, which
targets `claude/windows-ci-reliability-incident-mc59cm` and not `main` (merge order: §9).
**Parent record:** `docs/WINDOWS_RELIABILITY_INCIDENT_2026_09_24.md`, §6 ("Heavy-test headroom …
**Not improved by A1**") and §7. This package does not rewrite that record. It answers the item that
record deferred, and appends what it found.
**Risk entries:** `RISKS.md`, "(2026-09-25) Heavy-test headroom against the 120 s per-test timeout"
(amended), a new entry for the product's per-operation teardown (§8), and a new entry for the seven
undeclared same-shape tests (§8.1).
**Status (2026-09-27):** implemented, adversarially reviewed and verified locally. **The one Windows
qualification run passed every criterion registered before it** (Merge Candidate 36313600744, head
`006ccb8`; §7). Every required tier was green on that head, and Merge Qualification returned
**READY** for it. The head that records this is docs-only and not itself qualified. **Not merged. Taylor's User Approval Gate
is mandatory. The incident is not closed. Heavy-test headroom is narrowed, not resolved:** one run is
not repeatability, and same-shape tests remain undeclared.
**Taylor's direction (recorded 2026-09-28):**
- **Merge order.** #124 and #125 stay separate packages. #124 merges first. #125 is then retargeted
  onto `main` and requalified on its resulting exact head (§9).
- **Acceptance sequence.** The three-run sequence is to be prepared, not yet started (§7).
- **Follow-ups.** Three costs stay explicit, unresolved follow-up work outside this package (§8.1).
- **Proposed decision.** §10 is a proposal. It is not approved until Taylor explicitly approves it.

Statements are labelled **verified** (a log line, a measurement or a reproduction), **inference**
(reasoned from verified facts, not demonstrated) or **proposal**.

---

## 1. Why this package exists

**Verified (Merge Candidate 36153552522, Windows job 108132502235, head `1adf251`).** The job did not
fail on a test. It was **cancelled at its 40-minute cap** (`##[error]The operation was canceled.`,
16:01:11Z), after three workers were killed by the 120 s per-test timeout and the scheduler was
re-driven 22 times (W13). pytest never printed a summary, and the W13 step was skipped. The W15 summary
reports each lost worker as "per-test timeout (120s) imminent in call; evidence taken at 110.0s". In
all three stack files the test's thread is **inside a SQLite connection close**:

| Worker | Test | Close, from the timeout stacks | Connection seam |
|---|---|---|---|
| gw0 | `test_learning_memory_control_centre.py::test_b6d_the_material_field_vocabulary_is_enforced_not_documented` | `objective_store.py:749 _set_status` → `conn.close()` | `db_ctx.connect()` directly (sync, not `wal_db`) |
| gw2 | `test_memory_agency_review_fixes.py::test_queued_outcome_is_independent_of_inbox_size` | aiosqlite worker thread, `core.py:63` → `close` | `open_memory_db()` (aiosqlite) |
| gw3 | `integration/test_fts_unavailable_vector_quality.py::test_vector_quality_maintained_when_fts_unavailable` | `vector_store.py:316 upsert` → `db_ctx.py:610 wal_db` → `close_quietly` | `db_ctx.wal_db()` (sync) |

**The run's SQLite cost table.** In every heavy test, **91–98 % of wall time went to connect,
commit and close**, at 56–134 ms per close and 72–111 ms per commit. The FTS test, for example: 576
opens, 40.6 s commit, 77.1 s close, **120.0 s** wall.

**The runner was also degraded, and that is part of the cause.**
- Two setup phases took 55.6 s and 58.1 s.
- `test_b6d` ran in a **25.4 s** call on gw1 in the same run, but for more than 110 s on gw0.
- `test_conversational_task_control.py::…::test_two_matching_tasks_produce_a_question_and_no_change`
  is not heavy (75 opens and 43 commits in the Linux census). It took **83.3 s** in its call. Even at
  the run's worst per-operation costs, the teardown model predicts about 15 s for it.

So the incident's §5 slowdown class **recurred on `1adf251`**.

**Not visible, and not claimed.**
- The log API returns the last 5,000 of 12,592 lines.
- The `junit-windows-full` artifact could not be downloaded from this session's sandbox, because the
  proxy refuses its storage host. That artifact holds the per-phase SQLite fields, the `sqlite_slow`
  events and the full stack files.
- As a result, the heavy test's own cost row, gw0's crash lines and the killed phases' SQLite counters
  are not available here.

## 2. Diagnosis (before any change)

### 2.1 The mechanism — verified; one ownership defect

Every storage operation owns its own connection, and **between operations nothing holds the file
open**. So every close is SQLite's *last* close of a WAL database. A last close checkpoints the WAL
into the database file, with its fsyncs, and unlinks `-wal` and `-shm`. The next operation recreates
them, and its first commit writes into a fresh WAL.

**Verified evidence:**
- **Linux census of the default suite (5,693 tests):** 41,855 of 50,436 closes unlinked the WAL.
  By store: MemoryStore 11,137 of 11,677, ObjectiveStore 2,631 of 2,639, VectorStore 2,479 of 2,712.
  In the three killed tests, **every** close did (the heavy test: 1,062 of 1,062).
- **strace, per 20 unscoped operations:**
  - a vector upsert: 80 `fdatasync` and 40 `unlink`;
  - an ObjectiveStore write-and-reread: 80 `fdatasync` and 80 `unlink`;
  - even a read-only `is_revoked`: 40 `unlink`.

**What is and is not established about the kills.**
- The mechanism is **verified**.
- It is the **dominant** cost of the heavy tests: verified for gw3 by the cost table, and inferred for
  gw2 from earlier runs.
- It is **not established as the sole cause**. The observed kills are **two factors multiplied**: a
  degraded runner times the per-operation teardown. gw0's kill in particular was dominated by the
  slowdown, since the same test took 25.4 s on gw1.
- This package removes the teardown factor where it is declared. The slowdown factor remains
  unresolved (§8). **The repair is necessary for headroom, not sufficient for reliability.**

### 2.2 Lifecycle map of the three paths

| | ObjectiveStore `_set_status` (gw0) | MemoryStore (gw2) | VectorStore `upsert` (gw3) |
|---|---|---|---|
| Opens | `self._connect()` → `db_ctx.connect()` + `set_wal_pragmas` | `open_memory_db()` → `aiosqlite.connect` + policy pragmas (one OS thread per connection) | `wal_db()` → `connect` + `set_wal_pragmas` |
| Unit of work | one state transition (`BEGIN IMMEDIATE`, update, event insert), then a re-read on a second connection | one method; `upsert_memory` = `is_revoked` + a write (or `record_pending_write`) + chunking/embedding connections | one embedding row |
| Commits | in the method | each write method | each upsert |
| Closes | `conn.close()` (749), and again for the re-read | `await db.close()` on the aiosqlite thread | `close_quietly` (610) |
| Last close? | yes — 9 / 9 and 27 / 27 in `test_b6d` | yes — 1,062 / 1,062 in the heavy test | yes — 180 / 180 in the gw3 test |
| Can `db_session()` borrow-cover it? | no — it never goes through `wal_db` | no — aiosqlite, on its own thread, async | yes, on the session's thread only (not the executor-thread upserts inside `upsert_memory`) |
| Natural bounded caller scope | the test's propose/edit rounds; in the product, one transition or one re-engagement drive pass (inference) | the test's burst; in the product, a request or an event-processing pass (inference) | the test's seeding loop; `_upsert_all`; the embeddings rebuild loop |

**Verified:** one mechanism, three different connection seams.

### 2.3 The measured alternatives (Linux — mechanism only, not Windows performance)

The heavy test's own body was run three ways: 520 queued `upsert_memory` calls, then a list and a
correction. **B** here is the idle-connection effect, prototyped with a held aiosqlite connection. §3
implements it on a thread of its own instead.

| | connects | commit s | close s | WAL torn down | wall s |
|---|---|---|---|---|---|
| A — unchanged | 1057 | 0.41 | 0.57 | after 520 of 520 operations | 3.2 |
| B — one idle policy-configured connection held for the burst | 1058 | 0.035 | 0.061 | never | 2.0 |
| C — one aiosqlite connection *borrowed* by successive operations | 7 | 0.028 | 0.010 | never | 0.39 |

**Verified hazards of C** (borrowing an aiosqlite connection; the literal async `db_session`):
- **Lost writes across tasks.** In one experiment, task A's rollback discarded task B's insert, and
  B's commit became a no-op. `ContextVar`s are inherited by child tasks, so a context-bound session
  would reach `gather`/`create_task` children, including the fire-and-forget one at
  `parking_brake.py:128`.
- **Stale snapshots.** A partly read multi-row cursor pins a stale read snapshot while
  `in_transaction` reports False. The next write then fails at once with `SQLITE_BUSY_SNAPSHOT`,
  whatever `busy_timeout` says.
- **Inherited transactions.** A failed operation leaves an open transaction that the next borrower
  would commit.
- **Leaked connection state.** Eight sites set `row_factory` and never restore it, and `executescript`
  commits a pending transaction.
- **Zombie handles.** A close with a still-referenced cursor leaves a zombie that keeps its handles.
- **Coverage.** C would cover only the aiosqlite seam. ObjectiveStore, GovernanceStore, the sync chunk
  seam and the executor-thread vector upserts would benefit only from B's effect.

**The sync `db_session()` as an anchor, rejected.** After initialisation it has B's effect (0 of 520
teardowns). But entered *before* initialisation it silently does nothing (520 of 520), and in async
tests it runs blocking SQLite setup on the event loop. B's implementation refuses a missing or non-WAL
database and never touches the loop.

**How much of B's saving should carry to Windows (inference from verified figures).** In the four
A1-head Windows runs, the heavy test's time *outside* commit + close was only 2–4 s of 57–95 s. That
remainder holds the per-connection costs B keeps and C would remove: thread start, connect, setup
pragmas. B also keeps about 1,060 closes and about 529 commits that are no longer last closes or
fresh-WAL commits. Their Windows cost is **unmeasured**. The pre-registered criteria in §7 measure it.

Also rejected:
- raising the 120 s timeout or the 40-minute cap, using fewer xdist workers, or skipping, xfailing or
  shrinking the tests — all forbidden, and they remove the signal, not the cost;
- a pool or a process-lifetime connection — forbidden, and `tests/test_vector_store_handle_lifetime.py`
  guards against it.

## 3. The repair

**Chosen: B — `db_ctx.hold_wal_open()`.** A declared burst holds the database file open, and lends
nothing. It removes the one verified mechanism for every seam at once, while every operation keeps
exactly the connection lifecycle it has today.

**Relation to the approved decisions.**
- It is a **distinct mechanism** within the class the 2026-09-17 charter allows: "a connection
  explicitly held across a burst and explicitly closed", never a pool.
- It does **not** implement the 2026-09-18 ownership model. There, operations borrow the unit of work's
  connection; here nothing borrows.
- It is therefore proposed as its own decision (§10), not presented as an extension of either.

### 3.1 Code (`bartholomew/kernel/db_ctx.py`)

`hold_wal_open(db_path, *, label="") -> WalHold` is a class-based scope, usable as `async with` (on any
event loop) and as `with` (refused on an event-loop thread).
- **Entry.** It refuses a database that does not exist, and one that is not in WAL mode: on a
  rollback-journal file an idle connection holds no lock, so the hold would silently do nothing. It
  then starts **one daemon thread**, which opens **one connection** with the setup budget and the
  shared setup pragmas, then drops it to the operational budget. The thread attaches the connection
  with fully consumed reads (`PRAGMA journal_mode`, then `SELECT count(*) FROM sqlite_master`) and waits.
- **Exit.** The release is decided synchronously, before any `await`. The exit then waits until the
  thread has closed its connection and gone, **through any number of cancellations**, and re-raises
  one only if the body had completed normally. A cancellation during entry is held back the same way,
  until the connection that entry started has been closed.
- **Why a daemon thread, and not an aiosqlite connection.** The first implementation (`28d2a02`) held
  an aiosqlite connection. The lifecycle review showed that this inherits aiosqlite's non-daemon worker
  and its version-dependent close:
  - a hold never exited (its loop closed, its task destroyed) kept the process from exiting — on xdist,
    the very "not properly terminated" symptom this package exists to remove;
  - on aiosqlite 0.19–0.21, both allowed by `>=0.19`, the handle leaked under loop teardown during close.

  The thread keeper depends on no aiosqlite version. It cannot block interpreter exit, and an abandoned
  hold is released when it is garbage-collected (`weakref.finalize`).
- `MemoryStore` is unchanged, apart from one comment.

### 3.2 Contract

1. **Operations are unchanged.** Every operation inside a hold opens, configures, uses and closes its
   own connection. Transactions, rollback-on-close, cursors, `row_factory`, foreign keys,
   `synchronous=NORMAL`, and the 30 s setup / 5 s operational budgets are exactly as they are outside
   one. Governance is untouched.
2. **The held connection is idle.** It runs setup and two consumed reads, then nothing. It holds no
   transaction and no read snapshot. It blocks no reader, writer, `BEGIN IMMEDIATE` or TRUNCATE
   checkpoint (verified). Its effect is that no other close to the file is the last one.
   **Verified:** this is file-wide — it covers other threads *and other processes'* SQLite connections.
3. **Bounded, and not a pool.** It exists exactly for the `with`, and is closed on success, exception
   and cancellation. Nothing discoverable can lend or reuse the connection: it lives only on the hold
   thread's stack. Re-entering opens a new one; a hold object is entered once. The hold's own close is
   the one last close: it checkpoints the WAL into the database file and removes `-wal`/`-shm`.
4. **Explicit.** A caller declares the burst. No product code holds one. An exact allowlist test pins
   this (§5), so adopting it in the product means editing that test on purpose.

### 3.3 What a hold changes, stated rather than hidden

- **Durability.** `synchronous=NORMAL` is unchanged, and so is durability across an application
  crash: a process SIGKILLed inside a hold recovered every committed row, with `integrity_check` ok.
  - Without a hold, an operation that happened to be the last connection also checkpointed and fsynced
    on close. That is stronger than NORMAL promises, and obtained by accident.
  - Inside a hold, committed work stays in the WAL until an automatic checkpoint or the hold's end. That
    is exactly NORMAL's documented guarantee: the latest transactions may roll back on power loss or an
    OS crash.
  - The database file alone is not a complete copy until the hold ends. Afterwards it is (tested).
  - **Not new to production.** The approved `db_session()` has the same effect on other threads'
    connections while it is open (§8). `SchedulerStore.unit_of_work()` holds the daemon's shared file
    open during every scheduler tick.
- **Exclusive access fails while a hold is open.** This covers changing `journal_mode`,
  `locking_mode=EXCLUSIVE`, and, on Windows, deleting or renaming the file. No product code does any
  of these.
- **POSIX locks (Linux/macOS).** Forking, or a non-SQLite `open()`/`close()` of the file in the holding
  process, drops the process's `fcntl` locks. Combined with another process's close, that can lose
  writes committed inside the hold (reproduced). SQLite documents this hazard for any open connection;
  a hold widens its window from one operation to the burst. None of these belong inside a hold, and
  the adopted tests do none of them. SQLite may keep closed connections' descriptors until the hold
  ends; they are released with it.
- **Timing.**
  - A cancellation is delivered only after the hold's connection is closed: up to the setup budget on
    entry, and the close's I/O time on exit.
  - On Python 3.10 a KeyboardInterrupt during the exit is not a cancellation, so it abandons the wait.
    The release has already been decided, so the thread closes the connection shortly after. 3.10 is in
    the Ubuntu Merge Candidate matrix; the adopted tests do not raise KeyboardInterrupt.

### 3.4 Adoption in this package — the three tests killed on Windows

Each was killed inside the same last-close teardown on Merge Candidate 36153552522, and each now
declares its burst. **No assertion, workload, timeout or marker changed.** The 520-row workload is
intact, and every operation still runs its full production path on its own connection.

| Test | Declared | Why this is the burst |
|---|---|---|
| `test_queued_outcome_is_independent_of_inbox_size` | `async with hold_wal_open(db_path)` around the 520 writes and the list/write/correct that follow | one burst of governed writes and the reads that judge them |
| `test_vector_quality_maintained_when_fts_unavailable` | `hold_wal_open` around corpus ingestion; the approved sync `db_session()` around the `VectorStore.upsert` loop, which writes through `wal_db` and so borrows as designed | two seeding bursts; retrieval and assertions stay outside |
| `test_b6d_the_material_field_vocabulary_is_enforced_not_documented` | `hold_wal_open(ctx.mem.db_path)` around the nine propose/edit rounds | one burst through the real runtime contract, which reaches objectives, memories and governance on one file; the hold is file-wide, so it is declared on the file, not on a store |

**Compared precisely with PR #113.**
- PR #113 wrapped its containment bursts in `db_session()`, which *borrows*, paired those test scopes
  with a production scope (the scheduler tick), and kept its assertions after the scope.
- Here nothing is borrowed except in the FTS test's `wal_db` loop, and there is **no production
  pairing**.
- In the heavy test and in b6d, the behaviour under test runs inside the hold. Every read that judges
  it still uses its own connection, and `test_governed_outcomes_are_identical_with_and_without_a_hold`
  shows that governance decides identically either way.

`tests/test_wal_hold.py::test_each_burst_killed_on_windows_is_declared` pins all four declarations
(three `hold_wal_open`, one `db_session`) **structurally**: each burst's loop must sit lexically inside
a `with`/`async with` whose context expression is the scope call. A scope that is created but never
entered does not count.

## 4. Pre-fix control and post-fix evidence (Linux — mechanism, not Windows performance)

**Causal control, in the test suite.**

| Test | Without a hold | With one |
|---|---|---|
| `test_control_unheld_every_operation_tears_the_wal_down` / `test_while_held_no_operation_tears_the_wal_down` | WAL torn down after 5 of 5 governed writes | after 0 of 5 writes, then once when the hold ends |
| `test_the_hold_covers_every_connection_seam_on_any_thread` (aiosqlite, `open_memory_db_sync`, `wal_db`, ObjectiveStore's `db_ctx.connect`; the sync ones on worker threads) | every seam tears it down | no seam does |
| `test_the_synchronous_form_holds_the_file_for_sync_callers` | — | 0 of 5 ObjectiveStore transitions |

**Execution-trace counters for the three killed tests** (call phase, `-n 1`, two runs each). "Before"
is `1adf251` in a separate worktree; "after" is the final implementation.

| Test | opens (before → after) | commits | commit s | close s | wall s |
|---|---|---|---|---|---|
| heavy `test_queued_outcome…` | 1062 → 1063 | 529 | 0.51–0.54 → 0.04 | 0.59–0.62 → 0.08 | 3.7–3.8 → 2.2–2.3 |
| `test_vector_quality…` (FTS) | 576 → 488 | 367 | 0.33 → 0.04 | 0.40–0.43 → 0.06 | 3.0–3.1 → 2.1–2.2 |
| `test_b6d…` | 376 → 377 | 188 | 0.14–0.16 → 0.01 | 0.18 → 0.04 | 1.3–1.4 → 0.9–1.0 |

- **Opens are essentially unchanged.** Every operation still owns its connection; the one extra is the
  hold's. The FTS test's 88 fewer opens come from its `VectorStore.upsert` loop borrowing the approved
  `db_session()`.
- **The teardown is paid once per burst instead of once per operation.** On Linux, commit + close fall
  about 8–10×.
- **Inference:** on Windows, where commit + close were 91–98 % of these tests' wall time, the
  proportional effect should be larger, **but only the Windows run in §7 can say**.

**Mutation check of the forbidden-state tests.** Each deliberately broken implementation of
`hold_wal_open` was run against `tests/test_wal_hold.py`, and each was caught:

| Mutant | Caught by |
|---|---|
| holds nothing (a no-op) | 16 tests, the causal controls first |
| exit does not wait for the close | 8 tests (`nothing_survives`, `re_entering`, the durability copy …) |
| exit wait abandoned on cancellation | `repeated_cancellation_cannot_end_the_hold…` |
| never released | the suite hangs at the first exit (killed at the time limit) — the exit waits, by design, for a thread it never released |
| pins a read snapshot | `pins_no_snapshot…`, the ownership test, the concurrency test |
| holds a write transaction | 11 tests |
| connection kept on the hold (lendable) | `nothing_anywhere_can_find_or_keep…` |
| policy pragmas bypassed | the ownership test, the concurrency test |
| a module-level registry | `nothing_anywhere_can_find_or_keep…` |
| no existence check / no WAL check | the two refusal tests |
| non-daemon thread | `a_hold_still_open_at_interpreter_exit_does_not_block_the_exit` |
| entry cancellation not waited for | `a_cancellation_during_entry_waits…` |
| the thread keeps its hold alive (the finalizer cannot fire) | `an_abandoned_hold_never_keeps_the_process_alive…` |
| sync form allowed on an event loop | `the_synchronous_form_refuses_an_event_loop_thread` |
| a production module adopts a hold | `no_production_code_holds_a_database_open` |
| the heavy test's hold created but not entered | `each_burst_killed_on_windows_is_declared` |

The first implementation (`28d2a02`, an aiosqlite keeper) passed 24 tests of its own. The design
review then showed two gaps that let mutants through: its production guard accepted a scheduler
lifetime hold, and its adoption pin accepted an unentered scope. Both guards were rewritten, and the
implementation replaced, before this matrix was run.

## 5. Forbidden-state tests (`tests/test_wal_hold.py`)

| Would fail if the repair became… | Test |
|---|---|
| ineffective (the causal control) | `test_control_unheld_every_operation_tears_the_wal_down`, `test_while_held_no_operation_tears_the_wal_down`, `test_the_hold_covers_every_connection_seam_on_any_thread`, `test_the_synchronous_form_holds_the_file_for_sync_callers` |
| silently ineffective on an uninitialised or non-WAL file, or created the file | `test_a_hold_on_a_database_that_does_not_exist_yet_is_refused`, `test_a_hold_on_a_non_wal_database_is_refused_and_released` |
| borrowing / cross-task, thread or loop sharing | `test_every_operation_inside_still_owns_its_own_connection` (exactly one extra connection, which ran only its setup and reads), `test_concurrent_tasks_threads_and_loops_while_held_use_their_own_connections` |
| a configuration bypass | the same ownership test (every connection, held or not, starts with the policy statements under the 30 s budget), `test_an_operation_inside_the_hold_runs_under_the_shared_policy` |
| a pool or registry | `test_nothing_anywhere_can_find_or_keep_the_held_connection` (module state, containers, plain objects' attributes, context variables, the hold and the store — during and after), `test_re_entering_opens_a_new_held_connection_rather_than_reusing_one`, `test_a_hold_cannot_be_entered_twice` |
| a connection, thread or handle leak | `test_nothing_survives_the_hold`, `test_the_hold_is_released_when_the_body_raises`, `…_when_the_body_is_cancelled`, `test_repeated_cancellation_cannot_end_the_hold_before_its_connection_is_closed` (a slow close and a cancellation storm), `test_a_cancellation_during_entry_waits_for_the_connection_it_started` (GC off), `test_a_hold_whose_setup_fails_is_closed_and_the_error_raised`, `test_a_hold_can_be_ended_from_another_task`. Each is checked **immediately after the hold ends, inside the event loop**, for the connection *and* its thread. |
| a process that cannot exit | `test_an_abandoned_hold_never_keeps_the_process_alive_and_is_released_when_collected`, `test_a_hold_still_open_at_interpreter_exit_does_not_block_the_exit` (subprocesses) |
| blocking SQLite on the event loop | `test_the_synchronous_form_refuses_an_event_loop_thread` |
| an unbounded or implicit hold in the product | `test_no_production_code_holds_a_database_open` (an exact, empty allowlist) |
| an implicit transaction or snapshot spanning operations | `test_the_held_connection_pins_no_snapshot_and_blocks_no_one` (another writer's `BEGIN IMMEDIATE` at `timeout=0`; a TRUNCATE checkpoint returns busy 0 and a 0-byte WAL; the next read inside the hold sees the write), `test_an_uncommitted_write_inside_the_hold_does_not_persist` |
| a lost rollback on failure | `test_a_failed_operation_inside_the_hold_leaves_nothing_behind` |
| a weakened governance path | `test_governed_outcomes_are_identical_with_and_without_a_hold` (stored, queued, refused, forgotten, `refused_revoked` read fresh inside the hold, correction queued; identical outcomes and table contents; the inbox still encrypted at rest) |
| an incomplete database file when the hold ends | `test_when_the_hold_ends_the_database_file_alone_holds_every_committed_row` |
| the adoption removed or not entered | `test_each_burst_killed_on_windows_is_declared` (structural) |

## 6. Tests run (Linux locally: Python 3.11.15, SQLite 3.45.1, aiosqlite 0.22.1)

All runs are against the code of head `006ccb8`.

- `tests/test_wal_hold.py`: **32 passed**. The mutation matrix is in §4.
- The three adopted tests: **passed**.
- SQLite / MemoryStore / scheduler regression suites: **391 passed across 31 files**. They cover:
  - the connection contract, including its corrected closure probe;
  - `db_session` lifecycle, vector-store handle lifetime, and the root WAL cleanup test;
  - `test_sqlite_*` (WAL, concurrent processes, event-loop convoy);
  - `test_memory_store*`, `test_memory_agency*`, `test_fts_*` and `test_scheduler_*`;
  - seed ownership, clean start, daemon lifecycle, the objective store, and the FTS integration test.
- **Full default suite: 5,723 passed, 2 skipped, 0 failed** (9:06, `-n 4 --dist loadfile`).
  - An earlier full run, on the first implementation, reported 2 failures. Both were
    `inspect.getsource` tests reading `memory_store.py` while it was being edited mid-run. Both pass
    on the committed tree; they are not attributable to the change.
- pre-commit (black, ruff, hygiene): clean.
- **CI on `006ccb8`:** smoke, Quality, PR Fast tests (Ubuntu, parallel), Windows fast and the
  qualification self-test all green.
- **Integration**, dispatched once because draft PRs skip it (run 36315022494, on `006ccb8`): Tests +
  coverage (W13 clean), Critical integration + lifecycle, and Windows lifecycle + compatibility, all
  green.

## 7. Windows qualification — criteria registered before the run

The prompt for this package allows **one** Windows Merge Candidate on the exact candidate head. If it
shows a material reduction, **the work stops there and is reported to Taylor.** No multi-run acceptance
sequence starts without that decision, and no run is repeated to get a green.

**Pre-registered criteria for that one run.** They were written before the run and are not edited
after it.

- **Q1 — the run finishes.** The Windows job ends inside its cap: not cancelled, and the W13 step runs.
- **Q2 — no adopted test is lost.** None of the three adopted tests loses a worker or appears in W15
  evidence.
- **Q3 — the mechanism works on Windows** (read from the job's SQLite cost table). Each adopted test's
  commit + close is at most 20 % of its pre-fix Windows reference:

  | Test | Reference commit + close | Reference source | Limit |
  |---|---|---|---|
  | heavy test | 53.9 s — the *lowest* pre-fix observation (close 34.3 + commit 19.6) | run 36126510276 | **≤ 10.8 s** |
  | FTS test | 117.7 s | run 36153552522 | **≤ 23.5 s** |
  | b6d | 27.0 s (gw1's completed run) | run 36153552522 | **≤ 5.4 s** |

  Each adopted test's wall time must also be at most **60 s**, half its budget.
- **Recorded, not a criterion:** each adopted test's opens. They should stay close to the pre-fix count,
  because nothing is borrowed except the FTS test's `wal_db` loop. The number shows whether the
  remaining per-operation cost on Windows is small.

**How each outcome is read, decided in advance.**

| Outcome | Reading | Action |
|---|---|---|
| Q1–Q3 all met | a material reduction, shown on one run | stop and report to Taylor |
| Q3 met, but the run fails elsewhere (an unadopted test killed, or an unrelated failure) | the mechanism works on Windows, but the run is **not** clean | investigate that failure; report; no rerun |
| Q3 not met | no material reduction on Windows | investigate (e.g. a non-last close's cost on Windows) and revise; report; no rerun |

A kill in an *unadopted* test is not evidence against the repair. A clean run is not evidence that
headroom is fixed.

**The later acceptance sequence** is not started by this package. It is defined here so that it cannot
be defined after the fact. It needs at least **three consecutive** Merge Candidates on one unchanged
head, each meeting Q1–Q3 with no worker lost anywhere and W13 clean. A concurrent control run would
separate runner variance from the fix; that is desirable, not required. The `RISKS.md` headroom entry
stays open whatever the result, because same-shape tests remain undeclared (§8).

*Result: pending the run.*

### Result, appended 2026-09-27 after the run (the criteria above are unchanged)

**Merge Candidate 36313600744, head `006ccb8`, Windows job 108604037039: PASS on Q1, Q2 and Q3.**
All 7 Merge Candidate jobs succeeded. The figures below are verbatim from the job's trace summary.

- **Q1 — met.** `5622 passed, 103 skipped, 166 warnings in 1325.14s (0:22:05)`, inside the 40-minute
  cap. W13: "no scheduler re-drive was needed".
- **Q2 — met.** 4 workers were created and all 4 `finished`, each with
  `session_finish=yes process_exit=yes`. Stalls: none. "what failed: nothing: no worker crashed and no
  phase reported a bad outcome".
- **Q3 — met for all three adopted tests.** Their rows in the SQLite cost table:

  ```
     opens  connect  commits  commit   close    wall  nodeid
      1063     0.2s      529    0.4s    1.1s    3.7s  …::test_queued_outcome_is_independent_of_inbox_size
       488     0.1s      367    1.3s    3.1s    8.3s  …::test_vector_quality_maintained_when_fts_unavailable
       313     0.0s      122    0.1s    1.1s    3.0s  …::test_b6d_the_material_field_vocabulary_is_enforced_not_documented
  ```

  | Test | commit + close | Limit | Pre-fix | Wall |
  |---|---|---|---|---|
  | heavy test | **1.5 s** | ≤ 10.8 s | 53.9–91.3 s | **3.7 s** |
  | FTS test | **4.4 s** | ≤ 23.5 s | 117.7 s | **8.3 s** |
  | b6d | **1.2 s** | ≤ 5.4 s | 27.0 s | **3.0 s** |

  Every wall time is ≤ 60 s. Opens are essentially the pre-fix counts, so the per-operation costs the
  hold keeps — the question §2.3 left open — are small on Windows too.
- **A same-run control.** The same-shape tests that are *not* declared still spent 92–96 % of their
  wall time in commit + close:

  | Undeclared test | commit + close of wall |
  |---|---|
  | `test_lexical_beats_vector_on_exact_rare_tokens` | 54.2 of 57.6 s |
  | `test_hybrid_beats_single_channel` | 37.6 of 39.6 s |
  | `test_recency_boost_flips_rankings_rrf` | 34.5 of 37.0 s |
  | `test_recency_boost_flips_rankings_weighted` | 34.0 of 36.8 s |
  | `test_privacy_gates_upheld` | 29.6 of 31.2 s |

  On one runner, in one run, the declared tests left that profile and the undeclared ones did not.
- **Integration**, the other required tier, green on the same head (run 36315022494, §6).
- **Merge Qualification on `006ccb8` (run 36316432508): READY** — `merge-qualified at
  006ccb889a0c54afe9120a39ee0ec1fc7d3546a9`. Every required tier is proven green on that exact head,
  with no undispositioned finding. Two caveats:
  - Under the 2026-09-21 decision a READY verdict authorises nothing. Taylor's User Approval Gate
    remains the merge authority.
  - The commit that records this result changes the head. It changes docs only, but qualification
    binds to one commit, so that head is not itself qualified.

**How this reads under the pre-registered rules.** Q1–Q3 are all met, which is **a material reduction
shown on one run**. Per §7, work stops here and is reported to Taylor.

**What it does not establish:**
- **Repeatability.** The three-run acceptance sequence above has not been started; that is Taylor's
  decision.
- **Anything about the undeclared tests.** They still pay the teardown; `test_lexical_beats_vector` ran
  at 57.6 s.
- **The slowdown factor.** It was present in milder form: light tests' setup phases took 30.0 s,
  22.9 s and 21.4 s. Its cause is unresolved.
- **The orphaned msedge/notepad processes** were terminated at job end again. This is a known, deferred
  item.

The `RISKS.md` headroom entry stays **open, narrowed**.

## 8. Remaining risks and unresolved observations (recorded, not repaired)

- **The product defect.** The product still pays the per-operation teardown, with its latency, fsync
  amplification and last-close lock window, wherever no other connection happens to be open. It now
  has its own open entry in `RISKS.md`. Candidates for a declared hold (inference):
  - one `correct_memory`/`supersede_memory` (about 15 connections);
  - a request handler;
  - an event-processing pass;
  - the embeddings rebuild loop.

  None is adopted. Each is a design statement, and each must weigh §3.3: power-loss durability, POSIX
  locks, and several processes (daemon, CLI, API bridge) sharing one database. Separately,
  `request_permission_to_store` can await an arbitrary consent handler in the middle of
  `upsert_memory`, so a hold spanning it would be unbounded.
- **The slowdown factor is unresolved** (§2.1; incident §5). It recurred on `1adf251`: see the 83.3 s
  call of a light test, and b6d's 25.4 s against more than 110 s. W15 did not capture its cause.
- **Other heavy tests with the same shape remain undeclared.** From the same run:
  - `test_lexical_beats_vector_on_exact_rare_tokens` 69.2 s;
  - `test_privacy_gates_upheld` 61.6 s;
  - `test_recency_boost_flips_rankings_weighted` 58.3 s and `…_rrf` 55.4 s;
  - `test_hybrid_beats_single_channel` 50.6 s;
  - `test_lexical_top_k_coverage_on_rare_tokens` 38.8 s;
  - `test_recency_disabled_no_flip` 32.7 s.

  Each has its own ingestion loop. They are the next bounded item, not a widening of this package.
  `test_uncontained_baseline_grows_without_bound` (39.9 s) is a deliberately uncontained baseline and
  stays as it is. The slow-marked `test_memory_agency_review_fixes.py` tests that nightly Windows runs
  have the same shape, at 1,050–1,808 opens.
- **`db_session()` already has the hold's effect on other threads**, and it is undocumented. While a
  session is open, other threads' writes tore the WAL down 0 of 50 times, against 50 of 50 without
  one. Its docstring said other threads were "unaffected"; it is corrected in `db_ctx.py`.
- **`SchedulerStore.unit_of_work()`'s release can end before its close finishes.** The lifecycle review
  reproduced this: after a single `asyncio.shield` and one cancellation during close, the scope ended
  with the connection still open, then closed shortly after. This is pre-existing production code and
  is not changed here; it is recorded as a finding for its owner.
- **A test-evidence defect in the parent package, corrected here.**
  `tests/test_memory_store_connection_contract.py`'s `_is_closed()` probed with `execute`. aiosqlite's
  connections belong to its worker thread, so from the test's thread that raises the same
  `ProgrammingError` whether the connection is open or closed (verified), and every aiosqlite connection
  read as "closed". This branch switches it to a connection subclass that records its own close. The
  diff is visible in PR #125; it is not folded into #124.
- **A documentation error in the lifecycle record.** `docs/SQLITE_CONNECTION_LIFECYCLE_REPAIR.md` §3
  called `wal_db()` the choke point "every store already goes through". It is corrected there by a
  dated amendment.
- **Harness evidence gaps.**
  - `summarise_trace` prints neither a timeout's phase-elapsed nor its SQLite fields, and no
    `sqlite_slow` summary.
  - The W13 step is skipped when the test step is cancelled.
  - Artifacts cannot be read from this session's sandbox.
- **Incidental, pre-existing, not in scope:**
  - `BARTHO_EMBED_ENABLED` is parsed inconsistently. `memory_store.py` and `embedding_engine.py:137`
    treat `"0"` as *enabled*, while `memory_rules.py` and `embedding_engine.py:789` require `"1"`.
    The FTS test sets `"0"`, which turns embeddings on, and the setting leaks to later tests on the
    worker. It has its own `RISKS.md` line.
  - `_heal_unindexed_memories` has no per-row rollback.
  - aiosqlite is not pinned in `requirements.lock` (`>=0.19` elsewhere), and its close semantics differ
    across that range. The hold no longer depends on it; the product's per-operation path still does.
- **The deferred hybrid pooling model is not recorded in the repository.** This package does not record
  or pre-empt it. The exact-allowlist guard in §5 keeps it from being pre-empted in code.

### 8.1 Unresolved follow-up work, kept outside this package (Taylor's direction)

Taylor directed that three costs be kept as **explicit, unresolved follow-up work**, not absorbed into
this package:
- none of them is repaired here;
- this package's acceptance sequence (§7) cannot close any of them;
- a green run does not narrow them.

Each needs its own bounded package, and Taylor assigns the owner. Figures are verified from the
Windows job logs: Merge Candidate 36153552522 on `1adf251` (pre-fix) and 36313600744 on `006ccb8`
(this repair).

**FU-1 — the product's per-operation WAL/connection teardown.**
- **What.** An operation whose close is the file's last close pays three costs:
  - a checkpoint with its fsyncs;
  - removing and recreating `-wal`/`-shm`;
  - a last-close EXCLUSIVE window.
- **How often (§2).** In a Linux census of the suite, 41,855 of 50,436 closes did this.
- **Why it is not repaired here.** No product code declares a hold, and the exact-allowlist test (§5)
  pins that.
- **Tracked in.** `RISKS.md`, "(2026-09-27) The product pays the per-operation WAL teardown…".
- **What would close it.** A design decision per adopting caller that weighs §3.3 (power-loss
  durability, POSIX locks, several processes on one file, bursts that await external actors). Each
  adoption is its own package and edits the allowlist on purpose. Not a pool, and not a permanent
  connection.

**FU-2 — seven same-shape integration tests still pay the teardown on Windows.** Each seeds through
its own ingestion loop with no hold. Wall time, with the `006ccb8` commit + close in brackets:

| Test | `1adf251` wall | `006ccb8` wall (commit + close) |
|---|---|---|
| `test_lexical_beats_vector_on_exact_rare_tokens` | 69.2 s | 57.6 s (54.2 s) |
| `test_privacy_gates_upheld` | 61.6 s | 31.2 s (29.6 s) |
| `test_recency_boost_flips_rankings_weighted` | 58.3 s | 36.8 s (34.0 s) |
| `test_recency_boost_flips_rankings_rrf` | 55.4 s | 37.0 s (34.5 s) |
| `test_hybrid_beats_single_channel` | 50.6 s | 39.6 s (37.6 s) |
| `test_lexical_top_k_coverage_on_rare_tokens` | 38.8 s | 9.1 s (7.5 s) |
| `test_recency_disabled_no_flip` | 32.7 s | 20.4 s (18.8 s) |

- **The pattern.** On `006ccb8`, commit + close was 82–95 % of each test's wall. For the adopted tests
  in the same run it was 40–53 %.
- **How close to the budget.** The worst is at 48 % of the 120 s budget on `006ccb8`, and was at 58 %
  on `1adf251`.
  - At 48 %, a slowdown of about 2.1× reaches the timeout.
  - On `1adf251`, `test_b6d` ran more than 4× slower on one worker than on another in the same run.

  That this puts FU-2 at risk is an inference from the two-factor model (§2.1).
- **Tracked in.** `RISKS.md`, "(2026-09-28) Seven same-shape integration tests still pay the
  per-operation WAL teardown on Windows".
- **What would close it.** Each test's seeding burst is declared as in §3.4, with the structural pin
  extended to it, or it is shown not to need one. Then Windows evidence. Not a longer timeout, not a
  smaller corpus, not a marker.

**FU-3 — the unexplained runner setup slowdown.**
- **On `1adf251`:**
  - setup phases took 55.6 s and 58.1 s;
  - `test_b6d` ran 25.4 s on gw1 but more than 110 s on gw0;
  - a light test's call took 83.3 s.
- **On `006ccb8`**, light tests' setup phases took:
  - 30.0 s, gw0, `test_conversational_task_control.py::TestAcceptanceBar::test_a_plain_sentence_actually_creates_a_task`;
  - 22.9 s, gw2, `test_windows_action_governance.py::test_the_full_governed_path_works_end_to_end`;
  - 21.4 s, gw1, `test_learning_memory_control_centre.py::test_a1_candidate_exposes_its_supporting_experience_and_provenance`.
- **What W15 does and does not show.** It records where a killed thread was, not why the runner was
  slow. The cause is unresolved.
- **Tracked in.** It belongs to the incident package, PR #124, not to this one:
  - incident record §5, amended 2026-09-27;
  - its `RISKS.md` entry ("the worker-loss stall cause is not established").

  This package cross-references it and does not take it over.
- **What would close it.** A cause identified from evidence, for example setup-phase tracing on the
  Windows job, or a finding that it is runner-external.

## 9. Package boundary and merge order

- This package depends on PR #124: `open_memory_db()` and the A1 connection contract exist only on
  #124's branch.
- **PR #124 is not merge-qualified at `1adf251`.** Its Merge Candidate there, 36153552522, was
  cancelled, and under the 2026-09-21 qualification decision a cancelled job refuses. A later green run
  would not erase 36153552522; the incident's own attempt-1 rule applies.
- This PR targets #124's branch so that it can be reviewed on its own. **How the two land is Taylor's
  decision.** The two shapes consistent with the repository's rules are:
  1. **#124 first, this PR retargeted.** #124 qualifies on its own head. This PR is then retargeted to
     `main` and qualifies on its own head.
  2. **This PR merged into #124's branch.** #124's head then carries both packages. Its qualification,
     and the incident's acceptance evidence, must be recomputed on that combined head.

  Shape 2 is the folding Taylor rejected for #112 on 2026-09-17, which is why the review recommends
  shape 1.

**Decided by Taylor (recorded 2026-09-28): shape 1.** In Taylor's words: "Preserve PR #124 and PR #125
as separate packages. The intended merge order is #124 first, followed by #125 retargeted onto main and
independently requalified on its resulting exact head."
- This PR is not merged into #124's branch.
- **Nothing proven here carries over to the retargeted head.** That covers the qualification on
  `006ccb8` (§7) and any acceptance run on a pre-retarget head, because qualification binds to one
  commit. The retargeted head is qualified on its own.
- The retargeted head is made by merging `main`, once it contains #124, into this branch: a merge
  commit, no rebase and no force-push.
- The acceptance sequence (§7) runs one Merge Candidate at a time on one unchanged head. Its order
  under this decision is reported to Taylor, and confirmed, before any run starts.

## 10. Proposed decision (for Taylor's User Approval Gate — not written into `DECISIONS.md` unless approved)

> *Proposal.* A caller about to perform a burst of operations on one SQLite database may declare it
> with `db_ctx.hold_wal_open()`.
>
> - For the burst's duration this holds one idle connection, under the shared connection policy, on a
>   daemon thread of its own. It is never lent.
> - The WAL is therefore torn down once per burst instead of once per operation, for every connection
>   to the file. Every operation keeps its own connection.
> - This is a distinct mechanism within the 2026-09-17 charter ("a connection explicitly held across a
>   burst and explicitly closed"). It is not the 2026-09-18 borrowing model.
> - Adoption is explicit and per caller. In this package it is adopted only by the three test bursts
>   killed on Windows. Product adoption is a separate decision that must weigh §3.3; an exact-allowlist
>   test enforces this.

It decides nothing about the deferred hybrid pooling model, and introduces no pooling, no reuse of a
connection by anyone, and no connection that outlives its declaration.
