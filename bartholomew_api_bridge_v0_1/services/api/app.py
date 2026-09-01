import asyncio
import atexit
import datetime as dt
import ipaddress
import os
import re
import sys
import time as _time
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import JSONResponse, RedirectResponse

# Raised by ModelRouter when a real backend cannot generate. Imported at
# module scope so the chat route can translate it into a truthful 503
# instead of a 500 with a stack trace, or -- worse -- mock text.
from identity_interpreter.orchestrator.model_router import ModelBackendError

# Load timezone from kernel config (single source of truth)
with open("config/kernel.yaml", encoding="utf-8") as f:
    _kernel_cfg = yaml.safe_load(f)
    _tz_name = _kernel_cfg["timezone"]

# tz support (prefer zoneinfo, fallback to dateutil.tz)
try:
    from zoneinfo import ZoneInfo  # py>=3.9

    TZ = ZoneInfo(_tz_name)
except Exception:
    from dateutil import tz

    TZ = tz.gettz(_tz_name)

from prometheus_client import PlatformCollector, ProcessCollector

from bartholomew.platform.exposure import assert_exposure_is_safe, describe_exposure
from bartholomew.platform.http_identity import (
    authenticate_and_authorize,
)
from bartholomew.platform.http_identity import error_response as platform_error_response
from bartholomew.platform.principal import (
    AuthenticationError,
    AuthorizationError,
    AuthUnavailableError,
)
from bartholomew.platform.route_policy import UnclassifiedRouteError

from . import db_ctx
from .db import DB_PATH, resolve_db_path
from .models import ChatIn, ChatOut, ConversationList
from .routes import (
    auth,
    awaiting_response,
    consent,
    governance,
    inbound,
    liveness,
    memory,
    metrics,
    notifications,
    onboarding,
    self_state,
    training,
)
from .routes.metrics import BARTHOLOMEW_TICKS_TOTAL, KERNEL_TICKS_TOTAL, REGISTRY


def is_truthy(val: str | None) -> bool:
    """Check if an environment variable value is truthy."""
    if not val:
        return False
    return val.lower() in ("1", "true", "yes", "on")


# Import orchestrator
Orchestrator = None
try:
    from identity_interpreter.orchestrator.orchestrator import Orchestrator as _Orch

    Orchestrator = _Orch
except Exception:
    # Soft fallback stub so the API doesn't crash during wiring
    class _StubOrchestrator:
        def handle_input(self, msg: str) -> str:
            return f"[tone: warm] [emotion: helpful] (stub) You said: {msg}"

    Orchestrator = _StubOrchestrator

app = FastAPI(title="Bartholomew API v0.1", version="0.1.0")

# Include routers
app.include_router(auth.router)
app.include_router(liveness.router)
app.include_router(self_state.router)
app.include_router(governance.router)
app.include_router(notifications.router)
app.include_router(consent.router)
app.include_router(memory.router)
app.include_router(awaiting_response.router)
app.include_router(onboarding.router)
app.include_router(training.router)

# Governed inbound capture (Session D). Deliberately NOT added to
# `_ADMISSION_EXEMPT_PATHS`: unlike health and static UI, capture writes
# governed state and needs a live kernel, so it must be refused during the
# startup and shutdown windows like every other real ingress point.
app.include_router(inbound.router)

# Metrics: mount under /internal in production mode (METRICS_INTERNAL_ONLY=1)
# to restrict access; default (dev/test) leaves it at /metrics (unauthenticated)
metrics_internal_only = is_truthy(os.getenv("METRICS_INTERNAL_ONLY"))
app.include_router(metrics.router, prefix="/internal" if metrics_internal_only else "")

# Serve the minimal UI from this app, at /ui.
#
# The UI resolves its API base as `location.origin` (ui/minimal/index.html),
# so it only works when served from the same origin as the API. Nothing served
# it before this, and QUICKSTART.md pointed at the file path on disk -- opened
# as file://, location.origin is the string "null" and every fetch fails; served
# from a second port, every call 404s against that port instead of the API. The
# panels and the chat box were unreachable either way.
#
# Path is resolved from this module's location rather than the working
# directory, so `uvicorn app:app` works from anywhere.
_UI_DIR = Path(__file__).resolve().parents[2] / "ui" / "minimal"
if _UI_DIR.is_dir():
    # html=True serves index.html for /ui/ itself, not just /ui/index.html.
    app.mount("/ui", StaticFiles(directory=str(_UI_DIR), html=True), name="ui")


@app.get("/", include_in_schema=False)
async def root_redirect():
    """Send the bare host to the UI, so `localhost:5173` opens something."""
    return RedirectResponse(url="/ui/")


# Register atexit handler for WAL cleanup on shutdown
atexit.register(lambda: db_ctx.wal_checkpoint_truncate(DB_PATH))

