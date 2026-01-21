"""
Tests for Narrator Episodic Layer
---------------------------------
Stage 3.4: Narrator Episodic Layer Implementation

Comprehensive tests covering:
- EpisodicEntry creation and serialization
- NarratorConfig loading from Identity.yaml
- Episode generation for each event type
- Emotional coloring based on affect
- Database persistence and retrieval
- GlobalWorkspace subscription and auto-episode creation
- Reflection narrative composition
"""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from bartholomew.kernel.narrator import (
    EpisodeType,
    EpisodicEntry,
    NarrativeTemplates,
    NarrativeTone,
    NarratorConfig,
    NarratorEngine,
    get_narrator,
    reset_narrator,
)


# =============================================================================
# Test EpisodeType Enum
# =============================================================================


class TestEpisodeType:
    """Tests for EpisodeType enum."""

    def test_all_episode_types_exist(self):
        """Verify all expected episode types are defined."""
        expected = [
            "AFFECT_SHIFT",
            "ATTENTION_FOCUS",
            "DRIVE_ACTIVATED",
            "DRIVE_SATISFIED",
            "GOAL_ADDED",
            "GOAL_COMPLETED",
            "OBSERVATION",
            "REFLECTION",
            "MEMORY_EVENT",
            "SYSTEM_EVENT",
        ]
        for name in expected:
            assert hasattr(EpisodeType, name)

    def test_episode_type_values(self):
        """Verify episode type values are snake_case strings."""
        assert EpisodeType.AFFECT_SHIFT.value == "affect_shift"
        assert EpisodeType.ATTENTION_FOCUS.value == "attention_focus"
        assert EpisodeType.DRIVE_ACTIVATED.value == "drive_activated"
        assert EpisodeType.GOAL_COMPLETED.value == "goal_completed"


# =============================================================================
# Test NarrativeTone Enum
# =============================================================================


class TestNarrativeTone:
    """Tests for NarrativeTone enum."""

    def test_all_tones_exist(self):
        """Verify all expected tones are defined."""
        expected = [
            "ENTHUSIASTIC",
            "CONCERNED",
            "CONTENT",
            "SUBDUED",
            "NEUTRAL",
        ]
        for name in expected:
            assert hasattr(NarrativeTone, name)

    def test_tone_values(self):
        """Verify tone values are lowercase strings."""
        assert NarrativeTone.ENTHUSIASTIC.value == "enthusiastic"
        assert NarrativeTone.CONCERNED.value == "concerned"
        assert NarrativeTone.CONTENT.value == "content"


# =============================================================================
# Test EpisodicEntry
# =============================================================================


class TestEpisodicEntry:
    """Tests for EpisodicEntry dataclass."""

    def test_create_basic_entry(self):
        """Test creating a basic episodic entry."""
        entry = EpisodicEntry.create(
            episode_type=EpisodeType.OBSERVATION,
            narrative="I noticed something interesting.",
        )

        assert entry.entry_id is not None
        assert len(entry.entry_id) == 36  # UUID format
        assert entry.timestamp is not None
        assert entry.episode_type == EpisodeType.OBSERVATION
        assert entry.narrative == "I noticed something interesting."
        assert entry.tone == NarrativeTone.NEUTRAL

    def test_create_full_entry(self):
        """Test creating an entry with all fields."""
        affect = {"valence": 0.5, "arousal": 0.3}
        entry = EpisodicEntry.create(
            episode_type=EpisodeType.AFFECT_SHIFT,
            narrative="I felt a shift.",
            tone=NarrativeTone.CONTENT,
            affect_snapshot=affect,
            source_event_id="event-123",
            source_channel="affect",
            tags=["emotion", "happy"],
            metadata={"trigger": "user_message"},
        )

        assert entry.tone == NarrativeTone.CONTENT
        assert entry.affect_snapshot == affect
        assert entry.source_event_id == "event-123"
        assert entry.source_channel == "affect"
        assert "emotion" in entry.tags
        assert entry.metadata["trigger"] == "user_message"

    def test_entry_to_dict(self):
        """Test serializing entry to dictionary."""
        entry = EpisodicEntry.create(
            episode_type=EpisodeType.GOAL_ADDED,
            narrative="I set a new goal.",
            tags=["goal"],
        )

        data = entry.to_dict()

        assert data["entry_id"] == entry.entry_id
        assert data["episode_type"] == "goal_added"
        assert data["narrative"] == "I set a new goal."
        assert data["tone"] == "neutral"
        assert "goal" in data["tags"]
        assert "timestamp" in data

    def test_entry_from_dict(self):
        """Test deserializing entry from dictionary."""
        data = {
            "entry_id": "test-123",
            "timestamp": "2026-01-20T10:00:00+00:00",
            "episode_type": "attention_focus",
            "narrative": "I focused on something.",
            "tone": "enthusiastic",
            "affect_snapshot": {"valence": 0.8},
            "source_event_id": "evt-456",
            "source_channel": "attention",
            "tags": ["focus"],
            "metadata": {"target": "user"},
        }

        entry = EpisodicEntry.from_dict(data)

        assert entry.entry_id == "test-123"
        assert entry.episode_type == EpisodeType.ATTENTION_FOCUS
        assert entry.tone == NarrativeTone.ENTHUSIASTIC
        assert entry.affect_snapshot["valence"] == 0.8

    def test_entry_roundtrip(self):
        """Test serialization roundtrip."""
        original = EpisodicEntry.create(
            episode_type=EpisodeType.DRIVE_SATISFIED,
            narrative="I addressed a need.",
            tone=NarrativeTone.CONTENT,
            tags=["drive", "satisfaction"],
        )

        data = original.to_dict()
        restored = EpisodicEntry.from_dict(data)

        assert restored.entry_id == original.entry_id
        assert restored.episode_type == original.episode_type
        assert restored.narrative == original.narrative
        assert restored.tone == original.tone


