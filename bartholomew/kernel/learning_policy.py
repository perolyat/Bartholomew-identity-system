"""
Shadow learning policy (Package D)
===================================

The configurable policy system that a *future* wave would need before any
low-risk lesson could be accepted without a person in the loop -- built in
full, versioned, deterministic and inspectable, and **structurally unable to
accept anything in this one**.

Why a policy engine that cannot act
-----------------------------------
`COGNITIVE_RUNTIME.md` describes a "high-confidence, low-impact" branch that
consolidates a candidate lesson directly.
`candidate_learning.CandidateLesson.requires_review` deliberately omits it,
and PR #83 made `learning_accept` require a `LearningAcceptanceApproval`
bound to one exact candidate. Both remain true after this module exists.

What was missing was not the ability to skip review -- it was the ability to
*reason about* review out loud. A person cannot sensibly decide whether they
would ever want automatic acceptance without seeing, over months, what a
policy would have done to the candidates they actually reviewed by hand. So
this module answers exactly one question, for one candidate, against one
recorded policy revision:

    "Under this policy, would this lesson have been accepted, refused, or
    escalated to you -- and which rules said so?"

It answers it and stops. There is no branch here that accepts.

The structural prohibition, stated as code rather than as intent
----------------------------------------------------------------
Five properties, each independently sufficient, and each pinned by a test in
`tests/test_shadow_learning_policy.py`:

1. **No writer.** This module imports no `MemoryStore`, no `aiosqlite`, no
   `runtime_contract`. It is pure data and arithmetic -- the same discipline
   `competency.py`, `candidate_learning.py` and `learning_authorization.py`
   hold to. It cannot persist anything, so it cannot persist an acceptance.
2. **No approval constructor.** Nothing here imports, builds, returns or
   serialises a `LearningAcceptanceApproval`. A `ShadowDecision` carries a
   `candidate_fingerprint` because a reviewer must be able to tell *which*
   version of a candidate was judged -- not because anything can consume it
   as authorization. `ShadowDecision.authorizes_acceptance` is a property
   that returns `False` unconditionally and has no setter.
3. **A decision is not a permission.** `DECISION_WOULD_ACCEPT` is a
   *counterfactual*. `runtime_contract.evaluate_learning_admission()` never
   reads a `ShadowDecision`, and the acceptance path has no parameter
   through which one could be supplied. Grep for the constant: it appears in
   this module, in the record it writes, in the API projection and in the
   UI. It appears nowhere in an admission decision.
4. **The execution mode is a constant, not a setting.** `SHIPPED_EXECUTION_MODE`
   is a module-level constant fixed to `"shadow"`. `LearningPolicy` has a
   `requested_execution_mode` field the user may set to `"auto"` -- because
   describing the future they want is the entire point of letting them
   configure this -- and `LearningPolicy.execution_mode` ignores it and
   returns the constant. A user who configures auto-accept has recorded a
   preference, not enabled a behaviour.
5. **The write surface is enumerated.** `FORBIDDEN_SHADOW_WRITE_KINDS` names
   every kind the shadow path must never write: the candidate itself, the
   acceptance approval, and all five competency kinds. `runtime_contract`'s
   shadow seam routes its single write through a helper that raises on any
   kind but `EVALUATION_KIND`.

The one thing shadow evaluation *may* do is leave a record saying what it
would have done. That record is stored under `EVALUATION_KIND`, which is
absent from `competency.COMPETENCY_KINDS`, so the retrieval seam's kind
filter cannot see it and no future reasoning turn can cite it as knowledge.

Determinism
-----------
Given the same policy revision and the same candidate facts, `evaluate()`
returns the same decision, the same matched rules in the same order, and the
same reasons. Rules are evaluated in a fixed order (`RULE_ORDER`), every
matching rule is recorded rather than short-circuited, and the decision is a
pure function of the matched set: any refusal refuses; otherwise any
escalation escalates; otherwise the lesson would have been accepted. There
is no scoring, no threshold arithmetic on the aggregate, and no dependence
on evaluation order for the outcome -- so an explanation the user reads is
the complete reason, not the first reason.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any, Final

from bartholomew.kernel import candidate_learning
from bartholomew.kernel.competency import COMPETENCY_KINDS
from bartholomew.kernel.memory.privacy_guard import register_structural_schema

# ---------------------------------------------------------------------------
# Memory kinds
# ---------------------------------------------------------------------------

#: The `MemoryStore` kind one tenant's learning policy configuration is
#: stored under. Deliberately absent from `competency.COMPETENCY_KINDS`: a
#: policy is governance configuration, never reasoning material.
POLICY_KIND: str = "learning_policy"

#: The `MemoryStore` kind one shadow evaluation record is stored under. Also
#: absent from `COMPETENCY_KINDS`, for the stronger reason: a record saying
#: "this lesson would have been accepted" is the single most dangerous thing
#: a retrieval seam could mistake for "this lesson was accepted".
EVALUATION_KIND: str = "learning_shadow_evaluation"

#: The `MemoryStore` kind a superseded candidate revision is preserved under
#: when a reviewer materially edits a candidate. The live candidate keeps its
#: own key under `candidate_learning.KIND`; each prior revision is archived
#: here so the edit history a review decision was made against survives.
#: Absent from `COMPETENCY_KINDS` for the same reason the candidate is.
CANDIDATE_REVISION_KIND: str = "candidate_lesson_revision"

#: The `MemoryStore` kind a superseded *competency* record is preserved under
#: when it is corrected.
#:
#: S5.2's training seam records that a supersession happened -- the revision
#: it replaced and that claim's provenance -- but not what the superseded
#: record actually said, because a correction is an in-place upsert. That
#: makes "what did Bartholomew believe before I corrected this?" unanswerable,
#: and the contract names superseded and corrected knowledge as something a
#: person must be able to see. Archiving the prior record here answers it
#: without touching the training seam's own bookkeeping.
#:
#: Absent from `COMPETENCY_KINDS`, deliberately and importantly: a superseded
#: belief must not be retrievable as a current one.
COMPETENCY_REVISION_KIND: str = "competency_revision"

#: The single canonical policy key. One live policy per runtime, which is one
#: per tenant -- see `bartholomew.platform.runtime_registry`, where a tenant
#: *is* an isolated database file. Storing it under a fixed key rather than a
#: per-user key means the policy cannot be addressed across the isolation
#: boundary even by mistake: there is no user id in the key to get wrong.
POLICY_KEY: str = "default"

#: The `MemoryStore` kind an acceptance approval is stored under.
#:
#: A literal rather than an import of `learning_authorization`, deliberately:
#: this module must be able to *name* the approval kind in order to forbid
#: writing it, without importing the machinery that constructs one.
#: `tests/test_shadow_learning_policy.py` pins the literal against
#: `learning_authorization.KIND`, so drift fails a test rather than silently
#: unguarding the kind.
APPROVAL_KIND: str = "learning_acceptance_approval"

#: Kinds the shadow path must never write. `runtime_contract`'s shadow seam
#: refuses any of these at its single write helper.
#:
#: Derived from `COMPETENCY_KINDS` rather than hand-listed. An earlier draft
#: spelled the five competency kinds out and got the first one wrong
#: (`competency_record` -- the actual kind is `competency`), which would have
#: left the primary competency kind unguarded. Deriving means a sixth kind is
#: covered the day it is added.
FORBIDDEN_SHADOW_WRITE_KINDS: frozenset[str] = frozenset(
    {candidate_learning.KIND, APPROVAL_KIND, *COMPETENCY_KINDS},
)


# ---------------------------------------------------------------------------
# Execution mode
# ---------------------------------------------------------------------------

#: The mode a user may *record a preference for*. Configuration describes a
#: future; it does not select a present.
REQUESTED_MODE_SHADOW = "shadow"
REQUESTED_MODE_AUTO = "auto"
REQUESTED_MODES: frozenset[str] = frozenset({REQUESTED_MODE_SHADOW, REQUESTED_MODE_AUTO})

#: The mode this wave actually runs in. A `Final` module constant, not a
#: setting, not an environment variable, and not a field on anything a user
#: or an API body can reach. Changing it is a code change that must fail
#: `tests/test_shadow_learning_policy.py` before it can succeed.
SHIPPED_EXECUTION_MODE: Final[str] = "shadow"

#: What the UI says, in the user's words, wherever a shadow decision appears.
SHADOW_MODE_NOTICE: Final[str] = (
    "Bartholomew is running this policy in preview only. He will show you "
    "what it would have decided, and he cannot act on it: accepting a lesson "
    "still needs you to approve that exact lesson yourself."
)


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

DECISION_WOULD_ACCEPT = "would_accept"
DECISION_WOULD_REFUSE = "would_refuse"
DECISION_WOULD_ESCALATE = "would_escalate"

DECISIONS: frozenset[str] = frozenset(
    {DECISION_WOULD_ACCEPT, DECISION_WOULD_REFUSE, DECISION_WOULD_ESCALATE},
)

#: Risk classes, least to most severe. An ordered tuple rather than a set,
#: because "at most this risky" is the comparison the policy actually makes
#: and an unordered vocabulary cannot express it. Taken from
#: `candidate_learning` so a reviewer's assignment and the policy that reads
#: it cannot drift apart.
RISK_CLASSES: tuple[str, ...] = candidate_learning.RISK_CLASSES

#: What a policy does when the supporting experience is contradicted.
#: Deliberately two values, and neither is "ignore": evidence against a
#: lesson is never a thing a policy may be configured to disregard.
CONTRADICTION_REFUSE = "refuse"
CONTRADICTION_ESCALATE = "escalate"
CONTRADICTION_BEHAVIOURS: frozenset[str] = frozenset(
    {CONTRADICTION_REFUSE, CONTRADICTION_ESCALATE},
)

#: Sharing state. Session E owns transport; this package owns only the
#: eligibility and the *claim* about state, and `SHARING_NOT_SHARED` is the
#: only value anything in this wave can legitimately produce. The other two
#: exist so Session F has names to connect rather than fields to invent --
#: see `SharingInterface` at the bottom of this module.
SHARING_NOT_SHARED = "not_shared"
SHARING_OFFERED = "offered"
SHARING_SHARED = "shared"
SHARING_STATES: frozenset[str] = frozenset(
    {SHARING_NOT_SHARED, SHARING_OFFERED, SHARING_SHARED},
)


class PolicyError(ValueError):
    """A policy revision is malformed, or an update conflicts with a newer one."""


class PolicyConflictError(PolicyError):
    """
    The policy changed underneath an edit.

    Its own type so the conflict path is greppable and cannot be confused
    with a validation failure: nothing is wrong with what the user wrote,
    they were simply editing a revision that is no longer current.
    """


def _clean_list(values: Any) -> list[str]:
    """Normalise a string list: strip, drop blanks, de-duplicate, sort.

    Sorting matters more than it looks: a policy's serialised form feeds a
    revision comparison and an audit record, and two policies that differ
    only in the order a user typed two category names are the same policy.
    """
    if not values:
        return []
    if isinstance(values, str):
        values = [values]
    seen: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen.append(text)
    return sorted(seen)


# ---------------------------------------------------------------------------
# The policy
# ---------------------------------------------------------------------------


@dataclass
class LearningPolicy:
    """
    One tenant's configuration for a future governed automatic acceptance.

    Every dimension the contract names is a field here, and every field is
    read by `evaluate()`. A configuration option that nothing consults would
    be a lie told in a settings screen.

    Versioned: `revision` increments on every stored change, and an update
    that names a stale revision is refused rather than merged (see
    `PolicyConflictError`). Prior revisions are preserved as their own
    records, so a shadow decision taken under revision 3 stays explicable
    after revision 4 lands.
    """

    #: Bumped by the storing seam, never by a caller. Revision 0 is the
    #: unsaved built-in default -- a policy nobody has configured yet.
    revision: int = 0

    # -- what may be considered at all ----------------------------------
    #: Lesson categories (`candidate_learning.LESSON_KINDS` values) this
    #: policy would ever accept. Empty means "none" -- a deliberately safe
    #: default, not a wildcard.
    enabled_categories: list[str] = field(default_factory=list)
    #: Categories excluded outright. Wins over `enabled_categories` when a
    #: category appears in both: an exclusion a user wrote must never be
    #: silently outranked by an inclusion they also wrote.
    excluded_categories: list[str] = field(default_factory=list)

    # -- how risky, and how recoverable ---------------------------------
    max_risk: str = "low"
    require_reversible: bool = True

    # -- how well evidenced ---------------------------------------------
    #: *Independent* supporting experiences. The S5.4 slice produces exactly
    #: one per candidate, so a threshold above 1 refuses everything this
    #: wave can currently propose -- which is the honest default.
    min_supporting_experiences: int = 2
    min_confidence: float = 0.8
    contradiction_behaviour: str = CONTRADICTION_REFUSE

    # -- how far it reaches ----------------------------------------------
    max_affected_capabilities: int = 1
    max_affected_applications: int = 0

    # -- what it may touch ------------------------------------------------
    #: Every class the shipped `memory_rules.yaml` gates on consent, plus
    #: `user.health`. Kept in step with the export block list by
    #: `tests/test_learning_control_centre_api.py`.
    excluded_privacy_classes: list[str] = field(
        default_factory=lambda: [
            "user.secure",
            "user.sensitive",
            "user.health",
            "user.emotional",
            "thirdparty.private",
        ],
    )
    #: Competency classifications excluded from automatic acceptance.
    #: `potentially_generalisable` and `system` are excluded by default:
    #: neither is a claim about the user's own life alone.
    excluded_classifications: list[str] = field(
        default_factory=lambda: ["potentially_generalisable", "system"],
    )
    #: Refuse anything a household could see. Sharing is Session E's, and a
    #: lesson that could leave this runtime is not a low-risk lesson.
    exclude_sharing_eligible: bool = True

    # -- how long a decision would stay good ------------------------------
    #: Days after which an accepted lesson would need re-confirming. None
    #: means "no expiry", which the validator permits and the UI labels.
    expires_after_days: int | None = 180
    #: Days between mandatory human reviews of accepted knowledge.
    review_interval_days: int | None = 90

    # -- the future the user is describing --------------------------------
    #: What the user would *like* to happen once a future wave enables it.
    #: Read by nothing except the UI and the audit record. See
    #: `execution_mode` immediately below.
    requested_execution_mode: str = REQUESTED_MODE_SHADOW

    # -- provenance --------------------------------------------------------
    updated_at: str = ""
    updated_by: str = ""
    note: str | None = None

    # -- the invariant -----------------------------------------------------

    @property
    def execution_mode(self) -> str:
        """Always `SHIPPED_EXECUTION_MODE`.

        A property with no setter, deliberately, and deliberately not derived
        from `requested_execution_mode`. A user who sets that field to
        `"auto"` has told Bartholomew what they would like a later wave to
        do. It does not change what this one does, and the fact that it does
        not is the point: there is no configuration a user can write, and no
        Identity policy an operator can relax, that turns preview into
        action.
        """
        return SHIPPED_EXECUTION_MODE

    @property
    def auto_acceptance_enabled(self) -> bool:
        """Always False. Present so callers ask the question and get the
        truthful answer rather than inferring one from `requested_execution_mode`."""
        return False

    # -- serialisation -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "enabled_categories": list(self.enabled_categories),
            "excluded_categories": list(self.excluded_categories),
            "max_risk": self.max_risk,
            "require_reversible": self.require_reversible,
            "min_supporting_experiences": self.min_supporting_experiences,
            "min_confidence": self.min_confidence,
            "contradiction_behaviour": self.contradiction_behaviour,
            "max_affected_capabilities": self.max_affected_capabilities,
            "max_affected_applications": self.max_affected_applications,
            "excluded_privacy_classes": list(self.excluded_privacy_classes),
            "excluded_classifications": list(self.excluded_classifications),
            "exclude_sharing_eligible": self.exclude_sharing_eligible,
            "expires_after_days": self.expires_after_days,
            "review_interval_days": self.review_interval_days,
            "requested_execution_mode": self.requested_execution_mode,
            # Recorded, not read back: `execution_mode` is a property. Stored
            # so an archived policy revision states plainly which mode it was
            # ever able to run in, and a future reader cannot mistake a
            # revision written now for one written under a different regime.
            "execution_mode": self.execution_mode,
            "auto_acceptance_enabled": self.auto_acceptance_enabled,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LearningPolicy:
        """Rebuild a policy from its stored form.

        Tolerant of missing keys (an older revision predates a field) and
        deliberately *not* tolerant of a stored `execution_mode` other than
        the shipped one: `execution_mode` is not read back at all. A row
        hand-edited to say `"execution_mode": "auto"` therefore changes
        nothing, which is the property that matters most about this method.
        """
        defaults = cls()
        return cls(
            revision=int(data.get("revision", 0)),
            enabled_categories=_clean_list(data.get("enabled_categories")),
            excluded_categories=_clean_list(data.get("excluded_categories")),
            max_risk=str(data.get("max_risk", defaults.max_risk)),
            require_reversible=bool(data.get("require_reversible", defaults.require_reversible)),
            min_supporting_experiences=int(
                data.get("min_supporting_experiences", defaults.min_supporting_experiences),
            ),
            min_confidence=float(data.get("min_confidence", defaults.min_confidence)),
            contradiction_behaviour=str(
                data.get("contradiction_behaviour", defaults.contradiction_behaviour),
            ),
            max_affected_capabilities=int(
                data.get("max_affected_capabilities", defaults.max_affected_capabilities),
            ),
            max_affected_applications=int(
                data.get("max_affected_applications", defaults.max_affected_applications),
            ),
            excluded_privacy_classes=_clean_list(
                data.get("excluded_privacy_classes", defaults.excluded_privacy_classes),
            ),
            excluded_classifications=_clean_list(
                data.get("excluded_classifications", defaults.excluded_classifications),
            ),
            exclude_sharing_eligible=bool(
                data.get("exclude_sharing_eligible", defaults.exclude_sharing_eligible),
            ),
            expires_after_days=_optional_int(data.get("expires_after_days")),
            review_interval_days=_optional_int(data.get("review_interval_days")),
            requested_execution_mode=str(
                data.get("requested_execution_mode", defaults.requested_execution_mode),
            ),
            updated_at=str(data.get("updated_at") or ""),
            updated_by=str(data.get("updated_by") or ""),
            note=data.get("note"),
        )

    def to_summary_text(self) -> str:
        return (
            f"Learning policy revision {self.revision} "
            f"(preview only; automatic acceptance is disabled) "
            f"updated by {self.updated_by or 'nobody yet'}"
        )

    # -- validation ---------------------------------------------------------

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.max_risk not in RISK_CLASSES:
            errors.append(f"max_risk must be one of {list(RISK_CLASSES)}, got {self.max_risk!r}")
        if self.contradiction_behaviour not in CONTRADICTION_BEHAVIOURS:
            errors.append(
                "contradiction_behaviour must be one of "
                f"{sorted(CONTRADICTION_BEHAVIOURS)}, got {self.contradiction_behaviour!r}",
            )
        if self.requested_execution_mode not in REQUESTED_MODES:
            errors.append(
                f"requested_execution_mode must be one of {sorted(REQUESTED_MODES)}, "
                f"got {self.requested_execution_mode!r}",
            )
        if self.min_supporting_experiences < 1:
            errors.append("min_supporting_experiences must be at least 1")
        if not (0.0 <= self.min_confidence <= 1.0):
            errors.append(
                f"min_confidence must be between 0.0 and 1.0, got {self.min_confidence!r}",
            )
        if self.max_affected_capabilities < 0:
            errors.append("max_affected_capabilities cannot be negative")
        if self.max_affected_applications < 0:
            errors.append("max_affected_applications cannot be negative")
        if self.expires_after_days is not None and self.expires_after_days < 1:
            errors.append("expires_after_days must be at least 1 day, or null for no expiry")
        if self.review_interval_days is not None and self.review_interval_days < 1:
            errors.append("review_interval_days must be at least 1 day, or null for no interval")
        if self.revision < 0:
            errors.append("revision cannot be negative")
        return errors

    def normalised(self) -> LearningPolicy:
        """A copy with list fields cleaned, for a stable stored form."""
        return replace(
            self,
            enabled_categories=_clean_list(self.enabled_categories),
            excluded_categories=_clean_list(self.excluded_categories),
            excluded_privacy_classes=_clean_list(self.excluded_privacy_classes),
            excluded_classifications=_clean_list(self.excluded_classifications),
        )


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def default_policy() -> LearningPolicy:
    """
    The safe default a runtime starts with, before anyone configures one.

    Deliberately refuses everything the current loop can produce: no lesson
    category is enabled, two independent supporting experiences are required
    where S5.4 can produce one, and the confidence floor sits far above
    `candidate_learning.SINGLE_EXPERIENCE_CONFIDENCE` (0.4). A brand-new
    deployment that somehow acquired an acceptance branch would still accept
    nothing. Nothing about this default is load-bearing for safety -- the
    execution mode is -- but a default that *looked* permissive would
    misrepresent the system to the first person who read it.
    """
    return LearningPolicy(revision=0)


# ---------------------------------------------------------------------------
# What the policy is evaluated against
# ---------------------------------------------------------------------------


@dataclass
class CandidateFacts:
    """
    The normalised facts about one candidate that `evaluate()` reads.

    A separate type from `CandidateLesson` on purpose. Some of these -- risk
    class, reversibility, contradictory evidence, affected applications --
    are not fields the S5.4 slice records, because that slice never needed
    them. Rather than widen the candidate (which would change its material
    fingerprint and invalidate every existing approval), they are derived or
    supplied here, and default to the *conservative* value: unknown risk is
    treated as the highest, unknown reversibility as irreversible.

    `from_lesson()` derives everything derivable; the rest a caller may
    supply as it learns how to measure them.
    """

    candidate_key: str
    candidate_fingerprint: str
    lesson_category: str
    classification: str
    confidence: float
    epistemic_status: str
    supporting_experience_count: int = 0
    #: Distinct originating objectives behind the supporting evidence. The
    #: honest measure of "independent" experiences: ten evidence events from
    #: one objective are one experience, not ten.
    independent_experience_count: int = 0
    contradicting_evidence_count: int = 0
    risk_class: str = "critical"
    reversible: bool = False
    affected_capabilities: list[str] = field(default_factory=list)
    affected_applications: list[str] = field(default_factory=list)
    privacy_class: str | None = None
    sharing_eligible: bool = False
    sharing_state: str = SHARING_NOT_SHARED
    #: Age of the supporting experience in days, when known. Compared
    #: against the policy's `expires_after_days`.
    experience_age_days: float | None = None
    #: Days since the candidate was last reviewed by a person, when known.
    #: Compared against `review_interval_days`.
    days_since_last_review: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_key": self.candidate_key,
            "candidate_fingerprint": self.candidate_fingerprint,
            "lesson_category": self.lesson_category,
            "classification": self.classification,
            "confidence": self.confidence,
            "epistemic_status": self.epistemic_status,
            "supporting_experience_count": self.supporting_experience_count,
            "independent_experience_count": self.independent_experience_count,
            "contradicting_evidence_count": self.contradicting_evidence_count,
            "risk_class": self.risk_class,
            "reversible": self.reversible,
            "affected_capabilities": list(self.affected_capabilities),
            "affected_applications": list(self.affected_applications),
            "privacy_class": self.privacy_class,
            "sharing_eligible": self.sharing_eligible,
            "sharing_state": self.sharing_state,
            "experience_age_days": self.experience_age_days,
            "days_since_last_review": self.days_since_last_review,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CandidateFacts:
        defaults = cls(
            candidate_key="",
            candidate_fingerprint="",
            lesson_category="",
            classification="",
            confidence=0.0,
            epistemic_status="",
        )
        return cls(
            candidate_key=str(data.get("candidate_key", "")),
            candidate_fingerprint=str(data.get("candidate_fingerprint", "")),
            lesson_category=str(data.get("lesson_category", "")),
            classification=str(data.get("classification", "")),
            confidence=float(data.get("confidence", 0.0)),
            epistemic_status=str(data.get("epistemic_status", "")),
            supporting_experience_count=int(data.get("supporting_experience_count", 0)),
            independent_experience_count=int(data.get("independent_experience_count", 0)),
            contradicting_evidence_count=int(data.get("contradicting_evidence_count", 0)),
            risk_class=str(data.get("risk_class", defaults.risk_class)),
            reversible=bool(data.get("reversible", defaults.reversible)),
            affected_capabilities=_clean_list(data.get("affected_capabilities")),
            affected_applications=_clean_list(data.get("affected_applications")),
            privacy_class=data.get("privacy_class"),
            sharing_eligible=bool(data.get("sharing_eligible", False)),
            sharing_state=str(data.get("sharing_state", SHARING_NOT_SHARED)),
            experience_age_days=_optional_float(data.get("experience_age_days")),
            days_since_last_review=_optional_float(data.get("days_since_last_review")),
        )


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def facts_from_lesson(
    lesson: Any,
    fingerprint: str,
    *,
    risk_class: str | None = None,
    reversible: bool | None = None,
    contradicting_evidence_count: int = 0,
    affected_capabilities: list[str] | None = None,
    affected_applications: list[str] | None = None,
    privacy_class: str | None = None,
    sharing_eligible: bool | None = None,
    sharing_state: str | None = None,
    experience_age_days: float | None = None,
    days_since_last_review: float | None = None,
) -> CandidateFacts:
    """
    Derive the evaluable facts from a `CandidateLesson`, conservatively.

    Duck-typed on the lesson (attribute reads only) so this module keeps its
    no-import discipline towards the rest of the learning stack.

    Anything the S5.4 candidate does not record stays at its conservative
    default unless a caller supplies it. That direction is deliberate and
    one-way: an unmeasured dimension makes a lesson *less* likely to be
    marked acceptable, never more. A shadow decision that said "would
    accept" because a field was missing would be the exact failure this
    whole package exists to make impossible to reach by accident.
    """
    source = getattr(lesson, "source", None)
    supporting = list(getattr(source, "supporting_event_ids", []) or [])
    objective_id = getattr(source, "objective_id", None)

    return CandidateFacts(
        candidate_key=f"{lesson.competency_id}.{lesson.slug}",
        candidate_fingerprint=fingerprint,
        lesson_category=getattr(lesson, "lesson_kind", ""),
        classification=getattr(lesson, "classification", ""),
        confidence=float(getattr(lesson, "confidence", 0.0)),
        epistemic_status=getattr(lesson, "epistemic_status", ""),
        supporting_experience_count=len(supporting),
        # One objective is one experience. The S5.4 slice infers from exactly
        # one recorded outcome, so this is 1 whenever the candidate is well
        # formed -- and counting its evidence events instead would let a
        # verbose objective masquerade as corroboration.
        independent_experience_count=1 if objective_id else 0,
        contradicting_evidence_count=max(0, int(contradicting_evidence_count)),
        risk_class=risk_class if risk_class in RISK_CLASSES else "critical",
        reversible=bool(reversible) if reversible is not None else False,
        affected_capabilities=_clean_list(
            (
                affected_capabilities
                if affected_capabilities is not None
                else [getattr(lesson, "competency_id", "")]
            ),
        ),
        affected_applications=_clean_list(affected_applications),
        privacy_class=privacy_class,
        sharing_eligible=(
            bool(sharing_eligible)
            if sharing_eligible is not None
            # A `personal` lesson is not shareable; anything else is at least
            # a candidate for sharing and is treated as eligible until
            # Session E's registry can say otherwise.
            else getattr(lesson, "classification", "personal") != "personal"
        ),
        sharing_state=sharing_state if sharing_state in SHARING_STATES else SHARING_NOT_SHARED,
        experience_age_days=experience_age_days,
        days_since_last_review=days_since_last_review,
    )


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------


@dataclass
class MatchedRule:
    """One policy rule that fired, and why -- in the user's words."""

    rule_id: str
    effect: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"rule_id": self.rule_id, "effect": self.effect, "reason": self.reason}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MatchedRule:
        return cls(
            rule_id=str(data["rule_id"]),
            effect=str(data["effect"]),
            reason=str(data.get("reason", "")),
        )


