# B0 — Verified Persistence Baseline

> **Status:** B0 exit deliverable per `docs/PHASE_B_OVERVIEW.md` §5 and `ROADMAP.md`'s Phase B
> stage table. This is a **diagnostic report only** — it characterises current repository and
> runtime facts and does **not** propose fixes, redesigns, or implementation. No code, schema, or
> behaviour was changed to produce it. Every claim below is backed by a direct `file:line` citation
> against the repository at the base commit noted below; nothing here is inferred from the archived
> research document (`docs/archive/phase-b-persistence-ownership-final.md`) without being
> independently re-verified.
>
> **Base commit:** `72b2ed5` (branch `claude/b0-work-ug6rkv`, working tree clean at investigation
> time).
>
> **Scope:** the nine facts `docs/PHASE_B_OVERVIEW.md` §5 assigns to B0 — DB paths, connection
> owners, event-loop-blocking calls, WAL/checkpoint behaviour, Parking Brake construction sites,
> real API/CLI ingress, process topology, startup/shutdown order, Python/Windows support. Per B0's
> major deferral, no fix for anything found here is proposed; that is B1+ territory.

## 1. Actual DB path(s)

Three distinct default-path constants exist, not one:

- **`data/barth.db`** — the live-daemon path. Resolved identically in two places, both honouring a
  `BARTH_DB_PATH` env override:
  - `bartholomew/kernel/daemon.py:672-688` (`_default_db_path()`): env var, else
    `data/barth.db` under the directory containing `pyproject.toml` (walking up from
    `daemon.py`), else `data/barth.db` under `cwd()`. Used by `run_kernel()` at
    `daemon.py:694`.
  - `bartholomew_api_bridge_v0_1/services/api/db.py:9-19`: same resolution shape
    (`_find_project_root()`, then `DB_PATH = os.getenv("BARTH_DB_PATH", DEFAULT_DB_PATH)`).
  - Because the live API-bridge process constructs the `KernelDaemon` with this same `DB_PATH`
    (§7), the daemon and the API layer resolve to the identical file in production.
  - `config/kernel.yaml:5` also declares `memory.db_path: "data/barth.db"`, but no code path was
    found that actually loads and consumes this YAML key at runtime (only docstring/comment
    references in `retrieval.py:399,846`) — **unverified whether this key is live or vestigial**.
- **`data/bartholomew.db`** — a **different** default, used only by `bartholomew/cli.py`'s Typer
  `--db` options: `cli.py:28, 133, 250, 271, 285`. This is a real discrepancy: running any
  `bartholomew brake ...` or `embeddings ...` CLI command without an explicit `--db` operates on a
  different SQLite file than the one the live daemon uses (confirmed by direct read of
  `cli.py:242-301`, reproduced below in §5/§6).
- **`data/memory.db`** — a third default, in `identity_interpreter/adapters/memory_manager.py:169`
  (`self.data_dir / "memory.db"`, `data_dir` defaulting to `Path("data")`). This path is
  architecturally live code but **unreachable in the live process**: `context_builder.py`'s own
  module docstring (lines 6-13) states `Orchestrator()` is constructed with no `identity_config` in
  `bartholomew_api_bridge_v0_1/services/api/app.py:91` (`orch = Orchestrator()`), so
  `self.memory` there is always `None`. Confirmed: `orchestrator.py:23`'s `identity_config`
  parameter defaults to `None` and `app.py:91` passes nothing. Also used by standalone root
  scripts (`test_memory_functionality.py:119`, `cleanup_test_memory.py:38`, `test_cold_boot.py`).

**Override:** one env var, `BARTH_DB_PATH`, read at `daemon.py:681` and `db.py:19`. It does not
affect the CLI's per-command `--db` default.

## 2. SQLite connection owners