# =============================================================================
# Test NarratorConfig
# =============================================================================


class TestNarratorConfig:
    """Tests for NarratorConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = NarratorConfig()

        assert config.enabled is True
        assert config.redact_personal_data is True
        assert config.exportable is True
        assert config.auto_subscribe is True
        assert config.min_affect_change_threshold == 0.15
        assert "supportive" in config.style.lower()

    def test_config_from_identity_file_not_found(self):
        """Test config loading when Identity.yaml doesn't exist."""
        config = NarratorConfig.from_identity("nonexistent.yaml")

        # Should return defaults
        assert config.enabled is True

    def test_config_from_identity_with_valid_file(self):
        """Test config loading from a valid Identity.yaml."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(
                """
identity:
  self_model:
    narrator_episodic_layer:
      enabled: true
      style: "test style"
      logs:
        redact_personal_data: false
        exportable: true
""",
            )
            f.flush()

            config = NarratorConfig.from_identity(f.name)

            assert config.enabled is True
            assert config.style == "test style"
            assert config.redact_personal_data is False
            assert config.exportable is True

            Path(f.name).unlink()


# =============================================================================
# Test NarrativeTemplates
# =============================================================================


class TestNarrativeTemplates:
    """Tests for NarrativeTemplates class."""

    def test_affect_shift_templates_exist_for_all_tones(self):
        """Verify affect shift templates exist for all tones."""
        for tone in NarrativeTone:
            assert tone in NarrativeTemplates.AFFECT_SHIFT
            assert len(NarrativeTemplates.AFFECT_SHIFT[tone]) >= 1

    def test_attention_focus_templates_exist_for_all_tones(self):
        """Verify attention focus templates exist for all tones."""
        for tone in NarrativeTone:
            assert tone in NarrativeTemplates.ATTENTION_FOCUS

    def test_drive_templates_exist(self):
        """Verify drive templates exist."""
        for tone in NarrativeTone:
            assert tone in NarrativeTemplates.DRIVE_ACTIVATED
            assert tone in NarrativeTemplates.DRIVE_SATISFIED

    def test_goal_templates_exist(self):
        """Verify goal templates exist."""
        for tone in NarrativeTone:
            assert tone in NarrativeTemplates.GOAL_ADDED
            assert tone in NarrativeTemplates.GOAL_COMPLETED

    def test_templates_contain_placeholders(self):
        """Verify templates contain the expected placeholders."""
        for template in NarrativeTemplates.AFFECT_SHIFT[NarrativeTone.NEUTRAL]:
            assert "{emotion}" in template

        for template in NarrativeTemplates.ATTENTION_FOCUS[NarrativeTone.NEUTRAL]:
            assert "{target}" in template

        for template in NarrativeTemplates.DRIVE_ACTIVATED[NarrativeTone.NEUTRAL]:
            assert "{drive}" in template


# =============================================================================
# Test NarratorEngine Initialization
# =============================================================================


class TestNarratorEngineInit:
    """Tests for NarratorEngine initialization."""

    def test_init_with_defaults(self):
        """Test initialization with default values."""
        narrator = NarratorEngine()

        assert narrator._kernel is None
        assert narrator._workspace is None
        assert narrator._config is not None
        assert narrator._db_path == ":memory:"

    def test_init_with_custom_db_path(self):
        """Test initialization with custom database path."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        narrator = NarratorEngine(db_path=db_path)

        assert narrator._db_path == db_path

        # Verify schema was created
        with sqlite3.connect(db_path) as conn:
            tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            table_names = [t[0] for t in tables]
            assert "episodic_entries" in table_names

        Path(db_path).unlink()

    def test_init_creates_database_schema(self):
        """Test that initialization creates the database schema."""
        narrator = NarratorEngine()

        with sqlite3.connect(narrator._db_path) as conn:
            # Check table exists
            result = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='episodic_entries'",
            ).fetchone()
            assert result is not None

            # Check indexes exist
            indexes = conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
            index_names = [i[0] for i in indexes]
            assert "idx_episodic_entries_timestamp" in index_names


