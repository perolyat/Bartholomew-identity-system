"""
Tests for NotifySkill's quiet-hours/mute settings persistence and gating
(Stage 1, S1.3). This skill previously had no test file at all; these
tests target the settings additions specifically, not the pre-existing
send/queue/cancel behavior.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from bartholomew.kernel.skill_base import SkillContext
from bartholomew.skills.notify import NotifySkill


@pytest.fixture
async def skill(tmp_path):
    s = NotifySkill()
    context = SkillContext(
        db_path=str(tmp_path / "notify.db"),
        check_permission=lambda _perm: True,
    )
    await s.initialize(context)
    return s


@pytest.mark.asyncio
async def test_quiet_hours_default_on_fresh_db(skill):
    result = await skill.execute("get_quiet_hours")
    assert result.data["start"] == NotifySkill.DEFAULT_QUIET_HOURS_START
    assert result.data["end"] == NotifySkill.DEFAULT_QUIET_HOURS_END


@pytest.mark.asyncio
async def test_set_quiet_hours_persists_across_reinitialize(tmp_path):
    db_path = str(tmp_path / "notify.db")
    context = SkillContext(db_path=db_path, check_permission=lambda _perm: True)

    first = NotifySkill()
    await first.initialize(context)
    result = await first.execute("set_quiet_hours", {"start": "23:00", "end": "06:00"})
    assert result.status.value == "success"
    assert result.data["start"] == "23:00"
    assert result.data["end"] == "06:00"
    assert isinstance(result.data["is_active"], bool)

    second = NotifySkill()
    await second.initialize(context)
    reloaded = await second.execute("get_quiet_hours")
    assert reloaded.data["start"] == "23:00"
    assert reloaded.data["end"] == "06:00"


@pytest.mark.asyncio
async def test_set_quiet_hours_rejects_malformed_time(skill):
    result = await skill.execute("set_quiet_hours", {"start": "not-a-time", "end": "06:00"})
    assert result.status.value == "error"


@pytest.mark.asyncio
async def test_mute_and_unmute_persist(tmp_path):
    db_path = str(tmp_path / "notify.db")
    context = SkillContext(db_path=db_path, check_permission=lambda _perm: True)

    first = NotifySkill()
    await first.initialize(context)
    await first.execute("mute")

    second = NotifySkill()
    await second.initialize(context)
    settings = await second.execute("get_notification_settings")
    assert settings.data["muted"] is True
    assert settings.data["effective_muted"] is True

    await second.execute("unmute")

    third = NotifySkill()
    await third.initialize(context)
    settings = await third.execute("get_notification_settings")
    assert settings.data["muted"] is False
    assert settings.data["effective_muted"] is False


@pytest.mark.asyncio
async def test_mute_until_expires_and_self_clears(skill):
    past = (datetime.utcnow() - timedelta(hours=1)).isoformat() + "Z"
    await skill.execute("mute", {"until": past})

    settings = await skill.execute("get_notification_settings")
    assert settings.data["muted"] is False
    assert settings.data["effective_muted"] is False
    assert settings.data["muted_until"] is None


@pytest.mark.asyncio
async def test_mute_until_future_stays_muted(skill):
    future = (datetime.utcnow() + timedelta(hours=1)).isoformat() + "Z"
    await skill.execute("mute", {"until": future})

    settings = await skill.execute("get_notification_settings")
    assert settings.data["effective_muted"] is True
    assert settings.data["muted_until"] == future


@pytest.mark.asyncio
async def test_send_queues_non_urgent_while_muted(skill):
    await skill.execute("mute")

    result = await skill.execute("send", {"message": "hello"})

    assert result.status.value == "success"
    assert result.data["status"] == "pending"


@pytest.mark.asyncio
async def test_send_urgent_bypasses_mute(skill):
    await skill.execute("mute")

    result = await skill.execute("send", {"message": "fire", "priority": "urgent"})

    assert result.status.value == "success"
    assert result.data["status"] == "sent"
