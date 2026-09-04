"""
The Learning and Memory Control Centre's API (Package D).

The HTTP half of one coherent place where a person can see what Bartholomew
believes he has learned, what he is proposing to learn, what a future
auto-acceptance policy *would* have decided, and what any of it stands on --
and can act on all of it without a single write reaching the database except
through an authority that already existed.

Authority
---------
There is exactly one memory authority and this is not it; there is exactly one
learning-consolidation path and this is not that either. Every handler below
is a translation layer:

* reading candidates / competencies / approvals / evaluations /
  superseded versions / memories and preferences
                        -> `MemoryStore.list_memories()` and
                           `.list_memories_by_kind()`, the same reads
                           `routes/memory.py` uses. `list_memories()` is
                           preferred wherever governance metadata is shown,
                           because only it returns `_decorate_entry()`'s
                           output -- the privacy class, the recall policy and
                           the readability flag this module must display and
                           must never derive itself.
* previewing a policy   -> `runtime_contract.run_shadow_learning_evaluation_
                           through_runtime_contract()`
* editing a candidate   -> `runtime_contract.run_candidate_edit_through_
                           runtime_contract()`
* approving             -> `runtime_contract.grant_learning_acceptance_
                           approval()` (PR #83, unchanged)
* accepting / rejecting -> `runtime_contract.run_candidate_lesson_through_
                           runtime_contract()` (S5.4, unchanged)
* correcting knowledge  -> S5.2's training seam, as a `correction`
* revoking knowledge    -> `MemoryStore.forget_memory()` through a governed,
                           audited seam
* configuring the policy-> `runtime_contract.run_learning_policy_update_
                           through_runtime_contract()`

Nothing in this module opens a database connection, evaluates a governance
rule, decides what may be stored, or writes a memory. There is no raw SQL
here and no `aiosqlite` import; `tests/test_no_raw_sqlite_connect_in_api.py`
holds that for the whole package.

Acceptance is still manual, and still candidate-bound
-----------------------------------------------------
Two separate endpoints, deliberately, mirroring the two separate acts PR #83
established: `/approve` records a `LearningAcceptanceApproval` bound to that
candidate's exact material fingerprint, and `/accept` consolidates. Collapsing
them into one call would have made "approved" and "accepted" the same button,
which is the distinction the whole design rests on. Approving does not
consolidate; accepting without a matching approval is refused by
`evaluate_learning_admission()` regardless of what this route does.

`/shadow-evaluate` is a preview and says so in every response it produces.
Its `decision` field is a counterfactual: `would_accept` authorises nothing,
and there is no endpoint here that takes an evaluation as input.

Privacy, consent and tenancy
----------------------------
Reads are projections of `MemoryStore` rows, so redaction, encryption and the
privacy classification the rules engine assigned all arrive already applied --
this module never re-derives them. A record this process cannot decrypt is
reported as unreadable rather than shown blank, and is never exportable.

Tenancy is the process: `_get_kernel()` returns the single per-runtime kernel,
whose `mem.db_path` is one user's isolated database
(`bartholomew.platform.runtime_registry`). No handler here accepts a user id,
reads one from a header, or takes a database path -- there is nothing in this
module's surface through which another tenant's records could be named.

Parking Brake
-------------
Reading is allowed while the brake is engaged; every mutation is refused. As
in `routes/memory.py`, the refusal is raised by the authority underneath, not
decided here -- these handlers only translate it into an honest status code.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field

from bartholomew.kernel import (
    candidate_learning,
    learning_authorization,
    learning_policy,
    personal_facts,
    training,
)
from bartholomew.kernel.competency import COMPETENCY_KINDS
from bartholomew.kernel.runtime_contract import (
    LEARNING_ACTION_ACCEPT,
    LEARNING_ACTION_REJECT,
    LEARNING_OUTCOME_ACCEPTED,
    LEARNING_OUTCOME_EDITED,
    LEARNING_OUTCOME_NOT_STORED,
    LEARNING_OUTCOME_POLICY_UPDATED,
    LEARNING_OUTCOME_REJECTED,
    LEARNING_OUTCOME_REVISION_CONFLICT,
    LEARNING_OUTCOME_UNCHANGED,
    grant_learning_acceptance_approval,
    load_learning_policy,
    run_candidate_edit_through_runtime_contract,
    run_candidate_lesson_through_runtime_contract,
    run_competency_correction_through_runtime_contract,
    run_competency_revocation_through_runtime_contract,
    run_learning_policy_update_through_runtime_contract,
    run_shadow_learning_evaluation_through_runtime_contract,
)
from bartholomew.orchestrator.safety.governance_store import ParkingBrakeEngagedError

router = APIRouter(prefix="/api/learning", tags=["learning"])

#: How many rows a list endpoint reads at most. The control centre is a
#: review queue, not a bulk extraction surface, and the export endpoint below
#: takes an explicit selection rather than a page size.
_LIST_CEILING = 500

#: How `personal_facts` keys a stored preference, so the control centre can
#: name preferences as their own area without inventing a second
#: classification for them.
_PREFERENCE_KEY_PREFIX = "preference."

#: Privacy classes whose *content* the control centre will not put into an
#: export, whatever a caller selects. See `_export_blocked_reason()`.
#:
#: This is every class the shipped `memory_rules.yaml` marks
#: `requires_consent`, plus `user.health`. A hard-coded list rather than one
#: derived from the rules file, because the rules file is user-editable and a
#: derived list would let editing it silently remove an export restriction.
#: `tests/test_learning_control_centre_api.py::
#: test_every_consent_requiring_privacy_class_is_export_blocked` fails if the
#: shipped rules gain a consent-requiring class this list does not name, so a
#: new class is a decision someone has to make rather than a gap.
_NEVER_EXPORT_PRIVACY_CLASSES: frozenset[str] = frozenset(
    {
        "user.secure",
        # bank / medical / address / phone / email content. Missing this was a
        # real hole: it is the broadest consent-gated class the shipped rules
        # define, and it covers most of what a person would call private.
        "user.sensitive",
        "user.health",
        "user.emotional",
        "thirdparty.private",
    },
)


def _get_kernel():
    from bartholomew_api_bridge_v0_1.services.api.app import _kernel

    if _kernel is None:
        raise HTTPException(503, "Kernel not initialized")
    return _kernel


def _brake(exc: ParkingBrakeEngagedError) -> HTTPException:
    return HTTPException(503, str(exc))


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class CandidateEdit(BaseModel):
    """A reviewer's changes to a proposed candidate lesson.

    `expected_revision` is required, not optional: an edit that did not say
    which version it was based on could only be applied last-write-wins, and
    the seam underneath refuses that.
    """

    expected_revision: int
    editor: str = Field(min_length=1)
    inferred_rule: str | None = None
    conditions: str | None = None
    classification: str | None = None
    confidence: float | None = None
    risk_class: str | None = None
    reversible: bool | None = None
    affected_applications: list[str] | None = None
    sharing_eligible: bool | None = None
    #: Administrative. Changing only this leaves the fingerprint, the
    #: revision and any existing approval untouched.
    display_state: str | None = None


class ReviewDecision(BaseModel):
    reviewer: str = Field(min_length=1)
    note: str | None = None


class ApprovalGrant(BaseModel):
    approver: str = Field(min_length=1)
    note: str | None = None
    #: The revision the approver was looking at. Checked before the approval
    #: is granted so an approval cannot be recorded against a candidate that
    #: moved while the screen was open.
    expected_revision: int | None = None


class ShadowEvaluationRequest(BaseModel):
    requested_by: str | None = None
    #: Recorded observations that contradict the lesson, when the reviewer
    #: knows of any. Supplied rather than inferred: nothing in this wave
    #: measures contradiction automatically, and a fabricated zero would be a
    #: policy input nobody checked.
    contradicting_evidence_count: int = Field(default=0, ge=0)


class CompetencyCorrection(BaseModel):
    corrected_by: str = Field(min_length=1)
    expected_revision: int
    #: Field-level replacements for the stored record, e.g. `{"rule": "..."}`.
    #: Provenance, revision and timestamps are the seam's to set and are
    #: stripped before the record is rebuilt.
    updates: dict[str, Any] = Field(default_factory=dict)


class CompetencyRevocation(BaseModel):
    revoked_by: str = Field(min_length=1)
    reason: str | None = None


#: The engine's own safe default, read once so the request body cannot drift
#: laxer than it.
_POLICY_DEFAULTS = learning_policy.default_policy()


class PolicyUpdate(BaseModel):
    """A new learning policy revision, plus the revision it was based on.

    Every default here is taken from `learning_policy.default_policy()` rather
    than restated. An earlier version restated them and got three wrong --
    `excluded_privacy_classes` and `excluded_classifications` defaulted to
    empty lists and both expiry fields to None -- so a client that omitted
    those fields silently dropped every privacy exclusion the engine ships
    with. A request body is a place defaults get weakened by accident, and
    deriving them means it cannot happen again.
    """

    expected_revision: int
    updated_by: str = Field(min_length=1)
    note: str | None = None
    enabled_categories: list[str] = Field(default_factory=list)
    excluded_categories: list[str] = Field(default_factory=list)
    max_risk: str = _POLICY_DEFAULTS.max_risk
    require_reversible: bool = _POLICY_DEFAULTS.require_reversible
    min_supporting_experiences: int = _POLICY_DEFAULTS.min_supporting_experiences
    min_confidence: float = _POLICY_DEFAULTS.min_confidence
    contradiction_behaviour: str = _POLICY_DEFAULTS.contradiction_behaviour
    max_affected_capabilities: int = _POLICY_DEFAULTS.max_affected_capabilities
    max_affected_applications: int = _POLICY_DEFAULTS.max_affected_applications
    excluded_privacy_classes: list[str] = Field(
        default_factory=lambda: list(_POLICY_DEFAULTS.excluded_privacy_classes),
    )
    excluded_classifications: list[str] = Field(
        default_factory=lambda: list(_POLICY_DEFAULTS.excluded_classifications),
    )
    exclude_sharing_eligible: bool = _POLICY_DEFAULTS.exclude_sharing_eligible
    expires_after_days: int | None = _POLICY_DEFAULTS.expires_after_days
    review_interval_days: int | None = _POLICY_DEFAULTS.review_interval_days
    #: What the user would like a future wave to do. Recorded; never acted on.
    requested_execution_mode: str = learning_policy.REQUESTED_MODE_SHADOW


class ExportSelection(BaseModel):
    """An explicit list of records to export. There is no "everything" option.

    Each entry is a `{kind, key}` pair the caller has already seen through
    one of the read endpoints. A missing, ineligible or unreadable record is
    reported as skipped with its reason rather than silently omitted.
    """

    records: list[dict[str, str]] = Field(default_factory=list, max_length=200)
    requested_by: str | None = None


# ---------------------------------------------------------------------------
# Shared projections
# ---------------------------------------------------------------------------


#: Plain-language readings of the rules engine's `recall_policy` values.
_RETENTION_WORDS = {
    "always": "Bartholomew will always bring this up when it is relevant.",
    "context_only": "Kept, but only used in the conversation it belongs to.",
}


def _retention_description(entry: dict[str, Any]) -> str:
    """How long this is kept and how freely it is recalled, in a sentence.

    Read from the entry the memory authority decorated, never re-derived, so
    it says what the rules engine actually decided.
    """
    if entry.get("governance_known") is False:
        return "Bartholomew could not work out how this record is classified."
    policy = entry.get("recall_policy")
    words = _RETENTION_WORDS.get(policy or "", "No particular recall policy is recorded.")
    if entry.get("always_keep"):
        return f"{words} It is not set to expire."
    return words


def _shadow_banner() -> dict[str, Any]:
    """The same truthful statement on every response that mentions a policy."""
    return {
        "execution_mode": learning_policy.SHIPPED_EXECUTION_MODE,
        "automatic_acceptance_enabled": False,
        "notice": learning_policy.SHADOW_MODE_NOTICE,
    }


def _decode(row: dict[str, Any]) -> dict[str, Any] | None:
    """The stored JSON object for a row, or None if it is not one.

    The `isinstance` check is load-bearing: a row that decodes to a list or a
    string would otherwise be handed to a `from_dict()` that calls `.get()` on
    it, raising `AttributeError` -- which none of this module's callers catch,
    so one corrupted row would 500 an entire listing rather than being skipped.
    """
    try:
        payload = json.loads(row["value"])
    except (TypeError, ValueError, KeyError):
        return None
    return payload if isinstance(payload, dict) else None


async def _entries_of_kinds(kernel, kinds: list[str], limit: int) -> list[dict[str, Any]]:
    """Rows of the given kinds, with the governance metadata the UI needs.

    `list_memories()` is used rather than `list_memories_by_kind()` because
    only the former returns `_decorate_entry()`'s output -- the privacy class,
    the recall policy, the readability flag and the consent provenance the
    control centre must show alongside every record. This module derives none
    of that itself.

    Paged per kind rather than searched per row. An earlier version looked
    each row up with `list_memories(search=key)`, which scans and decrypts the
    *entire* store once per row: a hundred candidates meant a hundred
    full-store scans. Paging by kind is one indexed pass per kind.
    """
    decorated: list[dict[str, Any]] = []
    for kind in kinds:
        # A budget per kind, not one shared across them. Sharing it meant the
        # first kind with `limit` rows consumed the whole allowance and every
        # later kind returned nothing -- so a store with many
        # `competency_heuristic` records showed no `competency_knowledge` at
        # all, silently, as if none existed.
        collected = 0
        offset = 0
        while collected < limit:
            page = await kernel.mem.list_memories(
                limit=min(limit - collected, _LIST_CEILING),
                offset=offset,
                kind=kind,
            )
            batch = page["entries"]
            if not batch:
                break
            decorated.extend(batch)
            collected += len(batch)
            if not page["has_more"]:
                break
            offset += len(batch)

    # Newest first across all kinds, then the requested window: the caller
    # asked for the most recent `limit` records, not the most recent `limit`
    # of whichever kind happened to be listed first.
    decorated.sort(
        key=lambda entry: (str(entry.get("ts") or ""), entry.get("id") or 0),
        reverse=True,
    )
    return decorated[:limit]


async def _entries_with_key_prefix(
    kernel,
    kind: str,
    prefix: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Rows of one kind whose key starts with `prefix`, paging the whole kind.

    Filtering one fetched page would silently miss anything outside it: an
    evaluation recorded under an early policy revision, or the archived
    revisions of a candidate edited long ago, both sit behind newer rows of
    the same kind. Neither is rare, and "no previews recorded" for a candidate
    that has several is the sort of wrong answer nobody reports as a bug.
    """
    found: list[dict[str, Any]] = []
    offset = 0
    while len(found) < limit:
        page = await kernel.mem.list_memories(limit=_LIST_CEILING, offset=offset, kind=kind)
        batch = page["entries"]
        for entry in batch:
            if str(entry["key"]).startswith(prefix):
                found.append(entry)
                if len(found) >= limit:
                    break
        if not batch or not page["has_more"]:
            break
        offset += len(batch)
    return found


