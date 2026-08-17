# ROADMAP

> Milestones and stage gates with explicit exit criteria.
>
> **Last updated:** 2026-08-17 — two changes, neither altering any stage's scope, sequencing or
> status. **(1)** One line added to "What we will not do yet": native device agents and a
> device-capability protocol are **FUTURE PLATFORM WORK** under `DECISIONS.md`'s new server-centric
> deployment entry — TARGET architecture, nothing of it exists, and the Usable POC / TILT priority
> is explicitly unchanged. **(2)** A stale-status correction in the Phase B findings list: the two
> reflection pipelines are recorded as **resolved** (`8d87258`) rather than "partially addressed",
> and the separate repair of the reflection *model* path is noted — that text still said Stage 5
> live proactive reflection was blocked on a code change that had already landed.
>
> **Previously (2026-08-15):** one line added to "What we will not do yet": multi-user/tenancy/
> platform infrastructure is **FUTURE PLATFORM WORK**, explicitly out of current scope. This is a
> scope *guard*, not a scope change — **no stage's exit criteria, sequencing, or status changed**,
> and Stage 6's cross-device auth/threat-model work is unchanged. See `CONSTITUTION.md`'s new "One
> Platform, Many Personal Bartholomews" section and `DECISIONS.md`'s "One shared Bartholomew
> platform; many strongly isolated personal Bartholomew identities" entry.
>
> **Previously (2026-08-14):** **Usable POC slice 1 (Personal Memory Capture and Recall) is
> implemented**, commit `2d443a9`, approved on review. Ordinary conversation now produces durable,
> retrievable memory through the existing governed write path, chat retrieval sees it, and the
> `notify` skill has a real outbound delivery channel — closing the three gaps the 2026-08-12
> assessment identified. See the "Usable POC — progressive vertical slices" section below for the
> completion record, and `docs/POC_SLICE_1_MEMORY_CAPTURE_RECALL.md` for the as-implemented detail
> and known limitations. **Slice 1's completion does not authorise slice 2** — per `docs/TILT.md`
> the next step is real-world use of this slice, and slice 2 is scoped from that feedback.
> Stage 5 S5.4–S5.7 remain deferred, unchanged by this pass.
>
> **Previously (2026-08-12, two changes, same pass): (1)** a stale-status correction — **S5.3,
> Executive competency reasoning, is complete**, merged via PR #45 (merge commit `a4f094b`, CI run
> #65 green on all six jobs); this document had not been updated since S5.3's design approval and
> still read "not started" after implementation landed. See the corrected Stage 5 table and
> narrative below. **(2)** the **Usable POC / time-to-real-use prioritisation** is approved — see
> the new "Usable POC — progressive vertical slices" section below and the canonical
> `docs/TILT.md`. A repository-grounded assessment found that, despite S5.1–S5.3 being genuinely
> well-built, ordinary conversation still writes nothing durable and retrievable, so none of it has
> yet generated real usage feedback. **Stage 5 S5.4–S5.7 are deferred, not abandoned** — resequenced
> to follow the Usable POC's first vertical slice (Personal Memory Capture and Recall) rather than
> precede it. This is a **documentation-only** pass; no implementation is authorised by it beyond
> the planning/documentation work itself.)
>
> **Previously (2026-08-11, second pass, same day): S5.2 — Training and knowledge
> acquisition — is complete and merged.** PR #43, merge commit `5dacb52`; CI run #61 green on all
> six jobs. Delivered per the explicitly approved
> `docs/S5_2_TRAINING_KNOWLEDGE_ACQUISITION_DESIGN.md` in three separately reviewed steps, plus an
> approved correction to two pre-existing `privacy_guard.is_sensitive()` defects the integration
> surfaced. **No sub-stage beyond S5.2 is authorised, started, or reordered — S5.3 explicitly
> remains not started**, and the deferred items recorded in `RISKS.md` (FTS5 migration residual,
> the privacy_guard/`memory_rules.yaml` sensitivity-vocabulary disagreement) remain separate and
> unauthorised.)
>
> **Previously (2026-08-11):** status reconciliation against merged repository state, no new
> direction: **S5.1 is complete** — implemented 2026-08-10 (`e1277b7`, merged via PR #40) per the
> approved `docs/S5_1_COMPETENCY_ARCHITECTURE_DESIGN.md`, and marked `📋 not started` here until
> now purely because this document was last written before that merge. Stage 5's preamble sentence
> "No sub-stage of Stage 5 has started" is corrected for the same reason. S5.1's verify commands
> added below. **This is a fact-correction pass only:** no sub-stage beyond S5.1 is authorised,
> started, or reordered, and S5.1's completion does not authorise S5.2. See also `RISKS.md`'s
> updated FTS5 stale-index entry, which the same merge substantially — but not entirely —
> resolved.)
>
> **Previously (2026-08-08):** New Direction reconciliation: Stage 5 restructured from
> "Initiative engine" to "Developing Agency" — competency architecture (S5.1), training/knowledge
> acquisition (S5.2), Executive competency reasoning (S5.3), and the experience→learning loop
> (S5.4) now precede the pre-existing initiative safety scaffolding (S5.5), dry-run (S5.6), and
> live initiative (S5.7) work, which is preserved unchanged in substance. Added an "Estate
> Management as architecture acceptance test" subsection. See `DECISIONS.md`'s "Stage 5
> restructured around competency and training before live initiative" entry and
> `CONSTITUTION.md`'s "One Developing Digital Individual" section. No implementation authorised by
> this pass. **Same-day follow-up:** added personal / potentially-generalisable / system-level
> learning classification and provenance as explicit S5.1–S5.4 exit-criteria requirements, per
> `CONSTITUTION.md`'s "Personal learning vs. potentially generalisable and system-level learning"
> section and `DECISIONS.md`'s corresponding entry — a data-shape requirement only, not a
> cross-instance learning mechanism.)
>
> **Previously (2026-08-01):** B9 complete —  Phase B's final stage:
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

