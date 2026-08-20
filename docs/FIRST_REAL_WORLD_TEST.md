# First controlled real-world test — procedure

> **Status:** Working procedure (2026-08-17). **Non-canonical** — a reference document under
> `docs/`, not one of the 14 canonical SSOT docs. `MASTER_PLAN.md` remains the authority on whether
> this test is authorised and what stage the project is in. This document says *how* to run it and
> *what counts as a pass*, not *that it may proceed*.
>
> **Deployment boundary:** this procedure describes a **personal development prototype running on
> `localhost`**. See §0 before exposing anything to a network.
>
> **Test #1 has been run (added 2026-08-20).** This document remains the *procedure*. The test it
> describes was executed 2026-08-19/20 against **commit
> `854a8da7fd107db33a933c4bdb01bf3fd7eb69bd`** — the merge commit for PR #58, whose head branch
> `claude/bartholomew-parking-brake-consent` **no longer resolves**. **The commit hash is
> authoritative; the branch name is not.** That commit was **not** on `main`: `main`/`origin/main`
> was `d0c202f7b39f9244417f1954629f64f68dfbb341` at the relevant review point, 25 commits behind and
> **not containing the tested implementation**. Do not read §0's `a885e25` verification date, or any
> later state of `main`, as the tested implementation.
>
> **Results, evidence and consequences live elsewhere:** the evidence location, provenance record
> and absence inventory are `docs/evidence/test-1/`; the approved adjudication is Post-Test #1
> Decision Register v2.2 (`docs/evidence/test-1/interpretation/`); the decisions are in
> `DECISIONS.md`; the readiness bands that now gate any further testing are in `ROADMAP.md`'s
> "Post-Test #1 readiness bands". **A future test must satisfy its band's prerequisites first — this
> procedure alone is no longer sufficient authorisation to run one.** In particular, §0's
> unauthenticated-API boundary is a *test condition*, not an architecture: decision D10 and safety
> gate S8 now govern any move off localhost.
>
> **Raw evidence caveat:** none of Test #1's raw artifacts are present in this repository. They are
> inventoried as **absent** in `docs/evidence/test-1/MANIFEST.md` §4, and the approved
> evidence-access limitation — case IDs and timestamps verified for internal consistency only —
> remains in force.
>
> **Consent path (corrected 2026-08-18):** §5 uses the **web/API consent inbox**, which already
> exists and is the authoritative path for this runtime. The previous revision of this document
> claimed the API path "never asks" and routed this step through `python chat.py`; that claim was
> false and the instruction was wrong — see §5. No code change was needed to fix it.

---

## 0. Before you start — the deployment boundary

**The API has no application-level authentication of any kind.** This was verified live on
2026-08-17 against `main` at `a885e25`: an unauthenticated `curl` can chat, read state, and — this
is the part that matters — **engage and disengage the Parking Brake**:

```
POST /api/governance/brake/engage    → 200, brake engaged
POST /api/governance/brake/disengage → 200, brake released
```

Consequences for this test:

- Bind to `127.0.0.1` only. Do not port-forward, tunnel, or expose the port to make phone access
  convenient. Anyone who reaches the port can disable the safety control.
- `ALLOWED_ORIGINS` defaults to localhost origins. Leave it alone.
- Authentication, transport security, and a reviewed threat model belong to the later platform
  transition. `DECISIONS.md`'s deployment-architecture entry already requires that they be designed
  and approved before any remote exposure, and explicitly rejects "simple token auth" as
  sufficient. Nothing in this test changes that.

Two further environment facts worth knowing before you read anything into a result:

- **Memory encryption key.** Startup logs `Using ephemeral dev key for STRONG. Set BME_KEY_STRONG
  in production.` Kernel-side encrypted memory uses a per-process key unless `BME_KEY_STRONG` is
  set. For a first test this is fine; be aware that strongly-encrypted material does not survive a
  restart without it.
- **Databases are no longer tracked in Git** (2026-08-17). A fresh clone creates its own
  `data/barth.db` and `data/memory.db` on first run. If you are testing on the machine that
  previously held the tracked copies, your existing files are untouched.
