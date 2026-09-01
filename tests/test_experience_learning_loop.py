"""
S5.4 (narrow slice): the experience -> candidate learning -> review ->
consolidation -> later retrieval loop.

`TestAcceptanceScenario` is the whole point of the slice and runs the loop
end-to-end through real repository authorities: a real `ObjectiveStore`
objective with real evidence events reaches a real recorded outcome, a
candidate lesson is proposed, reviewed, and -- on acceptance only -- becomes
a competency record that a *later, unrelated chat turn* actually retrieves
and puts in the prompt through S5.3's existing seam.

The invariant suites around it exist because the failure modes here are all
silent: a candidate that reads as knowledge, a rejection that leaves
consolidated learning behind, an inference recorded as an observation, a
classification that quietly upgrades itself.
"""

from __future__ import annotations

import json

import pytest

from bartholomew.kernel import candidate_learning
from bartholomew.kernel import objective_store as os_mod
from bartholomew.kernel import runtime_contract as rc
from bartholomew.kernel.candidate_learning import (
    CandidateLesson,
    ReviewStateError,
    SourceExperience,
)
from bartholomew.kernel.competency import COMPETENCY_KINDS
from bartholomew.kernel.memory.privacy_guard import set_consent_handler
from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.objective_store import ObjectiveStore
from bartholomew.kernel.reflection import REFLECTION_KIND
from bartholomew.kernel.runtime_contract import (
    LEARNING_ACTION_ACCEPT,
    LEARNING_ACTION_PROPOSE,
    LEARNING_ACTION_REJECT,
    LEARNING_OUTCOME_ACCEPTED,
    LEARNING_OUTCOME_INVALID,
    LEARNING_OUTCOME_NO_EXPERIENCE,
    LEARNING_OUTCOME_PROPOSED,
    LEARNING_OUTCOME_REJECTED,
    run_candidate_lesson_through_runtime_contract,
    run_chat_through_runtime_contract,
)
from bartholomew.orchestrator.safety.governance_store import GovernanceStore

COMPETENCY_ID = "estate_management"
REVIEWER = "taylor"


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


class _Ctx:
    """The duck-typed context every seam function in `runtime_contract` takes."""

    def __init__(self, mem: MemoryStore, objectives: ObjectiveStore):
        self.mem = mem
        self.objective_store = objectives
        self.experience = _Experience()
        self.persona_manager = _Persona()
        self.working_memory = _WorkingMemory()
        self.identity_context = None
        self.governance_store = None
        self.blocking_executor = None


@pytest.fixture
async def ctx(tmp_path):
    db_path = str(tmp_path / "s54.db")
    mem = MemoryStore(db_path)
    await mem.init()
    os_mod.ensure_schema(db_path)
    yield _Ctx(mem, ObjectiveStore(db_path))
    await mem.close()


class _CapturingResponder:
    def __init__(self):
        self.prompt = None

    async def __call__(self, prompt: str) -> str:
        self.prompt = prompt
        return "ok"


# ---------------------------------------------------------------------------
# The experience: a real objective, real evidence, a real recorded outcome.
# ---------------------------------------------------------------------------


def _run_the_experience(ctx) -> int:
    """Steps 1-2 of the acceptance scenario, through the real store.

    A boiler objective accumulates a fact, a decision and an action, plus one
    *proposal* that was considered and never done -- the control that proves
    the lesson is inferred from evidence and not from speculation. It then
    completes, achieved.
    """
    store = ctx.objective_store
    objective = store.open(
        title="Get the boiler serviced before winter",
        outcome_statement="A working boiler with a valid service record",
    )
    store.record(
        objective.id,
        event_kind=os_mod.EVENT_FACT,
        summary="The boiler is still inside its manufacturer warranty period",
    )
    store.record(
        objective.id,
        event_kind=os_mod.EVENT_DECISION,
        summary="Decided to call the warranty line before booking a paid engineer",
    )
    store.record(
        objective.id,
        event_kind=os_mod.EVENT_ACTION,
        summary="Called the boiler warranty line and booked a free service visit",
    )
    store.record(
        objective.id,
        event_kind=os_mod.EVENT_PROPOSAL,
        summary="Could instead pay a private engineer four hundred pounds",
    )
    store.complete(
        objective.id,
        resolution=os_mod.RESOLUTION_ACHIEVED,
        outcome_note="Serviced free under warranty",
    )
    return objective.id


