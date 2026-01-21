"""
Skill Registry
==============

Central registry for managing skill lifecycle: discovery, loading, unloading,
and event routing.

Part of Stage 4: Skill Registry + Starter Skills.
"""

from __future__ import annotations

import importlib
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .skill_base import SkillBase, SkillContext, SkillResult, SkillState
from .skill_manifest import SkillManifest, discover_manifests
from .skill_permissions import PermissionChecker, get_permission_checker


if TYPE_CHECKING:
    from .experience_kernel import ExperienceKernel
    from .global_workspace import GlobalWorkspace, WorkspaceEvent
    from .memory_store import MemoryStore
    from .working_memory import WorkingMemoryManager

logger = logging.getLogger(__name__)


@dataclass
class LoadedSkill:
    """Container for a loaded skill instance."""

    manifest: SkillManifest
    instance: SkillBase
    loaded_at: datetime
    subscription_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.manifest.skill_id,
            "name": self.manifest.name,
            "version": self.manifest.version,
            "state": self.instance.state.value,
            "loaded_at": self.loaded_at.isoformat(),
        }


class SkillRegistry:
    """
    Central registry for skill management.

    Handles:
    - Skill discovery from manifest directory
    - Dynamic loading/unloading of skills
    - Permission enforcement
    - Event routing from GlobalWorkspace to skills
    - Skill state persistence
    """

    # Schema for skill registry state
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS skill_registry_state (
        skill_id TEXT PRIMARY KEY,
        enabled INTEGER NOT NULL DEFAULT 1,
        last_loaded TEXT,
        last_error TEXT,
        config_json TEXT
    );
    """

    def __init__(
        self,
        skills_dir: str | Path = "config/skills",
        db_path: str | None = None,
        workspace: GlobalWorkspace | None = None,
        kernel: ExperienceKernel | None = None,
        working_memory: WorkingMemoryManager | None = None,
        memory_store: MemoryStore | None = None,
        permission_checker: PermissionChecker | None = None,
    ) -> None:
        """
        Initialize skill registry.

        Args:
            skills_dir: Directory containing skill manifest YAML files
            db_path: Path to database for registry state
            workspace: GlobalWorkspace for event routing
            kernel: ExperienceKernel reference
            working_memory: WorkingMemoryManager reference
            memory_store: MemoryStore reference
            permission_checker: Permission checker instance
        """
        self._skills_dir = Path(skills_dir)
        self._db_path = db_path
        self._workspace = workspace
        self._kernel = kernel
        self._working_memory = working_memory
        self._memory_store = memory_store

        # Permission checker
        self._permission_checker = permission_checker or get_permission_checker(db_path=db_path)

        # Discovered manifests (skill_id -> manifest)
        self._manifests: dict[str, SkillManifest] = {}

        # Loaded skill instances (skill_id -> LoadedSkill)
        self._loaded: dict[str, LoadedSkill] = {}

        # Event subscriptions for routing
        self._channel_subscriptions: dict[str, str] = {}

        # Initialize database
        if self._db_path:
            self._init_database()

        # Discover available skills
        self.discover_skills()

    def _init_database(self) -> None:
        """Initialize database schema."""
        if not self._db_path:
            return

        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        try:
            conn.executescript(self.SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection."""
        if not self._db_path:
            raise RuntimeError("No database configured")
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # -------------------------------------------------------------------------
    # Discovery
    # -------------------------------------------------------------------------

    def discover_skills(self) -> list[SkillManifest]:
        """
        Discover all skill manifests in the skills directory.

        Returns:
            List of discovered manifests
        """
        manifests = discover_manifests(self._skills_dir)

        self._manifests.clear()
        for manifest in manifests:
            self._manifests[manifest.skill_id] = manifest
            logger.info(
                "Discovered skill: %s v%s",
                manifest.skill_id,
                manifest.version,
            )

        return manifests

    def get_manifest(self, skill_id: str) -> SkillManifest | None:
        """Get manifest for a skill."""
        return self._manifests.get(skill_id)

    def list_available(self) -> list[str]:
        """List all available skill IDs (discovered but not necessarily loaded)."""
        return list(self._manifests.keys())

    def list_loaded(self) -> list[str]:
        """List all currently loaded skill IDs."""
        return list(self._loaded.keys())

    def get_all_manifests(self) -> list[SkillManifest]:
        """Get all discovered manifests."""
        return list(self._manifests.values())

    # -------------------------------------------------------------------------
    # Loading / Unloading
    # -------------------------------------------------------------------------

    async def load_skill(self, skill_id: str) -> bool:
        """
        Load a skill by ID.

        Args:
            skill_id: ID of skill to load

        Returns:
            True if loaded successfully
        """
        # Check if already loaded
        if skill_id in self._loaded:
            logger.warning("Skill already loaded: %s", skill_id)
            return True

        # Get manifest
        manifest = self._manifests.get(skill_id)
        if not manifest:
            logger.error("Unknown skill: %s", skill_id)
            return False

        # Check if enabled
        if not manifest.enabled:
            logger.info("Skill disabled: %s", skill_id)
            return False

        try:
            # Import module and instantiate class
            instance = self._instantiate_skill(manifest)
            if not instance:
                return False

            # Set up auto-permissions from manifest
            if manifest.permissions.level == "auto":
                self._permission_checker.set_auto_permissions(
                    skill_id,
                    manifest.permissions.requires,
                )

            # Create context
            context = self._create_context(manifest)

            # Initialize skill
            instance._set_state(SkillState.LOADING)
            await instance.initialize(context)
            instance._context = context
            instance._loaded_at = datetime.utcnow()
            instance._set_state(SkillState.READY)

            # Subscribe to events
            subscription_ids = self._setup_subscriptions(manifest, instance)

            # Store loaded skill
            self._loaded[skill_id] = LoadedSkill(
                manifest=manifest,
                instance=instance,
                loaded_at=datetime.utcnow(),
                subscription_ids=subscription_ids,
            )

            # Persist state
            self._persist_skill_state(skill_id, enabled=True)

            logger.info(
                "Loaded skill: %s v%s",
                manifest.skill_id,
                manifest.version,
            )
            return True

        except Exception as e:
            logger.exception("Failed to load skill %s: %s", skill_id, e)
            self._persist_skill_state(skill_id, error=str(e))
            return False

    def _instantiate_skill(self, manifest: SkillManifest) -> SkillBase | None:
        """Dynamically import and instantiate a skill class."""
        try:
            module = importlib.import_module(manifest.entry_module)
            cls = getattr(module, manifest.entry_class)

            if not issubclass(cls, SkillBase):
                logger.error(
                    "%s.%s is not a SkillBase subclass",
                    manifest.entry_module,
                    manifest.entry_class,
                )
                return None

            return cls()
        except ImportError as e:
            logger.error(
                "Failed to import %s: %s",
                manifest.entry_module,
                e,
            )
            return None
        except AttributeError as e:
            logger.error(
                "Class %s not found in %s: %s",
                manifest.entry_class,
                manifest.entry_module,
                e,
            )
            return None

    def _create_context(self, manifest: SkillManifest) -> SkillContext:
        """Create a SkillContext for a skill."""
        skill_id = manifest.skill_id

        def check_permission(permission: str) -> bool:
            result = self._permission_checker.check(skill_id, permission)
            return result.granted

        return SkillContext(
            kernel=self._kernel,
            workspace=self._workspace,
            working_memory=self._working_memory,
            memory_store=self._memory_store,
            db_path=self._db_path,
            manifest=manifest,
            check_permission=check_permission,
        )

    def _setup_subscriptions(
        self,
        manifest: SkillManifest,
        instance: SkillBase,
    ) -> list[str]:
        """Set up GlobalWorkspace subscriptions for a skill."""
        subscription_ids = []

        if not self._workspace:
            return subscription_ids

        for sub in manifest.subscriptions:
            # Create handler that routes events to skill
            async def handler(
                event: WorkspaceEvent,
                skill: SkillBase = instance,
            ) -> None:
                try:
                    await skill.handle_event(event)
                except Exception as e:
                    logger.error(
                        "Skill %s event handler error: %s",
                        skill.skill_id,
                        e,
                    )

            sub_id = self._workspace.subscribe(sub.channel, handler)
            subscription_ids.append(sub_id)
            logger.debug(
                "Skill %s subscribed to channel: %s",
                manifest.skill_id,
                sub.channel,
            )

        return subscription_ids

    async def unload_skill(self, skill_id: str) -> bool:
        """
        Unload a skill.

        Args:
            skill_id: ID of skill to unload

        Returns:
            True if unloaded successfully
        """
        loaded = self._loaded.get(skill_id)
        if not loaded:
            logger.warning("Skill not loaded: %s", skill_id)
            return False

        try:
            # Unsubscribe from events
            if self._workspace:
                for sub_id in loaded.subscription_ids:
                    self._workspace.unsubscribe(sub_id)

            # Shutdown skill
            loaded.instance._set_state(SkillState.UNLOADING)
            await loaded.instance.shutdown()
            loaded.instance._set_state(SkillState.UNLOADED)

            # Clear auto-permissions
            self._permission_checker.clear_auto_permissions(skill_id)

            # Remove from loaded
            del self._loaded[skill_id]

            logger.info("Unloaded skill: %s", skill_id)
            return True

        except Exception as e:
            logger.exception("Failed to unload skill %s: %s", skill_id, e)
            return False

    async def reload_skill(self, skill_id: str) -> bool:
        """
        Reload a skill (unload then load).

        Args:
            skill_id: ID of skill to reload

        Returns:
            True if reloaded successfully
        """
        if skill_id in self._loaded:
            await self.unload_skill(skill_id)

        # Re-discover to pick up manifest changes
        self.discover_skills()

        return await self.load_skill(skill_id)

    # -------------------------------------------------------------------------
    # Execution
    # -------------------------------------------------------------------------

    def get_skill(self, skill_id: str) -> SkillBase | None:
        """Get loaded skill instance."""
        loaded = self._loaded.get(skill_id)
        return loaded.instance if loaded else None

    async def execute_action(
        self,
        skill_id: str,
        action: str,
        params: dict[str, Any] | None = None,
    ) -> SkillResult:
        """
        Execute an action on a loaded skill.

        Args:
            skill_id: ID of skill
            action: Action name to execute
            params: Parameters for the action

        Returns:
            SkillResult from the skill
        """
        loaded = self._loaded.get(skill_id)
        if not loaded:
            return SkillResult.fail(f"Skill not loaded: {skill_id}")

        if not loaded.instance.is_ready:
            return SkillResult.fail(
                f"Skill not ready: {skill_id} (state={loaded.instance.state.value})",
            )

        # Validate action exists
        manifest_action = loaded.manifest.get_action(action)
        if not manifest_action:
            return SkillResult.fail(f"Unknown action: {action} for skill {skill_id}")

        # Execute
        try:
            loaded.instance._set_state(SkillState.RUNNING)
            result = await loaded.instance.execute(action, params or {})
            loaded.instance._set_state(SkillState.READY)
            return result
        except Exception as e:
            logger.exception(
                "Skill %s action %s failed: %s",
                skill_id,
                action,
                e,
            )
            loaded.instance._set_error(str(e))
            return SkillResult.fail(str(e))

    # -------------------------------------------------------------------------
    # Event Routing
    # -------------------------------------------------------------------------

    async def route_event(self, event: WorkspaceEvent) -> None:
        """
        Route a GlobalWorkspace event to subscribed skills.

        Called by GlobalWorkspace when events are published.
        """
        channel = event.channel

        for skill_id, loaded in self._loaded.items():
            # Check if skill subscribes to this channel
            if loaded.manifest.subscribes_to(channel):
                try:
                    await loaded.instance.handle_event(event)
                except Exception as e:
                    logger.error(
                        "Skill %s failed to handle event: %s",
                        skill_id,
                        e,
                    )

    # -------------------------------------------------------------------------
    # Auto-activation
    # -------------------------------------------------------------------------

    async def check_auto_activation(
        self,
        context_tags: list[str],
    ) -> list[str]:
        """
        Check and activate skills based on context tags.

        Args:
            context_tags: Current context tags

        Returns:
            List of skill IDs that were activated
        """
        activated = []

        for skill_id, manifest in self._manifests.items():
            # Skip already loaded
            if skill_id in self._loaded:
                continue

            # Check auto-activation triggers
            for trigger in manifest.auto_activate_on:
                if trigger in context_tags:
                    if await self.load_skill(skill_id):
                        activated.append(skill_id)
                        logger.info(
                            "Auto-activated skill %s (trigger: %s)",
                            skill_id,
                            trigger,
                        )
                    break

        return activated

    # -------------------------------------------------------------------------
    # State Persistence
    # -------------------------------------------------------------------------

    def _persist_skill_state(
        self,
        skill_id: str,
        enabled: bool | None = None,
        error: str | None = None,
    ) -> None:
        """Persist skill state to database."""
        if not self._db_path:
            return

        now = datetime.utcnow().isoformat() + "Z"
        conn = self._get_connection()
        try:
            # Check if exists
            row = conn.execute(
                "SELECT * FROM skill_registry_state WHERE skill_id = ?",
                (skill_id,),
            ).fetchone()

            if row:
                if enabled is not None:
                    conn.execute(
                        """
                        UPDATE skill_registry_state
                        SET enabled = ?, last_loaded = ?
                        WHERE skill_id = ?
                        """,
                        (1 if enabled else 0, now, skill_id),
                    )
                if error:
                    conn.execute(
                        """
                        UPDATE skill_registry_state
                        SET last_error = ?
                        WHERE skill_id = ?
                        """,
                        (error, skill_id),
                    )
            else:
                conn.execute(
                    """
                    INSERT INTO skill_registry_state
                    (skill_id, enabled, last_loaded, last_error)
                    VALUES (?, ?, ?, ?)
                    """,
                    (skill_id, 1 if enabled else 0, now, error),
                )
            conn.commit()
        finally:
            conn.close()

    async def load_enabled_skills(self) -> int:
        """
        Load all skills that were previously enabled.

        Returns:
            Number of skills loaded
        """
        if not self._db_path:
            return 0

        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT skill_id FROM skill_registry_state WHERE enabled = 1",
            ).fetchall()
        finally:
            conn.close()

        loaded = 0
        for row in rows:
            skill_id = row["skill_id"]
            if skill_id in self._manifests:
                if await self.load_skill(skill_id):
                    loaded += 1

        return loaded

    # -------------------------------------------------------------------------
    # Status / Info
    # -------------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """Get registry status summary."""
        return {
            "skills_dir": str(self._skills_dir),
            "discovered": len(self._manifests),
            "loaded": len(self._loaded),
            "available": self.list_available(),
            "loaded_skills": [loaded.to_dict() for loaded in self._loaded.values()],
        }

    def get_skill_info(self, skill_id: str) -> dict[str, Any] | None:
        """Get detailed info about a skill."""
        manifest = self._manifests.get(skill_id)
        if not manifest:
            return None

        info = manifest.to_dict()
        info["is_loaded"] = skill_id in self._loaded

        if skill_id in self._loaded:
            loaded = self._loaded[skill_id]
            info["state"] = loaded.instance.state.value
            info["loaded_at"] = loaded.loaded_at.isoformat()
            info["status"] = loaded.instance.get_status()

        info["permissions"] = self._permission_checker.get_grants(skill_id)

        return info

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def shutdown(self) -> None:
        """Shutdown all loaded skills."""
        for skill_id in list(self._loaded.keys()):
            await self.unload_skill(skill_id)

        logger.info("Skill registry shutdown complete")


# Module-level singleton
_registry: SkillRegistry | None = None


def get_skill_registry(
    skills_dir: str | Path = "config/skills",
    db_path: str | None = None,
    **kwargs: Any,
) -> SkillRegistry:
    """Get or create the global skill registry."""
    global _registry
    if _registry is None:
        _registry = SkillRegistry(
            skills_dir=skills_dir,
            db_path=db_path,
            **kwargs,
        )
    return _registry


def reset_skill_registry() -> None:
    """Reset the global skill registry (for testing)."""
    global _registry
    _registry = None
