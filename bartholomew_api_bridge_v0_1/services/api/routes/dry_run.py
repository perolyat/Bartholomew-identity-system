"""
Stage 5, S5.5: the global dry-run switch control surface + provenance view.

Mirrors routes/governance.py's Parking Brake shape exactly --
bartholomew.orchestrator.safety.governance_store.GovernanceStore's new
dry_run_state/dry_run_audit persistence (S5.5, distinct from
parking_brake_state -- see GovernanceStore.DryRunSwitchState's docstring)
over HTTP, plus a read-only view over dry_run_results
(bartholomew.kernel.dry_run) so a caller can literally see "what would
Bartholomew have done." Purely additive: no new governance semantics
beyond what docs/S5_5_DRY_RUN_MODE_DESIGN.md already specifies.

Auth note: same as governance.py -- no authentication on any route in
this API bridge today; ROADMAP.md's Stage 1 section defers that to a
separate future project. `actor` is caller-declared, not verified.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from bartholomew.kernel.blocking_executor import run_off_loop
from bartholomew.kernel.dry_run import list_dry_run_results
from bartholomew.orchestrator.safety.governance_store import (
    StaleGovernanceWriteError,
    WriteFenceClosedError,
)

router = APIRouter(prefix="/api/dry-run", tags=["dry-run"])

VALID_SCOPES = frozenset({"global", "initiative", "skills"})


class DryRunEngageRequest(BaseModel):
    scopes: list[str] = []
    reason: str | None = None
    actor: str = "user"


class DryRunDisengageRequest(BaseModel):
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


@router.get("/status")
async def get_dry_run_status() -> dict:
    """
    Current global dry-run switch state. Refreshes off-loop before
    responding, same reasoning as GET /api/governance/brake -- another
    GovernanceStore instance (a CLI, another process) could have written
    since this daemon's shared instance last refreshed.
    """
    kernel = _get_kernel()
    state = await run_off_loop(
        kernel.governance_store.refresh_dry_run,
        executor=kernel.blocking_executor,
    )
    return _state_dict(state)


@router.post("/engage")
async def engage_dry_run(body: DryRunEngageRequest) -> dict:
    """
    Engage (force dry-run on) for the given scopes. Tightening is never
    refused regardless of staleness -- mirrors POST /api/governance/
    brake/engage's exact semantics against the separate dry-run switch.
    """
    kernel = _get_kernel()
    _validate_scopes(body.scopes)

    try:
        state = await run_off_loop(
            kernel.governance_store.engage_dry_run,
            *body.scopes,
            reason=body.reason,
            actor=body.actor,
            executor=kernel.blocking_executor,
        )
    except WriteFenceClosedError as e:
        raise HTTPException(503, str(e)) from e

    return _state_dict(state)


@router.post("/disengage")
async def disengage_dry_run(body: DryRunDisengageRequest) -> dict:
    """
    Disengage (allow real execution again). Refused with 409 if
    expected_revision (or this process's own cached revision, when
    omitted) is stale -- the caller should GET /api/dry-run/status again
    and retry. Mirrors POST /api/governance/brake/disengage exactly.
    """
    kernel = _get_kernel()

    try:
        state = await run_off_loop(
            kernel.governance_store.disengage_dry_run,
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


@router.get("/results")
async def get_dry_run_results(
    surface: str | None = None,
    target: str | None = None,
    limit: int = 50,
) -> dict:
    """
    Recent DryRunResult records, newest first -- "what would Bartholomew
    have done." Read-only, backed entirely by dry_run_results
    (bartholomew.kernel.dry_run), never joined against or merged with any
    real audit/initiative table (S5.5 design doc Sec 8).
    """
    if limit < 1 or limit > 100:
        raise HTTPException(400, "limit must be between 1 and 100")

    kernel = _get_kernel()
    results = await run_off_loop(
        list_dry_run_results,
        kernel.mem.db_path,
        surface=surface,
        target=target,
        limit=limit,
        executor=kernel.blocking_executor,
    )
    return {"results": results, "count": len(results)}
