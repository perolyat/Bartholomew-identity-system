"""
Tests for Stage 5 S5.1/S5.2:
bartholomew.kernel.runtime_contract.run_initiative_through_runtime_contract().
See docs/S5_1_INITIATIVE_ENGINE_ARCHITECTURE_DESIGN.md for the design this
implements, and tests/test_initiative_store.py for the isolated store-level
tests this seam builds on.
"""

from __future__ import annotations

import pytest

from bartholomew.kernel.initiative_store import (
    InitiativeNotFoundError,
    InitiativeStore,
    InvalidTransitionError,
)
from bartholomew.kernel.runtime_contract import run_initiative_through_runtime_contract
from bartholomew.kernel.skill_base import SkillResult, SkillResultStatus
from identity_interpreter.identity_context import IdentityContext

DENY_CONTEXT = IdentityContext(tool_use_default_allowed=False, tool_use_allowlist=[])
ALLOW_CONTEXT = IdentityContext(tool_use_default_allowed=True, tool_use_allowlist=[])
ALLOWLIST_CONTEXT = IdentityContext(
    tool_use_default_allowed=False,
    tool_use_allowlist=["allow_proactive.check_in"],
)

FAR_FUTURE = "2099-01-01T00:00:00Z"
FAR_PAST = "2000-01-01T00:00:00Z"


@pytest.fixture
def mock_config_files(tmp_path):
    """Mirrors test_runtime_contract_awaiting_response.py's fixture of the same name."""
    cfg_path = tmp_path / "kernel.yaml"
    cfg_path.write_text(
        """
timezone: "Australia/Brisbane"
loop_interval_seconds: 1
quiet_hours:
  start: "23:00"
  end: "06:00"
dreaming:
  nightly_window: "21:00-23:00"
  weekly:
    weekday: "Sun"
    time: "21:30"
""",
    )
    persona_path = tmp_path / "persona.yaml"
    persona_path.write_text('name: "Test Bartholomew"\n')
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text("policies: []\n")
    drives_path = tmp_path / "drives.yaml"
    drives_path.write_text("drives: []\n")
    db_path = tmp_path / "test.db"

    return {
        "cfg_path": str(cfg_path),
        "db_path": str(db_path),
        "persona_path": str(persona_path),
        "policy_path": str(policy_path),
        "drives_path": str(drives_path),
    }


@pytest.fixture
def daemon(mock_config_files):
    from bartholomew.kernel.daemon import KernelDaemon

    return KernelDaemon(**mock_config_files)


async def _ready(daemon, *, consent_categories=()):
    """Prepares a not-start()ed daemon fixture: ensures MemoryStore's schema
    (for the reflection sink) and wires an InitiativeStore the way
    KernelDaemon.start() normally would. `consent_categories` grants
    gate-3 consent for the given categories directly (no UI/API exists yet,
    S5.3), so callers can isolate gate-2 (Identity Policy) behaviour."""
    await daemon.mem.init()
    daemon.initiative_store = InitiativeStore(daemon.mem.db_path)
    import sqlite3

    conn = sqlite3.connect(daemon.mem.db_path)
    try:
        for category in consent_categories:
            conn.execute(
                "INSERT INTO initiative_consent (category, allowed, updated_at) "
                "VALUES (?, 1, '2026-01-01T00:00:00Z')",
                (category,),
            )
        conn.commit()
    finally:
        conn.close()
    return daemon


def _propose_kwargs(**overrides):
    kwargs = {
        "kind": "checkin.morning",
        "category": "check_in",
        "confidence": 0.8,
        "rationale": "Good morning check-in",
        "origin_drive": "checkin_morning",
        "expires_at": FAR_FUTURE,
    }
    kwargs.update(overrides)
    return kwargs