**Current execution priority (added 2026-08-12):** the stage gates below remain the architectural
record of what exists and why, but near-term sequencing is currently governed by
**[docs/TILT.md](docs/TILT.md)** — the canonical Usable POC / time-to-real-use priority. See the
"Usable POC — progressive vertical slices" section immediately below for how that priority applies
to this roadmap specifically.

## Usable POC — progressive vertical slices (established 2026-08-12)

> See `docs/TILT.md` for the full principle, the prioritisation test, and vertical-slice
> discipline; see `DECISIONS.md`'s "Usable POC / time-to-real-use prioritisation" entry for the
> assessment and rationale. This section is the roadmap-level summary and the interface between
> that priority and the stage gates below.

**Why this section exists.** A 2026-08-12 repository-grounded assessment found Bartholomew's
persistence, governance, and competency-retrieval machinery (Phase B, Stage 1, Stage 5 S5.1–S5.3)
genuinely well-built, but essentially unused in practice: ordinary conversation writes nothing
durable and retrievable, the one working retrieval seam (S5.3) only ever sees formally-trained
competency records, and the one notification mechanism has no delivery channel outside the browser
tab. Continuing down the pre-existing Stage 5 sequence (S5.4 → S5.5 → S5.6 → S5.7) would have kept
building architecture without putting real functionality in front of real use. This section
corrects that.

**The Usable POC is a progressive, end-to-end demonstration**, not a single deliverable:

```
real-world information/input
  -> Observation/Interpretation as appropriate
  -> persistent useful understanding/memory
  -> retrieval and reasoning
  -> useful Recommendation/proactive surfacing
  -> user interaction/approval where required
  -> at least one real governed Action
  -> visible real-world result
```

It is reached through small vertical slices, each shipped and tested in real use before the next
is scoped — not built end-to-end before any real-world testing begins.

**Slice 1 — Personal Memory Capture and Recall.** ✅ **done 2026-08-14** — commit `2d443a9`.
Scope, acceptance bar, and the vertical-slice discipline that governs it are in `docs/TILT.md`;
the planning note and the as-implemented record are in
`docs/POC_SLICE_1_MEMORY_CAPTURE_RECALL.md`. In one sentence: extend the existing consent-gated
write path and the existing S5.3 competency-retrieval seam so ordinary conversational facts (not
just formally-trained competency material) become durable, retrievable memory, plus one real
notification delivery channel.

**Slice 1 completion record.** Planning note approved 2026-08-14 (`4de2962`); implementation
approved separately and explicitly, then delivered in `2d443a9`. What shipped:

- **Capture** — a new pure-logic module (`bartholomew/kernel/personal_facts.py`) proposes durable
  personal facts found in a chat turn onto the **existing** `user_profile`/`user_schedule` kinds.
  Every proposal goes through `MemoryStore.upsert_memory()` unchanged: same rules engine, same
  `never_store` hard block, same `ask_before_store` → `pending_sensitive_writes` consent queue,
  same privacy guard. No new memory kind, no new write path, no bypass flags.
- **Recall** — the chat seam's retrieval filter widened to those kinds; S5.3's `select_relevant()`
  relevance gate reused unmodified. Facts and competencies are selected in two independent passes
  over the same retrieved candidates (the gate commits to one domain per selection, so a merged
  call would let a recalled fact evict an applicable competency) and render as separate prompt
  blocks.
- **Notification** — `NotifySkill._deliver_notification()`'s log-only stub replaced with a real
  provider-agnostic outbound HTTP POST to `BARTHOLOMEW_NOTIFY_WEBHOOK_URL`, run off the event loop
  per the B2/B8 discipline. Unset keeps the previous log-only behaviour exactly.
- **Governance preserved** — capture runs only inside the governance-allowed branch, so an engaged
  parking brake yields zero writes and zero consent-queue entries; consent-gated content is never
  stored, never recalled, and never quoted in an outbound notification or a Reflection; S5.3's
  Decision E exposure boundary and explanation-grade recording are inherited, not relaxed.
