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

Auth (corrected in FND-03; the previous note here claimed "no
authentication today" and had been untrue since the platform identity
boundary landed): these routes are NOT special-cased and carry no
route-local authentication of their own -- deliberately, because a second
auth framework beside the authoritative one is how bypasses happen. They
are protected by `bartholomew.platform.http_identity`, installed as the
API bridge's request boundary in `app.py`:

- `bartholomew/platform/route_policy.py` classifies all three endpoints
  (listing included -- reading the inbox is reading the raw pre-redaction
  payload) as requiring `Capability.CONSENT_DECIDE`;
- `bartholomew/platform/exposure.py` decides whether authentication is
  enforced. A non-loopback bind forces both authentication and TLS, and
  refuses to start otherwise, so the loopback-only development mode below
  cannot become an exposed deployment;
- identity comes from a verified session alone -- never a client-supplied
  header -- and `assert_principal_owns_this_process()` refuses a principal
  belonging to another runtime/user.

So: under enforced auth an anonymous caller gets 401, an authenticated
principal without CONSENT_DECIDE gets 403, and a principal from another
runtime gets refused. Under loopback-only development auth is not
enforced, which is the established platform contract for every route here,
not a consent-specific exception.

Storage contract (FND-03): the pending payload is the ORIGINAL,
pre-redaction content and is always encrypted at rest. It is decrypted
only here, on the authorised review path, and is scrubbed from the inbox
once the decision is made -- by approval, by denial, or by the user
forgetting/revoking the identity. Resolved rows keep content-free audit
metadata only.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from bartholomew.kernel.memory_store import ConsentInboxProtectionError
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
    except ConsentInboxProtectionError as e:
        # FND-03: the stored payload cannot be decrypted with this process's
        # key, so there is nothing reviewable to approve. The request stays
        # pending and decidable (denial still clears it); reporting success
        # here would be a lie, and storing the envelope would be worse.
        raise HTTPException(409, str(e)) from e
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
