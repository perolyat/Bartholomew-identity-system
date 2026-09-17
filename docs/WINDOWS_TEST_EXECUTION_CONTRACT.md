# Windows Test-Execution Contract

> **Status:** established 2026-09-16, after PR #110 (writer-lock / WAL repair) and PR #111
> (its approval record) merged as `6ccf693` and `d3c9992`. **Non-canonical** — a document
> under `docs/`; `CI.md` is the authority on which workflow runs when, `RISKS.md` on the
> disposition of the Windows Merge Candidate failures, `DECISIONS.md` on the decisions.
> Where this note and a canonical document disagree, the canonical document wins.
>
> **Owner:** `scripts/ci/xdist_contract.py` (worker ownership, replacement, work
> accounting) and `scripts/ci/execution_trace.py` (diagnostics), wired in the repository's
> root `conftest.py`. **Acceptance suite:**
> `tests/test_windows_execution_contract.py`, which drives both through real pytest runs
> rather than their internals, so any replacement of either module is measured against the
> same clauses.
>
> **Scope.** This is about the machinery that *runs* the tests — the pytest-xdist
> controller, its workers, and the trace that says what they did. It is not about the
> product's own execution contract, which is
> `docs/SKILL_EXECUTION_CONCURRENCY_CONTRACT.md` and is owned by
> `bartholomew/kernel/skill_registry.py`. The two share vocabulary — workers, queues,
> timeouts, cancellation — and nothing else.

## 1. Why this contract exists

The Merge Candidate tier's `windows-full` job runs the whole default suite with
`-n auto --dist loadfile` under a 40-minute cap. It has been cancelled at that cap on
roughly half of all attempts since 2026-08, and the writer-lock repair that closed the
`database is locked` class did not change that: PR #110's own evidence
(`docs/WINDOWS_WAL_WRITER_LOCK_REPAIR.md` §5.2) records six consecutive Windows runs, on
`main` and on the branch, every one of them cancelled at the cap.

Three distinct failures were folded together under "Windows is flaky".

**(a) The "stalled tail" — which turned out to be the wrong name for it.** Every other worker
finishes; one test on one worker sits for eighteen to twenty-four minutes and is reported
`PASSED` the instant the cap fires. It is a different test every run
(`test_lexical_beats_vector_on_exact_rare_tokens` in runs 34866265459, 34973153727,
35034729745 and 35037740445; `test_worked_example_round_trips_end_to_end` in run 34942899213,
with the rare-token test passing in 37 s that time), which is what rules out the tests
themselves. The 120-second per-test timeout does not end it. A cancelled job writes no junit,
so the only record is the console.

**The name described the symptom's location, not the defect.** Once W13 could count the
occurrences, run 35171239428 showed the underlying deadlock firing thirteen times in one run,
at roughly every work-unit boundary — the queue falling 50, 46, 42, … 2. A worker that starves
while three others still have work only slows the run; it is fatal the first time it catches
the last worker with nobody left to carry on. So the suite had been deadlocking constantly
all along, and the tail is simply where it stopped being survivable. A diagnosis framed around
"the last test" was always going to look like an unreproducible property of whichever test was
unlucky.

**(b) Worker loss.** `test_a_heavy_system_generated_burst_leaves_every_genuine_row_untouched`
makes 1000 `insert_nudge_contained` calls, each opening and closing its own SQLite
connection: 1.85 s on an idle Linux machine, more than 120 s on the Windows runner under
four workers. Past 120 s, pytest-timeout's `thread` method — the only method available on
Windows — calls `os._exit(1)`, so the whole worker process stops existing. The controller
sees `[gwN] node down: Not properly terminated` and nothing else.

**(c) Failed replacement, and work that is never run.** On the 16 Sep re-run of Merge
Candidate 35037740445, four of five workers died inside one minute and the controller then
crashed with `INTERNALERROR> KeyError: <WorkerController gw5>` in
`xdist/scheduler/loadscope.py`, ending the session at 61 %. Separately, run 34866265459
printed `1 failed, 4983 passed, 79 skipped` with **17 of its 5080 selected tests never
run** — the remainder of the crashed worker's file — and nothing in that summary said so.

**What no previous diagnosis could establish.** For (a), the controller's console shows a
`logstart` line and a `PASSED` line twenty minutes apart. That is the same output whether
the test's call phase ran for twenty minutes or the test finished in thirty seconds and
its report did not arrive until the cap. PR #110's record states the limit in those words:
"the controller log cannot distinguish a call phase that ended at the cap from a report
held back until it." Every conclusion after that point was inference from a global CI
cancellation, which is exactly what success gate 11 forbids.

## 2. Was the prior arrangement structurally capable?

No, on four counts, which is why the correction is at the harness boundary rather than in
any test.

1. **Only one end of each report was timestamped.** The controller recorded arrival; the
   worker recorded nothing. A single subtraction was unavailable, so the dominant failure
   mode could not be classified at all.
