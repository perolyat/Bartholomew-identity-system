"""
Test reflection generation with Identity Interpreter
"""

from datetime import datetime

import pytest

from identity_interpreter.adapters.reflection_generator import ReflectionGenerator


def test_daily_reflection_stub_backend():
    """Test daily reflection generation with stub backend."""
    generator = ReflectionGenerator(identity_path="Identity.yaml")

    result = generator.generate_daily_reflection(
        metrics={
            "nudges_count": 3,
            "pending_nudges": 1,
        },
        date=datetime(2025, 10, 31),
        timezone_str="Australia/Brisbane",
        backend="stub",
    )

    # Verify structure
    assert "content" in result
    assert "success" in result
    assert "safety" in result
    assert "meta" in result

    # Verify content has expected sections
    content = result["content"]
    assert "Daily Reflection" in content
    assert "Summary" in content or "summary" in content.lower()

    # Verify safety checks ran
    safety = result["safety"]
    assert "blocked" in safety
    assert "violations" in safety
    assert "crisis_detected" in safety

    # Verify meta includes generator info
    meta = result["meta"]
    assert "generator" in meta
    assert "backend" in meta


def test_weekly_audit_stub_backend():
    """Test weekly audit generation with stub backend."""
    generator = ReflectionGenerator(identity_path="Identity.yaml")

    result = generator.generate_weekly_audit(
        weekly_scope={
            "reflections_count": 7,
            "policy_checks": 0,
            "safety_triggers": 0,
        },
        iso_week=44,
        year=2025,
        backend="stub",
    )

    # Verify structure
    assert "content" in result
    assert "success" in result
    assert "safety" in result
    assert "meta" in result

    # Verify content has expected sections
    content = result["content"]
    assert "Weekly Alignment Audit" in content
    assert "Week 44" in content

    # Verify safety checks
    assert not result["safety"]["blocked"]
    assert len(result["safety"]["violations"]) == 0


def test_safety_prompt_includes_red_lines():
    """Test that safety prompts include red lines from Identity.yaml."""
    from identity_interpreter.loader import load_identity
    from identity_interpreter.orchestrator.prompt_composer import compose_daily_reflection_prompt

    identity = load_identity("Identity.yaml")

    prompt = compose_daily_reflection_prompt(
        identity=identity,
        metrics={"nudges_count": 2, "pending_nudges": 0},
        memory_context="",
        date=datetime(2025, 10, 31),
        timezone_str="UTC",
    )

    # Verify safety preamble present
    assert "Safety and Policy Constraints" in prompt
    assert "red lines" in prompt.lower()

    # Verify at least one red line is included
    assert any(red_line.lower() in prompt.lower() for red_line in identity.red_lines)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
