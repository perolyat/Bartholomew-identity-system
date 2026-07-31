# B2 Implementation — Event-Loop Isolation and Database Execution

> Phase B, stage B2. Builds on `docs/B1_IMPLEMENTATION.md`'s caller inventory, which assigned 7
> files to this stage: `bartholomew/kernel/memory_store.py`, `bartholomew/kernel/skill_registry.py`,
> `bartholomew/skills/tasks.py`, `bartholomew/skills/notify.py`, `bartholomew/skills/calendar_draft.py`,
> `bartholomew/kernel/persona_pack.py`, `bartholomew/kernel/narrator.py`.

## 1. The mechanism: `DedicatedDbExecutor`

`bartholomew/kernel/db_executor.py` generalizes
`bartholomew/kernel/scheduler/store.py`'s `SchedulerStore` pattern (already proven in production —
see `DECISIONS.md`'s 2026-07-24 entry) into a reusable `DedicatedDbExecutor`: one dedicated worker
thread per owning instance, an `asyncio.Lock`-gated single-in-flight-call submission model, and a
`close()` that bound-waits for the one outstanding call and reports `drained: bool` — confirmed, not
assumed, termination — rather than treating "submitted" as "done." `close()` is idempotent and safe
to call concurrently or after cancellation of the awaiting coroutine (the underlying
`concurrent.futures.Future` isn't stoppable once it starts running, so the thread always runs the
call to completion regardless of what happens to the caller). `call()` accepts `**kwargs` in addition
to `SchedulerStore._call()`'s positional-only original, needed by the skill modules' keyword-argument
call sites.

`tests/test_db_executor.py` exercises it directly (11 tests): result/exception propagation, kwargs,
strict serialization (peak-concurrency assertion), non-blocking-event-loop behavior, and the same
close()-lifecycle shape `tests/test_scheduler_persistence_concurrency.py` already established for
`SchedulerStore` (drain, idempotency, cancellation-safety, timeout-reports-`False`).

## 2. Migrated: class-internal blocking (5 files)

For `memory_store.py`, `skill_registry.py`, `tasks.py`, `notify.py`, `calendar_draft.py` — B0's
literal pattern (an `async def` method calling `sqlite3.connect()` directly, unwrapped) — each
gained one `DedicatedDbExecutor` (constructed in `__init__`/`initialize()`, drained in
`close()`/`shutdown()`) and every identified blocking call site now routes through it:

- `memory_store.py`: `_handle_chunking()` and `reembed_memory()`'s synchronous chunk/embedding-source
  lookups (module-level `_store_chunks_sync`/`_lookup_embedding_sources_sync` functions, so the
  executor thread has no reference to the instance beyond what it's given). `close()` now drains the
  executor first and skips the WAL checkpoint if it didn't drain cleanly, mirroring
  `SchedulerStore.close()`'s "don't checkpoint against a possibly-still-writing file" reasoning.
- `skill_registry.py`: `_audit_execution()` and `_persist_skill_state()` are now `async def`, and
  `load_enabled_skills()`'s connection is routed through the executor. `get_action_audit_log()` (a
  synchronous public method with zero callers anywhere in the repository, verified by grep) was left
  untouched — nothing calls it from an event loop, so it isn't in B2's scope.
- `tasks.py`, `notify.py`, `calendar_draft.py`: each skill's existing sync DB helper methods
  (`_save_task`/`_get_task`/etc.) were **not renamed or restructured** — every async call site
  (`_action_*`, `_check_overdue_tasks`, `_process_queue`) now calls them via
  `await self._db_executor.call(self._save_task, ...)` instead of calling them directly. The one
  exception, deliberately unchanged: `get_status()` on all three skills is a synchronous method
  (called from `SkillRegistry.get_skill_info()`, itself synchronous, with no async caller found) and
  still calls its DB helpers directly — routing it through the executor would require making
  `get_status()` itself `async def`, which ripples into `SkillRegistry`'s public API for no
  event-loop benefit, since nothing async ever reaches it today.

