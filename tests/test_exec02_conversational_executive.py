"""
EXEC-02: an outcome-level goal typed into ordinary conversation reaches the
Executive that already exists, and stops where it is supposed to stop.

This is the package's vertical slice. It is deliberately built on the *real*
pieces: the real `run_chat_through_runtime_contract()` chat turn, the real
`_CHAT_DISPATCH` table, the real `bartholomew/executive/` seam, the real
capability catalogue, the real parameter validators, the real device
allowlists, the real action envelope, the real Parking Brake and the real
approval requirement. The only stand-in is the model, which is a fixed-payload
`DeliberationPort` so the test is deterministic -- exactly the seam EXEC-01
already declared, and the same fixture shape
`tests/test_exec01_vertical_slice.py` uses.

The ten things the package brief requires proved, and where each lives:

  1. deterministic commands keep their existing path  -> TestDeterministicDispatchIsUnchanged
  2. ordinary conversation stays conversation          -> TestOrdinaryConversationIsUntouched
  3. a qualifying goal reaches EXEC-01 deliberation    -> TestAGoalReachesTheExistingDeliberation
  4. it uses the existing TaskIntent/plan contracts    -> TestItUsesTheExistingContracts
  5. governance still decides                          -> TestGovernanceStillDecides
  6. Parking Brake and denial paths stay authoritative -> TestTheParkingBrakeIsAuthoritative
  7. a plan cannot bypass approval                     -> TestApprovalCannotBeBypassed
  8. failure degrades safely and truthfully            -> TestFailureDegradesSafely
  9. disabled deliberation preserves chat behaviour    -> TestDisabledByDefault
 10. nothing is reported complete without execution
     and independent verification                     -> TestNothingIsReportedComplete
"""

from __future__ import annotations

import asyncio
import json
import pathlib

import pytest

from bartholomew.actuation import devices
from bartholomew.actuation.capabilities import CapabilityKind
from bartholomew.actuation.parameters import (
    ApplicationAllowlist,
    FilesystemRootAllowlist,
    UrlDomainAllowlist,
)
from bartholomew.integration.conversational_executive import (
    ConversationalExecutive,
    install_conversational_executive,
)
from bartholomew.kernel import goal_intents
from bartholomew.kernel import runtime_contract as rc
from bartholomew.kernel.runtime_contract import run_chat_through_runtime_contract

TENANT = "exec02-tenant"
DEVICE = "exec02-device"
REQUESTER = "chat"

#: The brief's own sentence. It names an outcome. It names no capability, no
#: application, no step and no count of steps.
GOAL = "Start a shopping list for me."

#: Ordinary conversation, which must stay ordinary conversation.
SMALL_TALK = "How are you today?"

#: An explicit task-control instruction, which `_handle_task_intent` has owned
#: since long before EXEC-02 and must continue to own.
EXPLICIT_TASK = "add a task to ring the roofer"


