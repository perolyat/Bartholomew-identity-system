# B0 Baseline Report — Verified Persistence Baseline

> **Status:** Complete. Produced per `docs/B0_EXECUTION_PLAN.md`. This is a diagnostic report only —
> **no code, schema, or config was changed to produce it.** Every claim below is cited to `file:line`
> and was independently re-verified against the repository at commit `72b2ed5` (plus this session's
> own uncommitted `docs/B0_EXECUTION_PLAN.md`), not assumed from `docs/PHASE_B_RISK_MAP.md` or the
> archived research document.
>
> **Date:** 2026-07-31.

## 1. DB path(s) — not a single resolved path

There are **at least four distinct default DB filenames**, reached through **two different env-var
names**, not one resolved path:

| Resolver | file:line | Default | Env var |
|---|---|---|---|
| `config/kernel.yaml` | `config/kernel.yaml:5` | `data/barth.db` | n/a (static) |
| `KernelDaemon`'s `_default_db_path()` | `bartholomew/kernel/daemon.py:672-698` | `<project_root>/data/barth.db` | `BARTH_DB_PATH` (`daemon.py:681`) |
| API bridge `DB_PATH` | `bartholomew_api_bridge_v0_1/services/api/db.py:9-19` | `<project_root>/data/barth.db` (own, independently-implemented project-root walk) | `BARTH_DB_PATH` (`db.py:19`) — same name as above, separate implementation |
| `retrieval.py`'s `_resolve_db_path()` | `bartholomew/kernel/retrieval.py:394-425` | `data/barth.db` | **`BARTHO_DB_PATH`** (`retrieval.py:406`) — a different env var name (extra "O") |
| `bartholomew/cli.py` Typer options | `cli.py:28,133,250,271,285` | **`data/bartholomew.db`** — a different filename | none |
| `identity_interpreter/adapters/memory_manager.py` | `memory_manager.py:165-169` | **`./data/memory.db`** — a third filename, under a `data_dir` param | none |

Tests themselves are split across the two env-var names: `tests/conftest.py:28` sets
`BARTH_DB_PATH`; four integration tests set `BARTHO_DB_PATH` instead (`tests/integration/test_hybrid_paraphrase_benchmark.py:124,251`,
`tests/integration/test_lexical_over_vector_on_rare_tokens.py:107,229`,
`tests/integration/test_recency_flip_integration.py:177,283,388`,
`tests/integration/test_fts_unavailable_vector_quality.py:176,256,320`).

**Note:** `MemoryManager`'s divergent `memory.db` path is documented elsewhere as not reachable from
the live daemon process (see §7) — its divergence mostly affects direct instantiation/tests, not the
running system. The `bartholomew/cli.py` (`bartholomew.db`) divergence is live-relevant: it means the
CLI's `brake on/off/status` commands (§5) default to a **different file** than the daemon's Parking
Brake state unless `--db` is passed explicitly.

## 2. SQLite connection owners

Repo-wide grep for `sqlite3\.connect\(|aiosqlite\.connect\(` across non-test `*.py` files found
**23 files** that call `connect()` directly (re-verified twice from a clean shell; the number in
`docs/B0_EXECUTION_PLAN.md`'s own scoping section was an initial estimate of "~20," which this
report supersedes) — plus 2 module-utility files that provide (but do not themselves consume) a
shared pragma helper:

