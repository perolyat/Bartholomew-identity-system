# Narrator Episodic Layer Implementation

## Overview

**Stage 3.4** implements Bartholomew's first-person narrative voice. The Narrator Episodic Layer creates episodic memories colored by emotional state, subscribes to GlobalWorkspace events, and generates narrative reflections.

From `Identity.yaml`:
```yaml
narrator_episodic_layer:
  enabled: true
  style: "supportive friend, precise, non-fluffy"
  logs:
    redact_personal_data: true
    exportable: true
```

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│ ExperienceKernel│────▶│  GlobalWorkspace │────▶│  NarratorEngine │
│   (affect,      │     │    (channels)    │     │   (episodes)    │
│    attention,   │     │                  │     │                 │
│    drives,      │     │  affect ─────────┼────▶│ AFFECT_SHIFT    │
│    goals)       │     │  attention ──────┼────▶│ ATTENTION_FOCUS │
└─────────────────┘     │  drives ─────────┼────▶│ DRIVE_*         │
                        │  goals ──────────┼────▶│ GOAL_*          │
                        └──────────────────┘     └────────┬────────┘
                                                          │
                                                          ▼
                                                ┌─────────────────┐
                                                │   SQLite DB     │
                                                │ episodic_entries│
                                                └─────────────────┘
```

## Core Components

### EpisodeType (Enum)

Categories of episodic entries:

| Type | Description |
|------|-------------|
| `AFFECT_SHIFT` | Emotional state changes |
| `ATTENTION_FOCUS` | Focus/attention changes |
| `DRIVE_ACTIVATED` | Drive activation events |
| `DRIVE_SATISFIED` | Drive satisfaction events |
| `GOAL_ADDED` | New goal created |
| `GOAL_COMPLETED` | Goal achieved |
| `OBSERVATION` | General observations |
| `REFLECTION` | Periodic reflections |
| `MEMORY_EVENT` | Memory operations |
| `SYSTEM_EVENT` | System lifecycle events |

### NarrativeTone (Enum)

Emotional coloring based on affect state:

| Tone | Valence | Arousal | Example |
|------|---------|---------|---------|
| `ENTHUSIASTIC` | ≥0.3 | ≥0.5 | "I felt a surge of..." |
| `CONCERNED` | <-0.1 | ≥0.5 | "I noticed with concern..." |
| `CONTENT` | ≥0.1 | <0.4 | "Peacefully, I observed..." |
| `SUBDUED` | <0 | <0.4 | "Quietly, I noted..." |
| `NEUTRAL` | else | else | "I noticed..." |

### EpisodicEntry (Dataclass)

```python
@dataclass
class EpisodicEntry:
    entry_id: str           # UUID
    timestamp: datetime     # When episode occurred
    episode_type: EpisodeType
    narrative: str          # First-person narrative text
    tone: NarrativeTone     # Emotional coloring
    affect_snapshot: dict   # Affect state when created
    source_event_id: str    # GlobalWorkspace event ID
    source_channel: str     # Source channel name
    tags: list[str]         # Categorization tags
    metadata: dict          # Additional data
```

### NarratorConfig (Dataclass)

Configuration from Identity.yaml:

```python
@dataclass
class NarratorConfig:
    enabled: bool = True
    style: str = "supportive friend, precise, non-fluffy"
    redact_personal_data: bool = True
    exportable: bool = True
    min_affect_change_threshold: float = 0.15
    auto_subscribe: bool = True
```

## NarratorEngine Class

### Initialization

```python
narrator = NarratorEngine(
    experience_kernel=kernel,  # For affect state
    workspace=workspace,       # For event subscription
    config=config,            # Or loads from Identity.yaml
    db_path="./data/narrator.db"
)
```

### Episode Generation Methods

```python
# Generate specific episode types
narrator.generate_affect_episode(emotion="excited")
narrator.generate_attention_episode(target="user message")
narrator.generate_drive_activated_episode(drive_id="protect_user")
narrator.generate_drive_satisfied_episode(drive_id="protect_user")
narrator.generate_goal_added_episode(goal="Help user relax")
narrator.generate_goal_completed_episode(goal="Help user relax")
narrator.generate_observation_episode(content="...", tags=[...])
narrator.generate_reflection_episode(content="...", period="daily")
```

### Persistence Methods

```python
# Save episode
entry_id = narrator.persist_episode(episode)