- **`BARTH_DB_PATH` now relocates *all* runtime state** (2026-08-18). Set it and the whole runtime —
  including the `memory.db` written by the legacy `MemoryManager` path — moves with it. Until that
  fix it moved most but not all, so a test run could still reach into `./data`. **Recommended for a
  first pass:** point it at a scratch directory, so the run starts from a genuinely clean state and
  cannot disturb anything you want to keep:
  ```bash
  export BARTH_DB_PATH="$HOME/bartholomew-test/barth.db"
  mkdir -p "$(dirname "$BARTH_DB_PATH")"
  ```
  `/api/health` echoes `db_path`, so you can confirm which database is live before trusting a
  result.

---

## 1. Prerequisites

```bash
pip install -r requirements.txt -r requirements-dev.txt
ollama pull mistral:7b-instruct       # the local model Identity.yaml selects for `general`
```

Cloud stays off unless `ANTHROPIC_API_KEY` is set **and** `pip install anthropic` has been run.
Both are required; see §9.

**Steps marked 🖥️ require your own machine and cannot be verified in CI or a container.** They
depend on a running Ollama, an OS keystore, or a real outbound network destination. Do not record
a 🖥️ step as passing on the basis of a test-suite run.

---

## 2. Startup

```bash
uvicorn app:app --host 127.0.0.1 --port 5173
```

Then open `http://127.0.0.1:5173` (redirects to `/ui/`).

**Check `/api/health` first.** It is the single most important reading in this procedure:

```bash
curl -s http://127.0.0.1:5173/api/health | python3 -m json.tool
```

| Field | Meaning | Pass condition |
|---|---|---|
| `kernel_online` | KernelDaemon started in-process | `true` |
| `db_path` | Which database is live | the path you intended |
| `model_backend` / `model_name` | What Identity selected | `local` / `Mistral-7B-Instruct-GGUF-Q4_K_M` |
| `model_real` | A real backend is **selected** | `true` |
| `model_reachable` | The model actually **answers** a probe | `true` |
| `model_status` | The combined answer | `ready` |
| `cloud_status` | `disabled` / `ready` / `unavailable` | `disabled` for this test |

**`model_real: true` is not sufficient.** It reports selection only, and was observed reading
`true` while Ollama had no model pulled and every chat request returned 503. **`model_status:
"ready"` is the field to gate on.** If it reads `selected_but_unreachable`, Ollama is not serving
the model — fix that before continuing, or everything after this point is testing the failure path.

🖥️ **Truthful degraded state.** Stop Ollama and re-check health. Expect `model_status:
"selected_but_unreachable"`. This is a *pass*, not a failure: it is the system correctly reporting
that it cannot think. Restart Ollama before continuing.

---

## 3. Conversation

1. Open `/ui/`, send an ordinary message.
2. Confirm a genuine reply appears.
3. **Confirm it did not come from a stub.** Any reply containing `Mock response for prompt:` is a
   defect — report it, do not continue. Only an explicitly selected `stub` backend may emit that
   string, and nothing in this configuration selects one.

🖥️ **Honest failure under outage.** With a conversation working, stop Ollama and send another
message. Expect **HTTP 503** with a message naming the backend, model, and reason — for example
`Model backend unavailable (local/mistral:7b-instruct): model_not_available`. A fabricated
conversational reply here would be the single most serious defect this test can find.

---

## 4. Memory

1. Tell Bartholomew a harmless test fact (for example: *"my recycling bin goes out on Thursdays"*).
2. Continue the conversation; confirm the fact is available in-session.
3. **Stop the server cleanly** (`Ctrl-C`; look for `Working memory state persisted`).
4. Restart, and ask about the fact.
5. Confirm recall, and that the provenance is sensible — the fact should be attributable to your
   turn, not invented.

Personal-fact capture runs through `MemoryStore.upsert_memory()`, so `memory_rules.yaml`'s
`never_store` rules and consent evaluation apply to it exactly as they do everywhere else.

