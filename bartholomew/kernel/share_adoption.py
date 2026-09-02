"""
What a recipient does with something a trusted group shared.

Adoption is the boundary between "somebody I trust sent me this" and "my
Bartholomew believes this". This module is that boundary, and its whole shape
is designed so the second never follows from the first automatically.

Pure data and validation -- no persistence, no retrieval, no I/O -- like
`competency.py`, `training.py`, `candidate_learning.py` and
`trusted_share.py`. The governed seam that writes an adopted candidate, and
the one that consolidates an accepted one, live in
`bartholomew.kernel.runtime_contract` alongside every other surface's seam.

Adoption produces a candidate, and only a candidate
---------------------------------------------------
`KIND` is `adopted_share_candidate`, and -- exactly like
`candidate_learning.KIND` -- it is deliberately **absent from
`competency.COMPETENCY_KINDS`**. That absence is structural, not a
convention: `runtime_contract._retrieve_memory_context()` filters retrieval
to `COMPETENCY_KINDS + PERSONAL_FACT_KINDS`, so an adopted candidate is
invisible to reasoning no matter what state it is in. Something a housemate
shared cannot become an answer Bartholomew gives until its recipient has
approved it, in their own runtime, under their own governance.

Acceptance is PR #83's approval, not a second one
--------------------------------------------------
An adopted candidate is deliberately *lesson-shaped*: it exposes the same
material attributes `learning_authorization.fingerprint_for()` reads, so the
authorization that gates `learning_accept` binds to it verbatim. There is no
parallel approval type, no "sharing enabled" switch, and no way to accept an
adopted candidate that does not go through
`runtime_contract.evaluate_learning_admission()` -- the same function, with
the same Parking-Brake-first ordering, that governs a lesson from the
recipient's own experience.

`lesson_kind` is `adopted_share`, which is part of the fingerprint material,
so an approval granted for a locally inferred lesson can never authorise an
adopted one or the reverse even if their keys somehow collided.

Revisions never overwrite
-------------------------
A candidate's slug carries the share revision it came from. Adopting revision
2 of a share therefore writes a *different key* from revision 1: a publisher
update is structurally incapable of overwriting what a recipient already
adopted, accepted or customised, because it is not writing to the same row.
`local_fork` records that the recipient changed their copy, so an update
arriving afterwards is presented as a proposal against a fork rather than as
a correction to it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar

from bartholomew.kernel.competency import (
    CLASSIFICATION_VALUES,
    CompetencyEnvelope,
    CompetencyHeuristic,
    CompetencyKnowledge,
    CompetencyProcedure,
    Provenance,
    Supervision,
)
from bartholomew.kernel.memory.privacy_guard import register_structural_schema
from bartholomew.kernel.trusted_share import (
    KIND_CORRECTION,
    KIND_GUIDANCE,
    KIND_HOUSEHOLD_ROUTINE,
    TrustedSharePackage,
)
from bartholomew.kernel.trusted_share import (
    fingerprint as _content_fingerprint,
)

#: The `MemoryStore` kind an adopted candidate is stored under.
#:
#: Absent from `competency.COMPETENCY_KINDS`, and that absence is the
#: structural half of "adoption is not acceptance": the retrieval seam's kind
#: filter cannot see this kind in any review state.
KIND: str = "adopted_share_candidate"

#: The provenance source type an adopted share carries into the competency
#: substrate once accepted. A distinct value on purpose: recording someone
#: else's shared rule as `user_instruction` would claim the recipient said
#: it, and as `experience` would claim Bartholomew observed it. Neither is
#: true, and provenance that is nearly true is the kind that misleads later.
TRUSTED_SHARE_SOURCE_TYPE = "trusted_share"

#: The `lesson_kind` an adopted candidate carries. Part of the approval
#: fingerprint material, so it also keeps the two candidate families'
#: approvals from ever being interchangeable.
LESSON_ADOPTED_SHARE = "adopted_share"

REVIEW_PROPOSED = "proposed"
REVIEW_ACCEPTED = "accepted"
REVIEW_REJECTED = "rejected"

REVIEW_STATES: frozenset[str] = frozenset({REVIEW_PROPOSED, REVIEW_ACCEPTED, REVIEW_REJECTED})
TERMINAL_REVIEW_STATES: frozenset[str] = frozenset({REVIEW_ACCEPTED, REVIEW_REJECTED})

#: Confidence an adopted share may carry.
#:
#: Deliberately lower than `candidate_learning.SINGLE_EXPERIENCE_CONFIDENCE`
#: (0.4): a rule the recipient's own Bartholomew watched play out once is
#: better evidence than a rule someone else wrote down, however much the
#: recipient trusts them. It stays above
#: `competency_reasoning.DEFAULT_CONFIDENCE_FLOOR` (0.3) so an accepted share
#: is genuinely retrievable rather than accepted-and-inert.
ADOPTED_SHARE_CONFIDENCE: float = 0.35


class AdoptionStateError(RuntimeError):
    """A review transition or consolidation was attempted from a state that
    does not permit it."""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slug_for_share(share_id: str, revision: int) -> str:
    """The candidate slug for one share at one revision.

    The revision is in the slug on purpose. It is what makes "a publisher
    update does not overwrite a recipient's adopted version" a property of
    the key space rather than a rule some later write path has to remember.
    """
    return f"adopted_share_{share_id}_r{int(revision)}"


def key_for(competency_id: str, slug: str) -> str:
    """`"<competency_id>.<slug>"`, matching `competency.key_for()`."""
    return f"{competency_id}.{slug}"


# ---------------------------------------------------------------------------
# Where it came from
# ---------------------------------------------------------------------------


@dataclass
class AdoptedShareOrigin:
    """The trusted-group provenance of one adopted candidate.

    Accounts, group, revision and hashes -- the things an audit needs -- and
    no shared source content beyond the digest. `source_candidate_fingerprint`
    is the publisher's origin digest: it binds the package to the record it
    was cut from without being a pointer anyone but the publisher can follow.

    `revoked_at` is carried here rather than only on the exchange, so a
    recipient reading their own local record can see that the publisher has
    withdrawn it. That is what "revocation remains visibly attached to
    provenance" means in practice; it is not a licence for the publisher to
    delete the record.
    """

    share_id: str
    group_id: str
    publisher_user_id: str
    share_revision: int
    content_hash: str
    source_candidate_fingerprint: str
    share_kind: str
    sanitization_policy_revision: int
    adopted_at: str = field(default_factory=_utcnow_iso)
    revoked_at: str | None = None

    #: Present so an adopted candidate reads through the same accessors as a
    #: locally inferred one -- see `learning_authorization.fingerprint_for`,
    #: which the acceptance approval binds through. There is no objective
    #: behind a share and inventing one would be a provenance lie, so this is
    #: honestly `None`.
    objective_id: int | None = None
    supporting_event_ids: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "share_id": self.share_id,
            "group_id": self.group_id,
            "publisher_user_id": self.publisher_user_id,
            "share_revision": self.share_revision,
            "content_hash": self.content_hash,
            "source_candidate_fingerprint": self.source_candidate_fingerprint,
            "share_kind": self.share_kind,
            "sanitization_policy_revision": self.sanitization_policy_revision,
            "adopted_at": self.adopted_at,
            "revoked_at": self.revoked_at,
            "objective_id": self.objective_id,
            "supporting_event_ids": list(self.supporting_event_ids),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AdoptedShareOrigin:
        return cls(
            share_id=data["share_id"],
            group_id=data["group_id"],
            publisher_user_id=data["publisher_user_id"],
            share_revision=int(data["share_revision"]),
            content_hash=data["content_hash"],
            source_candidate_fingerprint=data["source_candidate_fingerprint"],
            share_kind=data["share_kind"],
            sanitization_policy_revision=int(data.get("sanitization_policy_revision", 1)),
            adopted_at=data.get("adopted_at") or _utcnow_iso(),
            revoked_at=data.get("revoked_at"),
            objective_id=data.get("objective_id"),
            supporting_event_ids=[int(i) for i in data.get("supporting_event_ids", [])],
        )

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.share_id:
            errors.append("origin.share_id is required")
        if not self.group_id:
            errors.append("origin.group_id is required")
        if not self.publisher_user_id:
            errors.append("origin.publisher_user_id is required")
        if not self.content_hash:
            errors.append("origin.content_hash is required")
        if not self.source_candidate_fingerprint:
            errors.append("origin.source_candidate_fingerprint is required")
        if self.share_revision < 1:
            errors.append("origin.share_revision starts at 1")
        if self.objective_id is not None:
            errors.append(
                "an adopted share has no originating objective; a non-null objective_id "
                "would claim local experience the recipient never had",
            )
        return errors


# ---------------------------------------------------------------------------
# The candidate
# ---------------------------------------------------------------------------


@dataclass
class AdoptedShareCandidate:
    """One shared package the recipient has taken for local consideration.

    Lesson-shaped by design: `competency_id`, `slug`, `inferred_rule`,
    `conditions`, `lesson_kind`, `epistemic_status`, `classification`,
    `confidence` and `source` are the exact attributes
    `learning_authorization.fingerprint_for()` reads, so PR #83's
    candidate-bound approval binds to an adopted candidate without a second
    approval type existing anywhere.

    `epistemic_status` is `"inference"` and can be nothing else. Whatever the
    publisher observed, the recipient did not: from this Bartholomew's point
    of view a shared rule is a proposition it has been handed, never
    something it saw.
    """

    KIND: ClassVar[str] = KIND

    competency_id: str
    slug: str
    source: AdoptedShareOrigin
    #: The shared substance, as the recipient will read it.
    inferred_rule: str
    conditions: str = ""
    #: The sanitized package content, verbatim, so acceptance builds the
    #: local record from what was actually inspected rather than from a
    #: re-rendered summary of it.
    content: dict[str, Any] = field(default_factory=dict)
    lesson_kind: str = LESSON_ADOPTED_SHARE
    epistemic_status: str = "inference"
    classification: str = "personal"
    confidence: float = ADOPTED_SHARE_CONFIDENCE
    provenance: Provenance = field(
        default_factory=lambda: Provenance(
            source_type=TRUSTED_SHARE_SOURCE_TYPE,
            recorded_by="user",
        ),
    )
    review_state: str = REVIEW_PROPOSED
    reviewed_at: str | None = None
    reviewer: str | None = None
    review_note: str | None = None
    #: True once the recipient has edited their copy. A fork is never
    #: overwritten by an upstream revision -- upstream revisions arrive as
    #: separate candidates at separate keys regardless, so this is what makes
    #: the divergence *legible* rather than what prevents the overwrite.
    local_fork: bool = False
    consolidated_kind: str | None = None
    consolidated_key: str | None = None
    revision: int = 1
    updated_at: str = field(default_factory=_utcnow_iso)

    # -- invariants ------------------------------------------------------

    @property
    def requires_review(self) -> bool:
        """Unconditionally true.

        A property rather than a stored field so that no caller can persist
        an adopted candidate claiming not to need review. There is no
        high-confidence auto-adoption branch, no group-wide acceptance, and
        no confidence threshold above which a share consolidates itself --
        those are the mechanisms by which someone else's opinion would
        quietly become this Bartholomew's knowledge.
        """
        return True

    @property
    def is_accepted(self) -> bool:
        return self.review_state == REVIEW_ACCEPTED

    @property
    def is_revoked_upstream(self) -> bool:
        return self.source.revoked_at is not None

    def key(self) -> str:
        return key_for(self.competency_id, self.slug)

    def content_fingerprint(self) -> str:
        return _content_fingerprint(self.content)

    # -- serialisation ---------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "competency_id": self.competency_id,
            "slug": self.slug,
            "source": self.source.to_dict(),
            "inferred_rule": self.inferred_rule,
            "conditions": self.conditions,
            "content": self.content,
            "lesson_kind": self.lesson_kind,
            "epistemic_status": self.epistemic_status,
            "classification": self.classification,
            "confidence": self.confidence,
            "provenance": self.provenance.to_dict(),
            "requires_review": self.requires_review,
            "review_state": self.review_state,
            "reviewed_at": self.reviewed_at,
            "reviewer": self.reviewer,
            "review_note": self.review_note,
            "local_fork": self.local_fork,
            "consolidated_kind": self.consolidated_kind,
            "consolidated_key": self.consolidated_key,
            "revision": self.revision,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AdoptedShareCandidate:
        provenance_data = data.get("provenance") or {
            "source_type": TRUSTED_SHARE_SOURCE_TYPE,
            "recorded_by": "user",
        }
        return cls(
            competency_id=data["competency_id"],
            slug=data["slug"],
            source=AdoptedShareOrigin.from_dict(data["source"]),
            inferred_rule=data["inferred_rule"],
            conditions=data.get("conditions", ""),
            content=dict(data.get("content") or {}),
            lesson_kind=data.get("lesson_kind", LESSON_ADOPTED_SHARE),
            epistemic_status=data.get("epistemic_status", "inference"),
            classification=data.get("classification", "personal"),
            confidence=float(data.get("confidence", ADOPTED_SHARE_CONFIDENCE)),
            provenance=Provenance.from_dict(provenance_data),
            review_state=data.get("review_state", REVIEW_PROPOSED),
            reviewed_at=data.get("reviewed_at"),
            reviewer=data.get("reviewer"),
            review_note=data.get("review_note"),
            local_fork=bool(data.get("local_fork", False)),
            consolidated_kind=data.get("consolidated_kind"),
            consolidated_key=data.get("consolidated_key"),
            revision=int(data.get("revision", 1)),
            updated_at=data.get("updated_at") or _utcnow_iso(),
        )

    def to_summary_text(self) -> str:
        """The line that lands in `memories.summary`.

        Leads with what the row *is* before what it says, so anything reading
        it -- a review surface, an audit, a summariser -- is told it is
        looking at an unaccepted proposal from elsewhere first.
        """
        withdrawn = " [withdrawn upstream]" if self.is_revoked_upstream else ""
        return (
            f"Adopted trusted-group share ({self.source.share_kind}, {self.review_state}) "
            f"from group {self.source.group_id} rev {self.source.share_revision}"
            f"{withdrawn}: {self.inferred_rule}"
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
        if not self.content:
            errors.append("content is required -- an adopted candidate carries what it adopted")

        if self.epistemic_status != "inference":
            errors.append(
                "epistemic_status must be 'inference' -- an adopted share is a "
                "proposition this Bartholomew was handed, never something it observed; "
                f"got {self.epistemic_status!r}",
            )
        if self.lesson_kind != LESSON_ADOPTED_SHARE:
            errors.append(
                f"lesson_kind must be {LESSON_ADOPTED_SHARE!r}, got {self.lesson_kind!r}",
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
        if not (0.0 <= self.confidence <= 1.0):
            errors.append(f"confidence must be between 0.0 and 1.0, got {self.confidence!r}")
        if self.provenance.source_type != TRUSTED_SHARE_SOURCE_TYPE:
            errors.append(
                f"provenance.source_type must be {TRUSTED_SHARE_SOURCE_TYPE!r} for an "
                f"adopted share, got {self.provenance.source_type!r}",
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
                "or proposed share must leave nothing consolidated behind",
            )
        return errors

    # -- review transitions ----------------------------------------------

    def accept(self, *, reviewer: str, note: str | None = None) -> None:
        """Record the recipient's decision to accept. Consolidation is separate.

        Accepting writes nothing into the retrievable substrate on its own;
        `to_competency_record()` produces the record and the governed seam
        writes it. Keeping the two apart is what lets Governance refuse the
        consolidation of an accepted share without the acceptance having
        already leaked into memory.
        """
        self._require_proposed("accept")
        if not reviewer:
            raise AdoptionStateError("accept requires a reviewer -- review is never anonymous")
        if self.is_revoked_upstream:
            raise AdoptionStateError(
                "this share was withdrawn by its publisher; it cannot be accepted. "
                "Anything previously adopted from it remains yours and is untouched.",
            )
        self.review_state = REVIEW_ACCEPTED
        self.reviewer = reviewer
        self.review_note = note
        self.reviewed_at = _utcnow_iso()
        self.updated_at = self.reviewed_at
        self.revision += 1

    def reject(self, *, reviewer: str, note: str | None = None) -> None:
        """Record the recipient's decision to reject. Nothing consolidates, ever."""
        self._require_proposed("reject")
        if not reviewer:
            raise AdoptionStateError("reject requires a reviewer -- review is never anonymous")
        self.review_state = REVIEW_REJECTED
        self.reviewer = reviewer
        self.review_note = note
        self.reviewed_at = _utcnow_iso()
        self.updated_at = self.reviewed_at
        self.revision += 1

    def customise(self, *, rule: str | None = None, conditions: str | None = None) -> None:
        """Edit the local copy, making it a fork.

        Editing changes the candidate's material content, which changes the
        fingerprint an acceptance approval binds to -- so a customised
        candidate needs approving again for what it now says. That is the
        intended consequence, not a side effect: the reviewer approved the
        old text.
        """
        self._require_proposed("customise")
        if rule is not None:
            self.inferred_rule = rule
        if conditions is not None:
            self.conditions = conditions
        self.local_fork = True
        self.updated_at = _utcnow_iso()
        self.revision += 1

    def mark_upstream_revoked(self, revoked_at: str) -> None:
        """Record that the publisher has withdrawn the share.

        Visible, and nothing more. It does not delete the candidate, does not
        un-consolidate anything already accepted, and does not change the
        review state -- a recipient's independently adopted record is theirs.
        """
        self.source.revoked_at = revoked_at
        self.updated_at = _utcnow_iso()

    def _require_proposed(self, verb: str) -> None:
        if self.review_state != REVIEW_PROPOSED:
            raise AdoptionStateError(
                f"cannot {verb} a candidate in state {self.review_state!r}; "
                "review decisions are terminal",
            )

    # -- consolidation ---------------------------------------------------

    def to_competency_record(self) -> Any:
        """The S5.1 record an accepted adoption becomes.

        Shaped from the sanitized content the recipient actually inspected:
        a routine with steps becomes a `CompetencyProcedure`, a rule becomes a
        `CompetencyHeuristic`, and topical material becomes
        `CompetencyKnowledge`. No new record type and no new retrieval path.

        Provenance names the group, the publisher account, the share and the
        revision -- enough for an audit to answer "where did this come from?"
        -- and carries none of the publisher's own free-text provenance, which
        the sanitizer removed before the package was ever published.

        Supervision is stricter, never laxer: a rule this Bartholomew took on
        another person's word still wants a human in the loop when applied.
        """
        if self.review_state != REVIEW_ACCEPTED:
            raise AdoptionStateError(
                f"only an accepted adoption consolidates; this one is {self.review_state!r}",
            )
        if self.is_revoked_upstream:
            raise AdoptionStateError(
                "this share was withdrawn by its publisher and cannot be consolidated",
            )

        envelope = CompetencyEnvelope(
            competency_id=self.competency_id,
            # Copied from the recipient's own decision, never inherited from
            # the publisher: the sanitizer removed their classification.
            classification=self.classification,
            provenance=Provenance(
                source_type=TRUSTED_SHARE_SOURCE_TYPE,
                detail=(
                    f"adopted from trusted group {self.source.group_id} "
                    f"(share {self.source.share_id} rev {self.source.share_revision}, "
                    f"publisher {self.source.publisher_user_id}, "
                    f"content {self.source.content_hash}); "
                    f"accepted by {self.reviewer}"
                    + (" after local customisation" if self.local_fork else "")
                ),
                recorded_by="user",
            ),
            confidence=self.confidence,
            supervision=Supervision(requires_review=True),
        )

        content = self.content
        if self.source.share_kind == KIND_HOUSEHOLD_ROUTINE or "steps" in content:
            return CompetencyProcedure(
                envelope=envelope,
                slug=self.slug,
                name=str(content.get("name") or self.inferred_rule)[:200],
                steps=[str(step) for step in (content.get("steps") or [])] or [self.inferred_rule],
            )
        if self.source.share_kind in (KIND_CORRECTION, KIND_GUIDANCE) or "rule" in content:
            return CompetencyHeuristic(
                envelope=envelope,
                slug=self.slug,
                rule=self.inferred_rule,
                conditions=self.conditions,
                counterexamples=[],
            )
        return CompetencyKnowledge(
            envelope=envelope,
            slug=self.slug,
            topic=str(content.get("topic") or self.competency_id),
            content=str(content.get("content") or self.inferred_rule),
        )