async def _propose(ctx, objective_id: int, **kwargs):
    return await run_candidate_lesson_through_runtime_contract(
        ctx,
        LEARNING_ACTION_PROPOSE,
        objective_id=objective_id,
        competency_id=COMPETENCY_ID,
        **kwargs,
    )


_BOILER_RULE = (
    "Before booking a paid boiler engineer, check the manufacturer warranty "
    "and call the warranty line first"
)


async def _competency_rows(ctx) -> list[dict]:
    return await ctx.mem.list_memories_by_kind(list(COMPETENCY_KINDS), limit=50)


# ===========================================================================
# The acceptance scenario, in order.
# ===========================================================================
class TestAcceptanceScenario:
    async def test_a_completed_objective_produces_a_grounded_candidate(self, ctx):
        """Steps 1-7: outcome -> evidence -> candidate, identifying exactly
        which experience supports it, explicitly as inference, with confidence
        and classification recorded."""
        objective_id = _run_the_experience(ctx)

        result = await _propose(ctx, objective_id, inferred_rule=_BOILER_RULE)

        assert result.outcome == LEARNING_OUTCOME_PROPOSED, result.reason
        lesson = result.lesson

        # Step 5: exactly which experience supports it.
        assert lesson.source.objective_id == objective_id
        assert len(lesson.source.supporting_event_ids) == 3
        evidence_ids = {event.id for event in ctx.objective_store.evidence_events(objective_id)}
        assert set(lesson.source.supporting_event_ids) == evidence_ids

        # The proposal event is not among them: a lesson is never inferred
        # from something that was only ever contemplated.
        all_ids = {event.id for event in ctx.objective_store.events(objective_id)}
        excluded = all_ids - set(lesson.source.supporting_event_ids)
        assert excluded, "the control proposal/state_change events must exist"
        assert not any(
            "four hundred pounds" in observation for observation in lesson.source.observations
        )

        # Step 6: inference, not observation -- and the observations it stands
        # on are carried verbatim alongside it, kept apart from the inference.
        assert lesson.epistemic_status == candidate_learning.EPISTEMIC_INFERENCE
        assert lesson.inferred_rule == _BOILER_RULE
        assert any("warranty period" in text for text in lesson.source.observations)
        assert _BOILER_RULE not in " ".join(lesson.source.observations)

        # Step 7: confidence and classification.
        assert lesson.confidence == candidate_learning.SINGLE_EXPERIENCE_CONFIDENCE
        assert lesson.classification == "personal"
        assert lesson.lesson_kind == candidate_learning.LESSON_PROCEDURAL

        # Step 8's precondition: review is required, and it has not happened.
        assert lesson.requires_review is True
        assert lesson.review_state == candidate_learning.REVIEW_PROPOSED

    async def test_a_proposed_candidate_is_not_yet_knowledge(self, ctx):
        """Critical invariant 1: proposing changes nothing retrievable."""
        objective_id = _run_the_experience(ctx)
        await _propose(ctx, objective_id, inferred_rule=_BOILER_RULE)

        assert await _competency_rows(ctx) == []

        responder = _CapturingResponder()
        await run_chat_through_runtime_contract(
            ctx,
            "should I pay an engineer to service the boiler?",
            responder,
        )
        assert "warranty line first" not in (responder.prompt or "")
        assert "Relevant competency" not in (responder.prompt or "")

    async def test_rejection_leaves_no_consolidated_lesson(self, ctx):
        """Step 9 / critical invariant 3."""
        objective_id = _run_the_experience(ctx)
        proposed = await _propose(ctx, objective_id, inferred_rule=_BOILER_RULE)

        rejected = await run_candidate_lesson_through_runtime_contract(
            ctx,
            LEARNING_ACTION_REJECT,
            competency_id=COMPETENCY_ID,
            slug=proposed.lesson.slug,
            reviewer=REVIEWER,
            review_note="One boiler is not a pattern",
        )

        assert rejected.outcome == LEARNING_OUTCOME_REJECTED
        assert rejected.consolidation is None
        assert rejected.consolidated is False
        assert rejected.lesson.consolidated_key is None

        # Nothing consolidated, anywhere in the retrievable substrate.
        assert await _competency_rows(ctx) == []

        # And later reasoning cannot reach it.
        responder = _CapturingResponder()
        await run_chat_through_runtime_contract(
            ctx,
            "should I pay an engineer to service the boiler?",
            responder,
        )
        assert "warranty line first" not in (responder.prompt or "")

        # The rejection itself is durable and auditable.
        row = await ctx.mem.get_memory(
            candidate_learning.KIND,
            candidate_learning.key_for(COMPETENCY_ID, proposed.lesson.slug),
        )
        stored = json.loads(row["value"])
        assert stored["review_state"] == candidate_learning.REVIEW_REJECTED
        assert stored["reviewer"] == REVIEWER
        assert stored["consolidated_key"] is None

    async def test_a_rejected_candidate_can_never_be_accepted_afterwards(self, ctx):
        """Rejection is terminal -- not merely a state a later call can undo."""
        objective_id = _run_the_experience(ctx)
        proposed = await _propose(ctx, objective_id, inferred_rule=_BOILER_RULE)
        await run_candidate_lesson_through_runtime_contract(
            ctx,
            LEARNING_ACTION_REJECT,
            competency_id=COMPETENCY_ID,
            slug=proposed.lesson.slug,
            reviewer=REVIEWER,
        )

        second = await run_candidate_lesson_through_runtime_contract(
            ctx,
            LEARNING_ACTION_ACCEPT,
            competency_id=COMPETENCY_ID,
            slug=proposed.lesson.slug,
            reviewer=REVIEWER,
        )

        assert second.outcome == LEARNING_OUTCOME_INVALID
        assert "terminal" in (second.reason or "")
        assert await _competency_rows(ctx) == []

    async def test_acceptance_consolidates_into_the_competency_substrate(self, ctx):
        """Step 10: acceptance places the lesson into the existing governed
        competency/memory substrate -- as an S5.1 record, through S5.2's
        write, with its provenance and its low confidence intact."""
        objective_id = _run_the_experience(ctx)
        proposed = await _propose(ctx, objective_id, inferred_rule=_BOILER_RULE)

        accepted = await run_candidate_lesson_through_runtime_contract(
            ctx,
            LEARNING_ACTION_ACCEPT,
            competency_id=COMPETENCY_ID,
            slug=proposed.lesson.slug,
            reviewer=REVIEWER,
            review_note="Reasonable, and cheap to be wrong about",
        )

        assert accepted.outcome == LEARNING_OUTCOME_ACCEPTED, accepted.reason
        assert accepted.consolidated is True
        assert accepted.lesson.consolidated_kind == "competency_heuristic"

        row = await ctx.mem.get_memory(
            "competency_heuristic",
            accepted.lesson.consolidated_key,
        )
        assert row is not None
        stored = json.loads(row["value"])
        assert stored["rule"] == _BOILER_RULE
        assert stored["competency_id"] == COMPETENCY_ID
        # Provenance says where it came from, and that it was Bartholomew's
        # own inference reviewed by a person -- not user instruction.
        assert stored["provenance"]["source_type"] == "experience"
        assert stored["provenance"]["recorded_by"] == "reflection"
        assert f"objective {objective_id}" in stored["provenance"]["detail"]
        assert REVIEWER in stored["provenance"]["detail"]
        # Confidence is not laundered upward by acceptance, and the record
        # still asks to be reviewed before it is acted on.
        assert stored["confidence"] == candidate_learning.SINGLE_EXPERIENCE_CONFIDENCE
        assert stored["supervision"]["requires_review"] is True
        assert stored["classification"] == "personal"

    async def test_an_accepted_lesson_is_retrieved_and_used_later(self, ctx):
        """Step 11, the mandatory one: in a later relevant reasoning
        situation, the accepted lesson is actually retrieved and used through
        the existing S5.3 reasoning seam.

        Nothing in this test touches the learning seam -- it is an ordinary
        chat turn, days later as far as the system is concerned."""
        objective_id = _run_the_experience(ctx)
        proposed = await _propose(ctx, objective_id, inferred_rule=_BOILER_RULE)
        await run_candidate_lesson_through_runtime_contract(
            ctx,
            LEARNING_ACTION_ACCEPT,
            competency_id=COMPETENCY_ID,
            slug=proposed.lesson.slug,
            reviewer=REVIEWER,
        )

        responder = _CapturingResponder()
        result = await run_chat_through_runtime_contract(
            ctx,
            "the boiler needs servicing again -- should I book an engineer?",
            responder,
        )

        # Retrieved and selected by the existing seam...
        context = result.candidate_action.competency_context
        assert context is not None and not context.is_empty(), context.to_dict()
        assert context.competency_id == COMPETENCY_ID
        applied = [record.to_dict() for record in context.applied]
        assert any(record["kind"] == "competency_heuristic" for record in applied)
        assert any(record["provenance"]["source_type"] == "experience" for record in applied)
        # ...and the learned rule reached the actual prompt.
        assert "Relevant competency" in (responder.prompt or "")
        assert "warranty" in (responder.prompt or "").lower()
        # The supervision the lesson carries propagates into the selection --
        # an accepted lesson is retrievable guidance, not a licence to act.
        assert context.requires_review is True

    async def test_control_the_prompt_changes_only_because_of_the_lesson(self, ctx):
        """Non-vacuity control for step 11: the same question, before and
        after the loop runs, differs by exactly the learned guidance."""
        objective_id = _run_the_experience(ctx)
        question = "the boiler needs servicing again -- should I book an engineer?"

        before = _CapturingResponder()
        await run_chat_through_runtime_contract(ctx, question, before)

        proposed = await _propose(ctx, objective_id, inferred_rule=_BOILER_RULE)

        # Proposed but unreviewed: still no change. This is the half that
        # makes the control meaningful -- it is acceptance that moves the
        # needle, not the mere existence of a candidate.
        mid = _CapturingResponder()
        await run_chat_through_runtime_contract(ctx, question, mid)
        assert mid.prompt == before.prompt

        await run_candidate_lesson_through_runtime_contract(
            ctx,
            LEARNING_ACTION_ACCEPT,
            competency_id=COMPETENCY_ID,
            slug=proposed.lesson.slug,
            reviewer=REVIEWER,
        )

        after = _CapturingResponder()
        await run_chat_through_runtime_contract(ctx, question, after)
        assert after.prompt != before.prompt
        assert "Relevant competency" in after.prompt


