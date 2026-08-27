"""
Objective Continuity through an ordinary chat turn.

`test_objective_continuity.py` proves the durable loop. This suite proves the
half the user actually touches: that one plain sentence establishes an
objective, that a later turn *knows about it without being told*, and that a
plain sentence closes it -- and that the model is never asked to narrate any
of it.

The property that matters most here is the second one. An assistant that
records an objective but cannot bring it into the next conversation has moved
the remembering from the user's head into a database the user still has to
query. The prompt block is what makes the continuity real.
"""

from __future__ import annotations

import pytest

from bartholomew.kernel import objective_store
from bartholomew.kernel import runtime_contract as rc
from bartholomew.kernel.objective_store import ObjectiveStore
from bartholomew.kernel.runtime_contract import (
    render_objectives_for_prompt,
    run_chat_through_runtime_contract,
)
from identity_interpreter.identity_context import IdentityContext

ALLOW = IdentityContext(
    tool_use_default_allowed=False,
    tool_use_allowlist=[
        "objective_open",
        "objective_record",
        "objective_surface",
        "objective_complete",
        "objective_abandon",
        "objective_block",
        "objective_unblock",
    ],
)


@pytest.fixture
def mock_config_files(tmp_path):
    cfg_path = tmp_path / "kernel.yaml"
    cfg_path.write_text('timezone: "Australia/Brisbane"\nloop_interval_seconds: 1\n')
    persona_path = tmp_path / "persona.yaml"
    persona_path.write_text('name: "Test Bartholomew"\n')
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text("policies: []\n")
    drives_path = tmp_path / "drives.yaml"
    drives_path.write_text("drives: []\n")
    return {
        "cfg_path": str(cfg_path),
        "persona_path": str(persona_path),
        "policy_path": str(policy_path),
        "drives_path": str(drives_path),
        "db_path": str(tmp_path / "chat.db"),
    }


@pytest.fixture
def daemon(mock_config_files):
    from bartholomew.kernel.daemon import KernelDaemon

    made = KernelDaemon(**mock_config_files)
    # The daemon's own start() constructs this off-loop; these tests exercise
    # the chat seam without starting the background loops, so the store is
    # attached directly against the same database.
    made.objective_store = ObjectiveStore(mock_config_files["db_path"])
    made.identity_context = ALLOW
    return made


@pytest.fixture
def model():
    calls = []

    async def respond(prompt: str) -> str:
        calls.append(prompt)
        return "a generated sentence"

    respond.calls = calls
    return respond


@pytest.mark.asyncio
class TestEstablishing:
    async def test_one_ordinary_sentence_establishes_a_durable_objective(self, daemon, model):
        result = await run_chat_through_runtime_contract(
            daemon,
            "The roofer needs to come this week",
            model,
        )

        assert result.objective_action is not None
        assert result.objective_action["outcome"] == rc.OBJECTIVE_OUTCOME_EXECUTED
        assert result.objective_action["changed"] is True

        stored = daemon.objective_store.list_live()
        assert len(stored) == 1
        assert "roofer" in stored[0].title
        assert stored[0].horizon_kind == objective_store.HORIZON_THIS_WEEK

    async def test_the_reply_comes_from_what_happened_not_from_the_model(self, daemon, model):
        result = await run_chat_through_runtime_contract(
            daemon,
            "The roofer needs to come this week",
            model,
        )
        assert model.calls == [], "the model was asked to narrate a completed action"
        assert result.response == result.objective_action["reply"]
        assert "roofer" in result.response

    async def test_ordinary_conversation_is_untouched(self, daemon, model):
        result = await run_chat_through_runtime_contract(daemon, "hello there", model)
        assert result.objective_action is None
        assert result.response == "a generated sentence"
        assert len(model.calls) == 1
        assert daemon.objective_store.list_live() == []

    async def test_a_musing_does_not_become_a_durable_nag(self, daemon, model):
        await run_chat_through_runtime_contract(
            daemon,
            "should I get the roof repaired?",
            model,
        )
        assert daemon.objective_store.list_live() == []


