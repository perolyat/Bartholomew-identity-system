"""
The skill registry's execution contract, exercised through the production
chokepoint `SkillRegistry.execute_action()`.

docs/SKILL_EXECUTION_CONCURRENCY_CONTRACT.md states the contract; every test
here is one clause of it, driven through the real registry (Governance
stages, audit, Reflection) with a gated skill whose actions block, hang,
raise, call back into the registry or return at once -- so contention is
deterministic (events, not sleeps) and each outcome is asserted, not timed.
Two tests run the bundled TasksSkill and NotifySkill instead, because the
defect that led to this contract was only visible through real skills: once
their writes moved off the event loop (PR #110), a request arriving while
another action was executing was refused as "Skill not ready".

Timing appears only as hang guards (a deadline on a poll), never as the
thing being asserted.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
import threading
import time
from unittest.mock import patch

import pytest
import yaml

from bartholomew.kernel import skill_registry as skill_registry_module
from bartholomew.kernel.blocking_executor import SingleWorkerExecutor, run_off_loop
from bartholomew.kernel.memory.privacy_guard import set_consent_handler
from bartholomew.kernel.skill_base import SkillBase, SkillContext, SkillResult, SkillState
from bartholomew.kernel.skill_permissions import reset_permission_checker
from bartholomew.kernel.skill_registry import SkillRegistry, reset_skill_registry

pytestmark = pytest.mark.asyncio

ACTIONS = [
    "quick",
    "block",
    "hang",
    "raise_after_entry",
    "swallow_cancel",
    "call_self",
    "call_other",
    "call_cycle",
    "spawn_later",
    "spawn_now",
    "raise_on_cancel",
    "swallow_cancel_fast",
]

HANG_GUARD_S = 10.0


class GatedSkill(SkillBase):
    """A skill whose actions are controlled by the test, one event per phase."""

    def __init__(self, skill_id: str = "alpha") -> None:
        super().__init__()
        self._id = skill_id
        self.registry: SkillRegistry | None = None
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls: list[int] = []
        self.completed: list[int] = []
        self.active = 0
        self.max_active = 0
        self.cancelled = 0
        self.shutdown_called = False
        self.go = asyncio.Event()
        self.background: asyncio.Task | None = None
        self.background_result: SkillResult | None = None
        self.swallowed = asyncio.Event()
        self.transitions: list[tuple[str, str]] = []

    def _set_state(self, state: SkillState) -> None:  # record every transition
        self.transitions.append((self._state.value, state.value))
        super()._set_state(state)

    @property
    def skill_id(self) -> str:
        return self._id

    async def initialize(self, context: SkillContext) -> None:
        self._context = context

    async def shutdown(self) -> None:
        self.shutdown_called = True

    async def execute(self, action: str, params: dict | None = None) -> SkillResult:
        params = params or {}
        n = int(params.get("n", 0))
        self.calls.append(n)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.entered.set()
        try:
            if action == "quick":
                await asyncio.sleep(0)  # a suspension point, so overlap would be visible
                return SkillResult.ok(data={"n": n})
            if action == "block":
                await self.release.wait()
                return SkillResult.ok(data={"n": n})
            if action == "hang":
                await asyncio.Event().wait()
            if action == "raise_after_entry":
                await self.release.wait()
                raise RuntimeError("boom")
            if action == "swallow_cancel":
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.swallowed.set()  # a misbehaving skill: ignores the first cancellation
                await asyncio.sleep(0.5)
                return SkillResult.ok(data={"n": n, "late": True})
            if action == "swallow_cancel_fast":
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.swallowed.set()
                return SkillResult.ok(data={"n": n, "late": True})
            if action == "raise_on_cancel":
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    raise RuntimeError("cleanup failed") from None
            if action == "call_self":
                assert self.registry is not None
                return await self.registry.execute_action(self._id, "quick", {"n": n + 100})
            if action == "call_other":
                assert self.registry is not None
                return await self.registry.execute_action(
                    params["other"],
                    "quick",
                    {"n": n + 100},
                )
            if action == "call_cycle":
                assert self.registry is not None
                return await self.registry.execute_action(
                    params["other"],
                    "call_other",
                    {"n": n + 100, "other": self._id},
                )
            if action in ("spawn_later", "spawn_now"):
                # A detached task that calls this same skill: after the
                # action has ended (spawn_later) or while it still runs.
                assert self.registry is not None

                async def later() -> None:
                    if action == "spawn_later":
                        await self.go.wait()
                    assert self.registry is not None
                    self.background_result = await self.registry.execute_action(
                        self._id,
                        "quick",
                        {"n": n + 100},
                    )

                self.background = asyncio.create_task(later())
                if action == "spawn_now":
                    await self.release.wait()
                return SkillResult.ok(data={"n": n})
            return SkillResult.fail(f"Unknown action: {action}")
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        finally:
            self.active -= 1
            self.completed.append(n)


class SyncSkill(GatedSkill):
    """A mis-written skill: execute() is not a coroutine."""

    def execute(self, action: str, params: dict | None = None) -> SkillResult:  # type: ignore[override]
        self.calls.append(int((params or {}).get("n", 0)))
        return SkillResult.ok(data={"sync": True})


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except (PermissionError, FileNotFoundError):
        pass


@pytest.fixture
def skills_dir(tmp_path):
    d = tmp_path / "skills"
    d.mkdir()
    for sid in ("alpha", "beta"):
        manifest = {
            "skill_id": sid,
            "name": sid,
            "version": "1.0.0",
            "description": f"gated test skill {sid}",
            "entry_module": __name__,
            "entry_class": "GatedSkill",
            "permissions": {
                "level": "auto",
                "requires": [],
                "sandbox": {"filesystem": [], "network": []},
            },
            "subscriptions": [],
            "emits": [],
            "actions": [{"name": a, "description": a, "parameters": []} for a in ACTIONS],
            "author": "test",
            "enabled": True,
        }
        with open(d / f"{sid}.yaml", "w") as f:
            yaml.safe_dump(manifest, f)
    return d


@pytest.fixture(autouse=True)
def reset_singletons():
    reset_skill_registry()
    reset_permission_checker()
    yield
    reset_skill_registry()
    reset_permission_checker()


async def registry_with(skills_dir, temp_db, skills: dict[str, GatedSkill], **kw) -> SkillRegistry:
    registry = SkillRegistry(skills_dir=skills_dir, db_path=temp_db, **kw)
    for skill in skills.values():
        skill.registry = registry
    with patch.object(registry, "_instantiate_skill", side_effect=lambda m: skills[m.skill_id]):
        for sid in skills:
            assert await registry.load_skill(sid) is True
    return registry


async def until(predicate, what: str) -> None:
    """Yield to the loop until `predicate()` holds; the deadline is a hang guard."""
    deadline = time.monotonic() + HANG_GUARD_S
    while not predicate():
        assert time.monotonic() < deadline, f"gave up waiting for: {what}"
        await asyncio.sleep(0)


def waiters(registry: SkillRegistry, skill_id: str) -> int:
    return registry._loaded[skill_id].occupancy.waiters


def audit_rows(db_path: str, skill_id: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM skill_action_audit WHERE skill_id = ?",
            (skill_id,),
        ).fetchone()[0]
    finally:
        conn.close()


def query(db_path: str, sql: str) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Occupancy: overlapping requests to one skill wait their turn
# ---------------------------------------------------------------------------


async def test_a_request_arriving_while_the_skill_is_running_waits_its_turn(skills_dir, temp_db):
    alpha = GatedSkill("alpha")
    registry = await registry_with(skills_dir, temp_db, {"alpha": alpha})

    first = asyncio.create_task(registry.execute_action("alpha", "block", {"n": 1}))
    await alpha.entered.wait()
    assert alpha.state is SkillState.RUNNING

    second = asyncio.create_task(registry.execute_action("alpha", "quick", {"n": 2}))
    await until(lambda: waiters(registry, "alpha") == 1, "the second request to queue")
    assert alpha.calls == [1], "the second request must not run while the first executes"

    alpha.release.set()
    r1, r2 = await asyncio.gather(first, second)
    assert r1.success, r1.error
    assert r2.success, f"second request was refused instead of queued: {r2.error}"
    assert alpha.calls == [1, 2]
    assert alpha.max_active == 1
    assert alpha.state is SkillState.READY
    assert waiters(registry, "alpha") == 0


async def test_simultaneous_requests_each_execute_exactly_once_and_never_overlap(
    skills_dir,
    temp_db,
):
    alpha = GatedSkill("alpha")
    registry = await registry_with(skills_dir, temp_db, {"alpha": alpha})

    results = await asyncio.gather(
        *[registry.execute_action("alpha", "quick", {"n": n}) for n in range(1, 6)],
    )
    assert all(r.success for r in results), [r.error for r in results]
    assert sorted(alpha.calls) == [1, 2, 3, 4, 5]
    assert alpha.max_active == 1
    assert alpha.state is SkillState.READY
    assert audit_rows(temp_db, "alpha") == 5


async def test_independent_skills_do_not_wait_on_each_other(skills_dir, temp_db):
    alpha, beta = GatedSkill("alpha"), GatedSkill("beta")
    registry = await registry_with(skills_dir, temp_db, {"alpha": alpha, "beta": beta})

    first = asyncio.create_task(registry.execute_action("alpha", "block", {"n": 1}))
    await alpha.entered.wait()

    # beta completes while alpha is still occupied.
    r_beta = await asyncio.wait_for(
        registry.execute_action("beta", "quick", {"n": 2}),
        HANG_GUARD_S,
    )
    assert r_beta.success, r_beta.error
    assert alpha.state is SkillState.RUNNING
    assert beta.calls == [2]

    alpha.release.set()
    assert (await first).success


async def test_a_real_skill_request_arriving_inside_the_window_gets_a_real_outcome(temp_db):
    """The defect that led to this contract, through the bundled TasksSkill."""
    registry = SkillRegistry(db_path=temp_db)
    assert await registry.load_skill("tasks") is True
    instance = registry._loaded["tasks"].instance

    first = asyncio.create_task(registry.execute_action("tasks", "create", {"title": "first"}))
    await until(
        lambda: first.done() or instance.state is SkillState.RUNNING,
        "the first create to reach its RUNNING window",
    )
    assert instance.state is SkillState.RUNNING, "the window must be observable from another task"

    second = await registry.execute_action("tasks", "create", {"title": "second"})
    assert (await first).success
    assert second.success, f"a request inside the window was refused: {second.error}"
    assert query(temp_db, "SELECT title FROM skill_tasks ORDER BY title") == [
        ("first",),
        ("second",),
    ]


async def test_queued_blocking_work_extends_the_window_but_never_refuses_a_request(temp_db):
    """A blocking job queued on the daemon's single worker stretches the
    action in flight; the request behind it still waits and succeeds."""
    executor = SingleWorkerExecutor(label="contract-test")
    registry = SkillRegistry(db_path=temp_db, blocking_executor=executor)
    try:
        assert await registry.load_skill("tasks") is True
        instance = registry._loaded["tasks"].instance

        first = asyncio.create_task(
            registry.execute_action("tasks", "create", {"title": "first"}),
        )
        await until(
            lambda: first.done() or instance.state is SkillState.RUNNING,
            "the first create to reach its RUNNING window",
        )
        assert instance.state is SkillState.RUNNING
        # Queue 300 ms of unrelated blocking work ahead of the action's own
        # write, then arrive inside the stretched window.
        blocker = asyncio.create_task(run_off_loop(time.sleep, 0.3, executor=executor))
        await asyncio.sleep(0.05)
        assert instance.state is SkillState.RUNNING

        second = await registry.execute_action("tasks", "create", {"title": "second"})
        assert (await first).success
        assert second.success, f"refused behind queued blocking work: {second.error}"
        await blocker
        assert len(query(temp_db, "SELECT title FROM skill_tasks")) == 2
    finally:
        await executor.close()


# ---------------------------------------------------------------------------
# Bounded waiting and bounded execution
# ---------------------------------------------------------------------------


async def test_waiting_is_bounded_and_a_refusal_leaves_the_action_in_flight_untouched(
    skills_dir,
    temp_db,
):
    alpha = GatedSkill("alpha")
    registry = await registry_with(skills_dir, temp_db, {"alpha": alpha}, queue_timeout=0.2)

    first = asyncio.create_task(registry.execute_action("alpha", "block", {"n": 1}))
    await alpha.entered.wait()

    second = await asyncio.wait_for(
        registry.execute_action("alpha", "quick", {"n": 2}),
        HANG_GUARD_S,
    )
    assert not second.success
    assert "Skill busy" in (second.error or ""), second.error
    assert "block" in second.error, "the refusal names the action it waited behind"
    assert alpha.state is SkillState.RUNNING
    assert alpha.calls == [1]
    assert waiters(registry, "alpha") == 0

    alpha.release.set()
    assert (await first).success
    third = await registry.execute_action("alpha", "quick", {"n": 3})
    assert third.success, third.error
    assert alpha.calls == [1, 3]
    assert audit_rows(temp_db, "alpha") == 3, "every attempt, refusal included, is audited once"


async def test_execution_is_bounded_and_the_skill_returns_to_ready(skills_dir, temp_db):
    alpha = GatedSkill("alpha")
    registry = await registry_with(skills_dir, temp_db, {"alpha": alpha}, execution_timeout=0.2)

    result = await asyncio.wait_for(
        registry.execute_action("alpha", "hang", {"n": 1}),
        HANG_GUARD_S,
    )
    assert not result.success
    assert "timed out" in (result.error or ""), result.error
    assert alpha.cancelled == 1, "the hung action was cancelled"
    assert alpha.active == 0
    assert alpha.state is SkillState.READY

    again = await registry.execute_action("alpha", "quick", {"n": 2})
    assert again.success, again.error
    assert alpha.calls == [1, 2]


async def test_a_skill_that_swallows_cancellation_is_abandoned_not_waited_for_forever(
    skills_dir,
    temp_db,
):
    alpha = GatedSkill("alpha")
    registry = await registry_with(skills_dir, temp_db, {"alpha": alpha}, execution_timeout=0.2)

    with patch.object(skill_registry_module, "_SETTLE_TIMEOUT_S", 0.2):
        result = await asyncio.wait_for(
            registry.execute_action("alpha", "swallow_cancel", {"n": 1}),
            HANG_GUARD_S,
        )
    assert not result.success
    assert "timed out" in (result.error or ""), result.error
    assert alpha.state is SkillState.READY

    again = await registry.execute_action("alpha", "quick", {"n": 2})
    assert again.success, again.error
    # Let the abandoned action finish on its own so nothing is left pending.
    await until(lambda: 1 in alpha.completed, "the abandoned action to finish")


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


async def test_cancelling_the_request_in_flight_closes_the_window_and_serves_the_next(
    skills_dir,
    temp_db,
):
    alpha = GatedSkill("alpha")
    registry = await registry_with(skills_dir, temp_db, {"alpha": alpha})

    first = asyncio.create_task(registry.execute_action("alpha", "block", {"n": 1}))
    await alpha.entered.wait()
    second = asyncio.create_task(registry.execute_action("alpha", "quick", {"n": 2}))
    await until(lambda: waiters(registry, "alpha") == 1, "the second request to queue")

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert alpha.cancelled == 1
    assert alpha.active == 0

    r2 = await asyncio.wait_for(second, HANG_GUARD_S)
    assert r2.success, f"the queued request did not get its turn: {r2.error}"
    assert alpha.calls == [1, 2]
    assert alpha.state is SkillState.READY
    assert waiters(registry, "alpha") == 0


async def test_cancelling_a_waiting_request_leaves_no_trace(skills_dir, temp_db):
    alpha = GatedSkill("alpha")
    registry = await registry_with(skills_dir, temp_db, {"alpha": alpha})

    first = asyncio.create_task(registry.execute_action("alpha", "block", {"n": 1}))
    await alpha.entered.wait()
    second = asyncio.create_task(registry.execute_action("alpha", "quick", {"n": 2}))
    await until(lambda: waiters(registry, "alpha") == 1, "the second request to queue")

    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second
    assert waiters(registry, "alpha") == 0

    alpha.release.set()
    assert (await first).success
    third = await asyncio.wait_for(
        registry.execute_action("alpha", "quick", {"n": 3}),
        HANG_GUARD_S,
    )
    assert third.success, third.error
    assert alpha.calls == [1, 3], "the cancelled waiter never ran"


# ---------------------------------------------------------------------------
# Exceptions, ERROR, recovery
# ---------------------------------------------------------------------------


async def test_an_exception_marks_the_skill_error_and_a_queued_request_never_masks_it(
    skills_dir,
    temp_db,
):
    alpha = GatedSkill("alpha")
    registry = await registry_with(skills_dir, temp_db, {"alpha": alpha})

    first = asyncio.create_task(registry.execute_action("alpha", "raise_after_entry", {"n": 1}))
    await alpha.entered.wait()
    second = asyncio.create_task(registry.execute_action("alpha", "quick", {"n": 2}))
    await until(lambda: waiters(registry, "alpha") == 1, "the second request to queue")

    alpha.release.set()
    r1, r2 = await asyncio.gather(first, second)
    assert not r1.success and "boom" in (r1.error or "")
    assert alpha.state is SkillState.ERROR
    assert not r2.success
    assert "not ready" in (r2.error or "").lower() and "error" in (r2.error or ""), r2.error
    assert alpha.calls == [1], "a request queued behind a failure never runs on the broken skill"

    # Recovery is explicit: reload the skill.
    replacement = GatedSkill("alpha")
    with patch.object(registry, "_instantiate_skill", return_value=replacement):
        assert await registry.reload_skill("alpha") is True
    assert registry._loaded["alpha"].instance is replacement
    assert replacement.state is SkillState.READY
    r3 = await registry.execute_action("alpha", "quick", {"n": 3})
    assert r3.success, r3.error


# ---------------------------------------------------------------------------
# Re-entrancy
# ---------------------------------------------------------------------------


async def test_re_entrant_requests_are_refused_never_deadlocked(skills_dir, temp_db):
    alpha, beta = GatedSkill("alpha"), GatedSkill("beta")
    registry = await registry_with(skills_dir, temp_db, {"alpha": alpha, "beta": beta})

    # An action asking for another action on its own skill.
    result = await asyncio.wait_for(
        registry.execute_action("alpha", "call_self", {"n": 1}),
        HANG_GUARD_S,
    )
    assert not result.success
    assert "Re-entrant" in (result.error or ""), result.error
    assert alpha.calls == [1], "the nested action never ran"
    assert alpha.state is SkillState.READY

    # A nested call to a different skill is ordinary.
    result = await asyncio.wait_for(
        registry.execute_action("alpha", "call_other", {"n": 2, "other": "beta"}),
        HANG_GUARD_S,
    )
    assert result.success, result.error
    assert beta.calls == [102]

    # A cycle across skills (alpha -> beta -> alpha) is refused at the point
    # it closes, and both skills are free afterwards.
    result = await asyncio.wait_for(
        registry.execute_action("alpha", "call_cycle", {"n": 3, "other": "beta"}),
        HANG_GUARD_S,
    )
    assert not result.success
    assert "Re-entrant" in (result.error or ""), result.error
    assert alpha.state is SkillState.READY and beta.state is SkillState.READY
    assert waiters(registry, "alpha") == 0 and waiters(registry, "beta") == 0


async def test_a_task_spawned_by_an_action_may_call_the_skill_once_that_action_has_ended(
    skills_dir,
    temp_db,
):
    alpha = GatedSkill("alpha")
    registry = await registry_with(skills_dir, temp_db, {"alpha": alpha})

    # Spawned during the action, calling only after it has ended: ordinary.
    result = await registry.execute_action("alpha", "spawn_later", {"n": 1})
    assert result.success, result.error
    alpha.go.set()
    assert alpha.background is not None
    await asyncio.wait_for(alpha.background, HANG_GUARD_S)
    assert alpha.background_result is not None and alpha.background_result.success
    assert alpha.calls == [1, 101]

    # Spawned during the action and calling while it still runs: refused as
    # re-entrant (the action may be waiting for that task), never deadlocked.
    alpha.background = None
    alpha.background_result = None
    first = asyncio.create_task(registry.execute_action("alpha", "spawn_now", {"n": 2}))
    await until(lambda: alpha.background_result is not None, "the detached call to return")
    assert not alpha.background_result.success
    assert "Re-entrant" in (alpha.background_result.error or ""), alpha.background_result.error
    alpha.release.set()
    assert (await asyncio.wait_for(first, HANG_GUARD_S)).success
    assert alpha.calls == [1, 101, 2]
    assert alpha.state is SkillState.READY


# ---------------------------------------------------------------------------
# Governance while waiting
# ---------------------------------------------------------------------------


async def test_a_brake_engaged_while_the_request_waited_still_blocks_it(skills_dir, temp_db):
    alpha = GatedSkill("alpha")
    registry = await registry_with(skills_dir, temp_db, {"alpha": alpha})
    engaged = {"value": False}

    async def brake() -> bool:
        return engaged["value"]

    registry._is_blocked_by_brake = brake  # type: ignore[method-assign]

    first = asyncio.create_task(registry.execute_action("alpha", "block", {"n": 1}))
    await alpha.entered.wait()
    second = asyncio.create_task(registry.execute_action("alpha", "quick", {"n": 2}))
    await until(lambda: waiters(registry, "alpha") == 1, "the second request to queue")

    engaged["value"] = True  # the brake lands while the second request waits
    alpha.release.set()
    r1, r2 = await asyncio.gather(first, second)
    assert r1.success
    assert not r2.success and "parking brake" in (r2.error or ""), r2.error
    assert alpha.calls == [1]
    assert alpha.state is SkillState.READY


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------


async def test_unload_waits_for_the_action_in_flight_and_refuses_the_queued_one(
    skills_dir,
    temp_db,
):
    alpha = GatedSkill("alpha")
    registry = await registry_with(skills_dir, temp_db, {"alpha": alpha})

    first = asyncio.create_task(registry.execute_action("alpha", "block", {"n": 1}))
    await alpha.entered.wait()
    second = asyncio.create_task(registry.execute_action("alpha", "quick", {"n": 2}))
    await until(lambda: waiters(registry, "alpha") == 1, "the second request to queue")

    unloading = asyncio.create_task(registry.unload_skill("alpha"))
    await until(lambda: alpha.state is SkillState.UNLOADING, "unload to mark the skill")
    assert not alpha.shutdown_called, "shutdown must wait for the action in flight"

    # A new arrival is refused at once.
    third = await asyncio.wait_for(
        registry.execute_action("alpha", "quick", {"n": 3}),
        HANG_GUARD_S,
    )
    assert not third.success and "not ready" in (third.error or "").lower(), third.error

    alpha.release.set()
    assert (await first).success
    assert await asyncio.wait_for(unloading, HANG_GUARD_S) is True
    assert alpha.shutdown_called
    r2 = await asyncio.wait_for(second, HANG_GUARD_S)
    assert not r2.success and "not ready" in (r2.error or "").lower(), r2.error
    assert alpha.calls == [1], "the queued request never ran on the unloaded skill"
    assert "alpha" not in registry._loaded

    fourth = await registry.execute_action("alpha", "quick", {"n": 4})
    assert not fourth.success and "not loaded" in (fourth.error or "").lower()


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------


async def test_occupancy_is_reported_while_an_action_is_in_flight(skills_dir, temp_db):
    alpha = GatedSkill("alpha")
    registry = await registry_with(skills_dir, temp_db, {"alpha": alpha})

    first = asyncio.create_task(registry.execute_action("alpha", "block", {"n": 1}))
    await alpha.entered.wait()
    second = asyncio.create_task(registry.execute_action("alpha", "quick", {"n": 2}))
    await until(lambda: waiters(registry, "alpha") == 1, "the second request to queue")

    info = registry.get_skill_info("alpha")
    assert info["state"] == "running"
    assert info["occupancy"]["in_flight_action"] == "block"
    assert info["occupancy"]["waiters"] == 1
    assert info["occupancy"]["in_flight_for_s"] >= 0
    status = registry.get_status()
    assert status["loaded_skills"][0]["occupancy"]["in_flight_action"] == "block"

    alpha.release.set()
    await asyncio.gather(first, second)
    info = registry.get_skill_info("alpha")
    assert info["state"] == "ready"
    assert info["occupancy"] == {"in_flight_action": None, "in_flight_for_s": None, "waiters": 0}


# ---------------------------------------------------------------------------
# The blocking-worker rule: network I/O never rides the storage worker
# ---------------------------------------------------------------------------


async def test_the_notification_webhook_never_rides_the_storage_worker(temp_db, monkeypatch):
    from bartholomew.skills.notify import NotifySkill

    executor = SingleWorkerExecutor(thread_name_prefix="storage-worker-test", label="storage")
    registry = SkillRegistry(db_path=temp_db, blocking_executor=executor)
    seen: dict[str, str] = {}

    def fake_post(url: str, payload: dict) -> bool:
        seen["thread"] = threading.current_thread().name
        return True

    monkeypatch.setattr(NotifySkill, "_post_webhook", staticmethod(fake_post))
    try:
        assert await registry.load_skill("notify") is True
        instance = registry._loaded["notify"].instance
        instance._webhook_url = "http://127.0.0.1:9/hook"

        # Urgent bypasses quiet hours (22:00-07:00 by default), so the post
        # happens whatever the wall clock says when this test runs.
        result = await registry.execute_action(
            "notify",
            "send",
            {"title": "t", "message": "m", "priority": "urgent"},
        )
        assert result.success, result.error
        assert "thread" in seen, "the webhook was not posted"
        assert not seen["thread"].startswith("storage-worker-test"), seen["thread"]
    finally:
        await executor.close()


async def test_the_forecast_fetch_never_rides_the_storage_worker(temp_db, monkeypatch):
    from bartholomew.skills import forecast as forecast_module

    executor = SingleWorkerExecutor(thread_name_prefix="storage-worker-test", label="storage")
    registry = SkillRegistry(db_path=temp_db, blocking_executor=executor)
    seen: dict[str, str] = {}

    def fake_fetch(url: str, params: dict):
        seen["thread"] = threading.current_thread().name
        raise forecast_module.ForecastLookupError("stubbed")

    # The manifest-allowlisted provider host; _fetch is stubbed, nothing is contacted.
    monkeypatch.setenv(
        forecast_module.FORECAST_API_URL_ENV,
        "https://api.open-meteo.com/v1/forecast",
    )
    monkeypatch.setenv(forecast_module.LATITUDE_ENV, "-33.8688")
    monkeypatch.setenv(forecast_module.LONGITUDE_ENV, "151.2093")
    monkeypatch.setattr(forecast_module.ForecastSkill, "_fetch", staticmethod(fake_fetch))
    # The manifest's network.fetch permission is "ask"-level: grant it through
    # the real consent registration, as the forecast tests do.
    set_consent_handler(lambda _prompt: True)
    try:
        assert await registry.load_skill("forecast") is True
        await registry.execute_action("forecast", "lookup", {})
        assert "thread" in seen, "the provider fetch was not attempted"
        assert not seen["thread"].startswith("storage-worker-test"), seen["thread"]
    finally:
        set_consent_handler(None)
        await executor.close()


# ---------------------------------------------------------------------------
# Findings of the adversarial review of the first implementation, each pinned
# ---------------------------------------------------------------------------


async def test_a_request_cancelled_while_its_timed_out_action_settles_still_closes_the_window(
    skills_dir,
    temp_db,
):
    """The timed-out action ignores its cancellation; while the registry waits
    for it to settle, the request itself is cancelled. The window must close."""
    alpha = GatedSkill("alpha")
    registry = await registry_with(skills_dir, temp_db, {"alpha": alpha}, execution_timeout=0.2)

    with patch.object(skill_registry_module, "_SETTLE_TIMEOUT_S", 3.0):
        first = asyncio.create_task(registry.execute_action("alpha", "swallow_cancel", {"n": 1}))
        await asyncio.wait_for(alpha.swallowed.wait(), HANG_GUARD_S)  # now settling
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
    assert alpha.state is SkillState.READY, alpha.state
    assert waiters(registry, "alpha") == 0
    assert registry.get_skill_info("alpha")["occupancy"]["in_flight_action"] is None

    again = await registry.execute_action("alpha", "quick", {"n": 2})
    assert again.success, again.error
    await until(lambda: 1 in alpha.completed, "the abandoned action to finish")


async def test_a_second_cancellation_while_settling_still_closes_the_window(skills_dir, temp_db):
    alpha = GatedSkill("alpha")
    registry = await registry_with(skills_dir, temp_db, {"alpha": alpha})

    with patch.object(skill_registry_module, "_SETTLE_TIMEOUT_S", 3.0):
        first = asyncio.create_task(registry.execute_action("alpha", "swallow_cancel", {"n": 1}))
        await alpha.entered.wait()
        first.cancel()
        await asyncio.wait_for(
            alpha.swallowed.wait(),
            HANG_GUARD_S,
        )  # settling after the first cancel
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
    assert alpha.state is SkillState.READY, alpha.state
    again = await registry.execute_action("alpha", "quick", {"n": 2})
    assert again.success, again.error
    await until(lambda: 1 in alpha.completed, "the abandoned action to finish")


async def test_an_unload_arriving_during_the_brake_re_check_is_never_overwritten(
    skills_dir,
    temp_db,
):
    alpha = GatedSkill("alpha")
    registry = await registry_with(skills_dir, temp_db, {"alpha": alpha})
    brake_calls = {"n": 0}
    gate = asyncio.Event()
    in_recheck = asyncio.Event()

    async def brake() -> bool:
        brake_calls["n"] += 1
        if brake_calls["n"] == 2:  # the in-occupancy re-check of the first request
            in_recheck.set()
            await gate.wait()
        return False

    registry._is_blocked_by_brake = brake  # type: ignore[method-assign]

    first = asyncio.create_task(registry.execute_action("alpha", "quick", {"n": 1}))
    await asyncio.wait_for(in_recheck.wait(), HANG_GUARD_S)
    unloading = asyncio.create_task(registry.unload_skill("alpha"))
    await until(lambda: alpha.state is SkillState.UNLOADING, "unload to mark the skill")
    gate.set()

    r1 = await asyncio.wait_for(first, HANG_GUARD_S)
    assert not r1.success and "not ready" in (r1.error or "").lower(), r1.error
    assert await asyncio.wait_for(unloading, HANG_GUARD_S) is True
    assert alpha.calls == [], "the action never ran on the unloading skill"
    assert ("unloading", "running") not in alpha.transitions, alpha.transitions
    assert alpha.transitions[-1] == ("unloading", "unloaded"), alpha.transitions


async def test_a_sync_execute_is_contained_as_an_error_not_escaped(skills_dir, temp_db):
    alpha = SyncSkill("alpha")
    registry = await registry_with(skills_dir, temp_db, {"alpha": alpha})

    result = await registry.execute_action("alpha", "quick", {"n": 1})
    assert not result.success
    assert "not an awaitable" in (result.error or ""), result.error
    assert alpha.state is SkillState.ERROR
    assert waiters(registry, "alpha") == 0
    assert audit_rows(temp_db, "alpha") == 1


async def test_a_skill_that_raises_while_being_cancelled_is_marked_error(skills_dir, temp_db):
    alpha = GatedSkill("alpha")
    registry = await registry_with(skills_dir, temp_db, {"alpha": alpha})

    # The request is cancelled; the action turns that into its own exception.
    first = asyncio.create_task(registry.execute_action("alpha", "raise_on_cancel", {"n": 1}))
    await alpha.entered.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert alpha.state is SkillState.ERROR
    refused = await registry.execute_action("alpha", "quick", {"n": 2})
    assert not refused.success and "error" in (refused.error or ""), refused.error

    # The same through the execution timeout: the failure is the result.
    replacement = GatedSkill("alpha")
    with patch.object(registry, "_instantiate_skill", return_value=replacement):
        assert await registry.reload_skill("alpha") is True
    registry._execution_timeout = 0.2
    result = await asyncio.wait_for(
        registry.execute_action("alpha", "raise_on_cancel", {"n": 3}),
        HANG_GUARD_S,
    )
    assert not result.success and "cleanup failed" in (result.error or ""), result.error
    assert replacement.state is SkillState.ERROR


async def test_an_action_that_completes_while_being_cancelled_keeps_its_result(
    skills_dir,
    temp_db,
):
    alpha = GatedSkill("alpha")
    registry = await registry_with(skills_dir, temp_db, {"alpha": alpha}, execution_timeout=0.2)

    result = await asyncio.wait_for(
        registry.execute_action("alpha", "swallow_cancel_fast", {"n": 1}),
        HANG_GUARD_S,
    )
    assert result.success, result.error
    assert result.data == {"n": 1, "late": True}
    assert alpha.state is SkillState.READY


async def test_unload_cancels_an_action_that_outlives_the_unload_bound(skills_dir, temp_db):
    alpha = GatedSkill("alpha")
    registry = await registry_with(
        skills_dir,
        temp_db,
        {"alpha": alpha},
        execution_timeout=30.0,
        unload_timeout=0.2,
    )

    first = asyncio.create_task(registry.execute_action("alpha", "hang", {"n": 1}))
    await alpha.entered.wait()
    assert await asyncio.wait_for(registry.unload_skill("alpha"), HANG_GUARD_S) is True
    assert alpha.cancelled == 1, "the action outliving the bound was cancelled"
    assert alpha.shutdown_called
    r1 = await asyncio.wait_for(first, HANG_GUARD_S)
    assert not r1.success and "cancelled" in (r1.error or ""), r1.error
    assert "alpha" not in registry._loaded


async def test_a_cancelled_unload_leaves_the_skill_loaded_and_usable(skills_dir, temp_db):
    alpha = GatedSkill("alpha")
    registry = await registry_with(skills_dir, temp_db, {"alpha": alpha})

    first = asyncio.create_task(registry.execute_action("alpha", "block", {"n": 1}))
    await alpha.entered.wait()
    unloading = asyncio.create_task(registry.unload_skill("alpha"))
    await until(lambda: alpha.state is SkillState.UNLOADING, "unload to mark the skill")
    unloading.cancel()
    with pytest.raises(asyncio.CancelledError):
        await unloading
    assert alpha.state is SkillState.RUNNING, "restored: the action is still in flight"

    alpha.release.set()
    assert (await first).success
    assert alpha.state is SkillState.READY
    again = await registry.execute_action("alpha", "quick", {"n": 2})
    assert again.success, again.error
    assert await registry.unload_skill("alpha") is True


async def test_shutdown_unloads_skills_concurrently_within_one_unload_bound(skills_dir, temp_db):
    alpha, beta = GatedSkill("alpha"), GatedSkill("beta")
    registry = await registry_with(
        skills_dir,
        temp_db,
        {"alpha": alpha, "beta": beta},
        execution_timeout=30.0,
        unload_timeout=0.3,
    )
    tasks = [
        asyncio.create_task(registry.execute_action("alpha", "hang", {"n": 1})),
        asyncio.create_task(registry.execute_action("beta", "hang", {"n": 2})),
    ]
    await alpha.entered.wait()
    await beta.entered.wait()

    started = time.monotonic()
    await asyncio.wait_for(registry.shutdown(), HANG_GUARD_S)
    elapsed = time.monotonic() - started
    assert alpha.cancelled == 1 and beta.cancelled == 1
    assert registry._loaded == {}
    assert elapsed < 1.0, f"two unload bounds paid sequentially: {elapsed:.2f}s"
    for t in tasks:
        r = await asyncio.wait_for(t, HANG_GUARD_S)
        assert not r.success and "cancelled" in (r.error or ""), r.error


async def test_the_forecast_seam_does_not_relay_registry_refusal_wording():
    from bartholomew.kernel.runtime_contract import _forecast_error

    busy = _forecast_error(SkillResult.fail("Skill busy: forecast (waited 30s behind lookup)"))
    timed_out = _forecast_error(
        SkillResult.fail("Skill action timed out: forecast.lookup exceeded 60s and was cancelled"),
    )
    not_ready = _forecast_error(SkillResult.fail("Skill not ready: forecast (state=error)"))
    own_words = _forecast_error(
        SkillResult.fail(
            "The configured forecast provider is not in this skill's declared network allowlist",
        ),
    )
    assert "Skill" not in busy and "busy" in busy
    assert "Skill" not in timed_out and timed_out == busy
    assert not_ready == "the forecast capability is not available"
    assert own_words.startswith("The configured forecast provider")
