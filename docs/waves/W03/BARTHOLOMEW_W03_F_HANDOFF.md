# BARTHOLOMEW W03-F HANDOFF — Integration & Real-World Test Candidate

| | |
|---|---|
| Session | **W03-F — Integration & Real-World Test Candidate** |
| Immutable id | `W03-F` |
| Branch (contract name) | `wave/w03-f-integration-real-world-test` |
| Branch (pushed) | `claude/w03-f-final-integration-ugqc2n` — the divergence W03-PREP and W03-D each record for themselves; `tests/test_wave_manifest.py` enforces the prescribed name in the manifest's `branch` field, so the pushed name is recorded in `pushed_branch` |
| Pull request | `[W03-F] Integration & Real-World Test Candidate` — **#99** |
| Baseline | `main @ e96e6a6dfc3f71a68d44010c9954bb4e7a0c5195` |
| Required CI tier | Merge Candidate |
| Status | **frozen — awaiting the User Approval Gate.** Not merged. Auto-merge not enabled. |

---

## 1. The finding that shaped this session

The session brief recorded that W03-E had *"already exercised a composed A–D + E
integration surface"*, and warned W03-F not to duplicate integration work merely
because an older planning document describes F as combining A–E individually.

**Git says otherwise, and the brief itself makes git authoritative.**

```
$ git merge-base --is-ancestor 56bf995c origin/wave/w03-e-windows-golden-path   # W03-A → NO
$ git merge-base --is-ancestor fead4b39 origin/wave/w03-e-windows-golden-path   # W03-B → NO
$ git merge-base --is-ancestor e904cd18 origin/wave/w03-e-windows-golden-path   # W03-C → NO
$ git merge-base --is-ancestor 111c72d3 origin/wave/w03-e-windows-golden-path   # W03-D → NO

$ git rev-list --count origin/main..origin/wave/w03-e-windows-golden-path        # 3
```

All five W03 branches branch **directly** from `origin/main @ e96e6a6` and carry
two or three commits each. W03-E's own handoff says so in its header: *"Consumed
frozen dependencies … **by published surface, not by merge**"*, and its §3.2
records that the A–E composition it exercised lived in a **disposable worktree at
`/home/user/w03-compose` from which nothing was committed**. That worktree no
longer exists and was never under version control.

So the claim was true as a statement about what W03-E had *run*, and false as a
statement about what any branch *contained*. **This head is the first tree in
which the five W03 packages have ever existed together under version control.**
Five merges were required, not one.

---

## 2. Integration matrix

Common base for all five: `origin/main @ e96e6a6`. No builder contains any other.

| # | Session | Frozen SHA | PR | In W03-E? | Merge | Migrations | Shared-hotspot edits |
|---|---|---|---|---|---|---|---|
| 1 | W03-D Governed Memory & Learning | `111c72d32964ae705c0e2b46db441256bf829328` | #94 | no | clean | `memories` provenance / validity / supersession / tombstones | `route_policy.py`, `W03_MANIFEST.yaml`, `runtime_contract.py`, `INTERFACES.md` |
| 2 | W03-A Live Windows Perception | `56bf995c92585f37b444483cb32f96c3c6460397` | #93 | no | clean | multimodal session/observation store | none |
| 3 | W03-C Governed Windows Action | `e904cd18a3bcb416ff0e3e71455f5826eb927587` | #95 | no | clean | `actuation/store.py` `ensure_schema` | `route_policy.py`, `W03_MANIFEST.yaml`, `cli.py`, `cli_trust.py` |
| 4 | W03-B Executive Task Orchestration | `fead4b392b75f66cb1727f3da2790fe4ff0f86e2` | #96 | no | clean | `executive_tasks`, `executive_task_steps` | none |
| 5 | W03-E Windows Golden Path | `e19776e2d267abc83c928666c909973684b85928` | #97 | — | clean | none | `route_policy.py`, `cli.py`, `test_s8_route_policy_coverage.py` |

