# B8 Implementation — Remaining Persistence Consumers

> Phase B, stage B8. Per `docs/PHASE_B_OVERVIEW.md`'s B8 scope: migrate remaining persistence users
> onto the shared `bartholomew/kernel/db_ctx.py` policy (B1). Per the overview, this stage does not
> attempt cross-module schema consolidation beyond what B3 already scoped — every change here is
> connection-policy only (which pragmas, which timeout, which helper function), never a schema
> change.

## 1. Scope corrected mid-stage: B1's own inventory under-scoped this stage

`docs/B1_IMPLEMENTATION.md`'s caller inventory assigned `memory_store.py`, `skill_registry.py`,
`tasks.py`/`notify.py`/`calendar_draft.py`, `persona_pack.py`, and `narrator.py` entirely to **B2**
(event-loop-blocking) and never carried their connection-policy migration forward to B8 — an
oversight in that inventory, not a deliberate exclusion. The overview's own B8 scope explicitly
names "MemoryStore concurrency" and "remaining direct SQLite callers," which these files clearly
are. Mid-stage, after migrating the files B1 *did* assign to B8, a re-grep confirmed these 7 files
still called raw `sqlite3.connect()` with inconsistent (often absent) pragmas — the same gap B8
exists to close. They're included in this stage's migration, corrected here rather than left for a
future stage to rediscover.

## 2. Files migrated (21 total)

All changes follow the same mechanical shape: replace `sqlite3.connect(path)` with
`db_ctx.connect(path)`, and add `db_ctx.set_wal_pragmas(conn)` immediately after (or confirm it was
already being called manually and consolidate onto the shared helper). No control-flow, schema, or
behavioral change beyond pragma consistency and (where applicable) `check_same_thread=False`, which
`db_ctx.connect()` already defaults to.

**Already applying `set_wal_pragmas` manually — consolidated onto the shared `connect()` wrapper**
(no pragma-behavior change, since `set_wal_pragmas()` already overrides whatever `connect()`'s own
`timeout=` established): `bartholomew/kernel/fts_client.py` (13 sites), `vector_store.py` (6 sites),
`hybrid_retriever.py` (1 site).

**No pragmas applied before — pragma gap closed**: `retrieval.py` (3 sites, including an FTS5-
availability probe whose `set_wal_pragmas` call is a safe no-op for WAL specifically but still
applies `busy_timeout`/`foreign_keys`/`synchronous` consistently), `consent_gate.py` (2),
`working_memory.py` (4), `experience_kernel.py` (4, including a `:memory:`-capable path — WAL mode
itself is a no-op for `:memory:`, harmless), `skill_permissions.py` (2), `memory_store.py`'s two sync
call bodies (`_store_chunks_sync`, `_lookup_embedding_sources_sync` — the aiosqlite paths in the same
file are explicitly *not* touched here; see §4), `skill_registry.py` (5 sites: 3 module-level sync
bodies plus `_init_database`/`_get_connection`), `tasks.py`/`notify.py`/`calendar_draft.py` (2 sites
each, identical `_init_database`/`_get_connection` shape), `persona_pack.py` and `narrator.py` (3
sites each, including their `:memory:`-backed persistent-connection path — `check_same_thread=False`,
already applied in B2, is preserved since it's `db_ctx.connect()`'s own default).

**`identity_interpreter/adapters/memory_manager.py`** (7 sites): this module is documented in its own
file header as unreachable in production today (`ContextBuilder` never constructs it with a live
`identity_config` from the API bridge's actual `Orchestrator()` construction) — migrated for
consistency anyway, since B1's inventory assigned it and a future revival should find the shared
policy already in place, not a second thing to fix. No test exercises this class directly (confirmed
by grep — the only test-file hits on "MemoryManager" were `WorkingMemoryManager`, a different class).

**`bartholomew/orchestrator/safety/parking_brake.py`**'s legacy `BrakeStorage` (2 sites): also now
unreachable from any live code path after B6's schema swap (`check_scope_blocked()` and the CLI both
moved to `GovernanceBrakeStore`) — migrated anyway since its own unit tests
(`tests/unit/safety/test_parking_brake.py`, `tests/test_parking_brake_scoped_blocks.py`) still
exercise it directly, and it remains undeleted per this repository's "deprecate before deleting
duplicates" convention.

