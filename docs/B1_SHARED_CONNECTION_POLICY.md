# B1 — Shared SQLite Connection Policy

> **Status:** B1 exit deliverable per `docs/PHASE_B_OVERVIEW.md` §5 and `ROADMAP.md`'s Phase B
> stage table. Implements the shared connection policy and resolves the duplicate-implementation/
> hot-path checkpoint problem `docs/B0_PERSISTENCE_BASELINE.md` §4 characterised. Does **not**
> migrate any of the remaining consumers listed in the inventory below — that is B2's and B8's
> work, each requiring its own separately approved plan.
>
> **Base facts:** drawn from `docs/B0_PERSISTENCE_BASELINE.md` (as corrected during PR #33 review,
> commit `a28c1f9`).

## 1. What changed

`bartholomew_api_bridge_v0_1/services/api/db_ctx.py` — previously an independent, hand-copied
module whose `wal_db()` unconditionally ran a blocking `PRAGMA wal_checkpoint(TRUNCATE)` on every
call, including on three read-only liveness GET routes (`B0 §4`) — now re-exports
`bartholomew.kernel.db_ctx` directly instead of maintaining its own implementation. The kernel
module's `wal_db()` (`checkpoint: str | None = None` default) becomes the one shared policy; the
API layer inherits its behaviour, its `checkpoint=`/`label=` parameters, and its
`wal_checkpoint()` mode selection, with no caller-visible signature break — `wal_db(DB_PATH,
timeout=30.0)` (`liveness.py:55,113,156`, `db.py:40`) and `wal_checkpoint_truncate(DB_PATH)`
(`app.py:70`'s `atexit` hook) both continue to resolve, now onto the kernel implementation.

**Consolidation direction chosen:** a re-export shim at the existing import path
(`bartholomew_api_bridge_v0_1.services.api.db_ctx`), not deletion-and-caller-rewrite. Discovered
during implementation: `conftest.py:104` and `tests/test_sqlite_wal_concurrent_processes.py:16`
import `db_ctx` alongside this package's own `fs_helpers` module (`from
bartholomew_api_bridge_v0_1.services.api import db_ctx, fs_helpers`) — `fs_helpers.py` is
unrelated to this consolidation (it provides `robust_unlink`/`robust_rmtree`/`wal_aux_paths`, used
independently by those same two files) and was not touched. Keeping the import path stable avoided
rewriting those two test files' unrelated `fs_helpers` imports along with the `db_ctx` change,
for the same net effect as the plan's "(a) API bridge imports the kernel's db_ctx" option.

**Tests added:** `bartholomew_api_bridge_v0_1/tests/test_sqlite_wal_api.py` gained
`test_api_db_ctx_is_kernel_db_ctx` (module-identity guard rail — the two modules cannot silently
re-diverge again) and `test_api_wal_db_no_longer_checkpoints_on_every_call` (regression test for
the specific hot-path bug, asserting no checkpoint runs unless `checkpoint=` is passed explicitly).
All pre-existing tests in this file, plus `tests/test_sqlite_wal.py`,
`tests/test_no_raw_sqlite_connect_in_api.py`, `tests/test_sqlite_wal_concurrent_processes.py`,
`tests/test_scheduler_persistence_concurrency.py`, `tests/test_fts_schema_hygiene.py`,
`tests/test_self_state_api.py`, `tests/test_stage0_alive.py`, and
`tests/test_clean_start_lifecycle.py` were run and pass unchanged.

**Not changed:** `fs_helpers.py` (untouched, still used by `conftest.py` and
`tests/test_sqlite_wal_concurrent_processes.py`); `bartholomew/cli.py`'s separate
`data/bartholomew.db` default-path mismatch (`B0 §1`) — flagged, not fixed, as a CLI-safety
concern arguably belonging to B6; any Governance, admission, or shutdown behaviour.

## 2. Caller-migration inventory (required for B1 exit)

Every remaining persistence caller identified in `docs/B0_PERSISTENCE_BASELINE.md` §2, assigned to
B2 (confirmed event-loop-blocking, needs the dedicated execution mechanism B2 introduces) or a B8
sub-stage (remaining consumer migration, no confirmed event-loop blocking found yet). None of these
are migrated by B1 itself.

### → B2 (event-loop-blocking, confirmed reachable from `async def`)

| Caller | Evidence (B0 §) |
|---|---|
| `memory_store.py`'s `_handle_chunking` (`:562`) and `reembed_memory` (`:883`) | §3 |
| `persona_pack.py` / `narrator.py`, called unwrapped from `self_state.py`'s `switch_persona`/episode routes and `daemon.py`'s `_system_tick`/`_dream_loop`/`handle_command` | §3 |
| `parking_brake.py`'s `ParkingBrake` construction, inside 4 `async def`s in `runtime_contract.py` and `skill_registry.py`'s `execute_action` | §3, §5 |
| `fts_client.py`'s `init_schema()`/`init_chunk_schema()`, called synchronously from `MemoryStore.init()` (`async def`), itself the first step of `KernelDaemon.start()` | §3 (corrected in PR #33 review) |
| `skill_registry.py`'s `load_enabled_skills()` (`async def`) calling `_get_connection()` synchronously at `:998`, the fourth step of `KernelDaemon.start()` | §3 (corrected in PR #33 review), §8 |

### → B8 (remaining consumers, no confirmed event-loop reachability yet)

| Caller | Evidence (B0 §) |
|---|---|
| `fts_client.py`'s remaining call sites beyond the startup-schema-init path above (search/index operations) | §2, §3 |
| `vector_store.py` | §2 |
| `retrieval.py`, `hybrid_retriever.py` | §2 |
| `working_memory.py` | §2 |
| `experience_kernel.py` | §2 |
| `skill_permissions.py` | §2 |
| `skill_registry.py`'s own schema-write calls (`:186,197`) beyond `load_enabled_skills()` above | §2 |
| `consent_gate.py` | §2 |
| `embedding_engine.py` (`:memory:` only) | §2 |
| `identity_interpreter/adapters/memory_manager.py` | §2 |
| `bartholomew/skills/{calendar_draft.py, notify.py, tasks.py}` | §2 |
| `scripts/backfill_fts.py` | §2 |

This split is a proposal carried over from the B1 plan discussion, not independently re-litigated
here; B2's and B8's own planners must re-verify each row against the repository at their own plan
start, per `docs/PHASE_B_OVERVIEW.md`'s standing instruction that no stage assumes a prior stage's
finding without revalidating it.

## 3. Exit condition check

- [x] Shared connection policy implemented: the API layer now uses `bartholomew.kernel.db_ctx`
  directly, eliminating the second implementation.
- [x] Duplicate/hot-path checkpoint problem resolved: the API layer's `wal_db()` no longer
  checkpoints unconditionally on every call (verified by
  `test_api_wal_db_no_longer_checkpoints_on_every_call`).
- [x] Every remaining consumer migration inventoried and assigned to B2 or B8 (§2 above).
- [x] Focused tests for the shared policy itself added and passing.
- Not required for exit, and not done: migrating any B2/B8-assigned caller.
