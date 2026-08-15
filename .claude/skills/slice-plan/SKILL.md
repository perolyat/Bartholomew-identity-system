---
name: slice-plan
description: Scope the next Usable POC vertical slice for Bartholomew and produce an approval-ready planning note. Use when planning what to build next, when the user asks "what should the next slice be" or "plan slice N", when turning tester feedback into scoped work, or when a proposed piece of work needs sizing against the TILT time-to-real-use priority.
---

# Slice planning

`docs/TILT.md` is canonical and binding: work is sequenced by **time to real use**, and later
slices are **scoped from real feedback rather than designed ahead of it**. This skill produces a
planning note in the shape this repo already accepts — see
`docs/POC_SLICE_1_MEMORY_CAPTURE_RECALL.md` as the worked precedent.

## Before scoping anything

Establish where slice 1 actually stands in *real use*, not in implementation:

- Slice 1 (Personal Memory Capture and Recall) is merged (`2d443a9`).
- The recorded next move is putting it into real use — configuring
  `BARTHOLOMEW_NOTIFY_WEBHOOK_URL` and running the capture/recall loop against a real endpoint.

**If real usage has not happened yet, that is the answer.** Say so. Do not scope slice 2 from
imagination — `docs/TILT.md` explicitly forbids designing later slices ahead of feedback. The
correct output in that case is a short note saying the blocker is usage, not planning.

## The prioritisation test

> **What real Bartholomew capability does this unlock for the tester?**

Deferred if the honest answer is: cleaner architecture, more complete documentation, additional
abstraction, future-proofing, better theoretical correctness, or polish.

Overrides — the only six reasons to hold a shippable slice back: defects threatening **safety,
governance, privacy, data integrity, architectural validity, or validity of the experiment**.

## What makes a good slice here

- **Vertical.** Touches the real path end to end — capture → governed store → retrieval → visible
  result. A slice that stops at a well-tested internal seam is not a slice.
- **Reuses existing seams.** Slice 1 extended the consent-gated write path and the
  competency-retrieval seam rather than building new machinery. Prefer the same.
- **Produces something the tester can notice.** A capability with no observable surface cannot
  generate feedback, so it cannot advance the POC.
- **Smallest safe.** `MASTER_PLAN.md`'s backlog is explicitly ordered as "smallest safe slices".
- **Governance intact from the start.** Consent gate, parking brake, redaction/retention are part
  of the slice, never a follow-up.

Deferred-but-real material to draw on (`ROADMAP.md`): S5.4 experience → learning/consolidation
loop; S5.5–S5.7 initiative safety scaffolding, dry-run, controlled live initiative. These are
deferred, not discarded.

## Planning note structure

Write to `docs/POC_SLICE_<N>_<NAME>.md`:

1. **What the tester will be able to do** — in plain language, from their side. Not architecture.
2. **Why this, now** — the TILT test answered explicitly, plus the real feedback it came from.
3. **Existing seams reused** — name the modules and contracts; cite `INTERFACES.md`.
4. **Scope boundary** — and, just as important, what is deliberately *not* in it.
5. **Governance treatment** — consent gate path, parking-brake tier, redaction/retention, audit
   record. State how each is satisfied.
6. **Acceptance criteria** — observable, testable, stated before implementation.
7. **Test plan** — new tests and markers; reconcile with `TEST_MATRIX.md`.
8. **Known limitations** — what will be true but imperfect on delivery. Slice 1's note does this
   well; imitate it.
9. **Rollback note** — how to undo it if real use goes badly.

## Approval

The planning note and the implementation are **two separate approvals**. Slice 1 followed exactly
this: planning note approved in `4de2962`, implementation approved separately and explicitly, then
delivered in `2d443a9`.

Do not begin implementation on the strength of an approved planning note. Present the note, get
approval for the note, then ask separately for implementation approval. Record both in
`MASTER_PLAN.md`'s Approval Ledger and `DECISIONS.md`.
