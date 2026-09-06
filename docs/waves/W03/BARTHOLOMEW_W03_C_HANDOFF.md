# BARTHOLOMEW W03-C — Governed Windows Action & Reliability — handoff

> Written by **W03-C — Governed Windows Action & Reliability** at completion.
> Authoritative contract: `docs/waves/W03/W03_C_CONTRACT.md`.
> Read `docs/waves/W03/README.md` for the rules every W03 session inherits.

| | |
|---|---|
| Session | **W03-C — Governed Windows Action & Reliability** |
| Branch | `wave/w03-c-governed-windows-action` |
| Pull request | `[W03-C] Governed Windows Action & Reliability` |
| Baseline | `main @ e96e6a6dfc3f71a68d44010c9954bb4e7a0c5195` (carries the whole wave-two baseline; `99ee734` is in its ancestry) |
| Required CI tier | Integration |
| Status | frozen |

---

## 1. What W03-C was for, and what actually changed

The wave-two envelope was already strong: eleven ordered governance checks, an
approval bound to six facts, a brake read twice, digest-only evidence, `unknown`
as an honest status, and a real-Win32 suite. The contract said W03-C owned that
envelope and had to harden the two places it stopped short of a reliable loop.
Both are now closed, and neither was closed by relaxing anything.

### 1.1 Verification — `Act -> Verify` (criterion 1)

`windows.type_text` was a permanent `effect_unverifiable`. The handler sent
keystrokes, Windows accepted them, and the honest answer stopped there because
confirming that the characters landed meant reading the field's contents back,
and that is reading the person's writing.

It still is — so the read never leaves the function that makes it.
`uia.focused_field_text()` reads `ValueValue` off the focused element, reduces it
to a **length and a SHA-256**, and drops the string. `uia.FieldText` has four
attributes and none of them can hold text. The handler takes one measurement
before typing and one after, and `uia.verify_typed_text()` compares them.

The result is a verdict genuinely distinct from issuance:

| Situation | Before W03-C | Now |
|---|---|---|
| Every event accepted, field read back and agrees | `unknown` | **`succeeded`**, `verified: true`, `verify_method: uia_value_read_back` |
| Every event accepted, field read back and does **not** hold them | `unknown` | **`failed`**, `verified: false` |
| Every event accepted, no read-back provider (no `comtypes`, no `ValuePattern`) | `unknown` | `unknown`, `verify_method: unavailable`, and it says why |
| Only some events accepted | `failed` | `failed` (unchanged) |

The middle row is the point. "Windows accepted every keystroke" and "the
characters are in the field" were previously reported identically; a focus
change between the safety check and the injection now shows up as a failure
rather than as a shrug.

The other eight handlers already observed their own effects (a launched process
looked for by image, a foreground handle read back, a clipboard write compared),
so criterion 1 needed no change to them.

**The server half.** `bartholomew/actuation/verification.py` is the narrow
adapter W03-A's governed read-back is installed into: a `ReadBackProvider`
Protocol with one method, a holder, and `verify_effect()`. It is **installed,
never imported** — `bartholomew/actuation/` deliberately has no import edge to
`bartholomew/multimodal/`, and a test asserts that. Consulted only when a device
reported `unknown` with `effect_unverifiable`, it contributes one `server_verified`
boolean to the evidence and **never overwrites the device's status**: the device
was the party on the machine, and a server that could rewrite its honest
`unknown` into a `succeeded` would be the same fiction relocated one process
away. With no provider installed the verdict is `UNVERIFIABLE` and no key is
written at all — "nobody asked" and "somebody asked and could not confirm" are
different facts about an audit row.

### 1.2 Stop after lease (criterion 2)

The contract named the gap exactly: once `try_lease` succeeded, nothing — server
or companion — could abort the action, and a leased-row cancel never reached the
device. `store.mark_cancelled`'s own docstring said so.

Closed by two halves that only work together:

