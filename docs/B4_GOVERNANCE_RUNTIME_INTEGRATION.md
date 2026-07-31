# B4 — Shared Governance Runtime Integration

> **Status:** B4 exit deliverable per `docs/PHASE_B_OVERVIEW.md` §5 and `ROADMAP.md`'s Phase B
> stage table. Wires B3's `GovernanceStore` in as the daemon's one shared Governance instance at
> every real live-daemon construction site. Standalone CLI construction sites
> (`bartholomew/cli.py`) remain untouched — B6's responsibility, not a gap left open here, per the
> overview's explicit exit condition.
>
> **Base facts:** drawn from `docs/B0_PERSISTENCE_BASELINE.md`, `docs/B3_GOVERNANCE_PERSISTENCE
> .md`, and a fresh re-inventory of `ParkingBrake`/`BrakeStorage` construction sites done at B4
> plan start (B4's own exit condition requires re-verifying this, not assuming it from B0).

## 1. Re-verified construction-site inventory

Same 9 total sites B0 found; re-checked individually before scoping this stage:

- **CLI-only (`bartholomew/cli.py:261,277,291`)** — unchanged, B6's territory.
- **Confirmed still unreachable** (`run_sight_through_runtime_contract`/
  `run_voice_through_runtime_contract`, `runtime_contract.py`): re-checked — still nothing imports
  `identity_interpreter/adapters/sight/pipeline.py` or `.../voice_io/stream_bridge.py` from any
  live route or daemon path. Left untouched (still using
  `bartholomew.orchestrator.safety.parking_brake.construct_parking_brake_off_loop`, unchanged) —
  low priority given confirmed non-reachability; revisit if/when either surface is actually wired
  up.
- **4 real live-daemon sites**, all migrated in this stage: `skill_registry.py`'s
  `_is_blocked_by_brake`, `runtime_contract.py`'s chat and drive Governance gates, and
  `identity_interpreter/orchestrator/orchestrator.py`'s `Orchestrator.handle_input()`.

**New finding, not in B0's or B2's original inventory:** `Orchestrator.handle_input()`'s Parking
Brake check is textually synchronous (`def`, not `async def`), so B0's/B2's "sync call inside
`async def`" search missed it. It's actually reachable on the event loop on **every chat message**:
`app.py`'s `/api/chat` route wraps it in an `async def _respond(...)` closure passed as
`run_chat_through_runtime_contract`'s `respond_fn`. It was also **entirely redundant** on that
mainline path — `run_chat_through_runtime_contract` already gates on Governance *before* ever
calling `respond_fn`, so the redundant call only ever mattered on the `_kernel is None` fallback
path; on the normal path it was a wasted second blocking DB read on every message.

## 2. The CLI/live-daemon split-brain question and its resolution

Once the daemon's 4 live sites read through B3's new schema, `bartholomew/cli.py`'s `brake on`/
`brake off` (unchanged until B6) keeps writing only to the legacy `system_flags` value. Left
unaddressed, the CLI kill switch would stop affecting the running daemon the moment this stage
landed, until B6 closes the gap — an operator-facing safety regression.

**Resolution (approved direction): fail-closed dual-check.** New, explicitly temporary module
`bartholomew/orchestrator/safety/governance_bridge.py`: every live check consults *both* B3's
`GovernanceStore` and the legacy `system_flags` value directly, blocked if **either** source says
blocked. `is_blocked_fail_closed()` (sync) and `is_blocked_fail_closed_off_loop()` (Phase B stage
B2's off-loop pattern) are the two entry points; the module's own docstring states plainly that it
must be deleted, along with `tests/test_governance_bridge_dual_check.py`, in the same B6 change
that retires `bartholomew/cli.py`'s legacy write path.

**A plain read-only OR was not sufficient — caught by the requested regression tests before
merge.** `GovernanceStore.__init__()` imports the legacy `system_flags` row exactly once (B3's own,
correct, one-time-cutover design); nothing in B4 ever calls `GovernanceStore.engage()`/
`disengage()` on the shared instance directly, so a naive `store.is_blocked() or
legacy_is_blocked()` freezes the "new store" side at whatever the legacy value was at that one
moment. `tests/test_end_to_end_tasks_and_audit.py::test_parking_brake_blocks_then_disengage_allows`
caught this directly: engage via the legacy `ParkingBrake`, then disengage via it too — the second
check still reported blocked, because the new store's imported snapshot never re-synced. Fixed by
having every check **mirror** the current legacy value into the store via a real, audited write
(tagged with a distinct `_MIRROR_REASON`) whenever they disagree — but only when the store's own
latest transition isn't already a *genuine* (non-mirror, non-`"migrated"`) engagement, so a mirror
can never silently loosen a real engagement the new store holds independently. A genuinely-engaged
store is never overridden by a mirror (that would be an incorrect loosening from a lower-authority
source); a genuinely-disengaged, migrated, or previously-mirrored store is always kept in sync,
which can only ever tighten it (mirror engage, always safe) or match it (mirror disengage, also
safe — there was no independent restriction to lose). Both this scenario and the four the plan
originally called for are covered in `tests/test_governance_bridge_dual_check.py` (8 tests).

## 3. What changed

**`KernelDaemon`** (`daemon.py`): owns one shared `governance_store` (`GovernanceStore` instance),
constructed off the event loop in `start()` (its `__init__` does blocking schema/state I/O) rather
than eagerly in `__init__` — matching this class's existing pattern of deferring blocking work.
`None` until `start()` completes; every real call site is only reachable after a successful start
(guarded by `_kernel is not None` checks), and `governance_bridge.py`'s fallback tolerates `None`
regardless. Refreshed from disk on every check (`GovernanceStore.refresh()`), not cached-and-stale
— preserving today's "a CLI write takes effect on the very next check" behavior, just against the
new schema (plus the legacy value, per the dual-check bridge).

**`SkillRegistry`**: gains an optional `governance_store` constructor arg and a
`set_governance_store()` setter (used since `GovernanceStore` isn't constructed until `start()`,
after `SkillRegistry` itself). `_is_blocked_by_brake()` now dual-checks via `governance_bridge.py`
instead of constructing its own `ParkingBrake` per call.

**`runtime_contract.py`**: the chat and drive Governance gates now dual-check via
`governance_bridge.py`, passing the shared instance (`daemon.governance_store` /
`getattr(ctx, "governance_store", None)`) when available. The drive path's duck-typed test-context
contract (`docs/B1_SHARED_CONNECTION_POLICY.md` §2 / B2's own note) is preserved unchanged — no new
required attribute, same `getattr(..., None)` graceful-degradation shape B2 already established.

**`Orchestrator.handle_input()`**: gains `skip_governance_check: bool = False`. Its own internal
check (when not skipped) now dual-checks via `governance_bridge.py` too — the fallback path
(`_kernel is None`) has no shared instance to consult, so it constructs a temporary one per check,
same as before this stage but through the new bridge.

**`app.py`**'s `/api/chat`: the mainline path (`_kernel is not None`) now passes
`skip_governance_check=True` — `run_chat_through_runtime_contract` already gated it, closing the
redundant-read finding above. The fallback path (`_kernel is None`) now wraps the *entire*
`orch.handle_input()` call in `asyncio.to_thread(...)` — its synchronous Governance read would
otherwise block the event loop directly inside this `async def` route; this is the one case in this
stage where the whole call, not just the Governance check, needed off-loading, since no daemon
instance exists in that window to route through `run_off_loop`'s executor path.

## 4. Tests

**Temporary** (delete alongside `governance_bridge.py` in B6): `tests/test_governance_bridge_dual_
check.py` (8 tests) — the four scenarios this stage's plan specifically called for: legacy blocked
/ new store clear (stays blocked), new store blocked / legacy clear (stays blocked, both with an
absent and an explicitly-disengaged legacy row), both clear (allows execution, both with and
without a legacy row ever existing); two additional checks needed to trust the bridge isn't
accidentally coarse (scope-specificity, `global`-scope blanket blocking); and the regression test
for the staleness bug §2 describes — constructing a fresh `GovernanceStore` per check (no shared
instance reused, matching how `SkillRegistry` falls back with none configured) and toggling the
legacy value repeatedly, confirming each change is picked up rather than frozen at the first
snapshot.

Re-ran the full governance/runtime-contract/scheduler/lifecycle test set (211 tests, including
`tests/test_orchestration_integration.py`'s 15 `Orchestrator`-specific tests and
`tests/test_end_to_end_tasks_and_audit.py`'s engage-then-disengage end-to-end test) and the
complete non-integration/non-slow suite — both clean.

## 5. Exit condition check

- [x] Every real live-daemon construction site (re-inventoried against the current repository, not
  assumed from B0) uses the one shared instance: `skill_registry.py`, `runtime_contract.py`'s chat
  and drive gates, `Orchestrator.handle_input()`'s mainline path.
- [x] Standalone CLI construction sites (`bartholomew/cli.py`) untouched, per the overview's
  explicit exit condition — not a gap left open by this stage.
- Not required for exit, and not done: sight/voice consolidation (confirmed still unreachable);
  CLI construction-site treatment and process-lock integration (B6); startup/shutdown integrity
  (B5).
