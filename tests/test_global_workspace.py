"""
Tests for Global Workspace
--------------------------
Stage 3.2: Global Workspace Implementation

Tests cover:
- Channel creation and management
- Subscription and unsubscription
- Event publishing (sync and async)
- Event history retrieval
- Filter-based subscriptions
- Integration with ExperienceKernel
"""

from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from bartholomew.kernel.global_workspace import (
    EventType,
    GlobalWorkspace,
    Subscription,
    WorkspaceEvent,
)


# =============================================================================
# Test WorkspaceEvent
# =============================================================================


class TestWorkspaceEvent:
    """Tests for WorkspaceEvent dataclass."""

    def test_create_event(self):
        """Test creating an event with factory method."""
        event = WorkspaceEvent.create(
            event_type=EventType.AFFECT_CHANGED,
            channel="affect",
            source="test",
            payload={"valence": 0.5},
        )

        assert event.event_id is not None
        assert event.event_type == EventType.AFFECT_CHANGED
        assert event.channel == "affect"
        assert event.source == "test"
        assert event.payload == {"valence": 0.5}
        assert event.timestamp is not None
        assert event.metadata == {}

    def test_event_with_metadata(self):
        """Test creating an event with metadata."""
        event = WorkspaceEvent.create(
            event_type=EventType.CUSTOM,
            channel="custom",
            source="test",
            payload={"key": "value"},
            metadata={"correlation_id": "abc123"},
        )

        assert event.metadata == {"correlation_id": "abc123"}

    def test_event_to_dict(self):
        """Test serializing event to dictionary."""
        event = WorkspaceEvent.create(
            event_type=EventType.GOAL_ADDED,
            channel="goals",
            source="kernel",
            payload={"goal": "test goal"},
        )

        data = event.to_dict()

        assert data["event_id"] == event.event_id
        assert data["event_type"] == "goal_added"
        assert data["channel"] == "goals"
        assert data["source"] == "kernel"
        assert data["payload"] == {"goal": "test goal"}
        assert "timestamp" in data

    def test_event_from_dict(self):
        """Test deserializing event from dictionary."""
        data = {
            "event_id": "test-id",
            "event_type": "drive_activated",
            "channel": "drives",
            "timestamp": "2026-01-20T12:00:00+00:00",
            "source": "test",
            "payload": {"drive_id": "protect_user"},
            "metadata": {},
        }

        event = WorkspaceEvent.from_dict(data)

        assert event.event_id == "test-id"
        assert event.event_type == EventType.DRIVE_ACTIVATED
        assert event.channel == "drives"
        assert event.source == "test"

    def test_event_roundtrip(self):
        """Test serialization roundtrip."""
        original = WorkspaceEvent.create(
            event_type=EventType.ATTENTION_CHANGED,
            channel="attention",
            source="test",
            payload={"target": "user message"},
            metadata={"session": "123"},
        )

        data = original.to_dict()
        restored = WorkspaceEvent.from_dict(data)

        assert restored.event_id == original.event_id
        assert restored.event_type == original.event_type
        assert restored.channel == original.channel
        assert restored.source == original.source
        assert restored.payload == original.payload
        assert restored.metadata == original.metadata


# =============================================================================
# Test Subscription
# =============================================================================


