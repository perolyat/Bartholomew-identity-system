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


def _row_counts(db_path):
    import sqlite3

    from bartholomew.kernel.reflection import REFLECTION_KIND

    conn = sqlite3.connect(db_path)
    try:
        counts = {
            "initiatives": conn.execute("SELECT COUNT(*) FROM initiatives").fetchone()[0],
            "initiative_audit": conn.execute(
                "SELECT COUNT(*) FROM initiative_audit",
            ).fetchone()[0],
        }
        reflections_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'reflections'",
        ).fetchone()
        counts["reflections"] = (
            conn.execute(
                "SELECT COUNT(*) FROM reflections WHERE kind = ?",
                (REFLECTION_KIND,),
            ).fetchone()[0]
            if reflections_exists
            else 0
        )
        dry_run_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'dry_run_results'",
        ).fetchone()
        counts["dry_run_results"] = (
            conn.execute("SELECT COUNT(*) FROM dry_run_results").fetchone()[0]
            if dry_run_exists
            else 0
        )
        return counts
    finally:
        conn.close()


@pytest.mark.asyncio
class TestS55DryRun:
    """Stage 5, S5.5: dry-run mode. See docs/S5_5_DRY_RUN_MODE_DESIGN.md.
    `daemon`'s own `mem` is a duck-typed stub (no `insert_reflection`), so
    a *real* Reflection write for a live call already logs a caught,
    benign "Failed to record action reflection" error in these fixtures
    (pre-existing, unrelated to S5.5) -- what these tests assert is row
    counts and control flow, not whether that specific write succeeds."""

    async def test_dry_run_propose_writes_no_real_row(self, daemon):
        daemon = await _ready(daemon, consent_categories=["check_in"])
        before = _row_counts(daemon.mem.db_path)

        result = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
            dry_run=True,
        )

        assert result.outcome == "dry_run_approved"
        assert result.governance_allowed is True
        assert result.initiative is None
        assert result.dry_run_result is not None
        assert result.dry_run_result.would_execute is True

        after = _row_counts(daemon.mem.db_path)
        assert after["initiatives"] == before["initiatives"]
        assert after["initiative_audit"] == before["initiative_audit"]
        assert after["reflections"] == before["reflections"]
        assert after["dry_run_results"] == before["dry_run_results"] + 1

    async def test_dry_run_propose_denied_still_produces_truthful_result(self, daemon):
        """No consent granted -- Governance would deny this for real, and
        the dry run must report that truthfully, not a rosy hypothetical."""
        daemon = await _ready(daemon)  # no consent_categories granted
        before = _row_counts(daemon.mem.db_path)

        result = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
            dry_run=True,
        )

        assert result.outcome == "dry_run_denied"
        assert result.governance_allowed is False
        assert result.dry_run_result.would_execute is False
        assert result.dry_run_result.governance_decision == "denied"

        after = _row_counts(daemon.mem.db_path)
        assert after["initiatives"] == before["initiatives"]
        assert after["initiative_audit"] == before["initiative_audit"]

    async def test_dry_run_propose_approval_requirements_reflects_all_gates_when_allowed(
        self,
        daemon,
    ):
        """S5.5 correction: approval_requirements must carry each gate's
        own real verdict, not a coarse summary -- verified here for the
        fully-allowed case (Identity Policy explicitly wired so gate 2
        genuinely evaluates, not just skipped for lack of a context)."""
        daemon = await _ready(daemon, consent_categories=["check_in"])
        daemon.identity_context = ALLOW_CONTEXT

        result = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
            dry_run=True,
        )

        req = result.dry_run_result.approval_requirements
        assert req["parking_brake"] == {"scope": "initiative", "checked": True, "blocked": False}
        assert req["identity_policy"]["checked"] is True
        assert req["identity_policy"]["allowed"] is True
        assert req["consent"]["checked"] is True
        assert req["consent"]["consented"] is True
        assert req["consent"]["category"] == "check_in"

    async def test_dry_run_propose_approval_requirements_reflects_parking_brake_denial(
        self,
        daemon,
    ):
        daemon = await _ready(daemon, consent_categories=["check_in"])
        from bartholomew.orchestrator.safety.governance_store import GovernanceStore

        daemon.governance_store = GovernanceStore(daemon.mem.db_path)
        daemon.governance_store.engage("initiative", reason="test")

        result = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
            dry_run=True,
        )

        assert result.dry_run_result.would_execute is False
        req = result.dry_run_result.approval_requirements
        assert req["parking_brake"]["checked"] is True
        assert req["parking_brake"]["blocked"] is True
        # Gates 2/3 never ran -- governance_allowed was already False.
        assert req["identity_policy"]["checked"] is False
        assert req["consent"]["checked"] is False

    async def test_dry_run_propose_approval_requirements_reflects_identity_policy_denial(
        self,
        daemon,
    ):
        daemon = await _ready(daemon, consent_categories=["check_in"])
        daemon.identity_context = DENY_CONTEXT

        result = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
            dry_run=True,
        )

        assert result.dry_run_result.would_execute is False
        req = result.dry_run_result.approval_requirements
        assert req["parking_brake"]["checked"] is True
        assert req["parking_brake"]["blocked"] is False
        assert req["identity_policy"]["checked"] is True
        assert req["identity_policy"]["allowed"] is False
        assert req["identity_policy"]["reason"] is not None
        # Gate 3 never ran -- gate 2 already denied.
        assert req["consent"]["checked"] is False

    async def test_dry_run_propose_approval_requirements_reflects_consent_denial(
        self,
        daemon,
    ):
        daemon = await _ready(daemon)  # no consent granted
        daemon.identity_context = ALLOW_CONTEXT

        result = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
            dry_run=True,
        )

        assert result.dry_run_result.would_execute is False
        req = result.dry_run_result.approval_requirements
        assert req["identity_policy"]["checked"] is True
        assert req["identity_policy"]["allowed"] is True
        assert req["consent"]["checked"] is True
        assert req["consent"]["consented"] is False

    async def test_dry_run_deliver_approval_requirements_includes_category_mute(self, daemon):
        """Category mute (S5.3) is informational-only evidence in a
        deliver dry run's approval_requirements -- it never overrides
        governance_allowed, since mute is evaluated by the delivery-check
        drive, not this seam's own three gates."""
        daemon = await _ready(daemon, consent_categories=["check_in"])
        daemon.initiative_store.set_category_consent("check_in", allowed=True, muted=True)
        proposed = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
        )

        result = await run_initiative_through_runtime_contract(
            daemon,
            "deliver",
            initiative_id=proposed.initiative.id,
            dry_run=True,
        )

        req = result.dry_run_result.approval_requirements
        assert req["category_mute"] == {"category": "check_in", "muted": True}
        # Mute is informational -- Governance still says "allowed" (mute
        # is not one of this seam's own three gates).
        assert result.dry_run_result.would_execute is True

    async def test_dry_run_deliver_leaves_initiative_unchanged(self, daemon):
        daemon = await _ready(daemon, consent_categories=["check_in"])
        proposed = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
        )
        before = _row_counts(daemon.mem.db_path)

        result = await run_initiative_through_runtime_contract(
            daemon,
            "deliver",
            initiative_id=proposed.initiative.id,
            dry_run=True,
        )

        assert result.outcome == "dry_run_approved"
        assert result.initiative.id == proposed.initiative.id
        assert result.initiative.status == "approved"  # unchanged, not "delivered"
        assert result.dry_run_result.expected_effects["would_transition"] == (
            "approved -> delivered"
        )

        after = _row_counts(daemon.mem.db_path)
        assert after["initiative_audit"] == before["initiative_audit"]
        fetched = daemon.initiative_store.get(proposed.initiative.id)
        assert fetched.status == "approved"
        assert fetched.delivered_at is None

    async def test_dry_run_deliver_no_working_memory_note(self, daemon):
        daemon = await _ready(daemon, consent_categories=["check_in"])
        proposed = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
        )
        before = len(daemon.working_memory.get_by_source("initiative"))

        await run_initiative_through_runtime_contract(
            daemon,
            "deliver",
            initiative_id=proposed.initiative.id,
            dry_run=True,
        )

        after = len(daemon.working_memory.get_by_source("initiative"))
        assert after == before  # no note recorded for a simulated delivery

    async def test_dry_run_deliver_denied_reports_truthfully(self, daemon):
        daemon = await _ready(daemon, consent_categories=["check_in"])
        proposed = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
        )
        import sqlite3

        conn = sqlite3.connect(daemon.mem.db_path)
        conn.execute("UPDATE initiative_consent SET allowed = 0 WHERE category = 'check_in'")
        conn.commit()
        conn.close()

        result = await run_initiative_through_runtime_contract(
            daemon,
            "deliver",
            initiative_id=proposed.initiative.id,
            dry_run=True,
        )
        assert result.outcome == "dry_run_denied"
        assert result.dry_run_result.would_execute is False
        fetched = daemon.initiative_store.get(proposed.initiative.id)
        assert fetched.status == "approved"  # unchanged

    async def test_global_switch_engage_is_observed_by_a_subsequent_runtime_call(self, daemon):
        daemon = await _ready(daemon, consent_categories=["check_in"])
        from bartholomew.orchestrator.safety.governance_store import GovernanceStore

        daemon.governance_store = GovernanceStore(daemon.mem.db_path)
        daemon.governance_store.engage_dry_run("initiative", reason="test")

        result = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
            # no dry_run kwarg -- the global switch alone must force it
        )
        assert result.outcome == "dry_run_approved"
        assert result.initiative is None

    async def test_global_switch_disengage_is_observed_by_a_subsequent_runtime_call(self, daemon):
        daemon = await _ready(daemon, consent_categories=["check_in"])
        from bartholomew.orchestrator.safety.governance_store import GovernanceStore

        daemon.governance_store = GovernanceStore(daemon.mem.db_path)
        daemon.governance_store.engage_dry_run("initiative", reason="test")

        simulated = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(kind="checkin.a"),
        )
        assert simulated.outcome == "dry_run_approved"

        daemon.governance_store.disengage_dry_run(reason="lifted")
        live = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(kind="checkin.b"),
        )
        assert live.outcome == "approved"
        assert live.initiative is not None

    async def test_caller_dry_run_false_cannot_override_engaged_global_switch(self, daemon):
        daemon = await _ready(daemon, consent_categories=["check_in"])
        from bartholomew.orchestrator.safety.governance_store import GovernanceStore

        daemon.governance_store = GovernanceStore(daemon.mem.db_path)
        daemon.governance_store.engage_dry_run("initiative", reason="test")

        result = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
            dry_run=False,  # explicit -- must not weaken the engaged switch
        )
        assert result.outcome == "dry_run_approved"
        assert result.initiative is None

    async def test_stale_disengage_cannot_undo_a_newer_restrictive_state(self, daemon):
        """The stale-write protection observed at the runtime-call level,
        not just inside GovernanceStore's own unit tests: a disengage()
        based on a stale revision must not let a subsequent live-intent
        call slip through simulation."""
        daemon = await _ready(daemon, consent_categories=["check_in"])
        from bartholomew.orchestrator.safety.governance_store import (
            GovernanceStore,
            StaleGovernanceWriteError,
        )

        writer = GovernanceStore(daemon.mem.db_path)
        writer.engage_dry_run("initiative")  # revision 1

        stale_reader = GovernanceStore(daemon.mem.db_path)  # cached at revision 1
        writer.engage_dry_run("initiative", "skills")  # revision 2, still restrictive

        with pytest.raises(StaleGovernanceWriteError):
            stale_reader.disengage_dry_run(reason="stale attempt")

        daemon.governance_store = writer
        daemon.governance_store.refresh_dry_run()
        result = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
        )
        assert result.outcome == "dry_run_approved"  # still engaged -- rejected disengage held

    async def test_fail_closed_denies_live_propose_when_switch_resolution_errors(
        self,
        daemon,
        monkeypatch,
    ):
        """S5.5 correction: a dry-run-state *resolution* failure is an
        infrastructure/safety-resolution failure, not a Governance
        verdict. It must be represented as such (`outcome == "error"`,
        the seam's own existing vocabulary for "failed before any verdict
        was reached") -- not as `"denied"`, which would read exactly like
        a legitimate Governance denial to anything inspecting the result
        later, when Governance was never actually consulted."""
        daemon = await _ready(daemon, consent_categories=["check_in"])

        import bartholomew.orchestrator.safety.governance_store as gs_module

        async def boom(*args, **kwargs):
            raise RuntimeError("simulated storage failure")

        monkeypatch.setattr(gs_module, "is_dry_run_engaged_fail_closed_off_loop", boom)

        result = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
            dry_run=False,
        )
        assert result.governance_allowed is False
        assert result.outcome == "error"
        assert result.reason == "Dry-run state check errored"
        assert result.dry_run_result is None
        assert result.initiative is None

    async def test_resolution_failure_writes_zero_initiative_or_audit_rows(
        self,
        daemon,
        monkeypatch,
    ):
        """Regression guard for the correction above: no real Initiative
        row -- denied or otherwise -- and no `initiative_audit` row are
        ever written for a resolution-failure propose. Ground truth must
        stay completely untouched by an infrastructure failure that never
        reached a real Governance verdict."""
        daemon = await _ready(daemon, consent_categories=["check_in"])

        import bartholomew.orchestrator.safety.governance_store as gs_module

        async def boom(*args, **kwargs):
            raise RuntimeError("simulated storage failure")

        monkeypatch.setattr(gs_module, "is_dry_run_engaged_fail_closed_off_loop", boom)

        before = _row_counts(daemon.mem.db_path)
        await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
            dry_run=False,
        )
        after = _row_counts(daemon.mem.db_path)
        assert after["initiatives"] == before["initiatives"]
        assert after["initiative_audit"] == before["initiative_audit"]
        assert after["dry_run_results"] == before["dry_run_results"]
        assert after["reflections"] == before["reflections"]

    async def test_resolution_failure_never_calls_record_action_reflection(
        self,
        daemon,
        monkeypatch,
    ):
        """Direct proof that the infrastructure-resolution-failure path never
        reaches the real unified Reflection sink, for `propose`.

        Row-count assertions alone cannot prove this: this fixture's live-call
        Reflection writes already fail silently for reasons unrelated to
        S5.5 (see class docstring), so an unchanged `reflections` count would
        look identical whether `_record_initiative_reflection()` was
        correctly skipped or merely attempted-and-failed-to-persist. Spying
        directly on `record_action_reflection` sidesteps that masking --
        this test fails against the pre-correction code (which still called
        it on this path via `if dry_result is None:` alone, since
        `dry_result` is also `None` for a resolution failure) and passes once
        that condition is also guarded by `not dry_run_resolution_failed`."""
        daemon = await _ready(daemon, consent_categories=["check_in"])

        import bartholomew.kernel.runtime_contract as rc_module
        import bartholomew.orchestrator.safety.governance_store as gs_module

        async def boom(*args, **kwargs):
            raise RuntimeError("simulated storage failure")

        monkeypatch.setattr(gs_module, "is_dry_run_engaged_fail_closed_off_loop", boom)

        reflection_calls = []

        async def spy_record_action_reflection(*args, **kwargs):
            reflection_calls.append((args, kwargs))

        monkeypatch.setattr(rc_module, "record_action_reflection", spy_record_action_reflection)

        result = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
            dry_run=False,
        )
        assert result.outcome == "error"
        assert reflection_calls == []

    async def test_resolution_failure_on_deliver_leaves_no_ground_truth_whatsoever(
        self,
        daemon,
        monkeypatch,
    ):
        """Comprehensive companion proof for `deliver`: a resolution failure
        must never let a real notification go out, write a real Reflection,
        add a Working Memory note, or change the initiative's status -- even
        though `deliver`'s own dispatch (unlike `propose`) already requires
        `governance_allowed`, so most of this confirms existing safety, not
        just the propose-specific correction. Proves, together, every
        invariant the resolution-failure path must uphold:
        no Initiative mutation, no initiative_audit row, no DryRunResult, no
        real Reflection (spied directly -- see the propose-side test above
        for why row counts alone can't prove this), no Working Memory note
        (a genuine in-process check, not masked by any fixture quirk), no
        external/capability side effect, and a fail-closed
        outcome="error"/reason="Dry-run state check errored" return."""
        daemon = await _ready(daemon, consent_categories=["check_in"])
        proposed = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
        )
        assert proposed.initiative.status == "approved"

        sent = []

        class SpySkillRegistry:
            async def execute_action(self, skill_id, action, params=None, dry_run=False):
                sent.append((skill_id, action))
                from bartholomew.kernel.skill_base import SkillResult

                return SkillResult.ok()

        daemon.skill_registry = SpySkillRegistry()

        import bartholomew.kernel.runtime_contract as rc_module
        import bartholomew.orchestrator.safety.governance_store as gs_module

        async def boom(*args, **kwargs):
            raise RuntimeError("simulated storage failure")

        monkeypatch.setattr(gs_module, "is_dry_run_engaged_fail_closed_off_loop", boom)

        reflection_calls = []

        async def spy_record_action_reflection(*args, **kwargs):
            reflection_calls.append((args, kwargs))

        monkeypatch.setattr(rc_module, "record_action_reflection", spy_record_action_reflection)

        before = _row_counts(daemon.mem.db_path)
        wm_before = len(daemon.working_memory.get_by_source("initiative"))
        result = await run_initiative_through_runtime_contract(
            daemon,
            "deliver",
            initiative_id=proposed.initiative.id,
            dry_run=False,
        )
        assert result.governance_allowed is False
        assert result.outcome == "error"
        assert result.reason == "Dry-run state check errored"
        assert result.dry_run_result is None
        assert sent == []  # notify.send never called -- no capability/external side effect
        assert reflection_calls == []  # no real Reflection
        wm_after = len(daemon.working_memory.get_by_source("initiative"))
        assert wm_after == wm_before  # no Working Memory action note
        after = _row_counts(daemon.mem.db_path)
        assert after["initiative_audit"] == before["initiative_audit"]
        assert after["dry_run_results"] == before["dry_run_results"]
        fetched = daemon.initiative_store.get(proposed.initiative.id)
        assert fetched.status == "approved"  # unchanged -- no Initiative mutation
        assert fetched.delivered_at is None

    async def test_caller_dry_run_true_still_simulates_despite_resolution_error(
        self,
        daemon,
        monkeypatch,
    ):
        daemon = await _ready(daemon, consent_categories=["check_in"])

        import bartholomew.orchestrator.safety.governance_store as gs_module

        async def boom(*args, **kwargs):
            raise RuntimeError("simulated storage failure")

        monkeypatch.setattr(gs_module, "is_dry_run_engaged_fail_closed_off_loop", boom)

        before = _row_counts(daemon.mem.db_path)
        result = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
            dry_run=True,  # caller already committed to simulation
        )
        assert result.outcome == "dry_run_approved"
        assert result.initiative is None
        after = _row_counts(daemon.mem.db_path)
        assert after["initiatives"] == before["initiatives"]  # no real row either way

    async def test_default_dry_run_false_is_unchanged_from_pre_s5_5_behaviour(self, daemon):
        """Regression guard: omitting `dry_run` entirely must behave
        identically to every S5.1-S5.4 test written before this param
        existed."""
        daemon = await _ready(daemon, consent_categories=["check_in"])
        result = await run_initiative_through_runtime_contract(
            daemon,
            "propose",
            **_propose_kwargs(),
        )
        assert result.outcome == "approved"
        assert result.dry_run_result is None
        assert result.initiative is not None
