"""
Notify Skill
============

Send notifications and manage alert queuing.
Part of Bartholomew Stage 4 starter skills.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import requests

from bartholomew.kernel.blocking_executor import run_off_loop
from bartholomew.kernel.db_ctx import set_wal_pragmas
from bartholomew.kernel.skill_base import (
    SkillBase,
    SkillContext,
    SkillResult,
)

logger = logging.getLogger(__name__)

#: Usable POC slice 1: the one real notification delivery channel, so a
#: notification can reach the tester outside the browser tab.
#:
#: **Provider-agnostic by approval clarification (2026-08-14).** This is a
#: plain configurable-URL HTTP POST with a JSON body. There is deliberately no
#: provider SDK, no provider-specific payload shape, and no provider-specific
#: branch anywhere below: any endpoint that accepts a POST works. `ntfy` is
#: the intended first real-world test endpoint, supplied as a configuration
#: value -- it must not become an architectural dependency.
#:
#: Unset (the default) keeps the pre-existing log-only behaviour exactly, so
#: this is additive rather than a new failure mode.
WEBHOOK_URL_ENV = "BARTHOLOMEW_NOTIFY_WEBHOOK_URL"

#: Bounded so a slow or black-holed endpoint cannot stall notification
#: delivery indefinitely. Delivery already runs off the event loop, but an
#: unbounded socket read would still pin the worker thread.
WEBHOOK_TIMEOUT_SECONDS = 10.0


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

    -- Stage 1, S1.3: persisted quiet-hours/mute settings. Singleton row
    -- (id=1) -- previously quiet hours were hardcoded to DEFAULT_QUIET_
    -- HOURS_START/END on every initialize() with no way to change them,
    -- and there was no mute concept at all.
    CREATE TABLE IF NOT EXISTS notification_settings (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        quiet_hours_start TEXT NOT NULL,
        quiet_hours_end TEXT NOT NULL,
        muted INTEGER NOT NULL DEFAULT 0,
        muted_until TEXT,
        updated_at TEXT NOT NULL
    );
    """

    # Default quiet hours (from Identity.yaml or config)
    DEFAULT_QUIET_HOURS_START = "22:00"
    DEFAULT_QUIET_HOURS_END = "07:00"

    #: Resolved in initialize() from WEBHOOK_URL_ENV. Declared here so an
    #: uninitialised instance degrades to the log-only path rather than
    #: raising AttributeError.
    _webhook_url: str = ""

    @property
    def skill_id(self) -> str:
        return "notify"

    async def initialize(self, context: SkillContext) -> None:
        """Initialize the notify skill."""
        self._context = context
        self._db_path = context.db_path

        # Initialize database
        if self._db_path:
            self._init_database()

        # Stage 1, S1.3: load persisted quiet-hours/mute settings (seeding
        # the singleton row with the class defaults on first run) instead
        # of always resetting to the hardcoded defaults.
        self._quiet_hours_start = self.DEFAULT_QUIET_HOURS_START
        self._quiet_hours_end = self.DEFAULT_QUIET_HOURS_END
        self._muted = False
        self._muted_until: str | None = None
        if self._db_path:
            self._load_settings()

        # Usable POC slice 1: the outbound webhook delivery channel. Read from
        # the environment rather than persisted, so no credential-bearing
        # value is written to the database -- a topic-style endpoint needs no
        # secret at all, and even a URL-as-secret webhook stays in process
        # configuration where the operator already manages it.
        self._webhook_url = (os.getenv(WEBHOOK_URL_ENV) or "").strip()
        if self._webhook_url:
            logger.info("Notification webhook delivery enabled")

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
        """
        Get database connection.

        Previously a bare sqlite3.connect() with no explicit WAL/busy_timeout
        configuration -- relying only on Python's incidental ~5s default
        connect timeout. Under concurrent writers to the same shared db file
        (the scheduler's autonomy loop writes to `reflections`/
        `scheduled_tasks` on every drive tick, most heavily right at
        startup when every drive becomes immediately due), that was
        confirmed to raise sqlite3.OperationalError: database is locked --
        reproduced directly against this exact connection pattern.

        set_wal_pragmas() brings this in line with the rest of the kernel's
        connections (WAL mode, synchronous=NORMAL, foreign_keys=ON,
        busy_timeout=5000) -- the codebase's one existing standard for this,
        applied here rather than left implicit/undocumented. No shorter
        override: there's no UX/runtime requirement calling for this
        connection to fail faster than that standard, and narrowing it would
        only make this path more likely to lose exactly the kind of brief,
        self-resolving contention (the scheduler's startup burst) this fix
        exists to tolerate.
        """
        if not self._db_path:
            raise RuntimeError("No database configured")
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        set_wal_pragmas(conn)
        return conn

    def _load_settings(self) -> None:
        """
        Load persisted quiet-hours/mute settings into memory, seeding the
        singleton row with the class defaults if this is the first run
        against this database.
        """
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT quiet_hours_start, quiet_hours_end, muted, muted_until "
                "FROM notification_settings WHERE id = 1",
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO notification_settings "
                    "(id, quiet_hours_start, quiet_hours_end, muted, muted_until, updated_at) "
                    "VALUES (1, ?, ?, 0, NULL, ?)",
                    (
                        self._quiet_hours_start,
                        self._quiet_hours_end,
                        datetime.utcnow().isoformat() + "Z",
                    ),
                )
                conn.commit()
                return
            self._quiet_hours_start = row["quiet_hours_start"]
            self._quiet_hours_end = row["quiet_hours_end"]
            self._muted = bool(row["muted"])
            self._muted_until = row["muted_until"]
        finally:
            conn.close()

    def _save_settings(self) -> None:
        """Persist the current in-memory quiet-hours/mute settings."""
        if not self._db_path:
            return
        conn = self._get_connection()
        try:
            conn.execute(
                "UPDATE notification_settings "
                "SET quiet_hours_start = ?, quiet_hours_end = ?, muted = ?, "
                "muted_until = ?, updated_at = ? WHERE id = 1",
                (
                    self._quiet_hours_start,
                    self._quiet_hours_end,
                    1 if self._muted else 0,
                    self._muted_until,
                    datetime.utcnow().isoformat() + "Z",
                ),
            )
            conn.commit()
        finally:
            conn.close()

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
            "set_quiet_hours": self._action_set_quiet_hours,
            "mute": self._action_mute,
            "unmute": self._action_unmute,
            "get_notification_settings": self._action_get_notification_settings,
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

        # Check mute/quiet hours (urgent notifications bypass both -- mute
        # is treated the same as an always-on quiet-hours window rather
        # than a separate gate, per Stage 1 S1.3's design).
        if (self._is_muted() or self._is_quiet_hours()) and priority != NotificationPriority.URGENT:
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

        # Actually send -- real outbound delivery when a webhook is configured
        # (Usable POC slice 1), log-only otherwise.
        await self._deliver_notification(notification)

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

    async def _action_set_quiet_hours(self, params: dict[str, Any]) -> SkillResult:
        """Set and persist quiet hours (Stage 1, S1.3)."""
        perm_error = self._require_permission("nudge.create")
        if perm_error:
            return perm_error

        start = params.get("start")
        end = params.get("end")
        if not start or not end:
            return SkillResult.fail("start and end are required")
        if not (self._is_valid_hhmm(start) and self._is_valid_hhmm(end)):
            return SkillResult.fail("start and end must be 'HH:MM' (24-hour)")

        self._quiet_hours_start = start
        self._quiet_hours_end = end
        self._save_settings()

        return SkillResult.ok(
            data={"start": start, "end": end, "is_active": self._is_quiet_hours()},
            message="Quiet hours updated",
        )

    async def _action_mute(self, params: dict[str, Any]) -> SkillResult:
        """Mute non-urgent notifications, optionally until a given time (Stage 1, S1.3)."""
        perm_error = self._require_permission("nudge.create")
        if perm_error:
            return perm_error

        until = params.get("until")
        normalized_until = None
        if until is not None:
            normalized_until = self._normalize_until(until)
            if normalized_until is None:
                return SkillResult.fail("until must be a valid ISO-8601 timestamp")

        self._muted = True
        self._muted_until = normalized_until
        self._save_settings()

        return SkillResult.ok(
            data={"muted": True, "muted_until": normalized_until},
            message="Notifications muted",
        )

    async def _action_unmute(self, params: dict[str, Any]) -> SkillResult:
        """Clear mute (Stage 1, S1.3)."""
        perm_error = self._require_permission("nudge.create")
        if perm_error:
            return perm_error

        self._muted = False
        self._muted_until = None
        self._save_settings()

        return SkillResult.ok(data={"muted": False}, message="Notifications unmuted")

    async def _action_get_notification_settings(self, params: dict[str, Any]) -> SkillResult:
        """Combined quiet-hours + mute status (Stage 1, S1.3)."""
        # Compute effective_muted() first -- it lazily clears (and persists
        # the clear) an expired muted_until, so muted/muted_until below
        # must be read after that check runs, not before, or a just-expired
        # mute would report a self-contradictory muted=true/effective=false.
        effective_muted = self._is_muted()
        return SkillResult.ok(
            data={
                "quiet_hours": {
                    "start": self._quiet_hours_start,
                    "end": self._quiet_hours_end,
                    "is_active": self._is_quiet_hours(),
                },
                "muted": self._muted,
                "muted_until": self._muted_until,
                "effective_muted": effective_muted,
            },
        )

    @staticmethod
    def _is_valid_hhmm(value: str) -> bool:
        try:
            hh, mm = value.split(":")
            return len(hh) == 2 and len(mm) == 2 and 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59
        except (ValueError, AttributeError):
            return False

    @staticmethod
    def _normalize_until(value: str) -> str | None:
        """
        Parse a mute-expiration timestamp and normalize it to a UTC
        ISO-8601 string with a 'Z' suffix. `_is_muted()` used to compare
        `muted_until` lexicographically against a same-format `now` string
        -- correct only when every stored value already uses that exact
        format. An offset-aware ISO timestamp (e.g. "...-05:00") or an
        unparseable string would compare incorrectly or never expire.
        Normalizing at write time keeps the later comparison unambiguous.
        Returns None if `value` isn't a parseable ISO-8601 timestamp.
        """
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    # -------------------------------------------------------------------------
    # Quiet Hours / Mute
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

    def _is_muted(self) -> bool:
        """
        Check if notifications are currently muted, lazily clearing (and
        persisting the clear, not just computing a value) an expired
        `muted_until` so a stale mute doesn't silently outlive its window.
        """
        if not self._muted:
            return False
        if self._muted_until:
            try:
                until_dt = datetime.fromisoformat(self._muted_until.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                until_dt = None
            # An unparseable stored value (corrupt/legacy data) is treated
            # as already expired rather than an indefinite mute -- fails
            # toward notifications resuming, not toward a mute nothing can
            # clear.
            if until_dt is None or datetime.now(timezone.utc) >= until_dt:
                self._muted = False
                self._muted_until = None
                self._save_settings()
                return False
        return True

    # -------------------------------------------------------------------------
    # Delivery
    # -------------------------------------------------------------------------

    @staticmethod
    def _post_webhook(url: str, payload: dict[str, Any]) -> bool:
        """
        POST one notification to the configured webhook endpoint.

        Synchronous by design -- `requests` is already a declared dependency
        and is a blocking client, so this is called through `run_off_loop()`
        rather than being made async with a new async HTTP dependency.

        Returns True only for a 2xx response. Never raises: a delivery
        failure is logged and reported, never propagated into the caller,
        because notification delivery must not be able to break the action
        that triggered it.
        """
        try:
            response = requests.post(
                url,
                json=payload,
                timeout=WEBHOOK_TIMEOUT_SECONDS,
                headers={"Content-Type": "application/json"},
            )
            if 200 <= response.status_code < 300:
                return True
            logger.warning(
                "Notification webhook returned HTTP %s for notification %s",
                response.status_code,
                payload.get("id"),
            )
            return False
        except Exception:
            logger.exception("Notification webhook delivery failed for %s", payload.get("id"))
            return False

    async def _deliver_notification(self, notification: Notification) -> bool:
        """
        Actually deliver a notification.

        Usable POC slice 1 replaced this method's log-only stub with a real
        outbound HTTP POST to a configured webhook URL (see
        `WEBHOOK_URL_ENV`), so a notification can reach the tester outside the
        browser tab. The log line is kept -- it is the local record of every
        delivery attempt, and the whole behaviour when no URL is configured.

        Returns True when a webhook delivery succeeded. False covers both "no
        webhook configured" (the unchanged log-only path) and "delivery
        attempted and failed", which is why the caller treats the return value
        as reporting only, never as a reason to alter notification state: a
        notification that was sent is sent regardless of whether an external
        endpoint happened to be reachable.
        """
        logger.info(
            "Delivering notification: [%s] %s - %s",
            notification.priority.value,
            notification.title,
            notification.message,
        )

        url = self._webhook_url
        if not url:
            return False

        # Provider-agnostic body: the notification's own fields, as JSON.
        # No provider-specific shaping -- see WEBHOOK_URL_ENV's note.
        payload = notification.to_dict()
        payload["source"] = "bartholomew"

        try:
            return bool(
                await run_off_loop(
                    self._post_webhook,
                    url,
                    payload,
                    executor=getattr(self._context, "blocking_executor", None),
                ),
            )
        except Exception:
            logger.exception(
                "Notification webhook dispatch failed for %s",
                notification.id,
            )
            return False

    async def _process_queue(self) -> int:
        """
        Process queued notifications.

        Called periodically to deliver due notifications.
        """
        now = datetime.utcnow().isoformat() + "Z"
        is_suppressed = self._is_muted() or self._is_quiet_hours()
        delivered = 0

        notifications = self._get_pending_notifications(limit=100)

        for notification in notifications:
            should_deliver = False

            # Check deliver_at
            if notification.deliver_at and notification.deliver_at <= now:
                should_deliver = True

            # Check quiet hours/mute -- a queued item waits for both
            # quiet hours to end AND mute to be cleared, not just whichever
            # cleared first (Stage 1, S1.3).
            if notification.deliver_after_quiet_hours and not is_suppressed:
                should_deliver = True

            # Urgent always delivers
            if notification.priority == NotificationPriority.URGENT:
                should_deliver = True

            if should_deliver:
                notification.status = NotificationStatus.SENT
                notification.sent_at = now
                self._save_notification(notification)
                await self._deliver_notification(notification)

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
        status["muted"] = self._muted
        status["muted_until"] = self._muted_until

        if self._db_path:
            pending = len(self._get_pending_notifications())
            status["pending_count"] = pending

        return status