| Module | Async? | Pattern |
|---|---|---|
| `bartholomew/kernel/memory_store.py` (`MemoryStore`) | `aiosqlite`, but no persistent connection — 13 one-off `async with aiosqlite.connect(...)` sites (`memory_store.py:175,305,493,607,645,661,670,692,702,720,730,772,844`). Two methods additionally use **synchronous** `sqlite3.connect` directly inside `async def` (`memory_store.py:562` in `_handle_chunking`, `:883` in `reembed_memory`) — see §3. |
| `bartholomew/kernel/scheduler/store.py` (`SchedulerStore`) | One dedicated worker thread: `ThreadPoolExecutor(max_workers=1, thread_name_prefix="scheduler-db")` (`store.py:59`); `_call()` (`store.py:76-104`) submits `scheduler/persistence.py`'s synchronous `sqlite3` functions to it via `asyncio.wrap_future`. `KernelDaemon.__init__` constructs exactly one instance (`daemon.py:52`). No `DedicatedDbExecutor` class (general/governance two-lane executor) exists anywhere in the repo — that mechanism is proposed-only in the archived document, not implemented. |
| `bartholomew/kernel/persona_pack.py` (`PersonaPackManager`) | Fully synchronous, no `async def` in the file. `:memory:` case keeps one persistent `self._conn` (`persona_pack.py:363`); file-backed case opens one-off connections per call via `_get_connection()`/`_close_if_not_persistent()` (`:376-385`). Call sites: `:561-563, 671, 702`. |
| `bartholomew/kernel/narrator.py` (`NarratorEngine`) | Same shape as `persona_pack.py` — fully synchronous, no `async def`. `_get_connection()`/`_close_if_not_persistent()` (`narrator.py:604-613`), 9 call sites at `:1193,1232,1262,1305,1337,1356,1393,1471,1535,1635`. |
| `bartholomew/orchestrator/safety/parking_brake.py` (`BrakeStorage`) | Fully synchronous, one-off connection per call, no persistent connection: `fetch_flag()` (`:52`), `upsert_flag()` (`:66`). `ParkingBrake.__init__` (`:132-140`) synchronously calls `self._load()` (`:142`) → `fetch_flag()`, so **every** `ParkingBrake(storage)` construction opens and closes a SQLite connection as a side effect of construction. |
| `bartholomew/kernel/fts_client.py` (`FTSClient`) | Fully synchronous, no `async def`. One-off `sqlite3.connect(self.db_path)` at 13 call sites (`:328,368,424,478,581,684,712,769,825,854,908,1011,1055`). |

**Other direct `sqlite3.connect(` sites** (repo-wide, excluding `tests/`): the two `db_ctx.py`
modules' own `connect()` wrappers (`bartholomew/kernel/db_ctx.py:89`,
`bartholomew_api_bridge_v0_1/services/api/db_ctx.py:66`); `scripts/backfill_fts.py:256,273`;
`identity_interpreter/adapters/memory_manager.py:202,431,508,584,614,663,685`;
`cleanup_test_memory.py:39`; `bartholomew/cli.py:82,146`;
`bartholomew/skills/{calendar_draft.py:209,220; notify.py:163,174; tasks.py:156,167}`;
`bartholomew/kernel/{consent_gate.py:53,86; working_memory.py:713,738,779,830;
skill_permissions.py:186,197; experience_kernel.py:387,738,775,827;
embedding_engine.py:391 (":memory:" only); skill_registry.py:186,197;
vector_store.py:71,90,239,275,394,459; retrieval.py:50,266,650; hybrid_retriever.py:654}`.
None of these seven kernel modules (`vector_store.py`, `skill_permissions.py`, `consent_gate.py`,
`experience_kernel.py`, `working_memory.py`, `retrieval.py`, `hybrid_retriever.py`) contain any
`async def` method.

**`aiosqlite` usage repo-wide** (excluding tests) is confined entirely to `memory_store.py`. No
other module imports it.

## 3. Synchronous DB work reachable from `async def` (event-loop-blocking)

- `memory_store.py`: `_handle_chunking` (`async def`, `:508`) calls unwrapped `sqlite3.connect` at
  `:562`; `reembed_memory` (`async def`, `:860`) calls unwrapped `sqlite3.connect` at `:883`.
  Neither uses `asyncio.to_thread` or an executor.
- `persona_pack.py`/`narrator.py` define no `async def` themselves, but their synchronous,
  unwrapped methods are called directly (no thread offload) from several live `async def` sites:
  - `self_state.py:398` (`async def switch_persona`) → `persona_manager.switch_pack(...)` →
    `_log_switch()` → sync `sqlite3` I/O.
  - `self_state.py:244-354` — five `async def` route handlers call
    `narrator.get_recent_episodes/search_episodes/get_episode/get_episodes_by_type/
    get_episodes_by_tag(...)`, each hitting `_get_connection()` synchronously.
  - `daemon.py:377`, inside `async def _system_tick` (`:362`): `persona_manager
    .auto_activate_if_needed(...)` can call `switch_pack()` → sync DB I/O directly on the
    event-loop tick task.
  - `daemon.py:549,638` (`generate_daily_reflection_narrative`/`generate_weekly_reflection
    _narrative`), reachable from `async def _dream_loop` (`:407`) and from
    `async def handle_command` (`:664`), itself reachable from the live `POST
    /kernel/command/{cmd}` route (`app.py:183-189`).
