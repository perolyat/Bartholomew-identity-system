---
name: ssot-check
description: Check a proposed change, claim, or piece of work against Bartholomew's 14 canonical SSOT documents before it is written or committed. Use when about to modify a canonical doc, when a statement about project status needs verifying, when you suspect documentation drift, or when the user asks "does this contradict anything?" / "is this already decided?" / "what's the SSOT say about X?".
---

# SSOT check

This repository's failure mode is **documentation drift**: a claim in one document quietly
contradicting another, surviving because nobody grepped. It has already required two full
reconciliation passes. This skill is the pre-flight check that prevents a third.

## When to run this

- Before editing any of the 14 canonical documents.
- Before stating project status, stage completion, or "what's next" to the user.
- Before implementing anything that a canonical doc might already have decided, deferred, or ruled out.
- When a non-canonical file (README, `docs/*`, an implementation note) makes a status claim.

## The 14 canonical documents

`MASTER_PLAN.md`, `CONSTITUTION.md`, `COGNITIVE_RUNTIME.md`, `ROADMAP.md`, `docs/TILT.md`,
`DECISIONS.md`, `RISKS.md`, `ASSUMPTIONS.md`, `INTERFACES.md`, `CHECKLISTS.md`, `REVIEWS.md`,
`CI.md`, `TEST_MATRIX.md`, `PERF_BUDGETS.md`.

Everything else is a reference and loses any conflict. `docs/incubator/` and `docs/archive/` are
permanently non-authoritative.

## Procedure

**1. Extract the claim.** State in one sentence what is being asserted or changed. Vague inputs
produce useless checks — sharpen it first.

**2. Grep, do not read.** These files total ~500KB. Search for the concept across the canonical set:

```bash
grep -rniE "<concept>|<synonym>" MASTER_PLAN.md CONSTITUTION.md COGNITIVE_RUNTIME.md \
  ROADMAP.md docs/TILT.md DECISIONS.md RISKS.md ASSUMPTIONS.md INTERFACES.md \
  CHECKLISTS.md REVIEWS.md CI.md TEST_MATRIX.md PERF_BUDGETS.md
```

Then read only the matching regions with `sed -n 'START,ENDp'`.

**3. Check the four registries specifically.** A concept can be live in one and dead in another:

| Registry | Question |
|---|---|
| `MASTER_PLAN.md` "Next 3 Moves" + "Backlog" + "Pending Approvals" | Is this sequenced? Is it *authorised*? Those are different. |
| `DECISIONS.md` | Was this already decided — or explicitly rejected as an alternative? |
| `RISKS.md` | Is this a known open finding (e.g. F9) rather than a new discovery? |
| `ASSUMPTIONS.md` | Does this rest on an assumption already recorded as unvalidated? |

**4. Apply the TILT test.** `docs/TILT.md` is binding on sequencing. Ask: *what real Bartholomew
capability does this unlock for the tester?* If the answer is cleaner architecture, fuller docs,
more abstraction, future-proofing, theoretical correctness, or polish — it is deferred, and say so
even if the work is otherwise good. Only six things override this: defects threatening safety,
governance, privacy, data integrity, architectural validity, or the validity of the experiment.

**5. Report.** Use this shape:

```
CLAIM: <one sentence>
STATUS: consistent | contradicts | already-decided | already-open-risk | not-covered
EVIDENCE: <file:line> — <quoted phrase>
TILT: proceeds | deferred (reason)
APPROVAL: authorised (<where recorded>) | NOT authorised — needs explicit approval
ACTION: <what to do, or what to ask the user>
```

## Rules

- **Never resolve a contradiction silently.** Surface both sides with citations and let the user
  decide which is authoritative. Picking one yourself is how drift becomes permanent.
- **Listed is not approved.** A backlog or roadmap entry records sequencing. `MASTER_PLAN.md` says
  each step requires its own separate explicit approval before work begins.
- **Corrections are dated and explicit.** This repo's convention is
  `*Corrected 2026-07-28: <what was wrong and why>*`, not a silent edit. The history of the
  correction is part of the record.
- **"not-covered" is a real, useful outcome.** Say so plainly rather than stretching an unrelated
  passage to cover the case.