# Paths exempt from external request admission (Phase B stage B7; see
# docs/B7_EXTERNAL_REQUEST_ADMISSION.md) -- health/liveness/metrics/docs
# endpoints must keep answering during startup/shutdown windows (the
# k8s-liveness-probe convention this repository's own liveness routes are
# written for), and don't touch _kernel's governed state at all. Everything
# else that isn't one of these is real external ingress into governed
# daemon state, and is gated below.
#
# /api/onboarding (Stage 1, S1.6) joins this list for the same reason:
# static deployment-guide content with no _kernel dependency at all (see
# routes/onboarding.py's module docstring).
#
# /ui and / join it on the same grounds: static files and a redirect, no
# _kernel access. Serving them under admission would also make the shell
# itself 503 during startup/shutdown, which is precisely when a tester needs
# the page to load so its panels can report what the kernel is doing.
_ADMISSION_EXEMPT_PATHS = frozenset(
    {"/", "/healthz", "/api/health", "/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"},
)
_ADMISSION_EXEMPT_PREFIXES = ("/api/liveness", "/api/onboarding", "/ui")


# =============================================================================
# Network access boundary
# =============================================================================
#
# Decision (2026-08-26): the unauthenticated API bridge must be loopback-only
# by default. This resolves a contradiction that already existed in the
# repository -- `DECISIONS.md` and `INTERFACES.md` both state that the API has
# no authentication and "must not be exposed beyond localhost", while the
# Dockerfile bound 0.0.0.0 and QUICKSTART.md demonstrated a LAN bind -- in
# favour of the safer reading the canonical documents already held.
#
# This is NOT authentication and must not be mistaken for it. It is a network
# boundary: it decides *where the API is reachable from*, not *who is asking*.
# CORS is likewise not a boundary here -- it constrains browser origins only,
# and any non-browser client ignores it entirely.
#
# Enforced in two places, because either alone is insufficient:
#
#   1. The bind address (`resolve_bind_host()`), so the normal launch path
#      never listens on a non-loopback interface.
#   2. The request boundary (in `admission_middleware`, the existing single
#      chokepoint for external request admission -- not a second one), so a
#      process launched by hand with `--host 0.0.0.0`, or reached through a
#      container port publish, still refuses non-loopback callers.
#
# A deliberate override exists for container and development use. It is opt-in
# only, never a default, and says plainly what it is exposing.

ALLOW_NON_LOOPBACK_ENV = "BARTH_API_ALLOW_NON_LOOPBACK"
BIND_HOST_ENV = "BARTH_API_HOST"
DEFAULT_BIND_HOST = "127.0.0.1"

# Kept as a conspicuous notice, but no longer as the claim it used to make.
# It said "This API has NO AUTHENTICATION", which was true when the boundary
# was the only protection and is now false: a non-loopback bind forces
# authentication and TLS on, and no environment variable downgrades either
# (bartholomew/platform/exposure.py). A security banner that overstates the
# danger is not harmlessly cautious -- it trains operators to discount the
# banners that are accurate.
_NON_LOOPBACK_WARNING = (
    "\n"
    "  ****************************************************************\n"
    "  *  Bartholomew's API is bound to a NON-LOOPBACK address.       *\n"
    "  *                                                              *\n"
    "  *  Authentication and TLS are therefore ENFORCED and cannot    *\n"
    "  *  be disabled while this bind is in effect. Anything that can *\n"
    "  *  reach this port and hold valid credentials can read,        *\n"
    "  *  correct, delete and export that user's personal memory,     *\n"
    "  *  and can release their Parking Brake.                        *\n"
    "  *                                                              *\n"
    "  *  Bound to: %s\n"
    "  *  Enabled by: %s=1\n"
    "  ****************************************************************\n"
)


def is_loopback_host(host: str | None) -> bool:
    """True if `host` names a loopback interface (or nothing at all)."""
    if not host:
        return False
    candidate = host.strip().strip("[]")
    if candidate in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False


def _is_local_peer(client_host: str | None) -> bool:
    """
    True if this request did not arrive over a network from somewhere else.

    Three cases, and only the third is a real remote caller:

    * `None` -- no peer address. A UNIX-domain socket, or an ASGI transport
      with no network under it. Local by construction.
    * not an IP address -- Starlette's in-process TestClient reports the
      sentinel ``"testclient"``. Nothing that crossed a TCP socket can look
      like this: uvicorn fills `client` from the socket peer name, which is
      always an IP for TCP. So a non-IP peer never came over the network.
    * an IP address -- a genuine network peer, and it must be loopback.

    Rejecting the first two blocked every in-process caller, including the
    repository's own API tests, without adding any protection: a LAN caller
    always presents a routable IP and is still refused.
    """
    if client_host is None:
        return True
    candidate = client_host.strip().strip("[]")
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        # Not an address at all, so it never crossed a socket.
        return True
    return is_loopback_host(client_host)


def non_loopback_allowed() -> bool:
    """True only when a non-loopback boundary has been deliberately enabled."""
    return is_truthy(os.getenv(ALLOW_NON_LOOPBACK_ENV))


