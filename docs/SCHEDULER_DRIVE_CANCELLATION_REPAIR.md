# Scheduler Drive Cancellation Repair

**Status:** implemented; tested locally and in PR #126's CI; **not merged**. It waits for review and
Taylor's User Approval Gate.
**Baseline:** `origin/main` at `7b21a009e53ccde0f2dd13003528b0c3b2431be0` (the merge of PR #123).
**Branch:** `claude/scheduler-drive-cancellation-repair`, PR #126. It is a separate package: it is
not part of PR #124 or PR #125, and it changes neither of them.
**Scope:** cancellation propagation through the scheduler's drive seam,
`runtime_contract.run_drive_through_runtime_contract()`. It does not rewrite the scheduler or change
its cadence, pacing, tick, nudge or reflection semantics on the paths that are not cancelled. It adds
no retries.

> **This document is the package's durable evidence.** `RISKS.md` remains canonical for the risk's
> substance; Airtable owns live status.

---

## 1. What was observed

PR #125's acceptance run 2 (Merge Candidate 36566521591, head `2906a81`, Windows job
109399685268, 2026-09-29) failed:

- **The worker died.** Worker `gw3` was killed at the 120 s per-test timeout while running
  `tests/test_scheduler_persistence_concurrency.py::test_startup_drives_are_paced_not_burst`. It
  reported "node down: Not properly terminated" at 120.02 s.
- **Where the thread dump put it.** The pre-kill dump taken at 110 s (PR #124's W15 evidence
  clause, `scripts/ci/execution_trace.py` on that branch) showed:
  - the main thread idle in the test's own asyncio event loop (`windows_events._poll` under
    `run_until_complete`);
  - two idle `concurrent.futures` worker threads. The dump carries no thread names, so they are
    consistent with the daemon's two single-worker executors but not identified as them;
  - no thread inside SQLite.
- **Ten W13 re-drives followed.** They were a consequence of the loss, not a second defect.
  pytest-xdist 3.8.0's `LoadScopeScheduling.remove_node` re-queues every scope the dead worker ever
  held, finished ones included. Idle workers were then handed empty batches and stalled.
- **Nothing else failed.** The six other jobs passed, and the crashed test passed when it was
  re-queued on `gw1`.

The failing path — `runtime_contract.py`, `scheduler/`, `daemon.py` and the test — is
byte-identical on `main` `7b21a00`, PR #124's `1adf251` and PR #125's `2906a81`. The defect
predates both PRs.

## 2. Classification

| | |
|---|---|
| **Observed symptom** | A test that cancels `run_scheduler()` once and then waits for it without a bound never returns, and pytest-timeout kills the xdist worker. |
| **Immediate cause** | The one `task.cancel()` was lost. `run_scheduler()` kept looping because its only exit is `except asyncio.CancelledError: … break` (`scheduler/loop.py:380-382`), and that exit was never reached. On Windows this is inferred by elimination (§3); locally it is reproduced. |
| **Systemic cause** | The drive seam awaited `asyncio.wait_for(drive_fn(ctx), timeout=timeout)` (`runtime_contract.py:2218` at `7b21a00`). On CPython 3.10 and 3.11, `wait_for` turns a cancellation into a normal return when the inner future finished in the same loop iteration: `except exceptions.CancelledError: if fut.done(): return fut.result()` (3.10 `tasks.py:430-435`, 3.11 `tasks.py:474-479`). The seam sits on the scheduler task's stack, so a cancel landing as a drive completes was converted into that drive's result. |
| **Violated invariant** | *One cancellation of the task running a drive through the seam propagates out of the seam whatever instant it lands at, and the drive's task has finished before the seam returns or raises.* So one `cancel()` stops `run_scheduler()`, and no drive coroutine outlives it. Blocking work a drive already handed to an executor thread can still be finishing; `stop()` accounts for that through the executors' bounded `close()` drain. |
| **Shared boundary** | Every caller that stops the scheduler by cancelling it: `KernelDaemon.stop()` (`daemon.py:914-925`), the three tests that cancel the real `run_scheduler()`, and `asyncio.run` teardown. PR #124's and PR #125's Windows gates run the same code, so both are exposed. |

