# Global Workspace Implementation

> Stage 3.2: Channel-based broadcast system for inter-module communication

## Overview

The Global Workspace provides a publish/subscribe event system that enables kernel components to broadcast state changes and allows other modules (narrator, persona, daemon) to subscribe and react to those changes.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    GlobalWorkspace                       │
├─────────────────────────────────────────────────────────┤
│  Channels:                                              │
│  ├─ affect      → Emotional state changes               │
│  ├─ attention   → Focus/attention changes               │
│  ├─ drives      → Drive activation/satisfaction         │
│  ├─ goals       → Goal added/completed                  │
│  ├─ context     → Context value changes                 │
│  ├─ snapshots   → Persistence events                    │
│  ├─ system      → Lifecycle events (startup/shutdown)   │
│  ├─ memory      → Memory store events                   │
│  └─ persona     → Persona switching events              │
├─────────────────────────────────────────────────────────┤
│  Features:                                              │
│  • Multi-subscriber per channel                         │
│  • Typed WorkspaceEvent dataclass                       │
│  • Event history (bounded ring buffer)                  │
│  • Optional filters per subscription                    │
│  • Sync + async broadcast options                       │
└─────────────────────────────────────────────────────────┘
```

## Components

### EventType (Enum)

Standard event types recognized by the workspace:

```python
class EventType(Enum):
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

    # Memory events
    MEMORY_STORED = "memory_stored"
    MEMORY_RETRIEVED = "memory_retrieved"

    # Persona events
    PERSONA_SWITCHED = "persona_switched"

    # Custom/extension events
    CUSTOM = "custom"
```

### WorkspaceEvent (Dataclass)

All events flowing through the workspace have this structure:

```python
@dataclass
class WorkspaceEvent:
    event_id: str           # Unique identifier
    event_type: EventType   # Type of event
    channel: str            # Channel name
    timestamp: datetime     # When created
    source: str             # Emitting module
    payload: dict[str, Any] # Event-specific data
    metadata: dict[str, Any] # Correlation IDs, causality chains, etc.
```

### GlobalWorkspace (Class)

Main coordinator providing:

- **Channel Management**: `create_channel()`, `get_channels()`, `channel_exists()`
- **Subscriptions**: `subscribe()`, `unsubscribe()`, `get_subscription_count()`
- **Publishing**: `publish()`, `publish_async()`, `publish_event()`
- **History**: `get_history()`, `get_all_history()`, `get_latest_event()`, `clear_history()`
- **Convenience Emitters**: `emit_affect_changed()`, `emit_attention_changed()`, etc.

## Integration with ExperienceKernel

The ExperienceKernel now accepts an optional `workspace` parameter. When provided, state changes automatically emit events:

```python
from bartholomew.kernel.global_workspace import GlobalWorkspace
from bartholomew.kernel.experience_kernel import ExperienceKernel

# Create workspace
ws = GlobalWorkspace()

# Subscribe to events
def on_affect_change(event):
    print(f"Affect changed: {event.payload}")

ws.subscribe("affect", on_affect_change)

# Create kernel with workspace
kernel = ExperienceKernel(workspace=ws)

# State changes emit events automatically
kernel.update_affect(valence=0.8, emotion="happy")
# → Triggers on_affect_change callback
```

### Events Emitted by ExperienceKernel

| Method | Channel | Event Type |
|--------|---------|------------|
| `update_affect()` | affect | AFFECT_CHANGED |
| `set_attention()` | attention | ATTENTION_CHANGED |
| `activate_drive()` | drives | DRIVE_ACTIVATED |
| `satisfy_drive()` | drives | DRIVE_SATISFIED |
| `add_goal()` | goals | GOAL_ADDED |
| `complete_goal()` | goals | GOAL_COMPLETED |
| `persist_snapshot()` | snapshots | SNAPSHOT_PERSISTED |

## Usage Examples

### Basic Subscription

```python
ws = GlobalWorkspace()
received = []

ws.subscribe("affect", lambda e: received.append(e))

ws.publish(
    channel="affect",
    event_type=EventType.AFFECT_CHANGED,
    source="test",
    payload={"valence": 0.5},
)
```

### Filtered Subscription

```python
# Only receive positive valence events
ws.subscribe(
    "affect",
    callback=lambda e: print(e),
    filter_fn=lambda e: e.payload.get("valence", 0) > 0,
)
```

### Async Publishing

```python
async def handler(event):
    await process_event(event)

ws.subscribe("affect", lambda e: None, async_callback=handler)

await ws.publish_async(
    channel="affect",
    event_type=EventType.AFFECT_CHANGED,
    source="daemon",
    payload={"valence": 0.7},
)
```

### Event History

```python
# Get recent events from a channel
history = ws.get_history("affect", limit=10)

# Get events of a specific type
drive_events = ws.get_history(
    "drives",
    event_type=EventType.DRIVE_ACTIVATED
)

# Get events since a timestamp
recent = ws.get_history("affect", since=one_hour_ago)
```

## Test Coverage

45 tests in `tests/test_global_workspace.py`:

- **TestWorkspaceEvent**: Creation, serialization, roundtrip
- **TestSubscription**: Creation, filtering, matching
- **TestGlobalWorkspaceChannels**: Default channels, creation, existence
- **TestGlobalWorkspaceSubscriptions**: Subscribe, unsubscribe, multi-subscriber
- **TestGlobalWorkspacePublishing**: Publish, notify, filter, async
- **TestGlobalWorkspaceHistory**: Store, limit, filter, clear, bounded size
- **TestGlobalWorkspaceConveniencePublishers**: All emit_* methods
- **TestGlobalWorkspaceIntegration**: ExperienceKernel integration

## Verification

```bash
pytest -q tests/test_global_workspace.py
pytest -q tests/test_experience_kernel.py
```

## Exit Criteria

- [x] GlobalWorkspace class with typed events
- [x] Multi-subscriber channel registry
- [x] Event history with bounded retention (configurable, default 100 per channel)
- [x] ExperienceKernel emits events on state changes
- [x] 45 tests covering all public methods
- [x] Documentation complete

## Next Phases

- **Phase 3.3**: Working Memory Manager (token-bounded memory with overflow policies)
- **Phase 3.4**: Narrator Episodic Layer (narrator voice that subscribes to workspace events)
- **Phase 3.5**: Persona Pack System (switchable persona configurations)
- **Phase 3.6**: Integration & Wiring (connect to daemon, API endpoints)
