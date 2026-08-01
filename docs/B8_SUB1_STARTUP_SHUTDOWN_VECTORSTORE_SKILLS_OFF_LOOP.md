# B8 (sub-stage 1) — Remaining event-loop-blocking persistence consumers

> **Status:** first split sub-stage of B8 — "Remaining persistence consumers" — per
> `docs/PHASE_B_OVERVIEW.md` §5, which explicitly calls for B8 "to be split further as appropriate
> rather than forming one large unit." This sub-stage closes every real, confirmed-by-direct-read
> event-loop-blocking gap found while re-auditing the repository; it does not attempt B8's other
> candidate items (MemoryStore concurrency stress-testing, cross-module schema consolidation, or
> anything not backed by a concrete finding below).
>
> **Base facts:** drawn from a fresh, targeted re-read of `daemon.py`, `memory_store.py`,
> `vector_store.py`, `experience_kernel.py`, `working_memory.py`, `persona_pack.py`,
> `narrator.py`, `skill_registry.py`, and the API routers, not assumed from B0's original
> inventory or the archived design.

## 1. Grounded findings

1. **`ExperienceKernel`, `WorkingMemoryManager`, `PersonaPackManager`, and `VectorStore` have zero
   `async def` methods between them**, confirmed by direct read. None of their blocking-ness was
   ever intrinsic to the classes themselves — it depended entirely on whether *callers* routed
   them off the event loop, exactly as B2 established for the scheduler and FTS/skill paths.