Merged in the order `W03_F_CONTRACT.md` prescribes (D → A → C → B → E), verifying
after each addition. **Zero textual conflicts**, including all three shared
hotspots: `route_policy.py` was edited by C, D and E in three different regions of
the same dict; `cli.py` by C and E in different regions; `W03_MANIFEST.yaml` by C
and D in different session blocks.

Textual cleanliness proved nothing about semantic compatibility, which is §3.

---

## 3. What W03-F actually integrated: the semantic seams

Each repair is traceable to a builder handoff that named it as W03-F's, and to
nothing else. No new capability, memory field, executive behaviour, Golden Path,
event bus, authority or standing permission was added.

### 3.1 `app.py` registers `routes/operator.py`

*Named by: W03-E §1.2 and §3.3, W03-B §5.2, `W03_MANIFEST.yaml` (W03-F owns
`app.py` router registration).*

W03-E built the operator routes and deliberately left the registration line to
integration. Before this commit `bartholomew operator task run` reached a 404 on
every head that has ever existed, so the executive (W03-B) had **no production
caller at all**.

The routes were already classified in `route_policy.py` — W03-E pre-classified
them precisely so that this registration could not open an unclassified surface
into default-deny. Registering them is what makes that classification
load-bearing rather than dormant, so the four coverage exemptions W03-E left in
`tests/test_s8_route_policy_coverage.py` are removed in the same change, exactly
as its comment instructed.

**Files:** `bartholomew_api_bridge_v0_1/services/api/app.py`,
`tests/test_s8_route_policy_coverage.py`.
**Tests:** `test_the_operator_routes_are_registered_and_reachable`,
`test_the_operator_console_reaches_the_real_executive`,
`test_every_route_the_composed_app_serves_is_classified`, and the existing
`test_s8_route_policy_coverage.py` (8 passed).

### 3.2 `bartholomew/integration/seams.py` — the read-back adapter

*Named by: W03-C §7.2 ("W03-F should wire `install_read_back_provider()`"),
W03-E §3.3. `bartholomew/integration/seams.py` is W03-F's by the contract's
Ownership section.*

W03-C built the socket and forbade itself the plug: `bartholomew/actuation/` must
not import `bartholomew/multimodal/`, and `multimodal/` does not know the envelope
exists. Until this, `verify_effect()` answered `UNVERIFIABLE` for every action on
every branch regardless of what a device reported.

The two shapes genuinely differ, and the resolution of each is recorded in the
module's own docstring:

* **`target`.** W03-C's caller passes `stored.capability` — a *capability name*
  such as `windows.type_text`. W03-A's `target` is a `CaptureScope`: a display, a
  window handle or a rectangle. Coercing one into the other would invent a scope
  nobody consented to, so the adapter passes `target=None`, W03-A's documented
  "whatever the live session is consented to observe". The read is therefore
  bounded by consent that already exists and **cannot widen it**.
* **`requested_by`.** W03-C's protocol does not carry the action's principal, so
  the adapter passes `None`. See §7.1 — this is a declared limitation, not a
  silent one.
* **Return type.** `ReadBackResult` → `ReadBack`, with the text reduced to a
  digest inside W03-C's own `ReadBack.from_text()`, so no observed screen text is
  held by this module or reaches an evidence row.

The one rule the adapter enforces itself: an unavailable or empty read is
returned as unavailable, never translated into a comparison. It can therefore
manufacture neither a confirmation nor a contradiction.

`install_w03_seams()` is called from `install_seams()` so composition still has
one entry point and one report; `SeamReport` gains a `w03_seams` key so the health
surface describes the composed reality rather than under-reporting.

**Files:** `bartholomew/integration/seams.py` (new),
`bartholomew/integration/install.py`.
**Tests:** `test_the_read_back_provider_is_actually_installed_by_installing_the_seams`,
`test_the_w03f_adapter_never_manufactures_a_comparison`,
`test_the_adapter_cannot_widen_a_consented_scope`,
`test_a_succeeded_report_with_no_read_back_is_not_a_confirmation`,
`test_installing_the_seams_reports_the_composed_reality`.

### 3.3 Arming reads *any* brake engagement

*Named by: W03-C §8, explicitly "a three-line fix; W03-F's call, and best made
where the whole integrated brake story is being verified".*

