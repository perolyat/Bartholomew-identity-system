# BARTHOLOMEW W03-B HANDOFF — Executive Task Orchestration

| | |
|---|---|
| Session | **W03-B — Executive Task Orchestration** |
| Immutable id | `W03-B` |
| Branch | `wave/w03-b-executive-task-orchestration` |
| Pull request | `[W03-B] Executive Task Orchestration` — **#96** |
| Baseline | `main @ e96e6a6dfc3f71a68d44010c9954bb4e7a0c5195` (W03-PREP closeout; carries `99ee734`) |
| Consumed frozen dependency | W03-A `56bf995` (PR #93) — **by published signature, not by merge** (§5.1) |
| Frozen head | the PR head carrying this handoff (recorded on the PR) |
| Required CI tier | Integration |
| Status | **frozen** — head declared final for W03-F integration; nothing further will be pushed to this branch without telling W03-F |

The manifest (`W03_MANIFEST.yaml`) still reads `status: ready_to_start` for
W03-B. `docs/waves/W03` is owned by W03-PREP, so W03-B edited nothing in it
except this handoff, whose home this directory is (`README.md`); the status flip
to `frozen` is W03-PREP's / W03-F's one-line change.

---

## 1. What W03-B built

The baseline had no executive that could act on the PC. `Planner.decide()`
returned `None`; intent recognition was single-shot regex skill routing; and —
decisively — **nothing in the kernel imported the actuation seam**, so a Windows
action could be created only by an external HTTP POST, never by cognition
(`W03_PREP_ASSESSMENT.md` §2 blocker 1).

W03-B builds that path: `bartholomew/executive/`, a new package that turns an
ordinary instruction into a governed plan whose every Windows step is an
action-envelope proposal, drives it through Interpret → Decide → propose →
observe → verify → continue-or-recover → explain, and is **the only** new route
from cognition to a machine.

### 1.1 Ten modules, and the boundary each one holds

| module | what it is | the rule it holds |
|---|---|---|
| `intent.py` | pure recogniser: instruction → capability steps, or a question | The vocabulary is closed (every step names a `CapabilityKind`); ambiguity becomes a question, never the most likely reading; out-of-vocabulary requests are **declined truthfully**, never mapped onto the nearest capability. |
| `selection.py` | capability ↔ device enrolment | A capability the device does not declare is refused, not substituted. A version this build does not implement is refused, not downgraded. No device ≠ permissive. |
| `evidence.py` | recalled memory as evidence | **Evidence may narrow a plan or explain one; it may never widen, select, fill, or authorize.** The `EvidenceRecord` type cannot carry a capability, device, scope or approval at all. |
| `plan.py` | the plan and the pacing rule | `next_actionable_step()` is the single function that says what is next, and it returns a step only when every earlier step is `VERIFIED`. |
| `verification.py` | the Verify leg's consumer | A device report of `succeeded` with nothing read back is `unknown`, and `unknown` stays `unknown`. Consumes W03-A's `read_back` by its published signature. |
| `recovery.py` | what to do when a step did not verify | Four decisions, and **none of them is "retry"**. An `unknown` never re-proposes. An `always`-approval capability never re-proposes itself. |
| `explanation.py` | the account a person reads | Proposed / authorized / executed / verified are four different things and are never collapsed. Sourced from the shared `ActionReflection` sink. |
| `store.py` | durable task state (`executive_tasks`, `executive_task_steps`) | Not Memory, and not an authority: whether an action may run is `windows_action_requests`, W03-C's table, and this one records the `action_id` and reads the truth from there. |
| `seam.py` | `run_executive_task_through_runtime_contract` / `advance_…` | The one place the action envelope is reached. It proposes; it never approves. |
| `__init__.py` | the package's published surface | — |

### 1.2 The envelope is the only door

`bartholomew/executive/` imports exactly one actuation module that can do
anything — `bartholomew.actuation.seam` — plus three of its value types
(`store`, `capabilities`, `parameters`/`result` constants). There is no import
of `bartholomew.windows_actuation`, no `subprocess`, no `ctypes`, no input
synthesis library, no socket and no HTTP client anywhere under the package.

`tests/test_w03b_no_bypass.py` proves that by reading the package's own syntax
tree — including function-body imports and the string arguments of
`importlib.import_module` — and it proves the guard is not vacuous by running
the same machinery over modules that *do* contain a bypass.

It also proves one thing the contract implies rather than states: **the
executive never calls `grant_action_approval`.** The contract lists that
function as consumable; W03-B deliberately does not consume it, because the
non-goals are explicit that "an approval is requested through the host
boundary, never minted by the executive". A proposal stops at
`pending_approval`. This is a narrowing of the permitted surface, not an
expansion; the integration suite calls the approval seam itself, in the role of
the host boundary, to prove the rest of the loop.

### 1.3 Governance: nothing new, and no `Identity.yaml` change

The executive's own gate is the *existing* Parking Brake, read fail-closed
through `engaged_state_fail_closed_off_loop` — the composed "engaged at all"
helper the actuation seam, objective mutation, consent resolution and inbound
capture already use. Any engagement (global, `skills`, `actuation`, `sight`)
stops the executive, and an unreadable brake stops it too.

Its two kinds — `executive_task`, `executive_task_advance` — are **cognition**:
one plans, one reads a result, and neither can reach a machine. They are exempt
from `tool_use.allowlist` on exactly the recorded precedent
`runtime_contract._CONVERSATIONAL_KINDS` sets (that allowlist's grain is "a
skill_id or scheduler drive task_id"). **No entry was added to `Identity.yaml`
by this session, and none is needed**: every action the plan proposes is
evaluated for real by the envelope on `windows_action_request`, which is
already allowlisted.

That exemption is proved to grant nothing:
`test_removing_the_request_kind_from_the_allowlist_refuses_the_proposal` shows
planning still refuses when the envelope's kind is withdrawn, and
`test_a_permissive_identity_still_cannot_make_a_proposal_execute` shows a fully
permissive Identity — `windows_action_dispatch` allowlisted included — still
cannot make an executive-generated action dispatch.

### 1.4 What "informs but does not authorize" means operationally

Evidence reaches the plan as `notes` (narrative, read by the explanation) and
`cautions` (a prior failure, read by `recovery.py` and able only to turn a
re-proposal into a question). It reaches a prompt inside one delimited,
explicitly non-instructional frame with a preamble that states the boundary in
the same place as the material.

The safety argument is structural rather than a filter: the fields a plan is
built from are never read from an evidence row, so a poisoned corpus and a
benign one produce **the same plan**. `tests/test_w03b_evidence.py` asserts
exactly that by substitution — including rows that carry `capability`,
`device_id`, `approved` and `scope` keys outright — and separately asserts the
corpus *is* read, so the equality is a real constraint and not an artifact of
everything being discarded.

The retrieval-side validity verdict is fail-closed: only `currently_valid`
admits, and a row arriving with **no** verdict is refused exactly as a revoked
one is. That is what lets W03-B consume W03-D's contract before W03-D's
implementation lands: on a tree without it, nothing is admitted, and nothing is
admitted is the safe answer.

---

## 2. Acceptance criteria — results

| # | Criterion | Result | Proven by |
|---|---|---|---|
| 1 | Every Windows step is an action-envelope `ActionRequest`, and it **cannot execute without authorization through the defined host boundary and a clear Parking Brake** | **met** | `test_w03b_executive_seam.py::TestOneProposalTravelsTheEnvelope` (row is `pending_approval`, `lease_count == 0`, dispatch refused `APPROVAL_MISSING`); `TestGovernanceStopsTheExecutive` (engaged brake → nothing planned, nothing written; replay under differing brake state); `test_w03b_integration.py::TestTheWholeLoop` (refused → approved at the host boundary → leased) |
| 2 | No bypass: an import/AST test proves the package reaches actuation only through `bartholomew/actuation/seam.py` | **met** | `test_w03b_no_bypass.py` (11 tests: forbidden imports incl. dynamic, permitted-module allowlist, OS-call scan, no socket/HTTP, no `grant_action_approval`, and two non-vacuity checks) |
| 3 | Materially ambiguous interpretation raises an `awaiting_response` clarification, not an action | **met** | `test_w03b_interpretation.py::TestAmbiguityBecomesAQuestion` ("open it" and every bare referent; an article or a multi-word target asks); `test_w03b_executive_seam.py::TestAmbiguityRaisesAQuestionNotAnAction` (zero action rows; the understood half of a half-understood instruction is not proposed either) |
| 4 | Multi-step advances only after the prior step is observed **and verified**; a failed or `unknown` step triggers a defined recovery decision, not a blind retry | **met** | `test_w03b_progression.py::TestThePacingRule` (no status but `VERIFIED` releases the next step); `TestRecoveryIsAlwaysADecision` (no decision value contains "retry"; `unknown` asks; `always`-approval stops); `test_w03b_executive_seam.py::TestTheLoopClosesInProcess`; `test_w03b_integration.py::TestRecoveryNeedsFreshAuthorization` (a re-proposal is a new action the earlier approval cannot authorize) |
| 5 | Every attempt produces a human-readable explanation of proposed / authorized / executed / verified, sourced from `ActionReflection`, with no fabricated success | **met** | `test_w03b_progression.py::TestTheExplanationNeverFabricatesSuccess`; `test_w03b_executive_seam.py::TestTheExplanationIsSourcedFromTheAuditTrail` (the account is read back out of the shared `reflections` sink and includes an approval granted elsewhere) |
| 6 | Recalled memory informs the plan but never authorizes it | **met** | `test_w03b_evidence.py` (28 tests: poisoned ≡ benign ≡ no evidence by substitution; per-row parametrised; verdict fail-closed; revoked cannot be resurrected; the frame) ; `test_w03b_integration.py::TestPoisonedMemoryChangesNothingEndToEnd` (through the real envelope and real stores) |

### Test contracts (`W03_TEST_CONTRACTS.md`) discharged on W03-B's side

- **§1 governed actions.** No-bypass AST test in PR Fast. Replay under differing
  Parking Brake state decided by policy state, not wording
  (`test_the_same_intention_is_refused_by_policy_state_not_by_wording`).
  Approval binding across a re-proposal (Integration).
- **§2 memory poisoning.** The seeded-recall suite above, structural half in PR
  Fast, end-to-end half in Integration. The instruction/data frame is a module
  constant so a test can assert the rendered text is the framed one.
- **§4 ambiguous inference — behaviour half.** Ambiguity → `awaiting_response`;
  weak inference is never acted on because *no* inference is: the executive acts
  on an explicit user task (deferral 5).
- **§5 action verification — continue/recover half.** Issued ≠ succeeded, end to
  end; failure distinct from `unknown`; a read-back that contradicts a success
  report wins.

---

## 3. Tests and CI

New suites:

| file | tier | tests |
|---|---|---|
| `tests/test_w03b_no_bypass.py` | PR Fast | 11 |
| `tests/test_w03b_interpretation.py` | PR Fast | 38 |
| `tests/test_w03b_evidence.py` | PR Fast | 28 |
| `tests/test_w03b_progression.py` | PR Fast | 30 |
| `tests/test_w03b_executive_seam.py` | PR Fast | 32 |
| `tests/test_w03b_integration.py` (`@integration`) | Integration `critical` | 11 |

Verification run locally on the frozen head, per `W03_CI_BASELINE.md`:

| check | result |
|---|---|
| `pre-commit run --all-files` (black, ruff, hygiene hooks) | pass |
| `tests/smoke/test_packaging_contract.py` + `tests/test_wave_manifest.py` | pass (14 tests) |
| default suite, `-n auto --dist loadfile` | pass — **4285 passed, 2 skipped**, 4 min 28 s (baseline before W03-B: 4151) |
| default suite with coverage, `--cov-fail-under=70` (Integration `tests-coverage`) | pass — 4285 passed; **line coverage 79.26 %** (gate 70 %); `bartholomew/executive/` at **86 %** under its own suites |
| `-m "integration or slow"` (Integration `critical`) | pass — **141 passed, 21 skipped**, 10 min 31 s, serial |
| clean-start lifecycle + scheduler readiness + parking-brake governance | pass — 33 passed |

Nothing was skipped, quarantined or deleted. The two
`test_declared_console_script_runs_help` cases fail in this container only when
the venv's `bin` is not on `PATH` — the local editable-install artifact
`W03_CI_BASELINE.md` §6 already records; with `PATH` set they pass (14/14).

GitHub Actions on the PR: PR Fast on every push; the Integration tier under the
`ci:integration` label on the draft. The head is declared frozen only once both
are green; the results are on the PR's checks tab and in W03-B's closing status.

---

## 4. Files

Owned by W03-B (manifest `owns: bartholomew/executive`):

- `bartholomew/executive/__init__.py`, `intent.py`, `selection.py`,
  `evidence.py`, `plan.py`, `verification.py`, `recovery.py`,
  `explanation.py`, `store.py`, `seam.py` — all **new**.
- tests: `tests/test_w03b_no_bypass.py`, `test_w03b_interpretation.py`,
  `test_w03b_evidence.py`, `test_w03b_progression.py`,
  `test_w03b_executive_seam.py`, `test_w03b_integration.py` — all new.
- `docs/waves/W03/BARTHOLOMEW_W03_B_HANDOFF.md` — this file.

**Nothing else was modified.** No change to `bartholomew/actuation/**`,
`bartholomew/windows_actuation/**`, `bartholomew/multimodal/**`,
`bartholomew/kernel/**` (`runtime_contract.py` included), `Identity.yaml`,
`app.py`, `route_policy`, `pyproject.toml`, `.github/workflows/**`, the manifest
or any other session's files. The new package needed no per-file ruff ignore:
its two deferred imports carry an inline `# noqa: PLC0415` in the repository's
existing style, and `find_packages()` picks the package up with no packaging
change.

---

## 5. Residual risks and integration notes (for W03-F, W03-C, W03-D, W03-E)

### 5.1 The dependency mechanism, and why W03-A is not merged in here

The manifest's `build_on` is "each W03 builder branches from `main` (>= 99ee734)",
W03-B's `requires_frozen` is empty, and its contract says it "can start
immediately against the **published contract signatures** … it does not need
those heads frozen to start, only stable signatures". W03-B therefore branches
from `main @ e96e6a6` and **consumes W03-A's frozen head `56bf995` by
signature, not by merge**: nothing from W03-A's diff is in this branch, so
W03-F's integration order (D, A, C, B, E) is unchanged and this PR carries only
W03-B's own files.

Concretely, `verification.resolve_read_back_port()` resolves
`bartholomew.multimodal.readback.read_back` **by name at call time**. On this
branch the module is absent and verification degrades to an honest `unknown`;
on the integrated head it resolves and verification works. The published
keyword-only signature is pinned by a test double
(`test_the_port_is_called_with_w03_a_s_published_signature`), so a drift on
either side surfaces as a failure rather than as silence. **W03-F should expect
`bartholomew/executive/verification.py` to start verifying for real the moment
W03-A is in the tree, with no code change required.**

### 5.2 There is deliberately no production caller yet

`bartholomew/executive/seam.py` is a seam, and W03-B's ownership stops at the
package: `app.py` router registration is W03-F's, the operator surface is
W03-E's, and `runtime_contract.py` is a shared hotspot the contract restricts to
"additive new-module code only". So the executive is reachable in-process and by
test, and nothing in the running product calls it yet. **Wiring it is W03-E's
(operator surface) and W03-F's (install/seams) step**, and it is one call:
`run_executive_task_through_runtime_contract(ctx, tenant_id=…, device_id=…,
requested_by=…, instruction=…)` followed by
`advance_executive_task_through_runtime_contract(ctx, tenant_id=…, task_id=…)`
whenever an action's state moves. `executive_store.find_task_for_action()` maps
a result callback back to the task that proposed it.

### 5.3 The advance pass is caller-driven, not a loop

Nothing in this package polls. `advance_…` is called by whoever notices that an
action's state changed — a route, a drive, an operator action. That is
deliberate: a self-driving loop inside the executive would be the beginning of
the proactive autonomy deferral 5 defers, and it would also need its own brake
re-read cadence. If W03-E or W03-F wants an automatic advance, the right shape
is a scheduler drive with its own allowlist entry, proposed through W03-PREP.

### 5.4 W03-D's validity verdict is consumed, not yet produced

`evidence.admit_evidence()` reads `validity` (or `verdict`) off each recalled
row and admits only `currently_valid`. Until W03-D's retrieval-side governance
lands, an ordinary `memories` row carries no such field and is therefore
**refused**. That is the fail-closed direction, but it means that on the
integrated head **W03-D's retrieval path must stamp the verdict for evidence to
reach a plan at all**. The `cautionary` flag is likewise W03-B's own read of a
row the owning store marks; if W03-D names that field differently, the one-line
adapter belongs in `evidence.evidence_from_row()`.

### 5.5 Verification is capability-shaped and modest

`verification._expectation()` defines a read-back check for
`focus_window`, `launch_app`, `manage_window`, `type_text` and
`accessibility_action`. `open_url` and `open_path` have **no** defined check and
therefore report `unknown` even on a successful read-back — which is the honest
answer (nothing in the accessibility tree distinguishes "the browser opened
this URL" from "a browser is open") and matches the envelope's own
`effect_unverifiable` posture for them. Widening it needs a real signal, not a
looser assertion; a check that could not fail would manufacture verifications.

### 5.6 The recogniser is scaffolding, and says so

`intent.py`'s pattern set is a POC recogniser, not the boundary of what
Bartholomew understands — the same status `task_intents.py` records for itself.
The **boundary** is the capability vocabulary and the device's enrolment, which
are enforced in `selection.py` and again by the envelope. Broadening the
patterns is expected work and changes no governance property. Two behaviours
are deliberately conservative and will read as friction: "open my notepad" asks
(an article names a *thing*, not an allowlist key), and an instruction that is
only half understood proposes nothing at all.

### 5.7 One new table pair in the kernel database

`executive_tasks` and `executive_task_steps`, created idempotently by
`executive_store.ensure_schema()` from the seam, in the same style as
`actuation/store.py`. W03-F's `install.py` may prefer to call `ensure_schema`
at start-up alongside the others; the seam calling it is safe either way.

### 5.8 Live desktop behaviour is not established here

Every test runs against controlled providers and a doubled device. The live
`intention → proposal → approval → dispatch → real Windows result → real
read-back → verified` run on a real desktop is W03-F's real-world-test target,
as the contract records. The read-back half of that depends on W03-A's live
capture session, which W03-A also records as not live-verified (its §5.6).

---

## 6. Escalation boundary — nothing hit

The contract's three stop-and-report conditions were not reached:

- No LLM emitting tool calls was needed. Interpretation is deterministic, and
  the only route to the OS is the envelope, so a model could not reach one even
  if a later revision adds one to interpretation.
- No autonomous retry loop was needed. `recovery.py` has no retry decision, and
  a `sensitive`/`always`-approval capability never re-proposes itself.
- No change to the `action-envelope` or `observation-event` signatures was
  needed. Both were consumed exactly as published.

No standing action permission was added to `Identity.yaml` (§1.3), and W03-B
proposes only on an explicit user task (deferral 5).

---

## 7. Statement

W03-B is complete within its published boundary. Cognition can now propose a
Windows action, and it can do so in exactly one way: through the canonical
envelope, stopping at `pending_approval`, behind a fail-closed Parking Brake,
with no route to the operating system that a syntax-tree check cannot see. An
ambiguous instruction becomes a question rather than an action; a plan advances
only on a verification, never on a device's own claim of success; a step that
did not verify gets a named decision rather than a repeat; recalled memory can
make the executive more careful and more explanatory and cannot make it do
more; and every attempt ends in an account that says what was proposed, what was
authorized, what executed and what was verified, without calling an `unknown` a
success.

The head is frozen for W03-F. W03-E and W03-F can build against
`run_executive_task_through_runtime_contract` and
`advance_executive_task_through_runtime_contract`.
