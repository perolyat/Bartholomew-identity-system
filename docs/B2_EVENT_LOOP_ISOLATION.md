# B2 — Event-Loop Isolation and Database Execution

> **Status:** B2 exit deliverable per `docs/PHASE_B_OVERVIEW.md` §5 and `ROADMAP.md`'s Phase B
> stage table. Resolves the 5 event-loop-blocking caller groups
> `docs/B1_SHARED_CONNECTION_POLICY.md` §2 assigned to this stage. Does **not** migrate any of the
> 12 B8-assigned callers — that remains B8's own, separately approved work.
>
> **Base facts:** drawn from `docs/B0_PERSISTENCE_BASELINE.md` and
> `docs/B1_SHARED_CONNECTION_POLICY.md` §2's "→ B2" table.

## 1. What changed

**New primitive:** `bartholomew/kernel/blocking_executor.py` — `SingleWorkerExecutor`, a
storage-agnostic generalization of `scheduler/store.py`'s pre-existing `SchedulerStore` pattern
(one dedicated worker thread, a submission gate limiting it to one in-flight operation, and a
`close()` that bound-waits for the outstanding operation before reporting itself drained). It knows
nothing about SQLite or persistence — it offloads an arbitrary blocking callable. Also provides
`run_off_loop(fn, *args, executor=None, **kwargs)`: uses a caller-supplied `SingleWorkerExecutor`
when available, falling back to a one-off `asyncio.to_thread()` when a call site has no owning
daemon instance to provide one (still off the event loop, still confirmed-complete by the time it
returns, just without the dedicated-thread/backpressure lifecycle a shared executor gives repeated
callers).

**`SchedulerStore` refactored onto the new primitive** rather than left as a second independent
implementation of the same pattern (`scheduler/store.py`) — it is now a thin, `persistence.py`-
specific facade over one `SingleWorkerExecutor` instance. `SchedulerStoreClosedError` now subclasses
the new `ExecutorClosedError`. A `_closed` property proxies the delegate's flag so existing
introspection (`store._closed`, used directly by `tests/test_scheduler_startup_readiness.py`)
continues to work unchanged.

**`KernelDaemon` owns one shared `blocking_executor`** (`daemon.py`), constructed alongside
`scheduler_store` (before `MemoryStore`, so it can be injected into it), closed in `stop()`
*before* the final `MemoryStore` checkpoint — mirroring `scheduler_store`'s own ordering, and
folded into the same `checkpoint=` drain-confirmation gate: `self.mem.close(checkpoint=
scheduler_drained and blocking_drained)`. Also closed (via `asyncio.shield`) in `start()`'s
failure-unwind path, alongside `scheduler_store`.

## 2. Callers migrated

