"""
Tests for MASTER_PLAN.md's "P2.5 -- Runtime Convergence" item 11.17:
scheduler-drive convergence (Exit Gate questions #1-3).

Before this, scheduler drives (`scheduler/loop.py`'s `_run_drive()`) had
their own `ParkingBrake("scheduler")` check but never constructed an
`Observation`, were never modeled as a `CandidateAction`, and never
consulted the Identity Context -> Policy Decision path at all --
`COGNITIVE_RUNTIME.md`'s Exit Gate table named this as the concrete gap
behind all three "Partial" answers.

`bartholomew.kernel.runtime_contract.run_drive_through_runtime_contract()`
closes it, reusing the same Observation/Interpretation/CandidateAction shape
+ Governance stage chat already uses (items 11.3/11.6), routed through
`_run_drive()` so scheduler/loop.py's call site is unchanged.

The critical regression guard (see DECISIONS.md's "tool_use.allowlist gates
skill/capability execution, not scheduler drives" entry and
tests/test_runtime_convergence_policy.py's module docstring for the
precedent): item 11.2's *first* attempt at this exact wiring evaluated every
drive's task_id against `evaluate_tool_policy()` unconditionally, which
denied every registered drive by default in production (Identity.yaml's real
`tool_use.allowlist` is `[web_fetch, browser_action]`, never a drive
task_id) and busy-looped the asyncio event loop badly enough that `/healthz`
never answered. This time, the four registered drives
(`_SELF_MAINTENANCE_DRIVES`) stay exempt from that check -- these tests
prove the exemption holds under the *exact* restrictive `IdentityContext`
that caused the original incident, while also proving the Policy Decision
path is genuinely wired for any future non-exempt scheduler-originated
action.
"""

from __future__ import annotations

import pytest

from bartholomew.kernel.reflection import REFLECTION_KIND
from bartholomew.kernel.runtime_contract import (
    _SELF_MAINTENANCE_DRIVES,
    CandidateAction,
    Interpretation,
    Observation,
    run_drive_through_runtime_contract,
)
from bartholomew.kernel.scheduler.drives import REGISTRY as DRIVE_REGISTRY
from bartholomew.kernel.scheduler.loop import _run_drive
from bartholomew.orchestrator.safety.parking_brake import BrakeStorage, ParkingBrake
from identity_interpreter.identity_context import IdentityContext

# Identity.yaml's real tool_use section, reproduced exactly -- the shape that
# caused the original production incident when scheduler drives weren't exempt.
REAL_PROD_DENY_CONTEXT = IdentityContext(
    tool_use_default_allowed=False,
    tool_use_allowlist=["web_fetch", "browser_action"],
)
ALLOW_CONTEXT = IdentityContext(tool_use_default_allowed=True, tool_use_allowlist=[])


@pytest.fixture
def mock_config_files(tmp_path):
    """Create mock config files for daemon (mirrors test_runtime_contract_chat_seam.py)."""
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


async def _noop_drive(ctx):
    return None


async def _boom_drive(ctx):
    raise ValueError("drive crashed")


# Stage 1, S1.4 (docs/S1_4_AWAITING_RESPONSE_DESIGN.md): registered but
# deliberately NOT self-maintenance-exempt -- see
# bartholomew.kernel.scheduler.drives.drive_awaiting_response_check's
# docstring for why (it triggers genuine outbound contact, not
# kernel-internal housekeeping).
_NON_EXEMPT_REGISTERED_DRIVES = frozenset({"awaiting_response_check"})


class TestSelfMaintenanceDrivesMatchRegistry:
    def test_exempt_set_matches_registered_drives_minus_conscious_exceptions(self):
        """If a new drive is ever registered, this fails until someone
        consciously decides whether it belongs in the self-maintenance
        exemption or should be evaluated for real -- exactly the decision
        item 11.2's first attempt skipped. _NON_EXEMPT_REGISTERED_DRIVES is
        that conscious decision recorded, not an escape hatch: every entry
        in it must also be proven genuinely gated (see
        TestAwaitingResponseCheckIsGenuinelyGated below)."""
        assert _SELF_MAINTENANCE_DRIVES == (
            frozenset(DRIVE_REGISTRY.keys()) - _NON_EXEMPT_REGISTERED_DRIVES
        )
        assert _NON_EXEMPT_REGISTERED_DRIVES <= frozenset(DRIVE_REGISTRY.keys())


