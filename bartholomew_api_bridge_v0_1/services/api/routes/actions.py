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

from bartholomew.actuation import arming, seam
from bartholomew.actuation.result import ActionResultStatus, ErrorCategory
from bartholomew.actuation.store import ActionPersistenceError

from .. import device_action_auth
from ..companion_auth import require_companion
from ..db import resolve_db_path

logger = logging.getLogger(__name__)

#: The capability a device must declare before its channel may be armed.
#: `windows.focus_window` is the least powerful Windows capability in the
#: vocabulary -- naming it here asks "is this a Windows actuation device at
#: all?", not "may it do the specific thing somebody will approve later".
#: Which capability an individual action needs is still checked per action, by
#: the seam, against the device's own declaration.
WINDOWS_ARM_CAPABILITY = "windows.focus_window"
WINDOWS_ARM_CAPABILITY_VERSION = 1

router = APIRouter(prefix="/api/actions", tags=["actions"])

#: Cap on one request body, measured on the raw bytes **before** they are
#: parsed. Declaring a Pydantic model in a handler's signature is not a bound:
#: FastAPI parses the body to build it, so a size check inside the handler runs
#: after the parse it was meant to prevent. `_validated_body()` below reads and
#: caps first, which is the same order `routes/inbound.py` uses and for the
#: same reason -- `parameters` is an open object, and an unbounded one is a way
#: to make the door the problem.
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


async def _validated_body(request: Request, model: type[BaseModel]) -> Any:
    """Read the body, cap it, then validate. In that order.

    Read-before-parse, matching the inbound route: an over-large body is
    refused with 413 having been counted but never parsed, and a malformed one
    is refused with 422 having reached no governed state.
    """
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(
            413,
            f"The request body exceeds {MAX_BODY_BYTES} bytes; nothing was recorded.",
        )
    import json

    try:
        raw = json.loads(body or b"{}")
    except ValueError as e:
        raise HTTPException(422, f"Body is not valid JSON: {e}") from e
    if not isinstance(raw, dict):
        raise HTTPException(422, "Body must be a JSON object.")
    try:
        return model.model_validate(raw)
    except Exception as e:
        raise HTTPException(422, f"Invalid request: {e}") from e


def _existing_summary(action: Any) -> dict[str, Any]:
    """The minimum that identifies an action that already exists.

    Enough for a caller to recognise its own re-submission and go and read the
    record properly; not enough to be a read surface in its own right.
    """
    return {
        "action_id": action.action_id,
        "tenant_id": action.tenant_id,
        "state": action.state.value,
        "status": action.status.value,
    }


def _approval_summary(action: Any) -> dict[str, Any]:
    """What an approver needs beside the parameters: the power, and its risk.

    The capability descriptor's `summary` describes what the *capability* can
    do, not what this request asks for, so it stays true whatever the
    parameters say -- and it sits next to the canonical parameters, which say
    exactly what this request asks for. Between them an approver can answer
    both questions a decision needs: what am I allowing, and what will it do.
    """
    from bartholomew.actuation.capabilities import (
        UnsupportedCapabilityError,
        describe,
        parse_kind,
    )

    try:
        descriptor = describe(parse_kind(action.capability))
    except UnsupportedCapabilityError:
        # A stored row naming a capability this build no longer implements. It
        # can never be dispatched, and the summary says so rather than leaving
        # a reader to infer it from an absence.
        return {
            "capability": action.capability,
            "summary": (
                "This build does not implement this capability, so this action can never run."
            ),
            "risk_class": action.risk_class,
            "approval_requirement": action.approval_requirement,
        }
    return {
        "capability": descriptor.kind.value,
        "summary": descriptor.summary,
        "risk_class": descriptor.risk.value,
        "approval_requirement": descriptor.approval.value,
        "trusted_autonomy_eligible": descriptor.trusted_autonomy_eligible,
    }


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
        if not created:
            return 200
        if result.existing:
            # A re-submission of an id that already exists. 201 would tell a
            # client keying on the status code -- which is the documented
            # contract -- that it has a fresh pending action to wait on. If the
            # existing one has already finished, that client waits forever for
            # an approval prompt that will never come; and on a `succeeded`
            # row, "nothing has run" is simply false.
            return 409 if result.action is not None and result.action.terminal else 200
        return 201
    if result.category in _UNPROCESSABLE:
        return 422
    if result.category in _CONFLICT:
        return 409
    if result.category in (ErrorCategory.PARKING_BRAKE, ErrorCategory.INTERNAL_ERROR):
        # Both are "come back later", and both are 503. An internal error here
        # is a persistence failure -- the same underlying condition that
        # reaches `ActionPersistenceError` elsewhere in this file and is
        # answered 503 there. Answering it 500 instead was inconsistent, and
        # 500 is not in this module's own status table.
        return 503
    return 403


