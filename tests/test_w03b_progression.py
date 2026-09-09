"""W03-B acceptance criteria 4 and 5: pacing, verification, recovery, explanation.

Three properties, each of which the wave's research contract names:

* **A step advances only after the previous one verified** (`plan.py`'s
  `next_actionable_step` is the single function that says what is next).
* **Issued is not succeeded** (`W03_TEST_CONTRACTS.md` §5): a device's own
  report of success with nothing read back leaves the step `unknown`.
* **A failed or unknown step gets a defined recovery decision, never a blind
  retry** --- and an `unknown` never re-proposes at all.

The read-back port is W03-A's published `read_back` signature, injected here as
a controlled double. The tests assert the signature is called the way the
published contract defines it, so a change on either side shows up as a failure
rather than as silence.
"""

from __future__ import annotations

import pytest

from bartholomew.actuation.allowlists import (
    ApplicationAllowlist,
    FilesystemRootAllowlist,
    UrlDomainAllowlist,
)
from bartholomew.actuation.capabilities import ALL_CAPABILITIES
from bartholomew.actuation.devices import DeclaredCapability, EnrolledDevice
from bartholomew.executive import verification as verification_module
from bartholomew.executive.explanation import explain_task
from bartholomew.executive.intent import parse_task
from bartholomew.executive.plan import (
    StepStatus,
    TaskStatus,
    build_plan,
    next_actionable_step,
    plan_status,
)
from bartholomew.executive.recovery import (
    ABANDON_STEP,
    ASK_FOR_CLARIFICATION,
    MAX_STEP_ATTEMPTS,
    RE_PROPOSE,
    STOP_AND_REPORT,
    decide_recovery,
)
from bartholomew.executive.verification import (
    FAILED,
    SOURCE_ABSENT,
    SOURCE_READ_BACK,
    UNKNOWN,
    VERIFIED,
    verify_step,
)

TENANT = "tenant-a"
DEVICE = "desk-pc"
REQUESTER = "taylor"


def _device():
    return EnrolledDevice(
        device_id=DEVICE,
        tenant_id=TENANT,
        platform="windows",
        enrolled=True,
        capabilities=tuple(DeclaredCapability(kind=k, version=1) for k in ALL_CAPABILITIES),
        applications=ApplicationAllowlist.from_pairs(
            {"notepad": "C:\\Windows\\System32\\notepad.exe"},
        ),
        url_domains=UrlDomainAllowlist.from_iterable(["example.com"]),
        filesystem_roots=FilesystemRootAllowlist.from_iterable(["C:\\Users\\t\\Documents"]),
    )


def _two_step_plan():
    return build_plan(
        intent=parse_task('focus notepad and then type "hello"'),
        tenant_id=TENANT,
        device_id=DEVICE,
        requested_by=REQUESTER,
        device=_device(),
    )


class _ReadBack:
    """A double with W03-A's published `read_back` signature, exactly.

    Keyword-only, and it records what it was called with, so a drift in either
    direction --- this package calling it differently, or the contract changing
    --- surfaces as a failure here.
    """

    def __init__(self, *, available=True, text="", code=None, reason=None, event_id=7):
        self.available = available
        self.text = text
        self.code = code
        self.reason = reason
        self.event_id = event_id
        self.calls = []

    def __call__(self, *, tenant_id, device_id, target, requested_by, db_path, store):
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "device_id": device_id,
                "target": target,
                "requested_by": requested_by,
                "db_path": db_path,
                "store": store,
            },
        )
        return self


