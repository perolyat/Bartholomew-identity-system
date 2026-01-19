# REVIEWS

> Weekly check-ins and stage gate review rituals.
>
> **Last updated:** 2026-01-19

## Purpose

This document tracks:
- Weekly progress check-ins (solo or team)
- Stage gate reviews (formal milestone assessments)
- Current project health snapshot

**Rule:** Keep the "Last Review Snapshot" section current. It's the first thing a new coordinator reads.

---

## Weekly Check-in Template

**Date:** YYYY-MM-DD
**Attendees:** [names or "solo review"]

### Progress This Week

- [x] Completed item with brief outcome
- [ ] In-progress item with % done or blockers

### Blockers

- None / [describe blocker with owner + target resolution date]

### Risks Update

- **New risks identified:** [link to RISKS.md entries, or "none"]
- **Status changes:** [which risks escalated, mitigated, or closed]

### Next Week Focus

1. Priority 1 task
2. Priority 2 task
3. Priority 3 task

### Decisions Made This Week

- [Link to DECISIONS.md entries added, or "none"]

### Notes

[Any other relevant observations, learnings, or context]

---

## Stage Gate Review Template

**Gate:** [Stage N - Name from ROADMAP.md]
**Date:** YYYY-MM-DD
**Reviewers:** [names or "solo review"]

### Exit Criteria Assessment

Per [ROADMAP.md](ROADMAP.md), evaluate each exit criterion:

- [ ] **Criterion 1:** PASS / BLOCKED / NA
  - Evidence: [test results, verification command output, links]
- [ ] **Criterion 2:** PASS / BLOCKED / NA
  - Evidence: [test results, verification command output, links]
- [ ] **Criterion 3:** PASS / BLOCKED / NA
  - Evidence: [test results, verification command output, links]

### Evidence Presented

**Test Results:**
```bash
# Paste verification command outputs
pytest -q
pre-commit run --all-files
# etc.
```

**Documentation Updated:**
- [List canonical docs changed]
- [List implementation docs added/changed]

**Acceptance Criteria Verified:**
- [List criteria from ROADMAP backlog item]
- [Show verification command results]

### Governance Check

- [ ] Non-negotiables preserved (fail-closed, privacy-first, verification-first)
- [ ] Parking brake coverage verified for new subsystems
- [ ] Consent gates applied at lowest layer
- [ ] No doc sprawl introduced
- [ ] Interfaces updated if contracts changed

### Decision

**Select one:**

- [ ] **✅ APPROVED** - All criteria met; proceed to next stage gate
- [ ] **⚠️ BLOCKED** - Critical issues must be resolved (see action items)
- [ ] **🔄 CONDITIONAL** - Minor issues acceptable; address in next iteration

### Action Items (if blocked or conditional)

1. [Required action with owner + deadline]
2. [Required action with owner + deadline]

### Rollback Plan

If this stage introduces risk:
- [Describe how to roll back changes]
- [List affected systems]
- [Estimated rollback time]

---

## Last Review Snapshot

> **This section must stay current.** Update after every weekly check-in or gate review.

**Date:** 2026-01-19
**Type:** Coordinator Handoff Preparation
**Reviewer:** Cline (Kimi coordinator)

### Current Project State

**Stage Progress:**
- **Stage 0:** ✅ Complete (kernel alive, stable, dreaming)
- **Stage 1:** 📋 Planned (console/UI integration - not started)
- **Stage 2:** 🚧 In Progress (governance hardening - 6 P0 failures)
- **Stages 3-7:** 📋 Planned (Experience Kernel → embodiments)
- **Echo Integration:** 💡 Future (45 features across 4 gates)

**CI Health:**
- ✅ `pre-commit.yml` - Auto-run on push/PR (format + lint + smoke tests)
- ✅ `smoke.yml` - Auto-run on push/PR (server health checks)
- ⚠️ `tests.yml` - Manual-only (full test suite + coverage)
- **Target:** Enable auto-run tests.yml after P0 fixes

**Documentation:**
- ✅ 11/11 canonical docs present (CI.md + REVIEWS.md just created)
- ⚠️ STATUS snapshot dated 2025-12-29 (needs refresh to 2026-01-19)

**Test Health:**
- ✅ Stage 0 tests green (kernel lifecycle, WAL, dreaming loops)
- ⚠️ 6 P0 non-environmental failures (per Dec 29 snapshot):
  1. Summarization truncation fallback
  2. Encryption envelope round-trip
  3. Embeddings persist lifecycle
  4. embed_store defaulting logic
  5. Retrieval factory mode mismatch
  6. Metrics registry idempotency
- ℹ️ Many environmental failures (Windows file locking, SQLite FTS5 variance)

