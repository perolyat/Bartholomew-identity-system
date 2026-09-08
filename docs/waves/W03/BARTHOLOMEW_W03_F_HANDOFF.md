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

### 3.6 A Parking-Brake-aborted action could verify as success — **blocker**

*Found by an adversarial audit of the composed head, not by any builder suite.
Two independent auditors reported it; reproduced before repair.*

W03-C added `ActionResultStatus.ABORTED_BY_BRAKE` as its eighth member and its
handoff §7.5 warned: *"Anything in W03-B or W03-E that exhausts either enum needs
the new member."* W03-B's `verify_step` docstring enumerated exactly W03-C's
**pre-abort** vocabulary, and was never updated, because no tree carried both
packages until this head.

```
verify_step(device_status="aborted_by_brake", read_back_port=<shows expected state>)
  -> verdict: verified,  verified: True
```

`aborted_by_brake` matched no branch, fell through to the read-back, and a
read-back that happened to show the expected state returned `VERIFIED`. In
`seam.py` the `VERIFIED` branch **proposes the next step** — so a brake abort
advanced the plan and had the next Windows action proposed *while the brake was
engaged*. Two non-negotiable invariants at once. `refused` had the identical
shape.

The repair **inverts the rule** rather than extending a list: `verification.py`
now names the only two statuses that may consult a read-back at all (`succeeded`,
so the machine can contradict the claim; `unknown`, so it can settle it) and
fails closed on everything else. A ninth member added later reports `unknown`
with no change to this file. W03-C's enum is imported rather than restated,
because restating it is what caused this. `decide_recovery` additionally returns
`STOP_AND_REPORT` for a brake abort, honouring W03-C's other rule for it —
"never a reason to re-propose".

### 3.7 Blocking I/O that W03-F itself made blocking

Seam 3.2 broke the "no blocking I/O regressions in asynchronous runtime paths"
invariant in two places, and the repair is W03-F's because the regression is.

* `actuation/seam.py`: `_server_side_verification()` was called inline from
  `async def record_action_result_through_runtime_contract`. That was free while
  no read-back provider existed. Installing one turned it into a governed UIA
  read plus a backbone write, on the loop that also serves the brake read and the
  abort check, inside the handler a device posts its result to.
* `executive/seam.py`: `verify_step` likewise — `resolve_read_back_port()`
  returned `None` on W03-B's branch.
* `routes/operator.py`: the overview's channel, actions and consent sections each
  open SQLite; only the brake section went off-loop. This read is what a console
  polls.

### 3.8 The read-back seam's over-claims

All inert on every builder branch, all live the moment W03-A's read-back
resolves. Every repair **narrows a claim**; none invents a better check.

| Defect | Repair |
|---|---|
| A foreground claim was settled by a substring match against the **whole** observed block — summary plus every element's `role: name = value`. An app id in a taskbar button or a shortcut name proved "X is in the foreground". | Foreground claims match the read-back's own summary line; content claims (typed text) still match anywhere, because a control's value is exactly where that belongs. |
| `_expectation()` returned the app id for **every** `manage_window` operation, ignoring `operation`. For `minimize` the check was **inverted**: a minimize that worked read as `failed`, one that did nothing read as `verified`. | Only operations whose success is consistent with the app being described keep a token; `minimize`, `move`, `resize` report `unknown` — what the function's own docstring prescribes for a capability it has no check for. |
| A screenshot-fallback read reports `available=True` with prose about pixels and no control text, so a truthful `succeeded` was turned into `failed`, "the state wins". | `unknown`. A manufactured contradiction is the same untruth as a manufactured confirmation. |
| `provenance_degraded` was dropped, so a confirmation whose observation was never recorded looked identical to one that can be reconstructed. | The read stands and so does the verdict, but the evidence now says so. |

### 3.9 The operator surface W03-F made reachable

* **`GET /api/operator/overview` refuses a companion credential.**
  `_consent_section` re-implemented the read behind
  `GET /api/device-consent/pending` faithfully — `include_nonce=False` and all —
  but omitted `_refuse_device_credential`, the guard whose whole purpose is *"this
  surface is for the person, not the machine"*. An enrolled companion could read
  the tenant's pending observation asks, through a route **only W03-F's
  registration made reachable**.
* **The overview no longer reports an armed channel under a halt.** It read
  `arming.current()` with no brake cross-check while `GET /api/actions/channel`
  deliberately reports `armed: false, brake_engaged: true` — so the overview was a
  second and *less truthful* answer to exactly the question W03-C built that
  surface for, and the less truthful one is what a person reads. The brake comes
  from the section's single read, so the two cannot disagree.

