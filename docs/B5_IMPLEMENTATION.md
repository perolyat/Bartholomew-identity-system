# B5 Implementation — Startup and Shutdown Integrity

> Phase B, stage B5. Per `docs/PHASE_B_OVERVIEW.md`'s B5 scope: reliable startup failure handling
> and clean-shutdown evidence, established against "the concrete runtime B1–B4 produced" — not a
> hypothetical future one, and not yet covering the process lock (B6) or external request admission
> (B7).

## 1. A real gap found while reviewing the concrete B1-B4 runtime

`KernelDaemon.start()`'s pre-existing failed-start unwind (added for S5.0/issue #24, before Phase B)
only drained `self.scheduler_store` on an aborted startup. B2 (this phase) later added dedicated
executors to `MemoryStore`, `SkillRegistry`, and each of the three starter skills
(`TasksSkill`/`NotifySkill`/`CalendarDraftSkill`) — but never retrofitted the failed-start unwind to
drain them. Concretely: if `start()` failed *after* skills had loaded (e.g. during
`narrator.subscribe_to_workspace()` or later), those skills' and `SkillRegistry`'s own dedicated
worker threads would leak, since `stop()` never runs after a failed `start()` and nothing else would
ever close them. This is exactly the class of gap B5 exists to catch: "tests proving confirmed (not
assumed) termination of every resource B1–B4 introduced."

`KernelDaemon._unwind_after_failed_start()` (new) now drains, in the same order `stop()` uses (skills,
scheduler, memory store): `skill_registry.shutdown()` (which unloads any already-loaded skills,
draining their executors via each skill's own `shutdown()`, plus `skill_registry`'s own executor),
`scheduler_store.close()` (unchanged), and `mem.close(checkpoint=False)` (drains `mem`'s executor; no
checkpoint attempted on a possibly half-initialized database). Each step is independently
try/excepted so one resource's cleanup failure can't prevent the others' from running.

`tests/test_daemon_lifecycle_b5.py::test_failed_start_drains_mem_and_skill_registry_executors` and
`::test_failed_start_drains_already_loaded_skill_executors` reproduce this exact scenario (inject a
failure in `narrator.subscribe_to_workspace()`, chosen because it runs after skill loading) and prove
confirmed termination the same way `tests/test_db_executor.py` already establishes as this
codebase's convention: a closed `DedicatedDbExecutor` rejects new work with `DbExecutorClosedError`,
rather than asserting on `threading.enumerate()`'s exact timing (`DedicatedDbExecutor.close()`'s own
docstring is explicit that a `True`/drained return does not guarantee the underlying OS thread has
finished exiting by the moment it returns — only that the submitted work itself completed and no new
work will be accepted).

## 2. Poisoned-instance behaviour

A `KernelDaemon` whose `start()` has failed now sets `self._poisoned = True` before re-raising.
`start()` refuses to run again on a poisoned instance (raises `RuntimeError` immediately) rather than
attempting to re-initialize against sub-resources that may already be closed. `stop()` needed no
special-casing for a poisoned/never-started instance: every step it already performs
(`skill_registry.shutdown()`, `scheduler_store.close()`, `mem.close()`) was already idempotent before
this stage (verified, not assumed — see `docs/B2_IMPLEMENTATION.md`'s and `tests/test_db_executor.py`'s
own idempotency coverage), so calling `stop()` on an already-cleaned-up poisoned instance is safe.

## 3. Clean-shutdown marker (`bartholomew/kernel/lifecycle_marker.py`)

A new small module, not tied to Governance (unlike B3's schema) — this is general daemon-lifecycle
bookkeeping. Stored under `system_flags`'s existing `"daemon_lifecycle"` key (reusing the generic
key-value table `MemoryStore`'s schema already creates, rather than adding a new table for one flag):

- `start()` reads whatever marker the *previous* run left, immediately after `mem.init()` (so the
  table is guaranteed to exist). If it exists and its `state` never reached `"clean_shutdown"`, that
  means the previous run didn't call `stop()` to completion — a crash, `kill -9`, or an exception
  outside `start()`/`stop()`'s own coverage. This is logged as a warning
  (`"Unclean start detected: ..."`) and nothing else — per the overview's "conservative unclean-start
  recovery," no automatic remediation and no blocked startup. An aggressive automatic recovery action
  would itself be a new, unrequested risk surface; detection and honest logging is the bar this stage
  sets.
- `start()` then writes a fresh marker (`state="running"`, a new `instance_id`, `started_at`) before
  any further startup work.
- `stop()` writes `state="clean_shutdown"` (same `instance_id`, `stopped_at` set) as its very last
  step, after `mem.close()` has already drained and closed `mem`'s own dedicated executor — so this
  write uses a direct `sqlite3` connection rather than routing through it, consistent with several of
  `stop()`'s other steps (e.g. `experience.persist_snapshot()`) that already call synchronous code
  directly rather than through an executor.

Read/write logic lives in `lifecycle_marker.py` as plain functions (`read_marker`/`write_marker`,
mirroring `bartholomew/kernel/governance/schema.py`'s style), independently tested
(`tests/test_daemon_lifecycle_b5.py`'s `test_lifecycle_marker_*` tests) without needing a
`KernelDaemon` at all. A malformed marker value is treated as absent (logged nowhere, since this is a
diagnostic aid, not a correctness-critical value — unlike B3's Parking Brake, which fails closed on a
malformed legacy value because Governance state actually gates behavior).

`tests/test_daemon_lifecycle_b5.py::test_unclean_previous_run_is_detected_and_logged` reproduces a
simulated crash end-to-end: a first daemon starts against a database and is deliberately never
stopped (its executors are drained directly, without going through `stop()`, so the test itself
doesn't leak threads); a second daemon against the same database is asserted to log the unclean-start
warning naming the first instance's id, then to reach its own clean shutdown.

## 4. What "Governance write freeze and drain" (from the overview's B5 scope) does not cover here

The overview's B5 scope lists "Governance write freeze and drain" alongside producer termination and
executor shutdown. In the concrete runtime B1–B4 actually produced, there is nothing to freeze or
drain for Governance yet: B4's safety decision (`docs/B4_IMPLEMENTATION.md` §1) deliberately kept the
live daemon's Parking Brake checks on the legacy, stateless, per-call `check_scope_blocked()` path,
with no shared write path, no executor, and no long-lived instance — B3's `GovernanceBrakeStore`
remains unwired into any live path, deferred to land together with B6's CLI migration. There is
nothing live for this stage to freeze/drain; inventing a mechanism for a write path that doesn't
exist yet would be scope creep into B6's actual integration work, not honest characterization of the
concrete runtime this stage is scoped to. This is recorded here rather than silently skipped, so a
later reader doesn't assume it was overlooked.

## 5. Deliberately not covered (per the overview's own deferrals)

- The process lock and CLI racing against a running daemon — B6.
- Externally admitted work's inclusion in clean-shutdown evidence — B7 (this stage's clean marker
  reflects confirmed termination of the resources B1–B4 introduced, not the full §4 shutdown
  invariant, which the overview is explicit isn't satisfied until B7 lands).

## 6. Verification

`tests/test_daemon_lifecycle_b5.py` (9 tests: failed-start executor draining ×2, poisoned-instance
restart refusal, clean-marker write, unclean-previous-run detection, fresh-database silence, marker
module round-trip/missing-table/malformed-value ×3). `tests/test_clean_start_lifecycle.py`,
`test_kernel_alive.py`, and `test_cold_boot.py` (pre-existing lifecycle coverage) all still pass
unmodified. `black`/`ruff`/`mypy` clean on all new/modified files. Full existing test suite passes.
