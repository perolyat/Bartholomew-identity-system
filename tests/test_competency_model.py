"""
Unit tests for bartholomew.kernel.competency (S5.1) -- pure data-shape
tests, no database. See docs/S5_1_COMPETENCY_ARCHITECTURE_DESIGN.md.
"""

from __future__ import annotations

from bartholomew.kernel.competency import (
    CLASSIFICATION_VALUES,
    COMPETENCY_KINDS,
    CompetencyEnvelope,
    CompetencyEvidence,
    CompetencyHeuristic,
    CompetencyKnowledge,
    CompetencyProcedure,
    CompetencyRecord,
    Provenance,
    Supervision,
    key_for,
)


def _envelope(**overrides) -> CompetencyEnvelope:
    defaults = {
        "competency_id": "estate_management",
        "classification": "personal",
        "provenance": Provenance(source_type="user_instruction", detail="test"),
        "confidence": 0.5,
    }
    defaults.update(overrides)
    return CompetencyEnvelope(**defaults)


class TestCompetencyKinds:
    def test_five_kinds_match_design_doc(self):
        assert COMPETENCY_KINDS == (
            "competency",
            "competency_knowledge",
            "competency_procedure",
            "competency_heuristic",
            "competency_evidence",
        )

    def test_kind_constants_match_class_attributes(self):
        assert CompetencyRecord.KIND == "competency"
        assert CompetencyKnowledge.KIND == "competency_knowledge"
        assert CompetencyProcedure.KIND == "competency_procedure"
        assert CompetencyHeuristic.KIND == "competency_heuristic"
        assert CompetencyEvidence.KIND == "competency_evidence"


class TestKeyFor:
    def test_naming_convention(self):
        assert (
            key_for("estate_management", "quote_comparison") == "estate_management.quote_comparison"
        )

    def test_competency_record_key_has_no_slug(self):
        rec = CompetencyRecord(envelope=_envelope(), name="Residential Estate Management")
        assert rec.key() == "estate_management"

    def test_child_records_use_key_for(self):
        proc = CompetencyProcedure(
            envelope=_envelope(),
            slug="quote_comparison",
            name="Quote Comparison",
            steps=["step 1"],
        )
        assert proc.key() == key_for("estate_management", "quote_comparison")


class TestEnvelopeValidation:
    def test_valid_envelope_has_no_errors(self):
        assert _envelope().validate() == []

    def test_rejects_unknown_classification(self):
        errors = _envelope(classification="shared_globally").validate()
        assert any("classification" in e for e in errors)

    def test_accepts_every_documented_classification_value(self):
        for value in CLASSIFICATION_VALUES:
            assert _envelope(classification=value).validate() == []

    def test_rejects_confidence_out_of_range(self):
        assert any("confidence" in e for e in _envelope(confidence=1.5).validate())
        assert any("confidence" in e for e in _envelope(confidence=-0.1).validate())

    def test_confidence_none_is_valid(self):
        assert _envelope(confidence=None).validate() == []

    def test_requires_competency_id(self):
        errors = _envelope(competency_id="").validate()
        assert any("competency_id" in e for e in errors)

    def test_rejects_unknown_provenance_source_type(self):
        errors = _envelope(provenance=Provenance(source_type="hearsay")).validate()
        assert any("source_type" in e for e in errors)

    def test_rejects_unknown_provenance_recorded_by(self):
        errors = _envelope(
            provenance=Provenance(source_type="user_instruction", recorded_by="the_cloud"),
        ).validate()
        assert any("recorded_by" in e for e in errors)


class TestEnvelopeRoundTrip:
    def test_to_dict_from_dict_round_trip(self):
        env = _envelope(
            classification="potentially_generalisable",
            supervision=Supervision(requires_review=True, reason="low confidence"),
            revision=3,
        )
        restored = CompetencyEnvelope.from_dict(env.to_dict())
        assert restored.to_dict() == env.to_dict()

    def test_from_dict_defaults_missing_optional_fields(self):
        env = CompetencyEnvelope.from_dict({"competency_id": "estate_management"})
        assert env.classification == "personal"
        assert env.confidence is None
        assert env.supervision.requires_review is False
        assert env.revision == 1


