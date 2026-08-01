# B9 Implementation — Recovery, Rollback, and Adversarial Validation

> Phase B, stage B9, the final stage. Per `docs/PHASE_B_OVERVIEW.md`'s B9 scope: validate the
> integrated B0–B8 result adversarially, and formalise recovery operations. This stage does not add
> new features — its own exit condition is "adversarial scenarios exercised against the integrated
> system," not new capability.

## 1. A real bug found by adversarial testing, not by re-running existing tests

`tests/test_b9_adversarial_validation.py::test_concurrent_cli_engage_from_two_real_processes_
loses_no_scope` — a genuine two-OS-process concurrency test (`multiprocessing`, not two objects in
one process, which `tests/test_governance_brake_store.py`'s own 26 tests never exercised) — found
that `GovernanceBrakeStore._apply_transition()` had a real lost-update race: two concurrent
`engage()` calls could each read the same pre-transition state, compute their own transition from it,
and the second writer's `UPDATE` would silently clobber the first's, losing whichever scope only the
first writer added. B3's docstring had claimed "one connection, one commit... atomic with respect to
the state write" — true for surviving a crash mid-write, **not** true for serializing concurrent
writers, an overclaim that stood from B3 through B8 without being caught, because nothing before this
stage tested real concurrent access to it.

Fixed in `bartholomew/kernel/governance/brake_store.py`: `_apply_transition()` now issues
`conn.execute("BEGIN IMMEDIATE")` before its read, acquiring SQLite's RESERVED lock immediately
rather than relying on the default deferred-transaction behavior (which only acquires the write lock
at the first actual write statement). A second concurrent caller's own `BEGIN IMMEDIATE` now blocks
(bounded by `set_wal_pragmas()`'s `busy_timeout=5000`) until the first caller's transaction commits,
then reads the post-transition state — a true compare-and-swap under real concurrency, not just
within one process. Verified: the same adversarial test now passes, all 26 of B3's own tests still
pass unmodified (the fix changes nothing about single-caller behavior), and a second, harder race
(`test_concurrent_engage_and_disengage_from_two_processes_stays_coherent`) confirms the two racing
transitions serialize into one of exactly two coherent, non-torn outcomes — never a mix of both.

This is exactly the class of finding B9 exists to surface: a defect real only under genuine
concurrency, invisible to every earlier stage's own (necessarily narrower) test suite.

## 2. Other adversarial scenarios exercised

- **Partial migration**: `test_stale_legacy_flag_is_never_consulted_once_b3_schema_exists` — the
  legacy `system_flags` "parking_brake" value is made to actively disagree with the live
  `GovernanceBrakeStore` state (simulating a value that drifted after migration, never cleaned up).
  Confirms `check_scope_blocked()` (the one path every live Governance gate uses since B4/B6) never
  reads it, regardless of what it says.
- **Interrupted operations**: `test_admission_token_released_when_wrapped_work_raises` and
  `test_admission_token_released_when_wrapped_work_is_cancelled` — an admitted request whose handler
  raises, or is cancelled outright (simulating a client disconnect), must still release its
  `AdmissionToken` via the middleware's `finally` block (B7). Both confirmed.
- **Concurrent CLI attempts**: covered by §1's two tests, directly exercising the exact scenario
  `docs/B6_IMPLEMENTATION.md` §4's "brake commands are deliberately not write-fenced" decision
  accepted as a real possibility.

Scenarios considered and deliberately not built as new tests, because existing coverage already
addresses them directly: blocked/stuck workers (`DedicatedDbExecutor`'s timeout-and-abandon semantics
— B2's own 11 tests, including a genuine stuck-caller scenario); failed startup and failed shutdown
individually (B5's 9 tests, B6's 4); process crash and restart (B5/B6's simulated-crash tests, plus
§3 below for what "recovery" actually means here).

## 3. Recovery and rollback procedures

Written up in `docs/B9_RECOVERY_AND_ROLLBACK.md` — crash recovery (what's automatic vs. manual),
rolling back the Governance schema swap (and the specific partial-rollback hazard that must be
avoided), two known-and-accepted gaps carried forward from earlier stages (narrator.py's event-loop
exposure, `MemoryStore`'s `aiosqlite` pragma gap), and an honest statement of Windows verification
status. No procedure in that document claims a capability the code doesn't actually have.

## 4. Final cross-stage invariant validation

`docs/PHASE_B_OVERVIEW.md` §4's non-negotiable invariants, checked against the integrated B0–B9
result:

| Invariant | Status | Evidence |
|---|---|---|
| **Fail-closed governance** — Parking Brake can only become more restrictive without explicit, confirmed loosening | ✅ Held, with one real defect found and fixed this stage | B3: `engage()` unions instead of replacing; `disengage()`/`narrow_scopes()` require `confirm=True`. B9: the concurrent-write race (§1) could have caused a lost `engage()` scope under real concurrency — exactly a silent, accidental loosening — found and fixed via `BEGIN IMMEDIATE`, re-verified by the adversarial test. |
| **No event-loop-blocking database I/O** | ⚠️ Mostly held; one gap knowingly remains | B2 migrated `memory_store.py`'s two blocking sync bodies, `skill_registry.py`, and the three skill modules onto `DedicatedDbExecutor`. **`narrator.py`'s `persist_episode()` remains reachable from the event loop** — recorded as an open, deliberately-deferred finding since B2 (see `docs/B9_RECOVERY_AND_ROLLBACK.md` §3), not silently dropped. |
| **One authoritative schema per governance table** | ✅ Held | B3 defined the one schema; B4 kept the daemon read path stateless-legacy until B6 could swap both daemon and CLI together; B6 performed that swap. B9's partial-migration test confirms the legacy value is genuinely inert post-swap, not just assumed to be. |
| **Verified shutdown, not assumed shutdown** | ✅ Held for the resources B1–B7 introduced | B2 established confirmed-drain semantics (`DedicatedDbExecutor.close()`'s bool return). B5 extended failed-start unwind to cover every B1–B2 resource and added the clean-shutdown marker. B6 bound the process lock's release to being the last step, after B5's marker write. B7 added admission freeze-and-drain before `_kernel.stop()` — the piece B5 itself said was needed before this invariant could be called complete. |
| **No implicit authority expansion** | ⚠️ Consciously superseded for this session, not silently violated | `ROADMAP.md`'s Phase B section records the user's explicit authorization for autonomous B0–B9 implementation in this session, superseding the per-stage pre-implementation approval checkpoint this invariant otherwise describes. Within that authorization, two genuine safety forks were still surfaced and confirmed with the user rather than decided unilaterally (B4's schema-swap timing; the initial scope of autonomy itself). |
| **User approval gate unchanged** | ⚠️ Same conscious exception as above | Every commit in B0–B9 was still made individually, with a full test-suite run and a written implementation doc per stage — the *mechanism* of review wasn't skipped, only the *interactive pre-approval* step, per the same recorded authorization. |

The two ⚠️ rows are not failures of this phase — they are the two places this document's own honesty
requirement (per B9's exit condition) requires flagging a real, conscious trade-off rather than
claiming a clean sweep. Every ✅ row has direct, cited test evidence, not merely a design claim.

## 5. Verification

`tests/test_b9_adversarial_validation.py` (5 tests, including two real multi-process tests using
`multiprocessing.get_context("spawn")`, matching `tests/test_process_lock.py`'s established pattern
for genuine cross-process verification rather than same-process approximations). All of B3's,
B6's, B7's, and B8's own test suites re-verified passing after the `BEGIN IMMEDIATE` fix. `black`/
`ruff`/`mypy` clean. Full existing test suite passes.
