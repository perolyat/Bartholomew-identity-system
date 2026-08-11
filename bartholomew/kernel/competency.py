"""
Competency data/contract model (Stage 5, S5.1)
================================================

Implements `docs/S5_1_COMPETENCY_ARCHITECTURE_DESIGN.md` (approved 2026-08-08) --
the smallest generic competency data/contract model, expressed as five new
`bartholomew.kernel.memory_store.MemoryStore` `kind` values, per
`COGNITIVE_RUNTIME.md`'s "Memory semantics this implies (kinds, not a
schema)". This module defines the *shape* of that data only.

Deliberately pure data: no persistence, no retrieval, no I/O of any kind.
`MemoryStore` (via its existing, unmodified `upsert_memory()`) remains the
sole authority that writes to the database -- nothing here imports
`aiosqlite`/`sqlite3`, constructs a database connection, or calls
`MemoryStore` itself (see `tests/test_no_promotion_export_mechanism_introduced`
in `tests/test_competency_no_auto_promotion.py`, which asserts exactly this).

No new memory authority, Executive, or Governance path is introduced.
Applying competency data to a decision (retrieval + reasoning) is S5.3's
job, not this module's; training-material ingestion is S5.2; the
experience -> reflection -> candidate-learning -> consolidation loop is
S5.4. None of that is implemented here.

Classification is informational metadata only
-----------------------------------------------
`classification` on every record below is one of `"personal"`,
`"potentially_generalisable"`, or `"system"`. Recording a value as
`"potentially_generalisable"` is a *candidacy marker only* -- see
`CONSTITUTION.md`'s "Personal learning vs. potentially generalisable and
system-level learning" and this design doc's Sec 5. It never automatically
promotes, exports, transmits, or incorporates anything anywhere; no
de-identification/validation/consent/transport pipeline exists in this
repository, and none is introduced by this module. This module's own code
never branches on `classification`'s value to do anything beyond store and
serialise it (see `tests/test_competency_no_auto_promotion.py`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar

from bartholomew.kernel.memory.privacy_guard import register_structural_schema

# ---------------------------------------------------------------------------
# Shared vocabulary
# ---------------------------------------------------------------------------

CLASSIFICATION_VALUES: frozenset[str] = frozenset(
    {"personal", "potentially_generalisable", "system"},
)

PROVENANCE_SOURCE_TYPES: frozenset[str] = frozenset(
    {
        "formal_material",
        "user_instruction",
        "demonstration",
        "correction",
        "experience",
        "system_observation",
    },
)

PROVENANCE_RECORDED_BY_VALUES: frozenset[str] = frozenset({"user", "executive", "reflection"})


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def key_for(competency_id: str, slug: str) -> str:
    """
    `"<competency_id>.<slug>"` naming convention (design doc Sec 4.7).

    No database foreign key exists between competency-related memories --
    this, plus each record's `kind`, is the only linking mechanism.
    `WHERE kind = ? AND key LIKE '<competency_id>.%'` uses the existing
    `(kind, key)` unique index on `memories` for an efficient prefix scan.
    """
    return f"{competency_id}.{slug}"


# ---------------------------------------------------------------------------
# Shared envelope (composed into, not inherited by, each record type below --
# every record's `to_dict()` flattens the envelope's fields to the top level
# of the JSON stored in `memories.value`, matching the design doc's examples)
# ---------------------------------------------------------------------------


@dataclass
class Provenance:
    """Where a competency-related record's content came from."""

    source_type: str
    detail: str = ""
    recorded_by: str = "user"
    recorded_at: str = field(default_factory=_utcnow_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "detail": self.detail,
            "recorded_by": self.recorded_by,
            "recorded_at": self.recorded_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Provenance:
        return cls(
            source_type=data["source_type"],
            detail=data.get("detail", ""),
            recorded_by=data.get("recorded_by", "user"),
            recorded_at=data.get("recorded_at") or _utcnow_iso(),
        )

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.source_type not in PROVENANCE_SOURCE_TYPES:
            errors.append(
                f"provenance.source_type must be one of {sorted(PROVENANCE_SOURCE_TYPES)}, "
                f"got {self.source_type!r}",
            )
        if self.recorded_by not in PROVENANCE_RECORDED_BY_VALUES:
            errors.append(
                f"provenance.recorded_by must be one of {sorted(PROVENANCE_RECORDED_BY_VALUES)}, "
                f"got {self.recorded_by!r}",
            )
        return errors


@dataclass
class Supervision:
    """Whether this record (or the competency it belongs to) needs review
    before the Executive acts on it. Per-record fields override the owning
    competency's own default (S5.3 concern; not consumed here)."""

    requires_review: bool = False
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"requires_review": self.requires_review, "reason": self.reason}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Supervision:
        if not data:
            return cls()
        return cls(
            requires_review=bool(data.get("requires_review", False)),
            reason=data.get("reason"),
        )


