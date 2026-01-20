"""
Tests for Experience Kernel (Stage 3.1)
--------------------------------------
Comprehensive tests covering:
- DriveState dataclass and serialization
- AffectState dataclass and bounds
- AttentionState dataclass and transitions
- SelfSnapshot serialization/deserialization
- ExperienceKernel initialization
- Affect management
- Attention management
- Drive management
- Goal management
- Context management
- Persistence (save/load snapshots)
"""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bartholomew.kernel.experience_kernel import (
    AffectState,
    AttentionState,
    DriveState,
    ExperienceKernel,
    SelfSnapshot,
)


# =============================================================================
# DriveState Tests
# =============================================================================


class TestDriveState:
    """Tests for DriveState dataclass."""

    def test_drive_state_creation(self):
        """Test basic DriveState creation with defaults."""
        drive = DriveState(drive_id="test_drive")
        assert drive.drive_id == "test_drive"
        assert drive.base_priority == 0.5
        assert drive.current_activation == 0.5
        assert drive.last_satisfied is None
        assert drive.context_boost == 0.0

    def test_drive_state_with_values(self):
        """Test DriveState creation with explicit values."""
        now = datetime.now(timezone.utc)
        drive = DriveState(
            drive_id="protect_user",
            base_priority=0.9,
            current_activation=0.7,
            last_satisfied=now,
            context_boost=0.2,
        )
        assert drive.drive_id == "protect_user"
        assert drive.base_priority == 0.9
        assert drive.current_activation == 0.7
        assert drive.last_satisfied == now
        assert drive.context_boost == 0.2

    def test_effective_activation_with_boost(self):
        """Test effective activation includes context boost."""
        drive = DriveState(
            drive_id="test",
            current_activation=0.5,
            context_boost=0.3,
        )
        assert drive.effective_activation() == 0.8

    def test_effective_activation_clamps_to_bounds(self):
        """Test effective activation is clamped to 0.0-1.0."""
        drive = DriveState(
            drive_id="test",
            current_activation=0.9,
            context_boost=0.5,  # Would sum to 1.4
        )
        assert drive.effective_activation() == 1.0

        drive2 = DriveState(
            drive_id="test",
            current_activation=0.2,
            context_boost=-0.5,  # Would sum to -0.3
        )
        assert drive2.effective_activation() == 0.0

    def test_drive_state_serialization(self):
        """Test DriveState to_dict and from_dict."""
        now = datetime.now(timezone.utc)
        drive = DriveState(
            drive_id="test_drive",
            base_priority=0.8,
            current_activation=0.6,
            last_satisfied=now,
            context_boost=0.1,
        )

        data = drive.to_dict()
        assert data["drive_id"] == "test_drive"
        assert data["base_priority"] == 0.8

        restored = DriveState.from_dict(data)
        assert restored.drive_id == drive.drive_id
        assert restored.base_priority == drive.base_priority
        assert restored.current_activation == drive.current_activation
        assert restored.last_satisfied is not None


# =============================================================================
# AffectState Tests
# =============================================================================


class TestAffectState:
    """Tests for AffectState dataclass."""

    def test_affect_state_defaults(self):
        """Test AffectState default values."""
        affect = AffectState()
        assert affect.valence == 0.2
        assert affect.arousal == 0.3
        assert affect.energy == 0.8
        assert affect.dominant_emotion == "calm"
        assert affect.decay_rate == 0.1

    def test_affect_state_neutral(self):
        """Test AffectState.neutral() factory."""
        affect = AffectState.neutral()
        assert affect.valence == 0.2
        assert affect.arousal == 0.3
        assert affect.energy == 0.8
        assert affect.dominant_emotion == "calm"

    def test_affect_state_with_values(self):
        """Test AffectState with explicit values."""
        affect = AffectState(
            valence=-0.5,
            arousal=0.8,
            energy=0.4,
            dominant_emotion="anxious",
            decay_rate=0.2,
        )
        assert affect.valence == -0.5
        assert affect.arousal == 0.8
        assert affect.energy == 0.4
        assert affect.dominant_emotion == "anxious"

    def test_affect_state_serialization(self):
        """Test AffectState to_dict and from_dict."""
        affect = AffectState(
            valence=0.6,
            arousal=0.4,
            energy=0.7,
            dominant_emotion="joyful",
        )

        data = affect.to_dict()
        restored = AffectState.from_dict(data)

        assert restored.valence == affect.valence
        assert restored.arousal == affect.arousal
        assert restored.energy == affect.energy
        assert restored.dominant_emotion == affect.dominant_emotion


