"""
Bartholomew Skills Package
==========================

Contains skill implementations for Stage 4 starter skills.

Skills:
- tasks: Task management with reminders
- notify: Notification and alert management
- calendar_draft: Draft calendar events with .ics export
"""

from __future__ import annotations


__all__ = [
    "TasksSkill",
    "NotifySkill",
    "CalendarDraftSkill",
]


# Lazy imports to avoid circular dependencies
def __getattr__(name: str):
    if name == "TasksSkill":
        from .tasks import TasksSkill  # noqa: PLC0415

        return TasksSkill
    elif name == "NotifySkill":
        from .notify import NotifySkill  # noqa: PLC0415

        return NotifySkill
    elif name == "CalendarDraftSkill":
        from .calendar_draft import CalendarDraftSkill  # noqa: PLC0415

        return CalendarDraftSkill
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
