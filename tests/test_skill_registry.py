"""
Tests for Skill Registry (Stage 4)
==================================

Tests skill manifest loading, registry operations, permissions, and starter skills.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest
import yaml

from bartholomew.kernel.skill_base import (
    SkillBase,
    SkillContext,
    SkillResult,
    SkillResultStatus,
    SkillState,
)
from bartholomew.kernel.skill_manifest import (
    ActionParameter,
    SkillAction,
    SkillManifest,
    SkillPermissions,
    SkillSandbox,
    SkillSubscription,
    discover_manifests,
)
from bartholomew.kernel.skill_permissions import (
    PermissionChecker,
    PermissionStatus,
    reset_permission_checker,
)
from bartholomew.kernel.skill_registry import (
    SkillRegistry,
    reset_skill_registry,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_db():
    """Create a temporary database file."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except (PermissionError, FileNotFoundError):
        pass


@pytest.fixture
def temp_skills_dir(tmp_path):
    """Create a temporary skills directory with test manifests."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    # Create a test skill manifest
    test_manifest = {
        "skill_id": "test_skill",
        "name": "Test Skill",
        "version": "1.0.0",
        "description": "A test skill",
        "entry_module": "tests.test_skill_registry",
        "entry_class": "MockSkill",
        "permissions": {
            "level": "auto",
            "requires": ["memory.read", "memory.write"],
            "sandbox": {
                "filesystem": ["./data"],
                "network": [],
            },
        },
        "subscriptions": [
            {"channel": "test", "events": ["test_event"]},
        ],
        "emits": [
            {"channel": "test", "events": ["test_emitted"]},
        ],
        "actions": [
            {
                "name": "do_something",
                "description": "Does something",
                "parameters": [
                    {"name": "value", "type": "string", "required": True},
                ],
            },
        ],
        "author": "test",
        "enabled": True,
    }

    with open(skills_dir / "test_skill.yaml", "w") as f:
        yaml.safe_dump(test_manifest, f)

    return skills_dir


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset module-level singletons before each test."""
    reset_skill_registry()
    reset_permission_checker()
    yield
    reset_skill_registry()
    reset_permission_checker()


# =============================================================================
# Mock Skill for Testing
# =============================================================================


class MockSkill(SkillBase):
    """Mock skill implementation for testing."""

    @property
    def skill_id(self) -> str:
        return "test_skill"

    async def initialize(self, context: SkillContext) -> None:
        self._context = context
        self._initialized = True

    async def shutdown(self) -> None:
        self._initialized = False

    async def execute(self, action: str, params: dict | None = None) -> SkillResult:
        if action == "do_something":
            return SkillResult.ok(data={"done": True})
        return SkillResult.fail(f"Unknown action: {action}")


# =============================================================================
# Test SkillManifest
# =============================================================================


class TestActionParameter:
    """Tests for ActionParameter dataclass."""

    def test_create(self):
        param = ActionParameter(
            name="value",
            type="string",
            required=True,
            description="A value",
        )
        assert param.name == "value"
        assert param.type == "string"
        assert param.required is True
        assert param.description == "A value"

    def test_to_dict(self):
        param = ActionParameter(name="x", type="int")
        data = param.to_dict()
        assert data["name"] == "x"
        assert data["type"] == "int"
        assert data["required"] is False

    def test_from_dict(self):
        data = {"name": "y", "type": "bool", "required": True}
        param = ActionParameter.from_dict(data)
        assert param.name == "y"
        assert param.type == "bool"
        assert param.required is True


class TestSkillAction:
    """Tests for SkillAction dataclass."""

    def test_create_with_params(self):
        action = SkillAction(
            name="test",
            description="Test action",
            parameters=[ActionParameter("x", "int")],
        )
        assert action.name == "test"
        assert len(action.parameters) == 1

    def test_serialization_roundtrip(self):
        action = SkillAction(
            name="test",
            parameters=[ActionParameter("x", "int", True)],
        )
        data = action.to_dict()
        restored = SkillAction.from_dict(data)
        assert restored.name == action.name
        assert len(restored.parameters) == 1


class TestSkillPermissions:
    """Tests for SkillPermissions dataclass."""

    def test_create(self):
        perms = SkillPermissions(
            level="auto",
            requires=["memory.read"],
            sandbox=SkillSandbox(filesystem=["./data"]),
        )
        assert perms.level == "auto"
        assert "memory.read" in perms.requires
        assert "./data" in perms.sandbox.filesystem

    def test_from_dict(self):
        data = {
            "level": "ask",
            "requires": ["nudge.create"],
            "sandbox": {"filesystem": [], "network": ["https://api.example.com"]},
        }
        perms = SkillPermissions.from_dict(data)
        assert perms.level == "ask"
        assert "nudge.create" in perms.requires
        assert "https://api.example.com" in perms.sandbox.network


