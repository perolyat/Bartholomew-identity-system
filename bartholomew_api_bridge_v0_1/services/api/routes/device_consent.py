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

router = APIRouter(prefix="/api/device-consent", tags=["device-consent"])


def _consent_tenant(request: Request) -> str | None:
    """Whose asks this person may see and answer.

    A verified principal, else the process's runtime binding -- the same two
    platform-owned sources `device_action_auth.resolved_tenant_id` reads.
    Where neither exists (the single-account loopback deployment, unbound)
    the answer is None: no filter, because there is exactly one tenant and an
    ask records the enrolment account's id, which the `local` sentinel would
    never match. Never the body, never a header.
    """
    principal = getattr(getattr(request, "state", None), "principal", None)
    if principal is not None:
        user_id = getattr(principal, "user_id", None)
        if user_id:
            return str(user_id)
    from bartholomew.platform.runtime_registry import bound_runtime_user_id

    return bound_runtime_user_id() or None


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
    tenant = _consent_tenant(request)
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
    tenant = _consent_tenant(request)
    db_path = resolve_db_path()

    # A person answers their own asks: the tenant is the platform's, never
    # the body's, and a mismatch reads as "unknown". Unbound and without a
    # principal there is one tenant, and no filter.
    outcome = await asyncio.to_thread(
        device_consent.answer,
        db_path,
        request_id,
        nonce=payload.nonce,
        approve=payload.approve,
        decided_by=tenant or "local",
        note=payload.note,
        tenant_id=tenant,
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
