"""The visible multimodal status and stop surface (Package C, contract §7).

Two things only: *see* what is happening, and *stop* it. Contract §7 gate 18
requires active capture and output state to be visible through an API/UI
surface, and the visible-state requirement includes "how to stop it
immediately" -- so a stop control belongs here.

**There is deliberately no start endpoint.** A session is started through
`bartholomew.multimodal.runtime.start_session()`, which requires an
authenticated human principal, a resolved device and an explicit consent
decision. Exposing a start over this API bridge -- which, as every other route
here records, has no authentication today -- would put capture initiation
behind an unauthenticated HTTP call. That is exactly the shape contract §7
forbids ("no model response can start capture directly", "no autonomous
capture initiation"), so the start path is not reachable from HTTP at all.
Stopping needs no such protection: the worst an unauthenticated stop can do is
end a session the user could have ended anyway, which fails safe.

Auth note: same as every other route in this API bridge -- no authentication
today; ROADMAP.md's Stage 1 section defers that to a separate future project.
The absence of a start endpoint above is precisely because of it.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from bartholomew.multimodal.status import status_snapshot
from bartholomew.multimodal.store import SessionStore

router = APIRouter(prefix="/api/multimodal", tags=["multimodal"])

#: The process-wide session registry. A multimodal session is only real while
#: the process that owns the device is running (see `store.py`), so the
#: registry lives with the process rather than in a database -- and a restart
#: therefore cannot leave a session falsely reported as active.
_STORE = SessionStore()


def get_store() -> SessionStore:
    """The registry these routes read. Overridden in tests."""
    return _STORE


class StopRequest(BaseModel):
    reason: str | None = None


@router.get("/status")
def multimodal_status(tenant_id: str | None = None) -> dict:
    """Whether Bartholomew is listening, observing or speaking, and what stops it."""
    return status_snapshot(get_store(), tenant_id=tenant_id)


@router.get("/sessions")
def list_sessions(tenant_id: str | None = None) -> dict:
    """Every session this process knows about, including finished ones.

    Finished sessions are included deliberately: a person who saw "listening"
    a minute ago is entitled to see what became of it.
    """
    sessions = [
        s.snapshot() for s in get_store().all() if tenant_id is None or s.tenant_id == tenant_id
    ]
    return {"sessions": sessions, "count": len(sessions)}


@router.post("/sessions/{session_id}/stop")
def stop_session(session_id: str, request: StopRequest | None = None) -> dict:
    """Stop one session immediately. Idempotent.

    404 for a session this process has never heard of. A session that has
    already ended returns `stopped: false` with its current state rather than
    an error -- pressing stop twice is not a fault.
    """
    store = get_store()
    session = store.get(session_id)
    if session is None:
        raise HTTPException(404, f"No such multimodal session: {session_id}")
    reason = (request.reason if request else None) or "stopped by user"
    stopped = store.stop(session_id, reason)
    return {
        "stopped": stopped,
        "session": session.snapshot(),
        "detail": (
            "session stopped and cleaned up"
            if stopped
            else f"session was already {session.state.value}"
        ),
    }


@router.post("/sessions/stop-all")
def stop_all_sessions(request: StopRequest | None = None) -> dict:
    """The panic button: stop every live session at once."""
    store = get_store()
    reason = (request.reason if request else None) or "all sessions stopped by user"
    stopped = [s.session_id for s in store.live() if store.stop(s.session_id, reason)]
    return {"stopped": stopped, "count": len(stopped)}


@router.get("/diagnostics")
def multimodal_diagnostics() -> dict:
    """What this machine can and cannot do, and why not.

    Observes nothing: it asks the OS about device availability without opening
    a stream, reading the accessibility tree or taking an image.
    """
    from bartholomew.multimodal.diagnostics import diagnose

    return diagnose()
