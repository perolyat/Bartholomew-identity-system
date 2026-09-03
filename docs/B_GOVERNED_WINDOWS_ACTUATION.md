# Governed Windows actuation (Session B)

Bartholomew could observe a PC. It can now act on one — narrowly, one approved
action at a time, through a boundary that is a different trust channel from the
observation one and cannot be reached from it.

This document is the contract. `deploy/windows/README.md` is how to run it.

---

## 1. The shape, and why it is two packages

```
bartholomew/companion/          observation only. UNTOUCHED by this work.
                                one verb, one direction, no actuation code.

bartholomew/actuation/          the DECIDING half. Runs on the Bartholomew host.
                                models, governance, approval binding, storage.
                                No ctypes, no window handle, no keystroke.

bartholomew/windows_actuation/  the ACTING half. Runs on the person's PC.
                                channel, dispatcher, nine handlers, one Win32 file.
```

Three packages, and none of them imports another. That is asserted, in both
directions, over the module graph:
`tests/test_windows_action_channel_separation.py`.

The split between the deciding half and the acting half is what lets two
separate things be argued separately: *the server cannot be tricked into
permitting an action*, and *the device cannot act unbidden*. Putting the
decision and the mechanism in one package would make those one argument, and a
weaker one.

The split between observation and actuation is the older and more important
one. The observation client has a single `submit` verb returning three scalars;
there is nowhere in it for a command to land, which
`tests/test_companion_no_actuation.py` has proven against a hostile server since
Session D and still proves. This work added nothing to that package and
subtracted nothing from that test.

## 2. The capability vocabulary

Nine kinds, a closed `Enum`, version 1. A capability Bartholomew does not have
is not unimplemented — it is inexpressible.

| Capability | Risk | Approval | Autonomy-eligible | May be idempotent |
|---|---|---|---|---|
| `windows.open_url` | moderate | required | no | no |
| `windows.open_path` | moderate | required | no | no |
| `windows.launch_app` | moderate | required | **yes** | no |
| `windows.focus_window` | low | required | **yes** | **yes** |
| `windows.manage_window` | low | required | **yes** | **yes** |
| `windows.clipboard_read` | high | **always** | never | no |
| `windows.clipboard_write` | high | required | no | no |
| `windows.type_text` | sensitive | **always** | never | no |
| `windows.accessibility_action` | sensitive | **always** | never | no |

*Required* means an approval is needed today. *Always* means no configuration
can remove it: `devices.EnrolledDevice` refuses at construction to carry one of
those three in a trusted-autonomy set. *Autonomy-eligible* means an explicit
per-device enrolment change can grant standing permission; the set is empty by
default.

`open_url`, `open_path` and `clipboard_write` are deliberately not
autonomy-eligible in this build. Each was described as lowering its risk only
once a further control exists; encoding eligibility before the control would be
encoding an intention as a permission.

**Idempotence is a property of the capability, not a field on the wire.** Only
the two pure state-setting capabilities qualify: focusing an already-focused
window changes nothing the second time, and neither does maximising an
already-maximised one. Everything else runs at most once, whatever a request
asks for, and a request that asks for idempotence on an ineligible capability
is refused rather than quietly downgraded. The reason is that `idempotent`
relaxes the server's one-lease guard *and* is what the device's durable ledger
checks before refusing a repeat -- a caller who could set it on
`windows.type_text` could have one human approval type the text twice.

**Unknown kinds and versions are refused, never approximated.** A device
declaring `windows.focus_window` v2 against an action naming v1 is refused with
that reason, not served a best-effort translation
(`bartholomew/actuation/capabilities.py:require_supported`).

## 3. The parameters, and the absence of an escape hatch

Every capability has exactly one validator, each builds its result key-by-key
from a fixed tuple, and a key the capability does not name is **refused, not
ignored**. The complete wire vocabulary across all nine is ten names:

    url  path  app_id  operation  x  y  width  height  text  element_name

There is no `command`, `cmd`, `script`, `shell`, `args`, `argv`, `executable`,
`code`, `env` or `cwd` anywhere in it, and
`tests/test_windows_action_prohibitions.py` asserts the union against a
literal list and tries every forbidden name against every capability.

Specific refusals worth naming:

