"""
R-EXEC02-2, the governed half: a proposal surfaced in conversation is
authorised — or withdrawn — through the approval authority that already exists,
and through nothing else.

Built on the *real* pieces, deliberately, because the thing this package must
prove is precisely the thing a mock would remove: the real chat turn, the real
`_CHAT_DISPATCH` table, the real Executive, the real action envelope, the real
`bartholomew/actuation/seam.py` approval authority, the real action store, the
real capability descriptors, the real device allowlists, the real Parking
Brake, and the real `MemoryStore`. The only stand-in anywhere is the model,
which is a fixed-payload `DeliberationPort` — the seam EXEC-01 declared, and
the same fixture shape `tests/test_exec02_conversational_executive.py` uses.

What each class proves:

  the presented proposal is approved             -> TestTheExactProposalIsApproved
  the presented proposal is rejected             -> TestTheExactProposalIsRejected
  a decision is bound to what was shown          -> TestBindingToWhatWasShown
  nothing pending, ambiguity, ownership          -> TestNothingSafelyIdentifiable
  stale, expired, cancelled, consumed, changed   -> TestStaleProposalsRefuse
  replay and double execution                    -> TestOneShot
  the Parking Brake outranks an approval         -> TestTheParkingBrakeIsAuthoritative
  action classes conversation may not authorise  -> TestSurfaceEligibility
  approval is not execution, failure is failure,
  and `unknown` is never success                 -> TestApprovalIsNotExecutionSuccess
  the audit separates the human decision from
  the model's reasoning                          -> TestTheAuditSeparatesAuthorityFromCognition
  conversational text that is not a decision     -> TestConversationStaysConversation
"""

from __future__ import annotations

import asyncio
import json

import pytest

from bartholomew.actuation import devices
from bartholomew.actuation import seam as action_seam
from bartholomew.actuation import store as action_store
from bartholomew.actuation.approval import (
    SURFACE_CONVERSATION,
    SURFACE_OPERATOR_CONSOLE,
)
from bartholomew.actuation.capabilities import (
    ALWAYS_APPROVAL,
    CONVERSATIONAL_APPROVAL_INELIGIBLE,
    CapabilityKind,
)
from bartholomew.actuation.parameters import (
    ApplicationAllowlist,
    FilesystemRootAllowlist,
    UrlDomainAllowlist,
)
from bartholomew.actuation.store import ActionState
from bartholomew.integration import conversational_approval as ca
from bartholomew.integration.conversational_executive import (
    install_conversational_executive,
)
from bartholomew.kernel import approval_intents as ai
from bartholomew.kernel import runtime_contract as rc
from bartholomew.kernel.runtime_contract import run_chat_through_runtime_contract

TENANT = "approval-tenant"
DEVICE = "approval-device"
REQUESTER = "taylor"

GOAL = "Open notepad for me."

#: A plan whose one step is `windows.launch_app` — an ordinary, approvable
#: capability. One step, because the Executive proposes only the first.
LAUNCH_ANSWER = json.dumps(
    {
        "objective": "an editor is open",
        "situation": "nothing is open",
        "sub_goals": ["an editable surface exists"],
        "steps": [
            {
                "capability": "windows.launch_app",
                "parameters": {"app_id": "notepad"},
                "purpose": "open a text editor",
                "necessary_because": "there is nowhere to write yet",
            },
        ],
        "clarification": None,
        "refusal": None,
        "confidence": "high",
    },
)

#: A plan whose first step is `windows.type_text` — `ApprovalRequirement.ALWAYS`,
#: and therefore not authorisable from the conversational surface.
TYPE_ANSWER = json.dumps(
    {
        "objective": "the note has content",
        "situation": "an editor is focused",
        "sub_goals": ["the content exists"],
        "steps": [
            {
                "capability": "windows.type_text",
                "parameters": {"text": "Shopping list"},
                "purpose": "write the heading",
                "necessary_because": "an empty note is not a list",
            },
        ],
        "clarification": None,
        "refusal": None,
        "confidence": "high",
    },
)


class _Port:
    def __init__(self, payload=LAUNCH_ANSWER):
        self.payload = payload
        self.prompts: list[str] = []

    def deliberate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.payload


class _Registry:
    def __init__(self, *enrolled):
        self._by_key = {(d.tenant_id, d.device_id): d for d in enrolled}

    def lookup(self, *, tenant_id, device_id):
        return self._by_key.get((tenant_id, device_id))


def _device():
    kinds = (
        CapabilityKind.LAUNCH_APP,
        CapabilityKind.FOCUS_WINDOW,
        CapabilityKind.TYPE_TEXT,
        CapabilityKind.OPEN_URL,
    )
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
    )


@pytest.fixture
def mock_config_files(tmp_path):
    (tmp_path / "kernel.yaml").write_text(
        'timezone: "Australia/Brisbane"\nloop_interval_seconds: 1\n',
    )
    (tmp_path / "persona.yaml").write_text('name: "Test Bartholomew"\n')
    (tmp_path / "policy.yaml").write_text("policies: []\n")
    (tmp_path / "drives.yaml").write_text("drives: []\n")
    return {
        "cfg_path": str(tmp_path / "kernel.yaml"),
        "persona_path": str(tmp_path / "persona.yaml"),
        "policy_path": str(tmp_path / "policy.yaml"),
        "drives_path": str(tmp_path / "drives.yaml"),
        "db_path": str(tmp_path / "approval.db"),
    }


@pytest.fixture
def daemon(mock_config_files):
    from bartholomew.executive import store as executive_store
    from bartholomew.kernel.daemon import KernelDaemon
    from bartholomew.kernel.memory_store import MemoryStore
    from bartholomew.orchestrator.safety import governance_store as gs

    db_path = mock_config_files["db_path"]
    asyncio.run(MemoryStore(db_path).init())
    gs.ensure_schema(db_path)
    action_store.ensure_schema(db_path)
    executive_store.ensure_schema(db_path)
    return KernelDaemon(**mock_config_files)