# Retrieve episodes
episode = narrator.get_episode(entry_id)
recent = narrator.get_recent_episodes(limit=20, since=datetime)
by_type = narrator.get_episodes_by_type(EpisodeType.AFFECT_SHIFT)
by_tag = narrator.get_episodes_by_tag("emotion")
count = narrator.get_episode_count()
```

### Reflection Generation

```python
# Generate narrative summaries
daily = narrator.generate_daily_reflection_narrative(date=datetime)
weekly = narrator.generate_weekly_reflection_narrative(week_start=datetime)
```

## Database Schema

```sql
CREATE TABLE episodic_entries (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    episode_type TEXT NOT NULL,
    narrative TEXT NOT NULL,
    tone TEXT NOT NULL,
    affect_snapshot_json TEXT,
    source_event_id TEXT,
    source_channel TEXT,
    tags_json TEXT,
    metadata_json TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_episodic_entries_timestamp ON episodic_entries(timestamp DESC);
CREATE INDEX idx_episodic_entries_type ON episodic_entries(episode_type);
CREATE INDEX idx_episodic_entries_source_channel ON episodic_entries(source_channel);
```

## GlobalWorkspace Integration

The NarratorEngine auto-subscribes to workspace channels:

| Channel | Event Type | Episode Type |
|---------|------------|--------------|
| `affect` | AFFECT_CHANGED | AFFECT_SHIFT |
| `attention` | ATTENTION_CHANGED | ATTENTION_FOCUS |
| `drives` | DRIVE_ACTIVATED | DRIVE_ACTIVATED |
| `drives` | DRIVE_SATISFIED | DRIVE_SATISFIED |
| `goals` | GOAL_ADDED | GOAL_ADDED |
| `goals` | GOAL_COMPLETED | GOAL_COMPLETED |

### Threshold Filtering

Small affect changes (below `min_affect_change_threshold`) are filtered to avoid noise:

```python
if valence_change < threshold and arousal_change < threshold:
    return  # Skip episode creation
```

## Narrative Templates

Templates are organized by tone and include placeholders:

```python
AFFECT_SHIFT = {
    NarrativeTone.ENTHUSIASTIC: [
        "I felt a surge of {emotion}—something shifted inside me.",
        "My spirits lifted considerably; I'm feeling quite {emotion} now.",
    ],
    NarrativeTone.CONCERNED: [
        "I noticed a shift toward {emotion}. Something requires my attention.",
        "A sense of {emotion} crept in. I should be mindful of this.",
    ],
    # ... more tones
}
```

Templates rotate through options to provide variety.

## Example Usage

### Basic Usage

```python
from bartholomew.kernel.narrator import NarratorEngine

narrator = NarratorEngine()

# Create an observation
episode = narrator.generate_observation_episode(
    content="The user seems engaged today",
    tags=["user", "engagement"]
)

# Persist it
narrator.persist_episode(episode)

# Generate daily reflection
reflection = narrator.generate_daily_reflection_narrative()
print(reflection)
```

### Full Integration

```python
from bartholomew.kernel.experience_kernel import ExperienceKernel
from bartholomew.kernel.global_workspace import GlobalWorkspace
from bartholomew.kernel.narrator import NarratorEngine

# Setup integrated system
workspace = GlobalWorkspace()
kernel = ExperienceKernel(workspace=workspace)
narrator = NarratorEngine(
    experience_kernel=kernel,
    workspace=workspace,
    db_path="./data/narrator.db"
)

# Activity automatically creates episodes
kernel.update_affect(valence=0.7, arousal=0.8, emotion="excited")
# -> Creates AFFECT_SHIFT episode

kernel.set_attention("user message", "user_input", intensity=0.9)
# -> Creates ATTENTION_FOCUS episode

kernel.add_goal("Help with task")
# -> Creates GOAL_ADDED episode

kernel.complete_goal("Help with task")
# -> Creates GOAL_COMPLETED episode

# Generate reflection
daily = narrator.generate_daily_reflection_narrative()
```

### Singleton Pattern

```python
from bartholomew.kernel.narrator import get_narrator, reset_narrator

# Get singleton instance
narrator = get_narrator(
    experience_kernel=kernel,
    workspace=workspace
)

# Same instance
narrator2 = get_narrator()
assert narrator is narrator2

# Reset for testing
reset_narrator()
```

## Reflection Output Format

### Daily Reflection

```markdown
# Daily Reflection - 2026-01-21

## The Day's Journey

### Emotional Landscape
- I felt a surge of excited—something shifted inside me.
- Peacefully, I noticed a gentle shift to calm.

### Focus & Attention
- My attention was drawn eagerly to user message.

### Goals & Achievements
- I set a new goal: Help with task. I'm excited to pursue this!
- I completed my goal: Help with task! This feels wonderful.

## Summary

- Total episodes: 4
- Episode types: affect_shift, attention_focus, goal_added, goal_completed
```

### Weekly Reflection

```markdown
# Weekly Reflection - Week 4, 2026

## Week Overview

This week saw 25 recorded moments across my experience:

- **Affect Shift**: 8 episodes
- **Attention Focus**: 7 episodes
- **Goal Added**: 5 episodes
- **Goal Completed**: 5 episodes

## Highlights

- I felt a surge of excited—something shifted inside me.
- My attention was drawn eagerly to important task.
- I set a new goal: Complete project.

## Goals Progress

- Goals set: 5
- Goals completed: 5

## Emotional Journey

Experienced 8 notable emotional shifts this week.
```

## Testing

### Run Tests

```bash
pytest -q tests/test_narrator.py
```

### Test Coverage

| Test Class | Coverage |
|------------|----------|
| TestEpisodeType | Episode type enum values |
| TestNarrativeTone | Tone enum values |
| TestEpisodicEntry | Creation, serialization, roundtrip |
| TestNarratorConfig | Defaults, Identity.yaml loading |
| TestNarrativeTemplates | Template existence, placeholders |
| TestNarratorEngineInit | Initialization, schema creation |
| TestToneDetermination | Affect-to-tone mapping |
| TestEpisodeGeneration | All episode generation methods |
| TestEpisodeGenerationWithKernel | Kernel integration |
| TestPersistence | CRUD operations |
| TestWorkspaceIntegration | Auto-subscription, event handling |
| TestReflectionNarratives | Daily/weekly generation |
| TestSingleton | Singleton pattern |
| TestEdgeCases | Edge cases, unicode, long content |
| TestFullIntegration | End-to-end lifecycle |

## Exit Criteria

- [x] NarratorEngine class with episode generation
- [x] EpisodicEntry dataclass with serialization
- [x] Database schema for episodic entries
- [x] GlobalWorkspace subscription for auto-episodes
- [x] Emotional coloring based on AffectState
- [x] Persistence and retrieval methods
- [x] Daily and weekly reflection narrative generation
- [x] Comprehensive test suite (55+ tests)
- [x] Implementation documentation

## Verification Command

```bash
pytest -q tests/test_narrator.py
```
