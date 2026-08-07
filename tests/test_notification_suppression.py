"""
Tests for bartholomew.kernel.notification_suppression (Stage 5, S5.4). See
docs/S5_4_QUIET_HOURS_DEFER_DESIGN.md Sec 1: a pluggable registry of
suppression-policy checks, extensible beyond quiet hours without touching
the drive that consumes it.
"""

from __future__ import annotations

from bartholomew.kernel.notification_suppression import (
    SUPPRESSION_POLICIES,
    active_suppression_reason,
)
from bartholomew.kernel.skill_base import SkillResult, SkillResultStatus


class FakeSkillRegistry:
    def __init__(self, notify_settings: dict | None = None):
        self.notify_settings = (
            notify_settings
            if notify_settings is not None
            else {"quiet_hours": {"is_active": False}, "effective_muted": False}
        )
        self.calls: list[tuple[str, str, dict]] = []

    async def execute_action(self, skill_id, action, params=None):
        self.calls.append((skill_id, action, params or {}))
        if skill_id == "notify" and action == "get_notification_settings":
            return SkillResult(status=SkillResultStatus.SUCCESS, data=self.notify_settings)
        return SkillResult(status=SkillResultStatus.ERROR, error="unexpected call")


class Ctx:
    def __init__(self, skill_registry=None):
        self.skill_registry = skill_registry


async def test_no_policy_active_returns_none():
    ctx = Ctx(FakeSkillRegistry())
    assert await active_suppression_reason(ctx, {}) is None


async def test_quiet_hours_active_wins_first():
    ctx = Ctx(FakeSkillRegistry({"quiet_hours": {"is_active": True}, "effective_muted": True}))
    assert await active_suppression_reason(ctx, {}) == "quiet_hours"


async def test_notify_muted_reported_when_quiet_hours_inactive():
    ctx = Ctx(FakeSkillRegistry({"quiet_hours": {"is_active": False}, "effective_muted": True}))
    assert await active_suppression_reason(ctx, {}) == "notify_muted"


async def test_registry_order_matches_declared_precedence():
    names = [name for name, _ in SUPPRESSION_POLICIES]
    assert names == ["quiet_hours", "notify_muted"]


async def test_settings_fetched_at_most_once_per_shared_cache():
    registry = FakeSkillRegistry({"quiet_hours": {"is_active": False}, "effective_muted": False})
    ctx = Ctx(registry)
    cache: dict = {}
    await active_suppression_reason(ctx, cache)
    await active_suppression_reason(ctx, cache)
    await active_suppression_reason(ctx, cache)
    assert len(registry.calls) == 1


async def test_fresh_cache_per_call_fetches_again():
    registry = FakeSkillRegistry()
    ctx = Ctx(registry)
    await active_suppression_reason(ctx, {})
    await active_suppression_reason(ctx, {})
    assert len(registry.calls) == 2


async def test_missing_skill_registry_fails_open():
    """No skill_registry (e.g. a test context that never wired one) must
    not be mistaken for "everything is suppressed forever" -- matches the
    pre-S5.4 behaviour of simply skipping the notify call when no registry
    is present."""
    ctx = Ctx(None)
    assert await active_suppression_reason(ctx, {}) is None