@pytest.mark.asyncio
class TestContinuityIntoLaterTurns:
    async def test_a_later_turn_knows_the_objective_without_being_told(self, daemon, model):
        """The point of the whole slice: the user establishes it once, and
        does not carry it in their own head thereafter."""
        await run_chat_through_runtime_contract(
            daemon,
            "The roofer needs to come this week",
            model,
        )
        await run_chat_through_runtime_contract(daemon, "hello again", model)

        assert len(model.calls) == 1
        prompt = model.calls[0]
        assert "roofer" in prompt
        assert "Objectives you are carrying" in prompt

    async def test_the_objectives_block_is_distinct_from_experience_kernel_goals(self, daemon):
        """Two stores with different rules must not be conflated in the
        prompt, or 'complete this' becomes ambiguous between them."""
        daemon.objective_store.open(title="get the roof repaired")
        block = render_objectives_for_prompt(daemon.objective_store.list_live())
        assert "Objectives you are carrying" in block
        assert "Active goals:" not in block

    async def test_a_completed_objective_is_absent_from_later_prompts(self, daemon, model):
        await run_chat_through_runtime_contract(
            daemon,
            "The roofer needs to come this week",
            model,
        )
        await run_chat_through_runtime_contract(daemon, "the roofer is sorted", model)
        await run_chat_through_runtime_contract(daemon, "hello again", model)

        assert len(model.calls) == 1
        # The objectives block is gone entirely -- Bartholomew is no longer
        # carrying it. The recent-conversation block may still quote the
        # turns in which it was discussed, which is ordinary transcript, not
        # an objective being pursued.
        assert "Objectives you are carrying" not in model.calls[0]
        assert daemon.objective_store.list_live() == []

    async def test_the_horizon_reaches_the_prompt(self, daemon, model):
        await run_chat_through_runtime_contract(
            daemon,
            "The roofer needs to come this week",
            model,
        )
        await run_chat_through_runtime_contract(daemon, "hello again", model)
        assert "this week" in model.calls[0]


@pytest.mark.asyncio
class TestAsking:
    async def test_asking_what_is_outstanding_reports_the_real_objectives(self, daemon, model):
        await run_chat_through_runtime_contract(
            daemon,
            "The roofer needs to come this week",
            model,
        )
        result = await run_chat_through_runtime_contract(daemon, "what am I working on?", model)

        assert result.objective_action["outcome"] == rc.OBJECTIVE_OUTCOME_EXECUTED
        assert result.objective_action["count"] == 1
        assert "roofer" in result.response
        assert model.calls == []

    async def test_asking_with_nothing_outstanding_says_so_plainly(self, daemon, model):
        result = await run_chat_through_runtime_contract(daemon, "what am I working on?", model)
        assert "Nothing at the moment" in result.response


@pytest.mark.asyncio
class TestClosing:
    async def test_a_plain_sentence_completes_it(self, daemon, model):
        await run_chat_through_runtime_contract(
            daemon,
            "The roofer needs to come this week",
            model,
        )
        result = await run_chat_through_runtime_contract(daemon, "the roofer is sorted", model)

        assert result.objective_action["outcome"] == rc.OBJECTIVE_OUTCOME_EXECUTED
        assert result.objective_action["changed"] is True
        assert daemon.objective_store.list_live() == []

    async def test_the_users_own_words_are_kept_as_the_outcome(self, daemon, model):
        await run_chat_through_runtime_contract(
            daemon,
            "The roofer needs to come this week",
            model,
        )
        await run_chat_through_runtime_contract(daemon, "the roofer is sorted", model)

        [objective] = daemon.objective_store.list()
        assert objective.outcome_note == "the roofer is sorted"
        assert objective.resolution == objective_store.RESOLUTION_ACHIEVED

    async def test_closing_something_untracked_changes_nothing_and_says_so(self, daemon, model):
        result = await run_chat_through_runtime_contract(daemon, "the fence is sorted", model)
        assert result.objective_action["outcome"] == rc.OBJECTIVE_OUTCOME_NOT_FOUND
        assert result.objective_action["changed"] is False
        assert "nothing to change" in result.response.lower()

    async def test_abandoning_is_recorded_distinctly_from_completing(self, daemon, model):
        await run_chat_through_runtime_contract(
            daemon,
            "The roofer needs to come this week",
            model,
        )
        await run_chat_through_runtime_contract(daemon, "forget the roofer", model)

        [objective] = daemon.objective_store.list()
        assert objective.status == objective_store.STATUS_ABANDONED
        assert daemon.objective_store.list_live() == []


