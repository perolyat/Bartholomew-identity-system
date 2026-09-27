# Windows SQLite headroom repair — the record

**Package:** Windows SQLite headroom repair (bounded follow-up to the Windows reliability incident).
**Branch:** `claude/windows-sqlite-headroom-repair-ywxir4`, cut from PR #124's exact head
`1adf2510f2819e3ac5d06c7598028758638993a3`. A stacked draft PR targets
`claude/windows-ci-reliability-incident-mc59cm` (PR #124), not `main`.
**Parent record:** `docs/WINDOWS_RELIABILITY_INCIDENT_2026_09_24.md` — §6 ("Heavy-test headroom …
**Not improved by A1**") and §7 ("Heavy-test headroom is live, and A1 did not improve it"). This
package does not rewrite that record; it answers the item it deferred.
**Risk entry:** `RISKS.md`, "(2026-09-25) Heavy-test headroom against the 120 s per-test timeout".
**Status (2026-09-27):** implemented and verified locally. The Windows Merge Candidate result is in
§7. **Not merged. Taylor's User Approval Gate is mandatory. The incident is not closed, and
heavy-test headroom is not marked resolved until Windows evidence says so.**

Statements below are labelled **verified** (a log line, a measurement or a reproduction),
**inference** (reasoned from verified facts, not demonstrated), or **proposal**.

---

## 1. Why this package exists

**Verified (Merge Candidate 36153552522, Windows job 108132502235, head `1adf251`).** The job did
not fail on a test; it was **cancelled at its 40-minute cap** (`##[error]The operation was
canceled.`, 16:01:11Z), after three workers were killed by the 120 s per-test timeout and the
scheduler needed 22 W13 re-drives. pytest never printed a summary; the W13 step was skipped. The
execution trace's own summary (W15) says, for each lost worker, "per-test timeout (120s) imminent
in call; evidence taken at 110.0s", and every one of the three stack files has the test's thread
**inside a SQLite connection close**:

| Worker | Test | Close, from the timeout stacks | Connection seam |
|---|---|---|---|
| gw0 | `test_learning_memory_control_centre.py::test_b6d_the_material_field_vocabulary_is_enforced_not_documented` | `objective_store.py:749 _set_status` → `conn.close()` | `db_ctx.connect()` directly (sync, not `wal_db`) |
| gw2 | `test_memory_agency_review_fixes.py::test_queued_outcome_is_independent_of_inbox_size` | aiosqlite worker thread, `core.py:63` → `close` | `open_memory_db()` (aiosqlite) |
| gw3 | `integration/test_fts_unavailable_vector_quality.py::test_vector_quality_maintained_when_fts_unavailable` | `vector_store.py:316 upsert` → `db_ctx.py:610 wal_db` → `close_quietly` | `db_ctx.wal_db()` (sync) |

The run's SQLite cost table (top rows, verbatim figures): every heavy test spent **91–98 % of its
wall time in connect + commit + close**, at 56–134 ms per close and 72–111 ms per commit — the
FTS test 576 opens, 40.6 s commit, 77.1 s close, **120.0 s** wall. The runner was degraded: two
setup phases took 55.6 s and 58.1 s. Earlier runs of the heavy test on the A1 head took 57.1–94.9 s
(`docs/WINDOWS_RELIABILITY_INCIDENT_2026_09_24.md` §7).

**Not visible, and not claimed.** The log API returns the last 5,000 of 12,592 lines, and the
`junit-windows-full` artifact (per-phase SQLite fields, `sqlite_slow` events, full stack files)
could not be downloaded from this session's sandbox (its storage host is refused by the proxy). So
the heavy test's own cost row, gw0's crash lines and the killed phases' SQLite counters are not
available here. `summarise_trace` prints neither a timeout's phase-elapsed/SQLite fields nor any
`sqlite_slow` summary (§8).

## 2. Diagnosis (before any change)

### 2.1 Verified root cause — one ownership defect

Every storage operation owns its own connection, and **between operations nothing holds the file
open**, so every close is SQLite's *last* close of a WAL database. A last close checkpoints the WAL
into the database file (with its fsyncs) and unlinks `-wal` and `-shm`; the next operation recreates
them and its first commit writes into a fresh WAL.

- Linux census of the whole default suite (5,693 tests): **41,855 of 50,436 closes unlinked the WAL**;
  MemoryStore 11,137 / 11,677, ObjectiveStore 2,631 / 2,639, VectorStore 2,479 / 2,712. In the three
  killed tests, **every** close did (e.g. 1,062 / 1,062 in the heavy test).
- strace, per 20 operations, unscoped: a vector upsert 80 `fdatasync` + 40 `unlink`; an ObjectiveStore
  write-and-reread 80 `fdatasync` + 80 `unlink`; even a read-only `is_revoked` 40 `unlink`.
- That is the same mechanism the 2026-09-18 decision measured and repaired for `wal_db()` callers
  that adopted `db_session()`. It was left in place everywhere else.

### 2.2 Lifecycle map of the three paths

| | ObjectiveStore `_set_status` (gw0) | MemoryStore (gw2) | VectorStore `upsert` (gw3) |
|---|---|---|---|
| Opens | `self._connect()` → `db_ctx.connect()` + `set_wal_pragmas` | `open_memory_db()` → `aiosqlite.connect` + policy pragmas (one OS thread per connection) | `wal_db()` → `connect` + `set_wal_pragmas` |
| Unit of work | one state transition (`BEGIN IMMEDIATE`, update, event insert), then a re-read on a second connection | one method; `upsert_memory` = `is_revoked` + write (or `record_pending_write`) + chunking/embeddings connections | one embedding row |
| Commits | `commit` in the method | each write method | each upsert |
| Closes | `conn.close()` (749), and again for the re-read | `await db.close()` on the aiosqlite thread | `close_quietly` (610) |
| Last close? | yes — 9 / 9 and 27 / 27 in `test_b6d` | yes — 1,062 / 1,062 in the heavy test | yes — 180 / 180 in the gw3 test |
| Can `db_session()` cover it? | no — never goes through `wal_db` | no — aiosqlite, its own thread, async | yes, on the scope's thread only (not the executor-thread upserts inside `upsert_memory`) |
| Natural bounded caller scope | the test's propose/edit rounds; in the product, one transition or one re-engagement drive pass (inference) | the test's burst; in the product, a request or an event-processing pass (inference) | the test's seeding loop; `_upsert_all`; the embeddings rebuild loop |

**Verified:** one mechanism, three different connection seams. Borrowing (`db_session()`) reaches
only one of them.

### 2.3 The measured alternatives (Linux — mechanism only, not Windows performance)

The heavy test's own body, three ways (`upsert_memory` ×520 queued, then list/correct):

| | connects | commit s | close s | WAL torn down | wall s |
|---|---|---|---|---|---|
| A — unchanged | 1057 | 0.41 | 0.57 | after 520 of 520 operations | 3.2 |
| B — one idle policy-configured connection held for the burst | 1058 | 0.035 | 0.061 | never | 2.0 |
| C — one aiosqlite connection *borrowed* by successive operations | 7 | 0.028 | 0.010 | never | 0.39 |

**Verified hazards of C** (borrowing an aiosqlite connection; experiments in the diagnosis):
- a second task sharing the connection lost a write — task A's rollback discarded task B's insert,
  and B's commit became a no-op. `ContextVar`s are inherited by child tasks, so a context-bound
  session would reach `gather`/`create_task` children, including the fire-and-forget one at
  `parking_brake.py:128`;
- a partly read multi-row cursor pins a stale read snapshot while `in_transaction` reports False; the
  next write then fails at once with `SQLITE_BUSY_SNAPSHOT`, whatever `busy_timeout` says;
- a failed operation leaves an open transaction the next borrower would commit;
- eight sites set `row_factory` and never restore it;
- `executescript` commits a pending transaction;
- closing with a still-referenced cursor leaves a zombie that keeps handles and any write lock;
- a double cancellation during `close()` can end the wait with the handle still open;
- aiosqlite is unpinned (`>=0.19`), and its internals change across that range.

C would also leave ObjectiveStore, GovernanceStore, the sync chunk seam and the executor-thread
vector upserts uncovered — except through B's effect.

**Why the rest of the per-operation cost barely matters on Windows (inference from verified
figures).** In the four A1-head Windows runs the heavy test's time *outside* commit + close was only
2–4 s of 57–95 s. That remainder is the per-connection overhead B keeps and C removes (thread start,
connect, setup pragmas, non-last close). The teardown B removes is the ~90 %.

Also rejected: raising the 120 s timeout or the 40-minute cap, fewer xdist workers, skipping,
xfailing or shrinking the tests (all forbidden, and they remove the signal, not the cost), a pool or
a process-lifetime connection (forbidden; `tests/test_vector_store_handle_lifetime.py`), and using
the sync `db_session()` as an incidental anchor from async code (it would rely on an undocumented
side effect and do blocking setup on the event loop).

## 3. The repair

**Chosen: B — a declared, bounded unit of work that holds the database open and lends nothing.**
It removes the one verified defect for every seam at once, while every operation keeps exactly the
connection lifecycle it has today. It extends the 2026-09-18 principle ("a SQLite connection is
owned by a bounded unit of work") in its strictest form: the unit of work owns a connection, and
nobody else ever uses it.

### 3.1 Code (`bartholomew/kernel/memory_store.py`)

- `memory_unit_of_work(db_path, *, label="")` — an async context manager beside `open_memory_db()`.
  - It refuses a database that does not exist, and one that is not in WAL mode. On a
    rollback-journal file an idle connection holds no lock, so the scope would silently do nothing.
  - It opens **one** connection through `open_memory_db()`: the shared policy, the 30 s setup
    budget, then the 5 s operational budget.
  - It attaches that connection with fully consumed reads (`PRAGMA journal_mode`, then
    `SELECT count(*) FROM sqlite_master`). A connection that has read nothing holds nothing open.
  - It yields nothing, and closes the connection when the scope ends. The close is shielded, so a
    repeated cancellation cannot end the scope while the handle is still open (aiosqlite runs a
    queued close whether or not anyone waits for it).
- `MemoryStore.unit_of_work(label="")` — delegates with the store's path. No store method calls it.

### 3.2 Contract

1. **Operations are unchanged.** Every operation inside the scope opens, configures, uses and closes
   its own connection. Transactions, rollback-on-close, cursors, `row_factory`, foreign keys,
   `synchronous=NORMAL`, and the 30 s setup / 5 s operational budgets are exactly what they are
   outside a scope. Governance is untouched.
2. **The held connection is idle.** It runs its setup and two consumed reads, then nothing. It holds
   no transaction and no read snapshot, and it blocks no reader, no writer, no `BEGIN IMMEDIATE` and
   no TRUNCATE checkpoint (verified). Its effect is that no other close to the file in the process is
   the last one. SQLite keeps its lock bookkeeping per process, so this covers every thread and every
   seam.
3. **Bounded and not a pool.** It exists exactly for the `async with`. It is closed on success, on an
   exception and on cancellation. Nothing is registered at the Python level: no module state, no
   context variable, no attribute on the store. Re-entering opens a new connection. Nested scopes each
   hold their own. The scope's own close is the one last close: it checkpoints the WAL into the
   database file and removes `-wal`/`-shm`.
4. **Explicit.** A caller declares where its unit of work begins and ends (DECISIONS.md, 2026-09-18,
   alternative (d)). No production caller adopts it in this package.

### 3.3 What a scope changes, stated rather than hidden

- **Durability.** `synchronous=NORMAL` is unchanged, and so is durability across an application
  crash (verified: a process SIGKILLed mid-scope recovered every committed row, and
  `integrity_check` passed). Without a scope, each operation's last close also checkpointed and
  fsynced — stronger than NORMAL promises, and obtained by accident, only when that operation happened
  to be the last connection. Inside a scope, committed work stays in the WAL until an automatic
  checkpoint or the scope's end. That is exactly NORMAL's documented guarantee: the most recent
  transactions may roll back on power loss or an OS crash. **This is not a durability-policy change,
  but it is a real difference, and it is why production adoption is its own decision (§9).** The
  database file alone is not a complete copy until the scope ends. Afterwards it is: that is tested.
- **Exclusive access** fails while a scope holds the file: changing `journal_mode`,
  `locking_mode=EXCLUSIVE`, and on Windows deleting or renaming the file. No product code does any of
  these.
- **POSIX locks** (Linux/macOS). The held connection's lock is a process-wide `fcntl` lock. Forking, a
  second process on the file, or a raw `open()`/`close()` of the file inside a scope can drop or
  bypass it — as for any open SQLite connection, but for the scope instead of one operation. None of
  these belong inside a scope, and the adopted tests do none of them. SQLite may keep closed
  connections' descriptors until the scope ends; they are released with it.

### 3.4 Adoption in this package — the three tests killed on Windows

Each was killed inside the same last-close teardown on Merge Candidate 36153552522. Each now declares
its burst as one unit of work. **No assertion, workload, timeout or marker changed.** The 520-row
workload is intact, and every operation still runs the full governed path on its own connection.
This is the same kind of adoption PR #113 made for the containment bursts.

| Test | Scope | Why this is the unit of work |
|---|---|---|
| `test_queued_outcome_is_independent_of_inbox_size` | `store.unit_of_work()` around the 520 writes and the list/write/correct that follow | one burst of governed writes and the reads that judge it |
| `test_vector_quality_maintained_when_fts_unavailable` | `store.unit_of_work()` around corpus ingestion; the approved sync `db_ctx.db_session()` around the `VectorStore.upsert` loop (it writes through `wal_db`, so it borrows as designed) | two seeding bursts; retrieval and assertions stay outside |
| `test_b6d_the_material_field_vocabulary_is_enforced_not_documented` | `ctx.mem.unit_of_work()` around the nine propose/edit rounds | one burst of runtime-contract operations; every store still opens its own connections |

`tests/test_memory_unit_of_work.py::test_each_burst_killed_on_windows_declares_its_unit_of_work`
pins the three scopes. Removing one would reinstate the defect silently, because the test would
still pass locally.

## 4. Pre-fix control and post-fix evidence (Linux — mechanism, not Windows performance)

**Causal control, in the test suite.** The same governed writes are run unscoped and scoped:

| Test | Unscoped | In a scope |
|---|---|---|
| `test_control_unscoped_every_operation_tears_the_wal_down` / `test_inside_a_unit_of_work_no_operation_tears_the_wal_down` | WAL torn down after 5 of 5 writes | after 0 of 5 writes, then once at scope exit |
| `test_the_scope_covers_every_connection_seam_on_any_thread` (aiosqlite, `open_memory_db_sync`, `wal_db` and ObjectiveStore's `db_ctx.connect`, the sync ones on worker threads) | every seam tears it down | no seam does |

**Execution-trace counters for the three killed tests** (call phase, `-n 1`, two runs each). Before
is `1adf251` in a separate worktree; after is this branch.

| Test | opens (before → after) | commits | commit s | close s | wall s |
|---|---|---|---|---|---|
| heavy `test_queued_outcome…` | 1062 → 1063 | 529 | 0.47–0.49 → 0.04 | 0.56–0.58 → 0.08–0.09 | 3.4–4.1 → 2.2–2.3 |
| `test_vector_quality…` (FTS) | 576 → 488 | 367 | 0.34–0.35 → 0.04 | 0.42–0.44 → 0.06–0.07 | 3.2–3.3 → 2.3 |
| `test_b6d…` | 376 → 377 | 188 | 0.13–0.14 → 0.01–0.02 | 0.17–0.18 → 0.04 | 1.37 → 0.96–1.04 |

- The **opens are essentially unchanged**: every operation still owns its connection, plus one for
  the scope. The FTS test's 88 fewer opens are its `VectorStore.upsert` loop borrowing the approved
  `db_session()`.
- The **teardown is paid once per scope instead of once per operation**, and commit + close fall
  about 8–10× on Linux.
- **Inference:** on Windows, where commit + close were 91–98 % of these tests' wall time, the
  proportional effect should be much larger. **Only the Windows run in §7 can say.**

**Mutation check of the forbidden-state tests.** Each deliberately broken implementation must fail
at least one test. Every one did:
- a scope that holds nothing;
- an unshielded release;
- a release that never closes;
- a held connection that pins a snapshot;
- one that holds a write transaction;
- one that is lent to the caller;
- one that bypasses `open_memory_db()`;
- a module-level registry of held connections;
- a store method that opens a scope implicitly;
- a scope without the existence check or without the WAL check.

## 5. Forbidden-state tests (`tests/test_memory_unit_of_work.py`)

| Would fail if the repair became… | Test |
|---|---|
| ineffective (the causal control) | `test_control_unscoped_every_operation_tears_the_wal_down`, `test_inside_a_unit_of_work_no_operation_tears_the_wal_down`, `test_the_scope_covers_every_connection_seam_on_any_thread` |
| silently ineffective on an uninitialised or non-WAL file, or created the file | `test_a_scope_on_a_database_that_does_not_exist_yet_is_refused`, `test_a_scope_on_a_non_wal_database_is_refused_and_released` |
| borrowing / cross-task, cross-thread or cross-loop sharing | `test_every_operation_inside_still_owns_its_own_connection` (exactly one extra connection; it ran only its setup and reads), `test_concurrent_tasks_threads_and_loops_in_the_scope_use_their_own_connections` |
| a configuration bypass | the same ownership test (every connection, held or not, starts with the policy statements under the 30 s budget), `test_an_operation_inside_the_scope_runs_under_the_shared_policy` |
| a permanent pool or registry | `test_nothing_anywhere_can_find_or_keep_the_held_connection` (module state, containers, context variables, the store — during and after), `test_re_entering_opens_a_new_held_connection_rather_than_reusing_one` |
| a connection or handle leak | `test_nothing_survives_the_scope`, `…_released_when_the_body_raises`, `…_released_when_the_body_is_cancelled`, `test_repeated_cancellation_cannot_end_the_scope_before_its_handle_is_released`, `test_a_held_connection_whose_setup_fails_is_closed_and_the_error_raised` — each checked **inside the event loop, immediately after the scope exits** (after `asyncio.run()` returns, an abandoned async context manager has already been finalised, so a check there proves nothing) |
| an unbounded or implicit session | `test_no_production_code_opens_a_memory_unit_of_work_implicitly` |
| an implicit transaction or snapshot spanning operations | `test_the_held_connection_pins_no_snapshot_and_blocks_no_one` (another writer's `BEGIN IMMEDIATE` at `timeout=0`; TRUNCATE checkpoint returns busy 0 and a 0-byte WAL; the next read in the scope sees the write), `test_an_uncommitted_write_inside_the_scope_does_not_persist` |
| a lost rollback on failure | `test_a_failed_operation_inside_the_scope_leaves_nothing_behind` |
| a weakened governance path | `test_governed_outcomes_are_identical_inside_and_outside_a_scope` (stored, queued, refused, forgotten, `refused_revoked` read fresh inside the scope, correction queued; identical outcomes and table contents; the inbox still encrypted at rest) |
| an incomplete database file at scope end | `test_when_the_scope_ends_the_database_file_alone_holds_every_committed_row` |
| the adoption removed | `test_each_burst_killed_on_windows_declares_its_unit_of_work` |

## 6. Tests run locally (Linux, Python 3.11.15, SQLite 3.45.1, aiosqlite 0.22.1)

- `tests/test_memory_unit_of_work.py`: all passed.
- The three adopted tests: passed.
- SQLite / MemoryStore regression suites: 282 passed across 25 files, including the connection
  contract, `db_session` lifecycle, handle lifetime, WAL cleanup, WAL concurrency, event-loop
  convoy, MemoryStore concurrency stress, memory agency, FTS, seed ownership, clean start and daemon
  lifecycle.
- The full default suite: recorded in the pull request.

## 7. Windows Merge Candidate evidence

*Pending — filled in from the one qualification run on the exact candidate head. The evidence read
is the W13/W15/SQLite trace, not the badge.*

## 8. Remaining risks and unresolved observations

These are recorded, not repaired.

- **The other heavy tests with the same shape** still pay the per-operation teardown. From the same
  run's cost table:
  - `test_lexical_beats_vector_on_exact_rare_tokens` 69.2 s;
  - `test_privacy_gates_upheld` 61.6 s;
  - `test_recency_boost_flips_rankings_weighted` 58.3 s and `…_rrf` 55.4 s;
  - `test_hybrid_beats_single_channel` 50.6 s;
  - `test_lexical_top_k_coverage_on_rare_tokens` 38.8 s;
  - `test_recency_disabled_no_flip` 32.7 s.

  Each has its own ingestion loop, and there is no shared seeding helper. Adopting the scope there is
  the same repair at more callers; it is left as the next bounded item rather than widening this
  package. `test_uncontained_baseline_grows_without_bound` (39.9 s) is a deliberately uncontained
  baseline and must stay as it is.
- **Slow-marked tests run by the nightly Windows tier** (`-m ""`) have the same shape at larger scale:
  the other `test_memory_agency_review_fixes.py` tests, at 1,050–1,808 opens.
- **Production.** The product pays the per-operation teardown wherever no other connection happens to
  be open. Candidates for a declared unit of work (inference): one `correct_memory`/`supersede_memory`
  (about 15 connections), a request handler, an event-processing pass, the embeddings rebuild loop.
  Not adopted, because each is a design statement about where work begins and ends, and because of
  §3.3:
  - power-loss durability reverts to NORMAL's own;
  - several processes (daemon, CLI, API bridge) share one database, so the POSIX-lock caveats need
    review.

  `request_permission_to_store` can await an arbitrary consent handler mid-`upsert_memory`, so a scope
  spanning it would be unbounded.
- **The worker-loss stall cause is still unresolved** (incident §5). This package removes the
  dominant per-operation cost; it does not explain the runner-wide degradation (55–58 s setup phases),
  and it does not claim to.
- **Evidence gaps in the harness** (observable next time, not fixed here):
  - `summarise_trace` does not print a timeout's `phase_elapsed_s` or per-phase SQLite fields, nor any
    `sqlite_slow` summary;
  - the W13 step is skipped when the test step is cancelled;
  - artifacts could not be read from this session's sandbox.
- **A test-evidence weakness in the parent package, found here.** In
  `tests/test_memory_store_connection_contract.py`, `_is_closed()` probes a connection with `execute`.
  aiosqlite's connections belong to its worker thread, so from the test's thread that raises the same
  `ProgrammingError` whether the connection is open or closed (verified). For aiosqlite connections
  the probe therefore always reports "closed". The same tests' handle and file-removal checks still
  carry real evidence. This package's tests record closure through a connection subclass instead.
  **Recorded for PR #124's owner, not changed here.**
- **A documentation error in the lifecycle record.** `docs/SQLITE_CONNECTION_LIFECYCLE_REPAIR.md` §3
  says `wal_db()` is the choke point "every store already goes through", so every store would become
  scope-capable without code changes. That is not so: MemoryStore uses aiosqlite; ObjectiveStore,
  GovernanceStore and others call `db_ctx.connect()` directly; FTSClient calls `sqlite3.connect`. It
  is corrected there by an amendment note.
- **Incidental, pre-existing, not in scope:**
  - `test_fts_unavailable_vector_quality` sets `BARTHO_EMBED_ENABLED="0"`, which *enables* embeddings
    (`memory_store.py` checks `if not os.getenv(...)`) and leaks to later tests on the worker. This is
    already noted in `tests/test_competency_no_auto_promotion.py`.
  - `_heal_unindexed_memories` has no per-row rollback, so a partially applied reindex can be
    committed with later rows.
  - aiosqlite is not pinned in `requirements.lock`.

## 9. Proposed decision (for Taylor's User Approval Gate — not recorded in `DECISIONS.md` unless approved)

> *Proposal.* A caller that performs a burst of MemoryStore operations as one unit of work may
> declare it with `MemoryStore.unit_of_work()`. For the scope's duration this holds one idle,
> policy-configured connection that it never lends, so the WAL is torn down once per unit of work
> instead of once per operation; every operation keeps its own connection. Adoption is explicit
> and per caller. In this package it is adopted only by the three test bursts killed on Windows.
> Any production adoption is a separate decision that must weigh §3.3.

This does not decide, record or pre-empt the deferred future hybrid pooling model. It introduces no
pooling, no reuse of a connection by anyone, and no connection that outlives its scope.
