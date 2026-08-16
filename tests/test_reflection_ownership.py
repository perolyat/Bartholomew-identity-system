"""
Unit tests for the reflection-ownership contract (ROADMAP.md S5.4).

COGNITIVE_RUNTIME.md's "Reflection ownership" section records the approved
target architecture: ReflectionGenerator owns reflection composition and
final output; NarratorEngine's episodic narrative is supplementary evidence
supplied *to* that process.

These tests cover the two mechanisms that make that real, below the daemon
integration level:

1. `prompt_composer` puts the evidence *into the prompt*, so the authoritative
   composer interprets it rather than a caller appending it afterwards.
2. `ReflectionGenerator._compose_fallback_sections()` folds the evidence into
   the fallback templates as a *subsection of one document*, so an LLM outage
   does not silently discard the only real record of what happened -- and does
   not reintroduce two-document concatenation either.
"""

from __future__ import annotations

from datetime import datetime, timezone

from identity_interpreter.adapters.reflection_generator import ReflectionGenerator
from identity_interpreter.loader import load_identity
from identity_interpreter.orchestrator.prompt_composer import (
    compose_daily_reflection_prompt,
    compose_weekly_audit_prompt,
)

EVIDENCE = """# Daily Reflection - 2026-08-15

## The Day's Journey

### Goals & Achievements
- Goal added: finish the quarterly report

## Summary

- Total episodes: 1
"""


def _identity():
    return load_identity("Identity.yaml")


class TestEvidenceReachesThePrompt:
    def test_daily_prompt_carries_the_evidence(self):
        prompt = compose_daily_reflection_prompt(
            identity=_identity(),
            metrics={"nudges_count": 0, "pending_nudges": 0},
            memory_context="",
            date=datetime(2026, 8, 15, tzinfo=timezone.utc),
            timezone_str="UTC",
            episodic_evidence=EVIDENCE,
        )
        assert "finish the quarterly report" in prompt
        assert "Episodic evidence" in prompt

    def test_weekly_prompt_carries_the_evidence(self):
        prompt = compose_weekly_audit_prompt(
            identity=_identity(),
            weekly_scope={},
            memory_context="",
            iso_week=33,
            year=2026,
            episodic_evidence=EVIDENCE,
        )
        assert "finish the quarterly report" in prompt
        assert "Episodic evidence" in prompt

    def test_no_evidence_block_when_absent(self):
        """Absent evidence must not leave an empty scaffold in the prompt --
        that would invite the model to narrate having no episodes."""
        for evidence in (None, "", "   \n  "):
            prompt = compose_daily_reflection_prompt(
                identity=_identity(),
                metrics={"nudges_count": 0, "pending_nudges": 0},
                memory_context="",
                date=datetime(2026, 8, 15, tzinfo=timezone.utc),
                timezone_str="UTC",
                episodic_evidence=evidence,
            )
            assert "Episodic evidence" not in prompt

    def test_evidence_is_framed_as_source_material(self):
        """The framing is load-bearing: evidence presented as prose to echo
        would reproduce concatenation by another route."""
        prompt = compose_daily_reflection_prompt(
            identity=_identity(),
            metrics={},
            memory_context="",
            date=datetime(2026, 8, 15, tzinfo=timezone.utc),
            timezone_str="UTC",
            episodic_evidence=EVIDENCE,
        )
        assert "not text to copy" in prompt


class TestFallbackFoldsEvidenceIntoOneDocument:
    def test_evidence_title_is_dropped(self):
        section = ReflectionGenerator._compose_fallback_sections(EVIDENCE)
        assert "# Daily Reflection - 2026-08-15" not in section
        assert "finish the quarterly report" in section

    def test_evidence_headings_are_demoted(self):
        section = ReflectionGenerator._compose_fallback_sections(EVIDENCE)
        # "## The Day's Journey" -> "### The Day's Journey"
        assert "### The Day's Journey" in section
        # "### Goals & Achievements" -> "#### Goals & Achievements"
        assert "#### Goals & Achievements" in section

    def test_section_has_its_own_heading(self):
        section = ReflectionGenerator._compose_fallback_sections(EVIDENCE)
        assert "## Recorded Episodes" in section

    def test_empty_evidence_yields_nothing(self):
        for evidence in (None, "", "   \n  "):
            assert ReflectionGenerator._compose_fallback_sections(evidence) == ""

    def test_title_only_evidence_yields_nothing(self):
        """Evidence that is nothing but a title has no content to contribute,
        and must not produce an empty 'Recorded Episodes' heading."""
        assert ReflectionGenerator._compose_fallback_sections("# Daily Reflection\n") == ""

    def test_folded_section_introduces_no_second_title(self):
        section = ReflectionGenerator._compose_fallback_sections(EVIDENCE)
        assert not [ln for ln in section.splitlines() if ln.startswith("# ")]


class TestFallbackTemplatesCarryEvidence:
    def test_daily_fallback_includes_evidence(self):
        gen = object.__new__(ReflectionGenerator)  # no Identity/orchestrator needed
        result = ReflectionGenerator._fallback_daily_template(
            gen,
            metrics={"nudges_count": 0, "pending_nudges": 0},
            date=datetime(2026, 8, 15, tzinfo=timezone.utc),
            error="backend down",
            episodic_evidence=EVIDENCE,
        )
        assert "finish the quarterly report" in result["content"]
        assert result["success"] is False
        h1 = [ln for ln in result["content"].splitlines() if ln.startswith("# ")]
        assert len(h1) == 1

    def test_weekly_fallback_includes_evidence(self):
        gen = object.__new__(ReflectionGenerator)
        result = ReflectionGenerator._fallback_weekly_template(
            gen,
            iso_week=33,
            year=2026,
            error="backend down",
            episodic_evidence=EVIDENCE,
        )
        assert "finish the quarterly report" in result["content"]
        h1 = [ln for ln in result["content"].splitlines() if ln.startswith("# ")]
        assert len(h1) == 1

    def test_daily_fallback_without_evidence_is_unchanged_in_shape(self):
        gen = object.__new__(ReflectionGenerator)
        result = ReflectionGenerator._fallback_daily_template(
            gen,
            metrics={},
            date=datetime(2026, 8, 15, tzinfo=timezone.utc),
            error="backend down",
            episodic_evidence=None,
        )
        assert "Recorded Episodes" not in result["content"]
        assert "Intent for Tomorrow" in result["content"]
