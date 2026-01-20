"""
Tests for Working Memory Manager (Stage 3.3)

Comprehensive test coverage for the token-bounded working memory system.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pytest

from bartholomew.kernel.working_memory import (
    ItemSource,
    OverflowPolicy,
    WorkingMemoryItem,
    WorkingMemoryManager,
    get_working_memory,
    reset_working_memory,
)


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def wm():
    """Fresh working memory manager for each test."""
    return WorkingMemoryManager(token_budget=100)


@pytest.fixture
def wm_small():
    """Small budget working memory for overflow tests."""
    return WorkingMemoryManager(token_budget=20)


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset singleton before and after each test."""
    reset_working_memory()
    yield
    reset_working_memory()


# =============================================================================
# WorkingMemoryItem Tests
# =============================================================================


class TestWorkingMemoryItem:
    """Tests for the WorkingMemoryItem dataclass."""

    def test_item_creation(self):
        """Test basic item creation."""
        item = WorkingMemoryItem(
            item_id="test-123",
            content="Hello world",
            source="user_input",
            token_count=2,
        )
        assert item.item_id == "test-123"
        assert item.content == "Hello world"
        assert item.source == "user_input"
        assert item.token_count == 2
        assert item.priority == 0.5  # default

    def test_item_with_all_fields(self):
        """Test item creation with all fields."""
        now = datetime.now(timezone.utc)
        item = WorkingMemoryItem(
            item_id="test-456",
            content="Test content",
            source="memory_retrieval",
            token_count=10,
            priority=0.8,
            relevance_tags=["gaming", "wellness"],
            added_at=now,
            last_accessed=now,
            metadata={"memory_id": "mem-123"},
        )
        assert item.priority == 0.8
        assert item.relevance_tags == ["gaming", "wellness"]
        assert item.metadata == {"memory_id": "mem-123"}

    def test_item_to_dict(self):
        """Test serialization to dictionary."""
        item = WorkingMemoryItem(
            item_id="test-789",
            content="Serialize me",
            source="system",
            token_count=2,
            priority=0.7,
            relevance_tags=["test"],
        )
        data = item.to_dict()
        assert data["item_id"] == "test-789"
        assert data["content"] == "Serialize me"
        assert data["source"] == "system"
        assert data["token_count"] == 2
        assert data["priority"] == 0.7
        assert data["relevance_tags"] == ["test"]
        assert "added_at" in data
        assert "last_accessed" in data

    def test_item_from_dict(self):
        """Test deserialization from dictionary."""
        now = datetime.now(timezone.utc)
        data = {
            "item_id": "from-dict-123",
            "content": "Deserialized content",
            "source": "external",
            "token_count": 5,
            "priority": 0.9,
            "relevance_tags": ["important"],
            "added_at": now.isoformat(),
            "last_accessed": now.isoformat(),
            "metadata": {"key": "value"},
        }
        item = WorkingMemoryItem.from_dict(data)
        assert item.item_id == "from-dict-123"
        assert item.content == "Deserialized content"
        assert item.source == "external"
        assert item.token_count == 5
        assert item.priority == 0.9
        assert item.relevance_tags == ["important"]
        assert item.metadata == {"key": "value"}

    def test_item_serialization_roundtrip(self):
        """Test that to_dict/from_dict roundtrip preserves data."""
        original = WorkingMemoryItem(
            item_id="roundtrip-test",
            content="Test roundtrip",
            source="user_input",
            token_count=3,
            priority=0.75,
            relevance_tags=["a", "b"],
            metadata={"nested": {"key": "value"}},
        )
        data = original.to_dict()
        restored = WorkingMemoryItem.from_dict(data)
        assert restored.item_id == original.item_id
        assert restored.content == original.content
        assert restored.source == original.source
        assert restored.token_count == original.token_count
        assert restored.priority == original.priority
        assert restored.relevance_tags == original.relevance_tags
        assert restored.metadata == original.metadata


# =============================================================================
# Token Counting Tests
# =============================================================================


