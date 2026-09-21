# AUDIT-CTRL-1 — GitHub qualification / merge control repair

> **Work package record.** Scope: the qualification/merge control loop only. No CI redesign, no
> documentation sweep, no product change. Nothing in this package alters Bartholomew's runtime,
> governance, Parking Brake or any user-facing behaviour — it is project control, in the sense
> `START_HERE.md` §3 uses the word, and the word "control" must not be carried across into
> Bartholomew's own control architecture.

## 1. What was actually wrong

Merge qualification was a **narrative produced by whoever happened to be driving the merge**. The
claim "all required checks are green and there are no unresolved review findings" was written into
a report, a pull-request comment or a canonical document, and the merge proceeded on the strength
of that sentence. Nothing computed the claim, nothing bound it to a commit, and nothing refused
when the underlying facts could not be established.

The completed audit found four distinct shapes of the same defect:

| Pull request | What the process believed | What was true |
|---|---|---|
| **#108** | The required tiers had been exercised. | The Merge Candidate tier **never ran** on the reviewed head — it does not run on an unlabelled pull request — and **three substantive Codex findings** against that head survived into `main`. |
| **#101** | The head was green. | The **exact final head** had a **failing** Merge Candidate workflow, and it was merged anyway. |
| **#91** | The findings were handled. | **Two Codex P2 findings** were open against the merged head and were repaired only later, in PR #92. |
| **#120** | Zero review comments, zero unresolved threads. | A **substantive Codex finding was extant**. |

Read together these are not four incidents. They are one failure class with four faces: *missing
evidence, failing evidence, unaddressed evidence, and misreported evidence*, all of which the
process could convert into "merge-ready".

## 2. Root cause

**Qualification had no deterministic authority, and no binding to a commit.**

Three things follow from that, and all four incidents are explained by them:

