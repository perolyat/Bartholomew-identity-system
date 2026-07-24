"""
Tests for MASTER_PLAN.md's "P2.5 -- Runtime Convergence" item 11.19:
skill-execution convergence (Exit Gate questions #1-2 for the skill surface).

Before this, `SkillRegistry.execute_action()` was already a real
choke-point (parking brake, Identity Policy Decision, unified Reflection --
see tests/test_runtime_convergence_policy.py and
tests/test_reflection_unification.py) but never built an `Observation` or
modeled the request as a `CandidateAction` -- COGNITIVE_RUNTIME.md's Exit
Gate table named this as the concrete remaining gap alongside voice/sight.

This module proves two things the mere existence of `Observation`/
`CandidateAction` objects would not: (1) the CandidateAction genuinely
drives the Governance decision -- denied actions never reach the
underlying skill, and execution only ever happens after Governance allows
-- and (2) `run_skill_through_runtime_contract()` is the sole production
entry point into skill execution, mirroring chat (item 11.3) and scheduler
drives (item 11.17).
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from bartholomew.kernel import policy_engine
from bartholomew.kernel import skill_registry as skill_registry_module
from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.planner import Planner
from bartholomew.kernel.reflection import REFLECTION_KIND
from bartholomew.kernel.runtime_contract import (
    CandidateAction,
    run_skill_through_runtime_contract,
)
from bartholomew.kernel.skill_base import SkillBase, SkillContext, SkillResult
from bartholomew.kernel.skill_permissions import reset_permission_checker
from bartholomew.kernel.skill_registry import SkillRegistry, reset_skill_registry
from identity_interpreter.identity_context import IdentityContext


# =============================================================================
# A spy skill: records exactly what happened and when, so tests can prove
# ordering (Governance before Execution) and non-invocation on denial.
# =============================================================================

CALL_LOG: list[str] = []


class SpySkill(SkillBase):
    """Test double whose `execute()` logs an unmistakable side effect.

    If Governance is ever bypassed or the CandidateAction it evaluates isn't
    the real one, one of two things happens and a test below catches it:
    either this executes when it shouldn't (denied case), or it doesn't
    execute when it should (allowed case).

    `raise_on_execute` is a class attribute (not a module global) so a test
    can flip it without a `global` statement -- assigning `SpySkill.
    raise_on_execute` is an attribute write, not a name rebind.
    """

    raise_on_execute: bool = False

    @property
    def skill_id(self) -> str:
        return "spy_skill"

    async def initialize(self, context: SkillContext) -> None:
        self._context = context

    async def shutdown(self) -> None:
        pass

    async def execute(self, action: str, params: dict | None = None) -> SkillResult:
        CALL_LOG.append("skill_executed")
        if SpySkill.raise_on_execute:
            raise RuntimeError("spy skill crashed on purpose")
        return SkillResult.ok(data={"ran": action})


@pytest.fixture(autouse=True)
def _reset_spy_state():
    CALL_LOG.clear()
    SpySkill.raise_on_execute = False
    yield
    CALL_LOG.clear()
    SpySkill.raise_on_execute = False


@pytest.fixture(autouse=True)
def reset_singletons():
    reset_skill_registry()
    reset_permission_checker()
    yield
    reset_skill_registry()
    reset_permission_checker()


@pytest.fixture
def skills_dir(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    manifest = {
        "skill_id": "spy_skill",
        "name": "Spy Skill",
        "version": "1.0.0",
        "description": "Test double for Runtime Convergence tests",
        "entry_module": "tests.test_skill_runtime_contract_seam",
        "entry_class": "SpySkill",
        "permissions": {"level": "auto", "requires": []},
        "actions": [{"name": "run", "description": "Runs", "parameters": []}],
        "author": "test",
        "enabled": True,
    }
    with open(skills_dir / "spy_skill.yaml", "w") as f:
        yaml.safe_dump(manifest, f)
    return skills_dir


@pytest.fixture
async def registry(tmp_path, skills_dir):
    db_path = str(tmp_path / "t.db")
    mem = MemoryStore(db_path)
    await mem.init()
    reg = SkillRegistry(skills_dir=skills_dir, db_path=db_path, memory_store=mem)
    # Instantiate the spy directly (mirrors test_skill_registry.py's own
    # pattern) rather than relying on manifest-driven importlib discovery --
    # a manifest-driven `entry_module: "tests.test_skill_runtime_contract_seam"`
    # re-import can land as a second, distinct module object from this test
    # file's own import, which would silently give SpySkill.execute() a
    # different CALL_LOG / SpySkill.raise_on_execute than the one this file
    # asserts on.
    with patch.object(reg, "_instantiate_skill", return_value=SpySkill()):
        await reg.load_skill("spy_skill")
    reg._mem = mem  # stash for teardown/queries
    yield reg
    await mem.close()


ALLOW_CONTEXT = IdentityContext(tool_use_default_allowed=False, tool_use_allowlist=["spy_skill"])
DENY_CONTEXT = IdentityContext(tool_use_default_allowed=False, tool_use_allowlist=[])
# A context that allowlists an unrelated tool name -- if Governance ever
# stopped evaluating the *real* CandidateAction.kind (e.g. a hardcoded
# stand-in, or a value that drifted from the actual skill_id), this context
# would wrongly allow "spy_skill" too. It must still deny.
UNRELATED_ALLOW_CONTEXT = IdentityContext(
    tool_use_default_allowed=False,
    tool_use_allowlist=["some_other_tool"],
)


async def _count_reflections(mem: MemoryStore) -> int:
    import aiosqlite

    async with aiosqlite.connect(mem.db_path) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM reflections WHERE kind=?",
            (REFLECTION_KIND,),
        )
        (count,) = await cur.fetchone()
        return count


async def _count_audit_rows(mem: MemoryStore) -> int:
    import aiosqlite

    async with aiosqlite.connect(mem.db_path) as db:
        cur = await db.execute("SELECT COUNT(*) FROM skill_action_audit")
        (count,) = await cur.fetchone()
        return count


# =============================================================================
# 1. The CandidateAction genuinely drives the Governance decision.
# =============================================================================


@pytest.mark.asyncio
class TestCandidateActionGenuinelyConsumedByGovernance:
    async def test_policy_authority_receives_the_candidate_action(self, registry, monkeypatch):
        """The exact CandidateAction constructed inside execute_action() is
        what the policy authority evaluates -- captured by spying on both
        the CandidateAction constructor skill_registry.py calls and the
        evaluate_tool_policy() call it feeds, and asserting they agree."""
        registry._identity_context = ALLOW_CONTEXT

        constructed_kinds: list[str] = []
        real_candidate_action = CandidateAction

        def spy_candidate_action(*, kind, interpretation):
            constructed_kinds.append(kind)
            return real_candidate_action(kind=kind, interpretation=interpretation)

        monkeypatch.setattr(skill_registry_module, "CandidateAction", spy_candidate_action)

        evaluated_tool_names: list[str] = []
        real_evaluate = policy_engine.evaluate_tool_policy

        def spy_evaluate(context, tool_name):
            evaluated_tool_names.append(tool_name)
            return real_evaluate(context, tool_name)

        monkeypatch.setattr(
            skill_registry_module.policy_engine,
            "evaluate_tool_policy",
            spy_evaluate,
        )

        result = await registry.execute_action("spy_skill", "run", {})

        assert result.success is True
        assert constructed_kinds == ["spy_skill"]
        # The Governance stage evaluated *the CandidateAction just built*,
        # not an independently derived value.
        assert evaluated_tool_names == constructed_kinds

    async def test_denied_actions_never_invoke_the_underlying_skill(self, registry):
        registry._identity_context = DENY_CONTEXT

        result = await registry.execute_action("spy_skill", "run", {})

        assert result.success is False
        assert "Identity policy" in result.error
        assert "skill_executed" not in CALL_LOG

    async def test_unrelated_allowlist_entry_still_denies(self, registry):
        """Proves the policy check is keyed to *this* CandidateAction's
        kind, not merely 'some allowlist exists' -- a context that allows a
        different tool name must not let spy_skill through."""
        registry._identity_context = UNRELATED_ALLOW_CONTEXT

        result = await registry.execute_action("spy_skill", "run", {})

        assert result.success is False
        assert "skill_executed" not in CALL_LOG

    async def test_execution_occurs_only_after_the_governance_decision(self, registry, monkeypatch):
        """Ordering proof: the policy check runs and completes strictly
        before the skill's own execute() runs."""
        registry._identity_context = ALLOW_CONTEXT
        real_evaluate = policy_engine.evaluate_tool_policy

        def spy_evaluate(context, tool_name):
            CALL_LOG.append("policy_checked")
            return real_evaluate(context, tool_name)

        monkeypatch.setattr(
            skill_registry_module.policy_engine,
            "evaluate_tool_policy",
            spy_evaluate,
        )

        result = await registry.execute_action("spy_skill", "run", {})

        assert result.success is True
        assert CALL_LOG == ["policy_checked", "skill_executed"]

    async def test_denial_leaves_no_execution_call_logged(self, registry, monkeypatch):
        registry._identity_context = DENY_CONTEXT
        real_evaluate = policy_engine.evaluate_tool_policy

        def spy_evaluate(context, tool_name):
            CALL_LOG.append("policy_checked")
            return real_evaluate(context, tool_name)

        monkeypatch.setattr(
            skill_registry_module.policy_engine,
            "evaluate_tool_policy",
            spy_evaluate,
        )

        await registry.execute_action("spy_skill", "run", {})

        assert CALL_LOG == ["policy_checked"]

    async def test_parking_brake_also_blocks_before_any_execution(self, registry):
        """The parking brake is a Governance stage too (runs before the
        Policy Decision); it must also gate the same underlying skill call,
        with no identity_context required."""
        from bartholomew.orchestrator.safety.parking_brake import BrakeStorage, ParkingBrake

        brake = ParkingBrake(BrakeStorage(registry._db_path))
        brake.engage("skills")
        try:
            result = await registry.execute_action("spy_skill", "run", {})
            assert result.success is False
            assert "parking brake" in result.error.lower()
            assert "skill_executed" not in CALL_LOG
        finally:
            brake.disengage()


