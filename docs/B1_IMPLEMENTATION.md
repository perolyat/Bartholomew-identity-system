# B1 Implementation — Shared SQLite Connection Policy

> Phase B, stage B1. Builds on `docs/B0_BASELINE_REPORT.md`. Per `docs/PHASE_B_OVERVIEW.md`'s B1
> scope: a shared connection helper already existed (`bartholomew/kernel/db_ctx.py`) but was
> duplicated by a second, independently-drifted copy, and one of the two copies checkpointed on
> every call on a hot path. B1's job was to resolve those two problems and inventory every
> remaining persistence caller for B2/B8 — not to migrate all 23 callers onto the policy itself.

## 1. What changed

**Consolidated the duplicate connection-policy implementation.**
`bartholomew_api_bridge_v0_1/services/api/db_ctx.py` previously held its own independent
reimplementation of `connect()`, `set_wal_pragmas()`, `wal_checkpoint_truncate()`, `close_quietly()`,
`close_all_and_checkpoint()`, and `wal_db()` — a near-duplicate of
`bartholomew/kernel/db_ctx.py` that had already drifted in two ways (§2 below). It is now a thin
re-export of the kernel module (`bartholomew_api_bridge_v0_1/services/api/db_ctx.py`), keeping the
same importable path and public names so its three callers (`db.py`, `routes/liveness.py`,
`app.py`) needed no changes. Verified by direct identity check
(`api_db_ctx.wal_db is kernel_db_ctx.wal_db`, etc. — see the test added in §3) that there is now
exactly one implementation, not two that happen to agree.

**Fixed the unconditional hot-path checkpoint.** The API-bridge copy's `wal_db()` had no
`checkpoint=` parameter — its `finally` block unconditionally ran a blocking
`PRAGMA wal_checkpoint(TRUNCATE)` on every exit. Its three callers
(`routes/liveness.py:55,113,156`, backing `GET /api/liveness/self`, `/ticks`, `/nudges`,
`/reflections`, plus `db.py:40`) call `wal_db(DB_PATH, timeout=30.0)` without a `checkpoint=`
argument, so after the consolidation they now get the kernel module's default (`checkpoint=None`,
i.e. no explicit checkpoint — SQLite's own automatic WAL checkpoint is the standard mechanism for
routine reads, per the existing rationale already accepted for the kernel side, `DECISIONS.md`
2026-07-24). This removes a blocking exclusive-lock checkpoint from what B0 confirmed is a live
request-handling path in the same OS process as the daemon's event loop. `app.py:70`'s `atexit`
shutdown hook still calls `wal_checkpoint_truncate()` directly and is unaffected — deliberate
TRUNCATE-on-shutdown behavior is unchanged.

**Not touched:** `bartholomew_api_bridge_v0_1/services/api/fs_helpers.py` (a distinct utility module
— `wait_for_removal`, `robust_unlink`, `robust_rmtree`, `wal_aux_paths` — none of which duplicate
anything in the kernel module; only its `windows_release_handles` overlapped, and that overlap is now
moot since `db_ctx.py` no longer imports it, having moved to the kernel module's own internal
`_windows_release_handles`). `fs_helpers.py` is left in place, unmodified.

## 2. Verification

- `bartholomew_api_bridge_v0_1/tests/test_sqlite_wal_api.py` (pragma parity + context-manager
  behavior), `tests/test_no_raw_sqlite_connect_in_api.py`, `tests/test_sqlite_wal.py`,
  `test_sqlite_wal_cleanup.py`, `test_liveness_api.py`, `tests/test_liveness_self.py`,
  `tests/test_clean_start_lifecycle.py` — all pass unmodified against the consolidated module.
- A new regression test (`bartholomew_api_bridge_v0_1/tests/test_sqlite_wal_api.py`, see diff) pins
  the fix: `api_db_ctx.wal_db()` called without `checkpoint=` must not run a `TRUNCATE` checkpoint on
  exit, and the module's public functions must be object-identical to the kernel module's (not just
  behaviorally similar), so a future accidental reintroduction of a second implementation fails loudly.
- Full repository test suite run (excluding the pre-existing, environment-only packaging-contract
  console-script check, which fails for an unrelated reason — the sandbox's editable install wasn't
  registering console-script entry points until fixed with `pip install -e .`, nothing to do with
  this change).

## 3. Caller inventory — assignment to B2 or B8

Per `docs/B0_BASELINE_REPORT.md` §2/§3, 23 non-test files call `sqlite3.connect()`/`aiosqlite.connect()`
directly (plus `cleanup_test_memory.py`, found on a second verification pass — 24 total, excluding
the two `db_ctx.py` policy modules themselves, which this stage already resolved). Every one is
assigned below, per B1's exit condition, to either **B2** (event-loop-blocking — must move off the
event loop) or **B8** (remaining consumer — migrate onto the shared `db_ctx` policy, not
event-loop-urgent). This inventory does not migrate any of them; it only assigns.