The previous records understated its latency and liveness cost (§8). `RISKS.md` called it a
Python 3.11 swallow "that delays shutdown". `docs/WINDOWS_WAL_WRITER_LOCK_REPAIR.md` §8 item 3 put
the cost at "up to the waiter's timeout of extra shutdown latency (5 s in `KernelDaemon.stop()`),
never a lost write". The "never a lost write" part still holds (§4). The rest understated it. It:

- affects 3.10 as well;
- loses the cancellation outright;
- makes any waiter without a bound hang;
- costs `stop()` its full 5 s bound, silently, because `wait_for` then returns `None` instead of
  raising `TimeoutError`, so B5's `producer_tasks_terminal` stays `True`;
- lets a further due drive run after `skill_registry.shutdown()` (§4).

## 3. Mechanism and confidence

**Verified (source and experiment).**
- **Where the swallow lives.** The stdlib branch is quoted above. From 3.12, `wait_for` is
  `async with timeouts.timeout(timeout): return await fut`, which runs the drive inside the caller's
  task, so this race does not exist there.
- **It is the only swallow on the path.** Every other await on the scheduler task's path passes a
  cancellation on: the executor gate plus `wrap_future`, `unit_of_work`'s shielded close, and
  `record_action_reflection`, which catches `Exception` only. So the seam was the only place a
  cancel could be swallowed.
- **Reproduced locally on Linux** against the real code. Each row names its Python version:

  | Condition | Lost cancels |
  |---|---|
  | Forced alignment, cancel requested 0–2 `call_soon` hops before the drive returns | every trial, on 3.10 and 3.11 |
  | The run-2 test itself, its `sleep(4.0)` replaced so the cancel lands as a drive completes, at `1adf251` | 5 of 5, on 3.11 |
  | Natural race, the real run-2 test at `2906a81`, loaded runner | 9 of 1000, on 3.11: 1/300 plain, 4/400 with an observe-only detector, 4/300 with Windows-clock emulation |
  | Natural race, real `run_scheduler`, pacing off | 11 of 300, on 3.11 |
  | Random single cancel in a model loop over the real seam | 317 of 600 on 3.10; 316 of 600 on 3.11 |
  | Same model loop on 3.12 | 0 of 600 |

- **The hangs look like the Windows dump.** Every local hang has its stack shape: the main thread
  idle in the selector under `run_until_complete`, both executor workers idle, and no SQLite frame.
- **Swapping only the timeout wrapper removes it.** A non-swallowing wrapper removes every hang.

**Inferred.** That this is what killed `gw3` on Windows rests on elimination:

- The test's only unbounded await is `await task`.
- `scheduler_store.close()` is bounded at 5 s.
- `asyncio.sleep(4.0)` is a timer.
- Two idle executor workers still alive fit `close()` not having run. The dump has no thread
  names, so this is consistent with the daemon's executors, not proof.

Direct confirmation would come from the run's `junit-windows-full` artifact (ID 11033121800;
exec-trace thread names and `phase_sqlite_connections`). The sandbox could not download it; it
expires about 2026-10-29. Confidence: high in the mechanism, medium-high that `gw3` hit it.

**Why it showed on Windows.** Not established. On unloaded Linux the 4.0 s cancel usually lands in
the loop's idle `sleep(5)`. Slower per-iteration SQLite work, and the Windows clock's roughly
15.6 ms resolution, plausibly move it into a drive's completion window. A partial emulation of the
Windows clock (`loop.time()` quantised to 15.625 ms) did not measurably change the local rate
(4/300 against 4/400 without it), so the Windows trigger remains unproven.

## 4. Affected conditions

- **Python:** CPython 3.10 and 3.11. That covers every PR, Integration and Merge Candidate job:
  Ubuntu 3.10/3.11 and Windows 3.11.9. Nightly also runs 3.12 on Ubuntu and Windows, and the live
  Windows test machine runs 3.12.10 (`docs/G_WINDOWS_COMPANION_COMPLETION.md` §9). 3.12 and 3.13 do
  not have this race, but semantic changes 4 and 5 (§5) do apply to them.