async def _decorated_entry(kernel, kind: str, key: str) -> dict[str, Any] | None:
    """One record by identity, with its governance metadata.

    Pages the kind rather than searching the store, for the reason above.
    Returns None when the record does not exist under that kind.
    """
    offset = 0
    while True:
        page = await kernel.mem.list_memories(limit=_LIST_CEILING, offset=offset, kind=kind)
        batch = page["entries"]
        for entry in batch:
            if entry["key"] == key:
                return entry
        if not batch or not page["has_more"]:
            return None
        offset += len(batch)


def _candidate_projection(
    entry: dict[str, Any],
    lesson: Any,
    approval: Any = None,
) -> dict[str, Any]:
    """One candidate as the control centre shows it.

    Every field the contract requires a person to be able to see, in one
    place: what it claims, what it stands on, how confident it is, what kind
    of claim it is, how it is classified for privacy and for sharing, whether
    an approval currently authorises accepting it, and when it last changed.

    `approval_valid` is computed rather than stored, and computed by calling
    `LearningAcceptanceApproval.authorizes()` -- the *same function*
    `evaluate_learning_admission()` calls -- rather than by comparing
    fingerprints here. Comparing fingerprints was a second opinion, and it
    drifted the moment approvals also bound to the candidate revision: after an
    edit-and-revert the digests matched, so this said "still applies" and
    enabled the Accept button while acceptance refused. A control that offers
    an action the system will refuse is worse than no control.
    """
    fingerprint = learning_authorization.fingerprint_for(lesson)
    approval_valid = False
    approval_detail: str | None = None
    if approval is not None:
        approval_valid, approval_detail = approval.authorizes(lesson)
    return {
        "kind": candidate_learning.KIND,
        "key": lesson.key(),
        "competency_id": lesson.competency_id,
        "slug": lesson.slug,
        "revision": lesson.revision,
        "fingerprint": fingerprint,
        "review_state": lesson.review_state,
        "display_state": lesson.display_state,
        "requires_review": lesson.requires_review,
        "rule": lesson.inferred_rule,
        "conditions": lesson.conditions,
        "lesson_category": lesson.lesson_kind,
        "epistemic_status": lesson.epistemic_status,
        "classification": lesson.classification,
        "confidence": lesson.confidence,
        "risk_class": lesson.risk_class,
        "reversible": lesson.reversible,
        "affected_capabilities": [lesson.competency_id],
        "affected_applications": list(lesson.affected_applications),
        "sharing": learning_policy.SharingInterface(
            eligible=lesson.effective_sharing_eligible,
        ).to_dict(),
        # The raw field, so an edit form can show "work it out from the
        # classification" as distinct from an explicit yes or no. `sharing`
        # above is the resolved answer, which is what a reader wants.
        "sharing_eligible_explicit": lesson.sharing_eligible,
        "privacy_class": entry.get("privacy_class"),
        "category": entry.get("category"),
        # Retention, as the rules engine records it. `recall_policy` is how
        # long and how freely a record may be brought back; `always_keep` is
        # whether it is exempt from expiry. Both are classifications a person
        # is entitled to see next to the record they describe.
        "recall_policy": entry.get("recall_policy"),
        "always_keep": entry.get("always_keep"),
        "retention": _retention_description(entry),
        "governance_known": entry.get("governance_known"),
        "readable": entry.get("readable", True),
        "provenance": {
            "source_type": lesson.provenance.source_type,
            "detail": lesson.provenance.detail,
            "recorded_by": lesson.provenance.recorded_by,
            "recorded_at": lesson.provenance.recorded_at,
            "reflection_row_id": lesson.reflection_row_id,
        },
        "supporting_experience": {
            "objective_id": lesson.source.objective_id,
            "objective_title": lesson.source.objective_title,
            "resolution": lesson.source.resolution,
            "outcome_note": lesson.source.outcome_note,
            "supporting_event_ids": list(lesson.source.supporting_event_ids),
            "observations": list(lesson.source.observations),
        },
        "review": {
            "reviewer": lesson.reviewer,
            "reviewed_at": lesson.reviewed_at,
            "note": lesson.review_note,
        },
        "consolidated_kind": lesson.consolidated_kind,
        "consolidated_key": lesson.consolidated_key,
        "approval": (
            {
                "approver": approval.approver,
                "granted_at": approval.granted_at,
                "note": approval.note,
                "candidate_revision": approval.candidate_revision,
                "candidate_fingerprint": approval.candidate_fingerprint,
                "valid_for_current_revision": approval_valid,
                # The refusal reason comes from the authority itself, so the
                # screen says exactly why acceptance would refuse rather than
                # a paraphrase that could describe the wrong reason.
                "detail": (
                    "This approval still matches what the candidate says."
                    if approval_valid
                    else (
                        "This approval no longer authorises accepting this "
                        f"candidate: {approval_detail}. Read the current version "
                        "and approve it again if you still want to."
                    )
                ),
            }
            if approval
            else None
        ),
        "can_accept_now": approval_valid
        and lesson.review_state == candidate_learning.REVIEW_PROPOSED,
        "updated_at": lesson.updated_at,
        "last_seen_at": entry.get("ts"),
    }