@pytest.mark.asyncio
class TestGovernanceAtTheChatSurface:
    async def test_a_denied_transition_reports_truthfully_and_changes_nothing(
        self,
        daemon,
        model,
    ):
        """A policy denial produces a truthful 'nothing changed' reply, not a
        denial of the whole conversation turn -- the precedent
        `_handle_task_intent` set."""
        daemon.identity_context = IdentityContext(
            tool_use_default_allowed=False,
            tool_use_allowlist=["notify"],
        )
        result = await run_chat_through_runtime_contract(
            daemon,
            "The roofer needs to come this week",
            model,
        )
        assert result.governance_allowed is True
        assert result.objective_action["outcome"] == rc.OBJECTIVE_OUTCOME_DENIED
        assert result.objective_action["changed"] is False
        assert "Nothing was recorded" in result.response
        assert daemon.objective_store.list() == []

    async def test_the_turn_records_what_the_governed_path_did(self, daemon, model):
        result = await run_chat_through_runtime_contract(
            daemon,
            "The roofer needs to come this week",
            model,
        )
        # Explanation-grade provenance on the chat surface, same posture as
        # task_action and forecast_action.
        assert result.objective_action["action"] == "open"
        assert result.objective_action["objective_id"] is not None

    async def test_a_missing_store_is_reported_rather_than_crashing_the_turn(
        self,
        daemon,
        model,
    ):
        daemon.objective_store = None
        result = await run_chat_through_runtime_contract(
            daemon,
            "The roofer needs to come this week",
            model,
        )
        assert result.objective_action["outcome"] == rc.OBJECTIVE_OUTCOME_UNAVAILABLE
        assert "Nothing was recorded" in result.response


@pytest.mark.asyncio
class TestDispatchOrderInPractice:
    async def test_a_task_instruction_is_not_reinterpreted_as_an_objective(
        self,
        daemon,
        model,
        monkeypatch,
    ):
        """'add a task to ring the roofer' is a task instruction. Task
        control is consulted first and claims it, so no objective is
        created."""

        async def _claims(daemon_, observation):
            return {"reply": "task created", "action": "create", "changed": True}

        monkeypatch.setattr(rc, "_handle_task_intent", _claims)
        monkeypatch.setattr(
            rc,
            "_CHAT_DISPATCH",
            (
                (rc._DISPATCH_TASK, _claims),
                (rc._DISPATCH_OBJECTIVE, rc._handle_objective_intent),
            ),
        )

        result = await run_chat_through_runtime_contract(
            daemon,
            "I need to ring the roofer",
            model,
        )
        assert result.response == "task created"
        assert result.objective_action is None
        assert daemon.objective_store.list() == []