@dataclass
class ShadowDecision:
    """
    What the policy would have done, and nothing more.

    This object is inert by construction. It has no method that grants,
    accepts, consolidates or authorises; `authorizes_acceptance` exists only
    to answer False out loud, so a future caller reaching for "can I use this
    as permission?" finds a written no rather than an absent field.
    """

    candidate_key: str
    candidate_fingerprint: str
    policy_revision: int
    decision: str
    matched_rules: list[MatchedRule] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    evaluated_at: str = ""
    #: The candidate's review state when the evaluation ran, recorded so a
    #: reader can tell a preview of an untouched proposal from a preview run
    #: against something a person had already decided.
    candidate_review_state: str = ""
    facts: CandidateFacts | None = None

    @property
    def execution_mode(self) -> str:
        return SHIPPED_EXECUTION_MODE

    @property
    def authorizes_acceptance(self) -> bool:
        """Always False.

        `decision == "would_accept"` is a counterfactual about a policy, not
        a permission about a lesson. Consolidation requires a
        `LearningAcceptanceApproval` bound to the candidate's exact material
        fingerprint, granted by a named person, and nothing on this object
        can produce one.
        """
        return False

    @property
    def consolidated(self) -> bool:
        """Always False. Shadow evaluation writes no competency record."""
        return False

    def key(self) -> str:
        """`"<candidate key>@<policy revision>"`.

        Keyed by *both*, so a later policy revision leaves the earlier
        verdict standing instead of overwriting it. That is what makes
        "revision 3 said escalate, revision 4 said accept" readable, and it
        is the property the contract asks for when it says a configuration
        change must not silently rewrite prior evaluation records.

        Re-evaluating under the *same* revision is idempotent: same policy,
        same facts, same deterministic result, same row.
        """
        return f"{self.candidate_key}@{self.policy_revision}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_key": self.candidate_key,
            "candidate_fingerprint": self.candidate_fingerprint,
            "policy_revision": self.policy_revision,
            "decision": self.decision,
            "matched_rules": [rule.to_dict() for rule in self.matched_rules],
            "reasons": list(self.reasons),
            "evaluated_at": self.evaluated_at,
            "candidate_review_state": self.candidate_review_state,
            "execution_mode": self.execution_mode,
            "authorizes_acceptance": self.authorizes_acceptance,
            "consolidated": self.consolidated,
            "shadow_mode_notice": SHADOW_MODE_NOTICE,
            "facts": self.facts.to_dict() if self.facts else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ShadowDecision:
        facts = data.get("facts")
        return cls(
            candidate_key=str(data["candidate_key"]),
            candidate_fingerprint=str(data.get("candidate_fingerprint", "")),
            policy_revision=int(data.get("policy_revision", 0)),
            decision=str(data["decision"]),
            matched_rules=[MatchedRule.from_dict(r) for r in data.get("matched_rules", [])],
            reasons=list(data.get("reasons", [])),
            evaluated_at=str(data.get("evaluated_at") or ""),
            candidate_review_state=str(data.get("candidate_review_state") or ""),
            facts=CandidateFacts.from_dict(facts) if facts else None,
        )

    def to_summary_text(self) -> str:
        """Leads with "Preview" for the same reason a candidate lesson's
        summary leads with "Candidate lesson": whatever reads this row is
        told what it is looking at before it is told what it says."""
        return (
            f"Preview only (no action taken): under learning policy revision "
            f"{self.policy_revision}, candidate {self.candidate_key} "
            f"{self.decision.replace('_', ' ')}"
        )


