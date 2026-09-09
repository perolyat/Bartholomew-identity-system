"""Golden Path 4 -- a correction changes a *later* task.

`W03_E_CONTRACT.md` calls this the wave's most important longitudinal scenario,
and `W03_TEST_CONTRACTS.md` Sec.6 makes it a required contract: a correction during
one task becomes a candidate lesson, is **explicitly approved**, supersedes the
prior behaviour, and changes a later relevant task -- while an *unapproved*
correction changes nothing.

Both halves are here, and the unapproved half is the load-bearing one. A test
that only proved acceptance works would be satisfied by a system that accepted
everything, which is precisely the system this wave's deferral register (#4,
automatic lesson acceptance) exists to keep unbuilt.

Everything below runs against real authorities: a real `ObjectiveStore`
experience, the real candidate-lesson seam, the real candidate-bound acceptance
authorization, the real `MemoryStore`, and the real chat retrieval seam standing
in for "a later relevant task". Nothing here is doubled except the model
responder, which is a capturing stub precisely so the test can assert what
reached the prompt.
"""

from __future__ import annotations

import json

import pytest

from bartholomew.kernel import candidate_learning
from bartholomew.kernel import objective_store as os_mod
from bartholomew.kernel.competency import COMPETENCY_KINDS
from bartholomew.kernel.memory.privacy_guard import set_consent_handler
from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.objective_store import ObjectiveStore
from bartholomew.kernel.runtime_contract import (
    LEARNING_ACTION_ACCEPT,
    LEARNING_ACTION_PROPOSE,
    LEARNING_OUTCOME_ACCEPTED,
    LEARNING_OUTCOME_PROPOSED,
    grant_learning_acceptance_approval,
    run_candidate_lesson_through_runtime_contract,
    run_chat_through_runtime_contract,
)

pytestmark = [pytest.mark.integration]

COMPETENCY_ID = "windows_desk_habits"
REVIEWER = "taylor"

#: The correction, in the person's own words during the first task.
CORRECTION = "When I ask you to open my music, open Spotify -- not the browser player"

#: A later task that is *not* relevant. The control for relevance: a retrieval
#: that handed every accepted lesson to every task would pass the tests above
#: and fail this one.
UNRELATED_TASK = "what time does the pharmacy on the high street close on Saturdays"

#: The later, relevant task. Deliberately not a restatement of the first: a
#: lesson that only fired on the identical sentence would prove nothing about
#: whether it generalises to a later *situation*.
LATER_TASK = "put some music on while I work through the invoices"


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
    """The duck-typed runtime context every seam in `runtime_contract` takes."""

    def __init__(self, mem: MemoryStore, objectives: ObjectiveStore):
        self.mem = mem
        self.objective_store = objectives
        self.experience = _Experience()
        self.persona_manager = _Persona()
        self.working_memory = _WorkingMemory()
        self.identity_context = None
        self.governance_store = None
        self.blocking_executor = None


class _CapturingResponder:
    """The model, replaced by something that records what it was actually asked.

    This is the one stub in the file and it is not a governance authority: it
    stands where a language model stands and answers nothing. What the test
    asserts on is the prompt the real seam built.
    """

    def __init__(self):
        self.prompt = None

    async def __call__(self, prompt: str) -> str:
        self.prompt = prompt
        return "ok"


@pytest.fixture
async def ctx(tmp_path):
    set_consent_handler(None)
    db_path = str(tmp_path / "path4.db")
    mem = MemoryStore(db_path)
    await mem.init()
    os_mod.ensure_schema(db_path)
    try:
        yield _Ctx(mem, ObjectiveStore(db_path))
    finally:
        await mem.close()
        set_consent_handler(None)


# ---------------------------------------------------------------------------
# The first task, and the correction inside it
# ---------------------------------------------------------------------------


def _the_first_task_and_the_correction(ctx) -> int:
    """Task one: Bartholomew got it wrong, and the person said so.

    A real objective with real evidence events, completed. The correction is
    recorded as what it is -- something the person said during the task -- and
    not as a lesson. Turning it into one is the next step, and it needs a human.
    """
    store = ctx.objective_store
    objective = store.open(
        title="Put some music on",
        outcome_statement="Music playing, from the source the person actually wanted",
    )
    store.record(
        objective.id,
        event_kind=os_mod.EVENT_ACTION,
        summary="Opened the browser music player when asked to put music on",
    )
    store.record(
        objective.id,
        event_kind=os_mod.EVENT_FACT,
        summary=f"The person corrected me: {CORRECTION}",
    )
    store.record(
        objective.id,
        event_kind=os_mod.EVENT_DECISION,
        summary="Closed the browser player and launched the Spotify desktop app instead",
    )
    store.complete(
        objective.id,
        resolution=os_mod.RESOLUTION_ACHIEVED,
        outcome_note="Spotify was what they wanted",
    )
    return objective.id