@pytest.mark.asyncio
class TestForecastContributesEvidenceWithoutBecomingTheExecutive:
    """An external provider supplies evidence toward an objective, and only
    evidence. It decides nothing, cannot reach an objective on its own, and
    does not know objectives exist -- the Executive matched the utterance and
    filed the provider's own provenance against the objective it bears on.

    The forecast skill itself is proven by `test_forecast_external_capability`
    and `test_forecast_chat_seam`; these cases stub its chat handler, because
    what is under test here is the attachment, not the lookup.
    """

    @staticmethod
    def _forecast_claim(**overrides):
        claim = {
            "requested": "look up the forecast",
            "action": "lookup",
            "outcome": rc.FORECAST_OUTCOME_OBTAINED,
            "reply": "Thursday looks wet -- 8mm, 80% chance.",
            "provider_host": "api.open-meteo.com",
            "disclosed": {"latitude": -27.47, "longitude": 153.03},
        }
        claim.update(overrides)
        return claim

    async def _with_forecast(self, monkeypatch, claim):
        async def _handler(daemon_, observation):
            return claim

        monkeypatch.setattr(
            rc,
            "_CHAT_DISPATCH",
            (
                (rc._DISPATCH_TASK, rc._handle_task_intent),
                (rc._DISPATCH_FORECAST, _handler),
                (rc._DISPATCH_OBJECTIVE, rc._handle_objective_intent),
            ),
        )

    async def test_a_forecast_is_filed_as_evidence_against_the_related_objective(
        self,
        daemon,
        model,
        monkeypatch,
    ):
        await run_chat_through_runtime_contract(
            daemon,
            "The roofer needs to come this week",
            model,
        )
        [objective] = daemon.objective_store.list_live()

        await self._with_forecast(monkeypatch, self._forecast_claim())
        result = await run_chat_through_runtime_contract(
            daemon,
            "will it rain for the roofer on Thursday?",
            model,
        )

        assert result.objective_evidence is not None
        assert result.objective_evidence["objective_id"] == objective.id

        [event] = daemon.objective_store.evidence_events(objective.id)
        assert event.event_kind == objective_store.EVENT_FACT
        # Carried as an external assertion, never as established fact.
        assert event.provenance["evidence"] is True
        assert event.provenance["provider_host"] == "api.open-meteo.com"
        assert event.provenance["source_kind"] == "external_capability"

    async def test_the_disclosure_is_recorded_with_the_evidence(
        self,
        daemon,
        model,
        monkeypatch,
    ):
        """An egress nobody recorded is an egress nobody can audit -- the
        forecast slice's own posture, carried into the objective history."""
        await run_chat_through_runtime_contract(
            daemon,
            "The roofer needs to come this week",
            model,
        )
        [objective] = daemon.objective_store.list_live()

        await self._with_forecast(monkeypatch, self._forecast_claim())
        await run_chat_through_runtime_contract(
            daemon,
            "will it rain for the roofer on Thursday?",
            model,
        )

        [event] = daemon.objective_store.evidence_events(objective.id)
        assert event.provenance["disclosed"] == {"latitude": -27.47, "longitude": 153.03}

    async def test_a_failed_lookup_is_not_recorded_as_evidence(
        self,
        daemon,
        model,
        monkeypatch,
    ):
        """'We asked and got nothing' is not evidence of anything about the
        roof."""
        await run_chat_through_runtime_contract(
            daemon,
            "The roofer needs to come this week",
            model,
        )
        [objective] = daemon.objective_store.list_live()

        await self._with_forecast(
            monkeypatch,
            self._forecast_claim(
                outcome=rc.FORECAST_OUTCOME_FAILED,
                reply="I couldn't reach the forecast provider.",
            ),
        )
        result = await run_chat_through_runtime_contract(
            daemon,
            "will it rain for the roofer on Thursday?",
            model,
        )

        assert result.objective_evidence is None
        assert daemon.objective_store.evidence_events(objective.id) == []

    async def test_an_unrelated_forecast_is_attached_to_nothing(
        self,
        daemon,
        model,
        monkeypatch,
    ):
        await run_chat_through_runtime_contract(
            daemon,
            "The roofer needs to come this week",
            model,
        )
        [objective] = daemon.objective_store.list_live()

        await self._with_forecast(monkeypatch, self._forecast_claim())
        result = await run_chat_through_runtime_contract(
            daemon,
            "what's the weather like tomorrow?",
            model,
        )

        assert result.objective_evidence is None
        assert daemon.objective_store.evidence_events(objective.id) == []

    async def test_ambiguity_records_nothing(self, daemon, model, monkeypatch):
        """A fact filed against the wrong objective is read back later as if
        it belonged there. Two candidates means none."""
        for sentence in (
            "I need to sort the roofer for the house",
            "I need to sort the roofer for the shed",
        ):
            await run_chat_through_runtime_contract(daemon, sentence, model)
        assert len(daemon.objective_store.list_live()) == 2

        await self._with_forecast(monkeypatch, self._forecast_claim())
        result = await run_chat_through_runtime_contract(
            daemon,
            "will it rain for the roofer on Thursday?",
            model,
        )

        assert result.objective_evidence is None
        for objective in daemon.objective_store.list_live():
            assert daemon.objective_store.evidence_events(objective.id) == []

    async def test_the_attachment_never_changes_what_the_user_is_told(
        self,
        daemon,
        model,
        monkeypatch,
    ):
        """Continuity bookkeeping is a property of the *next* interaction and
        must not be able to alter this turn's reply."""
        await run_chat_through_runtime_contract(
            daemon,
            "The roofer needs to come this week",
            model,
        )
        claim = self._forecast_claim()
        await self._with_forecast(monkeypatch, claim)
        result = await run_chat_through_runtime_contract(
            daemon,
            "will it rain for the roofer on Thursday?",
            model,
        )
        assert result.response == claim["reply"]

    async def test_a_denied_objective_write_does_not_break_the_forecast_turn(
        self,
        daemon,
        model,
        monkeypatch,
    ):
        await run_chat_through_runtime_contract(
            daemon,
            "The roofer needs to come this week",
            model,
        )
        # Recording is no longer permitted; the forecast reply must stand.
        daemon.identity_context = IdentityContext(
            tool_use_default_allowed=False,
            tool_use_allowlist=["notify"],
        )
        claim = self._forecast_claim()
        await self._with_forecast(monkeypatch, claim)
        result = await run_chat_through_runtime_contract(
            daemon,
            "will it rain for the roofer on Thursday?",
            model,
        )
        assert result.response == claim["reply"]
        assert result.objective_evidence is None