### 3.10 A device cannot name its own `verify_method`

`verify_method` is documented as a closed vocabulary in two places, and was
enforced in none: the allowlist admitted the key and the value passed through.
W03-E's console renders confirmed success for a method merely *absent from* its
unverified list, so a device POSTing `verify_method: "totally_legit"` got *"the
effect was confirmed by reading the machine back"* with nothing having read
anything back.

Repaired at ingestion in `bounded_evidence`, which already calls itself *"the
enforcement boundary and not a convenience for well-behaved callers"*. An
out-of-vocabulary method is stored as `unavailable` and leaves
`verify_method:not_in_vocabulary` in `dropped_keys`.

**W03-E's console is deliberately unchanged.** A first attempt tightened it there
and was rejected by W03-E's own invariant test — the console must never import a
seam it should reach over HTTP. The test was right and the fix was in the wrong
place.

### 3.11 The unbound-deployment rule (W03-A §5.1's open question)

W03-A left this to W03-F: *"the live retest should run bound, or W03-F should
decide the unbound rule in `install.py`."*

**Decision: the rule is unchanged; what changed is that it now says what it
costs.** `bound_runtime_user_id()` returns `None` on the single-runtime local
deployment, so no capability resolver is installed, so **no observation session
can start and the server-side verdict is therefore always `unverifiable`** — the
Observe and Verify halves of the loop are inert while Act works. Installing a
resolver anyway would be W03-F deciding on its own authority that an unbound
process may resolve a device's capability to observe a screen, which
`install.py`'s own docstring forbids: *"Installing the seams makes the system
coherent; it does not make it permissive."*

Silence was the defect, not the rule. The seam report now names the consequence
and the one-line fix (`BARTH_RUNTIME_USER_ID`, which the recorded live procedure
in `docs/G` §8 already sets). **This is a real-world-test prerequisite.**

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
| W03-F integration suite | **53 passed** |
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

## 4a. CI — and the acceptance criterion W03-F cannot meet

The W03-F contract's acceptance criterion 1 is *"Merge Candidate tier green on
the integrated head"*. **That criterion is not met, and it is not met because
the tier has never been green in this repository.**