1. `bartholomew/cli.py` — 82, 146 — no pragmas beyond an inline `foreign_keys=ON` at 147; default timeout; per-call.
2. `bartholomew/skills/calendar_draft.py` — 209, 220 — no pragmas; default timeout; per-call.
3. `bartholomew/skills/notify.py` — 163, 174 — no pragmas; default timeout; per-call.
4. `bartholomew/skills/tasks.py` — 156, 167 — no pragmas; default timeout; per-call.
5. `bartholomew/orchestrator/safety/parking_brake.py` — 52, 66 — no pragmas; default timeout; per-call (`with` block).
6. `bartholomew/kernel/fts_client.py` — 328,368,424,478,581,684,712,769,825,854,908,1011,1055 (13 sites) — `set_wal_pragmas(conn)` applied at every site; 5s busy_timeout via that helper; per-call.
7. `bartholomew/kernel/db_ctx.py` — 89, plus its own `connect()` wrapper used by `wal_checkpoint`/`wal_db` — provides `set_wal_pragmas` (lines 40-60); 30s default via its wrapper; utility module, not a data-owning caller.
8. `bartholomew/kernel/consent_gate.py` — 53, 86 — no pragmas; default timeout; per-call (`try/finally`).
9. `bartholomew/kernel/narrator.py` — 584 (`:memory:`, held persistently as `self._conn`), 596, 608 — no pragmas; mixed persistent-in-memory / per-call-on-disk.
10. `bartholomew/kernel/working_memory.py` — 713, 738, 779, 830 — no pragmas; default timeout; per-call, manual `try/finally`.
11. `bartholomew/kernel/persona_pack.py` — 363 (`:memory:`, persistent), 373, 380 — no pragmas; same persistent/per-call split as narrator.py.
12. `bartholomew/kernel/skill_permissions.py` — 186, 197 — no pragmas; default timeout; per-call.
13. `bartholomew/kernel/experience_kernel.py` — 387, 738, 775, 827 — no pragmas; default timeout; per-call (`with` block).
14. `bartholomew/kernel/embedding_engine.py` — 391 — `:memory:` VSS-availability probe only, not the app DB.
15. `bartholomew/kernel/skill_registry.py` — 186, 197 (plus internal call sites via `_get_connection()`, see §3) — no pragmas; default timeout; per-call.
16. `bartholomew/kernel/memory_store.py` — many `aiosqlite.connect()` sites (175,305,493,607,645,661,670,692,702,720,730,772,844) + sync `sqlite3.connect()` at **562** and **883** — aiosqlite paths apply no explicit `set_wal_pragmas`; the two sync sites apply only `foreign_keys=ON`; default timeout; per-call throughout.
17. `bartholomew/kernel/vector_store.py` — 71, 90, 239, 275, 394, 459 — `set_wal_pragmas(conn)` applied at all 6 sites; per-call (`with` block).
18. `bartholomew/kernel/retrieval.py` — 50, 266, 650 — **no pragmas at any of the 3 sites** (no `set_wal_pragmas` import anywhere in the file); default timeout; per-call.
19. `bartholomew/kernel/hybrid_retriever.py` — 654 — `set_wal_pragmas(conn)` applied (imported at line 31); per-call.
20. `bartholomew_api_bridge_v0_1/services/api/db_ctx.py` — 66, its own `connect()` wrapper — provides its own separate copy of `set_wal_pragmas` (lines 17-37); 30s default; utility module, API-side twin of #7.
21. `identity_interpreter/adapters/memory_manager.py` — 202 (init: sets `foreign_keys=ON`, `journal_mode=WAL`, `synchronous=NORMAL`), plus 6 further per-call sites at 431,508,584,614,663,685 that reopen `sqlite3.connect(self.db_path)` with **no pragma re-application**.
22. `scripts/backfill_fts.py` — 256, 273 — standalone CLI script; opens two separate raw connections (`read_conn`, `write_conn`) to the same file concurrently, no pragmas on either.
23. `cleanup_test_memory.py` (repo root) — line 39 — a maintenance/cleanup script (not test code despite the name; it deletes stale rows from the real DB via `sqlite3.connect(db_path)`), no pragmas. **Not in the original candidate list or the initial agent pass — found only on a second independent verification grep**, which is itself a concrete illustration of why this report re-verifies rather than trusting either the archive or a first pass.

**Pragma-consistency summary:** only `fts_client.py`, `vector_store.py`, `hybrid_retriever.py`, and
the kernel/API `db_ctx.wal_db()` callers (§4) consistently apply `set_wal_pragmas` (WAL mode,
`synchronous=NORMAL`, `foreign_keys=ON`, 5000ms busy_timeout). Every other owner listed above opens a
raw connection with SQLite's compiled-in defaults (5s busy_timeout, `synchronous=FULL` unless WAL was
already set at the file level by some other writer).

**Existing guard rail's actual scope:** `tests/test_no_raw_sqlite_connect_in_api.py:22-28` bans raw
`sqlite3.connect()` — but only within `bartholomew_api_bridge_v0_1/services/api/` (allowlisting its
own `db_ctx.py`). It does not cover any of the 23 files above under `bartholomew/`,
`identity_interpreter/`, or the two root/`scripts/` maintenance scripts.

