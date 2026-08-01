"""
Phase B, stage B9: adversarial validation against the integrated B0-B8
result.

Per docs/PHASE_B_OVERVIEW.md's B9 scope: partial migrations, interrupted
operations, concurrent CLI attempts, and final cross-stage invariant
validation. Not a re-test of what each earlier stage's own suite already
covers (DedicatedDbExecutor's timeout/idempotency semantics, B5's failed-
start draining, B6's second-daemon-refusal) -- these tests specifically
exercise integrated, cross-stage scenarios no single earlier stage's own
test file was positioned to cover.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import sqlite3

import pytest

from bartholomew.kernel.governance import schema
from bartholomew.kernel.governance.brake_store import GovernanceBrakeStore

# =============================================================================
# Concurrent CLI attempts: two real OS processes racing engage()/disengage()
# =============================================================================


def _cli_engage_worker(db_path: str, scope: str, result_queue) -> None:
    try:
        store = GovernanceBrakeStore(db_path)
        state = store.engage(scope, actor="cli")
        result_queue.put(("ok", scope, sorted(state.scopes)))
    except Exception as e:
        result_queue.put(("error", scope, str(e)))


def test_concurrent_cli_engage_from_two_real_processes_loses_no_scope(tmp_path):
    """
    The adversarial scenario B6's "brake on/off must not be write-fenced"
    decision (docs/B6_IMPLEMENTATION.md section 4) accepts as a real
    possibility: two operators (or one operator running the command twice
    in quick succession, e.g. from two terminals) issue `bartholomew brake
    on --scope X` and `--scope Y` at genuinely the same time, from two
    separate OS processes, not just two objects in one process (which
    tests/test_governance_brake_store.py already covers). GovernanceBrakeStore's
    atomic, revision-tracked transactions must still union both scopes in,
    not lose one to a lost-update race.
    """
    db_path = str(tmp_path / "concurrent_cli.db")
    schema.ensure_schema(db_path)

    ctx = multiprocessing.get_context("spawn")
    result_queue: multiprocessing.Queue = ctx.Queue()
    p1 = ctx.Process(target=_cli_engage_worker, args=(db_path, "skills", result_queue))
    p2 = ctx.Process(target=_cli_engage_worker, args=(db_path, "sight", result_queue))

    p1.start()
    p2.start()
    p1.join(timeout=15.0)
    p2.join(timeout=15.0)

    assert not p1.is_alive()
    assert not p2.is_alive()

    results = [result_queue.get(timeout=1.0), result_queue.get(timeout=1.0)]
    assert all(r[0] == "ok" for r in results), f"a worker process errored: {results}"

    final_state = GovernanceBrakeStore(db_path).current_state()
    assert final_state.engaged is True
    assert final_state.scopes == frozenset({"skills", "sight"}), (
        "concurrent engage() from two real processes lost a scope -- "
        f"final state was {sorted(final_state.scopes)}"
    )
    # Two real transitions landed, not one clobbering the other.
    assert final_state.revision == 2


def _cli_disengage_worker(db_path: str, result_queue) -> None:
    try:
        store = GovernanceBrakeStore(db_path)
        store.disengage(confirm=True, actor="cli")
        result_queue.put(("ok",))
    except Exception as e:
        result_queue.put(("error", str(e)))


def test_concurrent_engage_and_disengage_from_two_processes_stays_coherent(tmp_path):
    """
    A harder race: one process trying to engage while another
    simultaneously disengages. Whichever transition's transaction commits
    second wins outright (no expected_revision passed by either, matching
    how the CLI actually calls these methods today) -- the invariant this
    test actually protects is narrower and non-negotiable regardless of
    which one wins: the final state must be a real, fully-formed state one
    of the two transitions actually produced, never a torn/partial mix of
    both (e.g. engaged=True with an empty scope set that neither call
    would have produced on its own).
    """
    db_path = str(tmp_path / "concurrent_race.db")
    schema.ensure_schema(db_path)
    GovernanceBrakeStore(db_path).engage("global")  # baseline: something to disengage

    ctx = multiprocessing.get_context("spawn")
    result_queue: multiprocessing.Queue = ctx.Queue()
    p1 = ctx.Process(target=_cli_engage_worker, args=(db_path, "skills", result_queue))
    p2 = ctx.Process(target=_cli_disengage_worker, args=(db_path, result_queue))

    p1.start()
    p2.start()
    p1.join(timeout=15.0)
    p2.join(timeout=15.0)

    results = [result_queue.get(timeout=1.0), result_queue.get(timeout=1.0)]
    assert all(r[0] == "ok" for r in results), f"a worker process errored: {results}"

    final_state = GovernanceBrakeStore(db_path).current_state()
    # With BEGIN IMMEDIATE serializing the two transactions (Phase B, stage
    # B9's fix -- see brake_store.py's _apply_transition() docstring),
    # exactly two outcomes are possible depending purely on commit order,
    # each a complete, correctly-computed result of the *second* writer
    # reading the *first* writer's already-committed state -- never a torn
    # mix of both:
    #   - engage() commits first (reads {global} -> {global, skills}), then
    #     disengage() commits second (unconditionally clears everything
    #     regardless of what it reads) -> final: disengaged, no scopes.
    #   - disengage() commits first (reads {global} -> clears it), then
    #     engage() commits second (reads {} -> unions to just {skills})
    #     -> final: engaged, scopes={skills} only ("global" is gone for
    #     good, since disengage() ran against it first).
    valid_outcomes = [
        (False, frozenset()),
        (True, frozenset({"skills"})),
    ]
    assert (final_state.engaged, final_state.scopes) in valid_outcomes, (
        f"torn state after concurrent engage/disengage: engaged={final_state.engaged}, "
        f"scopes={sorted(final_state.scopes)}"
    )
    assert final_state.revision == 3  # baseline engage + both racing transitions


# =============================================================================
# Partial migration: legacy system_flags value must be inert once B3's
# schema is live, even if it still exists and diverges from the truth.
# =============================================================================


def test_stale_legacy_flag_is_never_consulted_once_b3_schema_exists(tmp_path):
    """
    A genuinely adversarial "partial migration" scenario: the legacy
    system_flags "parking_brake" key still exists (e.g. never cleaned up)
    and has since drifted to say something different from the live
    GovernanceBrakeStore state (someone -- or old code -- wrote to it after
    migration). check_scope_blocked() (the one path every live Governance
    gate uses since B4/B6) must be reading GovernanceBrakeStore's state
    exclusively and never fall back to or get confused by the stale legacy
    value.
    """
    from bartholomew.orchestrator.safety.parking_brake import check_scope_blocked

    db_path = str(tmp_path / "partial_migration.db")

    # Migrate first (creates parking_brake_state with engaged=False).
    schema.ensure_schema(db_path)

    # Now simulate the legacy flag drifting to say something different --
    # e.g. an old script, or a human, wrote directly to system_flags after
    # migration, which check_scope_blocked() must never read.
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS system_flags "
            "(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)",
        )
        conn.execute(
            "INSERT OR REPLACE INTO system_flags VALUES "
            "('parking_brake', '{\"engaged\": true, \"scopes\": [\"global\"]}', '0')",
        )
        conn.commit()

    # The live schema still says disengaged -- and that's what must govern.
    assert check_scope_blocked(db_path, "skills") is False

    # Now engage for real, through the live path.
    GovernanceBrakeStore(db_path).engage("skills")
    assert check_scope_blocked(db_path, "skills") is True

    # The stale legacy flag is still sitting there, unread and untouched --
    # confirms this isn't passing by accident (e.g. some code path deleting
    # it along the way).
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM system_flags WHERE key = 'parking_brake'",
        ).fetchone()
    assert row is not None
    assert '"engaged": true' in row[0]  # untouched, still says the stale thing


# =============================================================================
# Interrupted operations: a cancelled/failed request must still release its
# admission token (Phase B, stage B7).
# =============================================================================


@pytest.mark.asyncio
async def test_admission_token_released_when_wrapped_work_raises():
    from bartholomew.kernel.admission_gate import AdmissionGate

    gate = AdmissionGate()
    token = await gate.admit("interrupted-request")

    async def _do_work():
        try:
            raise RuntimeError("simulated handler failure mid-request")
        finally:
            await token.release()

    with pytest.raises(RuntimeError):
        await _do_work()

    assert gate.in_flight_count == 0
    assert await gate.drain(timeout=1.0) is True


@pytest.mark.asyncio
async def test_admission_token_released_when_wrapped_work_is_cancelled():
    """Simulates a client disconnecting mid-request (the ASGI server
    cancels the handler task) -- app.py's middleware releases the token in
    a `finally`, which must still run on cancellation."""
    from bartholomew.kernel.admission_gate import AdmissionGate

    gate = AdmissionGate()
    token = await gate.admit("client-disconnected")

    async def _slow_handler():
        try:
            await asyncio.sleep(30.0)
        finally:
            await token.release()

    task = asyncio.create_task(_slow_handler())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert gate.in_flight_count == 0
