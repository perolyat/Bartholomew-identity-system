"""
Tests for bartholomew.kernel.scheduler.drives.drive_initiative_sweep (Stage
5, S5.2). See docs/S5_2_TYPED_CADENCE_DESIGN.md Sec 6/13: this is the first
production code exercising initiative_store.py /
run_initiative_through_runtime_contract(), but only the `expire`
transition -- no real proposing drive exists yet, so these tests seed
synthetic `initiatives` rows directly rather than relying on one.
"""

from __future__ import annotations

import pytest

from bartholomew.kernel.initiative_store import InitiativeStore
from bartholomew.kernel.scheduler.drives import REGISTRY, drive_initiative_sweep

FAR_FUTURE = "2099-01-01T00:00:00Z"
FAR_PAST = "2000-01-01T00:00:00Z"


class FakeMem:
    def __init__(self, db_path):
        self.db_path = db_path


class DuckTypedCtx:
    """Minimal context matching drive_initiative_sweep's own documented
    contract -- no KernelDaemon required."""

    def __init__(self, db_path):
        self.mem = FakeMem(db_path)
        self.initiative_store = InitiativeStore(db_path)
        self.governance_store = None
        self.blocking_executor = None
        self.identity_context = None
        self.skill_registry = None
        self.working_memory = None


def _seed(store, *, kind, expires_at):
    return store.propose(
        kind=kind,
        category="maintenance",
        confidence=0.5,
        rationale="test",
        origin_drive="test",
        expires_at=expires_at,
        governance_decision="allowed",
    )


@pytest.fixture
def ctx(tmp_path):
    return DuckTypedCtx(str(tmp_path / "test.db"))


async def test_sweep_expires_only_past_expiry_rows(ctx):
    store = ctx.initiative_store
    expired_a = _seed(store, kind="a", expires_at=FAR_PAST)
    expired_b = _seed(store, kind="b", expires_at=FAR_PAST)
    still_fresh = _seed(store, kind="c", expires_at=FAR_FUTURE)

    result = await drive_initiative_sweep(ctx)

    assert result is None  # no user-facing delivery of its own
    assert store.get(expired_a.id).status == "expired"
    assert store.get(expired_b.id).status == "expired"
    assert store.get(still_fresh.id).status == "approved"


async def test_sweep_is_a_no_op_when_no_store_is_wired(tmp_path):
    class BareCtx:
        pass

    result = await drive_initiative_sweep(BareCtx())
    assert result is None


async def test_sweep_is_a_no_op_when_nothing_is_expiring(ctx):
    store = ctx.initiative_store
    fresh = _seed(store, kind="a", expires_at=FAR_FUTURE)

    await drive_initiative_sweep(ctx)

    assert store.get(fresh.id).status == "approved"


async def test_sweep_per_entry_failure_does_not_block_the_rest(ctx, monkeypatch):
    """One row failing its `expire` transition must not stop the sweep
    from expiring the others -- mirrors drive_awaiting_response_check's
    own per-entry try/except isolation."""
    store = ctx.initiative_store
    poison = _seed(store, kind="poison", expires_at=FAR_PAST)
    healthy_a = _seed(store, kind="a", expires_at=FAR_PAST)
    healthy_b = _seed(store, kind="b", expires_at=FAR_PAST)

    import bartholomew.kernel.runtime_contract as rc_module

    real_fn = rc_module.run_initiative_through_runtime_contract

    async def flaky(ctx_arg, transition, *, initiative_id=None, **kwargs):
        if initiative_id == poison.id:
            raise RuntimeError("simulated failure for poison row")
        return await real_fn(ctx_arg, transition, initiative_id=initiative_id, **kwargs)

    monkeypatch.setattr(rc_module, "run_initiative_through_runtime_contract", flaky)

    result = await drive_initiative_sweep(ctx)

    assert result is None
    assert store.get(poison.id).status == "approved"  # unchanged -- the failure didn't corrupt it
    assert store.get(healthy_a.id).status == "expired"
    assert store.get(healthy_b.id).status == "expired"


async def test_sweep_expires_even_when_category_consent_and_policy_deny(ctx):
    """S5.2 design doc Sec 7 (approved 2026-08-06) regression case: an
    already-approved initiative must always be able to reach `expired`,
    even if consent/Identity Policy would otherwise deny it."""
    from identity_interpreter.identity_context import IdentityContext

    store = ctx.initiative_store
    initiative = _seed(store, kind="a", expires_at=FAR_PAST)

    # No initiative_consent row exists for "maintenance" (default-off), and
    # a maximally restrictive identity_context is wired in -- both gates
    # would deny propose/deliver/etc, but must not block expire.
    ctx.identity_context = IdentityContext(tool_use_default_allowed=False, tool_use_allowlist=[])

    await drive_initiative_sweep(ctx)

    assert store.get(initiative.id).status == "expired"


def test_registry_entry_shape():
    assert REGISTRY["initiative_sweep"]["fn"] is drive_initiative_sweep
    assert REGISTRY["initiative_sweep"]["cadence"] == "every:900"


async def test_sweep_ignores_already_terminal_rows(ctx):
    store = ctx.initiative_store
    initiative = _seed(store, kind="a", expires_at=FAR_PAST)
    store.cancel(initiative.id)  # already terminal before the sweep runs

    # Must not raise, despite the row no longer being a valid `expire`
    # pre-state -- list_expiring() itself excludes terminal statuses.
    await drive_initiative_sweep(ctx)

    assert store.get(initiative.id).status == "cancelled"


async def test_sweep_cadence_is_registered_as_self_maintenance():
    """S5.2 design doc Sec 6: initiative_sweep never proposes new outbound
    contact, so its own drive tick is self-maintenance-exempt."""
    from bartholomew.kernel.runtime_contract import _SELF_MAINTENANCE_DRIVES

    assert "initiative_sweep" in _SELF_MAINTENANCE_DRIVES