2. **`daemon.py`'s own `start()`/`stop()` called four of these methods directly**, undoing that
   discipline for its own startup/shutdown sequence: `_init_experience_kernel()` (called
   synchronously, no `await`, from within `start()`'s `async` body) did
   `self.experience.load_last_snapshot()` and `self.working_memory.load_last_snapshot(db_path)`
   (both real `sqlite3.connect()` reads) and, when no persona was active,
   `self.persona_manager.switch_pack(...)` (a real `sqlite3` write via `_log_switch()`).
   `stop()` directly called `self.experience.persist_snapshot()` and
   `self.working_memory.persist_snapshot(self.mem.db_path)` (both real writes). Each fires once
   per daemon lifecycle (startup or shutdown), not per-request — real, but lower severity than a
   per-request hot path.
3. **`memory_store.py`'s embedding pipeline had the same gap, on a genuine per-write hot path**
   (when `BARTHO_EMBED_ENABLED=1`): `_get_embedding_components()` (module-level, constructs
   `VectorStore` — real schema-init and VSS-availability I/O on first call per `db_path`) was
   called directly from three `async def` methods (`_handle_embeddings`, `persist_embeddings_for`,
   `reembed_memory`), and `vec_store.upsert()`/`vec_store.delete_for_memory()` (each a fresh
   `sqlite3.connect()`) were called directly in loops inside the first two and once in the third.
   `reembed_memory()`'s own comment ("Runs off the event loop since Phase B stage B2") shows the
   *sibling* call in the same method (a `_existing_sources()` lookup) was already correctly
   migrated — this was a partial, inconsistent application of the B2 pattern within one method, not
   an untouched one.
4. **`skill_registry.py`'s `_persist_skill_state()` and `_audit_execution()` had the identical
   gap, also on a real per-action hot path**: `load_skill()` called `_persist_skill_state()`
   directly (once per skill load — startup, plus any runtime enable/disable); `_finish()` — the
   sole completion path for every `execute_action()` attempt (success, failure, permission denial,
   or Parking Brake block alike) — called `_audit_execution()` directly. `load_enabled_skills()`
   in the same file already correctly wraps its own equivalent read in `run_off_loop()`, the same
   "partial migration within one file" shape as finding 3.
5. **Confirmed non-findings, worth stating rather than silently skipping** (matching this
   project's own convention, e.g. B4's "confirmed still unreachable" sight/voice note): the API
   layer already correctly wraps every `narrator.py`/`persona_pack.py` call it makes in
   `run_off_loop()` (`self_state.py`'s episode/persona routes) — the gaps above were entirely
   internal, non-HTTP call sites B0's original route-focused audit didn't cover.
   `routes/liveness.py`'s and `routes/metrics.py`'s handlers are plain `def` (not `async def`) —
   FastAPI/Starlette already dispatches these to its own threadpool automatically, so the risk
   map's "liveness and metrics reads currently on a blocking or inconsistent read lane" candidate
   does not apply to the current repository. `hybrid_retriever.py`'s `VectorStore`/FTS `.search()`
   calls are not reachable from any live async path today (confirmed: nothing outside
   `retrieval.py`/`hybrid_retriever.py`/`types.py` itself calls `get_retriever()` or `.retrieve()`
   — it's CLI/script-only, per `scripts/hybrid_search.py`), so not a live blocking concern; deferred
   to a future B8 sub-stage if/when that pipeline is ever wired into a live request path.
6. **Explicitly not fixed here, and named rather than silently left**: `SkillRegistry.__init__()`'s
   own `_init_database()` runs synchronously during `KernelDaemon.__init__()`, which itself runs on
   the event loop (`app.py`'s `startup()` constructs `KernelDaemon(...)` before awaiting
   `start()`). This is the same class of gap B4 already addressed for `GovernanceStore` by moving
   its construction into `start()`'s off-loop-executed region — but doing the same for
   `SkillRegistry` would mean restructuring *when and where* it's constructed relative to the rest
   of `__init__`'s wiring (narrator/policy/planner all take a constructed `SkillRegistry` reference
   downstream), a construction-site reorganization closer to B4's own scope than this sub-stage's
   "wrap the calls that are already isolated." Left as an honest, named limitation, not silently
   glossed over.

## 2. What was built

All fixes follow the identical, already-established B2 pattern: wrap the blocking call (or a small
closure around several blocking calls that logically belong together) in
`bartholomew.kernel.blocking_executor.run_off_loop()`, passing the caller's own
`blocking_executor`/`_blocking_executor` — no new mechanism introduced.

- **`daemon.py`**: `_init_experience_kernel()` is now `async def`, awaited from `start()`;
  `load_last_snapshot()` (both), `switch_pack()`, `persist_snapshot()` (both, in `stop()`) are each
  routed through `run_off_loop()`. `restore_from_snapshot()`/`get_active_pack_id()`/`list_packs()`
  are confirmed pure in-memory operations and deliberately left as direct calls.
- **`memory_store.py`**: `_get_embedding_components()` is routed through `run_off_loop()` at all
  three call sites; the `vec_store.upsert()` loops in `_handle_embeddings()`/
  `persist_embeddings_for()` are each wrapped in a small closure and submitted as one
  `run_off_loop()` call (one worker-thread round trip per memory, not one per embedded source);
  `vec_store.delete_for_memory()` in `reembed_memory()` likewise.
- **`skill_registry.py`**: `load_skill()`'s two `_persist_skill_state()` calls (success and
  failure paths) and `_finish()`'s `_audit_execution()` call are each routed through
  `run_off_loop()`.

## 3. Tests

`tests/test_b8_event_loop_isolation.py` (6 tests) proves the calls actually run off the event
loop now — not just that behavior is preserved (already covered by re-running the full existing
suite below) — by wiring a real `SingleWorkerExecutor`/daemon-owned executor, spying on each
formerly-direct call to record `threading.get_ident()`, and asserting every recorded thread differs
from the test's own (event-loop) thread: `switch_pack()` at startup (forcing the "no persona
active" precondition rather than relying on real config's incidental default-pack auto-activation),
`persist_snapshot()` for both experience and working-memory at shutdown, `_get_embedding_components()`
during `persist_embeddings_for()`, `delete_for_memory()` during `reembed_memory()`, and
`_persist_skill_state()` during `load_skill()`.

Two pre-existing call sites needed a one-line `await` added since `_init_experience_kernel()`
became `async def`: `daemon.py`'s own call in `start()`, and `tests/test_scenario_replay.py`'s
`_boot()` helper (which deliberately reaches the same ready state `start()` does, minus background
tasks). `tests/test_scheduler_startup_readiness.py`'s existing monkeypatch of
`_init_experience_kernel` with a plain synchronous raising function needed no change — Python
evaluates the call expression (and therefore the injected exception) before the `await` keyword is
ever reached.

Full non-integration/non-slow suite re-run clean after this change.

## 4. Exit condition check (this sub-stage only)

- [x] Every real, confirmed-by-direct-read event-loop-blocking gap in
  `ExperienceKernel`/`WorkingMemoryManager`/`PersonaPackManager` (daemon startup/shutdown),
  `VectorStore` (the embedding pipeline), and `skill_registry.py`'s audit/state persistence is
  closed, each proven off-loop by a dedicated test.
- [x] No cross-module schema consolidation attempted — `MemoryStore`'s, `VectorStore`'s, and
  `SkillRegistry`'s own DDL remain under their existing modules' ownership, per B8's own
  non-negotiable scope boundary.
- [x] Confirmed non-findings documented rather than silently assumed (§1 finding 5).

Not required for this sub-stage's exit, and not done: `MemoryStore` concurrency/stress testing
under the executor model (a distinct B8 candidate, its own sub-stage); `SkillRegistry`'s
constructor-time blocking I/O (§1 finding 6, named as a real but differently-scoped gap); the
`hybrid_retriever.py` search pipeline (confirmed unreachable from any live path today).