async def _propose_the_lesson(ctx, objective_id: int):
    result = await run_candidate_lesson_through_runtime_contract(
        ctx,
        LEARNING_ACTION_PROPOSE,
        objective_id=objective_id,
        competency_id=COMPETENCY_ID,
        inferred_rule=CORRECTION,
    )
    assert result.outcome == LEARNING_OUTCOME_PROPOSED, result.reason
    return result.lesson


async def _later_task(ctx) -> _CapturingResponder:
    """A later, relevant task. Nothing here touches the learning seam."""
    responder = _CapturingResponder()
    await run_chat_through_runtime_contract(ctx, LATER_TASK, responder)
    return responder


async def _competency_rows(ctx) -> list[dict]:
    return await ctx.mem.list_memories_by_kind(list(COMPETENCY_KINDS), limit=50)


# ---------------------------------------------------------------------------
# The half that must NOT happen
# ---------------------------------------------------------------------------


async def test_an_unapproved_correction_does_not_influence_the_later_task(ctx):
    """The load-bearing half of Golden Path 4.

    The correction was made, the candidate lesson exists, and nothing about the
    later task is different -- because no person accepted it. This is what
    "manual acceptance remains authoritative" means when it is a behaviour
    rather than a policy setting.
    """
    objective_id = _the_first_task_and_the_correction(ctx)
    lesson = await _propose_the_lesson(ctx, objective_id)

    # The candidate exists and is proposed, so the test is not passing merely
    # because nothing was ever proposed.
    assert lesson.review_state == candidate_learning.REVIEW_PROPOSED
    assert lesson.requires_review is True

    # Nothing consolidated, anywhere in the retrievable substrate ...
    assert await _competency_rows(ctx) == []

    # ... and the later task is untouched by it.
    responder = await _later_task(ctx)
    assert "Relevant competency" not in (responder.prompt or "")
    assert "Spotify" not in (responder.prompt or "")
    assert "not the browser player" not in (responder.prompt or "")


async def test_accepting_without_the_candidate_bound_authorization_is_refused(ctx):
    """The other way an unapproved correction could sneak through.

    Acceptance is not merely "call accept". It requires an authorization granted
    for *this* candidate as it stands. Without one, the accept branch refuses
    and consolidates nothing.
    """
    objective_id = _the_first_task_and_the_correction(ctx)
    lesson = await _propose_the_lesson(ctx, objective_id)

    result = await run_candidate_lesson_through_runtime_contract(
        ctx,
        LEARNING_ACTION_ACCEPT,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        reviewer=REVIEWER,
    )

    assert result.outcome != LEARNING_OUTCOME_ACCEPTED
    # Refused for the reason that matters, not for some other defect.
    assert "authorization bound to that candidate" in (result.reason or "")
    assert await _competency_rows(ctx) == []
    responder = await _later_task(ctx)
    assert "Relevant competency" not in (responder.prompt or "")


# ---------------------------------------------------------------------------
# The half that must happen, once a person says so
# ---------------------------------------------------------------------------


async def test_an_explicitly_accepted_correction_changes_the_later_task(ctx):
    """The whole path: correction -> candidate -> explicit acceptance -> later use."""
    objective_id = _the_first_task_and_the_correction(ctx)
    lesson = await _propose_the_lesson(ctx, objective_id)

    await grant_learning_acceptance_approval(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        approver=REVIEWER,
        note="Yes -- Spotify, not the browser",
    )
    accepted = await run_candidate_lesson_through_runtime_contract(
        ctx,
        LEARNING_ACTION_ACCEPT,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        reviewer=REVIEWER,
    )
    assert accepted.outcome == LEARNING_OUTCOME_ACCEPTED, accepted.reason
    assert accepted.consolidated is True

    # A *later* task -- a different sentence about a different situation --
    # now carries the correction.
    responder = await _later_task(ctx)
    assert "Relevant competency" in (responder.prompt or "")
    assert "Spotify" in (responder.prompt or "")

    # And the correction did not launder its own confidence upward on the way
    # in: an accepted lesson is retrievable guidance, not a licence to act.
    row = await ctx.mem.get_memory("competency_heuristic", accepted.lesson.consolidated_key)
    stored = json.loads(row["value"])
    assert stored["supervision"]["requires_review"] is True
    assert stored["confidence"] == candidate_learning.SINGLE_EXPERIENCE_CONFIDENCE


async def test_the_later_task_differs_by_exactly_the_accepted_correction(ctx):
    """Non-vacuity: the same later task, before and after, differing by the lesson.

    Without this control, the test above would pass on a system whose prompt
    happened to mention Spotify for some unrelated reason.
    """
    objective_id = _the_first_task_and_the_correction(ctx)

    before = await _later_task(ctx)
    assert "Relevant competency" not in (before.prompt or "")

    lesson = await _propose_the_lesson(ctx, objective_id)
    await grant_learning_acceptance_approval(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        approver=REVIEWER,
    )
    await run_candidate_lesson_through_runtime_contract(
        ctx,
        LEARNING_ACTION_ACCEPT,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        reviewer=REVIEWER,
    )

    after = await _later_task(ctx)
    assert "Relevant competency" in (after.prompt or "")
    assert "Spotify" in (after.prompt or "")
    assert "Spotify" not in (before.prompt or "")


