"""
Stage 5, S5.4: pluggable notification suppression-policy registry.

See docs/S5_4_QUIET_HOURS_DEFER_DESIGN.md Sec 1. `drive_initiative_delivery_
check` (bartholomew.kernel.scheduler.drives) previously only asked "is the
category muted" (S5.3). This module answers the separate, timing-oriented
question "is *some* suppression window currently active" -- quiet hours
today, Driving Mode/Focus Mode or similar in the future -- without the
drive needing to know how any individual policy determines that.

`SUPPRESSION_POLICIES` is the entire extension surface: adding a new
suppression source later means adding one `(name, check_fn)` entry here,
nothing else. `active_suppression_reason()` returns the first active
policy's name (checked in list order) or `None`.

Deliberately NOT the same question as S5.3's per-category mute
(`InitiativeStore.is_category_muted()`): mute is a deliberate, category-
scoped opt-out the user set explicitly; the policies here are timing/
context suppression that applies uniformly regardless of category. Both
stay in force independently, exactly as S5.3's design doc established for
category-mute vs. NotifySkill's own global mute.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from bartholomew.kernel.runtime_contract import run_skill_through_runtime_contract

SuppressionCheck = Callable[[Any, dict[str, Any]], Awaitable[bool]]


async def _get_notify_settings(ctx: Any, cache: dict[str, Any]) -> dict[str, Any]:
    """Fetches NotifySkill's quiet-hours/mute settings at most once per
    drive tick -- `cache` is a plain dict the caller creates once per tick
    and threads through every `active_suppression_reason()` call in that
    tick's loop, so N due initiatives cost one skill call, not N. Absent
    `ctx.skill_registry` (e.g. test contexts without one wired) fails open
    -- an empty settings dict, which both checks below read as "not
    active" -- matching the pre-S5.4 behavior of simply skipping the
    notify call entirely when no registry is present, rather than
    deferring everything forever."""
    if "settings" not in cache:
        skill_registry = getattr(ctx, "skill_registry", None)
        if skill_registry is None:
            cache["settings"] = {}
        else:
            result = await run_skill_through_runtime_contract(
                skill_registry,
                "notify",
                "get_notification_settings",
                {},
            )
            cache["settings"] = result.data or {} if result.success else {}
    return cache["settings"]


async def _check_quiet_hours(ctx: Any, cache: dict[str, Any]) -> bool:
    settings = await _get_notify_settings(ctx, cache)
    return bool(settings.get("quiet_hours", {}).get("is_active"))


async def _check_notify_muted(ctx: Any, cache: dict[str, Any]) -> bool:
    """NotifySkill's own manual, global mute (S1.3, `/api/notifications/
    mute`) -- distinct from quiet hours and from S5.3's per-category mute.
    Folded into this registry (not just quiet hours) because it produces
    the identical symptom the design doc's queued-notification
    inconsistency named: `NotifySkill.send()` silently queues instead of
    delivering, which `deliver()` marking `status="delivered"` beforehand
    would otherwise misrepresent."""
    settings = await _get_notify_settings(ctx, cache)
    return bool(settings.get("effective_muted"))


# The entire extension surface for future suppression sources (Driving
# Mode, Focus Mode, ...): append a (name, check_fn) pair here. The drive
# loop, the seam, and the schema need no changes to support a new one.
SUPPRESSION_POLICIES: list[tuple[str, SuppressionCheck]] = [
    ("quiet_hours", _check_quiet_hours),
    ("notify_muted", _check_notify_muted),
]


async def active_suppression_reason(ctx: Any, cache: dict[str, Any]) -> str | None:
    """Returns the first active policy's name (list order = precedence),
    or None if nothing in the registry is currently suppressing delivery.
    `cache` should be a single dict created once per drive tick and reused
    across every initiative checked in that tick (see
    `_get_notify_settings`'s docstring)."""
    for name, check in SUPPRESSION_POLICIES:
        if await check(ctx, cache):
            return name
    return None
