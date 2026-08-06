# ROADMAP

> Milestones and stage gates with explicit exit criteria.
>
> **Last updated:** 2026-08-01 (B9 complete —  Phase B's final stage:
> `docs/B9_RECOVERY_ROLLBACK_ADVERSARIAL_VALIDATION.md` delivered. Real (not monkeypatched)
> adversarial tests against the integrated B0–B8 result: genuine SQLite file corruption (real bytes
> overwritten in a real database, not a mocked failure) caught a real bug — `PRAGMA quick_check`
> raises `sqlite3.DatabaseError` on sufficiently severe corruption instead of returning a
> descriptive row, which `run_quick_integrity_check()` didn't handle, degrading B5's Startup
> Incident Log diagnostics for exactly the case it exists to explain (startup still correctly
> aborted either way — fixed to restore the intended, diagnosable `UnsafeStartupError`). Genuinely
> concurrent `daemon.start()` calls (via `asyncio.gather`, not sequential) prove `ProcessLock`'s
> exclusion property under real interleaved contention; real OS threads (not sequential calls)
> racing `ProcessLock` and `GovernanceStore` prove 20-way concurrent contention resolves correctly
> with no lost writes and no double-grants. A direct repository search confirmed the archived
> design's `rollback_clear_maintenance()` rollback/maintenance mechanism was never built anywhere in
> the current codebase — documented honestly as "no such mechanism exists," not glossed over.
> Windows-specific behaviour needed no new B9-specific verification, since the existing Windows CI
> leg already continuously re-validates every Phase B stage's additions on every commit. With B9
> complete, Phase B's full B0–B9 stage sequence is done.)
>
> **Previously (2026-08-01):** B8 sub-stage 2 complete:
> `docs/B8_SUB2_MEMORYSTORE_CONCURRENCY_STRESS.md` delivered, closing the last named B8 risk-map
> candidate sub-stage 1 didn't cover: "MemoryStore concurrency and its own serialization cost under
> the new executor model." A new stress test (`tests/test_memory_store_concurrency_stress.py`, 3
> tests) fires many concurrent `upsert_memory()`/`reembed_memory()` calls against one `MemoryStore`
> sharing one `SingleWorkerExecutor` (the shape a busy daemon actually subjects it to, now that
> sub-stage 1 routes `VectorStore` calls through that same executor too) and confirms no writes are
> lost and no embedding ever gets cross-filed under the wrong `memory_id`. All three passed on
> first run against the current implementation -- no bug found, legitimate new regression coverage
> added. With this, every B8 risk-map candidate is now either fixed, tested-and-confirmed-sound, or
> confirmed not applicable; the one remaining named-but-deferred item
> (`SkillRegistry.__init__()`'s constructor-time blocking I/O) is a construction-site
> reorganization outside B8's "migrate remaining consumers" scope. Approval of this sub-stage does
> not authorise B9 or any other stage.)
>
> **Previously (2026-08-01):** B8 sub-stage 1 complete:
> `docs/B8_SUB1_STARTUP_SHUTDOWN_VECTORSTORE_SKILLS_OFF_LOOP.md` delivered per B8's own explicit
> "split further as appropriate" direction — B8 as a whole remains open, not approved-complete.
> `ExperienceKernel`/`WorkingMemoryManager`/`PersonaPackManager`/`VectorStore` all have zero
> `async def` methods (confirmed by direct read) — their blocking-ness was entirely a function of
> whether callers routed them off the event loop, the same B2 pattern already established
> elsewhere. Found and fixed four real gaps where that discipline wasn't applied: `daemon.py`'s
> `start()`/`stop()` called `ExperienceKernel`/`WorkingMemoryManager`/`PersonaPackManager` methods
> directly (once per lifecycle, not per-request); `memory_store.py`'s embedding pipeline
> (`_handle_embeddings`/`persist_embeddings_for`/`reembed_memory`) called `VectorStore` directly
> on a genuine per-write hot path when `BARTHO_EMBED_ENABLED=1`; `skill_registry.py`'s
> `_persist_skill_state()`/`_audit_execution()` did the same on every skill load and every skill
> action. Each is now routed through `run_off_loop()`, proven by 6 new tests that spy on thread
> identity to confirm the calls actually moved off the event-loop thread, not just that behavior
> was preserved. Also confirmed several B8 candidates from the risk map do *not* apply to the
> current repository: `liveness.py`/`metrics.py`'s plain `def` handlers are already
> threadpool-dispatched by FastAPI/Starlette itself, and `hybrid_retriever.py`'s search pipeline is
> unreachable from any live path today (CLI/script-only). Approval of this sub-stage does not
> authorise the rest of B8, B9, or any other stage.)
>
> **Previously (2026-08-01):** B7 complete: `docs/B7_EXTERNAL_REQUEST_ADMISSION.md` delivered per
> an explicitly approved (autonomous-continuation) B7 plan. Closes the shutdown gap B5 explicitly
> could not cover: new `bartholomew/kernel/request_admission.py`'s `RequestAdmission` is an
> identity-bound admit/release/drain primitive (fixing a real named risk-map finding — a prior
> design's `release()` took no identity argument, so any caller could release any in-flight
> admission). `KernelDaemon` owns one instance; `stop()` closes it and awaits `drain()` first of
> all — before the Governance write-fence close, before anything else — so in-flight external
> requests finish against still-intact resources instead of ones being torn down underneath them,
> with the drain outcome now feeding the clean-shutdown marker alongside the existing tracked
> resources. `bartholomew_api_bridge_v0_1/services/api/app.py` gains a single HTTP middleware
> chokepoint (`admission_middleware`) rather than a ~35-route migration: every request is admitted
> or refused (503) at one point, checking `lifecycle_state is RUNNING` (not just `_kernel is not
> None`, which does not catch the `STARTING` window — `_kernel` is assigned before `start()` is
> awaited to completion) with health/liveness/metrics/docs endpoints explicitly exempt so they stay
> responsive through startup/shutdown, matching liveness-probe convention. A repository re-check
> found no detached/child task spawning exists anywhere in the current codebase, substantially
> narrowing this stage's real scope versus the archived design's token-propagation machinery for
> work that doesn't exist here. Approval of B7 does not authorise B8 or B9.)
>
> **Previously (2026-07-31):** B6 complete: `docs/B6_EXTERNAL_GOVERNANCE_CLI_SAFETY.md` delivered
> per an explicitly approved B6 plan. `bartholomew/cli.py`'s `brake on`/`brake off`/`brake status`
> now write through `GovernanceStore` directly, retiring the last legacy `ParkingBrake`/
> `BrakeStorage` write path and, with it, B4's temporary dual-check bridge
> (`governance_bridge.py`, deleted along with its 8-test file, per B4's own docs' instruction).
> Adds `bartholomew/kernel/process_lock.py`: a cross-platform (`fcntl.flock`/`msvcrt.locking`)
> advisory lock `KernelDaemon` acquires first in `start()` and releases last in `stop()` — both the
> daemon's own single-instance guard and the anchor for B5's lifecycle-terminal-state conditions.
> `embeddings rebuild-vss` (the one CLI operation genuinely assuming exclusive file access) takes
> the same lock; `brake on/off/status` deliberately do not, since they're designed to control a
> *running* daemon and are already protected by GovernanceStore's write fence and revision
> guarding. CLI-issued Governance writes are now audit-tagged (`"CLI: ..."`), and
> `WriteFenceClosedError`/`StaleGovernanceWriteError` surface as actionable CLI messages instead of
> raw tracebacks. Three pre-existing tests that exercised the now-deleted bridge indirectly (via a
> standalone legacy `ParkingBrake`) were updated to engage `GovernanceStore` directly. Approval of
> B6 does not authorise B7 or any later stage.)
>
> **Previously (2026-07-31, same day):** B5 complete: `docs/B5_STARTUP_SHUTDOWN_INTEGRITY.md` delivered per
> an explicitly approved B5 plan. Adds `DaemonLifecycleState` tracking (`FAILED` terminal, never
> silently reset), a write-fence/clean-marker (`brake_runtime`) and an append-only Startup Incident
> Log (`startup_incidents`), both in `governance_store.py` since runtime-integrity state stays
> under Governance's single authority. `start()`'s protected region now covers every resource it
> activates, not just the two it happened to before (fixing two real unwind gaps found while
> re-grounding this stage: `governance_store` construction activating `blocking_executor` outside
> the old protected region, and producer tasks never being cancelled if `scheduler_task` creation
> failed after them). An unclean prior shutdown triggers a lightweight `PRAGMA quick_check`,
> repairs the deferred WAL checkpoint if it passes, and aborts startup with a new
> `UnsafeStartupError` if it doesn't. `stop()` closes the write fence before any other teardown
> step and only marks the shutdown clean if every tracked resource was *confirmed* terminal, not
> merely asked to stop. Two real bugs in the incident-recording path were caught by the new tests
> before merge and fixed. Approval of B5 does not authorise B6 or any later stage.)
>
> **Previously (2026-07-31, same day):** B4 complete: `docs/B4_GOVERNANCE_RUNTIME_INTEGRATION.md`
> delivered per an explicitly approved B4 plan. `KernelDaemon` now owns one shared `GovernanceStore`
> instance, wired into every real live-daemon Parking Brake construction site
> (`skill_registry.py`, `runtime_contract.py`'s chat/drive gates, `Orchestrator.handle_input()`'s
> mainline path) — CLI construction sites untouched, per the overview's exit condition, B6's
> responsibility. Because `bartholomew/cli.py`'s `brake on`/`brake off` still only writes the
> legacy `system_flags` value until B6, a temporary fail-closed dual-check bridge
> (`governance_bridge.py`, deliberately deleted alongside B6's migration) blocks execution if
> *either* the new schema or the legacy value says blocked, so the CLI kill switch keeps working
> against the running daemon through the migration window. Also fixed a newly-discovered gap B0/B2
> missed: `Orchestrator.handle_input()`'s Parking Brake check ran synchronously on the event loop
> on every chat message and redundantly duplicated `run_chat_through_runtime_contract`'s own gate.
>
> **Previously (2026-07-31, same day):** B3 complete: `docs/B3_GOVERNANCE_PERSISTENCE.md` delivered
> per an explicitly approved B3 plan. Adds a new, governance-owned schema
> (`bartholomew/orchestrator/safety/governance_store.py`'s `parking_brake_state`/
> `governance_audit`, separate from `MemoryStore`'s schema) and a `GovernanceStore` class, built
> alongside — not modifying — the still-live `ParkingBrake`/`BrakeStorage`. Delivers
> revision-guarded loosening (`StaleGovernanceWriteError`), atomic state+audit writes (verified via
> a real crash-injection trigger, not a mock), structured audit reasons, and an idempotent, additive
> `system_flags` legacy-state migration. `brake_runtime` deferred entirely to B5, per this stage's
> approved direction.
>
> **Previously (2026-07-31, same day):** B2 complete: `docs/B2_EVENT_LOOP_ISOLATION.md` delivered
> per an explicitly approved B2 plan. Adds a storage-agnostic `SingleWorkerExecutor` primitive
> (`bartholomew/kernel/blocking_executor.py`), generalized from `SchedulerStore`'s pre-existing
> pattern, and migrates all 5 of B1's assigned event-loop-blocking caller groups onto it: FTS
> startup schema init, `SkillRegistry.load_enabled_skills()`, `ParkingBrake` construction (4
> `runtime_contract.py` functions plus skill execution), persona/narrator calls, and memory
> chunking/re-embedding. Every fail-closed governance behavior verified unchanged.
>
> **Previously (2026-07-31, same day):** B1 complete: `docs/B1_SHARED_CONNECTION_POLICY.md`
> delivered per an explicitly approved B1 plan. The API layer's independent `db_ctx.py` — whose
> `wal_db()` unconditionally checkpointed on every call, including on read-only liveness GET routes
> — now re-exports the kernel's already-corrected module (no unrequested checkpoint by default),
> with a regression test guarding against re-divergence. Every remaining persistence caller B0
> found is inventoried and assigned to B2 or a B8 sub-stage.
>
> **Previously (2026-07-31, same day):** B0 complete: `docs/B0_PERSISTENCE_BASELINE.md` delivered as
> the stage's repository-grounded current-state report, per an explicitly approved B0 plan, then
> corrected once against PR #33 review comments before merge. B0's re-verification against the
> current repository contradicted two of the archived document's headline counts — 9 real Parking
> Brake construction sites, not 7; 42 live HTTP routes, not 5 — and found a live discrepancy between
> the CLI's default `--db` path (`data/bartholomew.db`) and the daemon/API default
> (`data/barth.db`), plus a second, distinct DB-path override (`BARTHO_DB_PATH` +
> `kernel.yaml`'s `memory.db_path`) alongside the primary `BARTH_DB_PATH`.
>
> **Previously (2026-07-31, same day):** documentation-only Phase B restructuring: the single,
> monolithic "Phase B" workstream entry is replaced with the **B0–B9** staged structure — a concise
> overview (`docs/PHASE_B_OVERVIEW.md`) plus ten separately gated stages. The prior large Phase B
> design specification is preserved, non-authoritatively, at
> `docs/archive/phase-b-persistence-ownership-final.md`, indexed by stage in
> `docs/PHASE_B_RISK_MAP.md`. See `DECISIONS.md`'s 2026-07-31 entry for the full rationale.
>
> **Previously (2026-07-28):** documentation reconciliation pass 2: Echo Integration Gates moved
> to non-canonical `docs/incubator/ECHO_IDEAS.md`; approved sequencing corrected so Stage 1
> precedes Stage 5/S5.1; Stage 1 scoped as a minimal consumer web governance shell with host-device
> onboarding; Stage 6 auth criterion corrected to require a threat model rather than assuming
> simple token auth is sufficient; jurisdiction-aware capture, adaptive notifications, and data
> portability added to Stage 6; the Stage 3/Stage 5 reflection-ownership entries corrected to
> distinguish current concatenation from the approved target architecture; the Stage 0 water-logging
> exit criterion annotated as historical, not current product direction.

## Engineering workstreams (cross-cutting; not stage gates)

Stage gates describe product capability. These describe the engineering foundation underneath
them, and are tracked here so a stage label is never read as a claim about verification quality.

### Phase A — Truthful cross-platform verification ✅ (Complete, merged 2026-07-27)

**Goal:** make verification of this repository automatic, cross-platform and trustworthy before
Stage 5 resumes — trusting no roadmap label, prior summary, comment or log unless confirmed
against current code and executable tests.

**Merged:** PR #26, merge commit **`8b96319c4059d9dfada2579ca5f6da22b34e1f31`**.
All 9 GitHub checks green on the merged head `e923fb9` (Quality; Tests + coverage on Ubuntu 3.10
and 3.11; Critical integration + lifecycle on 3.10 and 3.11; Windows 3.11; `lint-test` 3.10 and
3.11; `smoke`) — the Windows job was the first in this repository's history.

**Delivered:**
- `.github/workflows/ci.yml` — auto-run on every pull request, every push to `main`, and manual
  dispatch; four jobs across Ubuntu (3.10, 3.11) and Windows (3.11). See `CI.md` for the matrix.
- Packaging/dependency contract (`tests/smoke/test_packaging_contract.py`, 9 tests) that fails CI
  on an undeclared third-party runtime import, a first-party module that will not import, or a
  declared console script that will not run `--help`.
- Clean-start lifecycle characterisation (`tests/test_clean_start_lifecycle.py`, 6 tests),
  including the database-handle-release property that fails first on Windows.
- Coverage widened from one first-party package to all three, with the project's **pre-existing**
  declared 70% gate enforced (measured baseline 73.5%; the gate was not lowered).
- Two live production defects fixed, both found by disbelieving prior status: the sensitive-memory
  consent path (`asyncio.run()` called inside `async def`, always falling through to an
  undeclared `import nest_asyncio`) and the `bartholomew` console script (broken at import time).

**Deliberately not done (deferred, recorded not fixed):** persistence restructuring; the
intermittent concurrent-process WAL failure; findings F9–F11. See `RISKS.md`.

### Phase B — Persistence ownership stabilisation ✅ (Complete, 2026-08-01)

**Status as of 2026-07-31 (documentation-only restructuring):** a large, indivisible Phase B
design specification was produced and independently reviewed, but attempting to bring the entire
specification to implementation-level approval as one unit reached diminishing returns (too many
independently complex concerns — SQLite ownership, event-loop blocking, executors, Governance,
admission, startup/shutdown, CLI, rollback, and more — in one approval unit). Phase B is **not**
restarted: that research is preserved, non-authoritatively, at
`docs/archive/phase-b-persistence-ownership-final.md`, indexed by stage in
`docs/PHASE_B_RISK_MAP.md`. Phase B is now governed by a concise overview
(`docs/PHASE_B_OVERVIEW.md`) plus ten separately gated stages, **B0–B9**, defined below. This
table is the canonical source for Phase B stage gates, status, dependencies, and approval
boundaries — `docs/PHASE_B_OVERVIEW.md` is subordinate to it.

**All of B0 through B9 are complete (2026-08-01) — Phase B is done.** Each
stage's plan was presented and explicitly approved before its implementation began, per this
document's own approval model. B0's exit deliverable is `docs/B0_PERSISTENCE_BASELINE.md`, a
repository-grounded current-state report (no implementation, per B0's exit condition). B1's exit
deliverable is `docs/B1_SHARED_CONNECTION_POLICY.md`: the API layer's independent, hand-copied
`db_ctx.py` (whose `wal_db()` unconditionally checkpointed on every call, including on read-only
liveness routes) now re-exports the kernel's already-corrected module, and every remaining
persistence caller B0 found is inventoried and assigned to B2 or a B8 sub-stage. B2's exit
deliverable is `docs/B2_EVENT_LOOP_ISOLATION.md`: a new storage-agnostic `SingleWorkerExecutor`
primitive (generalized from `SchedulerStore`'s pre-existing pattern), with all 5 of B1's B2-assigned
blocking-caller groups migrated onto it and every fail-closed governance behavior verified
unchanged. B3's exit deliverable is `docs/B3_GOVERNANCE_PERSISTENCE.md`: a new governance-owned
schema and `GovernanceStore` class, built alongside — not modifying — the still-live
`ParkingBrake`/`BrakeStorage`, with revision-guarded loosening, atomic state+audit writes, and an
idempotent legacy-state migration, all tested in isolation. B4's exit deliverable is
`docs/B4_GOVERNANCE_RUNTIME_INTEGRATION.md`: `KernelDaemon` now owns one shared `GovernanceStore`
instance, wired into every real live-daemon construction site (CLI sites untouched, B6's
responsibility), with a temporary fail-closed dual-check bridge
(`bartholomew/orchestrator/safety/governance_bridge.py`) keeping the CLI kill switch effective
against the running daemon until B6 migrates it off the legacy `system_flags` path. B5's exit
deliverable is `docs/B5_STARTUP_SHUTDOWN_INTEGRITY.md`: `DaemonLifecycleState` tracking (`FAILED`
terminal, never silently reset), a write-fence/clean-marker and append-only Startup Incident Log
(both in `governance_store.py`), a startup protected region now covering every resource it
activates (fixing two real unwind gaps found while re-grounding this stage), and conservative
non-blocking unclean-shutdown recovery (lightweight integrity check, deferred-WAL-checkpoint
repair, abort only on actual evidence of unsafety). B6's exit deliverable is
`docs/B6_EXTERNAL_GOVERNANCE_CLI_SAFETY.md`: `bartholomew/cli.py`'s `brake on`/`brake off`/
`brake status` now write through `GovernanceStore` directly (audit-tagged `"CLI: ..."`), retiring
the legacy `ParkingBrake`/`BrakeStorage` write path and, with it, B4's temporary dual-check bridge
(`governance_bridge.py`, deleted per B4's own docs' instruction); a new cross-platform
`ProcessLock` (`bartholomew/kernel/process_lock.py`) is acquired first in `KernelDaemon.start()`
and released last in `stop()`, and by `embeddings rebuild-vss` — the one CLI operation genuinely
assuming exclusive file access, deliberately not `brake on/off/status`, which are protected by
GovernanceStore's own write fence and revision guarding instead. B7's exit deliverable is
`docs/B7_EXTERNAL_REQUEST_ADMISSION.md`: a new identity-bound `RequestAdmission` primitive
(`bartholomew/kernel/request_admission.py`), owned by `KernelDaemon`, that `stop()` closes and
drains first of all — before the Governance write fence, before anything else — so in-flight
external requests finish against still-intact resources; a single HTTP middleware chokepoint in
`bartholomew_api_bridge_v0_1/services/api/app.py` gates every real ingress point at once (checking
`lifecycle_state is RUNNING`, closing a real `STARTING`-window gap a bare `_kernel is not None`
check missed) while exempting health/liveness/metrics/docs endpoints. A repository re-check found
no detached/child task spawning exists anywhere in the codebase, narrowing this stage's real scope
from the archived design's token-propagation machinery. Approval of B7 does **not** authorise B8
or B9. B8's first split sub-stage (per B8's own "split further as appropriate" scope note) is
`docs/B8_SUB1_STARTUP_SHUTDOWN_VECTORSTORE_SKILLS_OFF_LOOP.md`: `ExperienceKernel`/
`WorkingMemoryManager`/`PersonaPackManager`/`VectorStore` have zero `async def` methods between
them (confirmed by direct read), so their blocking-ness was entirely a function of caller
discipline; four real gaps where `daemon.py` (`start()`/`stop()`), `memory_store.py`'s embedding
pipeline, and `skill_registry.py`'s audit/state persistence called them directly instead of
through `run_off_loop()` are now closed, each proven off-loop by a dedicated thread-identity test.
Several other B8 candidates were checked and confirmed not to apply to the current repository
(liveness/metrics routes are already threadpool-dispatched by FastAPI itself; the
`hybrid_retriever.py` search pipeline is unreachable from any live path today). B8's second split
sub-stage is `docs/B8_SUB2_MEMORYSTORE_CONCURRENCY_STRESS.md`: a stress test
(`tests/test_memory_store_concurrency_stress.py`) firing many concurrent `upsert_memory()`/
`reembed_memory()` calls against one `MemoryStore` sharing one `SingleWorkerExecutor`, closing the
risk map's remaining named B8 candidate ("MemoryStore concurrency... under the new executor
model") -- all three tests passed on first run, no bug found, legitimate new regression coverage.
With both sub-stages complete, every named B8 risk-map candidate is now fixed, tested-and-confirmed
-sound, or confirmed not applicable; the one remaining named-but-deferred item
(`SkillRegistry.__init__()`'s constructor-time blocking I/O) sits outside B8's own "migrate
remaining consumers" scope. B9's exit deliverable is
`docs/B9_RECOVERY_ROLLBACK_ADVERSARIAL_VALIDATION.md`: real (not monkeypatched) adversarial
tests against the integrated B0–B8 result — genuine SQLite file corruption caught a real bug
(`run_quick_integrity_check()` didn't handle `PRAGMA quick_check` itself raising
`sqlite3.DatabaseError` on severe corruption, degrading B5's Startup Incident Log diagnostics —
fixed), genuinely concurrent `daemon.start()` calls and real-OS-thread `ProcessLock`/
`GovernanceStore` contention (20-way) both prove their exclusion/serialization guarantees under
real concurrency, not sequential mock calls. A direct search confirmed the archived design's
`rollback_clear_maintenance()` mechanism was never built in this repository — documented as an
honest non-finding, not glossed over. Phase B's B0–B9 stage sequence is complete.

**Problem statement (characterised by Phase A, not fixed by it):** one SQLite file has no single
owner. `bartholomew/kernel/memory_store.py` uses `aiosqlite`;
`bartholomew/kernel/scheduler/persistence.py` uses synchronous `sqlite3` behind `SchedulerStore`'s
dedicated worker thread; `bartholomew/kernel/persona_pack.py` and `narrator.py` use synchronous
`sqlite3` called directly from async methods; and `bartholomew/kernel/db_ctx.py` and
`bartholomew_api_bridge_v0_1/services/api/db_ctx.py` are near-duplicate context modules with the
same WAL/checkpoint pattern, the latter still checkpointing per call in `liveness.py`/`db.py`.

**Evidence preserved for it:** `tests/test_sqlite_wal_concurrent_processes.py::
test_wal_cleanup_concurrent_processes` failed once under full-suite load and passed 3/3 in
isolation immediately afterwards; it was deliberately **not** retried, quarantined, re-marked or
given a longer timeout. The unresolved "why did a `TRUNCATE` checkpoint outlast its own
busy-timeout" question and its temporary DEBUG instrumentation are the likely same root cause.
See `RISKS.md`'s tech-debt watchlist.

**Stage structure (all stages B0–B9 complete):**

| Stage | Objective | Dependency | Approval gate | Exit condition |
|---|---|---|---|---|
| **B0** — Verified persistence baseline ✅ | Establish repository/runtime facts later stages need | none | Approved 2026-07-31 | Repository-grounded current-state report; no implementation — delivered as `docs/B0_PERSISTENCE_BASELINE.md` |
| **B1** — Shared SQLite connection policy ✅ | One connection/pragma/close policy; inventory and assign every remaining consumer migration | B0 | Approved 2026-07-31 | Shared policy implemented and tested; duplicate/hot-path checkpoint problem resolved; every remaining consumer migration inventoried and assigned to B2 or B8 — delivered as `docs/B1_SHARED_CONNECTION_POLICY.md` |
| **B2** — Event-loop isolation and database execution ✅ | Remove blocking sync SQLite calls from the event loop | B1 | Approved 2026-07-31 | Known blocking call sites resolved; worker termination confirmed — delivered as `docs/B2_EVENT_LOOP_ISOLATION.md` |
| **B3** — Governance schema and Parking Brake persistence ✅ | One durable, auditable Governance representation | B2 | Approved 2026-07-31 | Schema + transition semantics implemented and tested in isolation — delivered as `docs/B3_GOVERNANCE_PERSISTENCE.md` |
| **B4** — Shared Governance runtime integration ✅ | One shared Parking Brake instance at every real live-daemon call site | B3 | Approved 2026-07-31 | Every real live-daemon construction site (re-inventoried, not assumed) uses the shared instance; standalone CLI construction sites remain out of scope here and are B6's responsibility — delivered as `docs/B4_GOVERNANCE_RUNTIME_INTEGRATION.md` |
| **B5** — Startup and shutdown integrity ✅ | Reliable failure handling; clean-shutdown evidence for B1–B4's own resources, as lifecycle-terminal-state conditions (no process lock or external-admission draining yet) | B1–B4 | Approved 2026-07-31 | Startup/shutdown sequences verified against the concrete B1–B4 runtime; does not yet cover externally admitted work (B7) — delivered as `docs/B5_STARTUP_SHUTDOWN_INTEGRITY.md` |
| **B6** — External Governance control and CLI safety ✅ | CLI/maintenance tools cannot race the daemon; introduces the process lock, bound to B5's terminal-state conditions | B3–B5 | Approved 2026-07-31 | Verified on both POSIX and Windows; B5's lifecycle tests rerun with the lock in place — delivered as `docs/B6_EXTERNAL_GOVERNANCE_CLI_SAFETY.md` |
| **B7** — External request admission and detached work ✅ | Shutdown cannot race externally admitted work | B4, B5 | Approved 2026-08-01 | Every real ingress point is identity-bound-admission-gated; does not block B1–B4 — delivered as `docs/B7_EXTERNAL_REQUEST_ADMISSION.md` |
| **B8** — Remaining persistence consumers ✅ | Migrate MemoryStore/VectorStore/FTS/liveness/scheduler onto the shared policy | B1, B2 | Both sub-stages approved 2026-08-01 | Every named risk-map candidate fixed, tested, or confirmed not applicable — delivered as `docs/B8_SUB1_STARTUP_SHUTDOWN_VECTORSTORE_SKILLS_OFF_LOOP.md` and `docs/B8_SUB2_MEMORYSTORE_CONCURRENCY_STRESS.md` |
| **B9** — Recovery, rollback, and adversarial validation ✅ | Validate the integrated result; formalise recovery | B0–B8 | Approved 2026-08-01 | Adversarial scenarios pass; rollback limitations documented honestly — delivered as `docs/B9_RECOVERY_ROLLBACK_ADVERSARIAL_VALIDATION.md` |

See `docs/PHASE_B_OVERVIEW.md` for each stage's purpose, scope, and deferrals in more detail, and
`docs/PHASE_B_RISK_MAP.md` for the index of prior research findings relevant to each stage.
Detailed, execution-level planning for any stage is produced only when that specific stage is
approved to be planned — not in advance for later stages.

---

## Guiding rule

**Ship by gates, not by vibes.** Each gate has:
- explicit scope
- acceptance criteria
- verification commands
- rollback notes

## Stage gates

### Stage 0 — Kernel alive, stable, dreaming ✅ (Complete)

**Goal:** A running kernel that can persist state, generate nudges, and produce daily/weekly reflections with governance constraints.

**Evidence:** `docs/archive/STAGE_0_COMPLETION.md`, `tests/test_stage0_alive.py`, exports under `exports/`.

**Exit criteria (historical — Stage 0's original 2025-10-30 bar, not current product direction):**
- Kernel lifecycle start/stop cleanly.
- Water logging works. *(Note added 2026-07-28: hydration/water logging was Stage 0's original,
  simplest example feature and is not part of current active product direction or
  `ExperienceKernel`'s governed drives — see `CONSTITUTION.md`'s consumer-value gate. The
  underlying endpoints/table/UI panel still exist in code as a legacy, working feature; removing
  them is an unapproved future code-cleanup decision, not scheduled ahead of current architectural
  work. This line records what Stage 0 verified historically; it is not forward guidance.)*
- Nudge pipeline persists and respects cadence/quiet-hours.
- Daily + weekly reflection generation persists + exports.

**Verify:**
```bash
pytest -q -m smoke || pytest -q tests/test_stage0_alive.py
```

---

### Stage 0.5 — Packaging & Architecture Fixes ✅ (Complete 2026-07-20)

> **Source:** Cline audit 2026-01-22 verifying ChatGPT repo analysis

**Goal:** Ensure the package is installable, dependencies are canonical, and kernel runs headless without blocking on stdin.

**Scope:**
- Add missing `bartholomew/__init__.py` for package discoverability
- Consolidate dependencies in `pyproject.toml` (add `numpy`, `cryptography`, `typer`, `rich`)
- Fix malformed `safety.audit` rule in `memory_rules.yaml` to use `match:`/`metadata:` schema
- Refactor `input()` out of `bartholomew/kernel/memory/privacy_guard.py`

**Exit criteria:**
- `pip install -e .` succeeds; `python -c "import bartholomew"` works
- `pyproject.toml` contains all runtime deps; `requirements.txt` mirrors or is deprecated
- All memory_rules.yaml rules use consistent `match:`/`metadata:` schema
- No `input()` calls in kernel code; consent handled via event bus

**Verify:**
```bash
pip install -e .
python -c "import bartholomew"
grep -r "input(" bartholomew/kernel/ | grep -v test  # should be empty
pytest -q tests/test_memory_rules.py  # all rules parse
```

**Rollback:**
```bash
git checkout -- bartholomew/__init__.py pyproject.toml bartholomew/config/memory_rules.yaml bartholomew/kernel/memory/privacy_guard.py
```

**Evidence:** all four exit criteria verified against a clean venv on 2026-07-20 (see
`MASTER_PLAN.md` P0 items 0–3 for per-item notes). Two gaps found during verification,
carried forward rather than fixed here:
- The dependency audit that scoped this stage missed `jsonschema`, `requests`, and
  `pydantic[email]` — also added, but the underlying process gap (nothing catches
  undeclared imports until a fresh install fails) was unaddressed. **Closed by Phase A
  (2026-07-27, `8b96319`):** `tests/smoke/test_packaging_contract.py` now fails CI on any
  undeclared third-party runtime import, and `ci.yml`'s `quality` job installs from declared
  dependencies only, so a missing declaration fails a pull request rather than a user's first
  clean install. The gap had in fact bitten twice more in the interim — an undeclared
  `nest_asyncio` on the sensitive-memory write path and an undeclared `pytest-cov` that made
  the old `tests.yml` unable to pass at all.
- `identity_interpreter/adapters/consent_terminal.py` has the same blocking-`input()`
  shape as the fixed `privacy_guard.py`; out of this stage's stated scope
  (`bartholomew/kernel/` only) but should get the same fix.
- No automated "all `memory_rules.yaml` rules parse" test was added (the stated `pytest -q
  tests/test_memory_rules.py` verify command references a test file that doesn't exist);
  verified instead with a one-off script.

---

### Stage 1 — Console/UI integration ✅ (Complete 2026-08-05: sub-stages S1.0–S1.6 all implemented)

**Status:** Stage 1 is a console/UI product slice, staged as sub-stages **S1.0–S1.6** — mirroring
Phase B's B0–B9 staging, since this slice's combined exit criteria are comparably broad. See
`docs/STAGE_1_OVERVIEW.md` for each sub-stage's purpose, scope, and deferrals; this section of
`ROADMAP.md` remains the canonical source for Stage 1's overall exit criteria and approval
boundaries — `docs/STAGE_1_OVERVIEW.md` is subordinate to it. **S1.1 (Parking Brake API + UI), S1.3
(notification settings + mute/quiet-hours), and S1.5 (governance audit/provenance view) are
implemented (2026-08-01); S1.2 (consent/approval inbox) is implemented (2026-08-04); S1.4 (the
`awaiting_response` queue) is implemented (2026-08-05), per the explicitly approved
`docs/S1_4_AWAITING_RESPONSE_DESIGN.md` design pass; S1.6 (host-device onboarding guidance) is
implemented (2026-08-05), per the explicitly approved `docs/S1_6_HOST_DEVICE_ONBOARDING_DESIGN.md`
design pass (itself revised per reviewer feedback — user-experience framing per target, future
upgrade paths, and a priority-conditional "How should I choose?" section — before approval).** Each
sub-stage's plan and implementation diff was separately and explicitly approved, per
`docs/STAGE_1_OVERVIEW.md`'s non-negotiable invariants; approving one never implicitly approved the
next. **Scope updated 2026-07-28** (planning
only): Stage 1 is sequenced *before* Stage 5/S5.1 (see "Near-term milestone plan" above) because
Stage 5's live proactive behaviour needs a real, user-facing governance surface that only Stage 1
can provide.

**Adjacent, non-Stage-1 fix (2026-08-03):** a standalone consent-handler fix (see
`docs/STAGE_1_OVERVIEW.md`'s "Standalone: consent-handler fix" section) — sensitive-content memory
writes are now queued for review instead of silently discarded when no consent handler is
registered (the real headless/API case). S1.2 (2026-08-04) closed the separate,
previously-unfixed gap in `memory_rules.yaml`'s `ask_before_store` category, reusing the same
`pending_sensitive_writes` inbox this fix built rather than a parallel one.

**Goal:** A minimal consumer web governance shell on top of the API bridge, consistent with the
hybrid local-first deployment architecture (`DECISIONS.md`) — browser-based, reaching the trusted
local Bartholomew runtime — that can, at minimum:
- display current state (nudges, last reflections) and overall system status
- acknowledge/dismiss nudges; trigger reflections (dev/testing)
- provide **parking-brake access** (view/engage/disengage, by scope)
- provide a **consent and approval inbox** (pending "ask"-level permission requests, memory-consent
  prompts)
- provide **notification settings** and **mute / quiet-hours controls**
- provide an **awaiting-response queue** (see `COGNITIVE_RUNTIME.md`'s `awaiting_response` state) —
  matters the user or Bartholomew is waiting on a reply for, so they aren't silently forgotten
- provide relevant **audit and provenance visibility** (who/what approved a given action and when)

**Also in scope:** host-device onboarding guidance — during setup, show the user the realistic
advantages and limitations of running the trusted local Bartholomew runtime on a phone, a personal
computer, a home server/hub, a hosted cloud service, or a hybrid local-plus-cloud deployment (see
`DECISIONS.md`'s deployment-architecture entry for the approved direction this onboarding must be
consistent with).

**Constraints:**
- Must honor parking brake and consent gates.
- Must not widen tool surface without governance review.
- Must not expose remote/cross-device access to the local runtime until authentication,
  authorization, transport security, and a threat model are separately designed and approved (see
  `DECISIONS.md` and `ASSUMPTIONS.md`) — a Stage 1 shell is not itself an authentication project.

**Exit criteria:**
- API endpoints stable and documented.
- Basic UI/console can safely perform: list/ack/dismiss nudges; fetch latest reflections;
  engage/disengage the parking brake by scope; view and resolve pending consent/approval requests;
  set mute/quiet-hours/notification preferences; view the awaiting-response queue; view audit
  history for a given action.
- Host-device onboarding presents the trade-offs above without recommending a specific device
  as though it were the only supported option.
- No “Act” capability beyond these actions.

**Verify:**
```bash
pytest -q tests/test_orchestration_integration.py
pytest -q bartholomew_api_bridge_v0_1/tests/test_sqlite_wal_api.py
# S1.1 Parking Brake API + UI (S1.5 audit view lives in the same test files)
pytest -q tests/test_governance_api.py tests/test_governance_store.py
# S1.3 Notification settings + mute/quiet-hours
pytest -q tests/test_notify_skill_settings.py tests/test_notifications_api.py
# Consent-handler fix (adjacent, non-Stage-1) + S1.2 consent/approval inbox
# (shares the pending_sensitive_writes inbox and tests/test_consent_api.py)
pytest -q tests/test_memory_store_sensitive_consent.py tests/test_memory_store_rule_consent.py \
  tests/test_consent_api.py tests/test_consent_gates.py tests/test_consent_bypass_redteam.py \
  tests/test_retrieval_consent_enforcement.py
# S1.4 awaiting_response queue
pytest -q tests/test_awaiting_response_store.py tests/test_runtime_contract_awaiting_response.py \
  tests/test_awaiting_response_api.py tests/test_scheduler_drive_convergence.py
# S1.6 host-device onboarding guidance
pytest -q tests/test_onboarding_api.py
# optional smoke
bash bartholomew_api_bridge_v0_1/scripts/curl_smoke.sh
```

---

### Stage 2 — Governance hardening + memory stack (Phases 2A–2D)

**Goal:** Redaction, encryption, summarization, embeddings, consent gates, retention, and retrieval modes are reliable and testable.

**Sub-gates:**
- **2A** Redaction correctness
- **2B** Encryption envelope round-trip + key handling
- **2C** Summarization (fallbacks, truncation, sensitive handling)
- **2D** Embeddings lifecycle + vector store + retrieval integration
- **2E** FTS + hybrid retrieval (with graceful fallbacks)
- **2F** Chunking (ingest + retrieval + snippet assembly)

**Exit criteria (minimum):**
- P0 failing tests identified in `docs/archive/STATUS_2025-12-29.md` are green on Linux CI.
- Explicit retriever modes behave correctly (`vector`, `fts`, `hybrid`).
- Consent gates applied by default at the lowest layer.
- Metrics registry is idempotent.

**Verify (Linux CI baseline):**
```bash
ruff check .
black --check .
pytest -q
```

---

### Stage 3 — Unified Persona Core (Experience Kernel) — largely done; gaps closed 2026-07-20

**Correction (2026-07-20):** this section previously described Stage 3 as future/not-started. It
was stale — `bartholomew/kernel/experience_kernel.py`, `narrator.py`, `global_workspace.py`,
`working_memory.py`, `persona_pack.py` are already implemented and wired into `daemon.py`, with
~320 existing tests across 7 test files (all passing). See `MASTER_PLAN.md`'s "Experience Kernel
MVP: bug fix + privacy gap" section for what was actually found and fixed this round: a
silently-swallowed `AttributeError` that disabled the tick loop's affect decay / persona
auto-activation / planner calls since Stage 3 landed, and a privacy gap (this section's own
"must preserve consent gates, privacy redaction" constraint below wasn't actually implemented for
this subsystem — episodic entries and self-model snapshots bypassed `ConsentGate`/
`memory_rules.py`/`redaction_engine.py` entirely).

**Goal:** Bartholomew behaves like one continuous “self” with an Experience Kernel (self-model + narrator) and configurable persona packs, without expanding the action surface.

**Constraints:**
- No new real-world “Act” powers.
- Must preserve consent gates, privacy redaction/encryption, and auditability.

**Exit criteria:**
- Experience Kernel MVP wired into the loop (self snapshot + narrator reflections). ✅
- Persona packs switchable via config/UI and recorded in audit logs. ✅ (`persona_pack.py`,
  `PersonaPackManager`; not independently re-verified against this exact criterion this round)
- New unit + integration tests for kernel/persona. ✅ (already exist, see correction above)
- A dedicated "scenario replay" test — ✅ added 2026-07-21 (`tests/test_scenario_replay.py`;
  see `MASTER_PLAN.md` item 11.9). Found and fixed a real restart-persistence bug in the
  process: `ExperienceKernel` state (goals/affect/attention/drives) was never actually
  restored on daemon restart despite a log line claiming it was.
- The two reflection pipelines (`daemon.py`'s `ReflectionGenerator` vs. `narrator.py`'s
  episodic-narrative generators) — ⚠️ **partially addressed, not fully unified.** 2026-07-21:
  `daemon.py`'s daily/weekly reflection generation was changed to append `narrator.py`'s real
  episodic-narrative output alongside `ReflectionGenerator`'s own content (`MASTER_PLAN.md` item
  11.8), replacing a placeholder that had never run at all. **Corrected 2026-07-28: this is
  concatenation, not architectural unification.** Both pipelines still execute independently and
  neither is the codebase's enforced single authority. See `COGNITIVE_RUNTIME.md`'s "Reflection
  ownership" section for the approved target architecture (`ReflectionGenerator` authoritative,
  `NarratorEngine` supplementary) and the tracked implementation gap between that target and the
  current appending behaviour. **Stage 5 live proactive reflection behaviour remains blocked until
  a separately authorised code change makes the implementation conform to the approved ownership
  model and tests verify it** — this exit criterion is not satisfied by concatenation alone.

**Verify:**
```bash
pytest -q tests/test_experience_kernel.py
pytest -q tests/test_persona_pack.py   # this doc previously cited a nonexistent
                                        # tests/test_persona_switching.py -- corrected 2026-07-20
```

---

### Stage 4 — Modularity: Skill registry + starter skills — done (2026-07-21)

**Goal:** Standardize skills as installable modules with explicit manifests, permissions, and test expectations.

**Exit criteria:**
- Skill manifest schema defined + enforced.
- Registry can list/load skills; permission model applied.
- Starter skills working end-to-end: tasks + notify + calendar draft.

The registry, manifests, and starter skills were already fully built and
unit-tested, but disconnected from the live daemon (`Planner` never called
into `SkillRegistry`, `KernelDaemon` never constructed one). Wired up, plus
a parking-brake check, "ask"-consent resolution for `calendar_draft`, and
a dedicated `skill_action_audit` trail -- see MASTER_PLAN.md's "P2
investigation & wiring" write-up for details.

**Verify:**
```bash
pytest -q tests/test_skill_registry.py
pytest -q tests/test_end_to_end_tasks_and_audit.py
```

---

### Stage 4.5 — Runtime Convergence (architectural prerequisite) ✅ (Complete 2026-07-24)

**Goal:** Close the gap a grounded architectural audit found (2026-07-21): the project
effectively has "two brains" (`bartholomew/kernel` and `identity_interpreter/`) with four
duplicated concepts (model routing, persona, permission gates, kill-switch), `Identity.yaml`
governing only the chat path, and a fully-built Experience Kernel/Narrator/Working Memory
stack ("Living Device" continuity) that chat never reaches. See MASTER_PLAN.md's "P2.5 —
Runtime Convergence" for the full narrative, governing principles (Principle Zero, Principle
One — Uniform Cognition, the Architectural Invariant), and the Runtime Contract.

**Exit criteria (the Runtime Convergence Exit Gate — all seven must be "yes"):**
- Can every input source create an Observation?
- Does every proposed action pass through the Executive?
- Does every execution pass through the same Governance path?
- Does every completed action produce a Reflection?
- Does every Reflection update Memory?
- Does every conversation see the Experience Kernel?
- Does every interface expose the same personality?

**Scope:**
- One authoritative owner per architectural concept; the four duplicate pairs marked
  deprecated (not deleted) and routed through the winner.
- Identity Context -> Executive -> Policy Decision (Identity stays declarative; the Executive
  constructs the executable decision).
- The Runtime Contract's pipeline (Observation -> Interpretation -> Executive -> Governance ->
  Capability -> Execution -> Reflection -> Memory) becomes a real code seam for chat +
  skill-execution.
- Chat wired into the Experience Kernel.
- `COGNITIVE_RUNTIME.md` authored as the canonical "how does Bartholomew think" document. —
  ✅ done 2026-07-21. Its own "Exit Gate status" table is the honest, continuously-updated
  scorecard. As of item 11.21 (2026-07-24), **all five live surfaces** — chat, skill execution,
  scheduler drives, *and* voice/sight — construct an Observation/CandidateAction that genuinely
  drives the Governance decision (not just constructed and discarded — proven per-surface, and for
  voice/sight by `tests/test_voice_sight_runtime_contract_seam.py` including deliberate
  gate-neutralisation non-vacuity controls) and pass through the shared Governance path. Voice/
  sight additionally require a fail-closed device consent gate; their capture/stream capability
  stays an inert Stage 6 placeholder reachable only through the governed seam. See
  `COGNITIVE_RUNTIME.md`'s Exit Gate table for the live, per-question status rather than restating
  it here.

**Status (2026-07-24): complete.** All seven Exit Gate questions are satisfied within Stage 4.5's
scope. Questions **#1–#6 are "yes"** for every surface that exists today (item 11.21 closed the
last current-production governance gap, voice/sight). Question **#7 (personality uniformity) is
"yes" within Stage 4.5's scope**: every personality-bearing interface (chat, CLI `explain`,
`chat.py`) sources persona from the single authority (`PersonaPackManager`); the `Identity.yaml`
`traits` split is deliberate-by-design, not a convergence gap. Q7's only residual — voice/sight
consulting persona — was **formally reclassified to Stage 6** (item 11.22, 2026-07-24), because a
surface producing no persona-bearing output cannot expose a personality until Stage 6 builds that
output; it is a Stage 6 dependency, not a Stage 4.5 deliverable left undone. No official exit
criterion is left partial. Real voice/sight functionality (capture, streaming, transcription,
sessions, persona output) remains Stage 6.

**Note:** Stage 5 (below) was recommended to wait until this stage's exit gate is fully green.
With all seven questions now satisfied within scope, that prerequisite is met — though pausing/
resuming P3 (Stage 5) still requires separate, explicit user sign-off, which this completion does
not itself grant.

---

### Stage 5 — Initiative engine (scheduled check-ins + workflows) 🚧 S5.1 design approved

**Status as of 2026-08-06:** Stage 5 is now staged **S5.0–S5.7**, mirroring Stage 1's S1.0–S1.6
and Phase B's B0–B9, per `docs/S5_1_INITIATIVE_ENGINE_ARCHITECTURE_DESIGN.md` (approved
2026-08-06). **S5.1's architecture is approved; no implementation exists yet for S5.1 or any later
sub-stage** — approving one sub-stage never implicitly approves the next, same as every other
staged workstream in this document.

| Stage | Objective | Status |
|---|---|---|
| **S5.0** | Scheduler-schema readiness (closes issue #24) | ✅ done 2026-07-25, PR #25 |
| **S5.1** | Initiative Engine architecture | ✅ design approved 2026-08-06 — `docs/S5_1_INITIATIVE_ENGINE_ARCHITECTURE_DESIGN.md`; not yet implemented |
| **S5.2** | Typed cadence | proposed 2026-08-06 — `docs/S5_2_TYPED_CADENCE_DESIGN.md`; not approved |
| **S5.3** | Default-off consent + functional mute | not started |
| **S5.4** | Quiet-hours defer | not started |
| **S5.5** | Dry-run mode | not started |
| **S5.6** | Structured rationale logging | not started |
| **S5.7** | Live check-in / weekly-review / next-best-action drives under `allow_proactive` | not started |

**S5.1 in brief:** a generic `Initiative` object (kind/category/status/priority/confidence/
rationale) that every future proactive behaviour instantiates instead of each getting its own
feature-specific scheduler, owned by the Kernel Executive (see `COGNITIVE_RUNTIME.md`'s ownership
table) via a new `run_initiative_through_runtime_contract()` seam mirroring
`run_awaiting_response_through_runtime_contract()`'s shape. Covers the lifecycle state machine, a
proactive-intent classification step ahead of Governance, three independent governance gates (a
dedicated `"initiative"` Parking Brake scope, a default-deny `allow_proactive` Identity Policy
category, and a default-off per-category user-consent table), mandatory audited rationale, and
reserved (not yet implemented) schema support for initiative dependencies and hierarchical
parent/child initiatives. Does not touch the already-shipped `awaiting_response_store.py`, and
does not close the reflection-ownership gap below (blocks only a future `review` initiative kind
specifically).

**Status as of 2026-07-27 (superseded by the above, kept for record):** No Stage 5 feature code
existed beyond S5.0 — no typed cadence, no proactive consent or mute, no quiet-hours defer, no
dry-run mode, no structured rationale logging, and no `allow_proactive` governance category.
**Sequencing corrected 2026-07-28:** Stage 5 requires Stage 1's minimal consumer web governance
shell (parking-brake access, consent/approval inbox, mute/notification controls,
awaiting-response queue) to exist and be proven working before live proactive delivery is
permitted — see "Near-term milestone plan" above. **Both of Stage 5's named prerequisites are now
satisfied**: Stage 1 shipped 2026-08-06 (PR #38), and `COGNITIVE_RUNTIME.md`'s Runtime Contract
Exit Gate is "yes" on all seven questions. Live proactive *reflection* behaviour specifically
remains blocked on the reflection-ownership implementation gap tracked in `COGNITIVE_RUNTIME.md`
(current concatenation vs. approved `ReflectionGenerator`-authoritative target) until that gap is
closed by a separately authorised code change with verifying tests — this blocks only a `review`-
category initiative kind (S5.7+), not S5.1's architecture or S5.2's typed cadence.

**Goal:** Proactive suggestions and check-ins that are safe, useful, and not naggy.

**Prerequisite — S5.0 (closes issue #24):** deterministic scheduler-schema readiness at startup —
`KernelDaemon.start()` ensures the scheduler tables synchronously (fail-closed) before returning,
so Stage 5's proactive drives and their user-visible state are not built on nondeterministic
scheduler initialization. ✅ merged 2026-07-25, PR #25, merge commit `3496cfb`; issue #24 is
confirmed closed. Proven by `tests/test_scheduler_startup_readiness.py` (10 tests) on the
3.10 + 3.11 matrix. See MASTER_PLAN.md's P3 "S5.0" note and DECISIONS.md.

**Sequencing (locked):** safety scaffolding lands before any live proactivity — typed cadence
(interval / daily / weekly wall-clock, S5.2) → **default-OFF** consent + **functional mute**
(S5.3) → quiet-hours *defer* (not suppress, with coalescing/expiry, S5.4) → dry-run (S5.5) →
structured rationale logging (S5.6) → *then* live check-in / weekly-review / next-best-action
drives under a default-deny `allow_proactive` governance category (S5.7; suggestion-only,
brake-blocked, excluded from `tool_use`, no self-maintenance exemption). Default-off consent and
working mute are prerequisites for live delivery, not later enhancements. S5.1's Initiative Engine
architecture underlies every one of these — see above.

**Exit criteria:**
- Scheduler runs check-ins (morning/evening) and weekly review in dry-run + live.
- Quiet-hours respected; parking brake scope coverage tested.
- Suggestions logged with rationale; user can mute/adjust cadence.

**Verify:**
```bash
pytest -q tests/test_scheduler_checkins.py
```

---

### Stage 6 — Distributed being (cross-device) + voice adapters

**Goal:** Same Bartholomew across devices with secure auth and optional voice, consistent with the
hybrid local-first deployment architecture (`DECISIONS.md`): the trusted local runtime remains
authoritative for sensitive memory and governance; cross-device access is an explicitly-designed,
threat-modelled extension of it, not a default assumption.

**Exit criteria:**
- **Auth updated 2026-07-28:** remote/cross-device exposure of the local runtime must not occur
  until authentication, authorization, transport security, and a threat model are designed and
  separately approved — a "simple token auth" scheme is explicitly **not** assumed sufficient (see
  `ASSUMPTIONS.md` and `DECISIONS.md`). "Token auth" alone is not an acceptable exit criterion;
  the exit criterion is a reviewed threat model plus an implementation that satisfies it.
- Cross-device client shows same timeline/state once that auth work is approved and implemented.
- Voice endpoints degrade gracefully when binaries missing.

**Carried-forward requirements from item 11.21 (voice/sight governance seam):**
- Real capture/streaming/transcription/computer-vision/device-driver work slots *into* the
  existing governed seams (`run_voice_/run_sight_through_runtime_contract()`), which already
  construct the Observation/CandidateAction and run brake + Identity Policy + fail-closed device
  consent. Do not add a second, ungoverned capture path — the inert `_perform_stream`/
  `_perform_capture` placeholders are the slot.
- Governance approval at the seam authorizes a **single start attempt** only. Continuous sessions,
  consent renewal, and revocation are new mechanisms to design here — not an extension of the
  single-start grant.
- **Safety invariant:** safely stopping or tearing down an active capture session must NEVER
  depend on obtaining permission to *continue* capturing. Teardown is not a governed "start" and
  must not be gated as one (a stuck consent/policy path must not be able to trap the device "on").
- **Jurisdiction-aware capture/recording compliance (added 2026-07-28, per `CONSTITUTION.md`'s
  capture-and-recording-safety invariant):** before any real microphone/camera/public-recording
  capability ships here, the design must account for whether recording is legal in the current
  jurisdiction, whether consent or notice is required, whether audio and video rules differ,
  retention limitations, deletion/revocation, public-vs-private environments, and a changing
  jurisdiction while travelling. This is design scope for Stage 6, not implemented yet.
- **Personality uniformity for voice/sight (reclassified here from Stage 4.5 Exit Gate question
  #7, item 11.22, 2026-07-24):** once voice/sight produce persona-bearing output, that output must
  source persona from the single authority (`PersonaPackManager`'s active pack), the same way the
  text interfaces already do — so Bartholomew presents "one personality, not one per interface"
  across voice/sight too. This could not be done in Stage 4.5: a surface with no persona-bearing
  output has no personality to converge. Satisfying it is part of building real voice/sight
  functionality here; it closes the last residual of Exit Gate question #7.

**Also in scope for Stage 6 (added 2026-07-28, design-only):**
- **Data portability/export delivery** — implement the export guarantee recorded in
  `CONSTITUTION.md` (memories, preferences, personal model, identity/governance settings,
  provenance, approvals/audit history, active goals and unresolved matters) as an actual,
  user-triggerable feature. Cross-device sync work in this stage should not ship without this,
  since portability is meant to prevent lock-in, not just describe an intention.
- **Adaptive notifications / awaiting-response delivery beyond the Stage 1 baseline** — Stage 1
  ships the minimal mute/notification-settings/awaiting-response-queue controls; genuinely adaptive
  notification behaviour (adapting to subject matter, urgency, time sensitivity, risk, user
  preferences, current context, and previous responses, per `CONSTITUTION.md`) is design/build
  scope here, once cross-device delivery exists to adapt across.

**Verify:**
```bash
pytest -q tests/test_cross_device_auth.py
pytest -q tests/test_voice_adapters.py
```

---

### Stage 7 — Embodiments (future)

**Goal:** Car mode, gaming overlays, smart home control — strictly gated, privacy-reviewed, and incrementally enabled.

**Exit criteria:**
- Each embodiment has: interface spec, threat model, consent model, and replay tests.

---

## Echo ideas — moved off the canonical roadmap (2026-07-28)

The brainstorm-derived "Echo" feature set (45 features across 4 conceptual gates: agent kernel,
gaming/device-identity, cross-device/smart-home/car-mode, and marketplace/ecosystem) previously
lived here as "Echo Integration Gates." It has been moved to
**[docs/incubator/ECHO_IDEAS.md](docs/incubator/ECHO_IDEAS.md)**, which is explicitly
non-canonical and non-authoritative, because embedding a second agent kernel (LangGraph), a second
memory architecture (Chroma+RAG), and a second permissions system as canonical roadmap content
directly conflicted with `CONSTITUTION.md`'s "one architectural authority per concept" principle.
Every individual idea in that document requires independent evaluation against `CONSTITUTION.md`,
`COGNITIVE_RUNTIME.md`'s ownership table, and the hybrid local-first deployment architecture
(`DECISIONS.md`) before any adoption — none of it is scheduled, approved, or a stage gate.

---

## Near-term milestone plan (recommended)

> **Updated:** 2026-07-28 (planning-document reconciliation, approved sequencing). Supersedes the
> 2026-07-27 ordering below, which listed Phase B, then Stage 5/S5.1, then Stage 1. That ordering
> is corrected: Stage 1 (a minimal consumer web governance shell) is sequenced **before** Stage
> 5/S5.1, because Stage 5's live proactive behaviour requires a user-facing governance surface
> (parking brake, consent/approval inbox, mute, notification controls, awaiting-response queue)
> that does not exist until Stage 1 ships. **This is a planning/sequencing decision only — it does
> not authorise implementation of any step below.** Each step still requires its own separate,
> explicit approval before work begins.

**Approved sequence (nothing is in flight; each numbered step requires its own separate approval):**

1. **Documentation reconciliation and the deployment-architecture decision** (this pass). See
   `DECISIONS.md`'s "Deployment architecture: hybrid local-first" entry.
2. **Plan Phase B one stage at a time**, beginning with B0 only after separate, explicit approval
   to plan it — persistence-ownership stabilisation, against the approved hybrid local-first
   architecture. See the Phase B workstream section above, `docs/PHASE_B_OVERVIEW.md`, and
   `docs/PHASE_B_RISK_MAP.md`. No stage's plan approval authorises any other stage's plan or
   implementation.
3. **Implement each Phase B stage**, only after separate, explicit approval of that stage's own
   plan.
4. **Build a minimal Stage 1 consumer web governance shell**, only after separate, explicit
   approval. At minimum this shell must eventually provide: parking-brake access; system status;
   a consent and approval inbox; notification settings; mute and quiet-hours controls; an
   awaiting-response queue; and relevant audit/provenance visibility (see Stage 1's own section
   above for the full exit-criteria treatment once scoped).
5. **Add Stage 5 safety scaffolding and dry-run behaviour**, only after separate, explicit
   approval. The locked internal sequence (typed cadence → default-off consent + functional mute
   → quiet-hours defer → dry-run → rationale logging) is recorded in the Stage 5 section above and
   is unchanged by this reordering.
6. **Permit live proactive Stage 5 behaviour** only after the Stage 1 user-facing governance
   controls above have shipped and are proven working — not merely designed. This is the concrete
   reason Stage 1 now precedes Stage 5 in this sequence: users need a real governance surface
   before Bartholomew begins live proactive intervention.

**Also open but unscheduled** (each requiring separate approval, not part of the sequence above):
issue #22 (forward `IdentityContext` through the voice/sight compat wrappers, deferred to Stage 6);
Phase A's deferred findings F9–F11 (`RISKS.md`); jurisdiction-aware capture/recording work (Stage
6/7, see `ROADMAP.md`'s Stage 6/7 sections); adaptive-notification and awaiting-response delivery
work beyond the Stage 1 shell's baseline controls; data-export/portability delivery (see the Stage
1/6 notes added 2026-07-28); host-device onboarding guidance (Stage 1, see above).

**Historical (2026-07-27 ordering, superseded by the sequence above — kept for record):**

1. **Phase B — persistence ownership stabilisation.** Proposed next engineering work; not
   approved. See the workstream section at the top of this document.
2. **Stage 5 / S5.1 — initiative engine.** Paused. The locked sequence (safety scaffolding
   before live proactivity) is recorded in the Stage 5 section above.
3. **Stage 1 — console/UI slice.** A deferred product slice: not started, and never a
   prerequisite for Stages 2–4.5.

**Historical (2026-01-22 Cline audit plan — all items done or superseded):**

1. ~~**Stage 0.5: Packaging & Architecture Fixes**~~ — done 2026-07-20.
2. ~~**Linux CI green for P0 core**~~ — done; superseded by Phase A's automatic, cross-platform CI.
3. ~~**Fix P0 logic bugs**~~ (summarization/encryption/embeddings/retrieval factory/metrics
   idempotency) — done 2026-07-20 (38 → 0 sweep, MASTER_PLAN.md).
4. **Quarantine or parameterize platform-specific tests** — *not done, and deliberately not done
   that way.* Phase A took the opposite approach: rather than quarantining Windows behaviour, it
   added a Windows CI job and a test asserting the exact handle-release property that fails first
   under Windows locking. No quarantine list was ever created (see `ASSUMPTIONS.md` A1).
5. **Stage 1 UI/console slice** — a deferred product slice; not started, and never a
   prerequisite for Stages 2–4.5 (carried forward above).

## What we will not do yet

- Expand automation/tooling surface without governance + test coverage.
- “Act” features without parking-brake, consent, audit, and rollback.