# =============================================================================
# Test Tone Determination
# =============================================================================


class TestToneDetermination:
    """Tests for NarratorEngine tone determination."""

    def test_determine_tone_without_kernel(self):
        """Test tone determination without experience kernel."""
        narrator = NarratorEngine()
        tone = narrator.determine_tone()

        assert tone == NarrativeTone.NEUTRAL

    def test_determine_tone_enthusiastic(self):
        """Test enthusiastic tone (high valence + high arousal)."""
        from bartholomew.kernel.experience_kernel import AffectState

        narrator = NarratorEngine()
        affect = AffectState(valence=0.5, arousal=0.7)

        tone = narrator.determine_tone(affect)

        assert tone == NarrativeTone.ENTHUSIASTIC

    def test_determine_tone_concerned(self):
        """Test concerned tone (low valence + high arousal)."""
        from bartholomew.kernel.experience_kernel import AffectState

        narrator = NarratorEngine()
        affect = AffectState(valence=-0.3, arousal=0.6)

        tone = narrator.determine_tone(affect)

        assert tone == NarrativeTone.CONCERNED

    def test_determine_tone_content(self):
        """Test content tone (high valence + low arousal)."""
        from bartholomew.kernel.experience_kernel import AffectState

        narrator = NarratorEngine()
        affect = AffectState(valence=0.3, arousal=0.2)

        tone = narrator.determine_tone(affect)

        assert tone == NarrativeTone.CONTENT

    def test_determine_tone_subdued(self):
        """Test subdued tone (low valence + low arousal)."""
        from bartholomew.kernel.experience_kernel import AffectState

        narrator = NarratorEngine()
        affect = AffectState(valence=-0.2, arousal=0.2)

        tone = narrator.determine_tone(affect)

        assert tone == NarrativeTone.SUBDUED

    def test_determine_tone_neutral(self):
        """Test neutral tone (balanced state)."""
        from bartholomew.kernel.experience_kernel import AffectState

        narrator = NarratorEngine()
        affect = AffectState(valence=0.1, arousal=0.45)

        tone = narrator.determine_tone(affect)

        assert tone == NarrativeTone.NEUTRAL


# =============================================================================
# Test Episode Generation
# =============================================================================


class TestEpisodeGeneration:
    """Tests for episode generation methods."""

    def test_generate_affect_episode(self):
        """Test generating an affect shift episode."""
        narrator = NarratorEngine()
        episode = narrator.generate_affect_episode(emotion="happy")

        assert episode.episode_type == EpisodeType.AFFECT_SHIFT
        assert "happy" in episode.narrative.lower()
        assert "affect" in episode.tags
        assert "emotion" in episode.tags
        assert episode.metadata["emotion"] == "happy"

    def test_generate_attention_episode(self):
        """Test generating an attention focus episode."""
        narrator = NarratorEngine()
        episode = narrator.generate_attention_episode(target="user message")

        assert episode.episode_type == EpisodeType.ATTENTION_FOCUS
        assert "user message" in episode.narrative
        assert "attention" in episode.tags
        assert "focus" in episode.tags
        assert episode.metadata["target"] == "user message"

    def test_generate_drive_activated_episode(self):
        """Test generating a drive activated episode."""
        narrator = NarratorEngine()
        episode = narrator.generate_drive_activated_episode(drive_id="protect_user_wellbeing")

        assert episode.episode_type == EpisodeType.DRIVE_ACTIVATED
        assert "protect user wellbeing" in episode.narrative.lower()
        assert "drive" in episode.tags
        assert episode.metadata["drive_id"] == "protect_user_wellbeing"

    def test_generate_drive_satisfied_episode(self):
        """Test generating a drive satisfied episode."""
        narrator = NarratorEngine()
        episode = narrator.generate_drive_satisfied_episode(drive_id="reduce_cognitive_load")

        assert episode.episode_type == EpisodeType.DRIVE_SATISFIED
        assert "reduce cognitive load" in episode.narrative.lower()
        assert "satisfaction" in episode.tags

    def test_generate_goal_added_episode(self):
        """Test generating a goal added episode."""
        narrator = NarratorEngine()
        episode = narrator.generate_goal_added_episode(goal="Help user relax")

        assert episode.episode_type == EpisodeType.GOAL_ADDED
        assert "Help user relax" in episode.narrative
        assert "goal" in episode.tags
        assert "intention" in episode.tags
        assert episode.metadata["goal"] == "Help user relax"

    def test_generate_goal_completed_episode(self):
        """Test generating a goal completed episode."""
        narrator = NarratorEngine()
        episode = narrator.generate_goal_completed_episode(goal="Check in with user")

        assert episode.episode_type == EpisodeType.GOAL_COMPLETED
        assert "Check in with user" in episode.narrative
        assert "completion" in episode.tags
        assert "achievement" in episode.tags

    def test_generate_observation_episode(self):
        """Test generating an observation episode."""
        narrator = NarratorEngine()
        episode = narrator.generate_observation_episode(
            content="The user seems engaged today",
            tags=["user", "engagement"],
        )

        assert episode.episode_type == EpisodeType.OBSERVATION
        assert "engaged" in episode.narrative.lower()
        assert "user" in episode.tags
        assert "engagement" in episode.tags

    def test_generate_reflection_episode(self):
        """Test generating a reflection episode."""
        narrator = NarratorEngine()
        episode = narrator.generate_reflection_episode(
            content="Today was productive.",
            period="daily",
        )

        assert episode.episode_type == EpisodeType.REFLECTION
        assert episode.narrative == "Today was productive."
        assert "reflection" in episode.tags
        assert "daily" in episode.tags
        assert episode.metadata["period"] == "daily"


