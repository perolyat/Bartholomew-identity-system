# Windows Merge Candidate Repair — Evidence

> **Status:** in progress, 2026-09-16. Companion to
> `docs/WINDOWS_TEST_EXECUTION_CONTRACT.md`, which states the contract; this file carries the
> runs, measurements and root causes behind it. **Non-canonical.**
>
> **Starting state:** `main` at `d3c9992` (PR #111), after PR #110 merged as `6ccf693`. The
> writer-lock / WAL repair and the skill-execution concurrency contract are merged and are not
> reopened here; `docs/WINDOWS_WAL_WRITER_LOCK_REPAIR.md` §8 items 1 and 7 are the two findings
> this package picks up.

## 1. Baseline reproduction, from unchanged `main`

**Run 35081868613**, `push` of `d3c9992`, job *Windows full default suite + actuation (py3.11)*
(job 104747517771). Nothing on the branch; this is the authoritative starting state.

| | |
|---|---|
| Job started | 09:52:38 UTC |
| Test step started | 09:54:36 |
| Last progress of any kind | **10:10:20.303** — `[gw4] [ 99%] PASSED tests/test_scenario_replay.py::TestScenarioReplay::test_full_multi_turn_session_coheres` |
| Last `logstart` | 10:10:19.964 — `tests/test_competency_worked_example.py::test_worked_example_round_trips_end_to_end` (gw0) |
| Silence | **22 min 26 s**, no output from any worker |
| Then | 10:32:46.703 `[gw0] [ 99%] PASSED tests/test_competency_worked_example.py::test_worked_example_round_trips_end_to_end` |
| Then | 10:32:46.725 `##[error]The operation was canceled.` — **22 ms later** |
| junit | never written (`No files were found with the provided path: junit-windows-full.xml`) |
| Other six jobs | all green |

The other six jobs of the same run passed, including both Ubuntu coverage jobs against the 70 %
gate, both Critical integration jobs, quality, smoke and real-Win32 governed actuation. The
Ubuntu full default suite finished in **9 min 10 s** (10:21:00–10:30:13 on the branch run; 09:53:01–10:02:11 on
this one). The Windows job did not finish in 40.

**Everything the earlier record describes is reproduced exactly:** the last test of the last
worker, a different test each run, reported `PASSED` as the cap fires, the 120-second per-test
timeout ending nothing, and no junit.

### 1.1 The 22-millisecond gap, across six runs

| Run | Head | Stalled test | Silence | PASSED to cancellation |
|---|---|---|---|---|
| 34866265459 | `5fb9c88` | `test_lexical_beats_vector_on_exact_rare_tokens` (gw2) | 24 min | 14 ms |
| 34942899213 | `830f554` | `test_worked_example_round_trips_end_to_end` (gw0) | 20 min | — |
| 34973153727 | `6094b9a` | `test_lexical_beats_vector_on_exact_rare_tokens` (gw4) | 18 min | — |
| 35034729745 | `04db28d` | `test_lexical_beats_vector_on_exact_rare_tokens` (gw4) | 22 min | — |
| 35037740445 | `8cf3707` | `test_lexical_beats_vector_on_exact_rare_tokens` (gw1) | 22 min | — |
| **35081868613** | **`d3c9992`** | **`test_worked_example_round_trips_end_to_end` (gw0)** | **22 min 26 s** | **22 ms** |

Two tests, five workers, six heads, and in both runs where the ordering is recorded to the
millisecond the final report lands within a few tens of milliseconds of a cancellation the run
knows nothing about. A test genuinely finishing does not land within 22 ms of an independent
forty-minute deadline twice.

That is strong evidence that **the cancellation is what delivers the report**, not the test
finishing — but it is evidence about correlation at one end of a channel, and the controller's
console cannot see the other end. It is not yet a root cause, and this record does not treat it
as one. §2 is the measurement that decides it.

**What the baseline also rules out.** The `logstart` for the stalled test *did* arrive
(10:10:19.964), so the worker-to-controller channel was carrying messages moments before the
silence began. Whatever happens, happens between that `logstart` and the first phase report.

## 2. The first instrumented run, and what it overturned

**Run 35085435731**, `workflow_dispatch` on `1ee0131`, job 104759104143. Test step started
10:33:39; the controller ended the session itself at **10:59:38**, on its own 600-second
bound, **fourteen minutes before the job cap**. Artifacts were written and uploaded (11 files);
the job is red for a stated reason rather than cancelled with nothing. That is clauses W5 and
W8, and success gate 11, demonstrated on the first attempt.

What it recorded:

```
controller STALL after 180.2s; last=report:…test_full_multi_turn_session_coheres:teardown  queued_units=4
controller STALL after 360.2s; …
controller STALL after 540.3s; …
controller ABORT after 600.3s; …
    gw1: 1 outstanding ['…test_identity_policy_change_flips_a_future_tool_shaped_candidate_action']
    gw2: 1 outstanding ['…test_uncontained_baseline_grows_without_bound']
    gw3: 1 outstanding ['tests/unit/kernel/test_time_utils.py::test_utc_now_iso_format']
    gw4: 1 outstanding ['…test_no_raw_sqlite_connect_in_api']

gw1 STALL after 180.0s in …test_full_multi_turn_session_coheres::after-teardown
gw2 STALL after 181.4s in …test_memory_manager_data_dir_follows_barth_db_path::after-teardown
gw3 STALL after 181.5s in …test_metrics_has_tick_counter::after-teardown
gw4 STALL after 180.1s in …test_liveness_self_ok::after-teardown
```

**Two findings, both of which change the shape of the problem.**

**(a) No test is running.** `after-teardown` is the trace's label for the interval *after* a
test's teardown report has been written and *before* the next test's `logstart`. All four
surviving workers are in it. The tests they name are finished. Nothing was waiting on a
database, a lock, or a computation — the previous explanations all required a test to be
executing, and none is.

**(b) The four workers stopped together.** Their stall bounds were reached at 180.0, 181.4,
181.5 and 180.1 seconds — within about one and a half seconds of each other, on four unrelated
files. Four independent hangs do not synchronise. Something common to all of them stopped, and
the controller's last processed event was a teardown report, with four work units still queued
and one outstanding test per worker.

The reading this supports is a **harness-level deadlock between the controller and its
workers**: the workers are idle waiting to be told what to run next, and the controller is not
telling them. `gw0` is absent from the outstanding list — it had finished its work and shut
down cleanly, which is why four and not five.

**This is not yet the root cause, and the difference from §1 matters.** In the baseline the
stalled test's `logstart` *did* arrive and its report did not, so there a test had started; here
no test had started at all. Those may be two faces of one defect or two defects, and nothing
measured so far decides it. The controller's own stack — which names the thread it is sitting
in — is what settles it, and the first run wrote it to a file this environment cannot reach
(see §2.1).

### 2.1 Two defects in the instrument itself, found by using it

Recorded because they cost a run each, and because an instrument that has not been used on the
real failure has not been tested.

1. **The summary crashed before printing anything.** Phase events carry a `threads` integer and
   stall events a `threads` list of names; one sorted list of both raised
   `TypeError: '<' not supported between instances of 'list' and 'int'`. The phase field is now
   `live_threads`.
2. **The stack dumps went somewhere unreadable.** They were written correctly and uploaded as an
   artifact — and CI artifacts are served from blob storage this environment's egress policy
   refuses, so the evidence the run existed to produce could not be opened. The summariser now
   echoes the tail of the controller's dump, each worker's dump and each worker's GIL-free dump
   into the job log. A diagnosis that requires downloading a zip is a diagnosis that does not
   arrive.

## 2.2 Root cause: a lost-wakeup deadlock in the pytest-xdist controller

**Run 35088270721**, `workflow_dispatch` on `ed03789`, job 104768193559. The controller ended
the session on its own bound again, and this time the stacks reached the job log. Three of
them, taken at the same moment on the Windows runner, settle it.

**The controller's main thread, idle on an empty event queue:**

```
  File ".../Lib/threading.py", line 331 in wait
  File ".../Lib/queue.py", line 180 in get
  File ".../site-packages/xdist/dsession.py", line 154 in loop_once
  File ".../site-packages/xdist/dsession.py", line 138 in pytest_runtestloop
```

**Its four execnet receiver threads, each blocked reading from a worker:**

```
  File ".../site-packages/execnet/gateway_base.py", line 534 in read
  File ".../site-packages/execnet/gateway_base.py", line 1160 in _thread_receiver
```

**Every worker's main thread, blocked waiting to be given work:**

```
  File ".../Lib/threading.py", line 327 in wait
  File ".../site-packages/xdist/remote.py", line 90 in get          # TestQueue.get()
  File ".../site-packages/xdist/remote.py", line 214 in run_one_test
  File ".../site-packages/xdist/remote.py", line 206 in pytest_runtestloop
```

The workers are waiting for the controller to send work. The controller is waiting for an event
from the workers. **Four work units are queued and four healthy workers are able to run them.**
Neither side will ever move. It ends only when something outside the run kills it, which before
this package was the 40-minute job cap.

**Precisely, and this matters for the correction:** the controller is not blocked forever.
`loop_once` is

```python
while 1:
    if not self._active_nodes:
        self.triggershutdown()
        raise RuntimeError("Unexpectedly no active workers available")
    try:
        eventcall = self.queue.get(timeout=2.0)
        break
    except Empty:
        continue
```

so the main thread wakes **every two seconds**, checks only whether every node has died, and
goes back to waiting. The scheduler is never consulted on that wakeup. So the run is not
deadlocked on a lock that cannot be released; it is a live loop that has stopped asking whether
there is work to hand out. That is a far cheaper thing to correct, and it is correctable at the
scheduler boundary this package already owns rather than by patching pytest-xdist.

**This also disproves the reading the evidence had been pointing at.** The controller is not
blocked in `channel.send`; `queue.get()` is waiting on an *empty* queue, so the loop is idle,
not stuck. The transport was never the problem. The two readings need opposite corrections, and
the inference from the 22-millisecond gap (section 1.1) would have selected the wrong one.

**Why a worker can starve at all** is structural, and visible in `xdist/remote.py`:

```python
def run_one_test(self) -> None:
    self.item_index = self.nextitem_index
    self.nextitem_index = self.torun.get()         # blocks
    ...
    self.config.hook.pytest_runtest_protocol(item=item, nextitem=nextitem)
    self.sendevent("runtest_protocol_complete", ...)  # what wakes the controller
```

The worker fetches the index *after* the one it is about to run, so that
`pytest_runtest_protocol` can be given a correct `nextitem`. A work unit under `--dist loadfile`
is one file and its indices are sent in a single batch, so **a worker always blocks before the
last test of its unit** and can only proceed once the controller assigns another unit. The
controller assigns one only while handling a completion event. The wake-up and the work are
mutually dependent: remove one event from that loop and it stops permanently.

`_reschedule` tops a node up when its pending count is at or below two, which normally lands the
next unit before the worker reaches that point. What is not yet established is why it did not
fire here, with the pending count at one and four units queued. The two gates that would explain
it are `node.shutting_down` and a momentarily empty queue. Both are now recorded (section 2.3),
and the correction is not written until that is measured, because the two want different fixes.

## 2.3 The confirming measurement

**Run 35090997379**, `workflow_dispatch` on `439345c`, job 104777009526. The controller
recorded, twice:

```
controller STALL after 184.9s; last=report:...test_lexical_beats_vector_on_exact_rare_tokens:teardown
  queued_units=47  event_queue_depth=0
  shutting_down={'gw1': False, 'gw2': False, 'gw3': False, 'gw5': False}
  => controller loop is IDLE (no event pending: the wakeup was lost)
```

**Forty-seven work units queued. Four workers alive, none shutting down, each blocked in
`TestQueue.get()`. The controller's own event queue empty.** That is the whole defect in one
line, and it decides the shape of the correction:

* `event_queue_depth=0` confirms the loop is **idle**, not blocked. A blocked-send correction
  would have been the wrong one.
* `shutting_down` all `False` **disproves the stranding hypothesis** (section 3.2's fifth
  candidate, added while this run was in flight): the nodes had not been told to shut down, so
  work was not stranded by `_reschedule`'s early return. It is a plain lost wakeup on live
  nodes.

Because the nodes are live and able, re-asking the scheduler is a **correction**, not a
workaround: the assignment is legitimate and was simply never requested. Had the flags read
`True`, re-driving would have been papering over stranded work, and a different repair would
have been required.

### 2.4 The same run confirms the SQLite causality, on Windows

Six workers were created for a five-worker run. Two crashed:

| Worker | Last test before it stopped existing | SQLite connections that test opens |
|---|---|---|
| `gw0` | `test_a_heavy_system_generated_burst_leaves_every_genuine_row_untouched` | **1036** |
| `gw4` | `test_queued_outcome_is_independent_of_inbox_size` | **1062** |

Both recorded `session_finish=NO, process_exit=NO` — the signature of `os._exit`, which is what
pytest-timeout's thread method does at 120 s. **The two highest-connection tests in the whole
suite are exactly the two that killed workers**, and nothing else did. That answers the brief's
SQLite question from measurement rather than argument: per-operation connection churn is
causal to worker loss on Windows (section 6).

### 2.5 A third defect in the instrument, and the one that mattered most

The 600-second abort **never fired** in this run; the job was cancelled at its cap at 12:14:11
with the diagnosis half-written. The controller logged stalls at 184.9 s and 360.0 s and then
reset.

The cause is in this package's own code. The watchdog counted *any* event as progress, and the
replacement worker `gw5` was still producing startup chatter and 36 reports of its own. A run
that had been deadlocked for half an hour therefore looked alive. **Progress has to mean a test
finished**, not that a message arrived; worker lifecycle events are now recorded without
resetting the bound (clause W14, pinned by
`test_only_a_finished_test_counts_as_progress`).

This is the third defect the instrument revealed in itself, and the most serious: the other two
cost a run each, this one would have let the contract's own ending fail silently on exactly the
case it exists for.

## 2.6 The correction

`scripts/ci/xdist_contract.py`'s `SchedulerRedrive` (clause W13). A controller-side thread
watches for *test completions*. When none has arrived for `BARTHO_XDIST_REDRIVE_IDLE_S`
(default 60 s) **and** the scheduler still holds queued work **and** at least one node is alive
and not shutting down **and** the controller's own event queue is empty, it posts one event
onto `DSession`'s queue. `loop_once` dispatches it on the controller's main thread — the thread
pytest-xdist uses for every scheduling decision and every `channel.send` — and the handler calls
`_reschedule` on each eligible node.

Why this is a correction and not a workaround, stated plainly because the brief forbids the
latter:

* **Not blind.** The trigger is a conjunction of measured facts about the scheduler's own
  state, every one of which was observed in run 35090997379.
* **Not a retry.** No test is re-run, no job is re-run. The action is the single `_reschedule`
  call the controller failed to make.
* **Not timing-dependent.** The deadlock is permanent, so the 60-second threshold is a
  detection bound, not a race; a longer bound would only waste time. A node that is genuinely
  busy has `_reschedule` return on its own pending count, so a re-drive that was not needed is
  a no-op — which is what the end-to-end test exercises.
* **Never silent.** Every re-drive is counted, printed to stderr as it happens, and reported in
  the terminal summary as a defect the run completed *over*. A re-driven run does not read as
  green.

**What it does not do.** It does not fix pytest-xdist. The controller still stops asking its
scheduler for work; this contract notices and asks. That is a symptom-level correction by
necessity — the defect is in a dependency — and it stays on the unresolved list (section 7).

## 3. Worker loss: the cost model

`tests/test_scheduler_queue_containment.py::TestContainmentNeverDestroysAnObligation::
test_a_heavy_system_generated_burst_leaves_every_genuine_row_untouched` is the test that has
killed a worker in every verbose run. Measured on Linux (4 cores, idle, this branch):

| | |
|---|---|
| Wall time | **1.85 s** |
| SQLite connections opened by the test | **1036** (measured by the trace's own counter) |
| Time in `sqlite3.connect` for all of them | 0.1 s |
| Same test on the Windows runner under four workers | **> 120 s** |

Each `wal_db()` open is a `sqlite3.connect`, a `PRAGMA journal_mode = WAL`, three further
pragmas and a close; one per `insert_nudge_contained` call, and the test makes 1000 of them.
Past 120 s, pytest-timeout's `thread` method — the only method Windows has — calls
`os._exit(1)`, and the worker stops existing with no report and no traceback.

For scale, the whole Linux default suite opens **48,481** connections across four workers
(9,342 / 15,267 / 11,856 / 12,016) for **4.7 s** of connect time in total. The same count on
Windows is what the instrumented run measures, and what decides whether this is causal or
merely correlated. *(Pending.)*

### 3.1 The stalled tail is not this, and the measurement settles it

The two tests that have held the tail across six runs were measured under the trace on Linux,
run together, one worker, nothing else competing:

| Test | Call phase | SQLite connections |
|---|---|---|
| `test_lexical_beats_vector_on_exact_rare_tokens` | 2.3 s | 589 |
| **`test_worked_example_round_trips_end_to_end`** | **0.1 s** | **14** |

`test_worked_example_round_trips_end_to_end` is the test that held the baseline
(§1) open for **22 minutes 26 seconds**. It costs a tenth of a second and opens fourteen
connections. There is no per-connection price on any operating system at which fourteen
connections cost twenty-two minutes, and the two tests that have stalled differ from each
other by a factor of forty in both time and connection count — they have nothing in common
except being last.

**The stalled tail is therefore not the connection-churn finding**, and is not folded into it.
Whatever the Windows connection cost turns out to be, it does not explain the tail. Taken with
the 22-millisecond gap in §1.1, the evidence points at the harness rather than at either test;
§2 is what turns that from a strong inference into a measurement, and until it does this record
does not call it settled.

### 3.2 Candidates ruled out before the trace, and why

Recorded so the next person does not spend the time again.

* **A child process inheriting the worker's execnet pipe.** On Windows a subprocess started
  with `stdout=None` inherits the parent's standard output, and for an xdist worker that would
  be the channel to the controller — a child writing to it would corrupt the protocol stream,
  which would look exactly like a channel that goes silent and unwinds on kill. Several tests
  in the default suite do start subprocesses. **Ruled out:** `execnet.gateway_base.init_popen_io`
  duplicates fd 1 to a private handle at worker start and points fd 1 at `NUL`, so a child
  inherits the null device, not the channel. The guard is upstream and predates this.
* **A worker poisoned by leaked background threads.** A test that leaks a daemon, a portal or a
  connection pool would make a worker's later tests progressively slower, and the stall is
  always on a worker's last test. **Ruled out on Linux by measurement:** live thread counts
  across the daemon-heavy files are flat (first 4, median 4, last 4 on one worker; 7/9/5 on the
  other), and the trace now records the count at every phase so the Windows run can say the same
  or contradict it.
* **The test being genuinely expensive.** §3.1: 0.1 s and 14 connections.
* **The per-test timeout being mis-configured.** `pytest-timeout` is declared in
  `requirements-dev.txt`, installed, and `timeout = 120` / `timeout_method = "thread"` are read
  from `pyproject.toml`; it fires correctly elsewhere in the same runs (it is what kills the
  worker in §3). It does not fire here, which is a fact about the stall, not about the setting.

## 4. Failed replacement: root cause, from the source

Established by reading pytest-xdist 3.8.0, and reproduced deterministically on Linux by
`tests/test_windows_execution_contract.py::test_stock_xdist_scheduler_crashes_on_a_still_collecting_replacement`.

`LoadScopeScheduling` (which `LoadFileScheduling` extends) keeps two per-node maps:

* `assigned_work`, populated by `add_node()` — called from `DSession.worker_workerready`, the
  moment a worker reports ready;
* `registered_collections`, populated by `add_node_collection()` — called from
  `DSession.worker_collectionfinish`, when that worker has finished collecting.

Between those two events a node is in the first map and not the second. `remove_node()`, called
when *any* worker dies, ends with:

```python
for node in self.assigned_work:
    self._reschedule(node)
```

`_reschedule()` on a node with no pending work calls `_assign_work_unit()`, whose first
statement is `worker_collection = self.registered_collections[node]`. For a node still
collecting, that is a `KeyError`, raised inside the controller's event loop, reported as
`INTERNALERROR` and ending the session — every test not yet run is never run.

It needs two deaths close together: the first to create a replacement, the second to reschedule
while that replacement is still collecting. That is why it took a heavy-burst run to surface,
and it is exactly what Merge Candidate **35037740445 attempt 2** showed: gw1 died at 03:06:37,
gw0/gw2/gw3 within 160 ms of each other at 03:07:36, then
`INTERNALERROR> KeyError: <WorkerController gw5>` in `xdist/scheduler/loadscope.py`, ending the
session at 61 % (`5 failed, 3132 passed, 6 skipped in 882.82s`).

`DSession.worker_errordown` already guards the `KeyError` from `remove_node`'s own
`assigned_work.pop(node)`; the one raised from `_assign_work_unit` is not on that path and is
not guarded.

**Correction.** `scripts/ci/xdist_contract.py` supplies a scheduler through the public
`pytest_xdist_make_scheduler` hook whose `_reschedule` returns early for a node not yet in
`registered_collections`. Such a node can be given no work in any case, and is picked up by
`add_node_collection` → `schedule()`, the ordinary path a healthy replacement already takes.
The upstream behaviour is pinned as a defect, so an upstream fix retires the override rather
than hiding it.

## 5. Silent work loss: root cause, from the record

`remove_node()` does re-queue the dead worker's unfinished work unit
(`self.workqueue.update(workload)`), but nothing verifies it is ever taken off again. Merge
Candidate **34866265459** ended `1 failed, 4983 passed, 79 skipped` — with **17 of its 5080
selected tests never run** (the remainder of the crashed worker's file, and one more) and
nothing in the summary saying so. A reader of that line would call the run all but green.

**Correction.** `WorkAccounting` reconciles the collection the workers agreed on against every
nodeid that produced a terminal report, names the difference and fails the session. An
intentional stop (`-x`, `--maxfail`, an internal error, an interrupt) is exempt, so it never
buries the real reason a run stopped.

**Verification that it does not cry wolf:** the full Linux default suite, `-n auto --dist
loadfile`, 4 workers, 15,387 phase reports — no unreported test, and no `TESTS LOST` banner.

## 5.1 Verification on Linux

The full default suite, `-n auto --dist loadfile`, four workers, with the correction active:

| | |
|---|---|
| Result | 2 failed — both `tests/smoke/test_packaging_contract.py::test_declared_console_script_runs_help`, `FileNotFoundError: 'bartholomew'`, a local virtualenv whose console scripts are not on `PATH`. Not a code failure and not reproducible in CI, where the same tests pass. |
| Scheduler re-drives | **0** |
| Tests never reported | **0** — the work accounting raised nothing |

Zero re-drives across roughly 5,100 tests is the non-regression signal that matters for W13:
the trigger is conservative enough never to fire on a healthy run. The deadlock has never been
observed on Linux, which finishes the same suite in about nine minutes, so a re-drive here would
have meant the condition was too loose.

The acceptance suite, `tests/test_windows_execution_contract.py`, is **20 passed**.

## 6. Unresolved, with severity

* **The upstream pytest-xdist defect itself — Medium, open.** The controller still stops asking
  its scheduler for queued work; clause W13 detects that and asks. The proper repair belongs
  upstream (a schedule re-examination on `loop_once`'s existing two-second wakeup would do it),
  and `test_the_deadlock_is_real_and_nothing_in_xdist_breaks_it` fails the day pytest-xdist
  grows one, which is how this package finds out. Until then every Windows run that needed a
  re-drive says so in its summary.
* **Per-operation SQLite connection churn — High, open, now proven causal.** Section 2.4: the
  two highest-connection tests are the two that killed workers. W13 does not address it; a
  worker still dies, its work is still re-queued, and the run still pays for it. The correction
  belongs inside `db_ctx`'s boundary and has a hard constraint the brief's "correct it
  comprehensively" has to respect: `tests/test_vector_store_handle_lifetime.py` and
  `tests/test_sqlite_wal_cleanup.py` deliberately assert that every call releases its handles
  before returning, because on Windows a file that is still open cannot be deleted. Any
  connection reuse must therefore be explicitly scoped and closed, not a silent pool. That is a
  storage-design decision with its own blast radius, it is not needed to make the Merge
  Candidate complete, and this package does not take it — see the decision note in section 8.
* **The orphan processes — Low, open, untouched.** Every Windows job ends with
  `Terminate orphan process: msedge / notepad / msedge`. They come from the governed-actuation
  step, not the default suite, and no evidence connects them to any failure here. Recorded, as
  the brief directs, and left alone.
* **Repeated completion on one head — not yet evidenced.** Success gate 2 needs the Windows
  Merge Candidate to complete repeatedly on the final functional head. At the time of writing
  the correction has been verified on Linux and is queued for Windows; no green Windows run
  exists yet, and nothing here should be read as claiming one.

## 8. Decision note: why this package stops at the harness

The brief's Decision Boundary asks for a stop-and-report if completion requires an
architectural change outside the worker/runtime/storage boundaries implicated here. It does
not, and this package did not expand.

The stalled tail — the blocker that has cancelled roughly half of all Windows Merge Candidate
runs since August — is entirely inside the test-execution harness, and is corrected there
(W13). The connection churn is inside the storage boundary and is causal to a *different*
symptom (worker loss), which the contract already survives without losing work (W4, W6). Making
the Merge Candidate complete does not require touching it, so taking a storage-lifetime
decision here would have been the expansion the brief forbids, not the completion it asks for.
It is recorded in section 6 with its constraint and left to its own package.

## 9. Runs

| Run | Head | Tier | Result |
|---|---|---|---|
| 35081868613 | `d3c9992` | Merge Candidate (push, `main`) | **baseline**: Windows cancelled at the cap, 22 min 26 s stall, no junit; other six green |
| 35084443075 | `916ac59` | Merge Candidate (dispatch) | superseded — cancelled by a second dispatch on the same ref (`cancel-in-progress` on `github.ref`); no evidence taken from it |
| 35085435731 | `1ee0131` | Merge Candidate (dispatch) | first instrumented run: controller **ended the session itself at 600 s**, 14 min inside the cap; four workers idle in `after-teardown`; summary step then crashed on its own mixed-type field (section 2.1) |
| 35088270721 | `ed03789` | Merge Candidate (dispatch) | **root cause**: simultaneous stacks — controller idle in `queue.get` inside `loop_once`, every worker blocked in `TestQueue.get` (section 2.2) |
| 35090997379 | `439345c` | Merge Candidate (dispatch) | **confirming measurement**: `queued_units=47`, `event_queue_depth=0`, `shutting_down` all `False` (section 2.3); two workers killed on the two highest-connection tests (section 2.4); abort failed to fire because lifecycle chatter reset it (section 2.5). Cancelled at the cap |

Section 6's last bullet stands: no Windows run has yet completed with the correction in place.
