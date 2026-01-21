"""
Working Memory Manager
----------------------
Token-bounded memory system that tracks Bartholomew's active context —
what he's "thinking about right now" — with intelligent overflow policies.

Stage 3.3: Working Memory Manager

Key features:
- Token-bounded capacity with configurable budget
- Multiple overflow policies (FIFO, LRU, PRIORITY, SUMMARIZE)
- Attention-aware eviction (integrates with ExperienceKernel)
- Event emission via GlobalWorkspace
- Snapshot/restore for persistence
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from bartholomew.kernel.experience_kernel import ExperienceKernel
    from bartholomew.kernel.global_workspace import GlobalWorkspace


logger = logging.getLogger(__name__)


# =============================================================================
# Enums
# =============================================================================


class OverflowPolicy(Enum):
    """Strategies for handling working memory overflow."""

    FIFO = "fifo"  # First in, first out
    LRU = "lru"  # Least recently used (by last_accessed)
    PRIORITY = "priority"  # Lowest priority first
    SUMMARIZE = "summarize"  # Summarize oldest items to reclaim space


class ItemSource(Enum):
    """Sources of working memory items."""

    USER_INPUT = "user_input"  # Direct user message
    MEMORY_RETRIEVAL = "memory_retrieval"  # Retrieved from long-term memory
    SYSTEM = "system"  # System-generated context
    REFLECTION = "reflection"  # From reflection/narrator
    EXTERNAL = "external"  # External API/tool results


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class WorkingMemoryItem:
    """
    A single item in working memory.

    Represents a piece of content that Bartholomew is currently
    "thinking about" or has in active context.
    """

    item_id: str
    """Unique identifier for this item"""

    content: str
    """The actual text content"""

    source: str
    """Where this item came from (user_input, memory_retrieval, etc.)"""

    token_count: int
    """Number of tokens in this item"""

    priority: float = 0.5
    """Priority score (0.0-1.0), higher = more important"""

    relevance_tags: list[str] = field(default_factory=list)
    """Context tags for attention matching"""

    added_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    """When this item was added"""

    last_accessed: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    """Last time this item was accessed (for LRU)"""

    metadata: dict[str, Any] = field(default_factory=dict)
    """Additional metadata (memory_id, chunk_id, etc.)"""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for JSON storage."""
        return {
            "item_id": self.item_id,
            "content": self.content,
            "source": self.source,
            "token_count": self.token_count,
            "priority": self.priority,
            "relevance_tags": self.relevance_tags,
            "added_at": self.added_at.isoformat(),
            "last_accessed": self.last_accessed.isoformat(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkingMemoryItem:
        """Deserialize from dictionary."""
        added_at = datetime.now(timezone.utc)
        if data.get("added_at"):
            added_at = datetime.fromisoformat(data["added_at"])

        last_accessed = datetime.now(timezone.utc)
        if data.get("last_accessed"):
            last_accessed = datetime.fromisoformat(data["last_accessed"])

        return cls(
            item_id=data["item_id"],
            content=data["content"],
            source=data.get("source", "unknown"),
            token_count=data.get("token_count", 0),
            priority=data.get("priority", 0.5),
            relevance_tags=data.get("relevance_tags", []),
            added_at=added_at,
            last_accessed=last_accessed,
            metadata=data.get("metadata", {}),
        )


# =============================================================================
# Working Memory Manager
# =============================================================================


class WorkingMemoryManager:
    """
    Token-bounded working memory system.

    Manages Bartholomew's active context with configurable capacity
    and intelligent overflow policies.
    """

    # Default token budget (~1 page of context)
    DEFAULT_TOKEN_BUDGET = 4000

    # Minimum priority floor (items can't go below this)
    MIN_PRIORITY = 0.01

    # Priority decay rate per minute
    PRIORITY_DECAY_RATE = 0.02

    # Attention boost multiplier
    ATTENTION_BOOST = 0.3

    def __init__(
        self,
        token_budget: int = DEFAULT_TOKEN_BUDGET,
        overflow_policy: OverflowPolicy = OverflowPolicy.PRIORITY,
        kernel: ExperienceKernel | None = None,
        workspace: GlobalWorkspace | None = None,
        summarizer: Any | None = None,  # Optional summarization engine
    ):
        """
        Initialize the Working Memory Manager.

        Args:
            token_budget: Maximum tokens allowed in working memory
            overflow_policy: Strategy for handling overflow
            kernel: Optional ExperienceKernel for attention-aware eviction
            workspace: Optional GlobalWorkspace for event emission
            summarizer: Optional summarization engine for SUMMARIZE policy
        """
        self._token_budget = token_budget
        self._overflow_policy = overflow_policy
        self._kernel = kernel
        self._workspace = workspace
        self._summarizer = summarizer

        # Use OrderedDict to maintain insertion order (for FIFO)
        self._items: OrderedDict[str, WorkingMemoryItem] = OrderedDict()

        # Track current token usage
        self._current_tokens = 0

        logger.debug(
            f"WorkingMemoryManager initialized: budget={token_budget}, "
            f"policy={overflow_policy.value}",
        )

    # =========================================================================
    # Token Counting
    # =========================================================================

    @staticmethod
    def count_tokens(text: str) -> int:
        """
        Count tokens in text using whitespace tokenization.

        This is a simple proxy for token counting, consistent with
        ChunkingEngine. For production, consider using tiktoken.

        Args:
            text: Text to count tokens in

        Returns:
            Token count
        """
        if not text or not text.strip():
            return 0
        return len(text.split())

    # =========================================================================
    # Core Operations
    # =========================================================================

    def add(
        self,
        content: str,
        source: str = "user_input",
        priority: float = 0.5,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WorkingMemoryItem:
        """
        Add an item to working memory.

        If adding the item would exceed the budget, overflow policy is applied
        to make room.

        Args:
            content: Text content to add
            source: Source of the content
            priority: Priority score (0.0-1.0)
            tags: Context tags for attention matching
            metadata: Additional metadata

        Returns:
            The created WorkingMemoryItem
        """
        token_count = self.count_tokens(content)
        now = datetime.now(timezone.utc)

        item = WorkingMemoryItem(
            item_id=str(uuid.uuid4()),
            content=content,
            source=source,
            token_count=token_count,
            priority=max(self.MIN_PRIORITY, min(1.0, priority)),
            relevance_tags=tags or [],
            added_at=now,
            last_accessed=now,
            metadata=metadata or {},
        )

        # Check if we need to make room
        while self._current_tokens + token_count > self._token_budget and len(self._items) > 0:
            evicted = self._evict_one()
            if evicted is None:
                # Can't evict anything, break to avoid infinite loop
                break

        # Add the item
        self._items[item.item_id] = item
        self._current_tokens += token_count

        # Emit event if workspace attached
        if self._workspace:
            self._emit_added_event(item)

        logger.debug(
            f"Added item {item.item_id[:8]}..., tokens={token_count}, "
            f"total={self._current_tokens}/{self._token_budget}",
        )

        return item

    def remove(self, item_id: str) -> bool:
        """
        Remove an item from working memory.

        Args:
            item_id: ID of item to remove

        Returns:
            True if item was found and removed
        """
        if item_id not in self._items:
            return False

        item = self._items.pop(item_id)
        self._current_tokens -= item.token_count

        # Emit event if workspace attached
        if self._workspace:
            self._emit_removed_event(item)

        logger.debug(f"Removed item {item_id[:8]}...")
        return True

    def get(self, item_id: str) -> WorkingMemoryItem | None:
        """
        Get an item by ID without updating last_accessed.

        Args:
            item_id: ID of item to get

        Returns:
            The item, or None if not found
        """
        return self._items.get(item_id)

    def access(self, item_id: str) -> WorkingMemoryItem | None:
        """
        Get an item by ID and update its last_accessed time.

        This is useful for LRU eviction policy.

        Args:
            item_id: ID of item to access

        Returns:
            The item, or None if not found
        """
        item = self._items.get(item_id)
        if item:
            item.last_accessed = datetime.now(timezone.utc)
        return item

    def clear(self) -> None:
        """Clear all items from working memory."""
        cleared_count = len(self._items)
        self._items.clear()
        self._current_tokens = 0

        # Emit event if workspace attached
        if self._workspace:
            self._emit_cleared_event(cleared_count)

        logger.debug("Cleared working memory")

    # =========================================================================
    # Query & Retrieval
    # =========================================================================

    def get_all(self) -> list[WorkingMemoryItem]:
        """
        Get all items in working memory.

        Returns:
            List of items, ordered by priority (highest first)
        """
        items = list(self._items.values())
        items.sort(key=lambda x: x.priority, reverse=True)
        return items

    def get_by_tags(self, tags: list[str]) -> list[WorkingMemoryItem]:
        """
        Get items matching any of the given tags.

        Args:
            tags: Tags to match

        Returns:
            List of matching items, ordered by priority
        """
        if not tags:
            return []

        tag_set = set(tags)
        matching = [
            item for item in self._items.values() if tag_set.intersection(item.relevance_tags)
        ]
        matching.sort(key=lambda x: x.priority, reverse=True)
        return matching

    def get_by_source(self, source: str) -> list[WorkingMemoryItem]:
        """
        Get items from a specific source.

        Args:
            source: Source to filter by

        Returns:
            List of matching items
        """
        return [item for item in self._items.values() if item.source == source]

    def get_context_string(
        self,
        max_tokens: int | None = None,
        separator: str = "\n\n",
        include_metadata: bool = False,
    ) -> str:
        """
        Render working memory as a context string.

        Items are ordered by priority and joined with separator.

        Args:
            max_tokens: Maximum tokens to include (None = all)
            separator: String to join items with
            include_metadata: If True, include source labels

        Returns:
            Rendered context string
        """
        items = self.get_all()

        if not items:
            return ""

        result_parts = []
        token_count = 0
        separator_tokens = self.count_tokens(separator)

        for item in items:
            if max_tokens and token_count + item.token_count > max_tokens:
                break

            if include_metadata:
                part = f"[{item.source}] {item.content}"
            else:
                part = item.content

            result_parts.append(part)
            token_count += item.token_count + separator_tokens

        return separator.join(result_parts)

    # =========================================================================
    # Budget Management
    # =========================================================================

    def get_token_usage(self) -> tuple[int, int]:
        """
        Get current token usage and budget.

        Returns:
            Tuple of (current_tokens, budget)
        """
        return (self._current_tokens, self._token_budget)

    def has_capacity(self, tokens: int) -> bool:
        """
        Check if there's capacity for the given number of tokens.

        Args:
            tokens: Number of tokens to check

        Returns:
            True if tokens can be added without eviction
        """
        return self._current_tokens + tokens <= self._token_budget

    def get_available_tokens(self) -> int:
        """
        Get the number of tokens available before eviction.

        Returns:
            Available token count
        """
        return max(0, self._token_budget - self._current_tokens)

    def set_token_budget(self, budget: int) -> list[WorkingMemoryItem]:
        """
        Update the token budget.

        If new budget is smaller, eviction may occur.

        Args:
            budget: New token budget

        Returns:
            List of any items that were evicted
        """
        self._token_budget = budget
        return self._enforce_budget()

    def _enforce_budget(self) -> list[WorkingMemoryItem]:
        """
        Enforce the token budget, evicting items if necessary.

        Returns:
            List of evicted items
        """
        evicted = []
        while self._current_tokens > self._token_budget and len(self._items) > 0:
            item = self._evict_one()
            if item:
                evicted.append(item)
            else:
                break
        return evicted

    def _evict_one(self) -> WorkingMemoryItem | None:
        """
        Evict one item based on overflow policy.

        Returns:
            The evicted item, or None if nothing to evict
        """
        if not self._items:
            return None

        victim = self._select_eviction_victim()
        if victim is None:
            return None

        # Remove from items
        self._items.pop(victim.item_id)
        self._current_tokens -= victim.token_count

        # Emit event if workspace attached
        if self._workspace:
            self._emit_evicted_event(victim)

        logger.debug(
            f"Evicted item {victim.item_id[:8]}... (policy={self._overflow_policy.value})",
        )

        return victim

    def _select_eviction_victim(self) -> WorkingMemoryItem | None:
        """
        Select which item to evict based on overflow policy.

        Returns:
            Item to evict, or None if nothing suitable
        """
        if not self._items:
            return None

        items = list(self._items.values())

        if self._overflow_policy == OverflowPolicy.FIFO:
            # First in, first out - get the oldest item
            return items[0]

        elif self._overflow_policy == OverflowPolicy.LRU:
            # Least recently used
            return min(items, key=lambda x: x.last_accessed)

        elif self._overflow_policy == OverflowPolicy.PRIORITY:
            # Lowest priority first (considering attention context)
            return min(items, key=lambda x: self._compute_eviction_score(x))

        elif self._overflow_policy == OverflowPolicy.SUMMARIZE:
            # For SUMMARIZE, we'd need to implement summarization
            # Fall back to FIFO for now if no summarizer
            if self._summarizer is None:
                return items[0]
            # TODO: Implement summarization-based eviction
            return items[0]

        return items[0]

    def _compute_eviction_score(self, item: WorkingMemoryItem) -> float:
        """
        Compute an eviction score for an item.

        Lower score = more likely to be evicted.
        Takes into account priority and attention context.

        Args:
            item: Item to score

        Returns:
            Eviction score (lower = evict first)
        """
        score = item.priority

        # Boost items matching current attention tags
        if self._kernel:
            attention = self._kernel.get_attention()
            if attention.context_tags:
                tag_overlap = set(item.relevance_tags).intersection(
                    attention.context_tags,
                )
                if tag_overlap:
                    # Boost based on attention intensity
                    score += self.ATTENTION_BOOST * attention.focus_intensity

        return score

    # =========================================================================
    # Attention Integration
    # =========================================================================

    def boost_by_attention(self) -> None:
        """
        Boost priority of items matching current attention context.

        Requires ExperienceKernel to be attached.
        """
        if not self._kernel:
            return

        attention = self._kernel.get_attention()
        if not attention.context_tags:
            return

        tag_set = set(attention.context_tags)
        boost_amount = self.ATTENTION_BOOST * attention.focus_intensity

        for item in self._items.values():
            if tag_set.intersection(item.relevance_tags):
                item.priority = min(1.0, item.priority + boost_amount)

        logger.debug(f"Boosted items by attention tags: {attention.context_tags}")

    def decay_priorities(self, delta_minutes: float = 1.0) -> None:
        """
        Decay priorities over time for items not in focus.

        Args:
            delta_minutes: Minutes since last decay
        """
        # Get current attention tags for protection
        protected_tags = set()
        if self._kernel:
            attention = self._kernel.get_attention()
            protected_tags = set(attention.context_tags or [])

        decay = self.PRIORITY_DECAY_RATE * delta_minutes

        for item in self._items.values():
            # Don't decay items matching attention
            if protected_tags.intersection(item.relevance_tags):
                continue

            item.priority = max(self.MIN_PRIORITY, item.priority - decay)

        logger.debug(f"Applied priority decay: {decay}")

    # =========================================================================
    # Persistence
    # =========================================================================

    def snapshot(self) -> dict[str, Any]:
        """
        Create a snapshot of working memory state.

        Returns:
            Dictionary containing full state
        """
        return {
            "token_budget": self._token_budget,
            "overflow_policy": self._overflow_policy.value,
            "current_tokens": self._current_tokens,
            "items": [item.to_dict() for item in self._items.values()],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def restore(self, snapshot: dict[str, Any]) -> None:
        """
        Restore working memory from a snapshot.

        Args:
            snapshot: Snapshot dictionary from snapshot()
        """
        self._token_budget = snapshot.get(
            "token_budget",
            self.DEFAULT_TOKEN_BUDGET,
        )

        policy_value = snapshot.get("overflow_policy", "priority")
        try:
            self._overflow_policy = OverflowPolicy(policy_value)
        except ValueError:
            self._overflow_policy = OverflowPolicy.PRIORITY

        self._items.clear()
        self._current_tokens = 0

        for item_data in snapshot.get("items", []):
            item = WorkingMemoryItem.from_dict(item_data)
            self._items[item.item_id] = item
            self._current_tokens += item.token_count

        logger.debug(
            f"Restored working memory: {len(self._items)} items, {self._current_tokens} tokens",
        )

    # =========================================================================
    # Database Persistence
    # =========================================================================

    WM_SCHEMA = """
    CREATE TABLE IF NOT EXISTS working_memory_snapshots (
        id TEXT PRIMARY KEY,
        timestamp TEXT NOT NULL,
        token_budget INTEGER NOT NULL,
        overflow_policy TEXT NOT NULL,
        current_tokens INTEGER NOT NULL,
        items_json TEXT NOT NULL,
        metadata_json TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """

    def _ensure_db_schema(self, db_path: str) -> None:
        """Ensure the working_memory_snapshots table exists."""
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(self.WM_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def persist_snapshot(self, db_path: str) -> str:
        """
        Persist working memory state to database.

        Args:
            db_path: Path to SQLite database

        Returns:
            Snapshot ID
        """
        self._ensure_db_schema(db_path)

        snapshot_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        snap = self.snapshot()
        items_json = json.dumps(snap["items"])

        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                INSERT INTO working_memory_snapshots
                (id, timestamp, token_budget, overflow_policy,
                 current_tokens, items_json, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    now.isoformat(),
                    self._token_budget,
                    self._overflow_policy.value,
                    self._current_tokens,
                    items_json,
                    json.dumps({"item_count": len(self._items)}),
                ),
            )
            conn.commit()
            logger.info(
                f"Persisted working memory snapshot {snapshot_id[:8]}... "
                f"({len(self._items)} items, {self._current_tokens} tokens)",
            )
        finally:
            conn.close()

        return snapshot_id

    def load_last_snapshot(self, db_path: str) -> bool:
        """
        Load most recent working memory snapshot from database.

        Args:
            db_path: Path to SQLite database

        Returns:
            True if snapshot was loaded, False if none found
        """
        self._ensure_db_schema(db_path)

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                """
                SELECT * FROM working_memory_snapshots
                ORDER BY timestamp DESC LIMIT 1
                """,
            ).fetchone()

            if not row:
                logger.debug("No working memory snapshot found")
                return False

            # Parse items from JSON
            items_data = json.loads(row["items_json"])

            # Build snapshot dict for restore()
            snapshot = {
                "token_budget": row["token_budget"],
                "overflow_policy": row["overflow_policy"],
                "current_tokens": row["current_tokens"],
                "items": items_data,
            }

            self.restore(snapshot)
            logger.info(
                f"Loaded working memory from snapshot {row['id'][:8]}...",
            )
            return True

        finally:
            conn.close()

    def get_snapshot_history(
        self,
        db_path: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Get recent snapshot history.

        Args:
            db_path: Path to SQLite database
            limit: Maximum snapshots to return

        Returns:
            List of snapshot metadata dicts
        """
        self._ensure_db_schema(db_path)

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT id, timestamp, token_budget, overflow_policy,
                       current_tokens, metadata_json
                FROM working_memory_snapshots
                ORDER BY timestamp DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()

            return [
                {
                    "id": row["id"],
                    "timestamp": row["timestamp"],
                    "token_budget": row["token_budget"],
                    "overflow_policy": row["overflow_policy"],
                    "current_tokens": row["current_tokens"],
                    "metadata": json.loads(row["metadata_json"] or "{}"),
                }
                for row in rows
            ]
        finally:
            conn.close()

    # =========================================================================
    # Event Emission
    # =========================================================================

    def _emit_added_event(self, item: WorkingMemoryItem) -> None:
        """Emit working memory added event."""
        if not self._workspace:
            return

        from bartholomew.kernel.global_workspace import EventType

        self._workspace.publish(
            channel="working_memory",
            event_type=EventType.CUSTOM,
            source="working_memory_manager",
            payload={
                "action": "added",
                "item_id": item.item_id,
                "source": item.source,
                "token_count": item.token_count,
                "priority": item.priority,
                "tags": item.relevance_tags,
                "total_tokens": self._current_tokens,
                "budget": self._token_budget,
            },
        )

    def _emit_removed_event(self, item: WorkingMemoryItem) -> None:
        """Emit working memory removed event."""
        if not self._workspace:
            return

        from bartholomew.kernel.global_workspace import EventType

        self._workspace.publish(
            channel="working_memory",
            event_type=EventType.CUSTOM,
            source="working_memory_manager",
            payload={
                "action": "removed",
                "item_id": item.item_id,
                "source": item.source,
                "token_count": item.token_count,
                "total_tokens": self._current_tokens,
                "budget": self._token_budget,
            },
        )

    def _emit_evicted_event(self, item: WorkingMemoryItem) -> None:
        """Emit working memory evicted event."""
        if not self._workspace:
            return

        from bartholomew.kernel.global_workspace import EventType

        self._workspace.publish(
            channel="working_memory",
            event_type=EventType.CUSTOM,
            source="working_memory_manager",
            payload={
                "action": "evicted",
                "item_id": item.item_id,
                "source": item.source,
                "token_count": item.token_count,
                "priority": item.priority,
                "policy": self._overflow_policy.value,
                "total_tokens": self._current_tokens,
                "budget": self._token_budget,
            },
        )

    def _emit_cleared_event(self, count: int) -> None:
        """Emit working memory cleared event."""
        if not self._workspace:
            return

        from bartholomew.kernel.global_workspace import EventType

        self._workspace.publish(
            channel="working_memory",
            event_type=EventType.CUSTOM,
            source="working_memory_manager",
            payload={
                "action": "cleared",
                "items_cleared": count,
                "total_tokens": 0,
                "budget": self._token_budget,
            },
        )

    # =========================================================================
    # Properties
    # =========================================================================

    @property
    def token_budget(self) -> int:
        """Current token budget."""
        return self._token_budget

    @property
    def overflow_policy(self) -> OverflowPolicy:
        """Current overflow policy."""
        return self._overflow_policy

    @overflow_policy.setter
    def overflow_policy(self, policy: OverflowPolicy) -> None:
        """Set overflow policy."""
        self._overflow_policy = policy

    @property
    def item_count(self) -> int:
        """Number of items in working memory."""
        return len(self._items)

    @property
    def is_empty(self) -> bool:
        """Check if working memory is empty."""
        return len(self._items) == 0

    @property
    def is_full(self) -> bool:
        """Check if working memory is at or over capacity."""
        return self._current_tokens >= self._token_budget


# =============================================================================
# Module-level singleton
# =============================================================================

_working_memory: WorkingMemoryManager | None = None


def get_working_memory(
    token_budget: int = WorkingMemoryManager.DEFAULT_TOKEN_BUDGET,
    overflow_policy: OverflowPolicy = OverflowPolicy.PRIORITY,
) -> WorkingMemoryManager:
    """
    Get or create working memory singleton.

    Args:
        token_budget: Token budget (only used if creating new instance)
        overflow_policy: Overflow policy (only used if creating new instance)

    Returns:
        WorkingMemoryManager instance
    """
    global _working_memory
    if _working_memory is None:
        _working_memory = WorkingMemoryManager(
            token_budget=token_budget,
            overflow_policy=overflow_policy,
        )
    return _working_memory


def reset_working_memory() -> None:
    """Reset the working memory singleton (useful for testing)."""
    global _working_memory
    _working_memory = None