class TestTokenCounting:
    """Tests for token counting functionality."""

    def test_count_empty_string(self):
        """Empty string should have 0 tokens."""
        assert WorkingMemoryManager.count_tokens("") == 0
        assert WorkingMemoryManager.count_tokens("   ") == 0

    def test_count_single_word(self):
        """Single word should be 1 token."""
        assert WorkingMemoryManager.count_tokens("hello") == 1

    def test_count_multiple_words(self):
        """Multiple words counted correctly."""
        assert WorkingMemoryManager.count_tokens("hello world") == 2
        assert WorkingMemoryManager.count_tokens("one two three four") == 4

    def test_count_with_punctuation(self):
        """Punctuation attached to words counts as part of token."""
        assert WorkingMemoryManager.count_tokens("Hello, world!") == 2

    def test_count_multiline(self):
        """Multiline text counted correctly."""
        text = "Line one\nLine two\nLine three"
        assert WorkingMemoryManager.count_tokens(text) == 6

    def test_count_none(self):
        """None should return 0."""
        assert WorkingMemoryManager.count_tokens(None) == 0


# =============================================================================
# Basic Operations Tests
# =============================================================================


class TestBasicOperations:
    """Tests for basic add/remove/get operations."""

    def test_add_item(self, wm):
        """Test adding an item."""
        item = wm.add("Hello world", source="user_input")
        assert item.content == "Hello world"
        assert item.source == "user_input"
        assert item.token_count == 2
        assert wm.item_count == 1

    def test_add_item_with_priority(self, wm):
        """Test adding with custom priority."""
        item = wm.add("High priority", priority=0.9)
        assert item.priority == 0.9

    def test_add_item_with_tags(self, wm):
        """Test adding with relevance tags."""
        item = wm.add("Tagged content", tags=["gaming", "chat"])
        assert item.relevance_tags == ["gaming", "chat"]

    def test_add_item_with_metadata(self, wm):
        """Test adding with metadata."""
        item = wm.add("With metadata", metadata={"memory_id": "123"})
        assert item.metadata == {"memory_id": "123"}

    def test_add_clamps_priority(self, wm):
        """Priority should be clamped to valid range."""
        item1 = wm.add("Too high", priority=1.5)
        assert item1.priority == 1.0

        item2 = wm.add("Too low", priority=-0.5)
        assert item2.priority == WorkingMemoryManager.MIN_PRIORITY

    def test_remove_item(self, wm):
        """Test removing an item."""
        item = wm.add("To remove")
        assert wm.item_count == 1

        result = wm.remove(item.item_id)
        assert result is True
        assert wm.item_count == 0

    def test_remove_nonexistent(self, wm):
        """Removing nonexistent item returns False."""
        result = wm.remove("nonexistent-id")
        assert result is False

    def test_get_item(self, wm):
        """Test getting an item by ID."""
        item = wm.add("Get me")
        retrieved = wm.get(item.item_id)
        assert retrieved is not None
        assert retrieved.content == "Get me"

    def test_get_nonexistent(self, wm):
        """Getting nonexistent item returns None."""
        result = wm.get("nonexistent-id")
        assert result is None

    def test_access_updates_last_accessed(self, wm):
        """access() should update last_accessed timestamp."""
        item = wm.add("Access me")
        original_time = item.last_accessed

        time.sleep(0.01)  # Small delay
        accessed = wm.access(item.item_id)

        assert accessed is not None
        assert accessed.last_accessed > original_time

    def test_clear(self, wm):
        """Test clearing all items."""
        wm.add("Item 1")
        wm.add("Item 2")
        wm.add("Item 3")
        assert wm.item_count == 3

        wm.clear()
        assert wm.item_count == 0
        assert wm.is_empty


# =============================================================================
# Query & Retrieval Tests
# =============================================================================