class TestSkillManifest:
    """Tests for SkillManifest dataclass."""

    def test_create(self):
        manifest = SkillManifest(
            skill_id="test",
            name="Test",
            version="1.0.0",
            entry_module="test.module",
            entry_class="TestSkill",
            permissions=SkillPermissions(level="auto"),
        )
        assert manifest.skill_id == "test"
        assert manifest.enabled is True

    def test_validate_valid(self):
        manifest = SkillManifest(
            skill_id="test_skill",
            name="Test",
            version="1.0.0",
            entry_module="test.module",
            entry_class="TestSkill",
            permissions=SkillPermissions(level="auto"),
        )
        errors = manifest.validate()
        assert len(errors) == 0

    def test_validate_invalid_skill_id(self):
        manifest = SkillManifest(
            skill_id="invalid-id!",
            name="Test",
            version="1.0.0",
            entry_module="test.module",
            entry_class="TestSkill",
            permissions=SkillPermissions(level="auto"),
        )
        errors = manifest.validate()
        assert any("skill_id" in e for e in errors)

    def test_validate_invalid_permission_level(self):
        manifest = SkillManifest(
            skill_id="test",
            name="Test",
            version="1.0.0",
            entry_module="test.module",
            entry_class="TestSkill",
            permissions=SkillPermissions(level="invalid"),
        )
        errors = manifest.validate()
        assert any("permissions.level" in e for e in errors)

    def test_get_action(self):
        manifest = SkillManifest(
            skill_id="test",
            name="Test",
            version="1.0.0",
            entry_module="test.module",
            entry_class="TestSkill",
            permissions=SkillPermissions(level="auto"),
            actions=[SkillAction(name="test_action")],
        )
        action = manifest.get_action("test_action")
        assert action is not None
        assert action.name == "test_action"

        missing = manifest.get_action("missing")
        assert missing is None

    def test_has_permission(self):
        manifest = SkillManifest(
            skill_id="test",
            name="Test",
            version="1.0.0",
            entry_module="test.module",
            entry_class="TestSkill",
            permissions=SkillPermissions(
                level="auto",
                requires=["memory.read"],
            ),
        )
        assert manifest.has_permission("memory.read") is True
        assert manifest.has_permission("memory.write") is False

    def test_subscribes_to(self):
        manifest = SkillManifest(
            skill_id="test",
            name="Test",
            version="1.0.0",
            entry_module="test.module",
            entry_class="TestSkill",
            permissions=SkillPermissions(level="auto"),
            subscriptions=[SkillSubscription(channel="events")],
        )
        assert manifest.subscribes_to("events") is True
        assert manifest.subscribes_to("other") is False

    def test_load_from_yaml(self, temp_skills_dir):
        manifest = SkillManifest.load_from_yaml(temp_skills_dir / "test_skill.yaml")
        assert manifest.skill_id == "test_skill"
        assert manifest.version == "1.0.0"
        assert manifest.permissions.level == "auto"

    def test_serialization_roundtrip(self):
        manifest = SkillManifest(
            skill_id="test",
            name="Test",
            version="1.0.0",
            entry_module="test.module",
            entry_class="TestSkill",
            permissions=SkillPermissions(level="auto"),
            actions=[SkillAction(name="act")],
        )
        data = manifest.to_dict()
        restored = SkillManifest.from_dict(data)
        assert restored.skill_id == manifest.skill_id
        assert len(restored.actions) == 1


class TestDiscoverManifests:
    """Tests for manifest discovery."""

    def test_discover_manifests(self, temp_skills_dir):
        manifests = discover_manifests(temp_skills_dir)
        assert len(manifests) == 1
        assert manifests[0].skill_id == "test_skill"

    def test_discover_nonexistent_dir(self, tmp_path):
        manifests = discover_manifests(tmp_path / "nonexistent")
        assert len(manifests) == 0


# =============================================================================
# Test PermissionChecker
# =============================================================================


