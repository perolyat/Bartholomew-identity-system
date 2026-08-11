"""
S5.3 Step 2 (chat seam integration) tests.

Covers the wiring: retrieval genuinely reaches the CandidateAction and the
prompt, governance is unchanged, consent still gates the read path, retrieval
runs off the event loop, and the boundaries the approved design fixes remain
true of the live seam.

`TestNonVacuity` is the required control, mirroring
`tests/test_voice_sight_runtime_contract_seam.py`: it proves the enrichment
is genuinely consumed rather than constructed and discarded.
"""

from __future__ import annotations

import json
import threading

import pytest

from bartholomew.kernel import runtime_contract as rc
from bartholomew.kernel.competency import (
    CompetencyEnvelope,
    CompetencyHeuristic,
    Provenance,
    Supervision,
)
from bartholomew.kernel.memory.privacy_guard import set_consent_handler
from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.reflection import REFLECTION_KIND
from bartholomew.kernel.runtime_contract import run_chat_through_runtime_contract
from bartholomew.orchestrator.safety.parking_brake import BrakeStorage, ParkingBrake

COMPETENCY_ID = "estate_management"
TS = "2026-08-11T00:00:00Z"


@pytest.fixture(autouse=True)
def _reset_consent_handler():
    set_consent_handler(None)
    yield
    set_consent_handler(None)


class _Experience:
    def get_active_goals(self):
        return []


class _Persona:
    def get_active_pack_id(self):
        return None


class _WorkingMemoryItem:
    item_id = "wm-1"


class _WorkingMemory:
    def get_context_string(self):
        return ""

    def add(self, **kwargs):
        return _WorkingMemoryItem()


class _Daemon:
    def __init__(self, mem: MemoryStore):
        self.mem = mem
        self.experience = _Experience()
        self.persona_manager = _Persona()
        self.working_memory = _WorkingMemory()
        # The chat seam consults Identity policy when one is wired in. None
        # means "not opted in", which is the existing additive behaviour --
        # these tests exercise competency reasoning, not Identity policy,
        # which has its own suites.
        self.identity_context = None


@pytest.fixture
async def daemon(tmp_path):
    mem = MemoryStore(str(tmp_path / "s53.db"))
    await mem.init()
    yield _Daemon(mem)
    await mem.close()


async def _store(daemon, record, *, kind=None):
    """Write a competency record straight through MemoryStore, the same way
    S5.2's seam does."""
    await daemon.mem.upsert_memory(
        kind or record.KIND,
        record.key(),
        json.dumps(record.to_dict()),
        TS,
        summary=record.to_summary_text(),
    )


def _heuristic(slug="check_warranty", rule=None, **envelope_kwargs) -> CompetencyHeuristic:
    envelope = CompetencyEnvelope(
        competency_id=envelope_kwargs.pop("competency_id", COMPETENCY_ID),
        confidence=envelope_kwargs.pop("confidence", 0.9),
        provenance=Provenance(source_type="user_instruction", detail="stated by the user"),
        **envelope_kwargs,
    )
    return CompetencyHeuristic(
        envelope=envelope,
        slug=slug,
        rule=rule or "Check the warranty before recommending replacement",
    )


async def _respond(prompt: str) -> str:
    return "ok"


class _CapturingResponder:
    """Captures the prompt the Capability stage actually receives."""

    def __init__(self):
        self.prompt = None

    async def __call__(self, prompt: str) -> str:
        self.prompt = prompt
        return "ok"


