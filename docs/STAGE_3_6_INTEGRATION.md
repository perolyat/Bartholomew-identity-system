# Stage 3.6: Integration & Wiring Implementation

**Status:** ✅ Implemented
**Date:** 2026-01-21

## Overview

Stage 3.6 connects all Stage 3 modules (Experience Kernel, Global Workspace, Working Memory, Narrator, Persona Pack) to the running daemon and exposes them via REST API endpoints.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      KernelDaemon                                │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────┐    ┌─────────────────┐    ┌──────────────┐    │
│  │  EventBus   │    │  GlobalWorkspace │◄───┤  Narrator    │    │
│  │  (legacy)   │    │  (Stage 3.2)     │    │  (Stage 3.4) │    │
│  └─────────────┘    └────────┬─────────┘    └──────────────┘    │
│                              │                                   │
│                              ▼ events                            │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              ExperienceKernel (Stage 3.1)                │    │
│  │  ┌─────────┐  ┌───────────┐  ┌────────┐  ┌───────────┐  │    │
│  │  │ Drives  │  │  Affect   │  │Attention│  │  Goals    │  │    │
│  │  └─────────┘  └───────────┘  └────────┘  └───────────┘  │    │
│  └─────────────────────────────────────────────────────────┘    │
│                              │                                   │
│                              ▼ boosts                            │
│  ┌─────────────────┐    ┌─────────────────┐                     │
│  │ WorkingMemory   │    │ PersonaPackMgr  │                     │
│  │ (Stage 3.3)     │    │ (Stage 3.5)     │                     │
│  └─────────────────┘    └─────────────────┘                     │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼ REST API
┌─────────────────────────────────────────────────────────────────┐
│                      FastAPI (app.py)                            │
├─────────────────────────────────────────────────────────────────┤
│  /api/self          - Self snapshot                              │
│  /api/self/affect   - Affect state                               │
│  /api/self/attention- Attention state                            │
│  /api/self/drives   - Drives list                                │
│  /api/self/goals    - Active goals                               │
│  /api/episodes/*    - Episodic entries                           │
│  /api/persona/*     - Persona management                         │
│  /api/working_memory- Working memory                             │
└─────────────────────────────────────────────────────────────────┘
```

## Daemon Integration

### Module Instantiation (daemon.py __init__)

```python
# Stage 3: Experience Kernel modules
self.workspace = GlobalWorkspace()
self.experience = ExperienceKernel(
    db_path=db_path,
    workspace=self.workspace,
)
self.narrator = NarratorEngine(db_path=db_path)
self.working_memory = WorkingMemoryManager(
    workspace=self.workspace,
    experience_kernel=self.experience,
)
self.persona_manager = PersonaPackManager(
    experience_kernel=self.experience,
    workspace=self.workspace,
    db_path=db_path,
)
```

### Lifecycle Hooks

#### start()
1. Initialize MemoryStore
2. Load last experience snapshot (if exists)
3. Subscribe Narrator to GlobalWorkspace
4. Emit `SYSTEM_EVENT` (startup) to workspace
5. Activate default persona if none active
6. Start background tasks

#### stop()
1. Emit `SYSTEM_EVENT` (shutdown) to workspace
2. Persist experience snapshot
3. Cancel background tasks
4. Close MemoryStore

#### _system_tick()
1. Decay affect toward baseline (rate=0.02)
2. Check for auto persona activation based on context tags
3. Execute planner decision
4. Publish actions to EventBus

## API Endpoints

### Self-State Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/self` | GET | Get complete self-snapshot (affect, attention, drives, goals) |
| `/api/self/affect` | GET | Get current affect state |
| `/api/self/affect` | PUT | Update affect (valence, arousal, energy) |
| `/api/self/attention` | GET | Get current attention state |
| `/api/self/attention` | PUT | Set attention focus |
| `/api/self/attention` | DELETE | Clear attention (set to idle) |
| `/api/self/drives` | GET | Get all drives |
| `/api/self/drives/top` | GET | Get top N drives by effective activation |
| `/api/self/drives/{id}/activate` | POST | Activate a drive |
| `/api/self/drives/{id}/satisfy` | POST | Satisfy a drive |
| `/api/self/goals` | GET | Get active goals |
| `/api/self/goals` | POST | Add a new goal |
| `/api/self/goals/{goal}` | DELETE | Complete a goal |

### Episode Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/episodes/recent` | GET | Get recent episodic entries |
| `/api/episodes/{id}` | GET | Get specific episode |
| `/api/episodes/by-type/{type}` | GET | Filter episodes by type |
| `/api/episodes/by-tag/{tag}` | GET | Filter episodes by tag |

### Persona Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/persona/current` | GET | Get active persona pack |
| `/api/persona/list` | GET | List all available personas |
| `/api/persona/switch` | POST | Switch to different persona |
| `/api/persona/history` | GET | Get switch history |
| `/api/persona/{pack_id}` | GET | Get specific persona pack |

### Working Memory Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/working_memory` | GET | Get working memory contents |
| `/api/working_memory/context` | GET | Get rendered context string |
| `/api/working_memory` | DELETE | Clear working memory |

## Example API Responses

### GET /api/self
```json
{
  "snapshot": {
    "timestamp": "2026-01-21T03:45:00+00:00",
    "affect": {
      "valence": 0.6,
      "arousal": 0.3,
      "energy": 0.7,
      "baseline_valence": 0.5,
      "baseline_arousal": 0.3,
      "baseline_energy": 0.6
    },
    "attention": {
      "target": "user_conversation",
      "type": "focused",
      "intensity": 0.8,
      "context_tags": ["chat", "active"],
      "since": "2026-01-21T03:44:00+00:00"
    },
    "drives": [...],
    "active_goals": ["help_user_with_task"],
    "context": {}
  },
  "active_persona": "default",
  "working_memory_tokens": 1250
}
```

### GET /api/persona/current
```json
{
  "active": true,
  "pack": {
    "pack_id": "default",
    "name": "Bartholomew",
    "description": "The default kind companion persona",
    "tone": ["warm", "helpful", "kind", "curious"],
    "style": {
      "brevity": "balanced",
      "formality": "conversational",
      "warmth": 0.8,
      "directness": 0.6
    },
    "archetype": "companion",
    "inspirations": ["Komachi", "Baymax"]
  }
}
```

### GET /api/episodes/recent
```json
{
  "episodes": [
    {
      "entry_id": "ep-abc123",
      "timestamp": "2026-01-21T03:40:00+00:00",
      "episode_type": "affect_shift",
      "narrative": "I'm feeling more upbeat now...",
      "tone": "content",
      "affect_snapshot": {...},
      "tags": ["positive_shift"]
    }
  ],
  "count": 1
}
```

## Event Flow

### Affect Change Flow
```
User interaction
    │
    ▼
ExperienceKernel.update_affect()
    │
    ├──► GlobalWorkspace.emit_affect_changed()
    │         │
    │         ▼
    │    Narrator receives event
    │         │
    │         ▼
    │    If threshold met, generate episode
    │         │
    │         ▼
    │    Persist to episodic_entries table
    │
    └──► Return updated affect state
```

### Persona Switch Flow
```
API request: POST /api/persona/switch
    │
    ▼
PersonaPackManager.switch_pack()
    │
    ├──► Remove old drive boosts from ExperienceKernel
    │
    ├──► Apply new drive boosts to ExperienceKernel
    │
    ├──► Log switch to persona_switch_log table
    │
    ├──► GlobalWorkspace.emit_persona_switched()
    │
    └──► Return new active pack
```

## Database Schema Additions

Stage 3.6 uses existing schemas from Stage 3.1-3.5:

- `experience_snapshots` - ExperienceKernel state persistence
- `episodic_entries` - Narrator episode storage
- `persona_switch_log` - Persona switch audit log

## Testing

Run Stage 3.6 integration tests:
```bash
pytest -q tests/test_stage3_integration.py
```

Test categories:
- `TestExperienceKernelIntegration` - Kernel with workspace
- `TestGlobalWorkspaceIntegration` - Multi-subscriber events
- `TestWorkingMemoryIntegration` - Attention boost
- `TestNarratorIntegration` - Auto-episode generation
- `TestPersonaPackIntegration` - Drive boosts, switch events
- `TestDaemonIntegration` - Module presence, lifecycle
- `TestAPIIntegration` - JSON serialization
- `TestFullLifecycle` - Complete boot→runtime→persist→restore

## Verification Commands

```bash
# Run all Stage 3 tests
pytest -q tests/test_experience_kernel.py tests/test_global_workspace.py \
       tests/test_working_memory.py tests/test_narrator.py \
       tests/test_persona_pack.py tests/test_stage3_integration.py

# Start the API server
uvicorn bartholomew_api_bridge_v0_1.services.api.app:app --reload

# Test endpoints (with server running)
curl http://localhost:8000/api/self
curl http://localhost:8000/api/persona/list
curl http://localhost:8000/api/episodes/recent
```

## Exit Criteria Checklist

- [x] All Stage 3 modules instantiated in KernelDaemon
- [x] Modules connected via GlobalWorkspace events
- [x] Experience state persists across daemon restarts
- [x] Narrator auto-generates episodes from workspace events
- [x] Persona switching works and applies drive boosts
- [x] API endpoints expose all Stage 3 functionality
- [x] Integration tests verify cross-module communication
- [x] Documentation complete

## Future Enhancements

1. **UI Integration** - Add Stage 3 panels to minimal UI
2. **Metrics** - Add Prometheus metrics for Stage 3 operations
3. **Hot Reload** - Allow persona pack changes without restart
4. **Episode Search** - Full-text search across episodes
5. **Working Memory Persistence** - Persist WM across sessions
