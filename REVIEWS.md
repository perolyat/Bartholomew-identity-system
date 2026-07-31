# REVIEWS

> Weekly check-ins and stage gate review rituals.
>
> **Last updated:** 2026-07-31 (Phase B governance restructuring: the engineering-workstream Phase B
> line and "Next Moves" below now reflect the B0–B9 staged structure rather than a single monolithic
> design-then-implement step. See "Review History" for the full entry.)
>
> **Previously (2026-07-28):** snapshot refreshed against the documentation reconciliation pass 2
> repository state — see the "Documentation" bullet list below and "Review History" for the full
> set of changes.
>
> **Previously (2026-07-27):** snapshot refreshed against the post-Phase-A repository state; the
> previous snapshot dated 2026-07-21/22 had gone stale on four counts — Stage 4.5 status, CI
> health, the consent red-team gap, and the "Next Moves" list — all corrected then.

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
- **Phase B (persistence ownership stabilisation):** 📋 **Staged; NOT approved for
  implementation. Updated 2026-07-31.** A large Phase B design specification exists and was
  independently reviewed, but is preserved as non-authoritative research/risk material
  (`docs/archive/phase-b-persistence-ownership-final.md`, indexed by stage in
  `docs/PHASE_B_RISK_MAP.md`) rather than as an implementation authority. Phase B is now governed
  by `docs/PHASE_B_OVERVIEW.md` plus ten separately gated stages (B0–B9) defined in `ROADMAP.md`.
  The project owner approved this change in direction and authorised preparation of the
  documentation changes; final diff and commit approval for those changes remained separately
  gated. B0 is the next stage, but planning it requires separate explicit approval and has not
  begun. No branch or implementation code exists.

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
- ✅ **(2026-07-28) `MASTER_PLAN.md` trimmed from ~2,200 to ~650 lines** — the engineering
  chronicle (items 11.1–11.22, four bug-fix "rounds," the Experience Kernel MVP write-up) moved
  verbatim to `docs/archive/ENGINEERING_LOG_2026.md`, with a compact index and preserved item
  numbers left in place.
- ✅ **(2026-07-28) Echo Integration Roadmap moved off both `ROADMAP.md` and `MASTER_PLAN.md`**
  to the new non-canonical `docs/incubator/ECHO_IDEAS.md`.
- ✅ **(2026-07-28) Reflection-ownership contradiction resolved** — `ROADMAP.md` had said
  "reconciled... additively," `COGNITIVE_RUNTIME.md` had said "remain unreconciled," and each
  pointed at the other. Both now consistently state: current implementation is concatenation, not
  unification; approved target is `ReflectionGenerator`-authoritative (see `DECISIONS.md`).
- ✅ **(2026-07-28) Legacy-implementation-notes cleanup pass completed** — the ~20-file review
  deferred on 2026-07-27 (see `RISKS.md`) is done; `UI_INTEGRATION_GUIDE.md` and both copies of
  `DEV_SETUP_NOTES.md` deleted; four files moved to `docs/archive/`; remainder updated/banner-ed.
- ✅ **(2026-07-28) Deployment architecture decided** — hybrid local-first, recorded in
  `DECISIONS.md`; `ROADMAP.md` Stage 6's auth criterion and `ASSUMPTIONS.md`'s token-auth entry
  corrected to match (no longer assumes simple token auth is sufficient).
- ✅ **(2026-07-28) Six new safety/product invariants added to `CONSTITUTION.md`**: independent
  emergency shutdown, capture/recording safety, data portability, cognitive accessibility,
  adaptive notifications, the consumer-value gate. Personality section split into enduring
  character values (kept constitutional) vs. default persona inspiration (moved to Experience
  layer).
- ✅ `docs/STATUS_2025-12-29.md` carries an explicit stale-doc banner; moved to `docs/archive/`
  2026-07-28.
- ⚠️ `docs/README.md` (identity_interpreter contributor guide) documented three modules deleted
  in items 11.12–11.14 as current API; corrected 2026-07-27.
- ⚠️ Root `README.md` documented the `barth` console script, which is not installed (finding F9);
  corrected 2026-07-27. `QUICKSTART.md` and `.github/copilot-instructions.md` had the same stale
  `barth` references, uncorrected by the 2026-07-27 pass; corrected 2026-07-28.

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

**Sequencing corrected 2026-07-28** (documentation reconciliation pass 2): Stage 1 now precedes
Stage 5/S5.1 — see `ROADMAP.md`'s "Near-term milestone plan" for the full rationale (Stage 5's
live proactivity needs the user-facing governance surface only Stage 1 provides). Nothing is in
flight. In sequence, each step requiring separate explicit approval to start:

1. **Documentation reconciliation and the deployment-architecture decision** — done 2026-07-28; the
   deployment decision (hybrid local-first) is recorded in `DECISIONS.md`.
2. **Plan Phase B one stage at a time**, beginning with B0 only after separate, explicit approval
   to plan it — persistence ownership stabilisation, against the approved architecture. See
   `docs/PHASE_B_OVERVIEW.md`, `ROADMAP.md`'s Phase B section, and `docs/PHASE_B_RISK_MAP.md`.
3. **Stage 1 — minimal consumer web governance shell** (parking-brake access, consent/approval
   inbox, notification/mute controls, awaiting-response queue, host-device onboarding).
4. **Stage 5 / S5.1** — initiative engine, under the locked safety-scaffolding-first sequence;
   also blocked on the reflection-ownership implementation gap (`COGNITIVE_RUNTIME.md`) for live
   proactive *reflection* behaviour specifically.