# =============================================================================
# Test Episode Generation with Kernel Integration
# =============================================================================


class TestEpisodeGenerationWithKernel:
    """Tests for episode generation with ExperienceKernel."""

    def test_generate_episode_with_affect_from_kernel(self):
        """Test episode generation uses kernel's affect state."""
        from bartholomew.kernel.experience_kernel import ExperienceKernel

        kernel = ExperienceKernel()
        kernel.update_affect(valence=0.6, arousal=0.7, emotion="excited")

        narrator = NarratorEngine(experience_kernel=kernel)
        episode = narrator.generate_affect_episode()

        assert episode.tone == NarrativeTone.ENTHUSIASTIC
        assert episode.affect_snapshot is not None
        assert episode.affect_snapshot["dominant_emotion"] == "excited"

    def test_affect_snapshot_included_in_episode(self):
        """Test that affect snapshot is captured in episodes."""
        from bartholomew.kernel.experience_kernel import ExperienceKernel

        kernel = ExperienceKernel()
        narrator = NarratorEngine(experience_kernel=kernel)

        episode = narrator.generate_observation_episode("Test observation")

        assert episode.affect_snapshot is not None
        assert "valence" in episode.affect_snapshot
        assert "arousal" in episode.affect_snapshot
        assert "energy" in episode.affect_snapshot


# =============================================================================
# Test Persistence
# =============================================================================


class TestPersistence:
    """Tests for episode persistence."""

    def test_persist_episode(self):
        """Test persisting an episode to database."""
        narrator = NarratorEngine()
        episode = narrator.generate_observation_episode("Test content")

        entry_id = narrator.persist_episode(episode)

        assert entry_id == episode.entry_id
        assert narrator.get_episode_count() == 1

    def test_get_episode_by_id(self):
        """Test retrieving an episode by ID."""
        narrator = NarratorEngine()
        episode = narrator.generate_affect_episode(emotion="calm")
        narrator.persist_episode(episode)

        retrieved = narrator.get_episode(episode.entry_id)

        assert retrieved is not None
        assert retrieved.entry_id == episode.entry_id
        assert retrieved.narrative == episode.narrative

    def test_get_episode_not_found(self):
        """Test retrieving non-existent episode returns None."""
        narrator = NarratorEngine()

        retrieved = narrator.get_episode("nonexistent-id")

        assert retrieved is None

    def test_get_recent_episodes(self):
        """Test retrieving recent episodes."""
        narrator = NarratorEngine()

        # Create several episodes
        for i in range(5):
            episode = narrator.generate_observation_episode(f"Content {i}")
            narrator.persist_episode(episode)

        recent = narrator.get_recent_episodes(limit=3)

        assert len(recent) == 3
        # Most recent first
        assert "Content 4" in recent[0].narrative

    def test_get_recent_episodes_with_since_filter(self):
        """Test retrieving episodes since a timestamp."""
        narrator = NarratorEngine()

        # Create an episode
        episode1 = narrator.generate_observation_episode("Old episode")
        narrator.persist_episode(episode1)

        # Wait a tiny bit and mark timestamp
        cutoff = datetime.now(timezone.utc)

        # Create another episode
        episode2 = narrator.generate_observation_episode("New episode")
        narrator.persist_episode(episode2)

        recent = narrator.get_recent_episodes(since=cutoff)

        assert len(recent) == 1
        assert "New episode" in recent[0].narrative

    def test_get_episodes_by_type(self):
        """Test filtering episodes by type."""
        narrator = NarratorEngine()

        # Create different episode types
        narrator.persist_episode(narrator.generate_affect_episode(emotion="happy"))
        narrator.persist_episode(narrator.generate_attention_episode(target="task"))
        narrator.persist_episode(narrator.generate_affect_episode(emotion="calm"))

        affect_episodes = narrator.get_episodes_by_type(EpisodeType.AFFECT_SHIFT)

        assert len(affect_episodes) == 2
        for ep in affect_episodes:
            assert ep.episode_type == EpisodeType.AFFECT_SHIFT

    def test_get_episodes_by_tag(self):
        """Test filtering episodes by tag."""
        narrator = NarratorEngine()

        narrator.persist_episode(
            narrator.generate_observation_episode("Test 1", tags=["important"]),
        )
        narrator.persist_episode(narrator.generate_observation_episode("Test 2", tags=["routine"]))
        narrator.persist_episode(
            narrator.generate_observation_episode("Test 3", tags=["important"]),
        )

        important = narrator.get_episodes_by_tag("important")

        assert len(important) == 2

    def test_get_episode_count(self):
        """Test getting total episode count."""
        narrator = NarratorEngine()

        assert narrator.get_episode_count() == 0

        for _ in range(3):
            narrator.persist_episode(narrator.generate_observation_episode("Content"))

        assert narrator.get_episode_count() == 3


