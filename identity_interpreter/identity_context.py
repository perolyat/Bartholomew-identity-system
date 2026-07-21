"""
Identity Context
================

Declarative summary of "who Bartholomew is" -- extracted from a loaded
`Identity` config. Identity never answers "what should I do?"; it only
answers "who am I?" (values, red lines, behavioral constraints, tool-use
policy, consent requirements). The Kernel's Executive
(`bartholomew.kernel.policy_engine`) is what turns an IdentityContext into an
executable Policy Decision -- this module computes no decisions itself.

Part of MASTER_PLAN.md's "P2.5 -- Runtime Convergence" (item 11.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Identity


@dataclass(frozen=True)
class IdentityContext:
    """Declarative identity data -- no decisions, just facts about who Bartholomew is."""

    core_values: list[str] = field(default_factory=list)
    red_lines: list[str] = field(default_factory=list)
    tool_use_default_allowed: bool = False
    tool_use_allowlist: list[str] = field(default_factory=list)
    tool_use_consent_prompts: list[str] = field(default_factory=list)


def build_identity_context(identity: Identity) -> IdentityContext:
    """Extract a declarative IdentityContext from a loaded Identity config."""
    consent_prompts = identity.tool_use.consent_prompts
    enabled_consent_prompts = [name for name, enabled in consent_prompts.items() if enabled]
    return IdentityContext(
        core_values=list(identity.values_and_principles.core_values),
        red_lines=list(identity.red_lines),
        tool_use_default_allowed=identity.tool_use.default_allowed,
        tool_use_allowlist=list(identity.tool_use.allowlist),
        tool_use_consent_prompts=enabled_consent_prompts,
    )
