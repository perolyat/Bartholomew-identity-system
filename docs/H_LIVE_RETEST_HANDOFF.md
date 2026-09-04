# Handoff: live Windows golden-path verification

**Your only job is to run the live verification described here and record what
you observe.** Do not develop, do not merge, do not start Wave Three. If you
find a defect, record it; repairing it is a separate, separately-authorised
piece of work.

Everything you need is in this file, in
`docs/G_WINDOWS_COMPANION_COMPLETION.md`, and in the code. You should not need
the conversation that produced this.

---

## 1. Where things stand

| | |
|---|---|
| Branch | `claude/windows-companion-completion-pkg` |
| Head | `3834b67` |
| PR | #90 (`perolyat/Bartholomew-identity-system`), **open, draft, not for merge** |
| Base | `claude/bartholomew-wave-two-integration-qjrxkr` (PR #89, Session F) |
| CI on the head | **All six checks green** — Quality; Tests + coverage on 3.10 and 3.11; Critical integration + lifecycle on 3.10 and 3.11; Windows lifecycle + compatibility |
| Working tree | Clean, in sync with origin |

A first live test was run on a real Windows 10 desktop and is recorded in
**`docs/G_WINDOWS_COMPANION_COMPLETION.md` §9**. Read that section first: it
says exactly what was proven and what was not. Two of its findings were then
repaired (**§10**), and this retest exists to verify those repairs and to close
the one leg of the loop nobody has demonstrated.

### CI flakiness you should know about before you trust a red run

Three CI runs on this branch each failed **one different test**, all in suites
this work does not touch. The decisive evidence that this is pre-existing:
commit `6b19159` changed *one Markdown file* — executable code byte-identical
to its parent, which had passed all six jobs — and still failed py3.10. A
re-run of identical code then passed both jobs. There is no `pytest-randomly`,
so collection order is fixed; different tests failing across runs means timing,
not state.

The two implicated tests are
`tests/test_personal_memory_capture_recall.py` (retrieval returned empty) and
`tests/test_event_backbone_drive.py::test_the_running_scheduler_records_a_tick_for_the_drive`.
**No root cause was found.** One hypothesis — `retrieval._check_fts5_once`
caching `False` permanently on any exception — was tested by poisoning the
cache and is **refuted**: those tests still pass. Do not propose it as the
cause. If you see one of these fail, it is very likely not yours.

---

## 2. What changed since the first live test

Full detail in `docs/G_WINDOWS_COMPANION_COMPLETION.md` §10. In brief:

**`d921ded` — the brake CLI now reaches the server's database.** Previously
`brake on` defaulted `--db` to a literal `data/bartholomew.db`, a scratch file
the running server never opened, so it printed `⚠ Parking brake ENGAGED` while
the server carried on dispatching. `bartholomew/kernel/db_paths.py` is now the
single resolver — explicit path, else `BARTH_DB_PATH`, else
`<project root>/data/barth.db`, read fresh — and the server, the kernel daemon
and the brake commands all delegate to it. The commands now print which file
they touched. An explicit `--db` still wins unconditionally.

**`d921ded` — an operator can now answer a device's request to observe.**
`bartholomew/multimodal/device_consent.py` plus
`bartholomew_api_bridge_v0_1/services/api/routes/device_consent.py` and
`bartholomew/cli_consent.py`. Previously the API server registered no consent
handler at all, so *every* observation start refused with "No consent handler
registered (fail-closed)". Now the Runtime Contract's consent gate consults a
separate device-consent handler; an ask is recorded in the kernel database with
a high-entropy nonce and the start waits on it without blocking the event loop;
a person answers with `bartholomew consent pending` then
`bartholomew consent approve <request_id>`.

**`50b61ad` — the brake is re-read after consent.** Consent can take minutes, so
the brake read before the ask is stale by the time a person answers.

**`df48c83` — hardening from adversarial review.** Most relevant to you:
stopping a session while its consent ask is open no longer hits an illegal state
transition; it ends the session `refused` and abandons the ask.

**`3834b67` — formatting only.**

---

## 3. The operator's machine, as it was left

This is carried over from the first run and will save you a great deal of time.
**Verify each item rather than assuming it**, but expect these to hold.

- Windows 10 19045, user `tpaul`. Repo at `C:\Users\tpaul\bartholomew-src`,
  virtualenv at `.venv` (Python 3.12). Editable install — a `git pull` needs no
  reinstall.
- **`BARTH_DB_PATH` is set as a persistent Windows environment variable** to
  `D:\workspace\bartholomew-test\barth.db`. Every new shell inherits it. This is
  the exact condition that made the old brake command silently useless, so it is
  also the condition that proves the repair.
- The platform database is separate and resolves independently
  (`bartholomew/platform/store.py`); `BARTH_DB_PATH` does not affect it.
- Account `tpaul`, `user_id` `c6c988e4-83cb-415b-8791-f10e7cfd1b2c`.
- Device `desk-pc`, `device_id` `fb05c2c1-d61b-44c4-b472-808b8a9f27a1`, **ACTIVE**,
  manifest declares `windows.focus_window@1`, `windows.type_text@1`,
  `windows.accessibility_action@1`, `multimodal.screen_capture@1`.
- **The device credential is already in Windows Credential Manager**
  (`WinVaultKeyring`). Do **not** re-enrol and do **not** rotate: the action
  companion's credential can be read back out of the keyring with
  `bartholomew.platform.companion_credential.load(device_id=...)`, which returns
  `(device_id, secret)`. Rotation would invalidate the stored credential and
  cost you a re-store.
- `manifest.json` and `allowlist.json` already exist in the repo folder.
- All PowerShell windows from the previous run are closed, so **every**
  environment variable they set is gone and must be re-established:
  `BARTH_RUNTIME_USER_ID`, `BARTH_DEVICE_ACTION_AUTH`,
  `BARTH_COMPANION_DEVICE_ID`, and the whole `BARTH_ACTION_*` set.
- Notepad may still contain `Bartholomew live golden path test` from last time.
  **Clear it before you start** so "text appeared" is unambiguous evidence.

---

## 4. Traps the first run hit, already paid for

Do not rediscover these:

1. **Hidden-input prompts cannot be pasted into in the Windows console.**
   `Ctrl+V` into a hidden prompt yields a bare "unknown device credential",
   which reads as a bad secret rather than an input-method problem. Pipe the
   value on stdin, or read it into a variable with a visible `Read-Host` first.
2. **A screen session must name exactly one capture scope.**
   `companion observe start --modality screen` alone is a 400. Use
   `--display-id 0`.
3. **The health endpoint is `/api/health`, not `/health`.** Its `db_path` field
   is the fastest way to see which database the server is actually using.
4. **`windows.focus_window` does not work and is out of scope.** Windows'
   foreground lock refuses `SetForegroundWindow` to a background process; the
   companion correctly reports `permission_denied` and refuses to synthesise
   keystrokes to force focus. **The operator focuses Notepad by hand**, and any
   write-up must say so.
5. **Set a generous action-companion poll interval.**
   `BARTH_ACTION_POLL_SECONDS=25` gives the operator time to click into Notepad
   after approving, so the keystrokes land where intended rather than in
   PowerShell.
6. **Arming does not survive a server restart** — it is in-process by design.
   Restart the server first, then arm.
7. The server needs `BARTH_RUNTIME_USER_ID` set or the Session F seams do not
   install the action resolver, and everything refuses in a way that looks like
   a new bug.

---

## 5. What the retest must establish

The acceptance targets, in the operator's words:

1. `brake on` **with no `--db`** forces `armed: false` against the *running*
   server — the repair to Finding 4.
2. A real observation session **starts**, with the operator answering the
   consent ask — the repair to Finding 1.
3. The full loop: **real observation → governed action → real keystrokes in
   Notepad → real observation of the resulting state.**

Everything already proven in §9 (enrolment, DPAPI credential storage,
authenticated device access, disarmed-by-default, the 15-minute arm, arming
granting no action, digest-only audit rows, learning candidate-only) does not
need re-proving, though most of it will be exercised again in passing.

---

## 6. Two things you must resolve yourself, honestly

**(a) The procedure in §8 is not fully pre-verified against the repaired code.**
A preflight pass was started and did not complete. §8 step 2 *was* corrected for
the new consent flow; the rest predates the repairs. **Verify every verb, flag
and response field against the code before you put it in front of the
operator** — they are not a developer, and a wrong flag costs a round trip.
Specifically confirmed already: `companion observe start` flags and the
capture-scope rule; `consent pending|approve|deny` exist in
`bartholomew/cli_consent.py`; `brake on|off|status` take `--db` and now resolve
through `db_paths`; `companion_credential.load()` returns `(device_id, secret)`.
Not confirmed: the action-request and approval HTTP bodies as PowerShell
one-liners, and the exact `BARTH_ACTION_*` set for a fresh window.

**(b) "Real observation of the resulting state" is an open question.**
Nobody has established that Bartholomew can report back *what* it read, as
opposed to that a capture session existed. Before promising the operator this
leg, work out which of these is actually true in this build:

- `windows.accessibility_action` (`bartholomew/windows_actuation/handlers.py`,
  `uia.py`) — can it read a window's text back, what are its parameters, and
  does its result carry the observed text, a digest, or nothing?
- the live screen session (`bartholomew/multimodal/screen.py`) — are frames
  captured at all on Windows, and is there any surface where a person can see
  *what* was captured?
- `bartholomew/integration/multimodal_events.py` — does an observation emit an
  event carrying anything about content, or only about the session?

**If the honest answer is "Bartholomew can observe that the screen was captured
but cannot report what it read", say so and demonstrate the strongest honest
thing instead.** The worst possible outcome is telling the operator the loop
closed when what was demonstrated is a session existing. The first live test's
best moment was the system recording `unknown` / `effect_unverifiable` rather
than claiming a success it could not observe; hold yourself to that standard.

---

## 7. How to work with this operator

They are not a developer and asked explicitly for **exactly one action at a
time, in plain English** — what to click, open, type or run, and where — then
**stop and wait** for them to report what happened. Do not assume a step
succeeded. Pasted terminal text is preferred over screenshots: it is cheaper and
exact, and it caught a misreading last time.

Tell them roughly how many steps are ahead so they can judge the commitment.
The first run took 55 steps; this one should be far shorter because enrolment
and the credential already exist.

---

## 8. Recording the result

Add a new dated section to `docs/G_WINDOWS_COMPANION_COMPLETION.md` after §10
(renumber the sections that follow, as §9 and §10 did) recording **what was
observed, not what was expected**, in the same shape as §9: a table against the
acceptance targets, then findings, then an explicit "what this test did not
establish". Update PR #90's description to match. Commit to
`claude/windows-companion-completion-pkg` and push.

**Do not merge. Do not enable auto-merge. Do not start Wave Three. Do not
weaken, skip, quarantine or delete a test.**