**Tech Stack:**
- Python 3.10+ (targeting 3.10/3.11/3.12)
- FastAPI + Uvicorn + Pydantic v2 + SQLAlchemy 2.0
- SQLite (single DB, WAL mode)
- Ubuntu CI baseline, Windows dev environment

### Active Blockers

**None currently blocking forward progress.**

P0 test failures are documented and prioritized but don't block documentation work or planning.

### Next 3 Moves (per MASTER_PLAN)

1. ✅ **Land SSOT docs** - Complete canonical docs (CI.md + REVIEWS.md just created)
2. ⏭️ **Reinstate minimal CI** - Enable auto-run tests.yml after fixing P0s
3. ⏭️ **Fix P0 non-environmental failures** - One fix per PR, smallest surface first

### Priority Stack (per ChatGPT handoff guidance)

1. ✅ **Canonical docs completeness** - Just completed (Phase 1)
2. ⏭️ **Refresh STATUS snapshot** - Next action (Phase 2)
3. ⏭️ **Fix P0 failures** - Restore test signal (Phase 3)
4. ⏭️ **Enable auto-run CI** - Make tests.yml continuous (Phase 4)
5. 📋 **New features** - Only after baseline stability restored

### Coordinator Handoff Status

**Handoff from:** ChatGPT (planning coordinator)
**Handoff to:** Cline/Kimi (execution coordinator)
**Status:** ✅ Complete as of 2026-01-19

**What was handed off:**
- Complete project context (9 canonical docs read + analyzed)
- CI infrastructure assessment (3 workflows analyzed)
- P0 failure documentation (STATUS_2025-12-29.md)
- Priority stack and 4-phase action plan
- Tribal knowledge captured from ChatGPT

**Handoff complete when:**
- [x] All canonical docs exist (just completed)
- [ ] STATUS snapshot refreshed (Phase 2 - next action)
- [x] Next coordinator briefed on project state
- [x] Priority stack agreed (Docs → CI → P0 fixes → Features)

### Recommended Next Actions

**Immediate (this week):**
1. Refresh STATUS snapshot to Jan 19, 2026 (Phase 2)
2. Run full test suite: `pytest -q --tb=short`
3. Document current state vs Dec 29 baseline
4. Re-prioritize P0 fixes based on current failures

**Short-term (next 2-4 weeks):**
1. Fix P0 failures one-by-one (Phase 3)
   - Start with metrics registry idempotency (likely smallest surface)
2. Quarantine environmental failures with markers
3. Enable auto-run tests.yml (Phase 4)

**Medium-term (Stage 3):**
1. Experience Kernel MVP (self-model + narrator)
2. Persona packs (config-driven personality switching)

### Links to Key Context

- [MASTER_PLAN.md](MASTER_PLAN.md) - Overall project plan + Next 3 Moves
- [ROADMAP.md](ROADMAP.md) - Stage gates with exit criteria
- [DECISIONS.md](DECISIONS.md) - Decision log
- [RISKS.md](RISKS.md) - Risk register
- [CI.md](CI.md) - CI infrastructure + local commands
- `docs/STATUS_2025-12-29.md` - Test health snapshot (needs refresh)

---

## Review History (Recent)

### 2026-01-19 - Coordinator Handoff Preparation
- **Type:** Planning review
- **Outcome:** Canonical docs scaffolding complete
- **Next:** Refresh STATUS, begin Phase 2

### 2025-12-29 - Test Health Snapshot
- **Type:** Ad-hoc assessment
- **Outcome:** STATUS snapshot created, 6 P0 failures documented
- **Next:** [This became stale; refresh planned for Jan 19]

### 2025-10-30 - Stage 0 Completion
- **Type:** Stage gate review
- **Outcome:** Stage 0 approved, kernel alive and stable
- **Next:** Begin Stage 2 (governance hardening)

---

## Review Cadence

**Weekly check-ins:**
- Frequency: Every Monday (or adjust to team cadence)
- Duration: 15-30 minutes
- Use weekly template above

**Stage gate reviews:**
- Frequency: At each ROADMAP milestone
- Duration: 1-2 hours
- Use stage gate template above
- Record decision in DECISIONS.md

**STATUS snapshot refresh:**
- Frequency: Before each stage gate + when test health significantly changes
- Action: Create `docs/STATUS_YYYY-MM-DD.md`
- Link in MASTER_PLAN and this doc

---

## Review Best Practices

1. **Update this snapshot section** after every review (weekly or gate)
2. **Keep "Next 3 Moves" current** - update MASTER_PLAN.md in parallel
3. **Link decisions** - every significant decision goes in DECISIONS.md
4. **Track risks** - update RISKS.md if new risks identified
5. **Be honest** - "BLOCKED" is better than fake progress
6. **Verification-first** - always include verification commands/evidence
7. **Single source of truth** - canonical docs only, no doc sprawl
