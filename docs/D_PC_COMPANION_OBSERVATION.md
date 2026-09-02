# The PC companion: an observation-only device boundary

> **Status:** prototype, on a branch, unmerged. This document describes what
> `bartholomew/companion/` actually does today and — at least as importantly —
> what it does not do and must not be described as doing.

## 1. What this is

A small process that runs on a personal computer and tells Bartholomew a
deliberately narrow amount about what is happening there. It is the first real
device → Bartholomew observation channel.

It is **not** "Bartholomew can control my computer." It is "Bartholomew can
safely become aware of a small amount of what is happening on my computer."

The companion is a **client**. It introduces no new ingestion path, no new
event authority, no new database, no new schema and no new route. Observations
enter through the inbound boundary that already exists — `POST
/api/inbound/events` — so the Parking Brake, the platform halt tier, the
Identity policy gate, the provenance record and the
`UNIQUE(source_id, event_id)` idempotency constraint are the ones already
built and already tested.

## 2. Exactly what may be observed

Four kinds, and a closed allowlist of payload keys per kind. The allowlist is
in `observation.py` and `DeviceObservation.payload()` builds its dictionary
from it, so an observation carrying anything else cannot be constructed.

| Kind | `event_type` | Payload keys |
|---|---|---|
| Presence | `device.companion.presence` | `device_id`, `state` (`online`/`offline`) |
| Activity | `device.companion.activity` | `device_id`, `state` (`active`/`idle`), `idle_seconds` |
| Foreground app | `device.companion.foreground_app` | `device_id`, `application` |
| System state | `device.companion.system_state` | `device_id`, `platform`, `companion_version` |

That table is the entire privacy surface. `tests/test_companion_observation.py`
pins the union of those keys, so widening it fails CI.

**`application` is a name, never content.** It is the focused window's owning
process image name, reduced to a lowercased base name with the path and
extension discarded: `chrome`, never `C:\Users\taylor\...\chrome.exe` (a path
leaks an account name) and never the window title (which is content — a
document name, a page title, a URL). The window title is not filtered out; it
is never read.

**`idle_seconds` is only carried while idle**, and clamped to an hour. While
active it would be a near-continuous readout of typing rhythm.

The live source emits **transitions, not samples**. Sampling every fifteen
seconds and sending every sample would assemble a second-by-second log of
someone's day out of fields that are each individually minimal.

## 3. The envelope

The companion produces exactly the body the inbound route already accepts:

```json
{
  "source_id":   "desk-companion",
  "event_id":    "companion:foreground_app:9f2c…",
  "event_type":  "device.companion.foreground_app",
  "payload":     {"device_id": "desk-pc", "application": "chrome"},
  "occurred_at": "2026-08-31T10:00:00Z"
}
```

Five keys, all pre-existing. Nothing was added to the inbound contract, which
is why this work needed no change to the route, the store, the schema, or
`ROUTE_CAPABILITIES`.

### Two kinds of provenance, and they are not the same kind

* **`source_id` is verified provenance.** The route compares the submitted
  value against the source the deployment's installed resolver actually
  verified, and refuses with 403 on a mismatch. A companion cannot claim a
  source it was not issued.
* **`payload.device_id` is claimed provenance.** It is a label the companion
  asserts about itself. It is durably recorded and it distinguishes two
  machines under one verified source — but it is **not authenticated**, and
  nothing downstream may treat it as though it were.

### Idempotency

`event_id` is derived deterministically (SHA-256) from the observation's
content — device, kind, sequence, timestamp, values. A retry therefore carries
the *same* id and lands on the existing row. It is content-derived rather than
random precisely so that a retry issued by a **restarted** companion, which has
no memory of a random id, is still a retry.

The companion writes the in-flight envelope to its state file *before* the
first attempt, so a process killed between "submitted" and "acknowledged"
restarts knowing exactly which envelope is in doubt and re-sends that one.
Duplicate *delivery* is expected and handled. Duplicate *capture* is prevented
by the boundary that already prevents it, not by a second mechanism.

Retries stop where retrying is dishonest: 401/403 (unverified) and 422
(malformed) are terminal, because repeating them cannot change the answer. 503
— the Parking Brake engaged, or persistence unavailable — is retried with
backoff, because that one genuinely does resolve.

## 4. Why it cannot actuate

Four independent structural arguments, each asserted in
`tests/test_companion_no_actuation.py` rather than left to this document:

1. **Vocabulary.** The payload surface is a closed allowlist with no actuation
   noun in it. There is no `command`, `execute`, `action`, `operation`,
   `shell` or `script` field, and no free-form passthrough to put one in.
2. **Absent verbs.** The package imports no process-launching, input-synthesis,
   screen-capture, audio/video-capture or browser-automation module — the test
   walks the AST of every file in the package and asserts it. `subprocess` is
   not imported anywhere, which rules out the usual shape of an accidental
   tunnel: a probe that shells out to a helper and grows a caller-influenced
   argument.
3. **No return path.** `InboundSubmitClient` has exactly one public method,
   `submit`. It returns a three-scalar delivery result — status, HTTP code, a
   log string — and the runner branches on the *status* and nothing else. There
   is no expression in the package by which a value returned by the server can
   reach the machine. This is proven behaviourally too, against a real HTTP
   server that answers every submission with `{"command": "rm -rf /", ...}` and
   is ignored.
