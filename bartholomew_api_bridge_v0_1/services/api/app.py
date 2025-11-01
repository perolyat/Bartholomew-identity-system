
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, date, time
import atexit
import asyncio
import re, os
from typing import Optional
import time as _time
import datetime as dt
import yaml

# Load timezone from kernel config (single source of truth)
with open("config/kernel.yaml", "r", encoding="utf-8") as f:
    _kernel_cfg = yaml.safe_load(f)
    _tz_name = _kernel_cfg["timezone"]

# tz support (prefer zoneinfo, fallback to dateutil.tz)
try:
    from zoneinfo import ZoneInfo  # py>=3.9
    TZ = ZoneInfo(_tz_name)
except Exception:
    from dateutil import tz
    TZ = tz.gettz(_tz_name)

from .models import ChatIn, ChatOut, ConversationItem, ConversationList
from .db import DB_PATH
from . import db_ctx
from .routes import liveness, metrics
from .routes.metrics import KERNEL_TICKS_TOTAL, BARTHOLOMEW_TICKS_TOTAL
from prometheus_client import ProcessCollector, PlatformCollector


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
except Exception as e:
    # Soft fallback stub so the API doesn't crash during wiring
    class _StubOrchestrator:
        def handle_input(self, msg: str) -> str:
            return f"[tone: warm] [emotion: helpful] (stub) You said: {msg}"
    Orchestrator = _StubOrchestrator

app = FastAPI(title="Bartholomew API v0.1", version="0.1.0")

# Include routers
app.include_router(liveness.router)

# Metrics: mount under /internal in production mode (METRICS_INTERNAL_ONLY=1)
# to restrict access; default (dev/test) leaves it at /metrics (unauthenticated)
metrics_internal_only = is_truthy(os.getenv("METRICS_INTERNAL_ONLY"))
app.include_router(
    metrics.router,
    prefix="/internal" if metrics_internal_only else ""
)

# Register atexit handler for WAL cleanup on shutdown
atexit.register(lambda: db_ctx.wal_checkpoint_truncate(DB_PATH))

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
    app.state.last_tick_iso = dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    app.state.drives = ["self_check", "curiosity_probe", "reflection_micro"]
    app.state.current_drive = "self_check"
    
    # Register Prometheus collectors
    try:
        ProcessCollector()
        PlatformCollector()
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
    )
    await _kernel.start()
    
    # Keep kernel running
    async def keep_alive():
        while True:
            await asyncio.sleep(3600)
    
    _kernel_task = asyncio.create_task(keep_alive())


@app.on_event("shutdown")
async def shutdown():
    global _kernel
    if _kernel:
        await _kernel.stop()


# --- Kernel-facing helpers ---
def set_last_tick(ts: dt.datetime | None = None, drive: str | None = None) -> None:
    """
    Call from the kernel loop after each tick.
    Optionally pass the active `drive` to increment the labeled counter.
    """
    if ts is None:
        ts = dt.datetime.utcnow().replace(microsecond=0)
    app.state.last_tick_iso = ts.isoformat() + "Z"

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
    m_em   = re.search(r"\[emotion:\s*([^\]]+)\]", raw, re.I)
    if m_tone: tone = m_tone.group(1).strip()
    if m_em: emotion = m_em.group(1).strip()
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
        "orchestrator": getattr(orch, "__class__", type("x",(object,),{})).__name__,
        "version": app.version,
        **kernel_info,
    }

@app.post("/api/chat", response_model=ChatOut)
def chat(body: ChatIn):
    raw = orch.handle_input(body.message)
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
                items.append({
                    "id": str(i),
                    "timestamp": getattr(ev, "timestamp", datetime.now(TZ).isoformat()),
                    "role": getattr(ev, "role", "unknown"),
                    "content": getattr(ev, "content", ""),
                })
    except Exception:
        pass
    if not items:
        now = datetime.now(TZ).isoformat()
        items = [
            {"id":"0","timestamp":now,"role":"system","content":"stub: conversation history not yet wired"},
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