- **Tests** — 40 new across three files, including the acceptance bar (fact stated in one turn,
  relevantly recalled in a later separate turn without restating it) and a full
  chat → governed write → notify skill → real outbound HTTP chain against a loopback server.

Known limitations are recorded in the planning note's "As implemented" section and are accepted as
POC scaffolding per `docs/TILT.md` — tuned from real usage, not ahead of it.

**Slice 1's completion does not authorise slice 2.** Per `docs/TILT.md`, the next step is real-world
use of this slice; slice 2 is scoped from that feedback and requires its own explicit approval.

**Slice 1 is the first slice, not the POC's boundary.** Later slices are expected to progress
toward proactive surfacing of something noticed and at least one genuine governed action with a
visible real-world result, drawing on the real material already scoped in S5.4–S5.7 below —
right-sized the same way slice 1 is, and scoped from slice 1's real feedback rather than designed
ahead of it. See `docs/TILT.md`'s "Direction for later slices."

**Effect on Stage 5 S5.4–S5.7:** deferred, not abandoned. See each sub-stage's row in the Stage 5
table below and `docs/TILT.md`'s "What is deferred" section for the complete list and reasons.

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
  concatenation, not architectural unification.** **Resolved 2026-08-17** (change landed
  `8d87258`, 2026-08-16): `daemon.py` now collects the narrator's episodic material first and
  passes it into `ReflectionGenerator` as `episodic_evidence`, so a single authority composes a
  single document; `tests/test_reflection_ownership.py` and
  `tests/test_reflection_narrative_integration.py` verify it. **This exit criterion is
  satisfied and Stage 5 live proactive reflection behaviour is no longer blocked on it.**
  Separately, the reflection *model* path — which had never actually run a model, because
  `daemon.py` pinned `backend="stub"` and `ReflectionGenerator` could not be constructed on a
  headless host — was repaired on 2026-08-17 and is pinned by
  `tests/test_reflection_model_path.py`. See `COGNITIVE_RUNTIME.md`'s "Reflection ownership"
  section, which is the authority for both.

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

### Stage 5 — Developing Agency (competency, training, learning, then initiative) 🚧 IN PROGRESS (S5.0–S5.3 complete; S5.4–S5.7 deferred, see Usable POC section)

