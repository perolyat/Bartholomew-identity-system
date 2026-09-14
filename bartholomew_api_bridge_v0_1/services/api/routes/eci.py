"""The External Capability Interface over HTTP -- the first transport, and only it.

**Why HTTP, and only HTTP, for now.** Every external surface this repository
already has is a FastAPI route on this one app: inbound capture, the device
action channel, multimodal status, consent, governance. The authenticated
control plane, the route-policy table, the admission middleware, the exposure
guard and the TLS posture all live at that boundary and all of them apply here
for free. A WebSocket or a local IPC socket would each need its own answer to
authentication, admission, route policy and exposure before it carried a
single byte -- so choosing one of those first would have meant building
transport machinery instead of proving the boundary.

`bartholomew/eci/contract.py` is written so that adding such a transport later
changes no core type: an adapter turns its own wire format into an
`InboundExchange`, calls `boundary.submit()`, and renders the
`ExchangeReceipt` back. This module is that translation for HTTP and contains
no rule of its own.

**This is a door, not an Executive.** It caps, authenticates, validates,
delegates and renders. Nothing here interprets a payload, decides what an
exchange means, or chooses what Bartholomew should do about it.

What the status codes actually mean
------------------------------------

| Code | Meaning |
|------|---------|
| 202  | Authenticated, validated and **durably recorded**. NOT processed. May carry one governed directive Bartholomew decided to issue. |
| 200  | A duplicate delivery of an exchange already recorded. No second exchange, and no second directive. |
| 401  | The endpoint was not verified, or the boundary is closed. Nothing was recorded. |
| 403  | Governance refused the exchange. The refusal is on the record; the exchange is not. |
| 409  | Recorded, but the thing asked for could not be done: the endpoint's capability standing did not admit a directive, or a result could not be correlated to a directive issued to this endpoint. |
| 413  | The body is larger than this boundary carries. Nothing was recorded. |
| 422  | The envelope is malformed. Nothing was recorded. |
| 503  | A Parking Brake or platform halt is engaged, the backlog is full, or persistence is unavailable. Nothing was recorded. |

**Domain blindness.** `payload` is opaque here exactly as it is on
`/api/inbound/events`: carried, digested and never branched on. Grep this file
for a provider name and you will find none; that is a property to preserve.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from starlette.responses import JSONResponse

from bartholomew.eci import endpoint_auth
from bartholomew.eci.boundary import report_availability, submit
from bartholomew.eci.contract import (
    ECI_CONTRACT_VERSION,
    CapabilityRef,
    ContractError,
    DirectiveStatus,
    ExchangeKind,
    ExchangeOutcome,
    InboundExchange,
    parse_iso,
)
from bartholomew.eci.store import EciPersistenceError, list_directives
from bartholomew.kernel.blocking_executor import run_off_loop

from ..db import resolve_db_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/eci", tags=["eci"])

#: Cap on one request body, measured on the raw bytes **before** they are
#: parsed. A Pydantic model in a handler signature is not a bound -- FastAPI
#: parses the body to build it -- so the body is read and capped first and
#: validated afterwards, matching `routes/inbound.py` and
#: `routes/device_actions.py`.
MAX_BODY_BYTES = 64 * 1024

#: How an outcome is rendered. One table, so no handler invents a code.
_STATUS: dict[ExchangeOutcome, int] = {
    ExchangeOutcome.ACCEPTED: 202,
    ExchangeOutcome.DUPLICATE: 200,
    ExchangeOutcome.REFUSED_IDENTITY: 401,
    ExchangeOutcome.REFUSED_MALFORMED: 422,
    ExchangeOutcome.REFUSED_CAPABILITY: 409,
    ExchangeOutcome.REFUSED_GOVERNANCE: 403,
    ExchangeOutcome.REFUSED_BRAKE: 503,
    ExchangeOutcome.UNCORRELATED: 409,
    ExchangeOutcome.EXPIRED: 409,
    ExchangeOutcome.ALREADY_SETTLED: 409,
    ExchangeOutcome.UNAVAILABLE: 503,
}


class CapabilityIn(BaseModel):
    """One capability, named exactly. Refused rather than approximated."""

    kind: str = Field(..., min_length=1, max_length=64)
    version: int = Field(..., ge=1, le=1_000_000)


class ExchangeIn(BaseModel):
    """The canonical inbound envelope. Domain-blind by construction.

    There is deliberately no `endpoint_id`, `user_id`, `runtime_id` or
    `tenant_id` field. Those are the platform's to establish and are resolved
    from the verified credential; a field for one here would be a field
    somebody could put a lie in.
    """

    exchange_id: str = Field(..., min_length=1, max_length=128)
    kind: str = Field(..., min_length=1, max_length=32)
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: str | None = Field(default=None, max_length=64)
    correlation_id: str | None = Field(default=None, max_length=128)
    status: str | None = Field(default=None, max_length=32)
    contract_version: int = Field(default=ECI_CONTRACT_VERSION, ge=1, le=1_000_000)

    @field_validator("exchange_id", "kind")
    @classmethod
    def _no_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v


class AvailabilityIn(BaseModel):
    """An endpoint's statement about what it can serve right now."""

    capability: CapabilityIn
    available: bool
    detail: str = Field(default="", max_length=200)


