# Skill Execution Concurrency Contract

> **Status:** established 2026-09-15 in PR #110 (**not merged — awaiting Taylor's User Approval
> Gate**), as the second consequence of the writer-lock / WAL reliability repair
> (`docs/WINDOWS_WAL_WRITER_LOCK_REPAIR.md`). **Non-canonical** — a document under `docs/`;
> `DECISIONS.md` carries the decision ("Skill execution is one action per skill instance at a
> time — an overlapping request waits, it is not refused"), `RISKS.md` the disposition and
> `START_HERE.md` the current-state snapshot. Where this note and a canonical document disagree,
> the canonical document wins.
>
> **Owner:** `bartholomew/kernel/skill_registry.py` (`SkillRegistry.execute_action()` and what it
> calls). **Acceptance suite:** `tests/test_skill_registry_execution_contract.py`, which drives
> the contract only through `execute_action()`, so any replacement of the implementation is
> measured against the same clauses.

## 1. Why this contract exists

**The defect.** The writer-lock repair moved every skill write, and every skill's permission
self-check, off the event-loop thread. That gave each action's `execute()` suspension points it
never had. The registry had always set the skill instance to `RUNNING` around `execute()` and
refused any request that found the instance in any state but `READY` — a guard born on
2026-01-21 with the registry itself (`a29bcca`, "SkillState enum for tracking skill lifecycle
states"), never documented as a serializer, never tested, and vacuous while `execute()` could not
suspend. After the repair the guard bit: a request arriving while another action was executing
on the same skill was refused as `Skill not ready: <skill> (state=running)`.

Measured through the production path (`SkillRegistry.execute_action()`, before this contract):

| case | unmodified `main` (`57f86f8`) | PR #110 before this contract |
|---|---|---|
| second request arrives while the first is executing, no contention | window never observable; 0 of 30 refused | window ~8 ms; **30 of 30 refused** |
| a 500 ms blocking job queued on the daemon's single worker | never observable | window ~513 ms; **10 of 10 refused** |
| two requests started in the same loop iteration | both succeed | both succeed (both clear the guard first) |

The window grows one-for-one with whatever the action waits for off the loop (queued work on the
single worker, a held SQLite write lock), which is exactly the contention regime the repair
targets. On `main` the same guard was already reachable for `notify.send` with a webhook configured
(the POST, up to 10 s) and for `forecast.lookup` (the provider fetch), and unreachable for every
other action. Where the refusal surfaced: HTTP 400 `{"detail": "Skill not ready: notify
(state=running)"}` on `/api/notifications/*`; "I tried to … but it didn't go through" chat replies
for task and forecast intents; a scheduler reminder recorded `DELIVERY_FAILED` and, by design, never
retried; a chat fact-capture notification dropped with only an audit row.

**The first implementation was then reviewed adversarially** (four lenses, scratch reproductions
against the working tree). It found that a cancellation landing while a cancelled or timed-out
action was being settled skipped the step that closes the `RUNNING` window — the very defect the
contract set out to remove, in a narrower form; that the lifecycle re-check ran before the brake
read, so an unload arriving during that read could be overwritten by `RUNNING`; that a 60 s unload
wait exceeded the daemon's 30 s shutdown budget; and four lesser gaps (a non-coroutine `execute()`
escaping, an exception after the settle never retrieved, a cancelled unload leaving `UNLOADING`,
one acquisition path without the withdraw checks). All are closed in the second pass and each is
pinned by a test written against the review's own reproduction (§3, §7).

**Two further defects found on the way, intrinsic to the same subsystem and repaired here.**
(1) Once `SkillContext` carried the daemon's blocking executor, `NotifySkill._deliver_notification`
and `ForecastSkill._action_lookup` ran their network I/O on the daemon's single SQLite worker (on
`main` the same `getattr` fell through to a plain thread because the field did not exist), so a
slow endpoint queued every persistence write in the process behind it for up to the 10 s webhook
timeout. (2) `GovernanceStore`'s first-touch seed of the `parking_brake_state` singleton was a
check-then-insert; two stores opened at once on a fresh file — what concurrent fail-closed brake
reads do when no shared store is wired — collided on the primary key, and the fail-closed reader
reported the brake **engaged**, refusing a legitimate concurrent request as "Blocked by parking
brake". Identical on `main`; forced deterministically by
`tests/test_governance_store.py::test_concurrent_first_touch_seeds_one_row_and_never_fails_closed`.