class TestSubscription:
    """Tests for Subscription dataclass."""

    def test_subscription_creation(self):
        """Test creating a subscription."""

        def callback(e):
            pass

        sub = Subscription(
            subscription_id="sub-1",
            channel="affect",
            callback=callback,
            source="test",
        )

        assert sub.subscription_id == "sub-1"
        assert sub.channel == "affect"
        assert sub.callback is callback
        assert sub.filter_fn is None
        assert sub.source == "test"

    def test_subscription_matches_without_filter(self):
        """Test that subscription without filter matches all events."""
        sub = Subscription(
            subscription_id="sub-1",
            channel="affect",
            callback=lambda e: None,
        )

        event = WorkspaceEvent.create(
            event_type=EventType.AFFECT_CHANGED,
            channel="affect",
            source="test",
            payload={},
        )

        assert sub.matches(event) is True

    def test_subscription_matches_with_filter(self):
        """Test that subscription with filter only matches filtered events."""
        sub = Subscription(
            subscription_id="sub-1",
            channel="affect",
            callback=lambda e: None,
            filter_fn=lambda e: e.payload.get("valence", 0) > 0,
        )

        positive_event = WorkspaceEvent.create(
            event_type=EventType.AFFECT_CHANGED,
            channel="affect",
            source="test",
            payload={"valence": 0.5},
        )

        negative_event = WorkspaceEvent.create(
            event_type=EventType.AFFECT_CHANGED,
            channel="affect",
            source="test",
            payload={"valence": -0.5},
        )

        assert sub.matches(positive_event) is True
        assert sub.matches(negative_event) is False


# =============================================================================
# Test GlobalWorkspace - Channel Management
# =============================================================================


class TestGlobalWorkspaceChannels:
    """Tests for GlobalWorkspace channel management."""

    def test_default_channels(self):
        """Test that default channels are created."""
        ws = GlobalWorkspace()
        channels = ws.get_channels()

        assert "affect" in channels
        assert "attention" in channels
        assert "drives" in channels
        assert "goals" in channels
        assert "context" in channels
        assert "snapshots" in channels
        assert "system" in channels

    def test_create_channel(self):
        """Test creating a new channel."""
        ws = GlobalWorkspace()

        ws.create_channel("custom_channel")

        assert ws.channel_exists("custom_channel")
        assert "custom_channel" in ws.get_channels()

    def test_auto_create_channel_on_publish(self):
        """Test that channels are auto-created on first publish."""
        ws = GlobalWorkspace(auto_create_channels=True)

        assert not ws.channel_exists("new_channel")

        ws.publish(
            channel="new_channel",
            event_type=EventType.CUSTOM,
            source="test",
            payload={},
        )

        assert ws.channel_exists("new_channel")

    def test_channel_exists(self):
        """Test checking if a channel exists."""
        ws = GlobalWorkspace()

        assert ws.channel_exists("affect") is True
        assert ws.channel_exists("nonexistent") is False


# =============================================================================
# Test GlobalWorkspace - Subscriptions
# =============================================================================


class TestGlobalWorkspaceSubscriptions:
    """Tests for GlobalWorkspace subscription management."""

    def test_subscribe(self):
        """Test subscribing to a channel."""
        ws = GlobalWorkspace()
        received = []

        sub_id = ws.subscribe(
            channel="affect",
            callback=lambda e: received.append(e),
            source="test",
        )

        assert sub_id is not None
        assert ws.get_subscription_count("affect") == 1

    def test_multiple_subscribers(self):
        """Test multiple subscribers on same channel."""
        ws = GlobalWorkspace()
        received1 = []
        received2 = []

        ws.subscribe("affect", lambda e: received1.append(e))
        ws.subscribe("affect", lambda e: received2.append(e))

        ws.publish(
            channel="affect",
            event_type=EventType.AFFECT_CHANGED,
            source="test",
            payload={"valence": 0.5},
        )

        assert len(received1) == 1
        assert len(received2) == 1
        assert received1[0].event_id == received2[0].event_id

    def test_unsubscribe(self):
        """Test unsubscribing from a channel."""
        ws = GlobalWorkspace()

        sub_id = ws.subscribe("affect", lambda e: None)
        assert ws.get_subscription_count("affect") == 1

        result = ws.unsubscribe(sub_id)
        assert result is True
        assert ws.get_subscription_count("affect") == 0

    def test_unsubscribe_nonexistent(self):
        """Test unsubscribing with invalid ID returns False."""
        ws = GlobalWorkspace()

        result = ws.unsubscribe("nonexistent-id")
        assert result is False

    def test_get_all_subscriptions(self):
        """Test getting subscription counts for all channels."""
        ws = GlobalWorkspace()

        ws.subscribe("affect", lambda e: None)
        ws.subscribe("affect", lambda e: None)
        ws.subscribe("drives", lambda e: None)

        counts = ws.get_all_subscriptions()

        assert counts["affect"] == 2
        assert counts["drives"] == 1


