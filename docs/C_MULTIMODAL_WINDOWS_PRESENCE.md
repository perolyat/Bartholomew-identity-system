# Multimodal Windows Presence (Package C)

Explicit-session hearing, speaking and bounded screen understanding for
Bartholomew on Windows.

This document is both the design record and the Windows deployment guide. It
describes what the package does, what it deliberately refuses to do, and how to
install, diagnose, verify, stop, uninstall and roll it back.

---

## 1. What this is

Bartholomew can, **only inside an explicit session that a person asked for and
consented to**:

- listen on a microphone for a bounded time;
- speak an answer aloud;
- read the accessibility tree of the active window to understand the UI;
- capture one approved screen, window or region as a *fallback* when the
  accessibility tree is not enough.

Every one of those is a separate permission, separately consented, separately
policed and separately stoppable.

### What it is not

There is no ambient listening, no wake word, no background screen recorder, no
webcam or household camera, no biometric or emotion inference, no speaker or
room discovery, and no automatic restart of a finished session. There is also
no PC *action* dispatch here — this package observes and speaks; it never
clicks, types or controls an application. Those are Package B's, and they are
not reachable from here.

A session always ends. It ends when you stop it, when the Parking Brake is
engaged, when it expires (default 2 minutes, hard ceiling 15), when the
hardware goes away, or when the process dies.

---

## 2. The three permissions

| Capability kind (§3.3) | Identity allowlist entry | Brake scope | Grants |
|---|---|---|---|
| `multimodal.microphone_session` | `multimodal_microphone_session` | `voice` | Listening, one bounded session |
| `multimodal.screen_capture` | `multimodal_screen_capture` | `sight` | Observing one approved screen/window/region |
| `multimodal.spoken_output` | `multimodal_spoken_output` | `voice` | Speaking aloud |

**Permission to speak is not permission to listen. Permission to listen is not
permission to watch your screen. Permission to capture one window is not
permission to capture another, or the whole desktop.** There is deliberately no
single "multimodal enabled" setting; removing one allowlist entry leaves the
other two working.

The brake scopes reuse the two the repository already registers rather than
adding new ones. A brake is a *stop*, never a permission, so a shared scope can
only ever stop more than asked — it can never let one modality authorise
another. Permission separation lives in consent and policy, which are per-kind.

---

## 3. The session state machine

```
requested ──► awaiting_approval ──► approved ──► active ──► stopping ──► stopped
     │                │                 │           │
     └────────────────┴─────────────────┴───────────┴──► refused
                                                     └──► failed
                                                     └──► unavailable
                                                     └──► expired
```

Ten states, of which five are terminal (`stopped`, `refused`, `failed`,
`unavailable`, `expired`). Every transition is validated against a fixed edge
set and appended to an audit trail with a timestamp and a reason; an illegal
move raises rather than being logged and ignored. A terminal session has no
outgoing edges at all, which is what makes automatic restart unexpressible.

`unavailable` is deliberately its own state. "You have no microphone" is a
different truth from "the microphone broke" (`failed`) and from "you may not"
(`refused`), and a user needs to be told which.

Every session is bound to: tenant, authenticated principal, resolved device,
modality, requested scope, consent decision, governance decision, start time,
expiry, and correlation/causation ids. A binding that cannot be resolved is a
denial, not a default.

---

## 4. Governance: what must pass before anything is captured

In this order, all before any device is touched:

1. **Tenant and principal resolved.** A request that cannot name them cannot be
   constructed.
2. **Device capability resolved** against Session E's declaration (§3.3). An
   unenrolled device, an undeclared kind or an unknown version denies. Unknown
   is unsupported, never approximated.
3. **Parking Brake**, read through the existing `GovernanceStore`. Unreadable
   fails closed.
4. **Identity policy decision** on the modality's own kind.
5. **Explicit session consent**, through the existing consent channel. An absent
   handler, a decline, or an unresolved result all deny.
6. **Scope and duration validated.** Over-ceiling durations are refused, not
   silently clamped.
7. **The decision is recorded** through the existing reflection/evidence sink —
   denials as durably as grants.

Missing or unreadable governance, identity, consent or device state **denies**.

**Nothing that is content can start a session.** A model response, an inbound
event payload and a companion observation are text, not people. Principals
prefixed `model:`, `assistant:`, `event:`, `inbound:`, `companion:` or `system:`
are refused at request construction. The HTTP surface has **no start endpoint at
all** — capture initiation is not reachable over the unauthenticated API bridge.