# ---------------------------------------------------------------------------
# Producing a candidate from an adopted package
# ---------------------------------------------------------------------------


def _rule_text(package: TrustedSharePackage) -> str:
    """The shared substance as a single readable line.

    Reads only the sanitized content -- there is nothing else on a package to
    read. Order matters: a routine's name, then a rule, then topical content,
    which is the order of specificity in the four package types.
    """
    content = package.content
    for key in ("rule", "name", "topic"):
        value = content.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    value = content.get("content")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return package.summary()


def candidate_from_package(
    package: TrustedSharePackage,
    *,
    competency_id: str,
    classification: str = "personal",
    adopted_at: str | None = None,
) -> AdoptedShareCandidate:
    """Turn an adopted package into one local candidate. Nothing more.

    `competency_id` is the recipient's own decision about where this belongs
    in *their* competency map -- it is not carried on the package, because
    the publisher's organisation of their own Bartholomew is not the
    recipient's. Requiring it here makes adoption an explicit local act
    rather than a copy.

    Raises `ValueError` for a revoked package: a withdrawn share is not
    adoptable, and producing a candidate from one would create a local record
    the recipient could then accept after the publisher had taken it back.
    """
    if package.is_revoked:
        raise ValueError(
            f"share {package.share_id} was withdrawn by its publisher at "
            f"{package.revoked_at}; it cannot be adopted",
        )
    if not competency_id:
        raise ValueError(
            "competency_id is required -- adopting a share is a decision about where it "
            "belongs in your own competency map",
        )

    conditions_value = package.content.get("conditions")
    conditions = conditions_value if isinstance(conditions_value, str) else ""

    return AdoptedShareCandidate(
        competency_id=competency_id,
        slug=slug_for_share(package.share_id, package.revision),
        source=AdoptedShareOrigin(
            share_id=package.share_id,
            group_id=package.group_id,
            publisher_user_id=package.publisher_user_id,
            share_revision=package.revision,
            content_hash=package.content_hash(),
            source_candidate_fingerprint=package.source_candidate_fingerprint,
            share_kind=package.kind,
            sanitization_policy_revision=package.sanitization.policy_revision,
            adopted_at=adopted_at or _utcnow_iso(),
        ),
        inferred_rule=_rule_text(package),
        conditions=conditions,
        content=dict(package.content),
        classification=classification,
        confidence=ADOPTED_SHARE_CONFIDENCE,
        provenance=Provenance(
            source_type=TRUSTED_SHARE_SOURCE_TYPE,
            detail=(
                f"trusted group {package.group_id}, share {package.share_id} "
                f"rev {package.revision}, sanitization policy "
                f"{package.sanitization.policy_revision}"
            ),
            recorded_by="user",
        ),
    )