### B2 — event-loop-blocking (synchronous DB work reachable from the running event loop)

| File | Mechanism | Evidence |
|---|---|---|
| `bartholomew/kernel/memory_store.py` | Class-internal: `async def` methods (`_handle_chunking`, `reembed_memory`) call `sqlite3.connect()` synchronously, unwrapped | B0 §3, lines 562, 883 |
| `bartholomew/kernel/skill_registry.py` | Class-internal: `async def` methods (`load_enabled_skills`, `load_skill`, `_finish`) call sync connection-opening helpers, unwrapped | B0 §3, lines 996, 946, 801 |
| `bartholomew/skills/tasks.py` | Class-internal: `async def initialize()`/`_action_*` call sync DB helpers, unwrapped | B0 §3 |
| `bartholomew/skills/notify.py` | Same pattern as `tasks.py` | B0 §3 |
| `bartholomew/skills/calendar_draft.py` | Same pattern as `tasks.py` | B0 §3 |
| `bartholomew/kernel/persona_pack.py` | Caller-side: fully-synchronous class, but its sync DB methods are invoked directly (no `to_thread`) from `KernelDaemon.start()` (`async def`) via `_init_experience_kernel()` | B0 §3, `daemon.py:189,239,273` |
| `bartholomew/kernel/narrator.py` | Caller-side: same mechanism — `daemon.py:203` calls `subscribe_to_workspace()` (sync) directly from `async def start()` | B0 §3 |

`bartholomew/kernel/scheduler/persistence.py` and `scheduler/health.py` are **not** in this list —
`DECISIONS.md`'s 2026-07-24 entry already moved scheduler persistence onto `SchedulerStore`'s
dedicated worker thread; B0 §4 confirms this still holds. They are the existing template B2 should
follow for the files above, not a remaining problem themselves.

### B8 — remaining consumers (no confirmed event-loop-blocking mechanism; migrate onto the shared `db_ctx` policy as B8 sub-stages)

| File | Notes |
|---|---|
| `bartholomew/kernel/fts_client.py` | Already applies `set_wal_pragmas` at all 13 sites — migration is mechanical (switch to `db_ctx.connect()`/`wal_db()` directly) |
| `bartholomew/kernel/vector_store.py` | Already applies `set_wal_pragmas` at all 6 sites — same mechanical migration |
| `bartholomew/kernel/hybrid_retriever.py` | Already applies `set_wal_pragmas` — same mechanical migration |
| `bartholomew/kernel/retrieval.py` | No pragmas applied at any of its 3 sites — needs both the policy migration and its pragma gap closed |
| `bartholomew/kernel/consent_gate.py` | No pragmas applied — needs policy migration |
| `bartholomew/kernel/working_memory.py` | No pragmas applied — needs policy migration |
| `bartholomew/kernel/experience_kernel.py` | No pragmas applied — needs policy migration |
| `bartholomew/kernel/skill_permissions.py` | No pragmas applied — needs policy migration |
| `bartholomew/kernel/embedding_engine.py` | `:memory:` VSS-availability probe only, not the app DB — lowest priority |
| `identity_interpreter/adapters/memory_manager.py` | Only its `_init_database()` site sets pragmas; 6 further per-call sites don't re-apply them — needs policy migration; also has its own divergent `memory.db` path (B0 §1), a separate concern from connection policy |
| `bartholomew/orchestrator/safety/parking_brake.py` | No pragmas applied. **Note:** this file's *connection-policy* migration (pragmas/timeout/close) is B8's concern; its *schema and transition semantics* are explicitly B3's concern (`docs/PHASE_B_OVERVIEW.md` B3) — B8 should migrate the connection handling without touching the schema B3 will define |
| `bartholomew/cli.py` | Standalone CLI process, not part of any event loop — policy migration only, no urgency |
| `scripts/backfill_fts.py` | Standalone script; opens two concurrent raw connections with no pragmas — policy migration |
| `cleanup_test_memory.py` | Standalone maintenance script — policy migration |

## 4. Deferred to later stages

- Actually migrating any B2- or B8-assigned file onto the shared policy or off the event loop —
  that's B2's and B8's own implementation work, not B1's.
- `bartholomew/orchestrator/safety/parking_brake.py`'s schema and transition semantics — B3.
- Reconciling the four divergent DB path/filename schemes (`docs/B0_BASELINE_REPORT.md` §1) — not
  assigned to any stage yet; flagged again here since it will block a clean B8 migration for
  `identity_interpreter/adapters/memory_manager.py` and `bartholomew/cli.py` specifically, which
  default to different files than everything else.
