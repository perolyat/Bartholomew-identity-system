"""
Calendar Draft Skill
====================

Create draft calendar events with natural language parsing and .ics export.
Part of Bartholomew Stage 4 starter skills.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from bartholomew.kernel.skill_base import (
    SkillBase,
    SkillContext,
    SkillResult,
)


logger = logging.getLogger(__name__)


@dataclass
class CalendarEvent:
    """Represents a draft calendar event."""

    id: str
    title: str
    start: str  # ISO 8601
    end: str | None = None  # ISO 8601, defaults to start + 1 hour
    description: str = ""
    location: str = ""
    all_day: bool = False
    reminder_minutes: int | None = None
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.utcnow().isoformat() + "Z"
        if not self.end and self.start and not self.all_day:
            # Default to 1 hour duration
            try:
                start_dt = datetime.fromisoformat(self.start.replace("Z", "+00:00"))
                end_dt = start_dt + timedelta(hours=1)
                self.end = end_dt.isoformat()
            except Exception:
                pass

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "start": self.start,
            "end": self.end,
            "description": self.description,
            "location": self.location,
            "all_day": self.all_day,
            "reminder_minutes": self.reminder_minutes,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CalendarEvent:
        return cls(
            id=data["id"],
            title=data["title"],
            start=data["start"],
            end=data.get("end"),
            description=data.get("description", ""),
            location=data.get("location", ""),
            all_day=data.get("all_day", False),
            reminder_minutes=data.get("reminder_minutes"),
            created_at=data.get("created_at", ""),
            metadata=data.get("metadata", {}),
        )

    def to_ics(self) -> str:
        """Convert event to iCalendar format."""
        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Bartholomew//Calendar Draft//EN",
            "BEGIN:VEVENT",
            f"UID:{self.id}",
            f"SUMMARY:{self._escape_ics(self.title)}",
        ]

        # Format dates
        if self.all_day:
            start_dt = datetime.fromisoformat(self.start.replace("Z", "+00:00"))
            lines.append(f"DTSTART;VALUE=DATE:{start_dt.strftime('%Y%m%d')}")
            if self.end:
                end_dt = datetime.fromisoformat(self.end.replace("Z", "+00:00"))
                lines.append(f"DTEND;VALUE=DATE:{end_dt.strftime('%Y%m%d')}")
        else:
            lines.append(f"DTSTART:{self._format_ics_datetime(self.start)}")
            if self.end:
                lines.append(f"DTEND:{self._format_ics_datetime(self.end)}")

        if self.description:
            lines.append(f"DESCRIPTION:{self._escape_ics(self.description)}")
        if self.location:
            lines.append(f"LOCATION:{self._escape_ics(self.location)}")

        if self.reminder_minutes:
            lines.extend(
                [
                    "BEGIN:VALARM",
                    "ACTION:DISPLAY",
                    f"TRIGGER:-PT{self.reminder_minutes}M",
                    "DESCRIPTION:Reminder",
                    "END:VALARM",
                ],
            )

        lines.extend(
            [
                f"CREATED:{self._format_ics_datetime(self.created_at)}",
                "END:VEVENT",
                "END:VCALENDAR",
            ],
        )

        return "\r\n".join(lines)

    def _format_ics_datetime(self, iso_str: str) -> str:
        """Format datetime for iCalendar."""
        try:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            return dt.strftime("%Y%m%dT%H%M%SZ")
        except Exception:
            return datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    def _escape_ics(self, text: str) -> str:
        """Escape special characters for iCalendar."""
        text = text.replace("\\", "\\\\")
        text = text.replace(",", "\\,")
        text = text.replace(";", "\\;")
        text = text.replace("\n", "\\n")
        return text


class CalendarDraftSkill(SkillBase):
    """
    Calendar draft skill.

    Provides draft event creation, natural language date parsing,
    and .ics export functionality.
    """

    # Database schema
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS skill_calendar_events (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        start TEXT NOT NULL,
        end TEXT,
        description TEXT DEFAULT '',
        location TEXT DEFAULT '',
        all_day INTEGER DEFAULT 0,
        reminder_minutes INTEGER,
        created_at TEXT NOT NULL,
        metadata_json TEXT DEFAULT '{}'
    );

    CREATE INDEX IF NOT EXISTS idx_cal_start
        ON skill_calendar_events(start);
    """

    @property
    def skill_id(self) -> str:
        return "calendar_draft"

    async def initialize(self, context: SkillContext) -> None:
        """Initialize the calendar draft skill."""
        self._context = context
        self._db_path = context.db_path

        # Initialize database
        if self._db_path:
            self._init_database()

        # Create exports directory
        self._exports_dir = Path("./exports/calendar")
        self._exports_dir.mkdir(parents=True, exist_ok=True)

        # Subscribe to events
        if context.workspace:
            self._subscribe_to_channel("tasks")
            self._subscribe_to_channel("system")

        logger.info("Calendar draft skill initialized")

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
        """Shutdown the calendar draft skill."""
        self._unsubscribe_all()
        logger.info("Calendar draft skill shutdown")

    async def execute(
        self,
        action: str,
        params: dict[str, Any] | None = None,
    ) -> SkillResult:
        """Execute a calendar action."""
        params = params or {}

        actions = {
            "create": self._action_create,
            "list": self._action_list,
            "get": self._action_get,
            "update": self._action_update,
            "delete": self._action_delete,
            "export_ics": self._action_export_ics,
            "parse_datetime": self._action_parse_datetime,
        }

        handler = actions.get(action)
        if not handler:
            return SkillResult.fail(f"Unknown action: {action}")

        try:
            return await handler(params)
        except Exception as e:
            logger.exception("Calendar action %s failed: %s", action, e)
            return SkillResult.fail(str(e))

    async def _action_create(self, params: dict[str, Any]) -> SkillResult:
        """Create a draft calendar event."""
        perm_error = self._require_permission("memory.write")
        if perm_error:
            return perm_error

        title = params.get("title")
        if not title:
            return SkillResult.fail("title is required")

        start_input = params.get("start")
        if not start_input:
            return SkillResult.fail("start is required")

        # Parse start time (natural language or ISO)
        start = self._parse_datetime(start_input)
        if not start:
            return SkillResult.fail(f"Could not parse start: {start_input}")

        # Parse end time if provided
        end = None
        end_input = params.get("end")
        if end_input:
            end = self._parse_datetime(end_input)

        event = CalendarEvent(
            id=str(uuid.uuid4()),
            title=title,
            start=start,
            end=end,
            description=params.get("description", ""),
            location=params.get("location", ""),
            all_day=params.get("all_day", False),
            reminder_minutes=params.get("reminder_minutes"),
        )

        self._save_event(event)

        # Emit event
        self._emit_event("calendar", "event_drafted", event.to_dict())

        logger.info("Created calendar event: %s", event.id)
        return SkillResult.ok(
            data=event.to_dict(),
            message=f"Event created: {event.title}",
        )

    async def _action_list(self, params: dict[str, Any]) -> SkillResult:
        """List calendar events."""
        perm_error = self._require_permission("memory.read")
        if perm_error:
            return perm_error

        from_date = params.get("from_date")
        to_date = params.get("to_date")
        limit = params.get("limit", 50)

        events = self._get_events(
            from_date=from_date,
            to_date=to_date,
            limit=limit,
        )

        return SkillResult.ok(
            data=[e.to_dict() for e in events],
            message=f"Found {len(events)} events",
        )

    async def _action_get(self, params: dict[str, Any]) -> SkillResult:
        """Get a specific event."""
        perm_error = self._require_permission("memory.read")
        if perm_error:
            return perm_error

        event_id = params.get("event_id")
        if not event_id:
            return SkillResult.fail("event_id is required")

        event = self._get_event(event_id)
        if not event:
            return SkillResult.fail(f"Event not found: {event_id}")

        return SkillResult.ok(data=event.to_dict())

    async def _action_update(self, params: dict[str, Any]) -> SkillResult:
        """Update an event."""
        perm_error = self._require_permission("memory.write")
        if perm_error:
            return perm_error

        event_id = params.get("event_id")
        if not event_id:
            return SkillResult.fail("event_id is required")

        event = self._get_event(event_id)
        if not event:
            return SkillResult.fail(f"Event not found: {event_id}")

        # Update fields
        if "title" in params:
            event.title = params["title"]
        if "start" in params:
            start = self._parse_datetime(params["start"])
            if start:
                event.start = start
        if "end" in params:
            end = self._parse_datetime(params["end"])
            if end:
                event.end = end
        if "description" in params:
            event.description = params["description"]
        if "location" in params:
            event.location = params["location"]
        if "all_day" in params:
            event.all_day = params["all_day"]
        if "reminder_minutes" in params:
            event.reminder_minutes = params["reminder_minutes"]

        self._save_event(event)

        # Emit event
        self._emit_event("calendar", "event_updated", event.to_dict())

        logger.info("Updated calendar event: %s", event.id)
        return SkillResult.ok(
            data=event.to_dict(),
            message=f"Event updated: {event.title}",
        )

    async def _action_delete(self, params: dict[str, Any]) -> SkillResult:
        """Delete an event."""
        perm_error = self._require_permission("memory.write")
        if perm_error:
            return perm_error

        event_id = params.get("event_id")
        if not event_id:
            return SkillResult.fail("event_id is required")

        event = self._get_event(event_id)
        if not event:
            return SkillResult.fail(f"Event not found: {event_id}")

        self._delete_event(event_id)

        # Emit event
        self._emit_event("calendar", "event_deleted", {"event_id": event_id})

        logger.info("Deleted calendar event: %s", event_id)
        return SkillResult.ok(message=f"Event deleted: {event.title}")

    async def _action_export_ics(self, params: dict[str, Any]) -> SkillResult:
        """Export events to .ics file."""
        perm_error = self._require_permission("memory.read")
        if perm_error:
            return perm_error

        event_id = params.get("event_id")
        from_date = params.get("from_date")
        to_date = params.get("to_date")

        if event_id:
            # Export single event
            event = self._get_event(event_id)
            if not event:
                return SkillResult.fail(f"Event not found: {event_id}")
            events = [event]
        else:
            # Export multiple events
            events = self._get_events(
                from_date=from_date,
                to_date=to_date,
            )

        if not events:
            return SkillResult.fail("No events to export")

        # Generate .ics file
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"calendar_export_{timestamp}.ics"
        filepath = self._exports_dir / filename

        # Combine events into single calendar
        ics_content = self._events_to_ics(events)
        filepath.write_text(ics_content)

        # Emit event
        self._emit_event(
            "calendar",
            "event_exported",
            {"filepath": str(filepath), "count": len(events)},
        )

        logger.info("Exported %d events to %s", len(events), filepath)
        return SkillResult.ok(
            data={
                "filepath": str(filepath),
                "count": len(events),
            },
            message=f"Exported {len(events)} events to {filename}",
        )

    async def _action_parse_datetime(self, params: dict[str, Any]) -> SkillResult:
        """Parse natural language datetime string."""
        text = params.get("text")
        if not text:
            return SkillResult.fail("text is required")

        result = self._parse_datetime(text)
        if not result:
            return SkillResult.fail(f"Could not parse: {text}")

        return SkillResult.ok(
            data={"input": text, "parsed": result},
            message=f"Parsed to: {result}",
        )

    # -------------------------------------------------------------------------
    # Natural Language Date Parsing
    # -------------------------------------------------------------------------

    def _parse_datetime(self, text: str) -> str | None:
        """
        Parse natural language or ISO datetime string.

        Supports:
        - ISO 8601: "2026-01-22T15:00:00Z"
        - "today", "tomorrow", "yesterday"
        - "next Monday", "this Friday"
        - "at 3pm", "at 15:00"
        - "tomorrow at 3pm"
        - "in 2 hours", "in 30 minutes"
        """
        text = text.strip().lower()

        # Try ISO format first
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00").replace("z", "+00:00"))
            return dt.isoformat()
        except ValueError:
            pass

        now = datetime.now()
        result_date = now.date()
        result_time = None

        # Parse relative days
        if "today" in text:
            result_date = now.date()
        elif "tomorrow" in text:
            result_date = now.date() + timedelta(days=1)
        elif "yesterday" in text:
            result_date = now.date() - timedelta(days=1)
        elif "next" in text or "this" in text:
            # next/this + weekday
            weekdays = {
                "monday": 0,
                "tuesday": 1,
                "wednesday": 2,
                "thursday": 3,
                "friday": 4,
                "saturday": 5,
                "sunday": 6,
            }
            for day_name, day_num in weekdays.items():
                if day_name in text:
                    days_ahead = day_num - now.weekday()
                    if "next" in text or days_ahead <= 0:
                        days_ahead += 7
                    result_date = now.date() + timedelta(days=days_ahead)
                    break

        # Parse relative time
        in_match = re.search(r"in\s+(\d+)\s+(hour|minute|day)s?", text)
        if in_match:
            amount = int(in_match.group(1))
            unit = in_match.group(2)
            if unit == "hour":
                result_dt = now + timedelta(hours=amount)
            elif unit == "minute":
                result_dt = now + timedelta(minutes=amount)
            elif unit == "day":
                result_dt = now + timedelta(days=amount)
            else:
                result_dt = now
            return result_dt.isoformat()

        # Parse time of day
        time_patterns = [
            (r"(\d{1,2}):(\d{2})", None),  # 15:00
            (r"(\d{1,2})\s*(am|pm)", None),  # 3pm
            (r"at\s+(\d{1,2}):(\d{2})", None),  # at 15:00
            (r"at\s+(\d{1,2})\s*(am|pm)?", None),  # at 3 or at 3pm
        ]

        for pattern, _ in time_patterns:
            match = re.search(pattern, text)
            if match:
                groups = match.groups()
                hour = int(groups[0])

                if len(groups) >= 2 and groups[1]:
                    if isinstance(groups[1], str) and groups[1] in ("am", "pm"):
                        if groups[1] == "pm" and hour != 12:
                            hour += 12
                        elif groups[1] == "am" and hour == 12:
                            hour = 0
                        minute = 0
                    else:
                        minute = int(groups[1])
                else:
                    minute = 0

                result_time = (hour, minute)
                break

        # Combine date and time
        if result_time:
            result_dt = datetime.combine(
                result_date,
                datetime.min.time().replace(
                    hour=result_time[0],
                    minute=result_time[1],
                ),
            )
        else:
            # Default to 9 AM if no time specified
            result_dt = datetime.combine(
                result_date,
                datetime.min.time().replace(hour=9, minute=0),
            )

        return result_dt.isoformat()

    # -------------------------------------------------------------------------
    # ICS Generation
    # -------------------------------------------------------------------------

    def _events_to_ics(self, events: list[CalendarEvent]) -> str:
        """Convert multiple events to a single iCalendar file."""
        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Bartholomew//Calendar Draft//EN",
        ]

        for event in events:
            # Add each event (without calendar wrapper)
            event_lines = event.to_ics().split("\r\n")
            # Skip calendar wrapper lines
            for line in event_lines:
                if line not in (
                    "BEGIN:VCALENDAR",
                    "VERSION:2.0",
                    "PRODID:-//Bartholomew//Calendar Draft//EN",
                    "END:VCALENDAR",
                ):
                    lines.append(line)

        lines.append("END:VCALENDAR")
        return "\r\n".join(lines)

    # -------------------------------------------------------------------------
    # Database operations
    # -------------------------------------------------------------------------

    def _save_event(self, event: CalendarEvent) -> None:
        """Save or update an event in the database."""
        if not self._db_path:
            return

        conn = self._get_connection()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO skill_calendar_events
                (id, title, start, end, description, location, all_day,
                    reminder_minutes, created_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.title,
                    event.start,
                    event.end,
                    event.description,
                    event.location,
                    1 if event.all_day else 0,
                    event.reminder_minutes,
                    event.created_at,
                    json.dumps(event.metadata),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _get_event(self, event_id: str) -> CalendarEvent | None:
        """Get an event by ID."""
        if not self._db_path:
            return None

        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM skill_calendar_events WHERE id = ?",
                (event_id,),
            ).fetchone()

            if not row:
                return None

            return self._row_to_event(row)
        finally:
            conn.close()

    def _get_events(
        self,
        from_date: str | None = None,
        to_date: str | None = None,
        limit: int = 50,
    ) -> list[CalendarEvent]:
        """Get events with optional date range filter."""
        if not self._db_path:
            return []

        conn = self._get_connection()
        try:
            query = "SELECT * FROM skill_calendar_events WHERE 1=1"
            params: list[Any] = []

            if from_date:
                query += " AND start >= ?"
                params.append(from_date)
            if to_date:
                query += " AND start <= ?"
                params.append(to_date)

            query += " ORDER BY start ASC"
            query += f" LIMIT {limit}"

            rows = conn.execute(query, params).fetchall()
            return [self._row_to_event(row) for row in rows]
        finally:
            conn.close()

    def _delete_event(self, event_id: str) -> None:
        """Delete an event from the database."""
        if not self._db_path:
            return

        conn = self._get_connection()
        try:
            conn.execute(
                "DELETE FROM skill_calendar_events WHERE id = ?",
                (event_id,),
            )
            conn.commit()
        finally:
            conn.close()

    def _row_to_event(self, row: sqlite3.Row) -> CalendarEvent:
        """Convert a database row to a CalendarEvent."""
        return CalendarEvent(
            id=row["id"],
            title=row["title"],
            start=row["start"],
            end=row["end"],
            description=row["description"],
            location=row["location"],
            all_day=bool(row["all_day"]),
            reminder_minutes=row["reminder_minutes"],
            created_at=row["created_at"],
            metadata=json.loads(row["metadata_json"]),
        )

    # -------------------------------------------------------------------------
    # Event handling
    # -------------------------------------------------------------------------

    async def handle_event(self, event: Any) -> None:
        """Handle GlobalWorkspace events."""
        if event.channel == "tasks":
            if event.event_type == "task_created":
                # Auto-create calendar block for tasks with due dates
                payload = event.payload or {}
                due_date = payload.get("due_date")
                if due_date:
                    await self._action_create(
                        {
                            "title": f"Task: {payload.get('title', 'Untitled')}",
                            "start": due_date,
                            "description": payload.get("description", ""),
                            "reminder_minutes": 30,
                        },
                    )

    # -------------------------------------------------------------------------
    # Status
    # -------------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """Get skill status with event count."""
        status = super().get_status()

        if self._db_path:
            events = self._get_events()
            upcoming = [e for e in events if e.start >= datetime.utcnow().isoformat()]
            status["event_counts"] = {
                "total": len(events),
                "upcoming": len(upcoming),
            }

        return status