def _kernel_or_none() -> Any:
    """The running kernel if there is one. Optional here, unlike on capture.

    The boundary's core takes a `db_path`, not a daemon, so an ECI exchange
    does not require a live kernel object -- but when one is present its
    identity context and loaded config are the honest ones to govern with, so
    they are used.
    """
    try:
        from ..app import _kernel

        return _kernel
    except Exception:  # pragma: no cover - import-time failure only
        return None


async def _authenticated_endpoint(request: Request) -> tuple[bytes, Any, Any]:
    """Cap, then verify the endpoint, then hand back the raw body. In that order.

    Size before anything, so an over-large body is refused having been counted
    and never parsed. Identity before parsing, so an unverified caller never
    reaches the validator -- exactly the order `routes/inbound.py` uses.

    The account `Principal` is enforced ahead of this by the platform's own
    chokepoint and by `route_policy`'s default-deny entry for these paths.
    This adds the second, different question: which endpoint is speaking.
    """
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(
            413,
            f"The exchange exceeds {MAX_BODY_BYTES} bytes; nothing was recorded.",
        )

    resolver = endpoint_auth.get_resolver()
    if resolver is None:
        raise HTTPException(
            401,
            "The External Capability Interface is closed: no endpoint resolver is "
            "installed. This boundary does not accept unauthenticated endpoints, "
            "including from localhost.",
        )
    try:
        identity, device = resolver.resolve(request)
    except endpoint_auth.EndpointResolutionError as exc:
        raise HTTPException(401, str(exc)) from None
    except Exception:
        logger.exception("ECI endpoint resolution failed; refusing the request")
        raise HTTPException(401, "The endpoint could not be verified.") from None
    return body, identity, device


def _parsed(body: bytes, model: type[BaseModel]) -> Any:
    try:
        raw = json.loads(body or b"{}")
    except ValueError as e:
        raise HTTPException(422, f"Body is not valid JSON: {e}") from e
    if not isinstance(raw, dict):
        raise HTTPException(422, "Body must be a JSON object.")
    try:
        return model.model_validate(raw)
    except Exception as e:
        raise HTTPException(422, f"Invalid envelope: {e}") from e


