"""
Notify Skill
============

Send notifications and manage alert queuing.
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


class NotificationPriority(Enum):
    """Priority levels for notifications."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class NotificationStatus(Enum):
    """Status of a notification."""

    PENDING = "pending"
    SENT = "sent"
    DISMISSED = "dismissed"
    CANCELLED = "cancelled"


@dataclass
class Notification:
    """Represents a notification."""

    id: str
    message: str
    title: str = ""
    priority: NotificationPriority = NotificationPriority.NORMAL
    status: NotificationStatus = NotificationStatus.PENDING
    sound: bool = True
    deliver_at: str | None = None
    deliver_after_quiet_hours: bool = False
    created_at: str = ""
    sent_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.utcnow().isoformat() + "Z"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "message": self.message,
            "title": self.title,
            "priority": self.priority.value,
            "status": self.status.value,
            "sound": self.sound,
            "deliver_at": self.deliver_at,
            "deliver_after_quiet_hours": self.deliver_after_quiet_hours,
            "created_at": self.created_at,
            "sent_at": self.sent_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Notification:
        return cls(
            id=data["id"],
            message=data["message"],
            title=data.get("title", ""),
            priority=NotificationPriority(data.get("priority", "normal")),
            status=NotificationStatus(data.get("status", "pending")),
            sound=data.get("sound", True),
            deliver_at=data.get("deliver_at"),
            deliver_after_quiet_hours=data.get("deliver_after_quiet_hours", False),
            created_at=data.get("created_at", ""),
            sent_at=data.get("sent_at"),
            metadata=data.get("metadata", {}),
        )