- `parking_brake.py` is the clearest concrete instance: `ParkingBrake.__init__` synchronously opens
  a SQLite connection (`:52,132-146`), and is constructed with no thread offload inside four
  `async def` functions in `runtime_contract.py` — `run_chat_through_runtime_contract` (`:195`,
  construction at `:238`), `run_drive_through_runtime_contract` (`:338`, `:392`),
  `run_sight_through_runtime_contract` (`:578`, `:631`), `run_voice_through_runtime_contract`
  (`:684`, `:718`) — and inside `skill_registry.py`'s `async def execute_action` (`:498`) via the
  plain-`def` helper `_is_blocked_by_brake` (`:649-671`, `ParkingBrake(storage)` at `:667`).
- `fts_client.py` is fully synchronous with no thread-offload wrapper anywhere in the file; its
  callers were not individually traced in this pass, so reachability from the live event loop is
  **not separately confirmed here** beyond the module-level fact that it offers no offload of its
  own.

## 4. WAL/checkpoint behaviour

Both `bartholomew/kernel/db_ctx.py` (320 lines) and `bartholomew_api_bridge_v0_1/services/api
/db_ctx.py` (205 lines) were read in full.

**Pragmas** (identical in both): `journal_mode=WAL`, `synchronous=NORMAL`, `foreign_keys=ON`,
`busy_timeout=5000` — kernel version at `db_ctx.py:57-60`, API-bridge version at `db_ctx.py:34-37`.

**Checkpoint-on-exit behaviour diverges — this is the key finding:**

- **Kernel** `bartholomew/kernel/db_ctx.py:254-319`: `wal_db(..., checkpoint: str | None = None, ...)`.
  Default is `None` — no checkpoint runs unless the caller explicitly passes a mode; the `finally`
  block (`:313-319`) only checkpoints `if checkpoint:`. Docstring (`:285-296`) states this is
  deliberate: SQLite's own automatic ~1000-page WAL checkpoint is the routine mechanism; explicit
  `checkpoint=` is a disk-layout/performance knob, not a correctness one.
- **API bridge** `bartholomew_api_bridge_v0_1/services/api/db_ctx.py:161-204`: `wal_db(...)` takes
  **no `checkpoint` parameter at all** — its `finally` block (`:199-204`) **unconditionally** calls
  `wal_checkpoint_truncate(...)` (a blocking `PRAGMA wal_checkpoint(TRUNCATE)`, `:74-112`) on
  **every** call, on every exit.
- **Callers of the API bridge's `wal_db()`:** `liveness.py:55, 113, 156` — three read-only GET
  routes (`/api/liveness/ticks`, `/api/liveness/nudges` at `liveness.py:101`,
  `/api/liveness/reflections` at `:144`) — each triggers a full blocking `TRUNCATE` checkpoint on
  every request. Also `db.py:40`'s `get_conn()` (used by `init_db()`, `db.py:44-47`). Separately,
  `app.py:70` registers `atexit.register(lambda: db_ctx.wal_checkpoint_truncate(DB_PATH))`,
  independent of `wal_db()`.
- `INTERFACES.md:56` documents the kernel-side corrected (non-checkpoint-per-call) behaviour but
  does not describe the API bridge's still-unconditional-checkpoint `wal_db()` — the two `db_ctx.py`
  modules' behaviour has diverged from what the canonical doc states for "the" `wal_db()`.

## 5. Real Parking Brake construction sites

Repo-wide grep for `ParkingBrake(` (excluding `tests/`, excluding docs), independently re-run and
read at each line:

