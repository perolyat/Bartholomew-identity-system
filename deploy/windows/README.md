# Bartholomew Windows alpha — the governed action companion

Opt-in. Nothing here runs, installs or enables itself: `bartholomew-action.ps1`
prints its help and exits unless you name a verb, and every verb that changes
anything states exactly what it will change and asks first.

Everything is **user-level**. No service, no `HKLM`, no Program Files, no
elevation, no firewall rule, no Defender exclusion, no execution-policy change,
no certificate. If the script is ever run elevated it says so and stops.

---

## What this actually is

A second, separate companion process. The observation companion
(`bartholomew.companion`) tells Bartholomew a little about what is happening on
this machine and **cannot act on it** — it has no actuation verb at all. This
one carries out actions that Bartholomew's server has already governed, and it
is a different process with different credentials talking to a different
endpoint. Neither can reach the other.

It can do exactly nine things, and only when each individual action has been
requested, admitted through eleven governance checks, and approved:

| Capability | What it does |
|---|---|
| `windows.launch_app` | Start one allowlisted application. No arguments, ever. |
| `windows.focus_window` | Bring one allowlisted application's window forward. |
| `windows.manage_window` | Focus, minimise, maximise, restore, bounded move/resize. |
| `windows.open_url` | Open one http/https URL from an allowlisted domain. |
| `windows.open_path` | Open one document or folder inside an allowlisted root. |
| `windows.clipboard_read` | Read the clipboard once. Content stays local by default. |
| `windows.clipboard_write` | Replace the clipboard with bounded ordinary text. |
| `windows.type_text` | Type bounded ordinary text, then read the field back to check it landed. Cannot press Enter or Tab. |
| `windows.accessibility_action` | Expand, collapse, scroll or focus an element. |

It **cannot** run a command, a script, PowerShell, Python or any interpreter;
cannot name an executable path; cannot delete, move, rename or edit a file;
cannot install anything; cannot send a message, submit a form, publish, buy
anything, or change an account or security setting; cannot type a password; and
cannot touch any machine but this one. Those are structural, not policy — see
`docs/B_GOVERNED_WINDOWS_ACTUATION.md`.

---

## Stopping something that has already started

Engaging a Parking Brake — any scope — stops actuation. Since W03-C that
includes an action this companion has **already been handed**, which it did
not before: a lease used to be the last word, so a brake pulled a second after
one was granted refused the *next* action and reached the one already in
flight not at all.

How it works now, and how long it takes:

1. Every lease arrives stamped with an **abort deadline** the server chose
   (about 5 seconds; the lease response also names it as `abort_check_seconds`).
2. Between leasing and running anything, the companion re-reads
   `POST /api/device-actions/abort-check`. If a brake is engaged, or the action
   has been cancelled underneath it, or the answer cannot be read at all, it
   does not run the action.
3. Inside a long step — `launch_app` waiting for a window to appear — it checks
   again, so a brake engaged mid-wait stops it there.
4. Past the deadline with no fresh check, it refuses the action outright. A
   missing or unreadable deadline counts as expired, never as unbounded.

So **an action already handed out stops within roughly five seconds** of the
brake being engaged, and the server records it as `aborted_by_brake` — a
status of its own, deliberately not folded into `cancelled`, so an audit can
count "a halt stopped this" separately from "a person withdrew this". The
server writes that row itself, so an action is recorded as stopped even if this
machine loses the network immediately afterwards.

```powershell
# On the Bartholomew side, from an operator shell:
bartholomew brake on --scope actuation      # prints the database file it touched
bartholomew brake status
bartholomew brake off
```