---

## 5. Privacy and retention

Every derived observation is classified before it leaves the adapter, using the
frozen §3.1 vocabularies.

| Observation | Privacy | Retention |
|---|---|---|
| Microphone transcript | `sensitive` | `ephemeral` |
| Screen / accessibility observation | `sensitive` | `ephemeral` |
| Spoken output | `ordinary` | `ephemeral` |
| Session state change | `context_only` | `audit` |

Anything from which a secret was redacted is escalated to `restricted`.

**Raw audio is never persisted.** The adapter holds frames only long enough to
derive a bounded transcript and drops them on every exit path. There is no file
path parameter, no recording directory, and no configuration that enables raw
retention.

**Raw images are never persisted.** `ScreenObservation` has no image field, no
bytes field and no path field, and `screen.py` never opens a file for writing.
The image object exists inside one `try` block and is deleted before the
function returns. Raw image retention requires a separate governed policy
(§3.6) that this package does not ship and cannot be configured into.

**Secrets are refused, not masked-and-carried.** An accessibility control whose
name or role marks it as a password, PIN, API key, recovery code, card number
or similar has its value **never read at all** — there is nothing downstream to
leak. Secret-shaped material inside ordinary free text is replaced with
`[redacted]`. Both are recorded in the observation's `redactions` list, so a
reader can see that something was removed.

Observations are bounded: 2000 characters, 40 accessibility elements, 200
characters per element. Truncation is recorded, never silent.

---

## 6. Accessibility before pixels

The accessibility tree is read **first, every time**. The screenshot path is
reached only when all three hold, and all three are recorded:

1. the accessibility reading was genuinely insufficient (the reason is stored
   verbatim);
2. the session explicitly authorised the screenshot fallback — a separate
   decision from authorising screen observation at all;
3. the requested target is inside the approved scope.

If any fails, no image is taken and the refusal reason is recorded. When a
screenshot *is* taken, the record carries which screen/window/region, why the
fallback was required, and an evidence reference (a digest binding the derived
description to the scope) — without retaining anything that was on screen.

Scope cannot widen silently: a request for another window, another display or a
larger region is refused outright. Widening means a new session with a new
consent decision.

---

## 7. Windows deployment

### 7.1 Supported versions

- **Windows 10 (21H2 or later) and Windows 11**, x64.
- **Python 3.11** (the version this repository targets and CI runs).

Windows Server is untested. Non-Windows machines can run everything here, but
report accessibility and (usually) microphone as unavailable — which is a
supported state, not a fault.

### 7.2 Install in an isolated environment

Never install into the system Python.

```powershell
cd C:\path\to\Bartholomew-identity-system
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Installing the repository does **not** enable ambient capture, does not start
listening, does not enable startup, and does not alter any Windows privacy
setting. Nothing captures until a person requests a session and consents to it.

### 7.3 Optional native dependencies

All three are **optional**. Without them the corresponding capability reports
`unavailable` truthfully; nothing pretends to work.

```powershell
python -m pip install sounddevice     # microphone input
python -m pip install uiautomation    # accessibility tree (preferred path)
python -m pip install mss             # screenshot fallback
```

They are deliberately not in `requirements.txt`: a native audio/UI dependency
tree must be an explicit choice, and CI must not need one.

### 7.4 Microphone and speaker setup

1. **Settings → System → Sound → Input** — confirm a device is present and its
   test meter moves when you speak.
2. **Settings → Privacy & security → Microphone** — turn on *Microphone access*,
   and *Let desktop apps access your microphone*. Python is a desktop app; with
   this off, opening a stream fails and Bartholomew reports
   `permission_denied`.
3. **Settings → System → Sound → Output** — confirm an output device for spoken
   output.

**Known limitation — spoken output on Windows.** The repository's existing
spoken-output adapter drives argv-based speech binaries (`espeak-ng`, `espeak`,
`spd-say`, macOS `say`). Windows has none of these, so `available_engine()`
finds nothing and speech reports "no engine" — truthfully, never as speech that
silently did not happen. Windows SAPI support means constructing a PowerShell
command string around user text, which is a larger safety question that the
existing adapter deliberately does not answer, and Package C does not
second-guess it by creating a competing speech authority. To get speech on
Windows today, install an argv-based engine (e.g. eSpeak NG) and put it on
`PATH`, or point `BARTH_TTS_COMMAND` at it.

### 7.5 Screen-capture and accessibility prerequisites

- Windows does **not** gate desktop screen capture behind a privacy toggle the
  way it gates the microphone; the practical prerequisite is an interactive
  desktop session. Capture from a service/session-0 context will not work.
- UI Automation needs no special permission for same-privilege applications. It
  **cannot** read windows owned by a higher-privilege process: if Bartholomew
  runs unelevated and the target application runs as administrator, the
  accessibility read returns incomplete — which is recorded as an insufficiency
  reason, not silently treated as an empty screen.
- Remote Desktop and some virtual displays report differently; verify with the
  diagnostic command below.

### 7.6 Diagnostics

```powershell
python -m bartholomew.cli multimodal diagnose
python -m bartholomew.cli multimodal diagnose --json
```

Reports, per capability, whether it is available and precisely why not.
**It observes nothing** — no audio stream is opened, no accessibility tree is
read, no image is taken — so it needs no session and no consent.

Also available over HTTP: `GET /api/multimodal/diagnostics`.

### 7.7 Verifying visible session status

```powershell
# What this CLI process sees (it owns no sessions)
python -m bartholomew.cli multimodal status

