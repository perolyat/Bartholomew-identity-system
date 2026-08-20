# Test #1 — evidence location (stable, referenceable)

> **Status:** Evidence store. **Non-canonical as authority**, but **canonical as location**: this
> directory is the stable, referenceable home for Real-World Test #1 evidence artifacts and for the
> approved post-test interpretation of them. Nothing here is a decision authority. The authority
> hierarchy is `CONSTITUTION.md` → canonical SSOT docs → approved implemented subsystem designs →
> direct Test #1 evidence → approved post-test interpretation/decisions → independent-review
> recommendations.
>
> **Created:** 2026-08-20, under the documentation-only handoff that followed Taylor's approval of
> the Post-Test #1 Decision Register v2.2. See `docs/evidence/test-1/MANIFEST.md` for the artifact
> inventory and checksums.

## What this directory is for

Post-Test #1 Decision Register v2.2 §13 item 4 requires Test #1 evidence artifacts and checksums to
be preserved at a stable referenceable location, so that a future reader can trace a register claim
back to the artifact it rests on. This directory is that location. It did not exist before
2026-08-20; the repository had no evidence store at all.

## Two kinds of content, deliberately kept apart

| Layer | Directory | What it is | May it be edited? |
|---|---|---|---|
| **Raw / historical evidence** | `raw/` | Artifacts produced *by* the test run — logs, captures, exported records, matrix result sheets, individual finding records. | **No.** Historical evidence is immutable for this purpose. Corrections are recorded *about* an artifact, never *inside* it. |
| **Post-test interpretation** | `interpretation/` | Documents produced *about* the test run after it finished — the Decision Register, review records. | Only by their own governed process. Superseded versions are added alongside, not overwritten. |

`raw/` is currently **empty of artifacts**. That is a recorded fact, not an oversight — see
"Evidence-access limitation" below and the absence inventory in `MANIFEST.md`.

## Test #1 implementation provenance (the evidence freeze)

**The commit hash is the authoritative freeze. A branch name is not.**

| Fact | Value |
|---|---|
| Authoritative tested implementation | `854a8da7fd107db33a933c4bdb01bf3fd7eb69bd` |
| What that commit is | The **merge commit for PR #58** |
| PR #58 head branch | `claude/bartholomew-parking-brake-consent` — **no longer resolves** |
| Reachable from, at independent-review time | `claude/bartholomew-test1-review-rfukzi` |
| Repository `main` / `origin/main` at the relevant review point | `d0c202f7b39f9244417f1954629f64f68dfbb341` (2026-08-15) |
| Did that `main` contain the tested implementation? | **No.** It was 25 commits behind the tested commit. |

**Test #1 was not run against `main`.** Any document that says or implies otherwise is wrong.

**Current repository state, recorded separately so it cannot be mistaken for the historical freeze
(verified 2026-08-20):** the tested commit `854a8da…` is reachable from this documentation branch,
`claude/bartholomew-post-test-docs-f8xwr1`, which is based directly on it; `main`/`origin/main` is
still `d0c202f7b39f9244417f1954629f64f68dfbb341`, and `git merge-base main 854a8da…` returns
`d0c202f…`, confirming the 25-commit relationship the register records. Later branch movement does
**not** rewrite the historical record above: whatever branches come and go, the frozen tested
implementation remains the commit hash.

## Evidence-access limitation (approved, still in force)

The independent review's evidence-access limitation is **not** discharged by this directory:

> Restored Test #1 case IDs and timestamps were verified for **internal consistency only**. They
> were **not** independently re-verified against the raw Test #1 artifacts, which were not
> accessible to the review.

Creating a stable location does not retroactively produce the artifacts. Every §5 register row that
cites a case ID, a timestamp, or a log line is therefore traceable to the register's own record and
to Taylor's direct observation, **not** to a preserved artifact in this repository. Until raw
artifacts are deposited in `raw/` and checksummed, that limitation stands, and any future claim of
independent verification of a Test #1 case ID or timestamp is unsupported.

## Depositing artifacts later

If the raw Test #1 artifacts become available:

1. Place them under `raw/`, unmodified. Do not clean, reformat, redact-in-place, or re-derive them.
2. Record each one in `MANIFEST.md` with its path and SHA-256 digest
   (`sha256sum <path>`), moving it from the absence inventory to the present inventory.
3. If an artifact contains sensitive material, record that fact in `MANIFEST.md` and handle it under
   the D12 test-data policy — do not edit the artifact to make it safe to store.
4. Do **not** reconstruct a missing artifact from the Decision Register. The register is
   interpretation *of* evidence; regenerating "evidence" from it would produce a circular record
   that looks corroborated and is not.

## Related documents

- `docs/evidence/test-1/MANIFEST.md` — artifact inventory, checksums, absence record.
- `docs/evidence/test-1/interpretation/BARTHOLOMEW_POST_TEST_1_DECISION_REGISTER_v2_2_FINAL_APPROVAL_CANDIDATE.md`
  — the approved register, preserved byte-for-byte.
- `docs/FIRST_REAL_WORLD_TEST.md` — the Test #1 *procedure* (how to run it, what counts as a pass).
  Pre-test, not evidence of results.
- `DECISIONS.md` — the fifteen approved post-Test #1 decisions, in this repository's own convention.
- `ROADMAP.md` "Post-Test #1 readiness bands" — the sequencing consequence.
- `MASTER_PLAN.md` "Approval Ledger" — the approval record.
