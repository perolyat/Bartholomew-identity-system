"""
Policy Engine (the Executive's Policy Decision constructor)
=============================================================

Consumes an `IdentityContext` (declarative -- "who Bartholomew is", produced
by `identity_interpreter.identity_context`) and constructs an executable
`PolicyDecision` for a specific proposed action. Identity never computes an
executable decision itself; the Kernel never parses `Identity.yaml` directly
-- it only receives an already-built `IdentityContext`.

This is the single source both `SkillRegistry.execute_action()` and
`scheduler/loop.py`'s `_run_drive()` consult so a tool-use/red-line rule
means the same thing regardless of which path proposed the action -- closing
the "Identity.yaml governs only chat" gap named in MASTER_PLAN.md's
"P2.5 -- Runtime Convergence" (item 11.2).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from identity_interpreter.identity_context import IdentityContext

logger = logging.getLogger(__name__)


@dataclass
class PolicyDecision:
    """Executable outcome of evaluating an IdentityContext for a proposed action."""

    allowed: bool
    requires_consent: bool = False
    rationale: list[str] = field(default_factory=list)
    reason: str | None = None


def evaluate_tool_policy(context: IdentityContext, tool_name: str) -> PolicyDecision:
    """
    Decide whether `tool_name` (a skill_id or scheduler drive task_id) is
    permitted under the given IdentityContext.

    Implements the tool_use-allowlist logic (default_allowed OR explicit
    allowlist membership) on the Executive side from a declarative context.
    This is now the sole implementation: the former
    `identity_interpreter.policies.tool_policy.check_tool_allowed()` it once
    mirrored was removed in item 11.14 once its last caller (the CLI `explain`
    command) migrated here -- see DECISIONS.md's "One authority per
    architectural concept" entry.
    """
    rationale: list[str] = []

    in_allowlist = tool_name in context.tool_use_allowlist
    if in_allowlist:
        rationale.append(f"tool_use.allowlist (contains '{tool_name}')")

    allowed = context.tool_use_default_allowed or in_allowlist
    rationale.append(
        (
            "tool_use.default_allowed"
            if context.tool_use_default_allowed
            else "tool_use.default_allowed is false"
        ),
    )

    requires_consent = allowed and bool(context.tool_use_consent_prompts)
    if requires_consent:
        rationale.append(f"tool_use.consent_prompts: {context.tool_use_consent_prompts}")

    reason = (
        None
        if allowed
        else f"'{tool_name}' is not in tool_use.allowlist and default_allowed is false"
    )

    return PolicyDecision(
        allowed=allowed,
        requires_consent=requires_consent,
        rationale=rationale,
        reason=reason,
    )
