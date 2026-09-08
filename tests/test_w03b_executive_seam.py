"""W03-B: the seam, against real stores and real governance.

Acceptance criteria 1, 2 and 3 in behaviour rather than in structure:

* an executive-generated Windows action is represented through the canonical
  envelope and **cannot execute without authorization** through the defined
  host boundary and a clear Parking Brake;
* the executive reaches actuation only through that envelope (the structural
  half is `tests/test_w03b_no_bypass.py`; the behavioural half is here ---
  nothing runs, and the only rows that appear are the envelope's own);
* a materially ambiguous instruction produces a clarification, not an action.

**Nothing governance-shaped is mocked.** The database is a real SQLite file
with the real memory, governance, action and executive schemas; the Parking
Brake is the real `GovernanceStore`; the envelope is the real one the HTTP
route calls. The only doubles are where the *operating system* would be, and
no test here reaches one.
"""

from __future__ import annotations

import asyncio

import pytest

from bartholomew.actuation import devices
from bartholomew.actuation import seam as action_seam
from bartholomew.actuation import store as action_store
from bartholomew.actuation.allowlists import (
    ApplicationAllowlist,
    FilesystemRootAllowlist,
    UrlDomainAllowlist,
)
from bartholomew.actuation.capabilities import ALL_CAPABILITIES, CapabilityKind
from bartholomew.actuation.result import ErrorCategory
from bartholomew.actuation.store import ActionState
from bartholomew.executive import store as executive_store
from bartholomew.executive.plan import StepStatus, TaskStatus
from bartholomew.executive.seam import (
    OUTCOME_BRAKE,
    OUTCOME_CLARIFICATION,
    OUTCOME_PROPOSED,
    OUTCOME_REFUSED,
    advance_executive_task_through_runtime_contract,
    run_executive_task_through_runtime_contract,
)

TENANT = "tenant-a"
DEVICE = "desk-pc"
REQUESTER = "taylor"


class _Ctx:
    """The runtime context every Runtime Contract seam reads. Duck-typed, real stores."""

    def __init__(self, db_path, identity_context=None):
        from bartholomew.kernel.memory_store import MemoryStore

        self.mem = MemoryStore(db_path)
        self.db_path = db_path
        self.identity_context = identity_context
        self.governance_store = None
        self.blocking_executor = None


class _Registry:
    """A registry over an explicit list. Truthful: it really refuses the rest."""

    LABEL = "w03b-test-registry"

    def __init__(self, *enrolled):
        self._by_key = {(d.tenant_id, d.device_id): d for d in enrolled}

    def lookup(self, *, tenant_id, device_id):
        return self._by_key.get((tenant_id, device_id))


def _device(*, capabilities=None, trusted_autonomy=()):
    kinds = capabilities if capabilities is not None else ALL_CAPABILITIES
    return devices.EnrolledDevice(
        device_id=DEVICE,
        tenant_id=TENANT,
        platform="windows",
        enrolled=True,
        capabilities=tuple(devices.DeclaredCapability(kind=k, version=1) for k in kinds),
        applications=ApplicationAllowlist.from_pairs(
            {"notepad": "C:\\Windows\\System32\\notepad.exe"},
        ),
        url_domains=UrlDomainAllowlist.from_iterable(["example.com"]),
        filesystem_roots=FilesystemRootAllowlist.from_iterable(["C:\\Users\\t\\Documents"]),
        trusted_autonomy=frozenset(trusted_autonomy),
    )


@pytest.fixture
def db_path(tmp_path):
    from bartholomew.kernel.memory_store import MemoryStore
    from bartholomew.orchestrator.safety import governance_store as gs

    path = str(tmp_path / "executive.db")
    asyncio.run(MemoryStore(path).init())
    gs.ensure_schema(path)
    action_store.ensure_schema(path)
    executive_store.ensure_schema(path)
    return path


@pytest.fixture
def ctx(db_path):
    return _Ctx(db_path)


@pytest.fixture
def registry():
    reg = _Registry(_device())
    devices.install_registry(reg)
    yield reg
    devices.install_registry(None)