@dataclass
class CompetencyEnvelope:
    """
    Fields every one of the five competency-related `kind` values carries.
    See `docs/S5_1_COMPETENCY_ARCHITECTURE_DESIGN.md` Sec 4.1.
    """

    competency_id: str
    classification: str = "personal"
    provenance: Provenance = field(
        default_factory=lambda: Provenance(source_type="user_instruction"),
    )
    confidence: float | None = None
    supervision: Supervision = field(default_factory=Supervision)
    revision: int = 1
    updated_at: str = field(default_factory=_utcnow_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "competency_id": self.competency_id,
            "classification": self.classification,
            "provenance": self.provenance.to_dict(),
            "confidence": self.confidence,
            "supervision": self.supervision.to_dict(),
            "revision": self.revision,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompetencyEnvelope:
        provenance_data = data.get("provenance") or {"source_type": "user_instruction"}
        return cls(
            competency_id=data["competency_id"],
            classification=data.get("classification", "personal"),
            provenance=Provenance.from_dict(provenance_data),
            confidence=data.get("confidence"),
            supervision=Supervision.from_dict(data.get("supervision")),
            revision=data.get("revision", 1),
            updated_at=data.get("updated_at") or _utcnow_iso(),
        )

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.competency_id:
            errors.append("competency_id is required")
        if self.classification not in CLASSIFICATION_VALUES:
            errors.append(
                f"classification must be one of {sorted(CLASSIFICATION_VALUES)}, "
                f"got {self.classification!r}",
            )
        if self.confidence is not None and not (0.0 <= self.confidence <= 1.0):
            errors.append(f"confidence must be between 0.0 and 1.0, got {self.confidence!r}")
        errors.extend(self.provenance.validate())
        return errors


# ---------------------------------------------------------------------------
# The five record kinds (design doc Sec 4.2-4.6)
# ---------------------------------------------------------------------------


@dataclass
class CompetencyRecord:
    """`kind = "competency"` -- one record per competency (index/definition).
    `key()` is just the `competency_id` -- there is exactly one of these per
    competency, unlike the other four kinds."""

    KIND: ClassVar[str] = "competency"

    envelope: CompetencyEnvelope
    name: str
    status: str = "learning"  # "learning" | "active" | "dormant"
    description: str = ""
    relevant_capabilities: list[str] = field(default_factory=list)  # skill_ids
    proficiency: dict[str, Any] = field(default_factory=lambda: {"overall": 0.0, "by_area": {}})
    known_gaps: list[str] = field(default_factory=list)

    def key(self) -> str:
        return self.envelope.competency_id

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.envelope.to_dict(),
            "name": self.name,
            "status": self.status,
            "description": self.description,
            "relevant_capabilities": self.relevant_capabilities,
            "proficiency": self.proficiency,
            "known_gaps": self.known_gaps,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompetencyRecord:
        return cls(
            envelope=CompetencyEnvelope.from_dict(data),
            name=data["name"],
            status=data.get("status", "learning"),
            description=data.get("description", ""),
            relevant_capabilities=list(data.get("relevant_capabilities", [])),
            proficiency=dict(data.get("proficiency", {"overall": 0.0, "by_area": {}})),
            known_gaps=list(data.get("known_gaps", [])),
        )

    def validate(self) -> list[str]:
        errors = self.envelope.validate()
        if not self.name:
            errors.append("name is required")
        if self.status not in {"learning", "active", "dormant"}:
            errors.append(f"status must be one of learning/active/dormant, got {self.status!r}")
        return errors

    def to_summary_text(self) -> str:
        return f"{self.name}: {self.description}" if self.description else self.name


@dataclass
class CompetencyKnowledge:
    """`kind = "competency_knowledge"` -- domain knowledge."""

    KIND: ClassVar[str] = "competency_knowledge"

    envelope: CompetencyEnvelope
    slug: str
    topic: str
    content: str

    def key(self) -> str:
        return key_for(self.envelope.competency_id, self.slug)

    def to_dict(self) -> dict[str, Any]:
        return {**self.envelope.to_dict(), "topic": self.topic, "content": self.content}

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, slug: str) -> CompetencyKnowledge:
        return cls(
            envelope=CompetencyEnvelope.from_dict(data),
            slug=slug,
            topic=data.get("topic", ""),
            content=data["content"],
        )

    def validate(self) -> list[str]:
        errors = self.envelope.validate()
        if not self.slug:
            errors.append("slug is required")
        if not self.content:
            errors.append("content is required")
        return errors

    def to_summary_text(self) -> str:
        return f"{self.topic}: {self.content}" if self.topic else self.content


