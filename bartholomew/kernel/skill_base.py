"""
Skill Base Class
================

Abstract base class that all Bartholomew skills must inherit from.
Part of Stage 4: Skill Registry + Starter Skills.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from .experience_kernel import ExperienceKernel
    from .global_workspace import GlobalWorkspace, WorkspaceEvent
    from .memory_store import MemoryStore
    from .skill_manifest import SkillManifest
    from .working_memory import WorkingMemoryManager

logger = logging.getLogger(__name__)


class SkillState(Enum):
    """Lifecycle state of a skill."""

    UNLOADED = "unloaded"
    LOADING = "loading"
    READY = "ready"
    RUNNING = "running"
    ERROR = "error"
    UNLOADING = "unloading"


class SkillResultStatus(Enum):
    """Result status for skill action execution."""

    SUCCESS = "success"
    ERROR = "error"
    PENDING = "pending"
    CANCELLED = "cancelled"
    PERMISSION_DENIED = "permission_denied"


@dataclass
class SkillResult:
    """
    Result of a skill action execution.

    Returned by SkillBase.execute() to indicate outcome of an action.
    """

    status: SkillResultStatus
    data: Any = None
    message: str = ""
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.status == SkillResultStatus.SUCCESS

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "data": self.data,
            "message": self.message,
            "error": self.error,
            "metadata": self.metadata,
        }

    @classmethod
    def ok(cls, data: Any = None, message: str = "") -> SkillResult:
        """Create a successful result."""
        return cls(
            status=SkillResultStatus.SUCCESS,
            data=data,
            message=message,
        )

    @classmethod
    def fail(cls, error: str, data: Any = None) -> SkillResult:
        """Create a failed result."""
        return cls(
            status=SkillResultStatus.ERROR,
            error=error,
            data=data,
        )

    @classmethod
    def denied(cls, permission: str) -> SkillResult:
        """Create a permission denied result."""
        return cls(
            status=SkillResultStatus.PERMISSION_DENIED,
            error=f"Permission denied: {permission}",
        )


@dataclass
class SkillContext:
    """
    Context provided to skills at initialization and during execution.

    Contains references to kernel components the skill may need.
    """

    # Core kernel reference
    kernel: ExperienceKernel | None = None

    # Event broadcasting
    workspace: GlobalWorkspace | None = None

    # Active context management
    working_memory: WorkingMemoryManager | None = None

    # Memory persistence (optional - requires memory.read/write permission)
    memory_store: MemoryStore | None = None

    # Database path for skill-specific storage
    db_path: str | None = None

    # Skill's own manifest
    manifest: SkillManifest | None = None

    # Permission checker callback
    check_permission: Any | None = None  # Callable[[str], bool]

    def has_permission(self, permission: str) -> bool:
        """Check if the skill has a specific permission."""
        if self.check_permission is None:
            return False
        return self.check_permission(permission)


class SkillBase(ABC):
    """
    Abstract base class for all Bartholomew skills.

    Skills must implement:
    - skill_id property
    - initialize() - called when skill is loaded
    - shutdown() - called when skill is unloaded

    Skills may override:
    - handle_event() - handle GlobalWorkspace events
    - execute() - execute skill actions
    - get_status() - return current skill status
    """

    def __init__(self) -> None:
        self._context: SkillContext | None = None
        self._state: SkillState = SkillState.UNLOADED
        self._loaded_at: datetime | None = None
        self._last_error: str | None = None
        self._subscription_ids: list[str] = []

    @property
    @abstractmethod
    def skill_id(self) -> str:
        """Unique identifier for this skill."""
        ...

    @property
    def state(self) -> SkillState:
        """Current lifecycle state of the skill."""
        return self._state

    @property
    def context(self) -> SkillContext | None:
        """Skill context (available after initialization)."""
        return self._context

    @property
    def is_ready(self) -> bool:
        """Check if skill is ready to execute actions."""
        return self._state == SkillState.READY

    @abstractmethod
    async def initialize(self, context: SkillContext) -> None:
        """
        Initialize the skill with the provided context.

        Called when the skill is loaded by the registry. Skills should:
        - Store the context reference
        - Set up any required database tables
        - Subscribe to workspace events
        - Initialize internal state

        Args:
            context: SkillContext with kernel component references
        """
        ...

    @abstractmethod
    async def shutdown(self) -> None:
        """
        Clean up and shut down the skill.

        Called when the skill is unloaded. Skills should:
        - Unsubscribe from workspace events
        - Close any open resources
        - Persist any pending state
        """
        ...

    async def handle_event(self, event: WorkspaceEvent) -> None:  # noqa: B027
        """
        Handle a GlobalWorkspace event.

        Override this to react to events the skill subscribes to.

        Args:
            event: WorkspaceEvent from a subscribed channel
        """
        pass  # Default: no-op

    async def execute(
        self,
        action: str,
        params: dict[str, Any] | None = None,
    ) -> SkillResult:
        """
        Execute a skill action with parameters.

        Override this to implement skill-specific actions.

        Args:
            action: Name of the action to execute
            params: Parameters for the action

        Returns:
            SkillResult indicating success/failure
        """
        return SkillResult.fail(f"Unknown action: {action}")

    def get_status(self) -> dict[str, Any]:
        """
        Get current status of the skill.

        Override to add skill-specific status information.

        Returns:
            Dictionary with status information
        """
        return {
            "skill_id": self.skill_id,
            "state": self._state.value,
            "loaded_at": (self._loaded_at.isoformat() if self._loaded_at else None),
            "last_error": self._last_error,
        }

    # -------------------------------------------------------------------------
    # Protected helper methods for subclasses
    # -------------------------------------------------------------------------

    def _set_state(self, state: SkillState) -> None:
        """Update skill state."""
        old_state = self._state
        self._state = state
        logger.debug(
            "Skill %s state: %s -> %s",
            self.skill_id,
            old_state.value,
            state.value,
        )

    def _set_error(self, error: str) -> None:
        """Record an error."""
        self._last_error = error
        self._state = SkillState.ERROR
        logger.error("Skill %s error: %s", self.skill_id, error)

    def _subscribe_to_channel(
        self,
        channel: str,
        handler: Any | None = None,
    ) -> str | None:
        """
        Subscribe to a GlobalWorkspace channel.

        Args:
            channel: Channel name to subscribe to
            handler: Optional callback (defaults to self.handle_event)

        Returns:
            Subscription ID or None if workspace unavailable
        """
        if not self._context or not self._context.workspace:
            return None

        callback = handler or self.handle_event
        sub_id = self._context.workspace.subscribe(channel, callback)
        self._subscription_ids.append(sub_id)
        return sub_id

    def _unsubscribe_all(self) -> None:
        """Unsubscribe from all channels."""
        if not self._context or not self._context.workspace:
            return

        for sub_id in self._subscription_ids:
            self._context.workspace.unsubscribe(sub_id)
        self._subscription_ids.clear()

    def _emit_event(
        self,
        channel: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """
        Emit an event to GlobalWorkspace.

        Args:
            channel: Channel to publish to
            event_type: Type of event
            payload: Event payload data
        """
        if not self._context or not self._context.workspace:
            return

        self._context.workspace.publish(
            channel=channel,
            event_type=event_type,
            source=f"skill:{self.skill_id}",
            payload=payload or {},
        )

    def _has_permission(self, permission: str) -> bool:
        """Check if skill has a permission."""
        if not self._context:
            return False
        return self._context.has_permission(permission)

    def _require_permission(self, permission: str) -> SkillResult | None:
        """
        Check permission and return error result if denied.

        Returns:
            SkillResult.denied() if permission missing, None if allowed
        """
        if not self._has_permission(permission):
            return SkillResult.denied(permission)
        return None

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(id={self.skill_id!r}, state={self._state.value})>"
