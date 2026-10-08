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
**Status (2026-10-03):** the head moved after that acceptance, from `a55c8f9` through `6125598`
(this record), `bfc207b` and `dd0a680` (a Codex finding and its disposition) to `1adf251` (W15 test
timing). The PR-triggered Merge Candidate on `1adf251` (36153552522) was **cancelled at the
40-minute cap** with three workers lost inside SQLite closes (§7, the `1adf251` row); Merge
Qualification refused that head, and Taylor's hold of 2026-09-27 (option 2) parked the PR unmerged
at `1adf251`
with no re-run. On 2026-10-03 current `main` (`2beeb6d`, the PR #126 drive-seam cancellation
repair) was merged into this branch by merge commit `7925644`; the one conflict,
`dispositions.yml`, was resolved by keeping both pull requests' records, and nothing in this
package's repairs changed. **Qualification and the §7 acceptance sequence restart from zero on the
new head.** The evidence on `a55c8f9` and the attempts on `dd0a680` and `1adf251` qualify nothing
later. The worker-loss stall cause (§5) is still unresolved, and the PR #126 repair does not
explain it. Not merged.
**Status (2026-10-05):** the §7 acceptance sequence **passed** on the unchanged head `8405905`
(`840590549cf64b9eea23018adda884ac1f169035`): PR-triggered Merge Candidate 37259077092, then three
consecutive dispatched Windows full-suite executions, Merge Candidate 37261089218, 37263416482 and
37265065826, each attempt 1, all seven jobs green, W13 clean, 4/4 workers finished normally, no
worker lost, no `database is locked` (§7). The head is `03c5f2d` plus `b9ab007` (the W13
contract-report checker accepts only this contract's own report; Codex finding
review_comment:4180458481, raised on `03c5f2d` before any acceptance run) and `8405905` (its
disposition). On `8405905` CI 37259077140, Integration 37259077099 and the PR-event Merge
Qualification 37259077090 are green; the hand-dispatched Merge Qualification 37260739733 is READY
and bound to this exact head; Codex found no major issues (PR #124 comment 5987558838). **Not
closed:** that is Taylor's decision. The worker-loss stall cause (§5) is still unresolved, and
heavy-test headroom (§6) stays deferred to PR #125. Not merged: Taylor's User Approval Gate is in
force.
**Status (2026-10-06):** **merged.** PR #124 was merged at Taylor's User Approval Gate as merge
commit `9ccaec9375338cab44e172515fc38e92d1662e78` (`b5402b1`, the head carrying this record's
acceptance rows, into `main` at `2beeb6d`; a GitHub merge commit, the repository's normal method;
the merge commit's tree is `b5402b1`'s). The earlier "not merged" statements above were true when
written and are left as dated history. The first Merge Candidate on `main` after the merge,
37399980843 (push, `9ccaec9`), **did not complete** (§7 post-merge row): its Windows full-suite job
was cancelled at the 40-minute job cap (GitHub's annotation on the job: "The job has exceeded the
maximum execution time of 40m0s"): the cap fired 37:50 into the test step, which never reported an
end, and GitHub force-closed the job 5 minutes later at 45:00, so no pytest summary is on record and
no log was served; its Critical py3.11 job lost its runner 3:25 into its test step ("The runner has
received a shutdown signal"), with no test failed; the other five jobs were green. It is kept as an
attempt and was not re-run; whether to re-run it is Taylor's decision. *(Re-run once at Taylor's
decision on 2026-10-06, failed jobs only, as attempt 2 of the same run: the Windows full-suite job
112146570889 and the Critical py3.11 job 112146571953 both passed, and the run's conclusion is now
`success` (§7, the attempt-2 row). Attempt 1 stays on record as written; the re-run establishes
that `main` at `9ccaec9` can pass this tier and neither repairs nor invalidates attempt 1, whose
cause stays unknown. The "was not re-run" statement above was true when written; corrected here,
2026-10-07. "Did not complete" remains true of attempt 1.)* **Not closed:** the merge
closes nothing by itself, and closure remains Taylor's decision. The lock, seed and W13 repairs are
on `main`; the worker-loss stall cause (§5) is still unresolved; heavy-test headroom (§6) stays with
PR #125, which is unchanged by the merge and still to be retargeted and requalified under its own
gate; gate 8 of `docs/WINDOWS_MERGE_CANDIDATE_REPAIR.md` is still unclaimed: the acceptance runs
that record and `RISKS.md` wait for now exist and passed (§7, rows 1–3 on `a55c8f9` and on
`8405905`), this update does not claim it, and whether it is now claimed is not decided here.
**Status (2026-10-08):** the gate 8 post-merge acceptance sequence on `main`, run at Taylor's
direction on the unchanged head `54c851b`, **stopped at Run 1/3: 0 of 3 clean** (§7, the gate 8
Run 1/3 row). That run's tests passed; its W13 step failed on a re-drive counted by a defect in
W13's own counting — established from the note's queue depth and the source, the moment itself
not observed — not on a scheduler stall. The defect is repaired in PR #128 (draft, unmerged;
`RISKS.md`, 2026-10-08 W13 entry), and the sequence restarts from Run 1/3 on the head that repair
produces, if Taylor approves it. **Not closed;** gate 8 is not claimed.

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
*(2026-10-08: the start-up false positive that occurred was a different one — a re-drive counted
from the watcher's snapshot taken inside pytest-xdist's initial `schedule()` — and it failed gate
8's post-merge Run 1/3 (§7), a run whose tests all passed. In PR #128 (unmerged) the count is
decided by the controller, and a proposal the controller never handles also fails the step; the
case named above, and what is still counted that is not a stall, are restated in `RISKS.md`'s
2026-10-08 W13 entry. This note leaves the words above as written.)*

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
finding on PR #124; the first wording overclaimed.)

*Limitation, observed.* The evidence and the kill race: they are two threads, and the evidence
gets a head start of 10 s at the production 120 s timeout (a tenth of a shorter one). If the
evidence thread is delayed by more than that -- as a runner-wide stall could delay it -- the kill
can land before the stacks are written, or between the event and the stack file. With a 0.5 s
head start that happened once in CI (§7, `dd0a680`). It never changes, prevents or delays the timeout.

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
  On `8405905` (§7) it ran 61.2, 82.3, 56.3 and 58.4 s across four runs, close 33–50 s: measured,
  not claimed improved (the run-to-run variance on one head is already recorded as 1.6x, and nothing
  since `a55c8f9` touches that path). Taken up by PR #125 as its own package; still deferred here.
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
| post-acceptance | Merge Candidate 36149262520 / job 108118576298 (PR-triggered) | `dd0a680` | **failed** — 1 failed, 5589 passed, 103 skipped | The failure was this package's own W15 test, `test_the_evidence_spans_setup_and_call_as_the_timeout_does`: its inner run's `timeout_imminent` event was written (phase `call`, elapsed ≥ 4 s), but `gw0.timeout.txt` never was. With a 5 s inner timeout the evidence had 0.5 s before the kill, and on the loaded runner the kill landed in between. Not reproduced on Linux (8x CPU oversubscription, 3 of 3 passed). Fixed at the next head: the killed-test cases use a 20 s inner timeout (a 2 s head start; production has 10 s), the inner run no longer re-runs the killed test on four replacement workers (which kept each test at about 20 s), and a missing stack file now says whether the kill pre-empted the write or the write failed. Otherwise: no worker lost, no stall, no `database is locked`, W13 clean. Heaviest test 79.6 s (close 49.9 s, commit 27.4 s). |
| post-acceptance | Merge Candidate 36153552522 / job 108132502235 (PR-triggered) | `1adf251` | **cancelled at the 40-minute job cap** — the test step ran 37 min and was cancelled; no pytest summary; the W13 step was skipped | Three workers lost to the 120 s per-test timeout, each with W15 evidence taken 10 s before the kill and the test's thread inside a SQLite connection close: gw0 `tests/test_learning_memory_control_centre.py::test_b6d_the_material_field_vocabulary_is_enforced_not_documented` (`objective_store._set_status`), gw2 `tests/test_memory_agency_review_fixes.py::test_queued_outcome_is_independent_of_inbox_size` (the MemoryStore `aiosqlite` close), gw3 `tests/integration/test_fts_unavailable_vector_quality.py::test_vector_quality_maintained_when_fts_unavailable` (`db_ctx.wal_db` via `vector_store.upsert`; 77 s in close, 41 s in commit). None was hung. One further failure on gw4, `tests/test_objective_store.py::…::test_the_window_holds_when_everything_happens_in_the_same_second`, a wall-clock assertion. 22 W13 re-drives followed once the replacement workers started. The runner was slow throughout: ordinary setups took 55–58 s. This is the heavy-test headroom route §6 and §7 name, the per-operation connection close, not the lock or seed defects this package repaired; it is W15's first real capture. Not re-run: Taylor's hold of 2026-09-27 (PR #124 comment 5853881682) parked the PR at this head, and a separate package stacked on it (PR #125) took up the headroom item. Under the 2026-09-21 qualification decision a cancelled job refuses: Merge Qualification 36153552561 refused `1adf251`. Reported in PR #124 comment 5835485341. |
| — | Merge Candidate 37145536077 / job 111268566815 (PR-triggered) | `03c5f2d` | **passed** — all 7 jobs; W13 step passed | Ordinary checks on the reconciled head (`main` at `2beeb6d` merged by `7925644`, plus this record's 2026-10-03 update): CI 37145536097, Integration 37145536072 and Merge Qualification 37145536079 also green. Windows: no worker lost, no stall, no `database is locked`. Heaviest test 61.3 s (close 36.5 s, commit 20.9 s). Not an acceptance run: the fresh Codex review requested on this head found review_comment:4180458481 (the W13 contract-report checker accepted any JSON object carrying `"redrives": 0`, without checking schema or clause), repaired at `b9ab007` with its disposition at `8405905`, so the head moved before any dispatched run. |
| — | Merge Candidate 37259077092 / job 111602247638 (PR-triggered) | `8405905` | **passed** — all 7 jobs; W13 step passed | Ordinary checks on the candidate head: CI 37259077140, Integration 37259077099 and Merge Qualification 37259077090 also green; the hand-dispatched Merge Qualification 37260739733 (`pr=124`, run from this branch) is READY, `merge-qualified at 840590549cf64b9eea23018adda884ac1f169035`, its checkout fetched by SHA and the `dispositions.yml` it read proven this head's own by the records that exist only here. Codex found no major issues on `840590549c` (PR #124 comment 5987558838). Windows: no worker lost, no stall, no `database is locked`. Heaviest test 61.2 s (close 36.1 s, commit 20.9 s). The sequence's criteria (expected counts, per-run checks, timing thresholds) were registered in PR #124 comment 5987779241 before Run 1 was dispatched. |
| **1** | Merge Candidate 37261089218 / job 111608282761 (dispatched, attempt 1) | `8405905` | **passed** — all 7 jobs; W13 step passed | `5639 passed, 103 skipped, 166 warnings in 1236.49s (0:20:36)`. 4/4 workers finished, each `session_finish=yes process_exit=yes`; no stall; no `database is locked`; "what failed: nothing". Heaviest test **82.3 s** (close 49.9 s, commit 27.9 s) — 69 % of the budget. The slowest Windows job of the three on every substantive step: its clean-start and scheduler-readiness steps ran at about twice the PR-triggered run's and its install step about a quarter longer (clean-start 20 s against 8 s, scheduler readiness 19 s against 11 s, install 55 s against 44 s): the unexplained runner-slowdown pattern already recorded on `1adf251`, which PR #125 tracks as its follow-up FU-3. **Observation only:** inside every registered threshold (test step 20:39 against a stop at 28:06; heaviest test under the 100 s flag), nothing failed, and no repair is claimed. |
| **2** | Merge Candidate 37263416482 / job 111615150799 (dispatched, attempt 1) | `8405905` | **passed** — all 7 jobs; W13 step passed | `5639 passed, 103 skipped, 166 warnings in 991.94s (0:16:31)`. 4/4 workers finished normally; no stall; no `database is locked`; "what failed: nothing". Heaviest test 56.3 s (close 33.2 s, commit 18.9 s). Setup steps back in line with the PR-triggered run. |
| **3** | Merge Candidate 37265065826 / job 111620025938 (dispatched, attempt 1) | `8405905` | **passed** — all 7 jobs; W13 step passed | `5639 passed, 103 skipped, 166 warnings in 1061.18s (0:17:41)`. 4/4 workers finished normally; no stall; no `database is locked`; "what failed: nothing". Heaviest test 58.4 s (close 34.4 s, commit 20.1 s). |
| post-merge | Merge Candidate 37399980843 / job 112064764414 (push to `main`) | `9ccaec9` | **failed** — the Windows job was **cancelled at the 40-minute job cap** (GitHub's annotation on the job: "The job has exceeded the maximum execution time of 40m0s"): the cap fired at 02:16:12Z, 37:50 into the test step (started 01:38:22Z); the runner never completed the cancellation (the step is recorded in progress with no end time, and the W13 step and the two `if: always()` upload and trace-summary steps are recorded pending), and GitHub force-closed the job at 02:21:12Z, at the end of its 5-minute cancellation timeout (job 45:00; 42:50 after the step started); no pytest summary is on record; the Critical integration + lifecycle (py3.11) job 112064764424 failed 3:25 into its test step on a runner loss (the log reads "The runner has received a shutdown signal", then "The operation was canceled"; 91 tests had passed and none failed); Quality, smoke, Tests + coverage (py3.10 and py3.11) and Critical (py3.10) were green | The first run of the merged tree on `main` (`9ccaec9` = `b5402b1` into `2beeb6d`; the trees are identical). GitHub served no log for the cancelled Windows job at the time of writing (HTTP 404), so whether a worker was lost, whether W15 fired and which test was running at the cap are **not known here**; the only evidence is the job and step timing and the annotation above. The runner loss on the Critical job of the same run is recorded as observed, not as the Windows job's cause. This run establishes nothing about the merged head either way: the merge stands on the `8405905` acceptance rows above (`b5402b1` is docs-only on `8405905`: it carries those rows and changes no code) and on the tier runs on `b5402b1` itself, recorded in the merge commit message rather than in this table (CI 37280890914, Integration 37280891312, Merge Candidate 37280891006 with all seven jobs, and Merge Qualification 37280890885 and 37283584700, READY at `b5402b1`); this is a post-merge verification of `main`. **Not re-run** at the time of writing; whether to re-run it is Taylor's decision, and no attempt is dropped. *(Re-run once on 2026-10-06 at Taylor's decision: the attempt-2 row below. These words were true when written; corrected 2026-10-07.)* |
| post-merge, attempt 2 | Merge Candidate 37399980843 attempt 2 / job 112146570889 (re-run of the failed jobs only, at Taylor's decision, 2026-10-06) | `9ccaec9` | **passed** — `5639 passed, 103 skipped, 166 warnings in 1313.94s (0:21:53)`; W13 step passed; the Critical integration + lifecycle (py3.11) job 112146571953 passed (`315 passed, 25 skipped` in 12:43, then 6, 10 and 17 passed); the run's conclusion is now `success` | Only the two non-green jobs re-executed (both started 06:53:07Z); the five green attempt-1 jobs are carried over unchanged into attempt 2, the repository's normal re-run form (as for 35971892908). Test step 21:56 (06:55:09Z to 07:17:05Z), under the 28:06 threshold. 4/4 workers finished, each `session_finish=yes process_exit=yes`; no stall; no `database is locked`; "what failed: nothing". Heaviest test 89.5 s (close 54.8 s, commit 30.7 s): below the 100 s flag and the 94.9 s clean maximum, above every clean figure on the heads after `a55c8f9` in this table (56.3–82.3 s). Setup steps slow again (install 55 s, clean-start 12 s, scheduler readiness 15 s), the pattern of Run 1 above. Artifact `junit-windows-full` 11396596359 (expires 2026-11-05). Attempt 1 (the row above) is kept as written and is not explained by this: no attempt-1 Windows log was served when the row above was written (HTTP 404), and a fetch of that job's log on 2026-10-07 still returned HTTP 404 for its content, so which test was running at the cap and whether a worker was lost stay unknown here. What this establishes: `main` at `9ccaec9` can pass this tier. What it does not: it neither repairs nor invalidates attempt 1, it is not an acceptance run (those are dispatched, attempt 1, on an unchanged candidate head), and it claims nothing for gate 8 or for closure. |
| gate 8 post-merge, Run 1/3 | Merge Candidate 37703362526 / job 113071834174 (dispatched, attempt 1) | `54c851b` | **failed** — the W13 step failed on one counted scheduler re-drive (`no test completed for 18s while 252 unit(s) queued and a node able to take them`); the test step passed, `5639 passed, 103 skipped, 166 warnings in 911.58s (0:15:11)`; the six other jobs were green | The post-merge gate 8 acceptance sequence on `main`, at Taylor's direction (2026-10-07): three consecutive dispatched runs on the unchanged head, one at a time, its criteria registered before dispatch in commit comment 203949027 on `54c851b` (PR #124 comment 5987779241's, unchanged; expected counts from Merge Candidate 37617163012, the push-triggered run on this head, all seven jobs green, not counted). `main` read `54c851b` before dispatch, at dispatch and at completion, and no other run was created during it. Windows: 4/4 workers finished, each `session_finish=yes process_exit=yes`; no stall; no `database is locked`; "what failed: nothing"; heaviest test 54.5 s (close 32.4 s, commit 18.0 s); setup steps on the fast pattern (install 41 s, clean-start 8 s, scheduler readiness 10 s); artifact `junit-windows-full` 11518889168 (expires 2026-11-06). Tests + coverage `5740 passed, 2 skipped` on py3.10 and py3.11 (79.88 %, 79.85 %), W13 clean on both; Critical `315 passed, 25 skipped`, then 6, 10 and 17, on both; every test step under its registered threshold. **The re-drive was a false positive of W13's own count, not a scheduler stall:** the watcher counted a snapshot taken inside pytest-xdist's initial `schedule()`, where 252 of the 253 file units are queued beside registered nodes that hold nothing yet, and the re-drive changed nothing. The mechanism is reproduced; this run's instance is established from the note's own queue depth (252 exists only inside the initial distribution of this collection) and no worker was lost, but the moment of the re-drive was not observed, because the job log's head and the artifact could not be read from the investigating session. It was not the first: Integration run 36519235007 (2026-09-29) failed the same way. A re-driven run counts as failed here, so the sequence stopped at Run 1/3 — **0 of 3 clean**; Run 2/3 was not dispatched and nothing was re-run. Recorded in commit comment 203956165; the counting defect, its repair (PR #128, unmerged) and the corrections to that comment are in `RISKS.md`'s 2026-10-08 W13 entry. The repair moves the head, so the sequence restarts from Run 1/3 on the new head, at Taylor's decision. |

**Result (2026-09-25, `a55c8f9`).** Three consecutive clean Windows full-suite executions on one
unchanged head, preceded by a clean Merge Candidate and green ordinary CI and Merge Qualification
on that head. The one failed attempt in this record (above) was on an earlier head and is kept.

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

**Result (2026-10-05, `8405905`).** The sequence restarted from zero on the new head, as the
2026-10-03 status required, and passed: three consecutive clean dispatched Windows full-suite
executions (Merge Candidate 37261089218, 37263416482, 37265065826), each attempt 1, on the one
unchanged head `840590549cf64b9eea23018adda884ac1f169035`, preceded by a clean PR-triggered Merge
Candidate and green CI, Integration and Merge Qualification on that head. The branch tip was read
from the forge at every dispatch and every completion and never moved; no Merge Candidate was in
flight when a run was dispatched; no attempt was discarded or repeated. In every run: all seven
jobs succeeded; Windows `5639 passed, 103 skipped`; 4/4 workers finished with `session_finish`
and `process_exit`; no scheduler re-drive (W13 clean in the Windows job and both Ubuntu xdist
jobs); no `database is locked`, `node down`, `TESTS LOST` or `Not properly terminated`; Tests +
coverage `5740 passed, 2 skipped` on both Pythons (coverage 79.84–79.91 %, gate 70 %); Critical
`315 passed, 25 skipped` then 6, 10 and 17 passed on both Pythons; no test step crossed its
registered threshold (Windows 20:39, 16:33, 17:42 against 28:06). The failed and cancelled
attempts above, on `4be5b44`, `dd0a680` and `1adf251`, are kept. Reported in PR #124 comment
5988506124.

**What this establishes (`8405905`).** On the reconciled head, with the W13 checker repaired: no
`database is locked` in four Windows full-suite runs; no W13 re-drive in any of them, with a
re-drive, a missing report and a report that is not this contract's all unable to pass as clean;
no worker lost. The lock, seed and W13 defects this package repaired did not recur.

**What this does not establish (`8405905`).**
- **The worker-loss stall cause is still unresolved.** No worker was lost in these four runs, so W15
  captured nothing here. Its real captures remain the `1adf251` run above and, through PR #125's
  branch, the `gw3` loss in PR #125's acceptance run 2 (Merge Candidate 36566521591 on `2906a81`,
  2026-09-29; `docs/SCHEDULER_DRIVE_CANCELLATION_REPAIR.md` §1). Four clean runs on this head are
  not evidence that the stall behind gw0/gw2, or the `1adf251` losses, cannot recur.
- **Heavy-test headroom is live and stays deferred.** The heaviest test,
  `tests/test_memory_agency_review_fixes.py::test_queued_outcome_is_independent_of_inbox_size`,
  took 61.2, 82.3, 56.3 and 58.4 s across the four runs on this head (close 33–50 s, commit 19–28
  s). These are lower than the `a55c8f9` figures, but within the 1.6x run-to-run variance already
  recorded, and nothing since `a55c8f9` touches the connection-teardown path: A1 is in both heads,
  and `git diff a55c8f9..8405905` changes neither `db_ctx.py` nor `memory_store.py` (the commits
  since then are this record, the W15 summary wording and test timing, dispositions, the merge of
  `main` with the PR #126 drive-seam repair in `runtime_contract.py`, and the W13 checker repair).
  **No improvement is claimed.** The repair is PR #125's package, stacked on `1adf251`, to be
  retargeted and requalified after this PR; its state is not changed by this record.
- **Run 1's slowness is an observation, not a finding.** Its clean-start and scheduler-readiness
  steps ran at about twice the other runs' (install about a fifth longer) and its heaviest test
  spent 49.9 s in SQLite close. That is the unexplained runner-slowdown pattern already recorded on
  `1adf251` (there, pytest setup phases of 55–58 s; here, the job's own setup steps), which PR #125
  tracks as its follow-up FU-3; its cause is unresolved, and this run stayed inside every registered
  threshold.

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