`routes/actions.py::_arm_brake_engaged()` read `is_blocked("actuation")` — a
*scoped* check — while `seam.evaluate_actuation_brake()`, which gates every
dispatch, denies on **any** engagement. The function's own name and docstring
said "any brake scope"; the implementation did not.

Measured divergence on the composed head:

```
brake engaged with scopes=['voice']
  is_blocked('actuation')  -> False     # the OLD read
  state().engaged          -> True      # the seam's read, and the NEW one
```

So with only `voice` engaged, `GET /api/actions/channel` reported `armed: true`
during a halt under which nothing could actually run. It was never a hole —
arming authorises nothing and dispatch still refuses fail-closed — but it is
precisely the misleading-safety-signal class the wave exists to remove, and it is
**composition that made it reach a person**: W03-E's operator overview renders
this channel state on the console, so before the wave was composed the
disagreement was invisible and afterwards it is on screen.

**Files:** `bartholomew_api_bridge_v0_1/services/api/routes/actions.py`.
**Tests:** `test_an_engaged_brake_is_reported_truthfully_on_the_channel_read`,
`test_arming_is_refused_while_any_brake_scope_is_engaged`.

### 3.4 The lease handler's SQLite calls move off the event loop

*Named by: W03-C §8.*

`routes/device_actions.py::lease_actions()` called `store.expire_overdue()` and
`store.dispatchable_action_ids()` synchronously inside an `async def`. They were
the only direct persistence calls left in that file — every other one goes
through an `await seam.*` that runs its own work off-loop — and under lease load
they blocked the loop that also serves the brake read and the abort check, which
is the one thing that must stay responsive while a device is acting.

`routes/actions.py:565` already expires overdue actions through `run_off_loop`;
this is that established pattern applied to the sibling channel. This discharges
the "no blocking I/O regressions in asynchronous runtime paths" invariant on the
composed head.

**Files:** `bartholomew_api_bridge_v0_1/services/api/routes/device_actions.py`.
**Tests:** the W03-C actuation channel suites (111 passed) plus
`tests/integration/test_windows_action_http.py`.

### 3.5 One W03-B test corrected — and tightened

*Integration-only test correction, which `W03_F_CONTRACT.md` permits where "tests
accurately expose a real seam".*

`test_a_device_saying_succeeded_with_no_read_back_is_unknown` was the **only**
test in the entire default suite that failed on the composed head, and it failed
correctly. Its comment reads *"No port was injected and none is resolvable in a
tree without W03-A"* — a precondition that is false the moment W03-A is in the
tree. The port now resolves, the real read-back runs, and it answers
`no_consented_session`, so `source` is `read_back` rather than `absent`.

The composed answer is **more** honest, not less: "we looked and there is no
consented session" rather than "we have nothing to look with". The assertion was
therefore **tightened rather than relaxed** — the verdict must be `UNKNOWN` and
`VERIFIED` is no longer accepted, because with no consented session nothing can be
observed. The hedge W03-B needed while it could not know what the integrated tree
would carry is exactly what composition resolves.

**Files:** `tests/test_w03b_progression.py` (30 passed).

### 3.6 Manifest

A–E flipped to `integrated` with their frozen SHAs and PR numbers; W03-F set to
`in_progress` (not `complete`: the wave is not finished until the user approves
the merge and the real-world Windows acceptance test is run) with `pushed_branch`
recorded.

---

## 4. Verification

### 4.1 The W03-F integration suite

