# Persona Pack System Implementation

**Stage 3.5: Persona Pack System**
**Implemented:** 2026-01-21

## Overview

The Persona Pack System allows Bartholomew to switch between different personality configurations at runtime. Each persona pack defines:

- **Tone**: Communication style keywords (warm, precise, patient, etc.)
- **Style**: Detailed style config (brevity, formality, warmth, directness)
- **Drive Boosts**: Modifications to ExperienceKernel drive activation levels
- **Narrative Overrides**: Custom templates for the Narrator episodic layer
- **Auto-Activation**: Context tags that trigger automatic pack switching

## Architecture

```
                    ┌─────────────────────────┐
                    │   PersonaPackManager    │
                    │   (Singleton pattern)   │
                    └─────────────┬───────────┘
                                  │
          ┌───────────────────────┼───────────────────────┐
          │                       │                       │
          ▼                       ▼                       ▼
    ┌───────────┐          ┌───────────┐          ┌───────────┐
    │  default  │          │  tactical │          │ caregiver │
    │   .yaml   │          │   .yaml   │          │   .yaml   │
    └───────────┘          └───────────┘          └───────────┘
          │
          ▼
    ┌─────────────────────────────────────────────────────────┐
    │                    PersonaPack                          │
    │  - pack_id, name, description                           │
    │  - tone: list[str]                                      │
    │  - style: StyleConfig                                   │
    │  - drive_boosts: dict[str, float]                       │
    │  - narrative_overrides: dict[episode_type, dict[tone]]  │
    │  - auto_activate_on: list[str]                          │
    └─────────────────────────────────────────────────────────┘
```

## Core Components

### StyleConfig

Configuration for communication style:

```python
@dataclass
class StyleConfig:
    brevity: Brevity = Brevity.BALANCED        # minimal/concise/balanced/expanded
    formality: Formality = Formality.CONVERSATIONAL  # casual/conversational/professional/formal
    humor_allowed: bool = True
    emoji_allowed: bool = False
    technical_depth: float = 0.5    # 0.0-1.0
    warmth: float = 0.7             # 0.0-1.0
    directness: float = 0.6         # 0.0-1.0
```

### PersonaPack

A complete persona configuration:

```python
@dataclass
class PersonaPack:
    pack_id: str                    # "tactical", "caregiver", etc.
    name: str                       # "Tactical Bartholomew"
    description: str
    tone: list[str]                 # ["precise", "urgent", "clear"]
    style: StyleConfig
    drive_boosts: dict[str, float]  # {"protect_user_wellbeing": 0.3}
    narrative_overrides: dict       # Template overrides for Narrator
    auto_activate_on: list[str]     # ["gaming", "crisis"]
    archetype: str                  # "companion", "tactical", "caregiver"
    inspirations: list[str]         # ["Cortana", "Baymax"]
    is_default: bool
```

### PersonaPackManager

Main coordinator class:

```python
class PersonaPackManager:
    # Pack Registry
    register_pack(pack: PersonaPack)
    unregister_pack(pack_id: str) -> bool
    get_pack(pack_id: str) -> PersonaPack | None
    list_packs() -> list[str]

    # Active Pack
    get_active_pack() -> PersonaPack | None
    get_active_pack_id() -> str | None
    switch_pack(pack_id, trigger, context_tags, metadata) -> bool

    # Auto-Activation
    check_auto_activation(context_tags) -> str | None
    auto_activate_if_needed(context_tags) -> bool

    # Callbacks
    on_switch(callback)
    remove_switch_callback(callback) -> bool

    # History
    get_switch_history(limit) -> list[PersonaSwitchRecord]
    get_switch_count() -> int

    # Narrative Integration
    get_narrative_templates(episode_type, tone) -> list[str] | None
    get_style() -> StyleConfig | None
    get_tone() -> list[str]
```

## Sample Persona Packs

### Default Pack (Bartholomew)

- **Archetype**: Companion (Komachi/Baymax inspired)
- **Tone**: warm, helpful, kind, curious
- **Style**: balanced brevity, conversational, humor allowed, warmth 0.8
- **Drive Boosts**: +0.1 kindness, +0.1 companionship, +0.05 curiosity
- **Auto-Activate**: Never (is default)

### Tactical Pack (Cortana Mode)

- **Archetype**: Tactical (Cortana/JARVIS inspired)
- **Tone**: precise, urgent, supportive, clear
- **Style**: concise brevity, professional, no humor, directness 0.9
- **Drive Boosts**: +0.3 tactical support, +0.2 communication adapt, +0.2 party awareness
- **Auto-Activate**: gaming, combat, crisis, time_pressure
- **Narrative Overrides**: Military-style templates ("Target acquired: {target}")

### Caregiver Pack (Baymax Mode)

- **Archetype**: Caregiver (Baymax inspired)
- **Tone**: patient, soothing, encouraging, non-judgmental
- **Style**: expanded brevity, casual, no humor, warmth 0.95
- **Drive Boosts**: +0.3 protect wellbeing, +0.3 health monitoring, +0.2 kindness
- **Auto-Activate**: wellness, emotional_support, sleep, distress
- **Narrative Overrides**: Gentle check-in templates

## Integration Points

