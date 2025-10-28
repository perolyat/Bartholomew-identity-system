# Bartholomew UI Integration Guide
**For Flutter/Flow PC Interface Development**

## 🎯 Backend API Surface

### Core Chat Interface
```python
# Primary chat interaction (via chat.py)
from identity_interpreter.orchestrator import Orchestrator

orch = Orchestrator()
response = orch.handle_input("Hello Bartholomew!")
# Returns: "[tone: warm_companion] Hello! I'm here and ready to help!"
```

### Health Monitoring API
```python
from identity_interpreter.orchestrator.system_health import check_health

health = check_health()
# Returns: {"memory": {"db": True, "cipher": True}, "orchestrator": {...}}
```

### Configuration Management
```python
from identity_interpreter.loader import load_identity, lint_identity

# Load current identity
identity = load_identity("Identity.yaml")

# Validate configuration
validation_result = lint_identity("Identity.yaml")
```

### Persona and State Access
```python
# Access personality traits
traits = identity.persona.traits  # ["curious", "playful", "kind", ...]
tone = identity.persona.tone      # ["warm_companion", "gentle_humor"]

# Session state management
orch.state.set("tone", "empathetic")
orch.state.set("emotion", "warm")
```

## 🚀 Flutter/Flow Integration Points

### 1. Chat Interface
**Purpose**: Real-time conversation with Bartholomew

**Backend Integration**:
- Import `identity_interpreter.orchestrator.Orchestrator`
- Call `handle_input(user_message)` for each message
- Parse response format: `[tone: X] [emotion: Y] <message_text>`

**UI Requirements**:
- Chat bubbles with tone/emotion indicators
- Typing indicators during processing
- Message history with timestamps

### 2. Health Dashboard
**Purpose**: System status monitoring

**Backend Integration**:
- Call `system_health.check_health()` periodically
- Monitor memory database status
- Check orchestrator logging health

**UI Requirements**:
- Health status indicators (green/yellow/red)
- Memory usage visualization
- Log file access and viewing

### 3. Personality Configuration
**Purpose**: Adjust Bartholomew's behavior and traits

**Backend Integration**:
- Read current `Identity.yaml` values
- Validate changes before applying
- Hot-reload configuration changes

**UI Requirements**:
- Sliders/toggles for personality traits
- Tone and emotion selection
- Live preview of personality changes

### 4. Memory Browser
**Purpose**: View and manage conversation history

**Backend Integration**:
- Access SQLite database at `./data/memory.db`
- Query conversation turns and context
- Export/import memory data

**UI Requirements**:
- Searchable conversation history
- Memory timeline visualization
- Export/backup functionality

## 📋 Required UI Features for Flutter/Flow

### Essential Features
1. **Chat Window**: Primary conversation interface
2. **Personality Panel**: Adjust traits, tone, emotion settings
3. **Health Monitor**: System status dashboard
4. **Memory Browser**: Conversation history and management

### Nice-to-Have Features
1. **Model Selection**: Switch between available LLMs
2. **Export Tools**: Save conversations and configurations
3. **Debug Console**: View orchestrator logs
4. **Settings Manager**: Modify Identity.yaml through UI

## 🔧 Backend Modifications Needed

### Minimal Changes (Recommended)
- **None required** - Current backend is UI-ready
- Add WebSocket wrapper around `Orchestrator.handle_input()` for real-time chat
- Create REST API endpoints wrapping existing CLI commands

### Optional Enhancements
- Streaming response support for longer conversations
- Real-time configuration validation
- WebSocket-based health status broadcasting

## 📦 Deployment Architecture

```
┌─────────────────────┐    ┌─────────────────────┐
│   Flutter/Flow UI   │    │  Bartholomew Core   │
│                     │◄──►│                     │
│ - Chat Interface    │    │ - Identity.yaml     │
│ - Personality Panel │    │ - Orchestrator      │
│ - Health Dashboard  │    │ - Memory System     │
│ - Memory Browser    │    │ - Ollama Backend    │
└─────────────────────┘    └─────────────────────┘
```

**Communication Options**:
1. **Direct Python Integration** (if Flutter supports Python)
2. **REST API** (create FastAPI wrapper)
3. **WebSocket** (for real-time features)
4. **CLI Process Communication** (spawn child processes)

## 🎨 Personality Expression in UI

### Visual Design Recommendations
- **Otter-inspired elements** (Komachi character basis)
- **Warm, approachable color palette** (blues, greens, soft oranges)
- **Gentle animations** reflecting playful nature
- **Baymax-inspired health indicators** (soft, caring visual language)

### Tone/Emotion Visualization
- **Tone Tags**: Visual badges showing current tone state
- **Emotion Indicators**: Color-coded emotion state
- **Adaptive UI**: Interface adjusts based on current personality mode

### Mode-Specific UI States
- **Exploration Mode**: Relaxed, expansive layout with rich context
- **Tactical Mode**: Compact, focused interface with quick actions
- **Healthcare Mode**: Gentle, soothing colors with wellness indicators

## ✅ Prerequisites for UI Development

### Backend Requirements (Already Met)
- ✅ Identity.yaml configuration system
- ✅ Model routing and selection
- ✅ Memory persistence and encryption
- ✅ Safety and alignment policies
- ✅ Orchestration layer with logging
- ✅ Health monitoring system

### Next Steps for Flutter/Flow
1. **Choose Integration Method**: REST API vs Direct Python vs CLI
2. **Set Up Flutter Project**: Create new Flutter application
3. **Design UI Mockups**: Based on personality traits and use cases
4. **Implement Core Features**: Chat → Health → Personality → Memory
5. **Test Integration**: Verify backend communication works correctly

## 🚦 Ready to Proceed

The Bartholomew backend is **completely ready** for UI development. No further backend work is required before transitioning to Flutter/Flow development.

**Recommended Starting Point**: Create a simple chat interface that calls `Orchestrator.handle_input()` and displays the response with tone/emotion tags.

---
*Generated by Bartholomew Identity System Analysis*
*Ready for your creative UI development journey! 🎨*