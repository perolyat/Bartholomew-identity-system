# REVIEWS

> Weekly check-ins and stage gate review rituals.
>
> **Last updated:** 2026-07-22 (governance addendum: Lead Architect transition recorded; see
> "Last Review Snapshot" below — the underlying content snapshot itself is from 2026-07-21,
> refreshed after going stale since 2026-01-19)

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
>
> This snapshot had gone stale since 2026-01-19 despite six months of substantial,
> well-documented progress in the interim (all six of the P0 failures it lists below were
> fixed 2026-07-20 — see MASTER_PLAN.md's "Full test suite investigation" and "P0 status:
> complete" sections). Refreshed 2026-07-21 against what this session directly verified plus
> the historical record in `MASTER_PLAN.md`/`ROADMAP.md`.
>
> **Governance addendum, 2026-07-22:** the Lead Architect role transitioned from Bartholomew to
> Claude this date (see `DECISIONS.md`'s "Lead Architect role transitions from Bartholomew to
> Claude"). `CONSTITUTION.md` was added as a 13th canonical doc (see `DECISIONS.md`'s "Adopt
> `CONSTITUTION.md` as a canonical SSOT doc"). This addendum does not re-verify the rest of this
> snapshot's content — see the 2026-07-21 refresh below for what was directly checked then.

**Date:** 2026-07-21 (governance addendum 2026-07-22)
**Type:** Ad-hoc snapshot refresh (found stale while auditing canonical docs for currency)
**Reviewer:** Claude (autonomous session) — also current Architect of record as of 2026-07-22

### Current Project State

**Stage Progress:**
- **Stage 0:** ✅ Complete (kernel alive, stable, dreaming)
- **Stage 0.5:** ✅ Complete 2026-07-20 (packaging/architecture fixes)
- **Stage 1:** 📋 Still not started (console/UI integration — no evidence of work on this
  found this session)
- **Stage 2:** ✅ P0 complete 2026-07-20 (all 6 failures below fixed; full suite green on Linux)
- **Stage 3:** ✅ Largely done (Experience Kernel/Narrator/Persona wired since before this
  session; this session closed the "still open" items: a scenario-replay test harness and
  reconciling the two reflection-narrative pipelines — see items 11.8–11.9 below)
- **Stage 4:** ✅ Done 2026-07-21 (skill registry wired into the live daemon)
- **Stage 4.5 (Runtime Convergence):** 🚧 In progress, substantial movement this session —
  items 11.1–11.11 done (see MASTER_PLAN.md); Exit Gate still mostly "partial," not "yes" (see
  `COGNITIVE_RUNTIME.md`'s Exit Gate status table for the honest per-question breakdown)
- **Stages 5–7:** 📋 Still planned/future
- **Echo Integration:** 💡 Still future (conceptual roadmap, unchanged)

**CI Health:**
- ✅ `pre-commit.yml` — auto-run on push/PR; now also runs `pytest -q` (the **full** suite, not
  just smoke tests) as of 2026-07-20 — CI.md itself hadn't mentioned this until today
- ✅ `smoke.yml` — auto-run on push/PR (server health checks)
- ⚠️ `tests.yml` — still manual-only (coverage-gated full suite); still not auto-enabled

**Documentation:**
- ✅ 13/13 canonical docs present (`COGNITIVE_RUNTIME.md` added 2026-07-21, per MASTER_PLAN.md
  item 11.5; `CONSTITUTION.md` added 2026-07-22, see DECISIONS.md)
- ✅ `docs/STATUS_2025-12-29.md` now carries an explicit stale-doc banner (2026-07-21) pointing
  back to `MASTER_PLAN.md`, rather than silently contradicting current docs
- This session also re-verified and corrected several other canonical docs against current
  code/tests: `RISKS.md`, `ASSUMPTIONS.md`, `INTERFACES.md`, `TEST_MATRIX.md`, `CI.md`

**Test Health:**
- ✅ The 6 P0 non-environmental failures this snapshot previously listed (summarization
  fallback, encryption round-trip, embeddings lifecycle, embed_store defaulting, retrieval
  factory mismatch, metrics idempotency) were **all fixed 2026-07-20** — verified again this
  session: `pytest -q` across the encryption/metrics/retrieval-factory test files passes
  locally in a clean venv.
- This session found and fixed several new, real bugs (not previously known/tracked):
  retrieval silently over-excluding consented memories; a restart-persistence bug where
  Experience Kernel state was never actually restored despite a log line claiming it was; five
  live 500s in the `self_state` API router (zero HTTP-level tests existed for that whole
  router before this session).
- **Not independently re-verified this session:** a single `pytest -q` run across the
  *entire* suite in one command (targeted, relevant subsets were run per change instead,
  30+ separate invocations covering ~400 distinct tests with no failures) — see this doc's
  own "Verification-first" best practice below; no reason to doubt full-suite health given
  every touched area passed, but this is named explicitly rather than assumed.

**Tech Stack:** unchanged from the 2026-01-19 snapshot (not re-verified this session).

### Active Blockers

**None.**

### Next Moves (per MASTER_PLAN.md, current as of this refresh)

The Runtime Convergence Exit Gate (`COGNITIVE_RUNTIME.md`) names the concrete remaining gaps
under Stage 4.5, in rough priority order:
1. Scheduler drives still don't construct an Observation/CandidateAction or consult the
   Policy Decision (a known, deliberately-deferred gap — an earlier attempt caused a real
   production regression, see DECISIONS.md).
2. Voice/sight adapters remain parking-brake-only stubs (Stage 6 future work).
3. Two Reflection *shapes* remain unreconciled (chat's Working Memory item vs. skills'
   `skill_action_audit` row) — distinct from the two reflection *narrative pipelines*, which
   this session did reconcile (item 11.8).
4. One persona-duplication caller remains (`identity_interpreter/policies/persona.py`'s two
   legacy callers — CLI `explain`, standalone `chat.py` script — not yet migrated).
5. Stage 1 (console/UI) hasn't been started at all.

### Links to Key Context

- [MASTER_PLAN.md](MASTER_PLAN.md) — overall project plan + Next 3 Moves (P0 section) + P2.5
  Runtime Convergence backlog (items 11.1–11.11)
- [COGNITIVE_RUNTIME.md](COGNITIVE_RUNTIME.md) — the cognitive-loop/ownership/governance
  reference, including the honest Exit Gate status table
- [ROADMAP.md](ROADMAP.md) — stage gates with exit criteria
- [DECISIONS.md](DECISIONS.md) — decision log
- [RISKS.md](RISKS.md) / [ASSUMPTIONS.md](ASSUMPTIONS.md) — both re-verified 2026-07-21
- [CI.md](CI.md) — CI infrastructure + local commands (corrected 2026-07-21)
- `docs/STATUS_2025-12-29.md` — historical snapshot, now explicitly marked stale

---

## Review History (Recent)

### 2026-07-21 - Snapshot Refresh + Canonical Doc Audit
- **Type:** Ad-hoc review (found this snapshot stale while auditing docs for currency)
- **Outcome:** Snapshot refreshed against current reality; several other canonical docs
  corrected in the same pass (RISKS.md, ASSUMPTIONS.md, INTERFACES.md, TEST_MATRIX.md, CI.md);
  `docs/STATUS_2025-12-29.md` given a stale-doc banner
- **Next:** Close remaining Runtime Convergence Exit Gate items (see "Next Moves" above)

### 2026-01-19 - Coordinator Handoff Preparation
- **Type:** Planning review
- **Outcome:** Canonical docs scaffolding complete
- **Next:** Refresh STATUS, begin Phase 2 — *(this next step didn't happen until the
  2026-07-21 entry above; the snapshot sat stale for six months of substantial progress in
  the interim)*

### 2025-12-29 - Test Health Snapshot
- **Type:** Ad-hoc assessment
- **Outcome:** STATUS snapshot created, 6 P0 failures documented
- **Next:** All 6 fixed 2026-07-20 (see MASTER_PLAN.md)

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
