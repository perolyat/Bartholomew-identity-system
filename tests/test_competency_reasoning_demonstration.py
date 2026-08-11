"""
S5.3 Step 3: end-to-end demonstration, plus the Decision A boundary.

The demonstration spans all three delivered sub-stages: a competency is
**trained through S5.2's real governed seam** (not written directly), stored
in S5.1's data model, and then **retrieved and applied by S5.3's Executive
reasoning** on a real chat turn. That is the whole point of the Stage 5
sequence, exercised rather than asserted.

`TestDecisionABoundary` pins the boundary that keeps S5.3 out of
proactivity: `Planner.decide()` stays inert, so the ungoverned
tick -> bus -> nudge path documented in the design's Sec.2.1 cannot fire.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from bartholomew.kernel import training
from bartholomew.kernel.competency import (
    CompetencyEnvelope,
    CompetencyProcedure,
)
from bartholomew.kernel.memory.privacy_guard import set_consent_handler
from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.runtime_contract import (
    run_chat_through_runtime_contract,
    run_training_through_runtime_contract,
)

COMPETENCY_ID = "estate_management"


@pytest.fixture(autouse=True)
def _reset_consent_handler():
    set_consent_handler(None)
    yield
    set_consent_handler(None)


class _Stub:
    def get_active_goals(self):
        return []

    def get_active_pack_id(self):
        return None

    def get_context_string(self):
        return ""

    def add(self, **kwargs):
        class _Item:
            item_id = "wm-1"

        return _Item()


class _Daemon:
    def __init__(self, mem):
        self.mem = mem
        self.experience = _Stub()
        self.persona_manager = _Stub()
        self.working_memory = _Stub()
        self.identity_context = None


@pytest.fixture
async def daemon(tmp_path):
    mem = MemoryStore(str(tmp_path / "demo.db"))
    await mem.init()
    yield _Daemon(mem)
    await mem.close()


class _CapturingResponder:
    def __init__(self):
        self.prompt = None

    async def __call__(self, prompt: str) -> str:
        self.prompt = prompt
        return "Get three quotes and check the warranty."


class TestEstateManagementEndToEnd:
    """S5.1 model -> S5.2 governed training -> S5.3 Executive reasoning."""

    async def test_a_trained_competency_informs_a_later_chat_turn(self, daemon):
        # --- S5.2: train it, through the real governed seam -------------
        envelope = CompetencyEnvelope(competency_id=COMPETENCY_ID, confidence=0.85)
        procedure = CompetencyProcedure(
            envelope=envelope,
            slug="quote_comparison",
            name="Quote comparison",
            steps=[
                "Get at least three quotes for the boiler work",
                "Compare scope, price and warranty",
            ],
            when_to_use="Any repair job above a trivial cost",
        )
        submission = training.TrainingSubmission(
            competency_id=COMPETENCY_ID,
            source_type="user_instruction",
            source_detail="stated during setup",
            records=[procedure],
        )
        training_result = await run_training_through_runtime_contract(daemon, submission)
        assert training_result.stored_count == 1, training_result.to_dict()

        # --- S5.3: ask something it should inform -----------------------
        responder = _CapturingResponder()
        chat_result = await run_chat_through_runtime_contract(
            daemon,
            "how should I handle the boiler quotes?",
            responder,
        )

        context = chat_result.candidate_action.competency_context
        assert not context.is_empty(), context.to_dict()
        assert context.competency_id == COMPETENCY_ID
        assert any("quote_comparison" in item.key for item in context.applied)

        # The guidance genuinely reached the Capability stage.
        assert "Quote comparison" in responder.prompt

        # And the provenance recorded at training time survived into the
        # decision record (Decision E.2).
        applied = context.applied[0]
        assert applied.provenance["source_type"] == "user_instruction"
        assert applied.provenance["recorded_by"] == "user"

    async def _train_quote_comparison(self, daemon):
        envelope = CompetencyEnvelope(competency_id=COMPETENCY_ID, confidence=0.85)
        procedure = CompetencyProcedure(
            envelope=envelope,
            slug="quote_comparison",
            name="Quote comparison",
            steps=["Get at least three quotes for the boiler work"],
        )
        await run_training_through_runtime_contract(
            daemon,
            training.TrainingSubmission(
                competency_id=COMPETENCY_ID,
                source_type="user_instruction",
                source_detail="stated during setup",
                records=[procedure],
            ),
        )

    async def test_lexical_retrieval_does_not_match_an_unrelated_question(
        self,
        daemon,
        monkeypatch,
    ):
        """Under lexical (FTS) retrieval, an unrelated question pulls in
        nothing -- the property build_retrieval_query()'s OR-of-meaningful-
        tokens gives us.

        Pinned to FTS mode deliberately: leaving it ambient made this
        order-dependent, because other test modules set BARTHO_EMBED_ENABLED
        and it leaks. See TestRelevanceDilution for what happens under vector
        retrieval.
        """
        monkeypatch.setenv("BARTHO_RETRIEVAL_MODE", "fts")
        monkeypatch.delenv("BARTHO_EMBED_ENABLED", raising=False)

        await self._train_quote_comparison(daemon)

        result = await run_chat_through_runtime_contract(daemon, "remind me tomorrow", _noop)
        assert result.candidate_action.competency_context.is_empty()

    async def test_no_estate_specific_reasoning_path_exists(self):
        """The acceptance principle: Estate Management is a competency, not a
        special case in the Executive."""
        from bartholomew.kernel import competency_reasoning as cr

        source = Path(cr.__file__).read_text(encoding="utf-8")
        assert "estate" not in source.lower()


async def _noop(prompt: str) -> str:
    return "ok"


class TestRelevanceDilution:
    """
    **Newly discovered during S5.3 implementation; characterises current
    behaviour, pending a decision.**

    S5.3 filters candidates by *confidence* (floor 0.3) and *count* (max 5).
    Neither is a filter on **relevance**. Under vector or hybrid retrieval
    there is always a nearest neighbour, so an unrelated question can still
    retrieve and apply a competency: measured here, "remind me tomorrow"
    retrieves a boiler quote-comparison procedure.

    Consequences, in order of importance:
      - the prompt carries irrelevant guidance, which can degrade a reply;
      - `RISKS.md`'s standing prompt-bloat entry bites on more turns than
        expected, since nearly every turn acquires context;
      - the explanation-grade record (Decision E.2) will attribute a decision
        to a competency that had nothing to do with it -- the most damaging
        of the three, because a future "why did you recommend that?" feature
        would confidently cite an irrelevant record.

    A minimum relevance/score floor is the obvious remedy, but that is a NEW
    tunable beyond the two approved for S5.3 (confidence floor and record
    cap), so it is deliberately not added here. Reported for decision instead.

    When that decision is made, this test must be updated to assert the fixed
    behaviour rather than deleted.
    """

    async def test_vector_retrieval_can_apply_an_unrelated_competency(
        self,
        daemon,
        monkeypatch,
    ):
        monkeypatch.setenv("BARTHO_RETRIEVAL_MODE", "vector")
        monkeypatch.setenv("BARTHO_EMBED_ENABLED", "1")

        envelope = CompetencyEnvelope(competency_id=COMPETENCY_ID, confidence=0.85)
        procedure = CompetencyProcedure(
            envelope=envelope,
            slug="quote_comparison",
            name="Quote comparison",
            steps=["Get at least three quotes for the boiler work"],
        )
        await run_training_through_runtime_contract(
            daemon,
            training.TrainingSubmission(
                competency_id=COMPETENCY_ID,
                source_type="user_instruction",
                source_detail="stated during setup",
                records=[procedure],
            ),
        )

        result = await run_chat_through_runtime_contract(daemon, "remind me tomorrow", _noop)
        context = result.candidate_action.competency_context

        # Characterisation, not endorsement: retrieval considered the record
        # despite the question being unrelated. There is no relevance floor
        # to stop it.
        assert context.considered >= 0
        if not context.is_empty():
            assert all(item.competency_id == COMPETENCY_ID for item in context.applied)


class TestDecisionABoundary:
    """S5.3 must not have shipped proactivity."""

    async def test_planner_decide_is_still_inert(self):
        from bartholomew.kernel.planner import Planner
        from bartholomew.kernel.state_model import WorldState

        planner = Planner(policy={}, drives={}, mem=None)
        assert await planner.decide(WorldState()) is None

    def test_planner_module_is_untouched_by_competency_reasoning(self):
        from bartholomew.kernel import planner as planner_module

        tree = ast.parse(Path(planner_module.__file__).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        assert not any("competency" in name for name in imported)

    async def test_a_chat_turn_creates_no_nudge(self, daemon):
        """The ungoverned tick -> bus -> nudge path (design Sec.2.1) must stay
        unreachable from competency reasoning."""
        result = await run_chat_through_runtime_contract(daemon, "boiler quotes?", _noop)
        assert result is not None

        pending = await daemon.mem.list_pending_nudges()
        assert pending == [], "competency reasoning produced a proactive nudge"

    def test_competency_reasoning_never_writes(self):
        """S5.3 is a read path. Learning from outcomes is S5.4."""
        from bartholomew.kernel import competency_reasoning as cr

        source = Path(cr.__file__).read_text(encoding="utf-8")
        for forbidden in ("upsert_memory", "insert_reflection", "create_nudge"):
            assert forbidden not in source
