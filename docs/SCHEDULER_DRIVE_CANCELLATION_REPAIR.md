# Scheduler Drive Cancellation Repair

**Status:** implemented and tested locally; **not merged**. It waits for review, CI, and Taylor's
User Approval Gate.
**Baseline:** `origin/main` at `7b21a009e53ccde0f2dd13003528b0c3b2431be0` (the merge of PR #123).
**Branch:** `claude/scheduler-drive-cancellation-repair`. It is a separate package: it is not part
of PR #124 or PR #125, and it changes neither of them.
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
- **Where the thread dump put it.** The W15 dump taken at 110 s showed:
  - the main thread idle in the test's own asyncio event loop (`windows_events._poll` under
    `run_until_complete`);
  - both of the daemon's single-worker executor threads idle;
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
| **Immediate cause** | The one `task.cancel()` was lost. `run_scheduler()` kept looping because its only exit is `except asyncio.CancelledError: … break` (`scheduler/loop.py:380-382`), and that exit was never reached. |
| **Systemic cause** | The drive seam awaited `asyncio.wait_for(drive_fn(ctx), timeout=timeout)` (`runtime_contract.py:2218` at `7b21a00`). On CPython 3.10 and 3.11, `wait_for` turns a cancellation into a normal return when the inner future finished in the same loop iteration: `except exceptions.CancelledError: if fut.done(): return fut.result()` (3.10 `tasks.py:430-435`, 3.11 `tasks.py:474-479`). The seam sits on the scheduler task's stack, so a cancel landing as a drive completes was converted into that drive's result. |
| **Violated invariant** | *One cancellation of the task running a drive through the seam propagates out of the seam whatever instant it lands at, and the drive has finished before the seam returns or raises.* So one `cancel()` stops `run_scheduler()`, and nothing it started keeps running. |
| **Shared boundary** | Every caller that stops the scheduler by cancelling it: `KernelDaemon.stop()` (`daemon.py:914-925`), the three tests that cancel the real `run_scheduler()`, and `asyncio.run` teardown. PR #124's and PR #125's Windows gates run the same code, so both are exposed. |

The previous record of this defect understated it (§8). It was recorded as a Python 3.11 swallow
that "delays shutdown … never a lost write". In fact it:

- affects 3.10 as well;
- loses the cancellation outright;
- makes any waiter without a bound hang;
- costs `stop()` its full 5 s bound, silently, because `wait_for` then returns `None` instead of
  raising `TimeoutError`, so B5's `producer_tasks_terminal` stays `True`;
- lets further drives run after `skill_registry.shutdown()`.

## 3. Mechanism and confidence

**Verified (source and experiment).**
- **Where the swallow lives.** The stdlib branch is quoted above. From 3.12, `wait_for` is
  `async with timeouts.timeout(timeout): return await fut`, which runs the drive inside the caller's
  task, so this race does not exist there.
- **It is the only swallow on the path.** Every other await on the scheduler task's path passes a
  cancellation on: the executor gate plus `wrap_future`, `unit_of_work`'s shielded close, and
  `record_action_reflection`, which catches `Exception` only. So the seam was the only place a
  cancel could be swallowed.
- **Reproduced locally on Linux, CPython 3.10 and 3.11,** against the real code:

  | Condition | Lost cancels |
  |---|---|
  | Forced alignment, cancel requested 0–2 `call_soon` hops before the drive returns | every trial, on 3.10 and 3.11 |
  | Same, at `1adf251` | 5 of 5 |
  | Natural race, real `run_scheduler`, loaded runner | 9 of 1000 |
  | Natural race, real `run_scheduler`, pacing off | 11 of 300 |
  | Random single cancel in a model loop over the real seam | 317 of 600 on 3.10; 316 of 600 on 3.11 |
  | Same model loop on 3.12 | 0 of 600 |

- **The hangs look like the Windows dump.** Every local hang has its stack shape: the main thread
  idle in the selector under `run_until_complete`, both executor workers idle, and no SQLite frame.
- **Swapping only the timeout wrapper removes it.** A non-swallowing wrapper removes every hang.

**Inferred.** That this is what killed `gw3` on Windows rests on elimination:

- The test's only unbounded await is `await task`.
- `scheduler_store.close()` is bounded at 5 s.
- `asyncio.sleep(4.0)` is a timer.
- An executor worker that is idle but still alive means `close()` had not run.

Direct confirmation would come from the run's `junit-windows-full` artifact (ID 11033121800;
exec-trace thread names and `phase_sqlite_connections`). The sandbox could not download it; it
expires about 2026-10-29. Confidence: high in the mechanism, medium-high that `gw3` hit it.

**Why it showed on Windows.** Not established. On unloaded Linux the 4.0 s cancel usually lands in
the loop's idle `sleep(5)`. Slower per-iteration SQLite work, and the Windows clock's roughly
15.6 ms resolution, plausibly move it into a drive's completion window. This is unmeasured.

## 4. Affected conditions