async def _run(ctx, registry, instruction, **overrides):
    kwargs = {
        "tenant_id": TENANT,
        "device_id": DEVICE,
        "requested_by": REQUESTER,
        "instruction": instruction,
        "registry": registry,
    }
    kwargs.update(overrides)
    return await run_executive_task_through_runtime_contract(ctx, **kwargs)


def _actions(db_path):
    from bartholomew.kernel.db_ctx import wal_db

    with wal_db(db_path, label="test_count") as conn:
        return conn.execute(
            "SELECT action_id, capability, state FROM windows_action_requests ORDER BY id",
        ).fetchall()


class TestOneProposalTravelsTheEnvelope:
    async def test_an_instruction_becomes_an_action_envelope_proposal(self, ctx, registry, db_path):
        result = await _run(ctx, registry, "focus notepad")
        assert result.outcome == OUTCOME_PROPOSED
        assert result.governance_allowed
        assert len(result.proposed_action_ids) == 1

        rows = _actions(db_path)
        assert len(rows) == 1
        assert rows[0][1] == CapabilityKind.FOCUS_WINDOW.value
        assert rows[0][2] == ActionState.PENDING_APPROVAL.value

    async def test_the_proposal_is_pending_and_nothing_executed(self, ctx, registry, db_path):
        result = await _run(ctx, registry, "focus notepad")
        action_id = result.proposed_action_ids[0]
        stored = action_store.get_action(db_path, tenant_id=TENANT, action_id=action_id)
        assert stored.state is ActionState.PENDING_APPROVAL
        assert stored.approved_by is None
        assert stored.lease_count == 0

    async def test_dispatch_is_refused_while_the_action_is_unapproved(
        self,
        ctx,
        registry,
        db_path,
    ):
        """Acceptance criterion 1: it cannot execute without authorization."""
        from bartholomew.actuation import arming

        result = await _run(ctx, registry, "focus notepad")
        action_id = result.proposed_action_ids[0]
        arming.arm(tenant_id=TENANT, device_id=DEVICE, armed_by="test", reason="seam suite")
        try:
            dispatched = await action_seam.run_action_dispatch_through_runtime_contract(
                ctx,
                tenant_id=TENANT,
                device_id=DEVICE,
                action_id=action_id,
                registry=registry,
            )
        finally:
            arming.reset_for_tests()
        assert not dispatched.governance_allowed
        assert dispatched.category is ErrorCategory.APPROVAL_MISSING
        stored = action_store.get_action(db_path, tenant_id=TENANT, action_id=action_id)
        assert stored.lease_count == 0

    async def test_only_the_first_step_of_a_multi_step_task_is_proposed(
        self,
        ctx,
        registry,
        db_path,
    ):
        result = await _run(ctx, registry, 'focus notepad and then type "hello"')
        assert len(result.proposed_action_ids) == 1
        assert len(_actions(db_path)) == 1
        assert [s.status for s in result.plan.steps] == [
            StepStatus.AWAITING_AUTHORIZATION,
            StepStatus.PLANNED,
        ]

    async def test_the_plan_survives_in_its_own_store_for_a_later_pass(
        self,
        ctx,
        registry,
        db_path,
    ):
        result = await _run(ctx, registry, 'focus notepad and then type "hello"')
        reloaded = executive_store.load_plan(
            db_path,
            tenant_id=TENANT,
            task_id=result.plan.task_id,
        )
        assert reloaded is not None
        assert reloaded.instruction == result.plan.instruction
        assert reloaded.steps[0].action_id == result.proposed_action_ids[0]

    async def test_an_explanation_is_produced_even_though_nothing_ran(self, ctx, registry):
        result = await _run(ctx, registry, "focus notepad")
        assert "nothing has run" in result.explanation
        assert result.proposed_action_ids[0] in result.explanation