def resolve_bind_host() -> str:
    """
    The address the API should bind to.

    Loopback unless `BARTH_API_HOST` names something else *and*
    `BARTH_API_ALLOW_NON_LOOPBACK` is explicitly set. A non-loopback request
    without that opt-in is refused rather than silently downgraded, so a
    misconfiguration is loud instead of surprising.
    """
    requested = os.getenv(BIND_HOST_ENV, DEFAULT_BIND_HOST).strip() or DEFAULT_BIND_HOST
    if is_loopback_host(requested):
        return requested
    if not non_loopback_allowed():
        raise RuntimeError(
            f"{BIND_HOST_ENV}={requested!r} is not a loopback address. The "
            f"Bartholomew API has no authentication and is loopback-only by "
            f"default. To bind it anyway -- exposing personal memory to "
            f"anything that can reach the port -- set "
            f"{ALLOW_NON_LOOPBACK_ENV}=1 deliberately.",
        )
    print(_NON_LOOPBACK_WARNING % (requested, ALLOW_NON_LOOPBACK_ENV), file=sys.stderr)
    return requested


def _admission_exempt(path: str) -> bool:
    if path in _ADMISSION_EXEMPT_PATHS:
        return True
    if path.startswith(_ADMISSION_EXEMPT_PREFIXES):
        return True
    return path.endswith("/metrics")


@app.middleware("http")
async def admission_middleware(request: Request, call_next):
    """
    Single chokepoint for Phase B stage B7's external request admission --
    gates every real HTTP ingress point at once (re-verified against the
    current router registrations, not touching all ~35 individual route
    handlers) rather than requiring every current and future route to
    remember to opt in.

    Refuses (503) before the route handler ever runs if there is no
    _kernel yet, or the daemon isn't in DaemonLifecycleState.RUNNING
    (covers the STARTING window too, not just "_kernel is None" -- app.py's
    startup() assigns the _kernel global before awaiting start(), so a
    bare None-check alone would let a request through mid-startup) or
    admission is closed (stop() has begun). Admits exactly one token per
    request that passes, and releases it in `finally` -- guaranteed release
    even on an unhandled exception or a client disconnect -- so
    KernelDaemon.stop() can drain() to a confirmed-empty state rather than
    assuming in-flight work has finished.
    """
    # Network boundary first: refuse a non-loopback caller before any route,
    # exempt or not, sees the request. Deliberately ahead of the exemption
    # list -- /healthz and /ui are exempt from *kernel readiness*, not from
    # the question of who may reach this process at all.
    #
    # `request.client.host` is the peer address of the actual connection. Any
    # X-Forwarded-* header is ignored on purpose: it is attacker-controlled
    # and this boundary exists precisely because no trusted proxy is part of
    # the current architecture.
    if not non_loopback_allowed():
        if not _is_local_peer(request.client.host if request.client else None):
            return JSONResponse(
                status_code=403,
                content={
                    "detail": (
                        "This Bartholomew deployment is loopback-only and "
                        "does not answer non-local callers. Reaching it "
                        "remotely requires a deliberately exposed deployment, "
                        "which is authenticated and TLS-only. See "
                        "DECISIONS.md (deployment architecture, and the S8 "
                        "Alpha authentication entry)."
                    ),
                },
            )

    # Transport check, before identity: on an exposed deployment every
    # request must have arrived over TLS.
    #
    # This exists because file-existence validation and an actual TLS socket
    # are different things. `serve()` configures TLS properly, but a process
    # started by hand -- `uvicorn app:app --host 0.0.0.0` -- never calls it,
    # and would happily carry session cookies in clear text. Checking the
    # scheme of the request that actually arrived closes that gap however the
    # process was launched, and fails closed rather than warning.
    #
    # `scope["scheme"]` is set by the ASGI server from its own listener, not
    # from any client-supplied header, so it cannot be spoofed by a caller.
    # X-Forwarded-Proto is deliberately NOT consulted: no trusted proxy is
    # part of this architecture, and honouring it would let any client assert
    # that its plaintext request was really TLS.
    if non_loopback_allowed() and request.url.scheme not in ("https", "wss"):
        return JSONResponse(
            status_code=403,
            content={
                "detail": (
                    "This deployment is exposed beyond loopback and accepts "
                    "TLS only. The request arrived over plaintext, so it was "
                    "refused before authentication. Launch through "
                    "`bartholomew-api` / app.serve(), which configures TLS on "
                    "the socket."
                ),
            },
        )
    # Authentication and authorisation (S8), in the one chokepoint rather
    # than a second one -- deliberately *after* the network boundary above
    # (may this peer reach the process at all) and *before* the admission
    # and readiness checks below (is the kernel ready to take work).
    #
    # It is also before the admission exemption list, which exempts paths
    # from *kernel readiness*, not from the question of who is asking. The
    # genuinely unauthenticated paths are named in route_policy.PUBLIC_PATHS
    # and are a deliberately shorter list.
    #
    # Nothing here decides whether Bartholomew may act. That remains
    # Governance's answer, below the route handler, unchanged.
    try:
        principal = authenticate_and_authorize(request)
    except (
        AuthenticationError,
        AuthorizationError,
        AuthUnavailableError,
        UnclassifiedRouteError,
    ) as exc:
        return platform_error_response(exc)
    request.state.principal = principal

    if _admission_exempt(request.url.path):
        return await call_next(request)

    from bartholomew.kernel.daemon import DaemonLifecycleState

    if _kernel is None or _kernel.lifecycle_state is not DaemonLifecycleState.RUNNING:
        return JSONResponse(
            status_code=503,
            content={"detail": "Kernel not available (starting, stopping, or not initialized)"},
        )

    token = _kernel.admission.try_admit()
    if token is None:
        return JSONResponse(status_code=503, content={"detail": "Kernel is shutting down"})
    try:
        return await call_next(request)
    finally:
        _kernel.admission.release(token)


