"""Governed inbound capture — the controlled front door (Session D).

One endpoint, `POST /api/inbound/events`, plus a read-only inspection list.
It is a door, not an Executive: it authenticates the source, validates the
envelope's shape, and hands the event to the Runtime Contract seam, which
governs it and records it. Nothing here decides what an event means.

**What the status codes actually mean.** These are three different states and
this route never conflates them:

| Code | Meaning |
|------|---------|
| 202  | Authenticated, validated, and **durably persisted as captured**. NOT processed — nothing has interpreted, believed or acted on it. |
| 200  | A duplicate delivery of an event already captured; the existing row is reported and no second logical event exists. |
| 401  | Not verified. Nothing was read as authoritative and nothing was written. |
| 422  | The envelope is malformed. Nothing was written. |
| 503  | The Parking Brake is engaged, or the runtime/persistence is unavailable. Nothing was written; the sender should retry. |

There is no code here that means "processed". Acknowledgement never outruns
what actually happened.

**Domain blindness.** `event_type` is an opaque string. It is stored, echoed,
and never branched on. Grep this file for `email`, `calendar` or any provider
name and you will find none — that is a property to preserve, not an accident.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from bartholomew.orchestrator.safety.governance_store import ParkingBrakeEngagedError

from .. import inbound_auth
from ..db import resolve_db_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/inbound", tags=["inbound"])

#: Cap on a single event's payload, measured on the raw request body.
#:
#: A door that accepts unbounded third-party input is a door that can fill the
#: disk. Chosen to be comfortably larger than any structured event envelope
#: and far smaller than a file upload -- inbound capture is not a file
#: transfer surface.
MAX_BODY_BYTES = 64 * 1024


class InboundEventIn(BaseModel):
    """The canonical inbound envelope. Domain-blind by construction.

    `payload` is opaque: it is stored verbatim (as canonical JSON) and its
    contents are never inspected, validated against a provider schema, or
    used to choose a code path. Provider-specific adapters -- when any exist
    -- translate *into* this shape upstream of the ingress.
    """

    source_id: str = Field(..., min_length=1, max_length=128)
    event_id: str = Field(..., min_length=1, max_length=256)
    event_type: str = Field(..., min_length=1, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: str | None = Field(default=None, max_length=64)

    @field_validator("source_id", "event_id", "event_type")
    @classmethod
    def _no_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v


def _kernel_or_503():
    """The running kernel, or an honest refusal.

    The admission middleware already refuses non-exempt routes while the
    daemon is not RUNNING, so reaching here with no kernel means the runtime
    went away between admission and handling. Refuse rather than capture into
    a database whose owner is not alive.
    """
    from ..app import _kernel

    if _kernel is None:  # pragma: no cover - admission middleware covers this first
        raise HTTPException(503, "Runtime unavailable; nothing was captured.")
    return _kernel


def _require_principal(request: Request):
    """The verified principal, or refuse before anything is captured.

    Two independent questions are answered on this route, and both must pass:

    * **Whose Bartholomew is this?** -- the control plane's verified principal
      plus this process's runtime binding. Platform authority.
    * **Is this sender who it claims to be?** -- the source resolver, below.
      Provenance only.

    A verified source is emphatically not a substitute for the first: it says
    an event genuinely came from Acme, not that Acme may write into Taylor's
    runtime. Checked here as well as at the control plane's own chokepoint --
    not because that boundary is doubted, but because a capture path that
    silently depends on a middleware ordering it does not control is one
    refactor away from being unauthenticated, and this is the surface where
    that would be least visible.
    """
    if not inbound_auth.principal_required():
        # Authentication is not enforced: the single-user loopback development
        # deployment, where the platform itself returns no principal for any
        # request. Capture is still gated by the source resolver, which is
        # fail-closed by default.
        return None

    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise HTTPException(
            401,
            "Authentication is required to capture inbound events; "
            "nothing was captured.",
        )
    return principal


async def _verify_source(request: Request, body: bytes):
    """Resolve the verified source, or refuse with 401.

    Fails closed three ways: no resolver installed, a resolver that returns
    None, and a resolver that raises. Loopback is never consulted -- see
    `inbound_auth`'s module docstring.
    """
    resolver = inbound_auth.get_resolver()
    if resolver is None:
        raise HTTPException(
            401,
            "Inbound capture is closed: no principal resolver is installed. "
            "This endpoint does not accept unauthenticated events, including "
            "from localhost.",
        )
    try:
        source = await resolver.resolve(request, body)
    except Exception:
        # An errored verifier is an unverified caller.
        logger.exception("Inbound principal resolution failed; refusing the request")
        raise HTTPException(401, "Source verification failed; nothing was captured.") from None
    if source is None:
        raise HTTPException(401, "Source could not be verified; nothing was captured.")
    return source


@router.post("/events", status_code=202)
async def receive_event(request: Request) -> Any:
    """Receive one external event through the governed inbound seam.

    The body is read once, before validation, because source verification is
    entitled to see the exact bytes that were signed. Order is deliberate:
    **verify before parse**, so an unverified caller never reaches the
    validator, and neither reaches Governance or persistence.
    """
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(
            413,
            f"Inbound payload exceeds {MAX_BODY_BYTES} bytes; nothing was captured.",
        )

    # Identity before provenance: refuse an unauthenticated caller before a
    # resolver ever sees the body.
    _require_principal(request)
    source = await _verify_source(request, body)

    # Parse and validate only after verification.
    import json

    try:
        raw = json.loads(body or b"{}")
    except ValueError as e:
        raise HTTPException(422, f"Body is not valid JSON: {e}") from e
    if not isinstance(raw, dict):
        raise HTTPException(422, "Body must be a JSON object.")
    try:
        event = InboundEventIn.model_validate(raw)
    except Exception as e:
        raise HTTPException(422, f"Invalid inbound envelope: {e}") from e

    # `source_id` comes from the *verified* source, not from the body. A
    # caller-supplied identifier is not authentication and must not be able to
    # claim provenance for, or collide idempotency keys with, another source.
    if event.source_id != source.source_id:
        raise HTTPException(
            403,
            "source_id does not match the verified source; nothing was captured.",
        )

    kernel = _kernel_or_503()
    db_path = getattr(getattr(kernel, "mem", None), "db_path", None) or resolve_db_path()

    from bartholomew.kernel.inbound_store import InboundPersistenceError
    from bartholomew.kernel.runtime_contract import run_inbound_through_runtime_contract

    try:
        result = await run_inbound_through_runtime_contract(
            db_path=db_path,
            source_id=source.source_id,
            event_id=event.event_id,
            event_type=event.event_type,
            payload=event.payload,
            verified_by=source.verified_by,
            occurred_at=event.occurred_at,
            # From the platform's authority -- the verified principal and this
            # process's runtime binding -- never from the source. A resolver
            # that claims a runtime_id is ignored entirely; see
            # `inbound_auth.resolved_runtime_id`.
            runtime_id=inbound_auth.resolved_runtime_id(request),
            identity_context=getattr(kernel, "identity_context", None),
        )
    except ParkingBrakeEngagedError as e:
        # "Inspect, but do not mutate." Nothing was written -- no event row,
        # no reflection -- and the refusal is retryable, so the sender's own
        # retry re-delivers once the brake is released.
        raise HTTPException(503, str(e)) from e
    except InboundPersistenceError as e:
        # Never a fabricated success: the event was not stored, so it is not
        # acknowledged as stored.
        logger.error("Inbound capture failed to persist: %s", e)
        raise HTTPException(503, str(e)) from e

    stored = result.stored
    payload: dict[str, Any] = {
        "captured": result.captured,
        "duplicate": result.duplicate,
        "outcome": result.outcome,
        "event": stored.as_dict() if stored is not None else None,
        # Said plainly in the response itself, not only in the docs: a
        # captured event has been recorded, not understood or acted upon.
        "detail": (
            "Received and durably captured. Not processed: nothing has interpreted, "
            "believed or acted on this event."
        ),
    }
    if result.reason:
        payload["reason"] = result.reason
    if result.provenance_degraded:
        # The row exists, so this is not a failed capture -- but it is not a
        # fully recorded one either, and the caller is told so.
        payload["provenance_degraded"] = True
        payload["provenance_error"] = result.provenance_error

    from starlette.responses import JSONResponse

    if not result.captured:
        # Governance refused this event. The refusal itself is recorded (a
        # policy denial is not a brake halt, so recording is permitted), but
        # a refusal is not a capture and the response must not read like one.
        payload["detail"] = "Received and recorded as refused by Governance. Not captured."
        return JSONResponse(status_code=403, content=payload)

    if result.duplicate:
        # 200, not 202: nothing new was created. Reachable only because the
        # original row exists -- `capture_event` reports a duplicate only
        # after reading it back.
        payload["detail"] = (
            "Duplicate delivery of an event already captured. No second event was created."
        )
        return JSONResponse(status_code=200, content=payload)

    return payload


@router.get("/events")
def list_events(limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    """Recently received events and what happened to each — the inspection surface.

    Read-only, and payload bodies are deliberately omitted (the digest
    identifies what was accepted without re-emitting third-party content).
    Readable while the Parking Brake is engaged, because inspection is exactly
    what a halt must not hide.
    """
    if limit < 1 or limit > 100:
        raise HTTPException(400, "limit must be between 1 and 100")
    if offset < 0:
        raise HTTPException(400, "offset must be non-negative")

    from bartholomew.kernel.inbound_store import recent_events

    return recent_events(resolve_db_path(), limit=limit, offset=offset)
