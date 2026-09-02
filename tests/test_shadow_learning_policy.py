"""
The shadow learning policy engine: deterministic, explained, and inert.

`tests/test_learning_memory_control_centre.py` proves the *end-to-end*
governance properties against real stores. This module proves the properties
of the engine itself -- the ones that must hold before any store is involved,
and that a future change could break without any integration test noticing:

  * the module is pure (it imports nothing that can write, and its own source
    contains no writer);
  * the execution mode is a constant, not a setting, and no configuration
    reaches it;
  * a decision object cannot be used as, or converted into, authorization;
  * evaluation is deterministic, order-independent in outcome, and complete
    in its explanation;
  * every configurable dimension the contract names is actually read.

The last one matters more than it looks. A settings screen offering a control
that nothing consults is a lie told in a form, so each dimension gets a test
that changes only that dimension and asserts the decision moved.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

from bartholomew.kernel import candidate_learning, learning_policy
from bartholomew.kernel.competency import COMPETENCY_KINDS

MODULE_PATH = pathlib.Path(learning_policy.__file__)
AT = "2026-09-01T12:00:00+00:00"


def _facts(**overrides) -> learning_policy.CandidateFacts:
    """A candidate that a fully permissive policy would accept.

    Every test below starts from "would_accept" and changes one thing, so a
    failure names the dimension that stopped mattering rather than reporting
    that some rule somewhere fired.
    """
    base = {
        "candidate_key": "estate_management.lesson_from_objective_1",
        "candidate_fingerprint": "f" * 64,
        "lesson_category": "procedural",
        "classification": "personal",
        "confidence": 0.95,
        "epistemic_status": "inference",
        "supporting_experience_count": 3,
        "independent_experience_count": 3,
        "contradicting_evidence_count": 0,
        "risk_class": "low",
        "reversible": True,
        "affected_capabilities": ["estate_management"],
        "affected_applications": [],
        "privacy_class": None,
        "sharing_eligible": False,
        "sharing_state": learning_policy.SHARING_NOT_SHARED,
        "experience_age_days": 1.0,
        "days_since_last_review": 1.0,
    }
    base.update(overrides)
    return learning_policy.CandidateFacts(**base)


def _permissive(**overrides) -> learning_policy.LearningPolicy:
    base = {
        "revision": 7,
        "enabled_categories": ["procedural"],
        "excluded_categories": [],
        "max_risk": "critical",
        "require_reversible": False,
        "min_supporting_experiences": 1,
        "min_confidence": 0.0,
        "contradiction_behaviour": learning_policy.CONTRADICTION_REFUSE,
        "max_affected_capabilities": 9,
        "max_affected_applications": 9,
        "excluded_privacy_classes": [],
        "excluded_classifications": [],
        "exclude_sharing_eligible": False,
        "expires_after_days": None,
        "review_interval_days": None,
    }
    base.update(overrides)
    return learning_policy.LearningPolicy(**base)


def _decide(policy=None, facts=None):
    return learning_policy.evaluate(
        policy or _permissive(),
        facts or _facts(),
        evaluated_at=AT,
    )


# ===========================================================================
# The module cannot act
# ===========================================================================


def test_the_policy_module_imports_nothing_that_can_write():
    """
    Property 1 of the structural prohibition, checked against the source.

    A module that cannot reach a store cannot persist an acceptance, however
    it is called. The one import it does have -- `privacy_guard`'s schema
    registry -- is a registry write, not I/O, and is the same one
    `competency.py` and `candidate_learning.py` make.
    """
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    forbidden = {
        "aiosqlite",
        "sqlite3",
        "bartholomew.kernel.memory_store",
        "bartholomew.kernel.runtime_contract",
        "bartholomew.kernel.learning_authorization",
        "bartholomew.kernel.training",
    }
    assert not (imported & forbidden), f"the policy engine imported {imported & forbidden}"

    # The complete set, pinned. Everything here is either the standard library
    # or one of this repository's pure-data modules -- none of them holds a
    # connection or writes a memory. A new import is a deliberate decision, and
    # this assertion is where it gets made.
    assert imported == {
        "__future__",
        "json",
        "dataclasses",
        "typing",
        "bartholomew.kernel",
        "bartholomew.kernel.competency",
        "bartholomew.kernel.memory.privacy_guard",
    }


def test_the_policy_module_never_names_an_approval_type():
    """
    Property 2: nothing here constructs authorization.

    Checked over the syntax tree's *identifiers* rather than the source text,
    so the prose that explains why the approval type is absent does not count
    as naming it -- while an actual reference, of any shape, does.
    """
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    identifiers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
        elif isinstance(node, ast.alias):
            identifiers.add(node.asname or node.name.rsplit(".", 1)[-1])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            identifiers.add(node.name)

    for forbidden in (
        "LearningAcceptanceApproval",
        "grant_learning_acceptance_approval",
        "fingerprint_for",
        "learning_authorization",
        "upsert_memory",
        "accept",
    ):
        assert forbidden not in identifiers, f"the policy engine references {forbidden!r}"


def test_a_decision_never_authorizes_acceptance():
    """Property 2, on the object a caller actually holds."""
    decision = _decide()
    assert decision.decision == learning_policy.DECISION_WOULD_ACCEPT
    assert decision.authorizes_acceptance is False
    assert decision.consolidated is False
    assert decision.to_dict()["authorizes_acceptance"] is False

    # And it is a property with no setter: nothing can flip it.
    with pytest.raises(AttributeError):
        decision.authorizes_acceptance = True


def test_the_execution_mode_is_a_constant_not_a_setting():
    """
    Property 4.

    A user may record that they want automatic acceptance. The mode does not
    move, and neither does `auto_acceptance_enabled`.
    """
    assert learning_policy.SHIPPED_EXECUTION_MODE == "shadow"

    policy = _permissive(requested_execution_mode=learning_policy.REQUESTED_MODE_AUTO)
    assert policy.requested_execution_mode == "auto"
    assert policy.execution_mode == "shadow"
    assert policy.auto_acceptance_enabled is False

    with pytest.raises(AttributeError):
        policy.execution_mode = "auto"

    # Round-tripping through storage does not launder it either.
    revived = learning_policy.LearningPolicy.from_dict(policy.to_dict())
    assert revived.execution_mode == "shadow"
    assert revived.auto_acceptance_enabled is False


def test_a_hand_edited_execution_mode_in_storage_changes_nothing():
    """
    Property 4, against the row rather than the API.

    `execution_mode` is written into the stored form so an archived revision
    states which regime it ran under. It is never read back, so editing the
    database by hand does not turn preview into action.
    """
    payload = _permissive().to_dict()
    payload["execution_mode"] = "auto"
    payload["auto_acceptance_enabled"] = True

    revived = learning_policy.LearningPolicy.from_dict(payload)
    assert revived.execution_mode == "shadow"
    assert revived.auto_acceptance_enabled is False


def test_the_forbidden_write_kinds_cover_every_way_a_lesson_becomes_knowledge():
    """
    Property 5: the enumeration is complete, and stays complete.

    If a sixth competency kind is added, this fails until it is named -- which
    is the point of enumerating rather than deriving.
    """
    from bartholomew.kernel import learning_authorization

    forbidden = learning_policy.FORBIDDEN_SHADOW_WRITE_KINDS
    assert candidate_learning.KIND in forbidden
    # The approval kind is a literal in the policy module (so that module need
    # not import the machinery that constructs an approval). Pinned here, so
    # renaming the kind fails a test rather than silently unguarding it.
    assert learning_policy.APPROVAL_KIND == learning_authorization.KIND
    assert learning_authorization.KIND in forbidden
    for kind in COMPETENCY_KINDS:
        assert kind in forbidden, f"{kind} is not named in FORBIDDEN_SHADOW_WRITE_KINDS"
    assert len(forbidden) == len(COMPETENCY_KINDS) + 2

    # And the kinds this package writes are not retrievable as knowledge.
    for kind in (
        learning_policy.POLICY_KIND,
        learning_policy.EVALUATION_KIND,
        learning_policy.CANDIDATE_REVISION_KIND,
    ):
        assert kind not in COMPETENCY_KINDS
        assert kind not in forbidden


def test_evaluate_has_no_hidden_input():
    """
    Determinism needs the clock passed in, not read.

    A function that stamped its own timestamp could not be compared run to
    run, which would make "deterministic" untestable rather than merely
    unproven.
    """
    signature = inspect.signature(learning_policy.evaluate)
    assert "evaluated_at" in signature.parameters
    assert signature.parameters["evaluated_at"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["evaluated_at"].default is inspect.Parameter.empty


# ===========================================================================
# Determinism and explanation
# ===========================================================================


def test_the_same_inputs_produce_a_byte_identical_decision():
    policy, facts = _permissive(), _facts(confidence=0.1, risk_class="high")
    first = learning_policy.evaluate(policy, facts, evaluated_at=AT)
    second = learning_policy.evaluate(policy, facts, evaluated_at=AT)
    assert first.to_dict() == second.to_dict()


def test_every_decision_is_one_of_exactly_three():
    for facts in (
        _facts(),
        _facts(confidence=0.0),
        _facts(sharing_state=learning_policy.SHARING_OFFERED),
    ):
        assert _decide(facts=facts).decision in learning_policy.DECISIONS
    assert learning_policy.DECISIONS == {
        "would_accept",
        "would_refuse",
        "would_escalate",
    }


def test_matched_rules_are_reported_in_the_declared_order():
    """
    Order is part of the contract, not an artefact of dict insertion.

    Two rules fire here in an order the checks are *not* written in, so a
    regression to "whatever order the code happened to run" is visible.
    """
    decision = _decide(
        policy=_permissive(min_confidence=0.9, max_affected_applications=0),
        facts=_facts(confidence=0.1, affected_applications=["calendar"]),
    )
    fired = [rule.rule_id for rule in decision.matched_rules]
    assert fired == ["confidence_below_threshold", "too_many_affected_applications"]
    assert fired == [r for r in learning_policy.RULE_ORDER if r in fired]


def test_every_matching_rule_is_reported_not_just_the_first():
    """A user reading the explanation gets the whole reason, not the first one."""
    decision = _decide(
        policy=_permissive(
            min_confidence=0.9,
            max_risk="low",
            require_reversible=True,
            min_supporting_experiences=5,
        ),
        facts=_facts(confidence=0.1, risk_class="critical", reversible=False),
    )
    fired = {rule.rule_id for rule in decision.matched_rules}
    assert fired == {
        "risk_above_maximum",
        "not_reversible",
        "insufficient_supporting_experiences",
        "confidence_below_threshold",
    }
    assert len(decision.reasons) == len(decision.matched_rules)


def test_a_refusal_outranks_an_escalation():
    """The decision is a pure function of the matched set, worst effect wins."""
    decision = _decide(
        policy=_permissive(min_confidence=0.9, max_affected_applications=0),
        facts=_facts(confidence=0.1, affected_applications=["calendar"]),
    )
    effects = {rule.effect for rule in decision.matched_rules}
    assert effects == {learning_policy.EFFECT_REFUSE, learning_policy.EFFECT_ESCALATE}
    assert decision.decision == learning_policy.DECISION_WOULD_REFUSE


def test_an_acceptance_still_explains_that_nothing_happened():
    """The one case where saying nothing would be most misleading."""
    decision = _decide()
    assert decision.decision == learning_policy.DECISION_WOULD_ACCEPT
    assert decision.matched_rules == []
    assert len(decision.reasons) == 1
    reason = decision.reasons[0]
    assert "has still not accepted it" in reason
    assert "approval" in reason


def test_reasons_are_written_for_a_person():
    """No rule identifiers, no field names, no bare booleans."""
    decision = _decide(
        policy=_permissive(min_confidence=0.9, require_reversible=True),
        facts=_facts(confidence=0.2, reversible=False),
    )
    for rule in decision.matched_rules:
        assert rule.reason[0].isupper()
        assert rule.reason.endswith(".")
        assert "_" not in rule.reason.replace("would_", "")


def test_an_unknown_risk_class_ranks_above_critical():
    """Unknown risk is not low risk. A future vocabulary fails towards refusal."""
    decision = _decide(
        policy=_permissive(max_risk="critical"),
        facts=_facts(risk_class="catastrophic"),
    )
    assert decision.decision == learning_policy.DECISION_WOULD_REFUSE
    assert any(r.rule_id == "risk_above_maximum" for r in decision.matched_rules)


# ===========================================================================
# Every configurable dimension is actually read
# ===========================================================================


@pytest.mark.parametrize(
    ("policy_kwargs", "facts_kwargs", "rule_id", "expected"),
    [
        (
            {"excluded_categories": ["procedural"]},
            {},
            "category_excluded",
            learning_policy.DECISION_WOULD_REFUSE,
        ),
        (
            {"enabled_categories": []},
            {},
            "category_not_enabled",
            learning_policy.DECISION_WOULD_REFUSE,
        ),
        (
            {"max_risk": "low"},
            {"risk_class": "high"},
            "risk_above_maximum",
            learning_policy.DECISION_WOULD_REFUSE,
        ),
        (
            {"require_reversible": True},
            {"reversible": False},
            "not_reversible",
            learning_policy.DECISION_WOULD_REFUSE,
        ),
        (
            {"min_supporting_experiences": 4},
            {"independent_experience_count": 1},
            "insufficient_supporting_experiences",
            learning_policy.DECISION_WOULD_REFUSE,
        ),
        (
            {"min_confidence": 0.9},
            {"confidence": 0.4},
            "confidence_below_threshold",
            learning_policy.DECISION_WOULD_REFUSE,
        ),
        (
            {"contradiction_behaviour": learning_policy.CONTRADICTION_REFUSE},
            {"contradicting_evidence_count": 1},
            "contradictory_evidence",
            learning_policy.DECISION_WOULD_REFUSE,
        ),
        (
            {"contradiction_behaviour": learning_policy.CONTRADICTION_ESCALATE},
            {"contradicting_evidence_count": 1},
            "contradictory_evidence",
            learning_policy.DECISION_WOULD_ESCALATE,
        ),
        (
            {"max_affected_capabilities": 0},
            {},
            "too_many_affected_capabilities",
            learning_policy.DECISION_WOULD_ESCALATE,
        ),
        (
            {"max_affected_applications": 0},
            {"affected_applications": ["calendar"]},
            "too_many_affected_applications",
            learning_policy.DECISION_WOULD_ESCALATE,
        ),
        (
            {"excluded_privacy_classes": ["user.health"]},
            {"privacy_class": "user.health"},
            "privacy_class_excluded",
            learning_policy.DECISION_WOULD_REFUSE,
        ),
        (
            {"excluded_classifications": ["potentially_generalisable"]},
            {"classification": "potentially_generalisable"},
            "classification_excluded",
            learning_policy.DECISION_WOULD_REFUSE,
        ),
        (
            {"exclude_sharing_eligible": True},
            {"sharing_eligible": True},
            "sharing_eligible_excluded",
            learning_policy.DECISION_WOULD_REFUSE,
        ),
        (
            {},
            {"sharing_state": learning_policy.SHARING_SHARED},
            "already_shared",
            learning_policy.DECISION_WOULD_ESCALATE,
        ),
        (
            {"expires_after_days": 30},
            {"experience_age_days": 400.0},
            "supporting_experience_expired",
            learning_policy.DECISION_WOULD_ESCALATE,
        ),
        (
            {"review_interval_days": 30},
            {"days_since_last_review": 400.0},
            "review_interval_elapsed",
            learning_policy.DECISION_WOULD_ESCALATE,
        ),
        (
            {},
            {"epistemic_status": "observation"},
            "epistemic_status_not_inference",
            learning_policy.DECISION_WOULD_REFUSE,
        ),
    ],
)
def test_each_configured_dimension_changes_the_decision(
    policy_kwargs,
    facts_kwargs,
    rule_id,
    expected,
):
    """
    Every dimension the contract names is read, and each on its own.

    Starting from an otherwise-accepting configuration means a failure here
    says exactly which control stopped doing anything.
    """
    decision = learning_policy.evaluate(
        _permissive(**policy_kwargs),
        _facts(**facts_kwargs),
        evaluated_at=AT,
    )
    assert decision.decision == expected
    fired = [rule.rule_id for rule in decision.matched_rules]
    assert fired == [rule_id], f"expected only {rule_id}, got {fired}"


def test_every_declared_rule_is_reachable():
    """`RULE_ORDER` is the contract; an unreachable entry in it is a lie."""
    covered = {
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
    }
    assert set(learning_policy.RULE_ORDER) == covered
    assert len(learning_policy.RULE_ORDER) == len(set(learning_policy.RULE_ORDER))


# ===========================================================================
# Conservative defaults
# ===========================================================================


def test_the_default_policy_refuses_what_this_wave_can_produce():
    """
    A brand-new runtime accepts nothing, even hypothetically.

    Not load-bearing for safety -- the execution mode is -- but a default that
    *looked* permissive would misrepresent the system to whoever reads it
    first.
    """
    policy = learning_policy.default_policy()
    assert policy.validate() == []
    assert policy.revision == 0
    assert policy.enabled_categories == []
    assert policy.min_confidence > candidate_learning.SINGLE_EXPERIENCE_CONFIDENCE
    assert policy.min_supporting_experiences > 1
    assert policy.require_reversible is True
    assert policy.max_risk == "low"
    assert policy.exclude_sharing_eligible is True

    decision = learning_policy.evaluate(policy, _facts(), evaluated_at=AT)
    assert decision.decision == learning_policy.DECISION_WOULD_REFUSE


def test_unassessed_candidate_dimensions_default_to_the_cautious_answer():
    """
    Derivation from a lesson never invents a permissive value.

    A lesson nobody has assessed for risk is treated as `critical`, and one
    nobody has assessed for reversibility as irreversible -- so an unmeasured
    dimension can only make a preview stricter.
    """

    class _Source:
        objective_id = 1
        supporting_event_ids = [1, 2]

    class _Lesson:
        competency_id = "estate_management"
        slug = "lesson_from_objective_1"
        lesson_kind = "procedural"
        classification = "personal"
        confidence = 0.4
        epistemic_status = "inference"
        source = _Source()

    facts = learning_policy.facts_from_lesson(_Lesson(), "f" * 64)
    assert facts.risk_class == "critical"
    assert facts.reversible is False
    assert facts.affected_applications == []
    assert facts.independent_experience_count == 1, "one objective is one experience"
    assert facts.supporting_experience_count == 2


def test_a_verbose_objective_is_still_one_experience():
    """
    Ten evidence events from one objective do not corroborate each other.

    Counting events instead of objectives would let a chatty objective clear
    a "two independent experiences" threshold on its own, which is the exact
    shape of the mistake the threshold exists to prevent.
    """

    class _Source:
        objective_id = 1
        supporting_event_ids = list(range(1, 11))

    class _Lesson:
        competency_id = "c"
        slug = "s"
        lesson_kind = "procedural"
        classification = "personal"
        confidence = 0.9
        epistemic_status = "inference"
        source = _Source()

    facts = learning_policy.facts_from_lesson(
        _Lesson(),
        "f" * 64,
        risk_class="low",
        reversible=True,
    )
    decision = learning_policy.evaluate(
        _permissive(min_supporting_experiences=2),
        facts,
        evaluated_at=AT,
    )
    assert decision.decision == learning_policy.DECISION_WOULD_REFUSE
    assert any(
        rule.rule_id == "insufficient_supporting_experiences" for rule in decision.matched_rules
    )


def test_the_risk_vocabulary_matches_the_candidate_module():
    """Two tuples, one vocabulary. Drift would make a reviewer's choice
    unevaluable by the policy that reads it."""
    assert learning_policy.RISK_CLASSES == candidate_learning.RISK_CLASSES


# ===========================================================================
# Validation
# ===========================================================================


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"max_risk": "extreme"}, "max_risk"),
        ({"contradiction_behaviour": "ignore"}, "contradiction_behaviour"),
        ({"requested_execution_mode": "always"}, "requested_execution_mode"),
        ({"min_supporting_experiences": 0}, "min_supporting_experiences"),
        ({"min_confidence": 1.5}, "min_confidence"),
        ({"max_affected_applications": -1}, "max_affected_applications"),
        ({"expires_after_days": 0}, "expires_after_days"),
        ({"review_interval_days": 0}, "review_interval_days"),
    ],
)
def test_invalid_policies_are_rejected(kwargs, fragment):
    errors = _permissive(**kwargs).validate()
    assert any(fragment in error for error in errors), errors


def test_contradiction_cannot_be_configured_to_ignore_evidence():
    """
    Evidence against a lesson is never something a policy may disregard.

    The vocabulary has two members and neither is "ignore" -- a deliberate
    omission rather than an oversight.
    """
    assert learning_policy.CONTRADICTION_BEHAVIOURS == {"refuse", "escalate"}
    assert "ignore" not in learning_policy.CONTRADICTION_BEHAVIOURS


def test_normalisation_makes_the_stored_form_stable():
    """Two policies a user would call the same must serialise the same."""
    a = _permissive(excluded_categories=["b", "a", "a", " b "])
    b = _permissive(excluded_categories=["a", "b"])
    assert a.normalised().to_dict() == b.normalised().to_dict()


# ===========================================================================
# The sharing seam Session E connects to
# ===========================================================================


def test_the_sharing_projection_never_claims_a_share_occurred():
    """
    Package D owns eligibility; Session E owns transport, and does not exist
    yet. The projection says so rather than showing an empty list that reads
    like "nothing has been shared".
    """
    sharing = learning_policy.sharing_for(_facts(sharing_eligible=True))
    assert sharing.eligible is True
    assert sharing.state == learning_policy.SHARING_NOT_SHARED
    assert sharing.transport_available is False
    assert "not connected" in sharing.detail
    assert set(sharing.to_dict()) == {"eligible", "state", "transport_available", "detail"}