## 2. Was the prior arrangement structurally capable?

No, on four counts, so the correction is at the registry's admission boundary rather than a
timing patch over the guard:

1. **A boolean lifecycle state stood in for occupancy.** `is_ready` (`state == READY`) answered
   two different questions — "is this skill alive?" and "is it free right now?" — with one bit,
   so the only possible answers to a busy skill were "refuse" or "let everything overlap".
2. **The window could not always close.** `CancelledError` is not an `Exception`, so a request
   cancelled mid-action (an HTTP client gone, the scheduler task cancelled at shutdown) left the
   skill `RUNNING` forever and every later request refused. An exception marked the skill
   `ERROR`, correctly, but a concurrent completion could overwrite that with `READY`.
3. **Nothing bounded anything.** No wait bound, no execution bound, no cancellation discipline,
   no protection against an action re-entering the registry for its own skill (any per-skill lock
   added naively would have turned that into a deadlock).
4. **The single blocking worker carried the wrong work.** It exists so that storage writes are
   sequential and drained with confirmation at shutdown; network I/O on it stalls the whole
   process's persistence.

Rejected shapes, each considered against the callers in §1: **keep refusing** (RUNNING as
unavailability) — never intended, and the refusals were user-visible failures; **unrestricted
overlap** (drop RUNNING from the guard) — masks `ERROR`, contradicts the one-occupancy meaning the
instance's own state has always carried, and leaves skills' in-memory state (NotifySkill's
settings and queue) to interleave; **global serialization** — unrelated skills would wait behind
one 10 s webhook POST; **retry at the callers** — hides the defect in four places.

## 3. The contract

Every clause is enforced in `SkillRegistry.execute_action()` and pinned by a test that drives
that method only.