# =============================================================================
# AttentionState Tests
# =============================================================================


class TestAttentionState:
    """Tests for AttentionState dataclass."""

    def test_attention_state_defaults(self):
        """Test AttentionState default values."""
        attention = AttentionState()
        assert attention.focus_target is None
        assert attention.focus_type == "idle"
        assert attention.focus_intensity == 0.5
        assert attention.context_tags == []
        assert attention.since is not None

    def test_attention_state_idle_factory(self):
        """Test AttentionState.idle() factory."""
        attention = AttentionState.idle()
        assert attention.focus_target is None
        assert attention.focus_type == "idle"
        assert attention.focus_intensity == 0.0

    def test_attention_state_with_values(self):
        """Test AttentionState with explicit values."""
        attention = AttentionState(
            focus_target="user message about health",
            focus_type="user_input",
            focus_intensity=0.9,
            context_tags=["wellness", "chat"],
        )
        assert attention.focus_target == "user message about health"
        assert attention.focus_type == "user_input"
        assert attention.focus_intensity == 0.9
        assert "wellness" in attention.context_tags

    def test_attention_state_serialization(self):
        """Test AttentionState to_dict and from_dict."""
        attention = AttentionState(
            focus_target="task processing",
            focus_type="task",
            focus_intensity=0.7,
            context_tags=["gaming"],
        )

        data = attention.to_dict()
        restored = AttentionState.from_dict(data)

        assert restored.focus_target == attention.focus_target
        assert restored.focus_type == attention.focus_type
        assert restored.focus_intensity == attention.focus_intensity
        assert restored.context_tags == attention.context_tags


# =============================================================================
# SelfSnapshot Tests
# =============================================================================


class TestSelfSnapshot:
    """Tests for SelfSnapshot dataclass."""

    def test_self_snapshot_creation(self):
        """Test SelfSnapshot creation."""
        now = datetime.now(timezone.utc)
        snapshot = SelfSnapshot(
            snapshot_id="snap-001",
            timestamp=now,
            drives=[DriveState(drive_id="test")],
            affect=AffectState.neutral(),
            attention=AttentionState.idle(),
            active_goals=["help user"],
            context={"session": "test"},
            metadata={"reason": "test"},
        )

        assert snapshot.snapshot_id == "snap-001"
        assert snapshot.timestamp == now
        assert len(snapshot.drives) == 1
        assert snapshot.active_goals == ["help user"]

    def test_self_snapshot_serialization_roundtrip(self):
        """Test SelfSnapshot to_dict and from_dict roundtrip."""
        now = datetime.now(timezone.utc)
        original = SelfSnapshot(
            snapshot_id="snap-002",
            timestamp=now,
            drives=[
                DriveState(drive_id="drive1", base_priority=0.9),
                DriveState(drive_id="drive2", base_priority=0.7),
            ],
            affect=AffectState(valence=0.5, arousal=0.4, energy=0.6),
            attention=AttentionState(
                focus_target="test",
                focus_type="task",
            ),
            active_goals=["goal1", "goal2"],
            context={"key": "value"},
            metadata={"meta": "data"},
        )

        data = original.to_dict()
        restored = SelfSnapshot.from_dict(data)

        assert restored.snapshot_id == original.snapshot_id
        assert len(restored.drives) == 2
        assert restored.drives[0].drive_id == "drive1"
        assert restored.affect.valence == 0.5
        assert restored.attention.focus_target == "test"
        assert restored.active_goals == ["goal1", "goal2"]
        assert restored.context["key"] == "value"


