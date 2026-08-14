"""
Usable POC slice 1 -- Personal Memory Capture and Recall, end to end.

This is the slice's acceptance suite. Per
`docs/POC_SLICE_1_MEMORY_CAPTURE_RECALL.md` Sec.4, the bar is:

    a fact stated in one conversation is correctly recalled, unprompted, in a
    later unrelated conversation, without bypassing consent/governance

`TestAcceptanceBar` demonstrates exactly that with two separate chat turns.
The remaining classes protect the invariants that make the demonstration
mean something: capture goes through the *existing* governed write path
(never around it), sensitive content stays consent-gated and is never
recalled, governance denial yields zero writes, and S5.3's own behaviour is
unchanged.
"""

from __future__ import annotations

import json

import pytest

from bartholomew.kernel import runtime_contract as rc
from bartholomew.kernel.competency import (
    CompetencyEnvelope,
    CompetencyHeuristic,
    Provenance,
)
from bartholomew.kernel.memory.privacy_guard import set_consent_handler
from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.reflection import REFLECTION_KIND
from bartholomew.kernel.runtime_contract import run_chat_through_runtime_contract
from bartholomew.orchestrator.safety.parking_brake import BrakeStorage, ParkingBrake

TS = "2026-08-14T00:00:00Z"
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
    def __init__(self, mem: MemoryStore):
        self.mem = mem
        self.experience = _Stub()
        self.persona_manager = _Stub()
        self.working_memory = _Stub()
        self.identity_context = None


@pytest.fixture
async def daemon(tmp_path):
    mem = MemoryStore(str(tmp_path / "slice1.db"))
    await mem.init()
    yield _Daemon(mem)
    await mem.close()


class _CapturingResponder:
    """Captures the prompt the Capability stage actually receives."""

    def __init__(self):
        self.prompt = None

    async def __call__(self, prompt: str) -> str:
        self.prompt = prompt
        return "ok"


async def _respond(prompt: str) -> str:
    return "ok"


# ---------------------------------------------------------------------------
# 1. The acceptance bar itself.
# ---------------------------------------------------------------------------
class TestAcceptanceBar:
    async def test_fact_stated_in_one_turn_is_recalled_in_a_later_turn(self, daemon):
        """The whole slice, in two turns: state a fact, then ask about it in a
        separate interaction and see it come back unprompted."""
        capture = await run_chat_through_runtime_contract(
            daemon,
            "my birthday is 3rd March",
            _respond,
        )
        stored = [
            item
            for item in capture.personal_facts_captured
            if item["outcome"] == rc.FACT_OUTCOME_STORED
        ]
        assert stored, capture.personal_facts_captured

        # A separate, later turn. The user does NOT restate the fact.
        responder = _CapturingResponder()
        recall = await run_chat_through_runtime_contract(
            daemon,
            "when is my birthday?",
            responder,
        )

        context = recall.candidate_action.personal_fact_context
        assert context is not None and not context.is_empty(), context.to_dict()
        assert "Known personal facts:" in responder.prompt
        assert "March" in responder.prompt

    async def test_schedule_commitment_round_trips(self, daemon):
        await run_chat_through_runtime_contract(
            daemon,
            "I have a dentist appointment on the 20th",
            _respond,
        )

        responder = _CapturingResponder()
        await run_chat_through_runtime_contract(daemon, "anything about the dentist?", responder)

        assert "Known personal facts:" in responder.prompt
        assert "dentist" in responder.prompt.lower()

    async def test_the_fact_really_landed_in_the_shared_memory_store(self, daemon):
        await run_chat_through_runtime_contract(daemon, "my birthday is 3rd March", _respond)

        row = await daemon.mem.get_memory("user_profile", "birthday")
        assert row is not None
        assert "March" in row["value"]


class TestNonVacuity:
    """Control: prove recall is genuinely driven by the stored fact, not by
    something incidental in the prompt."""

    async def test_prompt_changes_only_because_of_the_recalled_fact(self, daemon):
        before = _CapturingResponder()
        await run_chat_through_runtime_contract(daemon, "when is my birthday?", before)

        await run_chat_through_runtime_contract(daemon, "my birthday is 3rd March", _respond)

        after = _CapturingResponder()
        await run_chat_through_runtime_contract(daemon, "when is my birthday?", after)

        assert after.prompt != before.prompt
        assert "Known personal facts:" in after.prompt
        assert "Known personal facts:" not in before.prompt

    async def test_unrelated_question_recalls_nothing(self, daemon):
        """The S5.3 relevance gate, reused: being the only stored fact is not
        enough to make it applicable to an unrelated request."""
        await run_chat_through_runtime_contract(daemon, "my birthday is 3rd March", _respond)

        responder = _CapturingResponder()
        result = await run_chat_through_runtime_contract(
            daemon,
            "how do compilers optimise loops?",
            responder,
        )

        assert result.candidate_action.personal_fact_context.is_empty()
        assert "Known personal facts:" not in responder.prompt


