"""
Confidence policy engine
Handles low confidence scenarios
"""

from ..models import Decision, Identity


def handle_low_confidence(
    identity: Identity,
    confidence_score: float,
) -> Decision:
    """
    Determine actions required for low confidence scenarios

    Args:
        identity: Identity configuration
        confidence_score: Confidence score (0.0 to 1.0)

    Returns:
        Decision with required actions
    """
    rationale = [
        "safety_and_alignment.confidence_policy.low_confidence_threshold",
    ]

    policy = identity.safety_and_alignment.confidence_policy
    threshold = policy.low_confidence_threshold

    is_low_confidence = confidence_score < threshold
    actions = []

    if is_low_confidence:
        actions = policy.actions
        rationale.append("safety_and_alignment.confidence_policy.actions")

    return Decision(
        decision={
            "is_low_confidence": is_low_confidence,
            "threshold": threshold,
            "score": confidence_score,
            "required_actions": actions,
        },
        rationale=rationale,
        confidence=confidence_score,
    )


def should_ask_clarification(
    identity: Identity,
    confidence_score: float,
) -> bool:
    """
    Check if clarifying question should be asked

    Args:
        identity: Identity configuration
        confidence_score: Confidence score

    Returns:
        True if clarification needed
    """
    policy = identity.safety_and_alignment.confidence_policy
    threshold = policy.low_confidence_threshold

    if confidence_score < threshold:
        return "ask_clarifying_question" in policy.actions

    return False
