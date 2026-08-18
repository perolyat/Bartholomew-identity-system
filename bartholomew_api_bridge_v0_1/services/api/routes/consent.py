"""
Consent-handler fix (2026-08) + S1.2 (2026-08): pending memory writes
awaiting human review, from two independent gates in
MemoryStore.upsert_memory() -- each entry's `reason` field distinguishes
them:

- 'privacy_guard': a keyword-based sensitivity check
  (bartholomew.kernel.memory.privacy_guard.is_sensitive()). It already had
  working handler-based consent plumbing (chat.py registers a real
  terminal prompt for interactive CLI use), but headless callers -- the
  API bridge/daemon -- never registered a handler, so the content was
  silently and permanently discarded.
- 'rule_consent': memory_rules.yaml's ask_before_store category
  (requires_consent=true) -- previously hard-rejected outright with no
  promotion path, despite MemoryRulesEngine.should_store()'s own docstring
  describing one. `privacy_class` is populated for these entries.

Both land in the same pending_sensitive_writes inbox and are exposed here
as one unified, source-agnostic list -- content queued instead of dropped,
reviewable and resolvable via these same three endpoints regardless of
which gate queued it.

Every call is a direct `await kernel.mem.<method>()`, matching how
app.py's existing routes already call MemoryStore (e.g.
`kernel.mem.list_pending_nudges()`) -- MemoryStore isn't behind the
skill-execution runtime-contract seam `routes/notifications.py` had to go
through; it's called directly throughout this API bridge.

Parking Brake (2026-08-18): listing is allowed while the brake is
engaged, approving and denying are refused with 503. "Inspect, but do not
mutate" -- see `COGNITIVE_RUNTIME.md`'s "The kill-switch: `ParkingBrake`"
for the semantics and `DECISIONS.md` for the decision. The refusal is
raised by `MemoryStore`, not by these handlers: enforcement belongs at the
execution boundary so bypassing this API cannot bypass the halt. These
handlers only translate it into an honest status code.

Auth note: same as every other route in this API bridge -- no
authentication today; ROADMAP.md's Stage 1 section defers that to a
separate future project.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from bartholomew.orchestrator.safety.governance_store import ParkingBrakeEngagedError

router = APIRouter(prefix="/api/consent", tags=["consent"])


def _get_kernel():
    from bartholomew_api_bridge_v0_1.services.api.app import _kernel

    if _kernel is None:
        raise HTTPException(503, "Kernel not initialized")
    return _kernel


@router.get("/pending-writes")
async def get_pending_writes(limit: int = 50) -> dict:
    if limit < 1 or limit > 100:
        raise HTTPException(400, "limit must be between 1 and 100")

    kernel = _get_kernel()
    entries = await kernel.mem.list_pending_sensitive_writes(limit=limit)
    return {"entries": entries, "count": len(entries)}


@router.post("/pending-writes/{pending_id}/approve")
async def approve_pending_write(pending_id: int) -> dict:
    kernel = _get_kernel()
    try:
        result = await kernel.mem.approve_pending_sensitive_write(pending_id)
    except ParkingBrakeEngagedError as e:
        raise HTTPException(503, str(e)) from e
    except ValueError as e:
        raise HTTPException(404, str(e)) from e

    return {"ok": True, "stored": result.stored, "memory_id": result.memory_id}


@router.post("/pending-writes/{pending_id}/deny")
async def deny_pending_write(pending_id: int) -> dict:
    kernel = _get_kernel()
    try:
        await kernel.mem.deny_pending_sensitive_write(pending_id)
    except ParkingBrakeEngagedError as e:
        raise HTTPException(503, str(e)) from e
    except ValueError as e:
        raise HTTPException(404, str(e)) from e

    return {"ok": True, "pending_id": pending_id, "denied": True}