class TestQueryRetrieval:
    """Tests for query and retrieval methods."""

    def test_get_all_sorted_by_priority(self, wm):
        """get_all() returns items sorted by priority descending."""
        wm.add("Low", priority=0.3)
        wm.add("High", priority=0.9)
        wm.add("Medium", priority=0.6)

        items = wm.get_all()
        assert len(items) == 3
        assert items[0].priority == 0.9
        assert items[1].priority == 0.6
        assert items[2].priority == 0.3

    def test_get_by_tags_single_match(self, wm):
        """get_by_tags finds items with matching tags."""
        wm.add("Gaming stuff", tags=["gaming"])
        wm.add("Work stuff", tags=["work"])
        wm.add("Both", tags=["gaming", "work"])

        gaming = wm.get_by_tags(["gaming"])
        assert len(gaming) == 2

    def test_get_by_tags_multiple_tags(self, wm):
        """get_by_tags with multiple tags finds any match."""
        wm.add("Gaming", tags=["gaming"])
        wm.add("Work", tags=["work"])
        wm.add("Untagged")

        results = wm.get_by_tags(["gaming", "work"])
        assert len(results) == 2

    def test_get_by_tags_empty(self, wm):
        """get_by_tags with empty list returns empty."""
        wm.add("Tagged", tags=["test"])
        assert wm.get_by_tags([]) == []

    def test_get_by_source(self, wm):
        """get_by_source filters by source."""
        wm.add("User input", source="user_input")
        wm.add("Memory retrieval", source="memory_retrieval")
        wm.add("Another user input", source="user_input")

        user_items = wm.get_by_source("user_input")
        assert len(user_items) == 2

    def test_get_context_string(self, wm):
        """get_context_string renders items as string."""
        wm.add("First item", priority=0.9)
        wm.add("Second item", priority=0.8)

        context = wm.get_context_string()
        assert "First item" in context
        assert "Second item" in context

    def test_get_context_string_with_max_tokens(self, wm):
        """get_context_string respects max_tokens."""
        wm.add("A " * 50, priority=0.9)  # 50 tokens
        wm.add("B " * 50, priority=0.8)  # 50 tokens

        context = wm.get_context_string(max_tokens=60)
        assert "A" in context
        assert "B" not in context

    def test_get_context_string_with_metadata(self, wm):
        """get_context_string includes source labels with metadata."""
        wm.add("Test content", source="user_input")

        context = wm.get_context_string(include_metadata=True)
        assert "[user_input]" in context
        assert "Test content" in context


# =============================================================================
# Budget Management Tests
# =============================================================================


class TestBudgetManagement:
    """Tests for token budget management."""

    def test_get_token_usage(self, wm):
        """get_token_usage returns current and budget."""
        wm.add("one two three four five six seven eight nine ten")
        current, budget = wm.get_token_usage()
        assert current == 10
        assert budget == 100

    def test_has_capacity_true(self, wm):
        """has_capacity returns True when space available."""
        assert wm.has_capacity(50)

    def test_has_capacity_false(self, wm):
        """has_capacity returns False when over budget."""
        assert not wm.has_capacity(150)

    def test_get_available_tokens(self, wm):
        """get_available_tokens returns remaining space."""
        wm.add("one two three four five six seven eight nine ten")
        available = wm.get_available_tokens()
        assert available == 90

    def test_set_token_budget_increase(self, wm):
        """Increasing budget doesn't evict."""
        wm.add("Test")
        evicted = wm.set_token_budget(200)
        assert len(evicted) == 0
        assert wm.token_budget == 200

    def test_set_token_budget_decrease_evicts(self, wm):
        """Decreasing budget below current usage evicts."""
        wm.add("Ten tokens here one two three four five six")  # 10 tokens
        evicted = wm.set_token_budget(5)  # Shrink below current
        assert len(evicted) > 0

    def test_is_full_property(self, wm_small):
        """is_full property works correctly."""
        assert not wm_small.is_full
        # Add 20+ tokens to fill the budget
        wm_small.add("a b c d e f g h i j k l m n o p q r s t u v")
        assert wm_small.is_full


# =============================================================================
# Overflow Policy Tests
# =============================================================================


