# Windows Writer-Lock / WAL Reliability Repair

> **Status:** Work-package record (2026-09-14; **PR #110, not merged — awaiting Taylor's User
> Approval Gate**), step 2 of the sequence Taylor approved at the User Approval Gate
> (`DECISIONS.md`, "The approved sequence"). **Non-canonical** — a document
> under `docs/`; `RISKS.md` carries the risk disposition, `DECISIONS.md` the rule this repair
> establishes, and `START_HERE.md` the current-state snapshot. Where this note and a canonical
> document disagree, the canonical document wins.
>
> **Scope, as approved:** this reliability class only. Not general database cleanup, not
> unrelated refactoring, not EXEC-02, not Band 0. Findings outside the class are recorded in §8
> and left unabsorbed. The two `test_event_backbone_drive.py` tests were assigned to this package
> as named members of the class; their reclassification and correction (§4) is the classification
> this package owed, recorded here rather than deferred.

## 0. The defect, in one paragraph

Bartholomew's `MemoryStore` writes through **aiosqlite**, which runs each call on a worker
thread and returns the result through the event loop. A write is therefore two round trips:
`await db.execute("INSERT …")` — after which the worker thread holds SQLite's single write
lock — and `await db.commit()`, which releases it. Between those two awaits the lock is held
by a thread whose *only* way to release it is for the event loop to run the coroutine one
more step. Every scheduler drive records a Reflection this way, several times a minute. A
**synchronous `sqlite3` write made on the event-loop thread** inside that window blocks the
loop while it waits for the lock; the holder cannot commit, because committing needs the
loop that is blocked; so the writer waits its whole `busy_timeout` (5 s, set by
`db_ctx.set_wal_pragmas`) and fails with `database is locked`. Once the window is hit the
failure is certain, not probabilistic, which is why the observed failures are always exactly
one `busy_timeout` long. On a loaded Windows runner (four xdist workers, slower file and thread
operations) the window is hit often; nothing about the mechanism is Windows-specific.

The repair is the discipline the repository already adopted in Phase B stage B2
(`docs/B2_EVENT_LOOP_ISOLATION.md`): **a statement that can wait for the write lock never runs
on the event-loop thread.** The production paths that still did are moved off the loop through
the existing `run_off_loop()` primitive. Off the loop, the same write waits the few milliseconds
until the commit lands.

A second, independent defect of the same class was found and repaired while establishing the
first: two connections racing to convert a **fresh** database file to WAL mode, where SQLite
refuses to invoke the busy handler for the loser (§3).

## 1. How the diagnosis was established

Nothing here inherits the 2026-09-09 hypothesis in `RISKS.md`; it was re-derived and then
either confirmed or refuted by direct evidence.

