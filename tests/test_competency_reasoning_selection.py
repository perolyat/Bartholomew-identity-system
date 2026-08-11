"""
S5.3 Step 1 (reasoning core) tests.

Covers `competency_reasoning.select_relevant()` and `render_for_prompt()`
against the boundaries the approved design makes non-negotiable:

- supervision may only become stricter, never weaker;
- no cross-competency transfer (Decision C);
- the confidence floor treats `None` as *unknown*, not *low* (Decision D's
  tuning default);
- the record cap holds (prompt-bloat guard);
- output is explanation-grade (Decision E.2) and contains no chain-of-thought;
- the module stays pure logic, so a future deliberative Executive can replace
  the seam rather than inherit it (Sec.6.1).
"""

from __future__ import annotations

import ast
from pathlib import Path

from bartholomew.kernel import competency_reasoning as cr
from bartholomew.kernel.competency import (
    CompetencyEnvelope,
    CompetencyHeuristic,
    CompetencyKnowledge,
    CompetencyProcedure,
    Provenance,
    Supervision,
)


def _candidate(
    *,
    competency_id="estate_management",
    slug="r",
    rule="Check warranty before replacing",
    score=1.0,
    confidence=0.8,
    requires_review=False,
    review_reason=None,
    classification="personal",
) -> cr.CompetencyCandidate:
    envelope = CompetencyEnvelope(
        competency_id=competency_id,
        classification=classification,
        confidence=confidence,
        provenance=Provenance(source_type="user_instruction", detail="stated by the user"),
        supervision=Supervision(requires_review=requires_review, reason=review_reason),
    )
    record = CompetencyHeuristic(envelope=envelope, slug=slug, rule=rule)
    return cr.CompetencyCandidate(
        kind=record.KIND,
        key=record.key(),
        score=score,
        record=record,
    )


# ---------------------------------------------------------------------------
# 1. Baseline selection.
# ---------------------------------------------------------------------------
def test_empty_input_yields_empty_context():
    context = cr.select_relevant([])
    assert context.is_empty()
    assert cr.render_for_prompt(context) == ""


def test_relevant_records_are_applied_and_ranked_by_score():
    candidates = [
        _candidate(slug="low", score=0.1),
        _candidate(slug="high", score=0.9),
        _candidate(slug="mid", score=0.5),
    ]
    context = cr.select_relevant(candidates)

    assert [item.key.split(".")[-1] for item in context.applied] == ["high", "mid", "low"]
    assert context.competency_id == "estate_management"
    assert context.considered == 3


def test_unknown_confidence_ranks_below_evidenced_at_equal_relevance():
    """`None` means not-yet-evidenced, not low-confidence: kept, ranked after."""
    candidates = [
        _candidate(slug="unknown", score=0.5, confidence=None),
        _candidate(slug="evidenced", score=0.5, confidence=0.9),
    ]
    context = cr.select_relevant(candidates)

    assert [item.key.split(".")[-1] for item in context.applied] == ["evidenced", "unknown"]
    assert context.excluded_low_confidence == 0


# ---------------------------------------------------------------------------
# 2. Tuning defaults (approved as tunable, not permanent constants).
# ---------------------------------------------------------------------------
def test_confidence_floor_excludes_only_explicitly_low_records():
    candidates = [
        _candidate(slug="low", confidence=0.1),
        _candidate(slug="unknown", confidence=None),
        _candidate(slug="ok", confidence=0.9),
    ]
    context = cr.select_relevant(candidates)

    keys = {item.key.split(".")[-1] for item in context.applied}
    assert keys == {"unknown", "ok"}, "None must be treated as unknown, not low"
    assert context.excluded_low_confidence == 1


def test_confidence_floor_is_tunable_including_disabled():
    candidates = [_candidate(slug="low", confidence=0.1)]

    assert cr.select_relevant(candidates, confidence_floor=None).applied
    assert not cr.select_relevant(candidates, confidence_floor=0.5).applied


def test_record_cap_holds_and_reports_truncation():
    candidates = [_candidate(slug=f"r{i}", score=1.0 - i / 100) for i in range(20)]
    context = cr.select_relevant(candidates)

    assert len(context.applied) == cr.DEFAULT_MAX_RECORDS == 5
    assert context.truncated == 15


def test_record_cap_is_tunable():
    candidates = [_candidate(slug=f"r{i}") for i in range(10)]
    assert len(cr.select_relevant(candidates, max_records=2).applied) == 2


def test_defaults_match_the_approved_values():
    assert cr.DEFAULT_CONFIDENCE_FLOOR == 0.3
    assert cr.DEFAULT_MAX_RECORDS == 5


# ---------------------------------------------------------------------------
# 3. Decision C: no cross-competency transfer.
# ---------------------------------------------------------------------------
def test_selection_commits_to_a_single_competency():
    candidates = [
        _candidate(competency_id="estate_management", slug="estate", score=0.9),
        _candidate(competency_id="travel", slug="travel", score=0.4),
        _candidate(competency_id="vehicle", slug="vehicle", score=0.3),
    ]
    context = cr.select_relevant(candidates)

    assert context.competency_id == "estate_management"
    assert {item.competency_id for item in context.applied} == {"estate_management"}
    assert context.excluded_other_competencies == 2


def test_a_single_strong_match_beats_several_weak_ones_from_another_domain():
    """A specific question should be answered from the competency that
    actually matches it, not from whichever domain happens to have more
    records."""
    candidates = [
        _candidate(competency_id="estate_management", slug="strong", score=0.95),
        *[_candidate(competency_id="travel", slug=f"w{i}", score=0.2) for i in range(5)],
    ]
    context = cr.select_relevant(candidates)
    assert context.competency_id == "estate_management"