@pytest.fixture
def registry():
    reg = _Registry(_device())
    devices.install_registry(reg)
    yield reg
    devices.install_registry(None)


@pytest.fixture(autouse=True)
def armed_channel():
    """The arming window is a separate, coarser gate than anything this file
    is about. It is held open throughout so that no test here is accidentally
    passing for the wrong reason -- an unarmed channel refuses at dispatch,
    which would mask rather than prove the approval behaviour."""
    from bartholomew.actuation import arming

    arming.arm(
        tenant_id=TENANT,
        device_id=DEVICE,
        armed_by="test-fixture",
        reason="conversational approval suite",
    )
    yield
    arming.reset_for_tests()


@pytest.fixture
def port():
    return _Port()


@pytest.fixture
def enabled(daemon, port, registry):
    install_conversational_executive(
        daemon,
        tenant_id=TENANT,
        device_id=DEVICE,
        requested_by=REQUESTER,
    )
    daemon.deliberation_port = port
    return daemon


@pytest.fixture
def config(enabled):
    from bartholomew.integration import conversational_executive as ce

    return ce.resolve(enabled)


@pytest.fixture
def model():
    calls: list[str] = []

    async def respond(prompt: str) -> str:
        calls.append(prompt)
        return "a conversational reply"

    respond.calls = calls
    return respond


async def _say(daemon, model, text):
    return await run_chat_through_runtime_contract(daemon, text, model)


async def _propose(daemon, model, goal=GOAL):
    """Run the goal turn and return `(result, the one proposed action id)`."""
    result = await _say(daemon, model, goal)
    assert result.executive_action is not None, result.response
    assert result.executive_action["outcome"] == "proposed", result.executive_action
    ids = result.executive_action["proposed_action_ids"]
    assert len(ids) == 1
    return result, ids[0]


def _row(db_path, action_id):
    return action_store.get_action(db_path, tenant_id=TENANT, action_id=action_id)


async def _approvals(daemon):
    """Every approval record the authority wrote, as dicts."""
    from bartholomew.actuation.approval import KIND as APPROVAL_KIND

    rows = []
    for action in action_store.recent_actions(daemon.mem.db_path, tenant_id=TENANT, limit=50):
        action_id = action["action_id"] if isinstance(action, dict) else action.action_id
        row = await daemon.mem.get_memory(APPROVAL_KIND, f"{TENANT}::{action_id}")
        if row:
            rows.append(json.loads(row["value"]))
    return rows


async def _engage_brake(daemon, scope="skills"):
    from bartholomew.orchestrator.safety.governance_store import GovernanceStore

    GovernanceStore(daemon.mem.db_path).engage(scope, actor="operator", reason="halt")


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTheExactProposalIsApproved:
    async def test_a_goal_then_yes_approves_that_exact_action(self, enabled, model):
        _, action_id = await _propose(enabled, model)
        assert _row(enabled.mem.db_path, action_id).state is ActionState.PENDING_APPROVAL

        result = await _say(enabled, model, "yes")

        approval = result.approval_action
        assert approval is not None, result.response
        assert approval["outcome"] == ca.OUTCOME_APPROVED
        assert approval["action_id"] == action_id
        assert approval["human_decision_recorded"] is True
        assert _row(enabled.mem.db_path, action_id).state is ActionState.APPROVED

    async def test_the_authority_wrote_the_approval_not_this_package(self, enabled, model):
        """The approval on disk is the authority's own record, bound to the
        exact action, and it is the object dispatch will check."""
        _, action_id = await _propose(enabled, model)
        await _say(enabled, model, "go ahead")

        approvals = await _approvals(enabled)
        assert len(approvals) == 1
        record = approvals[0]
        assert record["action_id"] == action_id
        assert record["tenant_id"] == TENANT
        assert record["device_id"] == DEVICE
        assert record["capability"] == "windows.launch_app"
        assert record["approver"] == REQUESTER
        # Provenance, on the record: an audit can tell a conversational
        # decision from an operator-console one.
        assert record["surface"] == SURFACE_CONVERSATION
        # The person's own words are the evidence of the human act.
        assert "go ahead" in record["note"]

    async def test_the_approval_binds_to_the_parameters_that_were_shown(self, enabled, model):
        _, action_id = await _propose(enabled, model)
        shown = _row(enabled.mem.db_path, action_id).parameter_fingerprint
        await _say(enabled, model, "yes")
        assert (await _approvals(enabled))[0]["parameter_fingerprint"] == shown

    async def test_the_person_is_told_it_is_approved_and_has_not_run(self, enabled, model):
        await _propose(enabled, model)
        result = await _say(enabled, model, "yes")
        reply = result.response.lower()
        assert "approved" in reply
        assert "recording it is not running it" in reply
        # And it does not assert a fact about the machine it cannot know.
        assert "has not run" not in reply

    async def test_the_model_was_never_asked_whether_permission_was_granted(
        self,
        enabled,
        model,
        port,
    ):
        await _propose(enabled, model)
        before_model, before_port = len(model.calls), len(port.prompts)
        await _say(enabled, model, "yes")
        # Neither the conversational model nor the deliberation port is
        # consulted on the decision turn. The decision is the person's words
        # and a deterministic match, and nothing else.
        assert len(model.calls) == before_model
        assert len(port.prompts) == before_port


