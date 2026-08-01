# B9 — Recovery, rollback, and adversarial validation

> **Status:** final Phase B stage per `docs/PHASE_B_OVERVIEW.md` §5 and `ROADMAP.md`'s Phase B
> stage table. Validates the integrated B0–B8 result under real adversarial conditions — genuine
> file corruption, genuine thread-level concurrency, repeated crash cycles — rather than
> monkeypatched single-failure-mode simulations, and formalises what recovery/rollback capability
> actually exists in the current repository (as opposed to what the archived, non-authoritative
> design proposed).
>
> **Base facts:** drawn from direct execution of adversarial tests against the real, integrated
> `KernelDaemon`/`GovernanceStore`/`ProcessLock`/`RequestAdmission` stack, and a direct repository
> search for the archived design's named rollback/maintenance mechanism, not assumed from the risk
> map's own descriptions.

## 1. Grounded findings

1. **A real bug, caught by a genuinely corrupted database file, not a mock**: `PRAGMA quick_check`
   against a sufficiently corrupted SQLite file raises `sqlite3.DatabaseError` ("database disk
   image is malformed") instead of returning a descriptive result row.
   `governance_store.run_quick_integrity_check()` didn't handle this — the exception propagated as
   a raw, undiagnosed `DatabaseError` instead of the documented, operator-facing
   `UnsafeStartupError` B5 designed this exact scenario to produce. Startup still correctly aborted
   either way (the exception still unwinds through `start()`'s protected region and gets recorded
   as a startup incident), but the diagnostic quality — the entire point of B5's Startup Incident
   Log — was degraded for real, severe corruption specifically. Fixed: `run_quick_integrity_check()`
   now catches `sqlite3.DatabaseError` and folds it into the same `(False, ...)` contract every
   caller already handles.
2. **`rollback_clear_maintenance()` — the archived design's named rollback/maintenance mechanism —
   does not exist anywhere in the current repository**, confirmed by direct search. No maintenance-
   mode flag, no rollback marker file, no quiescence-probe mechanism of any kind exists in
   `bartholomew/`. This is the same shape of finding B7 made for `spawn_detached_governed_task` and
   B4 made for the sight/voice gates: an archived-design concept with no real-repository
   counterpart. B8's own risk-map row language ("rest on operator judgement alone... an honest,
   irreducible operator responsibility, not a complete guarantee") already anticipated this
   mechanism was thin even if it existed — it doesn't exist at all today.
3. **No genuinely partial/interrupted schema condition was reproducible against the current
   repository's actual migration shape.** Every Phase B stage introduced entirely new tables
   (`parking_brake_state`, `governance_audit`, `brake_runtime`, `startup_incidents`), never an
   `ALTER TABLE` on a pre-existing one — `ensure_schema()`'s `CREATE TABLE IF NOT EXISTS` pattern is
   naturally idempotent and additive by construction, unlike the archived design's "existing tables
   missing newly-required columns" concern, which doesn't apply to how this codebase actually
   evolved its schema.
4. **Windows-specific behaviour is already continuously re-verified**, not a one-time B9 check: the
   existing `Windows lifecycle + compatibility` CI job re-runs the full lifecycle/lock/admission
   suite on every commit, and has passed on every Phase B stage since B0, including B6's
   `msvcrt.locking()` branch (untestable directly in this Linux environment) and B7/B8's additions.
   No Windows-specific code was added in B9 itself, so there is nothing new for that job to newly
   validate beyond what it already continuously does.
5. **Single-process topology remains an explicit, deliberate non-goal**, per
   `docs/PHASE_B_OVERVIEW.md` §8's own listed deferral — restated here, not newly decided: a
   multi-daemon or multi-host deployment needs separately scoped future design work Phase B never
   claimed to cover.

## 2. What was built

### Real (not monkeypatched) adversarial tests

`tests/test_b9_adversarial_startup_shutdown.py` (4 tests):

- **Genuine file corruption** — brings up a real daemon, stops it, seeds an unclean-shutdown
  marker, then overwrites real bytes in the middle of the actual SQLite file (past the header, into
  real page data) and confirms startup genuinely aborts with `UnsafeStartupError` — the direct
  trigger for finding 1 above.
- **Genuinely concurrent daemon starts** — two `KernelDaemon.start()` calls run via
  `asyncio.gather` (interleaved on the same event loop, not sequential), proving `ProcessLock`'s
  exclusion property under real concurrency: exactly one succeeds, the other fails with
  `ProcessLockHeldError`.
- **Repeated crash/recovery cycles** — two full crash→recover cycles in a row against the same
  database, confirming each unclean shutdown is independently detected, recovered, and logged (not
  conflated with, or silently skipped after, the first).
- **Admission draining under a governance-writing in-flight request** — a more adversarial version
  of B7's own drain test: the in-flight "request" doesn't just sleep, it performs a real
  `governance_store.engage()` call while `stop()` is concurrently trying to close the write fence,
  proving admission genuinely drains *before* the fence closes under real contention, not just in
  the comment that documents the intended ordering.

`tests/test_b9_concurrent_cli_daemon.py` (3 tests), using real OS threads (not sequential calls) so
`ProcessLock` and `GovernanceStore`'s `BEGIN IMMEDIATE` locking are exercised the way a genuinely
separate CLI process racing a running daemon would exercise them:

- 20 threads race to acquire the same `ProcessLock` — exactly one wins, the other 19 are refused,
  none silently double-granted.
- 20 threads each `engage()` a distinct scope concurrently — every write succeeds (SQLite's busy
  timeout serializes rather than raising "database is locked"), and the final revision count proves
  none were lost.
- Two threads race a `disengage()` with the same stale `expected_revision` — exactly one succeeds,
  the other is refused with `StaleGovernanceWriteError`, never both silently applying against a
  state only one of them actually observed.

### Fix

`bartholomew/orchestrator/safety/governance_store.py`'s `run_quick_integrity_check()` — see finding
1.

## 3. Cross-stage invariant validation (`docs/PHASE_B_OVERVIEW.md` §4)

- **Fail-closed governance** — held under adversarial testing: `test_concurrent_disengage_with_
  stale_revision_is_refused_not_corrupted` proves a loosening action is refused, not silently
  applied, under real concurrent contention; `WriteFenceClosedError`/`StaleGovernanceWriteError`
  paths were already covered by B3/B5/B6's own suites and re-ran clean here.
- **No event-loop-blocking database I/O** — B8's two sub-stages closed every concrete gap found by
  direct audit; no new gap was introduced by B9 (no new event-loop-reachable code was added).
- **One authoritative schema per governance table** — unchanged by B9; re-confirmed by finding 3
  (every Phase B table is `CREATE TABLE IF NOT EXISTS`, never a competing definition).
- **Verified shutdown, not assumed shutdown** — B9's admission-draining-under-adversarial-write test
  and the repeated-crash-cycle test both exercise this against real timing pressure, not just
  sequential mock calls.
- **No implicit authority expansion** — unaffected; B9 approved no later stage (there is none) and
  granted no new authority.

## 4. Exit condition check

- [x] Adversarial scenarios exercised against the integrated system: real corruption, real
  concurrent daemon starts, repeated crash cycles, real thread-level CLI-vs-daemon contention — 7
  new tests, one real bug found and fixed.
- [x] Rollback/maintenance procedures documented with actual, honest limitations: none exist in the
  current repository (finding 2) — stated plainly rather than describing an archived-design
  mechanism as if it were real.
- [x] Windows-specific: continuously re-verified per commit, not newly re-tested here since B9 added
  no Windows-specific code (finding 4).
- [x] Final cross-stage invariant validation: §3 above, against the concrete, integrated B0–B8
  result.

With this, Phase B's B0–B9 stage sequence is complete. `docs/PHASE_B_OVERVIEW.md` §7's high-level
exit criteria — every stage separately planned, completed, reviewed, and committed under its own
approval gate; the §4 invariants holding under B9's adversarial validation; no known event-loop-
blocking database call, unverified shutdown claim, or unaudited Governance state transition
remaining in the persistence paths Phase B covers — are met for the scope Phase B actually defined
(single-process topology; no rollback mechanism ever existed to formalise; sight/voice and the
`hybrid_retriever` search pipeline remain confirmed unreachable, not newly wired in).