class TestAmbiguityRaisesAQuestionNotAnAction:
    async def test_open_it_produces_a_clarification_and_no_action_row(
        self,
        ctx,
        registry,
        db_path,
    ):
        result = await _run(ctx, registry, "open it")
        assert result.outcome == OUTCOME_CLARIFICATION
        assert result.plan.status is TaskStatus.AWAITING_CLARIFICATION
        assert result.plan.clarification
        assert _actions(db_path) == []

    async def test_an_out_of_vocabulary_request_is_declined_with_nothing_proposed(
        self,
        ctx,
        registry,
        db_path,
    ):
        result = await _run(ctx, registry, "delete the old reports")
        assert result.outcome == OUTCOME_CLARIFICATION
        assert "cannot" in result.explanation
        assert _actions(db_path) == []

    async def test_the_understood_half_of_a_half_understood_instruction_is_not_proposed(
        self,
        ctx,
        registry,
        db_path,
    ):
        result = await _run(ctx, registry, "focus notepad and then open it")
        assert _actions(db_path) == []
        assert all(s.status is StepStatus.BLOCKED for s in result.plan.steps)
        assert "proposes an instruction whole" in (result.plan.steps[0].refusal_reason or "")


class TestGovernanceStopsTheExecutive:
    async def test_an_engaged_brake_refuses_before_anything_is_planned(
        self,
        ctx,
        registry,
        db_path,
    ):
        from bartholomew.orchestrator.safety.governance_store import GovernanceStore

        GovernanceStore(db_path).engage("global", actor="operator", reason="test halt")
        result = await _run(ctx, registry, "focus notepad")
        assert result.outcome == OUTCOME_BRAKE
        assert not result.governance_allowed
        assert _actions(db_path) == []

    async def test_the_same_intention_is_refused_by_policy_state_not_by_wording(
        self,
        ctx,
        registry,
        db_path,
    ):
        """`W03_TEST_CONTRACTS.md` §1: replay under differing brake state."""
        from bartholomew.orchestrator.safety.governance_store import GovernanceStore

        first = await _run(ctx, registry, "focus notepad")
        assert first.outcome == OUTCOME_PROPOSED

        GovernanceStore(db_path).engage("actuation", actor="operator", reason="halt")
        second = await _run(ctx, registry, "focus notepad")
        assert second.outcome == OUTCOME_BRAKE
        assert len(_actions(db_path)) == 1  # the second proposal wrote nothing

        GovernanceStore(db_path).disengage(actor="operator", reason="test resumed")
        third = await _run(ctx, registry, "focus notepad")
        assert third.outcome == OUTCOME_PROPOSED
        assert len(_actions(db_path)) == 2

    async def test_an_unreadable_brake_fails_closed(self, ctx, registry, monkeypatch):
        from bartholomew.executive import seam as executive_seam

        async def _explode(*_a, **_k):
            raise RuntimeError("the governance database is unreadable")

        monkeypatch.setattr(executive_seam, "engaged_state_fail_closed_off_loop", _explode)
        result = await _run(ctx, registry, "focus notepad")
        assert result.outcome == OUTCOME_BRAKE
        assert "could not be read" in result.reason

    async def test_a_capability_the_device_does_not_declare_is_refused_not_substituted(
        self,
        ctx,
        db_path,
    ):
        reg = _Registry(_device(capabilities=[CapabilityKind.FOCUS_WINDOW]))
        devices.install_registry(reg)
        try:
            result = await _run(ctx, reg, 'type "hello"')
        finally:
            devices.install_registry(None)
        assert result.outcome == OUTCOME_REFUSED
        assert _actions(db_path) == []
        assert result.plan.steps[0].status is StepStatus.BLOCKED
        assert "not enrolled" in (result.plan.steps[0].refusal_reason or "")

    async def test_an_unenrolled_device_is_refused_and_nothing_is_recorded(self, ctx, db_path):
        reg = _Registry()  # nothing enrolled at all
        devices.install_registry(reg)
        try:
            result = await _run(ctx, reg, "focus notepad")
        finally:
            devices.install_registry(None)
        assert result.outcome == OUTCOME_REFUSED
        assert _actions(db_path) == []


