"""The operator surface's own routes: the overview, and giving Bartholomew a task.

**Wave 3 package W03-E.** Everything else the operator console needs already
had a route -- actions, approvals, the channel, the brake, device consent, the
learning control centre -- so this module deliberately adds only the two things
that had none:

* `GET /api/operator/overview` -- one read that answers "is he halted, can he
  act, what is waiting for me". The console could compose it from five calls
  and does when this route is absent; having it in one place is what makes a
  web surface possible later without re-deriving the composition.
* `POST /api/operator/tasks`, `GET /api/operator/tasks/{task_id}` and
  `POST /api/operator/tasks/{task_id}/advance` -- the executive (W03-B) had no
  production caller at all. The GET is a pure read of the stored plan; the two
  POSTs are the executive's two proposing passes and are classified as requests. Its handoff Sec.5.2 records
  that wiring it is W03-E's and W03-F's step and that it is one call. This is
  that call.

Three things this module is not
-------------------------------
1. **Not an authority.** There is no governance here: no brake evaluation, no
   approval, no capability selection, no lease. Every route delegates to the
   authority that owns the question and reports what it said. The task routes
   in particular reach the operating system through nothing at all -- they call
   the executive seam, which reaches it only through the one action envelope.
2. **Not a second way to approve.** Nothing here grants an action approval or
   accepts a lesson. Both remain where they are, behind their own capabilities
   (`action:approve`, `learning:approve`), and the console posts to those.
3. **Not registered here.** `app.py` router registration is W03-F's, per the
   manifest. Until W03-F registers this router the console reports its absence
   honestly rather than pretending the executive is unavailable for some other
   reason.

The executive package is resolved **by name at call time**, exactly as W03-B
resolves W03-A's read-back: on a tree without `bartholomew/executive/` these
routes answer 503 with the reason, and on the integrated head they work with no
code change. That is what lets W03-E ship its own bounded diff without
absorbing another builder's implementation.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..db import resolve_db_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/operator", tags=["operator"])

#: What the console is told when this build has no executive package. Its own
#: constant so the console can recognise the condition, and so "the executive
#: is not in this build" can never be confused with "the executive refused".
EXECUTIVE_ABSENT = (
    "This build does not carry the executive package, so Bartholomew cannot be "
    "given a task. Nothing was planned and nothing was proposed."
)


def _kernel_or_503() -> Any:
    from bartholomew_api_bridge_v0_1.services.api.app import _kernel

    if _kernel is None:
        raise HTTPException(503, "Kernel not initialized")
    return _kernel


def _db_path(kernel: Any) -> str:
    return getattr(getattr(kernel, "mem", None), "db_path", None) or resolve_db_path()


def _tenant(request: Request) -> str:
    """Whose Bartholomew this is. From the platform's authority, never a body."""
    from bartholomew_api_bridge_v0_1.services.api import device_action_auth

    return device_action_auth.resolved_tenant_id(request)


def _requesting_identity(request: Request) -> str:
    """Who asked -- resolved exactly as `routes/actions.py` resolves it, so the
    same person is recorded under one name whether they proposed an action
    directly or through a task."""
    from bartholomew_api_bridge_v0_1.services.api.routes.actions import _requesting_identity as who

    return who(request)


def _executive_seam() -> Any:
    """W03-B's seam, resolved by name, or `None` on a tree without it.

    Resolved rather than imported so this module carries no import edge to a
    package that may not be present. A missing package is reported; it is never
    worked around.
    """
    import importlib

    try:
        return importlib.import_module("bartholomew.executive.seam")
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# The overview
# ---------------------------------------------------------------------------


async def _brake_section(kernel: Any) -> dict[str, Any]:
    """The one `GovernanceStore` every other gate reads. Fail-closed."""
    from bartholomew.kernel.blocking_executor import run_off_loop

    try:
        state = await run_off_loop(
            kernel.governance_store.refresh,
            executor=getattr(kernel, "blocking_executor", None),
        )
    except Exception:
        logger.exception("Parking brake unreadable while building the operator overview")
        # Unreadable is reported as unreadable. A console that rendered this as
        # "clear" would be showing a halt that may be in force as absent.
        return {"readable": False, "engaged": None, "scopes": [], "detail": "brake unreadable"}
    return {
        "readable": True,
        "engaged": bool(state.engaged),
        "scopes": sorted(state.scopes),
        "revision": state.revision,
    }