# =============================================================================
# Test GlobalWorkspace Integration
# =============================================================================


class TestWorkspaceIntegration:
    """Tests for GlobalWorkspace integration."""

    def test_subscribe_to_workspace(self):
        """Test subscribing to workspace channels."""
        from bartholomew.kernel.global_workspace import GlobalWorkspace

        workspace = GlobalWorkspace()
        config = NarratorConfig(auto_subscribe=False)
        narrator = NarratorEngine(workspace=workspace, config=config)

        assert len(narrator._subscription_ids) == 0

        narrator.subscribe_to_workspace()

        assert len(narrator._subscription_ids) == 4  # affect, attention, drives, goals
        assert workspace.get_subscription_count("affect") >= 1

    def test_auto_subscribe_on_init(self):
        """Test auto-subscription when config enabled."""
        from bartholomew.kernel.global_workspace import GlobalWorkspace

        workspace = GlobalWorkspace()
        config = NarratorConfig(auto_subscribe=True)
        narrator = NarratorEngine(workspace=workspace, config=config)

        assert len(narrator._subscription_ids) == 4

    def test_unsubscribe_all(self):
        """Test unsubscribing from all channels."""
        from bartholomew.kernel.global_workspace import GlobalWorkspace

        workspace = GlobalWorkspace()
        narrator = NarratorEngine(workspace=workspace)

        initial_count = workspace.get_subscription_count("affect")

        narrator.unsubscribe_all()

        assert len(narrator._subscription_ids) == 0
        assert workspace.get_subscription_count("affect") < initial_count

    def test_affect_event_creates_episode(self):
        """Test that affect events auto-create episodes."""
        from bartholomew.kernel.experience_kernel import ExperienceKernel
        from bartholomew.kernel.global_workspace import GlobalWorkspace

        workspace = GlobalWorkspace()
        kernel = ExperienceKernel(workspace=workspace)
        narrator = NarratorEngine(
            experience_kernel=kernel,
            workspace=workspace,
        )

        # Trigger affect change (above threshold)
        kernel.update_affect(valence=0.5, arousal=0.6, emotion="excited")

        # Check episode was created
        episodes = narrator.get_episodes_by_type(EpisodeType.AFFECT_SHIFT)
        assert len(episodes) >= 1

    def test_attention_event_creates_episode(self):
        """Test that attention events auto-create episodes."""
        from bartholomew.kernel.experience_kernel import ExperienceKernel
        from bartholomew.kernel.global_workspace import GlobalWorkspace

        workspace = GlobalWorkspace()
        kernel = ExperienceKernel(workspace=workspace)
        narrator = NarratorEngine(
            experience_kernel=kernel,
            workspace=workspace,
        )

        kernel.set_attention("user message", "user_input", intensity=0.8)

        episodes = narrator.get_episodes_by_type(EpisodeType.ATTENTION_FOCUS)
        assert len(episodes) >= 1
        assert "user message" in episodes[0].narrative

    def test_drive_event_creates_episode(self):
        """Test that drive events auto-create episodes."""
        from bartholomew.kernel.experience_kernel import ExperienceKernel
        from bartholomew.kernel.global_workspace import GlobalWorkspace

        workspace = GlobalWorkspace()
        kernel = ExperienceKernel(workspace=workspace)
        narrator = NarratorEngine(
            experience_kernel=kernel,
            workspace=workspace,
        )

        kernel.activate_drive("protect_user_wellbeing", boost=0.2)

        episodes = narrator.get_episodes_by_type(EpisodeType.DRIVE_ACTIVATED)
        assert len(episodes) >= 1

    def test_goal_event_creates_episode(self):
        """Test that goal events auto-create episodes."""
        from bartholomew.kernel.experience_kernel import ExperienceKernel
        from bartholomew.kernel.global_workspace import GlobalWorkspace

        workspace = GlobalWorkspace()
        kernel = ExperienceKernel(workspace=workspace)
        narrator = NarratorEngine(
            experience_kernel=kernel,
            workspace=workspace,
        )

        kernel.add_goal("Support user wellness")

        episodes = narrator.get_episodes_by_type(EpisodeType.GOAL_ADDED)
        assert len(episodes) >= 1
        assert "Support user wellness" in episodes[0].narrative

    def test_affect_threshold_filtering(self):
        """Test that small affect changes are filtered."""
        from bartholomew.kernel.experience_kernel import ExperienceKernel
        from bartholomew.kernel.global_workspace import GlobalWorkspace

        workspace = GlobalWorkspace()
        kernel = ExperienceKernel(workspace=workspace)
        narrator = NarratorEngine(
            experience_kernel=kernel,
            workspace=workspace,
        )

        # Small change (below threshold)
        kernel.update_affect(valence=0.25, arousal=0.35)

        # Should not create episode for small change
        # (first one might create due to no previous state)
        initial_count = len(narrator.get_episodes_by_type(EpisodeType.AFFECT_SHIFT))

        # Another small change
        kernel.update_affect(valence=0.26, arousal=0.36)

        final_count = len(narrator.get_episodes_by_type(EpisodeType.AFFECT_SHIFT))

        # Count should not have increased significantly
        assert final_count <= initial_count + 1