2. **The only bound on the run was imposed from outside it.** The job cap cancels the
   process tree, which produces no junit, no summary and no stacks. A run that cannot end
   itself cannot explain itself.
3. **Worker loss was total and silent.** `os._exit` leaves no report, no traceback and no
   record of what that worker still owned. Whether its work was ever re-run was not
   checked by anything.
4. **Replacement was assumed to succeed.** pytest-xdist creates a replacement worker and
   re-queues the dead one's work unit; nothing verified the replacement started, took the
   work, or that the work finished. The one path where replacement *cannot* succeed
   crashed the controller outright.

Rejected shapes: **raise the cap** — hides the failure and costs 40 minutes a run to learn
nothing; **retry the job** — a blind retry, and the brief forbids it; **drop to `-n 2` or
serial on Windows** — pushes a 14-minute job past the cap by arithmetic, and removes the
contention the suite exists to exercise; **mark the expensive tests `slow` so the Merge
Candidate skips them** — weakening the tier to obtain a green result; **`--max-worker-restart=0`** —
turns worker loss into an immediate whole-session failure, which is observable but throws
away every test that would still have run.

## 3. The contract

Every clause is enforced by `scripts/ci/xdist_contract.py` or
`scripts/ci/execution_trace.py` and pinned by a test in
`tests/test_windows_execution_contract.py`.

| # | Clause | Behaviour | Pinned by |
|---|---|---|---|
| W1 | **Worker creation and ownership** | Every worker's creation, readiness, collection and death is recorded on the controller with its gateway id and, for a death, the error text. | `…lost_worker_is_recorded_as_lost_and_not_as_a_slow_test` |
| W2 | **Work assignment** | Work is assigned per file (`--dist loadfile`), and what each worker owns at any moment is readable from the trace. | `…trace_brackets_every_report_at_both_ends` |
| W3 | **Worker loss detection** | A worker that stops existing is recorded as `node_down` with `crashed=true`, distinct from a worker that finished. A lost worker is never reported as a slow test. | `…lost_worker_is_recorded_as_lost_and_not_as_a_slow_test` |
| W4 | **Safe replacement** | A replacement worker is created and, once it has collected, is given the re-queued work. A second worker death while a replacement is still collecting does not crash the controller. | `…contract_scheduler_survives_a_still_collecting_replacement`, `…replacement_gets_the_requeued_work_once_it_has_collected` |
| W5 | **Replacement failure is bounded and observable** | If replacement cannot proceed, the run ends on the watchdog's bound with the queue state and every worker's stacks written down — never by silent attrition and never by the job cap. | `…controller_ends_a_stalled_run_itself_rather_than_waiting_for_the_cap` |
| W6 | **No silent loss of work** | Every nodeid the workers collected must produce a terminal report. Any that do not are named at session end and the run fails. | `…worker_that_dies_mid_file_cannot_end_the_run_green`, `…work_accounting_names_every_test_that_never_reported` |
| W7 | **An intentional stop is not loss** | `-x`, `--maxfail` and an internal error leave tests unrun by design; W6 stays quiet for those, so it never buries the real reason a run stopped. | `…intentional_early_stop_is_not_reported_as_lost_work` |
| W8 | **The run owns its own ending** | A controller that receives nothing for `BARTHO_EXEC_STALL_ABORT_S` ends the session itself, with a named reason and exit code 3, inside the job cap. | `…controller_ends_a_stalled_run_itself_rather_than_waiting_for_the_cap` |
| W9 | **Slow work is distinguishable from slow transport** | Each report is timestamped in the worker before it is sent and in the controller when it arrives. The difference is the transport delay; `scripts/ci/summarise_trace.py` prints it. | `…trace_brackets_every_report_at_both_ends` |
| W10 | **A stalled process says where it is** | A worker with no phase transition for `BARTHO_EXEC_STALL_WARN_S` dumps every thread's stack; so does the controller, whose execnet receiver threads are the only place a sent-but-unarrived report can be. | `…stalled_worker_writes_its_own_stacks`, the controller-stacks assertion in `…ends_a_stalled_run_itself…` |
| W11 | **Database contention is attributable per test** | Every phase records the number of SQLite connections it opened and the time spent opening them, so per-operation connection cost is a measurement rather than an argument. | `…trace_brackets_every_report_at_both_ends` |
| W12 | **The contract is not a diagnostic mode** | W4, W6 and W7 are always on, on every platform and in every tier. Only the trace (W9–W11) is switched on by the environment. | `…worker_that_dies_mid_file_cannot_end_the_run_green` runs with no trace enabled |
| W13 | **Queued work reaches an idle worker** | If no test completes for `BARTHO_XDIST_REDRIVE_IDLE_S` (default 8 s) while the scheduler holds queued work, a node is alive and not shutting down, and the controller's own event queue is empty, the scheduler is re-asked to assign — on the controller's main thread, through its own event queue. Every re-drive is counted and reported: a run that needed one completed **over a defect** and does not read as green. | `…deadlock_is_real_and_nothing_in_xdist_breaks_it`, `…redrive_assigns_queued_work_to_an_idle_node`, `…redrive_fires_only_when_work_is_queued_and_a_node_can_take_it`, `…redrive_reaches_the_controller_through_a_real_xdist_run`, `…run_that_needed_redriving_says_so` |
| W14 | **Progress means a test finished** | The stall and abort bounds count completed tests, not messages. Worker lifecycle chatter — a crash, a replacement starting, a replacement collecting — is recorded but never resets them. | `…only_a_finished_test_counts_as_progress` |