Verified: `tests/test_stage2f_chunking.py`, `tests/test_phase2d_embeddings.py`,
`tests/test_phase2d_compute_only.py`, `tests/test_memory_store_sensitive_consent.py`,
`test_memory_functionality.py`, `test_cold_boot.py`, `tests/test_skill_registry.py`,
`tests/test_end_to_end_tasks_and_audit.py`, `tests/test_reflection_unification.py`,
`tests/test_runtime_contract_chat_seam.py`, `tests/test_skill_runtime_contract_seam.py`,
`tests/test_runtime_convergence_policy.py`, `test_kernel_alive.py` all pass unmodified.

## 3. Fixed: `persona_pack.py`'s two concrete caller-side call sites

Unlike the class-internal pattern, `PersonaPackManager` (and `NarratorEngine`, see §4) define no
`async def` methods at all — `docs/B0_BASELINE_REPORT.md` §3 already flagged this as a distinct,
caller-side mechanism, and revised the risk map's stale claim that these two files had the literal
class-internal pattern.

Two real call sites were found (one already known from B0, one newly found while implementing, per
this document's own "revalidate, don't assume" discipline):

1. `KernelDaemon._init_experience_kernel()` (now `async def`, called via `await` from
   `async def start()`) — startup persona activation.
2. `bartholomew_api_bridge_v0_1/services/api/routes/self_state.py`'s `POST /api/persona/switch`
   route (`async def switch_persona`) — a **live request-path** call B0's original investigation did
   not enumerate, found only once implementation started tracing every `switch_pack()` caller.

Both now call `await asyncio.to_thread(kernel.persona_manager.switch_pack, ...)` instead of calling
it directly. This is a different mechanism from `DedicatedDbExecutor` (a shared thread-pool call, not
a dedicated single thread) — chosen deliberately here because `switch_pack()` is a widely-used public
synchronous method with other callers that must keep working unchanged (`identity_interpreter/cli.py`,
several tests): giving `PersonaPackManager` its own dedicated executor would require *every* caller,
sync and async alike, to route through it consistently, or risk the exact thread-affinity bug this
paragraph exists to avoid (see below) — a larger restructuring than these two call sites justify.

**Correctness prerequisite:** `PersonaPackManager`'s persistent `:memory:` connection (kept alive for
the instance's lifetime, since each `:memory:` connect creates an independent empty database) was
opened with SQLite's default `check_same_thread=True`, meaning it could previously only ever be
safely touched by the exact thread that constructed it. Routing calls through `asyncio.to_thread`'s
shared pool would violate that guard the moment a call landed on a different worker thread than a
previous one. Both of `persona_pack.py`'s `sqlite3.connect()` sites now pass
`check_same_thread=False`; this is safe because SQLite's default build uses serialized threading mode
(internal mutexes make cross-thread access to one connection safe as long as it isn't genuinely
concurrent), and this object's actual call pattern never invokes it concurrently.

Verified: `tests/test_persona_pack.py`, `tests/test_narrator.py`, `tests/test_scheduler_startup_readiness.py`,
`tests/test_stage3_integration.py`, `tests/test_scenario_replay.py` (updated: `_boot()`'s direct
`kd._init_experience_kernel()` call is now `await`ed, since the method is no longer synchronous),
`tests/test_runtime_contract_chat_seam.py`, `tests/test_self_state_api.py`, `test_kernel_alive.py`.

## 4. Deferred, not resolved: `narrator.py`'s `persist_episode()`

`NarratorEngine.persist_episode()` (a synchronous DB write, same `:memory:`/`_get_connection()`
shape as `persona_pack.py`) is reached via a different, harder mechanism:
`GlobalWorkspace.publish()` synchronously and directly invokes every subscriber's callback inline
(`sub.callback(event)` — the same dispatch loop that produces this repository's pre-existing
"coroutine ... was never awaited" `RuntimeWarning`s for skills' `handle_event`, unrelated to this
change). `NarratorEngine.subscribe_to_workspace()` registers `_handle_affect_event`,
`_handle_attention_event`, `_handle_drive_event`, and `_handle_goal_event` this way; each calls
`persist_episode()` synchronously. `GlobalWorkspace.publish()` (as opposed to the also-existing
`async def publish_async()`) is called from both synchronous and asynchronous contexts throughout the
daemon's normal operation — not a one-time startup call like `persona_pack.py`'s case, and not
resolvable by wrapping one or two call sites.

Fixing this properly means giving `GlobalWorkspace`'s synchronous dispatch path an async-aware
calling convention (or a documented, deliberate split between sync-safe and event-loop-reached
subscribers) — a cross-cutting change affecting every current and future subscriber, not something
narrator-specific. That is a distinct architectural question from "route this one DB call off the
event loop," and is out of this stage's scope per `docs/PHASE_B_OVERVIEW.md` §9's "a stage may not
silently expand its own scope into a later stage's territory."

What *was* done here, safely and independently of that larger question: both of `narrator.py`'s
`sqlite3.connect()` sites now pass `check_same_thread=False`, for the same reason as `persona_pack.py`
(§3) — this alone changes no behavior today (nothing yet routes `persist_episode()` onto a different
thread), but removes a latent thread-affinity trap for whichever future change does fix the dispatch
mechanism.

**This is a known, open B2 finding, not a resolved one** — recorded here rather than left implicit,
so B2's exit condition claim below is accurate rather than overstated.

## 5. Exit condition — partial, explicitly

`docs/PHASE_B_OVERVIEW.md`'s B2 exit condition is "the known event-loop-blocking call sites resolved;
worker termination is confirmed, not merely submitted-and-assumed." Of the 7 files B1's inventory
assigned here:

- **6 of 7 resolved**: `memory_store.py`, `skill_registry.py`, `tasks.py`, `notify.py`,
  `calendar_draft.py` (fully migrated), `persona_pack.py` (both concrete call sites fixed).
- **1 of 7 open**: `narrator.py`'s `persist_episode()` remains reachable from the event loop via
  `GlobalWorkspace.publish()`'s synchronous dispatch — see §4. Not claimed as resolved.

Worker termination confirmation (`DedicatedDbExecutor.close()` returning `drained: bool`, checked by
every caller before assuming quiescence) is fully implemented and tested for all 5 migrated files'
executors, matching `SchedulerStore`'s existing standard.

## 6. Not touched

- `bartholomew/kernel/scheduler/persistence.py`/`health.py` — already fixed (2026-07-24), the
  template B2 followed; not re-touched.
- Every B8-assigned file from `docs/B1_IMPLEMENTATION.md` §3 (`fts_client.py`, `vector_store.py`,
  `hybrid_retriever.py`, `retrieval.py`, `consent_gate.py`, `working_memory.py`,
  `experience_kernel.py`, `skill_permissions.py`, `embedding_engine.py`,
  `identity_interpreter/adapters/memory_manager.py`, `parking_brake.py`'s connection handling,
  `bartholomew/cli.py`, `scripts/backfill_fts.py`, `cleanup_test_memory.py`) — unchanged, per B1's
  assignment; none of them have a confirmed event-loop-blocking mechanism, so migrating them isn't a
  B2 concern.
- `bartholomew/kernel/working_memory.py`'s `load_last_snapshot()` — also called synchronously from
  `KernelDaemon._init_experience_kernel()` (the same async caller as `persona_pack.py`'s case), which
  this implementation noticed while reading that method but did **not** fix, since
  `working_memory.py` was assigned to B8, not B2, by `docs/B1_IMPLEMENTATION.md`, and fixing it here
  would be exactly the "silently expand scope into a later stage" the overview's approval model
  prohibits. Recorded here as a finding for whoever plans B8 (or a corrected B1 inventory), not
  fixed.