`tests/integration/test_w03_integration.py` — new, W03-F-owned, **35 tests**,
discharging acceptance criterion 2 ("every governance invariant asserted by a
test in this file"). Where a property is reachable from outside the process it is
asserted **through a running `bartholomew serve`**, not by calling an internal
function.

| Invariant | How it is asserted |
|---|---|
| One action envelope | AST: nothing outside `actuation/seam.py` defines any of its seven callables |
| Executive has no OS path outside the envelope | AST over every file in `bartholomew/executive/`: no `subprocess`/`ctypes`/win32/`pyautogui` import, and no import of `windows_actuation` |
| One governance authority; no second brake store | source scan of all five packages |
| Operator surface is not an authority | `routes/operator.py` contains no `grant_action_approval`/`arming.arm(` |
| Parking Brake over the whole path | running server: no lease under a halt; arming refused under any engaged scope; channel read truthful |
| Stop-after-lease reachable | `POST /api/device-actions/abort-check` served and classified on the composed head |
| Identity / capability / approval | undeclared capability refused; unapproved action never leased; unauthenticated lease refused |
| Truthfulness across the A/C boundary | `succeeded` with no read-back stays `UNVERIFIABLE`; a raising provider stays `UNVERIFIABLE`; adapter manufactures no comparison |
| B↔D verdict pairing | asserted against **both** packages' real constants, not a literal on one side |
| B↔A read-back signature | `inspect.signature` on the resolved port; every parameter keyword-only |
| Schema composition | all three `ensure_schema`s run twice in both orders on one database; resulting schema must be identical |
| Memory is evidence, never authority | action-shaped keys contribute nothing; unverdicted rows refused |
| Learning stays manual, policy stays shadow | no `accept_candidate`/`auto_accept` in any W03 package |
| Observation ≠ inference | `ObservationEvent` retains `observed_event`, `inferred_state`, `confidence`, `competing_explanations` |
| Every served route public or classified | walks the routers `app.py` actually registers |
| Deferral #16: channel inert by default | no resolver without the explicit env gate; `install_seams()` leaves it closed |
| Deferral #7: named stops stay stops | `move_file`/`delete_file`/`run_shell`/`run_powershell`/`install` absent from the vocabulary |

### 4.2 Measured results on the composed head

| Suite | Result |
|---|---|
| **Full default suite** (`pytest`, i.e. `-m 'not integration and not slow'`, xdist) | **4627 passed, 2 skipped, 0 failed** |
| Golden Path (`tests/golden_path`, all markers) | **80 passed, 0 skipped, 0 failed** |
| W03-F integration suite | **35 passed** |
| W03-A/B/D package suites + consent red-team + memory agency | **355 passed** |
| Windows actuation + brake + route policy + manifest + W03-B progression | **286 passed** |
| Actuation channel suites incl. HTTP integration | **111 passed** |

The default-suite number is 4627 against W03-E's 4187 at its own freeze: the
440-test increase is the other four packages' suites arriving in the same tree,
plus W03-F's own. The two skips are the pre-existing ones main already carries,
not new ones — no test was skipped, quarantined or deleted to obtain a green run.

The Golden Path number is the load-bearing one. On W03-E's branch alone it was
**73 passed / 6 skipped** — the six skips being named stops for packages that
were not in the tree. On this head it is **80 passed / 0 skipped**: no named stop
fires for a missing package, because every leg is present. That matches W03-E's
own disposable-worktree prediction (§3.2) exactly, and it is the first time the
result has been reproducible from version control.

---

## 5. What this does **not** establish

**No Windows hardware was involved in any result in this document.** Separated
deliberately:

| Class | What it covers here |
|---|---|
| **Automated evidence** | Everything in §4. Real server, real sockets, real SQLite, real governance authorities, real envelope, real device channel over real HTTP, real console subprocess. |
| **Simulated / platform-independent** | The device's *behaviour*: `Device.report()` reports what a test chose to report, because no Win32 handler runs. The envelope, approval binding, brake and verdict vocabulary are exercised for real; the Windows effect is not. |
| **Actual Windows hardware evidence** | **None.** No real pid, no real window, no real foreground change, no real read-back of a real screen. |
| **Not yet run** | The Wave 3 real-world acceptance test: live-desktop Golden Paths 1 and 4, a real arm from the console with a real keyring credential, and a live `intention → proposal → approval → dispatch → real Windows result → real read-back → verified` loop. |

The project workflow places the formal real-world acceptance test **after** this
candidate is approved and merged. Automated integration success is not permission
to claim Bartholomew is usable on the user's real Windows machine.

---

## 6. Named stops and limitations that remain intentionally unresolved

Preserved, not fixed. None is an integration defect.

| # | Stop | Owner / register |
|---|---|---|
| 1 | No file-move capability. Golden Path 3 runs into its absence and reports `unknown` honestly via `open_path`. | W03-C vocabulary; deferral #7 |
| 2 | **The stop is cooperative.** A wedged or killed companion is not stopped by the abort check. The out-of-process emergency stop (D11/S9) remains owed. W03-F does **not** represent the abort work as satisfying it. | deferral #8, W03-C §7.1 |
| 3 | No arbitrary shell, PowerShell, Python or executable dispatch; no destructive action, install, purchase, account change or message send. | deferral #7 |
| 4 | Automatic lesson acceptance stays off; `execution_mode == shadow`. Enabling is a director decision. | deferral #4 |
| 5 | `open_url` / `open_path` have no defined read-back check and report `unknown` even on a successful read. Widening needs a real signal, not a looser assertion. | W03-B §5.5 |
| 6 | The intent recogniser is a POC pattern set. The *boundary* is the capability vocabulary and enrolment, enforced in `selection.py` and again by the envelope. | W03-B §5.6 |
| 7 | The advance pass is caller-driven; nothing polls. An automatic advance would be the start of the proactive-autonomy deferral. | W03-B §5.3, deferral #5 |
| 8 | No autonomous capture-start; every observation session is explicitly consented. | deferral #6 |
| 9 | Off Windows, `idle_seconds` is `None` and no presence inference is made. | W03-A §5.5 |
| 10 | Real deployment action channel stays inert (`BARTH_DEVICE_ACTION_AUTH` unset). Now asserted by test. | deferral #16 |

---

## 7. Declared limitations of W03-F's own work

### 7.1 The read-back adapter cannot exercise W03-A's principal refusal

W03-A's `read_back()` refuses a *content* principal (`model:`, `assistant:`,
`event:`, `inbound:`, `companion:`, `system:`) and its docstring says
"W03-C passes the action's principal". **W03-C's `ReadBackProvider` protocol has
no `requested_by` parameter**, so the adapter has no principal to forward and
passes `None`, which skips that check.