# ---------------------------------------------------------------------------
# 4. Supervision may only become stricter.
# ---------------------------------------------------------------------------
def test_any_applied_record_requiring_review_makes_the_context_require_review():
    candidates = [
        _candidate(slug="ordinary", score=0.9),
        _candidate(slug="sensitive", score=0.8, requires_review=True, review_reason="high impact"),
    ]
    context = cr.select_relevant(candidates)

    assert context.requires_review is True
    assert "high impact" in context.review_reasons


def test_a_record_not_requiring_review_cannot_clear_one_that_does():
    """The asymmetry: supervision is an OR. No ordering or majority of
    non-review records may relax a review requirement."""
    candidates = [
        _candidate(slug="needs", score=0.5, requires_review=True, review_reason="r"),
        *[_candidate(slug=f"ok{i}", score=0.9) for i in range(4)],
    ]
    context = cr.select_relevant(candidates)
    assert context.requires_review is True


def test_no_review_requirement_when_no_applied_record_asks_for_one():
    context = cr.select_relevant([_candidate(slug="ordinary")])
    assert context.requires_review is False


# ---------------------------------------------------------------------------
# 5. Decision E.2: explanation-grade output, and no chain-of-thought.
# ---------------------------------------------------------------------------
def test_applied_records_retain_identity_provenance_classification_confidence():
    context = cr.select_relevant(
        [_candidate(slug="warranty", confidence=0.75, classification="potentially_generalisable")],
    )
    item = context.applied[0]

    assert item.kind == "competency_heuristic"
    assert item.key == "estate_management.warranty"
    assert item.competency_id == "estate_management"
    assert item.classification == "potentially_generalisable"
    assert item.confidence == 0.75
    assert item.provenance["source_type"] == "user_instruction"
    assert item.provenance["detail"] == "stated by the user"


def test_to_dict_is_explanation_grade_not_a_flattened_summary():
    """A future explanation feature must be able to render this. A count or a
    summary string would foreclose that capability by omission."""
    context = cr.select_relevant([_candidate(slug="warranty")])
    payload = context.to_dict()

    assert payload["competency_id"] == "estate_management"
    assert isinstance(payload["applied"], list) and payload["applied"]

    record = payload["applied"][0]
    for required in ("kind", "key", "competency_id", "classification", "confidence", "provenance"):
        assert required in record, f"explanation-grade record is missing {required!r}"

    assert payload["selection"]["considered"] == 1


def test_context_carries_no_reasoning_trace_fields():
    """What is recorded is which stored records were applied -- never a model
    reasoning trace."""
    fields = set(cr.CompetencyContext.__dataclass_fields__) | set(
        cr.AppliedRecord.__dataclass_fields__,
    )
    forbidden = {"thoughts", "reasoning", "chain_of_thought", "deliberation", "rationale_text"}
    assert not (fields & forbidden)


# ---------------------------------------------------------------------------
# 6. Prompt rendering.
# ---------------------------------------------------------------------------
def test_render_produces_labelled_lines():
    procedure = CompetencyProcedure(
        envelope=CompetencyEnvelope(competency_id="estate_management", confidence=0.8),
        slug="quotes",
        name="Quote comparison",
        steps=["Get three quotes"],
    )
    knowledge = CompetencyKnowledge(
        envelope=CompetencyEnvelope(competency_id="estate_management", confidence=0.8),
        slug="boiler",
        topic="Boilers",
        content="Service annually",
    )
    context = cr.select_relevant(
        [
            cr.CompetencyCandidate(procedure.KIND, procedure.key(), 0.9, procedure),
            cr.CompetencyCandidate(knowledge.KIND, knowledge.key(), 0.8, knowledge),
        ],
    )
    rendered = cr.render_for_prompt(context)

    assert "Relevant competency (estate_management):" in rendered
    assert "[procedure] Quote comparison" in rendered
    assert "[knowledge] Boilers" in rendered


def test_render_surfaces_a_review_requirement():
    context = cr.select_relevant(
        [_candidate(requires_review=True, review_reason="high impact")],
    )
    rendered = cr.render_for_prompt(context)
    assert "requiring review" in rendered
    assert "high impact" in rendered


def test_render_of_empty_context_is_empty_string():
    """Decision D: no relevant competency means the request behaves exactly
    as it does today."""
    assert cr.render_for_prompt(cr.EMPTY_CONTEXT) == ""


# ---------------------------------------------------------------------------
# 7. Structural invariants (Sec.6.1: replaceable seam, pure logic).
# ---------------------------------------------------------------------------
def test_module_performs_no_io():
    source = Path(cr.__file__)
    tree = ast.parse(source.read_text(encoding="utf-8"))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    forbidden = {"sqlite3", "aiosqlite", "os", "requests", "httpx", "asyncio"}
    assert not (imported & forbidden), (
        f"competency_reasoning.py imports I/O machinery {sorted(imported & forbidden)} -- "
        "retrieval must stay with the governed retrieval layer"
    )


def test_selection_returns_structured_data_not_a_rendered_string():
    """Sec.6.1's replaceability requirement: a future deliberative Executive
    must be able to consume the same candidates and reach a different answer.
    That is only possible if selection yields structure, not prose."""
    context = cr.select_relevant([_candidate()])
    assert isinstance(context, cr.CompetencyContext)
    assert not isinstance(context, str)
    # Rendering is a separate, optional step.
    assert callable(cr.render_for_prompt)


def test_selection_is_deterministic():
    """Sec.6 boundary: no model call, so identical input yields identical
    output."""
    candidates = [_candidate(slug=f"r{i}", score=i / 10) for i in range(6)]
    first = cr.select_relevant(candidates).to_dict()
    second = cr.select_relevant(candidates).to_dict()
    assert first == second
