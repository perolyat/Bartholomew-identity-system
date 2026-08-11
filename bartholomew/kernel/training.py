"""
Training ingestion contract types (Stage 5, S5.2)
==================================================

Implements the data/contract half of
`docs/S5_2_TRAINING_KNOWLEDGE_ACQUISITION_DESIGN.md` (design and Decisions
A-E approved 2026-08-11). The governed write seam itself lives in
`bartholomew.kernel.runtime_contract.run_training_through_runtime_contract`,
alongside every other surface's seam, so training does not acquire an
ingestion runtime of its own (`COGNITIVE_RUNTIME.md`: "Training as Memory
input, not a separate pipeline").

Deliberately pure data and validation: no persistence, no retrieval, no I/O
of any kind -- the same discipline `competency.py` holds to, and asserted
structurally by `tests/test_training_runtime_contract_seam.py`'s
`TestStructuralInvariants`. `MemoryStore.upsert_memory()` remains the sole
authority that writes memories.

The seam consumes structured records, never keystrokes (Constraint 1)
-------------------------------------------------------------------------
Per the approved design's Sec.5.5 and Sec.9.1, structured manual submission
is the *first client* of this seam, **not the intended final user
experience**. Nothing in this module may assume a human authored a
submission: it requires structured, provenance-bearing records, whoever (or
whatever) produced them. That single property is what lets future
conversational / model-assisted / document-extraction paths ("Layer 0") feed
this same governed write without a second write path or a second governance
path. Extraction and interpretation are explicitly outside S5.2.

What this module does NOT do
-----------------------------
No extraction or interpretation of prose (Constraint 1). No document/file
ingestion. No Executive integration (S5.3). No candidate-learning or
consolidation loop, and no Bartholomew-originated competency writes (S5.4) --
the `experience` and `system_observation` provenance source types are
rejected here, which is what keeps the S5.2/S5.4 boundary enforced rather
than merely documented.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from bartholomew.kernel.competency import (
    CompetencyEvidence,
    CompetencyHeuristic,
    CompetencyKnowledge,
    CompetencyProcedure,
    CompetencyRecord,
    Provenance,
)

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

#: Any of S5.1's five record types. A submission carries these already-shaped
#: (Decision A: structured-only ingestion).
TrainingRecord = (
    CompetencyRecord
    | CompetencyKnowledge
    | CompetencyProcedure
    | CompetencyHeuristic
    | CompetencyEvidence
)

#: The four user-originated provenance source types S5.2 accepts (design
#: Sec.5.4). `formal_material` here means text supplied directly -- pasted or
#: posted -- not a file read off disk, which is deferred (design Sec.6).
ALLOWED_TRAINING_SOURCE_TYPES: frozenset[str] = frozenset(
    {"formal_material", "user_instruction", "demonstration", "correction"},
)

#: Bartholomew-originated source types, rejected by this seam. NOT a
#: permanent property: when S5.4's consolidation path is designed and
#: approved it may deliberately lift this so its learning flows through this
#: same governed write rather than a second one (design Sec.5.4). What must
#: not happen is the restriction lapsing incidentally.
S5_4_RESERVED_SOURCE_TYPES: frozenset[str] = frozenset({"experience", "system_observation"})

TRAINING_OBSERVATION_SOURCE = "training"
TRAINING_ACTION_KIND = "training_ingest"
TRAINING_BRAKE_SCOPE = "training"

# Per-record outcomes (Decision C: per-record independence).
OUTCOME_STORED = "stored"
OUTCOME_QUEUED_FOR_CONSENT = "queued_for_consent"
OUTCOME_REJECTED_BY_POLICY = "rejected_by_policy"
OUTCOME_DECLINED_BY_CONSENT_HANDLER = "declined_by_consent_handler"
OUTCOME_INVALID = "invalid"
OUTCOME_BLOCKED_BY_GOVERNANCE = "blocked_by_governance"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrainingSubmission:
    """
    One training act: some material, its provenance, and the structured
    records it produced.

    Deliberately carries no `classification`, `confidence`, or `supervision`
    of its own. S5.1's `CompetencyEnvelope` already holds all three
    per-record, and duplicating them here would create an override ambiguity
    that the design forbids resolving by inference -- the seam must never
    infer `potentially_generalisable` on its own (design Sec.7.3). Each
    record's envelope is authoritative for those fields.

    `competency_id` is a consistency assertion, not an override: every
    record must already belong to the competency the submission names.
    """

    competency_id: str
    source_type: str
    source_detail: str
    records: list[TrainingRecord] = field(default_factory=list)

    def validate(self) -> list[str]:
        errors: list[str] = []

        if not self.competency_id:
            errors.append("competency_id is required")

        if self.source_type in S5_4_RESERVED_SOURCE_TYPES:
            errors.append(
                f"source_type {self.source_type!r} is reserved for S5.4 "
                "(Bartholomew-originated learning) and cannot be submitted as training; "
                f"allowed: {sorted(ALLOWED_TRAINING_SOURCE_TYPES)}",
            )
        elif self.source_type not in ALLOWED_TRAINING_SOURCE_TYPES:
            errors.append(
                f"source_type must be one of {sorted(ALLOWED_TRAINING_SOURCE_TYPES)}, "
                f"got {self.source_type!r}",
            )

        if not self.records:
            errors.append("at least one record is required")

        for index, record in enumerate(self.records):
            record_id = f"records[{index}]"
            if not hasattr(record, "envelope") or not hasattr(record, "KIND"):
                errors.append(f"{record_id} is not a competency record")
                continue
            if record.envelope.competency_id != self.competency_id:
                errors.append(
                    f"{record_id} belongs to competency "
                    f"{record.envelope.competency_id!r}, not {self.competency_id!r}",
                )

        return errors


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class TrainingRecordOutcome:
    """What happened to one record. `queued_for_consent` is deliberately
    distinct from `stored`: a user told "trained" about a record sitting in
    the consent inbox would be misinformed about what Bartholomew knows."""

    kind: str
    key: str
    outcome: str
    detail: str | None = None
    memory_id: int | None = None
    revision: int = 1
    superseded_revision: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "key": self.key,
            "outcome": self.outcome,
            "detail": self.detail,
            "memory_id": self.memory_id,
            "revision": self.revision,
            "superseded_revision": self.superseded_revision,
        }


@dataclass
class TrainingRuntimeResult:
    """Everything produced by one pass through the training seam."""

    competency_id: str
    governance_allowed: bool
    governance_reason: str | None = None
    outcomes: list[TrainingRecordOutcome] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def count(self, outcome: str) -> int:
        return sum(1 for item in self.outcomes if item.outcome == outcome)

    @property
    def stored_count(self) -> int:
        return self.count(OUTCOME_STORED)

    @property
    def queued_count(self) -> int:
        return self.count(OUTCOME_QUEUED_FOR_CONSENT)

    @property
    def all_stored(self) -> bool:
        return bool(self.outcomes) and all(item.outcome == OUTCOME_STORED for item in self.outcomes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "competency_id": self.competency_id,
            "governance_allowed": self.governance_allowed,
            "governance_reason": self.governance_reason,
            "outcomes": [item.to_dict() for item in self.outcomes],
            "errors": list(self.errors),
            "summary": {
                "stored": self.stored_count,
                "queued_for_consent": self.queued_count,
                "rejected_by_policy": self.count(OUTCOME_REJECTED_BY_POLICY),
                "declined_by_consent_handler": self.count(OUTCOME_DECLINED_BY_CONSENT_HANDLER),
                "invalid": self.count(OUTCOME_INVALID),
                "blocked_by_governance": self.count(OUTCOME_BLOCKED_BY_GOVERNANCE),
            },
        }


# ---------------------------------------------------------------------------
# Provenance stamping (design Sec.5.3)
# ---------------------------------------------------------------------------


#: `kind` -> S5.1 record class. The five S5.1 kinds and nothing else; an
#: unknown kind is an error, never a silently-skipped record.
RECORD_CLASSES: dict[str, Any] = {
    CompetencyRecord.KIND: CompetencyRecord,
    CompetencyKnowledge.KIND: CompetencyKnowledge,
    CompetencyProcedure.KIND: CompetencyProcedure,
    CompetencyHeuristic.KIND: CompetencyHeuristic,
    CompetencyEvidence.KIND: CompetencyEvidence,
}


def record_from_payload(kind: str, data: dict[str, Any], *, slug: str | None = None):
    """
    Build an S5.1 record from a decoded payload (an API body, a CLI JSON
    file, or -- later -- a Layer 0 extractor's output; see this module's
    docstring on Constraint 1).

    Pure deserialisation: it constructs and returns a record, and performs
    no I/O and no governance. Validation is the caller's to run via the
    record's own `validate()`, and governance is the seam's.

    `slug` is required for the four keyed kinds and must be absent for
    `competency`, which has exactly one record per competency and derives
    its key from `competency_id` alone.
    """
    if kind not in RECORD_CLASSES:
        raise ValueError(
            f"unknown competency kind {kind!r}; expected one of {sorted(RECORD_CLASSES)}",
        )

    record_class = RECORD_CLASSES[kind]

    if record_class is CompetencyRecord:
        if slug:
            raise ValueError(
                "kind 'competency' takes no slug -- there is exactly one such record "
                "per competency and its key is the competency_id",
            )
        return record_class.from_dict(data)

    if not slug:
        raise ValueError(f"kind {kind!r} requires a slug")
    return record_class.from_dict(data, slug=slug)


def stamp_provenance(
    submission: TrainingSubmission,
    *,
    recorded_by: str,
    recorded_at: str | None = None,
) -> Provenance:
    """
    Build the provenance every record in this submission carries.

    The split is the one genuinely new safety rule in S5.2 (design Sec.5.3):
    `source_type` and `detail` come from the caller (only they know whether
    this is a manual or a correction), while `recorded_by` and `recorded_at`
    are supplied by the seam from the ingestion route and the server clock.
    A caller must not be able to claim material came from the user when it
    came from elsewhere, nor backdate it.

    `recorded_by` is therefore a seam argument, never read from the
    submission or from a request body.
    """
    return Provenance(
        source_type=submission.source_type,
        detail=submission.source_detail,
        recorded_by=recorded_by,
        recorded_at=recorded_at or _utcnow_iso(),
    )