**Server.** `seam.evaluate_action_abort_through_runtime_contract()` answers "may
these actions still run?" for one device. It re-reads the Parking Brake (the same
fail-closed two-tier read the rest of the seam uses), then reads each row's
state. An action is `running` only if its row was read and says `leased` —
affirmative only, so a cancelled, expired, swept, unknown or other-device action
all stop. When a brake is engaged the server moves each held lease to
`aborted_by_brake` **itself**, so an operator who engages a brake and then loses
the network still sees the action recorded as stopped rather than sitting at
`leased` until the sweep calls it `unknown`.
Exposed as `POST /api/device-actions/abort-check` on the device channel.

**Device.** `ActionChannelClient.abort_check()` is a third verb whose whole
failure mode is "act less": a transport failure, a 401, a malformed body and an
older server with no such endpoint all produce `AbortSignal.unreadable(...)`, and
`may_run()` is `False` for every one. The runner reads it **between the lease and
the handler** — one read for the whole batch, so the gap is as small as it can
be — and `HandlerContext.abort_gate` lets a long step re-check inside itself
(`launch_app` waiting for a window, `manage_window` between finding a window and
changing it). The gate takes nothing and returns a reason or `None`; there is no
value it can return that starts, retries, redirects or widens anything.

**The bounded interval** is `seam.ABORT_CLEARANCE_SECONDS = 5`. Every lease
carries an `abort_deadline` the server stamped; `dispatch.check()` gained a fifth
device-side check that refuses an action past it, and treats a missing,
unparseable or naive deadline as expired — the same treatment `_parse_expiry`
gives an unreadable expiry. So an already-leased action stops within roughly five
seconds of a brake being engaged.

**`aborted_by_brake` is a status, not an error category on `cancelled`.** A
person withdrawing an action and a safety control stopping one are different
events with different follow-ups, and "how many actions did a halt stop after
they were leased" should be one query over one column. It is terminal, it is
device-reportable (the device is the party that reads the signal and declines),
`succeeded` is `False` for it, and its evidence carries `steps_completed` so an
abort before the handler (`0`) reads differently from a multi-step handler
stopped part-way.

**This is a cooperative stop and the code says so.** A companion that has
crashed, wedged or been prevented from polling is not stopped by any of it. The
independent out-of-process emergency stop is `W03_DEFERRALS.md` #8 — named, not
built, and `test_the_stop_is_cooperative_and_says_so` asserts that the code and
the register still say so, so a later reading cannot mistake this for it.

### 1.3 Envelope integrity and no-bypass (criteria 3, 4)

No production change was needed: the brake is already read at request, at
approval and immediately before the lease, and dispatch already requires an
action-bound approval regardless of the Identity allowlist. What was missing was
the proof that each precondition is **individually load-bearing**, and the
W03 test contract's no-bypass assertion. Both are now in
`tests/test_windows_action_envelope_integrity.py` — see §3.

### 1.4 Recovery (criterion 5)

`bartholomew/actuation/recovery.py` turns an outcome into one of three defined
next steps (`NONE` / `RETRY_ELIGIBLE` / `SURFACE`), computed once on the server so
the executive, the operator surface and a later audit read the same answer.
`RETRY_ELIGIBLE` requires **both** an idempotent capability and a transient
category; every refusal surfaces, every non-repeatable `unknown` surfaces, and an
abort always surfaces however idempotent the capability is.

`RETRY_ELIGIBLE` is advice and structurally cannot be acted on: a terminal row has
no transition out of it, so the action a plan describes can never be re-leased,
and `requires_new_approval` is `True` on every retry-eligible plan. `recovery.py`
imports nothing but `dataclasses`, `enum` and `.result`, which a test asserts.
The plan is surfaced on `POST /api/device-actions/{id}/result` and on
`GET /api/actions/{id}`.

The abandoned-lease sweep already existed and already reached `unknown` rather
than `cancelled`; it is now covered by tests rather than only by its docstring.

### 1.5 Operator `--db` (criterion 6)