# ---------------------------------------------------------------------------
# 1. The seam: retrieval reaches CandidateAction and prompt.
# ---------------------------------------------------------------------------
class TestSeam:
    async def test_relevant_competency_reaches_the_candidate_action(self, daemon):
        await _store(daemon, _heuristic(rule="Check the warranty before replacing a boiler"))

        responder = _CapturingResponder()
        result = await run_chat_through_runtime_contract(
            daemon,
            "should I replace the boiler?",
            responder,
        )

        context = result.candidate_action.competency_context
        assert context is not None
        assert not context.is_empty(), context.to_dict()
        assert context.competency_id == COMPETENCY_ID

    async def test_competency_guidance_reaches_the_prompt(self, daemon):
        await _store(daemon, _heuristic(rule="Check the warranty before replacing a boiler"))

        responder = _CapturingResponder()
        await run_chat_through_runtime_contract(daemon, "replace the boiler?", responder)

        assert responder.prompt is not None
        assert "Relevant competency" in responder.prompt
        assert "warranty" in responder.prompt.lower()

    async def test_no_relevant_competency_leaves_the_turn_unchanged(self, daemon):
        """Decision D: low/no-confidence retrieval changes nothing
        user-visible."""
        responder = _CapturingResponder()
        result = await run_chat_through_runtime_contract(daemon, "hello there", responder)

        context = result.candidate_action.competency_context
        assert context is not None and context.is_empty()
        assert "Relevant competency" not in (responder.prompt or "")

    async def test_retrieval_failure_never_breaks_the_turn(self, daemon, monkeypatch):
        async def _boom(*args, **kwargs):
            raise RuntimeError("retrieval exploded")

        monkeypatch.setattr(rc, "run_off_loop", _boom)

        result = await run_chat_through_runtime_contract(daemon, "anything", _respond)

        assert result.response == "ok"
        assert result.candidate_action.competency_context.is_empty()


class TestNonVacuity:
    """Control: prove the competency block is genuinely consumed. If the
    seam stopped folding it into the prompt, TestSeam's positive assertion
    would be the only thing failing -- this pins the mechanism itself."""

    async def test_prompt_changes_only_because_of_the_competency_block(self, daemon):
        responder_without = _CapturingResponder()
        await run_chat_through_runtime_contract(daemon, "boiler question", responder_without)

        await _store(daemon, _heuristic(rule="Check the warranty before replacing a boiler"))

        responder_with = _CapturingResponder()
        await run_chat_through_runtime_contract(daemon, "boiler question", responder_with)

        assert responder_with.prompt != responder_without.prompt
        assert "Relevant competency" in responder_with.prompt
        assert "Relevant competency" not in responder_without.prompt


# ---------------------------------------------------------------------------
# 2. Governance unchanged.
# ---------------------------------------------------------------------------
class TestGovernanceUnchanged:
    async def test_engaged_brake_still_denies_regardless_of_competency(self, daemon):
        await _store(daemon, _heuristic())

        brake = ParkingBrake(BrakeStorage(daemon.mem.db_path))
        brake.engage("skills")
        try:
            result = await run_chat_through_runtime_contract(daemon, "boiler?", _respond)
        finally:
            brake.disengage()

        assert result.governance_allowed is False
        assert result.response is None

    async def test_supervision_only_ever_adds_a_review_requirement(self, daemon):
        """A competency asking for review must not be able to relax anything,
        and a competency NOT asking for review must not clear one."""
        await _store(
            daemon,
            _heuristic(
                slug="risky",
                rule="Replacing a boiler is a major decision",
                supervision=Supervision(requires_review=True, reason="high impact"),
            ),
        )
        await _store(daemon, _heuristic(slug="ordinary", rule="Boiler servicing is routine work"))

        result = await run_chat_through_runtime_contract(daemon, "boiler replacement", _respond)
        context = result.candidate_action.competency_context

        if any(item.requires_review for item in context.applied):
            assert context.requires_review is True


# ---------------------------------------------------------------------------
# 3. Consent still gates the read path.
# ---------------------------------------------------------------------------
class TestConsentOnTheReadPath:
    async def test_content_queued_for_consent_is_never_applied(self, daemon):
        """A record that never reached `memories` (queued for consent
        instead) cannot inform a CandidateAction."""
        sensitive = _heuristic(
            slug="account",
            rule="The electricity account number is 12345678 and the password is hunter2",
        )
        await _store(daemon, sensitive)

        # It was queued, not stored.
        assert await daemon.mem.get_memory("competency_heuristic", sensitive.key()) is None

        result = await run_chat_through_runtime_contract(
            daemon,
            "what is the electricity account number?",
            _respond,
        )
        context = result.candidate_action.competency_context
        assert all("account" not in item.key for item in context.applied)