4. **The Windows probe is read-only.** Its `ctypes` use is pinned to an
   allowlist of documented query-only Win32 calls (`GetForegroundWindow`,
   `GetWindowThreadProcessId`, `GetLastInputInfo`, `GetTickCount`,
   `OpenProcess` with `PROCESS_QUERY_LIMITED_INFORMATION`,
   `QueryFullProcessImageNameW`, `CloseHandle`). `SendInput`, `keybd_event`,
   `mouse_event`, `PostMessage`, `SetForegroundWindow`, `CreateProcess`,
   `ShellExecute`, `WriteProcessMemory`, `TerminateProcess`, `BitBlt` and
   `PrintWindow` are each named in the test as forbidden, so reaching for one
   fails CI. `ctypes` is confined to that one module, also asserted.

There is deliberately **no actuation stub for later**. Adding actuation would
be a new decision with its own governance, not an unused branch in this file.

## 5. Authentication: what this does NOT close

**This prototype does not implement device authentication, and no part of it
should be described as though it did.**

* The repository default is that **no inbound resolver is installed**, and
  inbound capture is fail-closed: every submission is refused with 401. The
  companion respects that and does not work around it —
  `test_a_companion_against_a_closed_deployment_captures_nothing` is the
  evidence.
* The companion **defines no authentication scheme of its own**. It carries
  whatever headers the operator configures
  (`BARTH_COMPANION_CREDENTIAL_HEADERS`) so that whichever resolver a
  deployment installs can verify the source. No weak "temporary production
  auth" was invented to make the demo work.
* The S8 Alpha session token is a **bearer credential and is replayable by
  anyone who captures it** (see `docs/S8_ALPHA_OPERATOR_GUIDE.md`). It is not
  device authentication and it provides no per-request replay resistance. A
  companion running unattended on a personal computer holds a long-lived
  credential on a general-purpose machine, which is a materially different
  exposure from a browser session, and nothing here addresses it.
* The end-to-end evidence uses the **test-only resolver**, which is a static
  token with no signature, no rotation and no replay window. It cannot enable
  itself: it requires two environment variables that exist in no deployed
  configuration, warns at startup, reports itself on `/api/health`, and stamps
  `verified_by="test-resolver"` on every durable row it admits. It exists to
  make the rest of the path provable, and it is not a device-authentication
  scheme.

Consequently the honest deployment posture today is: **run this only against a
loopback Bartholomew on the same machine, and only with an operator-installed
resolver.** A companion topology that spans machines needs real device
authentication, which is not in this slice.

### What Package E changed, and what it did not

Package E adds a **device registry** (`bartholomew/platform/devices.py`) and a
resolver backed by it (`bartholomew/platform/device_inbound.py`). A deployment
that enrols this companion and sets `BARTH_DEVICE_INBOUND_AUTH=1` now admits
its events on a registry-issued credential, stamped
`verified_by="device-credential"`, and can rotate or revoke that credential
immediately. Enrolment, per-device identity, capability declaration and
revocation are therefore no longer absent. See
`docs/E_DEVICE_TRUST_AND_TRUSTED_GROUPS.md`.

Two things are unchanged, and it matters that they read as unchanged:

* **`payload["device_id"]` is still claimed provenance**, exactly as Section 3
  says. The registry's `device_id` is a *different* value in a different
  namespace -- server-generated, never a label -- and nothing converts one
  into the other. A companion that puts another machine's label in its payload
  changes nothing about which device the platform verified.
* **A device credential is still a bearer credential.** It is not a
  per-request signature and gives no replay resistance beyond what TLS
  provides on the wire. The S8 note that "genuine per-request replay
  resistance arrives with device authentication" is only half-discharged: the
  identity now exists and can be withdrawn; the signing does not.

## 6. Running it

```
BARTH_COMPANION_BASE_URL=https://127.0.0.1:8765
BARTH_COMPANION_SOURCE_ID=<the source id the resolver issues this companion>
BARTH_COMPANION_DEVICE_ID=desk-pc
BARTH_COMPANION_CREDENTIAL_HEADERS='X-Whatever: <as the installed resolver requires>'
BARTH_COMPANION_POLL_SECONDS=15
BARTH_COMPANION_STATE_PATH=~/.bartholomew/companion-state.json

python -m bartholomew.companion
```

No arguments and no subcommands: there is exactly one thing this process does,
and a command surface would be the beginning of a control surface. A
misconfiguration exits non-zero before anything is observed. On a platform with
no probe (anything but Windows today) the companion still runs and reports
presence and system state; it reports "unknown" rather than guessing, and never
falls back to a broader collection method.

## 7. Scope boundary

This slice **supplies** observations to the inbound boundary. It does not
interpret them, does not attach them to objectives, and does not decide what
any of them means — the durable row records that something arrived, never that
Bartholomew believes it. Downstream meaning is owned elsewhere.

## 8. Known limitations

* Only Windows has a real probe. macOS and Linux run with `NullProbe` and
  therefore report presence and system state only.
* Replay resistance is absent: a device credential, like a session token, is a
  bearer credential (Section 5). Device *identity* is no longer absent -- see
  "What Package E changed" in Section 5 -- but request signing is.
* `payload["device_id"]` is unauthenticated (Section 3), and stays that way.
  The authenticated device identity is the registry's, carried by the
  credential, not by the payload.
* The companion is a foreground process with no service/daemon packaging, no
  auto-start and no supervision.
* The state file is unencrypted; it holds a sequence number and at most one
  in-flight envelope of the metadata above.
* The companion has no built-in enrolment client: an operator completes
  enrolment with `bartholomew devices complete` and configures the resulting
  credential by hand. The enrolment and revocation *flows* themselves exist
  (Package E); wiring them into this process does not.