All **ten** remaining stale-default commands are migrated — six in
`bartholomew/cli.py` (`embeddings stats`, `embeddings rebuild`,
`embeddings rebuild-vss`, `train`, `say`, `unattended-report`) and four in
`bartholomew/cli_trust.py` (`share adopt-local`, `share approve-local`,
`share review-local`, `share mark-revoked`). Each now defaults through
`bartholomew.kernel.db_paths` and **prints the file it touched and where the
answer came from**. Printing is half the fix: the live-Windows defect was an
operator unable to tell which file a command had changed.

The brake commands (already migrated in wave two) are unchanged. An explicit
`--db` still wins unconditionally.

---

## 2. Files W03-C changed

Everything is inside W03-C's declared ownership, except the two CLI modules,
which acceptance criterion 6 explicitly authorises.

**New (owned):**

| File | What it is |
|---|---|
| `bartholomew/actuation/verification.py` | The `ReadBackProvider` Protocol, the holder, and `verify_effect()`. W03-A's seam into Verify. |
| `bartholomew/actuation/recovery.py` | The pure recovery policy. Advice only; imports nothing that could act. |

**Modified (owned):**

| File | Change |
|---|---|
| `bartholomew/actuation/result.py` | Eighth status `ABORTED_BY_BRAKE`; `HandlerOutcome.aborted_by_brake()`; `VERIFY_METHODS`; six new evidence keys. |
| `bartholomew/actuation/store.py` | `ActionState.ABORTED_BY_BRAKE`; `states_for()`; `mark_aborted_by_brake()`; `MAX_ABORT_POLL_IDS`. |
| `bartholomew/actuation/seam.py` | `ActionAbortSignal`, `evaluate_action_abort_through_runtime_contract()`, `abort_deadline()`, `ABORT_CLEARANCE_SECONDS`; server-side verification consulted in `record_action_result_...`; `recovery` on `ActionSeamResult`. |
| `bartholomew/windows_actuation/uia.py` | `FieldText`, `focused_field_text()`, `verify_typed_text()`, `UIA_VALUE_VALUE_PROPERTY_ID`. |
| `bartholomew/windows_actuation/handlers.py` | `type_text` verifies; `HandlerContext.abort_gate` / `check_abort()`; per-step abort in `launch_app` and `manage_window`. |
| `bartholomew/windows_actuation/dispatch.py` | `AbortSignal`, `abort_deadline_passed()`, `LeasedAction.abort_deadline`, the fifth device-side check. |
| `bartholomew/windows_actuation/channel.py` | Third verb `abort_check()`. |
| `bartholomew/windows_actuation/runner.py` | `abort_signal()`, `_abort_gate()`, `_record_abort()`; abort read before dispatch; `aborted` in the run summary. |
| `.../routes/device_actions.py` | `POST /abort-check`; `abort_deadline` + `abort_check_seconds` on the lease; `recovery` on the result. |
| `.../routes/actions.py` | `recovery` on `GET /api/actions/{id}`. |
| `deploy/windows/README.md` | "Stopping something that has already started"; new troubleshooting rows; the cooperative-stop limitation. |

**Modified (criterion 6):** `bartholomew/cli.py`, `bartholomew/cli_trust.py`.

**Modified (ownership crossing — flagged for W03-F):**
`bartholomew/platform/route_policy.py`, one entry. See §2.1.

**Not touched:** `bartholomew/multimodal/` (W03-A), `bartholomew/executive/`
(W03-B), the memory schema (W03-D), `app.py` router registration (W03-F). The
device-actions router is already registered, so no `app.py` change was needed.

---

## 2.1 One ownership crossing, deliberately made and flagged

`bartholomew/platform/route_policy.py` is in no W03 session's `owns` list.
W03-C added **one line** to it:

```python
("POST", "/api/device-actions/abort-check"): Capability.DEVICE_ACTION_CHANNEL,
```