# What the running daemon is actually doing
curl http://127.0.0.1:8000/api/multimodal/status
curl http://127.0.0.1:8000/api/multimodal/sessions
```

`/api/multimodal/status` answers, in plain words: is Bartholomew listening, is
it observing the screen (and *which window*), is it speaking, when did it start,
when does it expire, how do you stop it, and is any hardware unavailable.

The CLI reports on its own process, which owns no sessions — deliberately. A
status command that guessed at another process's capture state would be exactly
the kind of claim this package must never make.

### 7.8 Starting and stopping

**Starting** a session is a governed, in-process act through
`bartholomew.multimodal.runtime.start_session()`. There is **no HTTP start
endpoint**, by design (§4 above).

**Stopping** is always available:

```powershell
curl -X POST http://127.0.0.1:8000/api/multimodal/sessions/<session_id>/stop
curl -X POST http://127.0.0.1:8000/api/multimodal/sessions/stop-all
```

These remain reachable during the daemon's startup and shutdown windows — a
stop that returned 503 exactly when someone urgently wanted capture to end
would be the wrong failure.

The **Parking Brake** stops sessions regardless:

```powershell
python -m bartholomew.cli brake on --scope voice    # stops listening and speaking
python -m bartholomew.cli brake on --scope sight    # stops screen observation
python -m bartholomew.cli brake on                  # global: stops everything
```

### 7.9 Data and log locations

| What | Where | Contents |
|---|---|---|
| Governance/brake state, reflections | `data/bartholomew.db` | Session *decisions* (allowed/denied and why). No captured content. |
| Session status file (if enabled) | operator-chosen path | State, timing and provenance snapshots only — no transcript, description or image. |
| Application logs | the daemon's configured log sink | Session lifecycle and errors. |

**No audio file, image file or screenshot directory is created anywhere.** There
is no code path that writes one.

### 7.10 Startup (opt-in only)

This package ships **no** startup installer, service registration or scheduled
task, and nothing here enables startup silently. If you add one yourself, keep
it user-level (`shell:startup` or a per-user Scheduled Task), never machine-wide,
and make sure it is removable by deleting the shortcut or task. Bartholomew
starting at login still does not start capture: a session needs an explicit
request and consent.

### 7.11 Uninstall

```powershell
# 1. Stop everything currently running
curl -X POST http://127.0.0.1:8000/api/multimodal/sessions/stop-all
python -m bartholomew.cli brake on

# 2. Remove the optional native dependencies (this alone disables the hardware paths)
python -m pip uninstall sounddevice uiautomation mss

# 3. Remove any startup shortcut or scheduled task you created yourself