COGNITION_ANSWER = json.dumps(
    {
        "objective": "an editable note is open, focused, and has the list started in it",
        "situation": "no editor appears to be open yet",
        "sub_goals": ["an editable surface exists", "the list has its first content"],
        "steps": [
            {
                "capability": "windows.launch_app",
                "parameters": {"app_id": "notepad"},
                "purpose": "open a text editor to hold the shopping list",
                "necessary_because": "there is nowhere to write the list yet",
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
    """A `DeliberationPort`. Records what it was asked; answers a fixed payload."""

    def __init__(self, payload=COGNITION_ANSWER, raises=None):
        self.payload = payload
        self.raises = raises
        self.prompts: list[str] = []

    def deliberate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.raises is not None:
            raise self.raises
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
            {
                "notepad": "C:\\Windows\\System32\\notepad.exe",
                "wordpad": "C:\\Program Files\\Windows NT\\Accessories\\wordpad.exe",
            },
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
        "db_path": str(tmp_path / "exec02.db"),
    }


@pytest.fixture
def daemon(mock_config_files):
    """A real KernelDaemon with the stores the governed path actually reads."""
    from bartholomew.actuation import store as action_store
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


@pytest.fixture
def port():
    return _Port()


@pytest.fixture
def enabled(daemon, port, registry):
    """The explicit activation. Nothing installs itself."""
    install_conversational_executive(
        daemon,
        tenant_id=TENANT,
        device_id=DEVICE,
        requested_by=REQUESTER,
    )
    daemon.deliberation_port = port
    return daemon


@pytest.fixture
def model():
    """The ordinary conversational model. Records whether it was consulted."""
    calls: list[str] = []

    async def respond(prompt: str) -> str:
        calls.append(prompt)
        return "a conversational reply"

    respond.calls = calls
    return respond


def _actions(db_path):
    from bartholomew.kernel.db_ctx import wal_db

    with wal_db(db_path, label="exec02_count") as conn:
        return conn.execute(
            "SELECT action_id, capability, state FROM windows_action_requests ORDER BY id",
        ).fetchall()


# ---------------------------------------------------------------------------
# 9. Disabled is the default, and the default is exactly today's behaviour.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDisabledByDefault:
    async def test_a_goal_stays_conversation_when_nothing_was_enabled(self, daemon, model):
        """No activation installed -- so the same sentence reaches the model."""
        result = await run_chat_through_runtime_contract(daemon, GOAL, model)
        assert result.executive_action is None
        assert result.response == "a conversational reply"
        assert model.calls, "the model must still be consulted"

    async def test_the_recogniser_is_not_even_run_when_disabled(self, daemon, model, monkeypatch):
        """Three gates, and the cheap two come first: an unconfigured runtime
        pays nothing at all for EXEC-02 existing."""
        calls = []
        monkeypatch.setattr(
            goal_intents,
            "parse_intent",
            lambda text: calls.append(text),
        )
        await run_chat_through_runtime_contract(daemon, GOAL, model)
        assert calls == []

    async def test_a_model_being_reachable_does_not_enable_anything(self, daemon, port, model):
        """Invariant 8. A deliberation port on the context is not consent for
        chat to start planning: the activation is a separate, explicit act."""
        daemon.deliberation_port = port
        result = await run_chat_through_runtime_contract(daemon, GOAL, model)
        assert result.executive_action is None
        assert port.prompts == []

    async def test_activation_refuses_to_be_half_specified(self, daemon):
        for bad in (
            {"tenant_id": "", "device_id": DEVICE},
            {"tenant_id": TENANT, "device_id": " "},
        ):
            with pytest.raises(ValueError):
                install_conversational_executive(daemon, requested_by=REQUESTER, **bad)


# ---------------------------------------------------------------------------
# 1. Existing deterministic dispatch is unchanged.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDeterministicDispatchIsUnchanged:
    async def test_the_executive_goal_entry_is_last_in_the_table(self):
        """Ordering is the whole of this handler's safety posture: every
        deterministic recogniser keeps first refusal on every utterance."""
        names = [name for name, _ in rc._CHAT_DISPATCH]
        assert names[-1] == rc._DISPATCH_EXECUTIVE_GOAL
        # R-EXEC02-2 added `approval` ahead of these. The property this test
        # exists for is unchanged and is asserted directly rather than through
        # a fixed index: the three deterministic instruction recognisers keep
        # their relative order, and every one of them still gets first refusal
        # before the Executive goal entry. The approval recogniser claims only
        # utterances that are, in their entirety, a bare decision, so it cannot
        # take a turn away from any of them -- proved separately in
        # `tests/test_conversational_approval_intents.py`.
        assert [n for n in names if n != rc._DISPATCH_APPROVAL][:3] == [
            rc._DISPATCH_TASK,
            rc._DISPATCH_FORECAST,
            rc._DISPATCH_OBJECTIVE,
        ]
        assert names[0] == rc._DISPATCH_APPROVAL

    async def test_an_explicit_task_instruction_is_not_diverted_to_the_executive(
        self,
        enabled,
        model,
        port,
    ):
        result = await run_chat_through_runtime_contract(enabled, EXPLICIT_TASK, model)
        assert result.task_action is not None
        assert result.executive_action is None
        assert port.prompts == [], "a known explicit command must not reach a planner"

    async def test_a_recogniser_that_claims_the_turn_stops_dispatch_before_the_executive(
        self,
        enabled,
        model,
        port,
        monkeypatch,
    ):
        claimed = {"reply": "the objective seam answered"}

        async def _claims(daemon, observation):
            return dict(claimed)

        monkeypatch.setattr(
            rc,
            "_CHAT_DISPATCH",
            (
                (rc._DISPATCH_OBJECTIVE, _claims),
                (rc._DISPATCH_EXECUTIVE_GOAL, rc._handle_executive_goal),
            ),
        )
        result = await run_chat_through_runtime_contract(enabled, GOAL, model)
        assert result.response == "the objective seam answered"
        assert result.executive_action is None
        assert port.prompts == []


# ---------------------------------------------------------------------------
# 2. Ordinary conversation stays ordinary conversation.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestOrdinaryConversationIsUntouched:
    @pytest.mark.parametrize(
        "utterance",
        [SMALL_TALK, "What is the capital of France?", "I had a rough day.", "Tell me about jazz."],
    )
    async def test_conversation_reaches_the_model_even_when_enabled(
        self,
        enabled,
        model,
        port,
        utterance,
    ):
        result = await run_chat_through_runtime_contract(enabled, utterance, model)
        assert result.executive_action is None
        assert result.response == "a conversational reply"
        assert port.prompts == [], "conversation must never be sent to the planner"

    async def test_enabling_this_does_not_turn_every_message_into_a_task(self, enabled, model):
        """Invariant 4, measured rather than asserted."""
        utterances = [SMALL_TALK, "Thanks.", "Say more.", "What do you think?", GOAL]
        claimed = []
        for utterance in utterances:
            result = await run_chat_through_runtime_contract(enabled, utterance, model)
            if result.executive_action is not None:
                claimed.append(utterance)
        assert claimed == [GOAL]


# ---------------------------------------------------------------------------
# 3 + 4. The goal reaches EXEC-01's deliberation, through EXEC-01's contracts.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAGoalReachesTheExistingDeliberation:
    async def test_the_goal_reaches_the_deliberation_port(self, enabled, model, port):
        result = await run_chat_through_runtime_contract(enabled, GOAL, model)
        assert port.prompts, "the goal never reached EXEC-01's deliberation"
        assert result.executive_action is not None
        assert result.executive_action["deliberated"] is True

    async def test_the_conversational_model_is_not_asked_to_narrate_it(self, enabled, model):
        """The turn's reply is built from what the Executive did, not generated.
        A generated sentence about an action could contradict the record, and
        the person would have no way to tell which was true."""
        await run_chat_through_runtime_contract(enabled, GOAL, model)
        assert model.calls == []

    async def test_the_prompt_is_the_persons_own_words(self, enabled, model, port):
        await run_chat_through_runtime_contract(enabled, GOAL, model)
        assert GOAL in port.prompts[0]

    async def test_a_literal_instruction_consults_no_model_at_all(self, enabled, model, port):
        """Invariant 3, inside the Executive: `parse_task` reads "open notepad"
        literally, so deliberation is never reached. EXEC-02 does not change
        that and must not."""
        result = await run_chat_through_runtime_contract(
            enabled,
            "Please open notepad",
            model,
        )
        assert result.executive_action is not None
        assert port.prompts == []
        assert result.executive_action["deliberated"] is False


@pytest.mark.asyncio
class TestItUsesTheExistingContracts:
    async def test_the_proposal_is_an_executive_plan_not_a_new_representation(
        self,
        enabled,
        model,
    ):
        """Invariant 7: no parallel plan object, no second TaskIntent."""
        from bartholomew.executive import store as executive_store

        result = await run_chat_through_runtime_contract(enabled, GOAL, model)
        task_id = result.executive_action["task_id"]
        assert task_id

        plan = executive_store.load_plan(
            enabled.mem.db_path,
            tenant_id=TENANT,
            task_id=task_id,
        )
        assert plan is not None, "the plan must live in the existing executive store"
        assert plan.instruction == GOAL
        assert [step.capability for step in plan.steps] == [
            "windows.launch_app",
            "windows.type_text",
        ]

    async def test_the_action_is_in_the_one_existing_action_envelope(self, enabled, model):
        result = await run_chat_through_runtime_contract(enabled, GOAL, model)
        rows = _actions(enabled.mem.db_path)
        assert [row[0] for row in rows] == result.executive_action["proposed_action_ids"]
        assert rows and rows[0][1] == "windows.launch_app"

    async def test_the_kernel_grows_no_second_planner(self):
        """`kernel/planner.py` is untouched and still inert: EXEC-02 added no
        competing decision-maker (invariant 1)."""
        import inspect

        from bartholomew.kernel.planner import Planner

        # `Planner.decide()` has returned None unconditionally since proactive
        # nudges moved to the scheduler, and EXEC-02 did not attach anything to
        # it. Nothing here builds a plan; the only planning in this repository
        # is still `bartholomew/executive/`.
        assert "return None" in inspect.getsource(Planner.decide)
        source = inspect.getsource(rc._handle_executive_goal)
        for forbidden in ("build_plan", "parse_task", "CapabilityKind", "grant_action_approval"):
            assert forbidden not in source, f"{forbidden} belongs to the Executive, not to chat"
        assert "build_plan" not in inspect.getsource(rc._goal_reply)


# ---------------------------------------------------------------------------
# 5 + 7 + 10. Governance decides; approval cannot be bypassed; nothing is done.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestGovernanceStillDecides:
    async def test_the_proposal_stops_at_pending_approval(self, enabled, model):
        await run_chat_through_runtime_contract(enabled, GOAL, model)
        rows = _actions(enabled.mem.db_path)
        assert rows, "the step must have gone through the envelope"
        assert {row[2] for row in rows} == {"pending_approval"}

    async def test_only_the_first_step_is_ever_proposed(self, enabled, model):
        """A step advances only when the previous one verified. A conversational
        goal does not get to queue a machine's whole afternoon."""
        result = await run_chat_through_runtime_contract(enabled, GOAL, model)
        assert len(result.executive_action["proposed_action_ids"]) == 1
        assert result.executive_action["steps"] == 2

    async def test_a_device_that_is_not_enrolled_is_refused_not_worked_around(
        self,
        daemon,
        port,
        model,
    ):
        devices.install_registry(_Registry())  # nothing enrolled
        try:
            install_conversational_executive(
                daemon,
                tenant_id=TENANT,
                device_id=DEVICE,
                requested_by=REQUESTER,
            )
            daemon.deliberation_port = port
            # A *literally* recognised instruction, so the pass reaches the
            # enrolment branch: EXEC-01's ordering raises a question about what
            # was meant before it says anything about a machine, and an
            # outcome-level goal against an unknown device has no capability
            # catalogue to reason from in the first place.
            result = await run_chat_through_runtime_contract(daemon, "Please open notepad", model)
        finally:
            devices.install_registry(None)
        assert result.executive_action["outcome"] == goal_intents.GOAL_OUTCOME_REFUSED
        assert _actions(daemon.mem.db_path) == []
        assert model.calls == [], "a refusal is reported, never handed to the model to narrate"

    async def test_a_request_outside_the_capability_vocabulary_is_refused_truthfully(
        self,
        enabled,
        model,
        port,
    ):
        """Invariant 5/6: cognition never reopens a refusal, and the person is
        told rather than being handed a generated sentence about a deletion."""
        result = await run_chat_through_runtime_contract(
            enabled,
            "Delete these files for me",
            model,
        )
        action = result.executive_action
        assert action["outcome"] in {
            goal_intents.GOAL_OUTCOME_REFUSED,
            goal_intents.GOAL_OUTCOME_CLARIFICATION,
        }
        assert action["deliberated"] is False
        assert _actions(enabled.mem.db_path) == []
        assert model.calls == []


@pytest.mark.asyncio
class TestApprovalCannotBeBypassed:
    async def test_a_deliberated_plan_mints_no_approval(self, enabled, model):
        """AST-enforced elsewhere (`tests/test_w03b_no_bypass.py`); measured
        here on the row the envelope actually wrote."""
        from bartholomew.kernel.db_ctx import wal_db

        await run_chat_through_runtime_contract(enabled, GOAL, model)
        with wal_db(enabled.mem.db_path, label="exec02_approval") as conn:
            approvals = conn.execute(
                "SELECT COUNT(*) FROM windows_action_requests WHERE state != 'pending_approval'",
            ).fetchone()[0]
        assert approvals == 0

    async def test_the_reply_never_says_the_work_happened(self, enabled, model):
        result = await run_chat_through_runtime_contract(enabled, GOAL, model)
        lowered = (result.response or "").lower()
        assert "nothing has run" in lowered
        assert "approv" in lowered


@pytest.mark.asyncio
class TestNothingIsReportedComplete:
    async def test_the_record_states_that_nothing_executed_or_verified(self, enabled, model):
        """Execution and *independent* verification happen on the `advance`
        pass, behind a human approval. This surface records the claim being
        made rather than leaving it to be inferred from an absence."""
        result = await run_chat_through_runtime_contract(enabled, GOAL, model)
        action = result.executive_action
        assert action["executed"] is False
        assert action["verified"] is False
        assert action["changed"] is False

    async def test_the_reflection_carries_the_same_account(self, enabled, model):
        from bartholomew.kernel.db_ctx import wal_db

        await run_chat_through_runtime_contract(enabled, GOAL, model)
        with wal_db(enabled.mem.db_path, label="exec02_reflection") as conn:
            rows = conn.execute(
                "SELECT meta FROM reflections ORDER BY id DESC LIMIT 20",
            ).fetchall()
        metas = [json.loads(row[0]) for row in rows if row[0]]
        chat = [m for m in metas if m.get("surface") == "chat" and "executive_action" in m]
        assert chat, "the chat turn must have written its Reflection with the goal record"
        recorded = chat[0]["executive_action"]
        assert recorded["executed"] is False
        assert recorded["verified"] is False
        assert recorded["outcome"] == goal_intents.GOAL_OUTCOME_PROPOSED


# ---------------------------------------------------------------------------
# 6. The Parking Brake stays authoritative.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTheParkingBrakeIsAuthoritative:
    async def test_an_engaged_brake_plans_nothing_and_consults_no_model(
        self,
        enabled,
        model,
        port,
    ):
        """The Executive reads the brake *before* it understands anything, in
        its most restrictive form. Chat's own Governance stage fails closed on
        the `skills` scope first, so this is belt and braces -- both are
        asserted, because either alone would be a single point of failure."""
        from bartholomew.orchestrator.safety.governance_store import GovernanceStore

        GovernanceStore(enabled.mem.db_path).engage("actuation", actor="operator", reason="halt")
        result = await run_chat_through_runtime_contract(enabled, GOAL, model)

        assert port.prompts == [], "an engaged brake must mean no model is consulted"
        assert _actions(enabled.mem.db_path) == []
        if result.executive_action is not None:
            assert result.executive_action["outcome"] == goal_intents.GOAL_OUTCOME_BRAKE
            assert "parking brake" in (result.response or "").lower()
        else:
            assert result.governance_allowed is False


# ---------------------------------------------------------------------------
# 8. Failure degrades safely and truthfully.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFailureDegradesSafely:
    async def test_a_model_that_raises_becomes_a_question_not_a_fabricated_plan(
        self,
        daemon,
        registry,
        model,
    ):
        install_conversational_executive(
            daemon,
            tenant_id=TENANT,
            device_id=DEVICE,
            requested_by=REQUESTER,
        )
        daemon.deliberation_port = _Port(raises=RuntimeError("the backend is down"))
        result = await run_chat_through_runtime_contract(daemon, GOAL, model)
        assert result.executive_action["outcome"] == goal_intents.GOAL_OUTCOME_CLARIFICATION
        assert _actions(daemon.mem.db_path) == []
        assert "haven't proposed" in (result.response or "").lower()

    async def test_unreadable_model_output_proposes_nothing(self, daemon, registry, model):
        install_conversational_executive(
            daemon,
            tenant_id=TENANT,
            device_id=DEVICE,
            requested_by=REQUESTER,
        )
        daemon.deliberation_port = _Port(payload="I'd be happy to help with that!")
        result = await run_chat_through_runtime_contract(daemon, GOAL, model)
        assert result.executive_action["outcome"] != goal_intents.GOAL_OUTCOME_PROPOSED
        assert _actions(daemon.mem.db_path) == []

    async def test_no_deliberation_port_keeps_the_literal_reading(self, daemon, registry, model):
        """Conversational routing without cognition is a real, safe posture:
        chat reaches the Executive, the Executive reads literally, and an
        outcome it cannot recognise becomes a question."""
        install_conversational_executive(
            daemon,
            tenant_id=TENANT,
            device_id=DEVICE,
            requested_by=REQUESTER,
        )
        result = await run_chat_through_runtime_contract(daemon, GOAL, model)
        assert result.executive_action["deliberated"] is False
        assert result.executive_action["outcome"] == goal_intents.GOAL_OUTCOME_CLARIFICATION
        assert _actions(daemon.mem.db_path) == []

    async def test_an_executive_that_raises_never_falls_through_to_the_model(
        self,
        enabled,
        model,
        monkeypatch,
    ):
        """Falling through is exactly how a fabricated "I've sorted your files
        out" would reach the user, so it must not be possible."""
        seam = rc._executive_seam()

        async def _boom(*args, **kwargs):
            raise RuntimeError("something broke")

        monkeypatch.setattr(seam, "run_executive_task_through_runtime_contract", _boom)
        result = await run_chat_through_runtime_contract(enabled, GOAL, model)
        assert result.executive_action["outcome"] == goal_intents.GOAL_OUTCOME_FAILED
        assert model.calls == []
        assert "nothing has run" in (result.response or "").lower()

    async def test_a_runtime_without_the_executive_package_says_so(
        self,
        enabled,
        model,
        monkeypatch,
    ):
        monkeypatch.setattr(rc, "_executive_seam", lambda: None)
        result = await run_chat_through_runtime_contract(enabled, GOAL, model)
        assert result.executive_action["outcome"] == goal_intents.GOAL_OUTCOME_UNAVAILABLE
        assert "isn't available" in (result.response or "")


class TestActivationContract:
    def test_the_config_holds_authority_and_nothing_else(self):
        config = ConversationalExecutive(tenant_id=TENANT, device_id=DEVICE)
        assert (config.tenant_id, config.device_id) == (TENANT, DEVICE)
        assert config.requested_by == "chat"
        with pytest.raises(Exception):
            config.device_id = "other"  # frozen


class TestTheEvidenceScriptExercisesTheRealPath:
    """Raised by review on PR #115 and confirmed.

    The script is the package's real-model proof, so a step it omits is a
    proof it cannot produce. `app.py` calls `install_seams()` on startup, which
    installs Session E's registry as the one device truth; a process that only
    builds a `KernelDaemon` leaves the actuation registry at its fail-closed
    default and refuses **every** device. The script would then report "not
    enrolled" as a fact about the deployment when it was a fact about the
    script.
    """

    def test_it_installs_the_same_seams_the_api_startup_does(self):
        source = pathlib.Path("scripts/exec02_real_model_evidence.py").read_text()
        assert "install_seams(" in source, (
            "the evidence script must install the device registry, or it can "
            "never reach a proposal however well a model reasons"
        )
        assert "bound_runtime_user_id" in source
        # Installed *before* the chat turn, not after it. Anchored on the call
        # sites rather than any mention, so the module docstring naming either
        # of them cannot make this pass or fail by accident.
        assert source.index("install_seams(") < source.index(
            "await run_chat_through_runtime_contract(",
        )


class TestProductionActivationIsExplicit:
    """Invariant 8, at the one place a real deployment turns this on.

    `configure_from_environment` is what `app.py` calls on startup. The
    properties that matter are that it does nothing by default, that a model
    being reachable is never itself the switch, and that a half-specified
    activation degrades to today's behaviour while *saying so* -- an activation
    that silently did not happen is the worst of the available outcomes.
    """

    class _Ctx:
        pass

    class _Router:
        def route(self, request):  # pragma: no cover - never called here
            return "{}"

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        from bartholomew.integration import conversational_executive as ce

        for name in (
            ce.ENV_ENABLED,
            ce.ENV_DEVICE_ID,
            ce.ENV_TENANT_ID,
            ce.ENV_REQUESTED_BY,
            ce.ENV_DELIBERATION,
        ):
            monkeypatch.delenv(name, raising=False)
        return ce

    def test_nothing_is_enabled_by_default(self, _clean_env):
        ctx = self._Ctx()
        report = _clean_env.configure_from_environment(ctx, self._Router())
        assert report["conversational_executive_enabled"] is False
        assert report["deliberation_installed"] is False
        assert _clean_env.resolve(ctx) is None
        assert getattr(ctx, "deliberation_port", None) is None

    def test_a_reachable_model_does_not_switch_anything_on(self, _clean_env):
        """A router being present is a precondition, never a trigger."""
        ctx = self._Ctx()
        report = _clean_env.configure_from_environment(ctx, self._Router())
        assert report["deliberation_installed"] is False
        assert any("is not set" in reason for reason in report["reasons"])

    def test_the_two_switches_are_independent(self, _clean_env, monkeypatch):
        monkeypatch.setenv(_clean_env.ENV_DELIBERATION, "1")
        ctx = self._Ctx()
        report = _clean_env.configure_from_environment(ctx, self._Router())
        assert report["deliberation_installed"] is True
        assert (
            report["conversational_executive_enabled"] is False
        ), "cognition inside the Executive must not imply conversational routing"

    def test_conversational_routing_without_cognition_is_a_real_posture(
        self,
        _clean_env,
        monkeypatch,
    ):
        monkeypatch.setenv(_clean_env.ENV_ENABLED, "1")
        monkeypatch.setenv(_clean_env.ENV_DEVICE_ID, DEVICE)
        ctx = self._Ctx()
        report = _clean_env.configure_from_environment(ctx, None)
        assert report["conversational_executive_enabled"] is True
        assert report["deliberation_installed"] is False
        assert _clean_env.resolve(ctx).device_id == DEVICE

    def test_activation_is_refused_without_a_named_machine(self, _clean_env, monkeypatch):
        monkeypatch.setenv(_clean_env.ENV_ENABLED, "1")
        ctx = self._Ctx()
        report = _clean_env.configure_from_environment(ctx, self._Router())
        assert report["conversational_executive_enabled"] is False
        assert _clean_env.resolve(ctx) is None
        assert any(_clean_env.ENV_DEVICE_ID in reason for reason in report["reasons"])

    def test_both_switches_together_produce_the_full_posture(self, _clean_env, monkeypatch):
        monkeypatch.setenv(_clean_env.ENV_DELIBERATION, "true")
        monkeypatch.setenv(_clean_env.ENV_ENABLED, "yes")
        monkeypatch.setenv(_clean_env.ENV_DEVICE_ID, DEVICE)
        monkeypatch.setenv(_clean_env.ENV_TENANT_ID, TENANT)
        monkeypatch.setenv(_clean_env.ENV_REQUESTED_BY, "taylor")
        ctx = self._Ctx()
        report = _clean_env.configure_from_environment(ctx, self._Router())
        assert report["deliberation_installed"] is True
        assert report["conversational_executive_enabled"] is True
        config = _clean_env.resolve(ctx)
        assert (config.tenant_id, config.device_id, config.requested_by) == (
            TENANT,
            DEVICE,
            "taylor",
        )

    def test_the_tenant_is_the_platform_s_answer_not_a_synthetic_default(
        self,
        _clean_env,
        monkeypatch,
    ):
        """Raised by review on PR #115 and confirmed.

        An earlier cut defaulted the tenant to the string `"default"`. Devices
        enrolled through the platform are tenant-qualified by the bound runtime
        user, falling back to the `local` sentinel when the process is unbound,
        so planning under `"default"` reported a normally-enrolled machine as
        **not enrolled**: the feature would have looked configured and refused
        every conversational goal. The tenant must be the same answer the
        operator route reaches, not a string this module invented.
        """
        monkeypatch.delenv("BARTH_RUNTIME_USER_ID", raising=False)
        monkeypatch.setenv(_clean_env.ENV_ENABLED, "1")
        monkeypatch.setenv(_clean_env.ENV_DEVICE_ID, DEVICE)
        ctx = self._Ctx()
        report = _clean_env.configure_from_environment(ctx, None)
        assert _clean_env.resolve(ctx).tenant_id == _clean_env.LOCAL_TENANT
        assert report["tenant_id"] == _clean_env.LOCAL_TENANT
        assert _clean_env.resolve(ctx).tenant_id != "default"

    def test_the_sentinel_cannot_drift_from_the_operator_route_s(self, _clean_env):
        """`LOCAL_TENANT` is duplicated here rather than imported, so that
        `bartholomew/` keeps no import edge to the API bridge. Duplication is
        only safe while something fails when the two diverge. This is it."""
        from bartholomew_api_bridge_v0_1.services.api import device_action_auth

        assert _clean_env.LOCAL_TENANT == device_action_auth.LOCAL_TENANT

    def test_a_bound_runtime_is_the_tenant(self, _clean_env, monkeypatch):
        monkeypatch.setenv("BARTH_RUNTIME_USER_ID", "user-42")
        assert _clean_env.resolve_tenant_id() == "user-42"

    def test_an_explicit_override_still_wins(self, _clean_env, monkeypatch):
        monkeypatch.setenv("BARTH_RUNTIME_USER_ID", "user-42")
        monkeypatch.setenv(_clean_env.ENV_TENANT_ID, "some-other-tenant")
        assert _clean_env.resolve_tenant_id() == "some-other-tenant"

    def test_a_broken_activation_degrades_rather_than_crashing_startup(
        self,
        _clean_env,
        monkeypatch,
    ):
        monkeypatch.setenv(_clean_env.ENV_ENABLED, "1")
        monkeypatch.setenv(_clean_env.ENV_DEVICE_ID, DEVICE)

        def _explode(*args, **kwargs):
            raise RuntimeError("no")

        monkeypatch.setattr(_clean_env, "install_conversational_executive", _explode)
        ctx = self._Ctx()
        report = _clean_env.configure_from_environment(ctx, None)
        assert report["conversational_executive_enabled"] is False
        assert any("activation failed" in reason for reason in report["reasons"])