class TestThePacingRule:
    def test_the_first_step_is_actionable_and_the_second_is_not(self):
        plan = _two_step_plan()
        assert len(plan.steps) == 2
        assert next_actionable_step(plan) is plan.steps[0]

    @pytest.mark.parametrize(
        "status",
        [
            StepStatus.AWAITING_AUTHORIZATION,
            StepStatus.DISPATCHED,
            StepStatus.FAILED,
            StepStatus.UNKNOWN,
            StepStatus.BLOCKED,
        ],
    )
    def test_no_state_but_verified_lets_the_plan_move_on(self, status):
        plan = _two_step_plan()
        plan.steps[0].status = status
        assert next_actionable_step(plan) is None

    def test_a_verified_first_step_releases_the_second(self):
        plan = _two_step_plan()
        plan.steps[0].status = StepStatus.VERIFIED
        assert next_actionable_step(plan) is plan.steps[1]

    def test_a_finished_plan_offers_nothing_further(self):
        plan = _two_step_plan()
        for step in plan.steps:
            step.status = StepStatus.VERIFIED
        assert next_actionable_step(plan) is None
        assert plan_status(plan) is TaskStatus.COMPLETED

    def test_an_unknown_step_stops_the_task_rather_than_completing_it(self):
        plan = _two_step_plan()
        plan.steps[0].status = StepStatus.UNKNOWN
        assert plan_status(plan) is TaskStatus.STOPPED


class TestIssuedIsNotSucceeded:
    def test_a_device_saying_succeeded_with_no_read_back_is_unknown(self):
        result = verify_step(
            capability="windows.focus_window",
            parameters={"app_id": "notepad"},
            device_status="succeeded",
            tenant_id=TENANT,
            device_id=DEVICE,
            requested_by=REQUESTER,
            read_back_port=None,
            db_path=None,
        )
        # Integration-only correction (W03-F). W03-B wrote this on a tree with
        # no `bartholomew/multimodal/`, where `resolve_read_back_port()` could
        # only answer `None` and the source could only be `absent`. On the
        # composed head W03-A IS present, the port resolves, the real read-back
        # runs and reports `no_consented_session` -- so the source is
        # `read_back` and carries a published unavailability code.
        #
        # The composition makes the answer MORE honest, not less: "we looked
        # and there is no consented session" rather than "we have nothing to
        # look with". So the load-bearing assertion is tightened rather than
        # relaxed. With no consented session nothing can be observed, so
        # VERIFIED is now impossible and is no longer accepted; the hedge W03-B
        # needed while it could not know what the integrated tree would carry
        # is exactly what composition resolves.
        assert result.verdict == UNKNOWN
        assert result.verified is False
        if result.source == SOURCE_ABSENT:
            # No perception package in this tree: nothing was read.
            assert result.read_back_code is None
        else:
            # W03-A present: the read was attempted and honestly refused.
            assert result.source == SOURCE_READ_BACK
            assert result.read_back_code == "no_consented_session"

    def test_an_unavailable_read_back_leaves_the_step_unknown_with_its_code(self):
        port = _ReadBack(available=False, code="no_consented_session", reason="none live")
        result = verify_step(
            capability="windows.focus_window",
            parameters={"app_id": "notepad"},
            device_status="succeeded",
            tenant_id=TENANT,
            device_id=DEVICE,
            requested_by=REQUESTER,
            read_back_port=port,
        )
        assert result.verdict == UNKNOWN
        assert result.read_back_code == "no_consented_session"
        assert result.source == SOURCE_READ_BACK

    def test_a_read_back_that_shows_the_expected_state_verifies(self):
        port = _ReadBack(text="active window 'Untitled - Notepad' (notepad): 4 controls")
        result = verify_step(
            capability="windows.focus_window",
            parameters={"app_id": "notepad"},
            device_status="succeeded",
            tenant_id=TENANT,
            device_id=DEVICE,
            requested_by=REQUESTER,
            read_back_port=port,
        )
        assert result.verdict == VERIFIED
        assert result.read_back_event_id == 7

    def test_a_read_back_that_contradicts_a_success_report_wins(self):
        port = _ReadBack(text="active window 'Calculator' (calc): 9 controls")
        result = verify_step(
            capability="windows.focus_window",
            parameters={"app_id": "notepad"},
            device_status="succeeded",
            tenant_id=TENANT,
            device_id=DEVICE,
            requested_by=REQUESTER,
            read_back_port=port,
        )
        assert result.verdict == FAILED
        assert "the state wins" in result.detail

    def test_a_device_reporting_failure_is_failed_without_consulting_anything(self):
        port = _ReadBack(text="notepad")
        result = verify_step(
            capability="windows.focus_window",
            parameters={"app_id": "notepad"},
            device_status="failed",
            tenant_id=TENANT,
            device_id=DEVICE,
            requested_by=REQUESTER,
            read_back_port=port,
        )
        assert result.verdict == FAILED
        assert port.calls == []

    def test_no_result_at_all_is_unknown_not_failed(self):
        result = verify_step(
            capability="windows.focus_window",
            parameters={"app_id": "notepad"},
            device_status=None,
            tenant_id=TENANT,
            device_id=DEVICE,
            requested_by=REQUESTER,
        )
        assert result.verdict == UNKNOWN
        assert result.device_status is None

    def test_a_capability_with_no_defined_check_is_unverified_not_verified(self):
        port = _ReadBack(text="anything at all")
        result = verify_step(
            capability="windows.open_url",
            parameters={"url": "https://example.com/x"},
            device_status="succeeded",
            tenant_id=TENANT,
            device_id=DEVICE,
            requested_by=REQUESTER,
            read_back_port=port,
        )
        assert result.verdict == UNKNOWN
        assert "no defined read-back check" in result.detail

    def test_the_port_is_called_with_w03_a_s_published_signature(self):
        port = _ReadBack(text="notepad")
        verify_step(
            capability="windows.focus_window",
            parameters={"app_id": "notepad"},
            device_status="succeeded",
            tenant_id=TENANT,
            device_id=DEVICE,
            requested_by=REQUESTER,
            db_path="/tmp/x.db",
            read_back_port=port,
            read_back_target=None,
            store="a-store",
        )
        assert port.calls == [
            {
                "tenant_id": TENANT,
                "device_id": DEVICE,
                "target": None,
                "requested_by": REQUESTER,
                "db_path": "/tmp/x.db",
                "store": "a-store",
            },
        ]

    def test_a_read_back_that_raises_degrades_to_unknown_not_to_a_crash(self):
        def _raises(**_kwargs):
            raise RuntimeError("the capture session went away")

        result = verify_step(
            capability="windows.focus_window",
            parameters={"app_id": "notepad"},
            device_status="succeeded",
            tenant_id=TENANT,
            device_id=DEVICE,
            requested_by=REQUESTER,
            read_back_port=_raises,
        )
        assert result.verdict == UNKNOWN

    def test_the_port_resolver_returns_none_when_the_module_is_absent(self, monkeypatch):
        monkeypatch.setattr(verification_module, "READ_BACK_MODULE", "not_a_real_module_at_all")
        assert verification_module.resolve_read_back_port() is None


