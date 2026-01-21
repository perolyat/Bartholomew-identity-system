"""
Tasks Skill
===========

Create, track, and complete tasks with reminders.
Part of Bartholomew Stage 4 starter skills.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from bartholomew.kernel.skill_base import (
    SkillBase,
    SkillContext,
    SkillResult,
)


logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    """Status of a task."""

    PENDING = "pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TaskPriority(Enum):
    """Priority levels for tasks."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class Task:
    """Represents a task."""

    id: str
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.MEDIUM
    due_date: str | None = None
    tags: list[str] = field(default_factory=list)
    created_at: str = ""
    completed_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.utcnow().isoformat() + "Z"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "priority": self.priority.value,
            "due_date": self.due_date,
            "tags": self.tags,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        return cls(
            id=data["id"],
            title=data["title"],
            description=data.get("description", ""),
            status=TaskStatus(data.get("status", "pending")),
            priority=TaskPriority(data.get("priority", "medium")),
            due_date=data.get("due_date"),
            tags=data.get("tags", []),
            created_at=data.get("created_at", ""),
            completed_at=data.get("completed_at"),
            metadata=data.get("metadata", {}),
        )

    @property
    def is_overdue(self) -> bool:
        """Check if task is overdue."""
        if not self.due_date or self.status != TaskStatus.PENDING:
            return False
        now = datetime.utcnow().isoformat() + "Z"
        return self.due_date < now


