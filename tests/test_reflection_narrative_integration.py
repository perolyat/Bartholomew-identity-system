"""
Tests for the reflection-ownership contract (ROADMAP.md S5.4).

History: daemon.py's reflection loop used to call ReflectionGenerator and
NarratorEngine independently and string-concatenate their two outputs
(`content = f"{content}\\n\\n---\\n\\n{episodic_narrative}"`). That closed
ROADMAP.md Stage 3's "two non-unified reflection pipelines" note by
*addition*, which COGNITIVE_RUNTIME.md's "Reflection ownership" section
named precisely: "concatenation, not architectural unification".

The approved target architecture (recorded 2026-07-28) is that
ReflectionGenerator is the authoritative owner of reflection composition and
final output, and NarratorEngine's episodic narrative is supplementary
evidence supplied *to* that process. These tests pin that contract:

- the narrator's material is collected first and passed in as evidence;
- it reaches the persisted reflection;
- the output is one document, not two joined by a rule.

Note on what is deliberately *not* asserted: when a real model composes the
reflection it interprets the evidence rather than reproducing it verbatim,
so these tests assert that the evidence was *supplied* (meta flag) plus
literal presence only on the template-fallback path, which does fold it in
verbatim. Asserting literal presence of episode text on the model path would
re-pin the concatenation behaviour this change removes.
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
        assert reflection["meta"].get("episodic_evidence_present") is True

    async def test_daily_reflection_still_works_with_no_episodes(self, daemon):
        """A quiet day (no episodes) doesn't break reflection generation --
        the narrator's own "quiet day" text is a valid, non-empty narrative,
        so it is still supplied as evidence."""
        now = datetime.now(timezone.utc)
        await daemon._run_daily_reflection(now)

        reflection = await daemon.mem.latest_reflection("daily_journal")
        assert reflection is not None
        assert "quiet day" in reflection["content"].lower()

    async def test_daily_reflection_is_one_document_not_two(self, daemon):
        """The defining regression: the old code appended the narrator's whole
        reflection document after the generator's, producing two top-level
        titles separated by a rule. One authority means one title."""
        daemon.narrator.subscribe_to_workspace()
        daemon.experience.add_goal("finish the quarterly report")

        now = datetime.now(timezone.utc)
        await daemon._run_daily_reflection(now)

        reflection = await daemon.mem.latest_reflection("daily_journal")
        assert reflection is not None
        content = reflection["content"]

        h1_titles = [ln for ln in content.splitlines() if ln.startswith("# ")]
        assert len(h1_titles) == 1, f"expected a single top-level title, got {h1_titles}"


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
        assert reflection["meta"].get("episodic_evidence_present") is True

    async def test_weekly_reflection_preserves_the_safety_audit_section(self, daemon):
        """Supplying evidence must not displace ReflectionGenerator's own
        safety-audit content (Identity Core Alignment checklist etc.)."""
        now = datetime.now(timezone.utc)
        await daemon._run_weekly_reflection(now)

        reflection = await daemon.mem.latest_reflection("weekly_alignment_audit")
        assert reflection is not None
        assert "Identity Core Alignment" in reflection["content"]

    async def test_weekly_reflection_is_one_document_not_two(self, daemon):
        daemon.narrator.subscribe_to_workspace()
        daemon.experience.add_goal("plan the offsite")

        now = datetime.now(timezone.utc)
        await daemon._run_weekly_reflection(now)

        reflection = await daemon.mem.latest_reflection("weekly_alignment_audit")
        assert reflection is not None

        h1_titles = [ln for ln in reflection["content"].splitlines() if ln.startswith("# ")]
        assert len(h1_titles) == 1, f"expected a single top-level title, got {h1_titles}"


@pytest.mark.asyncio
class TestNarratorFailureDoesNotBreakReflection:
    async def test_narrator_error_still_produces_a_reflection(self, daemon, monkeypatch):
        """Evidence collection is best-effort. A narrator failure must degrade
        to a reflection without evidence, never to no reflection at all."""

        def _boom(*args, **kwargs):
            raise RuntimeError("narrator exploded")

        monkeypatch.setattr(
            daemon.narrator,
            "generate_daily_reflection_narrative",
            _boom,
        )

        now = datetime.now(timezone.utc)
        await daemon._run_daily_reflection(now)

        reflection = await daemon.mem.latest_reflection("daily_journal")
        assert reflection is not None
        assert reflection["content"].strip()
        assert reflection["meta"].get("episodic_evidence_present") is False
