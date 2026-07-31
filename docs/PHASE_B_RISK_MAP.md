# Phase B Risk Map — Stage-Indexed Index of Archived Research

> **Status:** reference material supporting stage planning. Not a source of implementation
> authority — see `docs/PHASE_B_OVERVIEW.md`'s Approval Model (§9) and `ROADMAP.md`, which remains
> canonical for Phase B stage gates and status. Appearing in this table does not mean a mechanism
> is approved; every row must be revalidated against the actual repository state during its owning
> stage before being relied upon.
>
> **Source:** `docs/archive/phase-b-persistence-ownership-final.md` (the archived research
> specification). Section references (`§N`) point into that document.
>
> **Last updated:** 2026-07-31 (documentation-only restructuring).

## Columns

- **Risk / invariant** — concise statement of the finding.
- **Stage** — the B0–B9 stage that owns revalidating and (if still applicable) implementing it.
- **Source §** — section in the archived specification.
- **Evidence status** — `repository-verified` (checked directly against actual repository files at
  the recorded base commit), `inferred` (reasoned from verified facts, not directly observed),
  `proposed` (a design decision, not a repository fact), or `prior-design finding` (a defect or
  contradiction verified in an earlier Phase B design draft, not verified as current repository
  behaviour).
- **Revalidate at** — the point where the owning stage's planner must re-check this against the
  then-current repository, not assume it still holds.
- **Candidate test / evidence** — a starting point only; the owning stage's own plan defines the
  actual verifying test.

## B0 — Verified persistence baseline

| Risk / invariant | Stage | Source § | Evidence status | Revalidate at | Candidate test / evidence |
|---|---|---|---|---|---|
| One SQLite file has no single connection owner across `MemoryStore` (`aiosqlite`), scheduler (sync `sqlite3`), `persona_pack.py`/`narrator.py` (sync `sqlite3` in `async def`) | B0 | §1, §2 (I1) | repository-verified | B0 plan start | Repository grep/read of each module's own connection calls |
| Real Parking Brake construction sites and real API/CLI ingress may differ from any prior count | B0 | §12 | repository-verified (5 real routes, 7 real construction sites at archive time) | B0 plan start | Re-run the repository-wide search; do not assume the prior count still holds |
| Actual daemon/API process topology, startup/shutdown order, Python/Windows support | B0 | §8, §9 | repository-verified | B0 plan start | `tests/test_clean_start_lifecycle.py`, CI matrix (`CI.md`) |

## B1 — Shared SQLite connection policy

