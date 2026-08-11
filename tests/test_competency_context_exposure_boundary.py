"""
S5.3 Step 3: the Decision E exposure boundary, both halves.

E.1 -- competency context is never exposed automatically in an ordinary
response. This is a standing design position, not a sequencing artefact.

E.2 -- the recorded context stays explanation-grade, so a future
user-requested decision-explanation capability ("why did you recommend
that?") is not foreclosed by omission. A decision cannot be reconstructed
after the fact, so recording only a count or a flattened summary would
quietly destroy that capability.

The two halves pull in opposite directions on purpose: E.1 alone would be
satisfied by recording nothing, and E.2 alone by telling the user
everything. Testing them together is what pins the actual boundary.
"""

from __future__ import annotations

import json

import pytest

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

COMPETENCY_ID = "estate_management"
TS = "2026-08-11T00:00:00Z"


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
    mem = MemoryStore(str(tmp_path / "exposure.db"))
    await mem.init()
    yield _Daemon(mem)
    await mem.close()


async def _store_warranty_rule(daemon):
    record = CompetencyHeuristic(
        envelope=CompetencyEnvelope(
            competency_id=COMPETENCY_ID,
            confidence=0.9,
            provenance=Provenance(
                source_type="user_instruction",
                detail="stated during setup",
            ),
            supervision=Supervision(requires_review=True, reason="high impact"),
        ),
        slug="check_warranty",
        rule="Check the warranty before replacing a boiler",
    )
    await daemon.mem.upsert_memory(
        record.KIND,
        record.key(),
        json.dumps(record.to_dict()),
        TS,
        summary=record.to_summary_text(),
    )
    return record


class _EchoResponder:
    """A capability that returns its prompt verbatim -- the harshest possible
    test of E.1, since anything the seam put in the prompt would leak
    straight into the response."""

    async def __call__(self, prompt: str) -> str:
        return prompt


async def _plain_responder(prompt: str) -> str:
    return "You should check the warranty first."


# ---------------------------------------------------------------------------
# E.1 -- no automatic exposure.
# ---------------------------------------------------------------------------
class TestNoAutomaticExposure:
    async def test_response_is_not_decorated_with_competency_context(self, daemon):
        await _store_warranty_rule(daemon)

        result = await run_chat_through_runtime_contract(
            daemon,
            "should I replace the boiler?",
            _plain_responder,
        )

        # The competency genuinely applied...
        assert not result.candidate_action.competency_context.is_empty()
        # ...but the response is exactly what the capability produced.
        assert result.response == "You should check the warranty first."
        assert "Relevant competency" not in result.response
        assert "provenance" not in result.response.lower()

    async def test_seam_appends_nothing_to_the_capability_output(self, daemon):
        """The seam must not post-process the response to add context. An
        echo responder makes any appended block obvious."""
        await _store_warranty_rule(daemon)
        responder = _EchoResponder()

        result = await run_chat_through_runtime_contract(
            daemon,
            "should I replace the boiler?",
            responder,
        )

        # The response IS the prompt (echo), so it necessarily contains the
        # competency block -- that is the prompt's business. What matters is
        # that the seam added nothing further of its own after the capability
        # returned.
        assert result.response == result.interpretation.prompt

    async def test_no_explanation_endpoint_or_renderer_ships_in_s5_3(self):
        """E.2 is preserved as a *future* capability. S5.3 must not quietly
        ship a user-facing explanation surface."""
        from bartholomew.kernel import competency_reasoning as cr

        exported = {name for name in dir(cr) if not name.startswith("_")}
        for forbidden in ("explain", "explain_decision", "render_for_user", "user_explanation"):
            assert forbidden not in exported


# ---------------------------------------------------------------------------
# E.2 -- explanation-grade recording is preserved.
# ---------------------------------------------------------------------------
class TestExplanationGradePreserved:
    async def test_recorded_context_could_answer_why_did_you_recommend_that(self, daemon):
        """The concrete forward-compatibility check: everything a future
        explanation feature would need must survive in the record."""
        await _store_warranty_rule(daemon)

        await run_chat_through_runtime_contract(
            daemon,
            "should I replace the boiler?",
            _plain_responder,
        )

        reflection = await daemon.mem.latest_reflection(REFLECTION_KIND)
        meta = reflection["meta"]
        if isinstance(meta, str):
            meta = json.loads(meta)

        recorded = meta["competency_context"]
        applied = recorded["applied"]
        assert applied, "nothing recorded -- a future explanation has nothing to render"

        item = applied[0]
        # Which competency, and which specific record.
        assert item["competency_id"] == COMPETENCY_ID
        assert item["key"] == f"{COMPETENCY_ID}.check_warranty"
        assert item["kind"] == "competency_heuristic"
        # Where it came from.
        assert item["provenance"]["source_type"] == "user_instruction"
        assert item["provenance"]["detail"] == "stated during setup"
        assert item["provenance"]["recorded_by"]
        # How much to trust it, and how it is classified.
        assert item["confidence"] == 0.9
        assert item["classification"] == "personal"
        # Whether it asked for review, and why.
        assert item["requires_review"] is True
        assert item["review_reason"] == "high impact"

    async def test_recording_is_not_a_bare_count_or_flattened_summary(self, daemon):
        await _store_warranty_rule(daemon)
        await run_chat_through_runtime_contract(daemon, "replace the boiler?", _plain_responder)

        reflection = await daemon.mem.latest_reflection(REFLECTION_KIND)
        meta = reflection["meta"]
        if isinstance(meta, str):
            meta = json.loads(meta)
        recorded = meta["competency_context"]

        assert isinstance(recorded, dict), "context flattened to a scalar"
        assert isinstance(recorded["applied"], list)
        assert isinstance(
            recorded["applied"][0],
            dict,
        ), "applied records flattened to strings -- a future explanation could not cite them"

    async def test_no_chain_of_thought_is_recorded(self, daemon):
        """What is recorded is which stored, governed records were applied --
        never a reasoning trace."""
        await _store_warranty_rule(daemon)
        await run_chat_through_runtime_contract(daemon, "replace the boiler?", _plain_responder)

        reflection = await daemon.mem.latest_reflection(REFLECTION_KIND)
        meta = reflection["meta"]
        if isinstance(meta, str):
            meta = json.loads(meta)

        serialised = json.dumps(meta).lower()
        for forbidden in ("chain_of_thought", "reasoning_trace", "deliberation", "thoughts"):
            assert forbidden not in serialised
