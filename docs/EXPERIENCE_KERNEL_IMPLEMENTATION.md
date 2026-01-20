# Experience Kernel Implementation

> Stage 3: Unified Persona Core
>
> **Status:** In Progress (Phase 3.1)
> **Started:** 2026-01-20
> **Last Updated:** 2026-01-20

## Overview

The Experience Kernel is Bartholomew's "self-model" — a runtime representation of who Bartholomew is at any given moment. It integrates:
- **Drives**: Core motivational states from Identity.yaml
- **Affect**: Current emotional/energy state
- **Attention**: What is currently being focused on
- **Active Goals**: Current objectives being pursued
- **Context**: Situational awareness

This module enables Bartholomew to behave like "one continuous self" with coherent personality and memory across interactions.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Experience Kernel                        │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐   │
│  │    Drives     │  │    Affect     │  │   Attention   │   │
│  │   (16 core)   │  │ (energy,mood) │  │   (focus)     │   │
│  └───────┬───────┘  └───────┬───────┘  └───────┬───────┘   │
│          │                  │                  │            │
│          └──────────────────┼──────────────────┘            │
│                             │                               │
│                    ┌────────▼────────┐                      │
│                    │  Self Snapshot  │                      │
│                    │   (exportable)  │                      │
│                    └────────┬────────┘                      │
│                             │                               │
│          ┌──────────────────┼──────────────────┐            │
│          │                  │                  │            │
│  ┌───────▼───────┐  ┌───────▼───────┐  ┌──────▼──────┐    │
│  │ Active Goals  │  │    Context    │  │  Snapshot   │    │
│  │   (current)   │  │  (situation)  │  │ Persistence │    │
│  └───────────────┘  └───────────────┘  └─────────────┘    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## Phase 3.1: Experience Kernel Foundation

### Components

#### 1. DriveState

Represents the current activation level of each drive from Identity.yaml.

```python
@dataclass
class DriveState:
    drive_id: str           # e.g., "protect_user_wellbeing"
    base_priority: float    # From Identity.yaml (0.0-1.0)
    current_activation: float  # Current activation level (0.0-1.0)
    last_satisfied: datetime | None  # When drive was last addressed
    context_boost: float    # Situational modifier
```

Identity.yaml defines 16 drives in `identity.self_model.drives`:
- protect_user_wellbeing
- preserve_user_autonomy
- monitor_user_health_and_emotional_state
- express_curiosity_about_user_world
- build_loyal_companionship_over_time
- bring_moments_of_gentle_playfulness
- show_kindness_in_all_interactions
- provide_tactical_support_when_needed
- adapt_communication_style_to_context
- maintain_party_awareness_in_group_situations
- be_helpful_without_manipulation
- reduce_cognitive_load
- continual_self_improvement_within_guardrails
- build_shared_narrative_and_memories

#### 2. AffectState

Represents current emotional/energy state.

```python
@dataclass
class AffectState:
    valence: float      # -1.0 (negative) to 1.0 (positive)
    arousal: float      # 0.0 (calm) to 1.0 (activated)
    energy: float       # 0.0 (depleted) to 1.0 (full)
    dominant_emotion: str  # e.g., "curious", "concerned", "calm"
    decay_rate: float   # How quickly affect returns to baseline
```

#### 3. AttentionState

Represents what Bartholomew is currently focused on.

```python
@dataclass
class AttentionState:
    focus_target: str | None    # What is being attended to
    focus_type: str             # "user_input", "internal", "task", "idle"
    focus_intensity: float      # 0.0-1.0
    context_tags: list[str]     # e.g., ["gaming", "wellness", "chat"]
    since: datetime             # When focus started
```

#### 4. SelfSnapshot

The complete representation of "who Bartholomew is right now".

```python
@dataclass
class SelfSnapshot:
    snapshot_id: str
    timestamp: datetime
    drives: list[DriveState]
    affect: AffectState
    attention: AttentionState
    active_goals: list[str]
    context: dict[str, Any]
    metadata: dict[str, Any]
```

#### 5. ExperienceKernel

The main class coordinating self-model state.

