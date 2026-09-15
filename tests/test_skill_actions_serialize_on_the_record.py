"""
Concurrent skill actions on one record keep every change.

The writer-lock repair (PR #110) made each skill action await its permission
check and its save off the event loop. That gave an action body suspension
points it never had: two actions on the same record could each read the row
before either wrote it back, and the later write silently undid the earlier
one while both reported success (the independent review of the repair
measured 0/40 rounds losing a change before it and 39/40 after). The
read-modify-write of every mutating action now happens inside one off-loop
immediate transaction, so the row is the serialization point again. These
tests hold that, under both executors `run_off_loop()` can use.
"""

from __future__ import annotations

import asyncio

import pytest

from bartholomew.kernel.blocking_executor import SingleWorkerExecutor
from bartholomew.kernel.skill_base import SkillContext
from bartholomew.skills.calendar_draft import CalendarDraftSkill
from bartholomew.skills.notify import NotificationStatus, NotifySkill
from bartholomew.skills.tasks import TasksSkill

ROUNDS = 20


async def _skill(cls: type, db_path: str, executor: SingleWorkerExecutor | None):
    skill = cls()
    await skill.initialize(
        SkillContext(
            db_path=db_path,
            check_permission=lambda _permission: True,
            blocking_executor=executor,
        ),
    )
    return skill


@pytest.fixture(params=["to_thread", "single_worker"])
async def executor(request):
    """Both ways `run_off_loop()` leaves the loop: a fresh thread per call, or the daemon's single worker."""
    if request.param == "to_thread":
        yield None
        return
    single = SingleWorkerExecutor(label="test-skill-actions")
    try:
        yield single
    finally:
        await single.close()


async def test_update_and_complete_on_one_task_keep_both_changes(tmp_path, executor):
    skill = await _skill(TasksSkill, str(tmp_path / "skills.db"), executor)
    for i in range(ROUNDS):
        created = await skill.execute("create", {"title": f"orig {i}"})
        assert created.success, created.error
        task_id = created.data["id"]

        renamed, completed = await asyncio.gather(
            skill.execute("update", {"task_id": task_id, "title": "renamed"}),
            skill.execute("complete", {"task_id": task_id}),
        )
        assert renamed.success and completed.success, (renamed.error, completed.error)

        final = (await skill.execute("get", {"task_id": task_id})).data
        assert (final["title"], final["status"]) == (
            "renamed",
            "completed",
        ), f"round {i}: a concurrent action's change was lost: {final}"


async def test_delete_racing_an_update_never_leaves_a_resurrected_task(tmp_path, executor):
    skill = await _skill(TasksSkill, str(tmp_path / "skills.db"), executor)
    for i in range(ROUNDS):
        created = await skill.execute("create", {"title": f"orig {i}"})
        task_id = created.data["id"]

        updated, deleted = await asyncio.gather(
            skill.execute("update", {"task_id": task_id, "title": "renamed"}),
            skill.execute("delete", {"task_id": task_id}),
        )
        assert deleted.success, deleted.error
        # Whichever order the row was reached in, a deleted task stays deleted:
        # the update either landed before the delete or found nothing to update.
        gone = await skill.execute("get", {"task_id": task_id})
        assert not gone.success, f"round {i}: the task came back after its deletion: {gone.data}"
        if not updated.success:
            assert "not found" in (updated.error or "").lower()


async def test_two_field_updates_on_one_event_keep_both_fields(tmp_path, executor):
    skill = await _skill(CalendarDraftSkill, str(tmp_path / "skills.db"), executor)
    for i in range(ROUNDS):
        created = await skill.execute(
            "create",
            {"title": f"draft {i}", "start": "2030-01-01T10:00:00", "end": "2030-01-01T11:00:00"},
        )
        assert created.success, created.error
        event_id = created.data["id"]

        titled, located = await asyncio.gather(
            skill.execute("update", {"event_id": event_id, "title": "renamed"}),
            skill.execute("update", {"event_id": event_id, "location": "room 4"}),
        )
        assert titled.success and located.success, (titled.error, located.error)

        final = (await skill.execute("get", {"event_id": event_id})).data
        assert (final["title"], final["location"]) == (
            "renamed",
            "room 4",
        ), f"round {i}: a concurrent update's field was lost: {final}"


async def test_a_cancelled_notification_is_never_also_delivered(tmp_path, executor):
    skill = await _skill(NotifySkill, str(tmp_path / "skills.db"), executor)
    for i in range(ROUNDS):
        queued = await skill.execute(
            "queue",
            {"message": f"due {i}", "deliver_at": "2000-01-01T00:00:00Z"},
        )
        assert queued.success, queued.error
        notification_id = queued.data["id"]

        cancelled, delivered = await asyncio.gather(
            skill.execute("cancel", {"notification_id": notification_id}),
            skill._process_queue(),
        )
        final = skill._get_notification(notification_id)
        assert final is not None
        if cancelled.success:
            assert final.status == NotificationStatus.CANCELLED, f"round {i}: {final}"
            assert delivered == 0, f"round {i}: a cancelled notification was delivered"
        else:
            assert final.status == NotificationStatus.SENT, f"round {i}: {final}"
            assert delivered == 1