# =============================================================================
# 2. run_skill_through_runtime_contract() is the sole production entry path.
# =============================================================================


class TestNamedSeamIsTheSoleProductionEntryPath:
    def test_no_production_caller_bypasses_the_named_seam(self):
        """Structural guard: outside runtime_contract.py (the seam's thin
        delegator, whose one job is calling `registry.execute_action()`),
        no production module may contain an actual `x.execute_action(...)`
        call expression. Planner.handle_skill_request() is the one call
        site that matters and it now goes through
        run_skill_through_runtime_contract() -- if a future change
        reintroduces a direct call anywhere else in production code, this
        fails until that's a deliberate decision.

        Uses the AST (Call nodes whose func is an Attribute named
        "execute_action"), not a text/regex search, so docstring prose that
        merely *mentions* `execute_action()` (skill_registry.py and
        planner.py both narrate it in comments) doesn't produce false
        positives -- only real call expressions count.
        """
        repo_root = Path(__file__).resolve().parent.parent
        allowed = {repo_root / "bartholomew" / "kernel" / "runtime_contract.py"}

        offenders = []
        search_roots = [
            repo_root / "bartholomew",
            repo_root / "bartholomew_api_bridge_v0_1",
            repo_root / "identity_interpreter",
        ]
        for root in search_roots:
            if not root.exists():
                continue
            for py_file in root.rglob("*.py"):
                if py_file in allowed:
                    continue
                tree = ast.parse(py_file.read_text(), filename=str(py_file))
                for node in ast.walk(tree):
                    if (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "execute_action"
                    ):
                        offenders.append(f"{py_file}:{node.lineno}")

        assert offenders == [], f"Direct execute_action() call(s) outside the seam: {offenders}"

    def test_planner_source_calls_the_named_seam_not_execute_action_directly(self):
        """Belt-and-suspenders on top of the repo-wide scan above: confirm,
        via the planner module's own AST, that handle_skill_request() calls
        run_skill_through_runtime_contract and contains no
        `self._skill_registry.execute_action(...)` call."""
        import bartholomew.kernel.planner as planner_module

        source = Path(planner_module.__file__).read_text()
        tree = ast.parse(source)

        call_names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute):
                    call_names.append(func.attr)
                elif isinstance(func, ast.Name):
                    call_names.append(func.id)

        assert "run_skill_through_runtime_contract" in call_names
        assert "execute_action" not in call_names

    @pytest.mark.asyncio
    async def test_planner_handle_skill_request_calls_the_seam_function(
        self,
        registry,
        monkeypatch,
    ):
        """Behavioral confirmation, not just source inspection: patch the
        seam function planner.py imports at call time and prove
        handle_skill_request() actually invokes it."""
        import bartholomew.kernel.runtime_contract as runtime_contract_module

        spy = AsyncMock(return_value=SkillResult.ok(data={"patched": True}))
        monkeypatch.setattr(runtime_contract_module, "run_skill_through_runtime_contract", spy)

        planner = Planner(policy={}, drives={"drives": []}, mem=None, skill_registry=registry)
        result = await planner.handle_skill_request("spy_skill", "run", {"x": 1})

        spy.assert_awaited_once_with(registry, "spy_skill", "run", {"x": 1})
        assert result.data == {"patched": True}
        # And the real skill was never touched -- planner delegated entirely
        # to the (patched) seam rather than falling back to a direct call.
        assert "skill_executed" not in CALL_LOG

    @pytest.mark.asyncio
    async def test_seam_and_direct_call_are_behaviorally_identical(self, registry):
        """run_skill_through_runtime_contract() must not be a parallel path
        with different behavior -- it's a thin delegator, so calling it
        directly must match calling execute_action() directly."""
        registry._identity_context = ALLOW_CONTEXT

        via_seam = await run_skill_through_runtime_contract(registry, "spy_skill", "run", {"a": 1})
        assert CALL_LOG == ["skill_executed"]
        CALL_LOG.clear()

        via_direct = await registry.execute_action("spy_skill", "run", {"a": 1})
        assert CALL_LOG == ["skill_executed"]

        assert via_seam.status == via_direct.status
        assert via_seam.data == via_direct.data