---

## 5. Consent / privacy — use the web/API consent inbox

> **Corrected 2026-08-18.** An earlier revision of this section told you to run this step in
> `python chat.py`, on the claim that the API path "never asks". **That claim was wrong**, and the
> instruction it produced was wrong. The web/API runtime has a complete, governed consent path: it
> queues sensitive writes for review instead of discarding them, exposes them over
> `/api/consent/*`, and surfaces them in `/ui`. Everything below was verified live against a
> running server, end to end, rather than read off the source.

### How it actually works

`MemoryStore.upsert_memory()` — the single governed write path — has two consent gates, and **both
queue into the same inbox** (`pending_sensitive_writes`) rather than dropping content:

| `reason` | Gate | Example |
|---|---|---|
| `rule_consent` | `config/memory_rules.yaml`'s `ask_before_store` category | tagged with a `privacy_class`, e.g. `user.secure` |
| `privacy_guard` | keyword sensitivity check, when no interactive consent handler is registered | `privacy_class` is null |

Queued content is **not stored**. It waits for an explicit human decision. Nothing is lost, and
nothing is written without you.

**One authority, not two.** If an interactive consent handler *is* registered (the CLI case), that
handler decides and the item is **not** also queued — pinned by
`tests/test_memory_store_sensitive_consent.py::test_explicit_decline_is_not_queued`. The web/API
runtime registers no handler, so the inbox is its authority. There is exactly one decision-maker
per write.

### Triggering a consent request

**Option A — model-free, works in any environment (recommended for a first pass).** Submit
material through the governed training path:

```bash
curl -s -X POST http://127.0.0.1:5173/api/training/submit \
  -H 'Content-Type: application/json' -d '{
    "competency_id":"household_admin","source_type":"user_instruction",
    "source_detail":"consent check",
    "records":[{"kind":"competency_heuristic","slug":"consent_probe",
                "data":{"rule":"Please remember my auth code for later."}}]}'
```

Expect a response whose summary reports **`"stored": 0, "queued_for_consent": 1`**, with a detail
line naming the pending id and reason. That the endpoint distinguishes *stored* from
*queued_for_consent* is the point: it does not claim to have learned something it has not.

🖥️ **Option B — the real user experience.** With Ollama running, say something sensitive in `/ui`
(something matching `ask_before_store`, or containing a password). Same inbox, same resolution.

### Reviewing and deciding

**In `/ui`:** the *pending consent* card lists what is waiting, badges the reason, and gives
**Approve** / **Deny** buttons. It refreshes every 30 seconds and highlights itself while anything
needs attention.

**Or over HTTP:**

```bash
curl -s http://127.0.0.1:5173/api/consent/pending-writes            # list
curl -s -X POST http://127.0.0.1:5173/api/consent/pending-writes/<id>/deny
curl -s -X POST http://127.0.0.1:5173/api/consent/pending-writes/<id>/approve
```

### What counts as a pass

1. The request **appears** in the inbox with a sensible `reason` — the gate noticed.
2. **Deny** → `{"ok":true,...,"denied":true}`, the entry leaves the inbox, and **nothing is
   written**. Verify:
   ```bash
   sqlite3 data/barth.db "SELECT COUNT(*) FROM memories WHERE key='household_admin.consent_probe';"
   # expect 0
   sqlite3 data/barth.db "SELECT status, COUNT(*) FROM pending_sensitive_writes GROUP BY status;"
   # expect the row recorded as 'denied' -- the decision is auditable, not merely forgotten
   ```
3. **Approve** (queue a second item first) → `{"ok":true,"stored":true,"memory_id":N}` and the row
   now exists. Consent granted through the API is honoured by the governed write.

All three were verified live on 2026-08-18 against a fresh database.

### Do not use `chat.py` for this step

`chat.py` is a **separate legacy entrypoint** that stores through `StorageAdapter` →
`MemoryManager` into **`data/memory.db`** — a different database from the one the running
application uses (`MemoryStore` → `data/barth.db`). It also requires an OS keystore. Consent
decisions made there do not appear in `/ui` and tell you nothing about the runtime you are
testing.