def _channel_section(tenant: str) -> dict[str, Any]:
    from bartholomew.actuation import arming

    try:
        window = arming.current(tenant_id=tenant)
    except Exception:
        logger.exception("Arming window unreadable while building the operator overview")
        return {"readable": False, "armed": None}
    if window is None:
        return {"readable": True, "armed": False}
    return {"readable": True, "armed": True, **window.describe()}


def _actions_section(db_path: str, tenant: str) -> dict[str, Any]:
    from bartholomew.actuation import store

    try:
        rows = store.recent_actions(db_path, tenant_id=tenant, limit=100)
    except Exception:
        logger.exception("Actions unreadable while building the operator overview")
        return {"readable": False, "pending": []}
    pending = [
        {
            "action_id": row.get("action_id"),
            "capability": row.get("capability"),
            "device_id": row.get("device_id"),
            "state": row.get("state"),
        }
        for row in rows
        if (row.get("state") or "") == "pending_approval"
    ]
    return {"readable": True, "pending": pending, "recent_count": len(rows)}


def _consent_section(db_path: str, request: Request) -> dict[str, Any]:
    from bartholomew.multimodal import device_consent
    from bartholomew_api_bridge_v0_1.services.api.routes.device_consent import _consent_tenant

    try:
        # Scoped exactly as `GET /api/device-consent/pending` scopes it, and
        # `include_nonce=False`: the nonce is what proves an answer came from the
        # person at the machine, and a route that returned it would hand that
        # proof to anything that could reach the route.
        asks = device_consent.list_pending(
            db_path,
            tenant_id=_consent_tenant(request),
            include_nonce=False,
        )
    except Exception:
        logger.exception("Device consent asks unreadable while building the operator overview")
        return {"readable": False, "waiting": []}
    return {"readable": True, "waiting": asks}


@router.get("/overview")
async def overview(request: Request) -> dict[str, Any]:
    """Everything waiting for a person, in one read.

    Read-only, and deliberately readable while the Parking Brake is engaged:
    inspection is exactly what a halt must not hide. Every section reports
    whether it could be read, so a section that failed says so rather than
    rendering as "nothing is waiting" -- which is a different claim.
    """
    kernel = _kernel_or_503()
    db_path = _db_path(kernel)
    tenant = _tenant(request)
    return {
        "tenant_id": tenant,
        "parking_brake": await _brake_section(kernel),
        "action_channel": _channel_section(tenant),
        "actions": _actions_section(db_path, tenant),
        "device_consent": _consent_section(db_path, request),
        "executive_available": _executive_seam() is not None,
    }


# ---------------------------------------------------------------------------
# Tasks -- the executive's one production caller
# ---------------------------------------------------------------------------


class TaskIn(BaseModel):
    """One instruction, in ordinary words, for one enrolled machine.

    Note what is absent and cannot be added by a caller: `tenant_id`,
    `requested_by`, any capability, any parameter, and any notion of approval.
    The capability is chosen by the executive from a closed vocabulary and
    admitted by the envelope; the tenant and the requester come from the
    platform's own authority in the handler.
    """

    instruction: str = Field(..., min_length=1, max_length=1000)
    device_id: str = Field(..., min_length=1, max_length=128)


#: Outcomes of a planning pass that carry a plan for a person to act on.
_PLANNED_OUTCOMES = frozenset({"proposed", "clarification_requested"})


