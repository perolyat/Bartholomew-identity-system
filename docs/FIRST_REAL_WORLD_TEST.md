# First controlled real-world test — procedure

> **Status:** Working procedure (2026-08-17). **Non-canonical** — a reference document under
> `docs/`, not one of the 14 canonical SSOT docs. `MASTER_PLAN.md` remains the authority on whether
> this test is authorised and what stage the project is in. This document says *how* to run it and
> *what counts as a pass*, not *that it may proceed*.
>
> **Deployment boundary:** this procedure describes a **personal development prototype running on
> `localhost`**. See §0 before exposing anything to a network.

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

## 5. Consent / privacy — **read this before attempting it**

**This step cannot currently be performed through `/ui`.** The consent handler is registered only
by `chat.py`, the standalone terminal entrypoint; the API path never registers one. With no handler
registered, `request_permission_to_store()` returns `False` unconditionally.

The behaviour is therefore **fail-closed**: sensitive content is **not stored**, and the user is
**not asked**. Nothing unsafe happens — but the ask-and-deny path is not exercisable from the web
UI, so the "deny consent, confirm denial is respected" test reduces to "confirm nothing sensitive
was stored".

Two options:

- **(a) Accept the reduced check for this test.** Mention something the rules classify as sensitive
  in `/ui`, then confirm via `/api/self-state` or the database that it was not persisted. This
  verifies fail-closed behaviour, which is the safety-relevant half.
- 🖥️ **(b) Run the full path in the terminal client instead**, via `python chat.py` — which does
  register a terminal consent handler — and exercise ask → deny → confirm-not-stored there.

Registering a consent handler on the API path is a small, well-understood change. It was
deliberately **not** made as part of this stabilisation work because it is a new user-facing
behaviour rather than a repair, and it needs its own approval. It is the most likely candidate for
the next small piece of work.

---

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
- [ ] Sensitive content is not silently stored (§5, option (a) or (b))
- [ ] Parking Brake blocks, reports truthfully, releases, and is audited
- [ ] At least one real capability executes, and its failure mode is honest
- [ ] A reflection is stored with `meta.generator == "llm"`
- [ ] A failed reflection is stored as `template` with a real reason — never as `llm`
- [ ] A soak run produces no runaway loop, no notification spam, and no unexplained growth

**Known limitations carried into this test, none of which are defects to be found:**

1. The consent ask-path is not reachable from `/ui` (§5).
2. No API authentication — localhost only (§0).
3. The Platform/Admin Parking Brake is not built (§6).
4. `POST /api/reflection/run` reports trigger success, not composition success (§8).
5. Cloud budget accounting has a check-then-act window under concurrency; it is unreachable in
   single-user local operation and is recorded as pre-autonomy hardening.
