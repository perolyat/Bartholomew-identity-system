"""
Tests for MASTER_PLAN.md's "P2.5 -- Runtime Convergence" item 11.2:
Identity Context -> Executive -> Policy Decision.

Proves skill-execution (SkillRegistry.execute_action()) consults an
IdentityContext-derived Policy Decision
(bartholomew.kernel.policy_engine.evaluate_tool_policy()) -- closing the
"Identity.yaml governs only chat" gap the P2.5 architectural audit found.

Note: an earlier version of this change also wired scheduler/loop.py's
_run_drive() to the same check, using each drive's task_id (e.g.
"self_check") as the "tool name" against Identity.yaml's tool_use.allowlist.
That was reverted -- internal scheduler drives are kernel self-maintenance
functions, not "tools" in the tool_use.allowlist sense, and Identity.yaml's
real allowlist (web_fetch, browser_action) never includes drive task_ids.
Gating drives on it denied every drive by default in production, and the
scheduler's retry loop doesn't back off on denial (0-duration failures are
immediately re-due), which busy-loops and starves the asyncio event loop --
reproduced locally as the exact cause of a smoke-test hang (uvicorn never
answers /healthz). See DECISIONS.md's "Identity publishes a declarative
Identity Context..." entry for the corrected scope: the Executive's Policy
Decision applies to skill/capability execution, not the scheduler's
internal drives.
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.policy_engine import evaluate_tool_policy
from bartholomew.kernel.skill_permissions import reset_permission_checker
from bartholomew.kernel.skill_registry import SkillRegistry, reset_skill_registry
from identity_interpreter.identity_context import IdentityContext


@pytest.fixture
def temp_db():
    """Temp DB with schema initialized (system_flags, etc.) via MemoryStore."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    store = MemoryStore(path)
    asyncio.run(store.init())

    yield path
    try:
        os.unlink(path)
    except (PermissionError, FileNotFoundError):
        pass


@pytest.fixture(autouse=True)
def reset_singletons():
    reset_skill_registry()
    reset_permission_checker()
    yield
    reset_skill_registry()
    reset_permission_checker()


DENY_CONTEXT = IdentityContext(tool_use_default_allowed=False, tool_use_allowlist=[])
ALLOW_CONTEXT = IdentityContext(tool_use_default_allowed=False, tool_use_allowlist=["tasks"])


class TestPolicyEngine:
    """Unit tests for the Executive's Policy Decision constructor itself."""

    def test_denies_when_not_allowlisted_and_not_default_allowed(self):
        decision = evaluate_tool_policy(DENY_CONTEXT, "tasks")
        assert decision.allowed is False
        assert "not in tool_use.allowlist" in decision.reason

    def test_allows_when_explicitly_allowlisted(self):
        decision = evaluate_tool_policy(ALLOW_CONTEXT, "tasks")
        assert decision.allowed is True

    def test_allows_when_default_allowed(self):
        context = IdentityContext(tool_use_default_allowed=True, tool_use_allowlist=[])
        decision = evaluate_tool_policy(context, "anything")
        assert decision.allowed is True

    def test_requires_consent_when_configured(self):
        context = IdentityContext(
            tool_use_default_allowed=True,
            tool_use_allowlist=[],
            tool_use_consent_prompts=["per_session"],
        )
        decision = evaluate_tool_policy(context, "anything")
        assert decision.allowed is True
        assert decision.requires_consent is True


@pytest.mark.asyncio
class TestSkillExecutionConsultsPolicyDecision:
    async def test_denied_when_not_allowlisted(self, temp_db):
        registry = SkillRegistry(db_path=temp_db, identity_context=DENY_CONTEXT)
        await registry.load_skill("tasks")

        result = await registry.execute_action("tasks", "create", {"title": "x"})

        assert result.success is False
        assert "Identity policy" in result.error

    async def test_allowed_when_allowlisted(self, temp_db):
        registry = SkillRegistry(db_path=temp_db, identity_context=ALLOW_CONTEXT)
        await registry.load_skill("tasks")

        result = await registry.execute_action("tasks", "create", {"title": "x"})

        assert result.success is True

    async def test_skipped_entirely_when_no_identity_context_wired(self, temp_db):
        """No behavior change for callers that don't opt in (identity_context=None)."""
        registry = SkillRegistry(db_path=temp_db)
        await registry.load_skill("tasks")

        result = await registry.execute_action("tasks", "create", {"title": "x"})

        assert result.success is True

    async def test_rule_change_flips_the_same_execution_path(self, temp_db):
        """A single IdentityContext rule change flips the same skill-execution
        outcome, because it's read through the same evaluate_tool_policy()."""
        registry_before = SkillRegistry(db_path=temp_db, identity_context=DENY_CONTEXT)
        await registry_before.load_skill("tasks")
        before = await registry_before.execute_action("tasks", "create", {"title": "x"})
        assert before.success is False

        registry_after = SkillRegistry(db_path=temp_db, identity_context=ALLOW_CONTEXT)
        await registry_after.load_skill("tasks")
        after = await registry_after.execute_action("tasks", "create", {"title": "y"})
        assert after.success is True