#: What each outcome actually is, said in the response rather than only in the
#: docs. The default used to be "Recorded." for everything, so a 403 for an
#: unenrolled device -- which deliberately writes no action row at all -- came
#: back saying it had been recorded. That is precisely the "acknowledgement
#: never outruns what actually happened" invariant this module opens with.
_ACCEPTED_DETAIL = (
    "Recorded. Nothing has been dispatched to any device: the target device must "
    "separately request it over its own authenticated channel, at which point every "
    "governance check runs again."
)
_EXISTING_DETAIL = (
    "An action with this id already exists. Nothing was created, and the recorded "
    "action is returned unchanged."
)
_REFUSED_DETAIL = "Refused by Governance. Nothing was dispatched, and no new action was created."


def _body(result: seam.ActionSeamResult) -> dict[str, Any]:
    if not result.governance_allowed:
        detail = _REFUSED_DETAIL
    elif result.existing:
        detail = _EXISTING_DETAIL
    else:
        detail = _ACCEPTED_DETAIL
    payload: dict[str, Any] = {
        "status": result.status.value,
        "governance_allowed": result.governance_allowed,
        # A re-submission is answered with the minimum that identifies what
        # already exists. The full record is `GET /api/actions/{id}`, which
        # requires `action:read` -- a capability deliberately split from
        # `action:request`, and returning the whole row from the write verb
        # would have let a holder of the latter alone poll it to watch who
        # approved what and when.
        "action": (
            _existing_summary(result.action)
            if (result.existing and result.action is not None)
            else (result.action.as_dict() if result.action is not None else None)
        ),
        "detail": detail,
    }
    if result.category is not None:
        payload["error_category"] = result.category.value
    if result.reason:
        # An internal error's reason carries the driver's own exception text.
        # It is logged and replaced rather than returned -- not because sqlite3
        # leaks the file path (its messages here do not), but because the
        # driver's wording is an implementation detail that tells a caller
        # nothing it can act on, and the actionable fact is whether to retry.
        if result.category is ErrorCategory.INTERNAL_ERROR:
            logger.error("A governed action failed internally: %s", result.reason)
            payload["reason"] = (
                "the action could not be recorded; nothing changed and it is safe to retry"
            )
        else:
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


class ArmRequestIn(BaseModel):
    """Arming one device's channel. Deliberately tiny.

    There is no `tenant_id` here and there cannot be: which tenant is arming
    is the platform's answer, from `_tenant()`, and a body that could name one
    would be a cross-tenant arming primitive. `seconds` is accepted but capped
    by `arming.MAX_ARM_SECONDS`, so a caller cannot ask for a longer window
    than the design allows.
    """

    device_id: str
    seconds: int | None = None
    reason: str | None = None


def _arm_brake_engaged() -> bool:
    """Whether any brake scope is engaged. Fail-closed on an unreadable brake.

    Reads the same `GovernanceStore` every other gate reads -- there is no
    second safety authority here. Arming while halted is refused, and an
    unreadable brake refuses too: "we could not tell" is not "clear".
    """
    from bartholomew.orchestrator.safety.governance_store import GovernanceStore

    try:
        return bool(GovernanceStore(resolve_db_path()).is_blocked("actuation"))
    except Exception:
        logger.exception("Brake state unreadable while arming; refusing")
        return True


