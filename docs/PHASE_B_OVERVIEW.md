# Phase B Overview — Persistence Ownership Stabilisation

> **Authority note:** this document is the concise explanation of the *approved Phase B direction
> and stage structure*. It is subordinate to and linked from `ROADMAP.md`, which remains the
> canonical source for Phase B stage gates, status, dependencies, and approval boundaries. This
> overview does not itself authorise implementation of any stage. See
> `docs/PHASE_B_RISK_MAP.md` for the index of retained research findings, and
> `docs/archive/phase-b-persistence-ownership-final.md` for the full, non-authoritative research
> record those findings are drawn from.
>
> **Last updated:** 2026-07-31 (documentation-only restructuring: replaces a prior plan to seek
> implementation-level approval for one large, indivisible specification).

## 1. Purpose

Give the project's single SQLite-backed persistence layer one coherent ownership model —
connection handling, event-loop-safe execution, Governance (Parking Brake) durability, startup/
shutdown integrity, external control safety, and request admission — instead of the current mix of
independently-evolved, partially-overlapping mechanisms.

## 2. Current problem

One SQLite database file (default `data/barth.db`) has no single owner. `MemoryStore` uses
`aiosqlite`; the scheduler uses synchronous `sqlite3` behind its own dedicated worker thread;
`persona_pack.py` and `narrator.py` call synchronous `sqlite3` directly from `async def` methods;
`bartholomew/kernel/db_ctx.py` and `bartholomew_api_bridge_v0_1/services/api/db_ctx.py` are
near-duplicate WAL/checkpoint modules, the latter still checkpointing on every call. Phase A
(merged) characterised this problem but deliberately did not fix it, and left one intermittent,
unretried concurrent-process WAL failure as open evidence (`RISKS.md`).

## 3. Desired final outcome

A single, coherent persistence-ownership model in which: every SQLite connection is opened,
configured, and closed through one shared policy; no synchronous database call blocks the asyncio
event loop; Governance (Parking Brake) state has one durable, auditable, schema-defined
representation with monotonic tightening and confirmed loosening; startup and shutdown are ordered,
verified, and leave no ambiguous half-initialised or half-torn-down state; external CLI/maintenance
tools cannot race a running daemon; externally admitted governed work cannot be silently dropped by
shutdown; and the remaining persistence consumers (MemoryStore, VectorStore, FTS, liveness/metrics,
scheduler) are migrated onto the same policy.

## 4. Non-negotiable high-level invariants

- **Fail-closed governance.** The Parking Brake can only become *more* restrictive without an
  explicit, confirmed loosening action; no code path may silently widen access.
- **No event-loop-blocking database I/O.** Any synchronous SQLite call reachable from an `async
  def` must be moved off the event loop.
- **One authoritative schema per governance table.** `parking_brake_state`, `brake_runtime`, and
  `governance_audit` have exactly one schema definition, one migration path.
- **Verified shutdown, not assumed shutdown.** Clean-shutdown evidence must be based on confirmed
  termination of every relevant resource (executor threads, producer tasks, admitted work), not on
  an operation merely being submitted.
- **No implicit authority expansion.** Approving this overview does not approve any stage's
  implementation; approving one stage does not approve the next.
- **User approval gate unchanged.** Every stage's plan, every implementation diff, and every commit
  remains separately and explicitly gated, per `DECISIONS.md`'s existing "User Approval Gate"
  decision and `CHECKLISTS.md`'s commit-authorization checklist.

## 5. Stages B0–B9

### B0 — Verified persistence baseline
**Purpose:** establish repository and runtime facts later stages depend on.
**Scope:** actual DB paths; SQLite connection owners; synchronous DB work on the event loop;
current WAL/checkpoint behaviour; real Parking Brake construction sites; real API/CLI ingress;
actual daemon/API process topology; startup/shutdown order; supported Python/Windows behaviour.
**Required inputs from earlier stages:** none — this is the first stage.
**Exit condition:** a concise, repository-grounded current-state report; no production
implementation.
**Major deferrals:** any fix or redesign — B0 only characterises.