**What this contract does not do.** It does not change any timeout, any cap, any marker or
any test; it does not serialise execution. A run that fails under it fails for a stated
reason, which is the point.

**On W13 and the word "retry".** The brief this package was written to forbids blind retries
and timing-dependent workarounds, so it is worth being exact about what W13 is. It does not
re-run a test, re-run a job, or wait and hope. Its trigger is a conjunction of measured facts
about the scheduler's own state — queued work exists, a node is able to take it, the
controller has nothing pending — and its action is the single `_reschedule` call the
controller failed to make, executed on the thread pytest-xdist itself uses for scheduling. A
node that is genuinely busy has `_reschedule` return on its own pending count, so a re-drive
that was not needed is a no-op. The 60-second threshold is a detection bound, not a race: the
deadlock is permanent, so waiting longer only costs time.

What W13 does **not** do is fix the upstream defect. pytest-xdist's controller still stops
asking its scheduler for work; this contract notices and asks. That is a symptom-level
correction by necessity — the defect is in a dependency — and it is recorded as an unresolved
limitation in `docs/WINDOWS_MERGE_CANDIDATE_REPAIR.md` rather than treated as closed.

## 4. Diagnostics: the seven cases, and how each is told apart

Success gate 11 requires that a remaining failure be identified without inferring from a
global CI cancellation. Reading `scripts/ci/summarise_trace.py`'s output:

| Case | Signature in the trace |
|---|---|
| Product deadlock | Worker `stall` event; its stack dump shows the main thread blocked in product code (a lock, a join, a future). Transport delay ~0. |
| Worker loss | Controller `node_down` with `crashed=true`; that worker's JSONL has no `session_finish` and no `process_exit`; its last `phase` names the test it died on. |
| Slow work | Large `wall_s` on a `phase` event, small transport delay. The per-test SQLite counts say whether the cost is connection churn. |
| Failed replacement | `node_created` for a replacement with no following `node_collected`; controller `stall` showing that worker's work still queued. |
| Scheduler deadlock | Controller `stall` with `event_queue_depth=0`, `workqueue_units` above zero and every node `shutting_down=False`; each worker's stack in `xdist/remote.py`'s `TestQueue.get`. The summary prints "controller loop is IDLE (no event pending: the wakeup was lost)". |
| Database contention | High `sqlite_connections` and `sqlite_connect_s` on the slow phases, and worker stacks inside `sqlite3.connect` or a pragma. |
| Test-runner failure | Small `wall_s`, large transport delay, and controller stacks showing an execnet receiver thread — the report left the worker and did not arrive. |
| CI cancellation | No `abort` event, no `session_finish`, and the trace simply stops. Distinguishable from all of the above precisely because the watchdog would otherwise have written an ending first. |

## 5. Operating it

```
BARTHO_EXEC_TRACE=1 BARTHO_EXEC_TRACE_DIR=exec-trace \
  python -m pytest -n auto --dist loadfile
python -m scripts.ci.summarise_trace exec-trace
```

`BARTHO_EXEC_STALL_WARN_S` (default 180) is how long a process may make no progress before
it dumps stacks; `BARTHO_EXEC_STALL_ABORT_S` (default 900, `0` to disable) is how long the
controller may receive nothing before it ends the run. The Merge Candidate's Windows job
uses 180 and 600: the longest legitimate gap between reports in that suite is a few tens
of seconds, and 600 s is comfortably inside the 40-minute cap.

`BARTHO_XDIST_CONTRACT=0` disables the work accounting and the scheduler re-drive. It exists
so that a bug in either cannot block a release; it is not for making a red run green.
`BARTHO_XDIST_REDRIVE_IDLE_S` sets W13's detection bound (default 8 s). The bound is chosen
by how much dead time a recovery may cost, not by false-positive risk: a re-drive that was not
needed is a no-op, because `_reschedule` returns on the node's own pending count. Run
35171239428 needed thirteen recoveries, so a 60-second bound cost roughly fifteen minutes.

## 6. Evidence

Run-by-run results, the baseline reproduction and the measurements behind each root cause
are in `docs/WINDOWS_MERGE_CANDIDATE_REPAIR.md`.