**Standalone CLI/scripts** (no event loop, no urgency, migrated for consistency):
`bartholomew/cli.py`'s `embeddings stats`/`embeddings rebuild-vss` commands (2 sites — the `brake`
commands were already migrated in B6), `scripts/backfill_fts.py` (2 concurrent connections —
read/write — both now pragma-consistent, closing a real gap where two connections operating on the
same file concurrently previously had no `busy_timeout` set at all), `cleanup_test_memory.py` (1
site).

**Deliberately not touched**: `bartholomew/kernel/embedding_engine.py`'s VSS-availability probe
(`sqlite3.connect(":memory:")`, a pure capability check with no db_path, no concurrency, and no
shared-file access to be consistent about) — B1's own inventory already called this "lowest
priority" for exactly this reason, and re-confirming it here rather than silently migrating
something that gains nothing from the shared policy.

## 3. `db_ctx.py` itself widened to fix a real (if non-runtime) regression

Migrating `memory_manager.py` (whose `self.db_path` is a `Path`, not a `str`) surfaced a real mypy
regression: `db_ctx.connect()` and its three sibling functions (`wal_checkpoint`,
`wal_checkpoint_truncate`, `close_all_and_checkpoint`) were typed `db_path_or_uri: str`, narrower than
what `sqlite3.connect()` itself actually accepts (`str | os.PathLike[str]`). Widened all four to match
— a strict improvement for any future caller holding a `Path`, not a workaround specific to this one
call site. Confirmed via `git stash` comparison that this was the only new mypy finding this stage's
changes introduced; the pre-existing 64 (`yaml`/`keyring` stub-less imports, other unrelated
`Optional`/`Argument`-type findings) are unchanged.

## 4. Deliberately not touched: `MemoryStore`'s `aiosqlite` paths

`memory_store.py`'s `aiosqlite`-based connections (the majority of its I/O) still apply no explicit
pragma-setting. `db_ctx.set_wal_pragmas()` is written for a synchronous `sqlite3.Connection` — calling
it on an `aiosqlite.Connection` would silently do nothing useful (its `.execute()` returns an
unawaited coroutine/context manager, not a synchronous side effect). Building an async-aware
equivalent is a real, separate design decision (a new helper, or a different calling convention for
every `aiosqlite` call site in this codebase) — not a two-line mechanical substitution like everything
else in this stage, and not something to invent unannounced mid-stage. Recorded here as an open
question for whichever future stage takes it on, rather than silently left unmentioned.

## 5. A genuine pre-existing regression found and fixed along the way

While verifying this stage's changes, `tests/test_scheduler_startup_readiness.py::test_primary_
schema_error_survives_a_failing_cleanup` and `::test_cleanup_failure_does_not_mask_cancellation`
failed — on `HEAD` (commit `2340b58`, before any B8 change), not because of anything in this stage.
B5's `_unwind_after_failed_start()` refactor (`docs/B5_IMPLEMENTATION.md`) had changed the scheduler-
store cleanup-failure log message from `"Scheduler store cleanup failed during aborted startup"` to
`"scheduler_store cleanup failed during aborted startup"` — both of these pre-existing tests (from
S5.0, predating Phase B) assert on the original wording verbatim. Fixed by restoring the original
message text for that one log line (the two other, newly-introduced resources in that method —
`skill_registry`, `mem` — have no pre-existing test depending on their exact wording, left as
B5 wrote them). This is a real regression this stage's own verification discipline caught, not
something B8 introduced — recorded honestly rather than folded silently into this stage's diff without
explanation.

## 6. Verification

Ran the full targeted test suites for every touched area across three checkpoints during this stage
(FTS/retrieval/hybrid/consent, working-memory/experience-kernel/skill-registry/scenario-replay,
memory-store/skills/persona/narrator), plus import/syntax checks for the standalone scripts and the
otherwise-untested `memory_manager.py`/legacy `parking_brake.py` paths. `black`/`ruff` clean across
all 22 changed files (one pre-existing, unrelated `PLW0108` finding in `working_memory.py`, confirmed
via `git stash` to predate this stage). `mypy` introduces zero new findings (64 before, 64 after,
confirmed via `git stash` comparison) once `db_ctx.py`'s path typing was widened. Full existing test
suite passes, including the two-test regression found and fixed in §5.