@router.post("/channel/arm")
async def arm_channel(request: Request) -> Any:
    """Open this tenant's Windows action channel for a bounded window.

    **Arming is not approval.** It authorises no action: every action still
    needs its own explicit, content-bound approval, and an unapproved one is
    refused on an armed channel exactly as it was on a closed one. What this
    opens is the coarser question of whether the machine may carry out
    anything at all right now.

    Five things must hold, and each is checked by whoever owns it: the caller
    is an authenticated enrolled device (Session E's credential), the device
    belongs to the server-derived tenant, it declares Windows actuation, the
    Parking Brake is clear, and the request is explicit. None of them is read
    from the body.
    """
    payload: ArmRequestIn = await _validated_body(request, ArmRequestIn)
    companion = require_companion(request)
    tenant = _tenant(request)

    if payload.device_id != companion.device_id:
        # Arming a device other than the one that authenticated would let a
        # credential for one machine open another machine's channel.
        raise HTTPException(
            403,
            "the channel may only be armed for the device that authenticated " "this request",
        )
    # Ownership is checked against the platform's identity when the platform
    # has one. `LOCAL_TENANT` is `resolved_tenant_id`'s named sentinel for "this
    # process is unbound and no principal exists" -- the single-runtime local
    # deployment, where there is exactly one account and nothing to cross. It
    # is not a tenant a device can belong to, so comparing against it would
    # refuse every real enrolment rather than catching anything.
    if tenant not in (device_action_auth.LOCAL_TENANT, companion.owner_user_id):
        raise HTTPException(403, "that device does not belong to this account")

    companion.require_capability(WINDOWS_ARM_CAPABILITY, WINDOWS_ARM_CAPABILITY_VERSION)

    if _arm_brake_engaged():
        raise HTTPException(
            409,
            "The Parking Brake is engaged; the action channel cannot be armed.",
        )

    window = arming.arm(
        tenant_id=tenant,
        device_id=companion.device_id,
        armed_by=_requesting_identity(request),
        seconds=payload.seconds,
        reason=payload.reason,
    )
    return {"armed": True, "channel": window.describe()}


@router.get("/channel")
async def read_channel(request: Request) -> Any:
    """Whether the channel is armed, for how long, and for which device.

    Readable without a device credential, and readable while the brake is
    engaged: "can this machine act right now?" is exactly the question a
    person needs answered when they are worried, and an inspection surface
    that disappears under a halt is the wrong shape.
    """
    described = arming.describe(tenant_id=_tenant(request))
    if described["armed"] and _arm_brake_engaged():
        # Truthful rather than merely accurate: the window is open, and the
        # brake means nothing can be carried out through it anyway.
        described = dict(described)
        described["armed"] = False
        described["brake_engaged"] = True
        described["detail"] = (
            "The Parking Brake is engaged. The arming window is still open, but "
            "nothing can be carried out while the brake holds."
        )
    else:
        described["brake_engaged"] = False
    return {"channel": described}


@router.post("/channel/disarm")
async def disarm_channel(request: Request) -> Any:
    """Close this tenant's channel immediately. Idempotent.

    Deliberately needs no device credential and is never refused by the brake:
    disarming is a strictly-tightening safety act, and a control that removes
    authority must not be reachable only by whoever still holds it.
    """
    window = arming.disarm(tenant_id=_tenant(request))
    return {
        "disarmed": window is not None,
        "detail": (
            "The Windows action channel is disarmed."
            if window is not None
            else "The Windows action channel was already disarmed."
        ),
    }


