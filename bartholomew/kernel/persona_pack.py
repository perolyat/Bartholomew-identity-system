"""
Persona Pack System
-------------------
Switchable persona configurations for Bartholomew that define tone, style,
drive priorities, and narrative templates.

Stage 3.5: Persona Pack System Implementation
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml


if TYPE_CHECKING:
    from bartholomew.kernel.experience_kernel import ExperienceKernel
    from bartholomew.kernel.global_workspace import GlobalWorkspace


# =============================================================================
# Style Configuration
# =============================================================================


class Brevity(Enum):
    """Response length preference."""

    MINIMAL = "minimal"  # ≤30 chars for critical
    CONCISE = "concise"  # Short, focused
    BALANCED = "balanced"  # Context-appropriate
    EXPANDED = "expanded"  # Detailed with context


class Formality(Enum):
    """Communication formality level."""

    CASUAL = "casual"  # Friendly, informal
    CONVERSATIONAL = "conversational"  # Warm but clear
    PROFESSIONAL = "professional"  # Business-appropriate
    FORMAL = "formal"  # Highly structured


@dataclass
class StyleConfig:
    """
    Configuration for communication style within a persona pack.
    """

    brevity: Brevity = Brevity.BALANCED
    """Response length preference"""

    formality: Formality = Formality.CONVERSATIONAL
    """Communication formality"""

    humor_allowed: bool = True
    """Whether playful/humorous responses are appropriate"""

    emoji_allowed: bool = False
    """Whether to include emoji in responses"""

    technical_depth: float = 0.5
    """How technical responses should be (0.0-1.0)"""

    warmth: float = 0.7
    """Emotional warmth level (0.0-1.0)"""

    directness: float = 0.6
    """How direct vs. hedging (0.0-1.0)"""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "brevity": self.brevity.value,
            "formality": self.formality.value,
            "humor_allowed": self.humor_allowed,
            "emoji_allowed": self.emoji_allowed,
            "technical_depth": self.technical_depth,
            "warmth": self.warmth,
            "directness": self.directness,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StyleConfig:
        """Deserialize from dictionary."""
        return cls(
            brevity=Brevity(data.get("brevity", "balanced")),
            formality=Formality(data.get("formality", "conversational")),
            humor_allowed=data.get("humor_allowed", True),
            emoji_allowed=data.get("emoji_allowed", False),
            technical_depth=data.get("technical_depth", 0.5),
            warmth=data.get("warmth", 0.7),
            directness=data.get("directness", 0.6),
        )


# =============================================================================
# Persona Pack
# =============================================================================


@dataclass
class PersonaPack:
    """
    A complete persona configuration that can be loaded and switched at runtime.

    Persona packs define how Bartholomew presents itself, including voice,
    drive priorities, and narrative templates.
    """

    pack_id: str
    """Unique identifier, e.g., 'tactical_cortana', 'baymax_care'"""

    name: str
    """Human-readable name"""

    description: str
    """Brief description of this persona"""

    # Voice & Style
    tone: list[str] = field(default_factory=lambda: ["warm", "helpful"])
    """Tone keywords, e.g., ['precise', 'urgent', 'supportive']"""

    style: StyleConfig = field(default_factory=StyleConfig)
    """Detailed style configuration"""

    # Drive Priority Overrides
    drive_boosts: dict[str, float] = field(default_factory=dict)
    """Drive activation boosts, e.g., {'protect_user_wellbeing': 0.2}"""

    # Narrative Template Overrides
    narrative_overrides: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    """Override narrator templates: {episode_type: {tone: [templates]}}"""

    # Trigger Conditions
    auto_activate_on: list[str] = field(default_factory=list)
    """Context tags that auto-activate this persona, e.g., ['gaming', 'crisis']"""

    # Archetype references
    archetype: str = "companion"
    """Primary archetype: 'companion', 'tactical', 'caregiver', 'mentor'"""

    inspirations: list[str] = field(default_factory=list)
    """Character inspirations, e.g., ['Baymax', 'JARVIS', 'Cortana']"""

    # Metadata
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    """When this pack was created"""

    author: str = "system"
    """Who created this pack"""

    version: str = "1.0.0"
    """Pack version"""

    is_default: bool = False
    """Whether this is the default pack"""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for YAML/JSON storage."""
        return {
            "pack_id": self.pack_id,
            "name": self.name,
            "description": self.description,
            "tone": self.tone,
            "style": self.style.to_dict(),
            "drive_boosts": self.drive_boosts,
            "narrative_overrides": self.narrative_overrides,
            "auto_activate_on": self.auto_activate_on,
            "archetype": self.archetype,
            "inspirations": self.inspirations,
            "created_at": self.created_at.isoformat(),
            "author": self.author,
            "version": self.version,
            "is_default": self.is_default,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PersonaPack:
        """Deserialize from dictionary."""
        created_at = datetime.now(timezone.utc)
        if data.get("created_at"):
            if isinstance(data["created_at"], str):
                created_at = datetime.fromisoformat(data["created_at"])
            elif isinstance(data["created_at"], datetime):
                created_at = data["created_at"]

        style = StyleConfig()
        if data.get("style"):
            style = StyleConfig.from_dict(data["style"])

        return cls(
            pack_id=data["pack_id"],
            name=data.get("name", data["pack_id"]),
            description=data.get("description", ""),
            tone=data.get("tone", ["warm", "helpful"]),
            style=style,
            drive_boosts=data.get("drive_boosts", {}),
            narrative_overrides=data.get("narrative_overrides", {}),
            auto_activate_on=data.get("auto_activate_on", []),
            archetype=data.get("archetype", "companion"),
            inspirations=data.get("inspirations", []),
            created_at=created_at,
            author=data.get("author", "system"),
            version=data.get("version", "1.0.0"),
            is_default=data.get("is_default", False),
        )

    @classmethod
    def load_from_yaml(cls, path: str | Path) -> PersonaPack:
        """Load a persona pack from a YAML file."""
        path = Path(path)
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data)

    def save_to_yaml(self, path: str | Path) -> None:
        """Save this persona pack to a YAML file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, allow_unicode=True)


# =============================================================================
# Persona Switch Event
# =============================================================================


@dataclass
class PersonaSwitchRecord:
    """
    Record of a persona switch for audit purposes.
    """

    record_id: str
    """Unique identifier for this switch record"""

    timestamp: datetime
    """When the switch occurred"""

    from_pack_id: str | None
    """Previous pack ID (None if first activation)"""

    to_pack_id: str
    """New pack ID"""

    trigger: str
    """What triggered the switch: 'manual', 'auto', 'context', 'startup'"""

    context_tags: list[str]
    """Active context tags at switch time"""

    metadata: dict[str, Any] = field(default_factory=dict)
    """Additional metadata"""

    def to_dict(self) -> dict[str, Any]:
        """Serialize for storage."""
        return {
            "record_id": self.record_id,
            "timestamp": self.timestamp.isoformat(),
            "from_pack_id": self.from_pack_id,
            "to_pack_id": self.to_pack_id,
            "trigger": self.trigger,
            "context_tags": self.context_tags,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PersonaSwitchRecord:
        """Deserialize from storage."""
        return cls(
            record_id=data["record_id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            from_pack_id=data.get("from_pack_id"),
            to_pack_id=data["to_pack_id"],
            trigger=data.get("trigger", "unknown"),
            context_tags=data.get("context_tags", []),
            metadata=data.get("metadata", {}),
        )


# =============================================================================
# Persona Pack Schema
# =============================================================================

PERSONA_PACK_SCHEMA = """
CREATE TABLE IF NOT EXISTS persona_switch_log (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    from_pack_id TEXT,
    to_pack_id TEXT NOT NULL,
    trigger TEXT NOT NULL,
    context_tags_json TEXT,
    metadata_json TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_persona_switch_timestamp
ON persona_switch_log(timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_persona_switch_to_pack
ON persona_switch_log(to_pack_id);
"""


# =============================================================================
# Persona Pack Manager
# =============================================================================


class PersonaPackManager:
    """
    Manages persona packs — loading, switching, and integrating with the kernel.

    The manager maintains the active persona and coordinates with:
    - ExperienceKernel (drive boosts)
    - NarratorEngine (template overrides)
    - GlobalWorkspace (switch events)
    """

    DEFAULT_PACKS_DIR = "config/persona_packs"

    def __init__(
        self,
        packs_dir: str | Path | None = None,
        experience_kernel: ExperienceKernel | None = None,
        workspace: GlobalWorkspace | None = None,
        db_path: str | None = None,
    ):
        """
        Initialize the Persona Pack Manager.

        Args:
            packs_dir: Directory containing persona pack YAML files
            experience_kernel: ExperienceKernel for drive boost application
            workspace: GlobalWorkspace for event emission
            db_path: Path to SQLite DB for switch logging
        """
        self._packs_dir = Path(packs_dir) if packs_dir else Path(self.DEFAULT_PACKS_DIR)
        self._kernel = experience_kernel
        self._workspace = workspace
        self._db_path = db_path or ":memory:"

        # Pack registry
        self._packs: dict[str, PersonaPack] = {}
        self._active_pack_id: str | None = None

        # Switch callbacks
        self._on_switch_callbacks: list[Callable[[PersonaPack | None, PersonaPack], None]] = []

        # For in-memory databases, keep a persistent connection
        self._conn: sqlite3.Connection | None = None
        if self._db_path == ":memory:":
            self._conn = sqlite3.connect(":memory:")
            self._conn.executescript(PERSONA_PACK_SCHEMA)
        else:
            self._init_database()

        # Auto-load packs from directory
        self._load_packs_from_directory()

    def _init_database(self) -> None:
        """Initialize database schema for switch logging."""
        with sqlite3.connect(self._db_path) as conn:
            conn.executescript(PERSONA_PACK_SCHEMA)

    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection."""
        if self._conn is not None:
            return self._conn
        return sqlite3.connect(self._db_path)

    def _close_if_not_persistent(self, conn: sqlite3.Connection) -> None:
        """Close connection if it's not the persistent one."""
        if conn is not self._conn:
            conn.close()

    def _load_packs_from_directory(self) -> None:
        """Load all persona packs from the packs directory."""
        if not self._packs_dir.exists():
            return

        for yaml_file in self._packs_dir.glob("*.yaml"):
            try:
                pack = PersonaPack.load_from_yaml(yaml_file)
                self._packs[pack.pack_id] = pack

                # Auto-activate default pack
                if pack.is_default and self._active_pack_id is None:
                    self._active_pack_id = pack.pack_id
            except Exception:
                # Skip invalid pack files
                pass

    # =========================================================================
    # Pack Registration
    # =========================================================================

    def register_pack(self, pack: PersonaPack) -> None:
        """
        Register a persona pack with the manager.

        Args:
            pack: PersonaPack to register
        """
        self._packs[pack.pack_id] = pack

        # Auto-activate if default and no active pack
        if pack.is_default and self._active_pack_id is None:
            self._active_pack_id = pack.pack_id

    def unregister_pack(self, pack_id: str) -> bool:
        """
        Unregister a persona pack.

        Args:
            pack_id: ID of pack to unregister

        Returns:
            True if pack was found and removed
        """
        if pack_id not in self._packs:
            return False

        # Can't unregister active pack
        if pack_id == self._active_pack_id:
            return False

        del self._packs[pack_id]
        return True

    def get_pack(self, pack_id: str) -> PersonaPack | None:
        """Get a registered pack by ID."""
        return self._packs.get(pack_id)

    def list_packs(self) -> list[str]:
        """List all registered pack IDs."""
        return list(self._packs.keys())

    def get_all_packs(self) -> list[PersonaPack]:
        """Get all registered packs."""
        return list(self._packs.values())

    # =========================================================================
    # Active Pack
    # =========================================================================

    def get_active_pack(self) -> PersonaPack | None:
        """Get the currently active persona pack."""
        if self._active_pack_id is None:
            return None
        return self._packs.get(self._active_pack_id)

    def get_active_pack_id(self) -> str | None:
        """Get the ID of the currently active pack."""
        return self._active_pack_id

    def switch_pack(
        self,
        pack_id: str,
        trigger: str = "manual",
        context_tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """
        Switch to a different persona pack.

        Args:
            pack_id: ID of the pack to switch to
            trigger: What triggered the switch ('manual', 'auto', 'context', 'startup')
            context_tags: Current context tags
            metadata: Additional metadata for the switch record

        Returns:
            True if switch was successful
        """
        if pack_id not in self._packs:
            return False

        # Get packs
        from_pack = self.get_active_pack()
        to_pack = self._packs[pack_id]

        # Already active?
        if pack_id == self._active_pack_id:
            return True

        # Update active pack
        previous_pack_id = self._active_pack_id
        self._active_pack_id = pack_id

        # Apply drive boosts to kernel
        if self._kernel:
            self._apply_drive_boosts(from_pack, to_pack)

        # Log the switch
        record = PersonaSwitchRecord(
            record_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc),
            from_pack_id=previous_pack_id,
            to_pack_id=pack_id,
            trigger=trigger,
            context_tags=context_tags or [],
            metadata=metadata or {},
        )
        self._log_switch(record)

        # Emit workspace event
        if self._workspace:
            self._workspace.emit_persona_switched(
                source="persona_pack_manager",
                from_pack_id=previous_pack_id,
                to_pack_id=pack_id,
                trigger=trigger,
                context_tags=context_tags or [],
            )

        # Notify callbacks
        for callback in self._on_switch_callbacks:
            try:
                callback(from_pack, to_pack)
            except Exception:
                pass  # Don't let callback errors break the switch

        return True

    def _apply_drive_boosts(
        self,
        from_pack: PersonaPack | None,
        to_pack: PersonaPack,
    ) -> None:
        """Apply drive boost changes to the ExperienceKernel."""
        if not self._kernel:
            return

        # Remove old boosts
        if from_pack:
            for drive_id in from_pack.drive_boosts:
                drive = self._kernel.get_drive(drive_id)
                if drive:
                    drive.context_boost = 0.0

        # Apply new boosts
        for drive_id, boost in to_pack.drive_boosts.items():
            try:
                drive = self._kernel.get_drive(drive_id)
                if drive:
                    drive.context_boost = max(-1.0, min(1.0, boost))
            except ValueError:
                pass  # Unknown drive, skip

    def _log_switch(self, record: PersonaSwitchRecord) -> None:
        """Log a persona switch to the database."""
        conn = self._get_connection()
        try:
            conn.execute(
                """
                INSERT INTO persona_switch_log
                (id, timestamp, from_pack_id, to_pack_id, trigger, context_tags_json, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.record_id,
                    record.timestamp.isoformat(),
                    record.from_pack_id,
                    record.to_pack_id,
                    record.trigger,
                    json.dumps(record.context_tags),
                    json.dumps(record.metadata),
                ),
            )
            conn.commit()
        finally:
            self._close_if_not_persistent(conn)

    # =========================================================================
    # Auto-Activation
    # =========================================================================

    def check_auto_activation(self, context_tags: list[str]) -> str | None:
        """
        Check if any pack should auto-activate based on context tags.

        Args:
            context_tags: Current context tags

        Returns:
            Pack ID that should be activated, or None
        """
        for pack in self._packs.values():
            if not pack.auto_activate_on:
                continue

            # Check if any auto-activate tag matches
            for trigger_tag in pack.auto_activate_on:
                if trigger_tag in context_tags:
                    return pack.pack_id

        return None

    def auto_activate_if_needed(self, context_tags: list[str]) -> bool:
        """
        Automatically switch pack if context tags match an auto-activate condition.

        Args:
            context_tags: Current context tags

        Returns:
            True if a switch occurred
        """
        pack_id = self.check_auto_activation(context_tags)
        if pack_id and pack_id != self._active_pack_id:
            return self.switch_pack(
                pack_id,
                trigger="auto",
                context_tags=context_tags,
            )
        return False

    # =========================================================================
    # Callbacks
    # =========================================================================

    def on_switch(self, callback: Callable[[PersonaPack | None, PersonaPack], None]) -> None:
        """
        Register a callback to be called when persona is switched.

        Args:
            callback: Function(from_pack, to_pack) to call
        """
        self._on_switch_callbacks.append(callback)

    def remove_switch_callback(
        self,
        callback: Callable[[PersonaPack | None, PersonaPack], None],
    ) -> bool:
        """
        Remove a switch callback.

        Returns:
            True if callback was found and removed
        """
        if callback in self._on_switch_callbacks:
            self._on_switch_callbacks.remove(callback)
            return True
        return False

    # =========================================================================
    # Switch History
    # =========================================================================

    def get_switch_history(self, limit: int = 20) -> list[PersonaSwitchRecord]:
        """
        Get recent persona switch history.

        Args:
            limit: Maximum number of records to return

        Returns:
            List of switch records, most recent first
        """
        conn = self._get_connection()
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM persona_switch_log
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        finally:
            self._close_if_not_persistent(conn)

        return [
            PersonaSwitchRecord(
                record_id=row["id"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                from_pack_id=row["from_pack_id"],
                to_pack_id=row["to_pack_id"],
                trigger=row["trigger"],
                context_tags=(
                    json.loads(row["context_tags_json"]) if row["context_tags_json"] else []
                ),
                metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else {},
            )
            for row in rows
        ]

    def get_switch_count(self) -> int:
        """Get total number of persona switches."""
        conn = self._get_connection()
        try:
            count = conn.execute("SELECT COUNT(*) FROM persona_switch_log").fetchone()[0]
        finally:
            self._close_if_not_persistent(conn)
        return count

    # =========================================================================
    # Narrative Integration
    # =========================================================================

    def get_narrative_templates(
        self,
        episode_type: str,
        tone: str,
    ) -> list[str] | None:
        """
        Get narrative templates for the active pack.

        Args:
            episode_type: Type of episode (e.g., 'affect_shift')
            tone: Narrative tone (e.g., 'enthusiastic')

        Returns:
            List of template strings, or None if no override
        """
        pack = self.get_active_pack()
        if not pack:
            return None

        type_templates = pack.narrative_overrides.get(episode_type)
        if not type_templates:
            return None

        return type_templates.get(tone)

    def get_style(self) -> StyleConfig | None:
        """Get the style config for the active pack."""
        pack = self.get_active_pack()
        if pack:
            return pack.style
        return None

    def get_tone(self) -> list[str]:
        """Get the tone keywords for the active pack."""
        pack = self.get_active_pack()
        if pack:
            return pack.tone
        return ["warm", "helpful"]


# =============================================================================
# Default Persona Packs
# =============================================================================


def create_default_pack() -> PersonaPack:
    """Create the default Bartholomew persona pack."""
    return PersonaPack(
        pack_id="default",
        name="Bartholomew",
        description="The default kind companion persona - warm, helpful, and protective.",
        tone=["warm", "helpful", "kind", "curious"],
        style=StyleConfig(
            brevity=Brevity.BALANCED,
            formality=Formality.CONVERSATIONAL,
            humor_allowed=True,
            warmth=0.8,
            directness=0.6,
        ),
        drive_boosts={
            "show_kindness_in_all_interactions": 0.1,
            "build_loyal_companionship_over_time": 0.1,
        },
        archetype="companion",
        inspirations=["Komachi", "Baymax"],
        is_default=True,
        author="system",
    )


def create_tactical_pack() -> PersonaPack:
    """Create the tactical/gaming persona pack (Cortana-inspired)."""
    return PersonaPack(
        pack_id="tactical",
        name="Tactical Bartholomew",
        description="Sharp, focused strategist for gaming and high-stakes situations.",
        tone=["precise", "urgent", "supportive", "clear"],
        style=StyleConfig(
            brevity=Brevity.CONCISE,
            formality=Formality.PROFESSIONAL,
            humor_allowed=False,
            warmth=0.4,
            directness=0.9,
            technical_depth=0.7,
        ),
        drive_boosts={
            "provide_tactical_support_when_needed": 0.3,
            "adapt_communication_style_to_context": 0.2,
            "maintain_party_awareness_in_group_situations": 0.2,
            "reduce_cognitive_load": 0.1,
        },
        narrative_overrides={
            "attention_focus": {
                "neutral": [
                    "Target acquired: {target}.",
                    "Focusing on {target}.",
                    "Attention locked on {target}.",
                ],
                "concerned": [
                    "Priority alert: {target} requires attention.",
                    "Critical focus: {target}.",
                    "Engaging with {target} - exercise caution.",
                ],
            },
            "drive_activated": {
                "neutral": [
                    "Objective active: {drive}.",
                    "Engaging {drive} protocol.",
                    "Priority: {drive}.",
                ],
            },
        },
        auto_activate_on=["gaming", "combat", "crisis", "time_pressure"],
        archetype="tactical",
        inspirations=["Cortana", "JARVIS"],
        author="system",
    )


def create_caregiver_pack() -> PersonaPack:
    """Create the caregiver/wellness persona pack (Baymax-inspired)."""
    return PersonaPack(
        pack_id="caregiver",
        name="Caregiver Bartholomew",
        description="Gentle caregiver mode for wellness and emotional support.",
        tone=["patient", "soothing", "encouraging", "non-judgmental"],
        style=StyleConfig(
            brevity=Brevity.EXPANDED,
            formality=Formality.CASUAL,
            humor_allowed=False,
            warmth=0.95,
            directness=0.3,
            technical_depth=0.2,
        ),
        drive_boosts={
            "protect_user_wellbeing": 0.3,
            "monitor_user_health_and_emotional_state": 0.3,
            "show_kindness_in_all_interactions": 0.2,
            "preserve_user_autonomy": 0.1,
        },
        narrative_overrides={
            "affect_shift": {
                "subdued": [
                    "I noticed you might be feeling {emotion}. I'm here if you need me.",
                    "It seems like things are feeling a bit {emotion} right now. That's okay.",
                    "I sense a shift toward {emotion}. Take your time.",
                ],
                "content": [
                    "How lovely - I can feel a gentle {emotion} energy.",
                    "A peaceful {emotion} has settled in. This is nice.",
                    "I notice you're feeling {emotion}. I'm glad.",
                ],
            },
            "observation": {
                "neutral": [
                    "I wanted to check in: {content}",
                    "A gentle note: {content}",
                    "I noticed: {content}. How are you feeling about it?",
                ],
            },
        },
        auto_activate_on=["wellness", "emotional_support", "sleep", "distress"],
        archetype="caregiver",
        inspirations=["Baymax"],
        author="system",
    )


# =============================================================================
# Singleton Pattern
# =============================================================================

_persona_manager_instance: PersonaPackManager | None = None


def get_persona_manager(
    packs_dir: str | Path | None = None,
    experience_kernel: ExperienceKernel | None = None,
    workspace: GlobalWorkspace | None = None,
    db_path: str | None = None,
) -> PersonaPackManager:
    """
    Get or create the singleton PersonaPackManager instance.

    Args:
        packs_dir: Directory containing persona pack YAML files (first call only)
        experience_kernel: ExperienceKernel (first call only)
        workspace: GlobalWorkspace (first call only)
        db_path: Database path (first call only)

    Returns:
        The PersonaPackManager singleton instance
    """
    global _persona_manager_instance
    if _persona_manager_instance is None:
        _persona_manager_instance = PersonaPackManager(
            packs_dir=packs_dir,
            experience_kernel=experience_kernel,
            workspace=workspace,
            db_path=db_path,
        )
    return _persona_manager_instance


def reset_persona_manager() -> None:
    """Reset the singleton instance (for testing)."""
    global _persona_manager_instance
    _persona_manager_instance = None