The Merge Candidate tier has run exactly twice on `main`, both times on
`W03-PREP` commits that carry **no W03 product code at all** (the second is
`e96e6a6`, this candidate's own baseline, whose commit message reads *"No product
code; no W03-A..E implementation"*). Both failed. Evidence:

| `Windows full default suite + actuation (py3.11)` | `main @ e96e6a6` | W03-F head |
|---|---|---|
| `test_narrator.py::TestNarratorConfig::test_config_from_identity_with_valid_file` | FAIL `WinError 32` | identical |
| `test_narrator.py::TestNarratorEngineInit::test_init_with_custom_db_path` | FAIL `WinError 32` | identical |
| `test_narrator.py::TestPersistence::test_get_recent_episodes` | FAIL ordering | identical |
| `test_narrator.py::TestPersistence::test_get_recent_episodes_with_since_filter` | FAIL `2 == 1` | identical |
| `test_narrator.py::TestEpisodeFTSSearch::test_search_episodes_with_since_filter` | FAIL `2 == 1` | identical |
| `test_narrator.py::TestFullIntegration::test_persistence_across_narrator_instances` | FAIL `WinError 32` | identical |
| `test_persona_pack.py::…::test_get_switch_history` | FAIL `'tactical' == 'default'` | identical |
| `test_global_workspace.py::…::test_history_bounded_size` | FAIL `{'i': 5} == {'i': 9}` | identical |
| `test_voice_sight_runtime_contract_seam.py::…::test_placeholder_capabilities_are_never_called_directly` | FAIL `UnicodeDecodeError` | identical |
| `test_spoken_output.py::…::test_a_wedged_engine_is_abandoned_not_waited_on` | FAIL `WinError 193` | identical |
| **Total** | **10 failed, 2035 passed** | **10 failed, 2211 passed** |

Same ten tests, same failure modes. The 176-test difference is W03's own added
tests. **None of the ten is in a file any W03 package touches** — they are
wave-1/wave-2 modules (narrator, persona pack, global workspace, spoken output,
the voice/sight seam).

### Root causes, and a proposed patch

W03-F did **not** fix these: they are provably not this candidate's, they lie
across five unrelated modules no W03 session owns, and fixing them would widen
the PR substantially for no integration benefit. They are recorded here with
enough analysis to be actionable.

1. **Windows clock granularity + no stable tiebreaker (4 tests).**
   `persona_pack.get_switch_history` and the narrator's four `ORDER BY timestamp
   DESC` sites have no secondary sort key, and `global_workspace.get_history`
   sorts on `timestamp` alone with a *stable* `list.sort`. Windows' system clock
   granularity is ~15.6 ms against Linux's microseconds, so events written in a
   tight loop share a timestamp and "most recent first" becomes arbitrary — in
   practice *oldest* first, which is exactly what the failures show
   (`{'i': 5}` where `{'i': 9}` was expected).
   **Patch:** `ORDER BY timestamp DESC, id DESC` at each SQL site; a monotonic
   sequence tiebreaker in `global_workspace`. This is a latent product defect that
   only a coarse clock exposes, not merely a test bug.
2. **`WinError 32` — cannot unlink an open file (3 tests).** `test_narrator.py`
   creates a `NamedTemporaryFile(delete=False)` and `Path(db_path).unlink()`s it
   while SQLite still holds the handle. POSIX permits this; Windows does not.
   **Patch:** close the connection first, or use the `tmp_path` fixture.
3. **`UnicodeDecodeError` (1 test).** `test_voice_sight_runtime_contract_seam.py`
   lines 606 and 633 call `py_file.read_text()` with no encoding, so Windows
   decodes UTF-8 sources as cp1252. **This one matters beyond the red tick:** it
   is a *governance* structural no-bypass assertion, and it does not run on
   Windows at all. **Patch:** `read_text(encoding="utf-8")` — two words.
   (Every `read_text` in W03-F's own new suite already passes `encoding="utf-8"`;
   this was checked deliberately.)
4. **`WinError 193` (1 test).** `test_spoken_output.py` writes a `#!/bin/sh` stub
   and expects to execute it. **Patch:** skip on Windows, or use a `.cmd`/Python
   stub.

### The other tiers

`Quality`, `smoke`, `PR Fast tests`, `Windows lifecycle + compatibility` and
`Tests + coverage (Ubuntu, py3.11)` all passed on the integrated head. The known
xdist writer-lock class (`database is locked`), which `W03_CI_BASELINE.md` §2.6
records and which W03-A, W03-D and W03-E each hit once, is the documented
environmental flake; the same suites pass serially.

**Consequence for the recommendation:** every criterion W03-F can discharge is
discharged, but criterion 1 is blocked by a pre-existing condition of the tier
itself. That is a decision for the user, not something W03-F should paper over or
quietly widen its scope to fix.

### Addendum — resolved after W03-F closed

**This section records W03-F's own position and is left standing as written.** The
ten failures it diagnosed here were subsequently repaired by the Wave 3
merge-candidate readiness session, which continued this candidate on
`claude/wave3-merge-candidate-readiness-bop01v` (first commit `7af5fe7`, so every
commit above is preserved). W03-F's four root causes were correct and were fixed at
the root: the clock-granularity ordering defect is repaired in `narrator.py`,
`persona_pack.py` and `global_workspace.py` as a **product** defect with regression
tests that reproduce it on any platform; the three `WinError 32` unlinks, the cp1252
`read_text` and the `#!/bin/sh` stub are repaired in the tests. Nothing was skipped,
quarantined or weakened, and two assertions now run on Windows that previously could
not. See `docs/waves/W03/W03_MERGE_CANDIDATE_READINESS.md`.

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

## 6a. Escalations — found by W03-F, **not** repaired by W03-F

Per the W03-F contract's escalation boundary: a defect inside a builder's own
package, which composition did not cause and which cannot be reconciled without
a feature change, *"goes back to the builder, not fixed by widening F"*.

### 6a.1 An engaged Parking Brake does not stop a live microphone session — **W03-A**

`stop_all_for_brake(scope)` stops a session only when `scope == "global"` or
`BRAKE_SCOPE[modality] == scope`. The **only** brake poller in W03-A is the screen
`ObservationLoop`, whose `brake_scope` is `BRAKE_SCOPE[SCREEN] == "sight"`, so its
sweep can never reach a microphone session (`BRAKE_SCOPE[MICROPHONE] == "voice"`).
A microphone session runs on a bare thread (`runtime.py::_run`) with no brake
check of its own.

**Not repaired, for three reasons.** It is equally true on W03-A's own branch —
composition did not cause it. It is inside a package W03-A owns exclusively.
And adding a brake poller to the microphone path is a feature addition in another
session's package.

**Exposure in the shipped configuration is nil**: the default audio backend is
`NullAudioBackend`, which reports `NO_BACKEND`, so a microphone session never
reaches `ACTIVE`. Deferral #14 confirms Wave 3 ships no STT. An operator who
supplies a backend would reach the gap. Spoken output is unaffected —
`speech.py` delegates to the existing voice brake and creates no long-lived
session.

**Recommendation:** a W03-A follow-up giving every modality a brake poller, or a
brake sweep that stops every live session on any engagement.

### 6a.2 Recalled memory does not reach the executive, and the shapes do not fit — declared

Two separate facts, both real:

1. `POST /api/operator/tasks` — the executive's **only** production caller —
   passes no `evidence=`, so `admit_evidence(None)` admits nothing and no
   recalled memory ever reaches a plan.
2. W03-D's published `RetrievedItem` carries the text as `snippet`, the
   confidence inside `provenance`, and **no** `cautionary` field; W03-B's
   `evidence_from_row()` reads `content`/`text`, `confidence` and `cautionary`.
   So even if wired, a recalled row would be admitted with empty content.

**Not repaired.** The verdict pairing itself is correct and asserted (§4.1), and
W03-D *excludes* non-valid rows from retrieval anyway, so nothing unsafe reaches
anywhere. Wiring recall into the executive's planning input would change what the
system does rather than reconcile how two packages talk — a behaviour addition,
outside the integration scope boundary. Golden Path 4 (correction changes a later
task) passes through the correction seam without it.

**Recommendation:** a scoped follow-up that decides whether the executive should
plan on recalled memory at all, and if so adds the one-line field adapter in
`evidence_from_row()` that W03-B's handoff §5.4 already anticipated.

### 6a.3 Lower-severity findings recorded, not repaired

| Finding | Why not W03-F's |
|---|---|
| `bartholomew operator brake on --offline` cannot engage the brake after a clean shutdown: the governance write fence is closed and refuses even a tightening write. | Pre-existing platform behaviour; the auditor itself classified it as not an integration defect. Worth a follow-up — a *tightening* write arguably should be allowed through a closed fence. |
| `load_task_reflections()` selects the whole shared `reflections` table with no tenant filter and no `LIMIT`, then filters in Python. | Inside W03-B's package; a performance and scoping concern, not a composition defect. |
| An action cancelled by server-side expiry or by the person's own withdrawal is reported as *"the device reported 'cancelled'"* — false provenance in the account a person reads. | Inside W03-B; a wording/attribution fix that needs W03-C's state-to-cause mapping to be published first. |
| `_brake_section` reports the local `GovernanceStore` state, while the actuation and executive gates additionally compose the registered Platform/Admin halt. | The local read is the same one the repaired arming check uses; composing the platform tier on the overview is a small follow-up. |

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

**PASS WITH DECLARED LIMITATIONS — ready for user merge approval, with two
things the user must decide rather than W03-F.**

Everything the contract asks of W03-F that can be discharged without Windows
hardware is discharged. The wave is composed from the five frozen heads in the
prescribed order. Eleven seams are repaired, every one traceable to a builder
handoff that named it as W03-F's or to a defect composition itself created —
including one **blocker** (a Parking-Brake-aborted action verifying as success
and advancing the plan) that no builder suite could have caught, because no tree
carried both packages. Every governance invariant is asserted on the composed
head by a test rather than by inspection, and the Golden Path suite runs with no
named stop firing for a missing package for the first time in the wave's history.

The composition also **strengthened** governance rather than merely preserving
it: four over-claims in the verify path were narrowed, a device can no longer
name its own `verify_method`, a companion credential can no longer read the
person's consent asks, and the operator console can no longer report an armed
channel during a halt.

**The two open decisions:**

1. **Acceptance criterion 1 cannot be met by W03-F.** The Merge Candidate tier
   has never been green in this repository: the same ten Windows tests fail on
   `main @ e96e6a6`, this candidate's own baseline with no W03 code in it (§4a).
   They are pre-existing wave-1/wave-2 platform defects across five modules no
   W03 session owns. §4a gives the root causes and a proposed patch. Fixing them
   here would widen this PR substantially for no integration benefit; W03-F
   judged that the user's call, not its own.
2. **Responsibility 7 and the manifest's exit criterion are outstanding by
   design.** *"Demonstrated end-to-end on at least one Golden Path on a real
   Windows desktop"* is the Post-Test #1 real-world acceptance test, which the
   project workflow runs **after** this candidate is approved and merged. No
   Windows hardware was reachable from this session, and nothing here claims
   otherwise (§5). Note the §3.11 prerequisite: the live run must set
   `BARTH_RUNTIME_USER_ID`, or the Observe and Verify halves of the loop are
   inert.

**Do not merge without explicit user authorization.** Auto-merge is not enabled.

---

## 12. Frozen head

Recorded on PR #99.