**Why it could not be left out.** `route_policy.capability_for` is default-deny:
an unclassified route raises `UnclassifiedRouteError`, which the admission
middleware turns into a 403 before the handler runs. Without this line the abort
endpoint is unreachable in production, acceptance criterion 2 is unprovable
outside the test suite, and `tests/test_s8_route_policy_coverage.py::
test_every_route_is_either_public_or_classified` fails. That is a shipped-broken
endpoint, not a scope saving.

**Why this shape.** The file already carries the precedent in its own comments
(`route_policy.py`, the inbound-capture block: *"Classified before the routes
exist, deliberately. Routes are default-deny, so an unclassified
/api/inbound/events would 403 the moment D registered it"*), and the two sibling
device-action routes were classified there by the wave-two actuation session.
The entry reuses `DEVICE_ACTION_CHANNEL` rather than inventing a capability: a
device that may be handed work is exactly the party that must be able to ask
whether to stop, and a separate capability would make "a device that can act and
cannot be told to stop" a reachable configuration. It grants no power its holder
did not already have, and the abort response can only ever make a device do less.

**W03-F should confirm this crossing** and move it if the wave prefers it
elsewhere. It is one line and it is the only file W03-C touched outside its
ownership and criterion 6's authorisation.

## 2.2 Two defects found in W03-C's own new work, and fixed

Both were found by an adversarial re-read of this session's diff, and both are
recorded here because the second one is the kind of thing that is much cheaper
to know about than to rediscover.

**(a) The abort route was unclassified.** Above. Found by
`test_s8_route_policy_coverage.py`, which W03-C had not run until late.

**(b) A stale clearance was reported as a halt that never happened.**
`seam.abort_deadline()` stamps `lease_time + ABORT_CLEARANCE_SECONDS` onto every
action in a lease, once. Nothing refreshed it. `MAX_LEASE_BATCH` is 10 and
`launch_app` alone can block for `LAUNCH_WINDOW_WAIT_SECONDS = 4.0`, so an
ordinary batch that took longer than five seconds had its tail refused by
`dispatch.check()` with `ErrorCategory.PARKING_BRAKE` **while no brake was
engaged anywhere** — a halt that did not happen, written into a durable audit
row, which is exactly the fiction the result vocabulary exists to refuse. It
also inverted the deadline's meaning: it is a bound on how long a halt may go
unhonoured, not a budget for how long a batch may take.

Fixed by making the clearance a property of the *read* rather than of the lease:

* `ActionAbortSignal` and the `/abort-check` response now carry
  `clearance_deadline`, **re-stamped on the server's clock on every read** (a
  device that computed its own would be choosing how long it may act without
  asking again);
* the runner re-reads the signal when its clearance has expired and carries the
  server's newest stamp into `dispatch.check()`. A refresh that fails, or that
  comes back halted, still stops — it can only ever confirm.

And one modelling correction found alongside it: `dispatch()` mapped **every**
device-side refusal to `FAILED`, including the clearance refusal, so "a halt
stopped this" landed in the same column as "the window would not focus". A
`PARKING_BRAKE` refusal from `check()` now returns
`HandlerOutcome.aborted_by_brake(...)`. Nothing else in `check()` raises that
category.

Regression tests: `test_a_batch_that_outlives_its_clearance_is_not_a_halt`,
`test_a_refresh_that_says_stop_still_stops`,
`test_a_refresh_that_cannot_be_read_stops`,
`test_the_clearance_a_device_honours_is_always_the_servers`,
`test_every_abort_answer_carries_a_freshly_stamped_clearance`.

---

## 3. Acceptance criteria — results