@pytest.mark.asyncio
class TestAwaitingResponseCheckIsGenuinelyGated:
    """awaiting_response_check is registered but not self-maintenance-exempt
    (see _NON_EXEMPT_REGISTERED_DRIVES above) -- proves it is genuinely
    evaluated by the Identity Policy check, the same way
    TestPolicyDecisionIsRealForNonExemptDrives proves it for an arbitrary
    unregistered drive kind."""

    async def test_denied_by_restrictive_policy_without_allowlisting(self, daemon):
        daemon.identity_context = IdentityContext(
            tool_use_default_allowed=False,
            tool_use_allowlist=[],
        )

        result, success = await run_drive_through_runtime_contract(
            daemon,
            "awaiting_response_check",
            _noop_drive,
            timeout=1.0,
        )

        assert (result, success) == (None, 0)

    async def test_allowed_once_allowlisted(self, daemon):
        daemon.identity_context = IdentityContext(
            tool_use_default_allowed=False,
            tool_use_allowlist=["awaiting_response_check"],
        )

        result, success = await run_drive_through_runtime_contract(
            daemon,
            "awaiting_response_check",
            _noop_drive,
            timeout=1.0,
        )

        assert success == 1

    async def test_skipped_entirely_when_no_identity_context_wired(self, daemon):
        assert daemon.identity_context is None

        result, success = await run_drive_through_runtime_contract(
            daemon,
            "awaiting_response_check",
            _noop_drive,
            timeout=1.0,
        )

        assert success == 1


@pytest.mark.asyncio
class TestRegressionGuardAgainstItem112:
    """Reproduces the exact conditions of the original production incident
    and proves every registered drive survives them."""

    @pytest.mark.parametrize("task_id", sorted(_SELF_MAINTENANCE_DRIVES))
    async def test_registered_drive_not_denied_by_real_prod_policy(self, daemon, task_id):
        daemon.identity_context = REAL_PROD_DENY_CONTEXT

        result, success = await run_drive_through_runtime_contract(
            daemon,
            task_id,
            _noop_drive,
            timeout=1.0,
        )

        assert success == 1

    async def test_skipped_entirely_when_no_identity_context_wired(self, daemon):
        assert daemon.identity_context is None

        result, success = await run_drive_through_runtime_contract(
            daemon,
            "self_check",
            _noop_drive,
            timeout=1.0,
        )

        assert success == 1


@pytest.mark.asyncio
class TestPolicyDecisionIsRealForNonExemptDrives:
    """A hypothetical future scheduler-originated action outside the known
    self-maintenance set -- e.g. a drive that acts on the user's behalf --
    is genuinely gated, proving Governance isn't just a blanket bypass."""

    async def test_unregistered_drive_kind_denied_by_restrictive_policy(self, daemon):
        assert "send_on_users_behalf" not in _SELF_MAINTENANCE_DRIVES
        daemon.identity_context = IdentityContext(
            tool_use_default_allowed=False,
            tool_use_allowlist=[],
        )

        result, success = await run_drive_through_runtime_contract(
            daemon,
            "send_on_users_behalf",
            _noop_drive,
            timeout=1.0,
        )

        assert (result, success) == (None, 0)

    async def test_unregistered_drive_kind_allowed_by_permissive_policy(self, daemon):
        daemon.identity_context = ALLOW_CONTEXT

        result, success = await run_drive_through_runtime_contract(
            daemon,
            "send_on_users_behalf",
            _noop_drive,
            timeout=1.0,
        )

        assert success == 1


@pytest.mark.asyncio
class TestParkingBrakeTakesPriorityOverPolicyDecision:
    """The pre-existing ParkingBrake("scheduler") check is unchanged: it
    still raises rather than returning a denial, and still applies even to
    self-maintenance drives that are exempt from the Policy Decision."""

    async def test_brake_raises_even_for_a_self_maintenance_drive(self, daemon):
        brake = ParkingBrake(BrakeStorage(daemon.mem.db_path))
        brake.engage("scheduler")
        try:
            with pytest.raises(RuntimeError, match="ParkingBrake: scheduler blocked"):
                await run_drive_through_runtime_contract(
                    daemon,
                    "self_check",
                    _noop_drive,
                    timeout=1.0,
                )
        finally:
            brake.disengage()

    async def test_run_drive_from_loop_still_raises_on_brake(self, daemon):
        """End-to-end through scheduler/loop.py's _run_drive(), the actual
        call site -- not just the runtime_contract function directly."""
        brake = ParkingBrake(BrakeStorage(daemon.mem.db_path))
        brake.engage("scheduler")
        try:
            with pytest.raises(RuntimeError, match="ParkingBrake: scheduler blocked"):
                await _run_drive(daemon, "self_check", _noop_drive)
        finally:
            brake.disengage()