# =============================================================================
# ExperienceKernel Initialization Tests
# =============================================================================


class TestExperienceKernelInit:
    """Tests for ExperienceKernel initialization."""

    def test_kernel_init_with_defaults(self):
        """Test ExperienceKernel initialization with defaults."""
        kernel = ExperienceKernel()

        # Should have default drives
        assert len(kernel.get_all_drives()) > 0

        # Should have neutral affect
        affect = kernel.get_affect()
        assert affect.dominant_emotion == "calm"

        # Should have idle attention
        attention = kernel.get_attention()
        assert attention.focus_type == "idle"

    def test_kernel_init_with_identity_yaml(self):
        """Test ExperienceKernel loads drives from Identity.yaml."""
        kernel = ExperienceKernel(identity_path="Identity.yaml")

        drives = kernel.get_all_drives()
        # Identity.yaml has 16 drives defined
        assert len(drives) >= 14  # Allow for some variation

        # Check some expected drives exist
        drive_ids = [d.drive_id for d in drives]
        assert "protect_user_wellbeing" in drive_ids

    def test_kernel_init_with_custom_db_path(self):
        """Test ExperienceKernel with custom database path."""
        # Use ignore_cleanup_errors=True for Windows SQLite file locking
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "test_kernel.db"
            _kernel = ExperienceKernel(db_path=str(db_path))

            # Should create database
            assert db_path.exists()
            del _kernel  # Silence flake8 unused variable warning

            # Should have schema
            with sqlite3.connect(db_path) as conn:
                tables = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
                table_names = [t[0] for t in tables]
                assert "experience_snapshots" in table_names


# =============================================================================
# ExperienceKernel Affect Management Tests
# =============================================================================


class TestExperienceKernelAffect:
    """Tests for ExperienceKernel affect management."""

    def test_update_affect_valence(self):
        """Test updating affect valence."""
        kernel = ExperienceKernel()
        kernel.update_affect(valence=0.8)

        affect = kernel.get_affect()
        assert affect.valence == 0.8

    def test_update_affect_all_values(self):
        """Test updating all affect values."""
        kernel = ExperienceKernel()
        kernel.update_affect(
            valence=-0.3,
            arousal=0.9,
            energy=0.5,
            emotion="excited",
        )

        affect = kernel.get_affect()
        assert affect.valence == -0.3
        assert affect.arousal == 0.9
        assert affect.energy == 0.5
        assert affect.dominant_emotion == "excited"

    def test_update_affect_clamps_values(self):
        """Test that affect values are clamped to valid ranges."""
        kernel = ExperienceKernel()

        # Try to set out-of-range values
        kernel.update_affect(valence=2.0, arousal=-0.5, energy=1.5)

        affect = kernel.get_affect()
        assert affect.valence == 1.0  # Clamped to max
        assert affect.arousal == 0.0  # Clamped to min
        assert affect.energy == 1.0  # Clamped to max

    def test_decay_affect_to_baseline(self):
        """Test affect decay toward baseline."""
        kernel = ExperienceKernel()

        # Set non-baseline values
        kernel.update_affect(valence=0.9, arousal=0.9)

        # Decay
        kernel.decay_affect_to_baseline(delta_seconds=600)

        affect = kernel.get_affect()
        # Should have moved toward baseline (0.2, 0.3)
        assert affect.valence < 0.9
        assert affect.arousal < 0.9


# =============================================================================
# ExperienceKernel Attention Management Tests
# =============================================================================