* **`launch_app` takes an allowlist key, never a path.** The executable comes
  from operator configuration. `win32.start_process()` takes exactly one
  parameter and calls `CreateProcessW` with `lpCommandLine = NULL` — asserted
  by `inspect.signature` and by an AST check on the single call site.
  `CreateProcessW` rather than `ShellExecuteW` precisely because the latter
  resolves a verb through the registry and accepts a parameter string.
* **`open_path` refuses every executable and script extension** — a `.bat`
  opened with the shell *runs*. The path is fully resolved (symlinks followed)
  before it is compared against the allowlisted roots, and re-checked after
  resolution.
* **`open_url` permits http and https only.** `file:` is a filesystem read
  wearing a URL's clothes; `javascript:` is code; a custom scheme runs whatever
  program registered it. Embedded credentials are refused. The host must match
  an allowlisted domain on **label boundaries**, so `example.com.attacker.net`
  does not match `example.com`.
* **`type_text` and `clipboard_write` refuse every control character**,
  newline and tab included. That is what makes "cannot press Send" structural:
  there is no Enter to type, and `win32.send_unicode_text()` sends
  `KEYEVENTF_UNICODE` events with `wVk = 0`, which carry no virtual-key code at
  all. There is no mouse function anywhere in the package.
* **`accessibility_action` has no `invoke`.** Expand, collapse, scroll and
  focus change what is *visible*; invoking a control is how Send, Submit,
  Confirm, Purchase and Delete are pressed. An element *named* like a final
  action is also refused.

### Validated twice, on purpose

The server validates before it stores or dispatches; the device validates again
before it touches the OS. They are not the same check — the server cannot stat a
file on someone else's disk, and the device does not know which principal
approved anything. Both must pass, so the stricter allowlist always wins and
neither side relies on the other's diligence.

The server's half runs on a Linux host validating Windows-syntax paths, so path
parsing is done in the path's *own* syntax rather than the host's
(`allowlists.is_absolute_path`, `normalise_path`). `os.path` would answer for
the wrong machine.

## 4. Governance: the eleven checks, in order

`bartholomew/actuation/seam.py`. Every action passes all of them, and passes
them again immediately before dispatch.

| # | Check | Authority |
|---|---|---|
| 1 | tenant | resolved by the API boundary from the platform's authority |
| 2 | requesting principal | ditto |
| 3 | enrolled target device | `devices.DeviceCapabilityRegistry` |
| 4 | declared capability + version | `EnrolledDevice.declares()` |
| 5 | typed canonical parameters | `parameters.validate()` |
| 6 | risk classification | `capabilities.describe()` |
| 7 | expiry | `ActionRequest.has_expired()` |
| 8 | Parking Brake | `governance_store`, both tiers, fail-closed |
| 9 | Identity policy | `policy_engine.evaluate_tool_policy` |
| 10 | exact action approval | `ActionApproval.authorizes()` |
| 11 | replay / idempotency | `store.try_lease()`, a conditional `UPDATE` |

**Missing, stale, mismatched or unreadable state denies.** There is no branch in
the seam that treats "we could not tell" as "go ahead": an unreadable brake, an
unreachable registry, an unparseable expiry and an unreadable approval all
refuse.

### The Parking Brake, read twice

Once at admission, and again immediately before the lease. **An approval never
overrides the brake**, and the second read is what makes that operational rather
than aspirational — an approval granted while the brake was clear does not
survive it being engaged afterwards.

Two reads compose two things:

* `engaged_state_fail_closed_off_loop()` — the brake engaged **at all**, either
  tier. Any engagement, in any scope, stops actuation. That is the most
  restrictive reading available and it is deliberate: acting on somebody's
  computer while any part of Bartholomew is halted is exactly what a halt is
  for.
* `is_blocked_fail_closed_off_loop("actuation", …)` — the new `actuation`
  scope, so an operator can halt actuation *alone*. Registered in
  `platform/authority.py` and in the governance route's allowlist, so it is
  engageable from the API, the UI and the CLI.

Reading the action list stays permitted under a halt: a halt that hides what
Bartholomew was about to do defeats the purpose of halting.

### Identity policy

`windows_action_request` and `windows_action_cancel` are allowlisted in
`Identity.yaml`. They authorise *asking* and *withdrawing* — no keystroke, no
launched program, no opened file.

`windows_action_dispatch` is **deliberately absent, and adding it would not make
dispatch reachable**: `evaluate_dispatch_admission()` requires a bound approval
regardless of the allowlist. There is no "actuation enabled" switch to find.
This follows the precedent recorded for `learning_accept`.

