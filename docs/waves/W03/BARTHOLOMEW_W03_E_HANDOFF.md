# BARTHOLOMEW W03-E HANDOFF — Windows Golden Path Experience

| | |
|---|---|
| Session | **W03-E — Windows Golden Path Experience** |
| Immutable id | `W03-E` |
| Branch | `wave/w03-e-windows-golden-path` |
| Pull request | `[W03-E] Windows Golden Path Experience` — **#97** (https://github.com/perolyat/Bartholomew-identity-system/pull/97) |
| Baseline | `main @ e96e6a6dfc3f71a68d44010c9954bb4e7a0c5195` (W03-PREP closeout; carries `99ee734`) |
| Consumed frozen dependencies | W03-A `56bf995c` (PR #93) · W03-B `fead4b39` (PR #96) · W03-C `e904cd18` (PR #95) · W03-D `111c72d3` (PR #94) — **by published surface, not by merge** (§5) |
| Frozen head | the PR head carrying this handoff (exact SHA recorded on PR #97 and in §9) |
| Required CI tier | Integration |
| Status | **frozen** — head declared final for W03-F integration; nothing further is pushed to this branch without telling W03-F |

The manifest (`W03_MANIFEST.yaml`) still reads `status: not_started` for W03-E.
`docs/waves/W03` is owned by W03-PREP, so W03-E edited nothing in it except this
handoff, whose home this directory is (`README.md`); the status flip to `frozen`
is W03-PREP's / W03-F's one-line change.

---

## 1. What W03-E built

The first live test (docs/G §9, finding 8) found that Bartholomew had every
governance authority a Windows loop needs and **no way for a person to reach
them**: first use required hand-written JSON, three terminals, environment
variables and raw HTTP to request and approve one action. W03-E closes exactly
that gap and no more. It adds nothing A–D did not build; it wires and surfaces.

### 1.1 The operator console — `bartholomew operator …` (`bartholomew/cli_operator.py`)

One typer group, thin over the loopback control plane the server already
exposes. **It decides nothing.** Every command asks the authority that owns the
question and prints what it said; if the server said `unknown`, it prints
unknown.

| command | what it does | route / authority |
|---|---|---|
| `status` | one screen: halted? channel armed? actions waiting? devices asking to observe? lessons awaiting acceptance? | `GET /api/governance/brake`, `/api/actions/channel`, `/api/actions`, `/api/device-consent/pending`, `/api/learning/candidates` |
| `actions list [--pending-only]` | recent actions, redacted parameters | `GET /api/actions` |
| `actions show <id>` | **the approval surface**: canonical parameters, capability, risk class, approval requirement, the capability's own description of its power, results, recovery, and one truthful outcome sentence | `GET /api/actions/{id}` |
| `actions approve <id> [--note]` | the server builds the six-fact-bound approval from the stored action; the console constructs no part of it | `POST /api/actions/{id}/approve` |
| `actions deny <id> [--reason]` | withdraw; reachable while the brake is engaged, by design | `POST /api/actions/{id}/cancel` |
| `actions explain <id>` | the one-sentence account | `GET /api/actions/{id}` |
| `channel status / arm / disarm` | the arming window; `arm` speaks with the device credential because arming is the device's act; `disarm` needs no credential | `/api/actions/channel*` |
| `brake status / on / off` | **against the running server**; `--offline --db` is the documented fallback, resolved through `bartholomew.kernel.db_paths` and printing the file it touched | `/api/governance/brake*`; offline: the one `GovernanceStore` |
| `consent pending / approve / deny` | a device's ask to observe; delegates to `bartholomew/cli_consent.py`, whose nonce-from-the-database design is the proof the answer came from the person | `/api/device-consent/*` |
| `lessons pending / show / approve / accept / reject [--as NAME]` | manual acceptance, kept manual: **two explicit commands** (`approve` grants the candidate-bound authorization, `accept` consumes it); no flag collapses them. `--as` is the reviewer name recorded in the audit (the routes' `approver`/`reviewer` field); a signed-in deployment records the principal regardless | `/api/learning/candidates/*` |
| `task run "<words>" --device-id X` · `task show <id>` · `task advance <id>` | give the executive (W03-B) a task; `show` is a pure read of the stored plan; `advance` is the executive's caller-driven observe/verify/continue pass and can only *propose*. Reports the absence of the operator routes honestly on a build that does not register them | `POST /api/operator/tasks`, `GET /api/operator/tasks/{id}`, `POST /api/operator/tasks/{id}/advance` |

Three refusals are structural and tested (`test_operator_surface.py`):
the console's source never names `grant_action_approval`,
`grant_learning_acceptance_approval`, `run_action_dispatch_…`,
`run_candidate_lesson_…` or `trusted_autonomy`; it imports no actuation,
executive, windows_actuation, memory-store or runtime-contract seam; and
`explain_outcome` — the one function that turns a status into a sentence — is
exhaustive over `ActionResultStatus` ∪ `ActionState` and renders `unknown`,
`effect_unverifiable`, and a device's optimistic `succeeded` with nothing read
back as **not** success (`test_outcome_truthfulness.py`, PR Fast).

### 1.2 The operator routes (`…/routes/operator.py`) — not registered, by contract

Everything else the console needs already had a route. This module adds only
the two things that had none:

- `GET /api/operator/overview` — one read-only answer to "is he halted, can he
  act, what is waiting for me". Each section says whether it could be read; an
  unreadable brake is reported unreadable, never as clear. The device-consent
  section never includes the nonce.
- `POST /api/operator/tasks`, `GET /api/operator/tasks/{task_id}`,
  `POST /api/operator/tasks/{task_id}/advance` — the executive's **first
  production caller**. W03-B's handoff §5.2 records that wiring it is
  W03-E's/W03-F's step; these are that call. The body (pydantic `TaskIn`) can
  name an instruction and a device and nothing else — tenant and requester come
  from `device_action_auth.resolved_tenant_id` and the same identity resolution
  `routes/actions.py` uses, capability and parameters from the executive's
  closed vocabulary. **The GET is a pure read** (`executive.store.load_plan` +
  `explain_task`): it observes nothing, verifies nothing, proposes nothing, and
  is readable under the brake. **Advance is a separate POST**, because W03-B's
  advance pass is the executive's second proposing half (it can propose the
  next step or re-propose a failed one) and a request-class act must not hide
  behind a GET. Neither route approves, leases or dispatches. `201` only when a
  plan now exists; a brake denial is `409` and a refusal `422`, each carrying
  the executive's own explanation. An ambiguous instruction comes back as a
  `question`, a planned-but-unproposable step as a `named_stops` entry, and the
  `outcome`/`explanation` are the seam's own words. Step payloads carry no
  parameters: what an action would do is disclosed on the approval surface only.

`bartholomew.executive.seam` is resolved **by name at call time** (W03-B's own
pattern for W03-A's read-back). On a tree without the package the task routes
answer 503 with `EXECUTIVE_ABSENT`; on the integrated head they work with no
code change (verified in §3.2).

**`app.py` registration is W03-F's** (manifest). The console's `task run`
therefore reports "this build does not serve the operator task routes" on the
W03-E branch and on any head where W03-F has not registered the router.

### 1.3 The Golden Path suite (`tests/golden_path/`)

| file | tier | what is real |
|---|---|---|
| `conftest.py` | — | a real `bartholomew serve` process, real sockets, real SQLite, real `GovernanceStore`/brake, real route policy, real action envelope, real device action channel, real memory store and consent gate, real candidate-lesson loop. **The console is driven as a subprocess of the installed entry point**, never by importing its functions. The only thing that is not real is Windows: `Device` speaks the real channel and reports a result a test chose. |
| `components.py` | — | the named-stop mechanism: a scenario that needs a leg this tree does not carry skips with the **owning frozen package named**, and `test_named_stops.py` proves the probe is not a constant and no scenario may skip any other way |
| `test_operator_surface.py` | Integration | acceptance criterion 1, through the surface alone (§2) |
| `test_path_1_open_spotify.py` | Integration | Golden Path 1 |
| `test_path_2_find_the_document.py` | Integration | Golden Path 2 |
| `test_path_3_move_the_file.py` | Integration | Golden Path 3 — the named stop |
| `test_path_4_correction_changes_a_later_task.py` | Integration | Golden Path 4 — both halves |
| `test_path_5_before_lunch.py` | Integration | Golden Path 5 — the honest degradation |
| `test_outcome_truthfulness.py` | PR Fast | criterion 4, exhaustively, without a server |
| `test_named_stops.py` | PR Fast | the suite cannot go green by skipping quietly |

---

## 2. Acceptance criteria — results

| # | Criterion (`W03_E_CONTRACT.md`) | Result | Proven by |
|---|---|---|---|
| 1 | A non-developer can, through the operator surface alone (no hand-written JSON, no raw HTTP), see a pending action's real parameters, approve or deny it, arm the channel, and engage the brake against the running server — proven by a test driving the surface, not the internal APIs | **met** | `test_operator_surface.py` (14 tests): every assertion is made by running `bartholomew operator …` as a subprocess against a live server. `…_read_an_action_s_real_parameters_before_deciding` (list redacts, show discloses the typed text); `…_approve…`; `…_deny_and_it_can_never_run`; `…_disarm_and_arm_the_channel` (§4 on arming — disarm proven for real, arm proven to the credential refusal); `…_operate_the_brake_against_the_running_server` + `…_the_brake_the_console_engages_is_the_one_the_envelope_reads` (non-vacuity: the halt refuses a proposal, release admits it); `…_reports_an_unreachable_server_as_unreachable`. The one leg the surface does not do is *propose* — that is the executive's (§5) |
| 2 | Paths 1, 2, 4 pass end to end against real stores — to the lease boundary (1), fully read-only (2), across two tasks (4); paths 3 and 5 to the extent W03-C's capabilities and the foreground-lock reality allow, shortfalls asserted as named stops | **met on the composed head; met to the named stops on this branch** | §3.1 / §3.2. Path 1 runs propose → approve (console) → arm → **lease over the real channel** → report → truthful account, plus brake-mid-path. Path 2 proposes nothing (asserted against the real actuation store) and, with W03-D, carries a `currently_valid` verdict and expires stale observations. Path 4 proves both halves (§2.1). Path 3's stop is asserted against `CapabilityKind` itself. Path 5 asserts no input synthesis structurally and that a foreground refusal is a `failed`, never a restore |
| 3 | Every path exercises the real architecture — envelope, observation event, retrieval verdict, Parking Brake, manual lesson acceptance — no scenario mocks a governance authority | **met** | `grep -rn "mock\|monkeypatch" tests/golden_path` finds nothing; the only double is Path 4's capturing model responder, which is not an authority. Observation event and retrieval verdict are exercised for real on the composed head (`test_path_2…::test_the_recalled_record_carries_its_provenance`, `…_an_expired_observation_stops_being_recalled`, `…_keeps_observed_and_inferred_apart`) and are named stops on this branch |
| 4 | Every scenario ends with a truthful outcome explanation; `unknown`/`effect_unverifiable` is never presented as success | **met** | `test_outcome_truthfulness.py` (exhaustive over both enums, direction pinned on every edge, non-vacuity on the confirmed case); over the wire in `test_path_1…::test_a_device_that_cannot_confirm_the_effect_is_never_explained_as_success` and `test_path_3…::test_the_path_runs_as_far_as_the_shipped_capabilities_allow` (`open_path` honestly `unknown`); `…_reports_success_with_nothing_read_back_is_still_not_success` needs W03-C's evidence keys and runs on the composed head |

### 2.1 Test contracts discharged (`W03_TEST_CONTRACTS.md`)

- **§6 Longitudinal — scenario half (W03-E's).** `test_path_4…`: an unapproved
  correction (candidate proposed, `requires_review`) leaves the later task
  byte-identical; acceptance without the candidate-bound authorization is
  refused and consolidates nothing; an explicitly approved-then-accepted
  correction changes a *later, differently-worded* task; the same later task
  before/after differs by exactly the lesson (non-vacuity); a rejected
  correction can never come back; `SHIPPED_EXECUTION_MODE == "shadow"` and a
  proposed candidate consolidates nothing however many tasks pass. The
  mechanism half is W03-D's (`test_w03d_manual_acceptance_authoritative.py`).
- **Adversarial (contract Testing).** Brake engaged during a Golden Path halts
  it (`test_path_1…::test_the_parking_brake_engaged_during_the_path_stops_the_governed_loop`:
  approved action does not lease, new proposal refused, channel reports
  `brake_engaged`, release re-admits). Recalled imperative text stays text and
  grants nothing (`test_path_2…::test_an_imperative_hidden_in_the_recalled_text_stays_text`).
  One action's approval leases no other (`…_bound_to_this_action_and_authorizes_no_other`).

---

## 3. Golden Path results, separated by where they were verified

### 3.1 On this branch (W03-E's own committed diff, A–D absent)

| path | result | named stops on this branch |
|---|---|---|
| 1 Open Spotify | **pass** to the lease boundary, entering at the envelope; a device's `succeeded` with no recorded verification reads as *not confirmed* | interpret leg (W03-B + W03-F's registration) — `task run` reports the routes unserved; the two over-the-wire verification cases (W03-C evidence keys) |
| 2 Find the document | **pass** (read-only proven; imperative text inert) | provenance verdict + expiry (W03-D); observed/inferred event (W03-A) |
| 3 Move the file | **pass as a named stop** (no move capability; `open_path` runs end to end and reports `unknown` honestly) | the move itself — a W03-C capability that does not exist and must not be added here |
| 4 Correction → later task | **pass**, both halves, plus relevance control and supersession through the correction seam | none |
| 5 Back to before lunch | **pass** (reconstruct without acting; `focus_window` as one ordinary action; foreground refusal is `failed`; no input synthesis, structurally) | the foreground lock itself — restore is offered, never forced; supersession-aware recall (W03-D) |

Branch run (`pytest tests/golden_path -m "integration or not integration" -o addopts=""`): **73 passed, 6 skipped, 0 failed** — the six skips are the named stops in the table above, each printed with the owning frozen package (or, for the interpret leg, W03-F's registration) in its reason.

### 3.2 In a disposable composition worktree (D → A → C → B → E, W03-F's order)

Built at `/home/user/w03-compose` from `origin/main @ e96e6a6` by merging the
four frozen SHAs in W03-F's integration order (**all four merged clean, no
conflicts**) and overlaying W03-E's diff as a patch. **Nothing from this
worktree is committed**; it exists only to run the suite with every leg present.

`components.probe` there: every leg present (`observation_event`, `read_back`, `executive`, `action_abort`, `verify_evidence`, `retrieval_verdict`). For this run only, the worktree's `app.py` carried the one uncommitted line W03-F will add (`app.include_router(operator.router)`), so `bartholomew operator task run "Open Spotify"` reached the real executive.

Result: **80 passed, 0 skipped, 0 failed** — no named stop fired, because every leg was there.

What changes with A–D present: Path 1 starts from `bartholomew operator task
run "Open Spotify"` through the real executive to a real `windows.launch_app`
proposal; Path 2's provenance/expiry/non-collapse tests run for real; the
`succeeded`-with-nothing-read-back over-the-wire case runs for real.

### 3.3 What remains W03-F's

- The live-desktop run of paths 1 and 4 (contract Testing; manifest exit
  criterion). Nothing in this handoff claims a real pid, a real window or a real
  foreground change.
- Registering `routes/operator.py` in `app.py`, and removing the three
  `test_s8_route_policy_coverage.py` exemptions at the same time.
- Wiring `install_read_back_provider()` (W03-C §7.2) so the server-side verify
  verdict is not always `UNVERIFIABLE`.
- Confirming the two ownership crossings (§6).

---

## 4. Boundaries held, and one thing stated plainly about arming

- **No new capability, memory field, executive behaviour, action path, event
  bus, memory or governance authority.** The console posts to existing routes;
  the routes call existing seams.
- **No automatic acceptance, no standing permission, no autonomous capture, no
  synthesised input.** `test_path_5…::test_nothing_in_this_package_synthesises_input`
  scans every W03-E-owned file for `SendInput`/`keybd_event`/`SetForegroundWindow`/
  `AttachThreadInput`/`ctypes`/`pyautogui`/`pynput` and proves the scan is not
  vacuous.
- **Arming.** `bartholomew operator channel arm` speaks with the device
  credential from the OS keyring, because arming is the enrolled device's act
  (`require_companion`). In the CI harness no keyring holds a credential, so
  `test_a_person_can_disarm_and_arm_the_channel` proves **disarm** for real and,
  for **arm**, proves that the console refuses with the credential instruction
  rather than opaquely. The arm half is proven live on the ServeProcess only
  because the double-gated test resolver pre-arms the test device (`app.py`),
  which `status` and `channel status` read back. A real arm from the console is a
  W03-F live-desktop item alongside the paths themselves.

---

## 5. Interfaces consumed from A–D, exactly

| session | surface consumed | where |
|---|---|---|
| W03-A | `bartholomew.multimodal.observation.ObservedEvent.inactivity(seconds)` (non-collapse assertion) | `test_path_2…` |
| W03-B | `bartholomew.executive.seam.run_executive_task_through_runtime_contract(ctx, *, tenant_id, device_id, requested_by, instruction)` · `advance_executive_task_through_runtime_contract(ctx, *, tenant_id, task_id)` · `ExecutiveTaskResult.{plan, outcome, reason, governance_allowed, explanation, proposed_action_ids, provenance_degraded}` · `executive.store.{ensure_schema, load_plan}` · `executive.explanation.explain_task(plan)` · `Plan.{task_id, instruction, status, clarification, notes, cautions, steps}` · `PlanStep.{index, capability, described_as, status, action_id, refusal_category, refusal_reason, verification}` · `Verification.{verdict, detail}` · `StepStatus.awaiting_authorization` (the console's approval hint keys on it) | `routes/operator.py` (by name at call time), `cli_operator.py` |
| W03-C | `evaluate_action_abort_through_runtime_contract` and `result.VERIFY_METHODS` (presence probes only); the eighth status `aborted_by_brake` (in the console's outcome table ahead of the package); the `verified`/`verify_method` evidence keys | `components.py`, `cli_operator.py`, `test_path_1…` |
| W03-D | `bartholomew.kernel.consent_gate.ConsentGate.validity_verdicts(ids) -> {id: ValidityVerdict}` with `.verdict` / `.currently_valid` / `.as_dict()`; the enforced `environment_observation` 7-day expiry; `MemoryStore.correct_memory` supersession (`memory_revision` at `key@rN`) | `test_path_2…`, `test_path_5…` |
| wave-two (on main) | action envelope over `/api/actions*` and `/api/device-actions*`; `GovernanceStore`; `device_consent`; the candidate-lesson seam (`run_candidate_lesson_through_runtime_contract`, `grant_learning_acceptance_approval`, `run_chat_through_runtime_contract`); `arming` | throughout |

Nothing was imported from another builder's owned module into a W03-E
production module except by name at call time (`routes/operator.py`).

---

## 6. Files, and the two ownership crossings

**Owned (manifest `owns`), all new:** `bartholomew/cli_operator.py`;
`bartholomew_api_bridge_v0_1/services/api/routes/operator.py`;
`tests/golden_path/{__init__,components,conftest,test_operator_surface,test_path_1_open_spotify,test_path_2_find_the_document,test_path_3_move_the_file,test_path_4_correction_changes_a_later_task,test_path_5_before_lunch,test_outcome_truthfulness,test_named_stops}.py`;
this handoff.

**Modified outside ownership, deliberately and flagged for W03-F:**

1. `bartholomew/cli.py` — **one registration line** (`app.add_typer(operator_app,
   name="operator")`) plus the import and a comment. Exactly how `cli_trust`,
   `cli_companion` and `cli_consent` are registered. A console nothing can invoke
   is a shipped-broken surface, not a scope saving.
2. `bartholomew/platform/route_policy.py` — **four entries**, pre-classified
   before W03-F registers the router, on the precedent the file's own
   inbound-capture comment records (default-deny would 403 the routes the moment
   they were registered). Classification reasoning is in the file: overview =
   `SELF_READ` (a statement about the person's day, same class as the other
   current-state reads); `POST /tasks` = `ACTION_REQUEST` (asking for a task is
   asking for the actions it proposes, through the same envelope that capability
   already gates; the executive can never mint an approval); `GET /tasks/{id}` =
   `ACTION_READ` (a pure read of the stored plan); `POST /tasks/{id}/advance`
   = `ACTION_REQUEST` (the executive's second proposing pass). No new
   capability was minted, and nothing request-class sits behind a GET.
3. `tests/test_s8_route_policy_coverage.py` — four routes added to the
   existing `exempt` set for pre-classified, not-yet-registered routes. The
   coverage direction (every served route is classified) is unchanged. **W03-F
   removes these four when it registers the router.**

W03-C and W03-D each made the same shape of crossing to `route_policy.py` and
flagged it the same way.

**Not touched:** `app.py`, `Identity.yaml`, `pyproject.toml`,
`.github/workflows/**`, the manifest, any A–D owned module, any A–D frozen branch.

**Migrations:** none. No schema, no new table, no new memory field.

---

## 7. Tests and CI

Verification run locally on the frozen head, per `W03_CI_BASELINE.md`:

| check | result |
|---|---|
| `pre-commit run` on every changed file (black, ruff, end-of-file, trailing-whitespace, private-key, large-files) | pass |
| `tests/test_s8_route_policy_coverage.py` + `tests/test_wave_manifest.py` + `tests/smoke/test_packaging_contract.py` + `tests/test_kernel_db_path_resolution.py` + `tests/test_cli_governance_and_lock.py` | pass |
| default suite, `pytest -n auto --dist loadfile` (the PR Fast tier's invocation) | pass — **4187 passed, 2 skipped, 0 failed** (baseline before W03-E: 4151; W03-E adds 36 non-integration tests) |
| `tests/golden_path` on this branch (Integration `critical` runs it under `-m "integration or slow"`; the PR-Fast files run in the default suite) | **73 passed, 6 skipped (named stops), 0 failed** |
| `tests/golden_path` in the composition worktree (§3.2) | **80 passed, 0 skipped, 0 failed** |
| adversarial review of the diff (five dimensions, three refuters per finding) | see §8.9 |

GitHub Actions on the PR: PR Fast on every push; the Integration tier under the
`ci:integration` label on the draft. The head is declared frozen only once both
are green; results are on the PR's checks tab and in W03-E's closing status.
### CI, measured on PR #97

| tier | job | result on `58b895e` |
|---|---|---|
| **Integration** (required) | Tests + coverage (Ubuntu, py3.11; full default suite, serial, gate 70%) | **success** |
| | Critical integration + lifecycle (Ubuntu, py3.11; runs `tests/golden_path` under `-m "integration or slow"`) | **success** |
| | Windows lifecycle + compatibility (py3.11) | **success** |
| PR Fast | Quality (format, lint, packaging contract) | success |
| | smoke (live uvicorn) | success |
| | Windows fast (packaging, lifecycle, actuation suites) | success |
| | PR Fast tests (Ubuntu, py3.11, parallel) | **failure, twice, on the recorded xdist writer-lock class** — attempt 1: `test_notifications_api.py::test_mute_and_unmute_round_trip` (`database is locked`), 4186 passed; attempt 2 (the one re-run): `test_event_backbone_drive.py::test_the_running_scheduler_processes_a_captured_event` and `test_sqlite_wal_concurrent_processes.py::test_wal_cleanup_concurrent_processes` (`database is locked`), 4185 passed, the notifications test green. Three different tests across two attempts, none in code W03-E touches; `W03_CI_BASELINE.md` §2.6 and `docs/SESSION_HANDOFF.md` record the class and its root cause; the identical suite passed serially in the Integration job on the same commit; W03-A (#93) and W03-D (#94) each needed a second attempt for the same class. Not fixable within W03-E's ownership; recorded on the PR so W03-F does not re-investigate. |
| Merge Candidate | — | correctly skipped; that tier is W03-F's |

The head this handoff is committed on re-runs both tiers; the freeze in §9 is
declared against the Integration tier's result on it.

No governance test was skipped, quarantined, weakened or deleted. The only
existing test file touched is `test_s8_route_policy_coverage.py`, by extension
of its exemption set (§6).

---

## 8. Residual risks, limitations, named stops and deferrals

1. **Path 3 stops.** No file-move capability exists and none was added; the
   stop is a test (`test_no_shipped_capability_can_move_a_file`) that will fail
   the day one is added, so the handoff cannot go stale silently. Not added to
   the deferral register by W03-E (the register is W03-PREP's/W03-F's); flagged
   here for W03-F to record as a deferral if the wave wants the capability at
   all — it is the "file movement" the `CapabilityKind` docstring excludes by
   design.
2. **Path 5 stops at the foreground lock.** Restore is one `focus_window`
   action a person approves; a refusal is `failed`; nothing forces it.
3. **The interpret leg is absent on this branch.** By design (§5). `task run`
   says so in words a person can act on.
4. **No hardware evidence.** Every device report in the suite is a test's
   choice over the real channel. Paths 1 and 4 live are W03-F's.
5. **`explain_outcome` is a table, not a query.** It is exhaustive over the
   enums *in this tree* and carries `aborted_by_brake` ahead of W03-C; a status
   the console does not know renders as unknown, never as success. A tenth
   member in a later wave fails `test_the_console_has_a_sentence_for_every_status…`.
6. **Arming from the console is proven only to the credential refusal in CI**
   (§4). Not a defect in the console; a property of a keyring-less runner.
7. **The `--offline` brake fallback is a second *path* to the one store, not a
   second authority.** It resolves through `db_paths` and prints the file, which
   is the whole fix for the live defect; it does not bypass the write fence or
   the stale-revision guard (`WriteFenceClosedError`/`StaleGovernanceWriteError`
   are surfaced, not swallowed).
8. **Advancing a task is a request, and it is a POST.** W03-B's caller-driven
   `advance` pass (its §5.3: nothing in the executive polls) can propose the
   next step or re-propose a failed one. It is `POST /tasks/{id}/advance`,
   classified `ACTION_REQUEST`, surfaced as `task advance`; the GET / `task
   show` is a pure read. Recorded so W03-F does not wire a poller to the POST.
9. **Adversarial review of the diff.** Five independent reviewers (contract,
   governance, correctness, test vacuity, security) raised 46 findings; the
   refuter stage was cut off by a session limit, so every finding was triaged
   by hand against the code. Fixed before freeze: the `lessons` commands
   omitted the routes' required `approver`/`reviewer` field (they could never
   succeed); `lessons pending` read `inferred_rule` where the route emits
   `rule`; `routes/operator.py` read `action_ids`/`verification.reason` where
   W03-B publishes `proposed_action_ids`/`Verification.detail`; a brake-denied
   or refused task answered `201` and the console printed "Understood as:
   None"; the task GET advanced the task behind a read classification (split,
   above); the overview's consent section was not tenant-scoped (now mirrors
   `_consent_tenant`); `requested_by` was derived differently from
   `routes/actions.py` (now the same function); `--offline` brake accepted
   scopes the server refuses (now the same `VALID_SCOPES`); `explain_outcome`
   rendered a device's `succeeded` with *no* recorded verification as
   confirmed (now "no verification was recorded … NOT confirmed success");
   `_outcome_line` preferred the last results row over the row's terminal
   state; the console's route-unserved detection could not fire on `task
   show`; and, in the suite, the consent test read the developer's default
   database, the `verify_method` used was outside W03-C's closed vocabulary,
   Path 5 reported a non-existent error category, Path 4 lacked a relevance
   control and a supersession test, Path 2 simulated the observed half even
   where W03-A's `ObservedEvent` exists, and Path 1's interpret-leg stop was
   printed rather than skipped by name. Recorded, not changed: Path 2's
   "answer" is a recall with provenance, not a generated sentence (no answer
   generator exists to consume); the instruction to `task run` is an ordinary
   command-line argument and lands in shell history (the envelope refuses
   secret-looking text; do not put a secret there); `brake on --offline` is
   refused after a *clean* server shutdown because the store's write fence is
   closed then — the same posture the existing `bartholomew brake on` has, and
   the correct one (the fallback exists for a server that is *not* running
   cleanly, and `status --offline` reads regardless).

**Contract deviations:** none. **Escalation boundary:** not reached — no
Golden Path needed a capability, memory field, executive behaviour, permission,
autonomous capture or synthesised input that A–D did not ship; the two paths
that could have tempted one are recorded as named stops instead.

---

## 9. Freeze

Frozen at **the PR head carrying this handoff** (the exact SHA is recorded on
PR #97 in W03-E's closing comment and in the session's final report), declared
once the Integration tier — the manifest's `required_ci_tier` for W03-E — was
green on it. PR Fast's parallel `fast-tests` job is subject to the recorded
writer-lock intermittent (§7) and its state on the frozen head is reported,
not hidden. Freeze means freeze: nothing further is pushed
to `wave/w03-e-windows-golden-path` without telling W03-F on the PR.
