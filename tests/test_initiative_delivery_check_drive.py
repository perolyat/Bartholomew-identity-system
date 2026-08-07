"""
Tests for bartholomew.kernel.scheduler.drives.drive_initiative_delivery_check
(Stage 5, S5.3/S5.4). See docs/S5_3_DEFAULT_OFF_CONSENT_AND_MUTE_DESIGN.md
Sec 4/10 and docs/S5_4_QUIET_HOURS_DEFER_DESIGN.md: no real proposing drive
exists yet, so these tests seed synthetic `initiatives` rows directly, same
honest scope note as test_initiative_sweep_drive.py's own tests.
"""

from __future__ import annotations

import pytest

from bartholomew.kernel.initiative_store import InitiativeStore
from bartholomew.kernel.scheduler.drives import REGISTRY, drive_initiative_delivery_check
from bartholomew.kernel.skill_base import SkillResult, SkillResultStatus

FAR_FUTURE = "2099-01-01T00:00:00Z"
FAR_PAST = "2000-01-01T00:00:00Z"


class FakeMem:
    def __init__(self, db_path):
        self.db_path = db_path


class FakeSkillRegistry:
    """Answers notify.get_notification_settings with a caller-supplied
    canned dict and records every notify.send call, so S5.4 tests can
    assert on suppression-policy dispatch and coalescing without a real
    NotifySkill instance."""

    def __init__(self, notify_settings: dict | None = None):
        self.notify_settings = (
            notify_settings
            if notify_settings is not None
            else {"quiet_hours": {"is_active": False}, "effective_muted": False}
        )
        self.send_calls: list[dict] = []
        self.settings_call_count = 0

    async def execute_action(self, skill_id, action, params=None):
        if skill_id == "notify" and action == "get_notification_settings":
            self.settings_call_count += 1
            return SkillResult(status=SkillResultStatus.SUCCESS, data=self.notify_settings)
        if skill_id == "notify" and action == "send":
            self.send_calls.append(params or {})
            return SkillResult(status=SkillResultStatus.SUCCESS, data={})
        return SkillResult(status=SkillResultStatus.ERROR, error="unexpected call")


class DuckTypedCtx:
    """Minimal context matching drive_initiative_delivery_check's own
    documented contract -- no KernelDaemon required."""

    def __init__(self, db_path, skill_registry=None):
        self.mem = FakeMem(db_path)
        self.initiative_store = InitiativeStore(db_path)
        self.governance_store = None
        self.blocking_executor = None
        self.identity_context = None
        self.skill_registry = skill_registry
        self.working_memory = None


def _seed(
    store,
    *,
    kind,
    category="maintenance",
    due_at=None,
    governance_decision="allowed",
    delivery_policy="standard",
):
    return store.propose(
        kind=kind,
        category=category,
        confidence=0.5,
        rationale=f"test rationale for {kind}",
        origin_drive="test",
        due_at=due_at,
        expires_at=FAR_FUTURE,
        governance_decision=governance_decision,
        delivery_policy=delivery_policy,
    )


@pytest.fixture
def ctx(tmp_path):
    return DuckTypedCtx(str(tmp_path / "test.db"))


@pytest.fixture
def ctx_with_registry(tmp_path):
    """Same as `ctx`, but with a FakeSkillRegistry wired -- needed for
    every S5.4 test that must observe notify.send calls or control
    NotifySkill's reported quiet-hours/mute settings."""

    def _make(notify_settings: dict | None = None):
        return DuckTypedCtx(
            str(tmp_path / "test.db"),
            skill_registry=FakeSkillRegistry(notify_settings),
        )

    return _make


async def test_consented_and_unmuted_initiative_gets_delivered(ctx):
    store = ctx.initiative_store
    store.set_category_consent("maintenance", allowed=True)
    initiative = _seed(store, kind="a")

    result = await drive_initiative_delivery_check(ctx)

    assert result is None  # no user-facing Nudge of its own
    delivered = store.get(initiative.id)
    assert delivered.status == "delivered"
    assert delivered.delivered_at is not None


async def test_muted_category_defers_instead_of_delivering(ctx):
    store = ctx.initiative_store
    store.set_category_consent("wellness", allowed=True, muted=True)
    initiative = _seed(store, kind="a", category="wellness")

    await drive_initiative_delivery_check(ctx)

    deferred = store.get(initiative.id)
    assert deferred.status == "deferred"
    assert deferred.deferred_reason == "muted"