| # | Site | Reachability |
|---|---|---|
| 1 | `identity_interpreter/orchestrator/orchestrator.py:133` | Live-daemon: `Orchestrator.handle_input()` is called from the live `/api/chat` route (`app.py:262-263, 270`). |
| 2 | `bartholomew/cli.py:261` (`brake_on`) | CLI-only. |
| 3 | `bartholomew/cli.py:277` (`brake_off`) | CLI-only. |
| 4 | `bartholomew/cli.py:291` (`brake_status`) | CLI-only. |
| 5 | `bartholomew/kernel/skill_registry.py:667` (`_is_blocked_by_brake`) | Live-daemon: called from `async def execute_action` (`:586`), the live skill-execution path. |
| 6 | `bartholomew/kernel/runtime_contract.py:238` (`run_chat_through_runtime_contract`) | Live-daemon: reachable from `/api/chat` when `_kernel is not None` (`app.py:249-275`). |
| 7 | `bartholomew/kernel/runtime_contract.py:392` (`run_drive_through_runtime_contract`) | Live-daemon: reachable from the scheduler drive loop, started unconditionally by `KernelDaemon.start()` (`daemon.py:228`). |
| 8 | `bartholomew/kernel/runtime_contract.py:631` (`run_sight_through_runtime_contract`) | Not confirmed reachable from any live HTTP route (none found in `app.py`, `liveness.py`, `self_state.py`, `metrics.py`); referenced from `identity_interpreter/adapters/sight/pipeline.py`, whose own live wiring was not traced further in this pass. |
| 9 | `bartholomew/kernel/runtime_contract.py:718` (`run_voice_through_runtime_contract`) | Same unconfirmed status as #8; referenced from `identity_interpreter/adapters/voice_io/stream_bridge.py`. |

`bartholomew/cli.py`'s three sites are confirmed at exactly lines 261, 277, 291 — matching the
archived document's citation precisely. The **total count**, however, is **9, not 7** as the
archived document's headline figure states (its own Blocker-Resolution Matrix enumeration lists
essentially these same 9 locations, so the "7" appears to be an internal inconsistency in that
document, not a fact about a smaller current repository).

**Note on CLI wiring:** `bartholomew/cli.py` has no `console_scripts` entry in `setup.py` or
`pyproject.toml` — the packaged `barth` console script (`setup.py:19-23`) points at
`identity_interpreter.cli:main`, a different module. `bartholomew/cli.py` is only reachable via
`python -m bartholomew.cli` / direct invocation (`cli.py:309-310`), not as an installed command.

## 6. Real external API/CLI ingress

**Live HTTP routes**, read directly from each router file:

- `bartholomew_api_bridge_v0_1/services/api/app.py` — 11 routes: `POST /kernel/command/{cmd}`
  (`:183`), `GET /healthz` (`:207`), `GET /api/health` (`:213`), `POST /api/chat` (`:249`),
  `GET /api/conversation/recent` (`:278`), `GET /api/nudges/pending` (`:308`),
  `POST /api/nudges/{nudge_id}/ack` (`:318`), `POST /api/nudges/{nudge_id}/dismiss` (`:331`),
  `GET /api/reflection/daily/latest` (`:344`), `GET /api/reflection/weekly/latest` (`:357`),
  `POST /api/reflection/run` (`:370`).
- `routes/liveness.py` (mounted at `/api/liveness`, `:18`) — 4 routes: `GET /self` (`:33`),
  `GET /ticks` (`:43`), `GET /nudges` (`:101`), `GET /reflections` (`:144`).
- `routes/self_state.py` (mounted at `/api`, `:16`) — 21 routes, including state-mutating ones not
  named in the archived document: `PUT /self/affect` (`:92`), `PUT /self/attention` (`:117`),
  `DELETE /self/attention` (`:135`), `POST /self/drives/{drive_id}/activate` (`:166`),
  `POST /self/drives/{drive_id}/satisfy` (`:182`), `POST /self/goals` (`:215`),
  `DELETE /self/goals/{goal}` (`:227`), `POST /persona/switch` (`:393`),
  `DELETE /working_memory` (`:466`), plus 12 GET routes.
- `routes/metrics.py` — 1 route: `GET /metrics` (`:348`, mounted at `/internal/metrics` if
  `METRICS_INTERNAL_ONLY` is truthy — `app.py:66-67`).