| Caller | Site(s) | How |
|---|---|---|
| `FTSClient.init_schema()`/`init_chunk_schema()` | `memory_store.py`'s `init()` (`KernelDaemon.start()`'s first step) | Wrapped in `run_off_loop(..., executor=self._blocking_executor)`; `MemoryStore` now accepts an optional `blocking_executor` constructor arg |
| `SkillRegistry.load_enabled_skills()`'s DB read | `skill_registry.py` (`KernelDaemon.start()`'s fourth step) | Read extracted into a closure, submitted via `run_off_loop`; `SkillRegistry` now accepts an optional `blocking_executor` constructor arg, threaded from `KernelDaemon.__init__` |
| `ParkingBrake` construction | `runtime_contract.py`'s 4 functions (chat, drive, sight, voice) and `skill_registry.py`'s `_is_blocked_by_brake` (now `async`) | New shared helper `parking_brake.construct_parking_brake_off_loop(storage, executor=...)` — construction + the in-memory-only `is_blocked()` check submitted as one unit. Chat/skill-execution pass `daemon.blocking_executor`; drive passes `getattr(ctx, "blocking_executor", None)` (preserving `run_drive_through_runtime_contract`'s documented minimal-duck-typed-`ctx` test contract); sight/voice have no owning daemon and fall back to `run_off_loop`'s `asyncio.to_thread()` path |
| `PersonaPackManager.switch_pack()`/`auto_activate_if_needed()` | `self_state.py`'s `switch_persona` route; `daemon.py`'s `_system_tick` | Wrapped in `run_off_loop(..., executor=kernel.blocking_executor)` at each call site; `PersonaPackManager`/`NarratorEngine` internals unchanged |
| `NarratorEngine.get_recent_episodes`/`search_episodes`/`get_episode`/`get_episodes_by_type`/`get_episodes_by_tag` | `self_state.py`'s 5 episode routes | Same pattern |
| `NarratorEngine.generate_daily_reflection_narrative`/`generate_weekly_reflection_narrative` | `daemon.py`'s `_run_daily_reflection`/`_run_weekly_reflection` | Same pattern |
| `MemoryStore._handle_chunking`'s chunk-storage block | `memory_store.py:562` (was) | Extracted into a closure, submitted via `run_off_loop` |
| `MemoryStore.reembed_memory`'s existing-sources lookup | `memory_store.py:883` (was) | Same pattern |

Every governance-critical fail-closed behavior is preserved exactly: `ParkingBrake` construction's
own exceptions still propagate through `run_off_loop`/`submit()` unchanged, so
`run_chat_through_runtime_contract`'s `except Exception: ... fail closed` and `skill_registry.py`'s
`_is_blocked_by_brake`'s `except Exception: return True` (fail closed) still catch construction
failures exactly as before; `run_drive_through_runtime_contract`'s `raise RuntimeError(...)` on
block, and its `except ImportError: pass` tolerance, are unchanged in shape, just awaited.

## 3. Not migrated (out of scope, per B1's inventory)

The 12 B8-assigned callers: `vector_store.py`, `retrieval.py`, `hybrid_retriever.py`,
`working_memory.py`, `experience_kernel.py`, `skill_permissions.py`, `skill_registry.py`'s own
schema-write calls beyond `load_enabled_skills()`, `consent_gate.py`, `embedding_engine.py`,
`identity_interpreter/adapters/memory_manager.py`, `bartholomew/skills/{calendar_draft.py,
notify.py, tasks.py}`, `scripts/backfill_fts.py`. Also not touched:
`identity_interpreter/orchestrator/orchestrator.py:133`'s `ParkingBrake` construction (the
no-kernel `/api/chat` fallback path) — not part of B1's B2 assignment; `bartholomew/cli.py`'s three
`ParkingBrake` construction sites (CLI-only, B6's territory per `docs/B0_PERSISTENCE_BASELINE.md`
§5); `fts_client.py`'s non-startup call sites (search/index operations).

## 4. Tests

New: `tests/test_blocking_executor.py` (8 tests) exercises `SingleWorkerExecutor`/`run_off_loop`
directly with plain, non-persistence callables — proving the primitive is genuinely
storage-agnostic, not just in name — covering sequential execution, exception propagation, a custom
`closed_exception_cls`, idempotent `close()`, and (the risk map's specific "termination confirmed,
not merely submitted" requirement) that `close()` bound-waits for outstanding work and reports
`False` honestly on timeout rather than silently claiming success.

`tests/test_scheduler_persistence_concurrency.py` and `tests/test_scheduler_startup_readiness.py`
(18 tests total) updated only where they reached into `SchedulerStore`'s now-delegated internals
(`store._executor` → `store._worker._executor`; `store._closed` unaffected via the new proxy
property) — all pass unchanged otherwise.

Ran the full non-integration/non-slow suite (`pytest tests/`, matching `pyproject.toml`'s default
marker deselection) after every change: all pass except
`tests/test_sqlite_wal_concurrent_processes.py::test_wal_cleanup_concurrent_processes`, which is
`RISKS.md`'s and `ROADMAP.md`'s own documented pre-existing intermittent full-suite-load flake
(re-confirmed here: failed once under full-suite load, passed 3/3 in isolation immediately after —
the exact behavior those docs already recorded) — deliberately not retried, quarantined, or
re-marked, per that existing decision. Also ran the full governance/runtime-contract test set
directly (`test_parking_brake_*`, `test_*runtime_contract*`, `tests/unit/safety/test_parking_brake
.py`, `tests/test_scheduler_drive_convergence.py`) — all pass, including
`test_chat_returns_503_when_parking_brake_engaged`, which specifically exercises the fail-closed
path this stage's changes touch most directly.

## 5. Exit condition check

- [x] Known event-loop-blocking call sites (B1's 5 assigned groups) resolved.
- [x] Worker termination confirmed, not merely submitted-and-assumed: `SingleWorkerExecutor.close()`
  bound-waits and reports drain status honestly (§4); `KernelDaemon.stop()` folds
  `blocking_executor`'s drain status into the same shutdown-checkpoint gate `scheduler_store`
  already used.
- Not required for exit, and not done: migrating any B8-assigned caller.
