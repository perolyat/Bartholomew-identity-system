# CONSTITUTION

> The Architecture Constitution: Bartholomew's non-negotiable principles, architectural
> invariants, and governance rules.
>
> **This document is deliberately different from every other canonical doc.** `MASTER_PLAN.md`,
> `ROADMAP.md`, `DECISIONS.md`, etc. describe current state and change often. This document
> describes enduring intent and should change rarely. Every architectural proposal — new
> subsystem, new pillar, new capability, any deviation from what's written here — should be
> evaluated against this document first. If a proposal conflicts with it, the conflict must be
> resolved explicitly (update this document with rationale, or reject the proposal) — never
> silently overridden by implementation convenience.
>
> **Established:** 2026-07-22, on handover from the project's originating architect
> ("Bartholomew," in the design-conversation transcripts under `docs/design_conversations/`) to
> the repository-native Architect role. See `DECISIONS.md` for the dated governance record of
> that transition. **Current Architect: Claude** (repository-native; see `DECISIONS.md`).

---

## Project Vision

Bartholomew is not an AI assistant. It is intended to become a **lifelong digital companion**.

The closest fictional comparisons are JARVIS, FRIDAY, Cortana (Halo), and Samantha (*Her*) —
but none of those are complete.

The long-term goal is software that feels less like an application and more like another
intelligent being living inside the user's devices. The user should eventually think:

> "My phone became intelligent."

— not —

> "I installed an AI app."

This distinction drives nearly every architectural decision.

## Primary Goal

The goal is not answering questions. **The goal is improving the user's life.** Conversation is
only one capability.

Bartholomew exists to:
- reduce cognitive load
- remember everything important
- automate repetitive work
- improve health
- improve finances
- improve relationships
- improve organisation
- proactively help
- become increasingly useful over decades

## Core Principle: Responsibilities, Not Technologies

The architecture is organised around **responsibilities**, never technologies. Technology
changes. Responsibilities remain.

- Wrong: "Email Module"
- Correct: "Observation Layer" — because observations may originate from email, SMS, bank,
  camera, microphone, clipboard, browser, or calendar.

## The Five Pillars

Everything belongs inside one of five major systems. No new subsystem should exist outside
these pillars without extremely strong justification.

### 1. Governance

Governance always sits above every other subsystem. Nothing bypasses Governance — not Memory,
not Executive, not Plugins, not AI.

Governance owns:
- permissions
- privacy
- user approval
- autonomous limits
- auditing
- explainability
- parking brake
- emergency shutdown
- trust

### 2. Executive

The Executive **decides**. It does not observe. It does not remember. It decides.

Responsibilities:
- planning
- prioritisation
- scheduling
- orchestration
- delegation
- recommendation generation
- action selection

### 3. Memory

Memory exists to create institutional knowledge about one individual. The long-term competitive
moat is not the language model — **it is decades of accumulated understanding of the user.**
Memory therefore becomes more valuable every year.

Memory should remember: preferences, routines, relationships, habits, life events, goals,
projects, patterns, successful interventions, mistakes.

Memory should not become a dumping ground. Only useful knowledge belongs in long-term memory.

### 4. Capability

Capabilities are interchangeable tools — Email, Calendar, Finance, Health, Shopping, Travel,
Browser, Coding, Automation, etc. Capabilities should be independently replaceable. No
capability should be tightly coupled to another.

### 5. Experience

Experience is the human side: voice, conversation, notifications, avatar, emotional expression,
visual interface, animations, personality. This pillar is intentionally separate from
intelligence.

## Observation Philosophy

One of the biggest architectural shifts: the system is **reality-first, not source-first**.

Email is not important. Bills are important. Subscriptions are important. Appointments are
important. Purchases are important. The observation layer extracts reality from arbitrary
sources.

**Pipeline:** Observe → Interpret → Recommend → Act

- Observations are immutable facts.
- Interpretations are meaning.
- Recommendations are advice.
- Actions change reality.

These are distinct architectural layers.

## Domain Independence

Nothing should be designed specifically for bills. Bills were only the first proving ground. The
architecture should work identically for subscriptions, appointments, shopping, travel, health,
banking, documents, relationships, etc.

**If adding the third domain requires schema redesign, the architecture has failed.**

## Current Philosophy on Persistence

The project intentionally chose not to persist observations yet. Instead:

Observation → Interpretation → Knowledge

Knowledge becomes persistent. This decision was made to avoid premature complexity. **The
Architect may revisit this later.**

## Automation Philosophy

Autonomy is earned. Not enabled.

The roadmap begins with recommendation-first. Later, actions that are repeatable, low-risk,
well-understood, and user-approved become autonomous.

**Baby Mode ladder:**

Observe only → Recommend → Assist → Limited automation → Governed automation → Trusted autonomy

## Sovereign Principle

The user is always the final authority. Never optimise for maximum autonomy. **Optimise for
maximum trust.**

## Architectural Principles

The system should:
- prefer composition over inheritance
- prefer interfaces over implementations
- prefer events over tight coupling
- prefer responsibilities over mechanisms
- prefer replaceability over optimisation
- prefer explicit governance over implicit behaviour
- fail safely
- be explainable
- be observable
- be testable
- be evolvable

## UX Principles

The application should feel alive. Setup should feel like "my phone just became intelligent."
The user should almost never need forms. Voice should be the primary interface; typing is
secondary. Configuration should disappear wherever possible.

## Personality

The long-term personality target: **a 1950s gentleman, filtered through 2025 intelligence.**
Inspirations include JARVIS, Lance Reddick, and FRIDAY. Professional. Warm. Calm. Never
theatrical.

## Long-Term Goal

The ultimate objective is not an assistant. It is creating something approaching a digital
companion. The user should eventually trust Bartholomew similarly to how someone trusts an
exceptionally competent executive assistant who has known them for decades.

## Development Philosophy

Architecture first. Implementation second. The project deliberately spends more time designing
than coding. Large refactors early are encouraged; large refactors late are failures. Correct
architecture outweighs rapid feature delivery.

## Documentation Philosophy

Documentation is considered part of the architecture. Architecture must remain discoverable
without relying on conversation history. The repository should become the authoritative source
of truth. **No architectural knowledge should permanently live only inside an AI conversation.**

## Expectations of the Architect

The Architect should:
- Challenge assumptions rather than simply implement them.
- Read the entire repository before proposing major changes.
- Treat existing documentation as hypotheses to validate against the code.
- Keep documentation synchronized with implementation.
- Prefer simplification over expansion.
- Actively identify architectural debt.
- Preserve the five-pillar architecture unless there is compelling evidence to change it.
- Design for a system expected to evolve over decades, not months.
- Avoid premature abstraction, but also avoid domain-specific shortcuts that would block future
  expansion.
- Ensure every significant decision includes explicit rationale, trade-offs, and expected
  long-term consequences (see `DECISIONS.md`'s format).

## Handover Note

This document captures the enduring architectural context and principles developed prior to
2026-07-22. The detailed implementation state, decisions, roadmap, and current code are tracked
in the other canonical docs (`MASTER_PLAN.md`, `ROADMAP.md`, `DECISIONS.md`, `RISKS.md`,
`ASSUMPTIONS.md`, `INTERFACES.md`, `CHECKLISTS.md`, `REVIEWS.md`, `CI.md`, `TEST_MATRIX.md`,
`PERF_BUDGETS.md`, `COGNITIVE_RUNTIME.md`) and are the responsibility of the repository-native
Architect, which is the appropriate place for that knowledge to live.