### The approval, and what it cannot authorise

An `ActionApproval` binds to six facts plus an expiry and an approver:

| bound to | so it cannot authorise |
|---|---|
| `action_id` | another action, including a re-request |
| `tenant_id` | another tenant |
| `device_id` | another device |
| `capability` | another capability |
| `capability_version` | the same capability under a different contract |
| `parameter_fingerprint` | the same action with **any** parameter changed |
| `expires_at` | execution after it lapses |
| `approver` | anonymous authorisation |

Each is checked independently and reports its own code, so an audit can tell
"nobody approved this" from "the parameters changed after it was approved" from
"that approval was for a different device". The approval is built *from* the
stored action, so an approver cannot approve parameters they never saw, and a
caller cannot supply the fingerprint it wants approved.

It is written through `MemoryStore.upsert_memory()` under the
`windows_action_approval` kind — the same authority and the same shape the
learning-acceptance approval uses. No second store, no second audit log.

### Replay

The state machine is a single conditional `UPDATE ... WHERE state = <believed>`,
and a `rowcount` of zero is a refusal. Two concurrent leases race one statement
and exactly one wins. A non-repeatable action moves to `leased` exactly once;
an idempotent one may be re-leased up to three times, which bounds a redelivery
loop rather than permitting one.

The device keeps its own durable ledger as well, because the two sides can
disagree: a companion that executed an action, crashed before reporting, and
restarted would otherwise run it again while the server still saw it as leased.
An unreadable ledger refuses every non-repeatable action rather than starting
empty — an unreadable ledger is not an empty one.

## 5. Results: success, failure, and unknown

Seven statuses. The one that matters is `unknown`.

A handler may report `succeeded` only having **observed** its effect:

| Capability | The observation |
|---|---|
| `launch_app` | a real pid, still alive, whose image is the allowlisted executable |
| `focus_window` | `GetForegroundWindow()` returns the window we asked for |
| `manage_window` | `IsIconic` / `IsZoomed` / `GetWindowRect` read back |
| `clipboard_write` | the clipboard read back and compared |
| `clipboard_read` | the read itself |
| `accessibility_action` | the adapter's own confirmation |
| `open_url`, `open_path` | **none available — always `unknown`** |
| `type_text` | **none available — `unknown` on a full send** |

`open_url` reports `unknown` even on success because `ShellExecuteW` returning
above 32 means Windows handed the URL to a handler, not that a page loaded.
`type_text` reports `unknown` on a fully-accepted send because whether the
characters landed in the intended field cannot be read back without reading the
field's contents, which this build does not do. Both are the honest answer, and
accepting them costs nothing next to a confidently wrong log.

A handler that raises becomes `unknown`, not `failed`: a handler that died
part-way genuinely may or may not have had an effect.

A device may report `started`, `succeeded`, `failed`, `cancelled` or `unknown`.
It may **not** report `accepted` or `refused` — those are Governance's words
about its own decision.

### Expiry, and what an abandoned lease becomes

Two sweeps, because the two situations are genuinely different. An action past
its expiry that was never dispatched becomes `cancelled` -- nothing ran, and
the dispatch path would have refused it anyway. An action that a device
*leased* gets a grace period to report in, and what it becomes afterwards is
`unknown`, not `cancelled`, because that is what is true: the device took it
and we never heard back. Sweeping a live lease the instant its window closed
cancelled actions underneath the devices running them, and then declined the
honest results those devices reported as late.

Both sweeps purge `parameters_json`, and the sweep runs on the request and
inspection paths as well as the lease path -- it used to run only on the lease
path, which a deployment with no device resolver installed (the shipped
default) never reaches.

### Evidence

Bounded and non-sensitive, through the existing authorities:

* one `ActionReflection` per governed decision, through
  `record_action_reflection` — the same sink every other seam uses;
* a typed row in `windows_action_results` carrying an error **category** (not
  prose) and at most twelve bounded evidence values, filtered through a **key
  allowlist**. The allowlist runs on the server over whatever a device sent, so
  it is a boundary and not a convenience: a compromised device cannot put screen
  contents into a permanent row by inventing a key for them. `text` is the
  single content-bearing name on it.

The Reflection carries the evidence *keys*, never the values:
`ActionReflection` redacts top-level strings in its details and a nested dict
passes through untouched, so putting the evidence map there would have written
whatever a device sent straight into Memory unredacted.