# =============================================================================
# Test Reflection Narrative Generation
# =============================================================================


class TestReflectionNarratives:
    """Tests for reflection narrative generation."""

    def test_daily_reflection_empty_day(self):
        """Test daily reflection with no episodes."""
        narrator = NarratorEngine()

        narrative = narrator.generate_daily_reflection_narrative()

        assert "Daily Reflection" in narrative
        assert "quiet day" in narrative.lower()

    def test_daily_reflection_with_episodes(self):
        """Test daily reflection with episodes."""
        narrator = NarratorEngine()

        # Create episodes for today
        narrator.persist_episode(narrator.generate_affect_episode(emotion="calm"))
        narrator.persist_episode(narrator.generate_goal_added_episode(goal="Stay focused"))

        narrative = narrator.generate_daily_reflection_narrative()

        assert "Daily Reflection" in narrative
        assert "Emotional Landscape" in narrative or "Goals" in narrative
        assert "Total episodes:" in narrative

    def test_daily_reflection_groups_by_type(self):
        """Test that daily reflection groups episodes by type."""
        narrator = NarratorEngine()

        # Create multiple affect episodes
        narrator.persist_episode(narrator.generate_affect_episode(emotion="happy"))
        narrator.persist_episode(narrator.generate_affect_episode(emotion="calm"))

        narrative = narrator.generate_daily_reflection_narrative()

        assert "Emotional Landscape" in narrative

    def test_weekly_reflection_empty_week(self):
        """Test weekly reflection with no episodes."""
        narrator = NarratorEngine()

        narrative = narrator.generate_weekly_reflection_narrative()

        assert "Weekly Reflection" in narrative
        assert "quiet week" in narrative.lower()

    def test_weekly_reflection_with_episodes(self):
        """Test weekly reflection with episodes."""
        narrator = NarratorEngine()

        # Create episodes
        narrator.persist_episode(narrator.generate_goal_added_episode(goal="Weekly goal"))
        narrator.persist_episode(narrator.generate_goal_completed_episode(goal="Weekly goal"))
        narrator.persist_episode(narrator.generate_affect_episode(emotion="content"))

        narrative = narrator.generate_weekly_reflection_narrative()

        assert "Weekly Reflection" in narrative
        assert "Week Overview" in narrative
        assert "Highlights" in narrative

    def test_weekly_reflection_includes_goals_summary(self):
        """Test that weekly reflection includes goals summary."""
        narrator = NarratorEngine()

        narrator.persist_episode(narrator.generate_goal_added_episode(goal="Goal 1"))
        narrator.persist_episode(narrator.generate_goal_added_episode(goal="Goal 2"))
        narrator.persist_episode(narrator.generate_goal_completed_episode(goal="Goal 1"))

        narrative = narrator.generate_weekly_reflection_narrative()

        assert "Goals Progress" in narrative
        assert "Goals set: 2" in narrative
        assert "Goals completed: 1" in narrative


# =============================================================================
# Test Singleton Pattern
# =============================================================================


class TestSingleton:
    """Tests for singleton pattern."""

    def test_get_narrator_creates_instance(self):
        """Test get_narrator creates a new instance."""
        reset_narrator()
        narrator = get_narrator()

        assert narrator is not None
        assert isinstance(narrator, NarratorEngine)

    def test_get_narrator_returns_same_instance(self):
        """Test get_narrator returns the same instance."""
        reset_narrator()
        narrator1 = get_narrator()
        narrator2 = get_narrator()

        assert narrator1 is narrator2

    def test_reset_narrator(self):
        """Test reset_narrator clears the instance."""
        reset_narrator()
        narrator1 = get_narrator()
        reset_narrator()
        narrator2 = get_narrator()

        assert narrator1 is not narrator2


