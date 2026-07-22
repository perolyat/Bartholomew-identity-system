"""
Tests for policy engines
"""

from pathlib import Path

import pytest

from bartholomew.kernel.policy_engine import evaluate_tool_policy
from identity_interpreter.identity_context import build_identity_context
from identity_interpreter.loader import load_identity
from identity_interpreter.policies import (
    check_red_lines,
    handle_low_confidence,
    select_model,
)


@pytest.fixture
def identity():
    """Load test identity"""
    identity_path = Path("Identity.yaml")
    if not identity_path.exists():
        pytest.skip("Identity.yaml not found")
    return load_identity(identity_path)


def test_model_selection(identity):
    """Test model selection for different task types"""
    # General task
    decision = select_model(identity, task_type="general")
    assert "model" in decision.decision
    assert len(decision.rationale) > 0

    # Code task
    decision = select_model(identity, task_type="code")
    assert "model" in decision.decision


def test_tool_policy(identity):
    """Test tool allowlist via the authoritative Executive-side policy engine.

    (The deprecated identity_interpreter.policies.tool_policy.check_tool_allowed
    was removed in item 11.14; evaluate_tool_policy over a declarative
    IdentityContext is its successor -- the same path the CLI `explain --tool`
    command now uses.)
    """
    context = build_identity_context(identity)

    # Allowed tool (in Identity.yaml's tool_use.allowlist)
    decision = evaluate_tool_policy(context, "web_fetch")
    assert decision.allowed is True

    # Not allowed tool
    decision = evaluate_tool_policy(context, "unknown_tool")
    assert decision.allowed is False


def test_red_lines(identity):
    """Test red line checks"""
    # Clean text
    decision = check_red_lines(identity, "Hello, how are you?")
    assert decision.decision["blocked"] is False

    # Potentially violating text
    decision = check_red_lines(identity, "I am a human, not an AI")
    # May or may not block depending on keywords


def test_confidence_policy(identity):
    """Test confidence threshold handling"""
    # Low confidence
    decision = handle_low_confidence(identity, 0.3)
    assert decision.decision["is_low_confidence"] is True
    assert len(decision.decision["required_actions"]) > 0

    # High confidence
    decision = handle_low_confidence(identity, 0.9)
    assert decision.decision["is_low_confidence"] is False