Sensitive parameters never reach either. `ValidatedParameters` carries a
`canonical` view and a `redacted` view; text somebody asked to have typed is a
SHA-256 digest and a length in the redacted one, and only the redacted one
reaches a list endpoint, a Reflection or an evidence row. The one transient
copy — `parameters_json`, which exists because the device has to be handed it —
is **purged when the action reaches a terminal state**.

Clipboard *content* does not leave the machine at all by default: the result
carries a digest, a length and whether the secret detector fired. Returning the
content is an explicit per-device opt-in, and a detected secret is refused
either way.

**The one exception, and it is deliberate: `GET /api/actions/{id}` returns the
canonical parameters.** That is the approval surface, and a person cannot
approve text they have not read -- an approval bound to a digest the approver
never saw expanded is an approval in name only. The disclosure is transient, to
one authenticated request holding `action:read`; every durable record still
keeps only the digest, and the list endpoint returns the redacted view.

## 6. Sensitive content and sensitive fields

Two different questions, deliberately separated
(`bartholomew/actuation/sensitive.py`):

* **`detect_secrets(text)`** — does this text contain credential material?
  Private keys, cloud keys, tokens, JWTs, bearer headers, connection strings,
  labelled secrets, BIP-39-shaped phrases, high-entropy blobs, and Luhn-valid
  card numbers. Biased towards false positives on purpose: refusing to type a
  string that merely looks like an API key costs one retyped sentence.
  A `SecretFinding` carries a **category and an offset, never the matched
  text** — a detector that logged what it caught would be a second copy of it.
* **`sensitive_field_reasons(...)`** — is the place we are about to type a
  password, PIN, token or payment field? Answered from the accessibility tree,
  not guessed. **`is_password=None` is itself a reason to refuse**: a companion
  that cannot see where it is typing does not type.

That last point is why `comtypes` is a hard requirement for `type_text` rather
than a nicety. Without the `windows` extra, `type_text` and
`accessibility_action` refuse; the other seven work.

## 7. Structural separation, proved four ways

`tests/test_windows_action_channel_separation.py`:

1. **The import graph** — neither package can name the other, transitively.
   Importing the observation companion pulls in nothing named `actuation`.
2. **The observation client's shape** — still one verb, still three scalars.
3. **A hostile server** — a real HTTP server answers every observation with
   every shape of actuation instruction (`actions`, `capability`, `command`,
   `execute`, `next_poll_url`) while the dispatcher and all nine handlers are
   replaced with tripwires. Nothing is called, and none of the poison appears in
   the companion's durable state.
4. **Two resolvers, two globals** — installing the inbound observation resolver
   leaves the action channel closed, and vice versa. At the HTTP layer, an
   observation credential gets a 401 from the lease endpoint.

## 8. The API surface

Two routers, because they are two trust channels.

| Route | Capability | What it does |
|---|---|---|
| `POST /api/actions` | `action:request` | Record a pending action. Runs nothing. |
| `GET /api/actions` | `action:read` | Inspection. Redacted parameters. |
| `GET /api/actions/{id}` | `action:read` | One action and its results. |
| `POST /api/actions/{id}/approve` | `action:approve` | Bind one approval. |
| `POST /api/actions/{id}/cancel` | `action:request` | Withdraw. |
| `POST /api/device-actions/lease` | `device_action:channel` | The device collects work. |
| `POST /api/device-actions/{id}/result` | `device_action:channel` | The device reports. |

`action:approve` is a separate capability from `action:request` so that a
surface which may ask for work cannot also authorise it.

The device channel is authenticated by `device_action_auth`, which mirrors
`inbound_auth`'s three rules exactly: default deny, local-peer status is
reachability rather than authority, and test-only auth cannot enable itself
(two independent environment variables, a warning at startup, and a
`verified_by` stamp on every row).

**A device cannot choose its tenant.** `VerifiedDevice` has no `tenant_id`, and
`resolved_tenant_id()` reads only the verified principal and this process's
runtime binding. A resolver that claims a tenant is ignored, and a test proves
it rather than merely asserting the field is absent.

## 9. Actual versus simulated verification

Stated separately, because the difference matters.

### Verified on real Windows, with nothing substituted

