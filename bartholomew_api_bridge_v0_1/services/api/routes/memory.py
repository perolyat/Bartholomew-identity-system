"""
Memory Agency (2026-08): the user can see, understand and control what
Bartholomew remembers about them.

Before this, the API bridge had no memory-facing routes at all. Personal
facts were captured through the governed write path and could be recalled in
conversation, but there was no way for the person the memories are *about* to
enumerate them, correct one that was wrong, or delete one they did not want
kept. The only memory surface in the UI was the pending-consent inbox --
which shows what has *not* been stored.

Authority
---------
There is exactly one memory authority and this is not it. Every call here is
a direct `await kernel.mem.<method>()`, matching how the rest of this API
bridge already calls `MemoryStore`. Nothing in this module opens a database
connection, evaluates a rule, or decides what may be stored:

* listing/reading  -> `MemoryStore.list_memories()` / `.get_memory()`
* correcting       -> `MemoryStore.correct_memory()`, itself a thin wrapper
                      over `upsert_memory()`, the single governed write path
* forgetting       -> `MemoryStore.forget_memory()`

Consent, retention, redaction, encryption, privacy classification and
provenance are all applied by that authority exactly as they are for every
other caller. In particular a correction is *not* a privileged UPDATE: it
re-enters `upsert_memory()` and is therefore subject to the same gates the
original write faced. If the corrected value trips `never_store` it is
refused; if it trips `ask_before_store` it is queued into the existing
pending-consent inbox and **not stored**. Both cases return `stored: false`
and this module reports that distinction to the client rather than
flattening it into a success -- claiming an edit was applied when it is
sitting in a consent queue would be exactly the class of untruthful response
Test #1 was set up to catch.

Parking Brake
-------------
Listing and reading are allowed while the brake is engaged; correcting and
forgetting are refused with 503. "Inspect, but do not mutate" -- the same
semantics `routes/consent.py` already documents. As there, the refusal is
raised by `MemoryStore`, not decided here: enforcement sits at the execution
boundary so bypassing this API cannot bypass the halt. These handlers only
translate `ParkingBrakeEngagedError` into an honest status code.

Deletion safety
---------------
Deletion is permanent and this schema has no soft-delete tier to route to, so
`DELETE` requires an explicit `confirm=true`. Nothing here infers a deletion
from conversational text; the only way to reach `forget_memory()` is a
deliberate, confirmed request naming an exact record.

Auth note: same as every other route in this API bridge -- no authentication
today; ROADMAP.md's Stage 1 section defers that to a separate future project.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field

from bartholomew.orchestrator.safety.governance_store import ParkingBrakeEngagedError

router = APIRouter(prefix="/api/memory", tags=["memory"])


class MemoryCorrection(BaseModel):
    """A user-supplied replacement value for one stored memory."""

    value: str = Field(min_length=1)


def _get_kernel():
    from bartholomew_api_bridge_v0_1.services.api.app import _kernel

    if _kernel is None:
        raise HTTPException(503, "Kernel not initialized")
    return _kernel


@router.get("")
async def list_memories(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    kind: str | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    """
    Enumerate stored memories, newest first, with the governance metadata
    needed to present each one honestly (category, privacy class, recall
    policy, and provenance where an explicit consent decision was recorded).
    """
    kernel = _get_kernel()
    return await kernel.mem.list_memories(
        limit=limit,
        offset=offset,
        kind=kind,
        search=search,
    )


@router.get("/kinds")
async def list_kinds() -> dict[str, Any]:
    """Distinct memory kinds and their counts, for a filter control."""
    kernel = _get_kernel()
    return {"kinds": await kernel.mem.list_memory_kinds()}


@router.get("/export")
async def export_memories() -> Response:
    """
    Download every stored memory as JSON.

    Deliberately small in scope: it serialises exactly what `list_memories()`
    already returns, from data that already exists, through the same
    authority. It is not a portability subsystem -- there is no schema
    contract, no import path, and no cross-system format here, and building
    one is a separate piece of work. What it does give the user today is a
    truthful, complete copy of their own record that leaves the machine only
    when they ask for it.

    `truncated` is set when there is more than one export page's worth, so
    the file can never quietly claim to be complete when it is not.
    """
    kernel = _get_kernel()
    page = await kernel.mem.list_memories(limit=500, offset=0)
    payload = {
        "exported_at": datetime.now().astimezone().isoformat(),
        "total_stored": page["total"],
        "exported_count": len(page["entries"]),
        "truncated": page["total"] > len(page["entries"]),
        "memories": page["entries"],
    }
    return Response(
        content=json.dumps(payload, indent=2, default=str),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="bartholomew-memories.json"'},
    )


@router.get("/{kind}/{key}")
async def get_memory(kind: str, key: str) -> dict[str, Any]:
    """Read one memory by its exact (kind, key) identity."""
    kernel = _get_kernel()
    entry = await kernel.mem.get_memory(kind, key)
    if entry is None:
        raise HTTPException(404, f"no memory {kind}/{key}")
    return entry


@router.put("/{kind}/{key}")
async def correct_memory(kind: str, key: str, body: MemoryCorrection) -> dict[str, Any]:
    """
    Replace a memory's value.

    Goes through the governed write path, so the response distinguishes three
    real outcomes rather than reporting a blanket success:

    * `stored: true`  -- the correction was applied.
    * `stored: false` with `queued_for_consent: true` -- governance requires
      consent for the new value; it is waiting in the pending-consent inbox
      and the old value is still what Bartholomew holds.
    * `stored: false` without it -- governance refused the new value outright.
    """
    kernel = _get_kernel()
    try:
        result = await kernel.mem.correct_memory(kind, key, body.value)
    except ParkingBrakeEngagedError as e:
        raise HTTPException(503, str(e)) from e
    except KeyError as e:
        raise HTTPException(404, f"no memory {kind}/{key}") from e

    if result.stored:
        return {
            "ok": True,
            "stored": True,
            "memory_id": result.memory_id,
            "detail": "Memory updated.",
        }

    # Not stored. Which of the two governed refusals happened is decided by
    # the authority, not guessed here -- a queued correction is recoverable
    # and the user should be sent to the inbox; a refused one is not, and
    # saying "queued" would be a lie.
    queued = result.queued_for_consent
    return {
        "ok": False,
        "stored": False,
        "queued_for_consent": queued,
        "detail": (
            "This change needs your consent before it can be stored. It is "
            "waiting in Pending Memory Consent; the previous value is unchanged."
            if queued
            else "Governance rules refused this value, so the memory was not changed."
        ),
    }


@router.delete("/{kind}/{key}")
async def forget_memory(
    kind: str,
    key: str,
    confirm: bool = Query(False, description="Must be true; deletion is permanent."),
) -> dict[str, Any]:
    """
    Delete a memory permanently.

    `confirm=true` is required. There is no undo and no soft-delete tier in
    this schema, so the destructive step is made explicit at the boundary
    rather than being reachable by an accidental or inferred call.
    """
    if not confirm:
        raise HTTPException(
            400,
            "Deleting a memory is permanent and cannot be undone. "
            "Repeat this request with confirm=true to proceed.",
        )

    kernel = _get_kernel()
    try:
        deleted = await kernel.mem.forget_memory(kind, key)
    except ParkingBrakeEngagedError as e:
        raise HTTPException(503, str(e)) from e

    if not deleted:
        raise HTTPException(404, f"no memory {kind}/{key}")
    return {"ok": True, "forgotten": True, "kind": kind, "key": key}