@router.post("", status_code=201)
async def request_action(request: Request) -> Any:
    """Ask for one action on one enrolled device. Records; does not dispatch."""
    from starlette.responses import JSONResponse

    payload: ActionRequestIn = await _validated_body(request, ActionRequestIn)
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

    Parameters are the **redacted** form: text somebody asked to have typed
    appears as a digest and a length, never as itself. Readable while the
    Parking Brake is engaged, because inspection is exactly what a halt must
    not hide.

    Not quite read-only: it also sweeps this tenant's overdue actions, which is
    what purges their cleartext parameters. That sweep is best-effort and off
    the event loop -- it must never turn a readable database into a 503, and it
    must never stall the API on a write lock. See the comment below.
    """
    if limit < 1 or limit > 100:
        raise HTTPException(400, "limit must be between 1 and 100")
    if offset < 0:
        raise HTTPException(400, "offset must be non-negative")

    from bartholomew.actuation import store

    kernel = _kernel_or_503()
    db_path = getattr(getattr(kernel, "mem", None), "db_path", None) or resolve_db_path()
    tenant = _tenant(request)
    # Housekeeping on the path a person actually visits, as well as on the
    # request path -- the sweep is what purges the cleartext parameters of an
    # action that expired unapproved, and a deployment whose device channel is
    # closed (the shipped default) reaches it nowhere else.
    #
    # **Off the event loop, and best-effort.** It is a synchronous write with a
    # five-second busy timeout: run inline it stalled the whole API for five
    # seconds whenever another writer held the lock, and run inside the
    # response's `try` it then answered 503 -- on the one endpoint whose
    # contract is that inspection is what a halt must not hide, for a database
    # whose read would have succeeded instantly. The sibling call in the seam
    # was already wrapped for exactly this reason; the reasoning belongs here
    # too.
    try:
        from bartholomew.kernel.blocking_executor import run_off_loop

        await run_off_loop(store.expire_overdue, db_path, tenant_id=tenant)
    except Exception:
        logger.warning(
            "Could not sweep overdue actions for tenant %s while listing; cleartext "
            "parameters may be retained past their expiry until the next sweep",
            tenant,
            exc_info=True,
        )

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
    """One action, its state, its results -- and what it would actually do.

    **This is the approval surface, and it returns the canonical parameters.**
    The list endpoint returns the redacted view, and every durable record --
    the stored row's audit column, the Reflection, the evidence row -- keeps
    only a digest and a length. But a person cannot approve text they have not
    read, and an approval bound to a digest the approver never saw expanded is
    an approval in name only. For `windows.type_text` that is the whole
    decision.

    So the text is disclosed here, transiently, to one authenticated request
    holding `action:read`, and nowhere else. That is the trade this design
    makes deliberately: an authorised reader sees it, and no second durable
    copy of it is written down.
    """
    from bartholomew.actuation import store

    kernel = _kernel_or_503()
    db_path = getattr(getattr(kernel, "mem", None), "db_path", None) or resolve_db_path()
    tenant = _tenant(request)
    try:
        action = store.get_action(db_path, tenant_id=tenant, action_id=action_id)
        if action is None:
            raise HTTPException(404, "No such action in this tenant.")
        return {
            "action": action.as_dict(include_parameters=True),
            "results": store.results_for(db_path, tenant_id=tenant, action_id=action_id),
            "approval_summary": _approval_summary(action),
        }
    except ActionPersistenceError as e:
        raise HTTPException(503, str(e)) from e


@router.post("/{action_id}/approve")
async def approve_action(action_id: str, request: Request) -> Any:
    """Approve exactly this action, as it stands, for a bounded window.

    The approval binds to the action id, tenant, device, capability, capability
    version and a fingerprint of the canonical parameters. Changing any of them
    -- including re-requesting the same action with one character different --
    produces a different fingerprint and invalidates this approval.

    Approving still does not run anything: it makes one action eligible to be
    leased, and every gate is evaluated again when it is.
    """
    from starlette.responses import JSONResponse

    payload: ApprovalIn = await _validated_body(request, ApprovalIn)
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
async def cancel_action(action_id: str, request: Request) -> Any:
    """Withdraw an action so it can never run.

    Works on a pending, approved *or* leased action. Cancelling a leased action
    cannot reach out and stop a device mid-call -- nothing can -- but it makes
    the action un-leasable and makes any result the device later reports inert,
    so a cancelled action can never acquire a success.
    """
    from starlette.responses import JSONResponse

    payload: CancellationIn = await _validated_body(request, CancellationIn)
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
