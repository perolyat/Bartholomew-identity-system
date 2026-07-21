"""
End-to-end test for the P2 skill-system wiring: prompt -> decide -> tool
call -> persisted + audited.

Covers the full path from Planner.handle_skill_request() through
SkillRegistry.execute_action(), exercising:
  - an "auto"-permission skill (tasks): create -> persisted in
    skill_tasks -> audited in skill_action_audit
  - an "ask"-permission skill (calendar_draft) consent flow: approve via
    the registered consent handler -> granted + persisted + audited
  - the same "ask" flow with no consent handler registered -> denied +
    audited (fail-closed)
  - the global ParkingBrake's "skills" scope blocking execution, then
    disengaging to restore normal operation
"""

from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from bartholomew.kernel.memory.privacy_guard import set_consent_handler
from bartholomew.kernel.planner import Planner
from bartholomew.kernel.skill_base import SkillResultStatus
from bartholomew.kernel.skill_permissions import reset_permission_checker
from bartholomew.kernel.skill_registry import SkillRegistry, reset_skill_registry


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except (PermissionError, FileNotFoundError):
        pass


@pytest.fixture(autouse=True)
def reset_singletons():
    reset_skill_registry()
    reset_permission_checker()
    set_consent_handler(None)
    yield
    reset_skill_registry()
    reset_permission_checker()
    set_consent_handler(None)


def _query_all(db_path: str, sql: str, params: tuple = ()) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


@pytest.mark.asyncio
class TestEndToEndTasksAndAudit:
    """prompt -> decide -> tool call -> persisted + audited."""

    async def test_auto_permission_skill_persists_and_audits(self, temp_db):
        """tasks.create (level: auto) should succeed, persist, and audit."""
        registry = SkillRegistry(db_path=temp_db)
        planner = Planner(policy={}, drives={"drives": []}, mem=None, skill_registry=registry)

        assert await registry.load_skill("tasks") is True

        result = await planner.handle_skill_request(
            "tasks",
            "create",
            {"title": "Buy milk"},
        )

        assert result.success is True
        assert result.data["title"] == "Buy milk"

        rows = _query_all(temp_db, "SELECT title FROM skill_tasks")
        assert [r[0] for r in rows] == ["Buy milk"]

        audit_rows = _query_all(
            temp_db,
            "SELECT skill_id, action, status FROM skill_action_audit",
        )
        assert ("tasks", "create", "success") in audit_rows

    async def test_ask_permission_skill_approved_persists_and_audits(self, temp_db):
        """calendar_draft.create (level: ask) with an approving consent handler."""
        registry = SkillRegistry(db_path=temp_db)
        planner = Planner(policy={}, drives={"drives": []}, mem=None, skill_registry=registry)

        set_consent_handler(lambda prompt: True)

        assert await registry.load_skill("calendar_draft") is True

        result = await planner.handle_skill_request(
            "calendar_draft",
            "create",
            {"title": "Team sync", "start": "2026-08-01T10:00:00"},
        )

        assert result.success is True

        rows = _query_all(temp_db, "SELECT title FROM skill_calendar_events")
        assert [r[0] for r in rows] == ["Team sync"]

        audit_rows = _query_all(
            temp_db,
            "SELECT skill_id, action, status FROM skill_action_audit",
        )
        assert ("calendar_draft", "create", "success") in audit_rows

    async def test_ask_permission_skill_denied_without_consent_handler(self, temp_db):
        """Same "ask" flow, but with no consent handler registered -- fail closed."""
        registry = SkillRegistry(db_path=temp_db)
        planner = Planner(policy={}, drives={"drives": []}, mem=None, skill_registry=registry)

        assert await registry.load_skill("calendar_draft") is True

        result = await planner.handle_skill_request(
            "calendar_draft",
            "create",
            {"title": "Team sync", "start": "2026-08-01T10:00:00"},
        )

        assert result.status == SkillResultStatus.PERMISSION_DENIED

        rows = _query_all(temp_db, "SELECT title FROM skill_calendar_events")
        assert rows == []

        audit_rows = _query_all(
            temp_db,
            "SELECT skill_id, action, status FROM skill_action_audit",
        )
        assert ("calendar_draft", "create", "permission_denied") in audit_rows

    async def test_parking_brake_blocks_then_disengage_allows(self, temp_db):
        """Engaging the ParkingBrake's "skills" scope blocks execution; disengaging restores it."""
        from bartholomew.orchestrator.safety.parking_brake import BrakeStorage, ParkingBrake

        registry = SkillRegistry(db_path=temp_db)
        planner = Planner(policy={}, drives={"drives": []}, mem=None, skill_registry=registry)

        assert await registry.load_skill("tasks") is True

        brake = ParkingBrake(BrakeStorage(temp_db))
        brake.engage("skills")

        blocked_result = await planner.handle_skill_request(
            "tasks",
            "create",
            {"title": "Should be blocked"},
        )
        assert blocked_result.success is False
        assert "parking brake" in blocked_result.error.lower()

        rows = _query_all(temp_db, "SELECT title FROM skill_tasks")
        assert rows == []

        audit_rows = _query_all(
            temp_db,
            "SELECT skill_id, action, status, result_error FROM skill_action_audit",
        )
        assert any(
            row[0] == "tasks" and row[1] == "create" and row[2] == "error" for row in audit_rows
        )

        brake.disengage()

        allowed_result = await planner.handle_skill_request(
            "tasks",
            "create",
            {"title": "Should succeed"},
        )
        assert allowed_result.success is True

        rows = _query_all(temp_db, "SELECT title FROM skill_tasks")
        assert [r[0] for r in rows] == ["Should succeed"]
