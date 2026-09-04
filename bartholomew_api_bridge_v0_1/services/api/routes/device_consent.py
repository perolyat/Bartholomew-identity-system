"""Device consent: the surface a person answers a device observation ask on.

Two routes. Neither accepts the device credential: a machine that could
answer the question "may this machine start observing?" would make the
question meaningless, so a request carrying `x-bartholomew-device-credential`
is refused outright rather than authenticated.

Answering requires the ask's nonce. The nonce is written only to the kernel
database and is never returned by any route here, so in the default loopback
deployment -- where HTTP identity is disabled -- the separation between "the
person" and "the companion" is that the person can read the database file on
their own machine and the companion process cannot. The operator CLI
(`bartholomew consent approve <id>`) reads the nonce there and presents it.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from bartholomew.multimodal import device_consent
from bartholomew.platform.device_inbound import DEVICE_CREDENTIAL_HEADER, _header

from ..db import resolve_db_path
from ..device_action_auth import resolved_tenant_id

router = APIRouter(prefix="/api/device-consent", tags=["device-consent"])


class AnswerIn(BaseModel):
    nonce: str = Field(..., min_length=1, max_length=256)
    approve: bool
    note: str | None = Field(default=None, max_length=280)


def _refuse_device_credential(request: Request) -> None:
    if _header(request, DEVICE_CREDENTIAL_HEADER):
        raise HTTPException(
            403,
            "A device credential cannot read or answer device consent; "
            "this surface is for the person, not the machine.",
        )


@router.get("/pending")
async def pending(request: Request) -> Any:
    """Open asks for this tenant. Never includes the nonce."""
    _refuse_device_credential(request)
    tenant = resolved_tenant_id(request)
    db_path = resolve_db_path()
    asks = await asyncio.to_thread(
        device_consent.list_pending,
        db_path,
        tenant_id=tenant,
        include_nonce=False,
    )
    return {"pending": asks, "channel": device_consent.describe()}


@router.post("/{request_id}/answer")
async def answer(request_id: str, request: Request) -> Any:
    """Decide one ask. Requires the nonce; resolves at most one waiting start."""
    _refuse_device_credential(request)
    payload = AnswerIn(**(await request.json() if await request.body() else {}))
    tenant = resolved_tenant_id(request)
    db_path = resolve_db_path()

    # A person answers their own asks: the tenant is the platform's, never
    # the body's, and a mismatch reads as "unknown".
    outcome = await asyncio.to_thread(
        device_consent.answer,
        db_path,
        request_id,
        nonce=payload.nonce,
        approve=payload.approve,
        decided_by=str(tenant),
        note=payload.note,
        tenant_id=str(tenant),
    )
    status = {
        "unknown": 404,
        "already_decided": 409,
        "expired": 409,
        "refused": 403,
    }.get(outcome.outcome, 200)
    if status != 200:
        raise HTTPException(status, outcome.detail)
    return outcome.as_dict()