1. **The absence of a failure was treated as a pass.** "No red check" and "proven green" are not
   the same statement. A tier that never ran (#108), a tier still running, a cancelled run, a
   skipped job — none of them is a failure, and none of them is evidence either.
2. **Evidence was not head-bound.** A qualification formed against one commit continued to be
   believed after the head moved. #101's merged head is the sharp case: the tier's result on *that
   commit* was red.
3. **The final authority was a judgement rather than a record.** A human or a model read the page
   and reported a conclusion. That is exactly where #91 and #120 went wrong: a finding that was
   real, and visible, was reported as absent. A summary is not a control.

## 3. The repair

A deterministic gate, in the repository, that computes the verdict from forge state and committed
records and **fails closed on anything it cannot establish**.

### 3.1 Everything binds to one exact head

A check run is evidence only for the commit it ran against. A finding is classified relative to the
commit it was written against. A qualification report names the head it was computed for, and
`verify_evidence_applies_to()` refuses it against any other commit — so **a report generated for
commit A cannot authorise commit B**. The gate workflow re-runs on every `synchronize`, so a new
commit invalidates the previous head's qualification by construction.

### 3.2 Green must be proven, not assumed

`.github/merge-qualification/config.yml` lists the required tiers **job by job**, as
`(workflow, job)` pairs rather than bare job names — three workflows in this repository publish a
job called `smoke`, and two publish `Quality (format, lint, packaging contract)` and
`Tests + coverage (Ubuntu, py3.11)`. A bare name cannot tell the PR Fast tier from the Merge
Candidate tier, which is precisely what #108 could not tell.

Each required job must be **completed with conclusion `success` against the exact head**. The gate
distinguishes, and names in its evidence, the states that are *not* green:

`red` · `incomplete` · `cancelled` · `skipped` · `missing` · `stale` (green, but for another head) ·
`unknown` (including GitHub's `neutral`, and any conclusion the forge invents later).

**The Merge Candidate tier is required.** It does not run on an ordinary pull request; it needs the
`ci:merge-candidate` label. Until that label is applied and the tier is green on the exact head, the
gate answers NOT READY. That is the correct answer, not a tooling gap — it is #108's defect,
refused.

### 3.3 Review findings are classified truthfully, never ignored and never permanently blocking

Every finding is placed in exactly one of five states:

| Classification | Blocks? | How it is established |
|---|---|---|
| `unresolved_substantive` | **yes** | An open thread against the current head with no disposition. |
| `resolved_by_later_commit` | no | An explicit record naming a **full sha that is one of this pull request's commits and is strictly later** than the commit the finding was written against. Both conditions are checked against the commit list; a claim is verified, not believed. |
| `stale_superseded_head` | no | An explicit record, **refused** if the finding is in fact against the current head. |
| `non_substantive` | no | An explicit record. |
| `unknown` | **yes** | Anything else, including "the forge says the thread is resolved and nothing says why". |

Dispositions live in `.github/merge-qualification/dispositions.yml`. They are repository state, so
they arrive through a reviewed commit, and every one carries a rationale and a `recorded_by`.
`unknown` is not a permitted disposition: it is the state the evaluator assigns when it cannot
tell, and writing it down would be recording a decision nobody took.

**Forge-side "Resolve conversation" does not, on its own, dispose of a finding**
(`github_resolution_satisfies_disposition: false`). It is recorded in the evidence, and it leaves
the finding `unknown`, which blocks. Resolution is a click; it is not evidence that the code
changed. That is #120, refused — and #91, whose findings were genuinely fixed, but in a *different*
pull request, and a commit that is not one of this pull request's commits cannot dispose of a
finding here.

### 3.4 Unknown fails closed

A collector that cannot read check state or review state says so, by clearing
`check_state_determined` / `review_state_determined`. It never returns an empty list for the
evaluator to read as "nothing wrong". An unusable config or a malformed dispositions file raises
and the CLI exits non-zero. A head that is not a full 40-character sha qualifies nothing. There is
no path through the program on which "the gate broke" reads as "the gate passed".

### 3.5 Model judgement is not the authority

`scripts/ci/merge_qualification/evaluate.py` is a pure function of forge observations and committed
records: no network, no clock, no model, same input → same verdict (pinned by a test). A model may
help a person read a finding or draft a disposition record; the record then goes through review
like any other change. Nothing a model asserts can reach `READY`.

## 4. What this package does **not** do

- It does **not** merge, approve, or auto-label anything. It reports.
- It does **not** change any CI tier, threshold, timeout or test.
- It does **not** define any finding away, and it weakens nothing: every state it added is a state
  that **refuses**.
- It is **not** a branch-protection configuration. Making this gate a required status check on
  `main` is a repository-settings change that is Taylor's to make, and it is deliberately left
  outside this package. Until it is, the gate is an authoritative *report*, and the residual risk
  is that a human ignores it.

## 5. Files

| Path | What it is |
|---|---|
| `scripts/ci/merge_qualification/model.py` | The state vocabulary. Names the distinctions that must not collapse. |
| `scripts/ci/merge_qualification/evaluate.py` | The deterministic evaluator and the head-binding check. |
| `scripts/ci/merge_qualification/config.py` | Loads the committed required tiers and dispositions; raises rather than degrading. |
| `scripts/ci/merge_qualification/collect.py` | Forge I/O only. Normalises; decides nothing. |
| `scripts/ci/merge_qualification/cli.py` | `qualify` and `verify`. Exit codes: 0 ready, 1 not ready, 2 control state broken. |
| `.github/merge-qualification/config.yml` | Which tiers must be green. Changing it is a reviewed commit. |
| `.github/merge-qualification/dispositions.yml` | Explicit findings dispositions. Empty until something needs one. |
| `.github/workflows/merge-qualification.yml` | Runs the gate's own regression suite, then the gate. Not in its own required-tier list: a gate cannot be its own evidence. |
| `tests/test_merge_qualification.py` | The regression suite. |
| `tests/fixtures/merge_qualification/*.json` | Scenario fixtures, four of them derived from #108, #101, #91 and #120. |

## 6. How to use it

```
python -m scripts.ci.merge_qualification qualify --repo owner/name --pr 123 \
    --out merge-qualification-evidence.json
python -m scripts.ci.merge_qualification verify --evidence merge-qualification-evidence.json \
    --head <the exact head sha now>
```

`qualify` prints every required check with its outcome, every finding with its classification, and
every reason it refused. When a finding genuinely is stale, resolved or non-substantive, add a
record to `.github/merge-qualification/dispositions.yml` keyed by the `finding_id` the gate printed,
and commit it.