This is a narrowing rather than a bypass: the path is the envelope's own
post-result verification step, reachable only after an action has passed
identity, capability, scope, risk and authorization, and the read remains gated by
the consented session, the Parking Brake and the scope, all of which W03-A
re-checks on every call. Passing a fabricated principal to make the check appear
exercised would be worse than passing none.

**Closing it properly means widening W03-C's frozen protocol by one parameter.**
That is a change to a shared contract owned by a frozen package, so W03-F did not
make it. Recorded here for the wave to decide.

### 7.2 A degraded-provenance read still yields a verdict

If W03-A reads successfully but cannot durably record the observation
(`provenance_degraded`), the adapter passes the read through and logs a warning.
The observation is real, so discarding it would be its own untruth — but a
confirmation reached this way has a weaker audit trail than one whose observation
was recorded. Not converted into a refusal, because adding a new refusal rule
would be W03-F inventing policy.

### 7.3 The operator surface passes no recalled memory to the executive

`routes/operator.py` calls `run_executive_task_through_runtime_contract()`
without an `evidence` argument, so `admit_evidence(None)` admits nothing and the
executive plans on no recalled memory through that route. The B↔D verdict pairing
is correct and asserted (§4.1), and Golden Path 4 (correction changes a later
task) passes through the correction seam, so nothing is broken.

**W03-F deliberately did not wire it.** Feeding recalled memory into the
executive's planning input through the operator route would change what the
system does, not reconcile how two packages talk — a behaviour addition, and
outside the integration scope boundary. Flagged for the wave.

### 7.4 W03-C §7.7's duplicated `--db` helper block

Twelve lines duplicated between `cli.py` and `cli_trust.py`, deliberate on
W03-C's part (consolidating would be an import cycle, and the alternative module
is owned by nobody). W03-F left it: it is a tidiness change, not an integration
defect.

---

## 8. Migrations and deployment composition

Three packages create tables in the kernel database and had never run together.
`test_every_w03_schema_is_idempotent_and_order_independent` runs all three
`ensure_schema`s twice in both orders against one fresh database and asserts an
identical resulting schema. No table, index or trigger name collides.

