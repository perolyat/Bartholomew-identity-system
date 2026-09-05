"""The visible multimodal status and stop surface (Package C, contract §7).

Two things only: *see* what is happening, and *stop* it. Contract §7 gate 18
requires active capture and output state to be visible through an API/UI
surface, and the visible-state requirement includes "how to stop it
immediately" -- so a stop control belongs here.

**The start endpoint is authenticated, and that is the only reason it
exists.** Package C shipped no start route because this API bridge had no
authentication, and putting capture initiation behind an unauthenticated HTTP
call is exactly the shape contract §7 forbids. That premise changed when
Session E's device credentials became reachable here: `POST /sessions` now
requires an enrolled device credential, resolves the tenant server-side, and
still runs every gate `start_session()` ran before.

What has *not* changed is who may decide to observe. The credential proves
**which machine** is calling and nothing else. The `principal_id` on the
resulting session is the human account the device's enrolment row names --
never `companion:`, which `SessionRequest` refuses to build at all -- and the
Runtime Contract's fourth gate still asks that person, interactively and
fail-closed, for every single start. A companion holding a valid credential
therefore cannot begin observing on its own: it can ask, and a person still
answers. Contract §7's "no autonomous capture initiation" is preserved by that
gate, not by the absence of a route.

Stopping needs no such protection and deliberately has none: the worst an
unauthenticated stop can do is end a session the user could have ended anyway,
which fails safe. Stop stays reachable under a Parking Brake and during the
admission window for the same reason.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from bartholomew.multimodal.status import status_snapshot
from bartholomew.multimodal.store import SessionStore

from ..db import resolve_db_path

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


class StartRequest(BaseModel):
    """One explicit ask to begin observing.

    No `tenant_id` and no `principal_id`: both are server-derived, from the
    authenticated device's enrolment row. A body that could name either would
    let a caller observe on somebody else's behalf, which is the one thing a
    capture-start surface must never allow.
    """

    modality: str
    #: Required for a screen session and refused for the others -- Package C's
    #: rule, enforced by `SessionRequest`, not restated here.
    scope: dict | None = None
    correlation_id: str | None = None
    max_duration_seconds: int | None = None
    #: A separate decision from "may observe the screen at all". Approving
    #: screen observation does not approve pixels.
    allow_screenshot_fallback: bool = False


@router.post("/sessions", status_code=201)
async def start_multimodal_session(request: Request) -> dict:
    """Begin one bounded observation session on the authenticated device.

    Every gate is somebody else's and is reached unchanged: Session E answers
    whether this device declares the capability, and
    `run_multimodal_session_through_runtime_contract` runs the capability
    check, the Parking Brake, the Identity policy decision and -- always,
    fail-closed -- the interactive consent ask. A refusal at any of them
    returns 403 with the outcome named, because a refused session is a normal,
    inspectable state rather than an error to swallow.
    """
    from bartholomew.multimodal.modality import CaptureScope, Modality, ScopeKind
    from bartholomew.multimodal.runtime import (
        AutonomousStartRefusedError,
        SessionRequest,
        start_session,
    )

    from ..companion_auth import require_companion

    payload = StartRequest(**(await request.json() if await request.body() else {}))
    companion = require_companion(request)

    try:
        modality = Modality(payload.modality)
    except ValueError:
        raise HTTPException(400, f"unknown modality: {payload.modality!r}") from None

    scope = None
    if payload.scope is not None:
        raw = dict(payload.scope)
        try:
            scope = CaptureScope(
                kind=ScopeKind(str(raw.pop("kind", ""))),
                display_id=raw.pop("display_id", None),
                window_id=raw.pop("window_id", None),
                window_title=raw.pop("window_title", None),
                rect=tuple(raw["rect"]) if raw.get("rect") is not None else None,
            )
        except (ValueError, TypeError, KeyError) as e:
            raise HTTPException(400, f"invalid capture scope: {e}") from None

    try:
        session_request = SessionRequest(
            tenant_id=companion.owner_user_id,
            # The human the device is enrolled to -- an account row, not the
            # machine. `SessionRequest` refuses a companion principal outright.
            principal_id=companion.owner_user_id,
            device_id=companion.device_id,
            modality=modality,
            correlation_id=payload.correlation_id or f"multimodal-{uuid.uuid4().hex[:16]}",
            scope=scope,
            max_duration_seconds=payload.max_duration_seconds,
            allow_screenshot_fallback=payload.allow_screenshot_fallback,
        )
    except AutonomousStartRefusedError as e:
        raise HTTPException(403, str(e)) from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None

    from ..app import _kernel

    result = await start_session(
        session_request,
        store=get_store(),
        db_path=resolve_db_path(),
        identity_context=getattr(_kernel, "identity_context", None),
    )

    body = result.as_dict()
    if not result.allowed:
        # 403 with the governance outcome named. The session object is still
        # returned: a refused start is part of what the status surface shows.
        return JSONResponse(status_code=403, content=body)
    return JSONResponse(status_code=201, content=body)


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