class TestExperienceKernelAttention:
    """Tests for ExperienceKernel attention management."""

    def test_set_attention(self):
        """Test setting attention focus."""
        kernel = ExperienceKernel()
        kernel.set_attention(
            target="user question about health",
            focus_type="user_input",
            intensity=0.9,
            tags=["wellness", "chat"],
        )

        attention = kernel.get_attention()
        assert attention.focus_target == "user question about health"
        assert attention.focus_type == "user_input"
        assert attention.focus_intensity == 0.9
        assert "wellness" in attention.context_tags

    def test_set_attention_invalid_type_raises(self):
        """Test that invalid focus_type raises ValueError."""
        kernel = ExperienceKernel()

        with pytest.raises(ValueError, match="focus_type must be one of"):
            kernel.set_attention(
                target="test",
                focus_type="invalid_type",
            )

    def test_clear_attention(self):
        """Test clearing attention to idle."""
        kernel = ExperienceKernel()
        kernel.set_attention(
            target="something",
            focus_type="task",
        )

        kernel.clear_attention()

        attention = kernel.get_attention()
        assert attention.focus_type == "idle"
        assert attention.focus_target is None


# =============================================================================
# ExperienceKernel Drive Management Tests
# =============================================================================


class TestExperienceKernelDrives:
    """Tests for ExperienceKernel drive management."""

    def test_get_drive(self):
        """Test getting a specific drive."""
        kernel = ExperienceKernel()
        drive = kernel.get_drive("protect_user_wellbeing")

        assert drive is not None
        assert drive.drive_id == "protect_user_wellbeing"

    def test_get_nonexistent_drive(self):
        """Test getting a non-existent drive returns None."""
        kernel = ExperienceKernel()
        drive = kernel.get_drive("nonexistent_drive")
        assert drive is None

    def test_activate_drive(self):
        """Test activating a drive."""
        kernel = ExperienceKernel()
        initial_drive = kernel.get_drive("protect_user_wellbeing")
        initial_activation = initial_drive.current_activation

        kernel.activate_drive("protect_user_wellbeing", boost=0.2)

        drive = kernel.get_drive("protect_user_wellbeing")
        assert drive.current_activation > initial_activation
        assert drive.context_boost == 0.2

    def test_activate_unknown_drive_raises(self):
        """Test activating unknown drive raises ValueError."""
        kernel = ExperienceKernel()

        with pytest.raises(ValueError, match="Unknown drive"):
            kernel.activate_drive("unknown_drive")

    def test_satisfy_drive(self):
        """Test satisfying a drive."""
        kernel = ExperienceKernel()

        # Activate first to have something to satisfy
        kernel.activate_drive("protect_user_wellbeing")
        drive_before = kernel.get_drive("protect_user_wellbeing")
        activation_before = drive_before.current_activation

        kernel.satisfy_drive("protect_user_wellbeing")

        drive_after = kernel.get_drive("protect_user_wellbeing")
        assert drive_after.last_satisfied is not None
        assert drive_after.current_activation < activation_before
        assert drive_after.context_boost == 0.0

    def test_get_top_drives(self):
        """Test getting top N drives by activation."""
        kernel = ExperienceKernel()

        # Boost one drive
        kernel.activate_drive("protect_user_wellbeing", boost=0.5)

        top = kernel.get_top_drives(n=3)
        assert len(top) == 3

        # First drive should have highest activation
        assert top[0].effective_activation() >= top[1].effective_activation()


# =============================================================================
# ExperienceKernel Goal Management Tests
# =============================================================================


class TestExperienceKernelGoals:
    """Tests for ExperienceKernel goal management."""

    def test_add_goal(self):
        """Test adding a goal."""
        kernel = ExperienceKernel()
        kernel.add_goal("Help user with wellness check")

        goals = kernel.get_active_goals()
        assert "Help user with wellness check" in goals

    def test_add_duplicate_goal_ignored(self):
        """Test that duplicate goals are not added."""
        kernel = ExperienceKernel()
        kernel.add_goal("Test goal")
        kernel.add_goal("Test goal")

        goals = kernel.get_active_goals()
        assert goals.count("Test goal") == 1

    def test_complete_goal(self):
        """Test completing a goal."""
        kernel = ExperienceKernel()
        kernel.add_goal("Finish task")

        result = kernel.complete_goal("Finish task")
        assert result is True

        goals = kernel.get_active_goals()
        assert "Finish task" not in goals

    def test_complete_nonexistent_goal(self):
        """Test completing a goal that doesn't exist returns False."""
        kernel = ExperienceKernel()
        result = kernel.complete_goal("Never added")
        assert result is False

    def test_clear_goals(self):
        """Test clearing all goals."""
        kernel = ExperienceKernel()
        kernel.add_goal("Goal 1")
        kernel.add_goal("Goal 2")

        kernel.clear_goals()

        goals = kernel.get_active_goals()
        assert len(goals) == 0