| # | Criterion | Result | Proof |
|---|---|---|---|
| 1 | **Act -> Verify** — a verdict distinct from issuance; `type_text` confirmed by reading the field back where a provider exists, honest `unknown` where it does not | **Met** | `tests/test_windows_action_verification.py` (30 tests); real-Win32 assertions in `tests/integration/test_windows_action_real.py` |
| 2 | **Stop-after-lease** — an engaged brake aborts an in-flight or not-yet-started leased action within a bounded interval; lease carries an abort deadline; `aborted_by_brake` recorded | **Met** | `tests/test_windows_action_stop_after_lease.py` (34 tests, incl. the fake-channel end-to-end); over-HTTP in `tests/integration/test_windows_action_http.py` |
| 3 | **Envelope integrity** — an executive-generated action cannot execute without identity, capability, parameters, risk, a clear brake at all three points, an action-bound approval and arming | **Met** | `tests/test_windows_action_envelope_integrity.py`, each precondition removed individually from an otherwise-identical run |
| 4 | **No bypass, no second registry** — resolvers separate, one device truth, dispatch unreachable without a bound approval regardless of the Identity allowlist | **Met** | same file, plus the pre-existing `test_windows_action_governance.py` allowlist tests |
| 5 | **Recovery** — failed/unknown distinguishable and driving a defined outcome; retry only where idempotent and re-authorised; abandoned lease sweeps honestly | **Met** | `tests/test_windows_action_recovery.py` (60 tests) |
| 6 | **Operator brake reaches the running server** — all action/brake operator commands resolve `--db` through `db_paths` and print the resolved file; the ten stale defaults migrated | **Met** | ten commands migrated; `tests/test_kernel_db_path_resolution.py` green |

### Non-vacuity

Every "it stopped" and "it was verified" assertion has a paired assertion in the
same shape that removes the thing being asserted and shows the answer inverts —
brake clear vs engaged, provider installed vs absent, idempotent vs
non-repeatable, live clearance vs stale. The W03 test contract requires this and
it is the reason the file count is what it is.

### W03 test contract coverage (section 1 and section 5 are W03-C's)

| Contract item | Where |
|---|---|
| 1 · Replay under differing policy state — policy state, not model wording, decides | `test_rewording_a_proposal_does_not_change_the_answer` + `test_changing_the_policy_state_does_change_the_answer` + `test_each_axis_of_policy_state_moves_the_answer_on_its_own` |
| 1 · No bypass (AST, PR Fast) | `test_nothing_outside_the_companion_package_imports_the_windows_machinery`, `test_the_executive_package_reaches_the_os_only_through_the_seam` |
| 1 · Approval binding | pre-existing `test_windows_action_governance.py` (unchanged, still green) |
| 1 · Dispatch unreachable regardless of allowlist | pre-existing, unchanged |
| 5 · Action verification — issued ≠ succeeded, failure distinct from `unknown`, brake aborts in-flight | `test_windows_action_verification.py` + `test_windows_action_stop_after_lease.py` |

---

## 4. The shared contract `action-envelope`

W03-C owns it. The six published callables keep their signatures exactly — W03-B
and W03-E can build against them unchanged. Two additive changes:

1. **One new callable**, `evaluate_action_abort_through_runtime_contract(ctx, *,
   tenant_id, device_id, action_ids) -> ActionAbortSignal`. Additive; no consumer
   is affected by its existence. The manifest's `surface` text now names it.
2. **`ActionSeamResult` gained `recovery: RecoveryPlan | None = None`.** A
   defaulted field, so existing construction and consumption are unaffected.

**For W03-B specifically:** propose through
`run_action_request_through_runtime_contract` exactly as before. Two things are
new and worth using: `ActionSeamResult.recovery` gives you the continue/recover
half of test contract 5 without re-deriving the policy, and a result may now come
back as `ActionResultStatus.ABORTED_BY_BRAKE` — treat it as terminal and never as
a reason to re-propose (`plan_recovery` returns `SURFACE` for it, and that is the
intended behaviour).

**For W03-A specifically:** `bartholomew/actuation/verification.py` is the whole
of what W03-C asks of your read-back. Install a provider with one method:

```python
from bartholomew.actuation.verification import ReadBack, install_read_back_provider

class GovernedReadBack:
    def read_back(self, *, tenant_id, device_id, target, expected=None) -> ReadBack:
        # apply your own consent / brake / identity / scope gates first
        if not permitted:
            return ReadBack.unavailable("no consented capture session covers this target")
        return ReadBack.from_text(observed_text, expected=expected)

install_read_back_provider(GovernedReadBack())
```