# =============================================================================
# Test GlobalWorkspace - Publishing
# =============================================================================


class TestGlobalWorkspacePublishing:
    """Tests for GlobalWorkspace event publishing."""

    def test_publish_returns_event(self):
        """Test that publish returns the created event."""
        ws = GlobalWorkspace()

        event = ws.publish(
            channel="affect",
            event_type=EventType.AFFECT_CHANGED,
            source="test",
            payload={"valence": 0.5},
        )

        assert event is not None
        assert event.event_type == EventType.AFFECT_CHANGED
        assert event.payload == {"valence": 0.5}

    def test_publish_notifies_subscribers(self):
        """Test that publish notifies all matching subscribers."""
        ws = GlobalWorkspace()
        received = []

        ws.subscribe("affect", lambda e: received.append(e))

        ws.publish(
            channel="affect",
            event_type=EventType.AFFECT_CHANGED,
            source="test",
            payload={"valence": 0.5},
        )

        assert len(received) == 1
        assert received[0].payload == {"valence": 0.5}

    def test_publish_with_filter(self):
        """Test that filters work during publish."""
        ws = GlobalWorkspace()
        received = []

        # Only receive positive valence events
        ws.subscribe(
            "affect",
            lambda e: received.append(e),
            filter_fn=lambda e: e.payload.get("valence", 0) > 0,
        )

        ws.publish(
            channel="affect",
            event_type=EventType.AFFECT_CHANGED,
            source="test",
            payload={"valence": 0.5},
        )
        ws.publish(
            channel="affect",
            event_type=EventType.AFFECT_CHANGED,
            source="test",
            payload={"valence": -0.5},
        )

        assert len(received) == 1
        assert received[0].payload["valence"] == 0.5

    def test_publish_event(self):
        """Test publishing a pre-created event."""
        ws = GlobalWorkspace()
        received = []

        ws.subscribe("affect", lambda e: received.append(e))

        event = WorkspaceEvent.create(
            event_type=EventType.AFFECT_CHANGED,
            channel="affect",
            source="test",
            payload={"valence": 0.8},
        )

        result = ws.publish_event(event)

        assert result is event
        assert len(received) == 1
        assert received[0] is event

    @pytest.mark.asyncio
    async def test_publish_async(self):
        """Test async event publishing."""
        ws = GlobalWorkspace()
        received = []

        async def async_handler(e):
            await asyncio.sleep(0.01)
            received.append(e)

        ws.subscribe(
            "affect",
            lambda e: None,
            async_callback=async_handler,
        )

        await ws.publish_async(
            channel="affect",
            event_type=EventType.AFFECT_CHANGED,
            source="test",
            payload={"valence": 0.5},
        )

        assert len(received) == 1


# =============================================================================
# Test GlobalWorkspace - History
# =============================================================================