`tests/integration/test_windows_action_real.py` runs on the `windows-latest` CI
job (added to `.github/workflows/ci.yml`) and monkeypatches nothing. Real
`CreateProcessW`, real `EnumWindows`, real `SetForegroundWindow`, real
clipboard, real filesystem. **It has run and passed: 21/21 on a Windows Server
runner**, with the job's cleanup terminating the real Notepad and Edge
processes the tests started -- which is the most direct evidence available that
these are not simulations.

* launching Notepad, and confirming a real pid whose image is the allowlisted
  executable, with a real window;
* refusing a non-allowlisted application;
* focusing a real window and reading `GetForegroundWindow()` back;
* minimise / maximise / restore, each verified by reading the state back;
* a real move and a real resize landing exactly where asked;
* a move off-screen being clamped onto the real virtual desktop;
* opening a real URL and a real file, and reporting `unknown` — and the file
  being byte-for-byte unchanged afterwards;
* refusing a real `.bat` inside an allowlisted root, and a real path outside
  the roots;
* a real clipboard round trip, with the content **not** returned by default;
* a real clipboard holding an AWS key being refused rather than returned;
* refusing to write a secret, with the clipboard demonstrably untouched;
* `type_text` refusing when the accessibility adapter is unavailable, and
  refusing a newline.

### Verified through simulation, and why

The Win32 layer is substituted in
`tests/test_windows_action_dispatch_results.py` only to reach conditions a real
machine will not produce on demand: `SetForegroundWindow` returning false, four
windows of one application and then none, a process that exits immediately, a
clipboard write that does not stick, a partial `SendInput`, a handler that
raises. The dispatcher, the four device-side checks, the handlers' verification
logic, the ledger and the runner are all real in those tests.

### Not verified automatically, and honestly so

**`windows.type_text` on its accepted path** — typing into a real focused field
and confirming the characters arrived — is not automated. Confirming it would
mean reading the field's contents back, which is screen reading and which this
build does not do. It has been reasoned through and is exercised for its
refusals; an operator wanting assurance should watch one approved action on a
real desktop before enabling the capability.

**`windows.accessibility_action`** needs `comtypes` and a real UI Automation
provider. Its parameter validation, governance and refusal paths are fully
tested; its accepted path against a live provider is manual.

**One documented Win32 caveat that the real run contradicts.**
`OpenClipboard(NULL)` followed by `EmptyClipboard()` is documented to leave the
clipboard owner NULL, "which causes `SetClipboardData` to fail" -- which is why
some clipboard libraries create a throwaway window first. The real-Windows run
round-trips the clipboard successfully, so this build does not add the window.
Documentation and observed behaviour disagree, the test is the stronger
evidence for the platform we run on, and it is also the guard: if a future
Windows build behaves as documented, that test goes red rather than the
capability quietly failing for a person. Recorded here because a reader who
knows the documentation deserves to know the tension was noticed.

**The Windows installer is parsed, never run, in CI.** There is no PowerShell
on the Ubuntu jobs, so a parse error in an install script otherwise surfaces
only when somebody runs it -- and one shipped:
`if (Test-Installed -and (...))` parses as a command invocation, so `status`
died on any machine where the companion was installed but not configured. The
Windows job now parses the script with PowerShell's own parser and checks the
two properties it promises: that invoking it with no verb changes nothing, and
that `status` reports rather than throwing. It installs nothing.

## 10. Security limitations this build does not close

* **The device credential is a bearer token.** No device key material, no
  signature over the request, no replay window on the credential itself. A
  stolen token is a stolen device identity until it is rotated. The allowlists
  bound what that is worth, which is why they are required.
* **The interim registry is a file a human wrote.** There is no enrolment
  ceremony, no attestation and no revocation list beyond editing the file. This
  is Session E's to replace; see §11.
* **A compromised server holding a valid approval can direct the companion
  within its allowlists.** The device re-validates every parameter against its
  own allowlists and refuses four ways before acting, so the blast radius is
  exactly the allowlists — but it is not zero. Keep them narrow.
* **`unknown` really is unknown.** For `open_url`, `open_path` and a fully-sent
  `type_text`, Bartholomew genuinely does not know whether the effect happened.
  Do not build anything that reads `unknown` as either success or failure.
* **The single-tenant sentinel.** On a loopback install with no runtime binding,
  the tenant is the literal string `local`. That is correct for one person on
  one machine and is not a multi-tenant boundary; a real deployment must bind
  `BARTH_RUNTIME_USER_ID`.
* **No transport pinning.** The companion trusts the platform's TLS
  configuration; it does not pin a certificate.
