"""
Tests for bartholomew.kernel.scheduler.drives.drive_initiative_delivery_check
(Stage 5, S5.3). See docs/S5_3_DEFAULT_OFF_CONSENT_AND_MUTE_DESIGN.md Sec
4/10: no real proposing drive exists yet, so these tests seed synthetic
`initiatives` rows directly, same honest scope note as
test_initiative_sweep_drive.py's own tests.
"""

from __future__ import annotations

import pytest

from bartholomew.kernel.initiative_store import InitiativeStore
from bartholomew.kernel.scheduler.drives import REGISTRY, drive_initiative_delivery_check

FAR_FUTURE = "2099-01-01T00:00:00Z"
FAR_PAST = "2000-01-01T00:00:00Z"


class FakeMem:
    def __init__(self, db_path):
        self.db_path = db_path


class DuckTypedCtx:
    """Minimal context matching drive_initiative_delivery_check's own
    documented contract -- no KernelDaemon required."""

    def __init__(self, db_path):
        self.mem = FakeMem(db_path)
        self.initiative_store = InitiativeStore(db_path)
        self.governance_store = None
        self.blocking_executor = None
        self.identity_context = None
        self.skill_registry = None
        self.working_memory = None


def _seed(store, *, kind, category="maintenance", due_at=None, governance_decision="allowed"):
    return store.propose(
        kind=kind,
        category=category,
        confidence=0.5,
        rationale="test",
        origin_drive="test",
        due_at=due_at,
        expires_at=FAR_FUTURE,
        governance_decision=governance_decision,
    )


@pytest.fixture
def ctx(tmp_path):
    return DuckTypedCtx(str(tmp_path / "test.db"))


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