# ---------------------------------------------------------------------------
# The evaluator
# ---------------------------------------------------------------------------

EFFECT_REFUSE = "refuse"
EFFECT_ESCALATE = "escalate"

#: Fixed evaluation order. Determinism is a contract requirement, and a
#: dict's insertion order is a weaker promise than an explicit tuple that a
#: test can pin.
RULE_ORDER: tuple[str, ...] = (
    "category_excluded",
    "category_not_enabled",
    "epistemic_status_not_inference",
    "risk_above_maximum",
    "not_reversible",
    "insufficient_supporting_experiences",
    "confidence_below_threshold",
    "contradictory_evidence",
    "too_many_affected_capabilities",
    "too_many_affected_applications",
    "privacy_class_excluded",
    "classification_excluded",
    "sharing_eligible_excluded",
    "already_shared",
    "supporting_experience_expired",
    "review_interval_elapsed",
)


def _risk_rank(risk: str) -> int:
    """Rank a risk class. An unrecognised class ranks above `critical`.

    Unknown risk is not low risk. A future risk vocabulary that added a name
    this code does not know must fail towards refusal, not towards silence.
    """
    try:
        return RISK_CLASSES.index(risk)
    except ValueError:
        return len(RISK_CLASSES)


def evaluate(
    policy: LearningPolicy,
    facts: CandidateFacts,
    *,
    evaluated_at: str,
    candidate_review_state: str = "",
) -> ShadowDecision:
    """
    Decide what `policy` would have done about `facts`. Deterministic and pure.

    Every rule is evaluated -- none short-circuits -- so the reasons a user
    reads are complete rather than "the first thing that went wrong". The
    decision itself is a pure function of the matched set:

      * any rule with effect `refuse`   -> `would_refuse`
      * else any rule with `escalate`   -> `would_escalate`
      * else                            -> `would_accept`

    `evaluated_at` is supplied by the caller rather than read from the clock,
    so this function has no hidden input and two runs over the same arguments
    are byte-identical -- which is what makes "deterministic" testable rather
    than merely claimed.

    **This returns a description. It performs nothing.** See the module
    docstring's five structural properties.
    """
    matched: list[MatchedRule] = []

    def fire(rule_id: str, effect: str, reason: str) -> None:
        matched.append(MatchedRule(rule_id=rule_id, effect=effect, reason=reason))

    checks: dict[str, Any] = {}

    # -- what may be considered at all ------------------------------------
    if facts.lesson_category in policy.excluded_categories:
        checks["category_excluded"] = (
            EFFECT_REFUSE,
            f"You have excluded {facts.lesson_category!r} lessons from automatic acceptance.",
        )
    if facts.lesson_category not in policy.enabled_categories:
        checks["category_not_enabled"] = (
            EFFECT_REFUSE,
            f"{facts.lesson_category!r} lessons are not on your list of categories "
            "that could ever be accepted automatically.",
        )

    # -- what a candidate lesson must be ------------------------------------
    if facts.epistemic_status != "inference":
        checks["epistemic_status_not_inference"] = (
            EFFECT_REFUSE,
            "A candidate lesson must be recorded as something Bartholomew inferred; "
            f"this one says {facts.epistemic_status!r}.",
        )

    # -- how risky, and how recoverable -------------------------------------
    if _risk_rank(facts.risk_class) > _risk_rank(policy.max_risk):
        checks["risk_above_maximum"] = (
            EFFECT_REFUSE,
            f"Its risk is {facts.risk_class!r}, above the most you allow "
            f"({policy.max_risk!r}).",
        )
    if policy.require_reversible and not facts.reversible:
        checks["not_reversible"] = (
            EFFECT_REFUSE,
            "You require anything accepted automatically to be reversible, and this "
            "lesson is not recorded as reversible.",
        )

    # -- how well evidenced ---------------------------------------------------
    if facts.independent_experience_count < policy.min_supporting_experiences:
        checks["insufficient_supporting_experiences"] = (
            EFFECT_REFUSE,
            f"It rests on {facts.independent_experience_count} independent "
            f"experience(s); you require at least {policy.min_supporting_experiences}.",
        )
    if facts.confidence < policy.min_confidence:
        checks["confidence_below_threshold"] = (
            EFFECT_REFUSE,
            f"Its confidence is {facts.confidence:.2f}; your threshold is "
            f"{policy.min_confidence:.2f}.",
        )
    if facts.contradicting_evidence_count > 0:
        behaviour = (
            EFFECT_REFUSE
            if policy.contradiction_behaviour == CONTRADICTION_REFUSE
            else EFFECT_ESCALATE
        )
        checks["contradictory_evidence"] = (
            behaviour,
            f"{facts.contradicting_evidence_count} recorded observation(s) contradict it, "
            f"and you have asked Bartholomew to {policy.contradiction_behaviour} in that case.",
        )

    # -- how far it reaches -----------------------------------------------------
    if len(facts.affected_capabilities) > policy.max_affected_capabilities:
        checks["too_many_affected_capabilities"] = (
            EFFECT_ESCALATE,
            f"It touches {len(facts.affected_capabilities)} of Bartholomew's areas; "
            f"you allow at most {policy.max_affected_capabilities} without asking you.",
        )
    if len(facts.affected_applications) > policy.max_affected_applications:
        checks["too_many_affected_applications"] = (
            EFFECT_ESCALATE,
            f"It affects {len(facts.affected_applications)} application(s); you allow at "
            f"most {policy.max_affected_applications} without asking you.",
        )

    # -- what it may touch --------------------------------------------------------
    if facts.privacy_class and facts.privacy_class in policy.excluded_privacy_classes:
        checks["privacy_class_excluded"] = (
            EFFECT_REFUSE,
            f"It involves {facts.privacy_class!r} material, which you have excluded "
            "from automatic acceptance.",
        )
    if facts.classification in policy.excluded_classifications:
        checks["classification_excluded"] = (
            EFFECT_REFUSE,
            f"It is classified {facts.classification!r}, which you have excluded from "
            "automatic acceptance.",
        )
    if policy.exclude_sharing_eligible and facts.sharing_eligible:
        checks["sharing_eligible_excluded"] = (
            EFFECT_REFUSE,
            "It could be shared beyond you, and you have excluded shareable lessons "
            "from automatic acceptance.",
        )
    if facts.sharing_state != SHARING_NOT_SHARED:
        checks["already_shared"] = (
            EFFECT_ESCALATE,
            f"Its sharing state is {facts.sharing_state!r}, so a person should look at it.",
        )

    # -- how long a decision would stay good ---------------------------------------
    if (
        policy.expires_after_days is not None
        and facts.experience_age_days is not None
        and facts.experience_age_days > policy.expires_after_days
    ):
        checks["supporting_experience_expired"] = (
            EFFECT_ESCALATE,
            f"The experience behind it is {facts.experience_age_days:.0f} days old, past "
            f"the {policy.expires_after_days}-day limit you set.",
        )
    if (
        policy.review_interval_days is not None
        and facts.days_since_last_review is not None
        and facts.days_since_last_review > policy.review_interval_days
    ):
        checks["review_interval_elapsed"] = (
            EFFECT_ESCALATE,
            f"It has not been reviewed for {facts.days_since_last_review:.0f} days, past "
            f"the {policy.review_interval_days}-day review interval you set.",
        )

    for rule_id in RULE_ORDER:
        if rule_id in checks:
            effect, reason = checks[rule_id]
            fire(rule_id, effect, reason)

    if any(rule.effect == EFFECT_REFUSE for rule in matched):
        decision = DECISION_WOULD_REFUSE
    elif any(rule.effect == EFFECT_ESCALATE for rule in matched):
        decision = DECISION_WOULD_ESCALATE
    else:
        decision = DECISION_WOULD_ACCEPT

    reasons = [rule.reason for rule in matched]
    if not reasons:
        reasons = [
            "Every condition in your policy was met. Bartholomew has still not "
            "accepted it: preview mode cannot act, and acceptance needs your "
            "approval of this exact lesson.",
        ]

    return ShadowDecision(
        candidate_key=facts.candidate_key,
        candidate_fingerprint=facts.candidate_fingerprint,
        policy_revision=policy.revision,
        decision=decision,
        matched_rules=matched,
        reasons=reasons,
        evaluated_at=evaluated_at,
        candidate_review_state=candidate_review_state,
        facts=facts,
    )


