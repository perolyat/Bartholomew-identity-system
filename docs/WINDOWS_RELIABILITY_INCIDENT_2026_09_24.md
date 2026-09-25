# Windows full-suite reliability incident, 2026-09-24 — the record

**Incident:** Merge Candidate run 35971892908, attempt 1, on `main` at `7b21a00` (the PR #123
merge). Job 107543292415, "Windows full default suite + actuation (py3.11)", failed:
`3 failed, 5553 passed, 103 skipped in 1352.18s`.
**Airtable:** Risks and Open Questions row `recmtR5E4IOkvPjxo` (a working index; this file and
`RISKS.md` are the durable authority).
**Package branch:** `claude/windows-ci-reliability-incident-mc59cm`.
**Status (2026-09-25):** repair implemented, and the §7 acceptance sequence **passed** on the
unchanged head `a55c8f9` (three consecutive clean Windows full-suite executions, preceded by a
clean Merge Candidate and green ordinary CI). **Not closed:** that is Taylor's decision, and the
worker-loss stall cause is still unresolved (§5, §7 "What this does not establish"). Not merged.

This record keeps four kinds of statement apart, and labels each: **verified** (demonstrated by
a log, a runtime measurement or a reproduction), **repaired** (a verified defect this package
changes), **observable** (a cause this package cannot yet prove, made diagnosable for next time),
and **unresolved** (hypothesis, or merely not excluded).

---

## 1. What happened

Three failures in one job, on a tree that passed the same job before (#253 attempt 2, #254) and
after (attempt 2 of this run, job 107552466911: 5553 passed, no worker lost, no re-drive):

1. `tests/test_exec02_conversational_executive.py::TestFailureDegradesSafely::test_no_deliberation_port_keeps_the_literal_reading`
   — worker `gw2` crashed, "Not properly terminated". The trace shows 120.2 s in setup.
2. `tests/test_schedule_reminder_drive.py::TestGovernance::test_mute_defers_the_delivery_and_says_so`
   — worker `gw0` crashed, "Not properly terminated". 31.2 s in setup, the rest in the call.
3. `tests/test_learning_control_centre_api.py::test_a_correction_supersedes_and_a_stale_one_conflicts`
   — `sqlite3.OperationalError: database is locked`, raised from `_seed_candidate` →
   `MemoryStore.init()` → `executescript(SCHEMA)`, while the module's live app (a `KernelDaemon`
   with its scheduler) ran on the same file. Its own drive logged
   `inbound_event_processing timed out after 5.00s`, and a tick took 13 s.

The run also printed `xdist contract: the scheduler stalled and was re-driven 3 time(s)` and
concluded `failure`; the one re-run concluded `success`. **The re-run established that `main`
can pass. It did not repair or invalidate attempt 1.**

## 2. Verified

**Worker losses.**

- Both were ended by pytest-timeout (`timeout = 120`, `timeout_method = "thread"`,
  `pyproject.toml`). gw2's 120.2 s is the budget. Reproduced locally: a slow fixture under xdist
  with the thread method gives exactly `node down: Not properly terminated` / `worker 'gwN'
  crashed`.
- **They were undiagnosable by construction.** pytest-timeout prints its stack dump to the
  worker's terminal, which xdist discards (reproduced). The trace's own dumps were armed per phase
  at `BARTHO_EXEC_STALL_WARN_S` = 180 s, while the timeout spans setup, call and teardown together
  at 120 s — so no trace dump could ever precede the kill, and gw0's 31 s + 89 s shape would not
  have tripped a per-phase dump even at 120 s.
- The two tests are light: 0.2 s and 0.05 s setup locally, 1.6 s and 0.6 s on the same Windows
  runner when re-queued minutes later. A 75× spread inside one job is not a steady per-operation
  cost.

**SQLite connection configuration (runtime measurements on `main`, 2026-09-25).**

| Setting | Scope | Needs a DB lock to set | Fresh operational `MemoryStore` connection | `db_ctx.wal_db` (shared authority) |
|---|---|---|---|---|
| `journal_mode` | persistent in the file | yes | `wal` | `wal` |
| `synchronous` | connection | **yes** (loads the schema) | **FULL (2)** | NORMAL (1) |
| `foreign_keys` | connection | no | **OFF (0)** | ON (1) |
| `busy_timeout` | connection | no | 5000 ms from the first statement | **30 s during setup**, then 5000 ms |