@router.post("/exchanges", status_code=202)
async def submit_exchange(request: Request) -> Any:
    """Submit one observation, request or result across the boundary."""
    body, identity, device = await _authenticated_endpoint(request)
    envelope = _parsed(body, ExchangeIn)

    try:
        kind = ExchangeKind(envelope.kind)
    except ValueError:
        raise HTTPException(
            422,
            f"kind must be one of {[k.value for k in ExchangeKind]}; "
            f"got {envelope.kind!r}. An unknown kind is refused, never approximated.",
        ) from None

    status = None
    if envelope.status is not None:
        try:
            status = DirectiveStatus(envelope.status)
        except ValueError:
            raise HTTPException(
                422,
                f"status must be one of {[s.value for s in DirectiveStatus]}; "
                f"got {envelope.status!r}. 'unknown' is a real answer and is never "
                "folded into 'failed'.",
            ) from None

    occurred_at = None
    if envelope.occurred_at is not None:
        try:
            occurred_at = parse_iso(envelope.occurred_at)
        except ContractError as e:
            raise HTTPException(422, str(e)) from None

    try:
        exchange = InboundExchange(
            endpoint=identity,
            exchange_id=envelope.exchange_id,
            kind=kind,
            payload=envelope.payload,
            occurred_at=occurred_at,
            correlation_id=envelope.correlation_id,
            status=status,
            contract_version=envelope.contract_version,
        )
    except ContractError as e:
        raise HTTPException(422, str(e)) from None

    kernel = _kernel_or_none()
    db_path = getattr(getattr(kernel, "mem", None), "db_path", None) or resolve_db_path()

    receipt = await submit(
        exchange,
        device=device,
        db_path=db_path,
        identity_context=getattr(kernel, "identity_context", None),
        runtime_cfg=getattr(kernel, "cfg", None),
        # From the platform's authority -- this process's runtime binding --
        # never from the endpoint. See `eci/endpoint_auth.py`.
        runtime_id=identity.user_id,
    )
    return JSONResponse(status_code=_STATUS[receipt.outcome], content=receipt.as_dict())


@router.post("/availability", status_code=200)
async def report_capability_availability(request: Request) -> Any:
    """Report whether this endpoint can currently serve a capability.

    Declaration is not availability: an endpoint reporting itself ready for
    something it never declared is told so, and the report grants it nothing.
    The standing returned is the real one, not an echo of what was claimed.
    """
    body, identity, device = await _authenticated_endpoint(request)
    envelope = _parsed(body, AvailabilityIn)

    try:
        capability = CapabilityRef(
            kind=envelope.capability.kind,
            version=envelope.capability.version,
        )
    except ContractError as e:
        raise HTTPException(422, str(e)) from None

    db_path = resolve_db_path()
    try:
        # Synchronous sqlite3 writes (schema + the availability row), so off
        # the event loop -- see `bartholomew.eci.boundary.submit()`.
        decision = await run_off_loop(
            report_availability,
            db_path,
            identity,
            capability,
            device=device,
            available=envelope.available,
            detail=envelope.detail,
        )
    except EciPersistenceError as e:
        raise HTTPException(503, f"The report was NOT recorded: {e}") from None

    return {
        "endpoint_id": identity.endpoint_id,
        "standing": decision.as_dict(),
        "contract_version": ECI_CONTRACT_VERSION,
    }


@router.get("/endpoint")
async def describe_endpoint(request: Request) -> Any:
    """What this endpoint is, as the platform sees it. A read, and only its own.

    Deliberately answers about the caller rather than taking an id: an
    endpoint may inspect itself and nothing else, so there is no parameter
    here through which one endpoint could ask about another.
    """
    _body, identity, device = await _authenticated_endpoint(request)
    manifest = getattr(device, "manifest", None)
    # `known` and `unknown` are projections (properties) on
    # `DeviceCapabilityManifest`, not methods. Both are reported: a capability
    # this deployment does not understand was still *declared*, and hiding it
    # would make an endpoint look like it had claimed less than it did.
    declared = [c.as_dict() for c in getattr(manifest, "known", ())] if manifest else []
    undeclared_support = [c.as_dict() for c in getattr(manifest, "unknown", ())] if manifest else []
    return {
        "endpoint_id": identity.endpoint_id,
        "endpoint_kind": identity.endpoint_kind,
        "verified_by": identity.verified_by,
        "source_id": identity.source_id,
        "declared_capabilities": declared,
        "declared_but_unsupported": undeclared_support,
        "contract_version": ECI_CONTRACT_VERSION,
        "detail": (
            "Declared capabilities are what this endpoint says it can do. They are "
            "not permission to decide when any of them happens, and they are not a "
            "statement that any of them can be served right now."
        ),
    }


@router.get("/directives")
async def recent_directives(request: Request, limit: int = 50) -> Any:
    """Directives issued to this endpoint, newest first. Its own, and only its own."""
    _body, identity, _device = await _authenticated_endpoint(request)
    try:
        rows = list_directives(resolve_db_path(), identity.endpoint_id, limit=limit)
    except EciPersistenceError as e:
        raise HTTPException(503, str(e)) from None
    return {"endpoint_id": identity.endpoint_id, "directives": rows}


__all__ = ["router"]