def _competency_projection(entry: dict[str, Any]) -> dict[str, Any]:
    """One piece of accepted knowledge as the control centre shows it.

    `readable` gates the summary as well as the value. `_decorate_entry()`
    blanks an undecryptable *value* but leaves `summary` as whatever
    `decrypt_if_envelope()` returned -- which, for a key this process does not
    hold, is the encryption envelope itself. Rendering that would put a
    ciphertext blob on screen where a sentence belongs.
    """
    readable = entry.get("readable", True)
    record = _decode(entry) or {}
    envelope_confidence = record.get("confidence")
    provenance = record.get("provenance") or {}
    return {
        "kind": entry["kind"],
        "key": entry["key"],
        "competency_id": record.get("competency_id"),
        "classification": record.get("classification"),
        "confidence": envelope_confidence,
        "revision": record.get("revision", 1),
        "supervision": record.get("supervision"),
        "rule": record.get("rule"),
        "conditions": record.get("conditions"),
        "topic": record.get("topic"),
        "name": record.get("name"),
        "summary": entry.get("summary") if readable else None,
        "provenance": provenance,
        "epistemic_status": (
            "inference"
            if provenance.get("source_type") == candidate_learning.EXPERIENCE_SOURCE_TYPE
            else "instructed"
        ),
        # Same projection as a candidate's, so the two read alike: eligibility
        # is real, and the state is honestly "not connected in this release".
        "sharing": learning_policy.SharingInterface(
            eligible=record.get("classification") not in (None, "personal"),
        ).to_dict(),
        "privacy_class": entry.get("privacy_class"),
        "category": entry.get("category"),
        "recall_policy": entry.get("recall_policy"),
        "always_keep": entry.get("always_keep"),
        "retention": _retention_description(entry),
        "governance_known": entry.get("governance_known"),
        "readable": readable,
        "unreadable_reason": entry.get("unreadable_reason") if not readable else None,
        "retrievable": True,
        "updated_at": record.get("updated_at"),
        "last_seen_at": entry.get("ts"),
    }


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