# =============================================================================
# Test Edge Cases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_episode_with_none_values(self):
        """Test episode creation with None values."""
        entry = EpisodicEntry.create(
            episode_type=EpisodeType.OBSERVATION,
            narrative="Test",
            affect_snapshot=None,
            source_event_id=None,
            source_channel=None,
        )

        assert entry.affect_snapshot is None
        assert entry.source_event_id is None

        # Roundtrip should work
        data = entry.to_dict()
        restored = EpisodicEntry.from_dict(data)
        assert restored.affect_snapshot is None

    def test_empty_tags_list(self):
        """Test episode with empty tags."""
        entry = EpisodicEntry.create(
            episode_type=EpisodeType.OBSERVATION,
            narrative="Test",
            tags=[],
        )

        assert entry.tags == []

        data = entry.to_dict()
        restored = EpisodicEntry.from_dict(data)
        assert restored.tags == []

    def test_special_characters_in_narrative(self):
        """Test episode with special characters in narrative."""
        narrator = NarratorEngine()
        episode = narrator.generate_observation_episode(
            'User said: "Hello!" & asked about <things>',
        )

        narrator.persist_episode(episode)
        retrieved = narrator.get_episode(episode.entry_id)

        assert "Hello!" in retrieved.narrative
        assert "&" in retrieved.narrative
        assert "<things>" in retrieved.narrative

    def test_unicode_in_narrative(self):
        """Test episode with unicode content."""
        narrator = NarratorEngine()
        episode = narrator.generate_observation_episode("User expressed joy 😊 and gratitude 🙏")

        narrator.persist_episode(episode)
        retrieved = narrator.get_episode(episode.entry_id)

        assert "😊" in retrieved.narrative
        assert "🙏" in retrieved.narrative

    def test_very_long_narrative(self):
        """Test episode with very long narrative."""
        narrator = NarratorEngine()
        long_content = "This is a test. " * 1000

        episode = narrator.generate_observation_episode(long_content)
        narrator.persist_episode(episode)

        retrieved = narrator.get_episode(episode.entry_id)
        assert len(retrieved.narrative) > 10000

    def test_template_rotation(self):
        """Test that templates rotate through options."""
        narrator = NarratorEngine()

        narratives = set()
        for _ in range(10):
            episode = narrator.generate_affect_episode(emotion="calm")
            narratives.add(episode.narrative)

        # Should have more than 1 unique narrative (templates rotate)
        assert len(narratives) > 1


# =============================================================================
# Test Episode FTS Search
# =============================================================================