# ---------------------------------------------------------------------------
# Rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTheExactProposalIsRejected:
    async def test_no_withdraws_the_exact_action(self, enabled, model):
        _, action_id = await _propose(enabled, model)
        result = await _say(enabled, model, "no")

        assert result.approval_action["outcome"] == ca.OUTCOME_REJECTED
        assert result.approval_action["action_id"] == action_id
        assert _row(enabled.mem.db_path, action_id).state is ActionState.CANCELLED

    async def test_a_rejected_action_can_never_be_approved_afterwards(self, enabled, model):
        _, action_id = await _propose(enabled, model)
        await _say(enabled, model, "no")
        result = await _say(enabled, model, "yes")

        assert result.approval_action["outcome"] == ca.OUTCOME_STALE
        assert _row(enabled.mem.db_path, action_id).state is ActionState.CANCELLED
        assert await _approvals(enabled) == []

    async def test_withdrawing_an_already_leased_action_does_not_claim_nothing_happened(
        self,
        enabled,
        model,
        registry,
        config,
    ):
        """`store.mark_cancelled` accepts a leased action on purpose, and its own
        docstring says cancelling one "does not reach out and stop a device --
        nothing here can". So the withdrawal holds, but it did not *prevent*
        anything, and this surface must not imply that it did."""
        _, action_id = await _propose(enabled, model)
        await _say(enabled, model, "yes")
        await action_seam.run_action_dispatch_through_runtime_contract(
            enabled,
            tenant_id=TENANT,
            device_id=DEVICE,
            action_id=action_id,
            registry=registry,
        )
        assert _row(enabled.mem.db_path, action_id).state is ActionState.LEASED

        # The slot was consumed by the approval, so re-arm it: the person is
        # withdrawing the same proposal they were shown, just too late.
        await ca._write(
            enabled,
            ca.PresentedProposal(
                presentation_id="pres-late",
                tenant_id=TENANT,
                device_id=DEVICE,
                requested_by=REQUESTER,
                action_id=action_id,
                capability=_row(enabled.mem.db_path, action_id).capability,
                capability_version=1,
                parameter_fingerprint=_row(
                    enabled.mem.db_path,
                    action_id,
                ).parameter_fingerprint,
                action_expires_at=_row(enabled.mem.db_path, action_id).expires_at,
                presented_at="2026-09-19T00:00:00Z",
            ),
        )

        result = await ca.decide(enabled, config, ai.parse_decision("no"))

        assert result.outcome == ca.OUTCOME_REJECTED
        assert result.withdrawn_after_lease is True
        assert "can never run" not in result.reply
        assert "may have started" in result.reply
        assert _row(enabled.mem.db_path, action_id).state is ActionState.CANCELLED

    async def test_a_withdrawal_before_any_lease_may_still_promise_it_cannot_run(
        self,
        enabled,
        model,
        config,
    ):
        """Non-vacuity for the test above: the stronger sentence is still used
        where it is actually true."""
        await _propose(enabled, model)
        result = await ca.decide(enabled, config, ai.parse_decision("no"))
        assert result.withdrawn_after_lease is False
        assert "can never run" in result.reply

    async def test_the_decision_is_preserved_as_evidence_not_deleted(self, enabled, model):
        _, action_id = await _propose(enabled, model)
        await _say(enabled, model, "cancel that")

        record = await ca.load_presentation(enabled, rc._conversational_executive_config(enabled))
        assert record is not None, "a rejection must not erase what happened"
        assert record.state == ca.STATE_REJECTED
        assert record.decision["decision"] == ai.DECISION_REJECT
        assert record.decision["utterance"] == "cancel that"
        # And the action row itself carries the person's reason.
        assert "withdrawn in conversation" in _row(enabled.mem.db_path, action_id).state_reason


# ---------------------------------------------------------------------------
# Binding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestBindingToWhatWasShown:
    async def test_the_presentation_records_the_action_as_it_was_shown(self, enabled, model):
        _, action_id = await _propose(enabled, model)
        stored = _row(enabled.mem.db_path, action_id)
        record = await ca.load_presentation(enabled, rc._conversational_executive_config(enabled))

        assert record.action_id == action_id
        assert record.capability == stored.capability
        assert record.capability_version == stored.capability_version
        assert record.parameter_fingerprint == stored.parameter_fingerprint
        assert record.tenant_id == TENANT
        assert record.device_id == DEVICE
        assert record.requested_by == REQUESTER
        assert record.state == ca.STATE_AWAITING

    async def test_yes_cannot_approve_an_unrelated_pending_action(self, enabled, model, registry):
        """A second action exists and is pending. It was never shown in this
        conversation, so "yes" must not reach it."""
        _, shown_id = await _propose(enabled, model)

        other = await action_seam.run_action_request_through_runtime_contract(
            enabled,
            tenant_id=TENANT,
            device_id=DEVICE,
            requested_by="operator",
            capability="windows.focus_window",
            capability_version=1,
            parameters={"app_id": "notepad"},
            correlation_id="unrelated",
            registry=registry,
        )
        other_id = other.action.action_id
        assert _row(enabled.mem.db_path, other_id).state is ActionState.PENDING_APPROVAL

        await _say(enabled, model, "yes")

        assert _row(enabled.mem.db_path, shown_id).state is ActionState.APPROVED
        assert _row(enabled.mem.db_path, other_id).state is ActionState.PENDING_APPROVAL

    async def test_a_newer_proposal_supersedes_the_older_one(self, enabled, model):
        """One live presentation per conversation, so "yes" always means the
        proposal the person was last shown — never an older one."""
        _, first = await _propose(enabled, model)
        _, second = await _propose(enabled, model, "Open notepad for me again.")
        assert first != second

        await _say(enabled, model, "yes")

        assert _row(enabled.mem.db_path, second).state is ActionState.APPROVED
        # The older one is untouched: it was superseded, not approved.
        assert _row(enabled.mem.db_path, first).state is ActionState.PENDING_APPROVAL

    async def test_a_failed_re_arm_retires_the_older_proposal(self, enabled, model, config):
        """The binding failure this module's docstring claims to prevent.

        A is awaiting a decision. B is then put to the person, and arming B
        fails. A must not still be what "yes" means: the person last saw B, and
        authorising A instead would be an older proposal answering for a newer
        one. Every failure path in `present_proposal` retires the old slot before
        it can return, so the surface is left with nothing rather than with the
        wrong thing.
        """
        _, first = await _propose(enabled, model)
        assert (await ca.load_presentation(enabled, config)).action_id == first

        # Arming fails for the reason ambiguity always fails: two at once.
        armed, why_not = await ca.present_proposal(
            enabled,
            config,
            action_ids=["act-b1", "act-b2"],
            instruction="two things",
        )
        assert armed is None and "2 steps were proposed at once" in why_not

        retired = await ca.load_presentation(enabled, config)
        assert retired.state == ca.STATE_SUPERSEDED, "the older proposal is still live"

        result = await ca.decide(enabled, config, ai.parse_decision("yes"))
        assert result.outcome == ca.OUTCOME_STALE
        assert "replaced by a newer one" in result.reply
        assert _row(enabled.mem.db_path, first).state is ActionState.PENDING_APPROVAL
        assert await _approvals(enabled) == []

    async def test_a_re_arm_that_cannot_read_the_action_also_retires_the_older_one(
        self,
        enabled,
        model,
        config,
    ):
        """The same property on a different failure path, so the fix is not
        pinned to one branch."""
        _, first = await _propose(enabled, model)

        armed, why_not = await ca.present_proposal(
            enabled,
            config,
            action_ids=["act-does-not-exist"],
            instruction="a ghost",
        )
        assert armed is None and "could not be read back" in why_not
        assert (await ca.load_presentation(enabled, config)).state == ca.STATE_SUPERSEDED
        assert (
            await ca.decide(enabled, config, ai.parse_decision("yes"))
        ).outcome == ca.OUTCOME_STALE
        assert _row(enabled.mem.db_path, first).state is ActionState.PENDING_APPROVAL

    async def test_a_presentation_from_another_conversation_is_not_this_ones(
        self,
        enabled,
        model,
    ):
        """Ownership: the three dimensions this build actually has. A record
        whose tenant, device or requester does not match the live activation is
        not resolvable from here."""
        from bartholomew.integration import conversational_executive as ce

        await _propose(enabled, model)
        assert await ca.load_presentation(enabled, ce.resolve(enabled)) is not None

        for other in (
            ce.ConversationalExecutive(
                tenant_id="somebody-else",
                device_id=DEVICE,
                requested_by=REQUESTER,
            ),
            ce.ConversationalExecutive(
                tenant_id=TENANT,
                device_id="another-pc",
                requested_by=REQUESTER,
            ),
            ce.ConversationalExecutive(
                tenant_id=TENANT,
                device_id=DEVICE,
                requested_by="someone-else",
            ),
        ):
            assert await ca.load_presentation(enabled, other) is None, other

    async def test_another_identity_cannot_consume_the_decision(self, enabled, model):
        from bartholomew.integration import conversational_executive as ce

        _, action_id = await _propose(enabled, model)
        intruder = ce.ConversationalExecutive(
            tenant_id=TENANT,
            device_id=DEVICE,
            requested_by="someone-else",
        )
        result = await ca.decide(
            enabled,
            intruder,
            ai.parse_decision("yes"),
        )
        assert result.outcome == ca.OUTCOME_NOT_ARMED
        assert _row(enabled.mem.db_path, action_id).state is ActionState.PENDING_APPROVAL


