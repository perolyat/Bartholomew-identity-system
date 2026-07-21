"""
Tests for MASTER_PLAN.md's "P2.5 -- Runtime Convergence" items 11.3 + 11.4:
Runtime Contract as a code seam, and wiring chat into the Experience Kernel.

Traces a chat message through every named stage (Observation ->
Interpretation -> Executive -> Governance -> Capability -> Execution ->
Reflection -> Memory) via
bartholomew.kernel.runtime_contract.run_chat_through_runtime_contract(),
asserting the acceptance criteria directly:
- a chat input produces a distinct candidate-action representation before
  any execution, and a Working Memory entry afterward (11.3);
- a chat turn observably updates working memory and can reference
  persisted persona/goal state from a previous turn (11.4).
"""

from __future__ import annotations

import pytest

from bartholomew.kernel.runtime_contract import (
    CandidateAction,
    Interpretation,
    Observation,
    run_chat_through_runtime_contract,
)


@pytest.fixture
def mock_config_files(tmp_path):
    """Create mock config files for daemon (mirrors test_stage3_integration.py)."""
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
    persona_path.write_text(
        """
name: "Test Bartholomew"
""",
    )

    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        """
policies: []
""",
    )

    drives_path = tmp_path / "drives.yaml"
    drives_path.write_text(
        """
drives: []
""",
    )

    db_path = tmp_path / "test.db"

    return {
        "cfg_path": str(cfg_path),
        "db_path": str(db_path),
        "persona_path": str(persona_path),
        "policy_path": str(policy_path),
        "drives_path": str(drives_path),
    }


@pytest.fixture
def daemon(mock_config_files):
    from bartholomew.kernel.daemon import KernelDaemon

    return KernelDaemon(**mock_config_files)


async def _stub_respond(prompt: str) -> str:
    return f"echo: {prompt}"


@pytest.mark.asyncio
class TestRuntimeContractStages:
    async def test_every_stage_is_present_and_correctly_shaped(self, daemon):
        result = await run_chat_through_runtime_contract(daemon, "hello there", _stub_respond)

        # Stage 1: Observation
        assert isinstance(result.observation, Observation)
        assert result.observation.source == "chat"
        assert result.observation.raw_content == "hello there"

        # Stage 2: Interpretation -- prompt contains (but isn't limited to)
        # the raw input; a real KernelDaemon always has an active default
        # persona pack from construction, so the enriched prompt includes it.
        assert isinstance(result.interpretation, Interpretation)
        assert result.interpretation.observation is result.observation
        assert "hello there" in result.interpretation.prompt

        # Stage 3: Executive (candidate action, distinct from execution)
        assert isinstance(result.candidate_action, CandidateAction)
        assert result.candidate_action.kind == "chat_response"
        assert result.candidate_action.interpretation is result.interpretation

        # Stage 4: Governance
        assert result.governance_allowed is True
        assert result.governance_reason is None

        # Stage 5+6: Capability + Execution -- respond_fn receives the
        # *interpreted* prompt, not just the raw input.
        assert result.response == f"echo: {result.interpretation.prompt}"

        # Stage 7: Reflection -- a real Working Memory entry was created
        assert result.working_memory_item_id is not None
        item = daemon.working_memory.get(result.working_memory_item_id)
        assert item is not None
        assert "hello there" in item.content
        assert item.source == "chat"

    async def test_candidate_action_exists_before_any_execution(self, daemon):
        """The acceptance criterion's core claim: a distinct candidate-action
        representation exists before execution -- i.e. even if respond_fn is
        never called (governance denies), the candidate action was already
        constructed."""
        from bartholomew.orchestrator.safety.parking_brake import BrakeStorage, ParkingBrake

        brake = ParkingBrake(BrakeStorage(daemon.mem.db_path))
        brake.engage("skills")

        calls: list[str] = []

        async def tracking_respond(prompt: str) -> str:
            calls.append(prompt)
            return "should not be reached"

        result = await run_chat_through_runtime_contract(daemon, "do something", tracking_respond)

        # The candidate action was constructed regardless of governance outcome.
        assert result.candidate_action.kind == "chat_response"
        assert "do something" in result.candidate_action.interpretation.prompt

        # But execution never happened, and nothing was reflected into memory.
        assert result.governance_allowed is False
        assert "parking brake" in result.governance_reason.lower()
        assert calls == []
        assert result.response is None
        assert result.working_memory_item_id is None

        brake.disengage()

    async def test_allowed_again_after_disengage(self, daemon):
        from bartholomew.orchestrator.safety.parking_brake import BrakeStorage, ParkingBrake

        brake = ParkingBrake(BrakeStorage(daemon.mem.db_path))
        brake.engage("skills")
        brake.disengage()

        result = await run_chat_through_runtime_contract(daemon, "hi again", _stub_respond)

        assert result.governance_allowed is True
        assert result.response is not None
        assert result.working_memory_item_id is not None


@pytest.mark.asyncio
class TestChatReferencesPersistedExperienceKernelState:
    """Item 11.4's acceptance criterion: a chat turn observably updates
    working memory, and can reference persisted persona/goal state from a
    previous turn -- previously chat and the Experience Kernel were fully
    disconnected."""

    async def test_prompt_includes_active_persona_by_default(self, daemon):
        """A real daemon always has a default persona pack active from
        construction (PersonaPackManager auto-activates is_default: true),
        so even a first chat turn already references persisted state."""
        result = await run_chat_through_runtime_contract(daemon, "hi", _stub_respond)

        assert "Active persona: default" in result.interpretation.prompt

    async def test_prompt_references_a_goal_added_in_a_previous_turn(self, daemon):
        """Simulates a goal persisted by prior kernel activity (e.g. a
        scheduler drive or a direct /api/self/goals call) being genuinely
        referenced by a later chat turn -- not just theoretically
        available."""
        daemon.experience.add_goal("finish the quarterly report")

        result = await run_chat_through_runtime_contract(
            daemon,
            "what should I do today?",
            _stub_respond,
        )

        assert "finish the quarterly report" in result.interpretation.prompt
        assert "finish the quarterly report" in result.response

    async def test_working_memory_accumulates_across_turns(self, daemon):
        """Working Memory is not reset between turns -- multiple chat turns
        observably build up shared context, the same kernel state every
        other surface (scheduler, /api/self/*) already shares."""
        result1 = await run_chat_through_runtime_contract(daemon, "first turn", _stub_respond)
        result2 = await run_chat_through_runtime_contract(daemon, "second turn", _stub_respond)

        assert result1.working_memory_item_id != result2.working_memory_item_id
        item1 = daemon.working_memory.get(result1.working_memory_item_id)
        item2 = daemon.working_memory.get(result2.working_memory_item_id)
        assert item1 is not None and "first turn" in item1.content
        assert item2 is not None and "second turn" in item2.content

    async def test_persona_switch_is_reflected_in_the_next_chat_turn(self, daemon):
        """Switching persona (e.g. via /api/persona/switch) is observable
        in the very next chat turn, proving chat consults live Experience
        Kernel/PersonaPackManager state rather than a frozen snapshot."""
        packs = daemon.persona_manager.list_packs()
        other_pack = next(p for p in packs if p != "default")

        daemon.persona_manager.switch_pack(other_pack, trigger="test")

        result = await run_chat_through_runtime_contract(daemon, "hi again", _stub_respond)

        assert f"Active persona: {other_pack}" in result.interpretation.prompt
