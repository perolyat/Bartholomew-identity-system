# B7 — External request admission and detached work

> **Status:** B7 exit deliverable per `docs/PHASE_B_OVERVIEW.md` §5 and `ROADMAP.md`'s Phase B
> stage table. Closes the shutdown gap B5 explicitly could not cover ("does not yet cover
> externally admitted work, since B7 has not introduced request admission") and the last clause of
> §4's shutdown invariant: "externally admitted governed work cannot be silently dropped by
> shutdown."
>
> **Base facts:** drawn from a fresh re-read of `bartholomew_api_bridge_v0_1/services/api/app.py`,
> `routes/self_state.py`, `routes/liveness.py`, and `routes/metrics.py` at plan start — the real
> ingress inventory, re-verified, not assumed from the archived design or from B0's own count.

## 1. Grounded findings that shaped this stage's scope

1. **No detached/child task spawning exists anywhere in the current codebase.** Confirmed by
   direct search: no route handler, in `app.py` or any router, calls `asyncio.create_task()` (or
   any equivalent) to spawn work that outlives the request/response cycle. The archived design's
   `spawn_detached_governed_task`/`AdmissionToken`/`_AdmissionScope` machinery for propagating
   admission through detached child tasks describes a mechanism that does not exist here — every
   external request runs to completion synchronously before its response is returned. This
   substantially narrows this stage's real scope versus the archived design: "child and detached
   work" reduces to "nothing to do," not a token-propagation system to build.
2. **A real, previously-unguarded race, confirmed by direct read of `app.py`'s `startup()`**: the
   module-level `_kernel` global is assigned *before* `await _kernel.start()` is awaited to
   completion. A route handler's pre-existing `if _kernel is None` check (scattered across ~9
   handlers in `app.py`, centralized via `self_state.py`'s `_get_kernel()` for its 26) is therefore
   insufficient on its own — it does not catch the `STARTING` window Phase B stage B5 introduced,
   only the "not constructed yet" case. The same gap exists in reverse at shutdown: `_kernel` is
   never reset to `None` after `_kernel.stop()`, so those same checks pass throughout the entire
   `STOPPING`/`STOPPED` window too.
3. **The real external-ingress inventory, reclassified by whether it touches governed daemon
   state**: `self_state.py`'s 26 routes (all via `_get_kernel()`) and `app.py`'s 9
   kernel-touching routes (`/kernel/command/{cmd}`, `/api/chat`, `/api/conversation/recent`,
   `/api/nudges/*` ×3, `/api/reflection/*` ×3) are real governed ingress. `routes/liveness.py`'s 4
   routes read directly via their own `db_ctx.wal_db()` connection (not through `_kernel` at all)
   and `routes/metrics.py`'s 1 route reads the Prometheus registry only — both written to the
   liveness/health-check convention of staying responsive through startup/shutdown windows, and
   neither depends on daemon lifecycle state. `/healthz` and `/api/health` degrade gracefully
   rather than depending on the kernel. FastAPI's own `/docs`, `/redoc`, `/openapi.json` touch
   nothing kernel-related.

## 2. Architectural decisions

- **A single HTTP middleware chokepoint, not a per-route migration.** Given finding 3's ~35-route
  governed surface, a `Depends()`-based per-route migration was considered and rejected: it would
  require touching every current route body and could silently miss a future route that forgets to
  opt in. `bartholomew_api_bridge_v0_1/services/api/app.py`'s `@app.middleware("http")` wraps
  *every* HTTP request once, at the ASGI layer, regardless of which router registered the route —
  gating all current and future kernel-touching routes uniformly, with an explicit exemption list
  for the health/liveness/metrics/docs surface finding 3 identified as correctly ungated.
- **`RequestAdmission` is identity-bound, not a bare counter** — `docs/PHASE_B_RISK_MAP.md`'s B7
  rows name a real prior-design finding: a `release()` with no identity argument lets any caller
  release any in-flight admission. `try_admit()` returns a fresh, unguessable token; `release()`
  only ever removes that exact token, and is a safe no-op for a foreign, duplicate, or
  already-released one.
- **Owned by `KernelDaemon`, not by `app.py` directly** — matches this stage's own precedent
  (`process_lock`, `blocking_executor`, `governance_store` are all daemon-owned resources), keeps
  `RequestAdmission` testable in isolation from any HTTP framework, and keeps the daemon
  process-topology-agnostic (a future non-FastAPI ingress could reuse the same primitive).
- **The middleware checks `lifecycle_state is RUNNING`, not just `admission.closed`** — per finding
  2, `admission` itself defaults open the instant a `KernelDaemon` is constructed, before `start()`
  has run at all; the lifecycle-state check (not `RequestAdmission`'s own state) is what actually
  closes the `STARTING`-window race.

## 3. What was built

### `bartholomew/kernel/request_admission.py` (new)

`RequestAdmission`: `try_admit() -> str | None` (a fresh token, or `None` if closed — closed calls
must be treated as a refusal, not silently passed through), `release(token)` (identity-bound,
foreign/duplicate/`None`-safe no-op), `close()` (idempotent, stops new admission, doesn't affect
already-admitted tokens), `admitted_count`, and `drain(timeout) -> bool` (waits for every currently
admitted token to be released; `False` on timeout, which callers must treat as "not confirmed
clean," matching Phase B stage B5's verified-not-assumed invariant).

### `bartholomew/kernel/daemon.py`

`KernelDaemon.__init__()` constructs `self.admission = RequestAdmission()` (cheap, no I/O, matching
`process_lock`'s construction pattern). `stop()`'s first action after entering `STOPPING` (before
even the Governance write-fence close) is now `self.admission.close()` followed by
`await self.admission.drain(_ADMISSION_DRAIN_TIMEOUT_S)` (10s) — draining happens while
`governance_store`/`mem`/`blocking_executor` are all still fully intact, so in-flight requests
finish against working resources instead of ones being torn down underneath them. The drain
result now feeds `fully_drained` alongside the existing producer-task/scheduler-store/
blocking-executor checks, so a stuck or lost admission (never released) correctly prevents the
shutdown from being marked clean, the same honesty precedent B5 established for every other
tracked resource.

### `bartholomew_api_bridge_v0_1/services/api/app.py`

New `admission_middleware` (`@app.middleware("http")`): exempts the health/liveness/metrics/docs
paths (finding 3) via `_admission_exempt()`; for everything else, refuses with `503` before the
route handler runs if `_kernel is None`, `_kernel.lifecycle_state is not RUNNING` (closes finding
2's `STARTING`/`STOPPING`-window gap), or `admission.try_admit()` returns `None` (admission closed
— `stop()` has begun). Admits exactly one token per request that passes, releasing it in `finally`
— guaranteed release even on an unhandled exception or a client disconnect, giving `stop()`'s
`drain()` an honest signal to wait for.

## 4. Tests

`tests/test_request_admission.py` (15 tests): the primitive in isolation — admit/release, distinct
tokens, identity-bound release (a foreign or duplicate token never affects a real one — the direct
regression for the risk map's named finding), `close()` behavior, and `drain()` succeeding once
released vs. timing out with an admission still outstanding.

`tests/test_daemon_admission_drain.py` (6 tests): `stop()` closes admission immediately; `stop()`
provably waits for an in-flight admission to release (a background task releases it after a short
delay, and the test confirms `stop()` didn't return before that happened) before completing; a
clean drain marks the shutdown clean; a drain that times out (a token deliberately never released,
with `_ADMISSION_DRAIN_TIMEOUT_S` monkeypatched down for a fast test) does **not** mark it clean;
`admission` defaults open before `start()` has ever run (documenting exactly why the HTTP layer
additionally checks `lifecycle_state`); a double `stop()` is a safe no-op that doesn't re-toggle
admission state.

`tests/test_api_admission_gate.py` (9 tests), against the real live app (`TestClient` + a real
`KernelDaemon`, matching `test_api_chat_runtime_contract.py`'s established pattern): a gated route
(`/api/self`) responds normally while `RUNNING`, and admission count returns to zero after every
request (proving release always happens); `/healthz`, `/api/liveness/self`, and `/metrics` all stay
responsive even with admission closed or `lifecycle_state` not `RUNNING`; `/api/self` and
`/api/chat` are refused with `503` when admission is closed or when `lifecycle_state` is
monkeypatched to `STOPPING`; `/openapi.json` stays responsive regardless of lifecycle state.

Full non-integration/non-slow suite re-run clean after this change; no pre-existing test needed
modification (unlike B6's bridge deletion, this stage added a new gate rather than retiring an old
one, and every pre-existing test already ran with the kernel `RUNNING`).

## 5. Exit condition check

- [x] Every real external ingress point is admission-gated with identity-bound release: the HTTP
  middleware chokepoint covers all current and future routes at once; `RequestAdmission` is
  identity-bound per §2.
- [x] Shutdown drains admitted work deterministically: `stop()` closes admission and awaits
  `drain()` before touching any resource in-flight requests still need, with the outcome honestly
  reflected in the clean-shutdown marker.
- [x] Does not block B1–B4, and did not require B8/B9 to have started first, per this stage's own
  "may proceed once its own inputs are ready" note.

Not required for exit, and not done: child/detached task admission propagation (finding 1 — no
such mechanism exists in the current codebase to propagate through); gating `bartholomew/cli.py`'s
own process-local calls (out of scope — B7 is about *external* ingress, and B6 already covers
CLI-vs-daemon safety through the process lock); the sight/voice `runtime_contract.py` gates
(already confirmed unreachable by B4, not reopened here, same as B6's own deferral).
