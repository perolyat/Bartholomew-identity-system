"""The governed Windows action surface -- the request and approval side.

Four verbs a person (or a UI acting for one) uses: request an action, look at
what is pending, approve exactly one, and cancel one. **None of them makes
anything happen on a computer.** Dispatch is a different router
(`device_actions.py`), reached over a different authenticated channel, by the
device itself.

That split is the point. A request creates a pending record; an approval makes
one record eligible; the device then asks for work and the whole eleven-point
admission runs again before anything is handed over. There is no verb here
that reaches an operating system, and no response from here that a companion
consumes.

**What the status codes mean.** Distinct states, never conflated:

| Code | Meaning |
|------|---------|
| 201  | Admitted and durably recorded as pending approval (or as approved, where the device's enrolment grants explicit trusted autonomy). **Nothing has run.** |
| 200  | The read, approve or cancel succeeded. |
| 403  | Governance refused. The refusal itself may be recorded; nothing ran. |
| 409  | The action is not in a state this verb applies to -- already approved, already ended, already leased. |
| 422  | The envelope or the parameters were refused. Nothing was recorded. |
| 503  | The Parking Brake is engaged, or persistence is unavailable. Nothing was recorded. |

There is no code here that means "done": acknowledgement never outruns what
actually happened.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from bartholomew.actuation import seam
from bartholomew.actuation.result import ActionResultStatus, ErrorCategory
from bartholomew.actuation.store import ActionPersistenceError

from .. import device_action_auth
from ..db import resolve_db_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/actions", tags=["actions"])

#: Cap on one request body. An action envelope is small; anything larger is
#: either a mistake or an attempt to make the door the problem.
MAX_BODY_BYTES = 32 * 1024

#: Refusal categories that mean "the caller asked for something impossible"
#: rather than "governance said no". Mapped to 422 so a client can tell a
#: validation problem from a policy one without parsing prose.
_UNPROCESSABLE = frozenset(
    {
        ErrorCategory.PARAMETERS_INVALID,
        ErrorCategory.CAPABILITY_UNSUPPORTED,
    },
)

#: Refusal categories that mean "this action has moved on". 409, so a retry
#: loop can stop rather than repeating a request that can never succeed.
_CONFLICT = frozenset(
    {
        ErrorCategory.REPLAY_REFUSED,
        ErrorCategory.APPROVAL_INVALID,
        ErrorCategory.CANCELLED,
        ErrorCategory.EXPIRED,
    },
)


class ActionRequestIn(BaseModel):
    """The action envelope.

    Note what is **not** here, and cannot be added by a caller: `tenant_id`,
    `requested_by`, and any notion of who approved anything. Those are resolved
    from the platform's own authority in the handler. A caller that could name
    its tenant would have a cross-tenant write primitive.

    `parameters` is typed per capability by
    `bartholomew/actuation/parameters.py`, which refuses any key the capability
    does not name. It is not an opaque passthrough.
    """

    device_id: str = Field(..., min_length=1, max_length=128)
    capability: str = Field(..., min_length=1, max_length=64)
    capability_version: int = Field(default=1, ge=0, le=1_000_000)
    parameters: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = Field(default=None, max_length=128)
    causation_id: str | None = Field(default=None, max_length=128)
    #: Supply this to make a retry of the same logical request collapse onto
    #: the same action instead of creating a second one.
    action_id: str | None = Field(default=None, max_length=128)
    repeatability: str | None = Field(default=None, max_length=32)
    ttl_seconds: int | None = Field(default=None, ge=1, le=3600)

    @field_validator("device_id", "capability")
    @classmethod
    def _no_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v


class ApprovalIn(BaseModel):
    """One approval decision. The approver is never taken from the body."""

    note: str | None = Field(default=None, max_length=280)
    ttl_seconds: int | None = Field(default=None, ge=1, le=900)


class CancellationIn(BaseModel):
    reason: str | None = Field(default=None, max_length=200)


def _kernel_or_503() -> Any:
    """The running kernel, or an honest refusal.

    The admission middleware already refuses non-exempt routes while the daemon
    is not RUNNING, so reaching here with no kernel means the runtime went away
    between admission and handling. Refuse rather than record governed state
    into a database whose owner is not alive.
    """
    from ..app import _kernel

    if _kernel is None:  # pragma: no cover - admission middleware covers this first
        raise HTTPException(503, "Runtime unavailable; nothing was recorded.")
    return _kernel


def _requesting_identity(request: Request) -> str:
    """Who asked, from the platform's authority. Never from the body.

    Falls back to the named local sentinel on the single-user loopback
    deployment, where `auth_enforced()` is false and there is exactly one
    person. Named rather than blank, so the durable row always answers "who
    asked?" with something.
    """
    principal = getattr(request.state, "principal", None)
    if principal is not None:
        return str(getattr(principal, "user_id", "") or getattr(principal, "username", ""))
    return device_action_auth.LOCAL_TENANT


def _tenant(request: Request) -> str:
    return device_action_auth.resolved_tenant_id(request)


def _status_code(result: seam.ActionSeamResult, *, created: bool) -> int:
    if result.governance_allowed:
        return 201 if created else 200
    if result.category in _UNPROCESSABLE:
        return 422
    if result.category in _CONFLICT:
        return 409
    if result.category is ErrorCategory.PARKING_BRAKE:
        return 503
    if result.category is ErrorCategory.INTERNAL_ERROR:
        return 500
    return 403


def _body(result: seam.ActionSeamResult) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": result.status.value,
        "governance_allowed": result.governance_allowed,
        "action": result.action.as_dict() if result.action is not None else None,
        # Said plainly in the response itself, not only in the docs.
        "detail": (
            "Recorded. Nothing has been dispatched to any device: the target device "
            "must separately request it over its own authenticated channel, at which "
            "point every governance check runs again."
        ),
    }
    if result.category is not None:
        payload["error_category"] = result.category.value
    if result.reason:
        payload["reason"] = result.reason
    if result.provenance_degraded:
        # The row exists, so this is not a failed decision -- but it is not a
        # fully recorded one either, and the caller is told so.
        payload["provenance_degraded"] = True
        payload["provenance_error"] = result.provenance_error
    return payload


def _ctx(kernel: Any) -> Any:
    """The runtime context the seam reads. The kernel itself, unchanged.

    The seam reads `mem`, `identity_context`, `governance_store` and
    `blocking_executor` off whatever it is handed, exactly as every other
    Runtime Contract seam does -- so the kernel *is* the context and there is
    no adapter object to keep in step with it.
    """
    return kernel


@router.post("", status_code=201)
async def request_action(payload: ActionRequestIn, request: Request) -> Any:
    """Ask for one action on one enrolled device. Records; does not dispatch."""
    from starlette.responses import JSONResponse

    kernel = _kernel_or_503()
    try:
        result = await seam.run_action_request_through_runtime_contract(
            _ctx(kernel),
            tenant_id=_tenant(request),
            device_id=payload.device_id,
            requested_by=_requesting_identity(request),
            capability=payload.capability,
            capability_version=payload.capability_version,
            parameters=payload.parameters,
            correlation_id=payload.correlation_id,
            causation_id=payload.causation_id,
            action_id=payload.action_id,
            repeatability=payload.repeatability,
            ttl_seconds=payload.ttl_seconds,
        )
    except ActionPersistenceError as e:
        # Never a fabricated success: the action was not recorded, so it is not
        # acknowledged as recorded.
        logger.error("A Windows action could not be recorded: %s", e)
        raise HTTPException(503, str(e)) from e

    return JSONResponse(status_code=_status_code(result, created=True), content=_body(result))


@router.get("")
async def list_actions(request: Request, limit: int = 50, offset: int = 0) -> Any:
    """Recent actions and what happened to each -- the inspection surface.

    Read-only, and parameters are the **redacted** form: text somebody asked to
    have typed appears as a digest and a length, never as itself. Readable
    while the Parking Brake is engaged, because inspection is exactly what a
    halt must not hide.
    """
    if limit < 1 or limit > 100:
        raise HTTPException(400, "limit must be between 1 and 100")
    if offset < 0:
        raise HTTPException(400, "offset must be non-negative")

    from bartholomew.actuation import store

    kernel = _kernel_or_503()
    db_path = getattr(getattr(kernel, "mem", None), "db_path", None) or resolve_db_path()
    tenant = _tenant(request)
    try:
        return {
            "tenant_id": tenant,
            "actions": store.recent_actions(
                db_path,
                tenant_id=tenant,
                limit=limit,
                offset=offset,
            ),
        }
    except ActionPersistenceError as e:
        raise HTTPException(503, str(e)) from e


@router.get("/{action_id}")
async def read_action(action_id: str, request: Request) -> Any:
    """One action, its state, and every result recorded against it."""
    from bartholomew.actuation import store

    kernel = _kernel_or_503()
    db_path = getattr(getattr(kernel, "mem", None), "db_path", None) or resolve_db_path()
    tenant = _tenant(request)
    try:
        action = store.get_action(db_path, tenant_id=tenant, action_id=action_id)
        if action is None:
            raise HTTPException(404, "No such action in this tenant.")
        return {
            "action": action.as_dict(),
            "results": store.results_for(db_path, tenant_id=tenant, action_id=action_id),
        }
    except ActionPersistenceError as e:
        raise HTTPException(503, str(e)) from e


@router.post("/{action_id}/approve")
async def approve_action(action_id: str, payload: ApprovalIn, request: Request) -> Any:
    """Approve exactly this action, as it stands, for a bounded window.

    The approval binds to the action id, tenant, device, capability, capability
    version and a fingerprint of the canonical parameters. Changing any of them
    -- including re-requesting the same action with one character different --
    produces a different fingerprint and invalidates this approval.

    Approving still does not run anything: it makes one action eligible to be
    leased, and every gate is evaluated again when it is.
    """
    from starlette.responses import JSONResponse

    kernel = _kernel_or_503()
    try:
        result = await seam.grant_action_approval(
            _ctx(kernel),
            tenant_id=_tenant(request),
            action_id=action_id,
            approver=_requesting_identity(request),
            note=payload.note,
            ttl_seconds=payload.ttl_seconds,
        )
    except ActionPersistenceError as e:
        raise HTTPException(503, str(e)) from e

    body = _body(result)
    if result.governance_allowed:
        body["detail"] = (
            "Approved. The action is now eligible for its device to lease; every "
            "governance check, the Parking Brake included, is evaluated again at "
            "that point. This approval authorises no other action, device, "
            "capability, user or set of parameters."
        )
    return JSONResponse(status_code=_status_code(result, created=False), content=body)


@router.post("/{action_id}/cancel")
async def cancel_action(action_id: str, payload: CancellationIn, request: Request) -> Any:
    """Withdraw an action so it can never run.

    Works on a pending, approved *or* leased action. Cancelling a leased action
    cannot reach out and stop a device mid-call -- nothing can -- but it makes
    the action un-leasable and makes any result the device later reports inert,
    so a cancelled action can never acquire a success.
    """
    from starlette.responses import JSONResponse

    kernel = _kernel_or_503()
    try:
        result = await seam.cancel_action_through_runtime_contract(
            _ctx(kernel),
            tenant_id=_tenant(request),
            action_id=action_id,
            cancelled_by=_requesting_identity(request),
            reason=payload.reason,
        )
    except ActionPersistenceError as e:
        raise HTTPException(503, str(e)) from e

    body = _body(result)
    if result.status is ActionResultStatus.CANCELLED:
        body["detail"] = (
            "Cancelled. It cannot be leased, and any result a device reports for it "
            "from here on is recorded as inert rather than applied."
        )
    return JSONResponse(status_code=_status_code(result, created=False), content=body)
