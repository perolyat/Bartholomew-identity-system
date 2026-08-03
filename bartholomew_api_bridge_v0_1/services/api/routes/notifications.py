"""
Stage 1, S1.3: Notification settings (quiet hours + mute) control surface.

Exposes bartholomew.skills.notify.NotifySkill's quiet-hours/mute settings
-- a live, already-loaded (`config/skills/notify.yaml`, enabled: true)
Stage 4 starter skill whose quiet-hours gating already existed, just
hardcoded and with no mute concept at all until this stage -- over HTTP.

Every call goes through kernel.skill_registry.execute_action(), the same
single, already-governed choke-point every skill execution flows through
(parking-brake "skills"-scope check, audit trail, runtime-contract shape;
see SkillRegistry.execute_action()'s own docstring) -- a bare `await`,
exactly matching bartholomew.kernel.runtime_contract.run_skill_through_
runtime_contract()'s one existing production call site, not a new pattern.

Auth note: same as governance.py -- no authentication on any route in
this API bridge today; ROADMAP.md's Stage 1 section defers that to a
separate future project.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

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
    result = await kernel.skill_registry.execute_action("notify", action, params)
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
