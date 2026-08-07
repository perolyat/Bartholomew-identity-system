"""
Stage 5, S5.3: per-category Initiative consent + mute -- API surface.

Exposes bartholomew.kernel.initiative_store.InitiativeStore's
initiative_consent table (default-off `allowed`, functional `muted`) over
HTTP. See docs/S5_3_DEFAULT_OFF_CONSENT_AND_MUTE_DESIGN.md Sec 5/6.

Deliberately NOT routed through run_initiative_through_runtime_contract():
per the design doc's Sec 5 reasoning, granting/revoking a category's
consent is not any single initiative's own lifecycle transition -- it has
no initiative_id, no Observation/Interpretation/CandidateAction of its
own, and predates any initiative in that category ever existing. It is
the same shape as GovernanceStore.engage()/disengage() (Parking Brake
state), which routes/governance.py also calls directly rather than
through a seam -- this route mirrors that precedent, not awaiting_
response.py's/notifications.py's skill/seam-routed writes.

Not `/api/consent` -- that prefix is already S1.2's unrelated
pending_sensitive_writes memory-consent inbox (routes/consent.py).

Auth note: same as every other route in this API bridge -- no
authentication today; ROADMAP.md's Stage 1 section defers that to a
separate future project.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from bartholomew.kernel.blocking_executor import run_off_loop
from bartholomew.kernel.initiative_store import VALID_CATEGORIES, InvalidTransitionError

router = APIRouter(prefix="/api/initiatives/consent", tags=["initiatives"])


class ConsentUpdateRequest(BaseModel):
    allowed: bool | None = None
    muted: bool | None = None
    actor: str = "user"


def _get_kernel():
    from bartholomew_api_bridge_v0_1.services.api.app import _kernel

    if _kernel is None:
        raise HTTPException(503, "Kernel not initialized")
    return _kernel


@router.get("")
async def list_consent() -> dict:
    kernel = _get_kernel()
    rows = await run_off_loop(
        kernel.initiative_store.list_category_consent,
        executor=kernel.blocking_executor,
    )
    return {"categories": rows, "count": len(rows)}


@router.get("/{category}")
async def get_consent(category: str) -> dict:
    if category not in VALID_CATEGORIES:
        raise HTTPException(
            400, f"Unknown category {category!r}. Valid: {sorted(VALID_CATEGORIES)}"
        )

    kernel = _get_kernel()
    return await run_off_loop(
        kernel.initiative_store.get_category_consent,
        category,
        executor=kernel.blocking_executor,
    )


@router.put("/{category}")
async def set_consent(category: str, body: ConsentUpdateRequest) -> dict:
    if category not in VALID_CATEGORIES:
        raise HTTPException(
            400, f"Unknown category {category!r}. Valid: {sorted(VALID_CATEGORIES)}"
        )
    if body.allowed is None and body.muted is None:
        raise HTTPException(400, "At least one of allowed/muted must be provided")

    kernel = _get_kernel()
    try:
        return await run_off_loop(
            kernel.initiative_store.set_category_consent,
            category,
            allowed=body.allowed,
            muted=body.muted,
            actor=body.actor,
            executor=kernel.blocking_executor,
        )
    except InvalidTransitionError as e:
        # Defensive -- category is already validated above, but the store
        # method re-validates independently (it's the sole intended writer
        # of this table, not just this route).
        raise HTTPException(400, str(e)) from e