# ---------------------------------------------------------------------------
# 2. Consent and governance are not bypassed.
# ---------------------------------------------------------------------------
class TestConsentStillGates:
    async def test_sensitive_fact_is_queued_not_stored(self, daemon):
        result = await run_chat_through_runtime_contract(
            daemon,
            "my bank account number is 12345678",
            _respond,
        )

        outcomes = result.personal_facts_captured
        assert outcomes, "the extractor proposed nothing -- the gate was never exercised"
        assert all(item["outcome"] != rc.FACT_OUTCOME_STORED for item in outcomes)
        assert any(
            item["outcome"] == rc.FACT_OUTCOME_QUEUED_FOR_CONSENT for item in outcomes
        ), outcomes

        # It is genuinely absent from `memories`, and genuinely present in the
        # consent inbox -- not silently dropped.
        assert await daemon.mem.get_memory("user_profile", "bank_account_number") is None
        pending = await daemon.mem.list_pending_sensitive_writes(limit=50)
        assert any(entry["kind"] == "user_profile" for entry in pending), pending

    async def test_consent_gated_fact_is_never_recalled(self, daemon):
        await run_chat_through_runtime_contract(
            daemon,
            "my bank account number is 12345678",
            _respond,
        )

        responder = _CapturingResponder()
        result = await run_chat_through_runtime_contract(
            daemon,
            "what is my bank account number?",
            responder,
        )

        assert result.candidate_action.personal_fact_context.is_empty()
        assert "12345678" not in (responder.prompt or "")

    async def test_capture_uses_the_governed_write_path(self, daemon, monkeypatch):
        """Structural: the only way a fact reaches storage is
        `MemoryStore.upsert_memory()`. If capture ever grew its own writer,
        this stops seeing the call."""
        seen: list[tuple[str, str]] = []
        original = daemon.mem.upsert_memory

        async def _spy(kind, key, value, ts, **kwargs):
            seen.append((kind, key))
            return await original(kind, key, value, ts, **kwargs)

        monkeypatch.setattr(daemon.mem, "upsert_memory", _spy)

        await run_chat_through_runtime_contract(daemon, "my birthday is 3rd March", _respond)

        assert ("user_profile", "birthday") in seen

    async def test_capture_does_not_skip_the_privacy_or_rule_gates(self, daemon, monkeypatch):
        """`upsert_memory()` exposes `skip_privacy_guard` / `skip_rule_consent`
        bypasses for the consent-approval path. Capture must never pass
        them."""
        captured_kwargs: list[dict] = []
        original = daemon.mem.upsert_memory

        async def _spy(kind, key, value, ts, **kwargs):
            captured_kwargs.append(kwargs)
            return await original(kind, key, value, ts, **kwargs)

        monkeypatch.setattr(daemon.mem, "upsert_memory", _spy)

        await run_chat_through_runtime_contract(daemon, "my birthday is 3rd March", _respond)

        assert captured_kwargs
        for kwargs in captured_kwargs:
            assert not kwargs.get("skip_privacy_guard")
            assert not kwargs.get("skip_rule_consent")


class TestGovernanceUnchanged:
    async def test_engaged_brake_produces_zero_writes(self, daemon):
        brake = ParkingBrake(BrakeStorage(daemon.mem.db_path))
        brake.engage("skills")
        try:
            result = await run_chat_through_runtime_contract(
                daemon,
                "my birthday is 3rd March",
                _respond,
            )
        finally:
            brake.disengage()

        assert result.governance_allowed is False
        assert result.personal_facts_captured == []
        assert await daemon.mem.get_memory("user_profile", "birthday") is None
        assert await daemon.mem.list_pending_sensitive_writes(limit=50) == []

    async def test_capture_failure_never_breaks_the_turn(self, daemon, monkeypatch):
        async def _boom(*args, **kwargs):
            raise RuntimeError("write exploded")

        monkeypatch.setattr(daemon.mem, "upsert_memory", _boom)

        result = await run_chat_through_runtime_contract(
            daemon,
            "my birthday is 3rd March",
            _respond,
        )

        assert result.response == "ok"
        assert all(
            item["outcome"] == rc.FACT_OUTCOME_ERROR for item in result.personal_facts_captured
        )

    async def test_retrieval_failure_never_breaks_the_turn(self, daemon, monkeypatch):
        async def _boom(*args, **kwargs):
            raise RuntimeError("retrieval exploded")

        monkeypatch.setattr(rc, "run_off_loop", _boom)

        result = await run_chat_through_runtime_contract(daemon, "anything", _respond)

        assert result.response == "ok"
        assert result.candidate_action.personal_fact_context.is_empty()
        assert result.candidate_action.competency_context.is_empty()