# =============================================================================
# 3. Exactly one Reflection per attempt, through the shared sink -- no
#    duplicates, across every outcome the Governance/Execution stages produce.
# =============================================================================


@pytest.mark.asyncio
class TestExactlyOneReflectionPerAttempt:
    async def test_success_records_exactly_one_reflection_and_one_audit_row(self, registry):
        result = await registry.execute_action("spy_skill", "run", {})
        assert result.success is True

        assert await _count_reflections(registry._mem) == 1
        assert await _count_audit_rows(registry._mem) == 1

        row = await registry._mem.latest_reflection(REFLECTION_KIND)
        assert row["meta"]["surface"] == "skill"
        assert row["meta"]["action"] == "spy_skill.run"
        assert row["meta"]["outcome"] == "success"

    async def test_parking_brake_denial_records_exactly_one_reflection(self, registry):
        from bartholomew.orchestrator.safety.parking_brake import BrakeStorage, ParkingBrake

        brake = ParkingBrake(BrakeStorage(registry._db_path))
        brake.engage("skills")
        try:
            result = await registry.execute_action("spy_skill", "run", {})
            assert result.success is False
        finally:
            brake.disengage()

        assert await _count_reflections(registry._mem) == 1
        assert await _count_audit_rows(registry._mem) == 1
        row = await registry._mem.latest_reflection(REFLECTION_KIND)
        assert row["meta"]["outcome"] == "error"

    async def test_policy_denial_records_exactly_one_reflection(self, registry):
        registry._identity_context = DENY_CONTEXT

        result = await registry.execute_action("spy_skill", "run", {})
        assert result.success is False

        assert await _count_reflections(registry._mem) == 1
        assert await _count_audit_rows(registry._mem) == 1
        row = await registry._mem.latest_reflection(REFLECTION_KIND)
        assert row["meta"]["outcome"] == "error"
        assert "Identity policy" in row["meta"]["error"]

    async def test_execution_exception_records_exactly_one_reflection(self, registry):
        SpySkill.raise_on_execute = True

        result = await registry.execute_action("spy_skill", "run", {})
        assert result.success is False
        assert "spy skill crashed" in result.error

        assert await _count_reflections(registry._mem) == 1
        assert await _count_audit_rows(registry._mem) == 1
        row = await registry._mem.latest_reflection(REFLECTION_KIND)
        assert row["meta"]["outcome"] == "error"
        # The skill genuinely ran (and crashed) -- Governance allowed it
        # through before the exception occurred inside execution.
        assert "skill_executed" in CALL_LOG

    async def test_multiple_attempts_each_add_exactly_one_reflection(self, registry):
        await registry.execute_action("spy_skill", "run", {})
        await registry.execute_action("spy_skill", "run", {})
        registry._identity_context = DENY_CONTEXT
        await registry.execute_action("spy_skill", "run", {})

        assert await _count_reflections(registry._mem) == 3
        assert await _count_audit_rows(registry._mem) == 3
