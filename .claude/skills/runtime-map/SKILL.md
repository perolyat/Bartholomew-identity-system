---
name: runtime-map
description: Navigate COGNITIVE_RUNTIME.md, the canonical account of how Bartholomew actually thinks. Use when working on the runtime loop, Runtime Contract, Experience Kernel, Global Workspace, Working Memory, the narrator or episodic layer, reflection/consolidation, competency retrieval, or governance checkpoints inside the loop — and whenever a question is "how does this actually flow at runtime" or "which subsystem owns this".
---

# Runtime map

`COGNITIVE_RUNTIME.md` is ~62KB and canonical. **Do not read it whole** — grep for the section you
need and read that region only.

## Sections and what each answers

| Section heading | Answers |
|---|---|
| `## The governing principles` | The constraints the loop must always satisfy |
| `## The Runtime Contract (the loop itself)` | **The main event.** How a turn actually flows end to end |
| `## Competency, Training, and Learning` | S5.1–S5.3 competency model. Note: data model implemented, the rest is *not* |
| `## Ownership table` | **Which subsystem owns which concept.** Check here before adding behaviour anywhere |
| `## Governance checkpoints` | Where consent gates and the parking brake sit inside the loop |
| `## The memory / reflection lifecycle` | Capture → store → retrieve → reflect/consolidate |
| `## Exit Gate status` | What is actually done vs proposed |
| `## Verify` | How to confirm runtime behaviour empirically |

## How to use it

```bash
grep -n "^## " COGNITIVE_RUNTIME.md          # current section boundaries
grep -n "<subsystem name>" COGNITIVE_RUNTIME.md
sed -n 'START,ENDp' COGNITIVE_RUNTIME.md     # read only that region
```

## Rules

- **Check `## Ownership table` before adding behaviour.** `DECISIONS.md` records "One authority per
  architectural concept" — two subsystems owning the same concept is a defect this repo has
  deliberately fixed before (items 11.12/11.14 removed duplicated tool-policy and persona modules).
- **Distinguish implemented from proposed.** This document describes target architecture in places.
  `## Exit Gate status` and the parenthetical notes on headings say which is which. Do not read a
  described mechanism as a built one — confirm against the code before relying on it.
- **Governance checkpoints are load-bearing.** If a change moves work across a checkpoint, it is a
  governance change, not a refactor, and needs the treatment in `CHECKLISTS.md`.
- This document is the authority for runtime semantics. Where an implementation note under `docs/`
  disagrees, this wins.
