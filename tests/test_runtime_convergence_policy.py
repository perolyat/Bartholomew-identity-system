"""
Tests for MASTER_PLAN.md's "P2.5 -- Runtime Convergence" item 11.2:
Identity Context -> Executive -> Policy Decision.

Proves skill-execution (SkillRegistry.execute_action()) and a scheduler
drive (_run_drive()) both consult the same IdentityContext-derived Policy
Decision (bartholomew.kernel.policy_engine.evaluate_tool_policy()) for a
shared example tool-use rule -- closing the "Identity.yaml governs only
chat" gap the P2.5 architectural audit found.
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.policy_engine import evaluate_tool_policy
from bartholomew.kernel.scheduler.loop import _run_drive
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


class MockSchedulerCtx:
    """Minimal stand-in for KernelDaemon, matching _run_drive()'s needs."""

    class MockMem:
        def __init__(self, db_path: str) -> None:
            self.db_path = db_path

    def __init__(self, db_path: str, identity_context: IdentityContext | None) -> None:
        self.mem = self.MockMem(db_path)
        self.identity_context = identity_context


DENY_CONTEXT = IdentityContext(tool_use_default_allowed=False, tool_use_allowlist=[])
ALLOW_CONTEXT = IdentityContext(
    tool_use_default_allowed=False,
    tool_use_allowlist=["tasks", "self_check"],
)


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


@pytest.mark.asyncio
class TestSchedulerDriveConsultsPolicyDecision:
    async def test_denied_when_not_allowlisted(self, temp_db):
        ctx = MockSchedulerCtx(temp_db, DENY_CONTEXT)

        async def drive_fn(_ctx):
            return "ran"

        result, success = await _run_drive(ctx, "self_check", drive_fn)

        assert result is None
        assert success == 0

    async def test_allowed_when_allowlisted(self, temp_db):
        ctx = MockSchedulerCtx(temp_db, ALLOW_CONTEXT)

        async def drive_fn(_ctx):
            return "ran"

        result, success = await _run_drive(ctx, "self_check", drive_fn)

        assert result == "ran"
        assert success == 1

    async def test_skipped_entirely_when_no_identity_context_wired(self, temp_db):
        """No behavior change for callers that don't opt in (identity_context=None)."""
        ctx = MockSchedulerCtx(temp_db, None)

        async def drive_fn(_ctx):
            return "ran"

        result, success = await _run_drive(ctx, "self_check", drive_fn)

        assert result == "ran"
        assert success == 1


@pytest.mark.asyncio
class TestSharedPolicySource:
    """
    The acceptance criterion itself: a single skill-execution path and a
    single scheduler-drive both demonstrably respect the same Identity.yaml
    tool-use rule change, because both consult the same
    bartholomew.kernel.policy_engine.evaluate_tool_policy() with the same
    IdentityContext -- not two independent, potentially-diverging checks.
    """

    async def test_rule_change_flips_both_paths_together(self, temp_db):
        # Before: neither "tasks" (a skill) nor "self_check" (a scheduler
        # drive) is allowlisted -- both denied.
        before = IdentityContext(tool_use_default_allowed=False, tool_use_allowlist=[])

        registry = SkillRegistry(db_path=temp_db, identity_context=before)
        await registry.load_skill("tasks")
        skill_result = await registry.execute_action("tasks", "create", {"title": "x"})

        async def drive_fn(_ctx):
            return "ran"

        ctx = MockSchedulerCtx(temp_db, before)
        drive_result, drive_success = await _run_drive(ctx, "self_check", drive_fn)

        assert skill_result.success is False
        assert drive_success == 0

        # After: the *same* rule change (allowlisting both names) flips
        # *both* paths, because they read the same IdentityContext through
        # the same policy_engine function.
        after = IdentityContext(
            tool_use_default_allowed=False,
            tool_use_allowlist=["tasks", "self_check"],
        )

        registry2 = SkillRegistry(db_path=temp_db, identity_context=after)
        await registry2.load_skill("tasks")
        skill_result2 = await registry2.execute_action("tasks", "create", {"title": "y"})

        ctx2 = MockSchedulerCtx(temp_db, after)
        drive_result2, drive_success2 = await _run_drive(ctx2, "self_check", drive_fn)

        assert skill_result2.success is True
        assert drive_success2 == 1
        assert drive_result2 == "ran"
