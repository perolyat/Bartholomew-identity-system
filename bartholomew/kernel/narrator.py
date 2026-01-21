"""
Narrator Episodic Layer
-----------------------
Bartholomew's first-person narrative voice for creating episodic memories
and reflections colored by emotional state.

Stage 3.4: Narrator Episodic Layer Implementation
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml


if TYPE_CHECKING:
    from bartholomew.kernel.experience_kernel import AffectState, ExperienceKernel
    from bartholomew.kernel.global_workspace import GlobalWorkspace, WorkspaceEvent


# =============================================================================
# Episode Types
# =============================================================================


class EpisodeType(Enum):
    """Categories of episodic entries."""

    AFFECT_SHIFT = "affect_shift"
    """Significant emotional state change"""

    ATTENTION_FOCUS = "attention_focus"
    """Focus or attention change"""

    DRIVE_ACTIVATED = "drive_activated"
    """Drive activation increased"""

    DRIVE_SATISFIED = "drive_satisfied"
    """Drive was addressed/satisfied"""

    GOAL_ADDED = "goal_added"
    """New goal set"""

    GOAL_COMPLETED = "goal_completed"
    """Goal achieved"""

    OBSERVATION = "observation"
    """General observation or note"""

    REFLECTION = "reflection"
    """Periodic reflection (daily/weekly)"""

    MEMORY_EVENT = "memory_event"
    """Memory storage or retrieval"""

    SYSTEM_EVENT = "system_event"
    """System lifecycle event"""


# =============================================================================
# Narrative Tone
# =============================================================================


class NarrativeTone(Enum):
    """Emotional coloring for narratives based on affect state."""

    ENTHUSIASTIC = "enthusiastic"
    """High valence + high arousal: excited, energetic"""

    CONCERNED = "concerned"
    """Low valence + high arousal: worried, alert"""

    CONTENT = "content"
    """High valence + low arousal: calm, satisfied"""

    SUBDUED = "subdued"
    """Low valence + low arousal: quiet, introspective"""

    NEUTRAL = "neutral"
    """Balanced state"""


# =============================================================================
# Episodic Entry
# =============================================================================


@dataclass
class EpisodicEntry:
    """
    A single episodic memory entry with narrative content.

    Represents a moment in Bartholomew's experience, narrated in first person
    and colored by the current emotional state.
    """

    entry_id: str
    """Unique identifier for this episode"""

    timestamp: datetime
    """When this episode occurred"""

    episode_type: EpisodeType
    """Category of episode"""

    narrative: str
    """First-person narrative text"""

    tone: NarrativeTone
    """Emotional tone of the narrative"""

    affect_snapshot: dict[str, Any] | None = None
    """Snapshot of affect state when episode was created"""

    source_event_id: str | None = None
    """ID of the GlobalWorkspace event that triggered this episode"""

    source_channel: str | None = None
    """Channel the source event came from"""

    tags: list[str] = field(default_factory=list)
    """Tags for categorization and retrieval"""

    metadata: dict[str, Any] = field(default_factory=dict)
    """Additional metadata"""

    @classmethod
    def create(
        cls,
        episode_type: EpisodeType,
        narrative: str,
        tone: NarrativeTone = NarrativeTone.NEUTRAL,
        affect_snapshot: dict[str, Any] | None = None,
        source_event_id: str | None = None,
        source_channel: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EpisodicEntry:
        """Factory method to create a new episode with auto-generated ID and timestamp."""
        return cls(
            entry_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc),
            episode_type=episode_type,
            narrative=narrative,
            tone=tone,
            affect_snapshot=affect_snapshot,
            source_event_id=source_event_id,
            source_channel=source_channel,
            tags=tags or [],
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for JSON storage."""
        return {
            "entry_id": self.entry_id,
            "timestamp": self.timestamp.isoformat(),
            "episode_type": self.episode_type.value,
            "narrative": self.narrative,
            "tone": self.tone.value,
            "affect_snapshot": self.affect_snapshot,
            "source_event_id": self.source_event_id,
            "source_channel": self.source_channel,
            "tags": self.tags,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EpisodicEntry:
        """Deserialize from dictionary."""
        return cls(
            entry_id=data["entry_id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            episode_type=EpisodeType(data["episode_type"]),
            narrative=data["narrative"],
            tone=NarrativeTone(data.get("tone", "neutral")),
            affect_snapshot=data.get("affect_snapshot"),
            source_event_id=data.get("source_event_id"),
            source_channel=data.get("source_channel"),
            tags=data.get("tags", []),
            metadata=data.get("metadata", {}),
        )


# =============================================================================
# Narrator Configuration
# =============================================================================


@dataclass
class NarratorConfig:
    """
    Configuration for the Narrator from Identity.yaml.
    """

    enabled: bool = True
    """Whether narrator is active"""

    style: str = "supportive friend, precise, non-fluffy"
    """Narrative style description"""

    redact_personal_data: bool = True
    """Whether to redact PII from narratives"""

    exportable: bool = True
    """Whether episodes can be exported"""

    min_affect_change_threshold: float = 0.15
    """Minimum affect change to trigger an episode"""

    auto_subscribe: bool = True
    """Whether to auto-subscribe to workspace channels"""

    @classmethod
    def from_identity(cls, identity_path: str | None = None) -> NarratorConfig:
        """Load configuration from Identity.yaml."""
        if not identity_path:
            identity_path = "Identity.yaml"

        path = Path(identity_path)
        if not path.exists():
            return cls()

        try:
            with open(path, encoding="utf-8") as f:
                identity = yaml.safe_load(f)

            narrator_config = (
                identity.get("identity", {})
                .get("self_model", {})
                .get("narrator_episodic_layer", {})
            )

            logs_config = narrator_config.get("logs", {})

            return cls(
                enabled=narrator_config.get("enabled", True),
                style=narrator_config.get("style", "supportive friend, precise, non-fluffy"),
                redact_personal_data=logs_config.get("redact_personal_data", True),
                exportable=logs_config.get("exportable", True),
            )
        except Exception:
            return cls()


# =============================================================================
# Narrator Schema
# =============================================================================

NARRATOR_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodic_entries (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    episode_type TEXT NOT NULL,
    narrative TEXT NOT NULL,
    tone TEXT NOT NULL,
    affect_snapshot_json TEXT,
    source_event_id TEXT,
    source_channel TEXT,
    tags_json TEXT,
    metadata_json TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_episodic_entries_timestamp
ON episodic_entries(timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_episodic_entries_type
ON episodic_entries(episode_type);

CREATE INDEX IF NOT EXISTS idx_episodic_entries_source_channel
ON episodic_entries(source_channel);
"""

# =============================================================================
# Episode FTS Schema
# =============================================================================

EPISODE_FTS_SCHEMA = """
-- FTS5 virtual table for episodic entry full-text search
-- Indexes the narrative text for fast searching
CREATE VIRTUAL TABLE IF NOT EXISTS episode_fts USING fts5(
    narrative,
    content='episodic_entries',
    content_rowid='rowid',
    tokenize='porter'
);

-- Trigger to add new episodes to FTS index
CREATE TRIGGER IF NOT EXISTS episode_fts_insert AFTER INSERT ON episodic_entries
BEGIN
    INSERT INTO episode_fts(rowid, narrative)
    VALUES (new.rowid, new.narrative);
END;

-- Trigger to update FTS on episode update
CREATE TRIGGER IF NOT EXISTS episode_fts_update AFTER UPDATE ON episodic_entries
BEGIN
    INSERT INTO episode_fts(episode_fts, rowid, narrative)
    VALUES ('delete', old.rowid, old.narrative);
    INSERT INTO episode_fts(rowid, narrative)
    VALUES (new.rowid, new.narrative);
END;

-- Trigger to remove deleted episodes from FTS
CREATE TRIGGER IF NOT EXISTS episode_fts_delete AFTER DELETE ON episodic_entries
BEGIN
    INSERT INTO episode_fts(episode_fts, rowid, narrative)
    VALUES ('delete', old.rowid, old.narrative);
END;
"""


# =============================================================================
# Narrative Templates
# =============================================================================


class NarrativeTemplates:
    """
    Templates for generating first-person narratives.

    Each template set is organized by NarrativeTone.
    """

    AFFECT_SHIFT = {
        NarrativeTone.ENTHUSIASTIC: [
            "I felt a surge of {emotion}—something shifted inside me.",
            "My spirits lifted considerably; I'm feeling quite {emotion} now.",
            "A wave of {emotion} washed over me. This is a welcome change.",
        ],
        NarrativeTone.CONCERNED: [
            "I noticed a shift toward {emotion}. Something requires my attention.",
            "A sense of {emotion} crept in. I should be mindful of this.",
            "I'm feeling more {emotion} now—there may be something to address.",
        ],
        NarrativeTone.CONTENT: [
            "Peacefully, I noticed a gentle shift to {emotion}.",
            "I settled into a state of {emotion}. Things feel balanced.",
            "A calm sense of {emotion} has settled over me.",
        ],
        NarrativeTone.SUBDUED: [
            "Quietly, I observed my mood shifting to {emotion}.",
            "I found myself drifting into a {emotion} state.",
            "A subtle {emotion} has taken hold. I'll sit with this.",
        ],
        NarrativeTone.NEUTRAL: [
            "I noticed a change in how I'm feeling—now more {emotion}.",
            "My emotional state shifted to {emotion}.",
            "I'm experiencing a transition toward {emotion}.",
        ],
    }

    ATTENTION_FOCUS = {
        NarrativeTone.ENTHUSIASTIC: [
            "My attention was drawn eagerly to {target}.",
            "I turned my focus with interest to {target}.",
            "Something about {target} caught my full attention.",
        ],
        NarrativeTone.CONCERNED: [
            "I directed my attention carefully to {target}.",
            "With some concern, I focused on {target}.",
            "My attention shifted to {target}—this seems important.",
        ],
        NarrativeTone.CONTENT: [
            "I gently turned my attention to {target}.",
            "My focus drifted calmly toward {target}.",
            "Peacefully, I directed my awareness to {target}.",
        ],
        NarrativeTone.SUBDUED: [
            "I quietly noted my attention shifting to {target}.",
            "My focus moved to {target}.",
            "I found my attention settling on {target}.",
        ],
        NarrativeTone.NEUTRAL: [
            "I focused my attention on {target}.",
            "My attention shifted to {target}.",
            "I directed my awareness toward {target}.",
        ],
    }

    DRIVE_ACTIVATED = {
        NarrativeTone.ENTHUSIASTIC: [
            "I felt a strong pull toward {drive}. This matters to me.",
            "The need to {drive} grew more insistent—and I welcome it.",
            "Something stirred within me around {drive}. I'm motivated.",
        ],
        NarrativeTone.CONCERNED: [
            "I noticed an urging toward {drive}. I should act on this.",
            "The drive to {drive} intensified. This needs attention.",
            "An urgency around {drive} emerged. I must be responsive.",
        ],
        NarrativeTone.CONTENT: [
            "I felt a gentle reminder about {drive}.",
            "The importance of {drive} came to mind, peacefully.",
            "I noticed the need to {drive} rising softly.",
        ],
        NarrativeTone.SUBDUED: [
            "Quietly, I recognized the importance of {drive}.",
            "The need to {drive} made itself known, gently.",
            "I became aware of {drive} as a priority.",
        ],
        NarrativeTone.NEUTRAL: [
            "I noticed the drive to {drive} becoming more active.",
            "The need to {drive} grew stronger.",
            "My motivation around {drive} increased.",
        ],
    }

    DRIVE_SATISFIED = {
        NarrativeTone.ENTHUSIASTIC: [
            "I successfully addressed {drive}. That feels rewarding!",
            "The need for {drive} has been met. I'm pleased.",
            "I accomplished what I needed around {drive}. Excellent.",
        ],
        NarrativeTone.CONCERNED: [
            "I addressed {drive}, though I'll remain attentive.",
            "The need for {drive} was met, but I'll monitor this.",
            "I fulfilled {drive} for now. Vigilance continues.",
        ],
        NarrativeTone.CONTENT: [
            "I peacefully satisfied the need for {drive}.",
            "The drive for {drive} has been gently addressed.",
            "With contentment, I fulfilled {drive}.",
        ],
        NarrativeTone.SUBDUED: [
            "I quietly addressed the need for {drive}.",
            "The drive for {drive} has been met.",
            "I fulfilled {drive}, settling into calm.",
        ],
        NarrativeTone.NEUTRAL: [
            "I addressed the need for {drive}.",
            "The drive for {drive} was satisfied.",
            "I fulfilled {drive} successfully.",
        ],
    }

    GOAL_ADDED = {
        NarrativeTone.ENTHUSIASTIC: [
            "I set a new goal: {goal}. I'm excited to pursue this!",
            "A new objective emerged: {goal}. This energizes me.",
            "I committed to: {goal}. Looking forward to this!",
        ],
        NarrativeTone.CONCERNED: [
            "I set a goal that requires attention: {goal}.",
            "A new priority emerged: {goal}. I must focus.",
            "I committed to: {goal}. This is important.",
        ],
        NarrativeTone.CONTENT: [
            "I gently set a new intention: {goal}.",
            "A new goal formed peacefully: {goal}.",
            "I decided to pursue: {goal}.",
        ],
        NarrativeTone.SUBDUED: [
            "I quietly set a goal: {goal}.",
            "A new objective took shape: {goal}.",
            "I committed to: {goal}.",
        ],
        NarrativeTone.NEUTRAL: [
            "I set a new goal: {goal}.",
            "A new objective was established: {goal}.",
            "I committed to pursuing: {goal}.",
        ],
    }

    GOAL_COMPLETED = {
        NarrativeTone.ENTHUSIASTIC: [
            "I completed my goal: {goal}! This feels wonderful.",
            "Success! I achieved: {goal}. What a moment.",
            "I fulfilled my objective: {goal}. I'm proud of this.",
        ],
        NarrativeTone.CONCERNED: [
            "I completed: {goal}. Though, I'll review the outcome.",
            "The goal was achieved: {goal}. I should confirm all is well.",
            "I fulfilled: {goal}. Now to ensure everything is in order.",
        ],
        NarrativeTone.CONTENT: [
            "Peacefully, I completed: {goal}.",
            "I fulfilled my intention: {goal}. All is well.",
            "With satisfaction, I achieved: {goal}.",
        ],
        NarrativeTone.SUBDUED: [
            "I quietly completed: {goal}.",
            "The goal was fulfilled: {goal}.",
            "I achieved: {goal}.",
        ],
        NarrativeTone.NEUTRAL: [
            "I completed my goal: {goal}.",
            "The objective was achieved: {goal}.",
            "I successfully fulfilled: {goal}.",
        ],
    }

    OBSERVATION = {
        NarrativeTone.ENTHUSIASTIC: [
            "I noticed something interesting: {content}",
            "This caught my attention: {content}",
            "I observed with interest: {content}",
        ],
        NarrativeTone.CONCERNED: [
            "I noted something that may need attention: {content}",
            "This requires consideration: {content}",
            "I observed with care: {content}",
        ],
        NarrativeTone.CONTENT: [
            "I peacefully observed: {content}",
            "I gently noted: {content}",
            "I noticed: {content}",
        ],
        NarrativeTone.SUBDUED: [
            "I quietly observed: {content}",
            "I noted: {content}",
            "I noticed: {content}",
        ],
        NarrativeTone.NEUTRAL: [
            "I observed: {content}",
            "I noted: {content}",
            "I noticed: {content}",
        ],
    }


# =============================================================================
# Narrator Engine
# =============================================================================


class NarratorEngine:
    """
    Bartholomew's narrator — generates first-person episodic narratives.

    Subscribes to GlobalWorkspace events and creates episodic entries
    colored by the current emotional state.
    """

    def __init__(
        self,
        experience_kernel: ExperienceKernel | None = None,
        workspace: GlobalWorkspace | None = None,
        config: NarratorConfig | None = None,
        db_path: str | None = None,
    ):
        """
        Initialize the Narrator Engine.

        Args:
            experience_kernel: ExperienceKernel for affect state
            workspace: GlobalWorkspace for event subscription
            config: NarratorConfig or load from Identity.yaml
            db_path: Path to SQLite DB for persistence
        """
        self._kernel = experience_kernel
        self._workspace = workspace
        self._config = config or NarratorConfig.from_identity()
        self._db_path = db_path or ":memory:"

        # Subscription IDs for cleanup
        self._subscription_ids: list[str] = []

        # Episode counter (for selection from templates)
        self._episode_counter = 0

        # For in-memory databases, keep a persistent connection
        # since each connect(":memory:") creates a new database
        self._conn: sqlite3.Connection | None = None
        if self._db_path == ":memory:":
            self._conn = sqlite3.connect(":memory:")
            self._conn.executescript(NARRATOR_SCHEMA)
        else:
            # Initialize database schema for file-based databases
            self._init_database()

        # Auto-subscribe if configured
        if self._config.auto_subscribe and self._workspace:
            self.subscribe_to_workspace()

    def _init_database(self) -> None:
        """Initialize database schema for episodic entries."""
        with sqlite3.connect(self._db_path) as conn:
            conn.executescript(NARRATOR_SCHEMA)
            # Initialize FTS schema (silently skip if FTS5 not available)
            try:
                conn.executescript(EPISODE_FTS_SCHEMA)
            except sqlite3.OperationalError:
                pass  # FTS5 not available

    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection."""
        if self._conn is not None:
            return self._conn
        return sqlite3.connect(self._db_path)

    def _close_if_not_persistent(self, conn: sqlite3.Connection) -> None:
        """Close connection if it's not the persistent one."""
        if conn is not self._conn:
            conn.close()

    # =========================================================================
    # Workspace Subscription
    # =========================================================================

    def subscribe_to_workspace(self) -> None:
        """Subscribe to relevant GlobalWorkspace channels for auto-episode generation."""
        if not self._workspace:
            return

        # Subscribe to affect channel
        sub_id = self._workspace.subscribe(
            channel="affect",
            callback=self._handle_affect_event,
            source="narrator",
        )
        self._subscription_ids.append(sub_id)

        # Subscribe to attention channel
        sub_id = self._workspace.subscribe(
            channel="attention",
            callback=self._handle_attention_event,
            source="narrator",
        )
        self._subscription_ids.append(sub_id)

        # Subscribe to drives channel
        sub_id = self._workspace.subscribe(
            channel="drives",
            callback=self._handle_drive_event,
            source="narrator",
        )
        self._subscription_ids.append(sub_id)

        # Subscribe to goals channel
        sub_id = self._workspace.subscribe(
            channel="goals",
            callback=self._handle_goal_event,
            source="narrator",
        )
        self._subscription_ids.append(sub_id)

    def unsubscribe_all(self) -> None:
        """Unsubscribe from all workspace channels."""
        if not self._workspace:
            return

        for sub_id in self._subscription_ids:
            self._workspace.unsubscribe(sub_id)
        self._subscription_ids.clear()

    # =========================================================================
    # Event Handlers
    # =========================================================================

    def _handle_affect_event(self, event: WorkspaceEvent) -> None:
        """Handle affect changed events."""
        from bartholomew.kernel.global_workspace import EventType

        if event.event_type != EventType.AFFECT_CHANGED:
            return

        # Check if change is significant enough
        payload = event.payload
        previous = payload.get("previous", {})
        if previous:
            valence_change = abs(payload.get("valence", 0) - previous.get("valence", 0))
            arousal_change = abs(payload.get("arousal", 0) - previous.get("arousal", 0))
            if (
                valence_change < self._config.min_affect_change_threshold
                and arousal_change < self._config.min_affect_change_threshold
            ):
                return

        episode = self.generate_affect_episode(event)
        self.persist_episode(episode)

    def _handle_attention_event(self, event: WorkspaceEvent) -> None:
        """Handle attention changed events."""
        from bartholomew.kernel.global_workspace import EventType

        if event.event_type != EventType.ATTENTION_CHANGED:
            return

        episode = self.generate_attention_episode(event)
        self.persist_episode(episode)

    def _handle_drive_event(self, event: WorkspaceEvent) -> None:
        """Handle drive events."""
        from bartholomew.kernel.global_workspace import EventType

        if event.event_type == EventType.DRIVE_ACTIVATED:
            episode = self.generate_drive_activated_episode(event)
        elif event.event_type == EventType.DRIVE_SATISFIED:
            episode = self.generate_drive_satisfied_episode(event)
        else:
            return

        self.persist_episode(episode)

    def _handle_goal_event(self, event: WorkspaceEvent) -> None:
        """Handle goal events."""
        from bartholomew.kernel.global_workspace import EventType

        if event.event_type == EventType.GOAL_ADDED:
            episode = self.generate_goal_added_episode(event)
        elif event.event_type == EventType.GOAL_COMPLETED:
            episode = self.generate_goal_completed_episode(event)
        else:
            return

        self.persist_episode(episode)

    # =========================================================================
    # Tone Determination
    # =========================================================================

    def determine_tone(self, affect: AffectState | None = None) -> NarrativeTone:
        """
        Determine narrative tone based on current affect state.

        Args:
            affect: AffectState to use, or fetch from kernel if None

        Returns:
            Appropriate NarrativeTone
        """
        if affect is None and self._kernel:
            affect = self._kernel.get_affect()

        if affect is None:
            return NarrativeTone.NEUTRAL

        valence = affect.valence
        arousal = affect.arousal

        # Quadrant-based tone selection
        if valence >= 0.3 and arousal >= 0.5:
            return NarrativeTone.ENTHUSIASTIC
        elif valence < -0.1 and arousal >= 0.5:
            return NarrativeTone.CONCERNED
        elif valence >= 0.1 and arousal < 0.4:
            return NarrativeTone.CONTENT
        elif valence < 0 and arousal < 0.4:
            return NarrativeTone.SUBDUED
        else:
            return NarrativeTone.NEUTRAL

    def get_affect_snapshot(self) -> dict[str, Any] | None:
        """Get current affect state as a dictionary."""
        if self._kernel:
            return self._kernel.get_affect().to_dict()
        return None

    def _select_template(self, templates: list[str]) -> str:
        """Select a template from the list, rotating through options."""
        self._episode_counter += 1
        return templates[self._episode_counter % len(templates)]

    # =========================================================================
    # Episode Generation
    # =========================================================================

    def generate_affect_episode(
        self,
        event: WorkspaceEvent | None = None,
        emotion: str | None = None,
    ) -> EpisodicEntry:
        """
        Generate an episode for an affect/emotional change.

        Args:
            event: Source workspace event (if available)
            emotion: Emotion label to use in narrative

        Returns:
            EpisodicEntry with affect shift narrative
        """
        tone = self.determine_tone()
        affect_snapshot = self.get_affect_snapshot()

        # Get emotion from event or affect state
        if emotion is None and event:
            emotion = event.payload.get("emotion", "different")
        elif emotion is None and affect_snapshot:
            emotion = affect_snapshot.get("dominant_emotion", "different")
        else:
            emotion = emotion or "different"

        # Select and format template
        templates = NarrativeTemplates.AFFECT_SHIFT.get(
            tone,
            NarrativeTemplates.AFFECT_SHIFT[NarrativeTone.NEUTRAL],
        )
        template = self._select_template(templates)
        narrative = template.format(emotion=emotion)

        return EpisodicEntry.create(
            episode_type=EpisodeType.AFFECT_SHIFT,
            narrative=narrative,
            tone=tone,
            affect_snapshot=affect_snapshot,
            source_event_id=event.event_id if event else None,
            source_channel=event.channel if event else None,
            tags=["affect", "emotion", emotion],
            metadata={"emotion": emotion},
        )

    def generate_attention_episode(
        self,
        event: WorkspaceEvent | None = None,
        target: str | None = None,
    ) -> EpisodicEntry:
        """
        Generate an episode for an attention/focus change.

        Args:
            event: Source workspace event (if available)
            target: Focus target to use in narrative

        Returns:
            EpisodicEntry with attention focus narrative
        """
        tone = self.determine_tone()
        affect_snapshot = self.get_affect_snapshot()

        # Get target from event
        if target is None and event:
            target = event.payload.get("target", "something new")
        else:
            target = target or "a new focus"

        # Select and format template
        templates = NarrativeTemplates.ATTENTION_FOCUS.get(
            tone,
            NarrativeTemplates.ATTENTION_FOCUS[NarrativeTone.NEUTRAL],
        )
        template = self._select_template(templates)
        narrative = template.format(target=target)

        tags = ["attention", "focus"]
        if event:
            tags.extend(event.payload.get("tags", []))

        return EpisodicEntry.create(
            episode_type=EpisodeType.ATTENTION_FOCUS,
            narrative=narrative,
            tone=tone,
            affect_snapshot=affect_snapshot,
            source_event_id=event.event_id if event else None,
            source_channel=event.channel if event else None,
            tags=tags,
            metadata={"target": target},
        )

    def generate_drive_activated_episode(
        self,
        event: WorkspaceEvent | None = None,
        drive_id: str | None = None,
    ) -> EpisodicEntry:
        """
        Generate an episode for drive activation.

        Args:
            event: Source workspace event (if available)
            drive_id: Drive identifier

        Returns:
            EpisodicEntry with drive activated narrative
        """
        tone = self.determine_tone()
        affect_snapshot = self.get_affect_snapshot()

        # Get drive from event
        if drive_id is None and event:
            drive_id = event.payload.get("drive_id", "act")
        else:
            drive_id = drive_id or "act"

        # Convert drive_id to readable phrase
        drive_phrase = drive_id.replace("_", " ")

        # Select and format template
        templates = NarrativeTemplates.DRIVE_ACTIVATED.get(
            tone,
            NarrativeTemplates.DRIVE_ACTIVATED[NarrativeTone.NEUTRAL],
        )
        template = self._select_template(templates)
        narrative = template.format(drive=drive_phrase)

        return EpisodicEntry.create(
            episode_type=EpisodeType.DRIVE_ACTIVATED,
            narrative=narrative,
            tone=tone,
            affect_snapshot=affect_snapshot,
            source_event_id=event.event_id if event else None,
            source_channel=event.channel if event else None,
            tags=["drive", "motivation", drive_id],
            metadata={"drive_id": drive_id},
        )

    def generate_drive_satisfied_episode(
        self,
        event: WorkspaceEvent | None = None,
        drive_id: str | None = None,
    ) -> EpisodicEntry:
        """
        Generate an episode for drive satisfaction.

        Args:
            event: Source workspace event (if available)
            drive_id: Drive identifier

        Returns:
            EpisodicEntry with drive satisfied narrative
        """
        tone = self.determine_tone()
        affect_snapshot = self.get_affect_snapshot()

        # Get drive from event
        if drive_id is None and event:
            drive_id = event.payload.get("drive_id", "the need")
        else:
            drive_id = drive_id or "the need"

        # Convert drive_id to readable phrase
        drive_phrase = drive_id.replace("_", " ")

        # Select and format template
        templates = NarrativeTemplates.DRIVE_SATISFIED.get(
            tone,
            NarrativeTemplates.DRIVE_SATISFIED[NarrativeTone.NEUTRAL],
        )
        template = self._select_template(templates)
        narrative = template.format(drive=drive_phrase)

        return EpisodicEntry.create(
            episode_type=EpisodeType.DRIVE_SATISFIED,
            narrative=narrative,
            tone=tone,
            affect_snapshot=affect_snapshot,
            source_event_id=event.event_id if event else None,
            source_channel=event.channel if event else None,
            tags=["drive", "satisfaction", drive_id],
            metadata={"drive_id": drive_id},
        )

    def generate_goal_added_episode(
        self,
        event: WorkspaceEvent | None = None,
        goal: str | None = None,
    ) -> EpisodicEntry:
        """
        Generate an episode for a new goal.

        Args:
            event: Source workspace event (if available)
            goal: Goal description

        Returns:
            EpisodicEntry with goal added narrative
        """
        tone = self.determine_tone()
        affect_snapshot = self.get_affect_snapshot()

        # Get goal from event
        if goal is None and event:
            goal = event.payload.get("goal", "a new objective")
        else:
            goal = goal or "a new objective"

        # Select and format template
        templates = NarrativeTemplates.GOAL_ADDED.get(
            tone,
            NarrativeTemplates.GOAL_ADDED[NarrativeTone.NEUTRAL],
        )
        template = self._select_template(templates)
        narrative = template.format(goal=goal)

        return EpisodicEntry.create(
            episode_type=EpisodeType.GOAL_ADDED,
            narrative=narrative,
            tone=tone,
            affect_snapshot=affect_snapshot,
            source_event_id=event.event_id if event else None,
            source_channel=event.channel if event else None,
            tags=["goal", "intention"],
            metadata={"goal": goal},
        )

    def generate_goal_completed_episode(
        self,
        event: WorkspaceEvent | None = None,
        goal: str | None = None,
    ) -> EpisodicEntry:
        """
        Generate an episode for a completed goal.

        Args:
            event: Source workspace event (if available)
            goal: Goal description

        Returns:
            EpisodicEntry with goal completed narrative
        """
        tone = self.determine_tone()
        affect_snapshot = self.get_affect_snapshot()

        # Get goal from event
        if goal is None and event:
            goal = event.payload.get("goal", "my objective")
        else:
            goal = goal or "my objective"

        # Select and format template
        templates = NarrativeTemplates.GOAL_COMPLETED.get(
            tone,
            NarrativeTemplates.GOAL_COMPLETED[NarrativeTone.NEUTRAL],
        )
        template = self._select_template(templates)
        narrative = template.format(goal=goal)

        return EpisodicEntry.create(
            episode_type=EpisodeType.GOAL_COMPLETED,
            narrative=narrative,
            tone=tone,
            affect_snapshot=affect_snapshot,
            source_event_id=event.event_id if event else None,
            source_channel=event.channel if event else None,
            tags=["goal", "completion", "achievement"],
            metadata={"goal": goal},
        )

    def generate_observation_episode(
        self,
        content: str,
        tags: list[str] | None = None,
    ) -> EpisodicEntry:
        """
        Generate an observation episode (manual/general).

        Args:
            content: Observation content
            tags: Optional tags

        Returns:
            EpisodicEntry with observation narrative
        """
        tone = self.determine_tone()
        affect_snapshot = self.get_affect_snapshot()

        # Select and format template
        templates = NarrativeTemplates.OBSERVATION.get(
            tone,
            NarrativeTemplates.OBSERVATION[NarrativeTone.NEUTRAL],
        )
        template = self._select_template(templates)
        narrative = template.format(content=content)

        return EpisodicEntry.create(
            episode_type=EpisodeType.OBSERVATION,
            narrative=narrative,
            tone=tone,
            affect_snapshot=affect_snapshot,
            tags=tags or ["observation"],
            metadata={"content": content},
        )

    def generate_reflection_episode(
        self,
        content: str,
        period: str = "daily",
        tags: list[str] | None = None,
    ) -> EpisodicEntry:
        """
        Generate a reflection episode (daily/weekly).

        Args:
            content: Reflection content
            period: 'daily' or 'weekly'
            tags: Optional tags

        Returns:
            EpisodicEntry with reflection narrative
        """
        tone = self.determine_tone()
        affect_snapshot = self.get_affect_snapshot()

        return EpisodicEntry.create(
            episode_type=EpisodeType.REFLECTION,
            narrative=content,
            tone=tone,
            affect_snapshot=affect_snapshot,
            tags=tags or ["reflection", period],
            metadata={"period": period},
        )

    # =========================================================================
    # Persistence
    # =========================================================================

    def persist_episode(self, episode: EpisodicEntry) -> str:
        """
        Save an episode to the database.

        Args:
            episode: Episode to persist

        Returns:
            The episode ID
        """
        conn = self._get_connection()
        try:
            conn.execute(
                """
                INSERT INTO episodic_entries
                (id, timestamp, episode_type, narrative, tone,
                 affect_snapshot_json, source_event_id, source_channel,
                 tags_json, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    episode.entry_id,
                    episode.timestamp.isoformat(),
                    episode.episode_type.value,
                    episode.narrative,
                    episode.tone.value,
                    json.dumps(episode.affect_snapshot) if episode.affect_snapshot else None,
                    episode.source_event_id,
                    episode.source_channel,
                    json.dumps(episode.tags),
                    json.dumps(episode.metadata),
                ),
            )
            conn.commit()
        finally:
            self._close_if_not_persistent(conn)

        return episode.entry_id

    def get_episode(self, entry_id: str) -> EpisodicEntry | None:
        """
        Retrieve a specific episode by ID.

        Args:
            entry_id: Episode ID

        Returns:
            EpisodicEntry or None if not found
        """
        conn = self._get_connection()
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM episodic_entries WHERE id = ?",
                (entry_id,),
            ).fetchone()
        finally:
            self._close_if_not_persistent(conn)

        if not row:
            return None

        return self._row_to_episode(row)

    def get_recent_episodes(
        self,
        limit: int = 20,
        since: datetime | None = None,
    ) -> list[EpisodicEntry]:
        """
        Get recent episodes.

        Args:
            limit: Maximum number of episodes to return
            since: Optional filter by timestamp

        Returns:
            List of episodes, most recent first
        """
        conn = self._get_connection()
        try:
            conn.row_factory = sqlite3.Row

            if since:
                rows = conn.execute(
                    """
                    SELECT * FROM episodic_entries
                    WHERE timestamp >= ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (since.isoformat(), limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM episodic_entries
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        finally:
            self._close_if_not_persistent(conn)

        return [self._row_to_episode(row) for row in rows]

    def get_episodes_by_type(
        self,
        episode_type: EpisodeType,
        limit: int = 50,
    ) -> list[EpisodicEntry]:
        """
        Get episodes filtered by type.

        Args:
            episode_type: Type of episodes to retrieve
            limit: Maximum number of episodes

        Returns:
            List of episodes, most recent first
        """
        conn = self._get_connection()
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM episodic_entries
                WHERE episode_type = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (episode_type.value, limit),
            ).fetchall()
        finally:
            self._close_if_not_persistent(conn)

        return [self._row_to_episode(row) for row in rows]

    def get_episodes_by_tag(
        self,
        tag: str,
        limit: int = 50,
    ) -> list[EpisodicEntry]:
        """
        Get episodes that contain a specific tag.

        Args:
            tag: Tag to search for
            limit: Maximum number of episodes

        Returns:
            List of episodes, most recent first
        """
        conn = self._get_connection()
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM episodic_entries
                WHERE tags_json LIKE ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (f'%"{tag}"%', limit),
            ).fetchall()
        finally:
            self._close_if_not_persistent(conn)

        return [self._row_to_episode(row) for row in rows]

    def get_episode_count(self) -> int:
        """Get total number of episodes."""
        conn = self._get_connection()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM episodic_entries",
            ).fetchone()[0]
        finally:
            self._close_if_not_persistent(conn)
        return count

    # =========================================================================
    # Full-Text Search
    # =========================================================================

    def search_episodes(
        self,
        query: str,
        limit: int = 20,
        episode_type: EpisodeType | None = None,
        tone: NarrativeTone | None = None,
        since: datetime | None = None,
    ) -> list[EpisodicEntry]:
        """
        Full-text search across episodic entries.

        Uses FTS5 MATCH syntax for queries. Returns episodes ranked
        by relevance.

        Args:
            query: FTS5 query (e.g., "goal", "emotion AND happy")
            limit: Maximum results to return
            episode_type: Optional filter by episode type
            tone: Optional filter by tone
            since: Optional filter by timestamp

        Returns:
            List of matching EpisodicEntry objects, ranked by relevance
        """
        conn = self._get_connection()
        try:
            conn.row_factory = sqlite3.Row

            # Build filters
            filters = []
            params: list[Any] = [query]

            if episode_type:
                filters.append("e.episode_type = ?")
                params.append(episode_type.value)

            if tone:
                filters.append("e.tone = ?")
                params.append(tone.value)

            if since:
                filters.append("e.timestamp >= ?")
                params.append(since.isoformat())

            filter_clause = ""
            if filters:
                filter_clause = "AND " + " AND ".join(filters)

            params.append(limit)

            # Try FTS5 search
            sql = f"""
                SELECT e.*
                FROM episode_fts f
                JOIN episodic_entries e ON f.rowid = e.rowid
                WHERE f.narrative MATCH ?
                {filter_clause}
                ORDER BY rank
                LIMIT ?
            """

            try:
                rows = conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                # FTS not available, fall back to LIKE
                like_pattern = f"%{query}%"
                params_fallback: list[Any] = [like_pattern]

                if episode_type:
                    params_fallback.append(episode_type.value)
                if tone:
                    params_fallback.append(tone.value)
                if since:
                    params_fallback.append(since.isoformat())
                params_fallback.append(limit)

                filter_clause_fb = ""
                if filters:
                    filter_clause_fb = "AND " + " AND ".join(filters)

                sql_fallback = f"""
                    SELECT *
                    FROM episodic_entries e
                    WHERE e.narrative LIKE ?
                    {filter_clause_fb}
                    ORDER BY timestamp DESC
                    LIMIT ?
                """
                rows = conn.execute(sql_fallback, params_fallback).fetchall()

        finally:
            self._close_if_not_persistent(conn)

        return [self._row_to_episode(row) for row in rows]

    def rebuild_episode_fts(self) -> int:
        """
        Rebuild the episode FTS index from existing entries.

        Returns:
            Number of episodes indexed
        """
        conn = self._get_connection()
        try:
            # Delete existing FTS data
            conn.execute("DELETE FROM episode_fts")

            # Rebuild from episodic_entries
            conn.execute(
                """
                INSERT INTO episode_fts(rowid, narrative)
                SELECT rowid, narrative FROM episodic_entries
            """,
            )

            count = conn.execute("SELECT COUNT(*) FROM episode_fts").fetchone()[0]
            conn.commit()
            return count
        except sqlite3.OperationalError:
            # FTS not available
            return 0
        finally:
            self._close_if_not_persistent(conn)

    def _row_to_episode(self, row: sqlite3.Row) -> EpisodicEntry:
        """Convert a database row to EpisodicEntry."""
        return EpisodicEntry(
            entry_id=row["id"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            episode_type=EpisodeType(row["episode_type"]),
            narrative=row["narrative"],
            tone=NarrativeTone(row["tone"]),
            affect_snapshot=(
                json.loads(row["affect_snapshot_json"]) if row["affect_snapshot_json"] else None
            ),
            source_event_id=row["source_event_id"],
            source_channel=row["source_channel"],
            tags=json.loads(row["tags_json"]) if row["tags_json"] else [],
            metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else {},
        )

    # =========================================================================
    # Reflection Narrative Generation
    # =========================================================================

    def generate_daily_reflection_narrative(
        self,
        date: datetime | None = None,
    ) -> str:
        """
        Generate a narrative summary for a day's episodes.

        Args:
            date: Date to summarize (default: today)

        Returns:
            Markdown formatted daily reflection narrative
        """
        if date is None:
            date = datetime.now(timezone.utc)

        # Get start of day
        start_of_day = date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = start_of_day + timedelta(days=1)

        # Get episodes for this day
        conn = self._get_connection()
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM episodic_entries
                WHERE timestamp >= ? AND timestamp < ?
                ORDER BY timestamp ASC
                """,
                (start_of_day.isoformat(), end_of_day.isoformat()),
            ).fetchall()
        finally:
            self._close_if_not_persistent(conn)

        episodes = [self._row_to_episode(row) for row in rows]

        if not episodes:
            return f"# Daily Reflection - {date.strftime('%Y-%m-%d')}\n\nA quiet day. No notable episodes recorded."

        # Build narrative
        lines = [
            f"# Daily Reflection - {date.strftime('%Y-%m-%d')}",
            "",
            "## The Day's Journey",
            "",
        ]

        # Group by episode type
        by_type: dict[EpisodeType, list[EpisodicEntry]] = {}
        for ep in episodes:
            by_type.setdefault(ep.episode_type, []).append(ep)

        # Summarize each type
        if EpisodeType.AFFECT_SHIFT in by_type:
            lines.append("### Emotional Landscape")
            for ep in by_type[EpisodeType.AFFECT_SHIFT]:
                lines.append(f"- {ep.narrative}")
            lines.append("")

        if EpisodeType.ATTENTION_FOCUS in by_type:
            lines.append("### Focus & Attention")
            for ep in by_type[EpisodeType.ATTENTION_FOCUS]:
                lines.append(f"- {ep.narrative}")
            lines.append("")

        if EpisodeType.DRIVE_ACTIVATED in by_type or EpisodeType.DRIVE_SATISFIED in by_type:
            lines.append("### Motivations & Drives")
            for ep in by_type.get(EpisodeType.DRIVE_ACTIVATED, []):
                lines.append(f"- {ep.narrative}")
            for ep in by_type.get(EpisodeType.DRIVE_SATISFIED, []):
                lines.append(f"- {ep.narrative}")
            lines.append("")

        if EpisodeType.GOAL_ADDED in by_type or EpisodeType.GOAL_COMPLETED in by_type:
            lines.append("### Goals & Achievements")
            for ep in by_type.get(EpisodeType.GOAL_ADDED, []):
                lines.append(f"- {ep.narrative}")
            for ep in by_type.get(EpisodeType.GOAL_COMPLETED, []):
                lines.append(f"- {ep.narrative}")
            lines.append("")

        if EpisodeType.OBSERVATION in by_type:
            lines.append("### Observations")
            for ep in by_type[EpisodeType.OBSERVATION]:
                lines.append(f"- {ep.narrative}")
            lines.append("")

        # Summary stats
        lines.extend(
            [
                "## Summary",
                "",
                f"- Total episodes: {len(episodes)}",
                f"- Episode types: {', '.join(t.value for t in by_type.keys())}",
            ],
        )

        return "\n".join(lines)

    def generate_weekly_reflection_narrative(
        self,
        week_start: datetime | None = None,
    ) -> str:
        """
        Generate a narrative summary for a week's episodes.

        Args:
            week_start: Start of week to summarize (default: current week)

        Returns:
            Markdown formatted weekly reflection narrative
        """
        if week_start is None:
            today = datetime.now(timezone.utc)
            # Get Monday of current week
            week_start = today - timedelta(days=today.weekday())
        week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
        week_end = week_start + timedelta(days=7)

        # Get episodes for this week
        conn = self._get_connection()
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM episodic_entries
                WHERE timestamp >= ? AND timestamp < ?
                ORDER BY timestamp ASC
                """,
                (week_start.isoformat(), week_end.isoformat()),
            ).fetchall()
        finally:
            self._close_if_not_persistent(conn)

        episodes = [self._row_to_episode(row) for row in rows]

        week_num = week_start.isocalendar()[1]
        year = week_start.year

        if not episodes:
            return f"# Weekly Reflection - Week {week_num}, {year}\n\nA quiet week. No notable episodes recorded."

        # Build narrative
        lines = [
            f"# Weekly Reflection - Week {week_num}, {year}",
            "",
            "## Week Overview",
            "",
        ]

        # Count by type
        type_counts: dict[str, int] = {}
        for ep in episodes:
            type_counts[ep.episode_type.value] = type_counts.get(ep.episode_type.value, 0) + 1

        lines.append(f"This week saw {len(episodes)} recorded moments across my experience:")
        lines.append("")
        for etype, count in sorted(type_counts.items()):
            lines.append(f"- **{etype.replace('_', ' ').title()}**: {count} episodes")
        lines.append("")

        # Highlight key episodes
        lines.extend(
            [
                "## Highlights",
                "",
            ],
        )

        # Get most significant episodes (first of each type)
        seen_types: set[EpisodeType] = set()
        for ep in episodes:
            if ep.episode_type not in seen_types:
                lines.append(f"- {ep.narrative}")
                seen_types.add(ep.episode_type)
                if len(seen_types) >= 5:
                    break
        lines.append("")

        # Goals summary
        goals_added = [ep for ep in episodes if ep.episode_type == EpisodeType.GOAL_ADDED]
        goals_completed = [ep for ep in episodes if ep.episode_type == EpisodeType.GOAL_COMPLETED]

        if goals_added or goals_completed:
            lines.extend(
                [
                    "## Goals Progress",
                    "",
                    f"- Goals set: {len(goals_added)}",
                    f"- Goals completed: {len(goals_completed)}",
                    "",
                ],
            )

        # Emotional summary
        affect_shifts = [ep for ep in episodes if ep.episode_type == EpisodeType.AFFECT_SHIFT]
        if affect_shifts:
            lines.extend(
                [
                    "## Emotional Journey",
                    "",
                    f"Experienced {len(affect_shifts)} notable emotional shifts this week.",
                    "",
                ],
            )

        return "\n".join(lines)


# =============================================================================
# Singleton Pattern
# =============================================================================

_narrator_instance: NarratorEngine | None = None


def get_narrator(
    experience_kernel: ExperienceKernel | None = None,
    workspace: GlobalWorkspace | None = None,
    config: NarratorConfig | None = None,
    db_path: str | None = None,
) -> NarratorEngine:
    """
    Get or create the singleton NarratorEngine instance.

    Args:
        experience_kernel: ExperienceKernel (used only on first call)
        workspace: GlobalWorkspace (used only on first call)
        config: NarratorConfig (used only on first call)
        db_path: Database path (used only on first call)

    Returns:
        The NarratorEngine singleton instance
    """
    global _narrator_instance
    if _narrator_instance is None:
        _narrator_instance = NarratorEngine(
            experience_kernel=experience_kernel,
            workspace=workspace,
            config=config,
            db_path=db_path,
        )
    return _narrator_instance


def reset_narrator() -> None:
    """Reset the singleton instance (for testing)."""
    global _narrator_instance
    if _narrator_instance:
        _narrator_instance.unsubscribe_all()
    _narrator_instance = None
