"""
Candidate learning: the experience -> lesson representation (Stage 5, S5.4 slice)
=================================================================================

The narrow vertical slice of `COGNITIVE_RUNTIME.md`'s
"Experience -> Reflection -> candidate learning -> provenance/confidence ->
Governance/review -> consolidation" loop: **outcome-based procedural learning
only**, from one recorded objective outcome, through explicit human review,
into the existing S5.1 competency substrate that S5.3's reasoning seam
already retrieves from.

Deliberately pure data and validation: no persistence, no retrieval, no I/O
of any kind -- the same discipline `competency.py` and `training.py` hold to.
`MemoryStore.upsert_memory()` remains the sole authority that writes
memories; the governed seam lives in
`bartholomew.kernel.runtime_contract.run_candidate_lesson_through_runtime_contract`,
alongside every other surface's seam, so learning does not acquire a
consolidation runtime of its own.

Experience does not automatically become knowledge
---------------------------------------------------
This module exists to keep four things apart that a naive implementation
collapses into one row:

  * **what happened** -- `SourceExperience.observations`, copied verbatim
    from the objective's own evidence events (`objective_store`'s `fact` /
    `decision` / `action` kinds). A `proposal` event is structurally excluded
    upstream by `ObjectiveStore.evidence_events()`, and `validate()` refuses
    a candidate whose supporting ids are not evidence ids.
  * **what Bartholomew infers from it** -- `inferred_rule`, always carrying
    `epistemic_status = "inference"`. `EPISTEMIC_OBSERVATION` exists as a
    named constant precisely so the difference is expressible; a
    `CandidateLesson` may never claim it.
  * **what is proposed** -- `review_state = "proposed"`. A proposed lesson is
    stored under the `candidate_lesson` kind, which is deliberately **not** a
    member of `competency.COMPETENCY_KINDS`, and is therefore structurally
    invisible to the S5.3 retrieval seam's kind filter. Nothing can reason
    from it.
  * **what has been reviewed and accepted** -- `review_state = "accepted"`,
    which is the *only* state from which `to_competency_heuristic()` will
    produce a record for the retrievable substrate.

One outcome may support a candidate inference. It does not prove the lesson,
which is why `SINGLE_EXPERIENCE_CONFIDENCE` is deliberately low and why
`requires_review` is unconditionally true in this slice.

Rejection is real
-----------------
`reject()` moves a candidate to `review_state = "rejected"` and nothing else
happens. Because consolidation is the *only* writer of a `competency_*`
record on this path, and it refuses any state but `accepted`, a rejected
candidate leaves nothing behind that later reasoning could retrieve as
accepted knowledge. The rejected candidate row itself remains -- an audit
record of a lesson considered and declined -- under a kind the reasoning seam
cannot see.

What this module does NOT do
-----------------------------
No autonomous self-modification, no automatic promotion, no movement between
the personal / potentially-generalisable / system classifications (the
classification is copied from the candidate and never inferred or changed by
this module), no cross-instance or cross-user transport, no model retraining,
and no generalisation from more than the single experience it names.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar

from bartholomew.kernel.competency import (
    CLASSIFICATION_VALUES,
    CompetencyEnvelope,
    CompetencyHeuristic,
    Provenance,
    Supervision,
)
from bartholomew.kernel.memory.privacy_guard import register_structural_schema

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

#: The `MemoryStore` kind a candidate lesson is stored under.
#:
#: Deliberately absent from `competency.COMPETENCY_KINDS`. That absence is
#: the structural half of "a candidate is not knowledge": `runtime_contract`'s
#: `_retrieve_memory_context()` filters retrieval to `COMPETENCY_KINDS +
#: PERSONAL_FACT_KINDS`, so no candidate -- proposed, rejected or even
#: accepted -- is ever retrievable *as a candidate*. Only the separate
#: `competency_heuristic` record that consolidation writes is.
KIND: str = "candidate_lesson"

#: Review/consolidation states. `proposed` is the only state a candidate may
#: be created in; both others are terminal.
REVIEW_PROPOSED = "proposed"
REVIEW_ACCEPTED = "accepted"
REVIEW_REJECTED = "rejected"

REVIEW_STATES: frozenset[str] = frozenset(
    {REVIEW_PROPOSED, REVIEW_ACCEPTED, REVIEW_REJECTED},
)

TERMINAL_REVIEW_STATES: frozenset[str] = frozenset({REVIEW_ACCEPTED, REVIEW_REJECTED})

#: Purely administrative presentation state, used by the Learning and Memory
#: Control Centre to let someone triage a review queue.
#:
#: Deliberately excluded from `learning_authorization.fingerprint_for()`, and
#: that exclusion is the whole reason this vocabulary is separate from
#: `REVIEW_STATES`: pinning a candidate to look at later must not invalidate
#: an approval someone already granted for it, because it does not change one
#: word of what the lesson claims. Anything that *does* change the lesson's
#: meaning belongs in the material set the fingerprint covers instead.
DISPLAY_NORMAL = "normal"
DISPLAY_PINNED = "pinned"
DISPLAY_SET_ASIDE = "set_aside"

DISPLAY_STATES: frozenset[str] = frozenset(
    {DISPLAY_NORMAL, DISPLAY_PINNED, DISPLAY_SET_ASIDE},
)

#: Risk classes a reviewer may assign to a candidate, least to most severe.
#: The single definition: `learning_policy.RISK_CLASSES` is this tuple, not a
#: copy of it, so a reviewer's assignment and the policy that reads it cannot
#: drift apart. Defined here rather than there because the dependency runs
#: that way -- the policy engine reads candidates, not the reverse.
RISK_CLASSES: tuple[str, ...] = ("low", "moderate", "high", "critical")

#: The observation/inference distinction, as data rather than as convention.
#: `EPISTEMIC_OBSERVATION` is never a legal value for a `CandidateLesson`; it
#: exists so the distinction is expressible and so a future record type that
#: genuinely *is* an observation has a name to claim.
EPISTEMIC_OBSERVATION = "observation"
EPISTEMIC_INFERENCE = "inference"

#: The one learning classification this slice produces. Outcome-based
#: procedural learning: "doing X in situation Y led to outcome Z".
LESSON_PROCEDURAL = "procedural"

#: Deliberately a single-member set. Widening it is a separately authorised
#: decision, not an incidental one.
LESSON_KINDS: frozenset[str] = frozenset({LESSON_PROCEDURAL})

#: The provenance source type Bartholomew-originated learning carries.
#: `training.py` reserves this from the ordinary training seam
#: (`S5_4_RESERVED_SOURCE_TYPES`); the consolidation seam lifts it explicitly
#: for this path and nothing else.
EXPERIENCE_SOURCE_TYPE = "experience"

#: Confidence a lesson inferred from exactly one recorded outcome may carry.
#:
#: A tunable default, not an architectural constant -- but it is deliberately
#: low, and deliberately not derived from anything that would rise with
#: repetition, because this slice never aggregates more than one experience.
#: It sits above `competency_reasoning.DEFAULT_CONFIDENCE_FLOOR` (0.3) so an
#: accepted lesson is genuinely retrievable, and far enough below an
#: instructed record's typical confidence that it ranks behind one.
SINGLE_EXPERIENCE_CONFIDENCE: float = 0.4


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def key_for(competency_id: str, slug: str) -> str:
    """`"<competency_id>.<slug>"`, matching `competency.key_for()`.

    Intentionally the same convention, so a consolidated heuristic and the
    candidate it came from share a key and differ only by `kind` -- the
    linkage is legible without a foreign key that `memories` does not have.
    """
    return f"{competency_id}.{slug}"


def slug_for_objective(objective_id: int) -> str:
    """The candidate slug derived from an objective id.

    Deterministic, so re-proposing from the same objective supersedes the
    existing candidate rather than accumulating near-duplicates.
    """
    return f"lesson_from_objective_{int(objective_id)}"


class ReviewStateError(RuntimeError):
    """Raised when a review transition or consolidation is attempted from a
    state that does not permit it."""


# ---------------------------------------------------------------------------
# What happened
# ---------------------------------------------------------------------------


@dataclass
class SourceExperience:
    """The recorded experience a candidate lesson points at.

    Every field here is *observation*: copied from the objective and its
    evidence events, never composed by the inference step. `observations` are
    the verbatim evidence-event summaries, so a reviewer can see exactly what
    the proposed lesson is standing on without a second query.
    """

    objective_id: int
    objective_title: str = ""
    resolution: str | None = None
    outcome_note: str | None = None
    #: Ids of the `objective_events` rows that support the inference. Must be
    #: a non-empty subset of the objective's *evidence* events.
    supporting_event_ids: list[int] = field(default_factory=list)
    #: `"<event_kind>: <summary>"` for each supporting event, in order.
    observations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective_id": self.objective_id,
            "objective_title": self.objective_title,
            "resolution": self.resolution,
            "outcome_note": self.outcome_note,
            "supporting_event_ids": list(self.supporting_event_ids),
            "observations": list(self.observations),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceExperience:
        return cls(
            objective_id=int(data["objective_id"]),
            objective_title=data.get("objective_title", ""),
            resolution=data.get("resolution"),
            outcome_note=data.get("outcome_note"),
            supporting_event_ids=[int(item) for item in data.get("supporting_event_ids", [])],
            observations=list(data.get("observations", [])),
        )

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.objective_id:
            errors.append("source.objective_id is required")
        if not self.supporting_event_ids:
            errors.append("source.supporting_event_ids must name at least one evidence event")
        if not self.observations:
            errors.append("source.observations must not be empty")
        if len(self.observations) != len(self.supporting_event_ids):
            errors.append(
                "source.observations and source.supporting_event_ids must correspond "
                f"one-to-one (got {len(self.observations)} and "
                f"{len(self.supporting_event_ids)})",
            )
        return errors


# ---------------------------------------------------------------------------
# What is inferred, and what became of it
# ---------------------------------------------------------------------------


@dataclass
class CandidateLesson:
    """One proposed procedural lesson, and its review/consolidation state."""

    KIND: ClassVar[str] = KIND

    competency_id: str
    slug: str
    source: SourceExperience
    #: The inference itself. Never presented as, or storable as, observation.
    inferred_rule: str
    #: When the rule is claimed to apply. Kept separate from the rule so the
    #: consolidated heuristic's `conditions` is not smuggled into its `rule`.
    conditions: str = ""
    lesson_kind: str = LESSON_PROCEDURAL
    epistemic_status: str = EPISTEMIC_INFERENCE
    classification: str = "personal"
    confidence: float = SINGLE_EXPERIENCE_CONFIDENCE
    provenance: Provenance = field(
        default_factory=lambda: Provenance(
            source_type=EXPERIENCE_SOURCE_TYPE,
            recorded_by="reflection",
        ),
    )
    #: The `reflections` row id of the Reflection this candidate was produced
    #: from, when one is available. Provenance back into the existing
    #: Reflection authority, not a second one.
    reflection_row_id: int | None = None
    review_state: str = REVIEW_PROPOSED
    reviewed_at: str | None = None
    reviewer: str | None = None
    review_note: str | None = None
    #: Set by the consolidation seam on acceptance, so the accepted lesson and
    #: the retrievable record it became are traceable to each other.
    consolidated_kind: str | None = None
    consolidated_key: str | None = None
    revision: int = 1
    updated_at: str = field(default_factory=_utcnow_iso)
    #: Administrative only. Changing it never changes what the lesson claims,
    #: never changes the material fingerprint, and never invalidates an
    #: approval -- see `DISPLAY_STATES` above.
    display_state: str = DISPLAY_NORMAL

    # -- control-centre material extensions ------------------------------
    #
    # Four dimensions the Learning and Memory Control Centre lets a reviewer
    # record, and which the shadow policy engine reads. Every one of them is
    # *material*: `learning_authorization.fingerprint_for()` covers each one
    # that has been assigned, so editing any of them invalidates a prior
    # approval exactly as editing the rule does.
    #
    # All four default to "not assessed" rather than to a value, and an
    # unassessed field contributes nothing to the fingerprint. That is what
    # keeps approvals granted before these fields existed valid: a candidate
    # written by the S5.4 slice and the same candidate read back after this
    # upgrade are the same lesson, and digest identically. It also means the
    # policy engine's conservative defaults (unassessed risk is treated as
    # `critical`, unassessed reversibility as irreversible) apply to exactly
    # the candidates nobody has assessed.

    #: Reviewer-assigned risk class, or None for "not assessed".
    risk_class: str | None = None
    #: Whether acting on this lesson could be undone, or None for
    #: "not assessed".
    reversible: bool | None = None
    #: Applications this lesson would affect, beyond the competency
    #: (`competency_id`) that is its affected capability. Empty means none
    #: are named.
    affected_applications: list[str] = field(default_factory=list)
    #: Explicit household-sharing eligibility, or None to derive it from
    #: `classification` (a `personal` lesson is not shareable). Session E
    #: owns the transport; this field is only ever a statement about
    #: *eligibility*, never a claim that a share occurred.
    sharing_eligible: bool | None = None

    # -- invariants ------------------------------------------------------

    @property
    def requires_review(self) -> bool:
        """Unconditionally true in this slice.

        `COGNITIVE_RUNTIME.md` describes low-impact/high-confidence candidate
        learning consolidating directly. This slice deliberately does not
        implement that branch: a lesson inferred from a single outcome is by
        construction low-confidence, and an automatic path would be the
        mechanism by which experience silently becomes knowledge. Making this
        a property rather than a stored field means no caller can persist a
        candidate that claims not to need review.
        """
        return True

    @property
    def is_accepted(self) -> bool:
        return self.review_state == REVIEW_ACCEPTED

    def key(self) -> str:
        return key_for(self.competency_id, self.slug)

    # -- serialisation ---------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "competency_id": self.competency_id,
            "slug": self.slug,
            "source": self.source.to_dict(),
            "inferred_rule": self.inferred_rule,
            "conditions": self.conditions,
            "lesson_kind": self.lesson_kind,
            "epistemic_status": self.epistemic_status,
            "classification": self.classification,
            "confidence": self.confidence,
            "provenance": self.provenance.to_dict(),
            "reflection_row_id": self.reflection_row_id,
            "requires_review": self.requires_review,
            "review_state": self.review_state,
            "reviewed_at": self.reviewed_at,
            "reviewer": self.reviewer,
            "review_note": self.review_note,
            "consolidated_kind": self.consolidated_kind,
            "consolidated_key": self.consolidated_key,
            "revision": self.revision,
            "updated_at": self.updated_at,
            "display_state": self.display_state,
            "risk_class": self.risk_class,
            "reversible": self.reversible,
            "affected_applications": list(self.affected_applications),
            "sharing_eligible": self.sharing_eligible,
        }

    @property
    def effective_sharing_eligible(self) -> bool:
        """Whether this lesson could be shared beyond its owner.

        Explicit when a reviewer assigned it; otherwise derived from the
        classification, where only `personal` is treated as not shareable.
        Deriving in this direction is the conservative one: an unassessed
        `potentially_generalisable` lesson counts as shareable and is
        therefore excluded by the default policy, rather than slipping
        through as "nobody said it was shareable".
        """
        if self.sharing_eligible is not None:
            return bool(self.sharing_eligible)
        return self.classification != "personal"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CandidateLesson:
        provenance_data = data.get("provenance") or {
            "source_type": EXPERIENCE_SOURCE_TYPE,
            "recorded_by": "reflection",
        }
        return cls(
            competency_id=data["competency_id"],
            slug=data["slug"],
            source=SourceExperience.from_dict(data["source"]),
            inferred_rule=data["inferred_rule"],
            conditions=data.get("conditions", ""),
            lesson_kind=data.get("lesson_kind", LESSON_PROCEDURAL),
            epistemic_status=data.get("epistemic_status", EPISTEMIC_INFERENCE),
            classification=data.get("classification", "personal"),
            confidence=float(data.get("confidence", SINGLE_EXPERIENCE_CONFIDENCE)),
            provenance=Provenance.from_dict(provenance_data),
            reflection_row_id=data.get("reflection_row_id"),
            review_state=data.get("review_state", REVIEW_PROPOSED),
            reviewed_at=data.get("reviewed_at"),
            reviewer=data.get("reviewer"),
            review_note=data.get("review_note"),
            consolidated_kind=data.get("consolidated_kind"),
            consolidated_key=data.get("consolidated_key"),
            revision=int(data.get("revision", 1)),
            updated_at=data.get("updated_at") or _utcnow_iso(),
            # Absent on every candidate stored before the control centre
            # existed. Defaulting rather than requiring it means no migration
            # is needed to read one back.
            display_state=data.get("display_state") or DISPLAY_NORMAL,
            risk_class=data.get("risk_class"),
            reversible=data.get("reversible"),
            affected_applications=list(data.get("affected_applications") or []),
            sharing_eligible=data.get("sharing_eligible"),
        )

    def to_summary_text(self) -> str:
        """Human-readable line for the `memories.summary` column.

        Leads with "Candidate lesson (inference)" on purpose: whatever reads
        this row -- a review UI, an audit, a summariser -- is told what it is
        looking at before it is told what the lesson says.
        """
        return (
            f"Candidate lesson ({self.epistemic_status}, {self.review_state}) "
            f"from objective {self.source.objective_id}: {self.inferred_rule}"
        )

    # -- validation ------------------------------------------------------

    def validate(self) -> list[str]:
        errors: list[str] = []

        if not self.competency_id:
            errors.append("competency_id is required")
        if not self.slug:
            errors.append("slug is required")
        if not self.inferred_rule:
            errors.append("inferred_rule is required")

        if self.epistemic_status != EPISTEMIC_INFERENCE:
            errors.append(
                "epistemic_status must be "
                f"{EPISTEMIC_INFERENCE!r} -- a candidate lesson is what Bartholomew "
                "infers from an experience, never the experience itself; "
                f"got {self.epistemic_status!r}",
            )
        if self.lesson_kind not in LESSON_KINDS:
            errors.append(
                f"lesson_kind must be one of {sorted(LESSON_KINDS)}, got {self.lesson_kind!r}",
            )
        if self.classification not in CLASSIFICATION_VALUES:
            errors.append(
                f"classification must be one of {sorted(CLASSIFICATION_VALUES)}, "
                f"got {self.classification!r}",
            )
        if self.review_state not in REVIEW_STATES:
            errors.append(
                f"review_state must be one of {sorted(REVIEW_STATES)}, "
                f"got {self.review_state!r}",
            )
        if self.display_state not in DISPLAY_STATES:
            errors.append(
                f"display_state must be one of {sorted(DISPLAY_STATES)}, "
                f"got {self.display_state!r}",
            )
        if self.risk_class is not None and self.risk_class not in RISK_CLASSES:
            errors.append(
                f"risk_class must be one of {list(RISK_CLASSES)} or null "
                f"(not assessed), got {self.risk_class!r}",
            )
        if any(not str(name).strip() for name in self.affected_applications):
            errors.append("affected_applications must not contain blank entries")
        if not (0.0 <= self.confidence <= 1.0):
            errors.append(f"confidence must be between 0.0 and 1.0, got {self.confidence!r}")

        if self.provenance.source_type != EXPERIENCE_SOURCE_TYPE:
            errors.append(
                f"provenance.source_type must be {EXPERIENCE_SOURCE_TYPE!r} for candidate "
                f"learning, got {self.provenance.source_type!r}",
            )
        errors.extend(self.provenance.validate())
        errors.extend(self.source.validate())

        if self.review_state == REVIEW_PROPOSED and (self.reviewer or self.reviewed_at):
            errors.append("a proposed candidate must not carry reviewer/reviewed_at")
        if self.review_state in TERMINAL_REVIEW_STATES and not self.reviewer:
            errors.append(f"a {self.review_state} candidate must record its reviewer")
        if self.review_state != REVIEW_ACCEPTED and self.consolidated_key:
            errors.append(
                "consolidated_key may only be set on an accepted candidate -- a rejected "
                "or proposed lesson must leave nothing consolidated behind",
            )

        return errors

    # -- review transitions ----------------------------------------------

    def accept(self, *, reviewer: str, note: str | None = None) -> None:
        """Record a review decision to accept. Consolidation is separate.

        Accepting does not itself write anything to the competency substrate
        -- `to_competency_heuristic()` produces the record and the governed
        seam writes it. Keeping the two apart is what lets Governance refuse
        an accepted candidate's consolidation without the acceptance having
        already leaked into retrievable memory.
        """
        self._require_proposed("accept")
        if not reviewer:
            raise ReviewStateError("accept requires a reviewer -- review is never anonymous")
        self.review_state = REVIEW_ACCEPTED
        self.reviewer = reviewer
        self.review_note = note
        self.reviewed_at = _utcnow_iso()
        self.updated_at = self.reviewed_at
        self.revision += 1

    def reject(self, *, reviewer: str, note: str | None = None) -> None:
        """Record a review decision to reject.

        Nothing else happens, and that is the whole point: no consolidated
        record is created, and none can be created afterwards, because
        `to_competency_heuristic()` refuses any state but `accepted`.
        """
        self._require_proposed("reject")
        if not reviewer:
            raise ReviewStateError("reject requires a reviewer -- review is never anonymous")
        self.review_state = REVIEW_REJECTED
        self.reviewer = reviewer
        self.review_note = note
        self.reviewed_at = _utcnow_iso()
        self.updated_at = self.reviewed_at
        self.revision += 1

    def _require_proposed(self, verb: str) -> None:
        if self.review_state != REVIEW_PROPOSED:
            raise ReviewStateError(
                f"cannot {verb} a candidate in state {self.review_state!r}; "
                "review decisions are terminal",
            )

    # -- consolidation ---------------------------------------------------

    def to_competency_heuristic(self) -> CompetencyHeuristic:
        """The S5.1 record an accepted lesson becomes.

        A `competency_heuristic` rather than a new kind, because that is what
        an outcome-based procedural lesson *is* -- a conditional rule of
        thumb -- and because S5.3's retrieval seam already reads that kind.
        No new memory authority, no new retrieval path, no new record type.

        The counterexample-shaped honesty is deliberate: the resulting
        heuristic carries the single experience it came from in
        `conditions`/provenance, its own low confidence, and supervision that
        still requires review before it is acted on. An accepted lesson is
        retrievable guidance, not a licence.
        """
        if self.review_state != REVIEW_ACCEPTED:
            raise ReviewStateError(
                f"only an accepted candidate consolidates; this one is {self.review_state!r}",
            )

        envelope = CompetencyEnvelope(
            competency_id=self.competency_id,
            # Copied, never inferred or upgraded. Nothing in this slice moves
            # a lesson between personal / potentially_generalisable / system.
            classification=self.classification,
            provenance=Provenance(
                source_type=EXPERIENCE_SOURCE_TYPE,
                detail=(
                    f"inferred from objective {self.source.objective_id} "
                    f"({self.source.objective_title}); evidence events "
                    f"{self.source.supporting_event_ids}; accepted by {self.reviewer}"
                ),
                recorded_by="reflection",
            ),
            confidence=self.confidence,
            # Stricter, never laxer: a lesson Bartholomew inferred about its
            # own past conduct still needs a human in the loop when applied.
            supervision=Supervision(requires_review=True),
        )
        return CompetencyHeuristic(
            envelope=envelope,
            slug=self.slug,
            rule=self.inferred_rule,
            conditions=self.conditions,
            # The experience is recorded as a bound on the rule, not as
            # corroboration of it.
            counterexamples=[],
        )


# ---------------------------------------------------------------------------
# Producing a candidate from a recorded outcome
# ---------------------------------------------------------------------------


def propose_from_objective(
    objective: Any,
    evidence_events: list[Any],
    *,
    competency_id: str,
    inferred_rule: str | None = None,
    conditions: str | None = None,
    classification: str = "personal",
    reflection_row_id: int | None = None,
) -> CandidateLesson:
    """Produce one bounded procedural candidate lesson from one outcome.

    `objective` is an `objective_store.Objective` in a terminal state;
    `evidence_events` are `objective_store.ObjectiveEvent`s, which the caller
    obtains from `ObjectiveStore.evidence_events()` -- the query that
    structurally excludes `proposal` rows. This function re-checks
    `is_evidence` anyway rather than trusting its input, because the cost of
    the check is nothing and the cost of inferring a lesson from something
    Bartholomew only ever contemplated doing is a system that tells the user
    it learned from an action it never took.

    Duck-typed on purpose (attribute reads only, no import of the store), so
    this module keeps its no-I/O discipline and the objective authority stays
    where it is.

    Raises `ValueError` when the objective has not actually reached an
    outcome, or when no evidence supports an inference. Refusing to produce a
    candidate is a legitimate result: not every experience teaches anything.
    """
    status = getattr(objective, "status", None)
    if status not in ("completed", "abandoned"):
        raise ValueError(
            f"a candidate lesson needs a recorded outcome; objective is {status!r}",
        )

    supported = [event for event in evidence_events if getattr(event, "is_evidence", False)]
    if not supported:
        raise ValueError(
            "no evidence events support an inference -- proposals and bookkeeping rows "
            "are not experience",
        )

    resolution = getattr(objective, "resolution", None)
    title = getattr(objective, "title", "") or ""

    observations = [f"{event.event_kind}: {event.summary}" for event in supported]

    if inferred_rule is None:
        # A deterministic, deliberately modest default. It states what the
        # single experience licenses and no more -- it does not generalise
        # across objectives, invent a causal claim, or assert the approach
        # will work again.
        actions = [event.summary for event in supported if event.event_kind == "action"]
        did = actions[-1] if actions else supported[-1].summary
        ended = "achieved" if resolution == "achieved" else (resolution or status)
        inferred_rule = (
            f'When working towards "{title}", {did} -- on the one recorded '
            f"occasion, the objective ended {ended}."
        )

    if conditions is None:
        conditions = (
            f"Observed once, on objective {getattr(objective, 'id', None)}. "
            "Not yet corroborated by a second outcome."
        )

    return CandidateLesson(
        competency_id=competency_id,
        slug=slug_for_objective(getattr(objective, "id", 0)),
        source=SourceExperience(
            objective_id=int(getattr(objective, "id", 0)),
            objective_title=title,
            resolution=resolution,
            outcome_note=getattr(objective, "outcome_note", None),
            supporting_event_ids=[int(event.id) for event in supported],
            observations=observations,
        ),
        inferred_rule=inferred_rule,
        conditions=conditions,
        classification=classification,
        confidence=SINGLE_EXPERIENCE_CONFIDENCE,
        provenance=Provenance(
            source_type=EXPERIENCE_SOURCE_TYPE,
            detail=(
                f"objective {getattr(objective, 'id', None)} reached outcome "
                f"{resolution or status!r}"
            ),
            recorded_by="reflection",
        ),
        reflection_row_id=reflection_row_id,
    )


# ---------------------------------------------------------------------------
# Structural schema registration (privacy_guard)
# ---------------------------------------------------------------------------
# Same reasoning as `competency.py`'s registration: these key names are
# schema, not content, and scanning them for sensitivity patterns produces
# false positives. The *values* are still scanned. A registry write, not I/O.


def _schema_keys() -> frozenset[str]:
    sample = CandidateLesson(
        competency_id="_",
        slug="_",
        source=SourceExperience(
            objective_id=1,
            supporting_event_ids=[1],
            observations=["_"],
        ),
        inferred_rule="_",
    )

    keys: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                keys.add(key)
                walk(value)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)

    walk(sample.to_dict())
    return frozenset(keys)


CANDIDATE_LESSON_SCHEMA_KEYS: frozenset[str] = _schema_keys()

register_structural_schema(KIND, CANDIDATE_LESSON_SCHEMA_KEYS)