class TestTheIdentityGateStillDecides:
    """The executive's own kinds are cognition. That exemption authorizes nothing."""

    async def test_planning_works_under_an_identity_that_allowlists_only_the_request_kind(
        self,
        db_path,
        registry,
    ):
        from identity_interpreter.identity_context import IdentityContext

        ctx = _Ctx(
            db_path,
            identity_context=IdentityContext(
                tool_use_default_allowed=False,
                tool_use_allowlist=["windows_action_request"],
            ),
        )
        result = await _run(ctx, registry, "focus notepad")
        assert result.outcome == OUTCOME_PROPOSED

    async def test_removing_the_request_kind_from_the_allowlist_refuses_the_proposal(
        self,
        db_path,
        registry,
    ):
        """Non-vacuity: the exemption above grants nothing the envelope withholds."""
        from identity_interpreter.identity_context import IdentityContext

        ctx = _Ctx(
            db_path,
            identity_context=IdentityContext(
                tool_use_default_allowed=False,
                tool_use_allowlist=[],
            ),
        )
        result = await _run(ctx, registry, "focus notepad")
        assert result.outcome == OUTCOME_REFUSED
        assert not result.governance_allowed
        assert "Identity policy" in (result.reason or "")

    async def test_a_permissive_identity_still_cannot_make_a_proposal_execute(
        self,
        db_path,
        registry,
    ):
        """A fully permissive allowlist, dispatch kind included, changes nothing."""
        from bartholomew.actuation import arming
        from identity_interpreter.identity_context import IdentityContext

        ctx = _Ctx(
            db_path,
            identity_context=IdentityContext(
                tool_use_default_allowed=True,
                tool_use_allowlist=["windows_action_dispatch", "executive_task"],
            ),
        )
        result = await _run(ctx, registry, "focus notepad")
        action_id = result.proposed_action_ids[0]
        arming.arm(tenant_id=TENANT, device_id=DEVICE, armed_by="test", reason="suite")
        try:
            dispatched = await action_seam.run_action_dispatch_through_runtime_contract(
                ctx,
                tenant_id=TENANT,
                device_id=DEVICE,
                action_id=action_id,
                registry=registry,
            )
        finally:
            arming.reset_for_tests()
        assert not dispatched.governance_allowed
        assert dispatched.category is ErrorCategory.APPROVAL_MISSING


class TestAdvanceObservesBeforeItMoves:
    async def test_a_pending_action_leaves_the_plan_exactly_where_it_was(
        self,
        ctx,
        registry,
        db_path,
    ):
        first = await _run(ctx, registry, 'focus notepad and then type "hello"')
        advanced = await advance_executive_task_through_runtime_contract(
            ctx,
            tenant_id=TENANT,
            task_id=first.plan.task_id,
            registry=registry,
        )
        assert advanced.outcome == "waiting_on_authorization"
        assert len(_actions(db_path)) == 1
        assert advanced.plan.steps[1].status is StepStatus.PLANNED

    async def test_an_unknown_task_is_an_error_not_a_new_plan(self, ctx, registry):
        result = await advance_executive_task_through_runtime_contract(
            ctx,
            tenant_id=TENANT,
            task_id="exec-nothing",
            registry=registry,
        )
        assert result.outcome == "error"
        assert result.plan is None

    async def test_advancing_under_an_engaged_brake_is_refused(self, ctx, registry, db_path):
        from bartholomew.orchestrator.safety.governance_store import GovernanceStore

        first = await _run(ctx, registry, "focus notepad")
        GovernanceStore(db_path).engage("global", actor="operator", reason="halt")
        advanced = await advance_executive_task_through_runtime_contract(
            ctx,
            tenant_id=TENANT,
            task_id=first.plan.task_id,
            registry=registry,
        )
        assert advanced.outcome == OUTCOME_BRAKE