# ===========================================================================
# Governance
# ===========================================================================
class TestGovernance:
    async def test_review_is_never_anonymous(self, ctx):
        objective_id = _run_the_experience(ctx)
        proposed = await _propose(ctx, objective_id, inferred_rule=_BOILER_RULE)

        for action in (LEARNING_ACTION_ACCEPT, LEARNING_ACTION_REJECT):
            result = await run_candidate_lesson_through_runtime_contract(
                ctx,
                action,
                competency_id=COMPETENCY_ID,
                slug=proposed.lesson.slug,
                reviewer=None,
            )
            assert result.outcome == LEARNING_OUTCOME_INVALID
            assert "reviewer" in (result.reason or "")

        assert await _competency_rows(ctx) == []

    async def test_the_parking_brake_blocks_the_whole_loop(self, ctx, tmp_path):
        objective_id = _run_the_experience(ctx)
        proposed = await _propose(ctx, objective_id, inferred_rule=_BOILER_RULE)

        store = GovernanceStore(ctx.mem.db_path)
        store.engage("training", reason="test", actor="test")
        ctx.governance_store = store
        try:
            blocked_propose = await _propose(ctx, objective_id)
            blocked_accept = await run_candidate_lesson_through_runtime_contract(
                ctx,
                LEARNING_ACTION_ACCEPT,
                competency_id=COMPETENCY_ID,
                slug=proposed.lesson.slug,
                reviewer=REVIEWER,
            )
        finally:
            store.disengage(reason="test", actor="test")

        assert blocked_propose.governance_allowed is False
        assert blocked_propose.outcome == "parking_brake_denied"
        assert blocked_accept.governance_allowed is False
        assert await _competency_rows(ctx) == []

    async def test_governance_failure_fails_closed(self, ctx, monkeypatch):
        import bartholomew.orchestrator.safety.governance_store as gs

        async def _boom(*args, **kwargs):
            raise RuntimeError("governance unreadable")

        monkeypatch.setattr(gs, "is_blocked_fail_closed_off_loop", _boom)

        result = await _propose(ctx, 1)

        assert result.governance_allowed is False
        assert result.outcome == "parking_brake_denied"

    async def test_every_action_writes_exactly_one_reflection(self, ctx):
        objective_id = _run_the_experience(ctx)
        proposed = await _propose(ctx, objective_id, inferred_rule=_BOILER_RULE)
        await run_candidate_lesson_through_runtime_contract(
            ctx,
            LEARNING_ACTION_REJECT,
            competency_id=COMPETENCY_ID,
            slug=proposed.lesson.slug,
            reviewer=REVIEWER,
        )

        rows = await ctx.mem.list_memories_by_kind([REFLECTION_KIND], limit=100)
        learning = (
            [
                json.loads(row["value"])
                for row in rows
                if json.loads(row["value"]).get("surface") == "learning"
            ]
            if rows and "value" in rows[0]
            else []
        )
        # The reflections table is queried directly; the shape above is a
        # defensive fallback for stores that surface reflections as memories.
        if not learning:
            import sqlite3

            conn = sqlite3.connect(ctx.mem.db_path)
            try:
                conn.row_factory = sqlite3.Row
                learning = [
                    json.loads(row["meta"])
                    for row in conn.execute(
                        "SELECT meta FROM reflections WHERE kind = ?",
                        (REFLECTION_KIND,),
                    )
                ]
            finally:
                conn.close()
            learning = [item for item in learning if item.get("surface") == "learning"]

        outcomes = [item["outcome"] for item in learning]
        assert outcomes.count(LEARNING_OUTCOME_PROPOSED) == 1
        assert outcomes.count(LEARNING_OUTCOME_REJECTED) == 1
        rejection = next(item for item in learning if item["outcome"] == LEARNING_OUTCOME_REJECTED)
        assert rejection["consolidated"] is False
        assert rejection["lesson"]["epistemic_status"] == "inference"


