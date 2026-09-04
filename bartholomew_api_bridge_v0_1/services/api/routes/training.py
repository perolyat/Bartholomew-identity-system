"""
S5.2 training ingestion endpoint (Stage 5, step 3).

The API half of Decision B (API + CLI, no new UI in S5.2). Every request is
forwarded to the one governed seam,
`runtime_contract.run_training_through_runtime_contract()` -- this module
performs no writes, no governance, and no interpretation of its own. It
decodes a payload into S5.1 record objects and hands them over.

Two properties are load-bearing rather than incidental:

- **`recorded_by` is set here, from the ingestion route, and is never read
  from the request body** (design Sec.5.3). A client must not be able to
  claim its material came from the user, nor backdate it.
- **This route accepts structured records only** (Decision A). It does not
  extract records from prose. Per Constraint 1 that is a scope boundary for
  S5.2, not the intended final user experience: a future conversational or
  document-extraction path ("Layer 0") is expected to produce records and
  feed this same governed seam rather than gaining a write path of its own.

Auth note: same as every other route in this API bridge -- no
authentication today; ROADMAP.md's Stage 1 section defers that to a
separate future project. `recorded_by` is therefore currently a statement
about *which route* was used, not a verified identity claim.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from bartholomew.kernel import training
from bartholomew.kernel.runtime_contract import run_training_through_runtime_contract

router = APIRouter(prefix="/api/training", tags=["training"])


def _get_kernel():
    from bartholomew_api_bridge_v0_1.services.api.app import _kernel

    if _kernel is None:
        raise HTTPException(503, "Kernel not initialized")
    return _kernel


class TrainingRecordPayload(BaseModel):
    kind: str
    slug: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class TrainingSubmitRequest(BaseModel):
    competency_id: str
    source_type: str
    source_detail: str = ""
    records: list[TrainingRecordPayload] = Field(default_factory=list)


@router.get("/source-types")
async def list_source_types() -> dict:
    """The provenance source types this endpoint accepts, and those it does
    not. The rejected sets are the governed consolidation paths' -- S5.4's
    Bartholomew-originated learning, and Package E's adopted trusted-group
    shares -- not a permanent property of the seam.

    `reserved` is the complete rejected set; `reserved_for_s5_4` is kept
    alongside it so an existing client reading that key still gets the answer
    it always got rather than silently losing a value."""
    return {
        "allowed": sorted(training.ALLOWED_TRAINING_SOURCE_TYPES),
        "reserved": sorted(training.RESERVED_SOURCE_TYPES),
        "reserved_for_s5_4": sorted(training.S5_4_RESERVED_SOURCE_TYPES),
        "reserved_for_trusted_share": sorted(training.SHARE_ADOPTION_SOURCE_TYPES),
        "kinds": sorted(training.RECORD_CLASSES),
    }


@router.post("/submit")
async def submit_training(body: TrainingSubmitRequest) -> dict:
    """
    Submit structured training material for one competency.

    Returns per-record outcomes (Decision C: per-record independence), where
    `queued_for_consent` is deliberately distinct from `stored` -- a caller
    told "trained" about a record awaiting review in the consent inbox would
    be misinformed about what Bartholomew actually knows.

    HTTP 200 with a per-record breakdown is the normal response even when
    some records did not store; 400 is reserved for a submission that could
    not be interpreted at all.
    """
    kernel = _get_kernel()

    if not body.records:
        raise HTTPException(400, "at least one record is required")

    records = []
    for index, payload in enumerate(body.records):
        try:
            records.append(
                training.record_from_payload(
                    payload.kind,
                    {**payload.data, "competency_id": body.competency_id},
                    slug=payload.slug,
                ),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(400, f"records[{index}]: {exc}") from exc

    submission = training.TrainingSubmission(
        competency_id=body.competency_id,
        source_type=body.source_type,
        source_detail=body.source_detail,
        records=records,
    )

    result = await run_training_through_runtime_contract(
        kernel,
        submission,
        # Route-derived, never taken from the request body (design Sec.5.3).
        recorded_by="user",
    )

    if result.errors and not result.outcomes:
        raise HTTPException(400, "; ".join(result.errors))

    return result.to_dict()
