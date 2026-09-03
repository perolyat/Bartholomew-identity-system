"""
Learning acceptance authorization (S5.4 governance follow-up)
==============================================================

Bartholomew may autonomously conclude *"I may have learned something"*. It may
not autonomously conclude *"this lesson is now trusted knowledge"*. This module
is the data authority for the second half of that sentence: a narrow, explicit,
**candidate-bound** authorization that `learning_accept` -- and only
`learning_accept` -- requires before consolidation can run.

Why this is not a permission
----------------------------
`Identity.yaml`'s `tool_use.allowlist` is a *standing* grant: an entry there
says "this kind of action is permitted from now on". That is the right shape
for `learning_propose` (which creates a candidate that nothing can reason
from) and for `learning_reject` (which is conservative by construction). It is
the wrong shape for acceptance, which is the one durable mutation that makes a
lesson retrievable later, so `learning_accept` is deliberately **absent** from
the allowlist and is not made reachable by adding it there:
`runtime_contract.evaluate_learning_admission()` requires a
`LearningAcceptanceApproval` for the exact candidate regardless of what the
allowlist says. There is no "learning enabled" switch to find.

Binding, and why it is a fingerprint
------------------------------------
An approval names one candidate (`competency_id` + `slug`, the same key
`candidate_learning.key_for()` produces) and carries a `candidate_fingerprint`
over the candidate's *material* content -- the rule, its conditions, its
classification, its confidence, and the experience it stands on. Approving
"call the warranty line before booking an engineer" therefore does not approve
whatever a later re-proposal put at the same key: re-proposing supersedes the
candidate row, the fingerprint no longer matches, and acceptance fails until a
new approval is granted for what the reviewer can now actually see.

Review state, reviewer and timestamps are excluded from the fingerprint on
purpose: acceptance itself mutates all three, and an approval that its own
consumption invalidated would be unusable.

This module is pure data and validation -- no persistence, no retrieval, no
I/O -- the same discipline `candidate_learning.py` holds to. The approval row
is written through `MemoryStore.upsert_memory()` by the governed seam in
`bartholomew.kernel.runtime_contract`, and the decision to grant is audited
through the existing `ActionReflection` authority. No new store, no new audit
log, no new governance path.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from bartholomew.kernel.candidate_learning import REVIEW_PROPOSED, key_for
from bartholomew.kernel.memory.privacy_guard import register_structural_schema

#: The `MemoryStore` kind an acceptance approval is stored under.
#:
#: Like `candidate_learning.KIND`, deliberately absent from
#: `competency.COMPETENCY_KINDS`: an approval is a governance record, never
#: reasoning material, and must never be retrievable as knowledge.
KIND: str = "learning_acceptance_approval"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


#: Material dimensions the Learning and Memory Control Centre added, each
#: paired with the value that means "not assessed".
#:
#: Only an *assigned* value enters the digest (see `_material_extensions`).
#: That is what makes the extension backwards compatible: a candidate written
#: before these fields existed has none of them assigned, contributes no
#: extension block, and therefore digests exactly as it did under PR #83 --
#: so an approval granted before this upgrade is still valid after it. An
#: approval that survived an upgrade is the correct outcome here: nothing
#: about the lesson changed, only the vocabulary available to describe one.
_MATERIAL_EXTENSION_DEFAULTS: dict[str, Any] = {
    "risk_class": None,
    "reversible": None,
    "affected_applications": [],
    "sharing_eligible": None,
}


def _material_extensions(lesson: Any) -> dict[str, Any]:
    """The assigned control-centre material fields, normalised.

    Absent attributes are treated as unassigned, so this stays safe against
    any duck-typed lesson-shaped object (the tests use several).
    """
    assigned: dict[str, Any] = {}
    for name, unassigned in _MATERIAL_EXTENSION_DEFAULTS.items():
        value = getattr(lesson, name, unassigned)
        if isinstance(unassigned, list):
            value = sorted(str(item) for item in (value or []))
            if not value:
                continue
        elif value is None:
            continue
        assigned[name] = value
    return assigned


def fingerprint_for(lesson: Any) -> str:
    """A stable digest of everything a reviewer was actually approving.

    Deliberately narrow *and* deliberately complete: every field below changes
    what the accepted lesson would mean or what it would stand on, so a change
    to any of them must invalidate a prior approval. Fields that acceptance
    itself mutates (`review_state`, `reviewer`, `reviewed_at`, `revision`,
    `updated_at`) are excluded, because including them would make every
    approval invalid at the moment it is used.

    Two fields the Learning and Memory Control Centre displays are covered
    without appearing here by name, because they are *derived* from fields
    that are:

    * **Sharing eligibility** falls out of `classification` unless a reviewer
      assigned it explicitly, in which case it is in the extension block.
    * **Privacy classification** is derived by the memory rules engine from
      the stored value, which is a serialisation of these fields -- so no
      material edit can change the privacy class without changing the digest.

    `display_state` is deliberately *not* here: pinning a candidate for later
    is not a change to what it claims, and invalidating an approval over it
    would be a misleading fingerprint change.
    """
    material = {
        "competency_id": lesson.competency_id,
        "slug": lesson.slug,
        "inferred_rule": lesson.inferred_rule,
        "conditions": lesson.conditions,
        "lesson_kind": lesson.lesson_kind,
        "epistemic_status": lesson.epistemic_status,
        "classification": lesson.classification,
        "confidence": float(lesson.confidence),
        "objective_id": int(lesson.source.objective_id),
        "supporting_event_ids": sorted(int(i) for i in lesson.source.supporting_event_ids),
    }
    extensions = _material_extensions(lesson)
    if extensions:
        material["control_centre_material"] = extensions
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass
class LearningAcceptanceApproval:
    """One explicit authorization to consolidate one specific candidate lesson.

    Not a role, not a session, not a capability: a single record naming who
    approved what, when, and on the strength of which exact candidate content.
    """

    competency_id: str
    slug: str
    candidate_fingerprint: str
    #: Who authorised it. Never inferred, never defaulted, never "system".
    approver: str
    granted_at: str = ""
    note: str | None = None
    #: Provenance back into the candidate's own experience, so an audit can
    #: answer "approved on the strength of what?" without a second read.
    objective_id: int | None = None
    #: The candidate revision the approver saw, alongside the fingerprint that
    #: actually enforces the binding.
    candidate_revision: int | None = None

    def __post_init__(self) -> None:
        if not self.granted_at:
            self.granted_at = _utcnow_iso()

    def key(self) -> str:
        """The candidate's own key -- one live approval per candidate."""
        return key_for(self.competency_id, self.slug)

    def to_dict(self) -> dict[str, Any]:
        return {
            "competency_id": self.competency_id,
            "slug": self.slug,
            "candidate_fingerprint": self.candidate_fingerprint,
            "approver": self.approver,
            "granted_at": self.granted_at,
            "note": self.note,
            "objective_id": self.objective_id,
            "candidate_revision": self.candidate_revision,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LearningAcceptanceApproval:
        return cls(
            competency_id=data["competency_id"],
            slug=data["slug"],
            candidate_fingerprint=data["candidate_fingerprint"],
            approver=data["approver"],
            granted_at=data.get("granted_at") or _utcnow_iso(),
            note=data.get("note"),
            objective_id=data.get("objective_id"),
            candidate_revision=data.get("candidate_revision"),
        )

    def to_summary_text(self) -> str:
        return (
            f"Learning acceptance approval for candidate {self.key()} "
            f"granted by {self.approver} at {self.granted_at}"
        )

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.competency_id:
            errors.append("competency_id is required")
        if not self.slug:
            errors.append("slug is required")
        if not self.candidate_fingerprint:
            errors.append("candidate_fingerprint is required -- approval must bind to content")
        if not self.approver:
            errors.append("approver is required -- authorization is never anonymous")
        return errors

    def authorizes(self, lesson: Any) -> tuple[bool, str | None]:
        """Whether this approval authorises accepting `lesson`.

        Returns `(allowed, reason)`. The reason is always populated on refusal
        and is written verbatim into the refusal's Reflection, so an audit can
        tell "nobody approved this" apart from "the candidate changed after it
        was approved" apart from "the candidate has moved on since".

        Three checks, in order: identity, then content, then revision.

        The **revision** check is the one that is not obvious, and it exists
        because content binding alone has a hole. An approval is invalidated by
        an edit only because the digest moved -- so editing a candidate away
        from its approved wording and then editing it *back* restores the
        digest and silently revives an approval the reviewer was told, in those
        words, no longer applied. The candidate is two revisions on from the
        one they read, and a decision they believe they cancelled accepts it.

        Requiring the revision to match as well closes that: an edit increments
        it, and nothing decrements it.

        Two things make the check precise rather than blunt:

        * It runs **after** the content check, so a candidate whose wording
          genuinely changed is still refused for that reason, in those words.
          The revision check only ever speaks about a candidate that reads
          identically to the approved one.
        * It applies only while the candidate is still `proposed`, which is
          exactly the window in which an edit can happen -- the edit seam
          refuses a terminal candidate. Acceptance and rejection also increment
          the revision, and a terminal candidate is already refused by the
          review-state rules, which explain themselves better than "no
          approval" would.

        The comparison is safe against acceptance's own bookkeeping for the
        same reason: the whole admission runs *before* `accept()` mutates the
        candidate.

        `candidate_revision` is optional on this record, so an approval that
        never recorded one falls back to content binding alone rather than
        being refused outright. Every approval
        `grant_learning_acceptance_approval()` writes records it.
        """
        if lesson is None:
            return False, "acceptance authorization must be bound to a candidate lesson"
        if (self.competency_id, self.slug) != (lesson.competency_id, lesson.slug):
            return (
                False,
                f"approval is bound to candidate {self.key()!r}, not "
                f"{key_for(lesson.competency_id, lesson.slug)!r}",
            )
        if self.candidate_fingerprint != fingerprint_for(lesson):
            return (
                False,
                f"the candidate {self.key()!r} has changed since it was approved by "
                f"{self.approver!r}; a new approval is required",
            )
        lesson_revision = getattr(lesson, "revision", None)
        still_under_review = getattr(lesson, "review_state", REVIEW_PROPOSED) == REVIEW_PROPOSED
        if (
            still_under_review
            and self.candidate_revision is not None
            and self.candidate_revision != lesson_revision
        ):
            return (
                False,
                f"the candidate {self.key()!r} was approved by {self.approver!r} at "
                f"revision {self.candidate_revision} and has been edited since "
                f"(it is now at revision {lesson_revision}); a new approval is "
                "required even though it currently reads the same",
            )
        return True, None


APPROVAL_SCHEMA_KEYS: frozenset[str] = frozenset(
    LearningAcceptanceApproval(
        competency_id="c",
        slug="s",
        candidate_fingerprint="f",
        approver="a",
    )
    .to_dict()
    .keys(),
)

register_structural_schema(KIND, APPROVAL_SCHEMA_KEYS)