# CORS (safe default; UI likely same origin but this helps with previews)
# Allow override via ALLOWED_ORIGINS env var (comma-separated)
default_origins = [
    "http://localhost",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://127.0.0.1",
]
env_origins = os.getenv("ALLOWED_ORIGINS")
allow_origins = [o.strip() for o in env_origins.split(",")] if env_origins else default_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

orch = Orchestrator()

# Kernel daemon globals
_kernel = None
_kernel_task = None


@app.on_event("startup")
async def startup():
    global _kernel, _kernel_task

    # Fail closed on an unsafe exposure posture before anything is served.
    # Raising here stops the process; the alternative -- discovering it on
    # the first request -- is too late, because by then it is listening.
    assert_exposure_is_safe()
    from bartholomew.platform.authority import install_platform_halt_hook
    from bartholomew.platform.store import init_platform_schema

    init_platform_schema()
    # Compose the Platform/Admin tier into Governance before the kernel
    # starts, so autonomous work, skill execution and governed state
    # mutations are all subject to both tiers from the first tick rather
    # than from the first HTTP request.
    install_platform_halt_hook()
    print(f"[platform] exposure: {describe_exposure()}", file=sys.stderr)

    # Initialize state for liveness + metrics
    app.state.start_monotonic = _time.monotonic()
    app.state.last_tick_iso = (
        dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )
    app.state.drives = ["self_check", "curiosity_probe", "reflection_micro"]
    app.state.current_drive = "self_check"

    # Register Prometheus collectors to local registry
    try:
        ProcessCollector(registry=REGISTRY)
        PlatformCollector(registry=REGISTRY)
    except Exception:
        pass

    # Inbound capture stays fail-closed unless the authenticated control
    # plane installs a principal resolver. The one exception is the
    # double-gated test resolver, which exists so the end-to-end HTTP path is
    # provable against a real server process and which announces itself
    # everywhere it applies -- see inbound_auth's module docstring.
    from . import inbound_auth

    inbound_auth.maybe_install_test_resolver_from_env()

    # Import here to avoid circular imports
    from bartholomew.kernel.daemon import KernelDaemon

    # Start kernel in-process. Resolved fresh here (not the DB_PATH constant
    # imported above) -- see db.resolve_db_path()'s docstring: this is the
    # call site that was silently sharing one physical SQLite file across
    # unrelated test files before this fix (found investigating a CI flake
    # on PR #38).
    _kernel = KernelDaemon(
        cfg_path="config/kernel.yaml",
        db_path=resolve_db_path(),
        persona_path="config/persona.yaml",
        policy_path="config/policy.yaml",
        drives_path="config/drives.yaml",
        identity_path="Identity.yaml",
    )
    await _kernel.start()

    # Rebuild the chat orchestrator with the Identity the daemon just loaded,
    # so /api/chat reaches a real model instead of the stub.
    #
    # The module-level `orch = Orchestrator()` above is constructed at import
    # time, before any Identity exists, which is why the live chat path has
    # always run on the stub backend: ModelRouter with identity_config=None
    # builds no LLM adapter, and its default backend stays "stub". That was
    # the whole of the "Mock response for prompt: ..." behaviour -- the
    # runtime contract, governance, memory capture and recall around it were
    # already real.
    #
    # Rebuilt here rather than at import because the Identity is loaded by
    # KernelDaemon and this is the first point it exists. Best-effort by
    # design: if it fails, the stub orchestrator built at import stays in
    # place and chat keeps working exactly as it did before, rather than
    # taking down startup.
    #
    # `model_identity_config`, not `identity_config`: the Identity goes to the
    # model router alone. Passing it to the whole Orchestrator would also
    # build a ContextBuilder/MemoryManager -- the superseded conversational
    # memory path that runtime_contract replaced, and a hard OS-keystore
    # dependency on startup. See Orchestrator.__init__'s docstring.
    global orch
    if getattr(_kernel, "identity", None) is not None:
        try:
            orch = Orchestrator(model_identity_config=_kernel.identity)
        except Exception as e:
            print(f"[api] Real model path unavailable, staying on stub: {e}")

    # Unattended-run evidence (Session A). Inert unless
    # BARTH_UNATTENDED_RUN_ID is set, so a normal deployment gains no writer
    # and no table. It observes: the runtime_id it records is the kernel's
    # own (bartholomew.runtime.evidence's module docstring says why a second
    # one would be wrong), and nothing here decides anything about lifecycle
    # or health.
    from bartholomew.runtime import evidence as _evidence

    _evidence.record_process_start(
        resolve_db_path(),
        runtime_id=getattr(_kernel, "runtime_id", None),
    )

    # Keep kernel running
    async def keep_alive():
        while True:
            await asyncio.sleep(3600)

    _kernel_task = asyncio.create_task(keep_alive())


