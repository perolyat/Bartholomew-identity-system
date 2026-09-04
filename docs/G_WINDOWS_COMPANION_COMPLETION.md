# Windows companion completion package

Crosses the single boundary Session F left open: a Windows companion that
authenticates for real, a governed way to begin and end observation, and a
bounded, explicit arming window for real Windows actuation.

Not a wave. Four seams, and nothing else.

---

## 1. Starting state

| | |
|---|---|
| Session F head (verified) | `e3f92565c975d683fd75895395745a95842ab23d` |
| Completion branch | `claude/windows-companion-completion-pkg` |
| Branched from | the Session F head, directly |

Session F's integration assumptions were re-checked and still hold: E is the
single device truth, C's events reach A's ingress, B's actuation is integrated,
D reads E's sharing state, and the action channel is closed by default.

---

## 2. Authentication — the companion proves the machine, never the person

`bartholomew_api_bridge_v0_1/services/api/companion_auth.py`

Verification is Session E's `verify_device_credential`, unchanged: the same
high-entropy secret compared against a stored digest. **No second registry, no
second identity authority, no new secret shape, no plaintext credential
server-side.**

Fails closed on every one of: absent header, blank header, wrong secret,
unknown device, rotated credential, revoked device, disabled device, disabled
account, a credential enrolled under another account, and a registry that
cannot answer. All produce the same 401 with the same wording, so a prober
cannot distinguish them.

**Where the tenant comes from.** Never the request. The process's runtime
binding when it has one — passed to `verify_device_credential` as
`expected_user_id`, so the cross-account check happens at the verification
boundary rather than at whichever call site remembered it — otherwise the
owning account recorded on the device's own enrolment row, which is
server-side data an operator wrote, not a claim travelling with the call.

### The part that matters most

Package C refuses to build a `SessionRequest` whose `principal_id` begins
`companion:`. This package honours that rather than working around it:

* the **device id** comes from the credential;
* the **principal** is the human account the enrolment row names — a real row
  in `platform_accounts`;
* and the Runtime Contract's fourth gate still asks that person,
  interactively and fail-closed, on **every single start**.

Authenticating grants nothing: not observation, not action, not learning
acceptance, not user authority, not trusted autonomy. It answers "which
machine is this" and stops.

---

## 3. Credential storage

`bartholomew/platform/companion_credential.py`

Stored through `keyring`, already a declared dependency and already used for
the memory keyring namespace. On Windows its backend is the **Credential
Manager**, which protects the entry with **DPAPI** under the logged-in user's
profile. No new dependency, no new key material, no new format.

`store()` **raises rather than falling back to a file**: a companion that
believes its credential is protected when it is in plaintext somewhere is
worse off than one that knows the store failed.

`BARTH_COMPANION_CREDENTIAL` is read only when no keyring entry exists, for a
headless run. It is a deliberate downgrade and `describe()` reports it as one —
an environment variable is readable by anything that can read the process's
environment; the Credential Manager entry is not.

`describe()` never returns the secret or any part of it.

---

## 4. Observation start / stop

`POST /api/multimodal/sessions` — the route Package C deliberately did not
ship, now shipped because its blocking premise changed. C's reasoning was that
the API bridge had no authentication; Session E's device credentials are now
reachable here, so the route exists **and requires one**.

Every gate is somebody else's and is reached unchanged:

| Gate | Owner |
|---|---|
| device declares this capability | Session E's registry, via the F resolver |
| Parking Brake, per modality scope | `GovernanceStore` |
| Identity policy decision | `policy_engine`, on the modality's own kind |
| explicit session consent | `privacy_guard`, interactive, **fail-closed** |

A refusal at any gate returns 403 naming the outcome, because a refused
session is an inspectable state rather than an error to swallow.

**Stop** is unchanged and deliberately needs no credential: the worst an
unauthenticated stop can do is end a session the person could have ended
anyway. It stays reachable under a Parking Brake and during the admission
window, because a control that removes authority must never be the one that
jams.

### No autonomous observation was introduced

The consent gate is the enforcement point, and it grants a *single start
attempt* — never continuing access. A companion holding a valid credential can
**ask** to observe; it cannot answer for the person. With no consent handler
registered, a start is refused (`consent_denied`), which is asserted by test.

---

## 5. The Windows action channel — arming

`bartholomew/actuation/arming.py`, gated in
`seam.evaluate_dispatch_admission`.

**Arming is not approval.** An armed channel authorises nothing: every action
still travels B's whole governed path and needs its own explicit,
content-bound approval. Disarmed, an approved action does not run; armed, an
unapproved one does not. Both are required and neither substitutes for the
other — proved by three tests, one for each combination.

| Property | How |
|---|---|
| default | **disarmed** |
| window | 15 minutes, and `MAX_ARM_SECONDS` caps any longer request |
| bound to | one tenant, one named device; arming the desk PC does not arm the laptop |
| expiry | evaluated on read, so a window is never briefly usable after it lapses |
| restart | **fail-closed** — the window is in-process and is not persisted |
| brake | read *before* the window in dispatch admission, so time left never matters |