- `MemoryStore`'s SCHEMA declares `PRAGMA synchronous=NORMAL; PRAGMA foreign_keys=ON`; the shared
  policy (`db_ctx.set_wal_pragmas`, `docs/B0_PERSISTENCE_BASELINE.md` §4) applies NORMAL/ON/5000;
  five `MemoryStore` call sites already set `foreign_keys = ON` by hand, one with a comment saying
  why. Those pragmas are connection-local, so they reached the `init()` connection and those five
  sites only. The other ~31 operational connections ran on SQLite's defaults.
- **The shared authority's 30 s is not dead.** `connect(timeout=30)` installs a 30 s busy handler
  and `set_wal_pragmas()` runs its lock-needing setup statements under it before
  `busy_timeout = 5000` replaces it. Measured: a `db_ctx.wal_db()` connection's setup waits out an
  8 s exclusive hold (8.04 s); a `MemoryStore`-style connection gives up at 5.01 s. `RISKS.md`'s
  2026-08-22 entry ("dead in practice") was wrong for exactly these statements; corrected there.

**The lock mechanism (reproduced, Linux).**

- When the last connection to a WAL database closes, SQLite checkpoints the WAL and deletes
  `-wal`/`-shm` while holding the database's EXCLUSIVE lock. A new reader's first statement waits
  through it on its busy handler and fails if the close outlasts the budget: a 1.1 s last close
  failed a 50 ms reader with the identical `database is locked`; with a 5 s budget it succeeded.
- Only a connection's first lock acquisition is exposed: once it holds its read lock, no other
  connection's close can be the "last" one (measured: the peer's close took 0.000 s and kept the
  WAL). Nothing in this process held a connection open, so every close with no peer was a last
  close.