# ---------------------------------------------------------------------------
# Structural schema registration (privacy_guard)
# ---------------------------------------------------------------------------
# Same reasoning as `competency.py` and `candidate_learning.py`: these key
# names are schema, not content, and scanning them for sensitivity patterns
# produces false positives on every row. The *values* are still scanned in
# full -- which matters here more than anywhere, because the values came from
# somebody else.


def _schema_keys() -> frozenset[str]:
    sample = AdoptedShareCandidate(
        competency_id="_",
        slug="_",
        source=AdoptedShareOrigin(
            share_id="_",
            group_id="_",
            publisher_user_id="_",
            share_revision=1,
            content_hash="_",
            source_candidate_fingerprint="_",
            share_kind="_",
            sanitization_policy_revision=1,
        ),
        inferred_rule="_",
        content={"rule": "_"},
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
    # `content` is the publisher's, so its *keys* are content too and are
    # deliberately not registered as structure: only the fixed schema keys
    # above are, and the sanitizer's allowlist already bounds what can appear
    # inside `content` anyway.
    return frozenset(keys) - {"rule"}


ADOPTED_SHARE_SCHEMA_KEYS: frozenset[str] = _schema_keys()

register_structural_schema(KIND, ADOPTED_SHARE_SCHEMA_KEYS)


__all__ = [
    "ADOPTED_SHARE_CONFIDENCE",
    "KIND",
    "LESSON_ADOPTED_SHARE",
    "REVIEW_ACCEPTED",
    "REVIEW_PROPOSED",
    "REVIEW_REJECTED",
    "REVIEW_STATES",
    "TRUSTED_SHARE_SOURCE_TYPE",
    "AdoptedShareCandidate",
    "AdoptedShareOrigin",
    "AdoptionStateError",
    "candidate_from_package",
    "key_for",
    "slug_for_share",
]