**Total: 37 live HTTP routes**, not 5. The archived document's "5 real routes" figure
(`app.py:249,183,318,331,370`, all confirmed exact) is accurate as a **scoped** claim about the
`ParkingBrake`-gated routes in `app.py` specifically — every one of `self_state.py`'s 21 routes was
read and none construct a `ParkingBrake` or call `is_blocked()`. Read as a general "real external
ingress" claim, "5" materially undercounts the live surface.

**CLI commands touching persistence/governance** (`bartholomew/cli.py`, 6 commands total):
`embeddings stats` (`:26-27`), `embeddings rebuild-vss` (`:131-132`, direct `sqlite3.connect` at
`:82,146`), `brake on` (`:242-266`), `brake off` (`:269-280`), `brake status` (`:283-301`) — the
latter three each construct `BrakeStorage`+`ParkingBrake` independently (§5).

## 7. Daemon/API process topology

**Single process.** `bartholomew_api_bridge_v0_1/services/api/app.py`'s
`@app.on_event("startup")` handler (`:98-136`) constructs `KernelDaemon` in-process
(`:121-128`, `db_path=DB_PATH`) and `await`s `_kernel.start()` (`:129`) inside the same asyncio
event loop as the FastAPI app — not a separate OS process. Shutdown mirrors this
(`@app.on_event("shutdown")`, `:139-142`, `await _kernel.stop()`).

Entry point: repo-root `app.py` re-exports `bartholomew_api_bridge_v0_1.services.api.app.app` for
`uvicorn app:app`; `Dockerfile:31` confirms `CMD ["uvicorn", "app:app", ...]`; `docker-compose.yml`
defines exactly one service (`:3-21`), mounting `./data:/app/data`. No systemd unit or separate
daemon-process launcher was found. `daemon.py:691-703`'s `run_kernel()` (a standalone entry point
that constructs and starts a `KernelDaemon` directly) exists in the module but no script or
console-entry invokes it — it is an alternate/manual-only path, not what Docker/compose runs.

A second near-duplicate shim exists at `bartholomew_api_bridge_v0_1/app.py:1-13` (re-exports the
same app for running uvicorn from inside that subdirectory); the Dockerfile/compose setup uses the
repo-root shim.

## 8. Startup/shutdown order

Read `KernelDaemon.start()` (`daemon.py:147-237`) and `.stop()` (`:281-349`) in full.

**`start()`, in order:** (1) `await self.mem.init()` (`:148`); (2) `await
self.scheduler_store.ensure_schema()` (`:186`, explicitly awaited); (3)
`self._init_experience_kernel()` (`:189`, synchronous — restores experience/working-memory
snapshots, activates a default persona if none active); (4) `await
self.skill_registry.load_enabled_skills()` (`:197`, falling back to loading all discovered skills);
(5) `self.narrator.subscribe_to_workspace()` (`:203`); (6) startup event published, then
`await asyncio.sleep(0)` (`:206-218`); (7) background tasks created — `_tick_task`, `_consumer_task`,
`_dream_task` (`:221-223`); (8) `self._scheduler_task = asyncio.create_task(run_scheduler(self))`
(`:228`). A `try/except BaseException` wraps steps 2-8 (`:185-237`); on any exception it runs
`await asyncio.shield(self.scheduler_store.close())` (`:231`) then always re-raises (`:237`) —
**only `scheduler_store` is unwound on a failed start; `self.mem` is not explicitly closed** in
this path.

**`stop()`, in order:** (1) shutdown event published, `await asyncio.sleep(0)` (`:284-293`); (2)
`self.experience.persist_snapshot()` (`:297`); (3)
`self.working_memory.persist_snapshot(self.mem.db_path)` (`:304`); (4) `await
self.skill_registry.shutdown()` (`:311`); (5) cancel `_tick_task`/`_consumer_task`/`_dream_task`/
`_scheduler_task` (`:315-323`), each awaited with a 5.0s timeout, swallowing
`TimeoutError`/`CancelledError` (`:326-331`); (6) `scheduler_drained = await
self.scheduler_store.close()` (`:340`) — closed before the memory-store checkpoint so nothing is
mid-operation on the same file; (7) `await self.mem.close(checkpoint=scheduler_drained)` (`:349`) —
checkpointing is skipped and deferred to next startup if step 6 did not drain cleanly.

