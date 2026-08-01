import asyncio
import atexit
import datetime as dt
import os
import re
import time as _time
from datetime import datetime

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

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

from . import db_ctx
from .db import DB_PATH
from .models import ChatIn, ChatOut, ConversationList
from .routes import governance, liveness, metrics, self_state
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
app.include_router(liveness.router)
app.include_router(self_state.router)
app.include_router(governance.router)

# Metrics: mount under /internal in production mode (METRICS_INTERNAL_ONLY=1)
# to restrict access; default (dev/test) leaves it at /metrics (unauthenticated)
metrics_internal_only = is_truthy(os.getenv("METRICS_INTERNAL_ONLY"))
app.include_router(metrics.router, prefix="/internal" if metrics_internal_only else "")

# Register atexit handler for WAL cleanup on shutdown
atexit.register(lambda: db_ctx.wal_checkpoint_truncate(DB_PATH))

# Paths exempt from external request admission (Phase B stage B7; see
# docs/B7_EXTERNAL_REQUEST_ADMISSION.md) -- health/liveness/metrics/docs
# endpoints must keep answering during startup/shutdown windows (the
# k8s-liveness-probe convention this repository's own liveness routes are
# written for), and don't touch _kernel's governed state at all. Everything
# else that isn't one of these is real external ingress into governed
# daemon state, and is gated below.
_ADMISSION_EXEMPT_PATHS = frozenset(
    {"/healthz", "/api/health", "/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"},
)
_ADMISSION_EXEMPT_PREFIXES = ("/api/liveness",)


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

    # Import here to avoid circular imports
    from bartholomew.kernel.daemon import KernelDaemon

    # Start kernel in-process
    _kernel = KernelDaemon(
        cfg_path="config/kernel.yaml",
        db_path=DB_PATH,
        persona_path="config/persona.yaml",
        policy_path="config/policy.yaml",
        drives_path="config/drives.yaml",
        identity_path="Identity.yaml",
    )
    await _kernel.start()

    # Keep kernel running
    async def keep_alive():
        while True:
            await asyncio.sleep(3600)

    _kernel_task = asyncio.create_task(keep_alive())


@app.on_event("shutdown")
async def shutdown():
    if _kernel:
        await _kernel.stop()


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
    reply = re.sub(r"\[[^\]]+\]\s*", "", raw).strip()
    return (reply, tone, emotion)


@app.get("/healthz", tags=["health"])
def healthz():
    """Minimal liveness endpoint for load balancers and monitoring."""
    return {"status": "ok", "version": app.version}


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

    return {
        "status": "ok",
        "tz": str(TZ),
        "time": datetime.now(TZ).isoformat(),
        "orchestrator": getattr(orch, "__class__", type("x", (object,), {})).__name__,
        "version": app.version,
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

        result = await run_chat_through_runtime_contract(_kernel, body.message, _respond)
        if not result.governance_allowed:
            raise HTTPException(503, result.governance_reason or "Blocked by governance")
        raw = result.response
    else:
        # No _kernel (startup/shutdown window): no shared instance exists to
        # gate through, so handle_input()'s own check is the sole gate here
        # (skip_governance_check defaults to False). Its synchronous
        # Governance read would otherwise block the event loop directly in
        # this async route -- run the whole call off it (Phase B stage B4;
        # see docs/B4_GOVERNANCE_RUNTIME_INTEGRATION.md).
        raw = await asyncio.to_thread(orch.handle_input, body.message)

    reply, tone, emotion = _parse_reply(raw)
    if not reply:
        reply = str(raw)
    return ChatOut(reply=reply, tone=tone, emotion=emotion)


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