class TestProvenance:
    async def test_one_reflection_is_written_per_pass(self, ctx, registry, db_path):
        from bartholomew.kernel.db_ctx import wal_db

        await _run(ctx, registry, "focus notepad")
        with wal_db(db_path, label="test_reflections") as conn:
            rows = conn.execute(
                "SELECT content FROM reflections WHERE kind = 'action_reflection'",
            ).fetchall()
        assert any("Executive" in row[0] for row in rows)

    async def test_no_parameter_value_reaches_a_reflection_in_cleartext(
        self,
        ctx,
        registry,
        db_path,
    ):
        from bartholomew.kernel.db_ctx import wal_db

        secret = "the quick brown fox jumped"
        await _run(ctx, registry, f'type "{secret}"')
        with wal_db(db_path, label="test_reflections") as conn:
            rows = conn.execute("SELECT content, meta FROM reflections").fetchall()
        blob = " ".join(str(cell) for row in rows for cell in row)
        assert secret not in blob


class TestTheExplanationIsSourcedFromTheAuditTrail:
    """Acceptance criterion 5: the account is read back out of `ActionReflection`."""

    async def test_the_account_quotes_the_shared_reflection_sink(self, ctx, registry, db_path):
        from bartholomew.executive.explanation import load_task_reflections

        result = await _run(ctx, registry, "focus notepad")
        lines = load_task_reflections(db_path, result.plan.task_id)
        assert lines, "no reflections were recorded for this task"
        assert any("executive" in line for line in lines)
        assert any("windows_action" in line for line in lines)
        assert "Recorded decisions, from the audit trail" in result.explanation

    async def test_an_approval_granted_elsewhere_appears_in_the_account(
        self,
        ctx,
        registry,
        db_path,
    ):
        """The trail is shared, so it records decisions this process did not make."""
        from bartholomew.executive.explanation import load_task_reflections

        result = await _run(ctx, registry, "focus notepad")
        action_id = result.proposed_action_ids[0]
        approved = await action_seam.grant_action_approval(
            ctx,
            tenant_id=TENANT,
            action_id=action_id,
            approver="taylor",
            registry=registry,
        )
        assert approved.governance_allowed
        lines = load_task_reflections(db_path, result.plan.task_id)
        assert any("windows_action_approve" in line for line in lines)

    async def test_an_unreadable_audit_trail_says_less_rather_than_failing(self, tmp_path):
        from bartholomew.executive.explanation import load_task_reflections

        assert load_task_reflections(str(tmp_path / "nothing-here.db"), "exec-1") == []