# ---------------------------------------------------------------------------
# 4. Off-loop discipline (B2/B8).
# ---------------------------------------------------------------------------
class TestOffLoop:
    async def test_retrieval_does_not_run_on_the_event_loop_thread(self, daemon, monkeypatch):
        await _store(daemon, _heuristic(rule="Check the warranty before replacing a boiler"))

        loop_thread = threading.get_ident()
        observed: list[int] = []

        original = rc.run_off_loop

        async def _spy(fn, *args, **kwargs):
            def _wrapped(*a, **k):
                observed.append(threading.get_ident())
                return fn(*a, **k)

            return await original(_wrapped, *args, **kwargs)

        monkeypatch.setattr(rc, "run_off_loop", _spy)

        await run_chat_through_runtime_contract(daemon, "boiler?", _respond)

        assert observed, "retrieval did not go through run_off_loop at all"
        assert all(tid != loop_thread for tid in observed), (
            "competency retrieval ran on the event-loop thread -- the B2/B8 discipline requires "
            "synchronous database reads to run off it"
        )


# ---------------------------------------------------------------------------
# 5. Explanation-grade recording lands in the shared reflections sink.
# ---------------------------------------------------------------------------
class TestExplanationGradeRecording:
    async def test_applied_context_is_recorded_at_explanation_grade(self, daemon):
        await _store(daemon, _heuristic(rule="Check the warranty before replacing a boiler"))

        await run_chat_through_runtime_contract(daemon, "replace the boiler?", _respond)

        reflection = await daemon.mem.latest_reflection(REFLECTION_KIND)
        meta = reflection["meta"]
        if isinstance(meta, str):
            meta = json.loads(meta)

        recorded = meta["competency_context"]
        assert recorded["competency_id"] == COMPETENCY_ID
        assert recorded["applied"], "no applied records recorded"

        item = recorded["applied"][0]
        for required in ("kind", "key", "competency_id", "classification", "confidence"):
            assert required in item, f"explanation-grade record missing {required!r}"
        assert item["provenance"]["source_type"] == "user_instruction"

    async def test_nothing_is_recorded_when_no_competency_applied(self, daemon):
        await run_chat_through_runtime_contract(daemon, "hello", _respond)

        reflection = await daemon.mem.latest_reflection(REFLECTION_KIND)
        meta = reflection["meta"]
        if isinstance(meta, str):
            meta = json.loads(meta)
        assert "competency_context" not in meta


# ---------------------------------------------------------------------------
# 6. Other surfaces unaffected (Decision B: chat only).
# ---------------------------------------------------------------------------
class TestChatOnly:
    def test_candidate_action_competency_context_defaults_to_none(self):
        observation = rc.Observation(source="skill", raw_content="x")
        action = rc.CandidateAction(
            kind="some_skill",
            interpretation=rc.Interpretation(observation=observation, prompt="x"),
        )
        assert action.competency_context is None

    async def test_training_seam_does_not_acquire_competency_reasoning(self, daemon):
        """S5.2's seam constructs its own CandidateAction; it must not have
        gained competency context as a side effect."""
        import inspect

        source = inspect.getsource(rc.run_training_through_runtime_contract)
        assert "competency_context" not in source
        assert "_retrieve_competency_context" not in source


# ---------------------------------------------------------------------------
# 7. Boundary: Planner.decide() remains inert.
# ---------------------------------------------------------------------------
class TestPlannerRemainsInert:
    async def test_decide_still_returns_none(self):
        from bartholomew.kernel.planner import Planner
        from bartholomew.kernel.state_model import WorldState

        planner = Planner(policy={}, drives={}, mem=None)
        assert await planner.decide(WorldState()) is None

    def test_planner_module_does_not_import_competency_reasoning(self):
        import ast
        from pathlib import Path

        from bartholomew.kernel import planner as planner_module

        tree = ast.parse(Path(planner_module.__file__).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        assert not any("competency" in name for name in imported), (
            "Decision A: competency reasoning attaches to the request-driven path, not to "
            "Planner.decide(), whose only caller is the ungoverned proactive tick loop"
        )