# =============================================================================
# ExperienceKernel Context Management Tests
# =============================================================================


class TestExperienceKernelContext:
    """Tests for ExperienceKernel context management."""

    def test_set_and_get_context(self):
        """Test setting and getting context values."""
        kernel = ExperienceKernel()
        kernel.set_context("user_activity", "gaming")
        kernel.set_context("session_id", "abc123")

        assert kernel.get_context("user_activity") == "gaming"
        assert kernel.get_context("session_id") == "abc123"

    def test_get_context_default(self):
        """Test getting context with default value."""
        kernel = ExperienceKernel()
        value = kernel.get_context("nonexistent", default="default_value")
        assert value == "default_value"

    def test_clear_context(self):
        """Test clearing all context."""
        kernel = ExperienceKernel()
        kernel.set_context("key1", "value1")
        kernel.set_context("key2", "value2")

        kernel.clear_context()

        assert kernel.get_context("key1") is None
        assert kernel.get_context("key2") is None


# =============================================================================
# ExperienceKernel Self Snapshot Tests
# =============================================================================


class TestExperienceKernelSnapshot:
    """Tests for ExperienceKernel self_snapshot method."""

    def test_self_snapshot_returns_complete_state(self):
        """Test self_snapshot returns all state components."""
        kernel = ExperienceKernel()
        kernel.update_affect(valence=0.6, emotion="curious")
        kernel.set_attention("test", "task", tags=["testing"])
        kernel.add_goal("Test goal")
        kernel.set_context("test_key", "test_value")

        snapshot = kernel.self_snapshot()

        assert snapshot.snapshot_id is not None
        assert snapshot.timestamp is not None
        assert len(snapshot.drives) > 0
        assert snapshot.affect.valence == 0.6
        assert snapshot.attention.focus_target == "test"
        assert "Test goal" in snapshot.active_goals
        assert snapshot.context["test_key"] == "test_value"

    def test_self_snapshot_is_serializable(self):
        """Test that self_snapshot produces JSON-serializable output."""
        import json

        kernel = ExperienceKernel()
        snapshot = kernel.self_snapshot()

        # Should not raise
        data = snapshot.to_dict()
        json_str = json.dumps(data)
        assert len(json_str) > 0


# =============================================================================
# ExperienceKernel Persistence Tests
# =============================================================================


