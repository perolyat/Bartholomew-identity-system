"""
Stage 1, S1.6: host-device onboarding guidance -- API surface.

Exposes bartholomew_api_bridge_v0_1.services.api.onboarding_content's static
deployment-target guidance over HTTP. See
docs/S1_6_HOST_DEVICE_ONBOARDING_DESIGN.md (approved 2026-08-05) for the
design this implements.

Unlike every other Stage 1 route, this one has no governance implication at
all: it is equivalent to serving a static asset, not a capability, so there
is no Parking Brake check, no Identity Policy check, and (per this bridge's
existing no-auth posture -- INTERFACES.md: "local/dev surface, no auth") no
kernel dependency either -- this route works even before/without a running
KernelDaemon, unlike every route that reads `_kernel`.
"""

from __future__ import annotations

from fastapi import APIRouter

from bartholomew_api_bridge_v0_1.services.api.onboarding_content import (
    CHOOSING_GUIDE,
    DEPLOYMENT_TARGETS,
    MIGRATION_NOTE,
)

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


@router.get("/deployment-guide")
async def get_deployment_guide() -> dict:
    return {
        "targets": [t.to_dict() for t in DEPLOYMENT_TARGETS],
        "choosing_guide": [g.to_dict() for g in CHOOSING_GUIDE],
        "migration_note": MIGRATION_NOTE,
    }