class NotifySkill(SkillBase):
    """
    Notification management skill.

    Provides notification sending, queuing, and quiet hours integration.
    """

    # Database schema for notifications
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS skill_notifications (
        id TEXT PRIMARY KEY,
        message TEXT NOT NULL,
        title TEXT DEFAULT '',
        priority TEXT NOT NULL DEFAULT 'normal',
        status TEXT NOT NULL DEFAULT 'pending',
        sound INTEGER DEFAULT 1,
        deliver_at TEXT,
        deliver_after_quiet_hours INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        sent_at TEXT,
        metadata_json TEXT DEFAULT '{}'
    );

    CREATE INDEX IF NOT EXISTS idx_notif_status
        ON skill_notifications(status);
    CREATE INDEX IF NOT EXISTS idx_notif_deliver
        ON skill_notifications(deliver_at);
    """

    # Default quiet hours (from Identity.yaml or config)
    DEFAULT_QUIET_HOURS_START = "22:00"
    DEFAULT_QUIET_HOURS_END = "07:00"

    @property
    def skill_id(self) -> str:
        return "notify"

    async def initialize(self, context: SkillContext) -> None:
        """Initialize the notify skill."""
        self._context = context
        self._db_path = context.db_path

        # Load quiet hours config
        self._quiet_hours_start = self.DEFAULT_QUIET_HOURS_START
        self._quiet_hours_end = self.DEFAULT_QUIET_HOURS_END

        # Initialize database
        if self._db_path:
            self._init_database()

        # Subscribe to events
        if context.workspace:
            self._subscribe_to_channel("alerts")
            self._subscribe_to_channel("system")

        logger.info("Notify skill initialized")

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
        """Shutdown the notify skill."""
        self._unsubscribe_all()
        logger.info("Notify skill shutdown")

    async def execute(
        self,
        action: str,
        params: dict[str, Any] | None = None,
    ) -> SkillResult:
        """Execute a notification action."""
        params = params or {}

        actions = {
            "send": self._action_send,
            "queue": self._action_queue,
            "list_pending": self._action_list_pending,
            "cancel": self._action_cancel,
            "get_quiet_hours": self._action_get_quiet_hours,
            "is_quiet_hours": self._action_is_quiet_hours,
        }

        handler = actions.get(action)
        if not handler:
            return SkillResult.fail(f"Unknown action: {action}")

        try:
            return await handler(params)
        except Exception as e:
            logger.exception("Notify action %s failed: %s", action, e)
            return SkillResult.fail(str(e))

    async def _action_send(self, params: dict[str, Any]) -> SkillResult:
        """Send a notification immediately (respects quiet hours)."""
        # Check permission
        perm_error = self._require_permission("nudge.create")
        if perm_error:
            return perm_error

        message = params.get("message")
        if not message:
            return SkillResult.fail("message is required")

        priority = NotificationPriority(params.get("priority", "normal"))

        # Check quiet hours (urgent notifications bypass)
        if self._is_quiet_hours() and priority != NotificationPriority.URGENT:
            # Queue for later
            return await self._action_queue(
                {
                    **params,
                    "deliver_after_quiet_hours": True,
                },
            )

        notification = Notification(
            id=str(uuid.uuid4()),
            message=message,
            title=params.get("title", "Bartholomew"),
            priority=priority,
            sound=params.get("sound", True),
            status=NotificationStatus.SENT,
            sent_at=datetime.utcnow().isoformat() + "Z",
        )

        # Save to database
        self._save_notification(notification)

        # Emit event
        self._emit_event("alerts", "notification_sent", notification.to_dict())

        # Actually send (would integrate with system notifications)
        self._deliver_notification(notification)

        logger.info("Sent notification: %s", notification.id)
        return SkillResult.ok(
            data=notification.to_dict(),
            message="Notification sent",
        )

    async def _action_queue(self, params: dict[str, Any]) -> SkillResult:
        """Queue a notification for later delivery."""
        # Check permission
        perm_error = self._require_permission("nudge.create")
        if perm_error:
            return perm_error

        message = params.get("message")
        if not message:
            return SkillResult.fail("message is required")

        notification = Notification(
            id=str(uuid.uuid4()),
            message=message,
            title=params.get("title", "Bartholomew"),
            priority=NotificationPriority(params.get("priority", "normal")),
            sound=params.get("sound", True),
            deliver_at=params.get("deliver_at"),
            deliver_after_quiet_hours=params.get("deliver_after_quiet_hours", False),
            status=NotificationStatus.PENDING,
        )

        # Save to database
        self._save_notification(notification)

        # Emit event
        self._emit_event("alerts", "notification_queued", notification.to_dict())

        logger.info("Queued notification: %s", notification.id)
        return SkillResult.ok(
            data=notification.to_dict(),
            message="Notification queued",
        )

    async def _action_list_pending(self, params: dict[str, Any]) -> SkillResult:
        """List pending notifications."""
        # Check permission
        perm_error = self._require_permission("nudge.read")
        if perm_error:
            return perm_error

        limit = params.get("limit", 50)
        notifications = self._get_pending_notifications(limit)

        return SkillResult.ok(
            data=[n.to_dict() for n in notifications],
            message=f"Found {len(notifications)} pending notifications",
        )

    async def _action_cancel(self, params: dict[str, Any]) -> SkillResult:
        """Cancel a queued notification."""
        # Check permission
        perm_error = self._require_permission("nudge.create")
        if perm_error:
            return perm_error

        notification_id = params.get("notification_id")
        if not notification_id:
            return SkillResult.fail("notification_id is required")

        notification = self._get_notification(notification_id)
        if not notification:
            return SkillResult.fail(f"Notification not found: {notification_id}")

        if notification.status != NotificationStatus.PENDING:
            return SkillResult.fail("Can only cancel pending notifications")

        notification.status = NotificationStatus.CANCELLED
        self._save_notification(notification)

        # Emit event
        self._emit_event(
            "alerts",
            "notification_dismissed",
            {"notification_id": notification_id},
        )

        logger.info("Cancelled notification: %s", notification_id)
        return SkillResult.ok(message="Notification cancelled")

    async def _action_get_quiet_hours(self, params: dict[str, Any]) -> SkillResult:
        """Get quiet hours settings."""
        return SkillResult.ok(
            data={
                "start": self._quiet_hours_start,
                "end": self._quiet_hours_end,
                "is_active": self._is_quiet_hours(),
            },
        )

    async def _action_is_quiet_hours(self, params: dict[str, Any]) -> SkillResult:
        """Check if currently in quiet hours."""
        return SkillResult.ok(data={"is_quiet_hours": self._is_quiet_hours()})

    # -------------------------------------------------------------------------
    # Quiet Hours
    # -------------------------------------------------------------------------

    def _is_quiet_hours(self) -> bool:
        """Check if currently within quiet hours."""
        now = datetime.now()
        current_time = now.strftime("%H:%M")

        start = self._quiet_hours_start
        end = self._quiet_hours_end

        # Handle overnight quiet hours (e.g., 22:00 to 07:00)
        if start > end:
            return current_time >= start or current_time < end
        else:
            return start <= current_time < end

    # -------------------------------------------------------------------------
    # Delivery
    # -------------------------------------------------------------------------

    def _deliver_notification(self, notification: Notification) -> None:
        """
        Actually deliver a notification.

        This would integrate with:
        - Desktop notifications (toast)
        - Sound alerts
        - Mobile push (future)
        """
        # For now, just log
        logger.info(
            "Delivering notification: [%s] %s - %s",
            notification.priority.value,
            notification.title,
            notification.message,
        )

        # TODO: Integrate with system notification APIs
        # - Windows: win10toast or plyer
        # - macOS: osascript
        # - Linux: notify-send

    async def _process_queue(self) -> int:
        """
        Process queued notifications.

        Called periodically to deliver due notifications.
        """
        now = datetime.utcnow().isoformat() + "Z"
        is_quiet = self._is_quiet_hours()
        delivered = 0

        notifications = self._get_pending_notifications(limit=100)

        for notification in notifications:
            should_deliver = False

            # Check deliver_at
            if notification.deliver_at and notification.deliver_at <= now:
                should_deliver = True

            # Check quiet hours
            if notification.deliver_after_quiet_hours and not is_quiet:
                should_deliver = True

            # Urgent always delivers
            if notification.priority == NotificationPriority.URGENT:
                should_deliver = True

            if should_deliver:
                notification.status = NotificationStatus.SENT
                notification.sent_at = now
                self._save_notification(notification)
                self._deliver_notification(notification)

                self._emit_event(
                    "alerts",
                    "notification_sent",
                    notification.to_dict(),
                )
                delivered += 1

        return delivered

    # -------------------------------------------------------------------------
    # Database operations
    # -------------------------------------------------------------------------

    def _save_notification(self, notification: Notification) -> None:
        """Save or update a notification in the database."""
        if not self._db_path:
            return

        conn = self._get_connection()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO skill_notifications
                (id, message, title, priority, status, sound, deliver_at,
                    deliver_after_quiet_hours, created_at, sent_at,
                    metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    notification.id,
                    notification.message,
                    notification.title,
                    notification.priority.value,
                    notification.status.value,
                    1 if notification.sound else 0,
                    notification.deliver_at,
                    1 if notification.deliver_after_quiet_hours else 0,
                    notification.created_at,
                    notification.sent_at,
                    json.dumps(notification.metadata),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _get_notification(self, notification_id: str) -> Notification | None:
        """Get a notification by ID."""
        if not self._db_path:
            return None

        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM skill_notifications WHERE id = ?",
                (notification_id,),
            ).fetchone()

            if not row:
                return None

            return self._row_to_notification(row)
        finally:
            conn.close()

    def _get_pending_notifications(self, limit: int = 50) -> list[Notification]:
        """Get pending notifications."""
        if not self._db_path:
            return []

        conn = self._get_connection()
        try:
            rows = conn.execute(
                """
                SELECT * FROM skill_notifications
                WHERE status = 'pending'
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

            return [self._row_to_notification(row) for row in rows]
        finally:
            conn.close()

    def _row_to_notification(self, row: sqlite3.Row) -> Notification:
        """Convert a database row to a Notification."""
        return Notification(
            id=row["id"],
            message=row["message"],
            title=row["title"],
            priority=NotificationPriority(row["priority"]),
            status=NotificationStatus(row["status"]),
            sound=bool(row["sound"]),
            deliver_at=row["deliver_at"],
            deliver_after_quiet_hours=bool(row["deliver_after_quiet_hours"]),
            created_at=row["created_at"],
            sent_at=row["sent_at"],
            metadata=json.loads(row["metadata_json"]),
        )

    # -------------------------------------------------------------------------
    # Event handling
    # -------------------------------------------------------------------------

    async def handle_event(self, event: Any) -> None:
        """Handle GlobalWorkspace events."""
        if event.channel == "system":
            if event.event_type == "quiet_hours_end":
                await self._process_queue()

        elif event.channel == "alerts":
            if event.event_type == "alert_requested":
                # Handle alert request from other skills
                payload = event.payload or {}
                await self._action_send(
                    {
                        "message": payload.get("message", "Alert"),
                        "title": payload.get("title", "Bartholomew"),
                        "priority": payload.get("priority", "normal"),
                    },
                )

    # -------------------------------------------------------------------------
    # Status
    # -------------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """Get skill status with notification counts."""
        status = super().get_status()

        status["quiet_hours"] = {
            "start": self._quiet_hours_start,
            "end": self._quiet_hours_end,
            "is_active": self._is_quiet_hours(),
        }

        if self._db_path:
            pending = len(self._get_pending_notifications())
            status["pending_count"] = pending

        return status