# ---------------------------------------------------------------------------
# Nothing safely identifiable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestNothingSafelyIdentifiable:
    async def test_yes_with_nothing_pending_approves_nothing(self, enabled, model):
        result = await _say(enabled, model, "yes")
        assert result.approval_action["outcome"] == ca.OUTCOME_NOT_ARMED
        assert result.approval_action["human_decision_recorded"] is False
        assert "nothing" in result.response.lower()
        assert action_store.recent_actions(enabled.mem.db_path, tenant_id=TENANT, limit=10) == []

    async def test_yes_with_a_pending_action_nobody_showed_approves_nothing(
        self,
        enabled,
        model,
        registry,
    ):
        """The strongest form of the binding rule: something *is* pending, and
        "yes" still approves nothing, because it was never presented here."""
        created = await action_seam.run_action_request_through_runtime_contract(
            enabled,
            tenant_id=TENANT,
            device_id=DEVICE,
            requested_by="operator",
            capability="windows.launch_app",
            capability_version=1,
            parameters={"app_id": "notepad"},
            correlation_id="console-only",
            registry=registry,
        )
        action_id = created.action.action_id

        result = await _say(enabled, model, "yes")

        assert result.approval_action["outcome"] == ca.OUTCOME_NOT_ARMED
        assert _row(enabled.mem.db_path, action_id).state is ActionState.PENDING_APPROVAL

    async def test_more_than_one_proposal_at_once_arms_nothing(self, enabled, model, config):
        """Ambiguity fails safe: the seam declines rather than choosing."""
        record, why_not = await ca.present_proposal(
            enabled,
            config,
            action_ids=["act-a", "act-b"],
            instruction="do two things",
        )
        assert record is None
        assert "2 steps were proposed at once" in why_not

        result = await ca.decide(enabled, config, ai.parse_decision("yes"))
        assert result.outcome == ca.OUTCOME_NOT_ARMED

    async def test_the_ambiguity_renderer_never_picks_one(self):
        text = ai.render_ambiguous(3).lower()
        assert "can't tell which" in text
        assert "haven't approved anything" in text


