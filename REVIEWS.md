# REVIEWS

> Weekly check-ins and stage gate review rituals.
>
> **Last updated:** 2026-07-27 (snapshot refreshed against the post-Phase-A repository state;
> the previous snapshot dated 2026-07-21/22 had gone stale on four counts — Stage 4.5 status,
> CI health, the consent red-team gap, and the "Next Moves" list — all corrected below)

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
**Date:** 2026-07-27
**Type:** Planning-document reconciliation against the post-Phase-A repository state
**Reviewer:** Claude (Architect of record since 2026-07-22, per `DECISIONS.md`)
**Basis:** current code, `git log`, executable test collection, and the merged PR #26 CI results.
Roadmap labels, prior AI summaries and implementation notes were treated as claims to check, not
as evidence.

### Current Project State

**Stage Progress:**
- **Stage 0:** ✅ Complete (kernel alive, stable, dreaming)
- **Stage 0.5:** ✅ Complete 2026-07-20 (packaging/architecture fixes)
- **Stage 1:** 📋 **Deferred console/UI product slice. Not started, and never a prerequisite for
  Stages 2–4.5** (no code evidence found; historical numbering retained deliberately)
- **Stage 2:** ✅ P0 complete 2026-07-20 (all 6 previously-listed failures fixed)
- **Stage 3:** ✅ Largely done; the "still open" items closed 2026-07-21 (items 11.8–11.9)
- **Stage 4:** ✅ Done 2026-07-21 (skill registry wired into the live daemon)
- **Stage 4.5 (Runtime Convergence):** ✅ **Complete 2026-07-24** — *corrected from the previous
  snapshot, which still read "🚧 In progress ... Exit Gate still mostly partial."* All seven Exit
  Gate questions are satisfied within Stage 4.5's scope; Q7's voice/sight-persona residual was
  formally reclassified to Stage 6 (item 11.22), so no official exit criterion is left partial.
  See `COGNITIVE_RUNTIME.md`'s Exit Gate table for the per-question evidence.
- **Stage 5:** 📋 **Not started.** Only prerequisite S5.0 has landed (PR #25, `3496cfb`, closes
  issue #24). **S5.1 has not begun** and is paused pending explicit approval.
- **Stages 6–7:** 📋 Future. Issue #22 (forward `IdentityContext` through the voice/sight compat
  wrappers) is open and deferred to Stage 6.
- **Echo Integration:** 💡 Still future (conceptual roadmap, unchanged)