class TestExperienceKernelPersistence:
    """Tests for ExperienceKernel persistence."""

    def test_persist_snapshot(self):
        """Test persisting a snapshot to database."""
        # Use ignore_cleanup_errors=True for Windows SQLite file locking
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            kernel = ExperienceKernel(db_path=str(db_path))

            kernel.update_affect(valence=0.7)
            kernel.add_goal("Persist test")

            snapshot_id = kernel.persist_snapshot(reason="test")
            assert snapshot_id is not None

            # Verify in database
            with sqlite3.connect(db_path) as conn:
                count = conn.execute("SELECT COUNT(*) FROM experience_snapshots").fetchone()[0]
                assert count == 1

    def test_load_last_snapshot(self):
        """Test loading the most recent snapshot."""
        # Use ignore_cleanup_errors=True for Windows SQLite file locking
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            kernel = ExperienceKernel(db_path=str(db_path))

            kernel.update_affect(valence=0.8, emotion="joyful")
            kernel.add_goal("Load test goal")
            kernel.persist_snapshot()

            # Load it back
            loaded = kernel.load_last_snapshot()

            assert loaded is not None
            assert loaded.affect.valence == 0.8
            assert loaded.affect.dominant_emotion == "joyful"
            assert "Load test goal" in loaded.active_goals

    def test_load_last_snapshot_empty_db(self):
        """Test loading from empty database returns None."""
        # Use temp file DB to properly test empty scenario
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "empty.db"
            kernel = ExperienceKernel(db_path=str(db_path))
            loaded = kernel.load_last_snapshot()
            assert loaded is None

    def test_restore_from_snapshot(self):
        """Test restoring kernel state from a snapshot."""
        # Use ignore_cleanup_errors=True for Windows SQLite file locking
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            kernel = ExperienceKernel(db_path=str(db_path))

            # Set state
            kernel.update_affect(valence=0.9, emotion="excited")
            kernel.add_goal("Restore test")
            kernel.set_context("key", "value")
            kernel.persist_snapshot()

            # Create new kernel
            kernel2 = ExperienceKernel(db_path=str(db_path))
            snapshot = kernel2.load_last_snapshot()
            kernel2.restore_from_snapshot(snapshot)

            # Verify state restored
            assert kernel2.get_affect().valence == 0.9
            assert "Restore test" in kernel2.get_active_goals()
            assert kernel2.get_context("key") == "value"

    def test_snapshot_history(self):
        """Test getting snapshot history."""
        # Use ignore_cleanup_errors=True for Windows SQLite file locking
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            kernel = ExperienceKernel(db_path=str(db_path))

            # Create multiple snapshots
            for i in range(5):
                kernel.update_affect(valence=i * 0.2)
                kernel.persist_snapshot(reason=f"snapshot_{i}")

            history = kernel.get_snapshot_history(limit=3)
            assert len(history) == 3

            # Most recent should be first
            assert history[0].affect.valence == 0.8  # 4 * 0.2


# =============================================================================
# Integration Tests
# =============================================================================


class TestExperienceKernelIntegration:
    """Integration tests for ExperienceKernel."""

    def test_full_lifecycle(self):
        """Test complete kernel lifecycle."""
        # Use ignore_cleanup_errors=True for Windows SQLite file locking
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "lifecycle.db"

            # Create and configure kernel
            kernel = ExperienceKernel(
                identity_path="Identity.yaml",
                db_path=str(db_path),
            )

            # Set initial state
            kernel.update_affect(valence=0.5, arousal=0.6, emotion="curious")
            kernel.set_attention(
                target="user wellness question",
                focus_type="user_input",
                intensity=0.8,
                tags=["wellness"],
            )
            kernel.add_goal("Answer health question")
            kernel.activate_drive("protect_user_wellbeing")
            kernel.set_context("session_type", "wellness_check")

            # Take snapshot
            _snapshot1_id = kernel.persist_snapshot(reason="session_start")
            assert _snapshot1_id is not None

            # Simulate processing
            kernel.satisfy_drive("protect_user_wellbeing")
            kernel.complete_goal("Answer health question")
            kernel.update_affect(valence=0.7, emotion="satisfied")
            kernel.clear_attention()

            # Take another snapshot
            _snapshot2_id = kernel.persist_snapshot(reason="task_complete")
            assert _snapshot2_id is not None

            # Verify history
            history = kernel.get_snapshot_history(limit=10)
            assert len(history) == 2

            # Latest should show task completed
            latest = history[0]
            assert "Answer health question" not in latest.active_goals

            # Earlier should show task active
            earlier = history[1]
            assert "Answer health question" in earlier.active_goals

    def test_kernel_with_identity_drives_integration(self):
        """Test kernel integrates drives from Identity.yaml."""
        kernel = ExperienceKernel(identity_path="Identity.yaml")

        # Get snapshot
        snapshot = kernel.self_snapshot()

        # Should have drives from Identity.yaml
        drive_ids = [d.drive_id for d in snapshot.drives]

        # Check for expected drives from Identity.yaml
        expected = [
            "protect_user_wellbeing",
            "preserve_user_autonomy",
            "show_kindness_in_all_interactions",
        ]
        for drive_id in expected:
            assert drive_id in drive_ids, f"Missing drive: {drive_id}"