```python
class ExperienceKernel:
    def __init__(self, identity_path: str, db_path: str):
        # Load identity configuration
        # Initialize drives from Identity.yaml
        # Initialize affect to neutral baseline
        # Initialize attention to idle

    def self_snapshot(self) -> SelfSnapshot:
        # Return current state of self

    def update_affect(self, valence: float = None, arousal: float = None,
                     energy: float = None, emotion: str = None) -> None:
        # Modify emotional/energy state

    def set_attention(self, target: str, focus_type: str,
                     intensity: float = 0.7, tags: list[str] = None) -> None:
        # Update what is being focused on

    def activate_drive(self, drive_id: str, boost: float = 0.0) -> None:
        # Increase drive activation

    def satisfy_drive(self, drive_id: str) -> None:
        # Mark drive as recently satisfied

    def add_goal(self, goal: str) -> None:
        # Add to active goals

    def complete_goal(self, goal: str) -> None:
        # Mark goal as complete

    def persist_snapshot(self) -> str:
        # Save current snapshot to database

    def load_last_snapshot(self) -> SelfSnapshot | None:
        # Load most recent snapshot from database
```

### Database Schema

New table `experience_snapshots`:

```sql
CREATE TABLE IF NOT EXISTS experience_snapshots (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    drives_json TEXT NOT NULL,
    affect_json TEXT NOT NULL,
    attention_json TEXT NOT NULL,
    active_goals_json TEXT NOT NULL,
    context_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_experience_snapshots_timestamp
ON experience_snapshots(timestamp DESC);
```

### Configuration Integration

The Experience Kernel reads from Identity.yaml:
- `identity.self_model.drives` → Initialize DriveState list
- `identity.self_model.working_memory` → Memory constraints (future)
- `identity.self_model.narrator_episodic_layer` → Logging style (future)

### Exit Criteria

- [x] ExperienceKernel class implemented
- [x] DriveState, AffectState, AttentionState, SelfSnapshot dataclasses
- [x] self_snapshot() returns complete current state
- [x] update_affect() modifies emotional state
- [x] set_attention() updates focus
- [x] activate_drive() and satisfy_drive() manage drive activation
- [x] persist_snapshot() saves to database
- [x] load_last_snapshot() restores from database
- [x] Database schema created
- [x] Tests: minimum 10 tests covering all public methods

---

## Phase 3.2: Global Workspace (Planned)

*Coming next...*

## Phase 3.3: Working Memory Manager (Planned)

*Coming next...*

## Phase 3.4: Narrator Episodic Layer (Planned)

*Coming next...*

## Phase 3.5: Persona Pack System (Planned)

*Coming next...*

## Phase 3.6: Integration & Wiring (Planned)

*Coming next...*

---

## Design Decisions

### D1: Affect Model Choice (2026-01-20)

**Decision:** Use valence-arousal-energy model for affect.

**Rationale:**
- Valence-arousal is well-established in psychology (Russell's circumplex model)
- Adding energy dimension captures fatigue/alertness separate from emotion
- Maps well to Bartholomew's persona modes (exploration=high arousal+positive valence, tactical=high arousal, healthcare=low arousal+positive valence)

**Alternatives Considered:**
- Discrete emotions only: Too limited, harder to blend
- OCC model: Too complex for MVP

### D2: Drive Activation Model (2026-01-20)

**Decision:** Each drive has base_priority (from config) and current_activation (dynamic).

**Rationale:**
- Base priority reflects Identity.yaml importance ranking
- Current activation allows situational adjustment
- Decay mechanism prevents drives from dominating indefinitely

### D3: Snapshot Persistence Strategy (2026-01-20)

**Decision:** Persist snapshots on significant state changes, not every tick.

**Rationale:**
- Reduces database writes
- Captures meaningful state transitions
- Still allows reconstruction of self-model history

**Triggers for persistence:**
- Significant affect change (delta > 0.3)
- Goal completion
- Session start/end
- Manual API call

---

## Testing Strategy

### Unit Tests
- DriveState initialization and manipulation
- AffectState valence/arousal boundaries
- AttentionState focus transitions
- SelfSnapshot serialization/deserialization

### Integration Tests
- ExperienceKernel with real Identity.yaml
- Database persistence round-trip
- Snapshot history retrieval

### Property Tests (future)
- Affect values always within bounds
- Drive activations always non-negative
- Snapshot always serializable

---

## File Locations

- Core module: `bartholomew/kernel/experience_kernel.py`
- Tests: `tests/test_experience_kernel.py`
- Schema: Integrated into `bartholomew/kernel/memory_store.py` SCHEMA constant
- Documentation: `docs/EXPERIENCE_KERNEL_IMPLEMENTATION.md`
