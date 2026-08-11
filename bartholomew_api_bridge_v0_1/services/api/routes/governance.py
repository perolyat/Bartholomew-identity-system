"""
Stage 1, S1.1: Parking Brake control surface.
Stage 1, S1.5: Governance audit / provenance view (GET /audit).

Exposes bartholomew.orchestrator.safety.governance_store.GovernanceStore --
the live, tested, fail-closed Parking Brake persistence already wired as
the daemon's shared kernel.governance_store instance (Phase B stage B4)
and already the CLI's write path (Phase B stage B6) -- over HTTP. Purely
additive wiring: no new governance semantics are introduced here.

Auth note (Stage 1 plan review): the API bridge has no authentication on
any route today (chat, nudges, reflections, etc. are all equally open).
ROADMAP.md's Stage 1 section defers real auth to a separate, later
project ("a Stage 1 shell is not itself an authentication project"); per
that decision, these routes are deliberately left consistent with every
other route rather than singled out for one-off protection that would
give false assurance. The `actor` field below is caller-declared, not
verified -- it exists for audit-trail structure (who/what claims to have
made this change), not authorization.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from bartholomew.kernel.blocking_executor import run_off_loop
from bartholomew.orchestrator.safety.governance_store import (
    StaleGovernanceWriteError,
    WriteFenceClosedError,
)

router = APIRouter(prefix="/api/governance", tags=["governance"])

# "training" (S5.2): gates the training-ingestion seam
# (runtime_contract.run_training_through_runtime_contract). Deliberately its
# own scope rather than reusing "skills": executing a tool and changing what
# Bartholomew knows are different powers, and stopping one should not require
# stopping the other. Registration here is load-bearing, not cosmetic -- this
# allowlist rejects unknown scopes, so an unregistered scope would be
# enforceable inside the kernel yet impossible to engage from the API or UI.
VALID_SCOPES = frozenset({"global", "skills", "sight", "voice", "scheduler", "training"})


class BrakeEngageRequest(BaseModel):
    scopes: list[str] = []
    reason: str | None = None
    actor: str = "user"


class BrakeDisengageRequest(BaseModel):
    reason: str | None = None
    expected_revision: int | None = None
    actor: str = "user"


def _get_kernel():
    from bartholomew_api_bridge_v0_1.services.api.app import _kernel

    if _kernel is None:
        raise HTTPException(503, "Kernel not initialized")
    return _kernel


def _state_dict(state) -> dict:
    return {
        "engaged": state.engaged,
        "scopes": sorted(state.scopes),
        "revision": state.revision,
    }


def _validate_scopes(scopes: list[str]) -> None:
    unknown = sorted(set(scopes) - VALID_SCOPES)
    if unknown:
        raise HTTPException(
            400,
            f"Unknown scope(s) {unknown}. Valid scopes: {sorted(VALID_SCOPES)}",
        )


@router.get("/brake")
async def get_brake_status() -> dict:
    """
    Current Parking Brake state.

    Refreshes off-loop before responding rather than returning the daemon
    singleton's cached snapshot: the CLI (`bartholomew brake on/off`) or
    another GovernanceStore instance can write to the same database while
    this daemon is running (Phase B stage B6), and without a refresh here
    this route would keep reporting stale engaged/scopes/revision until an
    unrelated governed operation happened to refresh the shared instance --
    including masking a real external engage as "disengaged" and feeding a
    stale revision into a subsequent disengage() call.
    """
    kernel = _get_kernel()
    state = await run_off_loop(
        kernel.governance_store.refresh,
        executor=kernel.blocking_executor,
    )
    return _state_dict(state)


@router.post("/brake/engage")
async def engage_brake(body: BrakeEngageRequest) -> dict:
    """
    Engage (tighten) the brake. Tightening is never refused by
    GovernanceStore itself regardless of staleness -- the only refusal
    path here is WriteFenceClosedError (shutdown in progress), which by
    the time it could fire has already been preceded by this app's
    admission middleware returning 503 for the whole request (see
    app.py's admission_middleware and daemon.py's stop() sequence: lifecycle_state
    leaves RUNNING before the write fence closes).
    """
    kernel = _get_kernel()
    _validate_scopes(body.scopes)

    try:
        state = await run_off_loop(
            kernel.governance_store.engage,
            *body.scopes,
            reason=body.reason,
            actor=body.actor,
            executor=kernel.blocking_executor,
        )
    except WriteFenceClosedError as e:
        raise HTTPException(503, str(e)) from e

    return _state_dict(state)


@router.post("/brake/disengage")
async def disengage_brake(body: BrakeDisengageRequest) -> dict:
    """
    Disengage (loosen) the brake. Refused with 409 if expected_revision
    (or this process's own cached revision, when omitted) is stale --
    the caller should GET /api/governance/brake again and retry.
    """
    kernel = _get_kernel()

    try:
        state = await run_off_loop(
            kernel.governance_store.disengage,
            reason=body.reason,
            expected_revision=body.expected_revision,
            actor=body.actor,
            executor=kernel.blocking_executor,
        )
    except StaleGovernanceWriteError as e:
        raise HTTPException(409, str(e)) from e
    except WriteFenceClosedError as e:
        raise HTTPException(503, str(e)) from e

    return _state_dict(state)


@router.get("/audit")
async def get_audit_log(limit: int = 50) -> dict:
    """
    Recent governance_audit entries, newest first (Stage 1, S1.5) --
    read-only provenance view over the same audit trail engage()/
    disengage() already write atomically with each state change.
    """
    if limit < 1 or limit > 100:
        raise HTTPException(400, "limit must be between 1 and 100")

    kernel = _get_kernel()
    entries = await run_off_loop(
        kernel.governance_store.list_audit,
        limit=limit,
        executor=kernel.blocking_executor,
    )
    return {"entries": entries, "count": len(entries)}
