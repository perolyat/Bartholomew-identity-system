# R-EXEC02-2 — Governed Conversational Approval

**Status:** **approved at the User Approval Gate, 2026-09-19, and merged.** PR #117.
**Provenance:** code head `07c5af8`; approved head `4e8799c`; final reviewed head and merge
commit in §10.
**Baseline it was built on:** `origin/main` at `f99bf4245de4bbc2fce0836495341aa767895428`
(the merge of PR #116).
**Scope:** the governance boundary between the conversational surface and the existing
approval authority. No approval authority was created, replaced or duplicated; no
Executive cognition was added; no capability was added; no action class became
approvable that was not approvable before.

> **Written at merge, not retrofitted.** `RISKS.md` records stale status headers as a
> project-control defect in its own right (R-CTRL-1), and EXEC-01's header claiming "not
> merged" after PR #108 merged is the precedent that rule exists for. This header, §10's
> CI table, `START_HERE.md` §5b and `MASTER_PLAN.md`'s Approval Ledger were all written in
> the implementation PR, in the hour of the approval — not in a follow-up.
>
> **One field this PR structurally could not carry:** its own merge commit, which does not
> exist until the merge happens. §10 names it; it is the merge of PR #117 and is the single
> identifier a reader must take from git history rather than from this document.

---

## 1. The gap this closes, stated precisely

`RISKS.md`, R-EXEC02-2, at `f99bf42`:

> **A goal can be stated in chat but not approved there.** The surface that understands a
> goal is not the surface that authorises it: approving a proposed action remains the
> operator console's. **Consequence:** the conversational experience is genuinely
> incomplete end-to-end, and nothing should describe it as a finished user journey.

EXEC-02 got a sentence typed into chat as far as a governed `pending_approval` row. It
stopped there, and the person had to open a different surface to finish. This package
carries the *decision* the rest of the way — and nothing else.

**What was never the gap.** There was no missing approval mechanism. There was no missing
governance. `bartholomew/actuation/seam.grant_action_approval` already did the whole job,
correctly, behind eleven ordered gates. The gap was that nothing conversational could
reach it *safely* — because a chat surface has no row to click, and "yes" on its own does
not name an action.

---

## 2. What is, and remains, the canonical approval authority

`bartholomew/actuation/seam.grant_action_approval()`, and after this package it is still
the only one. `cancel_action_through_runtime_contract()` is the matching withdrawal seam.
Both are the same functions `POST /api/operator/actions/{id}/approve` and `.../cancel`
call, with the same arguments, the same contracts and the same governance.

There is no second approval store, no chat-specific execution path, no second Governance
authority, no route around the action envelope, no model-based approval, and no bypass of
identity/tenant/device ownership, the Parking Brake, audit, verification or recovery.
`tests/test_conversational_approval_no_bypass.py` asserts the strong form of that from the
repository's own syntax trees: **the complete set of `grant_action_approval` callers
outside tests and scripts is three** — the envelope itself, the two HTTP routes, and this
package's adapter — and a fourth appearing fails a test somebody has to justify.

### The three changes to the shared contract

Made in the shared authority rather than hidden in chat-specific code, exactly as §4 of
the package brief requires.

| Change | Where | Why it belongs there |
|---|---|---|
| `ActionApproval.surface` | `actuation/approval.py` | An approval record could say *who* decided and *what* they decided, but not *through which surface*. An audit distinguishing a decision made where a principal was verified from one made where it was not needs that on the record, not inferred from a timestamp. Defaults to `operator_console`, which is what every approval granted before this field existed actually was. |
| `capabilities.CONVERSATIONAL_APPROVAL_INELIGIBLE` | `actuation/capabilities.py` | Derived from the existing `ALWAYS_APPROVAL` set, never written twice. §4 below. |
| `grant_action_approval(..., surface=...)` enforces that set | `actuation/seam.py` | Enforcement in the authority, not in the caller, so a second conversational caller written later inherits the restriction instead of having to remember it. |

**`surface` is provenance, never authority.** It is deliberately absent from
`ActionApproval.authorizes()`: eligibility is decided once, at the moment of granting.
Re-deciding it at dispatch would mean a configuration change could silently revoke a
decision a person had already made and been told was recorded.

---

## 3. How conversation reaches the authority, and how the decision is bound

```
person states a goal
  -> EXEC-02's goal recogniser, EXEC-02's activation, the existing Executive
  -> the existing envelope writes one pending_approval row          [unchanged]
  -> chat renders it truthfully                                     [unchanged]
  -> chat records a PRESENTATION of what it just showed             [new]
person replies "yes"
  -> approval_intents.parse_decision: whole-utterance, deterministic, no model
  -> conversational_approval.decide: resolve the presentation, or refuse
  -> seam.grant_action_approval(surface="conversation")             [the authority]
  -> pending_approval -> approved, one conditional UPDATE           [unchanged]
  -> the device leases it, every gate runs again                    [unchanged]
  -> the device reports; verification decides                       [unchanged]
person asks "did it work?"
  -> the action store's own state column, one sentence per state    [new]
```

### Three modules, and what each may not do

| Module | Does | Cannot |
|---|---|---|
| `bartholomew/kernel/approval_intents.py` | decides whether a *string* is a decision; renders every outcome | read a store, read a clock, reach a model, know what is pending — a purity the AST test enforces |
| `bartholomew/integration/conversational_approval.py` | records what was shown; resolves "yes" to one action or refuses; calls the authority | dispatch, lease, abort, record a result, write the action table, or move an action's state itself |
| `bartholomew/kernel/runtime_contract.py` (one handler) | gates on the activation, recognises, renders | reach the envelope at all — every governed call goes through the adapter, so there is one place to audit |

### The presentation record, and why it is not an approval store

`conversational_action_presentation`, one row per conversational surface
(`tenant::device::requested_by`), written through `MemoryStore` — the same authority and
the same call shape the approval itself uses. It carries the action id, device, capability,
capability version and **the `parameter_fingerprint` of the proposal as the person saw it**.

**It cannot authorise anything, and that is testable rather than asserted.** Nothing
downstream reads it; the envelope does not know it exists. Delete every one and no approved
action becomes unapprovable and no pending action becomes dispatchable. *Forge* one — a row
naming an action nobody was shown — and the only thing it buys is the right to **ask** the
authority, which then re-reads the action, re-validates it against the device's current
allowlists, re-reads the Parking Brake, checks the state is `pending_approval`, and refuses
if any of that fails (`TestOneShot::test_a_forged_presentation_buys_only_the_right_to_be_refused`).

It is a pointer, held so that a pronoun in a sentence can be resolved to a noun. What it
*does* is narrow the surface: "yes" means one proposal this person was actually shown,
rather than "whatever is pending".

### What the binding prevents, and where each is proved

| Must not be possible | Prevented by | Test |
|---|---|---|
| "yes" approving an unrelated pending action | the presentation names one action id | `test_yes_cannot_approve_an_unrelated_pending_action` |
| "yes" approving something never shown in chat | no presentation, no target | `test_yes_with_a_pending_action_nobody_showed_approves_nothing` |
| an old turn approving a newer proposal | one slot; a new proposal supersedes | `test_a_newer_proposal_supersedes_the_older_one` |
| a proposal modified after presentation | fingerprint + capability + version + device compared | `test_a_proposal_changed_after_presentation_is_refused` |
| a decision replayed | the authority's conditional UPDATE | `test_a_replayed_decision_cannot_reopen_a_consumed_approval` |
| the same approval executing twice | `mark_approved` from `pending_approval` only, then the lease's own replay guard | `test_saying_yes_twice_approves_once` |
| another identity/tenant/device consuming it | the three ownership dimensions re-checked against the live activation | `test_another_identity_cannot_consume_the_decision` |
| an expired, rejected, cancelled or consumed action being resurrected | state and expiry checked, then re-checked by the authority | the five `TestStaleProposalsRefuse` cases |

**Ambiguity fails safe.** More than one proposal in a turn arms nothing and sends the
person to the console. It never picks the most recent.

---

## 4. Which action classes conversation may not authorise, and why that is a narrowing

The three `ApprovalRequirement.ALWAYS` capabilities — `clipboard_read`, `type_text`,
`accessibility_action` — **cannot be approved in conversation.** The refusal is truthful and
names the operator console.

`CONVERSATIONAL_APPROVAL_INELIGIBLE` is *derived from* `ALWAYS_APPROVAL` rather than written
as a second list, so the two can never drift and so this is a **restatement of an existing
governance distinction, not a new one**. Those three are already the kinds that read or
synthesise content on the person's behalf, and already the kinds no configuration may ever
place under trusted autonomy.

The reasoning for applying that line here: **conversation is a weaker approval surface than
the operator console.** The console's approval arrives on an HTTP request carrying the
platform capability `action:approve` and a principal the platform verified. A chat turn
carries the runtime's *configured* `requested_by` and no verified principal at all. So
conversational approval reaches the six kinds an approver could already authorise from a
surface of equal strength, and refuses the three already singled out as needing the
strongest one.

**This narrows what chat can reach; it widens nothing.** Nothing approvable before this
package is less approvable now — `TestSurfaceEligibility::test_the_same_action_is_still_approvable_at_the_console`
pins that.

> **For Taylor's decision at the gate.** This is a governance judgement made inside this
> package, and it is the one call here that could reasonably have gone the other way. If
> your view is that a conversational surface is *not* materially weaker than the console on
> a single-user local deployment, the line is one derived frozenset and the restriction can
> be removed without touching the binding, the lifecycle or the authority. §11.

---

## 5. Recognition is deterministic, and a model is never asked

`parse_decision` claims an utterance only when the **whole** of it, once trimmed of an
optional lead-in and an optional politeness, is a member of a closed phrase set. Not
"contains yes". Not "starts with yes". The whole of it.

That single rule is what keeps all of these conversation: *"If I say yes, what happens?"*,
*'Does "yes" approve it?'*, *"I would say yes but I want to think about it"*, *"Yes, I
remember that file"*, *"yes — but change the folder first"*, *"no idea what that does"*.
A qualified, hedged, quoted or embedded affirmative is not an unambiguous grant.

Weak acknowledgements — **"ok", "sure", "fine", "sounds good"** — are excluded on purpose.
They are what a person says while still thinking. A false negative costs one more word; a
false positive causes something to happen on a person's machine that they did not
authorise. The errors are not remotely symmetric.

**No model decides whether permission was granted.** If it did, a sufficiently persuasive
page of text in the model's context would be an approval and the whole `pending_approval`
gate would be decorative. The AST test forbids the recogniser from importing a clock, a
store, a router or a port; the vertical slice asserts that neither the conversational model
nor the deliberation port is consulted on a decision turn.

### Why the entry is *first* in `_CHAT_DISPATCH`, and why that is safe

"cancel that" must be read as a decision about a proposal just put to the person, not
reinterpreted by a broader recogniser. It is safe in first position **precisely because the
recogniser claims only bare decisions**: anything carrying content of its own — "yes, open
notepad", "no, sort these files out instead", "add a task to ring the roofer" — falls
straight through to the recogniser that has always owned it. EXEC-02's ordering property is
therefore intact and is now asserted as the property rather than as a fixed index, in both
`test_chat_dispatch_table.py` and `test_exec02_conversational_executive.py`.

---

## 6. Approval is not execution, and the surface says so

Six states, six renderings, and no branch that rounds one up:

| State | What the person is told |
|---|---|
| proposed / awaiting approval | *"is still waiting for your approval. Nothing has run."* |
| approved | *"approved … eligible for your PC to pick up … **it has not run yet**, and I won't tell you it worked until the machine has reported back and the effect has been checked independently."* |
| execution started | *"has been picked up by your PC and is running now. There's no result yet."* |
| succeeded | *"ran, and the effect was observed."* — the only rendering that reads as done |
| failed | *"ran and did not take effect … nothing changed."* |
| **unknown** | *"your PC could not observe whether it actually took effect. I do not know whether it worked, and I'm not going to guess."* |

`unknown` is a first-class answer, never folded into either success or failure.
`TestApprovalIsNotExecutionSuccess` drives a real action all the way through dispatch and a
recorded device result for each of the three terminal cases, and
`test_no_renderer_claims_work_is_finished_merely_because_it_was_approved` holds every
renderer in the module against a list of completion claims.

Approving writes **no** lease, **no** result and **no** terminal timestamp — asserted on the
real row, not on the reply.

---

## 7. Parking Brake precedence

Unchanged, and load-bearing at three separate points:

1. **Chat's own Governance stage** fails closed on `skills` before `_CHAT_DISPATCH` runs at
   all, so on a braked runtime the decision turn is never reached.
2. **`grant_action_approval` reads the brake first**, both tiers, fail-closed — so a
   proposal made before the brake went on gains no right to be authorised after it. Proved
   one layer below chat, with chat's own gate taken out of the picture, so the refusal is
   demonstrably the actuation brake's.
3. **The dispatch path re-reads the brake immediately before the lease.** Approved-then-braked
   stays approved and undispatchable.

**Withdrawing is deliberately still possible while braked.** The cancel seam is not
brake-gated — a halt that stopped somebody withdrawing a pending action would be a halt that
made things less safe — and this surface inherits that rather than inventing a new rule.

No new brake semantics were introduced.

---

## 8. Audit and provenance

The chain — proposal → human authority decision → Governance's decision → execution →
verification — is reconstructible from durable records, and the two claims that must never
merge are two rows:

| Record | Where | What it is |
|---|---|---|
| Executive/model reasoning | chat Reflection, `executive_action` | what was recognised, whether cognition was consulted, what was proposed, and `executed: false` |
| what was shown to the person | `conversational_action_presentation` | the exact action, capability, version and fingerprint as described |
| the human's authority decision | the same record's `decision` block, and the approval's `note` | the person's **verbatim** words, bounded, plus when |
| Governance's decision | the authority's own Reflection + the `ActionApproval` | approver, `surface: conversation`, expiry, fingerprint |
| execution | `windows_action_requests.lease_count` / `leased_at` | the device leased it |
| verification | `windows_action_results` | the device's observed status, and any read-back verdict as evidence only |

`human_decision_recorded` is a separate boolean on the chat record and is true only when the
*authority* recorded a decision on that pass, so an audit reader never has to infer "a person
authorised this" from a word in a reply. `test_the_reflection_carries_the_human_decision_separately`
asserts the two are different rows and that neither contains the other.

**A rejection is preserved, not deleted.** The presentation moves to `rejected` carrying the
person's words, and the action row carries `withdrawn in conversation: …`.

---

## 9. What is default-off, and what this does not change

* **EXEC-02's activation is unchanged and still default-off.** This package adds **no new
  switch**. The conversational decision handler gates on the same
  `conversational_executive` activation EXEC-02 installs; a runtime without it never runs
  the recogniser, never reads a store, and behaves exactly as it did at `f99bf42`.
  `test_a_runtime_that_never_enabled_this_never_runs_the_recogniser` proves the recogniser
  is not even called.
* **It does not make Bartholomew autonomous.** Approving still requires a person to say so,
  in their own words, about a specific proposal they were shown.
* **It does not widen the capability domain.** The same nine kinds; three of them are now
  explicitly *not* reachable from this surface.
* **It does not add Executive cognition.** Not one line of `bartholomew/executive/` changed.
* **It does not open multi-device routing** (R-EXEC02-3), **redesign the goal recogniser**
  (R-EXEC02-1), or **change the prompt-injection surface** (R-EXEC02-4).

---

## 10. Evidence

### Deterministic and integration

| Suite | Tests | What it proves |
|---|---|---|
| `tests/test_conversational_approval_intents.py` | 104 | the decision seam: affirmatives are recognised, questions/quotations/hedges/embedded affirmatives are not, weak acknowledgements are excluded on purpose, the recogniser cannot steal a turn from another recogniser, and no renderer can claim completion |
| `tests/test_conversational_approval.py` | 49 | the vertical slice on the real chat turn, real dispatch table, real Executive, real envelope, real approval authority, real action store, real allowlists, real brake and real `MemoryStore` — binding, ownership, staleness, expiry, cancellation, one-shot, replay, brake precedence, surface eligibility, failure/UNKNOWN/verified rendering, and the audit separation |
| `tests/test_conversational_approval_no_bypass.py` | 11 | structurally, from the syntax trees: no chat-to-execution path, a pure recogniser, and the repository-wide set of approval-granting callers |

The only stand-in anywhere is the model, a fixed-payload `DeliberationPort` — the seam
EXEC-01 declared and the fixture shape EXEC-02's slice already uses.

**Regression.** `test_exec02_conversational_executive.py`, `test_exec02_goal_intents.py`,
`test_exec01_vertical_slice.py`, `test_windows_action_governance.py`,
`test_windows_action_recovery.py`, `test_windows_action_review_regressions.py`,
`test_w03b_no_bypass.py`, `test_chat_dispatch_table.py`, `test_runtime_contract_chat_seam.py`,
`test_objective_chat_seam.py`, `test_forecast_chat_seam.py`,
`test_privacy_guard_structural_scanning.py`: all green.

**Full default suite, locally:** 5,482 tests run, **2 failures, both pre-existing**
(`test_kernel_db_path_resolution.py`, two cases whose assertion compares a path against
terminal-wrapped CLI output under `-n 4`). Both reproduce **identically on a clean
`origin/main` worktree in the same configuration**, which is how they were established as
not this package's. `tests/smoke/test_packaging_contract.py` is deselected locally because
this container has no editable install; CI installs one.

**Two existing tests were amended, and neither was weakened.** Both asserted dispatch
ordering by fixed index. The property each exists for — task control keeps first refusal over
every other *instruction* recogniser, and the Executive goal entry stays last — is now
asserted directly, plus the new entry's position, which is strictly more specific than what
was there before.

### CI

**All three tiers green on both heads, run 2026-09-19.** Every job on the code head was verified
individually rather than read from the rollup, following the precedent §9 of
`docs/EXEC_01_GOAL_TO_PLAN_DELIBERATION.md` set.

| Head | What it is | PR Fast | Integration | Merge Candidate |
|---|---|---|---|---|
| `07c5af8` | the **code** head — every production and test change | 35433585629 green | 35433653500 **3/3** | 35433653573 **7/7** |
| `4e8799c` | **approved head**; documentation only (this table) | 35434749438 green | 35434749440 **3/3** | 35434749433 **7/7** |

A third head carries the merge-time documentation — this section, the header, `START_HERE.md` §5b,
`MASTER_PLAN.md`'s Approval Ledger and `RISKS.md`'s R-EXEC02-2 closure and new R-EXEC02-5. Its tier
results are in PR #117's own checks, and the **merge commit is the merge of PR #117**: the one
identifier this document cannot carry, because it does not exist until the merge happens.

Integration's three: Tests + coverage (Ubuntu py3.11, ≥70 % gate); Critical integration + lifecycle
(Ubuntu py3.11); **Windows lifecycle + compatibility (py3.11)**, including *Governed Windows
actuation (real Win32, nothing substituted)* and *(capabilities, governance, prohibitions)*.

Merge Candidate's seven: Quality (black, ruff, pre-commit, packaging contract, wave manifest);
Tests + coverage on **py3.10 and py3.11** under the ≥70 % gate; Critical integration + lifecycle on
both Pythons; smoke; and the **Windows full default suite + actuation (py3.11)**, 19m31s, with
real-Win32 governed actuation and no worker loss.

Two tiers were skipped on the first push because both are label-gated on a draft PR
(`ci:integration`, `ci:merge-candidate`). The labels were added and both ran on the same head; no
code changed between the skip and the run.

**The two local failures did not occur in CI.** `test_kernel_db_path_resolution.py` passed in every
tier, on Ubuntu and on Windows, which confirms the reading in the paragraph above: they are an
artefact of this container's terminal width under `-n 4`, not a property of the change.
`tests/smoke/test_packaging_contract.py`, deselected locally for want of an editable install,
passed in Quality, in Integration's Windows job and in Merge Candidate's Windows job.

### What remains unproven until real-world testing

Everything about whether this is *useful*. The automated evidence proves the implementation
contract: that a conversational decision reaches the one authority, binds to exactly the
proposal the person saw, and cannot be replayed, staled, widened or turned into a claim of
success. It proves nothing about real-model plan quality (**R-EXEC01-3 stands unchanged**),
nothing about Band 0, and nothing about whether saying "yes" in chat actually reduces the
person's burden. Both deferred Windows-PC items remain deferred, neither passed nor waived.

---

## 11. For the User Approval Gate

1. **The eligibility line (§4).** Three capability classes are refused conversationally on
   the reasoning that chat is a weaker approval surface than the console. A defensible
   judgement, made inside the package, reversible in one derived frozenset.
2. **The approver's name.** A conversational approval records the activation's configured
   `requested_by` as the approver — not a principal the platform verified, because a chat
   turn has none to offer. It is never synthesised and never "system", and the `surface`
   field makes the difference legible in the audit. Whether that is strong enough for a
   multi-user deployment is a question this single-user build does not have to answer, and
   it is recorded as a carried limitation rather than solved speculatively.

---

## 11a. Three defects found by review *after* the approval, and fixed before the merge

An automated Codex review ran when the PR was marked ready for review, after Taylor had approved
it, and found **three real defects — two P1, one P2**. Every one was a *false statement to the
person*, which is the single thing this package exists to prevent, so each was verified against
the code and fixed rather than carried. They are recorded here rather than quietly folded in,
because the merged diff is therefore not byte-for-byte what was approved.

None of them touches the authority model, the binding, the lifecycle or either judgement Taylor
ruled on. All three are corrections to what Bartholomew *says*.

**1. A failed re-arm left an older proposal answering for a newer one (P1).**
`present_proposal` returned early on each of its failure paths — two steps proposed at once, an
unreadable row, a `MemoryStore` write the consent gate refused — without touching the existing
record. So if proposal A was awaiting a decision and presenting proposal B failed, the slot still
pointed at A, and the person, having just been shown B, could say "yes" and authorise A. **That is
exactly the binding failure this document's own table claims to prevent** ("an old conversational
turn approving a newer proposal"), so the claim was false as written. Fixed by retiring the live
presentation to a new `superseded` state at the *top* of `present_proposal`, before any path that
can fail is reached: every exit except a successful arm now leaves the surface with nothing rather
than with the wrong thing. Pinned on two separate failure paths, so the fix is not tied to one
branch.

**2. "It has not run yet" was an assertion the approval result does not establish (P2).**
Approving makes an action eligible to be leased, and a device that is polling can lease and even
complete it between the authority's conditional UPDATE and the reply reaching the person — so the
sentence could already be false as it was displayed. The same class of untruth this surface exists
to refuse, pointing the other way. `render_approved` now says what is actually guaranteed:
the authorisation is recorded, *recording it is not running it*, the machine may pick it up at any
moment, and no claim of success will be made without an observed and verified effect. A test
forbids the renderer from asserting non-execution at all.

**3. Withdrawing an already-leased action promised something withdrawal cannot deliver (P1).**
`store.mark_cancelled` accepts a `leased` action deliberately, and *its own docstring* says doing
so "does not reach out and stop a device — nothing here can". `render_rejected` nonetheless said
the action "can never run", and the `cancelled` status sentence said it "was withdrawn before it
ran, so nothing happened" — false reassurance about something that may be executing on the
person's machine as they read it. Fixed by reading the pre-cancellation state (it is unreadable
afterwards, because the column is overwritten) and rendering the leased case separately: the
withdrawal holds and is worth having — the action can never be leased again and no result for it
will be recorded — but it did not *prevent* execution, and the reply says so and says to check the
machine. `withdrawn_after_lease` is carried on the chat record, so an audit can tell a withdrawal
that prevented an action from one that only disowned its result. The `cancelled` status sentence
no longer asserts either possibility, because the state column genuinely cannot tell them apart.