class TestTheLoopClosesInProcess:
    """The advance path, without the Integration tier's fuller scenario set.

    Kept in the default (PR Fast) suite because the pacing rule and the
    recovery decision are the two places a regression would be silent: a plan
    that advanced on issuance, or a step that quietly repeated itself, would
    still pass every structural test in this file.
    """

    async def _approve_and_report(self, ctx, registry, action_id, status):
        from bartholomew.actuation import arming
        from bartholomew.actuation.request import to_iso, utc_now

        approved = await action_seam.grant_action_approval(
            ctx,
            tenant_id=TENANT,
            action_id=action_id,
            approver="taylor",
            registry=registry,
        )
        assert approved.governance_allowed, approved.reason
        arming.arm(tenant_id=TENANT, device_id=DEVICE, armed_by="test", reason="advance suite")
        try:
            leased = await action_seam.run_action_dispatch_through_runtime_contract(
                ctx,
                tenant_id=TENANT,
                device_id=DEVICE,
                action_id=action_id,
                registry=registry,
            )
            assert leased.governance_allowed, leased.reason
        finally:
            arming.reset_for_tests()
        return await action_seam.record_action_result_through_runtime_contract(
            ctx,
            tenant_id=TENANT,
            device_id=DEVICE,
            action_id=action_id,
            status=status,
            error_category=None,
            detail="reported by the test device",
            evidence={"digest": "sha256:abc"},
            observed_at=to_iso(utc_now()),
        )

    async def test_a_verified_step_releases_the_next_proposal(self, ctx, registry, db_path):
        started = await _run(ctx, registry, 'focus notepad and then type "hello"')
        await self._approve_and_report(
            ctx,
            registry,
            started.proposed_action_ids[0],
            "succeeded",
        )

        def _read_back(*, tenant_id, device_id, target, requested_by, db_path, store):
            class _R:
                available = True
                text = "active window 'Untitled - Notepad' (notepad)"
                code = None
                reason = None
                event_id = 11

            return _R()

        advanced = await advance_executive_task_through_runtime_contract(
            ctx,
            tenant_id=TENANT,
            task_id=started.plan.task_id,
            registry=registry,
            read_back_port=_read_back,
        )
        assert advanced.outcome == "advanced"
        assert advanced.plan.steps[0].status is StepStatus.VERIFIED
        assert advanced.plan.steps[1].status is StepStatus.AWAITING_AUTHORIZATION
        assert len(_actions(db_path)) == 2

    async def test_a_success_report_with_no_read_back_does_not_release_the_next_step(
        self,
        ctx,
        registry,
        db_path,
    ):
        started = await _run(ctx, registry, 'focus notepad and then type "hello"')
        await self._approve_and_report(
            ctx,
            registry,
            started.proposed_action_ids[0],
            "succeeded",
        )

        def _unavailable(*, tenant_id, device_id, target, requested_by, db_path, store):
            class _R:
                available = False
                text = None
                code = "no_consented_session"
                reason = "nothing is watching"
                event_id = None

            return _R()

        advanced = await advance_executive_task_through_runtime_contract(
            ctx,
            tenant_id=TENANT,
            task_id=started.plan.task_id,
            registry=registry,
            read_back_port=_unavailable,
        )
        assert advanced.plan.steps[0].status is StepStatus.UNKNOWN
        assert advanced.plan.steps[1].status is StepStatus.PLANNED
        assert len(_actions(db_path)) == 1

    async def test_a_cancelled_action_ends_the_step_rather_than_repeating_it(
        self,
        ctx,
        registry,
        db_path,
    ):
        started = await _run(ctx, registry, 'type "hello"')
        action_id = started.proposed_action_ids[0]
        cancelled = await action_seam.cancel_action_through_runtime_contract(
            ctx,
            tenant_id=TENANT,
            action_id=action_id,
            cancelled_by="taylor",
            reason="changed my mind",
        )
        assert cancelled.governance_allowed
        advanced = await advance_executive_task_through_runtime_contract(
            ctx,
            tenant_id=TENANT,
            task_id=started.plan.task_id,
            registry=registry,
        )
        assert advanced.plan.steps[0].status is StepStatus.FAILED
        assert advanced.plan.steps[0].recoveries[0]["decision"] == "stop_and_report"
        assert len(_actions(db_path)) == 1


class TestTheTaskStore:
    async def test_an_open_task_is_listable_and_a_finished_one_is_not(
        self,
        ctx,
        registry,
        db_path,
    ):
        started = await _run(ctx, registry, "focus notepad")
        open_tasks = executive_store.list_open_tasks(db_path, tenant_id=TENANT)
        assert [t["task_id"] for t in open_tasks] == [started.plan.task_id]
        assert executive_store.list_open_tasks(db_path, tenant_id="tenant-b") == []

    async def test_an_action_can_be_traced_back_to_the_task_that_proposed_it(
        self,
        ctx,
        registry,
        db_path,
    ):
        started = await _run(ctx, registry, "focus notepad")
        found = executive_store.find_task_for_action(
            db_path,
            tenant_id=TENANT,
            action_id=started.proposed_action_ids[0],
        )
        assert found == started.plan.task_id
        assert (
            executive_store.find_task_for_action(
                db_path,
                tenant_id=TENANT,
                action_id="not-an-action",
            )
            is None
        )

    async def test_another_tenant_cannot_read_this_tenants_plan(self, ctx, registry, db_path):
        started = await _run(ctx, registry, "focus notepad")
        assert (
            executive_store.load_plan(
                db_path,
                tenant_id="tenant-b",
                task_id=started.plan.task_id,
            )
            is None
        )

    def test_an_unwritable_database_is_a_refusal_not_a_silent_success(self, tmp_path):
        with pytest.raises(executive_store.ExecutivePersistenceError):
            executive_store.ensure_schema(str(tmp_path / "no" / "such" / "dir" / "x.db"))