# ===========================================================================
# Structural boundaries
# ===========================================================================
class TestBoundaries:
    def test_the_candidate_kind_is_not_a_competency_kind(self):
        """The structural half of "a candidate is not knowledge": the
        retrieval seam's kind filter cannot reach `candidate_lesson`."""
        assert candidate_learning.KIND not in COMPETENCY_KINDS

    def test_the_retrieval_filter_does_not_include_candidates(self):
        import inspect

        source = inspect.getsource(rc._retrieve_memory_context)
        assert "candidate_learning" not in source
        assert "COMPETENCY_KINDS" in source

    def test_candidate_learning_performs_no_io(self):
        """Same discipline as `competency.py` and `training.py`: the module
        is pure data, and `MemoryStore` stays the sole write authority.

        Checked against the code rather than the file, so the module may go
        on *documenting* which authority does the writing."""
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(candidate_learning))
        for node in ast.walk(tree):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                continue
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in {
                        "sqlite3",
                        "aiosqlite",
                        "asyncio",
                    }, alias.name
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[-1] not in {
                    "memory_store",
                    "db_ctx",
                    "blocking_executor",
                    "objective_store",
                }, node.module
            if isinstance(node, ast.Attribute):
                assert node.attr not in {"upsert_memory", "get_memory", "execute"}

    def test_a_candidate_can_never_claim_to_be_an_observation(self):
        lesson = _bare_lesson()
        lesson.epistemic_status = candidate_learning.EPISTEMIC_OBSERVATION
        errors = lesson.validate()
        assert any("epistemic_status" in error for error in errors)

    def test_only_an_accepted_lesson_consolidates(self):
        lesson = _bare_lesson()
        with pytest.raises(ReviewStateError):
            lesson.to_competency_heuristic()

        lesson.reject(reviewer=REVIEWER)
        with pytest.raises(ReviewStateError):
            lesson.to_competency_heuristic()

    def test_review_is_required_and_cannot_be_switched_off(self):
        lesson = _bare_lesson()
        assert lesson.requires_review is True
        with pytest.raises(AttributeError):
            lesson.requires_review = False

    def test_consolidated_key_is_rejected_on_an_unaccepted_candidate(self):
        lesson = _bare_lesson()
        lesson.consolidated_key = "estate_management.smuggled"
        assert any("consolidated_key" in error for error in lesson.validate())

    def test_classification_is_copied_never_upgraded(self):
        """Critical invariant 2: no automatic movement between the personal /
        potentially-generalisable / system classes."""
        for classification in ("personal", "potentially_generalisable", "system"):
            lesson = _bare_lesson(classification=classification)
            lesson.accept(reviewer=REVIEWER)
            assert lesson.to_competency_heuristic().envelope.classification == (classification)

        # And the module never branches on the value -- the same invariant
        # tests/test_competency_no_auto_promotion.py pins for S5.1. Checked
        # over the syntax tree, so the classes may still be *named* in prose.
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(candidate_learning))
        docstrings = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node in docstrings:
                continue
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert "potentially_generalisable" not in node.value

    def test_no_cross_instance_transport_is_introduced(self):
        import inspect

        source = inspect.getsource(candidate_learning)
        for forbidden in ("http", "requests", "urllib", "socket", "export", "publish"):
            assert forbidden not in source.lower().replace("epistemic", ""), forbidden

    def test_ordinary_training_still_cannot_claim_experience(self):
        """The S5.4 lift is one keyword on one call, not a widening."""
        from bartholomew.kernel import training

        submission = training.TrainingSubmission(
            competency_id=COMPETENCY_ID,
            source_type="experience",
            source_detail="pretending to be Bartholomew's own learning",
            records=[],
        )
        assert any("reserved for S5.4" in error for error in submission.validate())
        assert not any(
            "reserved for S5.4" in error
            for error in submission.validate(allow_consolidation_source=True)
        )

        # system_observation stays reserved either way -- out of this scope.
        other = training.TrainingSubmission(
            competency_id=COMPETENCY_ID,
            source_type="system_observation",
            source_detail="x",
            records=[],
        )
        assert any(
            "reserved for S5.4" in error
            for error in other.validate(allow_consolidation_source=True)
        )

    def test_only_the_consolidation_seam_lifts_the_reservation(self):
        """No user-facing ingestion surface passes the lift."""
        import inspect

        from bartholomew import cli
        from bartholomew_api_bridge_v0_1.services.api.routes import training as route

        for module in (route, cli):
            assert "allow_consolidation_source" not in inspect.getsource(module)


