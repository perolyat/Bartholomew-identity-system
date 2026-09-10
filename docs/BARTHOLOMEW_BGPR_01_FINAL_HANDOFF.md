# BGPR-01 — Golden Path Runtime Repair

Session identity: **BGPR-01 — Golden Path Runtime Repair** (bounded repair package; not Wave 4).

## Starting State

| Item | Value |
|---|---|
| Repository | `https://github.com/perolyat/Bartholomew-identity-system` (verified via `git remote -v`) |
| Base | `origin/main` = `68dd88bbaae4010453d72666764d4ea859404b01`, the PR #101 (Wave 3) merge commit named in the brief. `HEAD..origin/main` and `origin/main..HEAD` were both empty at session start. |
| Branch | `claude/bgpr-01-golden-path-runtime-f4enz1` (the designated development branch for this session), created from that SHA |
| Worktree | Clean at start (`git status --short` empty). A dedicated Python 3.11 venv (`.venv-bgpr`, untracked) was built from `requirements.txt` + `requirements-dev.txt`, the install `docs/FIRST_REAL_WORLD_TEST.md` documents. |
| Wave 3 delta on the chat path | `git diff 72ed19a 68dd88b` touches neither `/api/chat`, `_model_health`, `ModelRouter`, `LLMAdapter` nor the Orchestrator. Wave 3 added the operator router to `app.py` and memory framing to `runtime_contract.py`. This is therefore **not a Wave 3 regression in the chat files**; the defects below predate Wave 3 and were exposed by the clean-environment test. |

### Initial reproduction

This container has no Windows host and cannot reach `ollama.com` / `registry.ollama.ai` (egress policy), so the observed failure was traced with an **in-process fake Ollama** (`GET /api/tags`, `POST /api/show`, `POST /api/generate`, `POST /api/chat`, real response shapes, per-endpoint latency) and the real Bartholomew runtime: `uvicorn app:app --port 5173`, fresh `BARTH_DB_PATH` outside the repo, fresh `BARTH_DATA_ROOT`, the exact message *"Hello Bartholomew. Please reply with only: READY"*.