# ---------------------------------------------------------------------------
# Staleness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestStaleProposalsRefuse:
    async def test_a_proposal_changed_after_presentation_is_refused(self, enabled, model):
        """The defence the operator console does not need and this surface
        does: the person approves the proposal they *read*."""
        _, action_id = await _propose(enabled, model)

        from bartholomew.kernel.db_ctx import wal_db

        with wal_db(enabled.mem.db_path, label="tamper") as conn:
            conn.execute(
                "UPDATE windows_action_requests SET parameter_fingerprint = ? "
                "WHERE tenant_id = ? AND action_id = ?",
                ("a-different-fingerprint", TENANT, action_id),
            )
            conn.commit()

        result = await _say(enabled, model, "yes")

        assert result.approval_action["outcome"] == ca.OUTCOME_STALE
        assert "changed since I described it" in result.response
        assert _row(enabled.mem.db_path, action_id).state is ActionState.PENDING_APPROVAL
        assert await _approvals(enabled) == []

    async def test_an_expired_proposal_is_refused(self, enabled, model):
        _, action_id = await _propose(enabled, model)

        from bartholomew.kernel.db_ctx import wal_db

        with wal_db(enabled.mem.db_path, label="expire") as conn:
            conn.execute(
                "UPDATE windows_action_requests SET expires_at = ? "
                "WHERE tenant_id = ? AND action_id = ?",
                ("2000-01-01T00:00:00Z", TENANT, action_id),
            )
            conn.commit()

        result = await _say(enabled, model, "yes")

        assert result.approval_action["outcome"] == ca.OUTCOME_STALE
        assert "expired" in result.response.lower()
        assert await _approvals(enabled) == []

    async def test_a_cancelled_proposal_is_refused(self, enabled, model):
        _, action_id = await _propose(enabled, model)
        await action_seam.cancel_action_through_runtime_contract(
            enabled,
            tenant_id=TENANT,
            action_id=action_id,
            cancelled_by="operator",
            reason="withdrawn at the console",
        )

        result = await _say(enabled, model, "yes")

        assert result.approval_action["outcome"] == ca.OUTCOME_STALE
        assert _row(enabled.mem.db_path, action_id).state is ActionState.CANCELLED
        assert await _approvals(enabled) == []

    async def test_an_already_approved_proposal_is_refused(self, enabled, model, registry):
        """Somebody approved it at the console in the meantime. There is
        nothing left awaiting approval, and the conversation says so."""
        _, action_id = await _propose(enabled, model)
        console = await action_seam.grant_action_approval(
            enabled,
            tenant_id=TENANT,
            action_id=action_id,
            approver="operator",
            registry=registry,
        )
        assert console.governance_allowed

        result = await _say(enabled, model, "yes")

        assert result.approval_action["outcome"] == ca.OUTCOME_STALE
        # The console's approval is the one on file, unchanged and unreplaced.
        approvals = await _approvals(enabled)
        assert len(approvals) == 1
        assert approvals[0]["approver"] == "operator"
        assert approvals[0]["surface"] == SURFACE_OPERATOR_CONSOLE

    async def test_a_proposal_that_no_longer_exists_is_refused(self, enabled, model):
        _, action_id = await _propose(enabled, model)

        from bartholomew.kernel.db_ctx import wal_db

        with wal_db(enabled.mem.db_path, label="delete") as conn:
            conn.execute(
                "DELETE FROM windows_action_requests WHERE tenant_id = ? AND action_id = ?",
                (TENANT, action_id),
            )
            conn.commit()

        result = await _say(enabled, model, "yes")
        assert result.approval_action["outcome"] == ca.OUTCOME_STALE
        assert "no longer exists" in result.response


# ---------------------------------------------------------------------------
# One-shot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestOneShot:
    async def test_saying_yes_twice_approves_once(self, enabled, model):
        _, action_id = await _propose(enabled, model)

        first = await _say(enabled, model, "yes")
        second = await _say(enabled, model, "yes")

        assert first.approval_action["outcome"] == ca.OUTCOME_APPROVED
        assert second.approval_action["outcome"] == ca.OUTCOME_STALE
        assert second.approval_action["human_decision_recorded"] is False
        assert len(await _approvals(enabled)) == 1

    async def test_a_replayed_decision_cannot_reopen_a_consumed_approval(
        self,
        enabled,
        model,
        config,
    ):
        """Replay the *same* decision object, five times, straight at the
        adapter. The authority's conditional UPDATE is what refuses it — not
        this package's bookkeeping."""
        _, action_id = await _propose(enabled, model)
        decision = ai.parse_decision("yes")

        outcomes = [(await ca.decide(enabled, config, decision)).outcome for _ in range(5)]

        assert outcomes[0] == ca.OUTCOME_APPROVED
        assert set(outcomes[1:]) == {ca.OUTCOME_STALE}
        assert len(await _approvals(enabled)) == 1

    async def test_the_authority_refuses_even_if_the_bookkeeping_is_lost(
        self,
        enabled,
        model,
        config,
    ):
        """The presentation record is convenience, not consumption. Re-arm it
        by hand after an approval — the simulation of a lost bookkeeping write
        — and the authority still refuses the second "yes"."""
        _, action_id = await _propose(enabled, model)
        record = await ca.load_presentation(enabled, config)
        assert (await ca.decide(enabled, config, ai.parse_decision("yes"))).outcome == (
            ca.OUTCOME_APPROVED
        )

        await ca._write(enabled, record)  # the slot is armed again
        again = await ca.decide(enabled, config, ai.parse_decision("yes"))

        assert again.outcome == ca.OUTCOME_STALE
        assert "approved" in (again.reason or "")
        assert len(await _approvals(enabled)) == 1

    async def test_a_forged_presentation_buys_only_the_right_to_be_refused(
        self,
        enabled,
        model,
        config,
        registry,
    ):
        """Write a presentation naming an action nobody was shown. The
        authority still decides, and it still holds — but note that this is a
        *pending* action, so the honest statement is narrower than "it is
        refused": forging the pointer gets you exactly what the operator
        console already offers, and no more."""
        created = await action_seam.run_action_request_through_runtime_contract(
            enabled,
            tenant_id=TENANT,
            device_id=DEVICE,
            requested_by="operator",
            capability="windows.launch_app",
            capability_version=1,
            parameters={"app_id": "notepad"},
            correlation_id="console-only",
            registry=registry,
        )
        stored = _row(enabled.mem.db_path, created.action.action_id)

        forged = ca.PresentedProposal(
            presentation_id="pres-forged",
            tenant_id=TENANT,
            device_id=DEVICE,
            requested_by=REQUESTER,
            action_id=stored.action_id,
            capability=stored.capability,
            capability_version=stored.capability_version,
            # The fingerprint is deliberately wrong: a forger who did not see
            # the proposal cannot state what it contained.
            parameter_fingerprint="invented",
            action_expires_at=stored.expires_at,
            presented_at=stored.issued_at,
        )
        await ca._write(enabled, forged)

        result = await ca.decide(enabled, config, ai.parse_decision("yes"))
        assert result.outcome == ca.OUTCOME_STALE
        assert _row(enabled.mem.db_path, stored.action_id).state is (ActionState.PENDING_APPROVAL)