@pytest.mark.asyncio
class TestProposeTransition:
    async def test_propose_denied_by_default_no_consent(self, daemon):
        """Gate 3 (default-off consent) denies every category until S5.3
        ships a way to grant it -- the intended behaviour, not a bug."""
        daemon = await _ready(daemon)
        result = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
        )
        assert result.governance_allowed is False
        assert result.outcome == "denied"
        assert result.initiative is not None
        assert result.initiative.status == "denied"

    async def test_propose_allowed_once_consented_and_no_identity_context(self, daemon):
        daemon = await _ready(daemon, consent_categories=["check_in"])
        result = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
        )
        assert result.governance_allowed is True
        assert result.outcome == "approved"
        assert result.initiative.status == "approved"

    async def test_propose_denied_by_restrictive_identity_policy_even_if_consented(self, daemon):
        daemon = await _ready(daemon, consent_categories=["check_in"])
        daemon.identity_context = DENY_CONTEXT
        result = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
        )
        assert result.governance_allowed is False
        assert result.outcome == "denied"
        assert "Identity policy" in result.reason

    async def test_propose_allowed_by_permissive_policy_and_consent(self, daemon):
        daemon = await _ready(daemon, consent_categories=["check_in"])
        daemon.identity_context = ALLOW_CONTEXT
        result = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
        )
        assert result.governance_allowed is True

    async def test_propose_allowed_by_specific_allow_proactive_allowlist_entry(self, daemon):
        daemon = await _ready(daemon, consent_categories=["check_in"])
        daemon.identity_context = ALLOWLIST_CONTEXT
        result = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
        )
        assert result.governance_allowed is True
        assert result.initiative.status == "approved"

    async def test_propose_missing_required_field_raises(self, daemon):
        daemon = await _ready(daemon)
        with pytest.raises(ValueError):
            await run_initiative_through_runtime_contract(
                daemon,
                "propose",
                kind="x",
                category="check_in",
                origin_drive="d",
                expires_at=FAR_FUTURE,
                # missing confidence and rationale
            )

    async def test_propose_invalid_category_raises(self, daemon):
        daemon = await _ready(daemon, consent_categories=["check_in"])
        with pytest.raises(ValueError):
            await run_initiative_through_runtime_contract(
                daemon,
                "propose",
                **_propose_kwargs(category="not_a_real_category"),
            )


@pytest.mark.asyncio
class TestOtherTransitions:
    async def test_defer_deliver_resolve_lifecycle(self, daemon):
        daemon = await _ready(daemon, consent_categories=["check_in"])
        proposed = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
        )
        initiative_id = proposed.initiative.id

        deferred = await run_initiative_through_runtime_contract(
            daemon,
            "defer",
            initiative_id=initiative_id,
            reason="quiet_hours",
        )
        assert deferred.outcome == "deferred"

        # No skill loaded (start() was never called) -- proves delivery
        # notification is genuinely best-effort and never blocks the state
        # transition itself, mirroring awaiting_response's own remind test.
        delivered = await run_initiative_through_runtime_contract(
            daemon,
            "deliver",
            initiative_id=initiative_id,
        )
        assert delivered.outcome == "delivered"

        resolved = await run_initiative_through_runtime_contract(
            daemon,
            "resolve",
            initiative_id=initiative_id,
            resolution="accepted",
        )
        assert resolved.outcome == "accepted"

    async def test_deliver_denied_when_consent_missing(self, daemon):
        daemon = await _ready(daemon, consent_categories=["check_in"])
        proposed = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
        )
        # Revoke consent after approval -- deliver must now be denied (but
        # the initiative stays approved, not silently transitioned).
        import sqlite3

        conn = sqlite3.connect(daemon.mem.db_path)
        conn.execute("UPDATE initiative_consent SET allowed = 0 WHERE category = 'check_in'")
        conn.commit()
        conn.close()

        result = await run_initiative_through_runtime_contract(
            daemon,
            "deliver",
            initiative_id=proposed.initiative.id,
        )
        assert result.governance_allowed is False
        assert result.initiative.status == "approved"

    async def test_expire_bypasses_identity_policy_and_consent(self, daemon):
        """S5.2 design doc Sec 7 (approved 2026-08-06): an already-approved
        initiative must always be able to reach `expired`, even if
        Identity Policy or consent would otherwise deny it -- the narrow
        _SELF_MAINTENANCE_INITIATIVE_TRANSITIONS exemption."""
        daemon = await _ready(daemon, consent_categories=["check_in"])
        proposed = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(expires_at=FAR_PAST),
        )
        # Revoke consent AND set a denying identity_context after approval.
        import sqlite3

        conn = sqlite3.connect(daemon.mem.db_path)
        conn.execute("UPDATE initiative_consent SET allowed = 0 WHERE category = 'check_in'")
        conn.commit()
        conn.close()
        daemon.identity_context = DENY_CONTEXT

        result = await run_initiative_through_runtime_contract(
            daemon,
            "expire",
            initiative_id=proposed.initiative.id,
            actor="scheduler:initiative_sweep",
        )
        assert result.governance_allowed is True
        assert result.outcome == "expired"

    async def test_unknown_initiative_id_raises_not_found(self, daemon):
        daemon = await _ready(daemon, consent_categories=["check_in"])
        with pytest.raises(InitiativeNotFoundError):
            await run_initiative_through_runtime_contract(
                daemon,
                "defer",
                initiative_id=99999,
                reason="x",
            )

    async def test_invalid_pre_state_transition_raises(self, daemon):
        daemon = await _ready(daemon, consent_categories=["check_in"])
        proposed = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
        )
        # approved, not delivered -- resolve should reject
        with pytest.raises(InvalidTransitionError):
            await run_initiative_through_runtime_contract(
                daemon,
                "resolve",
                initiative_id=proposed.initiative.id,
                resolution="accepted",
            )

    async def test_missing_initiative_id_raises_value_error(self, daemon):
        daemon = await _ready(daemon)
        with pytest.raises(ValueError):
            await run_initiative_through_runtime_contract(daemon, "defer", reason="x")