class TestGlobalWorkspaceHistory:
    """Tests for GlobalWorkspace event history."""

    def test_history_stores_events(self):
        """Test that events are stored in history."""
        ws = GlobalWorkspace()

        ws.publish(
            channel="affect",
            event_type=EventType.AFFECT_CHANGED,
            source="test",
            payload={"valence": 0.5},
        )

        history = ws.get_history("affect")
        assert len(history) == 1
        assert history[0].payload == {"valence": 0.5}

    def test_history_limit(self):
        """Test that history respects limit parameter."""
        ws = GlobalWorkspace()

        for i in range(5):
            ws.publish(
                channel="affect",
                event_type=EventType.AFFECT_CHANGED,
                source="test",
                payload={"index": i},
            )

        history = ws.get_history("affect", limit=3)
        assert len(history) == 3

    def test_history_filter_by_event_type(self):
        """Test filtering history by event type."""
        ws = GlobalWorkspace()

        ws.publish("drives", EventType.DRIVE_ACTIVATED, "test", {"id": 1})
        ws.publish("drives", EventType.DRIVE_SATISFIED, "test", {"id": 2})
        ws.publish("drives", EventType.DRIVE_ACTIVATED, "test", {"id": 3})

        history = ws.get_history("drives", event_type=EventType.DRIVE_ACTIVATED)

        assert len(history) == 2
        assert all(e.event_type == EventType.DRIVE_ACTIVATED for e in history)

    def test_history_filter_by_since(self):
        """Test filtering history by timestamp."""
        ws = GlobalWorkspace()

        # Publish some events
        ws.publish("affect", EventType.AFFECT_CHANGED, "test", {"v": 1})
        ws.publish("affect", EventType.AFFECT_CHANGED, "test", {"v": 2})

        # Get events since now
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        history = ws.get_history("affect", since=future)

        assert len(history) == 0

    def test_get_all_history(self):
        """Test getting history across all channels."""
        ws = GlobalWorkspace()

        ws.publish("affect", EventType.AFFECT_CHANGED, "test", {"v": 1})
        ws.publish("drives", EventType.DRIVE_ACTIVATED, "test", {"d": 1})

        history = ws.get_all_history()

        assert len(history) >= 2
        channels = {e.channel for e in history}
        assert "affect" in channels
        assert "drives" in channels

    def test_get_latest_event(self):
        """Test getting the most recent event from a channel."""
        ws = GlobalWorkspace()

        ws.publish("affect", EventType.AFFECT_CHANGED, "test", {"v": 1})
        ws.publish("affect", EventType.AFFECT_CHANGED, "test", {"v": 2})
        ws.publish("affect", EventType.AFFECT_CHANGED, "test", {"v": 3})

        latest = ws.get_latest_event("affect")

        assert latest is not None
        assert latest.payload == {"v": 3}

    def test_get_latest_event_empty_channel(self):
        """Test getting latest event from empty channel returns None."""
        ws = GlobalWorkspace()

        latest = ws.get_latest_event("affect")
        assert latest is None

    def test_clear_history_single_channel(self):
        """Test clearing history for a single channel."""
        ws = GlobalWorkspace()

        ws.publish("affect", EventType.AFFECT_CHANGED, "test", {})
        ws.publish("drives", EventType.DRIVE_ACTIVATED, "test", {})

        ws.clear_history("affect")

        assert len(ws.get_history("affect")) == 0
        assert len(ws.get_history("drives")) == 1

    def test_clear_history_all(self):
        """Test clearing all history."""
        ws = GlobalWorkspace()

        ws.publish("affect", EventType.AFFECT_CHANGED, "test", {})
        ws.publish("drives", EventType.DRIVE_ACTIVATED, "test", {})

        ws.clear_history()

        assert len(ws.get_history("affect")) == 0
        assert len(ws.get_history("drives")) == 0
        assert len(ws.get_all_history()) == 0

    def test_history_bounded_size(self):
        """Test that history respects max size."""
        ws = GlobalWorkspace(history_size=5)

        for i in range(10):
            ws.publish("affect", EventType.AFFECT_CHANGED, "test", {"i": i})

        history = ws.get_history("affect")

        assert len(history) == 5
        # Most recent should be last published (i=9)
        assert history[0].payload == {"i": 9}


# =============================================================================
# Test GlobalWorkspace - Convenience Publishers
# =============================================================================


