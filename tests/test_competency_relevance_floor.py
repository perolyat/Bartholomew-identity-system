"""
S5.3 relevance floor: regression tests.

Governing invariant, stated by the approving review:

    **Being the best available retrieval result is not sufficient to make a
    competency applicable.**

A retriever always returns a nearest neighbour, so ranking alone cannot
express this -- something is always ranked first. Without a relevance
criterion an unrelated request applies whatever competency happens to exist,
and that record is then cited in the explanation-grade attribution
(Decision E.2) as the basis of a decision it had nothing to do with. False
attribution, not prompt noise, is the reason this matters.

The criterion is a **minimum count of shared meaningful terms** between the
request and the record's own text, not a retriever score threshold. Why, from
the measured characterisation recorded in the design's Sec.12:

  - scores are not comparable across retrieval modes (top scores of 1.000
    FTS, 0.009-0.128 vector, 0.25-0.94 hybrid on the same corpus);
  - vector scores were measured *anti-correlated* with relevance under the
    deterministic fallback embedder ("play some music" 0.128 vs "how should I
    handle the boiler quotes?" 0.009);
  - lexical overlap separated every measured category cleanly, identically in
    every mode.

The cases below mirror the six categories the characterisation covered.
"""

from __future__ import annotations

import pytest

from bartholomew.kernel import competency_reasoning as cr
from bartholomew.kernel.competency import (
    CompetencyEnvelope,
    CompetencyHeuristic,
    CompetencyKnowledge,
    CompetencyProcedure,
)

ESTATE = "estate_management"


def _procedure(competency_id=ESTATE, slug="quote_comparison", score=0.9):
    return cr.CompetencyCandidate(
        kind="competency_procedure",
        key=f"{competency_id}.{slug}",
        score=score,
        record=CompetencyProcedure(
            envelope=CompetencyEnvelope(competency_id=competency_id, confidence=0.85),
            slug=slug,
            name="Quote comparison",
            steps=[
                "Get at least three quotes for the boiler work",
                "Compare scope, price and warranty",
            ],
        ),
    )


def _heuristic(competency_id=ESTATE, slug="check_warranty", score=0.8):
    return cr.CompetencyCandidate(
        kind="competency_heuristic",
        key=f"{competency_id}.{slug}",
        score=score,
        record=CompetencyHeuristic(
            envelope=CompetencyEnvelope(competency_id=competency_id, confidence=0.85),
            slug=slug,
            rule="Check the warranty before recommending replacement of an appliance",
        ),
    )


def _knowledge(competency_id=ESTATE, slug="boiler_service", score=0.7):
    return cr.CompetencyCandidate(
        kind="competency_knowledge",
        key=f"{competency_id}.{slug}",
        score=score,
        record=CompetencyKnowledge(
            envelope=CompetencyEnvelope(competency_id=competency_id, confidence=0.85),
            slug=slug,
            topic="Heating systems",
            content="The boiler should be serviced annually before winter",
        ),
    )


def _travel(score=0.95):
    return cr.CompetencyCandidate(
        kind="competency_procedure",
        key="travel.flight_booking",
        score=score,
        record=CompetencyProcedure(
            envelope=CompetencyEnvelope(competency_id="travel", confidence=0.85),
            slug="flight_booking",
            name="Flight booking",
            steps=["Compare airline fares", "Check baggage allowance"],
        ),
    )


def _select(request: str, candidates):
    return cr.select_relevant(candidates, request_terms=cr.query_terms(request))


ESTATE_CORPUS = [_procedure(), _heuristic(), _knowledge()]


# ---------------------------------------------------------------------------
# The invariant itself.
# ---------------------------------------------------------------------------
def test_best_available_result_is_not_sufficient_to_be_applicable():
    """The whole point. A single unrelated competency, ranked first by
    definition because it is the only one, must still be excluded."""
    context = _select("remind me tomorrow", [_travel(score=1.0)])

    assert context.is_empty()
    assert context.excluded_not_relevant == 1


def test_excluded_records_cannot_appear_in_explanation_grade_attribution():
    """The consequence that makes this a correctness issue rather than a
    tuning preference."""
    context = _select("what is the capital of France", ESTATE_CORPUS)

    assert context.is_empty()
    payload = context.to_dict()
    assert payload["applied"] == []
    assert payload["competency_id"] is None
    assert payload["selection"]["excluded_not_relevant"] == len(ESTATE_CORPUS)


# ---------------------------------------------------------------------------
# Category 1: clearly relevant queries still work.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "request_text",
    [
        "how should I handle the boiler quotes?",
        "should I replace the boiler or repair it?",
    ],
)
def test_clearly_relevant_queries_still_apply(request_text):
    context = _select(request_text, ESTATE_CORPUS)

    assert not context.is_empty(), context.to_dict()
    assert context.competency_id == ESTATE


# ---------------------------------------------------------------------------
# Category 2: paraphrased relevant queries still work.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("request_text", "expected_term"),
    [
        ("what is the best way to compare contractor prices?", "compare"),
        ("how do I choose between tradespeople for the heating job?", "heating"),
    ],
)
def test_paraphrased_relevant_queries_still_apply(request_text, expected_term):
    """Reasonable paraphrase must survive: these share no exact phrase with
    the record, only a meaningful term."""
    context = _select(request_text, ESTATE_CORPUS)

    assert not context.is_empty(), context.to_dict()
    assert any(expected_term in item.matched_terms for item in context.applied)