Restart behaviour is deliberate, not a gap: a crash at minute two of fifteen
must not hand the next process thirteen minutes of authority nobody
re-granted.

### Arming requires

authenticated enrolled device · device not revoked or disabled · device
declares Windows actuation · device belongs to the server-derived tenant · the
body cannot name a different device · Parking Brake clear (409 if engaged, and
an unreadable brake also refuses) · explicit request.

**Disarm** needs no credential and is never refused by the brake.

---

## 6. Authorization model

| Route | Capability |
|---|---|
| `POST /api/multimodal/sessions` | `MULTIMODAL_SESSION_START` (new) |
| `POST /api/actions/channel/arm` | `ACTION_ARM` (new) |
| `GET /api/actions/channel` | `SELF_READ` |
| `POST /api/actions/channel/disarm` | `BRAKE_ENGAGE` |

`ACTION_ARM` is deliberately **not** implied by `ACTION_APPROVE`: a surface
that may approve must not thereby be able to open the channel. Disarming is
classified with the brake because it is strictly tightening.

No brake scope was added. No Identity allowlist entry was added — arming is an
operator control-plane act like engaging the brake, not something Bartholomew
does, and `learning_accept`, `share_accept` and any action-approve standing
grant remain absent.

No route reads `tenant_id` or `principal_id` from a body. Both request models
omit the fields entirely, so there is nothing to ignore.

---

## 7. Tests

`tests/test_windows_companion_completion.py` — 39 tests, against real stores.

Authentication: correct credential succeeds · missing/blank/wrong/oversized
fail identically · revoked fails · disabled fails · cross-account fails ·
authentication alone grants no capability.

Observation: unauthenticated start refused (401) · wrong credential refused ·
undeclared modality refused truthfully, naming the capability · **no consent
handler ⇒ `consent_denied`** · session principal is the person, never the
companion · stop needs no credential and is idempotent.

Arming: disarmed by default · capped at 15 minutes · expired window fails
closed · one device only · does not cross accounts · explicit disarm ·
**restart cannot leave a machine armed** · unauthenticated arm refused ·
revoked cannot arm · incapable device cannot arm · body cannot name another
device · brake prevents arming (409) · brake overrides an armed channel ·
disarm reachable under a brake.

Both gates: armed-but-unapproved refused · approved-but-disarmed refused ·
approved + armed permitted · brake beats both · expired window stops a
previously permitted action · a successful action does not become accepted
learning.

### Existing tests that changed, and why

Three files encoded a premise this package's authorized decisions changed.
None was weakened, skipped or disabled:

* `test_windows_action_governance.py`, `test_windows_action_review_regressions.py`
  — dispatch now also requires an armed channel. An autouse fixture opens one
  for the combinations those suites dispatch with, so arming never becomes the
  reason any assertion passes or fails and each test still discriminates
  exactly what it did before. The unarmed cases are tested on their own.
* `test_multimodal_governance.py::test_the_api_exposes_no_start_endpoint` —
  asserted that no start route existed, standing in for "capture initiation is
  not reachable unauthenticated". The route now exists by explicit decision, so
  the test asserts the underlying property **directly and empirically**: an
  unauthenticated `POST` gets 401 against the real app. Strictly stronger than
  what it replaced.
* `test_session_f_golden_path.py` — Session F's "stop 1" recorded that no
  production start surface existed; it now asserts the surface exists and is
  authenticated. The golden path also gained the two arming stages: the channel
  is asserted **disarmed**, an approved action is refused on it, and only then
  is it armed.

---

## 8. Live Windows golden path — operator procedure

**This has not been executed.** The environment available to this session is
Linux with no interactive desktop, so no claim of a live run is made. CI's
Win32 job is not evidence of this and is not offered as any.

Everything below is verified except the physical keystroke.

### Prerequisites

An interactive Windows desktop, logged in, with Bartholomew installed
(`deploy/windows/bartholomew-action.ps1 install`).

### One-time: enrol this machine

On the Bartholomew side:

```
bartholomew devices enrol --name desk-pc --platform windows
bartholomew devices approve --device-id <DEVICE_ID> --approver <you>
```

Complete enrolment from the companion with the one-time secret. The device
credential is printed **once**. Store it immediately:

```
bartholomew companion credential store --device-id <DEVICE_ID>
# paste the credential at the prompt; it is read from stdin, never argv
bartholomew companion credential show --device-id <DEVICE_ID>
# expect: "source": "os_keyring", protection naming Credential Manager / DPAPI
```

The device's manifest must declare `windows.focus_window`,
`windows.type_text` and `multimodal.screen_capture` at version 1.

### Open the two channels the deployment gates

```
set BARTH_DEVICE_ACTION_AUTH=1          # the production action-channel resolver
set BARTH_ACTION_PARAMETER_ALLOWLIST=%LOCALAPPDATA%\Bartholomew\allowlist.json
```

