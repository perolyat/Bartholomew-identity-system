"""
Tests for MASTER_PLAN.md's "P2.5 -- Runtime Convergence" item 11.3:
Runtime Contract as a code seam.

Traces a chat message through every named stage (Observation ->
Interpretation -> Executive -> Governance -> Capability -> Execution ->
Reflection -> Memory) via
bartholomew.kernel.runtime_contract.run_chat_through_runtime_contract(),
asserting the acceptance criterion directly: a chat input produces a
distinct candidate-action representation before any execution, and a
Working Memory entry afterward.
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

        # Stage 2: Interpretation
        assert isinstance(result.interpretation, Interpretation)
        assert result.interpretation.observation is result.observation
        assert result.interpretation.prompt == "hello there"

        # Stage 3: Executive (candidate action, distinct from execution)
        assert isinstance(result.candidate_action, CandidateAction)
        assert result.candidate_action.kind == "chat_response"
        assert result.candidate_action.interpretation is result.interpretation

        # Stage 4: Governance
        assert result.governance_allowed is True
        assert result.governance_reason is None

        # Stage 5+6: Capability + Execution
        assert result.response == "echo: hello there"

        # Stage 7: Reflection -- a real Working Memory entry was created
        assert result.working_memory_item_id is not None
        item = daemon.working_memory.get(result.working_memory_item_id)
        assert item is not None
        assert "hello there" in item.content
        assert "echo: hello there" in item.content
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
        assert result.candidate_action.interpretation.prompt == "do something"

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
        assert result.response == "echo: hi again"
        assert result.working_memory_item_id is not None