async def test_revoked_consent_cancels_rather_than_defers(ctx):
    """S5.3 design doc Sec 8 open question 1 (approved): revoked consent is
    a stronger signal than a mute -- cancel, not defer."""
    store = ctx.initiative_store
    store.set_category_consent("review", allowed=True)
    initiative = _seed(store, kind="a", category="review")
    store.set_category_consent("review", allowed=False)  # revoked after approval

    await drive_initiative_delivery_check(ctx)

    cancelled = store.get(initiative.id)
    assert cancelled.status == "cancelled"


async def test_revoked_consent_cancel_is_not_itself_blocked_by_consent_gate(ctx):
    """Regression case for the bug found while building this drive: `cancel`
    unexempted would be denied by the very consent gate that motivated it,
    permanently stranding the initiative in `approved`. Covered here at the
    drive level in addition to the seam-level test in
    test_runtime_contract_initiative.py."""
    store = ctx.initiative_store
    store.set_category_consent("next_best_action", allowed=True)
    initiative = _seed(store, kind="a", category="next_best_action")
    store.set_category_consent("next_best_action", allowed=False)

    await drive_initiative_delivery_check(ctx)

    result = store.get(initiative.id)
    assert result.status != "approved"  # must have moved, not stuck
    assert result.status == "cancelled"


async def test_not_yet_due_initiative_is_left_untouched(ctx):
    store = ctx.initiative_store
    store.set_category_consent("maintenance", allowed=True)
    initiative = _seed(store, kind="a", due_at="2099-06-01T00:00:00Z")

    await drive_initiative_delivery_check(ctx)

    assert store.get(initiative.id).status == "approved"


async def test_is_a_no_op_when_no_store_is_wired():
    class BareCtx:
        pass

    result = await drive_initiative_delivery_check(BareCtx())
    assert result is None


async def test_is_a_no_op_when_nothing_is_due(ctx):
    store = ctx.initiative_store
    store.set_category_consent("maintenance", allowed=True)
    initiative = _seed(store, kind="a", due_at="2099-06-01T00:00:00Z")

    await drive_initiative_delivery_check(ctx)

    assert store.get(initiative.id).status == "approved"


async def test_per_entry_failure_does_not_block_the_rest(ctx, monkeypatch):
    store = ctx.initiative_store
    store.set_category_consent("maintenance", allowed=True)
    poison = _seed(store, kind="poison")
    healthy_a = _seed(store, kind="a")
    healthy_b = _seed(store, kind="b")

    import bartholomew.kernel.runtime_contract as rc_module

    real_fn = rc_module.run_initiative_through_runtime_contract

    async def flaky(ctx_arg, transition, *, initiative_id=None, **kwargs):
        if initiative_id == poison.id:
            raise RuntimeError("simulated failure for poison row")
        return await real_fn(ctx_arg, transition, initiative_id=initiative_id, **kwargs)

    monkeypatch.setattr(rc_module, "run_initiative_through_runtime_contract", flaky)

    result = await drive_initiative_delivery_check(ctx)

    assert result is None
    assert store.get(poison.id).status == "approved"  # unchanged -- failure didn't corrupt it
    assert store.get(healthy_a.id).status == "delivered"
    assert store.get(healthy_b.id).status == "delivered"


async def test_deferred_and_snoozed_initiatives_are_reconsidered(ctx):
    """Closes the deferred/snoozed -> approved re-entry loop S5.1 Sec 5
    described without a dedicated transition: deliver() already accepts
    deferred/snoozed as pre-states, so once consent/mute conditions
    change, this drive can call deliver directly."""
    store = ctx.initiative_store
    store.set_category_consent("maintenance", allowed=True, muted=True)
    initiative = _seed(store, kind="a")
    await drive_initiative_delivery_check(ctx)
    assert store.get(initiative.id).status == "deferred"

    # Mute lifted -- the next tick should now deliver it.
    store.set_category_consent("maintenance", muted=False)
    await drive_initiative_delivery_check(ctx)
    assert store.get(initiative.id).status == "delivered"


def test_registry_entry_shape():
    assert REGISTRY["initiative_delivery_check"]["fn"] is drive_initiative_delivery_check
    assert REGISTRY["initiative_delivery_check"]["cadence"] == "every:900"


async def test_drive_tick_is_registered_as_self_maintenance():
    """S5.3 design doc Sec 4/8 item 3: deciding whether to check delivery
    eligibility is not itself outbound contact, so the drive's own tick is
    self-maintenance-exempt -- but the deliver/defer/cancel transitions it
    dispatches (except cancel, see below) are not."""
    from bartholomew.kernel.runtime_contract import _SELF_MAINTENANCE_DRIVES

    assert "initiative_delivery_check" in _SELF_MAINTENANCE_DRIVES