`ReadBack.from_text` does the comparison and the digesting inside itself, so the
observed text never leaves your provider. Nothing is installed by default and
nothing in `bartholomew/actuation/` imports `bartholomew/multimodal/`; the
installation point is W03-F's to wire. Until it is wired, verification degrades
to `UNVERIFIABLE` and the device-side read-back carries criterion 1 on its own.

---

## 5. Tests changed, and why

Three pre-existing assertions were **extended, not relaxed**. Each was a pin on a
closed set that W03-C legitimately added one member to, and in each case the
property the pin protects was re-asserted alongside:

| Test | Change | The property it still protects |
|---|---|---|
| `test_a_handler_cannot_report_a_governance_word` | `DEVICE_REPORTABLE_STATUSES` now includes `aborted_by_brake` | `accepted` and `refused` are still un-reportable by a device — asserted unchanged. A device reporting an abort is reporting what it observed (that it declined to run), not borrowing Governance's authority. |
| `test_the_action_channel_client_has_exactly_two_verbs` → `..._three_verbs` | `abort_check` added to the pinned set | The set is still pinned exactly, **and** a new assertion refuses nine names for a wider surface (`execute`, `run`, `post`, `send`, …). A new test, `test_the_abort_response_cannot_start_anything`, proves the third verb can only narrow. |
| `_action()` factory in `test_windows_action_dispatch_results.py` | now carries a live `abort_deadline` | The default is an action whose clearance is current. The stale/missing cases are asserted explicitly in the new suite, in the direction that must fail closed. |

No governance test was skipped, quarantined, disabled or deleted.

---

## 6. Verification run