class TestOverflowPolicies:
    """Tests for different overflow eviction policies."""

    def test_fifo_eviction(self):
        """FIFO policy evicts oldest item first."""
        wm = WorkingMemoryManager(
            token_budget=10,
            overflow_policy=OverflowPolicy.FIFO,
        )
        item1 = wm.add("first second")  # 2 tokens
        item2 = wm.add("third fourth")  # 2 tokens
        wm.add("fifth sixth")  # 2 tokens

        # Add something that triggers eviction (need 4 tokens, only 4 left)
        wm.add("a b c d e")  # 5 tokens, triggers eviction

        # First item should be evicted
        assert wm.get(item1.item_id) is None
        assert wm.get(item2.item_id) is not None

    def test_lru_eviction(self):
        """LRU policy evicts least recently accessed."""
        wm = WorkingMemoryManager(
            token_budget=10,
            overflow_policy=OverflowPolicy.LRU,
        )
        item1 = wm.add("first second")  # 2 tokens
        item2 = wm.add("third fourth")  # 2 tokens
        wm.add("fifth sixth")  # 2 tokens

        # Access item1 to make it recently used
        time.sleep(0.01)
        wm.access(item1.item_id)

        # Add something that triggers eviction
        wm.add("a b c d e")  # 5 tokens, triggers eviction

        # Item2 should be evicted (least recently accessed)
        assert wm.get(item1.item_id) is not None
        assert wm.get(item2.item_id) is None

    def test_priority_eviction(self):
        """PRIORITY policy evicts lowest priority first."""
        wm = WorkingMemoryManager(
            token_budget=10,
            overflow_policy=OverflowPolicy.PRIORITY,
        )
        item1 = wm.add("first second", priority=0.9)  # 2 tokens
        item2 = wm.add("third fourth", priority=0.3)  # 2 tokens, lowest
        wm.add("fifth sixth", priority=0.7)  # 2 tokens

        # Add something that triggers eviction
        wm.add("a b c d e")  # 5 tokens, triggers eviction

        # Item2 should be evicted (lowest priority)
        assert wm.get(item1.item_id) is not None
        assert wm.get(item2.item_id) is None

    def test_summarize_falls_back_to_fifo(self):
        """SUMMARIZE without summarizer falls back to FIFO."""
        wm = WorkingMemoryManager(
            token_budget=10,
            overflow_policy=OverflowPolicy.SUMMARIZE,
        )
        item1 = wm.add("first second")  # 2 tokens
        wm.add("third fourth")  # 2 tokens
        wm.add("fifth sixth")  # 2 tokens

        # Add something that triggers eviction
        wm.add("a b c d e")  # 5 tokens, triggers eviction

        # Should behave like FIFO
        assert wm.get(item1.item_id) is None


class TestEvictionScenarios:
    """Tests for various eviction scenarios."""

    def test_evict_multiple_items(self):
        """Adding large item may evict multiple smaller items."""
        wm = WorkingMemoryManager(token_budget=20)
        wm.add("One")  # 1 token
        wm.add("Two")  # 1 token
        wm.add("Three")  # 1 token

        # Add large item that requires evicting multiple
        wm.add("This is a very long content " * 3)

        # Some items should be evicted
        assert wm.item_count < 4

    def test_eviction_updates_token_count(self):
        """Eviction correctly updates current token count."""
        wm = WorkingMemoryManager(token_budget=10)
        wm.add("First item")  # 2 tokens
        wm.add("Second item")  # 2 tokens

        current_before, _ = wm.get_token_usage()
        assert current_before == 4

        # Force eviction
        wm.add("Third item here")  # 3 tokens, triggers eviction

        current_after, _ = wm.get_token_usage()
        # Should still be under budget
        assert current_after <= wm.token_budget

    def test_priority_tie_breaker(self):
        """Items with same priority use insertion order."""
        wm = WorkingMemoryManager(
            token_budget=8,
            overflow_policy=OverflowPolicy.PRIORITY,
        )
        item1 = wm.add("aa", priority=0.5)  # 1 token
        wm.add("bb", priority=0.5)  # 1 token
        wm.add("cc", priority=0.5)  # 1 token

        # Add something that triggers eviction (need 5, have 5 left)
        wm.add("a b c d e f")  # 6 tokens, triggers eviction

        # First should be evicted (earliest with same priority)
        assert wm.get(item1.item_id) is None