class TestGlobalWorkspaceConveniencePublishers:
    """Tests for GlobalWorkspace convenience emit methods."""

    def test_emit_affect_changed(self):
        """Test emit_affect_changed convenience method."""
        ws = GlobalWorkspace()
        received = []
        ws.subscribe("affect", lambda e: received.append(e))

        ws.emit_affect_changed(
            source="test",
            valence=0.5,
            arousal=0.3,
            energy=0.8,
            emotion="calm",
        )

        assert len(received) == 1
        assert received[0].event_type == EventType.AFFECT_CHANGED
        assert received[0].payload["valence"] == 0.5
        assert received[0].payload["arousal"] == 0.3
        assert received[0].payload["energy"] == 0.8
        assert received[0].payload["emotion"] == "calm"

    def test_emit_attention_changed(self):
        """Test emit_attention_changed convenience method."""
        ws = GlobalWorkspace()
        received = []
        ws.subscribe("attention", lambda e: received.append(e))

        ws.emit_attention_changed(
            source="test",
            target="user message",
            focus_type="user_input",
            intensity=0.8,
            tags=["chat"],
        )

        assert len(received) == 1
        assert received[0].event_type == EventType.ATTENTION_CHANGED
        assert received[0].payload["target"] == "user message"
        assert received[0].payload["focus_type"] == "user_input"

    def test_emit_drive_activated(self):
        """Test emit_drive_activated convenience method."""
        ws = GlobalWorkspace()
        received = []
        ws.subscribe("drives", lambda e: received.append(e))

        ws.emit_drive_activated(
            source="test",
            drive_id="protect_user",
            activation=0.9,
            boost=0.1,
        )

        assert len(received) == 1
        assert received[0].event_type == EventType.DRIVE_ACTIVATED
        assert received[0].payload["drive_id"] == "protect_user"

    def test_emit_goal_added(self):
        """Test emit_goal_added convenience method."""
        ws = GlobalWorkspace()
        received = []
        ws.subscribe("goals", lambda e: received.append(e))

        ws.emit_goal_added(
            source="test",
            goal="help user",
            total_goals=3,
        )

        assert len(received) == 1
        assert received[0].event_type == EventType.GOAL_ADDED
        assert received[0].payload["goal"] == "help user"
        assert received[0].payload["total_goals"] == 3

    def test_emit_snapshot_persisted(self):
        """Test emit_snapshot_persisted convenience method."""
        ws = GlobalWorkspace()
        received = []
        ws.subscribe("snapshots", lambda e: received.append(e))

        ws.emit_snapshot_persisted(
            source="test",
            snapshot_id="snap-123",
            reason="manual",
        )

        assert len(received) == 1
        assert received[0].event_type == EventType.SNAPSHOT_PERSISTED
        assert received[0].payload["snapshot_id"] == "snap-123"

    def test_emit_system_event(self):
        """Test emit_system_event convenience method."""
        ws = GlobalWorkspace()
        received = []
        ws.subscribe("system", lambda e: received.append(e))

        ws.emit_system_event(
            source="test",
            event_type=EventType.SYSTEM_STARTUP,
            message="Kernel started",
            details={"version": "1.0"},
        )

        assert len(received) == 1
        assert received[0].event_type == EventType.SYSTEM_STARTUP
        assert received[0].payload["message"] == "Kernel started"


# =============================================================================
# Test GlobalWorkspace - Integration with ExperienceKernel
# =============================================================================