`allowlist.json` must map `notepad` to `C:\Windows\System32\notepad.exe`.
Without it every parameter is refused — empty allowlists permit nothing.

Start Bartholomew, then the companion:

```
deploy\windows\bartholomew-action.ps1 start
```

with `BARTH_ACTION_CREDENTIAL_HEADERS=X-Bartholomew-Device-Credential: <credential>`
in `companion.env`.

### The test

1. **Confirm the channel starts disarmed.**
   `bartholomew companion channel status` → `"armed": false`.

2. **Start observation.** Open Notepad first, then:
   `bartholomew companion observe start --modality screen --window-title Notepad`
   Bartholomew asks you to confirm. **Answer it.** Nothing is observed until
   you do.

3. **Confirm a real observation arrived.**
   `bartholomew companion observe status` → the session is live and names the
   window. Then check the event landed in the one ingress:
   `sqlite3 %LOCALAPPDATA%\Bartholomew\data\bartholomew.db "select event_type, received_at from inbound_events order by id desc limit 5;"`
   Expect `multimodal.screen.observation` or
   `multimodal.accessibility.observation`.

4. **Arm the channel for fifteen minutes.**
   `bartholomew companion channel arm --reason "live windows test"`
   → `"armed": true`, `seconds_remaining` at most 900.

5. **Focus Notepad through the governed path.**
   ```
   curl -X POST http://127.0.0.1:8000/api/actions -H "Content-Type: application/json" ^
     -d "{\"device_id\":\"<DEVICE_ID>\",\"capability\":\"windows.focus_window\",\"capability_version\":1,\"parameters\":{\"app_id\":\"notepad\"}}"
   ```
   → `pending_approval`. Approve it:
   `curl -X POST http://127.0.0.1:8000/api/actions/<ACTION_ID>/approve`
   The companion leases and runs it. **Notepad comes to the foreground.**

6. **Propose the typing action** with a unique string:
   ```
   curl -X POST http://127.0.0.1:8000/api/actions -H "Content-Type: application/json" ^
     -d "{\"device_id\":\"<DEVICE_ID>\",\"capability\":\"windows.type_text\",\"capability_version\":1,\"parameters\":{\"text\":\"Bartholomew live Windows test - 2026-09-04T12:00:00Z\"}}"
   ```

7. **Confirm it does not run unapproved.** Wait one poll interval. Nothing is
   typed. `GET /api/actions/<ACTION_ID>` still reads `pending_approval`.

8. **Approve it**, then watch the desktop:
   `curl -X POST http://127.0.0.1:8000/api/actions/<ACTION_ID>/approve`
   **The string appears in Notepad.** This is the step no mock, adapter or CI
   job can stand in for.

9. **Observe the changed state.** `bartholomew companion observe status`, and
   a fresh `multimodal.*` row in `inbound_events`.

10. **Confirm the audit.** `GET /api/actions/<ACTION_ID>` → a result row with
    the outcome, and the typed text present only as a digest and a length,
    never as itself.

11. **Confirm learning stayed a candidate.**
    `GET /api/learning/candidates` → nothing accepted; `execution_mode` is
    `shadow`.

12. **Close both.**
    ```
    bartholomew companion channel disarm
    bartholomew companion observe stop
    ```
    Re-propose and approve any action → it is refused, "not armed". Nothing
    more can run.

### Negative check worth doing while you are there

Engage the brake mid-window (`bartholomew brake engage --scope actuation`) and
confirm `channel status` immediately reports `armed: false` with
`brake_engaged: true`, and that an approved action is refused — with time still
on the clock.

---

## 9. Limitations

* **The live test above has not been run.** No interactive Windows desktop was
  available to this session.
* **Anti-autonomy rests on the consent gate.** A deployment that registers a
  permissive auto-approving consent handler would let an authenticated
  companion start observing without a person answering. The gate is
  fail-closed and interactive by design; the residual risk is a deployment
  choosing to defeat it, and it is worth a future assertion that the registered
  handler is genuinely interactive.
* **Arming is per tenant, one device at a time.** Arming a second device
  replaces the first rather than accumulating. Two machines at once is not a
  state this package supports.
* **The window is not persisted**, so a Bartholomew restart mid-window closes
  the channel. Deliberate.
* **No autonomous observation policy exists**, by instruction. Every session is
  started by an explicit request and consented to individually.

---

## 10. Deployment and rollback

Inert by default. The action-channel resolver stays behind
`BARTH_DEVICE_ACTION_AUTH`; the channel additionally stays disarmed until
somebody arms it; observation additionally requires a credential *and* a
person answering. Three independent things must happen before this machine can
be acted upon, and none of them is a default.

**Rollback** is reverting the branch. No schema migration is introduced: the
arming window is in-process and the credential lives in the OS keyring, which
`bartholomew companion credential forget` clears. Reverting restores Session
F's behaviour exactly — no start route, and dispatch without an arming gate.

---

## 11. Statement

Nothing was merged to main. PR #89 remains open, draft and unmerged.
Auto-merge is off on both PRs.