# =============================================================================
# Attention Integration Tests
# =============================================================================


class TestAttentionIntegration:
    """Tests for ExperienceKernel attention integration."""

    def test_boost_by_attention_without_kernel(self, wm):
        """boost_by_attention does nothing without kernel."""
        item = wm.add("Test", priority=0.5, tags=["gaming"])
        wm.boost_by_attention()  # Should not raise
        assert item.priority == 0.5  # Unchanged

    def test_boost_by_attention_with_kernel(self):
        """boost_by_attention increases priority for matching tags."""
        from bartholomew.kernel.experience_kernel import ExperienceKernel

        kernel = ExperienceKernel()
        kernel.set_attention(
            target="gaming session",
            focus_type="task",
            intensity=1.0,
            tags=["gaming"],
        )

        wm = WorkingMemoryManager(kernel=kernel)
        item = wm.add("Gaming content", priority=0.5, tags=["gaming"])
        original_priority = item.priority

        wm.boost_by_attention()

        assert item.priority > original_priority

    def test_attention_aware_eviction(self):
        """PRIORITY eviction protects items matching attention."""
        from bartholomew.kernel.experience_kernel import ExperienceKernel

        kernel = ExperienceKernel()
        kernel.set_attention(
            target="gaming",
            focus_type="task",
            intensity=1.0,
            tags=["gaming"],
        )

        wm = WorkingMemoryManager(
            token_budget=10,
            overflow_policy=OverflowPolicy.PRIORITY,
            kernel=kernel,
        )

        # Same base priority, but gaming matches attention
        item1 = wm.add("gaming one two", priority=0.5, tags=["gaming"])  # 3 tokens
        item2 = wm.add("work one two", priority=0.5, tags=["work"])  # 3 tokens

        # Trigger eviction (need 5 more tokens, only 4 available)
        wm.add("a b c d e")  # 5 tokens, triggers eviction

        # Work stuff should be evicted (gaming gets attention boost)
        assert wm.get(item1.item_id) is not None
        assert wm.get(item2.item_id) is None

    def test_decay_priorities_without_kernel(self, wm):
        """decay_priorities works without kernel."""
        item = wm.add("Test", priority=0.5)
        wm.decay_priorities(delta_minutes=5.0)
        # Priority should decay
        assert item.priority < 0.5

    def test_decay_priorities_protects_attention_tags(self):
        """decay_priorities doesn't decay items matching attention."""
        from bartholomew.kernel.experience_kernel import ExperienceKernel

        kernel = ExperienceKernel()
        kernel.set_attention(
            target="protected",
            focus_type="task",
            tags=["protected"],
        )

        wm = WorkingMemoryManager(kernel=kernel)
        item1 = wm.add("Protected", priority=0.5, tags=["protected"])
        item2 = wm.add("Unprotected", priority=0.5, tags=["other"])

        wm.decay_priorities(delta_minutes=10.0)

        # Protected item unchanged, unprotected decayed
        assert item1.priority == 0.5
        assert item2.priority < 0.5

    def test_decay_respects_min_priority(self, wm):
        """Decay stops at MIN_PRIORITY floor."""
        item = wm.add("Test", priority=0.1)
        wm.decay_priorities(delta_minutes=100.0)  # Large decay
        assert item.priority >= WorkingMemoryManager.MIN_PRIORITY


# =============================================================================
# GlobalWorkspace Integration Tests
# =============================================================================