class TestGlobalWorkspaceIntegration:
    """Tests for GlobalWorkspace integration with ExperienceKernel."""

    def test_experience_kernel_with_workspace(self):
        """Test ExperienceKernel emits events to workspace."""
        from bartholomew.kernel.experience_kernel import ExperienceKernel

        ws = GlobalWorkspace()
        received = []

        ws.subscribe("affect", lambda e: received.append(e))

        kernel = ExperienceKernel(workspace=ws)
        kernel.update_affect(valence=0.8, emotion="happy")

        assert len(received) == 1
        assert received[0].event_type == EventType.AFFECT_CHANGED
        assert received[0].payload["valence"] == 0.8
        assert received[0].payload["emotion"] == "happy"

    def test_kernel_attention_events(self):
        """Test ExperienceKernel emits attention events."""
        from bartholomew.kernel.experience_kernel import ExperienceKernel

        ws = GlobalWorkspace()
        received = []

        ws.subscribe("attention", lambda e: received.append(e))

        kernel = ExperienceKernel(workspace=ws)
        kernel.set_attention("user message", "user_input", 0.9, ["chat"])

        assert len(received) == 1
        assert received[0].payload["target"] == "user message"
        assert received[0].payload["focus_type"] == "user_input"

    def test_kernel_drive_events(self):
        """Test ExperienceKernel emits drive events."""
        from bartholomew.kernel.experience_kernel import ExperienceKernel

        ws = GlobalWorkspace()
        received = []

        ws.subscribe("drives", lambda e: received.append(e))

        kernel = ExperienceKernel(workspace=ws)
        kernel.activate_drive("protect_user_wellbeing", boost=0.2)
        kernel.satisfy_drive("protect_user_wellbeing")

        assert len(received) == 2
        assert received[0].event_type == EventType.DRIVE_ACTIVATED
        assert received[1].event_type == EventType.DRIVE_SATISFIED

    def test_kernel_goal_events(self):
        """Test ExperienceKernel emits goal events."""
        from bartholomew.kernel.experience_kernel import ExperienceKernel

        ws = GlobalWorkspace()
        received = []

        ws.subscribe("goals", lambda e: received.append(e))

        kernel = ExperienceKernel(workspace=ws)
        kernel.add_goal("help user")
        kernel.complete_goal("help user")

        assert len(received) == 2
        assert received[0].event_type == EventType.GOAL_ADDED
        assert received[1].event_type == EventType.GOAL_COMPLETED

    def test_kernel_snapshot_events(self):
        """Test ExperienceKernel emits snapshot events."""
        from bartholomew.kernel.experience_kernel import ExperienceKernel

        ws = GlobalWorkspace()
        received = []

        ws.subscribe("snapshots", lambda e: received.append(e))

        # Use temp file DB to avoid :memory: connection issues
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        kernel = ExperienceKernel(workspace=ws, db_path=db_path)
        kernel.persist_snapshot(reason="test")

        assert len(received) == 1
        assert received[0].event_type == EventType.SNAPSHOT_PERSISTED
        assert received[0].payload["reason"] == "test"
        # Note: temp file cleanup skipped due to Windows file locking

    def test_kernel_without_workspace(self):
        """Test ExperienceKernel works without workspace."""
        from bartholomew.kernel.experience_kernel import ExperienceKernel

        # Should not raise
        kernel = ExperienceKernel()
        kernel.update_affect(valence=0.5)
        kernel.set_attention("test", "task")
        kernel.add_goal("test")
        kernel.complete_goal("test")

    def test_full_lifecycle_with_workspace(self):
        """Test full ExperienceKernel lifecycle with workspace events."""
        from bartholomew.kernel.experience_kernel import ExperienceKernel

        ws = GlobalWorkspace()
        all_events = []

        # Subscribe to all channels
        for channel in ws.get_channels():
            ws.subscribe(channel, lambda e: all_events.append(e))

        # Use temp file DB for snapshot persistence
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        kernel = ExperienceKernel(workspace=ws, db_path=db_path)

        # Perform various operations
        kernel.update_affect(valence=0.8, arousal=0.5, energy=0.9, emotion="focused")
        kernel.set_attention("code review", "task", 0.9, ["coding"])
        kernel.add_goal("complete review")
        kernel.activate_drive("be_helpful_without_manipulation")
        kernel.persist_snapshot("lifecycle_test")

        # Should have events from: affect, attention, goals, drives, snapshots
        event_types = {e.event_type for e in all_events}

        assert EventType.AFFECT_CHANGED in event_types
        assert EventType.ATTENTION_CHANGED in event_types
        assert EventType.GOAL_ADDED in event_types
        assert EventType.DRIVE_ACTIVATED in event_types
        assert EventType.SNAPSHOT_PERSISTED in event_types
        # Note: temp file cleanup skipped due to Windows file locking