| Risk / invariant | Stage | Source § | Evidence status | Revalidate at | Candidate test / evidence |
|---|---|---|---|---|---|
| SQLite pragma and checkpoint inconsistency: WAL pragmas, busy timeout, synchronous mode not uniformly applied across connection call sites; unconditional checkpoint work on hot paths | B1 | §3, §4 | repository-verified (per archived pass) | B1 plan start | Test coverage for a shared `connect()`/`set_wal_pragmas()`/`close_quietly()` module |
| Two near-duplicate WAL/checkpoint context modules (`bartholomew/kernel/db_ctx.py` vs. the API bridge's own copy) | B1 | §1 (deferred-scope note), §2 | repository-verified | B1 plan start | Grep for both modules' current call sites |

## B2 — Event-loop isolation and database execution

| Risk / invariant | Stage | Source § | Evidence status | Revalidate at | Candidate test / evidence |
|---|---|---|---|---|---|
| Event-loop blocking: synchronous SQLite calls reachable from `async def` methods (`persona_pack.py`, `narrator.py`) | B2 | §1, §4 | repository-verified | B2 plan start | Re-grep for synchronous `sqlite3` calls inside `async def` |
| `DedicatedDbExecutor` admission/timeout semantics: accepted-vs-not-submitted outcome distinction | B2 | §4.1–4.2 | proposed (design mechanism) | B2 plan start | Tests 1–3 (archived §13) as a starting template |
| Executor termination must be *confirmed*, not merely submitted: prior `close()` called `shutdown(wait=False)` and returned `drained=True` without verifying the worker thread actually exited | B2 | §4.3 (Blocker-Resolution Matrix finding 6) | prior-design finding | B2 plan start | Bounded-join test analogous to archived Test 6 |

## B3 — Governance schema and Parking Brake persistence

| Risk / invariant | Stage | Source § | Evidence status | Revalidate at | Candidate test / evidence |
|---|---|---|---|---|---|
| Governance schema ownership must be narrow: only `parking_brake_state`, `brake_runtime`, `governance_audit` — not `MemoryStore`'s or the scheduler's own DDL | B3 | §2 (I1), §3 | repository-verified (`MemoryStore`'s `CREATE TABLE` statements at its own lines; scheduler's own DDL in `scheduler/persistence.py`) | B3 plan start | Confirm current schema boundary before writing migrations |
| Stale Parking Brake transitions: a delayed loosening whose `revision` check still passes can regress `persisted_version` and reapply stale scopes if version comparison is missing | B3 | §5.5 (Blocker-Resolution Matrix finding 4) | prior-design finding | B3 plan start | Version-guarded state-application test analogous to archived Test 4 |
| Engage/loosening ordering: `last_loosen_version` intended as a database-resident, unmaskable transition-ordering signal, replacing an earlier Python-level generation counter | B3 | §5, §11 | proposed | B3 plan start | Concurrency test for engage-vs-loosen ordering |
| Replacement-vs-union compatibility: current `ParkingBrake.engage()` replaces the blocked-scope set on every call; four repository files (of 31 found) depend on that replace behaviour and need rewriting if `engage()` becomes monotonic/union | B3, B4 | §14 item 8 (full inventory table) | repository-verified (verified directly against `bartholomew/orchestrator/safety/parking_brake.py` at the archived pass's base commit) | B3 plan start (schema/semantics), B4 plan start (callers) | The four files named in §14 item 8's table, each with its own cited test |
| Legacy Parking Brake value migration must be additive/idempotent | B3 | §3 | proposed | B3 plan start | Migration idempotency test |

## B4 — Shared Governance runtime integration

| Risk / invariant | Stage | Source § | Evidence status | Revalidate at | Candidate test / evidence |
|---|---|---|---|---|---|
| Multiple Parking Brake construction sites: real live-daemon callers (API, orchestrator, scheduler) each independently construct a `ParkingBrake` instance rather than sharing one; `bartholomew/cli.py`'s own three standalone construction sites (lines 261, 277, 291) are a separate, CLI-process case owned by B6, not B4 — a standalone CLI process cannot share the daemon's in-process singleton | B4 (live-daemon sites), B6 (CLI sites) | §7.3, §8, §12 | repository-verified (7 real construction sites at archive time, of which 3 are CLI-process sites; e.g. `Orchestrator.handle_input()` constructs its own inline) | B4 plan start — re-inventory live-daemon sites before consolidating; B6 plan start for the CLI sites | Construction-site consistency check analogous to archived Test 54, scoped to live-daemon sites only |
| `run_sight_through_runtime_contract`/`run_voice_through_runtime_contract` are not wired to any external ingress currently, so not gated by request admission | B4, B7 | §14 item 12 | repository-verified | B4/B7 plan start | Re-check ingress wiring before assuming this still holds |

## B5 — Startup and shutdown integrity

| Risk / invariant | Stage | Source § | Evidence status | Revalidate at | Candidate test / evidence |
|---|---|---|---|---|---|
| Startup unwind: a failed `start()` previously released the daemon lock and re-raised with no unwind of `MemoryStore`, either executor, any producer task, or `BrakeWatcher` | B5 | §8 (Blocker-Resolution Matrix finding 2) | prior-design finding | B5 plan start | Stage-aware reverse-order unwind test analogous to archived Tests 2, 2a–2d |
| Shutdown producer/write draining: clean-shutdown evidence must include producer-task termination, not only the write fence; full evidence additionally requires admission termination, which B5 does not yet cover since B7 has not introduced request admission | B5 (producer/write fence), B7 (admission) | §9 | proposed | B5 plan start (producer/fence); B7 plan start (admission's contribution to the same invariant) | Ordered-shutdown test analogous to archived Test 1 |
| Clean-marker honesty: the clean-marker SQL `WHERE` clause alone only proves fence-closed/correct-runtime; it does not represent producer/admission/supervisor state, which must be verified separately in Python before the marker is written | B5 | §9 (Blocker-Resolution Matrix finding 1) | prior-design finding | B5 plan start | `internal_tasks_terminal` precondition test analogous to archived Test 1 |

## B6 — External Governance control and CLI safety

| Risk / invariant | Stage | Source § | Evidence status | Revalidate at | Candidate test / evidence |
|---|---|---|---|---|---|
| CLI and external-write conflicts: offline CLI operations must not race a running daemon; write fencing needed only where repository evidence shows an actual conflict path | B6 | §6 | proposed | B6 plan start | Offline-vs-online conflict test |
| Process-lock behaviour must be cross-platform: POSIX `fcntl.flock` vs. Windows `msvcrt.locking` with fixed-byte seeking | B6 | §10.1–10.2 | proposed | B6 plan start | Cross-platform lock test on both CI legs |
| Windows-specific locking and lifecycle behaviour generally (auto-release on process exit, handle-release timing) | B6, B9 | §10.1, §14 item 9 | proposed / repository-verified (per Phase A's Windows CI job) | B6 plan start; re-verified at B9 | Windows CI job (`ci.yml`), archived Tests 34–39 as a starting template |

## B7 — External request admission and detached work

| Risk / invariant | Stage | Source § | Evidence status | Revalidate at | Candidate test / evidence |
|---|---|---|---|---|---|
| Request admission identity: a prior `release()` took no identity argument, so any caller could release any in-flight admission regardless of which one it actually held | B7 | §7.2 (Blocker-Resolution Matrix finding 9) | prior-design finding | B7 plan start | Identity-bound admission/release test analogous to archived Test 9 |
| Detached work: `spawn_detached_governed_task` must propagate an admission token correctly through detached/child work so shutdown can still drain it | B7 | §7.4 | proposed | B7 plan start | Detached-task admission-drain test |
| Real external-ingress inventory must be re-confirmed, not assumed from prior research (routes, CLI commands, adapter callbacks) | B7 | §12 (5 real routes at archive time) | repository-verified | B7 plan start | Re-run the ingress inventory against the repository |

## B8 — Remaining persistence consumers

| Risk / invariant | Stage | Source § | Evidence status | Revalidate at | Candidate test / evidence |
|---|---|---|---|---|---|
| `MemoryStore` concurrency and its own serialization cost under the new executor model | B8 | §15 (Slices 9–16) | proposed | Each B8 sub-stage's plan start | Stress-harness test analogous to archived Test 51 |
| `VectorStore`/FTS daemon adapters not yet migrated to the shared connection/execution policy | B8 | §15 (Slices 9–16) | proposed | Each B8 sub-stage's plan start | Per-adapter migration test |
| Liveness and metrics reads currently on a blocking or inconsistent read lane | B8 | §14 item (liveness/metrics), §15 (Slice 17) | proposed | Each B8 sub-stage's plan start | Liveness-endpoint latency/availability test analogous to archived Test 45 |
| No cross-module schema consolidation is authorised for this stage — `MemoryStore`'s and the scheduler's own DDL remain under their existing modules' ownership | B8 | §2 (I1), §14 item 10 | repository-verified / proposed | Each B8 sub-stage's plan start | Boundary-respect test analogous to archived Test 55 |

## B9 — Recovery, rollback, and adversarial validation

| Risk / invariant | Stage | Source § | Evidence status | Revalidate at | Candidate test / evidence |
|---|---|---|---|---|---|
| Current `rollback_clear_maintenance()` deletes its marker file unconditionally, with no lock acquisition or process check of any kind; its own docstring already concedes this rests on operator judgement alone | B9 | §10.3 (Blocker-Resolution Matrix finding 8) | repository-verified | B9 plan start | Confirm this is still the current behaviour before designing the replacement |
| Proposed mitigation: a `BEGIN EXCLUSIVE` quiescence probe before clearing, which would detect an actively-transacting writer at the instant of the check but cannot prove the absence of an idle legacy process outside the process-lock protocol — this probe does not exist in current repository code; it is the archived specification's proposed design, carrying an honest, irreducible operator responsibility, not a complete guarantee | B9 | §10.3, §14 item 11 (I12) | proposed | B9 plan start | Maintenance-clearance sub-case test analogous to archived Test 41 |
| Partial migration / interrupted operations: a genuinely partial schema (existing tables missing newly-required columns) must be handled, not only fresh or fully-complete databases | B9 | §14 item, §13 test-matrix notes | proposed | B9 plan start | Partial-schema test analogous to archived Test 52 |
| Process crashes, stale callbacks, blocked workers, failed startup/shutdown, concurrent CLI attempts — adversarial scenarios against the integrated system | B9 | §8, §9, §10 | proposed | B9 plan start | Archived Tests 34–41 as a starting template, revalidated against the integrated B0–B8 result |
| Single-process topology is assumed throughout; a genuinely multi-daemon or multi-host deployment needs new design work not covered by Phase B | B9 (documented limitation, not solved) | §14 item 9 | proposed | Explicit deferral — revisit only as a separately scoped future effort | N/A |

## Cross-cutting note

Several archived findings (the Blocker-Resolution Matrix's nine accepted findings, and the
replacement-semantics inventory in §14 item 8) were themselves defects found in an *earlier draft*
of the archived specification during its own independent closure review — defects in that prior
design draft, not verified as current repository behaviour. Rows drawn from those findings are
labelled `prior-design finding` in the **Evidence status** column, distinct from `repository-
verified` (checked directly against actual repository files at the recorded base commit),
`inferred` (reasoned from verified facts, not directly observed), and `proposed` (a design decision
or mechanism that does not exist in current repository code, whether or not it addresses a
`prior-design finding` or a `repository-verified` fact). The owning stage's planner must not
conflate any of these four with another — in particular, "this was correctly identified as a
defect in a prior design draft" (`prior-design finding`) is not "this is confirmed to be the
current repository's behaviour" (`repository-verified`), and a `proposed` mechanism's own honest
limitations are not evidence that the mechanism itself currently exists — and must re-check each
row against the repository regardless of its labelled category before relying on it.