- In the incident, the seed's ObjectiveStore calls (shared authority, 30 s at setup) succeeded;
  its first `MemoryStore` connection (5 s at setup) failed. Both observations of this signature
  (Merge Candidate #147, and this one) failed at that same point.

**Seed ownership.** The live app initialises its database exactly once, at startup
(`KernelDaemon.start()` → `MemoryStore.init()`, `ObjectiveStore(...)` → `ensure_schema()`). The
seed helpers then re-ran the whole of `init()` — schema DDL, migrations, FTS init, the index heal —
and `objective_store.ensure_schema()`, from a second lifecycle, against that live database:
**28 times** in `test_learning_control_centre_api.py`, **14** in `test_memory_agency.py`
(counted at runtime).

**W13.** A re-driven run exits 0 and its job concludes `success`; Merge Qualification requires
`success` on four xdist jobs (CI "PR Fast tests", Integration "Tests + coverage", Merge Candidate
"Tests + coverage" ×2, Merge Candidate "Windows full") and could count a re-driven run as clean,
contrary to clause W13's own text.

## 3. Repaired

**A1 — MemoryStore connection contract** (`bartholomew/kernel/memory_store.py`,
`bartholomew/kernel/db_ctx.py`). Every `MemoryStore` connection now goes through
`open_memory_db()` / `open_memory_db_sync()`, which follow the shared authority's lifecycle
(Taylor's decision D1(b), 2026-09-25):

1. open with the 30 s setup budget (`db_ctx.SETUP_LOCK_TIMEOUT_S`);
2. under it, `PRAGMA synchronous = NORMAL` (the lock-needing setup statement) and
   `PRAGMA foreign_keys = ON` (`db_ctx.CONNECTION_SETUP_PRAGMAS`, the list `set_wal_pragmas`
   itself now uses);
3. only then `PRAGMA busy_timeout = 5000` (`db_ctx.OPERATIONAL_BUSY_TIMEOUT_PRAGMA`);
4. hand the connection over; close it when the unit of work ends, including on a failed setup.

`journal_mode` is persistent and is not re-issued. No permanent connection and no pool:
ownership is exactly as before. The five hand-written `foreign_keys` pragmas are gone (the seam
guarantees it), and the one `with sqlite3.connect(...) as conn:` that relied on garbage collection
to close its handle now closes explicitly.

- *What it repairs:* `synchronous` and `foreign_keys` now obey the declared policy on every
  connection (a configuration-consistency repair, not a new durability choice: NORMAL is what
  SCHEMA and the shared authority already declare). The reproduced lock mechanism is repaired for
  any competing close shorter than the 30 s setup budget — the same protection the seed's
  ObjectiveStore calls already had.
- *Foreign keys — compatibility:* the default suite passed with enforcement on every connection
  (5,652 passed; the only two failures were a pre-existing local path-wrapping artefact that also
  fails on unmodified `main` and passes with CI's `COLUMNS=200`). Both `DELETE FROM memories` paths
  already ran with enforcement; `memories.id` is `AUTOINCREMENT` (no id reuse). The one new refusal
  is a child insert racing a concurrent parent delete — the embedding insert that follows it
  already refuses that today (`wal_db` enforces foreign keys). Enforcement is not retroactive: a
  file holding orphans still opens and works.
- *Not claimed:* that the CI lock holder's window was under 30 s. It was over 5 s and is otherwise
  unobserved.

**A2 — seed architecture** (`tests/test_learning_control_centre_api.py`,
`tests/test_memory_agency.py`, `tests/helpers/live_app_db.py`). The seeds work through the state
the app established: a `MemoryStore` without `init()` (which sets no instance state), and the
live kernel's own `objective_store`. `forbid_schema_work_from_the_test_thread()` is active for
each module's live app and fails any `MemoryStore.init()` / `objective_store.ensure_schema()`
against the owned file from the test's thread.

**A3 — W13 restored** (`scripts/ci/xdist_contract.py`, the four required xdist jobs). The
controller writes `xdist-contract.json` when `BARTHO_XDIST_CONTRACT_REPORT` names it; a step named
"W13 clean-run contract (no scheduler re-drive)" after the test step fails the job if the run was
re-driven, or if there is no report (fail closed — `BARTHO_XDIST_CONTRACT=0` cannot produce clean
evidence). pytest's exit status, the re-drive itself and local runs are unchanged. The job
conclusion is the enforcement point because it is the one thing Merge Qualification reads.
*Known limitation:* the re-drive counts any node `_reschedule` would top up while work is queued
and nothing completed for the idle bound. That state does not persist in a healthy run except,
in principle, at start-up (a node whose first units total two tests or fewer, one of them slow).
None was seen in a healthy local full-suite run (`-n 8`) or in attempt 2; if one appears, the
report's notes identify it.

## 4. Observable (diagnosable next time; not claimed fixed)

**B1 — pre-kill evidence** (`scripts/ci/execution_trace.py`, clause W15). An observer on
pytest-timeout's own `pytest_timeout_set_timer` / `_cancel_timer` hooks arms one long-lived thread
per worker to fire shortly before the deadline (10 s, or a tenth of a short timeout), across
setup, call and teardown together. It writes `timeout_imminent` (test, phase, elapsed, phase
elapsed, live threads, SQLite counters) and every thread's stack to `<worker>.timeout.txt`, and
`summarise_trace` reports a lost worker as "per-test timeout (120s) imminent in `<phase>`" with the
stacks — or says there was no timeout evidence. It says "imminent", not "expired", and that a
different death in the remaining seconds is not excluded: the evidence proves the deadline was near,
and the kill (`os._exit`) leaves no record of its own. (Corrected before merge after a review
finding on PR #124; the first wording overclaimed.) It never changes, prevents or delays the timeout.

**B2 — lock correlation** (clause W16). Any single commit or close taking at least
`BARTHO_EXEC_SLOW_SQLITE_S` (default 1 s) becomes a `sqlite_slow` event with its file, thread and
wall time; a phase failing with `database is locked` becomes `sqlite_locked_failure`; the summary
lists overlapping slow operations on the same worker, **labelled correlation, not proof of lock
ownership.**

## 5. Unresolved

- **Why the two light tests exceeded 120 s.** Leading hypothesis (medium-low): a transient
  runner-wide stall early in the session — gw1 spent 31.9 s and 40.6 s in `MemoryStore.init()` on
  private fresh files at the same time, where cross-process lock contention is impossible. A hang
  or thread starvation is not excluded. B1 exists so the next occurrence answers this.
- **Which connection held the lock, and for how long** (beyond "over 5 s"). Best explanation
  (medium): a concurrent last close by the live daemon during degraded disk.
- **Whether the two symptoms share a cause.** Not established.
- **The 5,659th outcome** (5,656 tests; two re-queued re-runs account for two). Needs the
  attempt-1 artifact (10797093743, expires 2026-10-24), which this session's sandbox could not
  download.

## 6. Deferred, each tracked in `RISKS.md` and Airtable

- Orphaned msedge / Notepad processes from the actuation step, present in both attempts.
- Heavy-test headroom — re-measured on Windows after A1 (§7): the heaviest test ran 57.6–94.9 s
  of its 120 s budget across four runs on `a55c8f9`, dominated by close. **Not improved by A1.**
- No audit of existing field databases for orphan child rows written while operational
  connections ran with foreign keys off.
- The Linux xdist jobs run without the execution trace, so a timeout kill there stays
  undiagnosable.
- Already recorded, cross-referenced only: the upstream xdist lost-wakeup defect; the drive-seam
  5 s timeout; the 2026-08-22 startup-window `database is locked` entry.

## 7. Acceptance — required before this incident can close

Focused regression tests; the normal CI; Merge Candidate on the exact candidate head; then
**three consecutive** Windows full-suite executions on that same unchanged head. A failed
execution breaks the sequence and is investigated before a new one starts; no attempt is dropped
from this record. With W13 now enforced, a re-driven run counts as failed. Results are appended
below as they happen.

| # | Run / job | Head | Result | Notes |
|---|---|---|---|---|
| pre-acceptance | Merge Candidate 36118579395 / job 108018401102 (Windows full) | `4be5b44` | **failed** — 1 failed, 5588 passed, 103 skipped | The one failure was this package's own new control test (`test_a_default_connection_would_have_failed_under_the_same_hold`) asserting a wall-clock ceiling: the bare connection *did* fail with `database is locked` as intended, but SQLite's 5 s busy handler ran to 6.3 s of wall time on the loaded runner, past the test's 6.0 s ceiling. The same time-budget-assertion class `RISKS.md` records. Fixed at the next head by asserting causally (the hold was still in force when it gave up) and widening the hold to 9 s. Otherwise: no worker lost, no stall, no re-drive banner; the heaviest test ran 57.1 s (71.7 s in attempt 2 of the incident run), its commit time 19.3 s (36.9 s). Not an acceptance run: the sequence starts once ordinary checks are green on an unchanged head. |
| — | Merge Candidate 36120642805 / job 108025063418 (PR-triggered) | `a55c8f9` | **passed** — all 7 jobs; W13 step passed | Ordinary checks on the candidate head: CI 36120642794, Integration 36120642907 and Merge Qualification 36120642877 also green. Windows: no worker lost, no stall, no `database is locked`. Heaviest test 75.0 s (close 47.0 s, commit 25.9 s). |
| **1** | Merge Candidate 36122898029 / job 108032322449 (dispatched) | `a55c8f9` | **passed** — all 7 jobs; W13 step passed | No worker lost, no stall, no `database is locked`. Heaviest test **94.9 s** (close 58.1 s, commit 33.2 s) — 79 % of the budget. |
| **2** | Merge Candidate 36126510276 / job 108043755393 (dispatched) | `a55c8f9` | **passed** — all 7 jobs; W13 step passed | No worker lost, no stall, no `database is locked`. Heaviest test 57.6 s (close 34.3 s, commit 19.6 s). |
| **3** | Merge Candidate 36129492255 / job 108053221935 (dispatched) | `a55c8f9` | **passed** — all 7 jobs; W13 step passed | No worker lost, no stall, no `database is locked`. Heaviest test **92.1 s** (close 56.8 s, commit 31.8 s). **Observation, unexplained:** worker `gw0` wrote `session_finish` but not `process_exit`; the controller recorded it as finished, not crashed, and every one of its tests reported. Not a worker loss under W3; recorded rather than dismissed. |

**Result.** Three consecutive clean Windows full-suite executions on one unchanged head, preceded by
a clean Merge Candidate and green ordinary CI and Merge Qualification on that head. The one failed
attempt in this record (above) was on an earlier head and is kept.

**What this establishes.** On `a55c8f9`: no `database is locked` in four Windows full-suite runs
(the seed path that raised it twice no longer re-initialises a live database, and every
`MemoryStore` connection now has the 30 s setup budget); no W13 re-drive in any of them, with the
re-drive now unable to pass as clean; no worker lost.

**What this does not establish.**
- **The worker-loss stall cause is unresolved.** No worker was lost in these runs, so the new
  pre-kill evidence (W15) has not yet had anything to capture. Four clean runs are not evidence
  that the transient stall behind gw0/gw2 cannot recur.
- **Heavy-test headroom is live, and A1 did not improve it.** The heaviest test,
  `tests/test_memory_agency_review_fixes.py::test_queued_outcome_is_independent_of_inbox_size`,
  took 75.0, 94.9, 57.6 and 92.1 s across the four runs (57.1 s on `4be5b44`; 71.7 s in attempt 2
  of the incident run). Its cost is dominated by connection close (34–58 s): the per-operation
  last-close checkpoint the 2026-09-17 decision leaves in place. `synchronous=NORMAL` did not
  measurably move it. At 94.9 s a transient slowdown of about a quarter would reach the timeout,
  so this is the likeliest route to the next worker loss — and W15 will now say so if it happens.
- The heaviest-test figures vary by 1.6x between runs on one head, so no single run's figure is
  a measurement of the fix.

## 8. Forbidden-state tests

| Would fail if… | Test |
|---|---|
| a setup statement ran on the 5 s budget | `test_memory_store_connection_contract.py::test_the_contending_setup_statement_waits_on_the_setup_budget` (with its control, `…default_connection_would_have_failed…`) |
| the operational budget were not 5 s after setup | `…::test_after_setup_the_connection_runs_on_the_operational_budget` |
| setup order changed, or any `MemoryStore` connection skipped it | `…::test_setup_runs_first_and_drops_to_the_operational_budget_last`, `…::test_every_connection_memory_store_opens_is_configured_first`, `…::test_the_sync_seam_has_the_same_contract` |
| a raw connection path reappeared | `…::test_memory_store_has_no_connection_path_outside_the_seam` |
| a connection were left open, or a failed setup leaked one | `…::test_every_connection_is_closed_when_its_unit_of_work_ends`, `…::test_a_connection_whose_setup_fails_is_closed_and_the_error_raised`, `…::test_the_sync_seam_closes_on_error_too` |
| an orphan row could be written, a cascade stopped, or enforcement became retroactive | `…::test_an_invalid_relationship_is_refused_on_an_operational_connection`, `…::test_deleting_a_memory_still_removes_its_dependent_rows`, `…::test_a_database_that_already_holds_an_orphan_still_works` |
| a seed re-ran schema work against a live app | `test_live_app_seed_ownership.py` (runtime guard and structural checks) |
| a timeout kill left no evidence, or evidence restarted per phase | `test_windows_execution_contract.py::test_a_timeout_killed_worker_leaves_its_stacks_phase_and_elapsed_time`, `…::test_the_evidence_spans_setup_and_call_as_the_timeout_does` |
| a completed test left false evidence, or a non-timeout loss were called one | `…::test_a_test_that_finishes_leaves_no_timeout_evidence`, `…::test_a_worker_lost_without_a_timeout_is_not_called_a_timeout` |
| a lock failure could not be set against slow operations, or ordinary ones were flagged | `…::test_a_locked_failure_is_listed_with_overlapping_slow_sqlite_operations`, `…::test_ordinary_operations_are_not_reported_as_slow` |
| a re-driven run could satisfy the clean-run check, or the re-drive were disabled | `…::test_a_redriven_run_still_recovers_but_cannot_satisfy_the_w13_check`, `…::test_a_clean_run_satisfies_the_w13_check`, `…::test_the_w13_check_fails_closed`, `…::test_every_required_xdist_job_enforces_w13` |