Unscheduled and non-blocking: issue #22 (Stage 6); Phase A's deferred findings F9–F11
(`RISKS.md`); jurisdiction-aware capture/recording compliance, adaptive-notification delivery
beyond the Stage 1 baseline, and data-export/portability delivery (all Stage 6 scope, added
2026-07-28); the future, unapproved hydration/water-logging code-cleanup decision (`RISKS.md`).

*(The 2026-07-27 snapshot's "Next Moves" list — Phase B, then Stage 5/S5.1, then Stage 1 — is
superseded by the corrected sequencing above. Its own predecessor list was fully superseded then:
items 1 and 2 — scheduler drives not consulting the Policy Decision, and voice/sight being
parking-brake-only stubs — were closed by items 11.17 and 11.21; its item 3, the two Reflection
*shapes*, was closed by item 11.16. Note also: the reflection-*narrative*-pipeline item this
snapshot previously called "never a Stage 4.5 exit criterion" now has an approved target
architecture recorded in `COGNITIVE_RUNTIME.md`/`DECISIONS.md` — it remains unimplemented, but is
no longer merely "unreconciled" with no resolution in view.)*

### Links to Key Context

- [MASTER_PLAN.md](MASTER_PLAN.md) — overall project plan, stage-gate status table, Next 3 Moves,
  the P2.5 Runtime Convergence backlog (items 11.1–11.22, complete), and the Approval Ledger
- [COGNITIVE_RUNTIME.md](COGNITIVE_RUNTIME.md) — the cognitive-loop/ownership/governance
  reference, including the honest Exit Gate status table
- [ROADMAP.md](ROADMAP.md) — stage gates with exit criteria
- [DECISIONS.md](DECISIONS.md) — decision log
- [RISKS.md](RISKS.md) / [ASSUMPTIONS.md](ASSUMPTIONS.md) — both re-verified 2026-07-21
- [CI.md](CI.md) — CI infrastructure + local commands (corrected 2026-07-21)
- `docs/archive/STATUS_2025-12-29.md` — historical snapshot, now explicitly marked stale

---

## Review History (Recent)

### 2026-07-31 - Phase B Governance Restructuring (documentation-only)
- **Type:** Documentation-only restructuring. The project owner approved the change in direction
  (a concise overview plus separately gated B0–B9 stages, replacing one monolithic approval unit)
  and authorised preparation of these documentation changes; final diff and commit approval for
  the changes themselves remained separately gated, per this project's existing commit-
  authorization checklist.
- **Outcome:** The detailed Phase B persistence-ownership design specification's closure review
  identified substantial, valid concurrency, persistence, Governance, and lifecycle risks across
  nine independently-verified blocking findings and a complete replacement-semantics inventory.
  Continuing to pursue closure of the entire specification as one indivisible, implementation-level
  approval unit reached diminishing returns — each review round found genuine issues without
  shrinking the unit of approval or the distance to an actually-implementable first stage. The
  project therefore adopted staged planning: a concise `docs/PHASE_B_OVERVIEW.md` plus ten
  separately gated stages (B0–B9, defined in `ROADMAP.md`), with the prior findings preserved,
  non-authoritatively, at `docs/archive/phase-b-persistence-ownership-final.md` and indexed by
  stage in `docs/PHASE_B_RISK_MAP.md` for use when their owning stage is planned. No production
  implementation occurred during the design-review process or during this restructuring — this
  pass changed documentation only.
- **Next:** Once this documentation is committed, the next action is to seek separate, explicit
  approval to plan B0 (the first Phase B stage). B0 itself remains unapproved and has not begun;
  its own plan and implementation each require separate, explicit approval in turn.

### 2026-07-28 - Documentation Reconciliation Pass 2 (audit + full noncanonical-doc cleanup)
- **Type:** Documentation-only reconciliation, approved by the project owner following a full
  written audit (contradiction table, noncanonical-document inventory, unresolved-decisions list)
- **Outcome:** Echo Integration Roadmap moved off both `ROADMAP.md` and `MASTER_PLAN.md` to
  non-canonical `docs/incubator/ECHO_IDEAS.md`; `MASTER_PLAN.md` trimmed from ~2,200 to ~650 lines
  via verbatim extraction to `docs/archive/ENGINEERING_LOG_2026.md`; the reflection-ownership
  contradiction between `ROADMAP.md` and `COGNITIVE_RUNTIME.md` resolved (concatenation ≠
  unification; `ReflectionGenerator` is the approved target authority); `CI.md`'s self-
  contradicting Windows-failure posture corrected; `ASSUMPTIONS.md` A4 rescoped and the
  cross-device token-auth assumption rewritten; hybrid local-first deployment architecture
  decided and recorded in `DECISIONS.md`; `CONSTITUTION.md`'s Personality section split
  (enduring values vs. default persona inspiration) and six new safety/product invariants added
  (independent emergency shutdown, capture/recording safety, data portability, cognitive
  accessibility, adaptive notifications, consumer-value gate); `awaiting_response` state and the
  Observe/Interpret/Recommend/Govern/Act/Learn mapping added to `COGNITIVE_RUNTIME.md`;
  `CHECKLISTS.md` gained a product/safety-invariants checklist; the ~20-file legacy-
  implementation-notes cleanup deferred on 2026-07-27 completed (updates, banners, archive moves,
  merges, and deletions — see the changed-file list presented alongside this reconciliation);
  Stage 1/Stage 5 sequencing corrected so Stage 1 precedes Stage 5.
- **Next:** Await approval for Phase B design, Stage 1 implementation, or Stage 5 / S5.1; none
  has begun. This pass itself awaits separate commit approval (documentation changes made,
  nothing committed).

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