**Restructured 2026-08-08** (New Direction reconciliation — see `DECISIONS.md`'s "Stage 5
restructured around competency and training before live initiative" entry for full rationale).
Stage 5 was previously scoped as an initiative engine only (scheduled check-ins + workflows,
sub-staged S5.1–onward around proactivity safety scaffolding). That scope is **not discarded** —
it is preserved below as sub-stages **S5.5–S5.7** — but it is **resequenced to follow**, not
precede, the generic competency/training/learning architecture described in `CONSTITUTION.md`'s
"One Developing Digital Individual" section and `COGNITIVE_RUNTIME.md`'s "Competency, Training, and
Learning" section. Rationale in one sentence: `Planner.decide()` (the Executive's reasoning path)
returns `None` unconditionally today — there is no machinery yet for the Executive to retrieve and
apply competencies, so scheduling *when* to be proactive is premature before the Executive has
anything competent to be proactive *about*. **S5.0–S5.3 (runtime prerequisites, competency
architecture, training/knowledge acquisition, Executive competency reasoning) are complete.**

**S5.4–S5.7 are deferred as of 2026-08-12 — not started, and not next.** Per the Usable POC /
time-to-real-use prioritisation (`docs/TILT.md`, `DECISIONS.md`), the next work is the Usable
POC's first vertical slice (Personal Memory Capture and Recall — see the "Usable POC — progressive
vertical slices" section below), not a continuation of this sub-stage sequence. S5.4–S5.7 remain
real, approved-in-direction work and are **not abandoned**: later Usable POC slices are expected to
draw on this material (experience → learning loop, initiative safety scaffolding, dry-run, live
initiative) once real usage from the first slice informs what they should actually look like. See
`docs/TILT.md`'s "Direction for later slices" and "What is deferred" sections. Resuming any
sub-stage below still requires its own separate, explicit approval — neither restructuring the
sequence nor completing an earlier sub-stage authorises implementing any later one.

**Goal:** Bartholomew develops learned competence in genuine areas of responsibility (proven first
via Residential Estate Management — see "Estate Management as architecture acceptance test" below)
and, once that machinery exists and Stage 1's governance shell is proven working, becomes able to
initiate proactive suggestions and check-ins safely, usefully, and without being naggy.

**Sequencing (locked; supersedes the pre-2026-08-08 "safety scaffolding before live proactivity"
sequence below by inserting competency/training/learning work first, not by replacing it):**

| Sub-stage | Objective | Status |
|---|---|---|
| **S5.0** — Runtime prerequisites | Deterministic scheduler-schema readiness at startup (closes issue #24) | ✅ done 2026-07-25 |
| **S5.1** — Competency architecture | Define and implement the smallest generic competency data/contract model — knowledge areas, procedures, relevant capabilities, experience/evidence, proficiency/confidence, supervision requirements — as structured content in the existing shared Memory substrate, with no new memory authority, Executive, or Governance path. **Must also carry the personal / potentially-generalisable / system-level classification and provenance `CONSTITUTION.md`'s "Personal learning vs. potentially generalisable and system-level learning" section requires (added 2026-08-08) — not a cross-instance mechanism, just fields that don't foreclose one later.** See `CONSTITUTION.md`/`COGNITIVE_RUNTIME.md` for the shape this must take. | ✅ done 2026-08-10 |
| **S5.2** — Training and knowledge acquisition | Define and implement how training material (formal reference material, direct instruction, demonstration, correction, supervised-work outcomes) enters shared Memory with provenance and consent, per `CONSTITUTION.md`'s "Training vs. configuration." Delivered per `docs/S5_2_TRAINING_KNOWLEDGE_ACQUISITION_DESIGN.md` (design + Decisions A–E approved 2026-08-11, with the two future-facing constraints in §9.1). | ✅ done 2026-08-11 |
| **S5.3** — Executive competency reasoning | Extend `Planner.decide()` (today a stub returning `None`) so the Executive retrieves relevant competencies/knowledge/procedures for a given situation and constructs a `CandidateAction` informed by them and their confidence — through the existing Governance path, not a new one. Delivered per `docs/S5_3_EXECUTIVE_COMPETENCY_REASONING_DESIGN.md` (design and Decisions A–E approved 2026-08-11, including a same-day-added deterministic relevance floor, Decision E.2). Per Decision A, competency reasoning attaches to the **request-driven** (chat) path; `Planner.decide()` deliberately stays inert — reconnaissance found its only caller is an **ungoverned** proactive tick→bus→nudge path (§2.1). | ✅ done 2026-08-12 |
| **S5.4** — Experience → learning/consolidation loop | Implement the Experience → Reflection → candidate learning → provenance/confidence → Governance/review (where required) → consolidation loop described in `COGNITIVE_RUNTIME.md`. This is also where the pre-existing reflection-ownership implementation gap (`ReflectionGenerator` authoritative, `NarratorEngine` supplementary — see `COGNITIVE_RUNTIME.md`'s "Reflection ownership" section) must be closed, since S5.4 depends on reflection composition having a single authority. | 📋 **deferred** — see "Usable POC" section; expected to be informed by Usable POC slice feedback, not built ahead of it |
| **S5.5** — Initiative safety scaffolding | Typed cadence (interval / daily / weekly wall-clock) → **default-OFF** consent + **functional mute** → quiet-hours *defer* (not suppress, with coalescing/expiry). *(This is the beginning of the pre-2026-08-08 Stage 5 scope, preserved unchanged in substance, resequenced after S5.1–S5.4.)* | 📋 **deferred** — see "Usable POC" section |
| **S5.6** — Dry-run proactive reasoning | Dry-run mode; structured rationale logging for every would-be suggestion. | 📋 **deferred** — see "Usable POC" section |
| **S5.7** — Controlled live initiative | Live check-in / weekly-review / next-best-action drives under a default-deny `allow_proactive` governance category (suggestion-only, brake-blocked, excluded from `tool_use`, no self-maintenance exemption). | 📋 **deferred** — see "Usable POC" section |

Numbering is not load-bearing; the **dependency order** is: runtime prerequisites, then competency
architecture, then training/knowledge acquisition, then Executive competency reasoning, then the
experience/learning loop, then initiative safety scaffolding, then dry-run, then controlled live
initiative. Stage 5 also still requires Stage 1's minimal consumer web governance shell
(parking-brake access, consent/approval inbox, mute/notification controls, awaiting-response queue)
to exist and be proven working before **live** proactive delivery (S5.7) specifically — S5.1–S5.6
do not themselves deliver live proactive behaviour and are not gated on Stage 1.

**Prerequisite — S5.0 (closes issue #24):** deterministic scheduler-schema readiness at startup —
`KernelDaemon.start()` ensures the scheduler tables synchronously (fail-closed) before returning,
so Stage 5's proactive drives and their user-visible state are not built on nondeterministic
scheduler initialization. ✅ merged 2026-07-25, PR #25, merge commit `3496cfb`; issue #24 is
confirmed closed. Proven by `tests/test_scheduler_startup_readiness.py` (10 tests) on the
3.10 + 3.11 matrix. See MASTER_PLAN.md's P3 "S5.0" note and DECISIONS.md.

**S5.1 complete (2026-08-10, `e1277b7`, merged via PR #40).** Exit deliverable:
`bartholomew/kernel/competency.py`, implementing the explicitly-approved
`docs/S5_1_COMPETENCY_ARCHITECTURE_DESIGN.md`. Five new `MemoryStore` `kind` values
(`competency`, `competency_knowledge`, `competency_procedure`, `competency_heuristic`,
`competency_evidence`) carried as structured JSON in `memories.value`/`summary` — **no new table,
no new column, no new memory authority, no new Executive or Governance path**, per that design's
own non-negotiable invariants. Every record carries the classification (`personal` /
`potentially_generalisable` / `system`) and provenance envelope `CONSTITUTION.md`'s "Personal
learning vs. potentially generalisable and system-level learning" section requires; the module is
pure data (no persistence, retrieval, or I/O of any kind) and never branches on `classification`
to do anything — `tests/test_competency_no_auto_promotion.py` asserts structurally that no
promotion, export, or transport mechanism was introduced. Proven by **43 tests** across
`tests/test_competency_model.py` (27), `test_competency_memory_shapes.py` (8),
`test_competency_retrieval.py` (3), `test_competency_worked_example.py` (2), and
`test_competency_no_auto_promotion.py` (3), all passing. Estate Management appears only as inert
worked-example/fixture data, per this stage's scope. Two items the design flagged remain
deliberately **not** done and are not S5.1 defects: `memory_rules.yaml` carries no explicit
entries for the five new kinds (design §9, "recommended, not required" — **closed by S5.2**), and
`upsert_memory()`'s `memory_dict` still doesn't pass through `tags` (design §10,
flagged-not-fixed). **Approval of S5.1 does not authorise S5.2, S5.3, or S5.4.**

**S5.2 complete (2026-08-11) — PR #43, merge commit `5dacb52`.** CI run #61 green on all six jobs
(Quality/lint/format/packaging contract; Tests + coverage on Ubuntu 3.10 and 3.11; Critical
integration + lifecycle on 3.10 and 3.11; Windows 3.11). Exit deliverable:
`docs/S5_2_TRAINING_KNOWLEDGE_ACQUISITION_DESIGN.md` plus its implementation, delivered in three
separately reviewed and separately approved steps:

- **Step 1 (`8dfbd74`) — governance foundation.** Registers the `training` parking-brake scope in
  all four places it must appear (the Stage 1 governance API allowlist rejects unknown scopes, so
  an unregistered scope would be enforceable in the kernel yet impossible to engage from the API or
  UI). Adds explicit `memory_rules.yaml` entries for S5.1's five competency kinds, closing S5.1
  design §9. Deliberately sequenced **before** the write seam: the control that stops training must
  exist before the path that performs it.
- **Step 2 (`fb67156`) — the governed write seam.** `bartholomew/kernel/training.py` (pure
  data/validation, no I/O) plus `run_training_through_runtime_contract()`, writing S5.1's kinds
  through the existing `MemoryStore.upsert_memory()` chain — no separate ingestion runtime, no
  second Memory authority, no second Governance path, per `COGNITIVE_RUNTIME.md`'s "Training as
  Memory input, not a separate pipeline."
- **Step 3 (`e51e47f`) — surfaces and demonstration.** `POST /api/training/submit`,
  `GET /api/training/source-types`, and a `bartholomew train` CLI command (Decision B: API + CLI,
  no new UI). Includes the Estate Management end-to-end demonstration this document's own
  acceptance-test sequence calls for at S5.2 — trained *as a competency*, with a test asserting no
  Estate-specific kind was introduced.

**Properties enforced in code, not merely documented:** provenance integrity (`recorded_by`/
`recorded_at` are seam-derived, so a caller cannot forge attribution or backdate a write); the
S5.2/S5.4 boundary (`experience`/`system_observation` source types rejected by the seam — a
sub-stage marker S5.4 may deliberately lift, not a permanent property); per-record independence
with `queued_for_consent` reported distinctly from `stored`; supersession recorded in the
append-only `reflections` table so `memories` stays current-state-only; and the seam consuming
structured records rather than keystrokes, so future extraction paths can feed this same governed
write (design §9.1 Constraint 1).

**Included correction (`7bfab09`), separately approved:** two pre-existing
`privacy_guard.is_sensitive()` defects the integration surfaced — unanchored substring matching
(which flagged ordinary English such as "memory allocation" and "bankrupt") and scanning of JSON
schema keys (which flagged every `competency`/`competency_procedure` record on its `"name"` key
rather than its content). Fixed rather than bypassed: using `skip_privacy_guard=True` from the
production seam would have been a consent bypass the design forbids, and a test asserts the seam
never passes it. The approved governance rule is that **schema-defined keys of registered
structured kinds are structural metadata, while all values remain fully scanned and
unknown/unregistered structured data keeps conservative key-and-value scanning** — see
`DECISIONS.md`. **Approval of S5.2 does not authorise S5.3 or S5.4.**

**S5.3 complete (2026-08-12) — PR #45, merge commit `a4f094b`, CI run #65 green on all six jobs.**
Exit deliverable: `bartholomew/kernel/competency_reasoning.py` (the selection core — relevance
gate, confidence floor, single-competency commitment, supervision propagation, explanation-grade
`AppliedRecord`s) plus its wiring into `bartholomew/kernel/runtime_contract.py`'s
`run_chat_through_runtime_contract()`. Delivered in four commits: competency reasoning core
(`c912ddd`), the chat-only retrieval seam (`32fa344`), exposure-boundary verification and an
end-to-end demonstration (`ee446c2`), and a same-day-added deterministic relevance floor
(`e7bbc31`) — measured, not guessed: retriever scores were found not comparable across FTS/vector/
hybrid modes and, under the deterministic fallback embedder used when `sentence-transformers` is
absent, anti-correlated with relevance, so relevance is gated on lexical term overlap between the
request and each record's own text rather than on retriever score. Chat is the only surface wired
to this seam (Decision B); `Planner.decide()` remains deliberately inert. Supervision can only ever
become stricter (an OR across applied records); competency guidance is never surfaced to the user
automatically (Decision E.1) but is recorded at explanation-grade detail in the per-turn Reflection
(Decision E.2). Proven by five new test files (~1,600 lines): `test_competency_reasoning_selection.py`,
`test_competency_reasoning_seam.py`, `test_competency_reasoning_demonstration.py`,
`test_competency_context_exposure_boundary.py`, `test_competency_relevance_floor.py`.
**Approval of S5.3 does not authorise S5.4.**

**Note on S5.3's real reach (2026-08-12, recorded as part of the Usable POC assessment, not a
defect in S5.3 itself):** this retrieval/selection machinery only ever sees records of the five
`competency_*` kinds, written exclusively through S5.2's formal training-ingestion API/CLI.
Ordinary chat conversation does not write anything through that path, so in real day-to-day use
this seam has nothing to retrieve until something is deliberately trained in. This is precisely the
gap the Usable POC's first vertical slice (Personal Memory Capture and Recall, see the "Usable
POC" section below) closes — by extending, not replacing, this same seam.

**Exit criteria (S5.1–S5.4, competency/training/learning):**
- A generic competency data/contract model exists, expressed as structured Memory content (not a
  new schema/database), and is demonstrated end-to-end by training one worked competency
  (Residential Estate Management — see below) into it.
- Training material can enter shared Memory with provenance and consent, through the existing
  Observation → Interpretation → Memory path, not a separate ingestion runtime.
- The Executive (`Planner.decide()` or its successor) retrieves and applies relevant competencies
  when constructing a `CandidateAction`, still gated by the existing, single Governance path.
- The Experience → Reflection → candidate learning → consolidation loop is implemented and
  demonstrably governed (high-impact/low-confidence candidate learning is reviewed, not silently
  applied).
- Estate Management is implemented as a **competency** — no `EstateExecutive`, `EstateMemory`,
  `EstateGovernance`, `EstateLLM`, or comparable per-competency cognition/runtime exists anywhere in
  the result.
- Candidate learning and competency-evidence records carry a personal / potentially-generalisable /
  system-level classification and sufficient provenance to support (not implement) the future
  generalisation pipeline in `COGNITIVE_RUNTIME.md`'s "Personal, generalisable, and system-level
  learning classification" section (added 2026-08-08). No cross-instance/cross-user learning
  transport, de-identification pipeline, or product-level incorporation mechanism is built as part
  of S5.1–S5.4 — personal learning stays within the individual Bartholomew instance by default.

**Exit criteria (S5.5–S5.7, initiative — preserved from the pre-2026-08-08 scope):**
- Scheduler runs check-ins (morning/evening) and weekly review in dry-run + live.
- Quiet-hours respected; parking brake scope coverage tested.
- Suggestions logged with rationale; user can mute/adjust cadence.

**Verify:**
```bash
# S5.1 competency architecture data model (implemented 2026-08-10)
pytest -q tests/test_competency_model.py tests/test_competency_memory_shapes.py \
  tests/test_competency_retrieval.py tests/test_competency_worked_example.py \
  tests/test_competency_no_auto_promotion.py

# S5.2 training and knowledge acquisition (implemented 2026-08-11)
pytest -q tests/test_training_memory_rules.py tests/test_training_runtime_contract_seam.py \
  tests/test_training_api_and_demonstration.py
# S5.2's privacy_guard correction (lexical + structural false positives)
pytest -q tests/test_privacy_guard_structural_scanning.py

# S5.5-S5.7 initiative work -- not implemented; this test file does not exist yet.
pytest -q tests/test_scheduler_checkins.py
```

### Estate Management as architecture acceptance test

*(Added 2026-08-08.)* Per `CONSTITUTION.md`'s "One Developing Digital Individual" section,
Residential Estate Management is the **first serious proving ground** for the generic competency
architecture built in S5.1–S5.4 — not the architecture itself, and not a separate application.
**Do not build Estate Management production functionality ahead of, or as a substitute for, the
generic competency architecture.** The intended sequence, once separately approved:

1. Define the generic competency architecture (S5.1).
2. Implement the smallest generic substrate (S5.1).
3. Train Residential Estate Management into it (S5.2 onward) — knowledge areas (building systems,
   maintenance, warranties, contractors, utilities, asset lifecycle), procedures (maintenance
   triage, quote comparison, repair-vs-replace evaluation, warranty claims, contractor follow-up),
   relevant capabilities (documents.read, web.search, calendar, email, notifications, and
   eventually payment), and experience/evidence (prior cases, successful interventions, mistakes
   and corrections, observed outcomes).
4. Identify where the generic model fails to serve Estate Management's real needs.
5. Fix the general architecture — never add Estate-only exceptions to work around a limitation.
6. Repeat with a second competency, such as Vehicle Management.
7. Use a structurally different third competency (for example Travel or Finance) as the decisive
   generalisation test.

An Estate-specific UI may eventually exist (properties, rooms/areas, assets, appliances,
warranties, documents, maintenance, contractors, jobs, quotes, costs, upcoming obligations) — see
`CONSTITUTION.md`'s "Specialised interfaces are views, not separate applications." Building that UI
is separate, unapproved future work; it must render and edit the same shared Bartholomew state, not
own a private copy of it.

**Acceptance principle:** if adding the third competency requires redesigning the core competency
schema, or introducing another brain, memory authority, Executive, or Governance path, the
abstraction has failed and must be reconsidered before any further competency is added.

**Explicitly out of scope for S5.1–S5.4:** any Estate Management production functionality —
skills/actions that actually read property documents, contact contractors, or take real-world
action on the user's behalf. S5.1–S5.4 are architecture-and-worked-example passes; production
Estate features are later, separately-approved work once the architecture they'd run on exists and
has been proven.

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

> **Updated 2026-08-12** (Usable POC / time-to-real-use prioritisation — see `docs/TILT.md` and
> `DECISIONS.md`'s "Usable POC / time-to-real-use prioritisation" entry): steps 1–4 below are now
> complete (Phase B, Stage 1, and Stage 5 S5.1–S5.3 all shipped 2026-08-01 through 2026-08-12).
> Steps 5–7 of the 2026-08-08 ordering (build S5.1–S5.4, then S5.5–S5.6, then S5.7) are **rewritten
> below**: the next step is the Usable POC's first vertical slice, not a continuation of the
> pre-existing Stage 5 sequence. S5.4–S5.7 are deferred, not abandoned — see the new step 6 below.
>
> **Previously (2026-08-08, New Direction reconciliation):** step 5 was rewritten to insert the
> competency/training/learning architecture (Stage 5's S5.1–S5.4) ahead of initiative safety
> scaffolding (S5.5–S5.7), per `CONSTITUTION.md`'s "One Developing Digital Individual" section and
> `ROADMAP.md`'s restructured Stage 5. That did not change step 4's Stage-1-before-Stage-5 ordering
> rationale for **live proactive** delivery specifically — it only clarified that Stage 5's earlier
> sub-stages (S5.1–S5.4) are architecture/worked-example work, not live proactivity, and are not
> themselves gated on Stage 1. Superseded above for what comes after S5.1–S5.3, not reversed: that
> sequencing is exactly why S5.1–S5.3 were the right things to build before this pivot.
>
> **Previously (2026-07-28, planning-document reconciliation, approved sequencing):** Supersedes
> the 2026-07-27 ordering below, which listed Phase B, then Stage 5/S5.1, then Stage 1. That
> ordering is corrected: Stage 1 (a minimal consumer web governance shell) is sequenced **before**
> Stage 5/S5.1, because Stage 5's live proactive behaviour requires a user-facing governance
> surface (parking brake, consent/approval inbox, mute, notification controls, awaiting-response
> queue) that does not exist until Stage 1 ships. **This is a planning/sequencing decision only —
> it does not authorise implementation of any step below.** Each step still requires its own
> separate, explicit approval before work begins.

**Approved sequence (each numbered step requires its own separate approval):**

1. ✅ **Done.** Documentation reconciliation and the deployment-architecture decision. See
   `DECISIONS.md`'s "Deployment architecture: hybrid local-first" entry.
2. ✅ **Done.** Phase B (B0–B9) — persistence-ownership stabilisation, against the approved hybrid
   local-first architecture. See the Phase B workstream section above, `docs/PHASE_B_OVERVIEW.md`,
   and `docs/PHASE_B_RISK_MAP.md`.
3. ✅ **Done.** Stage 1 (S1.0–S1.6) — the minimal consumer web governance shell: parking-brake
   access; system status; consent and approval inbox; notification settings; mute and quiet-hours
   controls; awaiting-response queue; audit/provenance visibility; host-device onboarding.
4. ✅ **Done.** Stage 5 S5.1–S5.3 — competency data/contract model, training/knowledge acquisition,
   and Executive competency reasoning, worked through Residential Estate Management as the first
   proving ground (see `ROADMAP.md`'s Stage 5 section).
5. **Plan and implement the Usable POC's first vertical slice: Personal Memory Capture and
   Recall**, only after separate, explicit approval. **Added 2026-08-12; supersedes the
   pre-existing "S5.4 next" ordering.** Extends the existing consent-gated write path and the
   existing S5.3 competency-retrieval seam to ordinary conversational facts, plus one real
   notification delivery channel. See `docs/TILT.md`'s "First vertical slice" section and the
   "Usable POC — progressive vertical slices" section above. Not gated on anything below.
6. **Subsequent Usable POC vertical slices**, each requiring its own separate approval and each
   scoped only after the slice before it has generated real feedback — progressing toward
   proactive surfacing of something noticed and at least one genuine governed action with a
   visible real-world result. Draws on the real material already scoped in Stage 5 **S5.4**
   (experience→learning/consolidation loop) and **S5.5–S5.7** (initiative safety scaffolding,
   dry-run, controlled live initiative) — **deferred, not discarded**; see `docs/TILT.md`'s
   "Direction for later slices" and "What is deferred" sections. The locked internal ordering
   within that material (typed cadence → default-off consent + functional mute → quiet-hours
   defer → dry-run → rationale logging → live, brake-blocked initiative under a default-deny
   `allow_proactive` category) is unchanged in substance — only its position in the overall
   sequence (after Usable POC slices generate real feedback, not before) has moved.

**Also open but unscheduled** (each requiring separate approval, not part of the sequence above):
issue #22 (forward `IdentityContext` through the voice/sight compat wrappers, deferred to Stage 6);
Phase A's deferred findings F9–F11 (`RISKS.md`); jurisdiction-aware capture/recording work (Stage
6/7, see `ROADMAP.md`'s Stage 6/7 sections); adaptive-notification and awaiting-response delivery
work beyond the Stage 1 shell's baseline controls; data-export/portability delivery (see the Stage
1/6 notes added 2026-07-28); host-device onboarding guidance (Stage 1, see above); activating a
real embeddings model (`sentence-transformers`) in place of the deterministic fallback embedder;
a second competency domain per the Estate Management acceptance-test sequence.

**Historical (2026-07-28 ordering, superseded by the sequence above for step 5 onward — kept for
record):** steps 1–4 above are unchanged from the 2026-07-28 pass; the 2026-07-28 pass's steps 5–6
("Add Stage 5 safety scaffolding and dry-run behaviour" / "Permit live proactive Stage 5 behaviour")
described what is now S5.5–S5.7 only, without the S5.1–S5.4 competency/training/learning work this
pass inserts ahead of them.

**Historical (2026-07-27 ordering, superseded by the 2026-07-28 sequence — kept for record):**

1. **Phase B — persistence ownership stabilisation.** Proposed next engineering work; not
   approved. See the workstream section at the top of this document.
2. **Stage 5 / S5.1 — initiative engine.** Paused. The locked sequence (safety scaffolding
   before live proactivity) is recorded in the Stage 5 section above. *(Note added 2026-08-08:
   "S5.1" in this historical entry refers to the pre-2026-08-08 numbering, where S5.1 began the
   initiative-engine work; under the restructured numbering S5.1 is competency architecture — see
   the Stage 5 section above.)*
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
- **Build multi-user / tenancy / platform infrastructure (added 2026-08-15).** `CONSTITUTION.md`'s
  "One Platform, Many Personal Bartholomews" section establishes that Bartholomew is ultimately one
  shared platform serving many strongly isolated personal identities. That is **architectural
  direction, not current scope.** Actual multi-user infrastructure — production tenancy, ownership
  columns and their migrations, authentication and authorization systems, a client/server split,
  distributed services, identity migration/portability tooling, scalable backend deployment — is
  **FUTURE PLATFORM WORK**. It requires its own proposal and approval and **must not be pulled into
  any current stage's scope**, including the Usable POC slices. No stage's exit criteria changed as
  a result of that decision. What applies now is only the constraint on how *new* code is shaped
  (see `CHECKLISTS.md`'s "Platform and personal-identity architecture checklist"), not a mandate to
  build or refactor anything. The nearest genuinely-scheduled adjacent work remains Stage 6's
  cross-device auth and threat model, which is unchanged and still gated as it was.
- **Build native device agents or a device-capability protocol (added 2026-08-17).**
  `DECISIONS.md`'s "Deployment architecture — server-centric Bartholomew with local/edge capability
  agents" makes core cognition server-centric by default, with native PC/mobile applications acting
  as governed capability bridges. That is **TARGET architecture, not current scope, and nothing of
  it exists** — no agent, no capability protocol, no transport, no pairing or per-device trust, no
  authentication to carry any of it. It joins the line above as **FUTURE PLATFORM WORK** requiring
  its own proposal and approval.
  **Explicitly unchanged by that decision:** the Usable POC / time-to-real-use prioritisation and
  `docs/TILT.md` sequencing continue to govern what is worked on next; no stage's exit criteria,
  sequencing or status changed; and real-world use of slice 1 remains the next step. A destination
  was recorded, not a schedule. Two prerequisites are worth naming because they are easy to
  discover late: a defined loss-of-connectivity/degraded-mode behaviour with locally enforceable
  stop authority (`RISKS.md`, and clause (b) of that entry), and the same Stage 6 auth/threat-model
  gate that already blocks remote exposure. The proposed *shape* of the capability contract is
  recorded in `INTERFACES.md` §6 under "Proposed contracts — NOT implemented, NOT approved".