class TestRecoveryIsAlwaysADecision:
    def test_an_unknown_step_asks_and_never_re_proposes(self):
        decision = decide_recovery(
            verdict=UNKNOWN,
            capability="windows.type_text",
            described_as="type the quoted text",
            approval_requirement="always",
            attempts=1,
            read_back_code="parking_brake_engaged",
        )
        assert decision.decision == ASK_FOR_CLARIFICATION
        assert decision.requires_new_authorization
        assert "parking_brake_engaged" in decision.reason

    def test_an_always_approval_capability_never_re_proposes_itself(self):
        decision = decide_recovery(
            verdict=FAILED,
            capability="windows.type_text",
            described_as="type the quoted text",
            approval_requirement="always",
            attempts=1,
        )
        assert decision.decision == STOP_AND_REPORT

    def test_a_plainly_failed_ordinary_step_may_be_re_proposed_with_new_authorization(self):
        decision = decide_recovery(
            verdict=FAILED,
            capability="windows.focus_window",
            described_as="bring notepad's window to the foreground",
            approval_requirement="required_autonomy_eligible",
            attempts=1,
        )
        assert decision.decision == RE_PROPOSE
        assert decision.requires_new_authorization

    def test_the_attempt_bound_abandons_rather_than_asking_the_machine_again(self):
        decision = decide_recovery(
            verdict=FAILED,
            capability="windows.focus_window",
            described_as="bring notepad's window to the foreground",
            approval_requirement="required_autonomy_eligible",
            attempts=MAX_STEP_ATTEMPTS,
        )
        assert decision.decision == ABANDON_STEP

    def test_a_caution_from_evidence_turns_a_re_proposal_into_a_question(self):
        """Evidence narrows. This is the one behaviour it is allowed to change."""
        decision = decide_recovery(
            verdict=FAILED,
            capability="windows.focus_window",
            described_as="bring notepad's window to the foreground",
            approval_requirement="required_autonomy_eligible",
            attempts=1,
            cautions=("that window has failed to focus before",),
        )
        assert decision.decision == ASK_FOR_CLARIFICATION
        assert "cannot authorize a retry" in decision.reason

    def test_there_is_no_decision_value_meaning_retry(self):
        from bartholomew.executive import recovery

        values = {
            getattr(recovery, name)
            for name in dir(recovery)
            if name.isupper() and isinstance(getattr(recovery, name), str)
        }
        assert not any("retry" in v for v in values)
        assert all(
            decide_recovery(
                verdict=verdict,
                capability="windows.focus_window",
                described_as="x",
                approval_requirement=approval,
                attempts=attempts,
            ).requires_new_authorization
            or decision_is_terminal(verdict, approval, attempts)
            for verdict in (FAILED, UNKNOWN)
            for approval in ("always", "required", "required_autonomy_eligible")
            for attempts in (1, MAX_STEP_ATTEMPTS)
        )


