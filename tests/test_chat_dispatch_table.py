"""
The chat surface's explicit-instruction dispatch table.

`run_chat_through_runtime_contract()` grew from one recogniser to two to
three, and a nested `if/else` chain had begun to encode dispatch order in its
indentation. `_CHAT_DISPATCH` states that order once, as data.

The point of this suite is that the refactor is *behaviour-preserving* and
that the ordering is a pinned property rather than an accident of how the
table happens to be written today. What it asserts:

  * order is exactly task -> forecast -> ... -> model fall-through;
  * the first recogniser to claim an utterance owns the reply, and no later
    recogniser is even consulted;
  * a claimed turn never reaches the model;
  * an unclaimed turn reaches the model, with the enriched prompt, exactly as
    it always did;
  * each recogniser's result still lands on its own named result field;
  * a governance denial runs no recogniser at all.

The handlers themselves are stubbed here on purpose. What they do through the
governed skill path is proven by `test_conversational_task_control.py` and
`test_forecast_chat_seam.py`; this suite proves only the dispatch around them.
"""

from __future__ import annotations

import pytest

from bartholomew.kernel import runtime_contract as rc
from bartholomew.kernel.runtime_contract import run_chat_through_runtime_contract


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
        "drives_path": str(drives_path),
        "cfg_path": str(cfg_path),
        "persona_path": str(persona_path),
        "policy_path": str(policy_path),
        "db_path": str(tmp_path / "test.db"),
    }


@pytest.fixture
def daemon(mock_config_files):
    from bartholomew.kernel.daemon import KernelDaemon

    return KernelDaemon(**mock_config_files)


class _Recorder:
    """A stub recogniser that records that it was consulted."""

    def __init__(self, name, claims=None):
        self.name = name
        self.claims = claims
        self.calls = 0

    async def __call__(self, daemon, observation):
        self.calls += 1
        return dict(self.claims) if self.claims is not None else None


@pytest.fixture
def model_calls():
    calls = []

    async def respond(prompt: str) -> str:
        calls.append(prompt)
        return f"model said: {prompt}"

    respond.calls = calls
    return respond


@pytest.mark.asyncio
class TestDispatchOrder:
    async def test_declared_order_is_task_then_forecast(self):
        """Order is behaviour, not presentation.

        An explicit task instruction must never be reinterpreted as anything
        else, so task control is consulted first. Anything appended later
        goes after both.
        """
        names = [name for name, _ in rc._CHAT_DISPATCH]
        assert names[:2] == [rc._DISPATCH_TASK, rc._DISPATCH_FORECAST]

    async def test_every_entry_is_a_named_pair_of_callables(self):
        for entry in rc._CHAT_DISPATCH:
            name, handler = entry
            assert isinstance(name, str) and name
            assert callable(handler)

    async def test_names_are_unique(self):
        names = [name for name, _ in rc._CHAT_DISPATCH]
        assert len(names) == len(set(names))


@pytest.mark.asyncio
class TestFirstClaimWins:
    async def test_first_recogniser_to_claim_owns_the_reply_and_stops_dispatch(
        self,
        daemon,
        monkeypatch,
        model_calls,
    ):
        first = _Recorder("task", {"reply": "task did the thing"})
        second = _Recorder("forecast")
        monkeypatch.setattr(rc, "_CHAT_DISPATCH", (("task", first), ("forecast", second)))

        result = await run_chat_through_runtime_contract(daemon, "do the thing", model_calls)

        assert result.response == "task did the thing"
        assert first.calls == 1
        # The whole point: a later recogniser is not even consulted.
        assert second.calls == 0
        # And the model is never asked to narrate an action that already ran.
        assert model_calls.calls == []

    async def test_a_later_recogniser_runs_only_when_earlier_ones_decline(
        self,
        daemon,
        monkeypatch,
        model_calls,
    ):
        first = _Recorder("task")
        second = _Recorder("forecast", {"reply": "it will rain"})
        monkeypatch.setattr(rc, "_CHAT_DISPATCH", (("task", first), ("forecast", second)))

        result = await run_chat_through_runtime_contract(daemon, "will it rain?", model_calls)

        assert result.response == "it will rain"
        assert first.calls == 1
        assert second.calls == 1
        assert model_calls.calls == []

    async def test_unclaimed_turn_falls_through_to_the_model_with_the_enriched_prompt(
        self,
        daemon,
        monkeypatch,
        model_calls,
    ):
        first = _Recorder("task")
        second = _Recorder("forecast")
        monkeypatch.setattr(rc, "_CHAT_DISPATCH", (("task", first), ("forecast", second)))

        result = await run_chat_through_runtime_contract(daemon, "hello there", model_calls)

        assert first.calls == 1
        assert second.calls == 1
        assert len(model_calls.calls) == 1
        assert "hello there" in model_calls.calls[0]
        assert result.response == f"model said: {model_calls.calls[0]}"
        assert result.task_action is None
        assert result.forecast_action is None


@pytest.mark.asyncio
class TestResultFieldsAreUnchanged:
    async def test_task_outcome_lands_on_task_action_only(
        self,
        daemon,
        monkeypatch,
        model_calls,
    ):
        claim = {"reply": "created", "action": "create", "changed": True}
        monkeypatch.setattr(
            rc,
            "_CHAT_DISPATCH",
            (("task", _Recorder("task", claim)), ("forecast", _Recorder("forecast"))),
        )

        result = await run_chat_through_runtime_contract(daemon, "add a task", model_calls)

        assert result.task_action == claim
        assert result.forecast_action is None

    async def test_forecast_outcome_lands_on_forecast_action_only(
        self,
        daemon,
        monkeypatch,
        model_calls,
    ):
        claim = {"reply": "dry", "action": "lookup", "outcome": "obtained"}
        monkeypatch.setattr(
            rc,
            "_CHAT_DISPATCH",
            (("task", _Recorder("task")), ("forecast", _Recorder("forecast", claim))),
        )

        result = await run_chat_through_runtime_contract(daemon, "will it rain?", model_calls)

        assert result.forecast_action == claim
        assert result.task_action is None

    async def test_an_unknown_dispatch_name_does_not_leak_onto_a_named_field(
        self,
        daemon,
        monkeypatch,
        model_calls,
    ):
        """A recogniser added to the table cannot silently populate an
        existing result field. It gets its own, or none."""
        claim = {"reply": "noted"}
        monkeypatch.setattr(rc, "_CHAT_DISPATCH", (("something_else", _Recorder("x", claim)),))

        result = await run_chat_through_runtime_contract(daemon, "anything", model_calls)

        assert result.response == "noted"
        assert result.task_action is None
        assert result.forecast_action is None


@pytest.mark.asyncio
class TestGovernanceStillGatesDispatch:
    async def test_a_governance_denial_consults_no_recogniser_at_all(
        self,
        daemon,
        monkeypatch,
        model_calls,
    ):
        first = _Recorder("task", {"reply": "should never happen"})
        monkeypatch.setattr(rc, "_CHAT_DISPATCH", (("task", first),))

        async def _blocked(*a, **kw):
            return True

        monkeypatch.setattr(
            "bartholomew.orchestrator.safety.governance_store.is_blocked_fail_closed_off_loop",
            _blocked,
        )

        result = await run_chat_through_runtime_contract(daemon, "do the thing", model_calls)

        assert result.governance_allowed is False
        assert result.response is None
        assert first.calls == 0
        assert model_calls.calls == []