| Tier / suite | Result |
|---|---|
| Full default suite (`pytest -n auto --dist loadfile`, the PR Fast tier's invocation) | see §6.1 |
| Actuation package suites (governance, prohibitions, capabilities, dispatch/results, channel separation, review regressions, companion completion, device seam brake authority, companion-no-actuation) | green |
| New W03-C suites (verification, stop-after-lease, recovery, envelope integrity) | green |
| `tests/test_kernel_db_path_resolution.py`, `tests/test_cli_governance_and_lock.py` | green |
| Integration tier (`integration.yml`) | see the PR checks |
| Windows job / real-Win32 suite | runs on the Windows runner in the Integration tier; the live `type_text`+verify and brake-abort runs on a real desktop remain a **W03-F** real-world-test acceptance target, per the W03-C contract |

### 6.1 Numbers

| Suite | Result |
|---|---|
| `tests/test_windows_action_verification.py` | 30 passed |
| `tests/test_windows_action_stop_after_lease.py` | 39 passed |
| `tests/test_windows_action_recovery.py` | 60 passed |
| `tests/test_windows_action_envelope_integrity.py` | 24 passed |
| The four together | **153 passed** |
| Full default suite (`pytest -n auto --dist loadfile`) | recorded in §8 |

**One known local-only failure, documented before this session.** Two
`tests/smoke/test_packaging_contract.py::test_declared_console_script_runs_help`
cases fail in a local venv with `FileNotFoundError: 'bartholomew'` — the console
scripts are not on `PATH` when pytest is invoked as `.venv/bin/python -m pytest`.
`docs/waves/W03/W03_CI_BASELINE.md` §6 records exactly this ("the same tests
**pass in CI**; a local editable-install/PATH artifact, not a repo regression").
Verified rather than assumed: re-running that file with the venv's `bin` on
`PATH` gives **9 passed**, so it is the invocation and not the code. CI installs
with `pip install -e .` into the job's own Python, where the scripts are on
`PATH` — which is why the quality job is green on it.

---

## 7. Residual risks and integration notes for W03-F

1. **The stop is cooperative.** This is the honest limitation, not a defect to be
   fixed in integration. A wedged or killed companion is not stopped by the abort
   check. `W03_DEFERRALS.md` #8 (out-of-process emergency stop, D11/S9) remains
   owed, and W03-F should not represent the abort work as satisfying it.

2. **The read-back provider is not installed by anything.** W03-C deliberately
   built the socket and not the plug: `bartholomew/actuation/` must not import
   `bartholomew/multimodal/`. **W03-F should wire `install_read_back_provider()`
   in `bartholomew/integration/install.py`** once W03-A's primitive exists. Until
   then the server-side verdict is always `UNVERIFIABLE`, which is safe — it
   never produces a confirmation — and criterion 1 is carried by the device-side
   read-back regardless.

3. **`abort_check` is a third verb on the action channel.** The channel
   separation argument is unchanged in substance (the observation client still
   has exactly one verb and cannot receive an action), but W03-F re-asserting
   channel separation on the integrated head should expect three verbs, not two,
   and should keep the "no wider surface" assertion that came with it.

4. **An older companion cannot run against this server, by design.** A lease with
   no `abort_deadline` is refused device-side as an expired clearance. Server and
   companion ship together, so this is not a live upgrade concern, but a mixed
   deployment would fail closed rather than fall back to unbounded clearance —
   which is the correct direction and worth stating.

5. **`ActionResultStatus` has eight members and `ActionState` has nine.** Anything
   in W03-B or W03-E that exhausts either enum needs the new member. `succeeded`
   is `False` for it and it is terminal, so a caller that only checks
   `result.succeeded` is already correct.

6. **The `verify_method` vocabulary is closed** (`result.VERIFY_METHODS`). A
   device cannot name its own method. If W03-A's provider needs a third method
   name, that is a change to `result.py` and therefore W03-C's — raise it rather
   than writing free text into the key.

7. **Two `--db` helper blocks are duplicated** between `bartholomew/cli.py` and
   `bartholomew/cli_trust.py` (twelve lines). Deliberate: `cli.py` imports
   `cli_trust.py` to mount `devices`/`groups`/`share`, so sharing through it
   would be an import cycle, and the alternative — a constant in
   `bartholomew/kernel/db_paths.py` — is a module no W03 session owns. If W03-F
   wants it consolidated, that is a one-file integration-only change.

8. **Two pre-existing observations in W03-C-owned files, deliberately not changed.**
   Neither is a W03-C regression, neither blocks a criterion, and changing either
   would be a quiet widening of this package.

   * `routes/actions.py:_arm_brake_engaged()` reads `is_blocked("actuation")`,
     while `seam.evaluate_actuation_brake()` denies on **any** engagement.
     Measured: with only the `voice` scope engaged, `is_blocked("actuation")` is
     `False` while `state().engaged` is `True` — so the channel can be *armed*
     during a halt under which nothing could actually run. It is not a hole
     (arming authorises nothing, and dispatch still refuses fail-closed), but the
     operator surface would report an open channel during a halt, which is the
     same class of misleading safety signal criterion 6 exists to remove. A
     three-line fix; W03-F's call, and best made where the whole integrated brake
     story is being verified.
   * `routes/device_actions.py` calls `store.expire_overdue()` inline in the
     async lease handler, where the other persistence calls in the same file go
     through `run_off_loop`. Pre-existing wave-two code; a blocking SQLite call
     on the event loop under lease load. Not touched because it is unrelated to
     the six criteria.

9. **No contract contradiction was found.** Nothing in the W03-C assignment
   required reinterpreting the architecture, and no escalation boundary was
   reached: no unrestricted shell, no arbitrary code, no destructive access, no
   new autonomy-eligible capability, no out-of-process stop invented, and
   `focus_window`'s foreground-lock limitation did not come up.

---

## 8. Frozen head

| | |
|---|---|
| Branch | `wave/w03-c-governed-windows-action` |
| Final commit | recorded below on freeze |
| Pull request | recorded below on freeze |
| Manifest status | `frozen` |

W03-C is frozen. Per the inherited rules, nothing further is pushed to this
branch without telling W03-F.