class TestRecordRoundTripsAndValidation:
    """One representative round-trip + validation test per kind."""

    def test_competency_record(self):
        rec = CompetencyRecord(
            envelope=_envelope(),
            name="Residential Estate Management",
            status="learning",
            description="Managing a residential property.",
            relevant_capabilities=["documents_read", "web_search"],
            proficiency={"overall": 0.3, "by_area": {"quote_comparison": 0.6}},
            known_gaps=["never handled a warranty dispute"],
        )
        assert rec.validate() == []
        restored = CompetencyRecord.from_dict(rec.to_dict())
        assert restored.to_dict() == rec.to_dict()
        assert "Residential Estate Management" in rec.to_summary_text()

    def test_competency_record_rejects_bad_status(self):
        rec = CompetencyRecord(envelope=_envelope(), name="X", status="thriving")
        assert any("status" in e for e in rec.validate())

    def test_competency_record_requires_name(self):
        rec = CompetencyRecord(envelope=_envelope(), name="")
        assert any("name" in e for e in rec.validate())

    def test_competency_knowledge(self):
        rec = CompetencyKnowledge(
            envelope=_envelope(),
            slug="hot_water_warranty_terms",
            topic="Hot water system warranty",
            content="The Rheem X unit carries a 6-year parts warranty.",
        )
        assert rec.validate() == []
        restored = CompetencyKnowledge.from_dict(rec.to_dict(), slug=rec.slug)
        assert restored.to_dict() == rec.to_dict()
        assert "warranty" in rec.to_summary_text().lower()

    def test_competency_knowledge_requires_content(self):
        rec = CompetencyKnowledge(envelope=_envelope(), slug="x", topic="t", content="")
        assert any("content" in e for e in rec.validate())

    def test_competency_procedure(self):
        rec = CompetencyProcedure(
            envelope=_envelope(classification="potentially_generalisable"),
            slug="quote_comparison",
            name="Quote Comparison",
            steps=["Get at least three quotes", "Compare scope, price, and warranty"],
            when_to_use="Before hiring a contractor for non-trivial work.",
            capability_refs=["web_search"],
        )
        assert rec.validate() == []
        restored = CompetencyProcedure.from_dict(rec.to_dict(), slug=rec.slug)
        assert restored.to_dict() == rec.to_dict()
        assert "Get at least three quotes" in rec.to_summary_text()

    def test_competency_procedure_requires_nonempty_steps(self):
        rec = CompetencyProcedure(envelope=_envelope(), slug="x", name="X", steps=[])
        assert any("steps" in e for e in rec.validate())

    def test_competency_heuristic(self):
        rec = CompetencyHeuristic(
            envelope=_envelope(classification="potentially_generalisable"),
            slug="check_warranty_before_replace",
            rule="Check warranty terms before recommending replacement.",
            conditions="Any appliance or system under 10 years old.",
            counterexamples=["Warranty already void due to prior unauthorised repair."],
        )
        assert rec.validate() == []
        restored = CompetencyHeuristic.from_dict(rec.to_dict(), slug=rec.slug)
        assert restored.to_dict() == rec.to_dict()
        assert "warranty" in rec.to_summary_text().lower()

    def test_competency_heuristic_requires_rule(self):
        rec = CompetencyHeuristic(envelope=_envelope(), slug="x", rule="")
        assert any("rule" in e for e in rec.validate())

    def test_competency_evidence(self):
        rec = CompetencyEvidence(
            envelope=_envelope(classification="personal"),
            slug="smith_plumbing_2026_repair",
            situation="Hot water system stopped heating in 2026.",
            action_taken="Called Smith Plumbing for a repair quote and follow-up.",
            outcome="Repaired for $340; issue did not recur for 14 months.",
            judgement_was_correct=True,
            lesson="Repair was the right call given warranty was still active.",
        )
        assert rec.validate() == []
        restored = CompetencyEvidence.from_dict(rec.to_dict(), slug=rec.slug)
        assert restored.to_dict() == rec.to_dict()
        assert "Hot water system" in rec.to_summary_text()

    def test_competency_evidence_requires_situation(self):
        rec = CompetencyEvidence(envelope=_envelope(), slug="x", situation="")
        assert any("situation" in e for e in rec.validate())

    def test_competency_evidence_judgement_was_correct_is_nullable(self):
        rec = CompetencyEvidence(envelope=_envelope(), slug="x", situation="s")
        assert rec.judgement_was_correct is None
        assert rec.validate() == []