def decision_is_terminal(verdict, approval, attempts):
    decision = decide_recovery(
        verdict=verdict,
        capability="windows.focus_window",
        described_as="x",
        approval_requirement=approval,
        attempts=attempts,
    )
    return decision.decision in (ABANDON_STEP, STOP_AND_REPORT)


class TestTheExplanationNeverFabricatesSuccess:
    def test_an_unknown_step_is_reported_as_unverified_not_as_done(self):
        plan = _two_step_plan()
        plan.steps[0].status = StepStatus.UNKNOWN
        plan.steps[0].action_id = "act-1"
        plan.steps[0].verification = verify_step(
            capability="windows.focus_window",
            parameters={"app_id": "notepad"},
            device_status="succeeded",
            tenant_id=TENANT,
            device_id=DEVICE,
            requested_by=REQUESTER,
            read_back_port=_ReadBack(available=False, code="provider_unavailable"),
        )
        plan.status = plan_status(plan)
        text = explain_task(plan)
        assert "unverified" in text
        assert "not calling that success" in text
        assert "provider_unavailable" in text

    def test_a_waiting_step_says_nothing_has_run(self):
        plan = _two_step_plan()
        plan.steps[0].status = StepStatus.AWAITING_AUTHORIZATION
        plan.steps[0].action_id = "act-1"
        text = explain_task(plan)
        assert "nothing has run" in text
        assert "cannot approve these myself" in text

    def test_the_four_columns_are_distinguishable_in_the_account(self):
        plan = _two_step_plan()
        plan.steps[0].status = StepStatus.VERIFIED
        plan.steps[0].action_id = "act-1"
        plan.steps[0].verification = verify_step(
            capability="windows.focus_window",
            parameters={"app_id": "notepad"},
            device_status="succeeded",
            tenant_id=TENANT,
            device_id=DEVICE,
            requested_by=REQUESTER,
            read_back_port=_ReadBack(text="notepad is in front"),
        )
        plan.steps[1].status = StepStatus.AWAITING_AUTHORIZATION
        plan.steps[1].action_id = "act-2"
        text = explain_task(plan)
        assert "verified" in text
        assert "waiting for your approval" in text
        assert "act-1" in text and "act-2" in text

    def test_evidence_that_was_recalled_and_not_used_is_said_so(self):
        plan = build_plan(
            intent=parse_task("focus notepad"),
            tenant_id=TENANT,
            device_id=DEVICE,
            requested_by=REQUESTER,
            device=_device(),
            evidence=__import__(
                "bartholomew.executive.evidence",
                fromlist=["admit_evidence"],
            ).admit_evidence([{"content": "old advice", "validity": "revoked"}]),
        )
        assert "Recalled and not used" in explain_task(plan)

    def test_a_refused_plan_says_nothing_was_sent_to_the_computer(self):
        plan = build_plan(
            intent=parse_task("focus notepad"),
            tenant_id=TENANT,
            device_id=DEVICE,
            requested_by=REQUESTER,
            device=None,
        )
        plan.status = plan_status(plan)
        text = explain_task(plan)
        assert "not proposed" in text
