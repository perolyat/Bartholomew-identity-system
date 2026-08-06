"""
Stage 1, S1.4: the `awaiting_response` obligation queue -- API surface.

Exposes bartholomew.kernel.awaiting_response_store.AwaitingResponseStore
over HTTP. Every mutation (open/resolve) is routed through
bartholomew.kernel.runtime_contract.run_awaiting_response_through_runtime_contract()
-- docs/S1_4_AWAITING_RESPONSE_DESIGN.md's non-negotiable invariant that no
route writes the store directly. Reads (list/audit) go straight to the
store off-loop, matching governance.py's own read routes -- read paths
aren't governed actions.

POST /api/awaiting-response (open) is a manual-entry/dev-testing endpoint,
mirroring /api/reflection/run's "manually trigger... for testing"
precedent: real automatic creation from a live chat/scheduler surface
recognising an obligation (deciding *when* a chat turn implies one) is
further, separate integration work the design doc's Sec 8 Q3 scopes out of
S1.4 -- this at least makes the governed creation path reachable now.

Auth note: same as every other route in this API bridge -- no
authentication today; ROADMAP.md's Stage 1 section defers that to a
separate future project.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from bartholomew.kernel.awaiting_response_store import (
    VALID_RESOLUTIONS,
    VALID_STATUSES,
    AwaitingResponseNotFoundError,
    InvalidTransitionError,
)
from bartholomew.kernel.blocking_executor import run_off_loop
from bartholomew.kernel.runtime_contract import run_awaiting_response_through_runtime_contract

router = APIRouter(prefix="/api/awaiting-response", tags=["awaiting-response"])

VALID_ORIGIN_SURFACES = frozenset({"chat", "scheduler"})

# The exact format awaiting_response_store._parse_iso()/_now_iso() use --
# due_at must normalize to this or list_due_for_transition() silently
# excludes the entry forever (PR #38 review finding).
_DUE_AT_STORE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class OpenRequest(BaseModel):
    subject: str
    origin_surface: str = "chat"
    context_ref: str | None = None
    due_at: str | None = None
    actor: str = "user"

    @field_validator("subject")
    @classmethod
    def _subject_must_be_non_empty(cls, value: str) -> str:
        # PR #38 review finding: an empty/whitespace-only subject previously
        # reached run_awaiting_response_through_runtime_contract()'s own
        # precondition check, which raises a bare ValueError -- an
        # uncaught 500, not a validation-shaped 4xx. Rejecting it here
        # instead makes it a normal FastAPI/Pydantic 422.
        stripped = value.strip()
        if not stripped:
            raise ValueError("subject must not be empty or whitespace-only")
        return stripped

    @field_validator("due_at")
    @classmethod
    def _due_at_must_be_parseable_iso8601(cls, value: str | None) -> str | None:
        # PR #38 review finding: due_at was persisted unchanged, but
        # AwaitingResponseStore._parse_iso() only understands the exact
        # "YYYY-MM-DDTHH:MM:SSZ" form -- any other valid RFC 3339 form
        # (e.g. an explicit "+00:00" offset) or malformed input was
        # accepted here and then silently excluded from
        # list_due_for_transition() forever. Normalize valid input to that
        # exact form (mirrors bartholomew.skills.notify.NotifySkill.
        # _normalize_until()'s same normalize-at-write-time approach);
        # reject anything unparseable outright.
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                "due_at must be a valid ISO-8601 timestamp, e.g. '2026-08-07T12:00:00Z'",
            ) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).strftime(_DUE_AT_STORE_FORMAT)


class ResolveRequest(BaseModel):
    resolution: str = "user_dismissed"
    actor: str = "user"


def _get_kernel():
    from bartholomew_api_bridge_v0_1.services.api.app import _kernel

    if _kernel is None:
        raise HTTPException(503, "Kernel not initialized")
    return _kernel


@router.get("")
async def list_entries(status: str | None = None, limit: int = 50) -> dict:
    if limit < 1 or limit > 100:
        raise HTTPException(400, "limit must be between 1 and 100")
    if status is not None and status not in VALID_STATUSES:
        raise HTTPException(400, f"Unknown status {status!r}. Valid: {sorted(VALID_STATUSES)}")

    kernel = _get_kernel()
    entries = await run_off_loop(
        kernel.awaiting_response_store.list,
        status=status,
        limit=limit,
        executor=kernel.blocking_executor,
    )
    return {"entries": [e.to_dict() for e in entries], "count": len(entries)}


@router.post("")
async def open_entry(body: OpenRequest) -> dict:
    if body.origin_surface not in VALID_ORIGIN_SURFACES:
        raise HTTPException(
            400,
            f"Unknown origin_surface {body.origin_surface!r}. "
            f"Valid: {sorted(VALID_ORIGIN_SURFACES)}",
        )

    kernel = _get_kernel()
    result = await run_awaiting_response_through_runtime_contract(
        kernel,
        "open",
        subject=body.subject,
        origin_surface=body.origin_surface,
        context_ref=body.context_ref,
        due_at=body.due_at,
        actor=body.actor,
    )
    if not result.governance_allowed:
        raise HTTPException(503, result.reason or "Blocked by governance")
    return result.entry.to_dict()


@router.post("/{entry_id}/resolve")
async def resolve_entry(entry_id: int, body: ResolveRequest) -> dict:
    if body.resolution not in VALID_RESOLUTIONS:
        raise HTTPException(
            400,
            f"resolution must be one of {sorted(VALID_RESOLUTIONS)}, got {body.resolution!r}",
        )

    kernel = _get_kernel()
    try:
        result = await run_awaiting_response_through_runtime_contract(
            kernel,
            "resolve",
            entry_id=entry_id,
            resolution=body.resolution,
            actor=body.actor,
        )
    except AwaitingResponseNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except InvalidTransitionError as e:
        raise HTTPException(409, str(e)) from e

    if not result.governance_allowed:
        raise HTTPException(503, result.reason or "Blocked by governance")
    return result.entry.to_dict()


@router.get("/{entry_id}/audit")
async def get_entry_audit(entry_id: int, limit: int = 50) -> dict:
    if limit < 1 or limit > 100:
        raise HTTPException(400, "limit must be between 1 and 100")

    kernel = _get_kernel()
    entries = await run_off_loop(
        kernel.awaiting_response_store.list_audit,
        entry_id,
        limit=limit,
        executor=kernel.blocking_executor,
    )
    return {"entries": entries, "count": len(entries)}