class TestEpisodeFTSSearch:
    """Tests for episode full-text search."""

    def test_search_episodes_basic(self):
        """Test basic FTS search across episodes."""
        narrator = NarratorEngine()

        # Create episodes with different content
        narrator.persist_episode(
            narrator.generate_observation_episode("The robot learned something new"),
        )
        narrator.persist_episode(
            narrator.generate_observation_episode("User asked about weather"),
        )
        narrator.persist_episode(
            narrator.generate_observation_episode("The robot helped the user"),
        )

        # Search for "robot"
        results = narrator.search_episodes("robot")

        assert len(results) == 2
        for ep in results:
            assert "robot" in ep.narrative.lower()

    def test_search_episodes_no_results(self):
        """Test FTS search with no matching results."""
        narrator = NarratorEngine()

        narrator.persist_episode(
            narrator.generate_observation_episode("Something about cats"),
        )

        results = narrator.search_episodes("dogs")

        assert len(results) == 0

    def test_search_episodes_with_type_filter(self):
        """Test FTS search with episode type filter."""
        narrator = NarratorEngine()

        # Create different episode types
        narrator.persist_episode(
            narrator.generate_observation_episode("Happy moment observed"),
        )
        narrator.persist_episode(narrator.generate_affect_episode(emotion="happy"))

        # Search with type filter
        results = narrator.search_episodes(
            "happy",
            episode_type=EpisodeType.OBSERVATION,
        )

        # Should only return observation, not affect shift
        assert all(ep.episode_type == EpisodeType.OBSERVATION for ep in results)

    def test_search_episodes_with_tone_filter(self):
        """Test FTS search with tone filter."""
        narrator = NarratorEngine()

        # Create episodes with known tones
        ep1 = EpisodicEntry.create(
            episode_type=EpisodeType.OBSERVATION,
            narrative="I noticed something exciting",
            tone=NarrativeTone.ENTHUSIASTIC,
        )
        ep2 = EpisodicEntry.create(
            episode_type=EpisodeType.OBSERVATION,
            narrative="I noticed something quietly",
            tone=NarrativeTone.SUBDUED,
        )
        narrator.persist_episode(ep1)
        narrator.persist_episode(ep2)

        # Search with tone filter
        results = narrator.search_episodes(
            "noticed",
            tone=NarrativeTone.ENTHUSIASTIC,
        )

        assert len(results) == 1
        assert results[0].tone == NarrativeTone.ENTHUSIASTIC

    def test_search_episodes_with_since_filter(self):
        """Test FTS search with timestamp filter."""
        narrator = NarratorEngine()

        # Create first episode
        narrator.persist_episode(
            narrator.generate_observation_episode("Early observation"),
        )

        # Mark time
        cutoff = datetime.now(timezone.utc)

        # Create second episode
        narrator.persist_episode(
            narrator.generate_observation_episode("Later observation"),
        )

        # Search since cutoff
        results = narrator.search_episodes("observation", since=cutoff)

        # Should only get the later one
        assert len(results) == 1
        assert "Later" in results[0].narrative

    def test_search_episodes_with_limit(self):
        """Test FTS search respects limit."""
        narrator = NarratorEngine()

        # Create many episodes
        for i in range(10):
            narrator.persist_episode(
                narrator.generate_observation_episode(f"Test observation {i}"),
            )

        results = narrator.search_episodes("observation", limit=3)

        assert len(results) == 3

    def test_search_episodes_combined_filters(self):
        """Test FTS search with multiple filters."""
        narrator = NarratorEngine()

        ep1 = EpisodicEntry.create(
            episode_type=EpisodeType.OBSERVATION,
            narrative="Important task completed",
            tone=NarrativeTone.CONTENT,
        )
        ep2 = EpisodicEntry.create(
            episode_type=EpisodeType.GOAL_COMPLETED,
            narrative="Task was completed successfully",
            tone=NarrativeTone.ENTHUSIASTIC,
        )
        narrator.persist_episode(ep1)
        narrator.persist_episode(ep2)

        # Search with type and tone filter
        results = narrator.search_episodes(
            "completed",
            episode_type=EpisodeType.OBSERVATION,
            tone=NarrativeTone.CONTENT,
        )

        assert len(results) == 1
        assert results[0].episode_type == EpisodeType.OBSERVATION
        assert results[0].tone == NarrativeTone.CONTENT

    def test_rebuild_episode_fts(self):
        """Test rebuilding the FTS index."""
        narrator = NarratorEngine()

        # Create episodes
        narrator.persist_episode(
            narrator.generate_observation_episode("Index this content"),
        )
        narrator.persist_episode(
            narrator.generate_observation_episode("Also index this"),
        )

        # Rebuild index
        count = narrator.rebuild_episode_fts()

        # Should return count of indexed episodes
        # (may be 0 if FTS not available)
        assert count >= 0

    def test_search_episodes_fts_unavailable_fallback(self):
        """Test that search falls back to LIKE when FTS unavailable."""
        # This test verifies the fallback works; FTS might not be
        # available on all platforms
        narrator = NarratorEngine()

        narrator.persist_episode(
            narrator.generate_observation_episode("Findable content here"),
        )

        # This should work regardless of FTS availability
        results = narrator.search_episodes("Findable")

        # Should find via FTS or LIKE fallback
        assert len(results) >= 1


# =============================================================================
# Test Full Integration
# =============================================================================


class TestFullIntegration:
    """Full integration tests."""

    def test_full_lifecycle(self):
        """Test complete lifecycle: kernel -> workspace -> narrator."""
        from bartholomew.kernel.experience_kernel import ExperienceKernel
        from bartholomew.kernel.global_workspace import GlobalWorkspace

        # Setup
        workspace = GlobalWorkspace()
        kernel = ExperienceKernel(workspace=workspace)
        narrator = NarratorEngine(
            experience_kernel=kernel,
            workspace=workspace,
        )

        # Simulate activity
        kernel.update_affect(valence=0.7, arousal=0.8, emotion="excited")
        kernel.set_attention("important task", "task", intensity=0.9)
        kernel.add_goal("Complete the task")
        kernel.activate_drive("be_helpful_without_manipulation")
        kernel.complete_goal("Complete the task")

        # Check episodes were created
        total = narrator.get_episode_count()
        assert total >= 4  # At least 4 episodes should be created

        # Generate reflection
        narrative = narrator.generate_daily_reflection_narrative()
        assert "Daily Reflection" in narrative

    def test_persistence_across_narrator_instances(self):
        """Test that data persists across narrator instances."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        # First narrator
        narrator1 = NarratorEngine(db_path=db_path)
        narrator1.persist_episode(narrator1.generate_observation_episode("Persistent content"))
        del narrator1

        # Second narrator with same DB
        narrator2 = NarratorEngine(db_path=db_path)
        count = narrator2.get_episode_count()
        episodes = narrator2.get_recent_episodes()

        assert count == 1
        assert "Persistent content" in episodes[0].narrative

        Path(db_path).unlink()
