"""
Usable POC slice 1 -- the deterministic fact extractor, in isolation.

Covers `bartholomew/kernel/personal_facts.py`'s pure half: what it proposes,
what it deliberately leaves alone, and the properties the seam depends on
(determinism, stable keys, bounded output, no new memory kinds).

These tests pin *behaviour the rest of the slice relies on*, not the specific
phrasings the POC happens to recognise today. The pattern set is explicitly
scaffolding (see the module docstring and
`docs/POC_SLICE_1_MEMORY_CAPTURE_RECALL.md` Sec.1), so
`TestScaffoldingBoundary` asserts that status rather than freezing the
vocabulary.
"""

from __future__ import annotations

from bartholomew.kernel import personal_facts
from bartholomew.kernel.personal_facts import (
    PERSONAL_FACT_KINDS,
    extract_facts,
    record_from_row,
    render_facts_for_prompt,
)


class TestRecognisedFacts:
    def test_birthday_lands_on_the_existing_governed_key(self):
        (fact,) = extract_facts("my birthday is 3rd March")
        # `key: birthday` is the identity `memory_rules.yaml` already governs
        # with an always_keep rule -- not a new category.
        assert fact.kind == "user_profile"
        assert fact.key == "birthday"
        assert "3rd March" in fact.value

    def test_schedule_commitment_lands_on_user_schedule(self):
        (fact,) = extract_facts("I have a dentist appointment on the 20th")
        assert fact.kind == "user_schedule"
        assert fact.key == "dentist_appointment"
        assert "20th" in fact.value

    def test_preference_is_captured(self):
        (fact,) = extract_facts("I prefer window seats")
        assert fact.kind == "user_profile"
        assert fact.key.startswith("preference.")
        assert "window seats" in fact.value

    def test_profile_detail_is_captured(self):
        (fact,) = extract_facts("my partner's name is Jo")
        assert fact.kind == "user_profile"
        assert fact.key == "partner_name"
        assert "Jo" in fact.value

    def test_value_carries_both_subject_and_fact(self):
        """The stored text must contain the subject, not just the bare value.

        "3rd March" alone would be unretrievable from a later "when is my
        birthday?" -- the subject word is what the FTS query and the relevance
        gate both match on.
        """
        (fact,) = extract_facts("my birthday is 3rd March")
        lowered = fact.value.lower()
        assert "birthday" in lowered
        assert "march" in lowered


class TestLeavesOrdinaryConversationAlone:
    def test_greeting_proposes_nothing(self):
        assert extract_facts("hello there, how are you?") == []

    def test_question_proposes_nothing(self):
        assert extract_facts("what should I do about the boiler?") == []

    def test_conversational_framing_is_not_a_durable_fact(self):
        """ "my point is ..." matches the generic "my X is Y" shape but is
        framing, not a fact about the user."""
        assert extract_facts("my point is that this is wrong") == []

    def test_empty_and_blank_input(self):
        assert extract_facts("") == []
        assert extract_facts("   ") == []


class TestPropertiesTheSeamRelieson:
    def test_extraction_is_deterministic(self):
        message = "my birthday is 3rd March"
        assert extract_facts(message) == extract_facts(message)

    def test_restating_a_fact_produces_the_same_key(self):
        """Stable keys are what make `upsert_memory()` update in place rather
        than accumulate near-duplicate rows."""
        first = extract_facts("my birthday is 3rd March")[0]
        second = extract_facts("my birthday is 4th April")[0]
        assert first.key == second.key

    def test_output_is_bounded(self):
        crowded = (
            "my birthday is 1st Jan. I prefer aisle seats. "
            "my partner is Jo. I have a meeting at 5pm. my doctor is Smith."
        )
        assert len(extract_facts(crowded)) <= personal_facts._MAX_FACTS_PER_MESSAGE

    def test_value_is_bounded(self):
        fact = extract_facts("my birthday is " + "x" * 5000)[0]
        assert len(fact.value) < 400

    def test_clause_boundary_stops_a_runaway_capture(self):
        facts = extract_facts("my doctor is Dr Smith and I have a review on Friday")
        doctor = next(f for f in facts if f.key == "doctor")
        assert "review" not in doctor.value.lower()

    def test_only_ever_proposes_existing_governed_kinds(self):
        messages = [
            "my birthday is 3rd March",
            "I have a dentist appointment on the 20th",
            "I prefer window seats",
            "my partner's name is Jo",
            "my bank account number is 12345678",
        ]
        for message in messages:
            for fact in extract_facts(message):
                assert fact.kind in PERSONAL_FACT_KINDS

    def test_extractor_makes_no_storage_decision(self):
        """Sensitive content is still *proposed*; deciding it may not be
        stored is the governed write path's job, not the extractor's. If the
        extractor silently dropped it instead, the consent queue would never
        see it and the content would vanish with no record."""
        (fact,) = extract_facts("my bank account number is 12345678")
        assert fact.kind == "user_profile"
        assert "12345678" in fact.value