- **Python:** CPython 3.10 and 3.11. That covers every CI job: Ubuntu 3.10/3.11 and Windows 3.11.9.
  3.12 and 3.13 do not have this race.
- **Needs:** a cancellation of the scheduler task landing as a drive completes (0–2 loop iterations).
- **Production effect:**
  - `KernelDaemon.stop()` takes about 5.1 s instead of about 0.1 s. This was measured with a drive
    aligned to `stop()`'s cancel.
  - Drives keep running after the skills are unloaded.
  - The loss is invisible to B5's clean-shutdown accounting.
- **Harm:** no data corruption. A drive's own committed writes are kept, and a lost cancel only
  prolongs the loop.

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
  `_DRIVE_SETTLE_WARN_S` (10 s) it logs an ERROR if the drive is still running. That timer is
  observability only: it never abandons the drive and never cancels it again.
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

**Rejected alternatives** (evaluated with experiments; details in the PR):

| Alternative | Why rejected |
|---|---|
| Test-only bound | Hides the production defect. |
| Caller retry | Retry-hiding, and the loss stays silent. |
| `cancelling()` post-check | 3.10 has no `cancelling()`, and on 3.12+ it double-delivers the cancel. |
| `asyncio.timeout()` with a 3.10 fallback | Two code paths, a 3.12 loss when a drive suppresses the cancel, and `timeout(0)` starts the body. |
| Version switch | The 3.12+ branch is never run in CI. |
| `shield` variants | Still swallow the cancel. |
| A guard in `loop.py` | Changes scheduler semantics and breaks the soak test's fake asyncio. |
| A bounded settle that abandons the drive | The scheduler would end with its drive still writing, making B5's clean marker false. |
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
4. **On 3.12+ the drive runs as its own task**, as it already did on 3.10/3.11 and in CI. A
   `ContextVar.set()` inside a drive is not visible to the scheduler. Nothing on the drive path
   relies on that.
5. **A drive that suppresses the scheduler's cancel and returns** no longer loses the cancel, on
   any version. Stdlib 3.12's `wait_for` would lose it.

## 6. Regression coverage

**New: `tests/test_drive_seam_cancellation.py`, 29 tests.** All are in the default tier except
one `slow` stress test. Every wait on a task is bounded with `asyncio.wait(..., timeout)` plus an
assertion, so a regression fails the test instead of killing a worker.

| Test | What it proves |
|---|---|
| `test_control_stdlib_wait_for_loses_the_cancel_only_before_3_12[0,1,2]` | **Causal control.** The harness reaches the stdlib swallow on 3.10/3.11, and 3.12+ has none, so the tests below cannot pass without exercising the race. |
| `test_the_seam_helper_never_loses_the_cancel[0,1,2]` | The same harness never loses the cancel with the repaired helper. |
| `test_a_cancel_landing_as_the_drive_completes_propagates[0,1,2]` | Completion and forbidden states. The seam raises CancelledError; no `('nudge', 1)`, no `completed` reflection, the drive is done, the drive task is named, and an INFO log is written. |
| `test_a_drive_that_raises_as_the_cancel_lands_is_logged_not_leaked` | The cancel propagates, there is no `error` reflection, one ERROR with `exc_info` is logged, and no "exception was never retrieved" after GC. |
| `test_a_cancel_while_the_drive_runs_cancels_it_before_propagating` | The drive sees the cancel first, and nothing is recorded. |
| `test_a_second_cancel_during_cleanup_never_strands_the_drive` | The further cancel reaches the drive, and the seam ends only after the drive's cleanup. **Before, the drive was left running.** |
| `test_a_cancel_during_the_timeout_settle_waits_for_the_drive` | Same, during the timeout settle. |
| `test_a_drive_that_suppresses_the_cancel_still_does_not_lose_it` | The cancel is never lost, even when the drive suppresses it. |
| `test_a_drive_that_is_slow_to_settle_is_reported_and_still_awaited` | The settle warning is logged; the drive is never abandoned and never re-cancelled by the timer. |
| `test_a_drive_that_ends_cancelled_on_its_own_still_propagates` | The existing stop-on-self-cancel behaviour is kept. |
| `test_a_successful_drive_is_unchanged` / `test_a_crashing_drive_is_unchanged` | `('nudge', 1)` with `completed`; `(None, 0)` with `error` and the crash log. |
| `test_a_timeout_still_fails_the_drive_and_cancels_it` | `(None, 0)`, a `timeout` reflection, the warning, and the drive cancelled and done. Nothing tested this branch before. |
| `test_a_drive_that_completes_while_timing_out_keeps_its_result` / `…raises_while_timing_out_is_an_error` | `wait_for` parity on the timeout path. |
| `test_a_non_positive_timeout_never_starts_the_drive[0,-1]` | `wait_for` parity. |
| `test_stress_a_random_single_cancel_is_never_lost_and_strands_nothing` | 500 trials, a fixed seed, and one cancel at a random hop: no loss, and no drive left running. |
| `test_stress_control_the_same_harness_loses_cancels_to_stdlib_before_3_12` | The stress harness is sensitive: stdlib loses cancels there on 3.10/3.11 and none on 3.12+. |
| `test_one_cancel_landing_as_a_drive_completes_stops_the_real_scheduler[0,1]` | **The run-2 hang, deterministically, with the real `run_scheduler`.** It stops within the bound and *returns*, keeping the `loop.py` `break` contract that `_on_scheduler_task_done` relies on. The aligned drive ran once, no other drive ran, no ticks were written, and the store drained. |
| `test_stress_one_cancel_at_a_random_instant_stops_the_real_scheduler` (`slow`) | 120 trials with the real always-on drives and one cancel in the first 150 ms. |
| `test_daemon_stop_is_not_slowed_by_a_drive_completing_as_it_cancels` | **`KernelDaemon.stop()`, the production caller.** Under 4 s, no drive after the cancel, no tick, and no "did not terminate". |

