"""
Global Workspace
----------------
Channel-based broadcast system for inter-module communication.

The Global Workspace allows kernel components to publish state changes
and other modules (narrator, persona, daemon) to subscribe and react.

Stage 3.2: Global Workspace Implementation
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# =============================================================================
# Event Types
# =============================================================================


class EventType(Enum):
    """Standard event types for the Global Workspace."""

    # Experience Kernel events
    AFFECT_CHANGED = "affect_changed"
    ATTENTION_CHANGED = "attention_changed"
    DRIVE_ACTIVATED = "drive_activated"
    DRIVE_SATISFIED = "drive_satisfied"
    GOAL_ADDED = "goal_added"
    GOAL_COMPLETED = "goal_completed"
    CONTEXT_CHANGED = "context_changed"
    SNAPSHOT_PERSISTED = "snapshot_persisted"

    # System events
    SYSTEM_STARTUP = "system_startup"
    SYSTEM_SHUTDOWN = "system_shutdown"
    SYSTEM_ERROR = "system_error"
    SYSTEM_EVENT = "system_event"

    # Memory events
    MEMORY_STORED = "memory_stored"
    MEMORY_RETRIEVED = "memory_retrieved"

    # Persona events
    PERSONA_SWITCHED = "persona_switched"

    # Custom/extension events
    CUSTOM = "custom"


# =============================================================================
# Workspace Event
# =============================================================================


@dataclass
class WorkspaceEvent:
    """
    Typed event for the Global Workspace.

    All events flowing through the workspace are instances of this class,
    providing consistent structure and metadata.
    """

    event_id: str
    """Unique identifier for this event"""

    event_type: EventType
    """Type of event (determines which channel it routes to)"""

    channel: str
    """Channel name this event is published to"""

    timestamp: datetime
    """When this event was created"""

    source: str
    """Module/component that emitted this event"""

    payload: dict[str, Any]
    """Event-specific data"""

    metadata: dict[str, Any] = field(default_factory=dict)
    """Additional metadata (correlation IDs, causality chains, etc.)"""

    @classmethod
    def create(
        cls,
        event_type: EventType,
        channel: str,
        source: str,
        payload: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> WorkspaceEvent:
        """Factory method to create a new event with auto-generated ID and timestamp."""
        return cls(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            channel=channel,
            timestamp=datetime.now(timezone.utc),
            source=source,
            payload=payload,
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for JSON storage or transmission."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "channel": self.channel,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
            "payload": self.payload,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkspaceEvent:
        """Deserialize from dictionary."""
        return cls(
            event_id=data["event_id"],
            event_type=EventType(data["event_type"]),
            channel=data["channel"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            source=data["source"],
            payload=data.get("payload", {}),
            metadata=data.get("metadata", {}),
        )


# =============================================================================
# Subscription
# =============================================================================


@dataclass
class Subscription:
    """
    Represents a subscription to a channel.

    Subscriptions can have optional filters to receive only specific events.
    """

    subscription_id: str
    """Unique identifier for this subscription"""

    channel: str
    """Channel being subscribed to"""

    callback: Callable[[WorkspaceEvent], None]
    """Sync callback to invoke when an event is published"""

    async_callback: Callable[[WorkspaceEvent], Any] | None = None
    """Optional async callback for async subscribers"""

    filter_fn: Callable[[WorkspaceEvent], bool] | None = None
    """Optional filter function - event is delivered only if this returns True"""

    source: str = "anonymous"
    """Identifier of the subscriber"""

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    """When this subscription was created"""

    def matches(self, event: WorkspaceEvent) -> bool:
        """Check if this subscription should receive the given event."""
        if self.filter_fn is None:
            return True
        return self.filter_fn(event)


# =============================================================================
# Global Workspace
# =============================================================================


class GlobalWorkspace:
    """
    Channel-based broadcast system for inter-module communication.

    The Global Workspace provides:
    - Named channels for event routing
    - Multi-subscriber support per channel
    - Event history with bounded retention
    - Optional filters per subscription
    - Sync and async broadcast options
    """

    # Default channels for Experience Kernel integration
    DEFAULT_CHANNELS = [
        "affect",
        "attention",
        "drives",
        "goals",
        "context",
        "snapshots",
        "system",
        "memory",
        "persona",
        "working_memory",
    ]

    def __init__(
        self,
        history_size: int = 100,
        auto_create_channels: bool = True,
    ):
        """
        Initialize the Global Workspace.

        Args:
            history_size: Maximum number of events to retain per channel
            auto_create_channels: If True, create channels on first publish
        """
        self._history_size = history_size
        self._auto_create_channels = auto_create_channels

        # Channel -> list of subscriptions
        self._subscriptions: dict[str, list[Subscription]] = defaultdict(list)

        # Channel -> event history (bounded deque)
        self._history: dict[str, deque[WorkspaceEvent]] = defaultdict(
            lambda: deque(maxlen=history_size),
        )

        # All events history (for global queries)
        self._all_events: deque[WorkspaceEvent] = deque(maxlen=history_size * 2)

        # Track known channels
        self._channels: set[str] = set(self.DEFAULT_CHANNELS)

        # Event loop for async operations
        self._loop: asyncio.AbstractEventLoop | None = None

    # =========================================================================
    # Channel Management
    # =========================================================================

    def create_channel(self, channel: str) -> None:
        """
        Create a new channel.

        Args:
            channel: Name of the channel to create
        """
        self._channels.add(channel)
        if channel not in self._history:
            self._history[channel] = deque(maxlen=self._history_size)

    def get_channels(self) -> list[str]:
        """Return list of all known channels."""
        return sorted(self._channels)

    def channel_exists(self, channel: str) -> bool:
        """Check if a channel exists."""
        return channel in self._channels

    # =========================================================================
    # Subscription Management
    # =========================================================================

    def subscribe(
        self,
        channel: str,
        callback: Callable[[WorkspaceEvent], None],
        async_callback: Callable[[WorkspaceEvent], Any] | None = None,
        filter_fn: Callable[[WorkspaceEvent], bool] | None = None,
        source: str = "anonymous",
    ) -> str:
        """
        Subscribe to a channel.

        Args:
            channel: Channel to subscribe to
            callback: Sync callback to invoke on events
            async_callback: Optional async callback for async handling
            filter_fn: Optional filter - only events passing this are delivered
            source: Identifier of the subscriber

        Returns:
            Subscription ID that can be used to unsubscribe
        """
        if not self.channel_exists(channel) and self._auto_create_channels:
            self.create_channel(channel)

        subscription = Subscription(
            subscription_id=str(uuid.uuid4()),
            channel=channel,
            callback=callback,
            async_callback=async_callback,
            filter_fn=filter_fn,
            source=source,
        )

        self._subscriptions[channel].append(subscription)
        return subscription.subscription_id

    def unsubscribe(self, subscription_id: str) -> bool:
        """
        Remove a subscription.

        Args:
            subscription_id: ID of the subscription to remove

        Returns:
            True if subscription was found and removed, False otherwise
        """
        for _channel, subs in self._subscriptions.items():
            for sub in subs:
                if sub.subscription_id == subscription_id:
                    subs.remove(sub)
                    return True
        return False

    def get_subscription_count(self, channel: str) -> int:
        """Return the number of subscribers for a channel."""
        return len(self._subscriptions.get(channel, []))

    def get_all_subscriptions(self) -> dict[str, int]:
        """Return subscription counts for all channels."""
        return {ch: len(subs) for ch, subs in self._subscriptions.items() if subs}

    # =========================================================================
    # Publishing
    # =========================================================================

    def publish(
        self,
        channel: str,
        event_type: EventType,
        source: str,
        payload: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> WorkspaceEvent:
        """
        Publish an event to a channel (synchronous).

        Args:
            channel: Channel to publish to
            event_type: Type of event
            source: Module/component emitting the event
            payload: Event-specific data
            metadata: Additional metadata

        Returns:
            The created WorkspaceEvent
        """
        if not self.channel_exists(channel) and self._auto_create_channels:
            self.create_channel(channel)

        event = WorkspaceEvent.create(
            event_type=event_type,
            channel=channel,
            source=source,
            payload=payload,
            metadata=metadata,
        )

        # Store in history
        self._history[channel].append(event)
        self._all_events.append(event)

        # Notify subscribers
        for sub in self._subscriptions.get(channel, []):
            if sub.matches(event):
                try:
                    sub.callback(event)
                except Exception:
                    # Log but don't fail on subscriber errors
                    pass

        return event

    async def publish_async(
        self,
        channel: str,
        event_type: EventType,
        source: str,
        payload: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> WorkspaceEvent:
        """
        Publish an event to a channel (asynchronous).

        Args:
            channel: Channel to publish to
            event_type: Type of event
            source: Module/component emitting the event
            payload: Event-specific data
            metadata: Additional metadata

        Returns:
            The created WorkspaceEvent
        """
        if not self.channel_exists(channel) and self._auto_create_channels:
            self.create_channel(channel)

        event = WorkspaceEvent.create(
            event_type=event_type,
            channel=channel,
            source=source,
            payload=payload,
            metadata=metadata,
        )

        # Store in history
        self._history[channel].append(event)
        self._all_events.append(event)

        # Notify subscribers (async callbacks)
        for sub in self._subscriptions.get(channel, []):
            if sub.matches(event):
                try:
                    if sub.async_callback:
                        await sub.async_callback(event)
                    else:
                        sub.callback(event)
                except Exception:
                    # Log but don't fail on subscriber errors
                    pass

        return event

    def publish_event(self, event: WorkspaceEvent) -> WorkspaceEvent:
        """
        Publish a pre-created event to its channel (synchronous).

        Args:
            event: The event to publish

        Returns:
            The event (same instance)
        """
        channel = event.channel

        if not self.channel_exists(channel) and self._auto_create_channels:
            self.create_channel(channel)

        # Store in history
        self._history[channel].append(event)
        self._all_events.append(event)

        # Notify subscribers
        for sub in self._subscriptions.get(channel, []):
            if sub.matches(event):
                try:
                    sub.callback(event)
                except Exception:
                    pass

        return event

    # =========================================================================
    # History Retrieval
    # =========================================================================

    def get_history(
        self,
        channel: str,
        limit: int | None = None,
        event_type: EventType | None = None,
        since: datetime | None = None,
    ) -> list[WorkspaceEvent]:
        """
        Get event history for a channel.

        Args:
            channel: Channel to get history for
            limit: Maximum number of events to return
            event_type: Optional filter by event type
            since: Optional filter by timestamp

        Returns:
            List of events, most recent first
        """
        events = list(self._history.get(channel, []))

        # Apply filters
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        if since:
            events = [e for e in events if e.timestamp >= since]

        # Sort by timestamp descending (most recent first)
        events.sort(key=lambda e: e.timestamp, reverse=True)

        # Apply limit
        if limit:
            events = events[:limit]

        return events

    def get_all_history(
        self,
        limit: int | None = None,
        event_type: EventType | None = None,
        since: datetime | None = None,
    ) -> list[WorkspaceEvent]:
        """
        Get event history across all channels.

        Args:
            limit: Maximum number of events to return
            event_type: Optional filter by event type
            since: Optional filter by timestamp

        Returns:
            List of events, most recent first
        """
        events = list(self._all_events)

        # Apply filters
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        if since:
            events = [e for e in events if e.timestamp >= since]

        # Sort by timestamp descending
        events.sort(key=lambda e: e.timestamp, reverse=True)

        # Apply limit
        if limit:
            events = events[:limit]

        return events

    def get_latest_event(self, channel: str) -> WorkspaceEvent | None:
        """Get the most recent event from a channel."""
        history = self._history.get(channel)
        if history:
            return history[-1]
        return None

    def clear_history(self, channel: str | None = None) -> None:
        """
        Clear event history.

        Args:
            channel: Channel to clear, or None to clear all
        """
        if channel:
            if channel in self._history:
                self._history[channel].clear()
        else:
            for ch in self._history:
                self._history[ch].clear()
            self._all_events.clear()

    # =========================================================================
    # Convenience Publishers
    # =========================================================================

    def emit_affect_changed(
        self,
        source: str,
        valence: float,
        arousal: float,
        energy: float,
        emotion: str,
        previous: dict[str, Any] | None = None,
    ) -> WorkspaceEvent:
        """Emit an affect changed event."""
        return self.publish(
            channel="affect",
            event_type=EventType.AFFECT_CHANGED,
            source=source,
            payload={
                "valence": valence,
                "arousal": arousal,
                "energy": energy,
                "emotion": emotion,
                "previous": previous,
            },
        )

    def emit_attention_changed(
        self,
        source: str,
        target: str | None,
        focus_type: str,
        intensity: float,
        tags: list[str],
        previous_target: str | None = None,
    ) -> WorkspaceEvent:
        """Emit an attention changed event."""
        return self.publish(
            channel="attention",
            event_type=EventType.ATTENTION_CHANGED,
            source=source,
            payload={
                "target": target,
                "focus_type": focus_type,
                "intensity": intensity,
                "tags": tags,
                "previous_target": previous_target,
            },
        )

    def emit_drive_activated(
        self,
        source: str,
        drive_id: str,
        activation: float,
        boost: float,
    ) -> WorkspaceEvent:
        """Emit a drive activated event."""
        return self.publish(
            channel="drives",
            event_type=EventType.DRIVE_ACTIVATED,
            source=source,
            payload={
                "drive_id": drive_id,
                "activation": activation,
                "boost": boost,
            },
        )

    def emit_drive_satisfied(
        self,
        source: str,
        drive_id: str,
        activation: float,
    ) -> WorkspaceEvent:
        """Emit a drive satisfied event."""
        return self.publish(
            channel="drives",
            event_type=EventType.DRIVE_SATISFIED,
            source=source,
            payload={
                "drive_id": drive_id,
                "activation": activation,
            },
        )

    def emit_goal_added(
        self,
        source: str,
        goal: str,
        total_goals: int,
    ) -> WorkspaceEvent:
        """Emit a goal added event."""
        return self.publish(
            channel="goals",
            event_type=EventType.GOAL_ADDED,
            source=source,
            payload={
                "goal": goal,
                "total_goals": total_goals,
            },
        )

    def emit_goal_completed(
        self,
        source: str,
        goal: str,
        remaining_goals: int,
    ) -> WorkspaceEvent:
        """Emit a goal completed event."""
        return self.publish(
            channel="goals",
            event_type=EventType.GOAL_COMPLETED,
            source=source,
            payload={
                "goal": goal,
                "remaining_goals": remaining_goals,
            },
        )

    def emit_context_changed(
        self,
        source: str,
        key: str,
        value: Any,
        previous_value: Any = None,
    ) -> WorkspaceEvent:
        """Emit a context changed event."""
        return self.publish(
            channel="context",
            event_type=EventType.CONTEXT_CHANGED,
            source=source,
            payload={
                "key": key,
                "value": value,
                "previous_value": previous_value,
            },
        )

    def emit_snapshot_persisted(
        self,
        source: str,
        snapshot_id: str,
        reason: str,
    ) -> WorkspaceEvent:
        """Emit a snapshot persisted event."""
        return self.publish(
            channel="snapshots",
            event_type=EventType.SNAPSHOT_PERSISTED,
            source=source,
            payload={
                "snapshot_id": snapshot_id,
                "reason": reason,
            },
        )

    def emit_system_event(
        self,
        source: str,
        event_type: EventType,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> WorkspaceEvent:
        """Emit a system event."""
        return self.publish(
            channel="system",
            event_type=event_type,
            source=source,
            payload={
                "message": message,
                "details": details or {},
            },
        )

    def emit_persona_switched(
        self,
        source: str,
        from_pack_id: str | None,
        to_pack_id: str,
        trigger: str,
        context_tags: list[str] | None = None,
    ) -> WorkspaceEvent:
        """Emit a persona switched event."""
        return self.publish(
            channel="persona",
            event_type=EventType.PERSONA_SWITCHED,
            source=source,
            payload={
                "from_pack_id": from_pack_id,
                "to_pack_id": to_pack_id,
                "trigger": trigger,
                "context_tags": context_tags or [],
            },
        )