**Evidence after the fixes:** 172 tests across the three suites, all green; the EXEC-01/EXEC-02,
Governance, Windows action, no-bypass, chat-seam and privacy-guard regressions green; full default
suite 5,489 run with the same two pre-existing `test_kernel_db_path_resolution.py` failures and no
others. CI tiers re-run on the final head.

**What this says about the package's own review.** Three defects of the exact class the package is
built to prevent survived its author's adversarial pass and 164 tests, and were caught by a
reviewer reading the diff cold. Two of them contradicted claims made in this very document. That is
worth recording plainly: a test suite that asserts a property is not the same as a property
holding, and the renderer tests were checking for forbidden *completion* claims while missing
forbidden *non-completion* ones.

---

## 12. Residual risks carried forward

1. **R-EXEC02-1** (closed-list goal vocabulary) — unchanged. The decision vocabulary in
   `approval_intents.py` is a closed list with the same standing review obligation.
2. **R-EXEC02-3** (one device per deployment) — unchanged. The presentation slot is keyed on
   the three ownership dimensions this build has; a second device would need the recogniser
   and consent question that package did not open.
3. **R-EXEC02-4** (a wider prompt-injection exposure surface) — unchanged. No new path from
   memory to cognition, and specifically none to the decision: the recogniser reads the
   person's words and nothing else.
4. **R-EXEC01-3** (no real-model plan-quality evidence) — unchanged.
5. **The approver is a configured name, not a verified principal.** §11.2. A conversational
   approval is exactly as strong as the deployment's assumption that whoever is typing into
   chat is the person the activation names. True on a single-user local deployment; it is not
   a property this package establishes, and multi-user conversational approval would need it
   established first.
6. **Status reporting is bounded to the last decided proposal.** "Did it work?" reports on
   the one proposal this conversation last presented. It is not a general action-history
   surface, and the operator console remains the place to review anything else.
