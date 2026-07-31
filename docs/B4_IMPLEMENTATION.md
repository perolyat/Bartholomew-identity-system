# B4 Implementation — Shared Governance Runtime Integration

> Phase B, stage B4. Scoped, per `docs/PHASE_B_OVERVIEW.md`, to real live-daemon `ParkingBrake`
> construction sites only — standalone CLI construction sites (`bartholomew/cli.py`) remain B6's
> responsibility and are untouched here.

## 1. Pre-implementation finding: all 6 live sites are read-only

Before writing any code, re-checking every one of `docs/B0_BASELINE_REPORT.md` §5's 9 `ParkingBrake`
construction sites found that **all 6 live-daemon sites only ever call `is_blocked()`** — a pure
read. `engage()`/`disengage()` (the kill-switch's actual on/off controls) are called **only** from
the 3 CLI commands in `bartholomew/cli.py`, which are out of this stage's scope.

This mattered for a real safety decision, surfaced to and confirmed by the user before writing any
code: B3's new schema (`parking_brake_state`) is separate from the legacy `system_flags` row the CLI
reads/writes today. If B4 had switched the 6 live reads onto the new B3 schema while CLI's
`engage()`/`disengage()` kept writing to the old `system_flags` row (since CLI migration is
explicitly B6's job), there would be a real window — from whenever B4 lands until B6 lands — where
**an operator running `bartholomew brake on` would silently stop affecting the live daemon**: the
command would appear to succeed but the daemon's checks would keep reading a table nothing was
writing to anymore. The user chose the safe option: **B4 stays on the legacy `system_flags` schema**
and only deduplicates construction. The B3 schema switch happens later, together with B6's CLI
migration, so daemon reads and CLI writes are never split across two schemas at the same time.

## 2. What "shared instance" means here, and why it isn't a literal singleton

`docs/PHASE_B_OVERVIEW.md`'s B4 exit condition asks for "one shared instance." A literal always-alive
`ParkingBrake` object constructed once at daemon startup and reused for every check was considered
and rejected: `ParkingBrake.is_blocked()` reads `self._cache`, which is populated once at
`__init__`-time (`_load()`) and only refreshed by `_write()` (i.e. by that same instance's own
`engage()`/`disengage()`). Today, every one of the 6 sites constructs a brand-new `ParkingBrake` per
check, so each check is implicitly fresh. A genuinely long-lived shared instance would break that:
the daemon would never see a CLI-issued `engage()`/`disengage()` after the shared instance's
construction, since nothing ever refreshes its cache — the emergency stop would silently stop
working for the life of the process. That would be a regression on the exact invariant Phase B exists
to protect.

Instead, `bartholomew/orchestrator/safety/parking_brake.py` gained one new function,
`check_scope_blocked(db_path, scope, memory_store=None)`, used by all 6 live sites in place of their
own inlined `storage = BrakeStorage(db_path); brake = ParkingBrake(storage); brake.is_blocked(scope)`.
It still constructs a fresh `ParkingBrake` per call — preserving the exact same freshness guarantee
the 6 sites already had — but now there is **one shared implementation**, not six independently
written (and driftable) copies. This is "one coherent persistence path" in the sense the invariant
actually protects (a single, auditable way the brake gets checked) rather than a literal single
object reference, and the trade-off is recorded here rather than silently substituted for the
overview's literal wording.

## 3. The 6 sites migrated

All now call `check_scope_blocked(...)`, each still resolving its own `db_path` exactly as before —
this stage does not touch `docs/B0_BASELINE_REPORT.md` §1's four divergent path-resolution schemes,
an explicitly open question, not something to silently fold into this change:

- `bartholomew/kernel/runtime_contract.py`: `run_chat_through_runtime_contract()` (`daemon.mem.db_path`,
  scope `"skills"`, broad `except Exception` preserved), `run_drive_through_runtime_contract()`
  (`ctx.mem.db_path`, scope `"scheduler"`, still raises `RuntimeError` on block, `ImportError`-only
  catch preserved), `run_sight_through_runtime_contract()`/`run_voice_through_runtime_contract()`
  (`resolved_db_path`, scopes `"sight"`/`"voice"`, `ImportError`-only catch preserved).
- `bartholomew/kernel/skill_registry.py`: `_is_blocked_by_brake()` (`self._db_path`, scope `"skills"`,
  fail-closed broad `except Exception` preserved).
- `identity_interpreter/orchestrator/orchestrator.py`: `handle_input()` (`_default_db_path()`, scope
  `"skills"`, `(ImportError, Exception)` catch preserved).

Every site's exception-handling breadth (some catch bare `Exception`, some only `ImportError`) was
preserved exactly as-is — this stage changes *how* the check is constructed, not what happens when it
fails.

## 4. Not touched

- `bartholomew/cli.py`'s 3 construction sites (`brake_on`/`brake_off`/`brake_status`) — B6's scope,
  per the overview.
- The B3 schema (`bartholomew/kernel/governance/`) — still unwired into any live path; §1 explains
  why that swap is deferred to happen together with B6, not split across B4/B6.
- The four divergent DB-path-resolution schemes `docs/B0_BASELINE_REPORT.md` §1 found.
- `ParkingBrake`'s replace-vs-union `engage()` semantics — irrelevant to this stage in practice, since
  none of the 6 live sites call `engage()`/`disengage()` at all; the "four repository files depend on
  replace behaviour" finding `docs/PHASE_B_RISK_MAP.md` carried from the archived research applies to
  whatever future stage actually migrates CLI/engage callers onto the new schema (B6), not to this
  one.

## 5. Verification

`tests/test_parking_brake_persistence_roundtrip.py`, `tests/unit/safety/test_parking_brake.py`,
`tests/test_parking_brake_scoped_blocks.py`, `tests/integration/test_parking_brake_integration.py`,
`tests/test_api_chat_runtime_contract.py` (including
`test_chat_returns_503_when_parking_brake_engaged`, an end-to-end exercise of the live
engage-then-check path through the real API), `tests/test_voice_sight_runtime_contract_seam.py`,
`tests/test_runtime_contract_chat_seam.py`, `tests/test_skill_runtime_contract_seam.py`,
`tests/test_scheduler_drive_convergence.py`, `tests/test_scenario_replay.py`,
`tests/test_end_to_end_tasks_and_audit.py` all pass unmodified. `black`/`ruff` clean; the one `mypy`
finding in `parking_brake.py` (a pre-existing `fetch_flag()` return-type issue, confirmed via
`git stash` to predate this change) is unrelated and not fixed here (out of scope). Full existing
test suite passes.