def _step_dict(step: Any) -> dict[str, Any]:
    """One plan step as the console reads it.

    `action_id` is the load-bearing field: a step whose `action_id` is null has,
    by construction, never been near the action envelope and therefore never
    near a machine. Parameters are deliberately absent: what an action would do
    is disclosed on the approval surface (`GET /api/actions/{id}`) and nowhere
    else.
    """
    verification = getattr(step, "verification", None)
    return {
        "ordinal": getattr(step, "index", None),
        "capability": getattr(step, "capability", None),
        "described_as": getattr(step, "described_as", None),
        "status": getattr(getattr(step, "status", None), "value", None),
        "action_id": getattr(step, "action_id", None),
        "refusal_category": getattr(step, "refusal_category", None),
        "refusal_reason": getattr(step, "refusal_reason", None),
        "verification_verdict": getattr(verification, "verdict", None) if verification else None,
        "verification_detail": getattr(verification, "detail", None) if verification else None,
    }


def _plan_payload(
    plan: Any,
    *,
    outcome: str | None,
    reason: str | None,
    explanation: str,
    governance_allowed: Any,
    proposed_action_ids: list[str],
    provenance_degraded: bool,
) -> dict[str, Any]:
    """A plan as the console reads it. Nothing is upgraded.

    The outcome and the explanation are the seam's own words. A route that
    rewrote either would be the place a `unknown` quietly became a success.
    """
    steps = [_step_dict(step) for step in (getattr(plan, "steps", None) or [])]
    payload: dict[str, Any] = {
        "task": {
            "task_id": getattr(plan, "task_id", None),
            "instruction": getattr(plan, "instruction", None),
            "status": getattr(getattr(plan, "status", None), "value", None),
        },
        "outcome": outcome,
        "reason": reason,
        "governance_allowed": governance_allowed,
        "steps": steps,
        "explanation": explanation or "",
        "proposed_action_ids": list(proposed_action_ids),
        "provenance_degraded": bool(provenance_degraded),
    }
    question = getattr(plan, "clarification", None)
    if question:
        payload["question"] = question
    notes = list(getattr(plan, "notes", None) or [])
    cautions = list(getattr(plan, "cautions", None) or [])
    if notes:
        payload["notes"] = notes
    if cautions:
        payload["cautions"] = cautions
    # A step the executive planned but could not propose is a *named stop*:
    # the boundary is stated, not omitted, so a scenario that ran into the edge
    # of what this build can do reads as an edge rather than as a silence.
    payload["named_stops"] = [
        f"{step['capability'] or 'step'}: {step['refusal_reason'] or step['refusal_category']}"
        for step in steps
        if step.get("refusal_reason") or step.get("refusal_category")
    ]
    return payload


def _result_dict(result: Any) -> dict[str, Any]:
    """An `ExecutiveTaskResult` (W03-B) as the console reads it."""
    return _plan_payload(
        getattr(result, "plan", None),
        outcome=getattr(result, "outcome", None),
        reason=getattr(result, "reason", None),
        explanation=getattr(result, "explanation", "") or "",
        governance_allowed=getattr(result, "governance_allowed", None),
        proposed_action_ids=list(getattr(result, "proposed_action_ids", None) or []),
        provenance_degraded=bool(getattr(result, "provenance_degraded", False)),
    )


def _refuse_if_nothing_was_planned(result: Any) -> None:
    """A pass that produced no plan is an answer, not a task, and its status
    code must say so. A halt is a governed refusal that will lift (409); a
    refusal or an error is a request that cannot be honoured (422). Both carry
    the seam's own explanation as the detail. A `201 Created` for a task that
    was never created would be the console's "Understood as: None"."""
    if getattr(result, "plan", None) is not None:
        return
    outcome = getattr(result, "outcome", None)
    reason = getattr(result, "explanation", None) or getattr(result, "reason", None) or ""
    if outcome == "parking_brake_denied":
        raise HTTPException(409, reason or "The Parking Brake is engaged.")
    raise HTTPException(422, reason or "Nothing was planned.")