@router.get("/overview")
async def overview() -> dict[str, Any]:
    """
    Counts and the shadow-mode statement, for the top of the control centre.

    Deliberately the first thing the UI loads and the first thing this module
    documents: whatever else is on the screen, a person opening it should be
    told in one sentence that Bartholomew is not accepting lessons on his own.
    """
    kernel = _get_kernel()
    policy = await load_learning_policy(kernel)

    counts: dict[str, int] = {}
    for row in await kernel.mem.list_memory_kinds():
        counts[row["kind"]] = row["count"]

    candidates = await kernel.mem.list_memories_by_kind(
        [candidate_learning.KIND],
        limit=_LIST_CEILING,
    )
    by_state = {
        candidate_learning.REVIEW_PROPOSED: 0,
        candidate_learning.REVIEW_ACCEPTED: 0,
        candidate_learning.REVIEW_REJECTED: 0,
    }
    for row in candidates:
        data = _decode(row)
        state = (data or {}).get("review_state")
        if state in by_state:
            by_state[state] += 1

    return {
        "shadow_mode": _shadow_banner(),
        "policy": {
            "revision": policy.revision,
            "configured": policy.revision > 0,
            "requested_execution_mode": policy.requested_execution_mode,
            "execution_mode": policy.execution_mode,
            "auto_acceptance_enabled": policy.auto_acceptance_enabled,
            "updated_by": policy.updated_by,
            "updated_at": policy.updated_at,
        },
        "candidates": by_state,
        "counts": {
            "candidate_lessons": counts.get(candidate_learning.KIND, 0),
            "accepted_competencies": sum(counts.get(kind, 0) for kind in COMPETENCY_KINDS),
            "acceptance_approvals": counts.get(learning_authorization.KIND, 0),
            "shadow_evaluations": counts.get(learning_policy.EVALUATION_KIND, 0),
            "superseded_candidate_revisions": counts.get(
                learning_policy.CANDIDATE_REVISION_KIND,
                0,
            ),
            # Every stored record of every kind, learning records included.
            # Named for what it counts rather than "personal_memories", which
            # would have implied a subset it is not.
            "all_stored_records": sum(counts.values()),
        },
    }


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------


@router.get("/candidates")
async def list_candidates(
    review_state: str | None = Query(None),
    search: str | None = Query(None),
    limit: int = Query(100, ge=1, le=_LIST_CEILING),
) -> dict[str, Any]:
    """
    Candidate lessons: proposed, accepted and rejected.

    Rejected candidates are listed, not hidden. A rejection is a real
    decision about a real inference, and the row that records it is the only
    place "Bartholomew once thought this and I said no" is written down.
    """
    kernel = _get_kernel()
    entries = await _entries_of_kinds(kernel, [candidate_learning.KIND], _LIST_CEILING)

    items: list[dict[str, Any]] = []
    for entry in entries:
        data = _decode(entry)
        if data is None:
            continue
        try:
            lesson = candidate_learning.CandidateLesson.from_dict(data)
        except (KeyError, TypeError, ValueError):
            continue
        if review_state and lesson.review_state != review_state:
            continue
        if search:
            needle = search.casefold()
            haystack = f"{lesson.key()} {lesson.inferred_rule} {lesson.conditions}".casefold()
            if needle not in haystack:
                continue
        approval = await _load_approval(kernel, lesson)
        items.append(_candidate_projection(entry, lesson, approval))
        if len(items) >= limit:
            break

    return {"shadow_mode": _shadow_banner(), "candidates": items, "total": len(items)}


async def _load_approval(kernel, lesson):
    """The recorded acceptance approval for one candidate, or None."""
    try:
        row = await kernel.mem.get_memory(learning_authorization.KIND, lesson.key())
    except Exception:
        return None
    if not row:
        return None
    data = _decode(row)
    if data is None:
        return None
    try:
        return learning_authorization.LearningAcceptanceApproval.from_dict(data)
    except (KeyError, TypeError, ValueError):
        return None


async def _load_candidate(kernel, competency_id: str, slug: str):
    key = candidate_learning.key_for(competency_id, slug)
    row = await kernel.mem.get_memory(candidate_learning.KIND, key)
    if not row:
        raise HTTPException(404, f"no candidate lesson {key}")
    data = _decode(row)
    if data is None:
        raise HTTPException(409, f"the stored candidate {key} is not readable as a lesson")
    try:
        return row, candidate_learning.CandidateLesson.from_dict(data)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(409, f"the stored candidate {key} is malformed: {exc}") from exc


@router.get("/candidates/{competency_id}/{slug}")
async def get_candidate(competency_id: str, slug: str) -> dict[str, Any]:
    """
    One candidate in full: what it claims, what it stands on, and its status.

    Includes the archived revisions of the candidate and every shadow
    evaluation recorded against it, so "what exactly did I approve, and what
    would the policy have said?" is answerable from one response.
    """
    kernel = _get_kernel()
    row, lesson = await _load_candidate(kernel, competency_id, slug)
    entry = await _decorated_entry(kernel, candidate_learning.KIND, row["key"]) or row
    approval = await _load_approval(kernel, lesson)

    revisions = []
    for archived in await _entries_with_key_prefix(
        kernel,
        learning_policy.CANDIDATE_REVISION_KIND,
        f"{lesson.key()}@r",
        _LIST_CEILING,
    ):
        data = _decode(archived) or {}
        revisions.append(
            {
                "key": archived["key"],
                "revision": data.get("revision"),
                "rule": data.get("inferred_rule"),
                "conditions": data.get("conditions"),
                "classification": data.get("classification"),
                "confidence": data.get("confidence"),
                "archived_at": archived.get("ts"),
            },
        )

    evaluations = await _evaluations_for(kernel, lesson.key())

    return {
        "shadow_mode": _shadow_banner(),
        "candidate": _candidate_projection(entry, lesson, approval),
        "superseded_revisions": sorted(
            revisions,
            key=lambda r: r.get("revision") or 0,
            reverse=True,
        ),
        "shadow_evaluations": evaluations,
    }


async def _evaluations_for(kernel, candidate_key: str) -> list[dict[str, Any]]:
    """Every recorded shadow evaluation for one candidate, newest policy first.

    Evaluations are keyed `<candidate key>@<policy revision>`, so a new policy
    revision produces a new record instead of overwriting the previous one --
    which is what makes "revision 3 said escalate, revision 4 said accept"
    readable rather than lost.
    """
    out: list[dict[str, Any]] = []
    for row in await _entries_with_key_prefix(
        kernel,
        learning_policy.EVALUATION_KIND,
        f"{candidate_key}@",
        _LIST_CEILING,
    ):
        data = _decode(row)
        if data is None:
            continue
        out.append(data)
    return sorted(out, key=lambda d: d.get("policy_revision") or 0, reverse=True)