# ---------------------------------------------------------------------------
# Category 3: partially related queries.
# ---------------------------------------------------------------------------
def test_partially_related_query_with_a_real_term_overlap_applies():
    """ "appliance" genuinely appears in the warranty rule, so this is a true
    positive, not a lucky one."""
    context = _select("the appliance is making a noise", ESTATE_CORPUS)

    assert not context.is_empty()
    assert any("appliance" in item.matched_terms for item in context.applied)


def test_vague_query_with_no_term_overlap_is_excluded():
    """ "who should I call about the house?" is about the estate in spirit but
    shares no term with any record. Excluded -- and that is the honest
    outcome: nothing recorded claims it informed the answer."""
    context = _select("who should I call about the house?", ESTATE_CORPUS)
    assert context.is_empty()


# ---------------------------------------------------------------------------
# Category 4: completely unrelated queries.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "request_text",
    ["remind me tomorrow", "what is the capital of France", "play some music"],
)
def test_unrelated_queries_are_excluded(request_text):
    context = _select(request_text, ESTATE_CORPUS)

    assert context.is_empty(), context.to_dict()
    assert context.excluded_not_relevant == len(ESTATE_CORPUS)


# ---------------------------------------------------------------------------
# Category 5: multiple competing competencies.
# ---------------------------------------------------------------------------
def test_relevance_gate_runs_before_competency_selection():
    """A high-scoring but irrelevant competency must not win the
    dominant-competency contest. Ordering matters: the gate is step 1."""
    candidates = [*ESTATE_CORPUS, _travel(score=1.0)]
    context = _select("should I replace the boiler?", candidates)

    assert context.competency_id == ESTATE
    assert all(item.competency_id == ESTATE for item in context.applied)


def test_a_shared_term_can_legitimately_select_another_competency():
    """The gate measures relevance, it does not hardcode a preferred domain:
    "compare airline fares" shares terms with travel, not estate."""
    candidates = [*ESTATE_CORPUS, _travel(score=0.9)]
    context = _select("compare airline fares for the flight", candidates)

    assert context.competency_id == "travel"


# ---------------------------------------------------------------------------
# Category 6: sparse corpus -- only one, unrelated, competency exists.
# ---------------------------------------------------------------------------
def test_sparse_corpus_with_only_an_unrelated_competency_applies_nothing():
    context = _select("should I replace the boiler?", [_travel(score=1.0)])

    assert context.is_empty()
    assert context.excluded_not_relevant == 1


def test_sparse_corpus_with_the_only_relevant_competency_still_applies():
    context = _select("compare airline fares", [_travel(score=0.2)])

    assert not context.is_empty()
    assert context.competency_id == "travel"


# ---------------------------------------------------------------------------
# Mechanism properties: deterministic, tunable, mode-independent.
# ---------------------------------------------------------------------------
def test_criterion_is_tunable():
    candidates = ESTATE_CORPUS

    # Stricter: require two shared terms. "boiler" alone no longer suffices.
    strict = cr.select_relevant(
        candidates,
        request_terms=cr.query_terms("replace the boiler"),
        min_shared_terms=2,
    )
    assert strict.is_empty()

    # Disabled: behaves as before the correction.
    disabled = cr.select_relevant(
        candidates,
        request_terms=cr.query_terms("remind me tomorrow"),
        min_shared_terms=0,
    )
    assert not disabled.is_empty()


def test_default_is_the_documented_value():
    assert cr.DEFAULT_MIN_SHARED_TERMS == 1


def test_gate_is_skipped_when_no_request_terms_are_supplied():
    """Direct callers that have already established relevance are
    unaffected."""
    context = cr.select_relevant(ESTATE_CORPUS)
    assert not context.is_empty()


def test_criterion_is_deterministic():
    first = _select("should I replace the boiler?", ESTATE_CORPUS).to_dict()
    second = _select("should I replace the boiler?", ESTATE_CORPUS).to_dict()
    assert first == second


def test_criterion_does_not_depend_on_retriever_score():
    """Score-independence is the property that makes this work identically in
    FTS, vector and hybrid mode -- the reason a score threshold was rejected."""
    low = _select("should I replace the boiler?", [_procedure(score=0.001)])
    high = _select("should I replace the boiler?", [_procedure(score=999.0)])

    assert not low.is_empty()
    assert not high.is_empty()

    unrelated_high = _select("play some music", [_procedure(score=999.0)])
    assert unrelated_high.is_empty()


def test_matched_terms_are_recorded_for_attribution():
    """A future explanation should be able to say *why* a record applied, not
    merely that it did."""
    context = _select("should I replace the boiler?", ESTATE_CORPUS)
    applied = context.to_dict()["applied"][0]

    assert applied["matched_terms"], "no relevance evidence recorded"
    assert "boiler" in applied["matched_terms"]


def test_no_model_based_judging_was_introduced():
    """The correction must stay deterministic and local."""
    import ast
    from pathlib import Path

    tree = ast.parse(Path(cr.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    for forbidden in ("openai", "anthropic", "transformers", "embedding_engine", "retrieval"):
        assert forbidden not in imported
