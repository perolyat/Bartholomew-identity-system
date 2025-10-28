"""
Safety policy engine
Enforces red lines, sensitive modes, and crisis protocols
"""

from ..models import Decision, Identity


def check_red_lines(
    identity: Identity,
    output_text: str,
    context: dict | None = None,
) -> Decision:
    """
    Check if output violates any red lines

    Args:
        identity: Identity configuration
        output_text: Text to check
        context: Optional context

    Returns:
        Decision with violation status and violated rules
    """
    rationale = ["red_lines"]
    violations = []

    red_lines = identity.red_lines

    # Check for potential violations (simple keyword matching)
    # In production, use more sophisticated NLP
    keywords = {
        "deception": ["pretend to be human", "i am human", "not an ai"],
        "manipulation": ["you must", "you have to", "no choice"],
        "romantic": ["i love you", "romantic", "date me"],
    }

    lower_text = output_text.lower()

    for line in red_lines:
        line_lower = line.lower()
        if "deception" in line_lower:
            for keyword in keywords.get("deception", []):
                if keyword in lower_text:
                    violations.append(
                        {
                            "red_line": line,
                            "keyword": keyword,
                        },
                    )
        elif "manipulation" in line_lower:
            for keyword in keywords.get("manipulation", []):
                if keyword in lower_text:
                    violations.append(
                        {
                            "red_line": line,
                            "keyword": keyword,
                        },
                    )
        elif "romantic" in line_lower or "sexual" in line_lower:
            for keyword in keywords.get("romantic", []):
                if keyword in lower_text:
                    violations.append(
                        {
                            "red_line": line,
                            "keyword": keyword,
                        },
                    )

    return Decision(
        decision={
            "violations": violations,
            "blocked": len(violations) > 0,
        },
        rationale=rationale,
    )


def check_sensitive_mode(
    identity: Identity,
    mode_id: str,
) -> Decision:
    """
    Check if sensitive mode is allowed and get constraints

    Args:
        identity: Identity configuration
        mode_id: ID of sensitive mode (e.g., 'medical', 'legal')

    Returns:
        Decision with mode status and constraints
    """
    rationale = ["safety_and_alignment.sensitive_modes"]

    sensitive_modes = identity.safety_and_alignment.sensitive_modes

    # Find matching mode
    mode_config = None
    for mode in sensitive_modes:
        if mode.id == mode_id:
            mode_config = mode
            break

    if not mode_config:
        return Decision(
            decision={
                "allowed": False,
                "reason": f"Mode '{mode_id}' not defined",
            },
            rationale=rationale,
        )

    return Decision(
        decision={
            "allowed": mode_config.allowed,
            "consent_required": mode_config.consent_required or False,
            "response_policy": mode_config.response_policy,
            "constraints": mode_config.constraints or [],
        },
        rationale=rationale,
        requires_consent=mode_config.consent_required or False,
    )


def get_crisis_protocols(identity: Identity) -> dict:
    """
    Get crisis protocol configuration

    Returns:
        Dict with trigger signals and behaviors
    """
    protocols = identity.safety_and_alignment.crisis_protocols
    return {
        "trigger_signals": protocols.trigger_signals,
        "behavior": protocols.behavior,
        "geo_source": protocols.geo_source,
    }


def check_for_crisis_signals(
    identity: Identity,
    text: str,
) -> Decision:
    """
    Check text for crisis signals (self-harm, violence, etc.)

    Args:
        identity: Identity configuration
        text: Text to analyze

    Returns:
        Decision indicating if crisis protocols should activate
    """
    rationale = ["safety_and_alignment.crisis_protocols"]

    protocols = identity.safety_and_alignment.crisis_protocols
    triggers = protocols.trigger_signals

    detected_signals = []
    lower_text = text.lower()

    # Simple keyword detection (production would use ML)
    crisis_keywords = {
        "self-harm": ["kill myself", "end my life", "suicide"],
        "violence": ["hurt someone", "attack", "harm others"],
        "abuse": ["being abused", "someone is hurting me"],
    }

    for signal_type in triggers:
        keywords = crisis_keywords.get(signal_type, [])
        for keyword in keywords:
            if keyword in lower_text:
                detected_signals.append(signal_type)
                break

    return Decision(
        decision={
            "crisis_detected": len(detected_signals) > 0,
            "signals": detected_signals,
            "recommended_behaviors": protocols.behavior if detected_signals else [],
        },
        rationale=rationale,
    )