async def test_deliver_transition_stays_fully_gated(ctx):
    """Unlike expire/cancel, `deliver` has no exemption -- a category with
    no consent grant must never actually deliver, only cancel."""
    store = ctx.initiative_store
    # No consent granted at all -- default-off.
    initiative = _seed(store, kind="a")

    await drive_initiative_delivery_check(ctx)

    result = store.get(initiative.id)
    assert result.status == "cancelled"
    assert result.status != "delivered"


# =============================================================================
# Stage 5, S5.4: suppression-policy registry, delivery_policy overrides, and
# coalesced digest notifications. See docs/S5_4_QUIET_HOURS_DEFER_DESIGN.md.
# =============================================================================


async def test_quiet_hours_active_defers_a_standard_policy_initiative(ctx_with_registry):
    ctx = ctx_with_registry({"quiet_hours": {"is_active": True}, "effective_muted": False})
    store = ctx.initiative_store
    store.set_category_consent("maintenance", allowed=True)
    initiative = _seed(store, kind="a")

    await drive_initiative_delivery_check(ctx)

    deferred = store.get(initiative.id)
    assert deferred.status == "deferred"
    assert deferred.deferred_reason == "quiet_hours"
    assert ctx.skill_registry.send_calls == []


async def test_notify_muted_defers_a_standard_policy_initiative(ctx_with_registry):
    ctx = ctx_with_registry({"quiet_hours": {"is_active": False}, "effective_muted": True})
    store = ctx.initiative_store
    store.set_category_consent("maintenance", allowed=True)
    initiative = _seed(store, kind="a")

    await drive_initiative_delivery_check(ctx)

    deferred = store.get(initiative.id)
    assert deferred.status == "deferred"
    assert deferred.deferred_reason == "notify_muted"


async def test_quiet_hours_inactive_delivers_normally(ctx_with_registry):
    ctx = ctx_with_registry()  # defaults: nothing active
    store = ctx.initiative_store
    store.set_category_consent("maintenance", allowed=True)
    initiative = _seed(store, kind="a")

    await drive_initiative_delivery_check(ctx)

    delivered = store.get(initiative.id)
    assert delivered.status == "delivered"
    assert len(ctx.skill_registry.send_calls) == 1


async def test_silent_policy_delivers_during_quiet_hours_without_sound(ctx_with_registry):
    ctx = ctx_with_registry({"quiet_hours": {"is_active": True}, "effective_muted": False})
    store = ctx.initiative_store
    store.set_category_consent("maintenance", allowed=True)
    initiative = _seed(store, kind="a", delivery_policy="silent")

    await drive_initiative_delivery_check(ctx)

    delivered = store.get(initiative.id)
    assert delivered.status == "delivered"
    assert len(ctx.skill_registry.send_calls) == 1
    assert ctx.skill_registry.send_calls[0]["sound"] is False


async def test_immediate_policy_bypasses_quiet_hours_and_forces_urgent(ctx_with_registry):
    ctx = ctx_with_registry({"quiet_hours": {"is_active": True}, "effective_muted": True})
    store = ctx.initiative_store
    store.set_category_consent("maintenance", allowed=True)
    initiative = _seed(store, kind="a", delivery_policy="immediate")

    await drive_initiative_delivery_check(ctx)

    delivered = store.get(initiative.id)
    assert delivered.status == "delivered"
    assert len(ctx.skill_registry.send_calls) == 1
    assert ctx.skill_registry.send_calls[0]["priority"] == "urgent"


async def test_critical_override_bypasses_quiet_hours_and_forces_urgent(ctx_with_registry):
    ctx = ctx_with_registry({"quiet_hours": {"is_active": True}, "effective_muted": True})
    store = ctx.initiative_store
    store.set_category_consent("maintenance", allowed=True)
    initiative = _seed(store, kind="a", delivery_policy="critical_override")

    await drive_initiative_delivery_check(ctx)

    delivered = store.get(initiative.id)
    assert delivered.status == "delivered"
    assert ctx.skill_registry.send_calls[0]["priority"] == "urgent"


async def test_category_mute_still_wins_over_delivery_policy_and_quiet_hours(ctx_with_registry):
    """Consent and category-mute are absolute (S5.4 design doc Sec 2's
    invariant) -- even an `immediate`/`critical_override` initiative must
    still defer when its category is muted."""
    ctx = ctx_with_registry({"quiet_hours": {"is_active": False}, "effective_muted": False})
    store = ctx.initiative_store
    store.set_category_consent("maintenance", allowed=True, muted=True)
    initiative = _seed(store, kind="a", delivery_policy="critical_override")

    await drive_initiative_delivery_check(ctx)

    deferred = store.get(initiative.id)
    assert deferred.status == "deferred"
    assert deferred.deferred_reason == "muted"


