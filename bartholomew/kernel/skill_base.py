"""
Skill Base Class
================

Abstract base class that all Bartholomew skills must inherit from.
Part of Stage 4: Skill Registry + Starter Skills.
"""

from __future__ import annotations

import asyncio
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
    # Stage 5, S5.5: the action was not actually executed -- Governance
    # evaluated it for real, but the call was (or was forced into)
    # dry-run mode, so the capability's own execute() was never invoked.
    # Distinct from SUCCESS (nothing really happened) and from ERROR/
    # PERMISSION_DENIED (Governance did not deny this -- it would have
    # run, had this not been a simulation). `.success` deliberately stays
    # False for this status: no caller checking `.success` can mistake a
    # dry run for a completed action. See
    # docs/S5_5_DRY_RUN_MODE_DESIGN.md Sec 8.
    DRY_RUN = "dry_run"


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

    @classmethod
    def dry_run(cls, data: Any = None, message: str = "") -> SkillResult:
        """Create a dry-run result (Stage 5, S5.5) -- the action was
        simulated, not executed. See SkillResultStatus.DRY_RUN's docstring."""
        return cls(
            status=SkillResultStatus.DRY_RUN,
            data=data,
            message=message,
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
        self._subscribed_channels: set[str] = set()

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

        # A skill loaded through SkillRegistry reaches this channel twice:
        # once here (skills self-subscribe in their own initialize()) and
        # once via SkillRegistry._setup_subscriptions() for the channels the
        # skill's manifest declares -- and all three bundled skills do both
        # for identical channels. Neither path sets a filter_fn, so both
        # subscriptions match every event on the channel and handle_event
        # would run twice per event. Keep the first registration per channel
        # and ignore repeats of the default handler. An explicit handler is
        # a deliberate second consumer, so it is never deduplicated.
        if handler is None and channel in self._subscribed_channels:
            logger.debug(
                "Skill %s already subscribed to channel %s; skipping duplicate",
                self.skill_id,
                channel,
            )
            return None

        callback: Any = handler or self.handle_event

        if asyncio.iscoroutinefunction(callback):
            # GlobalWorkspace.publish() (the sync path used by most of the
            # kernel, e.g. daemon.py/working_memory.py) only ever invokes the
            # sync `callback` slot -- it never awaits `async_callback`.
            # Passing a bare async handler as `callback` created the
            # coroutine but never ran it (silent no-op event routing).
            # Schedule it on the running loop instead so the sync path still
            # executes the handler; publish_async() continues to use
            # `async_callback` directly. Mirrors
            # SkillRegistry._setup_subscriptions.
            async_callback = callback

            async def _run_handler(
                event: WorkspaceEvent,
                _async_callback: Any = async_callback,
            ) -> None:
                try:
                    await _async_callback(event)
                except Exception as e:
                    logger.error(
                        "Skill %s event handler error: %s",
                        self.skill_id,
                        e,
                    )

            def sync_callback(
                event: WorkspaceEvent,
                _run_handler: Any = _run_handler,
            ) -> None:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    logger.warning(
                        "Skill %s event dropped: no running event loop",
                        self.skill_id,
                    )
                    return
                loop.create_task(_run_handler(event))

            sub_id = self._context.workspace.subscribe(
                channel,
                sync_callback,
                async_callback=async_callback,
            )
        else:
            sub_id = self._context.workspace.subscribe(channel, callback)

        self._subscription_ids.append(sub_id)
        self._subscribed_channels.add(channel)
        return sub_id

    def _unsubscribe_all(self) -> None:
        """Unsubscribe from all channels."""
        if not self._context or not self._context.workspace:
            return

        for sub_id in self._subscription_ids:
            self._context.workspace.unsubscribe(sub_id)
        self._subscription_ids.clear()
        # Cleared so an unload/reload cycle can resubscribe: the duplicate
        # guard in _subscribe_to_channel() keys off this set.
        self._subscribed_channels.clear()

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
