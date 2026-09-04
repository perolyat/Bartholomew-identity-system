"""The live scheduler is what processes captured events (Package A).

The requirement this file exists for is narrow and easy to fake, so it is
worth stating precisely: *the running autonomy loop* must process a captured
event, without anything in the test calling the interpretation function, the
handler, the processor, or the drive.

So the test starts the real `run_scheduler()` loop against real components --
a real `MemoryStore`, a real `SchedulerStore`, a real `ObjectiveStore`, a real
`GovernanceStore`, the real drive registry -- captures an event through the
real inbound seam, and then waits for evidence to appear in the objective's
history. The only thing it does is wait.

`tests/integration/test_event_backbone_end_to_end.py` does the same across a
real HTTP boundary and a real `bartholomew serve` process. This one is the
fast, in-process version of the same claim, so a failure says which layer
broke.
"""

from __future__ import annotations

import asyncio
from datetime import timezone

import pytest

from bartholomew.kernel import inbound_store, objective_store
from bartholomew.kernel.event_processing import store
from bartholomew.kernel.event_processing.adapters import OBSERVATION_NOTE
from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.objective_store import ObjectiveStore
from bartholomew.kernel.runtime_contract import run_inbound_through_runtime_contract
from bartholomew.kernel.scheduler import drives
from bartholomew.kernel.scheduler.loop import run_scheduler
from bartholomew.kernel.scheduler.store import SchedulerStore
from bartholomew.orchestrator.safety import governance_store as gs
from bartholomew.orchestrator.safety.governance_store import GovernanceStore
from bartholomew.runtime.health import SchedulerHeartbeat
from identity_interpreter.identity_context import IdentityContext

ALLOW_CONTEXT = IdentityContext(
    tool_use_default_allowed=False,
    tool_use_allowlist=[
        "objective_record",
        "inbound_capture",
        "inbound_event_process",
        "inbound_event_processing",
    ],
)

#: How long the test will wait for the loop to get round to the drive. The
#: cadence below is one second, so this is many chances, not one.
WAIT_SECONDS = 30.0


class _SchedulerCtx:
    """A duck-typed scheduler context, the shape `run_scheduler()` accepts.

    The same shape `scheduler/loop.py`'s own tests use. Deliberately not a
    `KernelDaemon`: this test is about the loop reaching the drive, and a full
    daemon would drag in the process lock, the API and the model backend for
    no additional evidence.
    """

    def __init__(self, mem, scheduler_store, objectives, governance):
        self.mem = mem
        self.scheduler_store = scheduler_store
        self.objective_store = objectives
        self.governance_store = governance
        self.identity_context = ALLOW_CONTEXT
        self.blocking_executor = None
        self.scheduler_heartbeat = SchedulerHeartbeat()
        self.tz = timezone.utc
        self.cfg = {
            # The drive's own cadence, through the ordinary config override
            # block -- not a test hook. A deployment tunes it the same way.
            "drives": {drives.INBOUND_EVENT_PROCESSING_DRIVE: "every:1"},
            "event_processing": {"enabled": True, "batch_limit": 5},
        }


@pytest.fixture
async def scheduler(tmp_path, monkeypatch):
    """A running autonomy loop against a real database."""
    monkeypatch.setenv("BARTH_DRIVE_PACE_S", "0")
    monkeypatch.setattr("bartholomew.kernel.scheduler.loop.DRIVE_PACE_S", 0.0)

    mem = MemoryStore(str(tmp_path / "drive.db"))
    await mem.init()
    gs.ensure_schema(mem.db_path)
    inbound_store.ensure_schema(mem.db_path)
    objective_store.ensure_schema(mem.db_path)
    store.ensure_schema(mem.db_path)

    scheduler_store = SchedulerStore(mem.db_path)
    await scheduler_store.ensure_schema()
    ctx = _SchedulerCtx(
        mem,
        scheduler_store,
        ObjectiveStore(mem.db_path),
        GovernanceStore(mem.db_path),
    )
    task = asyncio.create_task(run_scheduler(ctx))
    try:
        yield ctx
    finally:
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=10)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        await scheduler_store.close()
        await mem.close()


async def _wait_for(predicate, *, timeout=WAIT_SECONDS, what="the expected state"):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        value = predicate()
        if value:
            return value
        await asyncio.sleep(0.2)
    raise AssertionError(f"the scheduler never reached {what} within {timeout}s")


async def _capture(db_path, *, event_id, payload, event_type=OBSERVATION_NOTE):
    return await run_inbound_through_runtime_contract(
        db_path=db_path,
        source_id="src-roofer",
        event_id=event_id,
        event_type=event_type,
        payload=payload,
        verified_by="test-resolver",
        occurred_at="2026-08-30T09:00:00Z",
    )


# ---------------------------------------------------------------------------


def test_the_drive_is_registered_by_default():
    class Ctx:
        cfg: dict = {}

    assert drives.INBOUND_EVENT_PROCESSING_DRIVE in drives.resolve_registry(Ctx())


def test_the_drive_is_absent_when_the_backbone_is_disabled():
    class Ctx:
        cfg = {"event_processing": {"enabled": False}}

    assert drives.INBOUND_EVENT_PROCESSING_DRIVE not in drives.resolve_registry(Ctx())