@dataclass
class CompetencyProcedure:
    """`kind = "competency_procedure"` -- a repeatable method/process.

    Kept distinct from `CompetencyHeuristic` (design doc Sec 4.4): a
    procedure is an ordered set of steps, a heuristic is a conditional
    rule-of-thumb. Revisit consolidating only on real implementation
    evidence that the distinction adds no value -- not decided here.
    """

    KIND: ClassVar[str] = "competency_procedure"

    envelope: CompetencyEnvelope
    slug: str
    name: str
    steps: list[str] = field(default_factory=list)
    when_to_use: str = ""
    capability_refs: list[str] = field(default_factory=list)  # skill_ids

    def key(self) -> str:
        return key_for(self.envelope.competency_id, self.slug)

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.envelope.to_dict(),
            "name": self.name,
            "steps": self.steps,
            "when_to_use": self.when_to_use,
            "capability_refs": self.capability_refs,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, slug: str) -> CompetencyProcedure:
        return cls(
            envelope=CompetencyEnvelope.from_dict(data),
            slug=slug,
            name=data["name"],
            steps=list(data.get("steps", [])),
            when_to_use=data.get("when_to_use", ""),
            capability_refs=list(data.get("capability_refs", [])),
        )

    def validate(self) -> list[str]:
        errors = self.envelope.validate()
        if not self.slug:
            errors.append("slug is required")
        if not self.name:
            errors.append("name is required")
        if not self.steps:
            errors.append("steps must not be empty")
        return errors

    def to_summary_text(self) -> str:
        steps_text = "; ".join(self.steps)
        return f"{self.name}: {steps_text}" if steps_text else self.name


@dataclass
class CompetencyHeuristic:
    """`kind = "competency_heuristic"` -- a learned rule-of-thumb."""

    KIND: ClassVar[str] = "competency_heuristic"

    envelope: CompetencyEnvelope
    slug: str
    rule: str
    conditions: str = ""
    counterexamples: list[str] = field(default_factory=list)

    def key(self) -> str:
        return key_for(self.envelope.competency_id, self.slug)

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.envelope.to_dict(),
            "rule": self.rule,
            "conditions": self.conditions,
            "counterexamples": self.counterexamples,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, slug: str) -> CompetencyHeuristic:
        return cls(
            envelope=CompetencyEnvelope.from_dict(data),
            slug=slug,
            rule=data["rule"],
            conditions=data.get("conditions", ""),
            counterexamples=list(data.get("counterexamples", [])),
        )

    def validate(self) -> list[str]:
        errors = self.envelope.validate()
        if not self.slug:
            errors.append("slug is required")
        if not self.rule:
            errors.append("rule is required")
        return errors

    def to_summary_text(self) -> str:
        return f"{self.rule} (when: {self.conditions})" if self.conditions else self.rule