@router.post("/candidates/{competency_id}/{slug}/edit")
async def edit_candidate(competency_id: str, slug: str, body: CandidateEdit) -> dict[str, Any]:
    """
    Change what a proposed candidate says, through the governed edit seam.

    Three outcomes a caller must be able to tell apart, and none of them is
    flattened into a bare success:

    * `edited` -- something material changed. The fingerprint moved, the
      revision was bumped, the previous revision is archived, and any prior
      approval no longer authorises acceptance.
    * `unchanged` -- only administrative fields changed (display state). The
      fingerprint, the revision and any approval are untouched, and the
      response says so rather than implying a re-review is needed.
    * `revision_conflict` -- the candidate moved underneath the edit. HTTP 409,
      with the stored version in the body so the UI can show both and ask the
      person to decide. Nothing was written.
    """
    kernel = _get_kernel()
    try:
        result = await run_candidate_edit_through_runtime_contract(
            kernel,
            competency_id=competency_id,
            slug=slug,
            editor=body.editor,
            expected_revision=body.expected_revision,
            inferred_rule=body.inferred_rule,
            conditions=body.conditions,
            classification=body.classification,
            confidence=body.confidence,
            risk_class=body.risk_class,
            reversible=body.reversible,
            affected_applications=body.affected_applications,
            sharing_eligible=body.sharing_eligible,
            display_state=body.display_state,
        )
    except ParkingBrakeEngagedError as exc:
        raise _brake(exc) from exc

    if result.outcome == LEARNING_OUTCOME_REVISION_CONFLICT:
        stored = result.stored_lesson
        raise HTTPException(
            409,
            {
                "detail": result.reason,
                "outcome": result.outcome,
                "stored_revision": stored.revision if stored else None,
                "stored_rule": stored.inferred_rule if stored else None,
                "stored_conditions": stored.conditions if stored else None,
                "your_expected_revision": body.expected_revision,
            },
        )

    if result.outcome not in (LEARNING_OUTCOME_EDITED, LEARNING_OUTCOME_UNCHANGED):
        raise HTTPException(400, {"detail": result.reason, "outcome": result.outcome})

    return {
        "ok": True,
        "outcome": result.outcome,
        "material_change": result.material_change,
        "fingerprint_before": result.fingerprint_before,
        "fingerprint_after": result.fingerprint_after,
        "revision": result.lesson.revision if result.lesson else None,
        "approval_invalidated": result.approval_invalidated,
        "archived_revision_key": result.archived_revision_key,
        "detail": (
            "Saved. Because you changed what this lesson says, any approval you "
            "had already given for it no longer applies -- read it again and "
            "approve the new version if you still want to."
            if result.approval_invalidated
            else (
                "Saved. This lesson now says something different, so it needs "
                "approving again before it can be accepted."
                if result.material_change
                else "Saved. Nothing about what this lesson claims has changed."
            )
        ),
    }


@router.post("/candidates/{competency_id}/{slug}/approve")
async def approve_candidate(competency_id: str, slug: str, body: ApprovalGrant) -> dict[str, Any]:
    """
    Grant the candidate-bound authorization that acceptance requires.

    A direct call to PR #83's `grant_learning_acceptance_approval()`, which
    records an approval keyed to this candidate and fingerprinted over its
    material content. It consolidates nothing on its own, and this endpoint
    does not accept afterwards: approving and accepting stay two acts.

    `expected_revision`, when supplied, is checked here rather than in the
    seam, because "the screen you approved from is out of date" is a
    presentation-layer fact -- the seam's own binding is the fingerprint,
    which is stricter and independent of it.
    """
    kernel = _get_kernel()
    _, lesson = await _load_candidate(kernel, competency_id, slug)

    if body.expected_revision is not None and int(body.expected_revision) != int(lesson.revision):
        raise HTTPException(
            409,
            {
                "detail": (
                    "This candidate changed since you opened it, so nothing was "
                    "approved. Read the current version and decide again."
                ),
                "outcome": LEARNING_OUTCOME_REVISION_CONFLICT,
                "stored_revision": lesson.revision,
                "your_expected_revision": body.expected_revision,
            },
        )

    try:
        result = await grant_learning_acceptance_approval(
            kernel,
            competency_id=competency_id,
            slug=slug,
            approver=body.approver,
            note=body.note,
        )
    except ParkingBrakeEngagedError as exc:
        raise _brake(exc) from exc

    if not result.granted:
        raise HTTPException(400, {"detail": result.reason, "outcome": result.outcome})

    return {
        "ok": True,
        "outcome": result.outcome,
        "consolidated": False,
        "approval": result.approval.to_dict() if result.approval else None,
        "detail": (
            "Approved. Nothing has been learned yet -- accepting is a separate "
            "step, and editing this lesson afterwards cancels this approval."
        ),
    }


@router.post("/candidates/{competency_id}/{slug}/accept")
async def accept_candidate(competency_id: str, slug: str, body: ReviewDecision) -> dict[str, Any]:
    """
    Accept an approved candidate, consolidating it into retrievable knowledge.

    Straight through S5.4's governed seam. The seam evaluates the Parking
    Brake and then requires a `LearningAcceptanceApproval` bound to this exact
    candidate; without one it refuses with
    `acceptance_approval_required`, whatever the Identity allowlist says and
    whatever any shadow evaluation concluded.

    The response never claims a lesson is retrievable when the write did not
    land: `consolidated` comes from the seam's own result, so a consolidation
    held in the consent queue reports `accepted_but_not_stored` rather than
    success.
    """
    kernel = _get_kernel()
    try:
        result = await run_candidate_lesson_through_runtime_contract(
            kernel,
            LEARNING_ACTION_ACCEPT,
            competency_id=competency_id,
            slug=slug,
            reviewer=body.reviewer,
            review_note=body.note,
        )
    except ParkingBrakeEngagedError as exc:
        raise _brake(exc) from exc

    consolidated = result.outcome == LEARNING_OUTCOME_ACCEPTED and result.consolidated
    if not result.governance_allowed or result.outcome not in (
        LEARNING_OUTCOME_ACCEPTED,
        # An acceptance the consent queue held is a real, recorded decision
        # whose knowledge did not land. It is reported below with
        # `consolidated: false`, not raised as a failure.
        LEARNING_OUTCOME_NOT_STORED,
    ):
        raise HTTPException(
            403 if not result.governance_allowed else 400,
            {"detail": result.reason, "outcome": result.outcome, "consolidated": False},
        )

    return {
        "ok": True,
        "outcome": result.outcome,
        "consolidated": consolidated,
        "consolidated_kind": result.lesson.consolidated_kind if result.lesson else None,
        "consolidated_key": result.lesson.consolidated_key if result.lesson else None,
        "detail": (
            "Accepted. Bartholomew can now recall this."
            if consolidated
            else (
                "Your decision to accept was recorded, but the knowledge was not "
                "stored -- it is waiting on consent or was refused by the memory "
                "rules. Bartholomew cannot recall it."
            )
        ),
    }


@router.post("/candidates/{competency_id}/{slug}/reject")
async def reject_candidate(competency_id: str, slug: str, body: ReviewDecision) -> dict[str, Any]:
    """Reject a candidate. Terminal, and nothing is consolidated -- then or ever."""
    kernel = _get_kernel()
    try:
        result = await run_candidate_lesson_through_runtime_contract(
            kernel,
            LEARNING_ACTION_REJECT,
            competency_id=competency_id,
            slug=slug,
            reviewer=body.reviewer,
            review_note=body.note,
        )
    except ParkingBrakeEngagedError as exc:
        raise _brake(exc) from exc

    if result.outcome != LEARNING_OUTCOME_REJECTED:
        raise HTTPException(
            403 if not result.governance_allowed else 400,
            {"detail": result.reason, "outcome": result.outcome},
        )
    return {
        "ok": True,
        "outcome": result.outcome,
        "consolidated": False,
        "detail": (
            "Rejected. This lesson cannot be accepted later -- the decision is "
            "final, and the record of it is kept."
        ),
    }