### B1 — Shared SQLite connection policy
**Purpose:** one reusable policy for opening, configuring, and closing SQLite connections, and a
clear assignment of every remaining persistence caller's migration to the stage that will actually
perform it.
**Scope:** common connection helper; WAL-related pragmas; foreign-key setting; busy timeout;
synchronous mode; connection closure; resolving the duplicate connection-policy implementation and
the unconditional hot-path checkpoint behaviour identified for this stage; an inventory of every
remaining persistence caller, each explicitly assigned to B2 (event-loop-blocking callers) or the
appropriate B8 sub-stage (remaining consumers); focused tests for the shared policy itself.
**Required inputs from earlier stages:** B0's current-state report (actual connection call sites).
**Exit condition:** the shared connection policy is implemented and tested; the duplicate/hot-path
checkpoint problem is resolved; every remaining consumer migration is inventoried and explicitly
assigned to B2 or B8 — B1 does not require all persistence consumers to already be migrated onto
the shared policy before it can exit.
**Major deferrals:** the actual migration of B2's and B8's assigned consumers (performed in those
stages, not here); Governance, API admission, and shutdown redesign — not touched here.

### B2 — Event-loop isolation and database execution
**Purpose:** remove blocking synchronous SQLite operations from the asyncio event loop.
**Scope:** minimal dedicated database execution mechanism; accepted-vs-not-submitted outcomes;
timeout semantics; shutdown; actual worker termination; migrating the callers B1's inventory
assigned to this stage (including scheduler and health-path callers) to resolve the known blocking
problem.
**Required inputs from earlier stages:** B1's connection policy and the subset of its caller
inventory assigned to B2.
**Exit condition:** the known event-loop-blocking call sites resolved; worker termination is
confirmed, not merely submitted-and-assumed.
**Major deferrals:** migrating all remaining persistence consumers (deferred to B8).

### B3 — Governance schema and Parking Brake persistence
**Purpose:** one authoritative, durable, auditable representation of Governance state.
**Scope:** governance schema; additive/idempotent migrations; Parking Brake state and versions;
engage, disengage, and scope narrowing; stale-result handling; atomic audit events; legacy-value
migration; isolated transition tests.
**Required inputs from earlier stages:** the actual repository state reached after B2 (not
hypothetical future components). Candidate risks/tests from `docs/PHASE_B_RISK_MAP.md`'s B3 rows
must be revalidated against that state before being relied upon.
**Exit condition:** governance schema and Parking Brake transition semantics implemented and
tested in isolation, without yet being the runtime's shared instance.
**Major deferrals:** shared-instance runtime integration (B4); CLI/process-lock integration (B6).

### B4 — Shared Governance runtime integration
**Purpose:** ensure the running system uses one coherent Parking Brake instance and persistence
path.
**Scope:** actual construction sites; dependency injection; runtime-contract integration; real
API, scheduler, and orchestrator callers; removal or adaptation of legacy direct-state paths;
replacement-versus-union compatibility changes where actually required.
**Required inputs from earlier stages:** B3's schema and transition semantics.
**Exit condition:** every real construction site (as inventoried against the repository at
planning time, not assumed from prior research) uses the one shared instance.
**Major deferrals:** startup/shutdown integrity (B5).

### B5 — Startup and shutdown integrity
**Purpose:** reliable startup failure handling and clean-shutdown evidence, established entirely in
terms of in-process lifecycle-terminal-state conditions.
**Scope:** ordered startup; failed-start unwind; producer termination; Governance write freeze and
drain; executor shutdown; conservative unclean-start recovery; clean-marker meaning;
poisoned-instance behaviour — all defined and proved without assuming the process lock (introduced
in B6) already exists.
**Required inputs from earlier stages:** the concrete runtime produced by B1–B4.
**Exit condition:** startup and shutdown sequences verified against that concrete runtime, with
tests proving confirmed (not assumed) termination, expressed as lifecycle-terminal-state
conditions B6 can later bind process-lock behaviour to.
**Major deferrals:** the process lock itself and external CLI racing against it (B6); external
request admission (B7).

### B6 — External Governance control and CLI safety
**Purpose:** prevent CLI and maintenance tools from racing the running daemon.
**Scope:** online/offline command behaviour; introducing the process lock and binding its
acquisition/release behaviour to the lifecycle-terminal-state conditions B5 established; write
fencing only where repository evidence shows it is necessary; audit parity; Windows and POSIX
behaviour; operational failure messages; rerunning B5's lifecycle integration tests with the
process lock now in place.
**Required inputs from earlier stages:** B3–B5's schema, runtime integration, and lifecycle-
terminal-state conditions.
**Exit condition:** CLI/maintenance operations cannot silently race a running daemon, proven on
both POSIX and Windows; B5's lifecycle integration tests pass again with the process lock added.

### B7 — External request admission and detached work
**Purpose:** prevent shutdown from racing externally admitted governed work.
**Scope:** actual external-ingress inventory; identity-bound admission; exact release ownership;
request cancellation; child and detached work; admission freeze and drain.
**Required inputs from earlier stages:** B4's shared runtime, B5's shutdown sequence.
**Exit condition:** every real external ingress point is admission-gated with identity-bound
release; shutdown drains admitted work deterministically.
**Note:** this stage must not block B1–B4; it may proceed once its own inputs are ready even if
B8/B9 have not started.