class TestPermissionChecker:
    """Tests for PermissionChecker."""

    def test_check_denied_by_default(self, temp_db):
        checker = PermissionChecker(db_path=temp_db)
        result = checker.check("skill", "memory.read")
        assert result.granted is False
        assert result.status == PermissionStatus.DENIED

    def test_auto_permission_granted(self, temp_db):
        checker = PermissionChecker(
            db_path=temp_db,
            auto_permissions={"skill": ["memory.read"]},
        )
        result = checker.check("skill", "memory.read")
        assert result.granted is True
        assert result.status == PermissionStatus.GRANTED

    def test_session_grant(self, temp_db):
        checker = PermissionChecker(db_path=temp_db)

        # Initially denied
        result = checker.check("skill", "memory.read")
        assert result.granted is False

        # Grant for session
        checker.grant_session("skill", "memory.read")

        # Now granted
        result = checker.check("skill", "memory.read")
        assert result.granted is True
        assert "session" in result.reason.lower()

    def test_persistent_grant(self, temp_db):
        checker = PermissionChecker(db_path=temp_db)

        checker.grant_persistent("skill", "memory.read")

        result = checker.check("skill", "memory.read")
        assert result.granted is True

    def test_revoke(self, temp_db):
        checker = PermissionChecker(db_path=temp_db)

        checker.grant_session("skill", "memory.read")
        assert checker.check("skill", "memory.read").granted is True

        checker.revoke("skill", "memory.read")
        assert checker.check("skill", "memory.read").granted is False

    def test_revoke_all(self, temp_db):
        checker = PermissionChecker(db_path=temp_db)

        checker.grant_session("skill", "memory.read")
        checker.grant_session("skill", "memory.write")
        checker.grant_persistent("skill", "nudge.create")

        checker.revoke_all("skill")

        assert checker.check("skill", "memory.read").granted is False
        assert checker.check("skill", "memory.write").granted is False
        assert checker.check("skill", "nudge.create").granted is False

    def test_get_grants(self, temp_db):
        checker = PermissionChecker(
            db_path=temp_db,
            auto_permissions={"skill": ["auto.perm"]},
        )
        checker.grant_session("skill", "session.perm")
        checker.grant_persistent("skill", "persistent.perm")

        grants = checker.get_grants("skill")
        assert "auto.perm" in grants
        assert "session.perm" in grants
        assert "persistent.perm" in grants

    def test_set_auto_permissions(self, temp_db):
        checker = PermissionChecker(db_path=temp_db)

        assert checker.check("skill", "memory.read").granted is False

        checker.set_auto_permissions("skill", ["memory.read"])
        assert checker.check("skill", "memory.read").granted is True

    def test_audit_log(self, temp_db):
        checker = PermissionChecker(db_path=temp_db)

        checker.check("skill", "memory.read")
        checker.grant_session("skill", "memory.read")
        checker.check("skill", "memory.read")

        log = checker.get_audit_log("skill")
        assert len(log) >= 3


# =============================================================================
# Test SkillRegistry
# =============================================================================