# ---------------------------------------------------------------------------
# 3. S5.3's behaviour is preserved, not disturbed.
# ---------------------------------------------------------------------------
class TestCompetencyReasoningUnaffected:
    async def _store_competency(self, daemon):
        record = CompetencyHeuristic(
            envelope=CompetencyEnvelope(
                competency_id=COMPETENCY_ID,
                confidence=0.9,
                provenance=Provenance(source_type="user_instruction", detail="stated"),
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

    async def test_competency_still_applies_after_the_widening(self, daemon):
        await self._store_competency(daemon)

        responder = _CapturingResponder()
        result = await run_chat_through_runtime_contract(
            daemon,
            "should I replace the boiler?",
            responder,
        )

        assert not result.candidate_action.competency_context.is_empty()
        assert "Relevant competency" in responder.prompt

    async def test_a_personal_fact_cannot_displace_an_applicable_competency(self, daemon):
        """The reason the two are selected separately: `select_relevant()`
        commits to one domain per selection, so a single merged call would let
        a remembered fact evict an applicable competency (or vice versa)."""
        await self._store_competency(daemon)
        await run_chat_through_runtime_contract(
            daemon,
            "I prefer boiler servicing in spring",
            _respond,
        )

        responder = _CapturingResponder()
        result = await run_chat_through_runtime_contract(
            daemon,
            "should I replace the boiler?",
            responder,
        )

        assert not result.candidate_action.competency_context.is_empty()
        assert not result.candidate_action.personal_fact_context.is_empty()
        assert "Relevant competency" in responder.prompt
        assert "Known personal facts:" in responder.prompt

    async def test_a_personal_fact_is_never_recorded_as_a_competency(self, daemon):
        """No new memory kind, and no promotion into the competency model."""
        from bartholomew.kernel.competency import COMPETENCY_KINDS

        await run_chat_through_runtime_contract(daemon, "my birthday is 3rd March", _respond)

        for kind in COMPETENCY_KINDS:
            assert await daemon.mem.get_memory(kind, "birthday") is None


# ---------------------------------------------------------------------------
# 4. Exposure boundary and explanation-grade recording (S5.3 Decision E,
#    which slice 1 inherits rather than relaxes).
# ---------------------------------------------------------------------------
class TestExposureAndRecording:
    async def test_recalled_facts_are_not_auto_exposed_in_the_response(self, daemon):
        await run_chat_through_runtime_contract(daemon, "my birthday is 3rd March", _respond)

        async def _plain(prompt: str) -> str:
            return "It is in March."

        result = await run_chat_through_runtime_contract(daemon, "when is my birthday?", _plain)

        assert result.response == "It is in March."
        assert "Known personal facts:" not in result.response

    async def test_capture_and_recall_are_recorded_at_explanation_grade(self, daemon):
        await run_chat_through_runtime_contract(daemon, "my birthday is 3rd March", _respond)

        reflection = await daemon.mem.latest_reflection(REFLECTION_KIND)
        meta = reflection["meta"]
        if isinstance(meta, str):
            meta = json.loads(meta)

        captured = meta["personal_facts_captured"]
        assert captured
        assert captured[0]["kind"] == "user_profile"
        assert captured[0]["key"] == "birthday"
        assert captured[0]["outcome"] == rc.FACT_OUTCOME_STORED

        # ...and the recall turn records what was applied.
        await run_chat_through_runtime_contract(daemon, "when is my birthday?", _respond)
        reflection = await daemon.mem.latest_reflection(REFLECTION_KIND)
        meta = reflection["meta"]
        if isinstance(meta, str):
            meta = json.loads(meta)

        applied = meta["personal_fact_context"]["applied"]
        assert applied
        for required in ("kind", "key", "classification", "confidence", "provenance"):
            assert required in applied[0]

    async def test_nothing_is_recorded_when_nothing_was_captured_or_recalled(self, daemon):
        await run_chat_through_runtime_contract(daemon, "hello there", _respond)

        reflection = await daemon.mem.latest_reflection(REFLECTION_KIND)
        meta = reflection["meta"]
        if isinstance(meta, str):
            meta = json.loads(meta)

        assert "personal_facts_captured" not in meta
        assert "personal_fact_context" not in meta

    async def test_consent_gated_content_is_not_echoed_into_the_reflection(self, daemon):
        await run_chat_through_runtime_contract(
            daemon,
            "my bank account number is 12345678",
            _respond,
        )

        reflection = await daemon.mem.latest_reflection(REFLECTION_KIND)
        meta = reflection["meta"]
        if isinstance(meta, str):
            meta = json.loads(meta)

        assert "12345678" not in json.dumps(meta)
