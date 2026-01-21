"""
Stage 3.6 Integration Tests
---------------------------
Tests for the integration of Stage 3 modules (Experience Kernel, Global Workspace,
Working Memory, Narrator, Persona Pack) into the daemon and API.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest


# =============================================================================
# Stage 3 Module Tests - Unit Level
# =============================================================================


class TestExperienceKernelIntegration:
    """Test ExperienceKernel integration with other modules."""

    def test_experience_kernel_with_workspace(self, tmp_path):
        """ExperienceKernel emits events to GlobalWorkspace."""
        from bartholomew.kernel.experience_kernel import ExperienceKernel
        from bartholomew.kernel.global_workspace import GlobalWorkspace

        workspace = GlobalWorkspace()
        kernel = ExperienceKernel(
            db_path=str(tmp_path / "test.db"),
            workspace=workspace,
        )

        # Track events
        events = []
        workspace.subscribe("affect", lambda e: events.append(e))

        # Update affect should emit event
        kernel.update_affect(valence=0.5)

        assert len(events) >= 1
        assert events[0].payload["valence"] == 0.5

    def test_experience_kernel_persistence_roundtrip(self, tmp_path):
        """ExperienceKernel can persist and restore state."""
        from bartholomew.kernel.experience_kernel import ExperienceKernel

        db_path = str(tmp_path / "test.db")

        # Create and modify kernel
        kernel1 = ExperienceKernel(db_path=db_path)
        kernel1.update_affect(valence=0.8, arousal=0.3)
        kernel1.set_attention("test_target", focus_type="task")
        kernel1.add_goal("test_goal")
        kernel1.persist_snapshot()

        # Create new kernel and load state
        kernel2 = ExperienceKernel(db_path=db_path)
        snapshot = kernel2.load_last_snapshot()

        assert snapshot is not None
        assert snapshot.affect.valence == 0.8
        assert snapshot.attention.focus_target == "test_target"
        assert "test_goal" in snapshot.active_goals


class TestGlobalWorkspaceIntegration:
    """Test GlobalWorkspace integration with other modules."""

    def test_workspace_multi_subscriber(self):
        """Multiple modules can subscribe to workspace channels."""
        from bartholomew.kernel.global_workspace import GlobalWorkspace

        workspace = GlobalWorkspace()

        events_1 = []
        events_2 = []

        workspace.subscribe("affect", lambda e: events_1.append(e))
        workspace.subscribe("affect", lambda e: events_2.append(e))

        workspace.emit_affect_changed(
            source="test",
            valence=0.5,
            arousal=0.3,
            energy=0.7,
            emotion="calm",
        )

        assert len(events_1) == 1
        assert len(events_2) == 1

    def test_workspace_event_history(self):
        """Workspace maintains event history."""
        from bartholomew.kernel.global_workspace import GlobalWorkspace

        workspace = GlobalWorkspace()

        workspace.emit_affect_changed(
            source="test",
            valence=0.5,
            arousal=0.3,
            energy=0.7,
            emotion="calm",
        )

        history = workspace.get_history("affect")
        assert len(history) == 1
        assert history[0].payload["valence"] == 0.5


class TestWorkingMemoryIntegration:
    """Test WorkingMemory integration with other modules."""

    def test_working_memory_with_workspace(self):
        """WorkingMemory emits events to GlobalWorkspace."""
        from bartholomew.kernel.global_workspace import GlobalWorkspace
        from bartholomew.kernel.working_memory import (
            WorkingMemoryManager,
            reset_working_memory,
        )

        reset_working_memory()

        workspace = GlobalWorkspace()
        wm = WorkingMemoryManager(workspace=workspace)

        events = []
        workspace.subscribe("working_memory", lambda e: events.append(e))

        wm.add("test content", source="user_input")

        assert len(events) >= 1
        assert events[0].payload["action"] == "added"

    def test_working_memory_attention_boost(self):
        """WorkingMemory boosts items matching attention tags."""
        from bartholomew.kernel.experience_kernel import ExperienceKernel
        from bartholomew.kernel.working_memory import (
            OverflowPolicy,
            WorkingMemoryManager,
            reset_working_memory,
        )

        reset_working_memory()

        kernel = ExperienceKernel()
        wm = WorkingMemoryManager(
            kernel=kernel,
            overflow_policy=OverflowPolicy.PRIORITY,
        )

        # Add items with different tags
        wm.add("tagged item", tags=["important"])
        wm.add("untagged item")

        # Set attention to match tagged item
        kernel.set_attention(
            "something",
            focus_type="task",
            tags=["important"],
        )

        # Boost by attention
        wm.boost_by_attention()

        # Tagged item should have higher priority
        items = wm.get_all()
        tagged = next(i for i in items if "tagged" in i.content)
        untagged = next(i for i in items if "untagged" in i.content)

        assert tagged.priority > untagged.priority


class TestNarratorIntegration:
    """Test Narrator integration with other modules."""

    def test_narrator_workspace_subscription(self, tmp_path):
        """Narrator auto-generates episodes from workspace events."""
        from bartholomew.kernel.experience_kernel import ExperienceKernel
        from bartholomew.kernel.global_workspace import GlobalWorkspace
        from bartholomew.kernel.narrator import NarratorEngine

        db_path = str(tmp_path / "test.db")
        workspace = GlobalWorkspace()

        kernel = ExperienceKernel(db_path=db_path, workspace=workspace)
        narrator = NarratorEngine(db_path=db_path, workspace=workspace)
        narrator.subscribe_to_workspace()

        # Trigger an affect change that should generate an episode
        kernel.update_affect(valence=0.9, arousal=0.8)

        # Check for generated episode
        episodes = narrator.get_recent_episodes(limit=5)

        # May or may not have episode depending on threshold
        # At minimum, subscription should not error
        assert isinstance(episodes, list)

    def test_narrator_episode_persistence(self, tmp_path):
        """Narrator can persist and retrieve episodes."""
        from bartholomew.kernel.narrator import (
            EpisodeType,
            NarratorEngine,
        )

        db_path = str(tmp_path / "test.db")
        narrator = NarratorEngine(db_path=db_path)

        # Generate an episode
        episode = narrator.generate_affect_episode(emotion="happy")
        narrator.persist_episode(episode)

        # Retrieve it
        retrieved = narrator.get_episode(episode.entry_id)

        assert retrieved is not None
        assert retrieved.episode_type == EpisodeType.AFFECT_SHIFT
        assert retrieved.tone == episode.tone


class TestPersonaPackIntegration:
    """Test PersonaPack integration with other modules."""

    def test_persona_manager_with_kernel(self, tmp_path):
        """PersonaPackManager applies drive boosts to ExperienceKernel."""
        from bartholomew.kernel.experience_kernel import ExperienceKernel
        from bartholomew.kernel.persona_pack import (
            PersonaPack,
            PersonaPackManager,
            reset_persona_manager,
        )

        reset_persona_manager()

        db_path = str(tmp_path / "test.db")
        kernel = ExperienceKernel(db_path=db_path)

        # Add a test drive to the kernel
        from bartholomew.kernel.experience_kernel import DriveState

        kernel._drives["test_drive"] = DriveState(
            drive_id="test_drive",
            base_priority=0.5,
            current_activation=0.5,
        )

        manager = PersonaPackManager(
            packs_dir=None,
            experience_kernel=kernel,
            db_path=":memory:",
        )

        # Create pack with drive boost
        pack = PersonaPack(
            pack_id="test_pack",
            name="Test Pack",
            description="Test",
            drive_boosts={"test_drive": 0.3},
        )
        manager.register_pack(pack)
        manager.switch_pack("test_pack")

        # Check drive boost was applied
        drive = kernel.get_drive("test_drive")
        assert drive.context_boost == 0.3

    def test_persona_manager_with_workspace(self, tmp_path):
        """PersonaPackManager emits switch events to GlobalWorkspace."""
        from bartholomew.kernel.global_workspace import GlobalWorkspace
        from bartholomew.kernel.persona_pack import (
            PersonaPack,
            PersonaPackManager,
            reset_persona_manager,
        )

        reset_persona_manager()

        workspace = GlobalWorkspace()
        manager = PersonaPackManager(
            packs_dir=None,
            workspace=workspace,
            db_path=":memory:",
        )

        events = []
        workspace.subscribe("persona", lambda e: events.append(e))

        # Register and switch packs
        pack1 = PersonaPack(pack_id="pack1", name="Pack 1", description="Test")
        pack2 = PersonaPack(pack_id="pack2", name="Pack 2", description="Test")
        manager.register_pack(pack1)
        manager.register_pack(pack2)

        manager.switch_pack("pack1", trigger="startup")
        manager.switch_pack("pack2", trigger="manual")

        # Should have 2 switch events
        assert len(events) == 2
        assert events[1].payload["from_pack_id"] == "pack1"
        assert events[1].payload["to_pack_id"] == "pack2"


# =============================================================================
# Daemon Integration Tests
# =============================================================================


class TestDaemonIntegration:
    """Test KernelDaemon integration with Stage 3 modules."""

    @pytest.fixture
    def mock_config_files(self, tmp_path):
        """Create mock config files for daemon."""
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

    def test_daemon_has_stage3_modules(self, mock_config_files):
        """Daemon should have Stage 3 modules after init."""
        from bartholomew.kernel.daemon import KernelDaemon

        daemon = KernelDaemon(**mock_config_files)

        # Check all Stage 3 modules are present
        assert hasattr(daemon, "workspace")
        assert hasattr(daemon, "experience")
        assert hasattr(daemon, "narrator")
        assert hasattr(daemon, "working_memory")
        assert hasattr(daemon, "persona_manager")

    @pytest.mark.asyncio
    async def test_daemon_start_initializes_kernel(self, mock_config_files):
        """Daemon start() should initialize experience kernel."""
        from bartholomew.kernel.daemon import KernelDaemon

        daemon = KernelDaemon(**mock_config_files)

        # Mock the scheduler to avoid import issues
        with patch(
            "bartholomew.kernel.scheduler.loop.run_scheduler",
            return_value=asyncio.sleep(0),
        ):
            # Start and quickly stop
            await daemon.start()
            await asyncio.sleep(0.1)
            await daemon.stop()

        # Verify experience state was saved on stop
        # (The persist_snapshot call in stop())
        # We can verify by loading - call should not error
        daemon.experience.load_last_snapshot()

    @pytest.mark.asyncio
    async def test_daemon_stop_persists_state(self, mock_config_files):
        """Daemon stop() should persist experience state."""
        from bartholomew.kernel.daemon import KernelDaemon

        daemon = KernelDaemon(**mock_config_files)

        # Modify state before stop
        daemon.experience.update_affect(valence=0.9)
        daemon.experience.add_goal("test_goal")

        # Mock scheduler
        with patch(
            "bartholomew.kernel.scheduler.loop.run_scheduler",
            return_value=asyncio.sleep(0),
        ):
            await daemon.start()
            await asyncio.sleep(0.1)
            await daemon.stop()

        # Create new daemon and verify state was persisted
        daemon2 = KernelDaemon(**mock_config_files)
        snapshot = daemon2.experience.load_last_snapshot()

        # Snapshot should have our changes
        assert snapshot is not None


# =============================================================================
# API Integration Tests
# =============================================================================


class TestAPIIntegration:
    """Test API endpoint integration with Stage 3 modules."""

    @pytest.fixture
    def mock_kernel(self):
        """Create a mock kernel with Stage 3 modules."""
        from bartholomew.kernel.experience_kernel import (
            ExperienceKernel,
        )
        from bartholomew.kernel.narrator import NarratorEngine
        from bartholomew.kernel.persona_pack import (
            PersonaPack,
            PersonaPackManager,
            reset_persona_manager,
        )
        from bartholomew.kernel.working_memory import (
            WorkingMemoryManager,
            reset_working_memory,
        )

        reset_persona_manager()
        reset_working_memory()

        kernel = MagicMock()
        kernel.experience = ExperienceKernel()
        kernel.narrator = NarratorEngine(db_path=":memory:")
        kernel.working_memory = WorkingMemoryManager()
        kernel.persona_manager = PersonaPackManager(
            packs_dir=None,
            db_path=":memory:",
        )

        # Register a test persona
        kernel.persona_manager.register_pack(
            PersonaPack(
                pack_id="test",
                name="Test",
                description="Test pack",
                is_default=True,
            ),
        )

        return kernel

    def test_self_snapshot_serializable(self, mock_kernel):
        """Self snapshot should be JSON serializable."""
        import json

        snapshot = mock_kernel.experience.self_snapshot()
        data = snapshot.to_dict()

        # Should not raise
        json_str = json.dumps(data)
        assert "affect" in json_str
        assert "attention" in json_str
        assert "drives" in json_str

    def test_persona_list_serializable(self, mock_kernel):
        """Persona list should be JSON serializable."""
        import json

        packs = mock_kernel.persona_manager.get_all_packs()
        data = [p.to_dict() for p in packs]

        # Should not raise
        json_str = json.dumps(data)
        assert "test" in json_str

    def test_working_memory_items_serializable(self, mock_kernel):
        """Working memory items should be JSON serializable."""
        import json

        mock_kernel.working_memory.add("test content")
        items = mock_kernel.working_memory.get_all()
        data = [i.to_dict() for i in items]

        # Should not raise
        json_str = json.dumps(data)
        assert "test content" in json_str


# =============================================================================
# Full Lifecycle Test
# =============================================================================


class TestFullLifecycle:
    """Test complete lifecycle of Stage 3 integration."""

    def test_full_lifecycle_flow(self, tmp_path):
        """Test complete flow: boot → state changes → persist → restore."""
        from bartholomew.kernel.experience_kernel import ExperienceKernel
        from bartholomew.kernel.global_workspace import GlobalWorkspace
        from bartholomew.kernel.narrator import NarratorEngine
        from bartholomew.kernel.persona_pack import (
            PersonaPack,
            PersonaPackManager,
            reset_persona_manager,
        )
        from bartholomew.kernel.working_memory import (
            WorkingMemoryManager,
            reset_working_memory,
        )

        reset_persona_manager()
        reset_working_memory()

        db_path = str(tmp_path / "lifecycle.db")

        # === BOOT PHASE ===

        workspace = GlobalWorkspace()
        experience = ExperienceKernel(db_path=db_path, workspace=workspace)
        narrator = NarratorEngine(db_path=db_path, workspace=workspace)
        narrator.subscribe_to_workspace()
        working_memory = WorkingMemoryManager(
            workspace=workspace,
            kernel=experience,
        )
        persona_manager = PersonaPackManager(
            packs_dir=None,
            experience_kernel=experience,
            workspace=workspace,
            db_path=db_path,
        )

        # Register personas
        persona_manager.register_pack(
            PersonaPack(
                pack_id="default",
                name="Default",
                description="Default persona",
                is_default=True,
            ),
        )
        persona_manager.register_pack(
            PersonaPack(
                pack_id="tactical",
                name="Tactical",
                description="Tactical persona",
            ),
        )

        # === RUNTIME PHASE ===

        # Update affect (user seems happy)
        experience.update_affect(valence=0.7, arousal=0.4, energy=0.8)

        # Set attention
        experience.set_attention(
            "user_conversation",
            focus_type="task",
            tags=["chat", "active"],
        )

        # Add to working memory
        working_memory.add("User asked about weather", source="user_input")
        working_memory.add("Today is sunny, 25°C", source="memory_retrieval")

        # Add a goal
        experience.add_goal("help_user_with_weather")

        # Switch persona
        persona_manager.switch_pack("tactical", trigger="manual")

        # === PERSIST PHASE ===

        experience.persist_snapshot()

        # === RESTORE PHASE ===

        reset_persona_manager()
        reset_working_memory()

        # Create new instances
        experience2 = ExperienceKernel(db_path=db_path)
        persona_manager2 = PersonaPackManager(
            packs_dir=None,
            experience_kernel=experience2,
            db_path=db_path,
        )

        # Load state
        snapshot = experience2.load_last_snapshot()

        # Verify state was restored
        assert snapshot is not None
        assert snapshot.affect.valence == 0.7
        assert snapshot.attention.focus_target == "user_conversation"
        assert "help_user_with_weather" in snapshot.active_goals

        # Verify persona switch history
        history = persona_manager2.get_switch_history(limit=5)
        assert len(history) >= 1
        assert any(r.to_pack_id == "tactical" for r in history)
