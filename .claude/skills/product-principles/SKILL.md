---
name: product-principles
description: Navigate CONSTITUTION.md, Bartholomew's canonical product and identity doctrine. Use when a question is about what Bartholomew should be rather than how it is built — product direction, personality, UX, privacy/sovereignty posture, platform vs personal identity, what belongs in the product at all, or whether a proposed feature fits the project's purpose.
---

# Product principles

`CONSTITUTION.md` is ~51KB and canonical. It answers *should we*, not *how do we*. Grep for the
section; do not read it whole.

## Sections

| Section heading | Answers |
|---|---|
| `## Project Vision` / `## Primary Goal` | What this is for |
| `## Core Principle: Responsibilities, Not Technologies` | Why subsystems are framed by responsibility |
| `## The Five Pillars` | The load-bearing product commitments |
| `## Observation Philosophy` | What Bartholomew may notice, and how |
| `## Domain Independence` | Why it is not a vertical assistant |
| `## One Developing Digital Individual` | Competency/training doctrine |
| `## One Platform, Many Personal Bartholomews` | Platform vs personal identity; isolation is non-negotiable (added 2026-08-15) |
| `## Sovereign Principle` | User ownership and control |
| `## Safety, Accessibility, and Product Invariants` | Hard product constraints (added 2026-07-28) |
| `## Architectural Principles` / `## UX Principles` / `## Personality` | Design posture |
| `## Development Philosophy` | **Amended 2026-08-12** to state the time-to-real-use principle directly |
| `## Documentation Philosophy` / `## Expectations of the Architect` | How work is recorded and reviewed |

## Rules

- **`## Development Philosophy` was amended 2026-08-12.** It previously said the project
  deliberately spends more time designing than coding and that correct architecture outweighs
  rapid delivery — a philosophy that, followed faithfully, produced a system with almost nothing in
  front of real use. It now states the time-to-real-use principle directly. `docs/TILT.md` is the
  tactical detail underneath it. **Do not quote the pre-amendment framing.**
- **Isolation between personal identities is non-negotiable.** Any proposal touching multi-user,
  sharing, or cross-identity features must be checked against `## One Platform, Many Personal
  Bartholomews` first.
- **Bartholomew is not the LLM.** Identity is portable across infrastructure and models. Proposals
  that couple identity to a specific provider contradict this section.
- This document answers product questions. For *how it runs*, use `runtime-map`; for *what was
  decided and why*, grep `DECISIONS.md`.
