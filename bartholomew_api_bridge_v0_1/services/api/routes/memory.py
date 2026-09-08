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

Revocation tombstones (W03-D)
-----------------------------
Deleting a memory now also records that its `(kind, key)` was withdrawn, so
the next personal-fact capture, lesson consolidation or training write cannot
silently put it back. No content is retained -- the tombstone is an identity,
a timestamp and who asked. Two routes make that visible and reversible rather
than a silent behaviour change: `GET /api/memory/revocations` lists what is
withheld, and `POST /api/memory/{kind}/{key}/reinstate` lifts one. Both are
`MemoryStore` calls like every other route here; this module still decides
nothing.

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


class MemoryReinstatement(BaseModel):
    """A named request to lift one revocation tombstone.

    `reinstated_by` is required and has no default. Lifting a withdrawal is
    never anonymous -- the store itself refuses an empty one -- and a default
    here would quietly supply the name the store is asking for.
    """

    reinstated_by: str = Field(min_length=1)
    reason: str | None = None


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

    Pages through `MemoryStore` to completion rather than returning one page.
    An earlier version fetched a single 500-row page and set `truncated: true`
    -- honest, but it meant "download your memories" silently produced a
    partial file for any store larger than that, which is not an export.

    Bounded work per step: pages are requested one at a time at the store's
    own page ceiling and appended, so peak memory is one page plus the
    accumulated result rather than an unbounded query. `complete` states
    plainly whether the file is the whole record.
    """
    kernel = _get_kernel()

    memories: list[dict[str, Any]] = []
    offset = 0
    page_size = 500
    store_total = 0
    while True:
        page = await kernel.mem.list_memories(limit=page_size, offset=offset)
        store_total = page["store_total"]
        batch = page["entries"]
        memories.extend(batch)
        if not batch or not page["has_more"]:
            break
        offset += len(batch)

    payload = {
        "exported_at": datetime.now().astimezone().isoformat(),
        "total_stored": store_total,
        "exported_count": len(memories),
        "complete": len(memories) >= store_total,
        "memories": memories,
    }
    return Response(
        content=json.dumps(payload, indent=2, default=str),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="bartholomew-memories.json"'},
    )


@router.get("/revocations")
async def list_revocations(limit: int = Query(200, ge=1, le=1000)) -> dict[str, Any]:
    """
    The `(kind, key)` identities currently withheld by a revocation tombstone.

    Declared before `/{kind}/{key}` so the literal path wins over the
    parameterised one; FastAPI matches in declaration order.

    Answers a question the store could not answer at all before W03-D: what
    has Bartholomew been told to stop remembering, and when. Content is not
    part of the answer, because none is kept.
    """
    kernel = _get_kernel()
    return {"revocations": await kernel.mem.list_revocations(limit=limit)}


@router.post("/{kind}/{key}/reinstate")
async def reinstate_memory(kind: str, key: str, body: MemoryReinstatement) -> dict[str, Any]:
    """
    Lift a revocation tombstone so this identity may be stored again.

    Deliberately a separate, named, attributed act rather than a flag on the
    write path: a bypass parameter would be reachable by anything that found
    the refusal inconvenient, while this shows up in the audit trail as
    somebody deciding to allow it again.

    Lifting a tombstone restores no content. A memory that was revoked
    (kept, marked) becomes live again; one that was forgotten (content
    erased) is simply writable once more.
    """
    kernel = _get_kernel()
    try:
        outcome = await kernel.mem.reinstate_memory(
            kind,
            key,
            reinstated_by=body.reinstated_by,
            reason=body.reason,
        )
    except ParkingBrakeEngagedError as e:
        raise HTTPException(503, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    return {
        "ok": True,
        "kind": kind,
        "key": key,
        "tombstone_lifted": outcome.reinstated,
        "record_restored": outcome.was_present,
        "detail": (
            "This memory may be stored again."
            if outcome.reinstated
            else "There was no revocation in force for this memory; nothing changed."
        ),
    }


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
    * `target_changed: true` -- the record was deleted or replaced while the
      correction was in flight; the conditional write did not land, and
      nothing was written or removed.
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

    # Not stored. The reason comes from the write authority itself -- it is
    # not reconstructed here from a side-channel scan of the consent inbox.
    if result.target_changed:
        return {
            "ok": False,
            "stored": False,
            "queued_for_consent": False,
            "target_changed": True,
            "detail": (
                "This memory changed while your correction was being saved, so "
                "nothing was written. Reload to see what is stored now."
            ),
        }

    return {
        "ok": False,
        "stored": False,
        "queued_for_consent": result.queued_for_consent,
        "target_changed": False,
        "detail": (
            "This change needs your consent before it can be stored. It is "
            "waiting in Pending Memory Consent; the previous value is unchanged."
            if result.queued_for_consent
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
    return {
        "ok": True,
        "forgotten": True,
        "kind": kind,
        "key": key,
        # W03-D. Reported rather than silent: "deleted" and "deleted, and
        # Bartholomew will not write this down again on its own" are
        # different promises, and only the second one is now true.
        "tombstoned": True,
        "detail": (
            "Deleted. Bartholomew will not re-learn this from conversation, "
            "training or a lesson unless you reinstate it."
        ),
    }
