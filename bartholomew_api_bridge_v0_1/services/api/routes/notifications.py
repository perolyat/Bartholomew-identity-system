"""
Stage 1, S1.3: Notification settings (quiet hours + mute) control surface.

Exposes bartholomew.skills.notify.NotifySkill's quiet-hours/mute settings
-- a live, already-loaded (`config/skills/notify.yaml`, enabled: true)
Stage 4 starter skill whose quiet-hours gating already existed, just
hardcoded and with no mute concept at all until this stage -- over HTTP.

Every call goes through bartholomew.kernel.runtime_contract.run_skill_
through_runtime_contract() -- the named production entry point every
skill invocation must go through (tests/test_skill_runtime_contract_seam
.py's TestNamedSeamIsTheSoleProductionEntryPath statically asserts no
production caller invokes SkillRegistry.execute_action() directly; an
earlier version of this file did exactly that and was caught by CI).
That seam is a thin wrapper -- Observation/Interpretation/CandidateAction
construction, the parking-brake "skills"-scope check, and the audit trail
all still live inside execute_action() itself -- so this changes nothing
about what actually runs, only which name production code is required to
call it through.

Auth note: same as governance.py -- no authentication on any route in
this API bridge today; ROADMAP.md's Stage 1 section defers that to a
separate future project.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from bartholomew.kernel.runtime_contract import run_skill_through_runtime_contract

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class QuietHoursRequest(BaseModel):
    start: str
    end: str


class MuteRequest(BaseModel):
    until: str | None = None


def _get_kernel():
    from bartholomew_api_bridge_v0_1.services.api.app import _kernel

    if _kernel is None:
        raise HTTPException(503, "Kernel not initialized")
    return _kernel


async def _execute(kernel, action: str, params: dict) -> dict:
    result = await run_skill_through_runtime_contract(
        kernel.skill_registry,
        "notify",
        action,
        params,
    )
    if result.status.value == "error":
        raise HTTPException(400, result.error or "Notification action failed")
    if result.status.value == "permission_denied":
        raise HTTPException(403, result.error or "Permission denied")
    return result.data or {}


@router.get("/settings")
async def get_notification_settings() -> dict:
    kernel = _get_kernel()
    return await _execute(kernel, "get_notification_settings", {})


@router.put("/quiet-hours")
async def set_quiet_hours(body: QuietHoursRequest) -> dict:
    kernel = _get_kernel()
    return await _execute(kernel, "set_quiet_hours", {"start": body.start, "end": body.end})


@router.post("/mute")
async def mute(body: MuteRequest) -> dict:
    kernel = _get_kernel()
    return await _execute(kernel, "mute", {"until": body.until})


@router.post("/unmute")
async def unmute() -> dict:
    kernel = _get_kernel()
    return await _execute(kernel, "unmute", {})