**This is a cooperative stop, and that is a real limitation.** It works because
this companion asks before it acts. A companion that has crashed, wedged, or
been prevented from reaching the server is not stopped by it — it is simply not
doing anything either, but nothing here can guarantee that. An independent,
out-of-process emergency stop is a separate piece of work that has been named
and deliberately not built yet (`docs/waves/W03/W03_DEFERRALS.md`, #8). If you
need to be certain right now, stop the companion process or the machine.

---

## Install

Requires Python 3.11+ on `PATH`. From an **ordinary** PowerShell prompt:

```powershell
cd <the Bartholomew repository>
.\deploy\windows\bartholomew-action.ps1 install
```

That creates `%LOCALAPPDATA%\Bartholomew\`, an isolated virtual environment
inside it, installs Bartholomew into **that environment only**, and copies
`companion.env.example` to `%LOCALAPPDATA%\Bartholomew\companion.env` with an
ACL granting only your account. It does not start anything and does not enable
startup.

## Configure

Edit `%LOCALAPPDATA%\Bartholomew\companion.env`. Nothing works until you do,
and **an empty allowlist permits nothing rather than everything** — that is the
intended failure mode.

You need four things:

1. `BARTH_ACTION_BASE_URL` — where Bartholomew is.
2. `BARTH_ACTION_DEVICE_ID` — this machine's enrolled device id.
3. `BARTH_ACTION_CREDENTIAL_HEADERS` — the device credential the deployment
   issued. **Do not commit this file.** The installer writes it readable only by
   you, and `diagnostics` prints header *names* only, never values.
4. The three allowlists: `BARTH_ACTION_APP_ALLOWLIST`,
   `BARTH_ACTION_URL_ALLOWLIST`, `BARTH_ACTION_PATH_ALLOWLIST`.

Then check it without contacting anything:

```powershell
.\deploy\windows\bartholomew-action.ps1 diagnostics
```

That prints the resolved configuration, whether the accessibility adapter is
available, the state of the executed-action ledger, and **an enrolment record to
paste on the Bartholomew side** so the server's copy of the allowlists matches
this machine's. Both copies are enforced independently, so a mismatch narrows
what is possible rather than widening it.

## Enrol this device on the Bartholomew side

The server refuses every action for a device it does not know. Point
`BARTH_ACTION_DEVICE_ENROLMENT` at a JSON file on the Bartholomew host:

```json
{
  "devices": [
    {
      "device_id": "desk-pc",
      "tenant_id": "<the runtime's user id, or \"local\" on a single-user install>",
      "platform": "windows",
      "enrolled": true,
      "capabilities": ["windows.focus_window", "windows.launch_app"],
      "applications": { "notepad": "C:\\Windows\\System32\\notepad.exe" },
      "url_domains": ["docs.python.org"],
      "filesystem_roots": ["C:\\Users\\YourName\\Documents"],
      "trusted_autonomy": []
    }
  ]
}
```

Set `"enrolled": false` to revoke a device without deleting its history; the
file is re-read whenever it changes, so revocation takes effect without a
restart. This is the **interim** registry — Session E replaces it with the
production device/group registry, and the interface it must satisfy is
documented in `bartholomew/actuation/devices.py`.

`trusted_autonomy` is empty by default and may only ever contain
`windows.launch_app`, `windows.focus_window` or `windows.manage_window`. The
other six always require a per-action approval, and three of them
(`clipboard_read`, `type_text`, `accessibility_action`) are refused at
construction if you try to put them there — there is no configuration that makes
typing autonomous.

## Run

```powershell
.\deploy\windows\bartholomew-action.ps1 start               # this window, Ctrl+C to stop
.\deploy\windows\bartholomew-action.ps1 start -Background   # detached
.\deploy\windows\bartholomew-action.ps1 status
.\deploy\windows\bartholomew-action.ps1 stop
```

`stop` only ever ends a process this script started, matched on the recorded pid
*and* on it still being a Python process, so a recycled pid is never mistaken
for the companion.

## Start at logon (optional)

```powershell
.\deploy\windows\bartholomew-action.ps1 enable-startup
.\deploy\windows\bartholomew-action.ps1 disable-startup
```

`enable-startup` creates exactly one Scheduled Task, `BartholomewAction`,
running as **your** account at **your** logon, explicitly `-RunLevel Limited`
(never elevated). It prints what it will create and asks first.
`disable-startup` deletes it and nothing else.

## Logs and files

| What | Where |
|---|---|
| Configuration | `%LOCALAPPDATA%\Bartholomew\companion.env` |
| Log (background runs) | `%LOCALAPPDATA%\Bartholomew\logs\action-companion.log` |
| Standard error | `%LOCALAPPDATA%\Bartholomew\logs\action-companion.log.err` |
| Executed-action ledger | `%LOCALAPPDATA%\Bartholomew\action-state.json` |
| Virtual environment | `%LOCALAPPDATA%\Bartholomew\venv` |
| Rollback backup | `%LOCALAPPDATA%\Bartholomew\venv.previous` |
| Recorded pid | `%LOCALAPPDATA%\Bartholomew\action-companion.pid` |

Foreground runs log to the console.

**The ledger is load-bearing.** It records which actions this machine has
already carried out, so a companion that crashed between acting and reporting
does not act twice on restart. If it becomes unreadable the companion
**refuses every non-repeatable action** rather than starting from an empty
ledger — an unreadable ledger is not an empty one. Delete it deliberately to
start a new one.

## Rollback

`install` backs the existing environment up before replacing it, so:

```powershell
.\deploy\windows\bartholomew-action.ps1 rollback
```

restores the previous environment. Configuration and the ledger are untouched.

## Uninstall

```powershell
.\deploy\windows\bartholomew-action.ps1 uninstall          # keeps config, ledger, logs
.\deploy\windows\bartholomew-action.ps1 uninstall -Purge   # removes those too
```

Nothing outside `%LOCALAPPDATA%\Bartholomew` was ever created, so nothing
outside it needs cleaning up.

## Troubleshooting

| Symptom | Cause |
|---|---|
| Every lease returns 401 | No device resolver is installed on the Bartholomew side, or the credential header is wrong. This is the shipped default and is correct; it is not worked around. |
| Every action is refused `device_not_enrolled` | `BARTH_ACTION_DEVICE_ID` does not match an enrolled record, or `BARTH_ACTION_DEVICE_ENROLMENT` is unset on the server. |
| Every action is refused `capability_not_declared` | The capability is not in `BARTH_ACTION_CAPABILITIES` here, or not in the device's enrolment there. Both must list it. |
| Every action is refused `parking_brake` | A Parking Brake is engaged — any scope stops actuation. Check `GET /api/governance/brake`. |
| `windows.type_text` always refuses | The accessibility adapter is unavailable, so the companion cannot see what field the caret is in. Re-run `install`, which installs the `windows` extra. |
| `windows.type_text` succeeds but always reports `unknown` | It typed, and the focused control does not expose its contents through UI Automation, so whether the characters landed could not be read back. That is the honest answer, not a fault. The evidence row says `verify_method: unavailable`. |
| Everything is refused `parking_brake` moments after being leased | Working as intended: a brake was engaged after the lease. `GET /api/governance/brake` says by whom and why; the actions are recorded as `aborted_by_brake`. |
| Every abort check fails and nothing runs | The companion cannot reach the server. It fails closed on purpose — an unreadable abort signal is treated as "stop", because a control that only works when nothing is wrong is not a control. |
| The companion will not start, mentioning the ledger | The ledger is unreadable. Delete `action-state.json` deliberately — and understand that doing so lets a previously-executed action run again if it is redelivered. |
| Nothing is ever leased, but the channel is open | Nothing has been approved. A request alone dispatches nothing. |

## What this deliberately does not close

Read `docs/B_GOVERNED_WINDOWS_ACTUATION.md` § "Security limitations" before
running this anywhere that matters. In short: the device credential is a bearer
token with no device-key material and no per-action signature; the interim
enrolment registry is a file a human wrote; and a compromised *server* that
holds a valid approval can direct this companion within its allowlists. The
allowlists are the bound on that, which is why they are required and why empty
means nothing.

And the stop is cooperative (see above). The abort check makes a brake reach an
action that has already been leased, which it could not do before; it does not
make a brake reach a process that has stopped listening. That is the deferred
out-of-process emergency stop, and until it exists the honest answer to "can I
be certain this machine will stop?" is "stop the process".
