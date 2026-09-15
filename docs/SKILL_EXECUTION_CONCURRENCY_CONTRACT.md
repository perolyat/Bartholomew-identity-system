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
| C5 | **Bounded execution** | An action longer than `execution_timeout` (default 60 s) is cancelled; the request fails `Skill action timed out: …`; the skill returns to `READY`. A skill that ignores cancellation is abandoned after 5 s rather than holding the skill. | `…execution_is_bounded…`, `…swallows_cancellation_is_abandoned…` |
| C6 | **Cancellation** | Cancelling a request in flight cancels its action, closes the `RUNNING` window, releases the occupancy and propagates; the next waiter runs. Cancelling a waiting request removes it with no other effect. | `…cancelling_the_request_in_flight…`, `…cancelling_a_waiting_request_leaves_no_trace` |
| C7 | **Exceptions and recovery** | An exception escaping `execute()` fails the request and marks the skill `ERROR` — sticky until `reload_skill()`, which is the recovery path. A request queued behind it is refused with `state=error`, never run on the broken skill, and never masks the `ERROR`. | `…exception_marks_the_skill_error_and_a_queued_request_never_masks_it` |
| C8 | **Re-entrancy** | An action that asks the registry to run an action on its own skill, directly or through a cycle of skills, is refused at once (`Re-entrant action refused: …`) — never deadlocked. A nested call to a different skill is ordinary. A task the action spawns is bound by the same rule only while that action is still executing; once it has ended, the spawned task may call the skill like any other caller. | `…re_entrant_requests_are_refused_never_deadlocked`, `…task_spawned_by_an_action_may_call_the_skill_once_that_action_has_ended` |
| C9 | **Governance ordering** | Brake, Identity policy and consent are evaluated once per request before it waits; the fail-closed brake and the skill's lifecycle are re-checked once the occupancy is held, right before the action runs. Consent is not asked twice. | `…brake_engaged_while_the_request_waited_still_blocks_it` |
| C10 | **Exactly once** | An admitted request executes at most once, is audited and reflected exactly once (`_finish()` once per attempt, refusals included), and the registry never retries. | `…simultaneous_requests…` (audit rows == attempts), `…waiting_is_bounded…` |
| C11 | **Shutdown** | `unload_skill()` marks the skill `UNLOADING` first (new arrivals refused), waits — bounded by `execution_timeout` — for the action in flight, refuses every queued waiter, then shuts the instance down. Nothing ever runs on an unloaded instance. | `…unload_waits_for_the_action_in_flight_and_refuses_the_queued_one` |
| C12 | **Telemetry** | `get_skill_info()` and `get_status()` report the in-flight action, how long it has run and the number of waiters (`"occupancy"`). | `…occupancy_is_reported_while_an_action_is_in_flight` |
| C13 | **Worker rule** | Only storage work rides the daemon's single blocking worker. Network and other non-storage blocking work runs on its own thread (`run_off_loop(..., executor=None)`), so a slow endpoint never queues persistence. | `…notification_webhook_never_rides_the_storage_worker`, `…forecast_fetch_never_rides_the_storage_worker` |
| C14 | **Fail-closed reads under concurrency** | Concurrent first-touch of the governance store seeds its singleton once and never turns a legitimate request into a brake refusal. | `tests/test_governance_store.py::test_concurrent_first_touch_seeds_one_row_and_never_fails_closed` |

Failure strings a caller may see from this contract, all as `SkillResult.fail` (status `error`):
`Skill not ready: …`, `Skill busy: …`, `Skill action timed out: …`, `Skill action cancelled: …`,
`Re-entrant action refused: …`, `Blocked by parking brake (scope=skills)`. None is retried by the
registry.

## 4. Implementation map

- `SkillOccupancy` (dataclass on `LoadedSkill`): the per-skill `asyncio.Lock`, the in-flight
  action and its start, the waiter count. Constructed only inside `load_skill()`, on the running
  loop.
- `_execute_action_inner()`: C1 and C8 at admission (before the Governance stages, which are
  unchanged), then `_execute_occupied()`, then `_finish()` exactly once.
- `_execute_occupied()`: C2–C7, C9, C10, C12. The action runs as its own task under
  `asyncio.wait()` — deliberately not `asyncio.wait_for()`, which on Python 3.11 can return the
  inner result instead of raising when completion and cancellation land in the same loop iteration
  (`docs/WINDOWS_WAL_WRITER_LOCK_REPAIR.md` §8 item 3). `_leave_running()` restores `READY` only
  from `RUNNING`, so a lifecycle transition that landed during the action (`UNLOADING`, `ERROR`)
  is never overwritten.
- `_acquire_occupancy()` / `_withdraw()`: bounded, cancellation-safe acquisition of the lock;
  an acquisition that won the lock in the same instant its wait was cancelled is given back.
- `unload_skill()`: C11. `reload_skill()`: the C7 recovery path (unchanged).
- `_ACTIVE_SKILL_CHAIN` (`ContextVar[tuple[_ActionFrame, ...]]`): the actions executing up the
  current task's call chain, copied into each action's task; a frame stops being live when its
  action ends. `_is_reentrant()` reads it for C8.
- `SkillRegistry(queue_timeout=30.0, execution_timeout=60.0)`: the two bounds, keyword-only
  defaults chosen above the longest legitimate action (a 10 s webhook POST, a 5 s `busy_timeout`).
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
  the contract prefers the registry to stay responsive over waiting forever.
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
| acceptance suite, 15 consecutive runs | 15 of 15 pass (18 tests each) |
| acceptance suite against the pre-contract registry and skills | 16 or 17 of 18 fail per run: the independence clause (C3) held before as well, and the simultaneous-requests test fails only on the runs where the old code actually overlaps the actions; the store race lives in `tests/test_governance_store.py` and fails 5 of 5 against the old seed |
| acceptance suite plus the store tests under four xdist workers with four CPU hogs, 3 runs | 45 of 45 pass each run |
| full default suite (`-n auto --dist loadfile`) | 5106 tests, 0 failures, 0 errors, 2 skipped, exit 0 |
| load probe: 300 requests per run across `tasks`, `notify` and `calendar_draft` through `execute_action()` with the daemon's single worker wired, random arrival ticks, ~6 % of requests cancelled mid-flight, 100 ms blocking jobs queued on the worker for ~15 % of arrivals; 3 seeds unloaded, 2 seeds under four CPU hogs | no contract refusal in any run; `max_active` 1 per skill (no overlap); waiters 0 and every skill `READY` at the end; one audit row per completed attempt (279–290 per run); 8–11 s per run |
| the two reproductions of the original defect (arrival inside the window; a 500 ms job queued on the worker) | 0 of 30 and 0 of 10 refused, the window intact (~6 ms and ~510 ms) |
