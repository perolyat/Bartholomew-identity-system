"""
Tests for NotifySkill's quiet-hours/mute settings persistence and gating
(Stage 1, S1.3). This skill previously had no test file at all; these
tests target the settings additions specifically, not the pre-existing
send/queue/cancel behavior.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
async def test_mute_rejects_unparseable_until(skill):
    """Codex review finding: an invalid `until` string used to be persisted
    verbatim, and _is_muted()'s lexicographic string comparison against it
    could never resolve to "expired" -- an effectively indefinite mute.
    It must now be rejected outright instead."""
    result = await skill.execute("mute", {"until": "not-a-timestamp"})
    assert result.status.value == "error"

    settings = await skill.execute("get_notification_settings")
    assert settings.data["muted"] is False


@pytest.mark.asyncio
async def test_mute_with_non_utc_offset_normalizes_and_compares_correctly(skill):
    """Codex review finding: a future `until` expressed with a non-'Z' UTC
    offset (e.g. "-05:00") could lexicographically compare as *earlier*
    than a same-instant-later 'Z'-suffixed `now`, incorrectly self-clearing
    a still-active mute. Build a real future instant, express it 5 hours
    behind UTC, and confirm it's still recognized as muted."""
    future_utc = datetime.now(timezone.utc) + timedelta(hours=1)
    future_minus_five = future_utc.astimezone(timezone(timedelta(hours=-5)))
    until = future_minus_five.isoformat()
    assert "-05:00" in until  # guard the guard: still a non-Z offset

    result = await skill.execute("mute", {"until": until})
    assert result.status.value == "success"

    settings = await skill.execute("get_notification_settings")
    assert settings.data["muted"] is True
    assert settings.data["effective_muted"] is True
    # Normalized to UTC/'Z' at write time, not stored as the raw offset string.
    assert settings.data["muted_until"].endswith("Z")
    assert "-05:00" not in settings.data["muted_until"]


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