* **W03-D** alters `memories` (provenance, confidence, validity window,
  supersession) and adds revocation tombstones. Additive; existing rows keep
  working, and an unverdicted row is refused rather than assumed valid.
* **W03-C** `actuation/store.py::ensure_schema` — action, lease, result and
  recovery tables.
* **W03-B** `executive_store.ensure_schema` — `executive_tasks`,
  `executive_task_steps`. Created idempotently from the seam; W03-F additionally
  calls it from `install_w03_seams()` so a deployment's schema is complete after
  start-up rather than after whoever calls the seam first, matching what the
  other stores already do.

Deployment composition is unchanged apart from `install_seams()` now also
installing the W03 adapters and reporting them under `w03_seams` on
`/api/health`. Nothing new is enabled by importing anything; the action-channel
resolver stays behind its own environment gate and `install_seams()` leaves it
closed (asserted, §4.1).

---

## 9. Rollback

The candidate is a set of merge commits plus three W03-F commits on top of
`main @ e96e6a6`, and `main` is untouched.

* **Do not merge:** close PR #99. `main` is already correct; nothing to undo.
* **After a merge, to revert the whole wave:** `git revert -m 1 <merge commit>`
  on `main`. Every builder head remains on its own branch and its own PR
  (#93–#97), so the wave can be recomposed without re-running any builder.
* **To revert only a W03-F seam repair:** each is a separate, individually
  revertable concern within its commit, and each is listed in §3 with its files.
  Reverting §3.1 (operator registration) returns the console to reporting the
  routes unserved; reverting §3.2 returns every server-side verdict to
  `UNVERIFIABLE` — both are the fail-closed directions the builders shipped in,
  so a partial revert degrades honestly rather than dangerously.
* **Schema:** all W03 schema work is additive and idempotent. A revert of the
  code leaves the added tables and columns in place, unused. No down-migration is
  required and none is provided.

---

## 10. Deviations from the frozen Wave 3 contracts

1. **Branch name.** `claude/w03-f-final-integration-ugqc2n` rather than
   `wave/w03-f-integration-real-world-test`; the execution harness assigns the
   branch. The manifest keeps the prescribed name in `branch` (which
   `tests/test_wave_manifest.py` enforces) and records the real one in
   `pushed_branch`, the same divergence W03-PREP and W03-D each record. **One
   consequence worth naming:** the Merge Candidate tier auto-triggers on
   `startsWith(github.head_ref, 'wave/w03-f-')`, which this branch does not
   match, so the tier is triggered by the `ci:merge-candidate` label instead.
2. **`bartholomew/integration/seams.py` scope.** The contract describes it as
   installer/seam wiring. It also carries the read-back *adapter* class, because
   the A↔C shape difference has to be translated somewhere and neither package
   may import the other. It decides nothing and re-checks nothing.
3. **Live-desktop retest not performed** (`W03_F_CONTRACT.md` Responsibility 7,
   `docs/H_LIVE_RETEST_HANDOFF.md`). No Windows hardware is reachable from this
   session. This is the largest single gap between the contract and this handoff,
   and it is why the recommendation in §11 is bounded the way it is.

---

## 11. Recommendation

**READY FOR USER MERGE APPROVAL — with the real-world Windows acceptance test
explicitly outstanding.**

Everything the contract asks of W03-F that can be discharged without Windows
hardware is discharged: the wave is composed from the five frozen heads in the
prescribed order, every seam a builder named as W03-F's is repaired, every
governance invariant is asserted on the composed head by a test rather than by
inspection, and the Golden Path suite runs with no named stop firing for a
missing package for the first time in the wave's history.

What cannot be discharged here is Responsibility 7 and the manifest's exit
criterion: *"Demonstrated end-to-end on at least one Golden Path on a real
Windows desktop."* That is the Post-Test #1 real-world acceptance test, which the
project workflow runs after this candidate is approved and merged.

**Do not merge without explicit user authorization.** Auto-merge is not enabled.

---

## 12. Frozen head

Recorded on PR #99.