@app.on_event("shutdown")
async def shutdown():
    if _kernel:
        await _kernel.stop()

    # Recorded after the kernel has actually stopped, never before: the whole
    # value of this row is that it distinguishes a process that completed its
    # shutdown from one that did not, and writing it on the way in would make
    # every ending look clean. A process killed before reaching here leaves
    # its incarnation open, and the next start closes it as `lost`.
    from bartholomew.runtime import evidence as _evidence
    from bartholomew.runtime import supervision as _supervision

    _fatal = _supervision.get_recorder().failure
    _evidence.record_process_stop(
        resolve_db_path(),
        end_kind=_evidence.END_CLEAN if _fatal is None else _evidence.END_FAILED,
        detail=(
            None
            if _fatal is None
            else f"{_fatal.component} failed ({_fatal.reason}) at {_fatal.at}"
        ),
    )

    # Remove the Platform/Admin halt hook this app installed at startup.
    #
    # The registration is process-global module state, so leaving it behind
    # would outlive the app instance that installed it -- harmless in a real
    # deployment (one app, one process, then exit), but in a test session it
    # would keep answering for every later Governance check against whatever
    # control-plane path happened to be configured at the time. Installed at
    # startup, removed at shutdown, symmetrically.
    from bartholomew.orchestrator.safety.governance_store import (
        register_additional_engaged_check,
        register_additional_halt_check,
    )

    register_additional_halt_check(None)
    register_additional_engaged_check(None)


# --- Kernel-facing helpers ---
def set_last_tick(ts: dt.datetime | None = None, drive: str | None = None) -> None:
    """
    Call from the kernel loop after each tick.
    Optionally pass the active `drive` to increment the labeled counter.
    """
    if ts is None:
        ts = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    app.state.last_tick_iso = ts.isoformat().replace("+00:00", "Z")

    if drive:
        app.state.current_drive = drive
        # Update the drives snapshot while preserving insertion order (unique, stable)
        try:
            lst = list(getattr(app.state, "drives", []))
            if drive not in lst:
                lst.append(drive)
            app.state.drives = lst
        except Exception:
            # Never let metrics/liveness helpers crash the app
            pass

    # Bump labeled counters
    try:
        label = getattr(app.state, "current_drive", None) or "unknown"
        KERNEL_TICKS_TOTAL.labels(label).inc()
        BARTHOLOMEW_TICKS_TOTAL.labels(label).inc()
    except Exception:
        pass


def set_drives(drives: list[str]) -> None:
    """Call when the active drive set changes."""
    app.state.drives = list(drives)
    if drives:
        app.state.current_drive = drives[0]


@app.post("/kernel/command/{cmd}")
async def kernel_command(cmd: str):
    """Execute a kernel command (e.g., reflection_run_daily, reflection_run_weekly)"""
    if _kernel is None:
        raise HTTPException(503, "Kernel not initialized")
    await _kernel.handle_command(cmd)
    return {"ok": True}


def _parse_reply(raw: str):
    """
    Split ResponseFormatter's `[tone: ...] [emotion: ...]` prefix off the
    reply body.

    Only those two known tags are removed. This previously stripped *every*
    bracketed span in the response (`re.sub(r"\\[[^\\]]+\\]\\s*", "", raw)`),
    which was harmless while the backend was a stub that never emitted
    brackets, but silently deletes legitimate content from a real model --
    array indexing and slices in code, markdown link text, citation markers,
    `[INFO]`-style lines. The tags are a prefix written by
    ResponseFormatter._format_tags(), so anchoring the strip to the start of
    the string keeps the body byte-exact.
    """
    tone = None
    emotion = None
    if not isinstance(raw, str):
        return ("", None, None)
    m_tone = re.search(r"\[tone:\s*([^\]]+)\]", raw, re.I)
    m_em = re.search(r"\[emotion:\s*([^\]]+)\]", raw, re.I)
    if m_tone:
        tone = m_tone.group(1).strip()
    if m_em:
        emotion = m_em.group(1).strip()
    # Strip only leading tone/emotion tags, in any order, and only at the
    # front -- never bracketed text inside the reply body.
    reply = re.sub(
        r"^(?:\s*\[(?:tone|emotion):\s*[^\]]*\]\s*)+",
        "",
        raw,
        flags=re.I,
    ).strip()
    return (reply, tone, emotion)


@app.get("/healthz", tags=["health"])
def healthz():
    """Minimal liveness endpoint for load balancers and monitoring."""
    return {"status": "ok", "version": app.version}


# Model-reachability probe cache: (monotonic_deadline, reachable).
#
# The probe is a real call to the local provider, so it is neither free nor
# instant, and /api/health is polled by the UI. A few seconds of staleness is
# the right trade: long enough that polling costs nothing, short enough that
# "I just started Ollama" shows up while the tester is still looking.
_MODEL_PROBE_TTL_SECONDS = 10.0
_MODEL_PROBE_TIMEOUT_SECONDS = 2.0
_model_probe_cache: tuple[float, bool | None] = (0.0, None)