- **Needs:** a cancellation of the scheduler task landing as a drive completes (0–2 loop iterations).
- **Production effect:**
  - `KernelDaemon.stop()` takes about 5.1 s instead of about 0.1 s. This was measured with a drive
    aligned to `stop()`'s cancel.
  - A further due drive runs after the skills are unloaded. With two due drives, the second ran
    after `skill_registry.shutdown()` in 4 of 4 runs on 3.10 and 3.11 (adversarial review), and
    `test_daemon_stop_…` now fails on exactly that against the old seam. With one drive the loop
    went to its idle `sleep(5)` instead, so the earlier one-drive measurement could not show it.
  - The loss is invisible to B5's clean-shutdown accounting.
- **Harm:** no data corruption: a drive's own committed writes are kept. A lost cancel does more
  than prolong the loop, though. A drive that runs after the skills are unloaded reaches an unloaded
  skill, so a due reminder would be recorded as `DELIVERY_FAILED` ("Skill not loaded";
  `skill_registry.py` `execute_action`, `drives.py`'s delivery classification). That is read from
  the code, not observed.

## 5. The repair

`bartholomew/kernel/runtime_contract.py` is the only production file changed.

The Execution stage's `asyncio.wait_for(drive_fn(ctx), timeout=timeout)` becomes
`_await_drive(drive_fn(ctx), timeout, task_id)`, with three private helpers beside it:

- **`_await_drive`** runs the drive as its own task, named `scheduler-drive:<task_id>`, and waits
  on it with `asyncio.wait()`. `asyncio.wait`'s internal `_wait` is `try: await waiter / finally:`
  with no except clause on 3.10–3.13, so it can never turn a cancel into a return. On a
  cancellation the helper cancels the drive, waits for it to finish, and **re-raises the caller's
  CancelledError, even if the drive had already completed.** Everything else matches `wait_for`:
  - a result, the drive's own exception or its own CancelledError is returned or raised as-is;
  - a timeout cancels the drive, waits for it, and raises `asyncio.TimeoutError` — unless the drive
    completed or raised while being cancelled, in which case that outcome is returned or raised
    as-is;
  - `timeout <= 0` never starts the drive.
- **`_cancel_and_settle`** waits for the cancelled drive **without a bound**, so the scheduler task
  never ends while its drive is still running. A further cancellation during that wait is passed on
  to the drive, never swallowed, and re-raised once the drive has finished. Every
  `_DRIVE_SETTLE_WARN_S` (10 s) it logs an ERROR giving the time since the cancel, if the drive is
  still running. That timer is observability only: it never abandons the drive and never cancels it
  again.
- **`_note_superseded_drive`** logs a drive outcome that cancellation superseded: INFO for a
  discarded result, ERROR with `exc_info` for an exception. It also retrieves the exception, so
  asyncio never reports it as unretrieved.

This is the task-per-action shape `SkillRegistry.execute_action()` already uses for the same
reason (DECISIONS.md, `skill_registry.py`). It is one code path on every version, with no version
branch.

**Why this fixes the invariant rather than masking the test.**
- The cancellation now propagates at the boundary that lost it, for every caller: `stop()`, tests,
  and `asyncio.run` teardown.
- Nothing retries, and no caller cancels twice.
- The test edits (§6) only make a lost cancel *fail loudly*. They would fail against the old seam
  instead of hanging, and they cannot hide a loss the way `wait_for(task, n)` does.

**Rejected alternatives.** Each was tried against the same harness during design. The experiment
notes were working files; the reasons below are the durable record.

| Alternative | Why rejected |
|---|---|
| Test-only bound | Hides the production defect. |
| Caller retry | Retry-hiding, and the loss stays silent. |
| `cancelling()` post-check | 3.10 has no `cancelling()`, and on 3.12+ it double-delivers the cancel. |
| `asyncio.timeout()` with a 3.10 fallback | Two code paths, a 3.12 loss when a drive suppresses the cancel, and `timeout(0)` starts the body. |
| Version switch | Two code paths, one of them (3.12+) run only by Nightly and by no PR or Merge Candidate gate. |
| `shield` variants | Still swallow the cancel. |
| A guard in `loop.py` | Changes scheduler semantics and breaks the soak test's fake asyncio. |
| A bounded settle that abandons the drive | The scheduler could end with its drive coroutine still running: untracked work that could issue new writes after `stop()` reported clean. Thread work a drive already submitted is different: the executors' bounded `close()` drain accounts for it. |
| Extracting a shared helper from `skill_registry` | Unrelated scope. |

**Semantic changes, disclosed.**
1. **Cancellation wins at the race instant (3.10/3.11).** When the cancel and a drive's completion
   land within 0–2 loop iterations, the CancelledError now propagates. That drive's result is
   discarded, with an INFO log. Its `completed` reflection, heartbeat, nudge, tick row and
   `next_run` update are skipped. The drive runs again at the next start, because
   `task_id:scheduled_ts` has no tick. Writes the drive itself committed are kept. This is what
   3.12+ already does.
2. **A drive that raised at that instant** is logged at ERROR as superseded. No `error` reflection
   is recorded, and the scheduler stops. Before, it counted as a crash and the loop carried on.
3. **A further cancel during a drive's cleanup, or during the timeout settle,** is passed on to the
   drive. The seam raises only after the drive has finished. Before, 3.10/3.11 raised at once and
   left the drive running. A drive that ignored cancellation forever would therefore now hold up
   `stop()`, just as 3.12+ running it inline would. No current drive does; skill actions are bounded
   by `skill_registry._settle`; and the settle logs every 10 s.
4. **On 3.12+ the drive now runs as its own task**, as it already did on 3.10/3.11 (every merge
   gate). Before, 3.12's `wait_for` ran it inline; that is what Nightly's 3.12 jobs and the live
   Windows test machine (3.12.10) ran. A `ContextVar.set()` or `current_task()` inside a drive now
   sees the drive's own task. A check of the drive path (its ContextVars and `current_task()` uses)
   found nothing that relies on the scheduler's task.
5. **A drive that suppresses the scheduler's cancel and returns** no longer loses the cancel, on
   any version. 3.10/3.11's `wait_for` already raised in that case; stdlib 3.12's `wait_for` would
   return the drive's result.

## 6. Regression coverage

**New: `tests/test_drive_seam_cancellation.py`, 29 tests.** All are in the default tier except
one `slow` stress test. Every wait on a task is bounded with `asyncio.wait(..., timeout)` plus an
assertion, so a regression fails the test instead of killing a worker. Every ordering between the
test, the seam and the drive is forced by events (a drive's cleanup lasts until the test releases
it), never by a wall-clock window. The only time bounds left are hang detectors (§10), so a
stalled runner can fail a test only by outlasting one of them.

| Test | What it proves |
|---|---|
| `test_control_stdlib_wait_for_loses_the_cancel_only_before_3_12[0,1,2]` | **Causal control.** The harness reaches the stdlib swallow on 3.10/3.11, and 3.12+ has none, so the tests below cannot pass without exercising the race. |
| `test_the_seam_helper_never_loses_the_cancel[0,1,2]` | The same harness never loses the cancel with the repaired helper. |
| `test_a_cancel_landing_as_the_drive_completes_propagates[0,1,2]` | Completion and forbidden states. The seam raises CancelledError; no `('nudge', 1)`, no `completed` reflection, the drive is done, the drive task is named, and an INFO log is written. |
| `test_a_drive_that_raises_as_the_cancel_lands_is_logged_not_leaked` | The cancel propagates, there is no `error` reflection, and one ERROR with `exc_info` is logged. Then every reference to the drive task is dropped, its collection is asserted with a weakref, and no "exception was never retrieved" is reported. |
| `test_a_cancel_while_the_drive_runs_cancels_it_before_propagating` | The drive has seen the cancel at the instant the seam raises (a snapshot taken inside the seam's own task), and nothing is recorded. |
| `test_a_second_cancel_during_cleanup_never_strands_the_drive` | The further cancel reaches the drive, the seam is still waiting while the drive cleans up, and it ends only after the cleanup. **Before, the drive was left running.** |
| `test_a_cancel_during_the_timeout_settle_waits_for_the_drive` | Same, during the timeout settle. |
| `test_a_drive_that_suppresses_the_cancel_still_does_not_lose_it` | The cancel is never lost, even when the drive suppresses it. |
| `test_a_drive_that_is_slow_to_settle_is_reported_and_still_awaited` | Two settle warnings are logged, each giving the time since the cancel (increasing), while the seam still waits; the drive is never abandoned and never re-cancelled by the timer. |
| `test_a_drive_that_ends_cancelled_on_its_own_still_propagates` | The existing stop-on-self-cancel behaviour is kept. |
| `test_a_successful_drive_is_unchanged` / `test_a_crashing_drive_is_unchanged` | `('nudge', 1)` with `completed`; `(None, 0)` with `error` and the crash log. |
| `test_a_timeout_still_fails_the_drive_and_cancels_it` | `(None, 0)`, a `timeout` reflection, the warning, and the drive cancelled and done. Nothing tested this branch before. |
| `test_a_drive_that_completes_while_timing_out_keeps_its_result` / `…raises_while_timing_out_is_an_error` | `wait_for` parity on the timeout path. |
| `test_a_non_positive_timeout_never_starts_the_drive[0,-1]` | `wait_for` parity. |
| `test_stress_a_random_single_cancel_is_never_lost_and_strands_nothing` | 500 trials, a fixed seed, and one cancel at a random hop: no loss, and no drive still running at the instant the loop stops. |
| `test_stress_control_the_same_harness_loses_cancels_to_stdlib_before_3_12` | The stress harness is sensitive: stdlib loses cancels there on 3.10/3.11 and none on 3.12+. |
| `test_one_cancel_landing_as_a_drive_completes_stops_the_real_scheduler[0,1]` | **The run-2 hang, deterministically, with the real `run_scheduler`.** It stops within the bound and *returns*, keeping the `loop.py` `break` contract that `_on_scheduler_task_done` relies on. The aligned drive ran once, no other drive ran, no ticks were written, and the store drained. |
| `test_stress_one_cancel_at_a_random_instant_stops_the_real_scheduler` (`slow`) | 120 trials with the real always-on drives and one cancel in the first 150 ms. |
| `test_daemon_stop_is_not_slowed_by_a_drive_completing_as_it_cancels` | **`KernelDaemon.stop()`, the production caller**, with two due drives. The second never runs and no tick is written. The test pins the scheduler's drive timeout to 60 s for itself, so a slow `stop()` stage cannot time out the drive it holds (§10). Before, the second drive ran after the skills were unloaded and `stop()` took 5.1 s. Its "did not terminate" check is a guard only: it could not see the old loss, because `wait_for` then returned `None`. |

**Hardened (defence in depth only): three teardowns that cancel the real scheduler.**
- They are `test_fresh_database_scheduler_startup_does_not_hang` and
  `test_startup_drives_are_paced_not_burst` (in `tests/test_scheduler_persistence_concurrency.py`),
  and the `scheduler` fixture in `tests/test_event_backbone_drive.py`.
- Each now cancels once, waits with a bound using `asyncio.wait`, and asserts the scheduler stopped.
- Before, a lost cancel either hung (`await task`) or passed silently late, because `wait_for(task, n)`
  cancels again on its timeout.
- If the cancel is lost they still cancel again, so nothing is left running.
- They change no assertion about what the tests test.

**Pre-fix control:** the new file run against the unmodified seam of `7b21a00`, on CPython 3.11.15
and 3.10.20. Both give **16 failed and 13 passed**, in three groups:
- **Causal, 10.** The cancel is lost or the drive is stranded:
  - "cancel swallowed; seam returned ('nudge', 1)" at hops 0, 1 and 2;
  - the raise-at-the-instant case: the seam returns instead of raising;
  - both further-cancel tests: "the second cancel reaching the drive never happened", because the
    old seam raised at once and left the drive running;
  - "a single cancel did not stop run_scheduler" at hops 0 and 1;
  - real-scheduler stress: "single cancels lost in trials [50, 71, 114]" on 3.11 and "[119]" on
    3.10;
  - `KernelDaemon.stop()`: "a drive ran after stop() had cancelled the scheduler".
- **Missing symbol, 5.** The three helper tests, the model-loop stress test and the slow-settle test
  use `_await_drive` or `_DRIVE_SETTLE_WARN_S`, which do not exist before the repair.
- **Log line only, 1.** The suppress-the-cancel test fails only on the new INFO line: 3.10/3.11's
  `wait_for` already propagated the cancel in that case (semantic change 5).
- **Passed, 13:** the causal controls, the stress control, the drive that cancels itself, the
  cancel-while-running case, and every "unchanged" test (success, crash, timeout parity,
  non-positive timeout).

**With the repair:** 29 passed on each of CPython 3.10.20, 3.11.15, 3.12.3 and 3.13.12.

**Robustness to a slow runner.** A probe injected loop-thread stalls of 0.3–0.6 s at several
instants in each test, plus a Parking Brake read slowed by 0.12–0.25 s. The event-gated tests
passed under every pattern, on 3.10 and 3.11. The same probe failed the first, wall-clock version
of the timeout-settle test in 2 of 3 patterns.

**Mutation (CPython 3.11): 13 of 13 mutants caught.** Each mutant edits the repaired helper in
place; the new file then fails.

| Mutant | Caught by |
|---|---|
| M1: the call site reverted to `asyncio.wait_for` | the landing-as-completed tests |
| M2: the helper swallows like 3.11 | the model-loop test (`'ran-away'`) |
| M3: the settle raises at once on a further cancel | the second-cancel test: the further cancel never reaches the drive |
| M4: the settle abandons the drive at the warning timer | the slow-settle test: no settle warning |
| M5: the warning timer re-cancels the drive | the slow-settle test: the drive records a further cancel |
| M6: the timeout path does not wait for the drive | the timeout-settle test: the caller's cancel never reaches the drive |
| M7: a non-positive timeout starts the drive | the non-positive-timeout test |
| M8: the superseded exception is neither retrieved nor logged | the raise-at-the-instant test: no ERROR record |
| M9: the drive task is not named | the landing-as-completed tests |
| M10: a timed-out drive becomes a success | the timeout test: `(None, 1)` |
| M11: the caller-cancel branch cancels the drive but does not wait for it | the cancel-while-running test: the drive had not seen the cancel when the seam raised |
| M12: the superseded exception is read without marking it retrieved | the raise-at-the-instant test: "exception was never retrieved" after collection |
| M13: the settle warning reports the interval, not the time since the cancel | the slow-settle test: `[0.05, 0.05]` |

M11–M13 and the event-gated forms of the M3–M6 tests come from the package's adversarial review:
it showed that the first versions of those tests either depended on wall-clock windows or could not
fail.

## 7. Same-pattern sites

**Included.** Only the seam, the one place a cancellation was swallowed on a task that is stopped by
cancelling it.

**Recorded as follow-up evidence, not changed here:**
1. **`daemon.py:925`, `await asyncio.wait_for(task, timeout=5.0)` in `stop()`.**
   - It is not a hard bound: on timeout it cancels again and then waits without limit.
   - After a lost cancel it returned `None`, not `TimeoutError`, so the "did not terminate" report
     and B5's `producer_tasks_terminal=False` never fired.
   - With the seam repaired, only a drive that ignores cancellation could still reach it.
   - A hard bound (`asyncio.wait` plus reporting a non-terminal task) touches B5 accounting. It
     belongs in its own package.
2. **`daemon.py:902` vs `:914`.** `stop()` unloads the skills before it cancels the scheduler, so
   one in-flight drive can still deliver through an unloaded skill. This predates the defect; the
   swallow only lengthened the window.
3. **Other stdlib `wait_for` sites.**
   - `multimodal/device_consent.py:283`: a one-shot, bounded request, harmless.
   - `kernel/blocking_executor.py:152`: `close()`, one-shot and bounded, harmless.
   - `bartholomew_api_bridge_v0_1/services/api/app.py:972` and `:1216`: bounded health probes,
     harmless.
   - None is on a task that is stopped by cancelling it.
4. **`bartholomew/kernel/runtime_contract.py.orig`.** A stale tracked copy that still contains the
   old `wait_for`. It cannot be imported. Removing it is a separate hygiene change; audits should
   target the `.py` file.
5. **`skill_registry.py`'s settle helpers.** A candidate for a future shared helper. Not
   consolidated here.
6. **No PR, Integration or Merge Candidate gate runs 3.12 or 3.13.** Nightly runs 3.12 (Ubuntu
   serial and Windows every-marker), so the new tests will run there; nothing in CI runs 3.13. The
   new tests assert the same behaviour on every version and were run locally on all four.

## 8. Records corrected by this package

- **`docs/WINDOWS_WAL_WRITER_LOCK_REPAIR.md` §8 item 3.** The cost assessment is corrected, and the
  entry points here.
- **`RISKS.md`.** The mention in the 2026-09-14 writer-lock repair note (PR #110, inside the
  2026-09-09 Windows Merge Candidate entry) is corrected in place, and a watchlist entry records this
  defect with its repair status.
- **`DECISIONS.md` (the SkillRegistry entry)** says "Python 3.11 cancellation swallow". It is left
  as written: decision records are not rewritten. This record supersedes it on the version range,
  3.10 as well.

## 9. Implications for PR #124 and PR #125 (Taylor's direction, 2026-09-30)

- **PR #125's acceptance sequence is invalidated and back at 0/3.** Runs 1 (36554163665) and 2
  (36566521591) on `2906a81` are diagnostic evidence only and never count toward the new sequence.
  The failure is not waived, reinterpreted or excluded from the criteria.
- **Order after this package.** Once this repair has passed its own review and CI and is merged
  through the normal approval gate, PR #125 is brought onto a base that contains it: retargeted to
  `main` after #124, by a merge commit, per §9 of `docs/SQLITE_WAL_HEADROOM_REPAIR.md` (on PR #125's
  branch). It is then requalified on its exact new head, and only then does the three-run sequence
  start, at 0/3.
- **PR #124's Windows gate runs the same seam.** Whether this repair lands before #124's next
  acceptance attempt, or #124 merges `main` after it, is Taylor's decision.

## 10. Remaining risks

- **The Windows attribution rests on elimination** (§3). The deterministic tests run in the Windows
  3.11 job, which is where they matter.
- **An unbounded settle can hold up `stop()`.** A future drive that ignores cancellation would do
  so. Mitigated by the ERROR log every 10 s, which gives the time since the cancel, and by the
  `daemon.py:925` follow-up; this is the same as 3.12+ today.
- **The forced-alignment tests rely on CPython's callback ordering.** They depend on `call_soon`
  being FIFO and on done-callbacks being scheduled when a task completes. Both were stable on
  3.10–3.13; the causal control would catch a change on 3.10/3.11.
- **Wall-clock dependence left in the tests.** Hang bounds only: the 5 s per-wait bounds
  (`BOUND_S`) and the daemon test's 15 s bound around `stop()`. A stall that outlasts one of them
  still fails a test, as a hang.
  - The daemon test no longer asserts `stop()` < 5 s (Codex review comment 4171700100,
    2026-10-03). That span covered every stage of `stop()`, before and after the cancel, so a slow
    snapshot or checkpoint could fail it while the seam worked.
  - The same test pins the scheduler's drive timeout (`DRIVE_TIMEOUT`, 5 s) to 60 s for itself.
    The drive it holds across `stop()`'s earlier stages could otherwise time out on a slow runner
    and change the ordering the test asserts. The value is finite and above the 15 s bound: with no
    timeout, the old seam's swallow branch is never reached and the test stops discriminating.
- **At-least-once re-run.** A drive superseded at shutdown runs again at the next start, and a
  time-dependent nudge can differ.