# ---------------------------------------------------------------------------
# Parking Brake
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTheParkingBrakeIsAuthoritative:
    async def test_a_proposal_made_before_the_brake_cannot_be_approved_after_it(
        self,
        enabled,
        model,
    ):
        """The exact sequence the brief names: presented while clear,
        authorised while engaged. An approval is never a brake override."""
        _, action_id = await _propose(enabled, model)
        await _engage_brake(enabled)

        result = await _say(enabled, model, "yes")

        # Chat's own Governance stage fails closed on `skills` before the
        # dispatch table is reached at all, so the turn never gets as far as
        # the decision — which is the *more* restrictive outcome, not a
        # weaker one. Either way nothing is approved.
        assert result.governance_allowed is False
        assert _row(enabled.mem.db_path, action_id).state is ActionState.PENDING_APPROVAL
        assert await _approvals(enabled) == []

    async def test_the_authority_itself_refuses_an_approval_under_the_brake(
        self,
        enabled,
        model,
        config,
    ):
        """And the same, one layer down, with chat's own gate taken out of the
        picture — so the refusal is demonstrably the actuation brake's and not
        an artefact of the chat seam."""
        _, action_id = await _propose(enabled, model)
        await _engage_brake(enabled, scope=action_seam.ACTUATION_BRAKE_SCOPE)

        result = await ca.decide(enabled, config, ai.parse_decision("yes"))

        assert result.outcome == ca.OUTCOME_BRAKE
        assert result.decision_recorded is False
        assert "Parking Brake" in result.reply
        assert _row(enabled.mem.db_path, action_id).state is ActionState.PENDING_APPROVAL
        assert await _approvals(enabled) == []

    async def test_a_brake_engaged_after_approval_still_stops_the_action(
        self,
        enabled,
        model,
        registry,
    ):
        """Approved, then braked. The action is approved and still cannot be
        dispatched: the second brake read, immediately before the lease, is
        the load-bearing one."""
        _, action_id = await _propose(enabled, model)
        assert (await _say(enabled, model, "yes")).approval_action["outcome"] == (
            ca.OUTCOME_APPROVED
        )
        await _engage_brake(enabled, scope=action_seam.ACTUATION_BRAKE_SCOPE)

        dispatched = await action_seam.run_action_dispatch_through_runtime_contract(
            enabled,
            tenant_id=TENANT,
            device_id=DEVICE,
            action_id=action_id,
            registry=registry,
        )

        assert dispatched.governance_allowed is False
        assert dispatched.category.value == "parking_brake"
        assert _row(enabled.mem.db_path, action_id).state is ActionState.APPROVED

    async def test_withdrawing_is_still_possible_while_braked(self, enabled, model, config):
        """A halt that stopped somebody withdrawing a pending action would be
        a halt that made things less safe. The cancel seam is deliberately not
        brake-gated, and this surface inherits that."""
        _, action_id = await _propose(enabled, model)
        await _engage_brake(enabled, scope=action_seam.ACTUATION_BRAKE_SCOPE)

        result = await ca.decide(enabled, config, ai.parse_decision("no"))

        assert result.outcome == ca.OUTCOME_REJECTED
        assert _row(enabled.mem.db_path, action_id).state is ActionState.CANCELLED


# ---------------------------------------------------------------------------
# Surface eligibility
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSurfaceEligibility:
    async def test_the_ineligible_set_is_the_always_approval_set(self):
        """Derived, not written twice. This is a restatement of an existing
        governance distinction, not a new one, and it narrows rather than
        widens what conversation can reach."""
        assert CONVERSATIONAL_APPROVAL_INELIGIBLE == ALWAYS_APPROVAL
        assert CapabilityKind.TYPE_TEXT in CONVERSATIONAL_APPROVAL_INELIGIBLE
        assert CapabilityKind.CLIPBOARD_READ in CONVERSATIONAL_APPROVAL_INELIGIBLE
        assert CapabilityKind.ACCESSIBILITY_ACTION in CONVERSATIONAL_APPROVAL_INELIGIBLE
        assert CapabilityKind.LAUNCH_APP not in CONVERSATIONAL_APPROVAL_INELIGIBLE

    async def test_the_authority_refuses_an_ineligible_class_from_conversation(
        self,
        enabled,
        registry,
    ):
        """Asserted against `grant_action_approval` directly, because the
        authority is the enforcement point. A caller that skipped the adapter
        entirely would still be refused."""
        created = await action_seam.run_action_request_through_runtime_contract(
            enabled,
            tenant_id=TENANT,
            device_id=DEVICE,
            requested_by=REQUESTER,
            capability="windows.type_text",
            capability_version=1,
            parameters={"text": "Shopping list"},
            correlation_id="typing",
            registry=registry,
        )
        action_id = created.action.action_id

        refused = await action_seam.grant_action_approval(
            enabled,
            tenant_id=TENANT,
            action_id=action_id,
            approver=REQUESTER,
            surface=SURFACE_CONVERSATION,
            registry=registry,
        )

        assert refused.governance_allowed is False
        assert "cannot be authorised in conversation" in refused.reason
        assert _row(enabled.mem.db_path, action_id).state is ActionState.PENDING_APPROVAL

    async def test_the_same_action_is_still_approvable_at_the_console(
        self,
        enabled,
        registry,
    ):
        """The restriction is about the surface, not about the action. Nothing
        that could be approved before this package can be approved less."""
        created = await action_seam.run_action_request_through_runtime_contract(
            enabled,
            tenant_id=TENANT,
            device_id=DEVICE,
            requested_by=REQUESTER,
            capability="windows.type_text",
            capability_version=1,
            parameters={"text": "Shopping list"},
            correlation_id="typing",
            registry=registry,
        )
        granted = await action_seam.grant_action_approval(
            enabled,
            tenant_id=TENANT,
            action_id=created.action.action_id,
            approver="operator",
            registry=registry,
        )
        assert granted.governance_allowed is True

    async def test_conversation_refuses_it_truthfully_and_names_the_console(
        self,
        enabled,
        model,
        registry,
    ):
        enabled.deliberation_port = _Port(TYPE_ANSWER)
        _, action_id = await _propose(enabled, model, "Write a shopping list in my note.")

        result = await _say(enabled, model, "yes")

        assert result.approval_action["outcome"] == ca.OUTCOME_INELIGIBLE
        assert "operator console" in result.response
        assert _row(enabled.mem.db_path, action_id).state is ActionState.PENDING_APPROVAL
        assert await _approvals(enabled) == []

    async def test_an_unknown_surface_is_refused_rather_than_recorded(
        self,
        enabled,
        registry,
    ):
        created = await action_seam.run_action_request_through_runtime_contract(
            enabled,
            tenant_id=TENANT,
            device_id=DEVICE,
            requested_by=REQUESTER,
            capability="windows.launch_app",
            capability_version=1,
            parameters={"app_id": "notepad"},
            correlation_id="unknown-surface",
            registry=registry,
        )
        refused = await action_seam.grant_action_approval(
            enabled,
            tenant_id=TENANT,
            action_id=created.action.action_id,
            approver=REQUESTER,
            surface="a-surface-nobody-built",
            registry=registry,
        )
        assert refused.governance_allowed is False
        assert "not a surface" in refused.reason