@router.post("/candidates/{competency_id}/{slug}/shadow-evaluate")
async def shadow_evaluate(
    competency_id: str,
    slug: str,
    body: ShadowEvaluationRequest,
) -> dict[str, Any]:
    """
    Preview what the recorded policy would have decided about this candidate.

    Runs the deterministic evaluator and records the result. It cannot accept,
    cannot approve, and cannot change the candidate's review state -- see the
    seam's docstring for the structural reasons rather than the intended ones.

    The response repeats the shadow-mode statement alongside the decision, so
    a `would_accept` is never rendered without the sentence explaining that
    nothing happened.
    """
    kernel = _get_kernel()
    try:
        result = await run_shadow_learning_evaluation_through_runtime_contract(
            kernel,
            competency_id=competency_id,
            slug=slug,
            contradicting_evidence_count=body.contradicting_evidence_count,
            requested_by=body.requested_by,
        )
    except ParkingBrakeEngagedError as exc:
        raise _brake(exc) from exc

    if result.decision is None:
        raise HTTPException(
            403 if not result.governance_allowed else 404,
            {"detail": result.reason, "outcome": result.outcome},
        )

    return {
        "shadow_mode": _shadow_banner(),
        "ok": True,
        "outcome": result.outcome,
        "consolidated": False,
        "authorizes_acceptance": False,
        "policy_revision": result.policy_revision,
        "evaluation": result.decision.to_dict(),
    }


# ---------------------------------------------------------------------------
# Accepted knowledge
# ---------------------------------------------------------------------------


@router.get("/competencies")
async def list_competencies(
    search: str | None = Query(None),
    limit: int = Query(100, ge=1, le=_LIST_CEILING),
) -> dict[str, Any]:
    """Accepted, retrievable competency knowledge."""
    kernel = _get_kernel()
    entries = await _entries_of_kinds(kernel, list(COMPETENCY_KINDS), _LIST_CEILING)
    items = []
    for entry in entries:
        projection = _competency_projection(entry)
        if search:
            needle = search.casefold()
            hay = json.dumps(projection, default=str).casefold()
            if needle not in hay:
                continue
        items.append(projection)
        if len(items) >= limit:
            break
    return {"competencies": items, "total": len(items)}


@router.post("/competencies/{kind}/{key}/correct")
async def correct_competency(
    kind: str,
    key: str,
    body: CompetencyCorrection,
) -> dict[str, Any]:
    """
    Correct a piece of accepted knowledge, superseding the previous revision.

    Goes through S5.2's training seam as a `correction`, so the corrected
    record faces the same governance the original write did and the
    supersession is recorded by the authority that owns it. Refused with 409
    when the record changed underneath the editor.
    """
    kernel = _get_kernel()
    try:
        result = await run_competency_correction_through_runtime_contract(
            kernel,
            kind=kind,
            key=key,
            corrected_by=body.corrected_by,
            expected_revision=body.expected_revision,
            updates=body.updates,
        )
    except ParkingBrakeEngagedError as exc:
        raise _brake(exc) from exc

    if result.errors:
        conflict = any("changed since you opened it" in err for err in result.errors)
        raise HTTPException(
            409 if conflict else 400,
            {"detail": "; ".join(result.errors), "outcome": "invalid"},
        )

    # `errors` covers submission-level problems only. A Parking Brake refusal
    # sets `governance_allowed=False` and a per-record outcome, with no error
    # at all -- so checking `errors` alone reported a halted system as a 200
    # whose detail said the correction was "waiting on consent". Different
    # facts need different answers.
    if not result.governance_allowed:
        raise HTTPException(
            503,
            {
                "detail": (
                    result.governance_reason or "Bartholomew is halted, so nothing was corrected."
                ),
                "outcome": "blocked_by_governance",
            },
        )

    payload = result.to_dict()
    payload["ok"] = result.stored_count > 0
    if result.stored_count > 0:
        payload["detail"] = "Corrected. Bartholomew will recall the new version from now on."
    else:
        # Say which not-stored this was. "Waiting on consent" and "refused
        # outright" and "the record was not valid" are three different things
        # to do next.
        outcome = result.outcomes[0] if result.outcomes else None
        reason = getattr(outcome, "outcome", None)
        detail = getattr(outcome, "detail", None)
        if reason == training.OUTCOME_QUEUED_FOR_CONSENT:
            payload["detail"] = (
                "This correction needs your consent before it can be stored. It "
                "is waiting in Pending Memory Consent; the previous version "
                "still stands."
            )
        elif reason == training.OUTCOME_INVALID:
            payload["detail"] = (
                f"The corrected record was not valid, so nothing was changed: "
                f"{detail or 'no detail recorded'}"
            )
        else:
            payload["detail"] = (
                "The memory rules refused this correction, so the previous version still stands."
            )
    return payload


@router.post("/competencies/{kind}/{key}/revoke")
async def revoke_competency(
    kind: str,
    key: str,
    body: CompetencyRevocation,
    confirm: bool = Query(False, description="Must be true; revocation is permanent."),
) -> dict[str, Any]:
    """
    Withdraw accepted knowledge from future retrieval. Permanent, and confirmed.

    The knowledge goes; the record that it was once proposed, approved and
    accepted stays -- the candidate row, its archived revisions, the approval
    and every Reflection are untouched. That asymmetry is deliberate: removing
    what Bartholomew can recall must not also remove the ability to answer
    "did I ever agree to this?".
    """
    if not confirm:
        raise HTTPException(
            400,
            "Revoking accepted knowledge removes it from what Bartholomew can "
            "recall and cannot be undone. Repeat this request with confirm=true.",
        )

    kernel = _get_kernel()
    try:
        result = await run_competency_revocation_through_runtime_contract(
            kernel,
            kind=kind,
            key=key,
            revoked_by=body.revoked_by,
            reason=body.reason,
        )
    except ParkingBrakeEngagedError as exc:
        raise _brake(exc) from exc

    if not result.removed:
        raise HTTPException(
            403 if not result.governance_allowed else 404,
            {"detail": result.reason, "outcome": result.outcome},
        )
    return {
        "ok": True,
        "outcome": result.outcome,
        "kind": result.kind,
        "key": result.key,
        "detail": (
            "Revoked. Bartholomew will not recall this again. The record that it "
            "was once accepted is kept."
        ),
    }


@router.get("/superseded")
async def list_superseded(limit: int = Query(100, ge=1, le=_LIST_CEILING)) -> dict[str, Any]:
    """
    What Bartholomew used to think: superseded candidate and competency revisions.

    A required area in its own right, and one that is easy to lose. A candidate
    edit archives the wording it replaced; a correction archives the belief it
    replaced. Both are otherwise reachable only by opening the one record they
    belong to, and neither is retrievable as knowledge -- both kinds are absent
    from `COMPETENCY_KINDS`, so a superseded belief can never come back as a
    current one.
    """
    kernel = _get_kernel()
    entries = await _entries_of_kinds(
        kernel,
        [learning_policy.CANDIDATE_REVISION_KIND, learning_policy.COMPETENCY_REVISION_KIND],
        limit,
    )

    items: list[dict[str, Any]] = []
    for entry in entries:
        record = _decode(entry) or {}
        is_candidate = entry["kind"] == learning_policy.CANDIDATE_REVISION_KIND
        live_key = str(entry["key"]).rsplit("@r", 1)[0]
        items.append(
            {
                "kind": entry["kind"],
                "key": entry["key"],
                "supersedes": live_key,
                "what_it_is": (
                    "a lesson he proposed, as it read before it was edited"
                    if is_candidate
                    else "something he had been taught, as it read before it was corrected"
                ),
                "revision": record.get("revision"),
                "text": (
                    record.get("inferred_rule")
                    if is_candidate
                    else (record.get("rule") or record.get("content") or record.get("name"))
                ),
                "conditions": record.get("conditions"),
                "classification": record.get("classification"),
                "confidence": record.get("confidence"),
                "provenance": record.get("provenance"),
                "privacy_class": entry.get("privacy_class"),
                "retention": _retention_description(entry),
                "readable": entry.get("readable", True),
                "archived_at": entry.get("ts"),
                "retrievable": False,
            },
        )
    return {"superseded": items, "total": len(items)}