def test_the_environment_kill_switch_can_only_turn_the_drive_off(monkeypatch):
    class On:
        cfg = {"event_processing": {"enabled": True}}

    class Off:
        cfg = {"event_processing": {"enabled": False}}

    monkeypatch.setenv("BARTH_EVENT_PROCESSING_ENABLED", "0")
    assert drives.INBOUND_EVENT_PROCESSING_DRIVE not in drives.resolve_registry(On())

    # And the same variable set the other way cannot switch it back on: only
    # the config file can do that.
    monkeypatch.setenv("BARTH_EVENT_PROCESSING_ENABLED", "1")
    assert drives.INBOUND_EVENT_PROCESSING_DRIVE not in drives.resolve_registry(Off())


def test_the_drive_is_not_exempt_from_identity_policy():
    """A self-maintenance exemption here would mean the allowlist entry in
    Identity.yaml did nothing, and turning processing off through policy would
    silently not work."""
    from bartholomew.kernel.runtime_contract import _SELF_MAINTENANCE_DRIVES

    assert drives.INBOUND_EVENT_PROCESSING_DRIVE not in _SELF_MAINTENANCE_DRIVES


@pytest.mark.asyncio
async def test_the_running_scheduler_processes_a_captured_event(scheduler):
    """The acceptance claim, in process.

    Note what this test never calls: `interpret_captured_event`, the handler,
    `process_batch`, or the drive. It captures, and then it waits.
    """
    objectives = scheduler.objective_store
    roof = objectives.open(
        title="Get the roof repaired",
        outcome_statement="The roof is repaired and no longer leaking",
    )
    captured = await _capture(
        scheduler.mem.db_path,
        event_id="evt-1",
        payload={"subject": "Roof repair", "body": "Roofer confirmed attendance Tuesday."},
    )
    assert captured.captured is True

    evidence = await _wait_for(
        lambda: objectives.evidence_events(roof.id),
        what="the captured event being attached as evidence",
    )
    assert len(evidence) == 1
    assert "Roofer confirmed attendance Tuesday." in evidence[0].summary
    assert evidence[0].provenance["event_id"] == "evt-1"
    assert evidence[0].event_kind == objective_store.EVENT_FACT

    record = store.get(scheduler.mem.db_path, "src-roofer", "evt-1")
    assert record.state == store.STATE_PROCESSED


@pytest.mark.asyncio
async def test_the_running_scheduler_records_a_tick_for_the_drive(scheduler):
    await _capture(
        scheduler.mem.db_path,
        event_id="evt-tick",
        payload={"body": "Your parcel was delivered."},
    )

    def processed():
        record = store.get(scheduler.mem.db_path, "src-roofer", "evt-tick")
        return record if record and record.terminal else None

    record = await _wait_for(processed, what="a terminal disposition")
    assert record.state == store.STATE_IRRELEVANT

    # The scheduler's own durable activity record names the drive, so an
    # operator reading `ticks` can see the backbone running.
    import sqlite3

    conn = sqlite3.connect(scheduler.mem.db_path)
    try:
        rows = conn.execute(
            "SELECT success FROM ticks WHERE task_id = ?",
            (drives.INBOUND_EVENT_PROCESSING_DRIVE,),
        ).fetchall()
    finally:
        conn.close()
    assert rows, "the scheduler recorded no tick for the processing drive"
    assert any(r[0] == 1 for r in rows)


@pytest.mark.asyncio
async def test_the_running_scheduler_holds_the_backlog_while_the_brake_is_engaged(scheduler):
    """Engaged, nothing moves; released, the preserved backlog drains."""
    objectives = scheduler.objective_store
    roof = objectives.open(title="Get the roof repaired")

    scheduler.governance_store.engage("global", reason="test", actor="test")
    # Written straight to the capture store rather than through the governed
    # seam, because that seam is *itself* braked -- an event cannot be captured
    # during a halt at all. This stands in for an event captured before the
    # brake was engaged, which is the case the backlog has to survive, and
    # writing it after engaging removes the race where the one-second drive
    # cadence processes it before the brake lands.
    inbound_store.capture_event(
        scheduler.mem.db_path,
        source_id="src-roofer",
        event_id="evt-braked",
        event_type=OBSERVATION_NOTE,
        occurred_at="2026-08-30T09:00:00Z",
        payload={"subject": "Roof repair", "body": "Roofer confirmed attendance Tuesday."},
        outcome=inbound_store.OUTCOME_CAPTURED,
        governance_reason=None,
        verified_by="test-resolver",
        runtime_id=None,
    )

    # Long enough for several cadence periods to pass with the brake on.
    await asyncio.sleep(3.0)
    assert objectives.evidence_events(roof.id) == []
    record = store.get(scheduler.mem.db_path, "src-roofer", "evt-braked")
    if record is not None:
        assert record.state == store.STATE_CAPTURED
        assert record.attempts == 0

    scheduler.governance_store.disengage(reason="test", actor="test")
    evidence = await _wait_for(
        lambda: objectives.evidence_events(roof.id),
        what="processing resuming after the brake was released",
    )
    assert len(evidence) == 1