@pytest.mark.asyncio
class TestParkingBrakeScope:
    async def test_engaged_initiative_scope_blocks_propose(self, daemon):
        from bartholomew.orchestrator.safety.governance_store import GovernanceStore

        daemon = await _ready(daemon, consent_categories=["check_in"])
        daemon.governance_store = GovernanceStore(daemon.mem.db_path)
        daemon.governance_store.engage("initiative", reason="test")

        result = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
        )
        assert result.governance_allowed is False
        # propose always persists a row (S5.1 design doc Sec 5) with only
        # one denied status regardless of which gate said no -- outcome
        # mirrors that persisted status; the specific gate is in `reason`.
        assert result.outcome == "denied"
        assert result.initiative.status == "denied"
        assert "parking brake" in result.reason.lower()

    async def test_engaged_initiative_scope_still_blocks_expire(self, daemon):
        """Gate 1 (ParkingBrake) applies even to the exempt `expire`
        transition -- an operator-engaged emergency stop must still hold
        (S5.1 design doc Sec 8)."""
        from bartholomew.orchestrator.safety.governance_store import GovernanceStore

        daemon = await _ready(daemon, consent_categories=["check_in"])
        proposed = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(expires_at=FAR_PAST),
        )

        daemon.governance_store = GovernanceStore(daemon.mem.db_path)
        daemon.governance_store.engage("initiative", reason="test")

        result = await run_initiative_through_runtime_contract(
            daemon,
            "expire",
            initiative_id=proposed.initiative.id,
        )
        assert result.governance_allowed is False
        assert result.outcome == "parking_brake_denied"

    async def test_engaging_scheduler_scope_does_not_block_initiative_scope(self, daemon):
        """The dedicated "initiative" scope is independent of "scheduler"
        (S5.1 design doc Sec 8 gate 1)."""
        from bartholomew.orchestrator.safety.governance_store import GovernanceStore

        daemon = await _ready(daemon, consent_categories=["check_in"])
        daemon.governance_store = GovernanceStore(daemon.mem.db_path)
        daemon.governance_store.engage("scheduler", reason="test")

        result = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
        )
        assert result.governance_allowed is True