class TestProposal:
    async def test_a_live_objective_teaches_nothing_yet(self, ctx):
        objective = ctx.objective_store.open(title="Still going")
        ctx.objective_store.record(
            objective.id,
            event_kind=os_mod.EVENT_ACTION,
            summary="Did a thing",
        )

        result = await _propose(ctx, objective.id)

        assert result.outcome == LEARNING_OUTCOME_NO_EXPERIENCE
        assert "recorded outcome" in (result.reason or "")

    async def test_an_outcome_with_no_evidence_teaches_nothing(self, ctx):
        objective = ctx.objective_store.open(title="Nothing happened")
        ctx.objective_store.record(
            objective.id,
            event_kind=os_mod.EVENT_PROPOSAL,
            summary="Considered calling someone",
        )
        ctx.objective_store.complete(objective.id)

        result = await _propose(ctx, objective.id)

        assert result.outcome == LEARNING_OUTCOME_NO_EXPERIENCE
        assert await _competency_rows(ctx) == []

    async def test_the_default_rule_states_only_what_one_outcome_licenses(self, ctx):
        objective_id = _run_the_experience(ctx)

        result = await _propose(ctx, objective_id)

        rule = result.lesson.inferred_rule
        assert "one recorded occasion" in rule
        assert "warranty line" in rule
        assert "once" in result.lesson.conditions or "Not yet corroborated" in (
            result.lesson.conditions
        )

    async def test_reproposing_supersedes_rather_than_accumulates(self, ctx):
        objective_id = _run_the_experience(ctx)
        first = await _propose(ctx, objective_id, inferred_rule=_BOILER_RULE)
        second = await _propose(ctx, objective_id, inferred_rule="A better wording")

        assert first.lesson.key() == second.lesson.key()
        rows = await ctx.mem.list_memories_by_kind([candidate_learning.KIND], limit=50)
        assert len(rows) == 1

    async def test_a_sensitive_lesson_is_queued_for_consent_not_stored(self, ctx):
        """The existing consent machinery governs this path too.

        Nothing about the learning loop bypasses `privacy_guard`: a candidate
        whose content trips the sensitivity scan is queued for review rather
        than stored, and the seam reports that truthfully instead of claiming
        a proposal exists. Found by this slice's own first test run, when an
        evidence summary happened to contain the word "private".
        """
        objective = ctx.objective_store.open(title="Sort out the health cover")
        ctx.objective_store.record(
            objective.id,
            event_kind=os_mod.EVENT_ACTION,
            summary="Called the private health insurer and renewed the policy",
        )
        ctx.objective_store.complete(objective.id)

        result = await _propose(ctx, objective.id)

        assert result.outcome == "not_stored"
        assert result.lesson is None
        pending = await ctx.mem.list_pending_sensitive_writes(limit=10)
        assert any(entry["kind"] == candidate_learning.KIND for entry in pending)
        assert await _competency_rows(ctx) == []

    async def test_the_candidate_carries_its_reflection_row(self, ctx):
        objective_id = _run_the_experience(ctx)
        result = await _propose(ctx, objective_id, inferred_rule=_BOILER_RULE)
        assert result.lesson.reflection_row_id is not None


def _bare_lesson(**kwargs) -> CandidateLesson:
    defaults = {
        "competency_id": COMPETENCY_ID,
        "slug": "lesson_from_objective_1",
        "source": SourceExperience(
            objective_id=1,
            objective_title="Get the boiler serviced",
            resolution="achieved",
            supporting_event_ids=[1],
            observations=["action: called the warranty line"],
        ),
        "inferred_rule": _BOILER_RULE,
    }
    defaults.update(kwargs)
    return CandidateLesson(**defaults)
