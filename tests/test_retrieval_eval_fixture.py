"""The retrieval-quality fixture and harness.

These tests check that the measurement apparatus is sound -- the fixture is
well-formed, the harness runs every mode, and a mode that cannot run is
recorded as such rather than as a score of zero.

**They deliberately assert nothing about retrieval quality.** No top-1 floor,
no top-3 target. The whole point of the fixture is to produce evidence before
anyone adjusts a relevance threshold; putting a pass mark here would create
pressure to tune the constant until the mark was met, which is exactly the
circular reasoning the approval for this work forbids. Quality numbers are
reported by `bartholomew embeddings evaluate` and recorded in
`docs/RETRIEVAL_EMBEDDER.md`, where they can be read together with the
embedder that produced them.
"""

from __future__ import annotations

import pytest

from bartholomew.kernel.retrieval_eval import EVAL_MODES, ModeReport, run_evaluation
from tests.fixtures.retrieval_eval_corpus import CASES, CORPUS

#: The categories the approved slice requires the fixture to cover.
REQUIRED_CATEGORIES = {
    "semantic_low_overlap",
    "paraphrase",
    "partial",
    "irrelevant",
    "competing",
    "sparse",
}


class TestFixtureIsWellFormed:
    def test_required_categories_are_covered(self):
        present = {category for category, _, _ in CASES}
        missing = REQUIRED_CATEGORIES - present
        assert not missing, f"fixture is missing required categories: {sorted(missing)}"

    def test_every_expectation_exists_in_the_corpus(self):
        for category, query, expected in CASES:
            for memory_id in expected:
                assert memory_id in CORPUS, (
                    f"case {query!r} ({category}) expects memory {memory_id}, "
                    "which is not in the corpus"
                )

    def test_irrelevant_cases_expect_nothing(self):
        for category, query, expected in CASES:
            if category == "irrelevant":
                assert expected == (), f"{query!r} is marked irrelevant but expects {expected}"

    def test_some_cases_have_no_correct_answer(self):
        """Without these, the fixture could only measure recall, never noise."""
        assert any(not expected for _, _, expected in CASES)


class TestHarnessRuns:
    @pytest.fixture
    def results(self, tmp_path):
        return run_evaluation(str(tmp_path / "eval.db"), CORPUS, CASES)

    def test_every_mode_is_reported(self, results):
        assert set(results["reports"]) == set(EVAL_MODES)

    def test_reports_carry_the_embedder_that_produced_them(self, results):
        # A score without its embedder is not evidence, so the harness must
        # never hand back numbers alone.
        retrieval = results["retrieval"]
        assert "embedding" in retrieval
        assert "semantic" in retrieval["embedding"]
        assert "mode_effective" in retrieval

    def test_each_case_is_attempted_once_per_runnable_mode(self, results):
        for mode, report in results["reports"].items():
            if report.error:
                continue
            assert len(report.cases) == len(CASES), f"{mode} did not run every case"

    def test_scores_are_proportions_of_answerable_cases(self, results):
        for report in results["reports"].values():
            if report.error or report.top1 is None:
                continue
            assert 0.0 <= report.top1 <= 1.0
            assert 0.0 <= report.top3 <= 1.0
            # top-3 counts a superset of what top-1 counts.
            assert report.top3 >= report.top1

    def test_irrelevant_cases_are_excluded_from_the_score(self, results):
        for report in results["reports"].values():
            if report.error:
                continue
            assert len(report.answerable) + len(report.irrelevant) == len(report.cases)
            assert all(case.expected for case in report.answerable)


class TestUnrunnableModeIsNotScoredZero:
    def test_error_is_distinct_from_a_zero_score(self):
        """ "Could not run" and "ran and found nothing" are different facts."""
        report = ModeReport(mode="vector", error="no embedder could be loaded")

        assert report.top1 is None
        assert report.top3 is None
        assert report.cases == []