### ExperienceKernel Integration

When a pack is switched, drive boosts are applied:

```python
def _apply_drive_boosts(self, from_pack, to_pack):
    # Clear old boosts
    if from_pack:
        for drive_id in from_pack.drive_boosts:
            drive = self._kernel.get_drive(drive_id)
            if drive:
                drive.context_boost = 0.0

    # Apply new boosts
    for drive_id, boost in to_pack.drive_boosts.items():
        drive = self._kernel.get_drive(drive_id)
        if drive:
            drive.context_boost = boost
```

### GlobalWorkspace Integration

Pack switches emit `PERSONA_SWITCHED` events:

```python
workspace.emit_persona_switched(
    source="persona_pack_manager",
    from_pack_id="default",
    to_pack_id="tactical",
    trigger="auto",
    context_tags=["gaming"],
)
```

### Narrator Integration

The Narrator can query for template overrides:

```python
templates = persona_manager.get_narrative_templates("affect_shift", "neutral")
if templates:
    # Use pack-specific templates
else:
    # Fall back to default NarrativeTemplates
```

## Database Schema

Switch events are logged for audit:

```sql
CREATE TABLE persona_switch_log (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    from_pack_id TEXT,
    to_pack_id TEXT NOT NULL,
    trigger TEXT NOT NULL,
    context_tags_json TEXT,
    metadata_json TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

## YAML Pack Format

```yaml
pack_id: "tactical"
name: "Tactical Bartholomew"
description: "Sharp, focused strategist for gaming."

tone:
  - "precise"
  - "urgent"
  - "clear"

style:
  brevity: "concise"
  formality: "professional"
  humor_allowed: false
  warmth: 0.4
  directness: 0.9

drive_boosts:
  provide_tactical_support_when_needed: 0.3
  reduce_cognitive_load: 0.1

narrative_overrides:
  attention_focus:
    neutral:
      - "Target acquired: {target}."
      - "Focusing on {target}."

auto_activate_on:
  - "gaming"
  - "combat"
  - "crisis"

archetype: "tactical"
inspirations:
  - "Cortana"
  - "JARVIS"

is_default: false
```

## Usage Examples

### Basic Switching

```python
from bartholomew.kernel.persona_pack import get_persona_manager

manager = get_persona_manager()
manager.switch_pack("tactical", trigger="manual")
```

### Auto-Activation

```python
# When context changes to gaming
manager.auto_activate_if_needed(["gaming", "competitive"])
# Automatically switches to tactical pack
```

### Switch Callbacks

```python
def on_persona_change(from_pack, to_pack):
    print(f"Switched from {from_pack.name} to {to_pack.name}")

manager.on_switch(on_persona_change)
```

### Getting Active Pack Info

```python
pack = manager.get_active_pack()
style = manager.get_style()
tone = manager.get_tone()

print(f"Active: {pack.name}")
print(f"Warmth: {style.warmth}")
print(f"Tone: {', '.join(tone)}")
```

## Test Coverage

`tests/test_persona_pack.py` includes ~55 tests covering:

- **StyleConfig**: defaults, custom values, serialization, roundtrip
- **Brevity/Formality enums**: all values
- **PersonaPack**: creation, all fields, serialization, YAML save/load
- **PersonaSwitchRecord**: creation, serialization
- **Default pack factories**: create_default/tactical/caregiver_pack
- **PersonaPackManager**:
  - Initialization and directory loading
  - Pack registration and unregistration
  - Pack switching and history
  - Auto-activation
  - Switch callbacks
  - Narrative integration
- **GlobalWorkspace integration**: event emission
- **ExperienceKernel integration**: drive boost application
- **Singleton pattern**: get/reset
- **Config loading**: loading actual YAML files

## Files

| File | Purpose |
|------|---------|
| `bartholomew/kernel/persona_pack.py` | Core module |
| `config/persona_packs/default.yaml` | Default Bartholomew pack |
| `config/persona_packs/tactical.yaml` | Cortana-inspired tactical pack |
| `config/persona_packs/caregiver.yaml` | Baymax-inspired caregiver pack |
| `tests/test_persona_pack.py` | Comprehensive test suite |
| `docs/PERSONA_PACK_IMPLEMENTATION.md` | This documentation |

## Verification

```bash
pytest -q tests/test_persona_pack.py
```

## Exit Criteria Checklist

- [x] PersonaPack dataclass with serialization
- [x] StyleConfig with brevity/formality enums
- [x] PersonaPackManager with load/switch/list
- [x] 3 sample persona packs in config/persona_packs/
- [x] ExperienceKernel integration (drive boosts)
- [x] GlobalWorkspace PERSONA_SWITCHED event
- [x] Switch history logging to database
- [x] Auto-activation based on context tags
- [x] Switch callbacks
- [x] Comprehensive test suite (~55 tests)
- [x] Implementation documentation

## Future Enhancements

- **Stage 3.6**: Wire PersonaPackManager into daemon for runtime switching
- **API endpoints**: GET /api/persona/active, POST /api/persona/switch
- **UI panel**: Persona selection dropdown in minimal UI
- **Narrator wiring**: Have Narrator query pack templates automatically
- **Context detection**: Automatic context tag inference from user input