class TestGlobalWorkspaceIntegration:
    """Tests for GlobalWorkspace event emission."""

    def test_emit_added_event(self):
        """Adding item emits event to workspace."""
        from bartholomew.kernel.global_workspace import GlobalWorkspace

        workspace = GlobalWorkspace()
        wm = WorkingMemoryManager(workspace=workspace)

        events = []
        workspace.subscribe(
            "working_memory",
            callback=lambda e: events.append(e),
        )

        wm.add("Test item")

        assert len(events) == 1
        assert events[0].payload["action"] == "added"

    def test_emit_removed_event(self):  # noqa: E501
        """Removing item emits event to workspace."""
        from bartholomew.kernel.global_workspace import GlobalWorkspace

        workspace = GlobalWorkspace()
        wm = WorkingMemoryManager(workspace=workspace)

        events = []
        workspace.subscribe(
            "working_memory",
            callback=lambda e: events.append(e),
        )

        item = wm.add("Test item")
        wm.remove(item.item_id)

        # Should have add + remove events
        assert len(events) == 2
        assert events[1].payload["action"] == "removed"

    def test_emit_evicted_event(self):  # noqa: E501
        """Eviction emits event to workspace."""
        from bartholomew.kernel.global_workspace import GlobalWorkspace

        workspace = GlobalWorkspace()
        wm = WorkingMemoryManager(
            token_budget=5,
            workspace=workspace,
        )

        events = []
        workspace.subscribe(
            "working_memory",
            callback=lambda e: events.append(e),
        )

        wm.add("aa")  # 1 token
        wm.add("bb")  # 1 token
        wm.add("cc dd ee ff gg")  # 5 tokens - triggers eviction

        evicted_events = [e for e in events if e.payload["action"] == "evicted"]
        assert len(evicted_events) >= 1

    def test_emit_cleared_event(self):
        """Clearing emits event to workspace."""
        from bartholomew.kernel.global_workspace import GlobalWorkspace

        workspace = GlobalWorkspace()
        wm = WorkingMemoryManager(workspace=workspace)

        events = []
        workspace.subscribe(
            "working_memory",
            callback=lambda e: events.append(e),
        )

        wm.add("Item 1")
        wm.add("Item 2")
        wm.clear()

        cleared_events = [e for e in events if e.payload["action"] == "cleared"]
        assert len(cleared_events) == 1
        assert cleared_events[0].payload["items_cleared"] == 2


# =============================================================================
# Persistence Tests
# =============================================================================


class TestPersistence:
    """Tests for snapshot and restore functionality."""

    def test_snapshot_captures_state(self, wm):
        """snapshot() captures full state."""
        wm.add("Item 1", priority=0.8, tags=["a"])
        wm.add("Item 2", priority=0.6, tags=["b"])

        snapshot = wm.snapshot()

        assert snapshot["token_budget"] == 100
        assert snapshot["overflow_policy"] == "priority"
        assert len(snapshot["items"]) == 2
        assert "timestamp" in snapshot

    def test_restore_from_snapshot(self, wm):
        """restore() restores full state."""
        wm.add("Item 1", priority=0.8, tags=["a"])
        wm.add("Item 2", priority=0.6, tags=["b"])

        snapshot = wm.snapshot()

        # Create new manager and restore
        wm2 = WorkingMemoryManager()
        wm2.restore(snapshot)

        assert wm2.item_count == 2
        assert wm2.token_budget == 100
        current, _ = wm2.get_token_usage()
        assert current == wm._current_tokens

    def test_snapshot_roundtrip(self, wm):
        """Snapshot/restore preserves all data."""
        wm.add("Test content", priority=0.7, tags=["test"], metadata={"key": "val"})

        snapshot = wm.snapshot()
        json_str = json.dumps(snapshot)  # Ensure JSON serializable
        restored_data = json.loads(json_str)

        wm2 = WorkingMemoryManager()
        wm2.restore(restored_data)

        items = wm2.get_all()
        assert len(items) == 1
        assert items[0].content == "Test content"
        assert items[0].priority == 0.7
        assert items[0].relevance_tags == ["test"]
        assert items[0].metadata == {"key": "val"}

    def test_restore_handles_invalid_policy(self, wm):
        """restore() handles invalid policy gracefully."""
        snapshot = {
            "token_budget": 100,
            "overflow_policy": "invalid_policy",
            "items": [],
        }

        wm.restore(snapshot)
        # Should default to PRIORITY
        assert wm.overflow_policy == OverflowPolicy.PRIORITY


# =============================================================================
# Properties Tests
# =============================================================================