async def _probe_model_reachable(router) -> bool | None:
    """Whether the selected local model can actually be reached right now.

    Returns None when reachability is unknown (no adapter, probe timed out,
    or the backend isn't one this can probe) -- deliberately tri-state, so
    "we could not tell" is never reported as "it works".
    """
    global _model_probe_cache

    deadline, cached = _model_probe_cache
    now = _time.monotonic()
    if now < deadline:
        return cached

    adapter = getattr(router, "llm_adapter", None)
    if adapter is None:
        return None

    backend = router.config.get("default_backend")
    if backend not in ("local", "ollama"):
        return None

    model = router.config["backends"].get(backend, {}).get("model")
    if not model:
        return None

    def _check() -> bool:
        return adapter._model_exists(adapter._map_model_name(model))

    try:
        # Off the event loop (it does blocking IO) and bounded, so an
        # unresponsive provider degrades the health *answer* rather than the
        # health *endpoint*.
        reachable: bool | None = await asyncio.wait_for(
            asyncio.to_thread(_check),
            timeout=_MODEL_PROBE_TIMEOUT_SECONDS,
        )
    except Exception:
        reachable = None

    _model_probe_cache = (_time.monotonic() + _MODEL_PROBE_TTL_SECONDS, reachable)
    return reachable


async def _model_health() -> dict[str, Any]:
    """What chat will actually do if a message arrives right now.

    Two independent questions, previously answered as one. `model_real` only
    ever meant "a real backend is *selected*" -- it reported True while
    Ollama had no model pulled and every chat request was returning 503, so
    the one field the real-world test relies on to answer "is this a genuine
    reply?" implied a readiness it had never checked. Selection and
    reachability are now separate fields, and `model_status` combines them
    into the single answer a tester actually wants.
    """
    info: dict[str, Any] = {
        "model_backend": "unknown",
        "model_real": False,
        "model_reachable": None,
        "model_status": "unknown",
    }
    try:
        router = orch.router
        backend = router.config.get("default_backend")
        real = backend != "stub"
        reachable = await _probe_model_reachable(router) if real else None

        if not real:
            status = "stub"
        elif reachable is True:
            status = "ready"
        elif reachable is False:
            status = "selected_but_unreachable"
        else:
            status = "selected_reachability_unknown"

        info = {
            "model_backend": backend,
            "model_name": router.config["backends"].get(backend, {}).get("model"),
            # A real backend is selected. NOT a promise that it will answer.
            "model_real": real,
            # Tri-state: True/False/None (unknown).
            "model_reachable": reachable,
            "model_status": status,
        }
    except Exception:
        pass

    # Cloud is off unless deliberately enabled, and "enabled" is not the same
    # as "usable" -- an API key without the optional SDK is configured but
    # unservable. Reported so that state is visible rather than surfacing
    # only as a failed request.
    try:
        from identity_interpreter.adapters.cloud_llm import readiness, unreadiness_reason

        info["cloud_status"] = readiness()
        info["cloud_unavailable_reason"] = unreadiness_reason()
    except Exception:
        pass

    return info


def _component_health() -> dict[str, Any]:
    """Whether each always-on component is actually alive (Session D).

    Four components an operator needs distinguished, because "the process is
    up" answers none of them:

    * **service**  -- this HTTP process. If you are reading this, it is up.
    * **runtime**  -- the kernel daemon, and which lifecycle state it is in.
    * **scheduler**-- the autonomy loop, from its own heartbeat. A loop that
      died, or that stopped beating, reports failed rather than silently
      leaving the service looking healthy.
    * **inbound**  -- whether the capture door is open, and on what. Fail-closed
      (no resolver) is a normal, reportable state, not a fault; a test-only
      resolver is flagged loudly so a running service can never be admitting
      events on test credentials unnoticed.

    Returns the components plus a private `_overall` key: "ok" only when
    nothing is failed, "degraded" otherwise. Never raises -- a health endpoint
    that 500s tells an operator nothing.
    """
    from bartholomew.runtime.health import ComponentHealth

    components: dict[str, Any] = {"service": {"status": "ok"}}

    if _kernel is None:
        components["runtime"] = {"status": "failed", "state": "not_initialized"}
        components["scheduler"] = {"status": "unknown", "state": "unknown"}
    else:
        state = getattr(_kernel.lifecycle_state, "value", str(_kernel.lifecycle_state))
        running = state == "running"
        components["runtime"] = ComponentHealth(
            "runtime",
            ok=running,
            detail={"state": state},
        ).as_dict()

        heartbeat = getattr(_kernel, "scheduler_heartbeat", None)
        if heartbeat is None:
            components["scheduler"] = {"status": "unknown", "state": "unknown"}
        else:
            snapshot = heartbeat.snapshot()
            components["scheduler"] = ComponentHealth(
                "scheduler",
                ok=snapshot["healthy"],
                detail=snapshot,
            ).as_dict()

    try:
        from . import inbound_auth

        open_for_capture = inbound_auth.get_resolver() is not None
        components["inbound"] = {
            # An intentionally closed door is working correctly, so this is
            # "ok" either way -- what matters is that it says which it is.
            "status": "ok",
            "open": open_for_capture,
            "test_resolver_active": inbound_auth.resolver_is_test_only(),
            "detail": (
                "Inbound capture is closed: no principal resolver installed."
                if not open_for_capture
                else "Inbound capture is open."
            ),
        }
    except Exception:
        components["inbound"] = {"status": "unknown"}

    failed = any(isinstance(v, dict) and v.get("status") == "failed" for v in components.values())
    components["_overall"] = "degraded" if failed else "ok"
    return components