# ---------------------------------------------------------------------------
# The sharing seam Session E connects to
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SharingInterface:
    """
    Everything this package claims about household sharing, and no more.

    Package D owns *eligibility* and *state as displayed*; Session E owns the
    transport and the group registry, and this wave implements neither. This
    dataclass is the whole seam between them: a narrow, read-only projection
    that a control-centre view can render truthfully today and that Session E
    can populate later without this package pretending anything was shared.

    `state` is `SHARING_NOT_SHARED` for everything this wave produces, and
    `transport_available` is False, so the UI can say "sharing is not
    connected yet" rather than showing an empty list that reads like "nothing
    has been shared".
    """

    eligible: bool
    state: str = SHARING_NOT_SHARED
    #: Session E sets this True once a transport exists. Nothing in Package D
    #: ever does.
    transport_available: bool = False
    #: Free text for the UI when `transport_available` is False.
    detail: str = "Household sharing is not connected in this release."

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "state": self.state,
            "transport_available": self.transport_available,
            "detail": self.detail,
        }


def sharing_for(facts: CandidateFacts) -> SharingInterface:
    """The sharing projection for one candidate. Never claims a share occurred."""
    return SharingInterface(eligible=facts.sharing_eligible, state=facts.sharing_state)


# ---------------------------------------------------------------------------
# Structural schema registration (privacy_guard)
# ---------------------------------------------------------------------------
# Same reasoning as `competency.py` and `candidate_learning.py`: these key
# names are schema, not content. Every *value* is still scanned in full.
# A registry write, not I/O -- this module still holds no connection and
# still writes nothing.