| # | Clause | Behaviour | Pinned by (`tests/test_skill_registry_execution_contract.py` unless noted) |
|---|---|---|---|
| C1 | **Lifecycle admission** | A request is admitted iff the skill is loaded and `READY` or `RUNNING`. `LOADING`, `UNLOADING`, `UNLOADED` and `ERROR` refuse at once (`Skill not ready: <skill> (state=<s>)`), before Governance, so a dead skill never raises a consent prompt. `RUNNING` is occupancy, not unavailability. | `…exception_marks_the_skill_error…`, `…unload_waits…` |
| C2 | **Per-skill occupancy** | At most one action executes on a skill instance at a time. A request that arrives while another is executing waits its turn, in arrival order, then gets a real outcome. | `…arriving_while_the_skill_is_running_waits_its_turn`, `…simultaneous_requests_each_execute_exactly_once_and_never_overlap`, `…a_real_skill_request_arriving_inside_the_window…` (TasksSkill), `…queued_blocking_work_extends_the_window_but_never_refuses…` (single worker) |
| C3 | **Independence** | Requests to different skills never wait on each other at the registry. | `…independent_skills_do_not_wait_on_each_other` |
| C4 | **Bounded waiting** | A wait longer than `queue_timeout` (default 30 s) is refused: `Skill busy: <skill> (waited <n>s behind <action>)`. The action in flight is untouched. | `…waiting_is_bounded…` |
| C5 | **Bounded execution** | An action longer than `execution_timeout` (default 60 s) is cancelled; the request fails `Skill action timed out: …`; the skill returns to `READY`. An action that completes while it is being cancelled keeps its real result. A skill that ignores cancellation is abandoned after 5 s rather than holding the skill. | `…execution_is_bounded…`, `…completes_while_being_cancelled_keeps_its_result`, `…swallows_cancellation_is_abandoned…` |
| C6 | **Cancellation** | Cancelling a request in flight cancels its action, closes the `RUNNING` window, releases the occupancy and propagates — at whichever await the cancellation lands, a second cancellation during the settle included; the next waiter runs. Cancelling a waiting request removes it with no other effect. | `…cancelling_the_request_in_flight…`, `…cancelled_while_its_timed_out_action_settles…`, `…second_cancellation_while_settling…`, `…cancelling_a_waiting_request_leaves_no_trace` |
| C7 | **Exceptions and recovery** | An exception escaping `execute()` — including one raised while the action was being cancelled, and an `execute()` that is not a coroutine or raises before returning one — fails the request and marks the skill `ERROR`, sticky until `reload_skill()`, which is the recovery path. A request queued behind it is refused with `state=error`, never run on the broken skill, and never masks the `ERROR`. | `…exception_marks_the_skill_error_and_a_queued_request_never_masks_it`, `…raises_while_being_cancelled_is_marked_error`, `…sync_execute_is_contained_as_an_error_not_escaped` |
| C8 | **Re-entrancy** | An action that asks the registry to run an action on its own skill, directly or through a cycle of skills, is refused at once (`Re-entrant action refused: …`) — never deadlocked. A nested call to a different skill is ordinary. A task the action spawns is bound by the same rule only while that action is still executing; once it has ended, the spawned task may call the skill like any other caller. | `…re_entrant_requests_are_refused_never_deadlocked`, `…task_spawned_by_an_action_may_call_the_skill_once_that_action_has_ended` |
| C9 | **Governance ordering** | Brake, Identity policy and consent are evaluated once per request before it waits; the fail-closed brake is read again once the occupancy is held, and the skill's lifecycle is checked once more with nothing awaited between that check and the `RUNNING` transition, so an unload that lands during the brake read is seen, never overwritten. Consent is not asked twice. | `…brake_engaged_while_the_request_waited_still_blocks_it`, `…unload_arriving_during_the_brake_re_check_is_never_overwritten` |
| C10 | **Exactly once** | An admitted request executes at most once, is audited and reflected exactly once (`_finish()` once per attempt, refusals included), and the registry never retries. | `…simultaneous_requests…` (audit rows == attempts), `…waiting_is_bounded…` |
| C11 | **Shutdown** | `unload_skill()` marks the skill `UNLOADING` first (new arrivals refused), gives the action in flight `unload_timeout` (default 10 s) to finish and cancels it if it does not, refuses every queued waiter, then shuts the instance down. `shutdown()` unloads every skill concurrently, so the daemon pays that bound once, inside its shutdown budget. An unload that is itself cancelled before it holds the occupancy leaves the skill loaded and usable. Nothing ever runs on an unloaded instance. | `…unload_waits_for_the_action_in_flight_and_refuses_the_queued_one`, `…unload_cancels_an_action_that_outlives_the_unload_bound`, `…cancelled_unload_leaves_the_skill_loaded_and_usable`, `…shutdown_unloads_skills_concurrently_within_one_unload_bound` |
| C12 | **Telemetry** | `get_skill_info()` and `get_status()` report the in-flight action, how long it has run and the number of waiters (`"occupancy"`). | `…occupancy_is_reported_while_an_action_is_in_flight` |
| C13 | **Worker rule** | Only storage work rides the daemon's single blocking worker. Network and other non-storage blocking work runs on its own thread (`run_off_loop(..., executor=None)`), so a slow endpoint never queues persistence. | `…notification_webhook_never_rides_the_storage_worker`, `…forecast_fetch_never_rides_the_storage_worker` |
| C14 | **Fail-closed reads under concurrency** | Concurrent first-touch of the governance store seeds its singleton once and never turns a legitimate request into a brake refusal. | `tests/test_governance_store.py::test_concurrent_first_touch_seeds_one_row_and_never_fails_closed` |

Failure strings a caller may see from this contract, all as `SkillResult.fail` (status `error`):
`Skill not ready: …`, `Skill busy: …`, `Skill action timed out: …`, `Skill action cancelled: …`,
`Re-entrant action refused: …`, `Blocked by parking brake (scope=skills)`. None is retried by the
registry. They are operator wording: the notification routes surface them as HTTP 400 as they
did every failure before, scheduler drives record them as the delivery detail, and the forecast
chat seam translates them (`runtime_contract._forecast_error`) into the capability's own words
rather than relaying registry text to a person who asked about the weather
(`…forecast_seam_does_not_relay_registry_refusal_wording`).

