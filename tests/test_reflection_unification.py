"""
Tests for the unified per-action Reflection (Runtime Contract stage 7).

Chat turns and skill executions now produce the *same* Reflection shape through
the *same* Memory sink -- `MemoryStore.reflections`, kind="action_reflection" --
rather than a Working Memory item (chat) vs. a `skill_action_audit` row (skills).
Closes COGNITIVE_RUNTIME.md's Exit Gate question #4 ("two Reflection shapes").

The surface-specific stores are intentionally left in place (Working Memory is
still chat's short-term context buffer; `skill_action_audit` is still the
detailed compliance audit) -- these tests assert the *new* unified record
exists for both surfaces, not that the old ones went away.
"""

from __future__ import annotations

import pytest

from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.reflection import (
    REFLECTION_KIND,
    ActionReflection,
    record_action_reflection,
)
from bartholomew.kernel.runtime_contract import run_chat_through_runtime_contract
from bartholomew.kernel.skill_registry import SkillRegistry


# =============================================================================
# Unit: canonical shape + PII redaction
# =============================================================================


def test_to_memory_row_shape():
    row = ActionReflection(
        surface="skill",
        action="notify.send",
        outcome="success",
        summary="Skill 'notify.send': success",
        details={"message": "ok", "error": None, "count": 3},
    ).to_memory_row()

    assert row["kind"] == REFLECTION_KIND
    assert row["meta"]["surface"] == "skill"
    assert row["meta"]["action"] == "notify.send"
    assert row["meta"]["outcome"] == "success"
    # Non-string detail values pass through untouched.
    assert row["meta"]["count"] == 3
    assert row["meta"]["error"] is None
    assert "T" in row["ts"]  # isoformat timestamp


def test_summary_and_string_details_are_redacted():
    row = ActionReflection(
        surface="chat",
        action="chat_response",
        outcome="responded",
        summary="emailed bob@example.com",
        details={"response_preview": "reach me at bob@example.com"},
    ).to_memory_row()

    # Durable reflections must be PII-safe by construction.
    assert "bob@example.com" not in row["content"]
    assert "bob@example.com" not in row["meta"]["response_preview"]


# =============================================================================
# Sink: best-effort, no store wired
# =============================================================================


@pytest.mark.asyncio
async def test_record_is_noop_without_store():
    reflection = ActionReflection("chat", "chat_response", "responded", "hi")
    assert await record_action_reflection(None, reflection) is None


# =============================================================================
# Integration: both surfaces write the SAME shape to the SAME sink
# =============================================================================


@pytest.mark.asyncio
async def test_skill_execution_records_action_reflection(tmp_path):
    db_path = str(tmp_path / "t.db")
    mem = MemoryStore(db_path)
    await mem.init()
    try:
        registry = SkillRegistry(
            skills_dir=tmp_path / "skills",
            db_path=db_path,
            memory_store=mem,
        )
        # Even a "not loaded" attempt funnels through _finish -> the reflection.
        result = await registry.execute_action("nonexistent_skill", "do", {"value": "x"})
        assert result.success is False

        row = await mem.latest_reflection(REFLECTION_KIND)
        assert row is not None
        assert row["meta"]["surface"] == "skill"
        assert row["meta"]["action"] == "nonexistent_skill.do"
    finally:
        await mem.close()


@pytest.fixture
def daemon(tmp_path):
    """A minimal real KernelDaemon (mirrors test_runtime_contract_chat_seam.py)."""
    (tmp_path / "kernel.yaml").write_text(
        'timezone: "Australia/Brisbane"\nloop_interval_seconds: 1\n',
    )
    (tmp_path / "persona.yaml").write_text('name: "Test Bartholomew"\n')
    (tmp_path / "policy.yaml").write_text("policies: []\n")
    (tmp_path / "drives.yaml").write_text("drives: []\n")

    from bartholomew.kernel.daemon import KernelDaemon

    return KernelDaemon(
        cfg_path=str(tmp_path / "kernel.yaml"),
        db_path=str(tmp_path / "test.db"),
        persona_path=str(tmp_path / "persona.yaml"),
        policy_path=str(tmp_path / "policy.yaml"),
        drives_path=str(tmp_path / "drives.yaml"),
    )


async def _stub_respond(prompt: str) -> str:
    return f"echo: {prompt}"


@pytest.mark.asyncio
async def test_chat_turn_records_action_reflection(daemon):
    await daemon.mem.init()  # ensure the shared reflections table exists
    try:
        await run_chat_through_runtime_contract(daemon, "hello there", _stub_respond)

        row = await daemon.mem.latest_reflection(REFLECTION_KIND)
        assert row is not None
        assert row["meta"]["surface"] == "chat"
        assert row["meta"]["outcome"] == "responded"
    finally:
        await daemon.mem.close()


@pytest.mark.asyncio
async def test_chat_and_skill_share_one_kind_and_sink(tmp_path, daemon):
    """The heart of the unification: a skill execution and a chat turn both land
    in the same reflections table under the same kind, distinguishable only by
    the `surface` field -- one Reflection shape, one Memory sink."""
    await daemon.mem.init()
    try:
        registry = SkillRegistry(
            skills_dir=tmp_path / "skills",
            db_path=daemon.mem.db_path,
            memory_store=daemon.mem,
        )
        await registry.execute_action("nonexistent_skill", "do", {})
        await run_chat_through_runtime_contract(daemon, "hi", _stub_respond)

        # Both rows exist in the same table under REFLECTION_KIND; the most
        # recent (chat, written last) is retrievable and the earlier skill row
        # is too -- proven by asserting both surfaces are represented.
        latest = await daemon.mem.latest_reflection(REFLECTION_KIND)
        assert latest["meta"]["surface"] == "chat"

        import aiosqlite

        async with aiosqlite.connect(daemon.mem.db_path) as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM reflections WHERE kind=?",
                (REFLECTION_KIND,),
            )
            (count,) = await cur.fetchone()
        assert count == 2  # one skill + one chat, same kind, same sink
    finally:
        await daemon.mem.close()