**No** `brake_runtime`/clean-shutdown marker, write fence, or "admission terminal"/"internal tasks
terminal" precondition of any kind exists in this `stop()` method or elsewhere in `daemon.py` —
confirming the archived document's entire write-fence/clean-marker design (its Blocker-Resolution
Matrix findings #1, #5, #6, #8, #9) is a **proposed mechanism**, not current behaviour.

## 9. Supported Python/Windows behaviour

Read `.github/workflows/ci.yml` (218 lines) in full.

**Current CI matrix:** `quality` job — `ubuntu-latest`, Python 3.11 only (`:34`). `tests` job —
`ubuntu-latest`, `["3.10", "3.11"]` (`:85`). `critical` job — `ubuntu-latest`, `["3.10", "3.11"]`
(`:142`). `windows` job — `windows-latest`, `["3.11"]` **only** (`:184`) — Windows is not tested on
3.10. Declared requirement: `pyproject.toml:12` / `setup.py:17`, `>=3.10`.

**`CI.md` is stale relative to `ci.yml`:** its jobs table (lines 20-25) lists a `lint-test` job
(Ubuntu, 3.10 and 3.11) that does not exist in the current `ci.yml`, which has exactly 4 jobs
(`quality`, `tests`, `critical`, `windows`), not the 5-6 implied by `CI.md`'s "9 checks green"
narrative. This is independent of the persistence question but is a current-state fact about
documentation currency.

**Windows-specific code branches** (repo-wide grep for `sys\.platform|platform\.system\(\)
|msvcrt|win32` across `*.py`, production source only): none found in `bartholomew/`,
`bartholomew_api_bridge_v0_1/`, or `identity_interpreter/` production modules. The only production
Windows accommodation is `db_ctx.py:24-37`'s `_windows_release_handles()` (`gc.collect()` +
`time.sleep(delay)`), which runs unconditionally on every platform after connection close (not
gated by a platform check — it's designed to matter on Windows specifically), called from
`wal_checkpoint()`'s `finally` block (`:176`) and mirrored via the API bridge's
`fs_helpers.windows_release_handles` (referenced at `bartholomew_api_bridge_v0_1/services/api
/db_ctx.py:14,112`, not itself opened in this pass). The three actual `sys.platform == "win32"`
branches found are all test-only: `conftest.py:77-85` (repo root) and `tests/conftest.py:27-29`.

## 10. Items flagged as unconfirmed, not asserted

- Whether `config/kernel.yaml`'s `memory.db_path` key (§1) is read by any live code path, versus
  being vestigial documentation — only comment references were found, no active load call.
- Whether `run_sight_through_runtime_contract`/`run_voice_through_runtime_contract` (§5, sites 8-9)
  are reachable from any currently-live entry point via the `sight`/`voice_io` adapters — the
  adapters reference these functions, but their own live wiring was not traced in this pass.
- Full caller graph of `fts_client.py` (§3) — confirmed fully synchronous with no offload of its
  own, but not every call site was traced back to confirm event-loop reachability.
- `fs_helpers.windows_release_handles` (§9) was referenced but not opened directly.

## 11. Summary — archived-document claims re-verified against current repository

| Archived claim | Verdict |
|---|---|
| "7 real Parking Brake construction sites, 3 CLI-only at `cli.py:261,277,291`" | Line numbers **confirmed exact**. Total **contradicted**: current repository has **9** (§5). |
| "5 real external API/CLI ingress routes" | **Confirmed as scoped** to `app.py`'s `ParkingBrake`-gated routes (exact lines match). **Incomplete** as a general ingress claim — 37 live routes exist (§6), most ungoverned. |
| `MemoryStore` holds one persistent `aiosqlite` connection | **Contradicted** — no persistent-connection attribute exists; 13 one-off `aiosqlite.connect()` sites (§2). |
| `DedicatedDbExecutor`, two lanes (general, governance) | **Not present** in current repository. The real analogue is `SchedulerStore`, one single-worker lane (§2). Proposed design only. |
| Write-fence / clean-shutdown-marker / `brake_runtime` mechanism | **Not present** in current `KernelDaemon.start()`/`stop()` (§8), confirmed by full read of both methods. |
| `CI.md`'s "9 checks... `lint-test` on 3.10 and 3.11" | **Contradicted** by current `ci.yml` — 4 jobs, no `lint-test` (§9). |

No fix, redesign, or recommendation is proposed for any finding above — per B0's scope, that is
deferred to B1 and later stages, each requiring its own separately approved plan.