@dataclass
class CompetencyEvidence:
    """`kind = "competency_evidence"` -- prior cases, successful
    interventions, mistakes/corrections, and observed outcomes (design doc
    Sec 4.6 groups these together, matching `ROADMAP.md`'s own Estate
    Management acceptance-test wording)."""

    KIND: ClassVar[str] = "competency_evidence"

    envelope: CompetencyEnvelope
    slug: str
    situation: str
    action_taken: str = ""
    outcome: str = ""
    judgement_was_correct: bool | None = None
    lesson: str = ""

    def key(self) -> str:
        return key_for(self.envelope.competency_id, self.slug)

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.envelope.to_dict(),
            "situation": self.situation,
            "action_taken": self.action_taken,
            "outcome": self.outcome,
            "judgement_was_correct": self.judgement_was_correct,
            "lesson": self.lesson,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, slug: str) -> CompetencyEvidence:
        return cls(
            envelope=CompetencyEnvelope.from_dict(data),
            slug=slug,
            situation=data["situation"],
            action_taken=data.get("action_taken", ""),
            outcome=data.get("outcome", ""),
            judgement_was_correct=data.get("judgement_was_correct"),
            lesson=data.get("lesson", ""),
        )

    def validate(self) -> list[str]:
        errors = self.envelope.validate()
        if not self.slug:
            errors.append("slug is required")
        if not self.situation:
            errors.append("situation is required")
        return errors

    def to_summary_text(self) -> str:
        parts = [self.situation]
        if self.outcome:
            parts.append(f"-> {self.outcome}")
        if self.lesson:
            parts.append(f"Lesson: {self.lesson}")
        return " ".join(parts)


COMPETENCY_KINDS: tuple[str, ...] = (
    CompetencyRecord.KIND,
    CompetencyKnowledge.KIND,
    CompetencyProcedure.KIND,
    CompetencyHeuristic.KIND,
    CompetencyEvidence.KIND,
)


# ---------------------------------------------------------------------------
# Structural schema registration (privacy_guard)
# ---------------------------------------------------------------------------
#
# These kinds serialise as JSON, so the raw stored value contains this
# module's own schema key names. `privacy_guard.is_sensitive()` scans the
# stored representation, which previously meant a competency record was
# flagged sensitive because the schema has a `"name"` key -- never because
# of anything a user wrote. Registering the schema keys tells the guard
# which names are structure rather than content; **every value is still
# scanned in full**, as is any key not listed here (e.g. the user-chosen
# area names inside `proficiency.by_area`).
#
# Derived from the dataclasses rather than hand-listed, so the registration
# cannot drift from the real serialised shape as the model evolves.
# `tests/test_privacy_guard_structural_scanning.py` additionally pins which
# of these keys collide with the sensitivity vocabulary, so a future schema
# change that added, say, a `password` field would fail that test rather
# than silently hide a signal.
#
# This is a registry write, not I/O: the module remains pure data, holds no
# connection, and still never writes a memory itself.


def _schema_keys() -> frozenset[str]:
    """Every key name the five kinds' `to_dict()` output can contain."""
    envelope = CompetencyEnvelope(competency_id="_")
    samples = [
        CompetencyRecord(envelope=envelope, name="_"),
        CompetencyKnowledge(envelope=envelope, slug="_", topic="_", content="_"),
        CompetencyProcedure(envelope=envelope, slug="_", name="_", steps=["_"]),
        CompetencyHeuristic(envelope=envelope, slug="_", rule="_"),
        CompetencyEvidence(envelope=envelope, slug="_", situation="_"),
    ]

    keys: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                keys.add(key)
                walk(value)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)

    for sample in samples:
        walk(sample.to_dict())
    return frozenset(keys)


COMPETENCY_SCHEMA_KEYS: frozenset[str] = _schema_keys()

for _kind in COMPETENCY_KINDS:
    register_structural_schema(_kind, COMPETENCY_SCHEMA_KEYS)
del _kind