class TestProperties:
    """Tests for property accessors."""

    def test_token_budget_property(self, wm):
        """token_budget property returns correct value."""
        assert wm.token_budget == 100

    def test_overflow_policy_property(self, wm):
        """overflow_policy property returns correct value."""
        assert wm.overflow_policy == OverflowPolicy.PRIORITY

    def test_overflow_policy_setter(self, wm):
        """overflow_policy can be changed."""
        wm.overflow_policy = OverflowPolicy.LRU
        assert wm.overflow_policy == OverflowPolicy.LRU

    def test_item_count_property(self, wm):
        """item_count returns correct count."""
        assert wm.item_count == 0
        wm.add("Item")
        assert wm.item_count == 1

    def test_is_empty_property(self, wm):
        """is_empty property works correctly."""
        assert wm.is_empty
        wm.add("Item")
        assert not wm.is_empty


# =============================================================================
# Singleton Tests
# =============================================================================


class TestSingleton:
    """Tests for singleton pattern."""

    def test_get_working_memory_creates_instance(self):
        """get_working_memory creates new instance."""
        wm = get_working_memory()
        assert wm is not None
        assert isinstance(wm, WorkingMemoryManager)

    def test_get_working_memory_returns_same_instance(self):
        """get_working_memory returns same instance."""
        wm1 = get_working_memory()
        wm2 = get_working_memory()
        assert wm1 is wm2

    def test_reset_working_memory(self):
        """reset_working_memory creates fresh instance."""
        wm1 = get_working_memory()
        wm1.add("Test")

        reset_working_memory()

        wm2 = get_working_memory()
        assert wm1 is not wm2
        assert wm2.is_empty

    def test_get_working_memory_with_custom_budget(self):
        """get_working_memory respects custom budget on creation."""
        wm = get_working_memory(token_budget=500)
        assert wm.token_budget == 500


# =============================================================================
# ItemSource Enum Tests
# =============================================================================


class TestItemSourceEnum:
    """Tests for ItemSource enum."""

    def test_all_sources_defined(self):
        """All expected sources are defined."""
        assert ItemSource.USER_INPUT.value == "user_input"
        assert ItemSource.MEMORY_RETRIEVAL.value == "memory_retrieval"
        assert ItemSource.SYSTEM.value == "system"
        assert ItemSource.REFLECTION.value == "reflection"
        assert ItemSource.EXTERNAL.value == "external"


# =============================================================================
# Edge Cases Tests
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_add_empty_content(self, wm):
        """Adding empty content creates item with 0 tokens."""
        item = wm.add("")
        assert item.token_count == 0

    def test_add_whitespace_only(self, wm):
        """Adding whitespace-only creates item with 0 tokens."""
        item = wm.add("   \n\t  ")
        assert item.token_count == 0

    def test_add_very_large_item(self):
        """Adding item larger than budget still works."""
        wm = WorkingMemoryManager(token_budget=10)
        # This item is larger than the entire budget
        item = wm.add("A " * 100)

        # Item should still be added (budget is soft limit)
        assert item is not None
        assert wm.item_count == 1

    def test_remove_updates_token_count_correctly(self, wm):
        """Removing item updates token count correctly."""
        item = wm.add("one two three four five six seven eight nine ten")
        assert wm._current_tokens == 10

        wm.remove(item.item_id)
        assert wm._current_tokens == 0

    def test_context_string_empty_memory(self, wm):
        """get_context_string on empty memory returns empty string."""
        assert wm.get_context_string() == ""

    def test_get_by_tags_no_matches(self, wm):
        """get_by_tags with no matches returns empty list."""
        wm.add("Tagged", tags=["a", "b"])
        result = wm.get_by_tags(["c", "d"])
        assert result == []

    def test_multiple_evictions_maintain_consistency(self):
        """Multiple rapid evictions maintain consistent state."""
        wm = WorkingMemoryManager(token_budget=20)

        # Add many items
        for i in range(10):
            wm.add(f"Item {i}")

        # Current tokens should never exceed budget by much
        current, budget = wm.get_token_usage()
        # Allow slight overage for the currently-being-added item
        assert current <= budget + 10