## 3. Synchronous DB work reachable from the event loop

**Correction to the risk map's carried-over assumption:** `docs/PHASE_B_RISK_MAP.md`'s B0 row named
`persona_pack.py` and `narrator.py` as classes mixing sync DB calls into `async def` methods. Neither
file defines *any* `async def` method (`grep "async def"` returns no matches in either) — so that
specific claim, taken literally, does not hold against the current repository and is superseded by
the findings below.

**Confirmed event-loop-blocking (class itself has `async def` methods that call `sqlite3.connect()`
synchronously and unwrapped):**

- **`bartholomew/kernel/memory_store.py`**: `_handle_chunking()` (`async def`, line 508) calls
  `sqlite3.connect()` synchronously at **line 562** (a comment at line 557 states this is deliberate,
  "to avoid Windows locking issues" — not wrapped in `asyncio.to_thread`). `reembed_memory()`
  (`async def`, line 860) calls `sqlite3.connect()` synchronously at **line 883**, also unwrapped.
- **`bartholomew/kernel/skill_registry.py`**: `load_enabled_skills()` (`async def`, line 986) calls
  `_get_connection()` → `sqlite3.connect()` directly at **line 996**. `load_skill()` (`async def`,
  line 245) calls sync `_persist_skill_state()` (306, 317), which opens a connection via
  `_get_connection()` at line 946. `_finish()` (`async def`, line 721) calls sync `_audit_execution()`
  (777), which opens a connection at line 801. All unwrapped; all reachable from
  `KernelDaemon.start()` (`daemon.py:197`) and from every skill execution.
- **`bartholomew/skills/tasks.py`, `notify.py`, `calendar_draft.py`**: each `*Skill(SkillBase)` has
  `async def initialize()` calling sync `_init_database()` (opens a connection), and multiple
  `async def _action_*` methods calling sync `_get_connection()` — e.g. `tasks.py:374,405,429,462`;
  `notify.py:447,480,499`; `calendar_draft.py:628,659,683,708`. All unwrapped.

**A distinct, separate mechanism (caller-side, not class-internal) still reaches `persona_pack.py`
and `narrator.py`'s synchronous DB calls from the event loop:** `KernelDaemon.start()` (`async def`,
`daemon.py:147`) calls `self._init_experience_kernel()` (sync, `daemon.py:239`) directly — not via
`asyncio.to_thread` — which calls `self.persona_manager.switch_pack(...)` (sync, `daemon.py:273`),
and `PersonaPackManager`'s sync methods call `sqlite3.connect()` (`persona_pack.py:373,380`).
Likewise `daemon.py:203` calls `self.narrator.subscribe_to_workspace()` (sync) directly from
`async def start()`. This is worth recording as its own category for B2 planning: the blocking call
sites are unwrapped either because the *class itself* mixes sync DB work into `async def` methods
(memory_store.py, skill_registry.py, the three skill modules), or because an `async def` *caller*
invokes a fully-synchronous class's methods without `to_thread` (persona_pack.py, narrator.py). Both
need the same fix shape (move off the event loop) but the fix's exact location differs.

**Files confirmed to define zero `async def` methods at all** (no class-internal blocking by this
test, though still synchronous-DB-in-hot-path candidates depending on caller): `consent_gate.py`,
`working_memory.py`, `experience_kernel.py`, `vector_store.py`, `retrieval.py`, `hybrid_retriever.py`,
`fts_client.py`, `embedding_engine.py`, `skill_permissions.py`,
`identity_interpreter/adapters/memory_manager.py`, `parking_brake.py`.

`bartholomew/cli.py`, `scripts/backfill_fts.py`, and `cleanup_test_memory.py` are standalone
CLI/script processes, not part of any event loop.

## 4. WAL/checkpoint behaviour — two `db_ctx.py` modules, opposite defaults