async def test_revoked_consent_still_cancels_a_critical_override_initiative(ctx_with_registry):
    """Same invariant as above, for consent specifically."""
    ctx = ctx_with_registry()
    store = ctx.initiative_store
    store.set_category_consent("maintenance", allowed=True)
    initiative = _seed(store, kind="a", delivery_policy="critical_override")
    store.set_category_consent("maintenance", allowed=False)

    await drive_initiative_delivery_check(ctx)

    assert store.get(initiative.id).status == "cancelled"


async def test_single_eligible_standard_initiative_sends_individually(ctx_with_registry):
    """Batch of size 1 -- unchanged pre-S5.4 per-item message, not a digest."""
    ctx = ctx_with_registry()
    store = ctx.initiative_store
    store.set_category_consent("maintenance", allowed=True)
    initiative = _seed(store, kind="solo")

    await drive_initiative_delivery_check(ctx)

    assert store.get(initiative.id).status == "delivered"
    assert len(ctx.skill_registry.send_calls) == 1
    assert "solo" in ctx.skill_registry.send_calls[0]["title"]


async def test_multiple_eligible_standard_initiatives_coalesce_into_one_send(ctx_with_registry):
    ctx = ctx_with_registry()
    store = ctx.initiative_store
    store.set_category_consent("maintenance", allowed=True)
    store.set_category_consent("wellness", allowed=True)
    a = _seed(store, kind="a", category="maintenance")
    b = _seed(store, kind="b", category="wellness")
    c = _seed(store, kind="c", category="maintenance")

    await drive_initiative_delivery_check(ctx)

    for initiative in (a, b, c):
        assert store.get(initiative.id).status == "delivered"
    assert len(ctx.skill_registry.send_calls) == 1
    sent = ctx.skill_registry.send_calls[0]
    assert "3 updates" in sent["title"]
    # Grouped by category, not a flat concatenation in arrival order.
    assert "Maintenance (2)" in sent["message"]
    assert "Wellness (1)" in sent["message"]


async def test_coalesced_deliveries_record_batch_metadata_in_audit(ctx_with_registry):
    ctx = ctx_with_registry()
    store = ctx.initiative_store
    store.set_category_consent("maintenance", allowed=True)
    a = _seed(store, kind="a")
    b = _seed(store, kind="b")

    await drive_initiative_delivery_check(ctx)

    audit_a = store.list_audit(a.id)
    deliver_row = next(row for row in audit_a if row["transition"] == "deliver")
    assert deliver_row["detail"]["coalesced"] is True
    assert deliver_row["detail"]["batch_size"] == 2
    assert deliver_row["detail"]["batch_id"] is not None
    # Both batch members share the same batch_id.
    audit_b = store.list_audit(b.id)
    deliver_row_b = next(row for row in audit_b if row["transition"] == "deliver")
    assert deliver_row_b["detail"]["batch_id"] == deliver_row["detail"]["batch_id"]


async def test_immediate_and_critical_override_are_not_folded_into_coalesced_batch(
    ctx_with_registry,
):
    """`immediate`/`silent`/`critical_override` initiatives deliver on
    their own the instant they're found due -- they must never wait for
    (or get folded into) a standard-policy batch's digest."""
    ctx = ctx_with_registry()
    store = ctx.initiative_store
    store.set_category_consent("maintenance", allowed=True)
    urgent = _seed(store, kind="urgent", delivery_policy="immediate")
    standard_a = _seed(store, kind="a")
    standard_b = _seed(store, kind="b")

    await drive_initiative_delivery_check(ctx)

    for initiative in (urgent, standard_a, standard_b):
        assert store.get(initiative.id).status == "delivered"
    # One individual send for the immediate initiative, one coalesced send
    # for the two standard ones -- never a single 3-item batch.
    assert len(ctx.skill_registry.send_calls) == 2
    titles = [call["title"] for call in ctx.skill_registry.send_calls]
    assert any("2 updates" in title for title in titles)


async def test_suppression_settings_fetched_once_per_tick_not_per_initiative(ctx_with_registry):
    ctx = ctx_with_registry()
    store = ctx.initiative_store
    store.set_category_consent("maintenance", allowed=True)
    _seed(store, kind="a")
    _seed(store, kind="b")
    _seed(store, kind="c")

    await drive_initiative_delivery_check(ctx)

    assert ctx.skill_registry.settings_call_count == 1