class TasksSkill(SkillBase):
    """
    Task management skill.

    Provides task creation, tracking, completion, and reminder functionality.
    """

    # Database schema for tasks
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS skill_tasks (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending',
        priority TEXT NOT NULL DEFAULT 'medium',
        due_date TEXT,
        tags_json TEXT DEFAULT '[]',
        created_at TEXT NOT NULL,
        completed_at TEXT,
        metadata_json TEXT DEFAULT '{}'
    );

    CREATE INDEX IF NOT EXISTS idx_tasks_status ON skill_tasks(status);
    CREATE INDEX IF NOT EXISTS idx_tasks_due ON skill_tasks(due_date);
    CREATE INDEX IF NOT EXISTS idx_tasks_priority ON skill_tasks(priority);
    """

    @property
    def skill_id(self) -> str:
        return "tasks"

    async def initialize(self, context: SkillContext) -> None:
        """Initialize the tasks skill."""
        self._context = context
        self._db_path = context.db_path

        # Initialize database
        if self._db_path:
            self._init_database()

        # Subscribe to events
        if context.workspace:
            self._subscribe_to_channel("tasks")
            self._subscribe_to_channel("system")

        logger.info("Tasks skill initialized")

    def _init_database(self) -> None:
        """Initialize database schema."""
        if not self._db_path:
            return

        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        try:
            conn.executescript(self.SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection."""
        if not self._db_path:
            raise RuntimeError("No database configured")
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    async def shutdown(self) -> None:
        """Shutdown the tasks skill."""
        self._unsubscribe_all()
        logger.info("Tasks skill shutdown")

    async def execute(
        self,
        action: str,
        params: dict[str, Any] | None = None,
    ) -> SkillResult:
        """Execute a task action."""
        params = params or {}

        actions = {
            "create": self._action_create,
            "list": self._action_list,
            "get": self._action_get,
            "complete": self._action_complete,
            "delete": self._action_delete,
            "update": self._action_update,
        }

        handler = actions.get(action)
        if not handler:
            return SkillResult.fail(f"Unknown action: {action}")

        try:
            return await handler(params)
        except Exception as e:
            logger.exception("Task action %s failed: %s", action, e)
            return SkillResult.fail(str(e))

    async def _action_create(self, params: dict[str, Any]) -> SkillResult:
        """Create a new task."""
        # Check permission
        perm_error = self._require_permission("memory.write")
        if perm_error:
            return perm_error

        title = params.get("title")
        if not title:
            return SkillResult.fail("title is required")

        task = Task(
            id=str(uuid.uuid4()),
            title=title,
            description=params.get("description", ""),
            priority=TaskPriority(params.get("priority", "medium")),
            due_date=params.get("due_date"),
            tags=params.get("tags", []),
        )

        # Save to database
        self._save_task(task)

        # Emit event
        self._emit_event("tasks", "task_created", task.to_dict())

        logger.info("Created task: %s", task.id)
        return SkillResult.ok(
            data=task.to_dict(),
            message=f"Task created: {task.title}",
        )

    async def _action_list(self, params: dict[str, Any]) -> SkillResult:
        """List tasks with optional filters."""
        # Check permission
        perm_error = self._require_permission("memory.read")
        if perm_error:
            return perm_error

        status_filter = params.get("status", "all")
        tags_filter = params.get("tags", [])
        limit = params.get("limit", 50)

        tasks = self._get_tasks(
            status=status_filter,
            tags=tags_filter,
            limit=limit,
        )

        return SkillResult.ok(
            data=[t.to_dict() for t in tasks],
            message=f"Found {len(tasks)} tasks",
        )

    async def _action_get(self, params: dict[str, Any]) -> SkillResult:
        """Get a specific task."""
        perm_error = self._require_permission("memory.read")
        if perm_error:
            return perm_error

        task_id = params.get("task_id")
        if not task_id:
            return SkillResult.fail("task_id is required")

        task = self._get_task(task_id)
        if not task:
            return SkillResult.fail(f"Task not found: {task_id}")

        return SkillResult.ok(data=task.to_dict())

    async def _action_complete(self, params: dict[str, Any]) -> SkillResult:
        """Mark a task as complete."""
        perm_error = self._require_permission("memory.write")
        if perm_error:
            return perm_error

        task_id = params.get("task_id")
        if not task_id:
            return SkillResult.fail("task_id is required")

        task = self._get_task(task_id)
        if not task:
            return SkillResult.fail(f"Task not found: {task_id}")

        if task.status == TaskStatus.COMPLETED:
            return SkillResult.ok(
                data=task.to_dict(),
                message="Task already completed",
            )

        task.status = TaskStatus.COMPLETED
        task.completed_at = datetime.utcnow().isoformat() + "Z"
        self._save_task(task)

        # Emit event
        self._emit_event("tasks", "task_completed", task.to_dict())

        logger.info("Completed task: %s", task.id)
        return SkillResult.ok(
            data=task.to_dict(),
            message=f"Task completed: {task.title}",
        )

    async def _action_delete(self, params: dict[str, Any]) -> SkillResult:
        """Delete a task."""
        perm_error = self._require_permission("memory.write")
        if perm_error:
            return perm_error

        task_id = params.get("task_id")
        if not task_id:
            return SkillResult.fail("task_id is required")

        task = self._get_task(task_id)
        if not task:
            return SkillResult.fail(f"Task not found: {task_id}")

        self._delete_task(task_id)

        # Emit event
        self._emit_event("tasks", "task_deleted", {"task_id": task_id})

        logger.info("Deleted task: %s", task_id)
        return SkillResult.ok(message=f"Task deleted: {task.title}")

    async def _action_update(self, params: dict[str, Any]) -> SkillResult:
        """Update a task."""
        perm_error = self._require_permission("memory.write")
        if perm_error:
            return perm_error

        task_id = params.get("task_id")
        if not task_id:
            return SkillResult.fail("task_id is required")

        task = self._get_task(task_id)
        if not task:
            return SkillResult.fail(f"Task not found: {task_id}")

        # Update fields
        if "title" in params:
            task.title = params["title"]
        if "description" in params:
            task.description = params["description"]
        if "priority" in params:
            task.priority = TaskPriority(params["priority"])
        if "due_date" in params:
            task.due_date = params["due_date"]
        if "tags" in params:
            task.tags = params["tags"]

        self._save_task(task)

        # Emit event
        self._emit_event("tasks", "task_updated", task.to_dict())

        logger.info("Updated task: %s", task.id)
        return SkillResult.ok(
            data=task.to_dict(),
            message=f"Task updated: {task.title}",
        )

    # -------------------------------------------------------------------------
    # Database operations
    # -------------------------------------------------------------------------

    def _save_task(self, task: Task) -> None:
        """Save or update a task in the database."""
        if not self._db_path:
            return

        conn = self._get_connection()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO skill_tasks
                (id, title, description, status, priority, due_date,
                    tags_json, created_at, completed_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.id,
                    task.title,
                    task.description,
                    task.status.value,
                    task.priority.value,
                    task.due_date,
                    json.dumps(task.tags),
                    task.created_at,
                    task.completed_at,
                    json.dumps(task.metadata),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _get_task(self, task_id: str) -> Task | None:
        """Get a task by ID."""
        if not self._db_path:
            return None

        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM skill_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()

            if not row:
                return None

            return self._row_to_task(row)
        finally:
            conn.close()

    def _get_tasks(
        self,
        status: str = "all",
        tags: list[str] | None = None,
        limit: int = 50,
    ) -> list[Task]:
        """Get tasks with filters."""
        if not self._db_path:
            return []

        conn = self._get_connection()
        try:
            query = "SELECT * FROM skill_tasks WHERE 1=1"
            params: list[Any] = []

            if status == "pending":
                query += " AND status = 'pending'"
            elif status == "completed":
                query += " AND status = 'completed'"
            elif status == "overdue":
                now = datetime.utcnow().isoformat() + "Z"
                query += " AND status = 'pending' AND due_date < ?"
                params.append(now)

            query += " ORDER BY due_date ASC, priority DESC"
            query += f" LIMIT {limit}"

            rows = conn.execute(query, params).fetchall()
            tasks = [self._row_to_task(row) for row in rows]

            # Filter by tags if specified
            if tags:
                tasks = [t for t in tasks if any(tag in t.tags for tag in tags)]

            return tasks
        finally:
            conn.close()

    def _delete_task(self, task_id: str) -> None:
        """Delete a task from the database."""
        if not self._db_path:
            return

        conn = self._get_connection()
        try:
            conn.execute(
                "DELETE FROM skill_tasks WHERE id = ?",
                (task_id,),
            )
            conn.commit()
        finally:
            conn.close()

    def _row_to_task(self, row: sqlite3.Row) -> Task:
        """Convert a database row to a Task."""
        return Task(
            id=row["id"],
            title=row["title"],
            description=row["description"],
            status=TaskStatus(row["status"]),
            priority=TaskPriority(row["priority"]),
            due_date=row["due_date"],
            tags=json.loads(row["tags_json"]),
            created_at=row["created_at"],
            completed_at=row["completed_at"],
            metadata=json.loads(row["metadata_json"]),
        )

    # -------------------------------------------------------------------------
    # Event handling
    # -------------------------------------------------------------------------

    async def handle_event(self, event: Any) -> None:
        """Handle GlobalWorkspace events."""
        if event.channel == "system":
            if event.event_type == "daily_tick":
                await self._check_overdue_tasks()

    async def _check_overdue_tasks(self) -> None:
        """Check for overdue tasks and emit events."""
        tasks = self._get_tasks(status="overdue")

        for task in tasks:
            self._emit_event(
                "tasks",
                "task_overdue",
                task.to_dict(),
            )

            # Create nudge for overdue task
            if self._has_permission("nudge.create"):
                if self._context and self._context.memory_store:
                    # Would integrate with nudge system here
                    logger.info("Overdue task: %s", task.title)

    # -------------------------------------------------------------------------
    # Status
    # -------------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """Get skill status with task counts."""
        status = super().get_status()

        if self._db_path:
            pending = len(self._get_tasks(status="pending"))
            completed = len(self._get_tasks(status="completed"))
            overdue = len(self._get_tasks(status="overdue"))

            status["task_counts"] = {
                "pending": pending,
                "completed": completed,
                "overdue": overdue,
            }

        return status
