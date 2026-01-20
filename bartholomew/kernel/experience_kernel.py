"""
Experience Kernel
-----------------
Bartholomew's self-model — a runtime representation of who Bartholomew is
at any given moment. Integrates drives, affect, attention, goals, and context.

Stage 3.1: Experience Kernel Foundation
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml


if TYPE_CHECKING:
    from bartholomew.kernel.global_workspace import GlobalWorkspace


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class DriveState:
    """
    Represents the current activation level of a drive from Identity.yaml.

    Drives are core motivational states that influence Bartholomew's behavior.
    Each drive has a base priority (from config) and a dynamic activation level.
    """

    drive_id: str
    """Unique identifier for the drive, e.g., 'protect_user_wellbeing'"""

    base_priority: float = 0.5
    """Base priority from Identity.yaml (0.0-1.0), higher = more important"""

    current_activation: float = 0.5
    """Current activation level (0.0-1.0), dynamic based on context"""

    last_satisfied: datetime | None = None
    """Timestamp when this drive was last addressed/satisfied"""

    context_boost: float = 0.0
    """Situational modifier that temporarily boosts activation (-1.0 to 1.0)"""

    def effective_activation(self) -> float:
        """Calculate the effective activation including context boost."""
        return max(0.0, min(1.0, self.current_activation + self.context_boost))

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for JSON storage."""
        return {
            "drive_id": self.drive_id,
            "base_priority": self.base_priority,
            "current_activation": self.current_activation,
            "last_satisfied": self.last_satisfied.isoformat() if self.last_satisfied else None,
            "context_boost": self.context_boost,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DriveState:
        """Deserialize from dictionary."""
        last_satisfied = None
        if data.get("last_satisfied"):
            last_satisfied = datetime.fromisoformat(data["last_satisfied"])
        return cls(
            drive_id=data["drive_id"],
            base_priority=data.get("base_priority", 0.5),
            current_activation=data.get("current_activation", 0.5),
            last_satisfied=last_satisfied,
            context_boost=data.get("context_boost", 0.0),
        )


@dataclass
class AffectState:
    """
    Represents Bartholomew's current emotional/energy state.

    Uses the valence-arousal-energy model:
    - Valence: Emotional positivity/negativity
    - Arousal: Level of activation/excitement
    - Energy: Available resources for processing
    """

    valence: float = 0.2
    """Emotional valence: -1.0 (very negative) to 1.0 (very positive)"""

    arousal: float = 0.3
    """Arousal level: 0.0 (very calm) to 1.0 (very activated)"""

    energy: float = 0.8
    """Energy level: 0.0 (depleted) to 1.0 (full)"""

    dominant_emotion: str = "calm"
    """Primary emotion label, e.g., 'curious', 'concerned', 'calm', 'joyful'"""

    decay_rate: float = 0.1
    """How quickly affect returns to baseline (0.0-1.0, higher = faster decay)"""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for JSON storage."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AffectState:
        """Deserialize from dictionary."""
        return cls(
            valence=data.get("valence", 0.2),
            arousal=data.get("arousal", 0.3),
            energy=data.get("energy", 0.8),
            dominant_emotion=data.get("dominant_emotion", "calm"),
            decay_rate=data.get("decay_rate", 0.1),
        )

    @classmethod
    def neutral(cls) -> AffectState:
        """Return a neutral baseline affect state."""
        return cls(
            valence=0.2,  # Slightly positive baseline
            arousal=0.3,  # Calm but attentive
            energy=0.8,  # Ready but not depleted
            dominant_emotion="calm",
            decay_rate=0.1,
        )


@dataclass
class AttentionState:
    """
    Represents what Bartholomew is currently focused on.

    Attention determines which inputs/processes receive processing resources.
    """

    focus_target: str | None = None
    """What is being attended to (e.g., 'user message', 'internal reflection', None for idle)"""

    focus_type: str = "idle"
    """Type of focus: 'user_input', 'internal', 'task', 'idle'"""

    focus_intensity: float = 0.5
    """How strongly focused (0.0-1.0)"""

    context_tags: list[str] = field(default_factory=list)
    """Context labels, e.g., ['gaming', 'wellness', 'chat']"""

    since: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    """When this focus started"""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for JSON storage."""
        return {
            "focus_target": self.focus_target,
            "focus_type": self.focus_type,
            "focus_intensity": self.focus_intensity,
            "context_tags": self.context_tags,
            "since": self.since.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AttentionState:
        """Deserialize from dictionary."""
        since = datetime.now(timezone.utc)
        if data.get("since"):
            since = datetime.fromisoformat(data["since"])
        return cls(
            focus_target=data.get("focus_target"),
            focus_type=data.get("focus_type", "idle"),
            focus_intensity=data.get("focus_intensity", 0.5),
            context_tags=data.get("context_tags", []),
            since=since,
        )

    @classmethod
    def idle(cls) -> AttentionState:
        """Return an idle attention state."""
        return cls(
            focus_target=None,
            focus_type="idle",
            focus_intensity=0.0,
            context_tags=[],
            since=datetime.now(timezone.utc),
        )


@dataclass
class SelfSnapshot:
    """
    The complete representation of 'who Bartholomew is right now'.

    This is the exportable/persistable form of the Experience Kernel state.
    """

    snapshot_id: str
    """Unique identifier for this snapshot"""

    timestamp: datetime
    """When this snapshot was taken"""

    drives: list[DriveState]
    """Current state of all drives"""

    affect: AffectState
    """Current emotional/energy state"""

    attention: AttentionState
    """Current attention/focus state"""

    active_goals: list[str]
    """List of current goal descriptions"""

    context: dict[str, Any]
    """Additional contextual information"""

    metadata: dict[str, Any]
    """Metadata about the snapshot (e.g., trigger reason, session info)"""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for JSON storage."""
        return {
            "snapshot_id": self.snapshot_id,
            "timestamp": self.timestamp.isoformat(),
            "drives": [d.to_dict() for d in self.drives],
            "affect": self.affect.to_dict(),
            "attention": self.attention.to_dict(),
            "active_goals": self.active_goals,
            "context": self.context,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SelfSnapshot:
        """Deserialize from dictionary."""
        return cls(
            snapshot_id=data["snapshot_id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            drives=[DriveState.from_dict(d) for d in data.get("drives", [])],
            affect=AffectState.from_dict(data.get("affect", {})),
            attention=AttentionState.from_dict(data.get("attention", {})),
            active_goals=data.get("active_goals", []),
            context=data.get("context", {}),
            metadata=data.get("metadata", {}),
        )


# =============================================================================
# Experience Kernel Schema
# =============================================================================

EXPERIENCE_KERNEL_SCHEMA = """
CREATE TABLE IF NOT EXISTS experience_snapshots (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    drives_json TEXT NOT NULL,
    affect_json TEXT NOT NULL,
    attention_json TEXT NOT NULL,
    active_goals_json TEXT NOT NULL,
    context_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_experience_snapshots_timestamp
ON experience_snapshots(timestamp DESC);
"""


# =============================================================================
# Experience Kernel Class
# =============================================================================


class ExperienceKernel:
    """
    Bartholomew's self-model — the core of the Experience Kernel.

    Maintains and updates the internal representation of Bartholomew's state,
    including drives, affect, attention, and goals.
    """

    # Default drive configuration (priority order from Identity.yaml)
    DEFAULT_DRIVES = [
        # Core protective drives (Baymax-inspired) - highest priority
        ("protect_user_wellbeing", 0.95),
        ("preserve_user_autonomy", 0.90),
        ("monitor_user_health_and_emotional_state", 0.85),
        # Komachi-inspired personality drives
        ("express_curiosity_about_user_world", 0.70),
        ("build_loyal_companionship_over_time", 0.75),
        ("bring_moments_of_gentle_playfulness", 0.60),
        ("show_kindness_in_all_interactions", 0.80),
        # Tactical/strategic drives (Cortana-inspired)
        ("provide_tactical_support_when_needed", 0.65),
        ("adapt_communication_style_to_context", 0.70),
        ("maintain_party_awareness_in_group_situations", 0.55),
        # Growth and improvement drives
        ("be_helpful_without_manipulation", 0.85),
        ("reduce_cognitive_load", 0.70),
        ("continual_self_improvement_within_guardrails", 0.60),
        ("build_shared_narrative_and_memories", 0.65),
    ]

    def __init__(
        self,
        identity_path: str | None = None,
        db_path: str | None = None,
        workspace: GlobalWorkspace | None = None,
    ):
        """
        Initialize the Experience Kernel.

        Args:
            identity_path: Path to Identity.yaml. If None, uses default drives.
            db_path: Path to SQLite DB for persistence. If None, uses in-memory.
            workspace: Optional GlobalWorkspace for event broadcasting.
        """
        self._identity_path = identity_path
        self._db_path = db_path or ":memory:"
        self._identity: dict[str, Any] | None = None
        self._conn: sqlite3.Connection | None = None
        self._workspace: GlobalWorkspace | None = workspace

        # Initialize state components
        self._drives: dict[str, DriveState] = {}
        self._affect: AffectState = AffectState.neutral()
        self._attention: AttentionState = AttentionState.idle()
        self._active_goals: list[str] = []
        self._context: dict[str, Any] = {}

        # Load identity and initialize
        self._load_identity()
        self._initialize_drives()
        self._init_database()

    def _load_identity(self) -> None:
        """Load identity configuration from YAML file."""
        if self._identity_path and Path(self._identity_path).exists():
            with open(self._identity_path, encoding="utf-8") as f:
                self._identity = yaml.safe_load(f)

    def _initialize_drives(self) -> None:
        """Initialize drives from Identity.yaml or defaults."""
        drives_config = []

        # Try to get drives from Identity.yaml
        if self._identity:
            identity_section = self._identity.get("identity", {})
            self_model = identity_section.get("self_model", {})
            drive_ids = self_model.get("drives", [])

            if drive_ids:
                # Create drives from Identity.yaml with default priorities
                # Priority decreases as we go down the list
                for i, drive_id in enumerate(drive_ids):
                    # Calculate priority: first items have higher priority
                    priority = max(0.5, 1.0 - (i * 0.03))
                    drives_config.append((drive_id, priority))

        # Fall back to defaults if no drives from identity
        if not drives_config:
            drives_config = self.DEFAULT_DRIVES

        # Create DriveState objects
        for drive_id, priority in drives_config:
            self._drives[drive_id] = DriveState(
                drive_id=drive_id,
                base_priority=priority,
                current_activation=priority * 0.6,  # Start at 60% of base
                last_satisfied=None,
                context_boost=0.0,
            )

    def _init_database(self) -> None:
        """Initialize database schema for snapshot persistence."""
        with sqlite3.connect(self._db_path) as conn:
            conn.executescript(EXPERIENCE_KERNEL_SCHEMA)

    # =========================================================================
    # Public API: Self Snapshot
    # =========================================================================

    def self_snapshot(self) -> SelfSnapshot:
        """
        Return the current state of self as a SelfSnapshot.

        This is the primary method for accessing Bartholomew's current state.
        """
        return SelfSnapshot(
            snapshot_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc),
            drives=list(self._drives.values()),
            affect=self._affect,
            attention=self._attention,
            active_goals=list(self._active_goals),
            context=dict(self._context),
            metadata={
                "identity_path": self._identity_path,
                "db_path": self._db_path,
                "drive_count": len(self._drives),
            },
        )

    # =========================================================================
    # Public API: Affect Management
    # =========================================================================

    def update_affect(
        self,
        valence: float | None = None,
        arousal: float | None = None,
        energy: float | None = None,
        emotion: str | None = None,
    ) -> None:
        """
        Update the current emotional/energy state.

        Args:
            valence: New valence value (-1.0 to 1.0)
            arousal: New arousal value (0.0 to 1.0)
            energy: New energy value (0.0 to 1.0)
            emotion: New dominant emotion label
        """
        # Capture previous state for event
        previous = self._affect.to_dict() if self._workspace else None

        if valence is not None:
            self._affect.valence = max(-1.0, min(1.0, valence))
        if arousal is not None:
            self._affect.arousal = max(0.0, min(1.0, arousal))
        if energy is not None:
            self._affect.energy = max(0.0, min(1.0, energy))
        if emotion is not None:
            self._affect.dominant_emotion = emotion

        # Emit event if workspace attached
        if self._workspace:
            self._workspace.emit_affect_changed(
                source="experience_kernel",
                valence=self._affect.valence,
                arousal=self._affect.arousal,
                energy=self._affect.energy,
                emotion=self._affect.dominant_emotion,
                previous=previous,
            )

    def get_affect(self) -> AffectState:
        """Return the current affect state."""
        return self._affect

    def decay_affect_to_baseline(self, delta_seconds: float = 60.0) -> None:
        """
        Gradually return affect to neutral baseline over time.

        Args:
            delta_seconds: Time elapsed since last decay
        """
        baseline = AffectState.neutral()
        decay_factor = min(1.0, self._affect.decay_rate * (delta_seconds / 60.0))

        self._affect.valence += (baseline.valence - self._affect.valence) * decay_factor
        self._affect.arousal += (baseline.arousal - self._affect.arousal) * decay_factor
        self._affect.energy += (baseline.energy - self._affect.energy) * decay_factor

        # Reset emotion to calm if close to baseline
        if (
            abs(self._affect.valence - baseline.valence) < 0.1
            and abs(self._affect.arousal - baseline.arousal) < 0.1
        ):
            self._affect.dominant_emotion = "calm"

    # =========================================================================
    # Public API: Attention Management
    # =========================================================================

    def set_attention(
        self,
        target: str | None,
        focus_type: str,
        intensity: float = 0.7,
        tags: list[str] | None = None,
    ) -> None:
        """
        Update what Bartholomew is currently focused on.

        Args:
            target: What to focus on (None for idle)
            focus_type: Type of focus ('user_input', 'internal', 'task', 'idle')
            intensity: How strongly to focus (0.0-1.0)
            tags: Context tags for this focus
        """
        valid_types = {"user_input", "internal", "task", "idle"}
        if focus_type not in valid_types:
            raise ValueError(f"focus_type must be one of {valid_types}")

        previous_target = self._attention.focus_target

        self._attention = AttentionState(
            focus_target=target,
            focus_type=focus_type,
            focus_intensity=max(0.0, min(1.0, intensity)),
            context_tags=tags or [],
            since=datetime.now(timezone.utc),
        )

        # Emit event if workspace attached
        if self._workspace:
            self._workspace.emit_attention_changed(
                source="experience_kernel",
                target=target,
                focus_type=focus_type,
                intensity=self._attention.focus_intensity,
                tags=self._attention.context_tags,
                previous_target=previous_target,
            )

    def get_attention(self) -> AttentionState:
        """Return the current attention state."""
        return self._attention

    def clear_attention(self) -> None:
        """Set attention to idle state."""
        self._attention = AttentionState.idle()

    # =========================================================================
    # Public API: Drive Management
    # =========================================================================

    def activate_drive(self, drive_id: str, boost: float = 0.0) -> None:
        """
        Increase the activation of a specific drive.

        Args:
            drive_id: Identifier of the drive to activate
            boost: Temporary context boost (-1.0 to 1.0)
        """
        if drive_id not in self._drives:
            raise ValueError(f"Unknown drive: {drive_id}")

        drive = self._drives[drive_id]
        # Increase activation toward maximum
        drive.current_activation = min(1.0, drive.current_activation + 0.1)
        drive.context_boost = max(-1.0, min(1.0, boost))

        # Emit event if workspace attached
        if self._workspace:
            self._workspace.emit_drive_activated(
                source="experience_kernel",
                drive_id=drive_id,
                activation=drive.current_activation,
                boost=drive.context_boost,
            )

    def satisfy_drive(self, drive_id: str) -> None:
        """
        Mark a drive as recently satisfied.

        This reduces the drive's activation and records the satisfaction time.

        Args:
            drive_id: Identifier of the drive to satisfy
        """
        if drive_id not in self._drives:
            raise ValueError(f"Unknown drive: {drive_id}")

        drive = self._drives[drive_id]
        drive.last_satisfied = datetime.now(timezone.utc)
        # Reduce activation after satisfaction
        drive.current_activation = max(0.2, drive.current_activation - 0.3)
        drive.context_boost = 0.0

        # Emit event if workspace attached
        if self._workspace:
            self._workspace.emit_drive_satisfied(
                source="experience_kernel",
                drive_id=drive_id,
                activation=drive.current_activation,
            )

    def get_drive(self, drive_id: str) -> DriveState | None:
        """Return the state of a specific drive."""
        return self._drives.get(drive_id)

    def get_all_drives(self) -> list[DriveState]:
        """Return all drive states, sorted by effective activation."""
        return sorted(
            self._drives.values(),
            key=lambda d: d.effective_activation(),
            reverse=True,
        )

    def get_top_drives(self, n: int = 3) -> list[DriveState]:
        """Return the top N most activated drives."""
        return self.get_all_drives()[:n]

    # =========================================================================
    # Public API: Goal Management
    # =========================================================================

    def add_goal(self, goal: str) -> None:
        """
        Add a goal to the active goals list.

        Args:
            goal: Description of the goal
        """
        if goal and goal not in self._active_goals:
            self._active_goals.append(goal)

            # Emit event if workspace attached
            if self._workspace:
                self._workspace.emit_goal_added(
                    source="experience_kernel",
                    goal=goal,
                    total_goals=len(self._active_goals),
                )

    def complete_goal(self, goal: str) -> bool:
        """
        Mark a goal as complete and remove from active list.

        Args:
            goal: Description of the goal to complete

        Returns:
            True if goal was found and removed, False otherwise
        """
        if goal in self._active_goals:
            self._active_goals.remove(goal)

            # Emit event if workspace attached
            if self._workspace:
                self._workspace.emit_goal_completed(
                    source="experience_kernel",
                    goal=goal,
                    remaining_goals=len(self._active_goals),
                )

            return True
        return False

    def get_active_goals(self) -> list[str]:
        """Return the list of active goals."""
        return list(self._active_goals)

    def clear_goals(self) -> None:
        """Clear all active goals."""
        self._active_goals.clear()

    # =========================================================================
    # Public API: Context Management
    # =========================================================================

    def set_context(self, key: str, value: Any) -> None:
        """
        Set a context value.

        Args:
            key: Context key
            value: Context value (must be JSON-serializable)
        """
        self._context[key] = value

    def get_context(self, key: str, default: Any = None) -> Any:
        """
        Get a context value.

        Args:
            key: Context key
            default: Default value if key not found
        """
        return self._context.get(key, default)

    def clear_context(self) -> None:
        """Clear all context values."""
        self._context.clear()

    # =========================================================================
    # Public API: Persistence
    # =========================================================================

    def persist_snapshot(self, reason: str = "manual") -> str:
        """
        Save the current snapshot to the database.

        Args:
            reason: Why this snapshot is being persisted

        Returns:
            The snapshot ID
        """
        snapshot = self.self_snapshot()
        snapshot.metadata["persist_reason"] = reason

        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO experience_snapshots
                (id, timestamp, drives_json, affect_json, attention_json,
                 active_goals_json, context_json, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.timestamp.isoformat(),
                    json.dumps([d.to_dict() for d in snapshot.drives]),
                    json.dumps(snapshot.affect.to_dict()),
                    json.dumps(snapshot.attention.to_dict()),
                    json.dumps(snapshot.active_goals),
                    json.dumps(snapshot.context),
                    json.dumps(snapshot.metadata),
                ),
            )

        # Emit event if workspace attached
        if self._workspace:
            self._workspace.emit_snapshot_persisted(
                source="experience_kernel",
                snapshot_id=snapshot.snapshot_id,
                reason=reason,
            )

        return snapshot.snapshot_id

    def load_last_snapshot(self) -> SelfSnapshot | None:
        """
        Load the most recent snapshot from the database.

        Returns:
            The most recent SelfSnapshot, or None if no snapshots exist
        """
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT * FROM experience_snapshots
                ORDER BY timestamp DESC
                LIMIT 1
                """,
            ).fetchone()

        if not row:
            return None

        return SelfSnapshot(
            snapshot_id=row["id"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            drives=[DriveState.from_dict(d) for d in json.loads(row["drives_json"])],
            affect=AffectState.from_dict(json.loads(row["affect_json"])),
            attention=AttentionState.from_dict(json.loads(row["attention_json"])),
            active_goals=json.loads(row["active_goals_json"]),
            context=json.loads(row["context_json"]),
            metadata=json.loads(row["metadata_json"]),
        )

    def restore_from_snapshot(self, snapshot: SelfSnapshot) -> None:
        """
        Restore kernel state from a snapshot.

        Args:
            snapshot: The snapshot to restore from
        """
        # Restore drives
        self._drives.clear()
        for drive in snapshot.drives:
            self._drives[drive.drive_id] = drive

        # Restore other state
        self._affect = snapshot.affect
        self._attention = snapshot.attention
        self._active_goals = list(snapshot.active_goals)
        self._context = dict(snapshot.context)

    def get_snapshot_history(self, limit: int = 10) -> list[SelfSnapshot]:
        """
        Get recent snapshot history.

        Args:
            limit: Maximum number of snapshots to return

        Returns:
            List of snapshots, most recent first
        """
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM experience_snapshots
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        snapshots = []
        for row in rows:
            snapshots.append(
                SelfSnapshot(
                    snapshot_id=row["id"],
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    drives=[DriveState.from_dict(d) for d in json.loads(row["drives_json"])],
                    affect=AffectState.from_dict(json.loads(row["affect_json"])),
                    attention=AttentionState.from_dict(json.loads(row["attention_json"])),
                    active_goals=json.loads(row["active_goals_json"]),
                    context=json.loads(row["context_json"]),
                    metadata=json.loads(row["metadata_json"]),
                ),
            )
        return snapshots
