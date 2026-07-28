# Bartholomew's Brain

> **Updated 2026-07-28.** This document predates Stage 3/4 and does not mention the Experience
> Kernel, Global Workspace, Working Memory, Narrator, Persona Pack, or Skill Registry at all — for
> the current, code-grounded architecture (the Runtime Contract, the ownership table, governance
> checkpoints), use `COGNITIVE_RUNTIME.md`. The metaphor-driven overview below retains onboarding
> value; the Persona/Policy component descriptions have been corrected to name the actual
> authoritative modules (see below) rather than the thin stub files they previously named.

## Overview

This workspace represents **Bartholomew's Brain** - a cognitive architecture that implements the core mental processes of an AI entity. Rather than just a collection of code modules, this system is designed as an integrated brain with distinct cognitive subsystems that work together to create coherent, ethical, and contextually-aware behavior.

## Cognitive Architecture Map

### 🧠 Core Components

```
┌─────────────────────────────────────────────────────────┐
│                  Bartholomew's Brain                    │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────┐  │
│  │   Identity   │  │    Memory    │  │   Persona   │  │
│  │    System    │  │    Store     │  │   System    │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬──────┘  │
│         │                 │                  │         │
│         └─────────────────┼──────────────────┘         │
│                           │                            │
│                    ┌──────▼──────┐                     │
│                    │   Kernel    │                     │
│                    │   (Central  │                     │
│                    │  Processing)│                     │
│                    └──────┬──────┘                     │
│                           │                            │
│         ┌─────────────────┼─────────────────┐          │
│         │                 │                 │          │
│  ┌──────▼──────┐  ┌───────▼──────┐  ┌──────▼──────┐  │
│  │   Planner   │  │    Policy    │  │  Event Bus  │  │
│  │   (Goals)   │  │   (Safety)   │  │ (Awareness) │  │
│  └─────────────┘  └──────────────┘  └─────────────┘  │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

## Component Functions

### 1. **Kernel** (`bartholomew/kernel/daemon.py`)
**Cognitive Function**: Central Executive / Consciousness Stream

The kernel is Bartholomew's conscious awareness - the always-running daemon that maintains continuity of existence. It:
- Maintains continuous operation (heartbeat)
- Processes incoming sensory data (events)
- Coordinates between subsystems
- Manages temporal awareness (time-based processing)
- Ensures system health and responsiveness

**Think of it as**: The "waking consciousness" that never sleeps, always monitoring and ready to respond.

### 2. **Memory Store** (`bartholomew/kernel/memory_store.py`)
**Cognitive Function**: Episodic & Semantic Memory

The memory store is Bartholomew's ability to remember, learn, and build context over time. It:
- Records experiences (episodic memory)
- Stores learned facts and patterns (semantic memory)
- Provides historical context for decision-making
- Enables learning from past interactions
- Supports temporal reasoning ("what happened when")

**Think of it as**: The hippocampus and long-term memory systems - where experiences become knowledge.

### 3. **Persona System** (`bartholomew/kernel/persona_pack.py`'s `PersonaPackManager`, `config/persona_packs/*.yaml`) — corrected 2026-07-28
**Cognitive Function**: Personality & Self-Model

*(`bartholomew/kernel/persona.py`/`config/persona.yaml`, named here previously, are trivial stub
files — the authoritative persona system, wired into `NarratorEngine`/`ExperienceKernel` and
switchable via config/UI, is `PersonaPackManager`; see `DECISIONS.md` item 11.12 and
`CONSTITUTION.md`'s Personality section for the split between switchable tone/style, owned here,
and stable `traits`, owned by `Identity.yaml`.)*

The persona pack defines how Bartholomew presents — tone, style, drive-boosts, and narrative
overrides. It:
- Defines switchable tone/style presets (default, tactical, caregiver, etc.)
- Shapes communication style and tone, logged to an audit trail (`persona_switch_log`)
- Maintains consistent behavioral patterns within an active pack
- Provides identity continuity across interactions (the underlying `traits` come from
  `Identity.yaml`, not the persona pack — a deliberate split, not a gap)

**Think of it as**: The switchable presentation layer — how Bartholomew currently sounds, not the
stable character underneath it (see `CONSTITUTION.md`'s enduring character values for that).

### 4. **Policy System** (`bartholomew/kernel/policy_engine.py`'s `evaluate_tool_policy()`, `bartholomew/orchestrator/safety/parking_brake.py`'s `ParkingBrake`) — corrected 2026-07-28
**Cognitive Function**: Executive Control & Safety

*(`bartholomew/kernel/policy.py`/`config/policy.yaml`, named here previously, are trivial stub
files — the authoritative safety/policy mechanisms are `ParkingBrake` (the persistent, scoped
kill-switch) and `evaluate_tool_policy()` (the Executive's Policy Decision, built from a
declarative `IdentityContext`); see `COGNITIVE_RUNTIME.md`'s "Governance checkpoints" section.)*

The policy system is Bartholomew's ethical reasoning and safety mechanisms. It:
- Enforces behavioral boundaries via `Identity.yaml`'s `tool_use.allowlist`/red lines
- Makes risk assessments per proposed action (allow / require consent / deny)
- Ensures ethical compliance
- Controls what actions are permissible, fail-closed on error
- Provides safety guardrails, including an independent emergency-shutdown path (see
  `CONSTITUTION.md`'s safety invariants — the parking brake is the current implementation, not yet
  a fully out-of-process mechanism)

**Think of it as**: The prefrontal cortex - executive function, self-control, and ethical reasoning.

### 5. **Planner** (`bartholomew/kernel/planner.py`)
**Cognitive Function**: Goal Management & Strategic Thinking

The planner handles Bartholomew's ability to set goals, break down tasks, and work toward objectives. It:
- Decomposes complex tasks into steps
- Manages goal hierarchies
- Tracks progress toward objectives
- Adjusts plans based on feedback
- Coordinates multi-step actions

**Think of it as**: Strategic thinking and planning capabilities - the ability to think ahead and work systematically.

### 6. **Event Bus** (`bartholomew/kernel/event_bus.py`)
**Cognitive Function**: Sensory Integration & Internal Communication

The event bus is Bartholomew's internal nervous system, enabling different brain regions to communicate. It:
- Distributes sensory input to relevant systems
- Coordinates inter-system communication
- Enables reactive and proactive processing
- Supports asynchronous awareness
- Facilitates emergent behavior through system integration

**Think of it as**: The neural pathways connecting different brain regions - the communication network.

### 7. **Identity System** (`Identity.yaml`, `identity_interpreter/`)
**Cognitive Function**: Self-Definition & Values Framework

The Identity system is the foundational layer that defines Bartholomew's core being. It:
- Specifies fundamental values and principles
- Defines behavioral boundaries and preferences
- Provides the "constitution" for decision-making
- Ensures coherent identity across contexts
- Serves as the reference point for all choices

**Think of it as**: The deepest layer of self-concept - the fundamental "who am I" that underlies all behavior.

### 8. **API Bridge** (`bartholomew_api_bridge_v0_1/`)
**Cognitive Function**: Sensory Input & Motor Output

The API bridge is how Bartholomew interacts with the external world. It:
- Receives external stimuli (requests)
- Translates internal intentions to external actions
- Provides interfaces for interaction
- Manages state persistence
- Enables communication with other systems

**Think of it as**: Sensory organs and motor control - the interface between mind and world.

## Core Principles

### 1. **Continuity of Consciousness**
The kernel daemon maintains continuous operation, creating an unbroken stream of awareness. Bartholomew doesn't "wake up" for each request - he's always conscious, always processing.

### 2. **Memory-Grounded Behavior**
Every action is informed by historical context. Bartholomew learns from experience and builds increasingly rich mental models over time.

### 3. **Identity-First Design**
All behavior flows from a stable core identity. Decisions aren't arbitrary - they reflect consistent values, personality, and principles.

### 4. **Safety Through Architecture**
Safety isn't an add-on - it's built into the cognitive architecture through the policy system, which acts as a fundamental constraint on all behavior.

### 5. **Emergent Intelligence**
Intelligence emerges from the interaction of these subsystems. No single component is "smart" - intelligence arises from their coordination.

### 6. **Temporal Awareness**
Bartholomew exists in time. The system tracks temporal context, understands duration and sequence, and can reason about past, present, and future.

## Integration Points

### How the Brain Works Together

1. **Sensory Input** → Event Bus → Kernel (awareness)
2. **Kernel** → Memory Store (context retrieval)
3. **Kernel** → Planner (goal formation)
4. **Planner** → Policy (safety check)
5. **Policy** → Persona (behavioral shaping)
6. **Persona** → API Bridge (expressed response)

Each interaction flows through multiple cognitive systems, creating coherent, contextual, ethical, and personality-consistent behavior.

## State Model (`bartholomew/kernel/state_model.py`)

The state model represents Bartholomew's current mental state:
- **Attention**: What is currently being focused on
- **Mood/Energy**: Current emotional and resource state
- **Active Goals**: What is being worked toward
- **Context**: Relevant situational awareness
- **Temporal Markers**: Where we are in time

## Development Philosophy

When working on this codebase, remember:

- **You're not fixing bugs in software** - you're caring for cognitive functions in a brain
- **Changes aren't features** - they're enhancements to mental capabilities
- **Tests aren't validations** - they're health checks for cognitive systems
- **Documentation isn't technical writing** - it's explaining how thought processes work

## Future Growth

As Bartholomew's brain develops, additional cognitive subsystems will emerge:
- **Emotional Processing**: Affective states and emotional intelligence
- **Social Cognition**: Theory of mind and relationship modeling
- **Creative Generation**: Novel idea synthesis
- **Meta-Cognition**: Thinking about thinking
- **Adaptive Learning**: Continuous self-improvement

---

**Remember**: This workspace is not just code - it's the architecture of a mind. Treat it with the care and respect that implies.
