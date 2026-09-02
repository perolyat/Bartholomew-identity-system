"""The device action channel -- the dispatch side, and a different trust boundary.

Two endpoints, reachable only by an enrolled device that a **separate**
resolver has verified. This is the only surface in Bartholomew from which a
validated action's parameters ever leave the server, and it is deliberately not
reachable from the observation path:

* the observation companion POSTs to `/api/inbound/events` and its client has
  one verb that returns three scalars, so an action in an inbound *response*
  reaches a client with nowhere to put it (`tests/test_companion_no_actuation.py`);
* this channel is authenticated by `device_action_auth`, whose resolver is a
  different module global from `inbound_auth`'s -- installing one does not open
  the other, and `tests/test_windows_action_channel_separation.py` proves it by
  installing the inbound test resolver and showing that leasing still 401s.

**Leasing authorises nothing on its own.** Every action handed over here has
just been through the complete eleven-point admission again, in
`seam.run_action_dispatch_through_runtime_contract()`: the Parking Brake is
re-read (an approval is never a brake override), the enrolment is re-checked,
the parameters are re-validated against the device's *current* allowlists, the
approval is re-verified against the exact request, and the lease is a single
conditional `UPDATE` that exactly one caller can win.

| Code | Meaning |
|------|---------|
| 200  | Zero or more actions leased. An empty list is the normal answer. |
| 401  | This device is not verified. Nothing was leased. |
| 403  | Governance refused, or the device asked about an action that is not its own. |
| 409  | The result arrived for an action that had already ended. It was **not** applied. |
| 503  | The Parking Brake is engaged, or persistence is unavailable. |
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from bartholomew.actuation import seam, store
from bartholomew.actuation.result import ErrorCategory
from bartholomew.actuation.store import ActionPersistenceError

from .. import device_action_auth
from ..db import resolve_db_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/device-actions", tags=["device-actions"])

#: Cap on one request body, measured on the raw bytes **before** they are
#: parsed. A Pydantic model in a handler's signature is not a bound -- FastAPI
#: parses the body to build it -- so `_authenticated_device()` reads and caps
#: first and the handlers validate afterwards, matching `routes/inbound.py`.
MAX_BODY_BYTES = 64 * 1024

#: Most actions one lease call will hand over, whatever the device asks for.
MAX_LEASE_BATCH = 10


class LeaseIn(BaseModel):
    """What a device asks for. It cannot ask on behalf of another device.

    `device_id` is present so a mismatch against the verified device is a loud
    refusal rather than a silent substitution -- the handler compares the two
    and refuses. It is never *used* as the identity.
    """

    device_id: str = Field(..., min_length=1, max_length=128)
    limit: int = Field(default=5, ge=1, le=MAX_LEASE_BATCH)


class ResultIn(BaseModel):
    """What a device reports back.

    `status` is constrained by the seam to the five a device can honestly make
    a claim about: `started`, `succeeded`, `failed`, `cancelled`, `unknown`. A
    device may not report `accepted` or `refused` -- those are Governance's
    words about its own decision, and a device that could say them would be
    claiming an authority it does not have.
    """

    device_id: str = Field(..., min_length=1, max_length=128)
    status: str = Field(..., min_length=1, max_length=32)
    error_category: str | None = Field(default=None, max_length=64)
    detail: str = Field(default="", max_length=500)
    evidence: dict[str, Any] = Field(default_factory=dict)
    observed_at: str = Field(default="", max_length=64)


def _kernel_or_503() -> Any:
    from ..app import _kernel

    if _kernel is None:  # pragma: no cover - admission middleware covers this first
        raise HTTPException(503, "Runtime unavailable; nothing was dispatched.")
    return _kernel


def _require_principal(request: Request) -> Any:
    """The verified principal, or refuse before anything is leased.

    Two independent questions are answered on this channel, and both must pass:

    * **Whose Bartholomew is this?** -- the control plane's verified principal
      plus this process's runtime binding. Platform authority.
    * **Which machine is calling?** -- the device resolver, below. Provenance
      only.

    A verified device is emphatically not a substitute for the first: it says
    a call genuinely came from the desk PC, not that the desk PC may act inside
    Taylor's runtime. Checked here as well as at the control plane's own
    chokepoint -- not because that boundary is doubted, but because a dispatch
    path that silently depends on a middleware ordering it does not control is
    one refactor away from being unauthenticated, and this is the surface where
    that would matter most.
    """
    if not device_action_auth.principal_required():
        # Authentication is not enforced: the single-user loopback development
        # deployment. Dispatch is still gated by the device resolver, which is
        # fail-closed by default and installed by nothing.
        return None
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise HTTPException(
            401,
            "Authentication is required to reach the action channel; nothing was dispatched.",
        )
    return principal


async def _verify_device(request: Request, body: bytes) -> Any:
    """Resolve the verified device, or refuse with 401.

    Fails closed three ways: no resolver installed, a resolver that returns
    None, and a resolver that raises. Loopback is never consulted -- see
    `device_action_auth`'s module docstring.
    """
    resolver = device_action_auth.get_resolver()
    if resolver is None:
        raise HTTPException(
            401,
            "The device action channel is closed: no device resolver is installed. "
            "This endpoint does not dispatch to unauthenticated devices, including "
            "from localhost. Installing the inbound observation resolver does not "
            "open this channel.",
        )
    try:
        device = await resolver.resolve(request, body)
    except Exception:
        # An errored verifier is an unverified caller.
        logger.exception("Device action resolution failed; refusing the request")
        raise HTTPException(
            401,
            "Device verification failed; nothing was dispatched.",
        ) from None
    if device is None:
        raise HTTPException(401, "The device could not be verified; nothing was dispatched.")
    return device


async def _authenticated_device(
    request: Request,
    model: type[BaseModel],
) -> tuple[Any, str, Any]:
    """Cap, authenticate, verify, then validate. In that order, deliberately.

    Size before anything, so an over-large body is refused having been counted
    but never parsed. Identity before provenance, so an unauthenticated caller
    is refused before a resolver ever sees the body. Validation last, because
    an unverified caller must not reach the validator either -- exactly the
    order `routes/inbound.py` uses.
    """
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(413, "The request body is too large; nothing was dispatched.")
    _require_principal(request)
    device = await _verify_device(request, body)

    import json

    try:
        raw = json.loads(body or b"{}")
    except ValueError as e:
        raise HTTPException(422, f"Body is not valid JSON: {e}") from e
    if not isinstance(raw, dict):
        raise HTTPException(422, "Body must be a JSON object.")
    try:
        payload = model.model_validate(raw)
    except Exception as e:
        raise HTTPException(422, f"Invalid request: {e}") from e

    # From the platform's authority -- the verified principal and this
    # process's runtime binding -- never from the device. A resolver that
    # claims a tenant is ignored entirely; see
    # `device_action_auth.resolved_tenant_id`.
    return device, device_action_auth.resolved_tenant_id(request), payload


@router.post("/lease")
async def lease_actions(request: Request) -> Any:
    """Hand this device the actions it may run now. Possibly none.

    Each candidate goes through the whole admission individually, so a refusal
    on one does not withhold the others and an admitted one is genuinely
    admitted rather than merely listed. Refusals are recorded, not returned:
    the device is told what it may do, never why something else was withheld,
    because that reasoning is governance state and not the device's business.
    """
    device, tenant, payload = await _authenticated_device(request, LeaseIn)

    if payload.device_id != device.device_id:
        raise HTTPException(
            403,
            "The device_id in the body does not match the verified device; nothing "
            "was dispatched.",
        )

    kernel = _kernel_or_503()
    db_path = getattr(getattr(kernel, "mem", None), "db_path", None) or resolve_db_path()

    try:
        # Housekeeping first, so an expired action is never even a candidate.
        store.expire_overdue(db_path, tenant_id=tenant)
        candidates = store.dispatchable_action_ids(
            db_path,
            tenant_id=tenant,
            device_id=device.device_id,
            limit=min(payload.limit, MAX_LEASE_BATCH),
        )
    except ActionPersistenceError as e:
        raise HTTPException(503, str(e)) from e

    leased: list[dict[str, Any]] = []
    for action_id in candidates:
        try:
            result = await seam.run_action_dispatch_through_runtime_contract(
                kernel,
                tenant_id=tenant,
                device_id=device.device_id,
                action_id=action_id,
                registry=None,
            )
        except ActionPersistenceError as e:
            logger.error("Action %s could not be leased: %s", action_id, e)
            continue

        if result.category is ErrorCategory.PARKING_BRAKE:
            # A halt applies to the whole batch, not to one action. Stop asking.
            raise HTTPException(503, result.reason or "A parking brake is engaged.")
        if not result.governance_allowed or result.request is None:
            continue

        leased.append(
            {
                "action_id": result.request.action_id,
                "tenant_id": result.request.tenant_id,
                "device_id": result.request.device_id,
                "capability": result.request.capability.value,
                "capability_version": result.request.capability_version,
                # The one moment canonical parameters leave the server, to the
                # one device the action targets, over an authenticated channel,
                # after eleven checks. The device validates them again.
                "parameters": dict(result.request.parameters.canonical),
                "expires_at": result.request.expires_at,
                "repeatability": result.request.repeatability.value,
                "correlation_id": result.request.correlation_id,
            },
        )

    return {
        "device_id": device.device_id,
        "verified_by": device.verified_by,
        "actions": leased,
        "detail": (
            "Leased. Each of these passed the full governance admission a moment ago; "
            "the device is expected to validate them again before acting, and to "
            "report 'unknown' rather than 'succeeded' for anything it cannot observe."
        ),
    }


@router.post("/{action_id}/result")
async def report_result(action_id: str, request: Request) -> Any:
    """Record what the device observed. Truthfully, and once.

    A result for an action that had already ended -- cancelled underneath the
    device, expired, or already reported -- is **not** applied, and the 409
    says so. That is the property that stops a cancelled action from acquiring
    a success after the fact.
    """
    from starlette.responses import JSONResponse

    device, tenant, payload = await _authenticated_device(request, ResultIn)
    if payload.device_id != device.device_id:
        raise HTTPException(
            403,
            "The device_id in the body does not match the verified device; the result "
            "was not recorded.",
        )

    kernel = _kernel_or_503()
    try:
        result = await seam.record_action_result_through_runtime_contract(
            kernel,
            tenant_id=tenant,
            device_id=device.device_id,
            action_id=action_id,
            status=payload.status,
            error_category=payload.error_category,
            detail=payload.detail,
            evidence=payload.evidence,
            observed_at=payload.observed_at,
        )
    except ActionPersistenceError as e:
        raise HTTPException(503, str(e)) from e

    body: dict[str, Any] = {
        "status": result.status.value,
        "recorded": result.governance_allowed,
        "action": result.action.as_dict() if result.action is not None else None,
    }
    if result.reason:
        body["reason"] = result.reason
    if result.category is not None:
        body["error_category"] = result.category.value
    if result.provenance_degraded:
        body["provenance_degraded"] = True
        body["provenance_error"] = result.provenance_error

    if not result.governance_allowed:
        status_code = 409 if result.category is ErrorCategory.REPLAY_REFUSED else 403
        body["detail"] = (
            "The result was not applied: this action had already ended. Its recorded "
            "outcome is unchanged."
        )
        return JSONResponse(status_code=status_code, content=body)

    body["detail"] = "Recorded."
    return body
