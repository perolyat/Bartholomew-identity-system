---
name: interfaces
description: Navigate INTERFACES.md, the canonical contracts between Bartholomew's subsystems. Use before changing any function signature, DB schema, API route, memory ingestion or retrieval path, parking-brake call, skill manifest, or audit/log record — and whenever a change might alter what another subsystem can rely on.
---

# Interface contracts

`INTERFACES.md` is the canonical record of what each subsystem promises. A change that alters a
contract without updating it is a Definition-of-Done failure (`MASTER_PLAN.md`: "Interfaces updated
if contracts change").

## Sections

| Section heading | Covers |
|---|---|
| `## 1) Identity configuration` | `Identity.yaml` shape and what reads it |
| `## 2) Kernel DB interface (SQLite)` | Schema, connection ownership, WAL behaviour |
| `## 3) Memory ingestion` | The consent-gated write path |
| `## 4) Retrieval` | FTS/vector retrieval, `context_only` flags, filtered result sets |
| `## 5) Parking brake` | Gating calls every subsystem must honour |
| `## 6) API bridge (FastAPI)` | Routes and their admission rules |
| `## 7) Logging / audit` | Audit record shape — the provenance trail |
| `## 8) Performance expectations` | Cross-reference to `PERF_BUDGETS.md` |
| `## Experience Kernel` | Implemented — heading was corrected from "proposed" 2026-07-20 |
| `## Skill manifest` | Implemented — corrected 2026-07-21 |
| `## Identity Context / Policy Decision contract` | How Identity publishes context and the Executive builds decisions |

## How to use it

```bash
grep -n "^## " INTERFACES.md
grep -n "<function or table or route name>" INTERFACES.md
```

## Rules

- **Read before changing, update in the same commit.** A contract change split across commits is
  how the record drifts from the code.
- **Ingestion and retrieval are consent-gated by design.** `DECISIONS.md` records "Consent/privacy
  gates applied at the lowest retrieval layer" specifically so downstream callers cannot bypass
  them. A change that moves filtering upward is a consent bypass, not an optimisation — stop and
  raise it.
- **The parking brake is a contract, not a convention.** New subsystems must add a gating point and
  a test (`RISKS.md` R2, `CHECKLISTS.md`).
- **Several headings carry dated corrections** where they previously said "proposed". Trust the
  correction note over the surrounding prose, and check the code if it still reads ambiguously.
