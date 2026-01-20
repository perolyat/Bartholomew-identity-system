# Working Memory Manager Implementation

**Stage 3.3: Working Memory Manager**

Implemented: 2026-01-20

## Overview

The Working Memory Manager is a token-bounded memory system that tracks Bartholomew's active context — what he's "thinking about right now". It provides intelligent overflow policies that integrate with the ExperienceKernel's attention system.

## Core Components

### File: `bartholomew/kernel/working_memory.py`

### WorkingMemoryItem (Dataclass)

Represents a single item in working memory:

```python
@dataclass
class WorkingMemoryItem:
    item_id: str           # Unique identifier
    content: str           # The actual text content
    source: str            # Where it came from (user_input, memory_retrieval, etc.)
    token_count: int       # Computed token count
    priority: float        # 0.0-1.0 priority score
    relevance_tags: list   # Context tags for attention matching
    added_at: datetime     # When added
    last_accessed: datetime # For LRU eviction
    metadata: dict         # Additional info (memory_id, chunk_id, etc.)
```

### OverflowPolicy (Enum)

Strategies for handling memory overflow:

| Policy | Description |
|--------|-------------|
| `FIFO` | First in, first out — evicts oldest items |
| `LRU` | Least recently used — evicts items not accessed recently |
| `PRIORITY` | Lowest priority first, with attention context boosting |
| `SUMMARIZE` | (Future) Summarize older items to reclaim space |

### ItemSource (Enum)

Standard sources for working memory items:

- `USER_INPUT` — Direct user message
- `MEMORY_RETRIEVAL` — Retrieved from long-term memory
- `SYSTEM` — System-generated context
- `REFLECTION` — From reflection/narrator
- `EXTERNAL` — External API/tool results

### WorkingMemoryManager (Class)

Main coordinator for the working memory system.

#### Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `token_budget` | 4000 | Maximum tokens allowed in working memory |
| `overflow_policy` | PRIORITY | Strategy for handling overflow |
| `kernel` | None | Optional ExperienceKernel for attention-aware eviction |
| `workspace` | None | Optional GlobalWorkspace for event emission |

#### Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `DEFAULT_TOKEN_BUDGET` | 4000 | ~1 page of context |
| `MIN_PRIORITY` | 0.01 | Floor for priority decay |
| `PRIORITY_DECAY_RATE` | 0.02 | Per-minute decay rate |
| `ATTENTION_BOOST` | 0.3 | Boost for attention-matching items |

## Public API

### Core Operations

```python
# Add item to working memory
item = wm.add(
    content="User said hello",
    source="user_input",
    priority=0.7,
    tags=["chat"],
    metadata={"turn_id": 42}
)

# Remove item by ID
wm.remove(item.item_id)

# Get item without updating last_accessed
item = wm.get(item_id)

# Access item (updates last_accessed for LRU)
item = wm.access(item_id)

# Clear all items
wm.clear()
```

### Query & Retrieval

```python
# Get all items, sorted by priority
items = wm.get_all()

# Get items matching tags
gaming_items = wm.get_by_tags(["gaming", "game"])

# Get items from specific source
user_items = wm.get_by_source("user_input")

# Render as context string
context = wm.get_context_string(
    max_tokens=2000,
    separator="\n\n",
    include_metadata=True
)
```

### Budget Management

```python
# Get current usage
current, budget = wm.get_token_usage()

# Check capacity
if wm.has_capacity(100):
    wm.add(content)

# Get available tokens
available = wm.get_available_tokens()

# Change budget (may trigger eviction)
evicted = wm.set_token_budget(2000)
```

### Attention Integration

```python
# Boost items matching current attention focus
wm.boost_by_attention()

# Decay priorities over time
wm.decay_priorities(delta_minutes=5.0)
```

### Persistence

```python
# Create snapshot for persistence
snapshot = wm.snapshot()

# Restore from snapshot
wm.restore(snapshot)
```

## Attention-Aware Eviction

When using PRIORITY policy with an attached ExperienceKernel:

1. **Base priority**: Each item has a 0.0-1.0 priority score
2. **Attention boost**: Items matching `attention.context_tags` get boosted
3. **Boost amount**: `ATTENTION_BOOST * attention.focus_intensity`

Example:
```python
kernel.set_attention(target="gaming", focus_type="task", tags=["gaming"])
wm = WorkingMemoryManager(kernel=kernel, overflow_policy=OverflowPolicy.PRIORITY)

# Gaming items get protected from eviction
wm.add("Gaming tip", priority=0.5, tags=["gaming"])  # Effective: 0.8
wm.add("Work task", priority=0.5, tags=["work"])     # Effective: 0.5
```

## GlobalWorkspace Integration

Working memory emits events on the `working_memory` channel:

| Action | Payload |
|--------|---------|
| `added` | item_id, source, token_count, priority, tags, total_tokens, budget |
| `removed` | item_id, source, token_count, total_tokens, budget |
| `evicted` | item_id, source, token_count, priority, policy, total_tokens, budget |
| `cleared` | items_cleared, total_tokens, budget |

Example subscription:
```python
workspace.subscribe(
    "working_memory",
    callback=lambda e: print(f"WM Event: {e.payload['action']}")
)
```

## Token Counting

Uses simple whitespace tokenization (consistent with ChunkingEngine):

```python
WorkingMemoryManager.count_tokens("Hello world")  # Returns 2
```

For production, consider integrating tiktoken for accurate GPT token counts.

## Singleton Pattern

```python
from bartholomew.kernel.working_memory import (
    get_working_memory,
    reset_working_memory,
)

# Get/create singleton
wm = get_working_memory(token_budget=4000)

# Reset (useful for testing)
reset_working_memory()
```

## Test Coverage

`tests/test_working_memory.py` — 60+ tests covering:

- WorkingMemoryItem creation and serialization
- Token counting edge cases
- Basic CRUD operations
- Query and retrieval methods
- Budget management and eviction
- All overflow policies (FIFO, LRU, PRIORITY, SUMMARIZE)
- Attention integration with ExperienceKernel
- GlobalWorkspace event emission
- Persistence (snapshot/restore)
- Singleton pattern
- Edge cases

## Verification

```bash
pytest -q tests/test_working_memory.py
```

## Exit Criteria Checklist

- [x] WorkingMemoryManager class implemented
- [x] WorkingMemoryItem dataclass with serialization
- [x] Token counting with configurable budget
- [x] 4 overflow policies (FIFO, LRU, PRIORITY, SUMMARIZE)
- [x] ExperienceKernel integration (attention-aware eviction)
- [x] GlobalWorkspace integration (event emission)
- [x] Comprehensive test suite (60+ tests)
- [x] Documentation

## Future Enhancements

1. **Tiktoken integration**: More accurate token counting for GPT models
2. **SUMMARIZE policy**: Actually summarize items instead of FIFO fallback
3. **Chunk-level items**: Store memory chunks directly in working memory
4. **Persistence to SQLite**: Persist working memory across sessions
5. **Priority scheduling**: Automatic priority boosting based on recency/relevance