## 4. Implementation map

- `SkillOccupancy` (dataclass on `LoadedSkill`): the per-skill `asyncio.Lock`, the in-flight
  action and its start, the waiter count. Constructed only inside `load_skill()`, on the running
  loop.
- `_execute_action_inner()`: C1 and C8 at admission (before the Governance stages, which are
  unchanged), then `_execute_occupied()`, then `_finish()` exactly once.
- `_execute_occupied()`: C2–C7, C9, C10, C12. The action runs as its own task
  (`_start_action()`, which contains an `execute()` that is not a coroutine as an action failure)
  under `asyncio.wait()` — deliberately not `asyncio.wait_for()`, which on Python 3.11 can return
  the inner result instead of raising when completion and cancellation land in the same loop
  iteration (`docs/WINDOWS_WAL_WRITER_LOCK_REPAIR.md` §8 item 3). The window is closed in the
  function's `finally`, so every exit — result, refusal, exception, a cancellation at any await,
  a second cancellation during the settle — passes through `_leave_running()`, which restores
  `READY` only from `RUNNING`, so a lifecycle transition that landed during the action
  (`UNLOADING`, `ERROR`) is never overwritten. `_consume_settled()` reads a cancelled action's
  outcome: a result that landed during the settle stands, an exception marks `ERROR`. Every action
  task carries a done-callback that retrieves an exception nobody else consumed, so an abandoned
  action never leaves asyncio's "exception was never retrieved" behind.
- `_acquire_occupancy()` / `_withdraw()`: bounded, cancellation-safe acquisition of the lock;
  an acquisition that won the lock in the same instant its wait was cancelled is given back; an
  acquisition that finished any other way than with the lock is never treated as holding it.
- `unload_skill()`: C11, with `SkillOccupancy.in_flight_task` as the handle it cancels after
  `unload_timeout`. `shutdown()`: every skill concurrently. `reload_skill()`: the C7 recovery
  path (unchanged).
- `_ACTIVE_SKILL_CHAIN` (`ContextVar[tuple[_ActionFrame, ...]]`): the actions executing up the
  current task's call chain, copied into each action's task; a frame stops being live when its
  action ends. `_is_reentrant()` reads it for C8.
- `SkillRegistry(queue_timeout=30.0, execution_timeout=60.0, unload_timeout=10.0)`: the three
  bounds, keyword defaults chosen above the longest legitimate action (a 10 s webhook POST, a 5 s
  `busy_timeout`) and, for unload, inside the daemon's 30 s shutdown budget
  (`bartholomew/runtime/serve.py`, whose accounting now names the unload wait).
- `NotifySkill._deliver_notification()`, `ForecastSkill._action_lookup()`: C13
  (`executor=None`).
- `governance_store._import_legacy_state_if_needed()`: C14 (`INSERT OR IGNORE`, audit row only
  from the connection whose seed landed).

## 5. Scaling boundary and replacement path

**Boundary today.** One process, one event loop, one `SkillRegistry`, one blocking worker for
storage. Occupancy is in-process state on the registry; a skill's actions are serialized only for
requests that go through `execute_action()` — the workspace-event path (`handle_event`) runs
outside it, as it always has, and is recorded in `RISKS.md`. Latency, not refusal, is the cost of
contention: a request may wait up to one action (≤ `execution_timeout`) behind a slow one. There
are no priorities and no per-action concurrency classes.

**What must hold under replacement.** The contract is stated on `execute_action()`'s observable
behaviour, not on the lock. A future out-of-process skill host, a multi-worker registry, or
per-skill executors must enforce C1–C13 at their own admission boundary and pass the acceptance
suite unchanged (it never reaches into the implementation except to read `occupancy` telemetry).
Governance stays where it is: admission → Governance → occupancy → execution → one audit and one
Reflection, in that order.

