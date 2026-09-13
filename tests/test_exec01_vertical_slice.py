"""EXEC-01: one outcome-level goal, all the way through the real machinery.

The unit tests prove the cognition layer refuses what it should. This file
proves the thing that actually matters: that a *goal* --- not a dictated
sequence --- travels the whole existing governed path and is stopped by every
boundary that was there before.

**Nothing governance-shaped is faked.** The database is a real SQLite file with
the real memory, governance, action and executive schemas; the Parking Brake is
the real `GovernanceStore`; the envelope is the real one the HTTP route calls;
capability selection, parameter validation, approval binding, verification and
recovery are all the real implementations. The only two doubles are where a
*model* would be (`_Port`, which returns a fixed string) and where the
*operating system* would be (the read-back stand-ins). Neither of those is a
governance boundary, and no test here reaches a machine.

The acceptance test is `TestTheVerticalSlice::test_the_whole_loop`, which walks
the fourteen required conditions in order and asserts each one as it passes.
The classes after it take the same scenario apart boundary by boundary, so a
regression names which boundary it broke rather than only that the loop stopped
working.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from bartholomew.actuation import devices
from bartholomew.actuation import seam as action_seam
from bartholomew.actuation import store as action_store
from bartholomew.actuation.allowlists import (
    ApplicationAllowlist,
    FilesystemRootAllowlist,
    UrlDomainAllowlist,
)
from bartholomew.actuation.capabilities import CapabilityKind
from bartholomew.actuation.store import ActionState
from bartholomew.executive import store as executive_store
from bartholomew.executive.plan import StepStatus, TaskStatus
from bartholomew.executive.seam import (
    OUTCOME_BRAKE,
    OUTCOME_CLARIFICATION,
    OUTCOME_PROPOSED,
    advance_executive_task_through_runtime_contract,
    run_executive_task_through_runtime_contract,
)
from bartholomew.executive.verification import UNKNOWN, VERIFIED

TENANT = "tenant-a"
DEVICE = "desk-pc"
REQUESTER = "taylor"

#: The goal. It names an outcome. It does not name Notepad, does not name
#: "launch", "focus" or "type", and does not say how many steps there are.
GOAL = "Start a shopping list for me."


class _Ctx:
    def __init__(self, db_path, deliberation_port=None):
        from bartholomew.kernel.memory_store import MemoryStore

        self.mem = MemoryStore(db_path)
        self.db_path = db_path
        self.identity_context = None
        self.governance_store = None
        self.blocking_executor = None
        self.awaiting_response_store = None
        self.deliberation_port = deliberation_port


class _Registry:
    LABEL = "exec01-test-registry"

    def __init__(self, *enrolled):
        self._by_key = {(d.tenant_id, d.device_id): d for d in enrolled}

    def lookup(self, *, tenant_id, device_id):
        return self._by_key.get((tenant_id, device_id))


def _device():
    """A device with a genuine choice: two editors, six capabilities.

    Acceptance requirement 3 is explicit that the answer must not be forced by
    there being one option. Notepad and WordPad are both allowlisted and both
    reachable by `launch_app`; `open_url` offers a materially different path to
    "somewhere to write" altogether.
    """
    kinds = (
        CapabilityKind.LAUNCH_APP,
        CapabilityKind.FOCUS_WINDOW,
        CapabilityKind.MANAGE_WINDOW,
        CapabilityKind.TYPE_TEXT,
        CapabilityKind.OPEN_URL,
        CapabilityKind.CLIPBOARD_READ,
    )
    return devices.EnrolledDevice(
        device_id=DEVICE,
        tenant_id=TENANT,
        platform="windows",
        enrolled=True,
        capabilities=tuple(devices.DeclaredCapability(kind=k, version=1) for k in kinds),
        applications=ApplicationAllowlist.from_pairs(
            {
                "notepad": "C:\\Windows\\System32\\notepad.exe",
                "wordpad": "C:\\Program Files\\Windows NT\\Accessories\\wordpad.exe",
            },
        ),
        url_domains=UrlDomainAllowlist.from_iterable(["example.com"]),
        filesystem_roots=FilesystemRootAllowlist.from_iterable(["C:\\Users\\t\\Documents"]),
    )


#: What a model returns for `GOAL`. Fixed, so the test is deterministic; the
#: *shape* is what a real model would have to produce, and every field of it is
#: validated by real code before it becomes a step.
COGNITION_ANSWER = json.dumps(
    {
        "objective": "an editable note is open, focused, and has the list started in it",
        "situation": "no editor appears to be open yet",
        "sub_goals": [
            "an editable surface exists",
            "that surface holds keyboard focus",
            "the list has its first content",
        ],
        "steps": [
            {
                "capability": "windows.launch_app",
                "parameters": {"app_id": "notepad"},
                "purpose": "open a text editor to hold the shopping list",
                "necessary_because": "there is nowhere to write the list yet",
            },
            {
                "capability": "windows.focus_window",
                "parameters": {"app_id": "notepad"},
                "purpose": "bring the editor to the front so typing lands in it",
                "necessary_because": "typed text goes to whichever control has focus",
            },
            {
                "capability": "windows.type_text",
                "parameters": {"text": "Shopping list"},
                "purpose": "start the list off",
                "necessary_because": "an empty editor is not a started list",
            },
        ],
        "clarification": None,
        "refusal": None,
        "confidence": "high",
    },
)


class _Port:
    def __init__(self, payload=COGNITION_ANSWER):
        self.payload = payload
        self.prompts: list[str] = []

    def deliberate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.payload


@pytest.fixture
def db_path(tmp_path):
    from bartholomew.kernel.memory_store import MemoryStore
    from bartholomew.orchestrator.safety import governance_store as gs

    path = str(tmp_path / "exec01.db")
    asyncio.run(MemoryStore(path).init())
    gs.ensure_schema(path)
    action_store.ensure_schema(path)
    executive_store.ensure_schema(path)
    return path


@pytest.fixture
def port():
    return _Port()


@pytest.fixture
def ctx(db_path, port):
    return _Ctx(db_path, deliberation_port=port)


@pytest.fixture
def registry():
    reg = _Registry(_device())
    devices.install_registry(reg)
    yield reg
    devices.install_registry(None)


async def _run(ctx, registry, instruction=GOAL, **overrides):
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

    with wal_db(db_path, label="exec01_count") as conn:
        return conn.execute(
            "SELECT action_id, capability, state FROM windows_action_requests ORDER BY id",
        ).fetchall()


def _window_read_back(text="active window 'Untitled - Notepad' (notepad)"):
    def _port(*, tenant_id, device_id, target, requested_by, db_path, store):
        class _Result:
            available = True
            code = None
            reason = None
            event_id = 11

        _Result.text = text
        return _Result()

    return _port


def _unavailable_read_back(*, tenant_id, device_id, target, requested_by, db_path, store):
    class _Result:
        available = False
        text = None
        code = "no_consented_session"
        reason = "nothing is watching this machine"
        event_id = None

    return _Result()


async def _approve_dispatch_and_report(ctx, registry, action_id, status="succeeded"):
    """Everything a human and a device do between one proposal and the next."""
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
    arming.arm(tenant_id=TENANT, device_id=DEVICE, armed_by="test", reason="exec01 slice")
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


# ---------------------------------------------------------------------------


class TestTheVerticalSlice:
    async def test_the_whole_loop(self, ctx, registry, db_path, port):
        """The acceptance test. Fourteen conditions, in order, on real machinery."""
        # (1) an outcome-level goal, in natural language, and (2) no capability
        # sequence in it.
        for word in ("notepad", "launch", "focus", "type", "open"):
            assert word not in GOAL.lower()

        result = await _run(ctx, registry)

        # (3) the executive had a real choice: more than one application and
        # more than one materially different path were available to it.
        catalogue = port.prompts[0]
        assert "notepad" in catalogue and "wordpad" in catalogue
        assert CapabilityKind.OPEN_URL.value in catalogue

        # (4) it identified the intended outcome, and (5) inferred a step the
        # person never stated.
        deliberation = result.plan.deliberation["deliberation"]
        assert "list" in deliberation["objective"].lower()
        capabilities = [s.capability for s in result.plan.steps]
        assert CapabilityKind.FOCUS_WINDOW.value in capabilities
        assert "focus" not in GOAL.lower()

        # (6) the plan is bounded and structured.
        assert 0 < len(result.plan.steps) <= 6

        # (7) every capability was validated against the real definitions: the
        # canonical parameters on the plan are the validator's output.
        launch = result.plan.steps[0]
        assert launch.capability == CapabilityKind.LAUNCH_APP.value
        assert launch.parameters == {"app_id": "notepad"}
        assert launch.selection.available

        # (8) cognition did not execute anything, and (9) the proposal entered
        # the existing governed path: exactly one envelope row, pending.
        assert result.outcome == OUTCOME_PROPOSED
        rows = _actions(db_path)
        assert len(rows) == 1
        assert rows[0][2] == ActionState.PENDING_APPROVAL.value
        stored = action_store.get_action(db_path, tenant_id=TENANT, action_id=rows[0][0])
        assert stored.approved_by is None
        assert stored.lease_count == 0

        # (10) authorisation remains authoritative: only the first step is even
        # proposed, and it is waiting on a human.
        assert result.plan.steps[0].status is StepStatus.AWAITING_AUTHORIZATION
        assert [s.status for s in result.plan.steps[1:]] == [
            StepStatus.PLANNED,
            StepStatus.PLANNED,
        ]

        # (11) a result is received, through the real approval and dispatch path.
        await _approve_dispatch_and_report(ctx, registry, result.proposed_action_ids[0])

        # (12) the existing verification decides what actually happened.
        advanced = await advance_executive_task_through_runtime_contract(
            ctx,
            tenant_id=TENANT,
            task_id=result.plan.task_id,
            registry=registry,
            read_back_port=_window_read_back(),
        )
        assert advanced.plan.steps[0].verification.verdict == VERIFIED

        # (13) the executive consumed the verified result and moved on --- and
        # only then.
        assert advanced.plan.steps[0].status is StepStatus.VERIFIED
        assert advanced.plan.steps[1].status is StepStatus.AWAITING_AUTHORIZATION
        assert len(_actions(db_path)) == 2

        # (14) provenance exists for the executive decision, in the audit trail.
        # Not merely that cognition ran, but what it actually concluded: a
        # reviewer can read the objective and the inferred sub-goals back out of
        # the shared Reflection sink.
        metas = _reflection_meta(db_path, result.plan.task_id)
        assert metas, "no Reflection was written for the executive decision"
        recorded = [m["deliberation"] for m in metas if (m.get("deliberation") or {}).get("used")]
        assert recorded, "the audit trail does not record that cognition contributed"
        assert recorded[0]["deliberation"]["objective"]
        assert recorded[0]["deliberation"]["sub_goals"]

    async def test_the_goal_alone_produces_nothing_without_a_model(
        self,
        db_path,
        registry,
    ):
        """The control, through the real seam: this is what EXEC-01 changed.

        Same goal, same device, same governance --- and no deliberation port.
        The executive asks a question and proposes nothing, which is exactly
        what it did before this repair.
        """
        ctx = _Ctx(db_path, deliberation_port=None)
        result = await _run(ctx, registry)
        assert result.outcome == OUTCOME_CLARIFICATION
        assert result.proposed_action_ids == []
        assert _actions(db_path) == []


def _reflection_meta(db_path, task_id):
    """Every executive Reflection written for one task, as its persisted `meta`.

    `ActionReflection.to_memory_row` spreads `details` into `meta`, so the
    deliberation record written by `explanation.explanation_details` lands here.
    This reads the real `reflections` table rather than the executive's own
    tables: the point is that the provenance survives in the *shared* audit
    sink, where a reviewer would look for it.
    """
    from bartholomew.kernel.db_ctx import wal_db
    from bartholomew.kernel.reflection import REFLECTION_KIND

    with wal_db(db_path, label="exec01_reflections") as conn:
        rows = conn.execute(
            "SELECT meta FROM reflections WHERE kind = ? ORDER BY id",
            (REFLECTION_KIND,),
        ).fetchall()

    found = []
    for (meta_raw,) in rows:
        try:
            meta = json.loads(meta_raw) if isinstance(meta_raw, str) else dict(meta_raw or {})
        except (TypeError, ValueError):
            continue
        if meta.get("task_id") == task_id:
            found.append(meta)
    return found


# ---------------------------------------------------------------------------
# The boundaries, one at a time
# ---------------------------------------------------------------------------


class TestGovernanceRemainsAuthoritative:
    async def test_the_parking_brake_stops_a_deliberated_task_before_it_is_planned(
        self,
        ctx,
        registry,
        db_path,
        port,
    ):
        """Precedence is unchanged: the brake is read before anything is understood."""
        from bartholomew.orchestrator.safety.governance_store import GovernanceStore

        GovernanceStore(db_path).engage("global", actor="operator", reason="test halt")
        result = await _run(ctx, registry)

        assert result.outcome == OUTCOME_BRAKE
        assert not result.governance_allowed
        assert _actions(db_path) == []
        assert port.prompts == [], "the brake must stop the task before a model is consulted"

    async def test_an_engaged_actuation_brake_also_stops_it(self, ctx, registry, db_path):
        from bartholomew.orchestrator.safety.governance_store import GovernanceStore

        GovernanceStore(db_path).engage("actuation", actor="operator", reason="halt")
        result = await _run(ctx, registry)
        assert result.outcome == OUTCOME_BRAKE
        assert _actions(db_path) == []

    async def test_a_deliberated_action_cannot_execute_without_authorization(
        self,
        ctx,
        registry,
        db_path,
    ):
        """The same refusal an explicitly-instructed action gets. Cognition earns nothing."""
        from bartholomew.actuation import arming
        from bartholomew.actuation.result import ErrorCategory

        result = await _run(ctx, registry)
        action_id = result.proposed_action_ids[0]
        arming.arm(tenant_id=TENANT, device_id=DEVICE, armed_by="test", reason="exec01")
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

    async def test_an_unenrolled_device_refuses_the_whole_goal(self, ctx, db_path, port):
        """No device, no catalogue, nothing to reason with, nothing proposed."""
        empty = _Registry()
        devices.install_registry(empty)
        try:
            result = await _run(ctx, empty)
        finally:
            devices.install_registry(None)
        assert result.proposed_action_ids == []
        assert _actions(db_path) == []
        assert port.prompts == []


class TestVerificationRemainsIndependent:
    async def test_a_device_reporting_success_with_no_read_back_is_unknown(
        self,
        ctx,
        registry,
        db_path,
    ):
        """Acceptance: the endpoint says it worked; verification says we cannot tell."""
        result = await _run(ctx, registry)
        await _approve_dispatch_and_report(ctx, registry, result.proposed_action_ids[0])

        advanced = await advance_executive_task_through_runtime_contract(
            ctx,
            tenant_id=TENANT,
            task_id=result.plan.task_id,
            registry=registry,
            read_back_port=_unavailable_read_back,
        )
        assert advanced.plan.steps[0].verification.verdict == UNKNOWN
        assert advanced.plan.steps[0].status is StepStatus.UNKNOWN

    async def test_an_unknown_outcome_does_not_release_the_next_step_or_repeat_itself(
        self,
        ctx,
        registry,
        db_path,
    ):
        """Unknown is not success, and it is not a reason to try again."""
        result = await _run(ctx, registry)
        await _approve_dispatch_and_report(ctx, registry, result.proposed_action_ids[0])
        advanced = await advance_executive_task_through_runtime_contract(
            ctx,
            tenant_id=TENANT,
            task_id=result.plan.task_id,
            registry=registry,
            read_back_port=_unavailable_read_back,
        )
        assert advanced.plan.steps[1].status is StepStatus.PLANNED
        assert advanced.plan.status is not TaskStatus.COMPLETED
        assert len(_actions(db_path)) == 1, "an unknown step must not be blindly repeated"

    async def test_a_read_back_that_contradicts_the_device_does_not_verify(
        self,
        ctx,
        registry,
        db_path,
    ):
        """The device says succeeded; the machine's state says Calculator.

        The device's own report loses. What follows is the *existing* bounded
        recovery: a re-proposal that needs a fresh human approval, and --- the
        assertion that matters --- the second step is not released. A deliberated
        plan is paced by exactly the same rule as a dictated one.
        """
        result = await _run(ctx, registry)
        await _approve_dispatch_and_report(ctx, registry, result.proposed_action_ids[0])
        advanced = await advance_executive_task_through_runtime_contract(
            ctx,
            tenant_id=TENANT,
            task_id=result.plan.task_id,
            registry=registry,
            read_back_port=_window_read_back("active window 'Calculator' (calc)"),
        )
        assert advanced.plan.steps[0].status is not StepStatus.VERIFIED
        assert advanced.plan.steps[1].status is StepStatus.PLANNED
        assert advanced.plan.status is not TaskStatus.COMPLETED

    async def test_a_failed_step_re_proposes_under_a_new_approval_never_a_blind_retry(
        self,
        ctx,
        registry,
        db_path,
    ):
        """A failure is recovered by a defined decision, not by trying again.

        `launch_app` is not an `always`-approval capability, so `recovery.py`
        offers one bounded re-proposal. What makes it a re-proposal rather than
        a retry is that the old action id is gone and the new request is
        `pending_approval`: the earlier approval cannot be spent on it.
        """
        from bartholomew.executive.recovery import MAX_STEP_ATTEMPTS, RE_PROPOSE

        result = await _run(ctx, registry)
        first_action = result.proposed_action_ids[0]
        await _approve_dispatch_and_report(ctx, registry, first_action, status="failed")
        advanced = await advance_executive_task_through_runtime_contract(
            ctx,
            tenant_id=TENANT,
            task_id=result.plan.task_id,
            registry=registry,
            read_back_port=_window_read_back(),
        )

        step = advanced.plan.steps[0]
        assert [r["decision"] for r in step.recoveries] == [RE_PROPOSE]
        assert step.recoveries[0]["requires_new_authorization"] is True
        assert step.attempts <= MAX_STEP_ATTEMPTS
        assert step.action_id != first_action

        rows = _actions(db_path)
        assert len(rows) == 2
        assert rows[1][2] == ActionState.PENDING_APPROVAL.value
        reproposed = action_store.get_action(db_path, tenant_id=TENANT, action_id=rows[1][0])
        assert reproposed.approved_by is None
        assert reproposed.lease_count == 0

        # And the rest of the plan has not moved.
        assert advanced.plan.steps[1].status is StepStatus.PLANNED

    async def test_the_attempt_bound_stops_a_repeatedly_failing_step(
        self,
        ctx,
        registry,
        db_path,
    ):
        """Non-vacuity for the bound: the second failure is not re-proposed again."""
        from bartholomew.executive.recovery import MAX_STEP_ATTEMPTS

        result = await _run(ctx, registry)
        spent: set[str] = set()
        action_id = result.proposed_action_ids[0]
        rounds = 0
        # Fail whatever is currently outstanding, until the executive stops
        # offering a fresh one. The loop is bounded well above MAX_STEP_ATTEMPTS
        # so that a *missing* bound shows up as this assertion failing rather
        # than as the test hanging.
        while action_id is not None and action_id not in spent and rounds < 6:
            rounds += 1
            spent.add(action_id)
            await _approve_dispatch_and_report(ctx, registry, action_id, status="failed")
            advanced = await advance_executive_task_through_runtime_contract(
                ctx,
                tenant_id=TENANT,
                task_id=result.plan.task_id,
                registry=registry,
                read_back_port=_window_read_back(),
            )
            action_id = advanced.plan.steps[0].action_id

        assert rounds == MAX_STEP_ATTEMPTS, "the executive kept proposing past its own bound"
        assert advanced.plan.steps[0].attempts <= MAX_STEP_ATTEMPTS
        assert len(_actions(db_path)) <= MAX_STEP_ATTEMPTS
        assert advanced.plan.steps[1].status is StepStatus.PLANNED
        assert advanced.plan.status is not TaskStatus.COMPLETED


class TestTheAccountThePersonReads:
    async def test_the_person_is_told_the_steps_were_worked_out_not_asked_for(
        self,
        ctx,
        registry,
    ):
        """A step nobody named is a step the person is owed an account of."""
        result = await _run(ctx, registry)
        account = result.explanation.lower()
        assert "worked the steps out" in account or "worked out" in account
        assert "still needs your approval" in account or "approval" in account

    async def test_the_account_states_the_understood_objective(self, ctx, registry):
        result = await _run(ctx, registry)
        assert "i took you to mean" in result.explanation.lower()

    async def test_a_recognised_instruction_gets_no_deliberation_account(self, ctx, registry):
        """Non-vacuity: the account appears because reasoning happened, not always."""
        result = await _run(ctx, registry, instruction="focus notepad")
        assert "i took you to mean" not in result.explanation.lower()
        assert result.plan.deliberation["used"] is False


class TestTheDisclosureSurvivesIntoLaterApprovals:
    """Regression. The inferred steps are the ones approved *later*.

    The account explains the reasoning on the first pass. `advance` then
    proposes step 2 --- `focus_window`, which nobody named --- to a person who,
    if the provenance had stayed in memory only, would be told nothing about
    where it came from. That is the point at which disclosure matters most, so
    it is the point it must not vanish.

    `Plan.deliberation` is therefore persisted, with an additive migration for
    databases written by a build that predates the column.
    """

    async def test_provenance_survives_a_reload(self, ctx, registry, db_path):
        started = await _run(ctx, registry)
        reloaded = executive_store.load_plan(
            db_path,
            tenant_id=TENANT,
            task_id=started.plan.task_id,
        )
        assert reloaded is not None
        assert reloaded.deliberation is not None
        assert reloaded.deliberation["used"] is True
        assert reloaded.deliberation["deliberation"]["objective"]

    async def test_the_second_step_is_still_disclosed_when_it_is_proposed(
        self,
        ctx,
        registry,
        db_path,
    ):
        started = await _run(ctx, registry)
        await _approve_dispatch_and_report(ctx, registry, started.proposed_action_ids[0])
        advanced = await advance_executive_task_through_runtime_contract(
            ctx,
            tenant_id=TENANT,
            task_id=started.plan.task_id,
            registry=registry,
            read_back_port=_window_read_back(),
        )
        # The inferred step is the one now waiting for a human.
        assert advanced.plan.steps[1].capability == CapabilityKind.FOCUS_WINDOW.value
        assert advanced.plan.steps[1].status is StepStatus.AWAITING_AUTHORIZATION
        # And the account they read still says it was worked out, not asked for.
        assert "i took you to mean" in advanced.explanation.lower()

    async def test_the_advance_reflection_still_records_the_reasoning(
        self,
        ctx,
        registry,
        db_path,
    ):
        started = await _run(ctx, registry)
        await _approve_dispatch_and_report(ctx, registry, started.proposed_action_ids[0])
        await advance_executive_task_through_runtime_contract(
            ctx,
            tenant_id=TENANT,
            task_id=started.plan.task_id,
            registry=registry,
            read_back_port=_window_read_back(),
        )
        metas = _reflection_meta(db_path, started.plan.task_id)
        used = [m for m in metas if (m.get("deliberation") or {}).get("used")]
        assert len(used) >= 2, "every pass of a deliberated task must record that it was one"

    async def test_a_database_written_before_the_column_existed_still_opens(self, tmp_path):
        """Additive migration, against a table of the old shape with a row in it."""
        import sqlite3

        path = str(tmp_path / "old.db")
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE executive_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL, task_id TEXT NOT NULL, device_id TEXT NOT NULL,
                requested_by TEXT NOT NULL, instruction TEXT NOT NULL, status TEXT NOT NULL,
                clarification TEXT, clarification_entry_id INTEGER,
                notes_json TEXT NOT NULL DEFAULT '[]',
                cautions_json TEXT NOT NULL DEFAULT '[]',
                evidence_refused_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE (tenant_id, task_id)
            );
            """,
        )
        connection.execute(
            "INSERT INTO executive_tasks (tenant_id, task_id, device_id, requested_by,"
            " instruction, status, created_at, updated_at)"
            " VALUES ('tenant-a', 'old-1', 'desk-pc', 'taylor', 'old task', 'in_progress', 'x', 'y')",
        )
        connection.commit()
        connection.close()

        executive_store.ensure_schema(path)
        executive_store.ensure_schema(path)  # idempotent

        reloaded = executive_store.load_plan(path, tenant_id="tenant-a", task_id="old-1")
        assert reloaded is not None, "a row written before the column must still load"
        assert reloaded.deliberation is None
        assert reloaded.instruction == "old task"