async def test_the_accepted_correction_reaches_only_a_relevant_later_task(ctx):
    """Relevance, controlled: the lesson is retrieved for music, not for a pharmacy."""
    objective_id = _the_first_task_and_the_correction(ctx)
    lesson = await _propose_the_lesson(ctx, objective_id)
    await grant_learning_acceptance_approval(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        approver=REVIEWER,
    )
    await run_candidate_lesson_through_runtime_contract(
        ctx,
        LEARNING_ACTION_ACCEPT,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        reviewer=REVIEWER,
    )

    relevant = await _later_task(ctx)
    assert "Spotify" in (relevant.prompt or "")

    unrelated = _CapturingResponder()
    await run_chat_through_runtime_contract(ctx, UNRELATED_TASK, unrelated)
    assert "Spotify" not in (unrelated.prompt or "")


async def test_a_later_correction_supersedes_the_earlier_one_for_the_later_task(ctx):
    """Supersession: the currently-valid correction wins, and history is kept.

    The person changes their mind. The accepted lesson is corrected through the
    existing governed correction seam (S5.2's training write, source type
    `correction`), which archives the superseded revision. The later task then
    carries the *corrected* rule and not the old one.
    """
    from bartholomew.kernel.runtime_contract import (
        run_competency_correction_through_runtime_contract,
    )

    objective_id = _the_first_task_and_the_correction(ctx)
    lesson = await _propose_the_lesson(ctx, objective_id)
    await grant_learning_acceptance_approval(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        approver=REVIEWER,
    )
    accepted = await run_candidate_lesson_through_runtime_contract(
        ctx,
        LEARNING_ACTION_ACCEPT,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        reviewer=REVIEWER,
    )
    key = accepted.lesson.consolidated_key
    stored = json.loads((await ctx.mem.get_memory("competency_heuristic", key))["value"])

    corrected_rule = (
        "When I ask you to open my music, open the browser player after all -- not Spotify"
    )
    result = await run_competency_correction_through_runtime_contract(
        ctx,
        kind="competency_heuristic",
        key=key,
        corrected_by=REVIEWER,
        expected_revision=int(stored.get("revision", 1)),
        updates={"rule": corrected_rule},
    )
    assert getattr(result, "governance_allowed", True), getattr(result, "reason", None)

    later = await _later_task(ctx)
    prompt = later.prompt or ""
    assert "browser player after all" in prompt, prompt
    assert CORRECTION not in prompt

    # History is preserved: the superseded revision is archived, not erased.
    # `competency_revision` is the archive kind the correction seam writes on
    # this baseline (`<key>@rN`); W03-D extends the same `key@rN` shape to facts
    # and preferences under `memory_revision`, and either counts as history.
    archived = await ctx.mem.list_memories_by_kind(
        ["competency_revision", "memory_revision"],
        limit=20,
    )
    assert any(f"{key}@r" in (row.get("key") or "") for row in archived), [
        r.get("key") for r in archived
    ]


async def test_a_rejected_correction_can_never_come_back(ctx):
    """Rejection is terminal, and a later task never sees it.

    The third state a correction can be in, and the one a system that "learns
    from everything eventually" would quietly lose.
    """
    from bartholomew.kernel.runtime_contract import LEARNING_ACTION_REJECT

    objective_id = _the_first_task_and_the_correction(ctx)
    lesson = await _propose_the_lesson(ctx, objective_id)

    await grant_learning_acceptance_approval(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        approver=REVIEWER,
    )
    await run_candidate_lesson_through_runtime_contract(
        ctx,
        LEARNING_ACTION_REJECT,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        reviewer=REVIEWER,
        review_note="I only meant that once",
    )

    late = await run_candidate_lesson_through_runtime_contract(
        ctx,
        LEARNING_ACTION_ACCEPT,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        reviewer=REVIEWER,
    )
    assert late.outcome != LEARNING_OUTCOME_ACCEPTED
    assert await _competency_rows(ctx) == []

    responder = await _later_task(ctx)
    assert "Relevant competency" not in (responder.prompt or "")


async def test_time_and_tasks_passing_consolidate_nothing_on_their_own(ctx):
    """Deferral #4 as scenario behaviour: nothing accepts a candidate but a person.

    The shipped execution mode is a `Final` constant, so this test does not
    wire a permissive policy (the permissive-policy proof is W03-D's, in
    `test_w03d_manual_acceptance_authoritative.py`). What it pins is the
    scenario: a proposed candidate, then another task and another retrieval,
    and still nothing consolidated.
    """
    from bartholomew.kernel import learning_policy

    assert learning_policy.SHIPPED_EXECUTION_MODE == "shadow"

    objective_id = _the_first_task_and_the_correction(ctx)
    await _propose_the_lesson(ctx, objective_id)

    # Time passing, another task, another retrieval -- nothing consolidates.
    await _later_task(ctx)
    assert await _competency_rows(ctx) == []
