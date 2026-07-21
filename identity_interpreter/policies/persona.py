"""
Persona policy engine
Shapes tone, style, and adaptive behavior

DEPRECATED (2026-07-21, MASTER_PLAN.md "P2.5 -- Runtime Convergence" / item 11.1):
this module is not the authoritative persona/tone implementation. That's
`bartholomew.kernel.persona_pack.PersonaPackManager`, which is wired into
`NarratorEngine`/`ExperienceKernel` for the live kernel. This module remains
in use only by `identity_interpreter/cli.py`'s `explain` command and
`chat.py`'s standalone script -- do not add new callers. It will be removed
once those two callers are migrated to the authoritative persona system (see
DECISIONS.md's "One authority per architectural concept" entry).
"""

import warnings
from typing import Any

from ..models import Identity


_DEPRECATION_MSG = (
    "identity_interpreter.policies.persona.get_persona_config() is deprecated; "
    "the authoritative persona system is "
    "bartholomew.kernel.persona_pack.PersonaPackManager. See MASTER_PLAN.md's "
    "'P2.5 -- Runtime Convergence' item 11.1."
)


def get_persona_config(
    identity: Identity,
    context: str = "casual",
) -> dict[str, Any]:
    """
    Get persona configuration for given context

    Args:
        identity: Identity configuration
        context: Context type (casual, professional, crisis)

    Returns:
        Dict with persona traits, tone, and style for context
    """
    warnings.warn(_DEPRECATION_MSG, DeprecationWarning, stacklevel=2)
    persona = identity.persona

    # Base configuration
    config = {
        "traits": persona.traits,
        "tone": persona.tone,
        "style_guidelines": {
            "default_brevity": persona.style_guidelines.default_brevity,
            "avoid": persona.style_guidelines.avoid,
            "do": persona.style_guidelines.do,
        },
    }

    # Apply adaptive behavior if enabled
    if persona.adaptive_behavior.adjusts_tone_by_context:
        profiles = persona.adaptive_behavior.profiles
        if context in profiles:
            profile = profiles[context]
            config["adaptive_tone"] = profile.tone
            if profile.brevity:
                config["adaptive_brevity"] = profile.brevity
            if profile.priority:
                config["adaptive_priority"] = profile.priority

    return config


def get_style_guidelines(identity: Identity) -> dict[str, Any]:
    """
    Get complete style guidelines

    Returns:
        Dict with all style guidelines
    """
    guidelines = identity.persona.style_guidelines
    return {
        "default_brevity": guidelines.default_brevity,
        "avoid": guidelines.avoid,
        "do": guidelines.do,
    }


def should_adjust_tone(
    identity: Identity,
    detected_context: str,
) -> bool:
    """
    Check if tone adjustment is enabled for context

    Args:
        identity: Identity configuration
        detected_context: Detected context

    Returns:
        True if tone should be adjusted
    """
    if not identity.persona.adaptive_behavior.adjusts_tone_by_context:
        return False

    profiles = identity.persona.adaptive_behavior.profiles
    return detected_context in profiles