def _retrieval_health() -> dict[str, Any]:
    """What retrieval will actually do if a query arrives right now.

    The same shape of answer as `_model_health()` above, for the same reason:
    what was *configured* and what is *running* are different questions, and
    reporting only the first is how OP-W003 happened -- Real-World Test #1 ran
    its retrieval on a deterministic fallback embedder, and nothing said so.

    `retrieval_semantic` is the field that answers it directly: False means
    matching is lexical only, whatever `retrieval_mode_configured` says.
    Degrading the health *answer* rather than the health *endpoint*: any
    failure here reports unknown instead of raising.
    """
    try:
        from bartholomew.kernel.retrieval import describe_retrieval

        described = describe_retrieval()
        embedding = described["embedding"]
        return {
            "retrieval_mode_configured": described["mode_configured"],
            "retrieval_mode_effective": described["mode_effective"],
            "retrieval_semantic": described["semantic"],
            "retrieval_degraded": described["degraded"],
            "retrieval_degraded_reason": described["reason"],
            "embedding_mode": embedding["mode"],
            "embedding_model": embedding["model"],
            "embedding_provider": embedding["provider"],
        }
    except Exception as e:
        # Unknown, never assumed-good.
        return {
            "retrieval_mode_configured": "unknown",
            "retrieval_mode_effective": "unknown",
            "retrieval_semantic": None,
            "retrieval_degraded": None,
            "retrieval_degraded_reason": f"Retrieval state could not be determined: {e}",
            "embedding_mode": "unknown",
        }


@app.get("/api/health")
async def health():
    kernel_info = {}
    if _kernel:
        kernel_info = {
            "kernel_online": True,
            "last_kernel_beat": _kernel.state.now.isoformat() if _kernel.state.now else None,
            "db_path": _kernel.mem.db_path,
        }
        # Get pending nudges count
        try:
            pending = await _kernel.mem.list_pending_nudges(limit=1000)
            kernel_info["nudges_pending_count"] = len(pending)
        except Exception:
            kernel_info["nudges_pending_count"] = 0

        # Get last daily reflection
        try:
            last_daily = await _kernel.mem.latest_reflection("daily_journal")
            if last_daily:
                kernel_info["last_daily_reflection"] = last_daily["ts"]
        except Exception:
            pass
    else:
        kernel_info = {"kernel_online": False}

    model_info = await _model_health()
    components = _component_health()
    retrieval_info = _retrieval_health()

    return {
        # Not hardcoded any more. An always-on service whose scheduler has
        # died must not answer "ok" -- that is the failure this endpoint
        # exists to make visible (Session D).
        "status": components["_overall"],
        "tz": str(TZ),
        "time": datetime.now(TZ).isoformat(),
        "orchestrator": getattr(orch, "__class__", type("x", (object,), {})).__name__,
        "version": app.version,
        "components": {k: v for k, v in components.items() if not k.startswith("_")},
        **model_info,
        # Retrieval's own truthful answer (Session C): what retrieval will
        # actually do, not what it was configured to do. Reported alongside
        # component liveness rather than folded into it -- "is the scheduler
        # alive" and "is matching actually semantic" are different questions,
        # and neither should be able to mask the other.
        **retrieval_info,
        **kernel_info,
    }