def _walk_keys(node: Any, into: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            into.add(key)
            _walk_keys(value, into)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _walk_keys(item, into)


def _policy_schema_keys() -> frozenset[str]:
    keys: set[str] = set()
    _walk_keys(default_policy().to_dict(), keys)
    return frozenset(keys)


def _evaluation_schema_keys() -> frozenset[str]:
    facts = CandidateFacts(
        candidate_key="_",
        candidate_fingerprint="_",
        lesson_category="_",
        classification="_",
        confidence=0.0,
        epistemic_status="_",
    )
    sample = ShadowDecision(
        candidate_key="_",
        candidate_fingerprint="_",
        policy_revision=0,
        decision=DECISION_WOULD_REFUSE,
        matched_rules=[MatchedRule(rule_id="_", effect=EFFECT_REFUSE, reason="_")],
        reasons=["_"],
        facts=facts,
    )
    keys: set[str] = set()
    _walk_keys(sample.to_dict(), keys)
    return frozenset(keys)


POLICY_SCHEMA_KEYS: frozenset[str] = _policy_schema_keys()
EVALUATION_SCHEMA_KEYS: frozenset[str] = _evaluation_schema_keys()

register_structural_schema(POLICY_KIND, POLICY_SCHEMA_KEYS)
register_structural_schema(EVALUATION_KIND, EVALUATION_SCHEMA_KEYS)


def encode(payload: dict[str, Any]) -> str:
    """Canonical JSON for a stored policy/evaluation row.

    `sort_keys` so two equal records serialise identically -- a stored form
    that varied with dict ordering would make revision comparison and audit
    diffing unreliable.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