**Hardened (defence in depth only): three teardowns that cancel the real scheduler.**
- They are `test_fresh_database_scheduler_startup_does_not_hang` and
  `test_startup_drives_are_paced_not_burst` (in `tests/test_scheduler_persistence_concurrency.py`),
  and the `scheduler` fixture in `tests/test_event_backbone_drive.py`.
- Each now cancels once, waits with a bound using `asyncio.wait`, and asserts the scheduler stopped.
- Before, a lost cancel either hung (`await task`) or passed silently late, because `wait_for(task, n)`
  cancels again on its timeout.
- If the cancel is lost they still cancel again, so nothing is left running.
- They change no assertion about what the tests test.

**Pre-fix control:** the new file run against the unmodified seam of `7b21a00`, on CPython 3.11.
- **16 failed and 13 passed.**
- **Failed, for the right reasons:**
  - "cancel swallowed; seam returned ('nudge', 1)" at hops 0, 1 and 2;
  - "a single cancel did not stop run_scheduler" at hops 0 and 1;
  - real-scheduler stress: "single cancels lost in trials [4, 88]";
  - `KernelDaemon.stop()`: "took 5.1s";
  - both further-cancel tests: the drive was still running when the seam returned;
  - the raise-at-the-instant and suppress-the-cancel cases.
  - The remaining failures are the helper-only tests (the helper does not exist there) and the
    settle-warning test.
- **Passed:** the controls and every "unchanged" test (success, crash, timeout parity, non-positive
  timeout).

**With the repair:** 29 passed on each of CPython 3.10.20, 3.11.15, 3.12.3 and 3.13.12.

**Mutation (CPython 3.11): 10 of 10 mutants caught.** Each mutant edits the repaired helper in
place; the new file then fails.

| Mutant | Caught by |
|---|---|
| M1: the call site reverted to `asyncio.wait_for` | the landing-as-completed tests |
| M2: the helper swallows like 3.11 | the model-loop test (`'ran-away'`) |
| M3: the settle raises at once on a further cancel | the second-cancel test: the drive is stranded |
| M4: the settle abandons the drive at the warning timer | the slow-settle test |
| M5: the warning timer re-cancels the drive | the slow-settle test |
| M6: the timeout path does not wait for the drive | the timeout-settle test |
| M7: a non-positive timeout starts the drive | the non-positive-timeout test |
| M8: the superseded exception is not retrieved | the raise-at-the-instant test |
| M9: the drive task is not named | the landing-as-completed tests |
| M10: a timed-out drive becomes a success | the timeout test: `(None, 1)` |

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
6. **CI never runs 3.12 or 3.13.** The new tests assert the same behaviour on every version and were
   run locally on all four.

## 8. Records corrected by this package

- **`docs/WINDOWS_WAL_WRITER_LOCK_REPAIR.md` §8 item 3.** The cost assessment is corrected, and the
  entry points here.
- **`RISKS.md`.** The writer-lock entry's mention is corrected, and a watchlist entry records this
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
  `main` after #124, by a merge commit, per record §9 of `docs/SQLITE_WAL_HEADROOM_REPAIR.md`. It is
  then requalified on its exact new head, and only then does the three-run sequence start, at 0/3.
- **PR #124's Windows gate runs the same seam.** Whether this repair lands before #124's next
  acceptance attempt, or #124 merges `main` after it, is Taylor's decision.

## 10. Remaining risks

- **The Windows attribution rests on elimination** (§3). The deterministic tests run in the Windows
  3.11 job, which is where they matter.
- **An unbounded settle can hold up `stop()`.** A future drive that ignores cancellation would do
  so. Mitigated by the 10 s ERROR log and by the `daemon.py:925` follow-up; this is the same as 3.12+
  today.
- **The forced-alignment tests rely on CPython's callback ordering.** They depend on `call_soon`
  being FIFO and on done-callbacks being scheduled when a task completes. Both were stable on
  3.10–3.13; the causal control would catch a change on 3.10/3.11.
- **At-least-once re-run.** A drive superseded at shutdown runs again at the next start, and a
  time-dependent nudge can differ.