- **Kernel `bartholomew/kernel/db_ctx.py`'s `wal_db()`** (lines 254-319): `checkpoint: str | None =
  None` — default is **no explicit checkpoint**; relies on SQLite's automatic WAL checkpoint
  (~every 1000 pages), per the docstring at 285-292. This matches `DECISIONS.md`'s 2026-07-24 entry
  ("Scheduler persistence moved off the event loop; routine WAL checkpointing turned off by
  default") — confirmed still true.
- **API-bridge `bartholomew_api_bridge_v0_1/services/api/db_ctx.py`'s `wal_db()`** (lines 161-204):
  has **no `checkpoint` parameter at all** — its `finally` block unconditionally calls
  `wal_checkpoint_truncate()` (line 204) on **every** exit, i.e. every call does a blocking
  `PRAGMA wal_checkpoint(TRUNCATE)`. `DECISIONS.md`'s note that this module "still checkpoints on
  every call" is confirmed current, not stale.

**Callers of kernel `db_ctx.wal_db()`** (rely on the automatic checkpoint):
`bartholomew/kernel/scheduler/persistence.py:59,90,126,166,201,244,287,317` and
`bartholomew/kernel/scheduler/health.py:47`.

**Callers of kernel `db_ctx.wal_checkpoint_truncate()`** (explicit, one-off, shutdown/maintenance
only): `bartholomew/kernel/memory_store.py:928` (inside `close()`).

**Callers of the API-bridge `wal_db()`** (checkpoint TRUNCATE on every call):
`bartholomew_api_bridge_v0_1/services/api/routes/liveness.py:55,113,156` — these back
`GET /api/liveness/self`, `/ticks`, `/nudges`, `/reflections` (§6), meaning **every liveness-API read
currently triggers a blocking exclusive-lock WAL TRUNCATE checkpoint** against the shared DB file —
and `bartholomew_api_bridge_v0_1/services/api/db.py:40` (`get_conn()`, used by `init_db()`).

**Callers of the API-bridge `wal_checkpoint_truncate()`**:
`bartholomew_api_bridge_v0_1/services/api/app.py:70`, registered via `atexit.register(...)`.

**Shutdown-time duplication:** two independent WAL-truncate implementations act on the same DB file
at shutdown — `KernelDaemon.stop()` → `self.mem.close(checkpoint=...)` → the kernel's
`wal_checkpoint_truncate()` (§8), and separately `app.py:70`'s `atexit` hook calling the API bridge's
own, separately-implemented `wal_checkpoint_truncate()`.

## 5. Parking Brake construction sites

Repo-wide grep for `ParkingBrake\(` (the class instantiation) found **9 production construction
sites** (re-verified twice), not the 7 recorded in `docs/PHASE_B_RISK_MAP.md`/
`docs/B0_EXECUTION_PLAN.md`'s carried-over count — see the discrepancy note below.

| # | file:line | Reachability |
|---|---|---|
| 1 | `bartholomew/kernel/runtime_contract.py:238` | live — `run_chat_through_runtime_contract()` (`async def`, line 195) |
| 2 | `bartholomew/kernel/runtime_contract.py:392` | live — `run_drive_through_runtime_contract()` (`async def`, line 338) |
| 3 | `bartholomew/kernel/runtime_contract.py:631` | live — `run_sight_through_runtime_contract()` (`async def`, line 578) |
| 4 | `bartholomew/kernel/runtime_contract.py:718` | live — `run_voice_through_runtime_contract()` (`async def`, line 684) |
| 5 | `bartholomew/kernel/skill_registry.py:667` | live — `_is_blocked_by_brake()`, called from `execute_action()` |
| 6 | `identity_interpreter/orchestrator/orchestrator.py:133` | live — `Orchestrator.handle_input()` |
| 7 | `bartholomew/cli.py:261` | standalone CLI — `brake_on` |
| 8 | `bartholomew/cli.py:277` | standalone CLI — `brake_off` |
| 9 | `bartholomew/cli.py:291` | standalone CLI — `brake_status` |

**Count: 6 live-process sites / 3 standalone-CLI sites (9 total).**

Live reachability, traced:
- **#1**: called from `bartholomew_api_bridge_v0_1/services/api/app.py:265` inside `POST /api/chat`,
  only when `_kernel is not None` (`app.py:259`).
- **#2**: called from the drive-tick path (`bartholomew/kernel/planner.py`), part of
  `KernelDaemon._system_tick()`.
- **#3, #4**: called from `identity_interpreter/adapters/sight/pipeline.py:49-52` and
  `identity_interpreter/adapters/voice_io/stream_bridge.py:48-51` respectively.
- **#5**: called from `SkillRegistry.execute_action()`, constructed as `self.skill_registry` in the
  live `KernelDaemon` (`daemon.py:115-123`).
- **#6**: `identity_interpreter.orchestrator.orchestrator.Orchestrator()` is constructed in-process
  at `app.py:91` (module scope). Its `handle_input()` runs **both** as the `_kernel is None` fallback
  (`app.py:270`) **and**, when the kernel *is* present, as the `_respond` callback passed into
  `run_chat_through_runtime_contract()` (`app.py:262-265`) — so this site is exercised on essentially
  every `/api/chat` call regardless of kernel state. It resolves its own DB path via
  `bartholomew.kernel.daemon._default_db_path()` (`orchestrator.py:129,132`) rather than the API
  bridge's `DB_PATH` — a third path-resolution route feeding this one call site (see §1).

Standalone CLI: **#7-9** each construct their own `BrakeStorage(db)` against the CLI's `--db` option
(default `data/bartholomew.db` — the divergent filename from §1), independent of any running daemon.

**Discrepancy flagged, not resolved:** `docs/B0_EXECUTION_PLAN.md:32` (quoting
`docs/PHASE_B_RISK_MAP.md`) carried over "7 sites / 3 CLI at archive time." Re-counting against the
current repository gives 9 total (6 live + 3 CLI) — one more live site than the archived count,
plausibly reflecting `runtime_contract.py`'s sight/voice additions since that archive snapshot. This
report records the discrepancy as found; resolving *why* the count changed (and whether it should)
is out of B0's scope.

## 6. Real API/CLI ingress

**FastAPI routes**, `bartholomew_api_bridge_v0_1/services/api/app.py` (root, no prefix):
`POST /kernel/command/{cmd}` (183), `GET /healthz` (207), `GET /api/health` (213),
`POST /api/chat` (249), `GET /api/conversation/recent` (278), `GET /api/nudges/pending` (308),
`POST /api/nudges/{nudge_id}/ack` (318), `POST /api/nudges/{nudge_id}/dismiss` (331),
`GET /api/reflection/daily/latest` (344), `GET /api/reflection/weekly/latest` (357),
`POST /api/reflection/run` (370).

`routes/self_state.py` (prefix `/api`, line 16): `GET /api/self` (69), `GET /api/self/affect` (84),
`PUT /api/self/affect` (92), `GET /api/self/attention` (109), `PUT /api/self/attention` (117),
`DELETE /api/self/attention` (135), `GET /api/self/drives` (146), `GET /api/self/drives/top` (156),
`POST /api/self/drives/{drive_id}/activate` (166), `POST /api/self/drives/{drive_id}/satisfy` (182),
`GET /api/self/goals` (207), `POST /api/self/goals` (215), `DELETE /api/self/goals/{goal}` (227),
`GET /api/episodes/recent` (244), `GET /api/episodes/search` (255),
`GET /api/episodes/{episode_id}` (308), `GET /api/episodes/by-type/{episode_type}` (318),
`GET /api/episodes/by-tag/{tag}` (345), `GET /api/persona/current` (362),
`GET /api/persona/list` (375), `POST /api/persona/switch` (393),
`GET /api/persona/history` (414), `GET /api/persona/{pack_id}` (426),
`GET /api/working_memory` (441), `GET /api/working_memory/context` (455),
`DELETE /api/working_memory` (466).

`routes/liveness.py` (prefix `/api/liveness`, line 18): `GET /api/liveness/self` (33),
`GET /api/liveness/ticks` (43), `GET /api/liveness/nudges` (101),
`GET /api/liveness/reflections` (144).

`routes/metrics.py`: `GET /metrics` (or `/internal/metrics` when `METRICS_INTERNAL_ONLY=1`,
mounted per `app.py:67`) — line 348.

**CLI subcommands**, `bartholomew/cli.py`: `embeddings stats` (26-27), `embeddings rebuild-vss`
(131-132), `brake on` (242-243), `brake off` (269-270), `brake status` (283-284).

**Note:** a second, unrelated CLI entry point exists at `identity_interpreter/cli.py` — out of this
report's scope (B0's scope names `bartholomew/cli.py` specifically) but flagged as existing for
whichever later stage needs the full CLI-process inventory.

## 7. Process topology — same OS process, in-process construction

`bartholomew_api_bridge_v0_1/services/api/app.py` uses legacy `@app.on_event("startup"/"shutdown")`
hooks (not an ASGI `lifespan` context manager). Startup (`app.py:98-136`) imports `KernelDaemon`
(118) and constructs it **in-process** at `app.py:121-128`, then `await`s `_kernel.start()`, followed
by a `keep_alive()` task (132-136) that just sleeps to keep task references alive. Shutdown
(139-142) `await`s `_kernel.stop()`.

So the FastAPI process and `KernelDaemon` are **the same OS process** — not a subprocess or separate
service. `_kernel` is a module-level global (`app.py:94-95`) referenced throughout route handlers.

`identity_interpreter.orchestrator.orchestrator.Orchestrator()` is *also* constructed in-process at
`app.py:91` (module scope, before the FastAPI startup event runs) — relevant to §5's Parking Brake
site #6.

`bartholomew/kernel/daemon.py:691-699`'s `run_kernel()` is a second, independent entry point that
constructs a bare `KernelDaemon` with no FastAPI wrapper — a standalone-daemon code path that exists
but was not confirmed to be invoked by any production caller checked in this pass.

## 8. Startup/shutdown order — `KernelDaemon` (`bartholomew/kernel/daemon.py`)

**`__init__`** (29-145, synchronous): config YAML (39) → `EventBus()` (42) → `MemoryStore` (43) →
`SchedulerStore` (52) → persona/policy/drives YAML (53-55) → `Planner` (56) → `WorldState` (57) →
optional `IdentityContext` (66-80) → `GlobalWorkspace` (83) → `ExperienceKernel` (84-87) →
`WorkingMemoryManager` (88-91) → `PersonaPackManager` (99-103) → `NarratorEngine` (104-108) →
`SkillRegistry` (115-122) → `planner.set_skill_registry()` (123).

**`async def start()`** (147-237), in order: (1) `await self.mem.init()` (148); (2)
`await self.scheduler_store.ensure_schema()` (186), with the whole block guarded so any
`BaseException` (including `CancelledError`) triggers `await asyncio.shield(self.scheduler_store.close())`
before re-raising (185-237); (3) `self._init_experience_kernel()` (189, sync — loads last snapshot,
working-memory snapshot, activates default persona if none active); (4)
`await self.skill_registry.load_enabled_skills()` (197), falling back to loading every discovered
skill if none loaded (198-200); (5) `self.narrator.subscribe_to_workspace()` (203, sync); (6) publish
"startup" system event (206-214) then `await asyncio.sleep(0)` (218); (7)-(10) create the four
background tasks — `_tick_task` (221), `_consumer_task` (222), `_dream_task` (223),
`_scheduler_task` (228, `run_scheduler(self)`).

**`async def stop()`** (281-349), in order: (1) publish "shutdown" system event (284-292),
`await asyncio.sleep(0)` (293); (2) `self.experience.persist_snapshot()` (297, sync, try/except); (3)
`self.working_memory.persist_snapshot(self.mem.db_path)` (304, sync, try/except); (4)
`await self.skill_registry.shutdown()` (311, try/except); (5) cancel all 4 background tasks
(315-323), then `await asyncio.wait_for(task, timeout=5.0)` each (326-331); (6)
`scheduler_drained = await self.scheduler_store.close()` (340) — must run before the memory-store
checkpoint per the comment at 333-339 ("nothing is still mid-operation on the same file when that
checkpoint runs"); (7) `await self.mem.close(checkpoint=scheduler_drained)` (349) — the checkpoint is
**skipped** if the scheduler store didn't drain cleanly (comment 341-345: deferred to next startup,
not silently dropped).

## 9. Python/Windows support

`pyproject.toml:12`: `requires-python = ">=3.10"`. No `classifiers` block exists in the file at all
(no `Programming Language ::`/`Operating System ::` entries — confirmed absent, not merely unread).
Pytest markers registered in `pyproject.toml:139-145`: `integration`, `slow`, `smoke`, `asyncio`,
`unit`; `windows_quirk` and `database` are instead registered dynamically in `conftest.py:216-223`.

`CI.md:33-36` matrix: `quality` (Ubuntu, 3.11, packaging/lint); `tests` (Ubuntu, 3.10 & 3.11, full
suite + ≥70% coverage); `critical` (Ubuntu, 3.10 & 3.11, integration/slow excluded by default marker);
`windows` (Windows, **3.11 only** — no Windows 3.10 leg) running `pip check`, packaging contract,
clean-start lifecycle, scheduler readiness, and smoke tests. `CI.md:19-20` notes this was "the first
[Windows job] ever run in this repository"; `CI.md:192-208` documents a posture change toward
diagnosing Windows failures rather than dismissing them as environmental noise.

Windows-specific skip markers: `conftest.py:77-85` defines `SKIP_WINDOWS_FTS` and
`SKIP_WINDOWS_FILE_LOCKING` (both `skipif(sys.platform == "win32", ...)`), applied across 15 files
(`tests/test_retrieval_fts5_fallback.py`, `test_retrieval_hot_reload.py`, `test_hybrid_boosts_flip.py`,
`test_hybrid_fusion_math.py`, `test_hybrid_recency.py`, `test_hybrid_rrf.py`,
`test_hybrid_tiebreakers.py`, `tests/conftest.py`, `test_fts_schema_hygiene.py`, root `conftest.py`,
`test_stage2f_chunking.py`, `test_retrieval_factory.py`, `test_bm25_udf_fallback.py`,
`test_consent_bypass_redteam.py`, `test_consent_gates.py`). **`tests/conftest.py:33` independently
redefines its own `SKIP_WINDOWS_FTS`, separate from the root `conftest.py:77` definition of the same
name** — a duplicate-definition detail worth flagging, not resolving, here. `@pytest.mark.windows_quirk`
explicit usages: `test_fixtures_windows.py:14`, `test_sqlite_wal_cleanup.py:17,95`; `conftest.py:234-237`
also auto-applies `windows_quirk` to any test whose name contains `"cleanup"` or `"teardown"`.

## 10. Confirmed / revised / new since `docs/PHASE_B_RISK_MAP.md`'s B0 row

| Risk map claim | Status here |
|---|---|
| "One SQLite file has no single connection owner" across `MemoryStore`, scheduler, `persona_pack.py`/`narrator.py` | **Confirmed, but the surface is far larger than named** — 23 files call `connect()` directly (§2), not ~4. |
| "`persona_pack.py`/`narrator.py` call synchronous `sqlite3` directly from `async def` methods" | **Revised** — neither file defines any `async def` method; the actual blocking mechanism is caller-side (`KernelDaemon.start()` calling their sync methods without `to_thread`), and a distinct, larger set of files (`memory_store.py`, `skill_registry.py`, the three skill modules) has the literal class-internal pattern instead (§3). |
| "Real Parking Brake construction sites and real API/CLI ingress may differ from any prior count (5 real routes, 7 real construction sites at archive time)" | **Revised** — 9 Parking Brake sites (6 live + 3 CLI), not 7; ~30 real API routes across 4 route modules (§6), not 5 — the archive's route count appears to predate `self_state.py`'s and `liveness.py`'s routers. |
| Duplicate WAL/checkpoint modules, API bridge checkpointing on every call | **Confirmed unchanged** (§4), consistent with `DECISIONS.md`'s 2026-07-24 entry. |

## 11. Open questions for B1 (recorded, not answered here)

- Which of the four DB-path/filename schemes (§1) should become authoritative, and how should the
  three divergent env-var/filename schemes be reconciled without silently changing which file a
  long-running deployment's data already lives in?
- Whether the 23-file connection-owner list (§2) should all migrate onto one shared policy in B1
  itself, or whether B1 should (per its own stated scope) merely inventory and assign each to B2/B8 —
  this report deliberately does not decide, per B0's "characterise, don't fix" scope.
- Whether `identity_interpreter/orchestrator/orchestrator.py`'s Parking Brake site (§5 #6) — which
  resolves its own DB path independently via `_default_db_path()` — should be reconciled with the API
  bridge's `DB_PATH` as part of B4's shared-instance work, given it is live-reachable on every
  `/api/chat` call.
- Whether `bartholomew/cli.py`'s divergent `data/bartholomew.db` default (§1, §5) means the CLI's
  `brake on/off/status` commands can silently operate on a different Parking Brake state than the
  live daemon's when `--db` isn't passed explicitly — a candidate B6 finding, flagged here since B0
  is where it was first observed.
