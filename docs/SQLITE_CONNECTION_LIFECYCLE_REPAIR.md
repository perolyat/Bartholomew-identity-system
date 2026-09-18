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
| Heavy-burst test ~108 s against a 120 s per-test timeout | Not re-measurable here (Linux runner) | Taken from the recorded Windows measurement; the *structure* it describes is reproduced below. |
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

## 5. Limitations and remaining risk

- **The decisive measurement is still owed by Windows CI.** Everything above is Linux. The
  mechanism is platform-independent and the Windows cost of the removed operations is higher, not
  lower, so the direction is not in doubt — but "the heavy-burst test now completes well inside
  120 s on Windows" is a claim only a Windows Merge Candidate run can make. Treat the Linux
  figures as the reason to expect it, not as the evidence for it.
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
4. **The one thing still required: a Windows Merge Candidate run on this branch showing
   `test_a_heavy_system_generated_burst_leaves_every_genuine_row_untouched` completing well inside
   the unchanged 120 s per-test timeout, and no worker killed by it.** Band 0's outstanding
   condition is *repeatably* all-green Windows completion, so more than one run is needed — the
   record before this package was two green and three red across four heads.

Until (4) exists, the correct statement is that the blocker is **technically repaired and
evidenced on Linux, pending Windows confirmation** — not that Band 0 is clear.