@router.post("/tasks", status_code=201)
async def create_task(body: TaskIn, request: Request) -> dict[str, Any]:
    """Give Bartholomew a task. Nothing runs.

    He interprets it, selects a capability the device actually declares, and
    proposes the first Windows step through the one action envelope, where it
    stops at `pending_approval`. An ambiguous instruction becomes a question
    and no proposal at all; an instruction outside the capability vocabulary is
    declined truthfully rather than mapped onto the nearest thing.

    This route grants nothing. The executive never mints an approval, and the
    envelope refuses dispatch without one regardless of what any allowlist
    says. `201` is returned only when a plan now exists; a halt is `409` and a
    refusal `422`, each with the executive's own explanation.
    """
    seam = _executive_seam()
    if seam is None:
        raise HTTPException(503, EXECUTIVE_ABSENT)
    kernel = _kernel_or_503()

    result = await seam.run_executive_task_through_runtime_contract(
        kernel,
        tenant_id=_tenant(request),
        device_id=body.device_id,
        requested_by=_requesting_identity(request),
        instruction=body.instruction,
    )
    _refuse_if_nothing_was_planned(result)
    return _result_dict(result)


@router.get("/tasks/{task_id}")
async def read_task(task_id: str, request: Request) -> dict[str, Any]:
    """Where a task stands, as recorded. A read, and nothing but a read.

    Loads the stored plan and renders the executive's own account of it. It
    does not observe the machine, verify a step, or propose the next one --
    that is `POST /tasks/{task_id}/advance`, a request-class act classified as
    one. Readable while the Parking Brake is engaged, because inspection is
    exactly what a halt must not hide.
    """
    seam = _executive_seam()
    if seam is None:
        raise HTTPException(503, EXECUTIVE_ABSENT)
    import importlib

    executive_store = importlib.import_module("bartholomew.executive.store")
    explanation_mod = importlib.import_module("bartholomew.executive.explanation")
    from bartholomew.kernel.blocking_executor import run_off_loop

    kernel = _kernel_or_503()
    db_path = _db_path(kernel)
    tenant = _tenant(request)
    await run_off_loop(
        executive_store.ensure_schema,
        db_path,
        executor=getattr(kernel, "blocking_executor", None),
    )
    plan = await run_off_loop(
        executive_store.load_plan,
        db_path,
        tenant_id=tenant,
        task_id=task_id,
        executor=getattr(kernel, "blocking_executor", None),
    )
    if plan is None:
        raise HTTPException(404, f"No executive task {task_id!r} in this tenant.")
    status = getattr(getattr(plan, "status", None), "value", None)
    return _plan_payload(
        plan,
        outcome=status,
        reason=None,
        explanation=explanation_mod.explain_task(plan),
        governance_allowed=None,
        proposed_action_ids=[
            step.action_id for step in (plan.steps or []) if getattr(step, "action_id", None)
        ],
        provenance_degraded=False,
    )


@router.post("/tasks/{task_id}/advance")
async def advance_task(task_id: str, request: Request) -> dict[str, Any]:
    """Observe what became of the outstanding step, verify it, continue or recover.

    Calls the executive's caller-driven `advance` pass (W03-B, its handoff
    Sec.5.3: nothing in the executive polls). It reads each proposed action's real
    state from the envelope's own table, verifies what it can through W03-A's
    governed read-back, and then either proposes the next step -- which stops at
    `pending_approval` like every other -- raises a question, or stops. It can
    propose; it can never approve, lease or dispatch. Classified as a request
    for exactly that reason.
    """
    seam = _executive_seam()
    if seam is None:
        raise HTTPException(503, EXECUTIVE_ABSENT)
    kernel = _kernel_or_503()
    try:
        result = await seam.advance_executive_task_through_runtime_contract(
            kernel,
            tenant_id=_tenant(request),
            task_id=task_id,
        )
    except Exception as e:  # noqa: BLE001 - reported, never swallowed into a success
        logger.exception("Executive advance failed for task %s", task_id)
        raise HTTPException(503, f"could not advance the task: {e}") from e
    if getattr(result, "plan", None) is None:
        outcome = getattr(result, "outcome", None)
        reason = getattr(result, "explanation", None) or getattr(result, "reason", None) or ""
        if outcome == "parking_brake_denied":
            raise HTTPException(409, reason)
        raise HTTPException(404, reason or f"No executive task {task_id!r} in this tenant.")
    return _result_dict(result)


__all__ = ["router", "EXECUTIVE_ABSENT"]