1. **Windows CI log, `main` at `a64f5af` (Merge Candidate run 34775856466).** The FND-04
   vertical-slice failure's captured log shows the causal chain verbatim:
   `eci/store.py:record_directive` → `sqlite3.OperationalError: database is locked`, raised
   from `eci_responder.py:speak_fn` inside
   `runtime_contract.py:run_spoken_output_through_runtime_contract` — a synchronous write on
   the event-loop thread — while the same test's captured stdout shows the scheduler's
   `self_check` drive in flight with `dur_ms=5000`: a drive that was mid-Reflection-write for
   exactly the writer's `busy_timeout`. The test then failed on `'eci.result' not in
   ['eci.request']` because no directive was issued, so no result could be reported.
2. **Deterministic reproduction on Linux** (`scratchpad/convoy_repro.py`, now
   `tests/test_sqlite_event_loop_convoy.py::test_the_mechanism…`): an aiosqlite connection
   with an executed-but-uncommitted `INSERT`, then the repository's own `wal_db()` writer on
   the loop thread with `busy_timeout = 1000` — **fails after 1.00 s, every time**; the same
   writer through `asyncio.to_thread` with the loop free — **succeeds in 54 ms**.
   The lines, verbatim from the job log (run 34775856466, Windows job 103773709751, captured
   stdout and log of the failing FND-04 test), so the classification in §6 can be checked
   without GitHub access:

       [Scheduler] tick=self_check ok=1 dur_ms=5000 next=1789325902
       ERROR bartholomew.kernel.runtime_contract:runtime_contract.py:2420 Spoken output failed after governance approval
       Traceback (most recent call last):
         File "...\bartholomew\eci\store.py", line 198, in record_directive
           conn.execute(
       sqlite3.OperationalError: database is locked
       The above exception was the direct cause of the following exception:
         File "...\bartholomew\kernel\runtime_contract.py", line 2399, in run_spoken_output_through_runtime_contract
           speech_result = speak_fn(text)
         File "...\bartholomew\integration\eci_responder.py", line 159, in speak_fn
           directive = issuer.issue(
         File "...\bartholomew\eci\boundary.py", line 265, in issue
           record_directive(self.db_path, directive, user_id=self.endpoint.user_id)
       bartholomew.eci.store.EciPersistenceError: Directive dir-40518511d8c34e1f88a092e727ff6446 was NOT recorded (OperationalError: database is locked). It was not issued.

3. **Suite-wide enumeration** of every SQLite statement that can wait for a lock and that
   executed on a thread running an event loop, by running the whole default suite under an
   instrumented `sqlite3.connect` (the detector now lives in
   `tests/helpers/event_loop_sqlite.py`). 905 recorded rows, one per xdist worker per site:
   442 distinct sites, 258 after excluding `PRAGMA journal_mode` (a read on an already-WAL
   database), 381 distinct production call chains. Classified by outermost
   production frame into: writers reachable **during operation** concurrently with the
   scheduler's aiosqlite Reflection writes (the defect class, §2); writers that run only at
   daemon construction, start or stop, when no aiosqlite transaction can be in flight (out of
   scope, §8); writers on the **platform** database, a separate file no aiosqlite connection
   ever opens, which therefore cannot convoy (§8); and stores that tests call directly on the
   loop, which is the test's business rather than the product's.
4. **Reproduction of each repaired production path** against a real in-flight `MemoryStore`
   transaction, before and after the repair (§4).

## 2. What was repaired, and why each change is the smallest correct one

Every change routes an existing synchronous `sqlite3` write through the existing
`bartholomew.kernel.blocking_executor.run_off_loop()`. No timeout was changed, no data write
gained a retry (the one retry added is of the WAL-conversion pragma on a fresh file, scoped to
a conversion still pending — see the last row), no test was skipped, quarantined or loosened,
WAL stays on, and the `busy_timeout` decision of 2026-08-22 is untouched.

One consequence of the change needed its own repair, found by the independent review of this
PR: awaiting the permission check and the save gave each skill action's read-modify-write
suspension points it never had, so two concurrent actions on one record could each read the
row before either wrote it back, and the later write silently undid the earlier one while both
reported success (measured: 0/40 rounds lost a change on `main`, 39/40 on the branch as first
written). Every mutating skill action now does its read-modify-write inside one off-loop
immediate transaction (`TasksSkill._mutate_task`/`_take_task`, `CalendarDraftSkill._mutate_event`/
`_take_event`, `NotifySkill._transition_notification`, which also makes the queue's SENT claim a
compare-and-set against a concurrent cancel), so the row is the serialization point again.
`tests/test_skill_actions_serialize_on_the_record.py` holds it under both executors.

| Path (during operation) | Writer that ran on the loop | Change |
|---|---|---|
| Skill actions: `notify` (`set_quiet_hours`, `mute`, `unmute`, `send`, `queue`, `cancel`, queue processing, the lazy mute-expiry clear), `tasks` (`create`, `complete`, `update`, `delete`), `calendar_draft` (`create`, `update`, `delete`), and each skill's `initialize()` schema/settings writes | `_save_settings`, `_save_notification`, `_save_task`, `_delete_task`, `_save_event`, `_delete_event`, `_init_database`, `_load_settings` | Each call site awaits the new `SkillBase._run_off_loop()`; `NotifySkill._is_muted()` is now awaitable because its expiry clear persists |
| Every skill's own permission self-check (`SkillBase._require_permission`, 20 call sites across four skills) | `PermissionChecker.check()` → `_log_audit` (the required `permission_audit` row) and `_check_db_grant` | `_require_permission` is now `async`, runs the check off the loop **under a copy of the task's context** so WP-A2's `ContextVar` collector of failed audit writes still receives a failure recorded on the worker thread |
| The registry's own pre-action gate, `SkillRegistry._resolve_permissions` | `PermissionChecker.check()` / `grant_session()` | New `_permission_call()` helper, same context-preserving off-loop pattern; the registry now hands its shared `SingleWorkerExecutor` to skills through `SkillContext.blocking_executor` so skill writes join the daemon's confirmed drain at shutdown |
| The `fts_optimize` scheduler drive | `FTSClient.optimize()` | `run_off_loop(fts.optimize, executor=ctx.blocking_executor)` |
| The External Capability Interface: `boundary.submit()` (schema), `boundary._settle()` (result correlation), `DirectiveIssuer.issue()` reached through the production responder's `speak_fn`, and the `/api/eci/availability` route | `eci/store.py`'s `ensure_schema`, `settle_directive`, `record_directive`, `record_availability` | Off-loop at each async seam; `speak_fn` is now a coroutine (the spoken-output seam already awaited an awaitable result) |

**Independent defect, same class — the fresh-file WAL conversion race (`db_ctx.set_wal_pragmas`).**
On a database that is not yet in WAL mode, `PRAGMA journal_mode = WAL` rewrites the header by
escalating its own read lock to a write lock. When two connections race to convert the same
fresh file, SQLite deliberately does **not** invoke the busy handler for the loser of that
escalation (its documented deadlock-avoidance rule) and returns `SQLITE_BUSY` at once, whatever
`busy_timeout` or the connection's `timeout=` says — so `database is locked` is raised on the
very first pragma of a brand-new database. Reproduced through the repository's own `wal_db()`:
two spawned processes failed on **19 of 40** barrier-synchronised attempts on Linux; two threads
on **9 of 60**. This is the mechanism behind
`tests/test_sqlite_wal_concurrent_processes.py`'s intermittent `Worker 0 failed: database is
locked` recorded on `main` since 2026-08-15. `set_wal_pragmas()` now retries that one pragma
for up to five seconds with a short backoff — the winner's conversion takes milliseconds, after
which the pragma is a read. Nothing else about the connection authority changed. **0 of 60**
attempts fail after the repair.

**Second consequence of the rule — the registry's occupancy (found 2026-09-15 by automated
review after the gate report; repaired here, not deferred).** Moving the writes off the loop gave
every action's `execute()` suspension points, and `SkillRegistry.execute_action()`'s `is_ready`
guard — one bit that had always stood in for both "alive" and "free", born as lifecycle
telemetry with the registry (`a29bcca`, 2026-01-21) and never intended or tested as a serializer
— began refusing a request that arrived while another action was executing on the same skill:
`Skill not ready: <skill> (state=running)`. Measured through the production path on this tree and
on unmodified `main` (import path pinned per tree): a request arriving inside the ~8 ms window was
refused **30 of 30** times here and **0 of 30** on `main`, where the window had no suspension
point; a 500 ms blocking job queued on the daemon's single worker stretched the window to 513 ms
and a request arriving 100 ms into it was refused **10 of 10** times. The same guard was already
reachable on `main` for `notify.send` with a webhook configured (the POST, up to 10 s) and
`forecast.lookup` (the provider fetch); a `CancelledError` escaping any action had always left the
skill `RUNNING` forever. Four independent readers (callers, design intent, repair, refutation)
established the reach, the history and the shape of the fix; the correction is a contract at the
registry's admission boundary rather than a patch over the guard: lifecycle-only admission,
per-skill FIFO occupancy, bounded waiting and execution without `asyncio.wait_for`, exact
cancellation, `ERROR` never masked, re-entrancy refused, the fail-closed brake re-checked under
occupancy, occupancy-aware unload, and occupancy telemetry —
`docs/SKILL_EXECUTION_CONCURRENCY_CONTRACT.md`, `DECISIONS.md` ("Skill execution is one action per
skill instance at a time"). Two further defects intrinsic to the same subsystem were repaired with
it: the notification webhook POST and the forecast provider fetch had come to ride the daemon's
single SQLite worker once `SkillContext` carried it (on `main` the same `getattr` fell through to a
plain thread), so a slow endpoint queued every persistence write in the process — network I/O now
runs on its own thread (`executor=None`); and `GovernanceStore`'s first-touch seed of its
singleton row was a check-then-insert, so concurrent fail-closed brake reads on a fresh file —
what a burst of requests does when no shared store is wired — collided and reported the brake
**engaged**, refusing legitimate requests as "Blocked by parking brake" (2 of 40 five-request
bursts locally); the seed is now `INSERT OR IGNORE`, forced deterministically by
`tests/test_governance_store.py`. The first implementation of the contract was then reviewed
adversarially (scratch reproductions against the working tree): a cancellation landing while a
cancelled or timed-out action was being settled still skipped the step that closes the `RUNNING`
window; the lifecycle re-check ran before the brake read, so an unload landing during that read
could be overwritten by `RUNNING`; the 60 s unload wait exceeded the daemon's 30 s shutdown
budget; and four lesser gaps. All are closed in the second pass — the window closes in the
execution path's `finally`, the lifecycle is checked with nothing awaited before the `RUNNING`
transition, unload gives an action 10 s then cancels it and `shutdown()` unloads concurrently, a
non-coroutine `execute()` is contained as an error, an action's outcome after the settle is read,
a cancelled unload restores the skill — each pinned by a test written against the review's own
reproduction, and the review's scripts all hold on the second pass.

## 3. Why this is the defect and not a symptom

- The failure signature is *exactly* one `busy_timeout` long, in every observation: the RISKS
  probe of 2026-09-09 (three writers each waiting exactly 5 s), the `dur_ms=5000` tick in the
  FND-04 log, the 1.00 s failure in the deterministic reproduction. Ordinary lock contention
  produces a distribution of waits; a convoy produces the timeout value.
- The 2026-08-27 scheduler pacing (`DRIVE_PACE_S`) entry in `RISKS.md` records that the failure
  rate was never re-measured after pacing, so pacing is not evidence either way; mechanically,
  pacing spreads writes and cannot help a writer that is blocking the only thread able to
  release the lock it wants.
- The repair removes the *dependency* (a loop-thread wait on a loop-dependent holder), not the
  contention. The same contention still occurs; it now resolves in milliseconds, which is what
  the "repaired" reproductions measure.
- Widening `busy_timeout` would have lengthened the stall, not removed it; disabling WAL would
  not have changed the two-round-trip aiosqlite transaction at all.

## 4. Regression protection

| Test | What it detects |
|---|---|
| `tests/test_sqlite_event_loop_convoy.py::test_the_mechanism…` | The convoy itself, executable: a loop-thread writer through `wal_db()` fails after its `busy_timeout` against an in-flight aiosqlite commit; the same writer off the loop succeeds. |
| `…::test_*_completes_while_a_memory_write_is_in_flight` (notify quiet-hours; notify send + mute; tasks create; calendar create; a skill's permission-audit write; ECI directive issue; the `fts_optimize` drive) | Each repaired production entry point, run against a real held `MemoryStore` transaction that only the loop can release. **On `main` at `57f86f8` all of them fail** — `database is locked`, "no directive was issued", "took 5.33 s" (verified by running the new tests against an unmodified worktree of `main`). |
| `…::test_the_repaired_paths_write_nothing_to_sqlite_on_the_event_loop_thread` | The structural invariant over every repaired path, with no lock holder: no lock-waiting statement executes on a loop thread from production code. Guards against the defect returning one call site at a time. |
| `tests/test_fnd04_eci_vertical_slice.py::TestTheLoopStaysOffTheEventLoop` | The same invariant through the real app, the real `/api/eci` routes and the production responder — the exact path that failed in CI. |
| `tests/test_sqlite_wal_concurrent_processes.py::test_two_connections_converting_a_fresh_database_to_wal_both_succeed` | The fresh-file conversion race, 40 barrier-synchronised attempts. **Fails on `main`** (`fresh-database WAL conversion lost the race`). |
| `tests/helpers/event_loop_sqlite.py` | The detector the two invariant tests use, available to any future suite. |
| `tests/test_skill_actions_serialize_on_the_record.py` (8 tests, `to_thread` and `SingleWorkerExecutor`) | The consequence the review found (§2): concurrent `update`+`complete` on one task, `update`+`delete`, two field updates on one event, and `cancel` racing the notification queue keep every change. **Fails on the branch as first written** (round 0 loses the rename). |
| `tests/test_skill_registry_execution_contract.py` (28 tests) | The second consequence (§2) and the whole execution contract, each clause through `SkillRegistry.execute_action()` only: a request arriving during another's window waits and succeeds (gated skill; the real `TasksSkill`; behind queued blocking work on the single worker), simultaneous requests execute once each without overlap, independent skills do not wait, bounded waiting and execution, cancellation in flight and while waiting, an exception marks `ERROR` and is never masked, re-entrancy refused without deadlock, a brake engaged while waiting still blocks, unload waits for the action in flight and refuses the queued one, occupancy telemetry, the webhook and forecast fetch off the storage worker, and the ten findings of the adversarial review (cancellation during the settle, a second cancellation, an unload during the brake re-check, a non-coroutine `execute()`, an exception while being cancelled, a result landing during the settle, the unload bound, a cancelled unload, concurrent shutdown, the forecast seam's wording). **Of the first 18, 16 or 17 fail against the pre-contract registry and skills** (the independence clause held before as well); 15 of 15 consecutive runs pass with the contract, and a task an action spawns may call the skill once that action has ended. |
| `tests/test_governance_store.py::test_concurrent_first_touch_seeds_one_row_and_never_fails_closed` | The governance store's seed race, forced by holding every thread between its SELECT and its INSERT. **Fails 5 of 5 against the old seed** (`IntegrityError: UNIQUE constraint failed`). |

The two convoy tests that call a skill's permission self-check do so through a shape-agnostic
helper (`_permission_outcome`, awaiting the result only if it is awaitable), so on the unrepaired
tree they fail for the defect (`took 5.32s: the audit write convoyed`; the invariant listing the
on-loop writes) and not for the changed signature.

### `tests/test_event_backbone_drive.py` — reclassified, and its two failing tests corrected

The two members of this file named in `RISKS.md` as writer-lock failures are not: the
`a64f5af` log shows no lock error in either. Both tests waited for the **first** durable
effect of a processing pass and then asserted a **later** one without waiting for it: the
handler attaches the evidence, a separate transaction settles the processing row to
`processed`, and the scheduler records the drive's tick only after the drive returns. A poll
returning on the evidence can read the processing row a few milliseconds before settle lands
(`assert 'claimed' == 'processed'`); a poll returning on the terminal state can read `ticks`
before the tick is written (`the scheduler recorded no tick`). Injecting a 0.5 s delay
between effect and record makes the **unmodified** tests fail on every run, with exactly the
CI messages; the corrected tests, which wait for the state each assertion is about, pass under
the same injected delay and in repeated plain runs. The assertions are unchanged; only the
wait condition now matches what the design guarantees.

## 5. Validation

Linux (this session, `scratchpad` virtualenv, Python 3.11.15, SQLite 3.45.1, aiosqlite 0.22.1):

- Focused suites for every touched area: green once the two callers of `notify._is_muted()`
  awaited the now-async method (§4); the full-suite run in §5.1 supersedes them.
- New tests against unmodified `main`: 10 failures, as designed (§4) — the seven
  `*_completes_while_a_memory_write_is_in_flight` tests, the two invariant tests and the WAL-race
  test; the mechanism test passes on `main` by design.
- `tests/test_event_backbone_drive.py`: passes under the injected delay and in three plain runs.

### 5.1 Linux full default suite

Code head `ac8eea9` (the later `5fb9c88` changes only the two Windows CI invocations, §5.2):

    venv/bin/python -m pytest -p no:cacheprovider -n auto --dist loadfile

5080 outcomes to 100 %, no `F` or `E` in the progress record, exit status 0 — the same invocation
shape as the Windows Merge Candidate job. Python 3.11.15, SQLite 3.45.1, aiosqlite 0.22.1,
pytest 9.1.1. The four touched test files of §4 were run five times each on the repaired tree (all
pass) and once against unmodified `main` in a worktree (10 failures, as designed).

**One test-side margin, found by the Merge Candidate re-run on `04db28d` (run 35034729745, Ubuntu
3.10 default suite, 15 Sep 23:24).** `tests/test_sqlite_event_loop_convoy.py::test_the_mechanism_a_writer_on_the_loop_cannot_outwait_an_aiosqlite_commit`
failed `database is locked` in its second half, where the same write is repeated off the loop and
must succeed: that writer reused the 500 ms busy budget the first half uses to demonstrate the
convoy briefly, so on a loaded runner it gave up before the holder's commit — which the loop
schedules 0.1 s later — could reach the aiosqlite thread. The test had passed on every earlier
Ubuntu and Windows run of both Pythons, including the first dispatch on the same head. The off-loop
writer now waits with the production budget (5 s, as `set_wal_pragmas` sets it); the completion
assertion still bounds it at 2.5 s and the on-loop half is unchanged. Not the repair's mechanism —
a test that reported a convoy where there was none.

### 5.2 Windows Merge Candidate runs

Runs of `merge-candidate.yml` — the `push` of `57f86f8` to `main`, then two `workflow_dispatch`
runs on this branch; the Windows job runs the whole default suite with `-n auto --dist loadfile`
under a 40-minute cap. The `-vv` on the Windows jobs nets to pytest's `-v` because `pyproject.toml`'s
`addopts` carries `-q`; that is the one-line-per-test level the attribution needs.

| Run (UTC) | Head | Windows full default suite | Other six jobs |
|---|---|---|---|
| 34850155554 (13:35) | `57f86f8`, unmodified `main` | **cancelled at the cap**: `[gw2] node down: Not properly terminated` at ~65 %, 99 % reached at 13:56, nothing further, junit never written | all green |
| 34857413080 (14:43) | `ac8eea9`, this branch | **cancelled at the cap**: `[gw0] node down: Not properly terminated` at ~67 % (14:54), 99 % reached at 15:02, seven more tests by the 15:23 cap, junit never written | all green |
| 34866265459 (16:04) | `5fb9c88`, this branch, `-vv` | **reached its summary**: `1 failed, 4983 passed, 79 skipped, 164 warnings in 0:38:33` — 5063 of the 5080 selected tests reported; 17 never ran (the remainder of `test_scheduler_queue_containment.py` after the worker crash, and the second rare-token test); junit written. Still `cancelled`: the last test, `tests/integration/test_lexical_over_vector_on_rare_tokens.py::test_lexical_beats_vector_on_exact_rare_tokens` (gw2, started 16:20:34), was reported PASSED at 16:44:29 — the instant the 40-minute cap fired; the one failure is `tests/test_scheduler_queue_containment.py::TestContainmentNeverDestroysAnObligation::test_a_heavy_system_generated_burst_leaves_every_genuine_row_untouched`, `worker 'gw1' crashed` (the 120 s per-test timeout) | all green |
| 34942899213 (07:40, 15 Sep) | `830f554`, this branch, `-vv` | **cancelled at the cap again, before the summary** (no junit): `[gw2] node down` at 07:52:05 on the heavy-burst containment test again; two further failures, `tests/test_device_consent_channel.py::test_the_per_tenant_cap_holds_under_concurrent_starts` (a 0.3 s settle window) and `tests/test_event_backbone_processing.py::test_a_crash_after_claiming_loses_nothing_and_duplicates_nothing` (a 1 s lease) — time-budget assertions on per-operation SQLite paths, green in run 34866265459 and 10/10 locally under load; their messages are not in the log. The tail this time was `tests/test_competency_worked_example.py::test_worked_example_round_trips_end_to_end` on gw0 (started 08:00:54, PASSED at 08:20:39 as the cap fired), while the rare-token test passed in 37 s. The eight `test_skill_actions_serialize_on_the_record.py` tests passed on Windows. No `database is locked` anywhere. | all six green |
| 34973153727 (13:08, 15 Sep) | `6094b9a`, this branch with the execution contract, `-vv` | **cancelled at the cap again**: `[gw2] node down` at 13:21:22 on the heavy-burst containment test as in both earlier verbose runs; one further failure, `tests/test_event_backbone_processing.py::test_a_crash_after_claiming_loses_nothing_and_duplicates_nothing` (the 1 s lease time-budget test, as on `830f554`); the tail this time `tests/integration/test_lexical_over_vector_on_rare_tokens.py::test_lexical_beats_vector_on_exact_rare_tokens` on gw4, started 13:30:27 and reported PASSED at 13:49:07 as the cap fired — 18 minutes for the test that held the tail for 24 minutes in run 34866265459 and passed in 37 s in run 34942899213, the last worker's last test once more. **All 18 execution-contract tests and the governance-store seed test passed on Windows** (13:24:05–13:24:33 on gw0, each in 1–3 s). No `database is locked`, no `Skill not ready`, `Skill busy` or other contract refusal anywhere in the log. Of the outcomes in the saved half of the log (from 51 %): 2377 passed, 2 failed, 73 skipped. | all six green (tests + coverage 3.10 and 3.11, critical integration 3.10 and 3.11, quality, smoke) |
| 35034729745 (23:14, 15 Sep) | `04db28d`, this branch with the contract's second pass, `-vv` | **cancelled at the cap again**: `[gw3] node down` at 23:24:35 on the heavy-burst containment test, the only failure; the tail `tests/integration/test_lexical_over_vector_on_rare_tokens.py::test_lexical_beats_vector_on_exact_rare_tokens` on gw4, started 23:31:40 and reported PASSED at 23:54:16 as the cap fired (22 minutes). **All 28 execution-contract tests and the governance-store seed test passed on Windows** (23:23:00–23:23:31). No `database is locked`, no contract refusal anywhere in the log. Of the outcomes in the saved half of the log: 2313 passed, 1 failed, 73 skipped. An earlier dispatch on the same head (35032692977) was cancelled from outside the job at 98 % after 16 minutes with no failure in its tail; superseded. | five green; the Ubuntu 3.10 tests job failed in the convoy mechanism test's own timing margin (§5.1), fixed in `8cf3707` |
| 35037740445 (23:54, 15 Sep) | `8cf3707`, this branch, the final code head, `-vv` | **cancelled at the cap again**: `[gw3] node down` at 00:05:06 on the heavy-burst containment test, the only failure in the log; 99 % reached at 00:13:10 and the tail `tests/integration/test_lexical_over_vector_on_rare_tokens.py::test_lexical_beats_vector_on_exact_rare_tokens` on gw1, started 00:12:59 and reported PASSED at 00:35:06 as the cap fired (22 minutes), the last worker's last test for the fourth verbose run running. **All 28 execution-contract tests passed (00:03:30–00:04:04), with the governance-store seed test, the eight `test_skill_actions_serialize_on_the_record.py` tests, the nine convoy tests and the three fresh-file WAL-conversion tests.** No `database is locked`, no contract refusal anywhere in the log. Of the outcomes in the saved tail (from 00:01): 2403 passed, 1 failed, 73 skipped. | five green; “Tests + coverage (Ubuntu, py3.11)” failed once on the 1 s lease time-budget test (§8 item 7), re-run once under the drive rule and **green on attempt 2** (02:52:07–03:00:14), which is the row below |
| 35037740445 attempt 2 (02:51, 16 Sep) | `8cf3707`, the re-run of that run's two non-green jobs | **ended early at 61 % on an xdist controller crash**, a failure mode not seen before on any head: gw1 died at 03:06:37 in the same millisecond it reported a test PASSED, then gw0, gw2 and gw3 died within 160 ms of each other at 03:07:36 — four of five workers inside a minute, on four unrelated files (the heavy-burst containment test, an execution-contract test, an experience-learning test, an ECI boundary test) — and the controller then crashed while rescheduling a replacement: `INTERNALERROR> KeyError: <WorkerController gw5>` in `xdist/scheduler/loadscope.py:275`, ending the session at `5 failed, 3132 passed, 6 skipped in 882.82s (0:14:42)`. Every one of the five failures is a worker that stopped existing (`node down: Not properly terminated`); none carries an assertion or a message. The ten execution-contract tests and the governance-store seed test that had run before the loss all passed, and there is no `database is locked` and no contract refusal in the log. Nothing here is attributable to a test's own behaviour from the controller log; it belongs with §8 item 1. | “Tests + coverage (Ubuntu, py3.11)” **passed** on this attempt (02:52:07–03:00:14), which is the one re-run the drive rule allows and confirms §8 item 7's reading of the 1 s lease test |

What the four runs establish:

- **No writer-lock failure appears in any Windows full-suite log.** The `database is locked`
  signature that named this class (§0) is absent from all four; on this branch the repaired paths
  ran under `-n auto` contention three times without it, the third verbose run with the
  read-modify-write repair and its eight tests included.
- **The "stalled tail" is the last test of the last worker, whichever test that is — outside this
  class.** In run 34866265459 it was `test_lexical_beats_vector_on_exact_rare_tokens` on gw2 (started
  16:20:34, PASSED at 16:44:29, the second the cap fired); in run 34942899213 it was
  `test_worked_example_round_trips_end_to_end` on gw0 (started 08:00:54, PASSED at 08:20:39, the
  second the cap fired) while the rare-token test passed in 37 s. In both, every other worker had
  finished minutes earlier and the one remaining test's PASSED report arrived with the cancellation.
  The timestamped lines from the first of the two:

      2026-09-14T16:20:34.5831756Z tests/integration/test_lexical_over_vector_on_rare_tokens.py::test_lexical_beats_vector_on_exact_rare_tokens
      2026-09-14T16:20:45.5255564Z [gw3] [ 99%] PASSED tests/test_stage0_alive.py::test_liveness_endpoints
      2026-09-14T16:44:29.9996481Z [gw2] [ 99%] PASSED tests/integration/test_lexical_over_vector_on_rare_tokens.py::test_lexical_beats_vector_on_exact_rare_tokens
      2026-09-14T16:44:30.0138975Z !!!!!!!! KeyboardInterrupt !!!!!!!!
      2026-09-14T16:44:30.0139874Z C:\hostedtoolcache\windows\Python\3.11.9\x64\Lib\threading.py:331: KeyboardInterrupt
      2026-09-14T16:44:30.0141549Z ===== 1 failed, 4983 passed, 79 skipped, 164 warnings in 2313.94s (0:38:33) =====

  The 120 s per-test timeout did not end either test, so this is not an ordinary blocking wait, or
  the timer thread could not run; the controller log cannot distinguish a call phase that ended at
  the cap from a report held back until it. That it moves between unrelated tests and always sits
  on the last worker points at the harness or the runner rather than at either test. The same
  shape — 99 %, silence until the cap, one earlier `node down` — is what unmodified `main`
  (34850155554) and the pre-verbose branch run (34857413080) showed. Nothing in either test is a
  repaired path.
- **The mid-run "node down" is the heavy-burst containment test, outside this class.**
  `test_a_heavy_system_generated_burst_leaves_every_genuine_row_untouched` makes 1000 `insert_nudge`
  calls, each opening and closing its own `wal_db()` connection: 4.25 s on Ubuntu, more than 120 s on
  the Windows runner under four workers, consistent with pytest-timeout's thread method ending the
  worker with `os._exit` — which the controller log cannot confirm; it shows only
  `[gw1] node down: Not properly terminated` and `worker 'gw1' crashed while running …`. xdist
  replaced the worker (as `gw4`) and the suite continued; the file's remaining tests were not
  re-run. Run 34942899213 repeated it exactly (`[gw2] node down` on the same test) and added two
  time-budget failures of the same cost shape. §8 item 7 carries the cost model.
- **The Windows baseline is therefore not yet trustworthy on this evidence**, for two named
  reasons that are independent of the writer lock. §8 items 1 and 7 carry them; §9 draws the
  consequence.

### 5.3 Nightly Windows serial runs (context, not the baseline)

`nightly.yml` "Windows, every marker" runs the whole collection serially, integration and slow
included, under a 120-minute cap. On `main` (run 34825458349, `a64f5af`, 08:58) both jobs complete
red — py3.11: `12 failed, 5305 passed, 80 skipped, 11 errors in 1:39:26`. On this branch (run
34857416478, `ac8eea9`, 14:43) both jobs were instead **ended by the 120 s per-test timeout**:
py3.11 at 35 %, inside `tests/test_memory_agency_review_fixes.py::test_filtered_pagination_addresses_the_filtered_set`
(a `slow` test: 900 upserts through `MemoryStore`, which opens a new aiosqlite connection and worker
thread per operation — the dump shows that thread inside `sqlite3.connect`; the test takes 10.6 s on
this Linux machine); py3.12 at 60 %, with `scheduler-db_0` inside `insert_tick`'s `conn.commit()`.
Neither stack is in a repaired path and neither is a lock wait (`busy_timeout` raises within 3–5 s;
these waited 120 s). The paired same-time runs dispatched at 16:04 settle it:

| Nightly, Windows every-marker | py3.11 | py3.12 |
|---|---|---|
| `main` `57f86f8`, run 34866271890 (control) | **ended by the 120 s timeout at 35 %** in `test_filtered_pagination_addresses_the_filtered_set` — the same test, at the same place, as the branch's earlier run; the dump is in `vector_store.upsert` → `wal_db` exit → `conn.close()` | completed: `12 failed, 5305 passed, 80 skipped, 11 errors in 1:39:02` |
| this branch `5fb9c88`, run 34866268731 (`-vv`) | completed: `10 failed, 5318 passed, 80 skipped, 11 errors in 1:39:41` | **ended by the 120 s timeout at 62 %** in `tests/test_scheduler_queue_soak.py::TestAcceleratedQueueSoak::test_queue_reaches_a_bounded_stable_state`; the dump is in `insert_tick` → `wal_db` exit → `close_quietly` → `conn.close()` |

The kill moved between `main` and the branch and between Python versions, and the test that ended
the branch's first py3.11 run ended `main`'s control run. Every dump sits inside a per-operation
connection open or close. Classification: **runner slowness on connection-churn-heavy tests,
pre-existing on `main`, not this repair's effect** (§8 item 7). The branch's completed py3.11 run
shows 10 failures to `main`'s 12; the per-test attribution is in the junit artifacts and is not made
here.

## 6. FND-04 classification

**A. SAME ROOT CAUSE — supported by evidence.** The failing assertion
(`'eci.result' not in ['eci.request']`) is the *consequence*; the *cause* is in the same test's
captured log: `record_directive` on the event-loop thread failing with `database is locked`
after its full `busy_timeout`, while a scheduler drive's Reflection write was in flight
(`self_check … dur_ms=5000`). No directive was issued, so the reference endpoint had no result
to report, so the second `inbound_events` row never existed. That is the convoy of §0 on the
ECI's own writer, and the ECI writers are repaired and covered by the invariant test through
the real routes. The classification is from the log evidence, not from co-occurrence in the same
Windows run.

## 7. The Ubuntu critical-integration timeout

**NO CAUSAL RELATIONSHIP ESTABLISHED.** The cancelled PR #109 job (run 34842391085, attempt 2)
shows the 25-minute cap consumed by the two WP-A1 soak tests: 56 % reached at 12:52, the first
soak's progress line at 13:00:32 after ~8 minutes, the second's at 13:05:53, then no output until
the cancellation at 13:10:23. Those soaks are CPU-bound simulated-clock loops (17,663 iterations),
and the identical code passed in 16m13s on the next head. No lock error and no `busy_timeout`-shaped
stall appears in the log. The variance is consistent with runner performance, not with this
defect; it is preserved as a separate reliability concern.

**Seen again on this branch, 15 Sep 23:52 UTC:** the Integration tier's "Critical integration +
lifecycle (Ubuntu, py3.11)" job on `8cf3707` (run 35035701705) was cancelled by its 25-minute job
timeout inside the integration-marked step, while the same run's Ubuntu default-suite and Windows
jobs were green and the same job had passed on `04db28d` (22 minutes) and `6094b9a` (14 minutes) —
and while the Merge Candidate's seven jobs were running at the same time. The commit between the
two heads changes one test's timing budget and a docs paragraph. The classification stands:
NO CAUSAL RELATIONSHIP ESTABLISHED; the job was re-run once (drive rule), not fixed here.

**Not present on the head under review.** That re-run was itself cancelled 64 seconds in (23:55:26–23:57:04) — not by the 25-minute cap but by `integration.yml`'s `ci-integration-${{ github.ref }}` concurrency group, because the documentation commit `e195c13` was pushed at 23:56:45 and started a new Integration run for the new head. That run (35037876700) finished the same job **green in 13m52s** (23:57:14–00:11:06) alongside its two other green jobs, and the Merge Candidate's own two critical-integration jobs on `8cf3707` (3.10 and 3.11) were green in the run whose Windows job is recorded above. Three consecutive green results on this code, against one cancellation while seven other jobs ran: the classification stands, and the job-timeout variance remains a separate reliability concern rather than a property of this change.


## 8. Findings outside this package (recorded, not absorbed)

1. **Windows Merge Candidate "stalled tail".** The `push` of `57f86f8` (unmodified `main`, run
   34850155554) reached 99 % of the Windows default suite at 13:56 and then produced no output
   until the 40-minute cap at 14:15; earlier in the same run one xdist worker died
   (`[gw2] node down: Not properly terminated`). This is a hang, not a lock error, and it is what
   `RISKS.md` records as the job-budget symptom. **It recurred on this branch** (§5.2, run
   34857413080) with the same shape, and the verbose runs show it is **the last test of the last
   worker, a different test each time** — `test_lexical_beats_vector_on_exact_rare_tokens` for 24
   minutes in run 34866265459, `test_worked_example_round_trips_end_to_end` for 20 minutes in run
   34942899213 (the rare-token test passing in 37 s that time) — reported PASSED the second the cap
   fires, unended by the 120 s per-test timeout, after every other worker has finished. It points at
   the harness or the runner, not at a test. Independent of this repair and of the writer lock; the
   reproduction pointer (a Windows machine, `-n auto`, `faulthandler.dump_traceback_later` in the
   last worker) is in the Airtable row. Not this class; not absorbed.
   **A second shape appeared on the re-run of 16 Sep** (§5.2, run 35037740445 attempt 2): four of the five workers died inside one minute on four unrelated files, one of them in the same millisecond it reported a test PASSED, and the controller then crashed rescheduling a replacement worker (`KeyError: <WorkerController gw5>` in pytest-xdist's `loadscope.py`), ending the session at 61 %. A worker that stops existing reports its in-flight test as failed with no message, so those rows say nothing about the tests named. Same finding, same conclusion: the Windows baseline is not trustworthy yet, for reasons outside this repair.
2. **Nightly serial Windows job** (`nightly.yml`, run 34825458349 on `a64f5af`): 12 failed,
   11 errors in 1h39m, including at least one cp1252 console-encoding failure in a CLI test.
   On this branch and on `main`'s same-time control the jobs were ended by a per-test timeout in
   connection-churn-heavy tests, the kill moving between heads and Python versions (§5.3). Separate
   tier, not the Merge Candidate baseline; not classified here beyond §5.3.
3. **Cancellation swallowed by `asyncio.wait_for` (Python 3.11).**
   `run_drive_through_runtime_contract` awaits `asyncio.wait_for(drive_fn(ctx), timeout)`; in
   Python 3.11 `wait_for` returns the inner result instead of raising `CancelledError` when the
   inner future completes in the same loop iteration as the cancellation. The `a64f5af` log's
   second backbone-drive failure shows it: twelve more one-second ticks *after* the fixture's
   `task.cancel()`, until its 10 s `wait_for` timed out and cancelled again. Cost: up to the
   waiter's timeout of extra shutdown latency (5 s in `KernelDaemon.stop()`), never a lost write.
   Not this class.
4. **Operator self-state routes** (`routes/self_state.py`: `update_affect`, `set_attention`,
   `activate_drive`, `satisfy_drive`, `add_goal`, `complete_goal`) reach `narrator.py`'s
   episode writes synchronously through `global_workspace.publish` on the loop thread. Same
   shape as this class, operator-only, not observed failing, and offloading them would move
   `ExperienceKernel`'s in-memory mutations onto a worker thread — a separate decision.
5. **Platform-database writes on the loop** (`endpoint_auth.resolve` stamping `last_seen_at`,
   `auth.py` login/logout sessions, `device_inbound.resolve`). A separate file that aiosqlite never
   opens, so they cannot convoy; they block the loop only for ordinary contention. Recorded for
   completeness.
6. **Construction/start/stop-time writes on the loop** (`daemon.__init__`'s component schema
   scripts, skill `initialize()` before this repair, `_record_startup_incident`,
   `mark_clean_shutdown`, `MemoryStore.close()`'s TRUNCATE checkpoint). No aiosqlite transaction
   can be in flight at those points, so they are not members of this class; the skill
   `initialize()` writes were moved anyway because the same helpers serve runtime actions.
7. **`MemoryStore` opens a connection, and an aiosqlite worker thread, per operation.**
   `bartholomew/kernel/memory_store.py` uses `async with aiosqlite.connect(self.db_path)` at every
   method (36 sites); the nightly dump names that worker `Thread-14051` at 35 % of the run.
   Scheduler persistence and the vector store open a `wal_db()` connection per call. Each open
   pays file open, WAL pragma, shm mapping and a handle release, which is where Windows is slowest.
   Not a correctness defect and not this class; it is what pushed the heavy-burst containment test
   past 120 s in the Merge Candidate run (§5.2) and the `slow` memory and soak tests past 120 s in
   the nightly runs (§5.3). Its own Airtable row names the decision it needs.

   **The time-budget tests are wall-clock tolerances, not cost measurements, and one of them reached Ubuntu.** `tests/test_event_backbone_processing.py::test_a_crash_after_claiming_loses_nothing_and_duplicates_nothing` claims an event with a **one-second lease** and then asserts that the very next processing pass claims nothing; the pass reclaims it the moment more than a second of wall clock has passed. It failed on Windows in runs 34942899213 and 34973153727 and, for the first time, on Ubuntu in run 35037740445's py3.11 coverage job (`assert 1 == 0`, gw3). Measured on the two trees, the interval that assertion has to spare is the whole second: from the in-test claim to the pass's own claim is **3.0 ms median on this branch and 2.9 ms on unmodified `main`** (10 rounds each, idle), and **6.2 ms median / 18.9 ms worst on the branch against 3.6 ms / 22.9 ms on `main`** under 2.5× CPU oversubscription with coverage tracing (15 rounds each). The failure needs a stall of over a second in a path that costs three milliseconds, and the branch does not move that number: the test file, `event_processing/processor.py` and `event_processing/store.py` are untouched by this PR, and the pass's claim has always run through `run_off_loop()`. Runner stall, pre-existing, not this class.


## 9. Band 0 readiness

**NOT READY** on the evidence to date (2026-09-14/16; PR #110 open, not merged, head `e195c13`).

- The writer-lock class is root-caused, repaired and protected (§§0–4); the Linux suite is green on
  the repaired head (§5.1); the Windows full default suite reached its summary on the repaired
  branch with no writer-lock failure, 5063 of 5080 selected tests reported (§5.2, run
  34866265459; run 34942899213 on the final head adds the read-modify-write repair under the same
  contention, again with no writer-lock failure). That part of the pre-Band-0 requirement is met.
- The Windows Merge Candidate baseline is still not trustworthy, for two named reasons outside this
  class: the last worker's last test holds the suite past its cap on every verbose run (§8 item 1),
  and the per-operation connection cost pushes the heaviest tests past the 120 s per-test timeout
  and time-budget tests past their windows (§8 item 7). A baseline that cannot finish inside its cap cannot be called green, and Band 0 was gated on a
  trustworthy Windows baseline.
- The skill-execution concurrency contract that this branch also carries (§2, `docs/SKILL_EXECUTION_CONCURRENCY_CONTRACT.md`) is green everywhere it has been measured: all 28 of its tests on Windows in runs 35034729745 and 35037740445, and in every Ubuntu tier on the head under review.
- What changes the call: the two findings handled in their own packages (or a decision from Taylor
  that the attended checkpoint may proceed with them recorded), then the Windows full default suite
  finishing green inside its cap repeatedly.
