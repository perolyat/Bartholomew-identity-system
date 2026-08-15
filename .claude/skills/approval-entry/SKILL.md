---
name: approval-entry
description: Draft the paired governance records Bartholomew requires when a change is approved — the MASTER_PLAN.md Approval Ledger line, the DECISIONS.md entry and its header note, and any RISKS/ASSUMPTIONS/CHECKLISTS updates. Use when the user approves a change, when a decision needs recording, when moving an item from Pending Approvals to the ledger, or when adding a commit hash to a previously recorded approval.
---

# Approval and decision recording

Getting these records right first time matters: this repository's ledger once read "*No entries
yet*" while five approved changes had already merged. The records are the audit trail for a
consent-first, governance-first system — treat them as a deliverable, not bookkeeping.

## Which records a change needs

| Change | Records required |
|---|---|
| Any approved change | `MASTER_PLAN.md` Approval Ledger line |
| Architectural choice, or one with rejected alternatives | + `DECISIONS.md` entry **and** header note |
| Newly discovered defect, debt, or unresolved disagreement | + `RISKS.md` watchlist entry |
| New unvalidated premise | + `ASSUMPTIONS.md` entry |
| New recurring gate to enforce | + `CHECKLISTS.md` line |
| Behaviour or contract change | + `INTERFACES.md` / `TEST_MATRIX.md` |

## The lifecycle

```
Agent proposes → User reviews → User approves → Commit executed → Record in ledger
```

An item sits under `MASTER_PLAN.md`'s **"Pending (awaiting user approval)"** while proposed, and
moves to the **Approval Ledger** once approved and committed.

**Rule, stated in the document itself: never mark anything as committed without a commit hash.**
If the commit has not happened, the entry reads `**not yet committed**` and is updated afterwards.

## Approval Ledger format

```
- YYYY-MM-DD — <short description> — Approved by <user> — Commit: <hash>
```

Conventions actually used in this repo:

- **Full 40-character hash** for recent entries; short hashes appear only in older ones.
- **State the blast radius in the description.** The established phrasing is
  `(documentation-only, no production code/tests/dependencies/workflows/configuration/schema touched)`.
  Use it when true; when it is not true, say specifically what *was* touched.
- **Name the affected documents** — e.g. `` (`DECISIONS.md` new decision entry; `RISKS.md` three
  new tech-debt watchlist entries; `CHECKLISTS.md` one new PR-checklist line) ``.
- **Approved by project owner** is the standard attribution.
- The ledger header says **most recent 5**; older entries stay below the explanatory note. Add new
  entries at the top of that recent group.
- If a change landed without a merge commit or PR number, say so explicitly rather than leaving it
  ambiguous — see the `bc5f24d`, `29d0ec9` entry as precedent.

## DECISIONS.md entries

Two parts, both required.

**1. The header note.** `DECISIONS.md` opens with a `> **Last updated:**` block followed by a chain
of `> **Previously (<date>):**` notes. On each pass: write the new note as "Last updated", and demote
the existing one to "Previously". Do not delete the chain — it is the document's own history.

The note summarises: how many decisions were added, their titles, what they establish, whether
implementation was authorised (usually **not** — most entries are documentation-only), and
cross-references to every other canonical doc touched in the same pass.

**2. The decision entry itself,** which must carry:

- **What was decided** — plainly stated.
- **What prompted it** — the assessment, review, or event.
- **Alternatives considered and why they were rejected.** This is the part that gives the log its
  value; an entry without it is a changelog line. The repo has real precedent for recording a
  rejected alternative *because* it would have been a consent bypass.
- **Consequences**, including what it extends rather than replaces.
- **Authority pointer** — which canonical document now owns the semantics.
- **What is explicitly NOT authorised** by this decision.

## Corrections

Never silently edit an approved record. The convention is a dated, explicit note:

```
**Corrected 2026-08-14** (same day, following an automated review comment): <what was wrong,
what it now says>. Wording-only; the decision's substance is unchanged.
```

Say whether the correction is wording-only or substantive. If a decision is superseded, mark it
superseded and point to its replacement — do not remove it.

## Before writing anything

1. Confirm approval actually happened, explicitly, for **this specific change**. Sequencing in
   `MASTER_PLAN.md` is not approval.
2. Get the real commit hash with `git log` — never reconstruct or guess one.
3. Check whether the decision already exists in `DECISIONS.md` (run `ssot-check`), so you extend
   rather than duplicate.
4. Present the drafted records to the user for review before committing them. The records
   themselves are a doc change and fall under the same approval gate.