**Engineering workstreams:**
- **Phase A (truthful cross-platform verification):** ✅ **Complete and merged 2026-07-27**,
  merge commit `8b96319c4059d9dfada2579ca5f6da22b34e1f31` (PR #26).
- **Phase B (persistence ownership stabilisation):** 📋 **Proposed, NOT approved for
  implementation.** No design, branch or code exists.

**CI Health** *(corrected — the previous snapshot's entry was wrong in both directions)*:
- ✅ `ci.yml` — **new in Phase A**, auto-run on every pull request, every push to `main`, and
  manual dispatch. Four jobs: `quality`, `tests` (Ubuntu 3.10/3.11 + coverage gate),
  `critical` (Ubuntu 3.10/3.11), `windows` (3.11).
- ✅ `pre-commit.yml` — auto-run on push/PR; hooks + `pytest -q -m smoke` + `pytest -q`.
- ✅ `smoke.yml` — auto-run on push/PR; the only workflow that boots a real HTTP server.
- ❌ `tests.yml` — **removed in Phase A.** The previous snapshot called it "manual-only ...
  still not auto-enabled"; the sharper truth is that it *could never have passed* — it ran
  `pytest --cov=...` while `pytest-cov` was declared in no manifest.
- **Evidence:** 9/9 checks green on PR #26's merged head `e923fb9`, including the first Windows
  job in this repository's history.

**Documentation:**
- ✅ 13/13 canonical docs present. *(Reconciled 2026-07-27: `MASTER_PLAN.md`'s own canonical-docs
  list had omitted `CONSTITUTION.md`, listing 12 while `DECISIONS.md` and `CONSTITUTION.md` both
  said 13. `MASTER_PLAN.md` was the incorrect one and is now fixed.)*
- ✅ `docs/STATUS_2025-12-29.md` carries an explicit stale-doc banner.
- ⚠️ `docs/README.md` (identity_interpreter contributor guide) documented three modules deleted
  in items 11.12–11.14 as current API; corrected 2026-07-27.
- ⚠️ Root `README.md` documented the `barth` console script, which is not installed (finding F9);
  corrected 2026-07-27.

**Test Health (verified 2026-07-27 by collection + the merged CI run, not assumed):**
- 915 tests collected in total; the default `pytest -q` runs 912 of them, because
  `addopts = -m 'not integration and not slow'` deselects exactly 3. `ci.yml`'s `critical` job
  runs those 3 explicitly, on both interpreter legs.
- Coverage baseline **73.5%** across all three first-party packages against the pre-existing
  declared 70% gate. The gate was measured before being enforced and was **not** lowered.
- ⚠️ **One known intermittent failure, deliberately left visible:**
  `tests/test_sqlite_wal_concurrent_processes.py::test_wal_cleanup_concurrent_processes` failed
  once under full-suite load and passed 3/3 in isolation immediately afterwards. It was **not**
  retried, quarantined, re-marked, or given a longer timeout — it is preserved as Phase B
  evidence. Treat "the suite is green" as conditional on this.

**Tech Stack:** unchanged from the 2026-01-19 snapshot (not re-verified this pass).

### Active Blockers

**None blocking.** Two things are *paused by decision*, not blocked: S5.1 and Phase B, both
awaiting explicit approval.

### Next Moves

Nothing is in flight. In priority order, each requiring separate explicit approval to start:

1. **Phase B — persistence ownership stabilisation** (proposed next engineering work).
2. **Stage 5 / S5.1** — initiative engine, under the locked safety-scaffolding-first sequence.
3. **Stage 1** — console/UI slice; the oldest unstarted product gate.

Unscheduled and non-blocking: issue #22 (Stage 6); Phase A's deferred findings F9–F11
(`RISKS.md`); the two unreconciled reflection *narrative* pipelines (never a Stage 4.5 exit
criterion — see `ROADMAP.md` Stage 3).

*(The previous snapshot's "Next Moves" list is fully superseded: its items 1 and 2 — scheduler
drives not consulting the Policy Decision, and voice/sight being parking-brake-only stubs — were
closed by items 11.17 and 11.21; its item 3, the two Reflection *shapes*, was closed by item
11.16.)*

### Links to Key Context

- [MASTER_PLAN.md](MASTER_PLAN.md) — overall project plan, stage-gate status table, Next 3 Moves,
  the P2.5 Runtime Convergence backlog (items 11.1–11.22, complete), and the Approval Ledger
- [COGNITIVE_RUNTIME.md](COGNITIVE_RUNTIME.md) — the cognitive-loop/ownership/governance
  reference, including the honest Exit Gate status table
- [ROADMAP.md](ROADMAP.md) — stage gates with exit criteria
- [DECISIONS.md](DECISIONS.md) — decision log
- [RISKS.md](RISKS.md) / [ASSUMPTIONS.md](ASSUMPTIONS.md) — both re-verified 2026-07-21
- [CI.md](CI.md) — CI infrastructure + local commands (corrected 2026-07-21)
- `docs/STATUS_2025-12-29.md` — historical snapshot, now explicitly marked stale

---

## Review History (Recent)

### 2026-07-27 - Planning-Document Reconciliation (post-Phase A)
- **Type:** Documentation-only reconciliation against the merged repository state (`8b96319`)
- **Outcome:** Phase A recorded as complete and merged with its real merge commit; Stage 4.5
  corrected from "in progress" to complete; S5.1 and Phase B stated plainly as not started /
  not approved; CI matrix reconciled with the workflows that actually run; deleted-module and
  broken-entry-point claims corrected in the contributor guides; Phase A's deferred findings
  F9–F11 preserved in `RISKS.md`; the Approval Ledger populated with verified commit hashes
- **Next:** Await approval for Phase B or Stage 5 / S5.1; neither has begun

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