class TestSkillRegistry:
    """Tests for SkillRegistry."""

    def test_discover_skills(self, temp_skills_dir, temp_db):
        registry = SkillRegistry(
            skills_dir=temp_skills_dir,
            db_path=temp_db,
        )
        manifests = registry.discover_skills()
        assert len(manifests) == 1
        assert "test_skill" in registry.list_available()

    def test_get_manifest(self, temp_skills_dir, temp_db):
        registry = SkillRegistry(
            skills_dir=temp_skills_dir,
            db_path=temp_db,
        )
        manifest = registry.get_manifest("test_skill")
        assert manifest is not None
        assert manifest.skill_id == "test_skill"

    @pytest.mark.asyncio
    async def test_load_skill(self, temp_skills_dir, temp_db):
        registry = SkillRegistry(
            skills_dir=temp_skills_dir,
            db_path=temp_db,
        )

        # Patch the module import
        with patch.object(registry, "_instantiate_skill", return_value=MockSkill()):
            success = await registry.load_skill("test_skill")
            assert success is True
            assert "test_skill" in registry.list_loaded()

    @pytest.mark.asyncio
    async def test_load_unknown_skill(self, temp_skills_dir, temp_db):
        registry = SkillRegistry(
            skills_dir=temp_skills_dir,
            db_path=temp_db,
        )
        success = await registry.load_skill("nonexistent")
        assert success is False

    @pytest.mark.asyncio
    async def test_unload_skill(self, temp_skills_dir, temp_db):
        registry = SkillRegistry(
            skills_dir=temp_skills_dir,
            db_path=temp_db,
        )

        with patch.object(registry, "_instantiate_skill", return_value=MockSkill()):
            await registry.load_skill("test_skill")
            assert "test_skill" in registry.list_loaded()

            success = await registry.unload_skill("test_skill")
            assert success is True
            assert "test_skill" not in registry.list_loaded()

    @pytest.mark.asyncio
    async def test_get_skill(self, temp_skills_dir, temp_db):
        registry = SkillRegistry(
            skills_dir=temp_skills_dir,
            db_path=temp_db,
        )

        with patch.object(registry, "_instantiate_skill", return_value=MockSkill()):
            await registry.load_skill("test_skill")
            skill = registry.get_skill("test_skill")
            assert skill is not None
            assert skill.skill_id == "test_skill"

    @pytest.mark.asyncio
    async def test_execute_action(self, temp_skills_dir, temp_db):
        registry = SkillRegistry(
            skills_dir=temp_skills_dir,
            db_path=temp_db,
        )

        mock_skill = MockSkill()
        with patch.object(registry, "_instantiate_skill", return_value=mock_skill):
            await registry.load_skill("test_skill")

            # Mock that skill is ready
            mock_skill._state = SkillState.READY

            result = await registry.execute_action(
                "test_skill",
                "do_something",
                {"value": "test"},
            )
            assert result.success is True

    @pytest.mark.asyncio
    async def test_execute_action_not_loaded(self, temp_skills_dir, temp_db):
        registry = SkillRegistry(
            skills_dir=temp_skills_dir,
            db_path=temp_db,
        )
        result = await registry.execute_action("test_skill", "do_something", {})
        assert result.success is False
        assert "not loaded" in result.error.lower()

    def test_get_status(self, temp_skills_dir, temp_db):
        registry = SkillRegistry(
            skills_dir=temp_skills_dir,
            db_path=temp_db,
        )
        status = registry.get_status()
        assert "discovered" in status
        assert "loaded" in status
        assert status["discovered"] == 1
        assert status["loaded"] == 0

    def test_get_skill_info(self, temp_skills_dir, temp_db):
        registry = SkillRegistry(
            skills_dir=temp_skills_dir,
            db_path=temp_db,
        )
        info = registry.get_skill_info("test_skill")
        assert info is not None
        assert info["skill_id"] == "test_skill"
        assert info["is_loaded"] is False

    @pytest.mark.asyncio
    async def test_shutdown(self, temp_skills_dir, temp_db):
        registry = SkillRegistry(
            skills_dir=temp_skills_dir,
            db_path=temp_db,
        )

        with patch.object(registry, "_instantiate_skill", return_value=MockSkill()):
            await registry.load_skill("test_skill")
            assert len(registry.list_loaded()) == 1

            await registry.shutdown()
            assert len(registry.list_loaded()) == 0


# =============================================================================
# Test SkillBase
# =============================================================================


class TestSkillBase:
    """Tests for SkillBase abstract class."""

    def test_skill_state(self):
        skill = MockSkill()
        assert skill.state == SkillState.UNLOADED
        assert skill.is_ready is False

    @pytest.mark.asyncio
    async def test_initialize(self):
        skill = MockSkill()
        context = SkillContext()

        await skill.initialize(context)
        assert skill._initialized is True

    @pytest.mark.asyncio
    async def test_execute(self):
        skill = MockSkill()
        result = await skill.execute("do_something", {"value": "x"})
        assert result.success is True

    @pytest.mark.asyncio
    async def test_execute_unknown_action(self):
        skill = MockSkill()
        result = await skill.execute("unknown", {})
        assert result.success is False


class TestSkillResult:
    """Tests for SkillResult."""

    def test_ok(self):
        result = SkillResult.ok(data={"x": 1}, message="Done")
        assert result.success is True
        assert result.status == SkillResultStatus.SUCCESS
        assert result.data == {"x": 1}

    def test_fail(self):
        result = SkillResult.fail("Something went wrong")
        assert result.success is False
        assert result.status == SkillResultStatus.ERROR
        assert result.error == "Something went wrong"

    def test_denied(self):
        result = SkillResult.denied("memory.write")
        assert result.success is False
        assert result.status == SkillResultStatus.PERMISSION_DENIED

    def test_to_dict(self):
        result = SkillResult.ok(data={"x": 1})
        data = result.to_dict()
        assert data["status"] == "success"
        assert data["data"] == {"x": 1}


# =============================================================================
# Test SkillContext
# =============================================================================


class TestSkillContext:
    """Tests for SkillContext."""

    def test_has_permission_no_checker(self):
        context = SkillContext()
        assert context.has_permission("memory.read") is False

    def test_has_permission_with_checker(self):
        def mock_checker(perm):
            return perm == "memory.read"

        context = SkillContext(check_permission=mock_checker)
        assert context.has_permission("memory.read") is True
        assert context.has_permission("memory.write") is False