@router.get("/memories")
async def list_personal_memories(
    area: str = Query("all", pattern="^(all|preferences|facts)$"),
    search: str | None = Query(None),
    limit: int = Query(100, ge=1, le=_LIST_CEILING),
) -> dict[str, Any]:
    """
    Personal memories and preferences, as two named areas rather than one list.

    Both are required areas of the control centre, and they are stored in the
    same place: `personal_facts` writes a preference as a `user_profile` row
    keyed `preference.<slug>` (see `personal_facts.py`). Nothing in the store
    distinguishes them, so this is where the distinction is drawn -- from the
    key convention the writer already uses, not from a second classification.

    Reading only. Correcting and forgetting a memory stay on `/api/memory`,
    which is the surface that already owns them; duplicating those verbs here
    would be a second way to do the same governed thing.
    """
    kernel = _get_kernel()

    # Paged until the window is filled or the store is exhausted, because the
    # area filter is applied *after* the read: one page of the newest rows
    # would report "no preferences" for a store whose preferences all sit
    # behind five hundred newer facts.
    items: list[dict[str, Any]] = []
    offset = 0
    store_total = 0
    filtered = False
    while len(items) < limit:
        page = await kernel.mem.list_memories(
            limit=_LIST_CEILING,
            offset=offset,
            search=search or None,
        )
        store_total = page["store_total"]
        filtered = page["filtered"]
        batch = page["entries"]
        if not batch:
            break
        for entry in batch:
            is_preference = entry["kind"] in personal_facts.PERSONAL_FACT_KINDS and str(
                entry["key"],
            ).startswith(_PREFERENCE_KEY_PREFIX)
            if area == "preferences" and not is_preference:
                continue
            if area == "facts" and is_preference:
                continue
            items.append(
                {
                    "kind": entry["kind"],
                    "key": entry["key"],
                    "area": "preference" if is_preference else "memory",
                    "value": entry.get("value") if entry.get("readable", True) else None,
                    "summary": entry.get("summary") if entry.get("readable", True) else None,
                    "privacy_class": entry.get("privacy_class"),
                    "category": entry.get("category"),
                    "recall_policy": entry.get("recall_policy"),
                    "always_keep": entry.get("always_keep"),
                    "retention": _retention_description(entry),
                    "governance_known": entry.get("governance_known"),
                    "readable": entry.get("readable", True),
                    "unreadable_reason": entry.get("unreadable_reason"),
                    "consent_at": entry.get("consent_at"),
                    "consent_source": entry.get("consent_source"),
                    "last_updated": entry.get("ts"),
                    "exportable": _export_blocked_reason(entry) is None,
                    "export_blocked_reason": _export_blocked_reason(entry),
                },
            )
            if len(items) >= limit:
                break
        if not page["has_more"]:
            break
        offset += len(batch)

    return {
        "area": area,
        "memories": items,
        "total": len(items),
        "store_total": store_total,
        "filtered": filtered,
    }


# ---------------------------------------------------------------------------
# Approvals and evaluations
# ---------------------------------------------------------------------------


@router.get("/approvals")
async def list_approvals(limit: int = Query(100, ge=1, le=_LIST_CEILING)) -> dict[str, Any]:
    """
    Every recorded acceptance approval, and whether it still authorises anything.

    An approval whose candidate has since changed is shown as no longer
    valid rather than hidden: it is the record of a decision somebody made,
    and the reason it stopped applying is itself worth reading.
    """
    kernel = _get_kernel()
    rows = await kernel.mem.list_memories_by_kind([learning_authorization.KIND], limit=limit)
    items = []
    for row in rows:
        data = _decode(row)
        if data is None:
            continue
        try:
            approval = learning_authorization.LearningAcceptanceApproval.from_dict(data)
        except (KeyError, TypeError, ValueError):
            continue
        candidate_row = await kernel.mem.get_memory(candidate_learning.KIND, approval.key())
        still_valid = False
        candidate_state = None
        if candidate_row:
            candidate_data = _decode(candidate_row)
            if candidate_data:
                try:
                    lesson = candidate_learning.CandidateLesson.from_dict(candidate_data)
                    candidate_state = lesson.review_state
                    # Same authority as acceptance, for the same reason
                    # `_candidate_projection` uses it.
                    still_valid, _reason = approval.authorizes(lesson)
                except (KeyError, TypeError, ValueError):
                    pass
        items.append(
            {
                **approval.to_dict(),
                "candidate_key": approval.key(),
                "candidate_review_state": candidate_state,
                "valid_for_current_revision": still_valid,
                "granted_at_display": approval.granted_at,
            },
        )
    return {"approvals": items, "total": len(items)}


@router.get("/evaluations")
async def list_evaluations(limit: int = Query(100, ge=1, le=_LIST_CEILING)) -> dict[str, Any]:
    """Every recorded shadow evaluation, newest first, with the mode statement."""
    kernel = _get_kernel()
    rows = await kernel.mem.list_memories_by_kind([learning_policy.EVALUATION_KIND], limit=limit)
    items = [d for d in (_decode(row) for row in rows) if d is not None]
    return {"shadow_mode": _shadow_banner(), "evaluations": items, "total": len(items)}


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


@router.get("/policy")
async def get_policy() -> dict[str, Any]:
    """The current learning policy, and an unambiguous statement of what runs."""
    kernel = _get_kernel()
    policy = await load_learning_policy(kernel)
    return {
        "shadow_mode": _shadow_banner(),
        "policy": policy.to_dict(),
        "vocabulary": {
            "risk_classes": list(learning_policy.RISK_CLASSES),
            "contradiction_behaviours": sorted(learning_policy.CONTRADICTION_BEHAVIOURS),
            "lesson_categories": sorted(candidate_learning.LESSON_KINDS),
            "classifications": sorted(
                {"personal", "potentially_generalisable", "system"},
            ),
            "requested_execution_modes": sorted(learning_policy.REQUESTED_MODES),
            "rules": list(learning_policy.RULE_ORDER),
        },
    }


@router.get("/policy/history")
async def policy_history(limit: int = Query(50, ge=1, le=_LIST_CEILING)) -> dict[str, Any]:
    """Superseded policy revisions, newest first.

    A shadow decision names the revision it ran under; without this endpoint
    that number would be unresolvable once the policy moved on.
    """
    kernel = _get_kernel()
    history = []
    for row in await _entries_with_key_prefix(
        kernel,
        learning_policy.POLICY_KIND,
        f"{learning_policy.POLICY_KEY}@r",
        limit,
    ):
        data = _decode(row)
        if data is not None:
            history.append(data)
    return {
        "shadow_mode": _shadow_banner(),
        "history": sorted(history, key=lambda d: d.get("revision") or 0, reverse=True),
    }


