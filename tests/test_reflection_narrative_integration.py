"""
Tests for reconciling ROADMAP.md Stage 3's "Still open: reconciling the two
non-unified reflection pipelines" note.

daemon.py's daily/weekly reflection loop (_run_daily_reflection,
_run_weekly_reflection) generates content exclusively via
identity_interpreter.adapters.reflection_generator.ReflectionGenerator (or a
generic template fallback) -- its own "Notable Events" section literally
says "(Future: chat highlights, emotional events, user activities)" as a
placeholder. Meanwhile narrator.py's NarratorEngine already builds a real
narrative from actual persisted episodes (affect/attention/drive/goal/
observation) via generate_daily_reflection_narrative()/
generate_weekly_reflection_narrative(), but nothing in the live daemon ever
called them -- confirmed by grep: their only callers anywhere in the repo
were tests.

These tests prove the fix: the persisted/exported daily and weekly
reflections now include the real episodic narrative, appended alongside
(not replacing) ReflectionGenerator's own output.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bartholomew.kernel.daemon import KernelDaemon


@pytest.fixture
def mock_config_files(tmp_path):
    cfg_path = tmp_path / "kernel.yaml"
    cfg_path.write_text(
        """
timezone: "Australia/Brisbane"
loop_interval_seconds: 1
quiet_hours:
  start: "23:00"
  end: "06:00"
dreaming:
  nightly_window: "21:00-23:00"
  weekly:
    weekday: "Sun"
    time: "21:30"
""",
    )

    persona_path = tmp_path / "persona.yaml"
    persona_path.write_text('name: "Test Bartholomew"\n')

    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text("policies: []\n")

    drives_path = tmp_path / "drives.yaml"
    drives_path.write_text("drives: []\n")

    db_path = tmp_path / "test.db"

    return {
        "cfg_path": str(cfg_path),
        "db_path": str(db_path),
        "persona_path": str(persona_path),
        "policy_path": str(policy_path),
        "drives_path": str(drives_path),
    }


@pytest.fixture
async def daemon(mock_config_files):
    kd = KernelDaemon(**mock_config_files)
    await kd.mem.init()
    return kd


@pytest.mark.asyncio
class TestDailyReflectionIncludesEpisodicNarrative:
    async def test_daily_reflection_includes_a_real_goal_episode(self, daemon):
        # Narrator must be subscribed for goal-add events to become episodes
        # (mirrors what KernelDaemon.start() does at startup).
        daemon.narrator.subscribe_to_workspace()
        daemon.experience.add_goal("finish the quarterly report")

        now = datetime.now(timezone.utc)
        await daemon._run_daily_reflection(now)

        reflection = await daemon.mem.latest_reflection("daily_journal")
        assert reflection is not None
        assert "finish the quarterly report" in reflection["content"]
        assert reflection["meta"].get("episodic_narrative_included") is True

    async def test_daily_reflection_still_works_with_no_episodes(self, daemon):
        """A quiet day (no episodes) doesn't break reflection generation --
        the narrator's own "quiet day" text is a valid, non-empty narrative,
        so it's still appended."""
        now = datetime.now(timezone.utc)
        await daemon._run_daily_reflection(now)

        reflection = await daemon.mem.latest_reflection("daily_journal")
        assert reflection is not None
        assert "quiet day" in reflection["content"].lower()


@pytest.mark.asyncio
class TestWeeklyReflectionIncludesEpisodicNarrative:
    async def test_weekly_reflection_includes_a_real_goal_episode(self, daemon):
        daemon.narrator.subscribe_to_workspace()
        daemon.experience.add_goal("plan the offsite")

        now = datetime.now(timezone.utc)
        await daemon._run_weekly_reflection(now)

        reflection = await daemon.mem.latest_reflection("weekly_alignment_audit")
        assert reflection is not None
        assert "plan the offsite" in reflection["content"]
        assert reflection["meta"].get("episodic_narrative_included") is True

    async def test_weekly_reflection_preserves_the_safety_audit_section(self, daemon):
        """The append-only integration must not clobber ReflectionGenerator's
        own safety-audit content (Identity Core Alignment checklist etc.)."""
        now = datetime.now(timezone.utc)
        await daemon._run_weekly_reflection(now)

        reflection = await daemon.mem.latest_reflection("weekly_alignment_audit")
        assert reflection is not None
        assert "Identity Core Alignment" in reflection["content"]