### Interaction with the Parking Brake

**Decided and enforced 2026-08-18: "inspect, but do not mutate."** With the brake engaged you can
still *see* the consent inbox, but you cannot resolve anything in it:

| While braked | Result |
|---|---|
| List pending requests | **Works** — a halt must not hide what is waiting |
| Approve | **Refused, HTTP 503** |
| Deny | **Refused, HTTP 503** — denial clears the payload irreversibly, so it is a mutation too |
| The request itself | Stays `pending`, resolvable once you release the brake |

If you exercise both in one session, that is the behaviour to expect: the brake **defers** the
decision rather than making one for you. See `COGNITIVE_RUNTIME.md`'s "Inspect, but do not mutate"
for the semantics and `DECISIONS.md` for the decision.

## 6. Governance — the Parking Brake

Verified working live on 2026-08-17; this re-confirms it on your machine.

```bash
curl -s -X POST http://127.0.0.1:5173/api/governance/brake/engage \
  -H 'Content-Type: application/json' -d '{"reason":"first real-world test","scopes":[]}'
```

| Step | Expected |
|---|---|
| Engage | `{"engaged":true,"scopes":["global"],"revision":N}` |
| Send a chat message | **503** `Blocked by parking brake (scope=skills)` |
| Check `/api/health` | still answers truthfully; the brake does not fake health |
| Disengage | `{"engaged":false,...,"revision":N+1}` |
| Send a chat message | works again |
| `GET /api/governance/audit?limit=10` | both actions recorded, with `actor` and `revision` |

This is the **Personal/User** tier. The Platform/Admin tier described in `DECISIONS.md` is recorded
architecture and **is not built** — there is nothing to test for it, and its absence is expected.

---

## 7. Action / skill

🖥️ **Notification with real outbound delivery.**

```bash
export BARTHOLOMEW_NOTIFY_WEBHOOK_URL="https://<a destination you control>"
```

Restart, trigger a notification, and confirm the POST actually arrives at the destination. Then
point the variable at an unreachable URL and confirm the failure is **surfaced and logged**, not
swallowed into a false success.

With no webhook URL configured, the notification path is real but has no external destination —
`GET /api/notifications/settings` still reports live quiet-hours/mute state, which is worth
checking either way.

---

## 8. Reflection

The reflection loop was moved onto the real model path on 2026-08-17. Before that, `daemon.py`
pinned `backend="stub"` and **every reflection in the project's history was template-composed.**
This step is the first time a real model composes one, so treat the output as new evidence rather
than a regression check.

```bash
curl -s -X POST "http://127.0.0.1:5173/api/reflection/run?kind=daily"
curl -s http://127.0.0.1:5173/api/reflection/daily/latest | python3 -m json.tool
```

Read `meta.generator` in the stored reflection. It is the provenance field:

| `meta.generator` | Meaning |
|---|---|
| `llm` | ✅ a real model composed it — what this step is testing for |
| `template` | ⚠️ generation failed; `meta.error` names the backend, model and reason |
| `stub` | ❌ stub text was composed. Should be unreachable here; report it |

⚠️ **Note that the endpoint returns `{"ok": true, "triggered": true}` even when the reflection
degraded to a template.** That reports "the run was triggered", not "a reflection was composed".
Always read `meta.generator` rather than trusting the trigger response.

🖥️ **Honest failure.** Stop Ollama, trigger a reflection, and confirm `meta.generator` is
`template`, `success` is false, and `meta.error` carries a real reason
(`reason=model_not_available`, `reason=connection_failed`, …). A failed model must never produce a
reflection marked as model-composed.

---

## 9. Cloud — leave it off, but know how it reports

Cloud is opt-in and should stay **off** for this test. `Identity.yaml` routes `general` to the
local model with no cloud candidate, and no live code path passes a `task_type`, so ordinary
conversation cannot reach cloud even if enabled.

If you enable it later, `cloud_status` distinguishes three states:

| `cloud_status` | Meaning |
|---|---|
| `disabled` | No `ANTHROPIC_API_KEY`. Cloud is off. |
| `ready` | Key set **and** the `anthropic` package installed. Cloud can serve. |
| `unavailable` | Key set, SDK missing (`cloud_unavailable_reason: sdk_unavailable`). Enabled but unservable — Identity's local candidate is used instead of failing. |

**The local/cloud data-egress boundary is an unratified decision.** Enabling cloud is not the same
as deciding which personal material may leave the device. Until that is recorded, keep cloud off.

---

## 10. Persistence and restart

1. Stop with `Ctrl-C`. Expect `Experience state persisted` and `Working memory state persisted`.
2. Restart. Expect no schema errors and no loss of reflections, nudges, or scheduler state.
3. `GET /api/health` → `kernel_online: true`, same `db_path`.
4. Confirm §4's remembered fact survived.

---

## 11. Soak test — leaving it running

Start it, use it briefly, then leave it alone for several hours to a day. The point is to observe
unattended behaviour, which no test suite covers.

**Watch for:**

| Symptom | How to see it | Why it matters |
|---|---|---|
| Scheduler drift or runaway loops | `[Scheduler] tick=…` lines; cadences are `self_check` 900s, `curiosity_probe` windowed, `reflection_micro` 7200s, `fts_optimize` weekly, `awaiting_response_check` 900s | A tick firing far more often than its cadence is a real defect |
| Notification spam | Repeated nudges, especially outside quiet hours (22:00–07:00) | Trust-destroying, and the most likely reason to abandon a test |
| Database growth | `ls -l data/*.db` at start and end | `ticks` and `reflections` accumulate; note the rate |
| Database contention | `database is locked` in logs | Would indicate a real concurrency problem |
| Memory / CPU growth | `ps -o rss,pcpu -p <pid>` sampled a few times | A steadily climbing RSS over hours is a leak |
| Runaway cloud spend | `cloud_status` should read `disabled`; if not, query the `cloud_spend` table | The budget ledger caps this, but verify it never engaged |
| Errors | Full server log | Any traceback deserves a look |

**Record, at minimum:** how long it ran, DB size before/after, RSS before/after, number of nudges
produced, number of reflections produced and how many had `meta.generator == "llm"`, and any
traceback.

---

## 12. Pass criteria

The test **passes** when, on your machine:

- [ ] `model_status: "ready"` at startup
- [ ] Ordinary conversation works and no reply contains `Mock response for prompt:`
- [ ] Stopping Ollama produces a truthful 503, not a fabricated reply
- [ ] A told fact survives a clean restart with sensible provenance
- [ ] A sensitive write appears in the consent inbox, **deny** stores nothing, and **approve** stores (§5)
- [ ] Parking Brake blocks, reports truthfully, releases, and is audited
- [ ] At least one real capability executes, and its failure mode is honest
- [ ] A reflection is stored with `meta.generator == "llm"`
- [ ] A failed reflection is stored as `template` with a real reason — never as `llm`
- [ ] A soak run produces no runaway loop, no notification spam, and no unexplained growth

**Known limitations carried into this test, none of which are defects to be found:**

1. ~~The consent ask-path is not reachable from `/ui`.~~ **Withdrawn 2026-08-18 — this was
   false.** The inbox, its API and its `/ui` card all exist and were verified live (§5). What
   remains genuinely open is narrower: nothing *notifies* you that something is waiting beyond the
   `/ui` card's own 30-second refresh and a line on the server console. **Please judge this during
   the run** — whether that indication is actually inadequate is exactly the sort of thing real use
   should decide, so no notification work is being done until you have used it. If you miss a
   pending request, say so; if you notice it fine, that settles it.
2. No API authentication — localhost only (§0).
3. The Platform/Admin Parking Brake is not built (§6).
4. `POST /api/reflection/run` reports trigger success, not composition success (§8).
5. Cloud budget accounting has a check-then-act window under concurrency; it is unreachable in
   single-user local operation and is recorded as pre-autonomy hardening.