# ---------------------------------------------------------------------------
# Approval is not execution success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestApprovalIsNotExecutionSuccess:
    async def test_approving_executes_nothing(self, enabled, model):
        _, action_id = await _propose(enabled, model)
        await _say(enabled, model, "yes")

        row = _row(enabled.mem.db_path, action_id)
        assert row.state is ActionState.APPROVED
        assert row.lease_count == 0
        assert row.leased_at is None
        assert row.terminal_at is None
        assert (
            action_store.results_for(
                enabled.mem.db_path,
                tenant_id=TENANT,
                action_id=action_id,
            )
            == []
        )

    async def test_the_record_never_claims_execution_or_verification(self, enabled, model):
        await _propose(enabled, model)
        result = await _say(enabled, model, "yes")
        assert result.approval_action["executed"] is False
        assert result.approval_action["verified"] is False

    async def test_the_surface_distinguishes_every_stage(self, enabled, model, registry):
        """Proposed, awaiting approval, approved, started, result — five
        different sentences for five different states, and never one for
        another."""
        _, action_id = await _propose(enabled, model)
        assert "waiting for your approval" in (await _say(enabled, model, "did it work?")).response

        await _say(enabled, model, "yes")
        assert "has not run yet" in (await _say(enabled, model, "did it work?")).response
        # The *status* read may say this: it is read from the state column, not
        # asserted from the fact that an approval succeeded.

        await action_seam.run_action_dispatch_through_runtime_contract(
            enabled,
            tenant_id=TENANT,
            device_id=DEVICE,
            action_id=action_id,
            registry=registry,
        )
        assert "running now" in (await _say(enabled, model, "did it work?")).response

    async def _finish(self, enabled, registry, action_id, status, category=None):
        from bartholomew.actuation.request import to_iso, utc_now

        return await action_seam.record_action_result_through_runtime_contract(
            enabled,
            tenant_id=TENANT,
            device_id=DEVICE,
            action_id=action_id,
            status=status,
            error_category=category,
            detail="",
            evidence={},
            observed_at=to_iso(utc_now()),
        )

    async def _through_to_execution(self, enabled, model, registry):
        _, action_id = await _propose(enabled, model)
        await _say(enabled, model, "yes")
        await action_seam.run_action_dispatch_through_runtime_contract(
            enabled,
            tenant_id=TENANT,
            device_id=DEVICE,
            action_id=action_id,
            registry=registry,
        )
        return action_id

    async def test_an_execution_failure_is_reported_as_failure(self, enabled, model, registry):
        action_id = await self._through_to_execution(enabled, model, registry)
        await self._finish(enabled, registry, action_id, "failed", "os_call_failed")

        reply = (await _say(enabled, model, "did it work?")).response.lower()
        assert "did not take effect" in reply
        assert "nothing changed" in reply
        assert "succeeded" not in reply

    async def test_an_unverifiable_execution_stays_unknown(self, enabled, model, registry):
        """The invariant the whole result vocabulary exists for. A device that
        reported it could not observe its own effect must never be rendered as
        success on the conversational surface either."""
        action_id = await self._through_to_execution(enabled, model, registry)
        await self._finish(enabled, registry, action_id, "unknown", "effect_unverifiable")
        assert _row(enabled.mem.db_path, action_id).state is ActionState.UNKNOWN

        reply = (await _say(enabled, model, "did it work?")).response.lower()
        assert "could not observe" in reply
        assert "i do not know whether it worked" in reply
        for claim in ("it worked.", "succeeded", "all done", "that's done"):
            assert claim not in reply

    async def test_a_verified_success_is_the_only_thing_reported_as_done(
        self,
        enabled,
        model,
        registry,
    ):
        action_id = await self._through_to_execution(enabled, model, registry)
        await self._finish(enabled, registry, action_id, "succeeded")

        reply = (await _say(enabled, model, "did it work?")).response.lower()
        assert "the effect was observed" in reply

    async def test_asking_what_happened_authorises_nothing(self, enabled, model):
        _, action_id = await _propose(enabled, model)
        await _say(enabled, model, "did it work?")
        assert _row(enabled.mem.db_path, action_id).state is ActionState.PENDING_APPROVAL
        assert await _approvals(enabled) == []


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTheAuditSeparatesAuthorityFromCognition:
    async def test_the_two_records_are_separate_rows_not_one(self, enabled, model):
        """ "The model suggested this" and "the user authorised this" must never
        be the same claim."""
        goal_turn, action_id = await _propose(enabled, model)
        decision_turn = await _say(enabled, model, "yes")

        # The Executive/cognition record: what was reasoned, and explicitly
        # that nothing was executed or authorised on that turn.
        assert goal_turn.executive_action["deliberated"] is True
        assert goal_turn.executive_action["executed"] is False
        assert goal_turn.approval_action is None

        # The human authority record: a separate turn, a separate field.
        assert decision_turn.executive_action is None
        assert decision_turn.approval_action["human_decision_recorded"] is True
        assert decision_turn.approval_action["decision"] == ai.DECISION_APPROVE

    async def test_the_chain_can_be_reconstructed_end_to_end(self, enabled, model, registry):
        """proposal -> human decision -> governance decision -> execution ->
        verification, each from a durable record, each naming the same action."""
        _, action_id = await _propose(enabled, model)
        config = rc._conversational_executive_config(enabled)

        # 1. what the person was shown
        presented = await ca.load_presentation(enabled, config)
        assert presented.action_id == action_id

        # 2. the human authority decision, verbatim
        await _say(enabled, model, "go ahead")
        decided = await ca.load_presentation(enabled, config)
        assert decided.decision["utterance"] == "go ahead"
        assert decided.state == ca.STATE_APPROVED

        # 3. Governance's own decision, written by the authority
        approval = (await _approvals(enabled))[0]
        assert approval["action_id"] == action_id
        assert approval["surface"] == SURFACE_CONVERSATION
        assert approval["approver"] == REQUESTER

        # 4. execution, and 5. its independently-recorded result
        await action_seam.run_action_dispatch_through_runtime_contract(
            enabled,
            tenant_id=TENANT,
            device_id=DEVICE,
            action_id=action_id,
            registry=registry,
        )
        from bartholomew.actuation.request import to_iso, utc_now

        await action_seam.record_action_result_through_runtime_contract(
            enabled,
            tenant_id=TENANT,
            device_id=DEVICE,
            action_id=action_id,
            status="succeeded",
            error_category=None,
            detail="",
            evidence={},
            observed_at=to_iso(utc_now()),
        )
        results = action_store.results_for(
            enabled.mem.db_path,
            tenant_id=TENANT,
            action_id=action_id,
        )
        assert [r["status"] for r in results] == ["succeeded"]

    async def test_the_reflection_carries_the_human_decision_separately(self, enabled, model):
        from bartholomew.kernel.db_ctx import wal_db
        from bartholomew.kernel.reflection import REFLECTION_KIND

        _, action_id = await _propose(enabled, model)
        await _say(enabled, model, "yes")

        with wal_db(enabled.mem.db_path, label="audit") as conn:
            rows = conn.execute(
                "SELECT content, meta FROM reflections WHERE kind = ? ORDER BY id",
                (REFLECTION_KIND,),
            ).fetchall()
        metas = [json.loads(r[1] or "{}") for r in rows]

        # `ActionReflection.to_memory_row` flattens `details` into `meta`.
        approvals = [m for m in metas if m.get("approval_surface") == SURFACE_CONVERSATION]
        assert approvals, "the authority's own reflection must name the surface"
        assert approvals[0]["action_id"] == action_id
        assert approvals[0]["approver"] == REQUESTER
        assert approvals[0]["outcome"] == "approved"
        assert approvals[0]["action"] == action_seam.ACTION_KIND_APPROVE

        # And the chat turn's own Reflection carries the human decision under
        # its own key, beside -- never inside -- the Executive's reasoning.
        chat = [m for m in metas if m.get("approval_action")]
        assert chat, "the chat Reflection must record the human decision separately"
        assert chat[0]["approval_action"]["human_decision_recorded"] is True
        assert chat[0]["approval_action"]["executed"] is False
        # The goal turn's cognition record is a *different* row. "The model
        # suggested this" and "the user authorised this" are never one claim.
        assert "executive_action" not in chat[0]
        cognition = [m for m in metas if m.get("executive_action")]
        assert cognition and "approval_action" not in cognition[0]

    async def test_an_approval_granted_before_this_field_existed_still_reads(self):
        """`surface` defaults to the operator console on an older record,
        because that is what those approvals actually were."""
        from bartholomew.actuation.approval import ActionApproval

        legacy = ActionApproval.from_dict(
            {
                "action_id": "a",
                "tenant_id": "t",
                "device_id": "d",
                "capability": "windows.launch_app",
                "capability_version": 1,
                "parameter_fingerprint": "f",
                "approver": "p",
                "granted_at": "2026-01-01T00:00:00Z",
                "expires_at": "2026-01-01T00:15:00Z",
            },
        )
        assert legacy.surface == SURFACE_OPERATOR_CONSOLE
        assert legacy.validate() == []


