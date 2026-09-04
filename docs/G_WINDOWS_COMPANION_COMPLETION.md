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

2. **Start observation.** Open Notepad first, then, in one window:
   `bartholomew companion observe start --modality screen --display-id 0`
   (a screen session must name exactly one capture scope; `--window-title`
   alone is not one). The command waits. Bartholomew records an ask for you;
   in **another** window:
   `bartholomew consent pending` → shows the ask, its device and modality;
   `bartholomew consent approve <request_id>` → answers it, once.
   Nothing is observed until you do, and the ask expires unanswered after
   180 seconds. The first window then reports the session as started.

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

Engage the brake mid-window (`bartholomew brake on --scope global` -- since
§10.1 this addresses the same database the server reads; the command prints
which file it touched) and
confirm `channel status` immediately reports `armed: false` with
`brake_engaged: true`, and that an approved action is refused — with time still
on the clock.

---

## 9. Live Windows golden path — recorded results (2026-09-04)

The procedure in §8 was executed on a real Windows desktop, interactively, by
the operator. This section records what was **observed**, not what was
expected. Where a target was not met it is marked as such; nothing simulated is
reported as real.

### Environment

Windows 10 19045 · Python 3.12.10 (3.14 was present and deliberately not used;
every dependency, `comtypes` included, installed from pre-built wheels with no
compilation) · repo at `C:\Users\<user>\bartholomew-src` on this branch ·
server on `127.0.0.1:8000`, loopback only (`non_loopback_enabled: False`) ·
`BARTH_RUNTIME_USER_ID` bound to the account · `BARTH_DEVICE_ACTION_AUTH=1`.

Three processes: the API server, the action companion
(`python -m bartholomew.windows_actuation run`), and an operator shell.

### Results against the acceptance targets

| # | Target | Result |
|---|---|---|
| 1 | Enrolment ceremony | **Met.** `pending` → `approved` (ceiling of four capabilities) → `complete` → active. The device reported `approved, not active` until first verified contact, exactly as designed. |
| 2 | Credential OS-protected | **Met.** `WinVaultKeyring` — Windows Credential Manager / DPAPI. `credential show` reported protection without ever returning the secret. |
| 3 | Real enrolled-device authentication | **Met.** The multimodal start returned `tenant_id` and `principal_id` derived by the server from the enrolment row, not from anything the caller sent. The action channel's `POST /api/device-actions/lease` returned `200`. |
| 4 | Real observation start | **NOT MET — blocked.** Refused with `consent_denied`, `"No consent handler registered (fail-closed)"`. See Finding 1. |
| 5 | Channel initially disarmed | **Met.** `armed: false`, with the detail noting that this holds "including actions that are already approved". |
| 6 | Explicit 15-minute arm | **Met.** `armed_at 07:15:02Z` → `expires_at 07:30:02Z`, `seconds_remaining: 900`. |
| 7 | Arming approves no action | **Met.** With the channel armed, requesting an action recorded it at `pending_approval`, `lease_count: 0`, and nothing happened on the desktop. |
| 8 | Governed `windows.type_text` → real keystrokes in Notepad | **Met.** After explicit approval, `Bartholomew live golden path test` physically appeared in Notepad. Notepad's status bar read `Ln 1, Col 34`, matching the recorded `text_length: 33`. |
| 9 | Result honesty | **Met, and notable.** The action was recorded `unknown` / `effect_unverifiable`: *"every keystroke was accepted by Windows; whether the characters landed in the intended field is not observable without reading the field back, which this build does not do"*, with `events_sent: 66`. It did not claim success for an effect it could not observe. The human observation supplied what the machine would not assert. |
| 10 | Audit digest-only | **Met.** Neither the action row nor the result row carried the typed text. Only `text_sha256` and `text_length`. |
| 11 | Typing is never delegable | **Met.** `risk_class: sensitive`, `approval_requirement: always`, `trusted_autonomy_eligible: false` — compared with `focus_window`'s `low` / `required_autonomy_eligible` / `true`. |
| 12 | Parking Brake forces `armed: false` | **Met.** Two ways. Arming under an engaged brake was refused outright. With a *live* window (`seconds_remaining: 861`), status reported `armed: false`, `brake_engaged: true`, and kept the window's real times visible rather than rewriting them. |
| 13 | Learning candidate-only | **Met.** `accepted: 0`, `accepted_competencies: 0`, `auto_acceptance_enabled: false`, `execution_mode: shadow`. Nothing was even proposed. |