# 4. Remove the isolated environment
deactivate
Remove-Item -Recurse -Force .\.venv
```

Uninstalling the optional dependencies is a complete functional disable on its
own: every capability then reports `unavailable` and no session can capture
anything.

### 7.12 Rollback

Package C is additive. To roll it back:

1. Stop all sessions and engage the brake (step 1 above).
2. Check out the commit before this package's branch.
3. Remove the three `multimodal_*` entries from `Identity.yaml`'s
   `tool_use.allowlist` if you are keeping the code but withdrawing permission.

**No database migration ships with this package**, so there is no schema to
reverse and no backup step required before rollback. Sessions live in process
memory and are gone when the process stops.

Withdrawing one capability without a code rollback is a one-line edit: delete
that modality's allowlist entry. The other two keep working.

---

## 8. What was verified, and how

Honest separation, per the contract's requirement to distinguish simulated from
real-hardware verification.

### Verified against real repository paths (not doubles)

`tests/test_multimodal_integration.py` runs the real
`run_multimodal_session_through_runtime_contract()` against a real
`GovernanceStore` database and the real shipped `Identity.yaml`:

- the full grant path reaches `started`;
- an engaged `voice` brake denies a microphone session;
- an engaged `global` brake denies all three modalities;
- a `sight` brake stops screen observation **without** silencing speech;
- Identity policy receives the modality-specific kind, and all three kinds are
  permitted by the real allowlist;
- consent is genuinely required, and each modality raises its own distinct
  prompt;
- a denied session never reports as active on the status surface;
- an approved session on a machine with no microphone becomes `unavailable`.

### Verified through controlled adapters (simulated hardware)

Microphone availability and failure modes (no backend, no device, OS permission
denied, probe failure, mid-session device loss), stream release on every exit
path, stop latency, accessibility-tree parsing and redaction, screenshot
fallback authorisation and refusal, and scope containment. These use injected
doubles: CI has no microphone, no display and no Windows accessibility tree.

### Not verified — no Windows hardware available

**None of the following has been run on actual Windows hardware**, and nothing
in this package should be described as Windows-verified until it has:

- `sounddevice` against a real Windows microphone, including the real Windows
  privacy-toggle denial path;
- `uiautomation` against a real Windows accessibility tree, including the
  elevated-window incomplete-read case;
- `mss` against a real Windows display, including multi-monitor and DPI scaling;
- the Windows spoken-output limitation (documented in §7.4 from the existing
  adapter's own recorded behaviour, not from a Windows test run).

The `_SoundDeviceBackend`, `_UIAutomationProvider` and `_MssScreenBackend`
classes are marked `# pragma: no cover` for exactly this reason. The logic
*around* them is tested; they themselves are not.

Additionally, **this package ships no speech-to-text engine**. A microphone
session with the default backend produces an empty transcript, reported
honestly as empty. A deployment that wants transcription supplies a backend
whose `read_transcript()` performs it.

---

## 9. Integration points for Session F

### Session E — device capability registry

Package C consumes, and does not own, the device registry. It needs exactly one
question answered, defined as a `Protocol` in `bartholomew/multimodal/devices.py`:

```python
class DeviceCapabilityResolver(Protocol):
    def resolve(self, device_id: str, kind: str, version: int) -> DeviceCapability: ...
```

`DeviceCapability` carries `device_id`, `kind`, `version`, `supported`,
`reason` and `verification`. F replaces `StaticCapabilityResolver` (a
non-persistent stand-in that enrols nothing and verifies nothing) with E's
registry. Nothing else in the package changes — no other module knows where a
manifest came from. `kind` values are exactly §3.3's
`multimodal.microphone_session`, `multimodal.screen_capture`,
`multimodal.spoken_output` at version `1`.

Package C never raises `verification` above `claimed`; only E's registry may.

### Session A — canonical event pipeline

`bartholomew/multimodal/events.py` builds §3.1-shaped envelopes and **imports
nothing from any event package**. It persists nothing, delivers nothing and
consumes nothing. F connects A's ingress by supplying a sink:

```python
class MultimodalEventSink(Protocol):
    def submit(self, envelope: dict[str, Any]) -> None: ...
```

The default `NullEventSink` drops events and logs at debug — the honest state
until F wires A in. Event types to register with A's registry:

- `multimodal.microphone.transcript`
- `multimodal.screen.observation`
- `multimodal.accessibility.observation`
- `multimodal.spoken_output.utterance`
- `multimodal.session.state`

`captured_at` is emitted as `null`: per §3.1 ingress assigns it from trusted
server-side context, and inventing one here would falsely imply ingress had
seen the event. `event_id` is content-derived, so a retry collapses onto one
logical event under A's `(tenant_id, source_id, event_id)` rule.