### B8 — Remaining persistence consumers
**Purpose:** migrate remaining persistence users to the established policy.
**Scope (candidates, to be split further as appropriate rather than forming one large unit):**
MemoryStore concurrency; transactional re-embedding; VectorStore; FTS; liveness and metrics reads;
remaining scheduler persistence; remaining direct SQLite callers.
**Required inputs from earlier stages:** B1's connection policy and the subset of its caller
inventory assigned to B8; B2's execution mechanism.
**Exit condition:** each split sub-stage's own consumer migrated and tested; no stage here
attempts cross-module schema consolidation beyond what B3 already scoped.

### B9 — Recovery, rollback, and adversarial validation
**Purpose:** validate the integrated Phase B result; formalise recovery operations.
**Scope:** partial migrations; interrupted operations; process crashes; stale callbacks; blocked
workers; failed startup; failed shutdown; concurrent CLI attempts; rollback and maintenance
procedures; Windows-specific tests; final cross-stage invariant validation.
**Required inputs from earlier stages:** all of B0–B8, integrated.
**Exit condition:** adversarial scenarios exercised against the integrated system; rollback/
maintenance procedures documented with their actual, honest limitations (not overstated
guarantees).

## 6. Stage dependencies

```
B0 → B1 → B2 → B3 → B4 → B5 → B6
                  \-------------→ B7 (needs B4, B5; does not block B1–B4)
B1, B2 → B8 (split further; parallel to B4–B7 once B1/B2 ready)
B0–B8 → B9
```

Stage boundaries above may be narrowly adjusted later only when concrete repository evidence,
found while planning a specific stage, demonstrates the provisional order is unsafe or impossible —
not as a general reopening of the architecture.

## 7. High-level exit criteria

Phase B is complete when: every stage B0–B9 has been separately planned, completed, reviewed, and
committed under its own approval gate, with implementation performed where applicable (B0 is a
diagnostic/current-state stage and exits with a report, not production code); the non-negotiable
invariants (§4) hold under the adversarial
validation performed in B9; and no known event-loop-blocking database call, unverified shutdown
claim, or unaudited Governance state transition remains in the persistence paths this phase covers.

## 8. Explicit deferrals

- Relocating `MemoryStore`'s or the scheduler's own schema ownership into
  `bartholomew/kernel/db/schema.py` — out of scope; would require a separately approved Phase C.
- A split-process or multi-host deployment topology — out of scope; the single-process topology is
  assumed throughout B0–B9.
- A constitutional, out-of-process independent emergency-shutdown mechanism — out of scope; the
  in-process Parking Brake kill switch does not satisfy that separate invariant.
- Any detailed B0 (or later-stage) execution plan — not produced by this overview; produced
  separately, only when that stage is approved to be planned.
- Slice-level execution detail of any kind (hypotheses, instrumentation, deterministic procedures,
  named outcomes) — not part of this overview; any such diagnostic work requires its own separately
  produced, standalone, explicitly approved execution specification when the owning stage is
  reached.

## 9. Approval model

- Approval of this overview authorises only the existence and shape of the B0–B9 structure as the
  planning frame for Phase B. It does **not** authorise implementation of B0 or any other stage.
- Each stage requires its own compact, repository-grounded execution plan, produced only as that
  stage approaches, and its own explicit user approval before implementation begins.
- Approval of one stage's plan or implementation does not authorise any later stage.
- Every implementation diff is separately reviewed; every commit requires separate, explicit user
  approval, per `DECISIONS.md`'s "User Approval Gate" decision and `CHECKLISTS.md`'s commit
  authorization checklist.
- A stage may not silently expand its own scope into a later stage's territory.

## 10. Relationship to the archived research specification and risk map

The large Phase B design-review process (Designs v1–v12 and their final consolidation) produced a
substantial, independently-reviewed body of research: concurrency and lifecycle risk analysis,
repository investigation findings, candidate invariants, and candidate tests. That material is
preserved verbatim, non-authoritatively, at
`docs/archive/phase-b-persistence-ownership-final.md`. It is not an implementation specification
and confers no implementation authority by itself.

`docs/PHASE_B_RISK_MAP.md` indexes that material against the B0–B9 stages above, so a stage's
planner can find the relevant prior findings without rereading the full archived document — every
indexed item must still be revalidated against the actual repository state when its owning stage is
planned, per this document's approval model (§9) and non-negotiable invariants (§4).