class TestRetrievalAdapter:
    def test_personal_fact_row_becomes_a_record(self):
        record = record_from_row(
            {"kind": "user_profile", "key": "birthday", "value": "Birthday: 3rd March"},
        )
        assert record is not None
        assert record.to_summary_text() == "Birthday: 3rd March"

    def test_competency_row_is_never_mistaken_for_a_fact(self):
        assert (
            record_from_row(
                {"kind": "competency_heuristic", "key": "estate.x", "value": "{}"},
            )
            is None
        )

    def test_blank_row_is_rejected(self):
        assert record_from_row({"kind": "user_profile", "key": "x", "value": "   "}) is None

    def test_envelope_confidence_is_unknown_not_low(self):
        """`None` means unknown, which `select_relevant()` keeps; a fabricated
        number would be invented evidence in explanation-grade output."""
        record = record_from_row(
            {"kind": "user_profile", "key": "birthday", "value": "Birthday: 3rd March"},
        )
        assert record.envelope.confidence is None

    def test_envelope_never_clears_a_review_requirement(self):
        record = record_from_row(
            {"kind": "user_profile", "key": "birthday", "value": "Birthday: 3rd March"},
        )
        assert record.envelope.supervision.requires_review is False


class TestPromptRendering:
    def test_empty_context_renders_nothing(self):
        from bartholomew.kernel.competency_reasoning import EMPTY_CONTEXT

        assert render_facts_for_prompt(EMPTY_CONTEXT) == ""
        assert render_facts_for_prompt(None) == ""

    def test_rendered_block_is_distinct_from_the_competency_block(self):
        from bartholomew.kernel.competency_reasoning import (
            AppliedRecord,
            CompetencyContext,
        )

        context = CompetencyContext(
            applied=(
                AppliedRecord(
                    kind="user_profile",
                    key="birthday",
                    competency_id=personal_facts.PERSONAL_MEMORY_ID,
                    classification="personal",
                    confidence=None,
                    provenance={},
                    requires_review=False,
                    review_reason=None,
                    summary="Birthday: 3rd March",
                    score=1.0,
                ),
            ),
            competency_id=personal_facts.PERSONAL_MEMORY_ID,
        )
        rendered = render_facts_for_prompt(context)
        assert "Known personal facts:" in rendered
        assert "Birthday: 3rd March" in rendered
        assert "Relevant competency" not in rendered


class TestScaffoldingBoundary:
    def test_module_records_its_scaffolding_status(self):
        """Approval clarification (2026-08-14): the pattern set is POC
        scaffolding, not the long-term boundary of memory capture. That status
        is load-bearing -- it is why the narrow vocabulary below is acceptable
        -- so it must not be quietly dropped from the module."""
        assert "scaffolding" in (personal_facts.__doc__ or "").lower()

    def test_module_is_pure_no_io(self):
        """Same discipline competency_reasoning.py holds to: the governed
        write and the retrieval read stay in the seam, so this module cannot
        become a second Memory authority."""
        import ast
        from pathlib import Path

        tree = ast.parse(Path(personal_facts.__file__).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        for forbidden in ("sqlite3", "aiosqlite", "requests", "os", "pathlib"):
            assert forbidden not in imported, f"{forbidden} implies I/O in a pure module"