# ---------------------------------------------------------------------------
# Conversation stays conversation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestConversationStaysConversation:
    async def test_discussing_the_proposal_does_not_approve_it(self, enabled, model):
        _, action_id = await _propose(enabled, model)

        for utterance in (
            "What is that step actually going to do?",
            "Why notepad and not wordpad?",
            "If I say yes, will it close my other windows?",
            "That sounds reasonable I suppose",
        ):
            result = await _say(enabled, model, utterance)
            assert result.approval_action is None, utterance
            assert _row(enabled.mem.db_path, action_id).state is (
                ActionState.PENDING_APPROVAL
            ), utterance
        assert await _approvals(enabled) == []

    async def test_a_runtime_that_never_enabled_this_never_runs_the_recogniser(
        self,
        daemon,
        model,
        monkeypatch,
    ):
        """Default-off is preserved exactly: no activation, no recogniser call,
        no read, and the same conversational turn as before this package."""
        calls: list[str] = []
        real = ai.parse_decision
        monkeypatch.setattr(
            ai,
            "parse_decision",
            lambda text: (calls.append(text), real(text))[1],
        )

        result = await _say(daemon, model, "yes")

        assert calls == []
        assert result.approval_action is None
        assert result.response == "a conversational reply"

    async def test_an_explicit_instruction_still_reaches_its_own_recogniser(
        self,
        enabled,
        model,
    ):
        result = await _say(enabled, model, "add a task to ring the roofer")
        assert result.approval_action is None
        assert result.task_action is not None