@pytest.mark.asyncio
class TestRunDriveFromLoopDelegatesCorrectly:
    """scheduler/loop.py's _run_drive() now delegates to
    run_drive_through_runtime_contract() -- proves the delegation preserves
    _run_drive's pre-existing (nudge_or_none, success_flag) contract."""

    async def test_success_path_unchanged(self, daemon):
        result, success = await _run_drive(daemon, "self_check", _noop_drive)
        assert success == 1
        assert result is None

    async def test_crash_path_unchanged(self, daemon):
        result, success = await _run_drive(daemon, "self_check", _boom_drive)
        assert (result, success) == (None, 0)

    async def test_denial_path_through_loop(self, daemon):
        daemon.identity_context = IdentityContext(
            tool_use_default_allowed=False,
            tool_use_allowlist=[],
        )

        result, success = await _run_drive(daemon, "send_on_users_behalf", _noop_drive)
        assert (result, success) == (None, 0)


@pytest.mark.asyncio
class TestDriveReflectionRecordedIntoSharedSink:
    """Extends Exit Gate #4's unified Reflection (item 11.16) to the
    scheduler surface -- the same `MemoryStore.reflections` sink chat and
    skill execution already write into."""

    async def test_completed_drive_records_a_reflection(self, daemon):
        await daemon.mem.init()  # ensure the shared reflections table exists
        await run_drive_through_runtime_contract(daemon, "self_check", _noop_drive, timeout=1.0)

        row = await daemon.mem.latest_reflection(REFLECTION_KIND)
        assert row is not None
        assert row["meta"]["surface"] == "scheduler"
        assert row["meta"]["action"] == "self_check"
        assert row["meta"]["outcome"] == "completed"

    async def test_denied_drive_records_a_reflection_with_reason(self, daemon):
        await daemon.mem.init()  # ensure the shared reflections table exists
        daemon.identity_context = IdentityContext(
            tool_use_default_allowed=False,
            tool_use_allowlist=[],
        )

        await run_drive_through_runtime_contract(
            daemon,
            "send_on_users_behalf",
            _noop_drive,
            timeout=1.0,
        )

        row = await daemon.mem.latest_reflection(REFLECTION_KIND)
        assert row is not None
        assert row["meta"]["surface"] == "scheduler"
        assert row["meta"]["action"] == "send_on_users_behalf"
        assert row["meta"]["outcome"] == "governance_denied"
        assert row["meta"]["reason"]

    async def test_reflection_safe_with_minimal_duck_typed_ctx(self, tmp_path):
        """_run_drive's pre-existing tests (test_parking_brake_integration.py)
        exercise a minimal MockCtx whose `.mem` is a bare object with only
        `db_path` (no MemoryStore, no `insert_reflection`, no
        identity_context) -- the added Reflection stage must stay
        best-effort and never break the drive's own return value for a
        context like that. The db itself still needs schema (system_flags)
        for the pre-existing ParkingBrake check, same as those tests."""
        from bartholomew.kernel.memory_store import MemoryStore

        db_path = str(tmp_path / "test.db")
        await MemoryStore(db_path).init()

        class MockMem:
            def __init__(self, db_path):
                self.db_path = db_path

        class MockCtx:
            def __init__(self, db_path):
                self.mem = MockMem(db_path)

        ctx = MockCtx(db_path)

        result, success = await run_drive_through_runtime_contract(
            ctx,
            "self_check",
            _noop_drive,
            timeout=1.0,
        )

        assert (result, success) == (None, 1)


class TestObservationInterpretationCandidateActionShapesAreReused:
    """Scheduler drives reuse the exact same generic dataclasses chat does
    -- proving 'the same stage sequence, without a redesign' (runtime_
    contract.py's own module docstring) is genuinely true, not aspirational."""

    def test_shapes_compose_the_way_run_drive_through_runtime_contract_uses_them(self):
        observation = Observation(source="scheduler", raw_content="self_check")
        interpretation = Interpretation(observation=observation, prompt="self_check")
        candidate_action = CandidateAction(kind="self_check", interpretation=interpretation)

        assert candidate_action.interpretation.observation.source == "scheduler"
        assert candidate_action.kind == "self_check"