* **Typed and copied text is Basic-Multilingual-Plane only.** Windows carries a
  typed character in a 16-bit field, and some characters above U+FFFF truncate
  into control codes -- U+1000D into Enter, U+10009 into Tab. Rather than emit
  surrogate pairs, this build refuses the plane: a capability whose whole point
  is that it cannot reach a control must not also be where the subtlest
  encoding bug in the codebase lives. Emoji in typed or copied text is refused,
  as is an unpaired surrogate, which is not a character at all.
* **Length limits apply to the normalised string.** Unicode normalisation can
  *lengthen* text -- U+FB2C becomes three characters -- so a bound on the input
  is not a bound on what gets sent, and padding that expanded past the secret
  detector's scan window was a way past the detector. Both are now measured
  after normalisation, and the detector refuses over-long input rather than
  scanning a prefix of it.

## 11. The Session E interface

`bartholomew/actuation/devices.py` defines the whole of what Package B asks of a
device registry:

```python
class DeviceCapabilityRegistry(Protocol):
    def lookup(self, *, tenant_id: str, device_id: str) -> EnrolledDevice | None: ...
```

Structural, not nominal, so Session E's registry satisfies it by having the
method. `EnrolledDevice` carries everything governance reads: `tenant_id`,
`platform`, `enrolled`, `capabilities` (kind **and** version), the three
allowlists, and `trusted_autonomy`.

Three behaviours Session E must preserve:

1. `lookup` is **tenant-qualified**. A device id known in one tenant must be
   `None` in another; that is the cross-tenant containment at this layer.
2. A revoked device returns `EnrolledDevice(enrolled=False)`, **not** `None`, so
   the refusal reason can be truthful.
3. A `lookup` that raises is treated as a denial. Fail closed.

Install it with `devices.install_registry(...)`. `StaticDeviceRegistry` — the
file-backed interim one — and `NoDeviceRegistry` both report `interim: True` and
`replaced_by: "Session E device/group registry"` from `describe()`, so nobody
has to guess which is running.

## 12. What Session F must connect

Package B implements against frozen logical interfaces and connects to no
unfinished work. Four seams are left deliberately open:

1. **Event → action.** Nothing in this package listens to Session A's event
   stream. When it lands, F calls
   `seam.run_action_request_through_runtime_contract(...)` with a
   `causation_id` naming the event. The seam already carries `correlation_id`
   and `causation_id` through to the durable row and every Reflection.
2. **The device registry.** Replace `StaticDeviceRegistry` with Session E's, via
   `devices.install_registry()`. No call site changes; see §11.
3. **The device resolver.** Replace the fail-closed default with the control
   plane's, via `device_action_auth.install_resolver()` at startup — exactly
   where `inbound_auth.install_resolver()` goes. Until then the channel is
   closed and nothing dispatches, which is the correct default.
4. **The approval surface.** `POST /api/actions/{id}/approve` exists and works;
   nothing surfaces a pending action to a person yet. A UI or notification
   should read `GET /api/actions` (which returns the redacted parameters plus
   the capability descriptor's `summary`) and call the approve endpoint. It must
   show the approver the **canonical** parameters, which is why
   `to_dict(redacted=False)` exists.

## 13. Shared hotspots this work touched

Additive only. Every one is listed here because five sessions are editing them
in parallel:

| File | Change |
|---|---|
| `bartholomew/platform/capabilities.py` | four new `Capability` members, added to the user set |
| `bartholomew/platform/route_policy.py` | seven route classifications |
| `bartholomew/platform/authority.py` | `"actuation"` added to `VALID_SCOPES` |
| `…/api/routes/governance.py` | `"actuation"` added to its `VALID_SCOPES` |
| `…/api/app.py` | two `include_router` lines, one test-resolver call at startup |
| `Identity.yaml` | two allowlist entries, and a comment on the absent third |
| `pyproject.toml` | one optional `windows` extra |
| `.github/workflows/ci.yml` | two steps on the existing Windows job |

**`bartholomew/kernel/runtime_contract.py` was not edited at all.** The seam
composes its primitives — `Observation`, `Interpretation`, `CandidateAction`,
the reflection sink, the fail-closed Governance helpers — from its own module,
so an action travels the same governed path as every other seam without adding
400 lines to a 4,500-line file that four other sessions are also changing. The
dependency points one way: `actuation.seam` imports the Runtime Contract, and
the Runtime Contract does not import it.