@app.post("/api/chat", response_model=ChatOut)
async def chat(body: ChatIn):
    # MASTER_PLAN.md "P2.5 -- Runtime Convergence" item 11.4: route chat
    # through the Runtime Contract seam (bartholomew.kernel.runtime_contract)
    # so it observably updates Working Memory, the same Experience Kernel
    # every other kernel-driven surface shares -- previously chat and the
    # Experience Kernel were fully disconnected. Falls back to the prior,
    # unwrapped orch.handle_input() call when the kernel isn't available
    # (e.g. during startup/shutdown windows), preserving existing behavior
    # exactly in that case.
    if _kernel is not None:
        from bartholomew.kernel.runtime_contract import run_chat_through_runtime_contract

        async def _respond(prompt: str) -> str:
            # skip_governance_check=True: run_chat_through_runtime_contract
            # (below) already ran its own Stage 4 Governance gate against
            # the shared instance before ever calling this -- Phase B
            # stage B4 removed handle_input()'s own redundant blocking
            # Parking Brake read on this path (previously a second,
            # synchronous SQLite read on every single chat message).
            return orch.handle_input(prompt, skip_governance_check=True)

        try:
            result = await run_chat_through_runtime_contract(_kernel, body.message, _respond)
        except ModelBackendError as e:
            # The model backend could not generate. Report that truthfully
            # rather than letting a fabricated reply reach the user -- see
            # ModelRouter.route()'s docstring. 503, because this is a
            # temporarily-unavailable dependency (Ollama down, model not
            # pulled), not a malformed request.
            raise HTTPException(
                503,
                f"Model backend unavailable ({e.backend}/{e.model}): {e.reason}. {e}",
            ) from e
        if not result.governance_allowed:
            raise HTTPException(503, result.governance_reason or "Blocked by governance")
        raw = result.response
        # WP-A2b: carry a lost chat-provenance record (see RuntimeContract
        # Result.provenance_degraded) out to the response. Only the governed
        # kernel path can degrade -- the no-kernel fallback below records no
        # Reflection at all and predates this contract.
        provenance_degraded = result.provenance_degraded
        provenance_error = result.provenance_error
    else:
        provenance_degraded = False
        provenance_error = None
        # No _kernel (startup/shutdown window): no shared instance exists to
        # gate through, so handle_input()'s own check is the sole gate here
        # (skip_governance_check defaults to False). Its synchronous
        # Governance read would otherwise block the event loop directly in
        # this async route -- run the whole call off it (Phase B stage B4;
        # see docs/B4_GOVERNANCE_RUNTIME_INTEGRATION.md).
        try:
            raw = await asyncio.to_thread(orch.handle_input, body.message)
        except ModelBackendError as e:
            raise HTTPException(
                503,
                f"Model backend unavailable ({e.backend}/{e.model}): {e.reason}. {e}",
            ) from e

    reply, tone, emotion = _parse_reply(raw)
    if not reply:
        reply = str(raw)
    return ChatOut(
        reply=reply,
        tone=tone,
        emotion=emotion,
        audit_degraded=True if provenance_degraded else None,
        audit_error=provenance_error,
    )


@app.get("/api/conversation/recent", response_model=ConversationList)
def conversation_recent(limit: int = 10):
    # Try to read from orchestrator/memory if available; otherwise return stub
    items = []
    try:
        if hasattr(orch, "memory") and hasattr(orch.memory, "recent"):
            for i, ev in enumerate(orch.memory.recent(limit=limit)):
                items.append(
                    {
                        "id": str(i),
                        "timestamp": getattr(ev, "timestamp", datetime.now(TZ).isoformat()),
                        "role": getattr(ev, "role", "unknown"),
                        "content": getattr(ev, "content", ""),
                    },
                )
    except Exception:
        pass
    if not items:
        now = datetime.now(TZ).isoformat()
        items = [
            {
                "id": "0",
                "timestamp": now,
                "role": "system",
                "content": "stub: conversation history not yet wired",
            },
        ]
    return ConversationList(items=items)


@app.get("/api/nudges/pending")
async def get_pending_nudges(limit: int = 50):
    """Get pending nudges from kernel memory."""
    if not _kernel:
        raise HTTPException(503, "Kernel not initialized")

    nudges = await _kernel.mem.list_pending_nudges(limit=limit)
    return {"nudges": nudges}


@app.post("/api/nudges/{nudge_id}/ack")
async def ack_nudge(nudge_id: int):
    """Acknowledge a nudge."""
    if not _kernel:
        raise HTTPException(503, "Kernel not initialized")

    from datetime import timezone

    acted_ts = datetime.now(timezone.utc).isoformat()
    await _kernel.mem.set_nudge_status(nudge_id, "acked", acted_ts)
    return {"ok": True, "nudge_id": nudge_id, "status": "acked"}


@app.post("/api/nudges/{nudge_id}/dismiss")
async def dismiss_nudge(nudge_id: int):
    """Dismiss a nudge."""
    if not _kernel:
        raise HTTPException(503, "Kernel not initialized")

    from datetime import timezone

    acted_ts = datetime.now(timezone.utc).isoformat()
    await _kernel.mem.set_nudge_status(nudge_id, "dismissed", acted_ts)
    return {"ok": True, "nudge_id": nudge_id, "status": "dismissed"}


@app.get("/api/reflection/daily/latest")
async def get_latest_daily_reflection():
    """Get the most recent daily reflection."""
    if not _kernel:
        raise HTTPException(503, "Kernel not initialized")

    reflection = await _kernel.mem.latest_reflection("daily_journal")
    if not reflection:
        raise HTTPException(404, "No daily reflection found")

    return {"reflection": reflection}


@app.get("/api/reflection/weekly/latest")
async def get_latest_weekly_reflection():
    """Get the most recent weekly reflection."""
    if not _kernel:
        raise HTTPException(503, "Kernel not initialized")

    reflection = await _kernel.mem.latest_reflection("weekly_alignment_audit")
    if not reflection:
        raise HTTPException(404, "No weekly reflection found")

    return {"reflection": reflection}


@app.post("/api/reflection/run")
async def trigger_reflection(kind: str = "daily"):
    """Manually trigger a reflection run (for testing)."""
    if not _kernel:
        raise HTTPException(503, "Kernel not initialized")

    if kind == "daily":
        await _kernel.handle_command("reflection_run_daily")
    elif kind == "weekly":
        await _kernel.handle_command("reflection_run_weekly")
    else:
        raise HTTPException(400, f"Unknown reflection kind: {kind}")

    return {"ok": True, "kind": kind, "triggered": True}