`windows.focus_window` was also exercised and **failed** — see Finding 3. Because
of it, **Notepad's focus was established by the human operator, not by
Bartholomew.** The typing result above is real; the focusing step was not
performed by the system.

### Findings

**1 — Observation cannot be started in a headless server deployment.** *(Blocking
for the observation half of this package. Repaired in §10.2.)* `set_consent_handler()` is called in
exactly one non-test place in the repository: `chat.py`, the interactive
terminal front-end. The API server never registers one, so the fail-closed
consent gate — the anti-autonomy enforcement point for Decision 2 — has no
channel to reach a human, and every device observation start refuses. This is
correct fail-closed behaviour and nothing was observed; the gap is that the
package added the authenticated route and the governance path without an
operator-reachable consent channel for a server process. Closing it is design
work (who is asked, through what surface, and what happens when nobody is
present), not configuration.

**2 — The action companion does not use the OS keyring.** It reads its
credential from `BARTH_ACTION_CREDENTIAL_HEADERS`, by design: its config module
reads only `BARTH_ACTION_*` and shares nothing with the observation companion.
The credential therefore lives in that process's environment rather than in
Credential Manager. Acceptable for a local loopback test; worth closing.

**3 — `windows.focus_window` cannot succeed from a background process.**
Windows' foreground lock refuses `SetForegroundWindow` to a process that does
not already own the foreground. Recorded honestly: `permission_denied`,
*"Windows did not give the window the foreground. No keystroke-injection
fallback is attempted"*, with both window handles as evidence. The behaviour is
right — it refused to force focus by synthesising keystrokes — but the
capability is effectively unusable in this deployment shape and the closeout
should say so rather than list it as working.

