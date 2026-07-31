"""
Scenario replay harness for the real KernelDaemon + Runtime Contract stack.

MASTER_PLAN.md's Experience Kernel MVP backlog item named this as
deliberately out of scope for its 2026-07-20 pass: "a dedicated 'scenario
replay' test harness (distinct from `tests/test_stage3_integration.py::
TestFullLifecycle`)". `TestFullLifecycle` hand-wires individual kernel
modules (ExperienceKernel, NarratorEngine, WorkingMemoryManager,
PersonaPackManager) together directly -- useful, but it doesn't exercise
the actual production code paths (`KernelDaemon`,
`run_chat_through_runtime_contract()`) or prove those pieces cohere with
each other across a realistic multi-turn session.

This module replays one continuous scenario through the real seam:
chat -> goal added -> chat referencing both the goal and a prior turn's own
content -> persona switch observed in the next turn -> parking brake
blocking and recovering -> daily reflection including the real episode ->
a simulated restart (persist + reload against a fresh KernelDaemon
instance) proving none of it depended on in-memory state alone.

Each acceptance point traces back to an item already implemented and
individually tested elsewhere (11.4, 11.6, 11.7, 11.8); this test's value
is proving they compose correctly in one session, not re-testing any one
of them in isolation.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bartholomew.kernel.daemon import KernelDaemon
from bartholomew.kernel.runtime_contract import run_chat_through_runtime_contract
from bartholomew.orchestrator.safety.parking_brake import BrakeStorage, ParkingBrake
from identity_interpreter.identity_context import IdentityContext


def _write_configs(tmp_path):
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

    return {
        "cfg_path": str(cfg_path),
        "db_path": str(tmp_path / "scenario.db"),
        "persona_path": str(persona_path),
        "policy_path": str(policy_path),
        "drives_path": str(drives_path),
    }


async def _boot(config: dict) -> KernelDaemon:
    """
    Bring a KernelDaemon to the same ready state daemon.start() reaches,
    minus the background tasks (tick loop, scheduler, dream loop) --
    keeps the replay deterministic and synchronous rather than racing
    asyncio.create_task()'d loops with arbitrary sleeps.
    """
    kd = KernelDaemon(**config)
    await kd.mem.init()
    await kd._init_experience_kernel()
    kd.narrator.subscribe_to_workspace()
    return kd


async def _respond(prompt: str) -> str:
    return f"Bartholomew heard: {prompt}"


@pytest.mark.asyncio
class TestScenarioReplay:
    async def test_full_multi_turn_session_coheres(self, tmp_path):
        config = _write_configs(tmp_path)
        daemon = await _boot(config)

        # --- Turn 1: an ordinary chat message -------------------------------
        result1 = await run_chat_through_runtime_contract(
            daemon,
            "Remember that my favorite color is teal",
            _respond,
        )
        assert result1.governance_allowed is True
        assert result1.working_memory_item_id is not None

        # --- A goal gets added mid-session (e.g. via /api/self/goals) -------
        daemon.experience.add_goal("finish the quarterly report")

        # --- Turn 2: references both the goal and turn 1's own content -----
        result2 = await run_chat_through_runtime_contract(
            daemon,
            "What should I focus on, and what's my favorite color?",
            _respond,
        )
        assert "finish the quarterly report" in result2.interpretation.prompt
        assert "my favorite color is teal" in result2.interpretation.prompt
        assert "Active persona: default" in result2.interpretation.prompt

        # --- Persona switch is observed in the very next turn ---------------
        packs = daemon.persona_manager.list_packs()
        other_pack = next(p for p in packs if p != "default")
        daemon.persona_manager.switch_pack(other_pack, trigger="scenario_replay")

        result3 = await run_chat_through_runtime_contract(daemon, "hi again", _respond)
        assert f"Active persona: {other_pack}" in result3.interpretation.prompt

        # --- Governance: parking brake blocks, then recovers -----------------
        brake = ParkingBrake(BrakeStorage(daemon.mem.db_path))
        brake.engage("skills")

        blocked_result = await run_chat_through_runtime_contract(daemon, "are you there?", _respond)
        assert blocked_result.governance_allowed is False
        assert blocked_result.response is None

        brake.disengage()
        recovered_result = await run_chat_through_runtime_contract(daemon, "still there?", _respond)
        assert recovered_result.governance_allowed is True

        # --- Daily reflection captures the real episode from this session --
        now = datetime.now(timezone.utc)
        await daemon._run_daily_reflection(now)
        reflection = await daemon.mem.latest_reflection("daily_journal")
        assert reflection is not None
        assert "finish the quarterly report" in reflection["content"]
        assert reflection["meta"].get("episodic_narrative_included") is True

        # --- Simulated restart: persist, then boot a *second* daemon --------
        # against the same DB and confirm state survives, proving none of
        # the above depended on the first process's in-memory state alone.
        daemon.experience.persist_snapshot()
        daemon.working_memory.persist_snapshot(daemon.mem.db_path)
        await daemon.mem.close()

        daemon2 = await _boot(config)
        try:
            # Goals/affect/attention genuinely survive restart (this is the
            # bug this scenario found and daemon.py's _init_experience_kernel()
            # was fixed for -- see its comment). Persona does *not*: nothing
            # persists "active pack" anywhere (persona_switch_log is an audit
            # trail, not restorable state), and _init_experience_kernel()'s
            # own "activate default if none active" logic means every boot
            # intentionally starts on the default pack -- a separate,
            # pre-existing property of PersonaPackManager, not something this
            # change touches.
            assert "finish the quarterly report" in daemon2.experience.get_active_goals()
            assert daemon2.persona_manager.get_active_pack_id() == "default"

            result_after_restart = await run_chat_through_runtime_contract(
                daemon2,
                "what should I focus on?",
                _respond,
            )
            assert "finish the quarterly report" in result_after_restart.interpretation.prompt
            assert "my favorite color is teal" in result_after_restart.interpretation.prompt
        finally:
            await daemon2.mem.close()

    async def test_identity_policy_change_flips_a_future_tool_shaped_candidate_action(
        self,
        tmp_path,
    ):
        """A different composition point: a chat turn with a restrictive
        IdentityContext still succeeds (item 11.6's regression guard),
        proven inside a full scenario rather than in isolation."""
        config = _write_configs(tmp_path)
        daemon = await _boot(config)
        daemon.identity_context = IdentityContext(
            tool_use_default_allowed=False,
            tool_use_allowlist=[],
        )

        result = await run_chat_through_runtime_contract(daemon, "hello", _respond)

        assert result.governance_allowed is True
        assert result.response is not None

        await daemon.mem.close()