@router.put("/policy")
async def update_policy(body: PolicyUpdate) -> dict[str, Any]:
    """
    Record a new policy revision.

    Conflict-guarded: `expected_revision` must be the stored revision, or the
    write is refused with 409 and both versions are returned. Nothing here can
    enable automatic acceptance -- a `requested_execution_mode` of `"auto"` is
    stored as a preference and the response states plainly that the execution
    mode is still shadow.
    """
    kernel = _get_kernel()
    proposed = learning_policy.LearningPolicy(
        enabled_categories=body.enabled_categories,
        excluded_categories=body.excluded_categories,
        max_risk=body.max_risk,
        require_reversible=body.require_reversible,
        min_supporting_experiences=body.min_supporting_experiences,
        min_confidence=body.min_confidence,
        contradiction_behaviour=body.contradiction_behaviour,
        max_affected_capabilities=body.max_affected_capabilities,
        max_affected_applications=body.max_affected_applications,
        excluded_privacy_classes=body.excluded_privacy_classes,
        excluded_classifications=body.excluded_classifications,
        exclude_sharing_eligible=body.exclude_sharing_eligible,
        expires_after_days=body.expires_after_days,
        review_interval_days=body.review_interval_days,
        requested_execution_mode=body.requested_execution_mode,
    )
    try:
        result = await run_learning_policy_update_through_runtime_contract(
            kernel,
            proposed,
            expected_revision=body.expected_revision,
            updated_by=body.updated_by,
            note=body.note,
        )
    except ParkingBrakeEngagedError as exc:
        raise _brake(exc) from exc

    if result.outcome == LEARNING_OUTCOME_REVISION_CONFLICT:
        raise HTTPException(
            409,
            {
                "detail": result.reason,
                "outcome": result.outcome,
                "stored_policy": result.stored_policy.to_dict() if result.stored_policy else None,
                "your_expected_revision": body.expected_revision,
            },
        )
    if result.outcome != LEARNING_OUTCOME_POLICY_UPDATED:
        raise HTTPException(
            403 if result.outcome.endswith("denied") else 400,
            {"detail": result.reason, "outcome": result.outcome, "errors": result.errors},
        )

    return {
        "shadow_mode": _shadow_banner(),
        "ok": True,
        "outcome": result.outcome,
        "policy": result.policy.to_dict() if result.policy else None,
        "auto_acceptance_enabled": False,
        "detail": (
            "Saved. This changes what the preview will say about lessons from "
            "now on. It does not accept anything, now or retrospectively, and "
            "it does not change any preview already recorded."
        ),
    }


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def _export_blocked_reason(entry: dict[str, Any]) -> str | None:
    """Why this record may not be exported, or None if it may.

    Three refusals, and each is a different fact.

    One thing to know about the third. `_decorate_entry()` derives the privacy
    class by re-running the rules over the value *as stored*, and the shipped
    rules redact the consent-gated classes before storing them -- so a
    `user.secure` row comes back masked, and its re-derived class is None
    rather than `user.secure`. That reads like a hole and is not one: the
    sensitive text is not in the row to export. Where the class survives is
    exactly where it matters -- a deployment whose `memory_rules.yaml` assigns
    a restricted class *without* redacting, so the original value is stored,
    re-derives the same class, and is refused here. The two halves cover each
    other: redaction removes the content, and this gate removes the record.

    Refusals in detail:

    * **unreadable** -- stored encrypted under a key this process does not
      hold. Exporting it would produce a file of ciphertext presented as the
      user's records.
    * **classification unknown** -- follows from the first: a record whose
      governance metadata could not be derived cannot be shown to be safe to
      export, and "we could not tell" must not resolve to "yes".
    * **restricted privacy class** -- secure, health, emotional or
      third-party material. Being visible in an administrative view is not
      the same permission as being copied out of the runtime, and this is the
      line the contract draws.

    Never-store material never reaches this check at all: it was refused at
    `upsert_memory()` and no row exists.
    """
    if entry.get("readable") is False:
        return (
            "This record is stored encrypted and cannot be read by this process, "
            "so it cannot be exported."
        )
    if entry.get("governance_known") is False:
        return (
            "Bartholomew could not work out how this record is classified, so it "
            "is left out rather than exported without knowing."
        )
    privacy_class = entry.get("privacy_class")
    if privacy_class and privacy_class in _NEVER_EXPORT_PRIVACY_CLASSES:
        return (
            f"This record is classified {privacy_class!r}. Sensitive material is "
            "not exportable, even though you can see it here."
        )
    return None


@router.post("/export")
async def export_selection(body: ExportSelection) -> Response:
    """
    Export exactly the records the caller selected, and nothing else.

    Deliberately a POST with a body rather than a GET with a filter: there is
    no argument to this endpoint that means "everything", and there is no
    default that produces a bulk dump of the memory database. Selecting 200
    records is the ceiling, and the caller had to name each one.

    What an export carries: the record, its provenance, its classification,
    and the schema/version information needed to read it later. What it never
    carries:

    * anything unreadable, unclassified, or in a restricted privacy class
      (`_export_blocked_reason`);
    * acceptance approvals. `learning_acceptance_approval` rows are refused
      by name -- an approval is internal governance material naming who
      authorised what, and disclosing the set of approvals is a different and
      riskier act than disclosing the knowledge they led to. The approval's
      *effect* is visible in the exported candidate's own review record.

    Every refusal is reported in `skipped` with its reason. An export that
    quietly omitted a record the user asked for would misrepresent itself as
    complete.
    """
    kernel = _get_kernel()

    exported: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    # One decorated pass per *kind* in the selection, not one per record: a
    # 200-record export must not become 200 full-store reads.
    selected_kinds = sorted(
        {
            str(selector.get("kind", ""))
            for selector in body.records
            if selector.get("kind") and selector.get("kind") != learning_authorization.KIND
        },
    )
    by_identity: dict[tuple[str, str], dict[str, Any]] = {}
    for kind in selected_kinds:
        for entry in await _entries_of_kinds(kernel, [kind], _LIST_CEILING):
            by_identity[(entry["kind"], entry["key"])] = entry

    for selector in body.records:
        kind = str(selector.get("kind", ""))
        key = str(selector.get("key", ""))
        if not kind or not key:
            skipped.append({"kind": kind, "key": key, "reason": "kind and key are required"})
            continue
        if kind == learning_authorization.KIND:
            skipped.append(
                {
                    "kind": kind,
                    "key": key,
                    "reason": (
                        "Acceptance approvals are internal governance records and "
                        "are not exportable. What they authorised is visible on "
                        "the candidate itself."
                    ),
                },
            )
            continue

        entry = by_identity.get((kind, key))
        if entry is None:
            # The bulk map is capped at the newest `_LIST_CEILING` rows per
            # kind, so an older record is absent from it without being absent
            # from the store. Reporting "no such record" for one the user can
            # see on screen would be a lie; page for it instead. Bounded by
            # the selection cap, so this is at most a few full passes.
            entry = await _decorated_entry(kernel, kind, key)
        if entry is None:
            skipped.append({"kind": kind, "key": key, "reason": "no such record"})
            continue

        blocked = _export_blocked_reason(entry)
        if blocked:
            skipped.append({"kind": kind, "key": key, "reason": blocked})
            continue

        exported.append(
            {
                "kind": entry["kind"],
                "key": entry["key"],
                "recorded_at": entry.get("ts"),
                "summary": entry.get("summary"),
                "value": _decode(entry) or entry.get("value"),
                "classification": {
                    "privacy_class": entry.get("privacy_class"),
                    "category": entry.get("category"),
                    "recall_policy": entry.get("recall_policy"),
                    "always_keep": entry.get("always_keep"),
                },
                "consent": {
                    "consent_at": entry.get("consent_at"),
                    "consent_source": entry.get("consent_source"),
                },
            },
        )

    payload = {
        "schema": "bartholomew.learning_memory_export",
        "schema_version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "requested_by": body.requested_by,
        "selection_size": len(body.records),
        "exported_count": len(exported),
        "skipped": skipped,
        "shadow_mode": _shadow_banner(),
        "notice": (
            "This file contains only the records you selected. Sensitive, "
            "unreadable and internal approval records are never included, and "
            "anything left out is listed under 'skipped' with the reason."
        ),
        "records": exported,
    }
    return Response(
        content=json.dumps(payload, indent=2, default=str),
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="bartholomew-learning-export.json"',
        },
    )