| Run (unrepaired `68dd88b`) | Fake Ollama | `/api/health` `model_status` | `/api/chat` | What it proves |
|---|---|---|---|---|
| A | healthy, fast | `ready` | **200**, reply `READY` | Wiring is correct: Identity → `ModelRouter` → `LLMAdapter` → `mistral:7b-instruct` → runtime contract → UI-shaped reply. Request sequence seen by Ollama: `tags` (probe), `tags` (pre-flight), `generate`. |
| D | `/api/tags` takes 3 s | **`selected_reachability_unknown`** (the UI renders exactly *"unknown — cannot tell whether the model will answer"*) | 200 | Readiness disagreed with generation: the probe's own 2 s bound expired while the adapter's 5 s listing bound did not. |
| D2 | `/api/tags` takes 6 s | `selected_reachability_unknown` | **503** `model_not_available. [ERROR] Model 'mistral:7b-instruct' not found. Run: ollama pull mistral:7b-instruct` | **Both observed symptoms from one cause.** The listing call timed out; `generate()` reported that as a *missing model* although the model was installed and generation would have succeeded. |
| E | healthy; `OLLAMA_HOST=127.0.0.1:11434` (Ollama's own documented, scheme-less form) | `selected_but_unreachable` | **503** `model_not_available … Run: ollama pull` | A scheme-less host produced `MissingSchema` inside `requests`, also reported as "model not found". |
| C | generation takes 65 s | `ready` before; **`/api/health` unanswerable (`000`) while the chat was in flight** | no answer within 120 s; server log: `[Scheduler] tick=self_check ok=1 dur_ms=120000`, `Daily reflection used fallback: … reason=timeout` | The chat generation and the nightly reflection's generation both ran **on the asyncio event loop**, freezing every other request (health polls, the UI) for the whole model call; the fixed 60 s bound then failed the call. |

## Root Cause

`/api/chat` returned 503 because `ModelRouter.route()` raised `ModelBackendError` from the **local Ollama adapter's HTTP layer**, not because routing, model selection, the model-name mapping, the Identity `ollama_enabled` flag, or governance were wrong (run A proves each of those correct on a fresh database). The adapter turned a healthy-but-slow, busy, or unusually addressed Ollama into a false failure, and the health surface held Ollama to a stricter clock than the adapter did:

1. **False "model not found" (the 503 text a person would have seen).** `LLMAdapter.generate()` first called `GET /api/tags` (5 s bound) and treated *any* failure of that call — timeout, connection error, proxy error, `MissingSchema` from a scheme-less `OLLAMA_HOST` — as "model not installed", returning `error=model_not_available` with a `ollama pull` instruction. The router raised `ModelBackendError(reason="model_not_available")` and the route answered 503. On the Windows machine the model *was* installed and Ollama *did* answer the person's own shell, which is exactly this signature.
2. **Readiness "unknown" while chat was possible.** `_probe_model_reachable()` bounded its probe at 2 s while the adapter's listing bound was 5 s. Any listing between 2 s and 5 s — a cold Ollama, or an Ollama whose host is saturated by a 7B model on CPU — produced `selected_reachability_unknown`, which the UI renders as *"unknown — cannot tell whether the model will answer"*.
3. **The API froze during every model call.** In the kernel path, `app.py`'s `_respond()` called the synchronous `orch.handle_input()` directly inside the coroutine, so the whole event loop blocked for the duration of the Ollama round trip (tens of seconds on CPU). The nightly reflection (`KernelDaemon._run_daily_reflection` / `_run_weekly_reflection`, fired by the dream loop inside `config` window `21:00–23:00` kernel time, on a fresh database at the first check) did the same for up to two full generation timeouts. A frozen API is why the presence line went stale and why the tester's next move was a refresh.
4. **A 60 s generation bound that a cold CPU load can exceed.** Ollama loads a 4 GB model into memory on the first request after start; with prompt evaluation on CPU that can pass 60 s, which the adapter reported as `timeout` — truthful, but it fails the Golden Path on the first message of a fresh runtime.

Which of (1) and (4) carried the exact 503 body on the Windows machine cannot be settled from the evidence supplied: the uvicorn access line was the only server-side record, and the runtime never logged the reason. Both are reproduced above, both are repaired, and the reason is now written to the runtime log so the next run pins it in one line.

Ruled out with evidence: Identity `ollama_enabled` (true, read correctly); mapping `Mistral-7B-Instruct-GGUF-Q4_K_M → mistral:7b-instruct` (applied in run A); adapter construction (present, run A); `select_route()` task-type selection (`local` / `local_primary`, run A); the platform halt tier (inert on a loopback install: `platform_tier_active()` is false unless non-loopback or auth is enabled); the admission middleware (would have produced `Kernel not available`, and `kernel_online` was true); the parking brake (the brake status the UI showed was *released*, and the runtime contract's governance record would read `governance_denied`).

## Repair

| File | Change | Why |
|---|---|---|
| `identity_interpreter/adapters/llm_stub.py` | `generate()` no longer performs a separate listing call before generating; the generation call's own outcome is the answer. Ollama's `404` for a missing model is classified `model_not_available` (with the `ollama pull` hint kept); connection errors are `connection_failed`; timeouts are `timeout` and name the knob. The same classification covers the optional `ollama` Python client (`ResponseError.status_code`, built-in `ConnectionError`, httpx timeout classes). | Defect 1. |
| same | `resolve_ollama_base_url()` accepts every `OLLAMA_HOST` form Ollama documents (`host`, `host:port`, `:port`, `scheme://host[/]`); a bind-all address (`0.0.0.0`, `[::]`) is read as loopback for a client; default is Ollama's own `http://127.0.0.1:11434`. Loopback targets never go through an inherited `HTTP(S)_PROXY` (`requests.Session.trust_env=False`; `trust_env=False` for the optional client). | Defect 1 (run E) and the "works from my shell, fails from Bartholomew" class generally. |
| same | Generation read bound configurable: `BARTH_OLLAMA_TIMEOUT_SECONDS` (default 120 s; connect bound 5 s). Listing bound configurable: `BARTH_OLLAMA_LIST_TIMEOUT_SECONDS` (default 5 s). | Defect 4. A hung provider still becomes a truthful `timeout`. |
| same | New `probe(model)` → `{reachable: True/False/None, reason, detail, host, model, ollama_model}` using the same listing call, bound and reason vocabulary as generation. `_model_exists()`/`is_available()` unchanged in contract. | Defect 2: readiness answers from the adapter's own truth. |
| `bartholomew_api_bridge_v0_1/services/api/app.py` | `_respond()` runs `orch.handle_input()` via `asyncio.to_thread` (the no-kernel branch already did). | Defect 3. |
| same | `_probe_model_reachable()` uses `adapter.probe()`, with its bound derived from the adapter's listing bound plus 0.5 s (never stricter than the adapter); a probe timeout is `probe_timeout`. `/api/health` gains `model_reachability_reason` and `model_host`. | Defect 2, and a tester can read *why* from health. |
| same | `_model_backend_failure_detail()` builds the 503 body and logs `WARNING /api/chat 503: … backend= model= reason= host= detail=` (reaches the console under uvicorn's default logging). | The access line was the only record; now the reason is beside it. |
| `bartholomew/kernel/daemon.py` | `_run_daily_reflection` / `_run_weekly_reflection` compose (construct `ReflectionGenerator` + generate) inside `asyncio.to_thread`; the `generate_*` call keeps no `backend=` override (pinned by `tests/test_reflection_model_path.py`). | Defect 3 (the `dur_ms=120000` tick). `to_thread`, not the shared single-worker blocking executor, so a minutes-long model call cannot queue every governed SQLite read. |
| `docs/REFLECTION_GENERATION.md` | One line: the documented default host. | Truthfulness of the doc that names the default. |
| `tests/test_bgpr01_golden_path_local_chat.py` | New (see next section). | Regression guard. |

Not changed, deliberately: the UI (its presence logic was truthful; it rendered what health said), `ModelRouter`/`select_model` (correct), governance and the runtime contract (governance still runs before `respond_fn`; proven by a new test), the reflection *policy* (when it fires), Identity.yaml, dependencies.

### Post-repair reproduction (same fake-Ollama harness, same fresh-DB procedure)

| Run (repaired) | Fake Ollama | `/api/health` | `/api/chat` |
|---|---|---|---|
| A | healthy, fast | `ready`, reason `None`, `model_host` `http://127.0.0.1:11434`; **`/api/health` answered in 0.02 s while the chat was in flight** | 200 `READY`; Ollama saw one `generate` (no pre-flight listing) |
| D2 | `/api/tags` takes 6 s | `selected_reachability_unknown`, reason **`timeout`** (the absence of a reading, truthfully labelled) | **200 `READY`** — generation is no longer gated on the listing |
| C | generation takes 65 s | `ready`; **`/api/health` answered in 0.04 s during the generation**; the nightly reflection ran concurrently without freezing the API | **200 `READY` after 65.05 s** (was: no answer in 120 s) |
| E | `OLLAMA_HOST=127.0.0.1:11434` | `ready`, `model_host` `http://127.0.0.1:11434` | **200 `READY`** (was: 503 "model not found") |
| G | model not installed | `selected_but_unreachable`, reason **`model_not_available`** | 503 `model_not_available … Run: ollama pull mistral:7b-instruct`; console: `WARNING … /api/chat 503: … reason=model_not_available host=http://127.0.0.1:11434` |
| H | Ollama down | `selected_but_unreachable`, reason **`connection_failed`**, `model_host` shows the address tried | 503 `connection_failed. [ERROR] Could not connect to Ollama at http://127.0.0.1:11435`; console WARNING line with `reason=connection_failed` |

## Regression Coverage

`tests/test_bgpr01_golden_path_local_chat.py` — 34 tests, no live Ollama, an in-process fake Ollama HTTP server on an ephemeral loopback port (mocks/fakes test wiring and failure semantics only; the live Windows verification below still uses real Ollama):

| Brief requirement | Tests |
|---|---|
| 1. identity-configured `ModelRouter` gets a usable real local adapter | `TestIdentityWiring::test_identity_configured_router_gets_a_usable_real_local_adapter` |
| 2. Identity model selection resolves the expected local model | `TestIdentityWiring::test_identity_model_selection_resolves_the_local_primary` |
| 3. mapping `Mistral-7B-Instruct-GGUF-Q4_K_M → mistral:7b-instruct` | `TestIdentityWiring::test_identity_model_maps_to_the_installed_ollama_name` |
| 4. healthy Ollama + installed model → genuine generation | `TestGenuineGeneration::*` (router, listing-independence, Orchestrator pipeline) |
| 5. adapter/backend failure stays a truthful failure | `TestTruthfulFailure::*` (down → `connection_failed`; missing → `model_not_available` + pull hint; slow → `timeout` naming `BARTH_OLLAMA_TIMEOUT_SECONDS`) |
| 6. no real-backend failure falls back to fabricated output | every failure test asserts the exception is raised and carries no "mock response"; `tests/test_model_backend_honesty.py` still passes |
| 7. `/api/chat` success path returns genuine model output | `TestLiveChatRoute::test_chat_returns_the_genuine_local_model_reply` (live route, real `KernelDaemon`, fresh DB), plus truthful/logged 503s for down and missing-model backends |
| 8. readiness agrees with actual local backend usability | `TestReadinessAgreesWithGeneration::*`, `TestLiveChatRoute::test_readiness_is_unknown_only_for_an_unanswered_probe_and_chat_still_works`, `test_health_probe_bound_is_never_stricter_than_the_adapters` |
| Governance preserved | `TestLiveChatRoute::test_governance_still_gates_chat_ahead_of_the_real_backend` (engaged brake → 503, Ollama never asked; released → 200 `READY`) |
| Event loop not frozen | `TestLiveChatRoute::test_health_answers_while_a_chat_generation_is_in_flight`; `TestReflectionCompositionIsOffLoop::test_daily_reflection_composition_does_not_block_the_event_loop` |
| Host forms | `TestHostResolution::*` |

Existing suites that pin the surrounding contracts and still pass: `test_real_model_path_wiring.py`, `test_model_backend_honesty.py`, `test_hybrid_model_routing.py`, `test_reflection_model_path.py`, `test_stage1_api_endpoints.py`, `test_api_chat_runtime_contract.py`, `test_reflection_generation.py`. Full default suite: **interim revision — the local `-n auto --dist loadfile` run was still executing when this revision was committed; the final figure is recorded in the final revision of this document and by the PR's CI.** `pre-commit run --files …` (black, ruff, hygiene hooks): passed.

## Real Windows Verification

**Not executed in this session.** This session ran in a Linux container with no Windows host, no GPU, and no route to Ollama's model registry, so items 1–16 of the brief's clean-Windows procedure could not be performed here. Everything above that says "verified" was verified against the fake Ollama and the real Bartholomew runtime on Linux. The live Windows run remains the acceptance step and is the user's to perform on the PR head. The procedure, with what to record at each step:

```powershell
# 0. Clean worktree + venv on the PR head
git fetch origin claude/bgpr-01-golden-path-runtime-f4enz1
git worktree add ..\bgpr01 claude/bgpr-01-golden-path-runtime-f4enz1
cd ..\bgpr01
py -3.12 -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt ; pip install -e .

# 1. Fresh DB outside the repo (record: it must not exist yet)
$env:BARTH_DB_PATH = "$env:LOCALAPPDATA\bartholomew-bgpr01\barth.db"
Test-Path $env:BARTH_DB_PATH        # expect False

# 2-3. Runtime
uvicorn app:app --host 127.0.0.1 --port 5173
#   record: "[Kernel] Starting with empty working memory"

# 4-5. UI + readiness
#   open http://localhost:5173/ui/ ; presence line must NOT read "unknown"
Invoke-RestMethod http://127.0.0.1:5173/api/health |
  Select-Object kernel_online, db_path, model_backend, model_name, model_reachable, model_status, model_reachability_reason, model_host
#   expect model_status = ready, model_reachability_reason empty, model_host = http://127.0.0.1:11434

# 6-9. Chat (record the response body and the server console line)
Invoke-RestMethod -Method Post -ContentType 'application/json' `
  -Body '{"message":"Hello Bartholomew. Please reply with only: READY"}' http://127.0.0.1:5173/api/chat
#   expect HTTP 200 and a reply that is the model's own (no "Mock response for prompt")
#   if 503: the console now carries "WARNING ... /api/chat 503: ... reason=<x> host=<y>" -- record it verbatim

# 10-12. Parking brake (record status before/after)
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{"scopes":["global"]}' http://127.0.0.1:5173/api/governance/brake/engage
#   chat again -> expect 503 "Blocked by parking brake (scope=skills)"
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{}' http://127.0.0.1:5173/api/governance/brake/disengage
#   chat again -> expect 200

# 13-15. One harmless Windows action + audit: see "Governed Windows action" below

# 16. Ctrl-C ; expect "[Kernel] Working memory state persisted" and "Application shutdown complete"
```

If the machine loads the 7B model slowly (HDD, low RAM) and the first message still reports `timeout`, set `BARTH_OLLAMA_TIMEOUT_SECONDS=300` for the run and record that; the default is 120 s.

### Governed Windows action

*Interim revision: the step-by-step governed-action procedure (endpoints, arming, approval, companion, verification, audit queries) is being compiled from the actuation package's own code and handoffs and is recorded in the final revision of this document.*

## Out-of-Scope Findings

Recorded, not fixed:

1. **Nightly reflection contends with chat for a single CPU model.** Composition now runs off the loop, but it still occupies Ollama for up to a full generation bound (and a redraft) whenever the dream loop fires inside `21:00–23:00` kernel time on a day with no reflection yet — on a fresh database that is the first check, 60 s after start. A chat sent during that window waits in Ollama's queue and may still time out. Whether reflection should defer while a person is chatting is a scheduling decision, not this repair.
2. **`docs/REFLECTION_GENERATION.md` "Configuration" is stale beyond the one line corrected** (it still tells the reader to pin `backend="ollama"` in the daemon, which `tests/test_reflection_model_path.py` forbids).
3. **`requirements.txt` and `requirements.lock` disagree on the `ollama` client** (`ollama==0.6.0` is only in the lock). Which install a venv used decides whether the adapter uses the `requests` path or the httpx-based client. Both paths are now classified identically, but the two files describe two different environments. Also `requirements.lock` + `requirements-dev.txt` cannot be installed together (`ruff==0.14.2` vs `0.14.3`).
4. **The `ollama` Python client path had no timeout at all** before this repair (`timeout=None`); it now receives the same bound. Not otherwise exercised here because the documented install does not include the client.
5. **`Orchestrator.handle_input()` is now called from worker threads**; it writes `logs/orchestrator/orchestrator.log` and the adapter shares one `requests.Session` across concurrent chats. Both are safe for this usage, but `Orchestrator` was not designed with a thread-safety statement and has none.
6. **The `_kernel is None` chat branch and the kernel branch duplicate their 503 handling** (now via one helper); the branch itself predates this repair.
7. **`platform_halt_check` is not consulted by `GET /api/governance/brake`**, so on a deployment where the platform tier *is* active the UI could show the personal brake released while chat is refused by the platform halt. Inert on a loopback install, so not on this Golden Path.

## Golden Path Gate

Legend: **PASS (here)** = verified in this session against the real runtime with a fake Ollama on Linux; **PENDING WINDOWS** = requires the user's live run on the PR head; the repair does not claim these.

| Gate | Result |
|---|---|
| Clean Windows runtime starts | PENDING WINDOWS (Linux fresh-start: PASS) |
| Fresh DB confirmed | PENDING WINDOWS (Linux: PASS — `fresh db exists before start? no`, `[Kernel] Starting with empty working memory`) |
| UI loads | PENDING WINDOWS (unchanged by this repair; served from `/ui/` as before) |
| Local model readiness is truthful | PASS (here): `ready` for a healthy backend; `selected_but_unreachable` + reason for down/missing; `unknown` only for an unanswered probe, with reason `timeout` |
| Ollama connection works through Bartholomew | PASS (here) against the fake; PENDING WINDOWS against real Ollama |
| `/api/chat` returns genuine local-model response | PASS (here): `READY` from the backend via the real route and runtime contract; PENDING WINDOWS |
| UI displays the response | PENDING WINDOWS (UI unchanged; it renders `reply` and `detail` as before) |
| No mock/stub fallback occurred | PASS (here): every failure path raises; tests assert no "mock response" |
| Parking Brake remains effective | PASS (here): engaged brake → 503, Ollama never asked; released → 200 |
| One harmless existing Windows action works through governance | PENDING WINDOWS (procedure above; not executable on Linux) |
| Action outcome is verified | PENDING WINDOWS |
| Audit/provenance reconstructs the path | PENDING WINDOWS for the action; chat turns: `reflections` (surface `chat`) + working memory, unchanged |
| No unrelated feature work was introduced | PASS: four source files, one doc line, one test module; no UI, provider, schema, dependency or architecture change |

**Overall: NOT COMPLETE until the Windows run above is performed.** This package restores the runtime path and proves it against a faithful fake; the brief's acceptance gate is a live Windows result, which this session could not produce.

## Remaining Risk

- The exact 503 body from the Windows run was never captured, so the trigger there (listing timeout vs cold-load timeout vs host form) is inferred from reproductions, not read. The new WARNING line closes this for the next run.
- If the Windows machine needs more than 120 s for a cold 7B load plus a reply, the first message still returns a truthful `timeout` (knob: `BARTH_OLLAMA_TIMEOUT_SECONDS`).
- Reflection/chat contention for Ollama in the nightly window (Out-of-Scope #1).
- The health probe's bound is now up to 5.5 s, so `/api/health` can take that long when Ollama is slow; the UI polls every 30 s and the value is cached for 10 s.

## Exact PR Head

Interim revision: repair commit `f3ec6010bea663e2163b766d5e7c89a3c7b8ac8e` on `claude/bgpr-01-golden-path-runtime-f4enz1` (PR #102). The final head SHA is recorded in the final revision of this document.