**4 — `brake on` does not honour `BARTH_DB_PATH`.** *(Safety-relevant. Repaired in §10.1.)* The
server resolves its database through `resolve_db_path()`, which reads
`BARTH_DB_PATH`; the CLI's `--db` defaults to a literal `data/bartholomew.db`
(itself a second mismatch — the server's default basename is `barth.db`). On the
test machine `BARTH_DB_PATH` was set, so `brake on` printed
`⚠ Parking brake ENGAGED` while the running server was entirely unaffected —
twice, before the divergence was found. **The emergency stop appears to work
while doing nothing.** The brake itself is sound; the operator command reaches
the wrong file.

**5 — §8 names a command that does not exist.** It says
`bartholomew brake engage --scope actuation`; the actual verbs are
`brake on` / `brake off` / `brake status`, and they take `--db`.

**6 — Hidden-prompt paste fails in the Windows console.** `devices complete`
prompts for the enrolment secret with hidden input, into which `Ctrl+V` does not
paste; the result is a bare `unknown device credential`, which reads as a bad
secret rather than an input-method problem. Piping the secret on stdin worked.

**7 — `companion observe start` needs a device id it does not ask for.** Without
`--device-id` or `BARTH_COMPANION_DEVICE_ID` the keyring cannot be enumerated,
and the error names only the `--device-id` flag, not the environment variable
most operators will want.

**8 — There is no operator console.** First-time setup required hand-writing two
JSON files, three terminals, environment variables in each, and raw HTTP calls
for requesting and approving actions (no CLI exists for either). Every gate
demanding a human is deliberate; the absence of any surface through which a
human can answer is not.

### What this test did not establish

* No screen, microphone or accessibility observation was performed at all.
* `windows.focus_window` was never observed to succeed.
* Nothing was tested off loopback, and no non-loopback transport was exercised.
* Multi-tenant and cross-tenant refusal paths were not exercised live; they
  remain covered only by the test suite.

---

## 10. Repairs after the live test

Two of §9's findings were repaired in a bounded pass, on the operator's
instruction, before the live retest. Nothing else in this section was changed;
`windows.focus_window` (Finding 3) was explicitly left alone.

### 10.1 The brake command and the server now name the same database (Finding 4)

`bartholomew/kernel/db_paths.py` is the one resolver: an explicit path wins,
then `BARTH_DB_PATH` as-is, then `<project root>/data/barth.db`, read fresh on
every call. The server (`services/api/db.py`), the kernel daemon
(`daemon._default_db_path`) and `brake on/off/status` all delegate to it, so
the brake a person engages from a shell is the brake the server reads -- in
both configurations, variable set or unset. The commands now print the file
they touched and, for `status`, where that path came from.

`--db` still wins unconditionally: tests address per-test databases while a
session-wide `BARTH_DB_PATH` is set, and a per-user runtime sets the variable
for itself. Ten other kernel-database `--db` options (`train`, `say`,
`unattended-report`, `embeddings *`, `share *-local`) carry the same stale
default and were deliberately not widened into this pass; they are listed as
follow-up.

### 10.2 An operator-reachable consent channel for observation (Finding 1)

`bartholomew/multimodal/device_consent.py`. The Runtime Contract's consent
gate (`_resolve_device_consent`) now consults a **separate** device-consent
handler registry (`privacy_guard.set_device_consent_handler`) ahead of the
plain string handler, and the API server installs the channel at startup.
It is a separate registry for one reason that matters: `MemoryStore` branches
on whether the plain handler is `None` to decide whether a sensitive write is
*queued for review* (none) or *asked and discarded* (any handler). Registering
a server-side channel on the shared global would have silently turned every
unanswered memory prompt from "held for you" into "thrown away". Memory
behaviour does not move; only the device seams see the new channel.

One ask: the gate calls `ask()`, which mints a request id and a separate
high-entropy answer nonce, records the pending ask in the kernel database,
and awaits an `asyncio.Future` -- never blocking the event loop, so `status`,
`stop` and `disarm` keep answering while a person decides. A person lists and
answers:

```
bartholomew consent pending
bartholomew consent approve <request_id>      # or: deny
```

The answer resolves that one Future exactly once and nothing is remembered;
the next start attempt asks again. Unanswered asks expire after 180 seconds
and deny. At most three asks may be open per tenant.

Why the companion cannot answer its own ask, given that HTTP identity is
disabled on loopback: the answer route (`POST /api/device-consent/{id}/answer`,
classified `CONSENT_DECIDE`) and the listing route (`SELF_READ`) both refuse
any request carrying the device credential; no response the companion can
receive carries the nonce; and the nonce is written only to the kernel
database, which `bartholomew consent approve` reads on the operator's own
machine and account. Reading that file is what proves the answer is the
person's.

`companion observe start` now waits for the answer (it says so, and names the
commands to run in another window) rather than timing out at 30 seconds.

### 10.3 Hardening from adversarial review, before the retest

Five independent review lenses were run over the two repairs; the findings
that survived verification were fixed and are held by tests:

* **A brake engaged while the person is deciding.** Consent can take
  minutes; the brake read at gate 2 was stale by the time the answer came.
  The multimodal seam now re-reads the brake at the moment of action, after
  consent -- the same discipline an approved Windows action gets at lease.
* **Stop must never be unreachable.** A session parked in
  `awaiting_approval` had no legal edge to `stopped`, so `observe stop`
  during the wait would have raised. It now ends the session `refused`,
  abandons the open ask so the waiting start returns at once, and the start
  path refuses to touch a device for a session that was stopped meanwhile.
* **The per-tenant cap held only for sequential starts.** The count and the
  registration are now one critical section, before anything is awaited.
* **Two concurrent answers could disagree with the record.** Only the answer
  whose database update wins may resolve the waiting start; the other is
  told it was decided concurrently.
* **Unbound single-account servers could never match an ask.** The routes
  filtered by the `local` sentinel while asks carry the enrolment account id.
  With no principal and no runtime binding there is one tenant and no
  filter; with either, the filter is strict.
* Smaller: database marks on the cancel and error paths now run off the event
  loop; the CLI's plaintext guard is a real loopback check rather than a
  string prefix (`http://localhost.example.net` no longer passes); and the
  channel's docstring no longer claims a disconnecting companion abandons
  its ask -- on this stack it does not, and the residual (one human-approved
  start the requester is no longer attached to, bounded by its own expiry
  and stoppable) is stated instead.

Recorded for follow-up, not changed here: pending asks are not pre-denied on
shutdown, so a restart with an open ask burns the graceful-shutdown budget
before denying; and `POST /api/multimodal/sessions` inherits the
`/api/multimodal` admission exemption whose justification ("there is no start
endpoint") this package changed.

### 10.4 What the retest must show

Recorded in §11 once run: `brake on` with no `--db` forcing `armed: false`
against the running server; `observe start` producing a pending ask, the
operator approving it, and a real screen session starting; then the full loop
-- observation running, governed `windows.type_text` approved, keystrokes in
Notepad, and the resulting state observed.

---

## 11. Limitations

* **The live test has now been run; see §9 for what it did and did not
  establish.** Observation was never started (no consent channel exists in a
  server process) and `windows.focus_window` never succeeded (Windows
  foreground lock). The governed `windows.type_text` path was exercised end to
  end on a real desktop.
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

## 12. Deployment and rollback

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

## 13. Statement

Nothing was merged to main. PR #89 remains open, draft and unmerged.
Auto-merge is off on both PRs.
