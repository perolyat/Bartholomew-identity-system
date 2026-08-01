# B7 Implementation — External Request Admission and Detached Work

> Phase B, stage B7. Per `docs/PHASE_B_OVERVIEW.md`'s B7 scope: prevent shutdown from racing
> externally admitted governed work. Required inputs: B4's shared runtime, B5's shutdown sequence —
> both already in place. Per the overview's own note, this stage does not block on B8/B9.

## 1. Ingress inventory, re-checked against the concrete code (not assumed from B0)

`docs/B0_BASELINE_REPORT.md` §6 counted ~30 real API routes. Before wiring an admission gate onto
all of them uniformly, each route file was grep-checked for whether it actually touches `_kernel`'s
in-process objects (the state `KernelDaemon.stop()` tears down) or not:

- **Touches `_kernel` directly** (confirmed via grep for `_kernel.`): `app.py`'s `/kernel/command/{cmd}`,
  `/api/health`, `/api/chat` (via `run_chat_through_runtime_contract`), `/api/nudges/*`,
  `/api/reflection/*`; `routes/self_state.py`'s entire `/api/self/*`, `/api/episodes/*`,
  `/api/persona/*`, `/api/working_memory/*` (via its own `_get_kernel()` helper). All of these are
  admission-gated.
- **Never touches `_kernel`** (confirmed the same way): `/healthz` (pure computed response),
  `/metrics`/`/internal/metrics` (`routes/metrics.py`, reads only the Prometheus registry),
  `/api/liveness/*` (`routes/liveness.py`, reads `app.state` and does independent WAL-safe SQL
  directly against `DB_PATH` via `db_ctx.wal_db()` — safe under concurrent access regardless of
  `_kernel.stop()`'s progress, since WAL mode already guarantees that; admission gating would protect
  nothing here). These three are exempt from gating (`app.py`'s `_ADMISSION_EXEMPT_PATHS`/
  `_ADMISSION_EXEMPT_PREFIXES`), deliberately — not for test convenience, though the exemption also
  happens to be what test isolation needed (see §4).

The exemption is a genuine operational decision, not just a technical one: gating monitoring/health
endpoints would risk an orchestrator (load balancer, Kubernetes liveness probe, Prometheus scraper)
misreading a draining-but-healthy process as hung and escalating to a harder kill — exactly the
outcome a graceful-shutdown mechanism is supposed to prevent.

## 2. `AdmissionGate` (`bartholomew/kernel/admission_gate.py`)

A small, reusable, non-HTTP-specific class — `admit()`/`AdmissionToken.release()`/`freeze()`/`drain()` —
not built directly into the ASGI app, so it's independently testable (`tests/test_admission_gate.py`,
13 tests) without any FastAPI/HTTP machinery involved.

- **Identity-bound admission**: each `admit()` call returns its own `AdmissionToken`, carrying an
  opaque per-call identity (caller-supplied, or an auto-generated UUID). There is no "release by id"
  entry point — only a token's own `release()` method can give back its slot, so one request's
  cleanup can never accidentally release a different request's admission. Not tied to an
  authenticated end-user identity: this codebase has no such identity system for HTTP callers, per
  `docs/B0_BASELINE_REPORT.md`'s ingress inventory — recorded here so a future reader doesn't assume
  more than what actually exists.
- **Exact release ownership**: `release()` is idempotent (matches `DedicatedDbExecutor.close()`'s
  convention from B2) — safe to call from both the happy path and an unconditional `finally`.
- **Freeze then drain, not freeze-and-cancel**: `freeze()` makes every subsequent `admit()` raise
  `AdmissionFrozenError` immediately — no new work can start racing the teardown that's about to
  begin. `drain(timeout)` bound-waits for the in-flight count to reach zero and returns `True`/`False`
  accordingly, mirroring the same confirmed-or-logged-and-proceed pattern `DedicatedDbExecutor.close()`
  and `SchedulerStore.close()` (B2, B5) already established — a `False` return is logged, never
  silently swallowed, and callers must not assume "abandoned" means "gone" (this gate has no mechanism
  to forcibly cancel a request handler's own task; that's the ASGI server's concern, not this
  module's — see "child and detached work" note below).

## 3. Wiring into `bartholomew_api_bridge_v0_1/services/api/app.py`

`@app.middleware("http")` admits every non-exempt request before it reaches its route handler and
releases the token in a `finally` once the response is ready (or the handler raises) — so a token is
always released exactly once, regardless of outcome. A frozen gate returns `503` with a clear
`"Server is shutting down"` body rather than letting the request fall through to a route that might
touch an already-torn-down `_kernel`.

`shutdown()` now does, in order: `await admission_gate.freeze()`, `await admission_gate.drain(timeout=30.0)`,
*then* `await _kernel.stop()` — an in-flight request mid-call into
`run_chat_through_runtime_contract()` (or any other kernel-touching route) is guaranteed to either
finish or be abandoned-and-logged before `_kernel`'s own teardown begins, never racing it.

`admission_gate` is reconstructed fresh inside `startup()` (not a true module-level constant) — a
FastAPI app in this codebase can be started and stopped more than once within a single process
(every `TestClient`-based test does exactly this via its context manager), and reusing a gate a prior
`shutdown()` already froze would permanently `503` every request for the rest of the process's life.
This mirrors `_kernel`/`_kernel_task`'s existing `global`-reassignment pattern in the same function.

## 4. "Child and detached work" — recorded honestly as not currently present

The overview's B7 scope names "child and detached work" alongside admission. Grepped the entire API
layer for `asyncio.create_task`/`BackgroundTasks`: the only detached task in this codebase is
`app.py`'s own `_kernel_task` (`keep_alive()`), which is a daemon-lifetime task, not a per-request
fire-and-forget child spawned by any route handler. No route currently detaches child work that would
need its own admission tracking. `AdmissionGate` is written generically enough (`admit()`'s identity
is caller-supplied, not HTTP-specific) that a future route needing to admit detached child work could
reuse it directly — but nothing here invents tracking for a problem that doesn't exist in the
concrete repository today, matching this project's own "characterize honestly, don't overstate"
posture (the same one `docs/B5_IMPLEMENTATION.md` §4 already used for "Governance write freeze and
drain").

## 5. Verification

`tests/test_admission_gate.py` (13 isolated tests). `tests/test_api_admission_shutdown.py` (4
integration tests): a frozen gate returns 503 for a gated route; `/healthz` and `/api/liveness/*`
survive a freeze; `shutdown()` genuinely waits for an admitted token before stopping `_kernel` (proven
by asserting the shutdown task is *not* done while the token is held, then completing once released);
`shutdown()` proceeds (does not hang forever) once `drain()`'s bound is exceeded by a deliberately
unreleased admission. Full existing test suite passes, including test files that hit the live app
directly without going through `TestClient`'s startup/shutdown lifecycle (`tests/test_liveness_self.py`,
`tests/test_metrics_production_mode.py`, `tests/test_metrics_labeled.py`) — these depend on
`/api/liveness/*`/`/metrics` being genuinely exempt from gating, which is real evidence the exemption
list is correctly scoped, not merely convenient. `black`/`ruff`/`mypy` clean.
