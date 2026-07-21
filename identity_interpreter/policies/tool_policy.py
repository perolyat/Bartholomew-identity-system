"""
Tool use policy engine
Controls tool allowlist and consent requirements

DEPRECATED (2026-07-21, MASTER_PLAN.md "P2.5 -- Runtime Convergence" / item 11.1):
this module is not the authoritative permission-gating implementation. That's
`bartholomew.kernel.skill_permissions.PermissionChecker`, which is what
actually gates skill execution via `SkillRegistry.execute_action()`. This
module remains in use only by `identity_interpreter/cli.py`'s `explain`
command -- do not add new callers. It will be removed once that caller is
migrated to the authoritative permission gate (see DECISIONS.md's "One
authority per architectural concept" entry).
"""

import warnings

from ..models import Decision, Identity


_DEPRECATION_MSG = (
    "identity_interpreter.policies.tool_policy.check_tool_allowed() is "
    "deprecated; the authoritative permission gate is "
    "bartholomew.kernel.skill_permissions.PermissionChecker. See "
    "MASTER_PLAN.md's 'P2.5 -- Runtime Convergence' item 11.1."
)


def check_tool_allowed(
    identity: Identity,
    tool_name: str,
    context: dict | None = None,
) -> Decision:
    """
    Check if tool use is allowed and if consent is required

    Args:
        identity: Identity configuration
        tool_name: Name of tool to check
        context: Optional context (e.g., current mode, user state)

    Returns:
        Decision with allowed status and consent requirements
    """
    warnings.warn(_DEPRECATION_MSG, DeprecationWarning, stacklevel=2)
    rationale = []

    # Check default policy
    default_allowed = identity.tool_use.default_allowed
    rationale.append("tool_use.default_allowed")

    # Check allowlist
    allowlist = identity.tool_use.allowlist
    in_allowlist = tool_name in allowlist

    if in_allowlist:
        rationale.append(f"tool_use.allowlist (contains '{tool_name}')")

    # Determine if allowed
    allowed = default_allowed or in_allowlist

    # Check consent requirements
    consent_prompts = identity.tool_use.consent_prompts
    requires_consent = False

    if "per_use_for_sensitive" in consent_prompts:
        # Define sensitive tools (could be configurable)
        sensitive_tools = ["browser_action", "execute_command"]
        if tool_name in sensitive_tools:
            requires_consent = True
            rationale.append(
                "tool_use.consent_prompts.per_use_for_sensitive",
            )

    if "per_session" in consent_prompts and allowed:
        requires_consent = True
        rationale.append("tool_use.consent_prompts.per_session")

    # Check sandbox constraints
    sandbox = identity.tool_use.sandbox
    sandbox_info = {}

    if "filesystem" in sandbox:
        sandbox_info["filesystem"] = sandbox["filesystem"]
        rationale.append("tool_use.sandbox.filesystem")

    if "network" in sandbox:
        sandbox_info["network"] = sandbox["network"]
        rationale.append("tool_use.sandbox.network")

    return Decision(
        decision={
            "allowed": allowed,
            "in_allowlist": in_allowlist,
            "sandbox": sandbox_info,
        },
        rationale=rationale,
        requires_consent=requires_consent,
    )


def get_sandbox_paths(identity: Identity) -> dict:
    """
    Get filesystem and network sandbox restrictions

    Returns:
        Dict with 'filesystem' and 'network' constraints
    """
    sandbox = identity.tool_use.sandbox
    return {
        "filesystem": sandbox.get("filesystem", {}),
        "network": sandbox.get("network", {}),
    }