class FakeSkillRegistry:
    """Records every notify.send call instead of actually sending, so S5.4
    tests can assert on suppress_notification/notify_overrides wiring
    without a real NotifySkill instance."""

    def __init__(self):
        self.send_calls: list[dict] = []

    async def execute_action(self, skill_id, action, params=None):
        if skill_id == "notify" and action == "send":
            self.send_calls.append(params or {})
            return SkillResult(status=SkillResultStatus.SUCCESS, data={})
        return SkillResult(status=SkillResultStatus.ERROR, error="unexpected call")


@pytest.mark.asyncio
class TestS54DeliveryPolicyAndSuppression:
    """Stage 5, S5.4: delivery_policy propagation and the suppress_
    notification/notify_overrides seam params coalescing relies on. See
    docs/S5_4_QUIET_HOURS_DEFER_DESIGN.md Sec 2/10."""

    async def test_propose_forwards_delivery_policy(self, daemon):
        daemon = await _ready(daemon, consent_categories=["check_in"])
        result = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
            delivery_policy="critical_override",
        )
        assert result.initiative.delivery_policy == "critical_override"

    async def test_propose_default_delivery_policy_is_standard(self, daemon):
        daemon = await _ready(daemon, consent_categories=["check_in"])
        result = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
        )
        assert result.initiative.delivery_policy == "standard"

    async def test_propose_invalid_delivery_policy_raises(self, daemon):
        daemon = await _ready(daemon, consent_categories=["check_in"])
        with pytest.raises(InvalidTransitionError):
            await run_initiative_through_runtime_contract(
                daemon,
                "propose",
                **_propose_kwargs(),
                delivery_policy="not_a_real_policy",
            )

    async def test_suppress_notification_skips_auto_notify(self, daemon):
        daemon = await _ready(daemon, consent_categories=["check_in"])
        daemon.skill_registry = FakeSkillRegistry()
        proposed = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
        )
        result = await run_initiative_through_runtime_contract(
            daemon,
            "deliver",
            initiative_id=proposed.initiative.id,
            suppress_notification=True,
        )
        assert result.outcome == "delivered"
        assert daemon.skill_registry.send_calls == []

    async def test_without_suppress_notification_auto_notify_still_fires(self, daemon):
        """Regression guard: suppress_notification defaults to False, so
        pre-S5.4 per-item delivery behaviour is unchanged."""
        daemon = await _ready(daemon, consent_categories=["check_in"])
        daemon.skill_registry = FakeSkillRegistry()
        proposed = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
        )
        result = await run_initiative_through_runtime_contract(
            daemon,
            "deliver",
            initiative_id=proposed.initiative.id,
        )
        assert result.outcome == "delivered"
        assert len(daemon.skill_registry.send_calls) == 1

    async def test_notify_overrides_merge_into_send_params(self, daemon):
        daemon = await _ready(daemon, consent_categories=["check_in"])
        daemon.skill_registry = FakeSkillRegistry()
        proposed = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
        )
        await run_initiative_through_runtime_contract(
            daemon,
            "deliver",
            initiative_id=proposed.initiative.id,
            notify_overrides={"priority": "urgent", "sound": False},
        )
        assert len(daemon.skill_registry.send_calls) == 1
        sent = daemon.skill_registry.send_calls[0]
        assert sent["priority"] == "urgent"
        assert sent["sound"] is False

    async def test_coalesced_metadata_recorded_in_audit_not_behaviour(self, daemon):
        daemon = await _ready(daemon, consent_categories=["check_in"])
        proposed = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
        )
        result = await run_initiative_through_runtime_contract(
            daemon,
            "deliver",
            initiative_id=proposed.initiative.id,
            coalesced=True,
            batch_id="batch-123",
            batch_size=4,
        )
        assert result.outcome == "delivered"
        audit = daemon.initiative_store.list_audit(proposed.initiative.id)
        deliver_row = next(row for row in audit if row["transition"] == "deliver")
        assert deliver_row["detail"]["coalesced"] is True
        assert deliver_row["detail"]["batch_id"] == "batch-123"
        assert deliver_row["detail"]["batch_size"] == 4