**Documented relaxation.** Manifest-declared concurrency classes (for example, read-only actions
that may overlap a mutating one) would change C2 only — the occupancy would become per class
rather than per instance — and would require the manifest field that does not exist yet. Every
other clause is unchanged by it. Replacing the single storage worker with a per-database writer
or a pool changes nothing here except that C13's "own thread" becomes "not the storage
writer(s)".

## 6. Limitations, stated

- **Duplicate requests** (the same semantic request submitted twice) are not the registry's to
  detect; there are no idempotency keys. C10 guarantees each admitted request runs at most once.
- **Cancellation cannot interrupt thread work already submitted.** Off-loop SQLite work runs to
  completion (or rolls back) on its thread; the awaiting request is released at once. This is the
  blocking executor's documented behaviour and unchanged.
- **A skill that ignores cancellation** is abandoned after the settle bound (C5); its detached
  task may still be executing when the next request is admitted. This is a misbehaving skill, and
  the contract prefers the registry to stay responsive over waiting forever. The same skill can
  hold its occupancy past `unload_timeout` and the settle bound; unload then proceeds anyway
  (logged) rather than block shutdown.
- **Detached background tasks** an action spawns inherit its re-entrancy chain (a `contextvars`
  copy of live action frames). A call from such a task on the same skill is refused only while the
  spawning action is still executing — the registry cannot tell whether that action is waiting
  for the task, so it refuses rather than risk a deadlock — and is ordinary once the action has
  ended. No bundled skill spawns such tasks today.
- **The queue and execution bounds are policy**, chosen for today's actions; they are constructor
  arguments, not manifest fields.

## 7. Validation

CI evidence for the head that carries this contract is in `docs/WINDOWS_WAL_WRITER_LOCK_REPAIR.md`
§5 and on PR #110. Locally, on the contract's tree:

| check | result |
|---|---|
| acceptance suite, 15 consecutive runs | 15 of 15 pass (28 tests each, after the adversarial review's findings were pinned: cancellation during the settle, a second cancellation, an unload during the brake re-check, a non-coroutine `execute()`, an exception raised while being cancelled, a result that lands during the settle, the unload bound, a cancelled unload, concurrent shutdown, the forecast seam's wording) |
| acceptance suite against the pre-contract registry and skills | of the first 18 tests, 16 or 17 fail per run: the independence clause (C3) held before as well, and the simultaneous-requests test fails only on the runs where the old code actually overlaps the actions; the store race lives in `tests/test_governance_store.py` and fails 5 of 5 against the old seed. The ten review-finding tests were each written against a reproduction the review produced on the first implementation. |
| acceptance suite plus the store tests under four xdist workers with four CPU hogs, 3 runs | 45 of 45 pass each run |
| full default suite (`-n auto --dist loadfile`) | 5107 tests, 0 failures, 0 errors, 2 skipped, exit 0 on the first implementation; 5117 tests, 0 failures, 0 errors, 2 skipped, exit 0 on the second pass |
| load probe: 300 requests per run across `tasks`, `notify` and `calendar_draft` through `execute_action()` with the daemon's single worker wired, random arrival ticks, ~6 % of requests cancelled mid-flight, 100 ms blocking jobs queued on the worker for ~15 % of arrivals; 5 seeds unloaded, 2 seeds under four CPU hogs | no contract refusal in any run; `max_active` 1 per skill (no overlap); waiters 0 and every skill `READY` at the end; one audit row per completed attempt (279–290 per run); 8–11 s per run |
| the two reproductions of the original defect (arrival inside the window; a 500 ms job queued on the worker) | 0 of 30 and 0 of 10 refused, the window intact (~6 ms and ~510 ms) |
| the adversarial review's own reproductions of its findings on the first implementation (double cancellation, cancellation during the settle, unload during the brake re-check, unload giving up, a sync `execute()`, an exception during cancellation, a cancelled unload) | every one holds on the second pass: the skill ends `READY` or `ERROR` as the contract says, never `RUNNING` with a free lock, and nothing runs on an unloaded instance |
