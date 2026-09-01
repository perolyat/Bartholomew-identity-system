"""
Learning acceptance authorization: the governance follow-up to S5.4.

The rule these tests exist to hold:

    Bartholomew may autonomously conclude "I may have learned something".
    Bartholomew may NOT autonomously conclude "this lesson is now trusted
    knowledge".

So `learning_propose` and `learning_reject` run on the shipped identity's
standing grants, and `learning_accept` -- the one durable mutation that makes
a lesson retrievable in a later, unrelated reasoning turn -- requires an
explicit authorization bound to one specific candidate lesson. There is no
"learning enabled" switch: allowlisting `learning_accept` does not make it
reachable, which suite C proves directly.

Everything here runs against real authorities: a real `MemoryStore`, a real
`ObjectiveStore` objective with real evidence, the real `GovernanceStore`
Parking Brake, and the real `Identity.yaml` this repository ships.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from bartholomew.kernel import candidate_learning, learning_authorization
from bartholomew.kernel import objective_store as os_mod
from bartholomew.kernel.competency import COMPETENCY_KINDS
from bartholomew.kernel.memory.privacy_guard import set_consent_handler
from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.objective_store import ObjectiveStore
from bartholomew.kernel.reflection import REFLECTION_KIND
from bartholomew.kernel.runtime_contract import (
    LEARNING_ACTION_ACCEPT,
    LEARNING_ACTION_APPROVE_ACCEPTANCE,
    LEARNING_ACTION_PROPOSE,
    LEARNING_ACTION_REJECT,
    LEARNING_OUTCOME_ACCEPTED,
    LEARNING_OUTCOME_APPROVAL_REQUIRED,
    LEARNING_OUTCOME_BRAKE_DENIED,
    LEARNING_OUTCOME_GOVERNANCE_DENIED,
    LEARNING_OUTCOME_INVALID,
    LEARNING_OUTCOME_NOT_FOUND,
    LEARNING_OUTCOME_PROPOSED,
    LEARNING_OUTCOME_REJECTED,
    grant_learning_acceptance_approval,
    run_candidate_lesson_through_runtime_contract,
    run_chat_through_runtime_contract,
)
from bartholomew.orchestrator.safety.governance_store import GovernanceStore
from identity_interpreter.identity_context import IdentityContext

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPETENCY_ID = "estate_management"
REVIEWER = "taylor"
APPROVER = "taylor"

_RULE = (
    "Before booking a paid boiler engineer, check the manufacturer warranty "
    "and call the warranty line first"
)
_OTHER_RULE = "Always book the cheapest engineer available, warranty or not"


@pytest.fixture(autouse=True)
def _reset_consent_handler():
    set_consent_handler(None)
    yield
    set_consent_handler(None)


def _shipped_identity_context() -> IdentityContext:
    """The allowlist this repository actually ships, not a stand-in."""
    data = yaml.safe_load((REPO_ROOT / "Identity.yaml").read_text(encoding="utf-8"))
    tool_use = data["tool_use"]
    return IdentityContext(
        tool_use_default_allowed=bool(tool_use.get("default_allowed", False)),
        tool_use_allowlist=list(tool_use.get("allowlist", [])),
    )


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
    def __init__(self, mem, objectives, identity):
        self.mem = mem
        self.objective_store = objectives
        self.experience = _Experience()
        self.persona_manager = _Persona()
        self.working_memory = _WorkingMemory()
        self.identity_context = identity
        self.governance_store = None
        self.blocking_executor = None


@pytest.fixture
async def ctx(tmp_path):
    db_path = str(tmp_path / "learning_authz.db")
    mem = MemoryStore(db_path)
    await mem.init()
    os_mod.ensure_schema(db_path)
    yield _Ctx(mem, ObjectiveStore(db_path), _shipped_identity_context())
    await mem.close()


def _run_the_experience(ctx, title="Get the boiler serviced before winter") -> int:
    store = ctx.objective_store
    objective = store.open(
        title=title,
        outcome_statement="A working boiler with a valid service record",
    )
    store.record(
        objective.id,
        event_kind=os_mod.EVENT_FACT,
        summary="The boiler is still inside its manufacturer warranty period",
    )
    store.record(
        objective.id,
        event_kind=os_mod.EVENT_ACTION,
        summary="Called the boiler warranty line and booked a free service visit",
    )
    store.complete(
        objective.id,
        resolution=os_mod.RESOLUTION_ACHIEVED,
        outcome_note="Serviced free under warranty",
    )
    return objective.id


async def _propose(ctx, objective_id: int, rule: str = _RULE, **kwargs):
    return await run_candidate_lesson_through_runtime_contract(
        ctx,
        LEARNING_ACTION_PROPOSE,
        objective_id=objective_id,
        competency_id=COMPETENCY_ID,
        inferred_rule=rule,
        **kwargs,
    )


async def _accept(ctx, slug: str, reviewer: str = REVIEWER):
    return await run_candidate_lesson_through_runtime_contract(
        ctx,
        LEARNING_ACTION_ACCEPT,
        competency_id=COMPETENCY_ID,
        slug=slug,
        reviewer=reviewer,
    )


async def _competency_rows(ctx) -> list[dict]:
    return await ctx.mem.list_memories_by_kind(list(COMPETENCY_KINDS), limit=50)


async def _learning_reflections(ctx) -> list[dict]:
    import sqlite3

    conn = sqlite3.connect(ctx.mem.db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = [
            json.loads(row["meta"])
            for row in conn.execute(
                "SELECT meta FROM reflections WHERE kind = ?",
                (REFLECTION_KIND,),
            )
        ]
    finally:
        conn.close()
    return [item for item in rows if item.get("surface") == "learning"]


class _CapturingResponder:
    def __init__(self):
        self.prompt = None

    async def __call__(self, prompt: str) -> str:
        self.prompt = prompt
        return "ok"


async def _asks_about_the_boiler(ctx) -> str:
    responder = _CapturingResponder()
    await run_chat_through_runtime_contract(
        ctx,
        "the boiler needs servicing again -- should I book an engineer?",
        responder,
    )
    return responder.prompt or ""


# ===========================================================================
# A. Propose
# ===========================================================================
class TestPropose:
    async def test_the_shipped_identity_allows_proposing(self, ctx):
        """An authenticated, allowed identity creates a candidate lesson --
        on the allowlist this repository actually ships, not a test-only one."""
        assert LEARNING_ACTION_PROPOSE in ctx.identity_context.tool_use_allowlist

        objective_id = _run_the_experience(ctx)
        result = await _propose(ctx, objective_id)

        assert result.outcome == LEARNING_OUTCOME_PROPOSED, result.reason
        assert result.governance_allowed is True
        assert result.lesson.review_state == candidate_learning.REVIEW_PROPOSED
        assert result.lesson.requires_review is True

        row = await ctx.mem.get_memory(
            candidate_learning.KIND,
            candidate_learning.key_for(COMPETENCY_ID, result.lesson.slug),
        )
        assert row is not None

    async def test_a_proposal_consolidates_no_trusted_learning(self, ctx):
        """ "I may have learned something" is all a proposal says."""
        objective_id = _run_the_experience(ctx)
        result = await _propose(ctx, objective_id)

        assert result.consolidation is None
        assert result.consolidated is False
        assert await _competency_rows(ctx) == []
        assert candidate_learning.KIND not in COMPETENCY_KINDS
        assert "warranty line first" not in await _asks_about_the_boiler(ctx)

    async def test_proposing_grants_no_acceptance_authorization(self, ctx):
        """The proposal must not authorise its own consolidation."""
        objective_id = _run_the_experience(ctx)
        proposed = await _propose(ctx, objective_id)

        approval_row = await ctx.mem.get_memory(
            learning_authorization.KIND,
            candidate_learning.key_for(COMPETENCY_ID, proposed.lesson.slug),
        )
        assert approval_row is None


# ===========================================================================
# B. Reject
# ===========================================================================
class TestReject:
    async def test_the_shipped_identity_allows_rejecting(self, ctx):
        assert LEARNING_ACTION_REJECT in ctx.identity_context.tool_use_allowlist

        objective_id = _run_the_experience(ctx)
        proposed = await _propose(ctx, objective_id)

        rejected = await run_candidate_lesson_through_runtime_contract(
            ctx,
            LEARNING_ACTION_REJECT,
            competency_id=COMPETENCY_ID,
            slug=proposed.lesson.slug,
            reviewer=REVIEWER,
            review_note="One boiler is not a pattern",
        )

        assert rejected.outcome == LEARNING_OUTCOME_REJECTED, rejected.reason
        assert rejected.governance_allowed is True

    async def test_rejection_leaves_nothing_accepted_and_nothing_retrievable(self, ctx):
        objective_id = _run_the_experience(ctx)
        proposed = await _propose(ctx, objective_id)
        rejected = await run_candidate_lesson_through_runtime_contract(
            ctx,
            LEARNING_ACTION_REJECT,
            competency_id=COMPETENCY_ID,
            slug=proposed.lesson.slug,
            reviewer=REVIEWER,
        )

        assert rejected.consolidation is None
        assert rejected.consolidated is False
        assert rejected.lesson.consolidated_key is None
        assert await _competency_rows(ctx) == []
        assert "warranty line first" not in await _asks_about_the_boiler(ctx)

    async def test_rejection_needs_no_acceptance_approval(self, ctx):
        """Rejection is conservative: it is deliberately *not* gated on the
        acceptance authorization, because refusing to learn something can
        never be the unsafe direction."""
        objective_id = _run_the_experience(ctx)
        proposed = await _propose(ctx, objective_id)

        approval_row = await ctx.mem.get_memory(
            learning_authorization.KIND,
            candidate_learning.key_for(COMPETENCY_ID, proposed.lesson.slug),
        )
        assert approval_row is None

        rejected = await run_candidate_lesson_through_runtime_contract(
            ctx,
            LEARNING_ACTION_REJECT,
            competency_id=COMPETENCY_ID,
            slug=proposed.lesson.slug,
            reviewer=REVIEWER,
        )
        assert rejected.outcome == LEARNING_OUTCOME_REJECTED


# ===========================================================================
# C. Accept without explicit authorization
# ===========================================================================
class TestAcceptWithoutAuthorization:
    async def test_acceptance_is_refused_and_nothing_durable_is_created(self, ctx):
        objective_id = _run_the_experience(ctx)
        proposed = await _propose(ctx, objective_id)

        refused = await _accept(ctx, proposed.lesson.slug)

        assert refused.governance_allowed is False
        assert refused.outcome == LEARNING_OUTCOME_APPROVAL_REQUIRED
        assert "explicit authorization" in (refused.reason or "")
        assert refused.consolidation is None
        assert refused.consolidated is False
        assert await _competency_rows(ctx) == []

        # The candidate is untouched: a refused acceptance must not leave a
        # half-accepted row behind.
        row = await ctx.mem.get_memory(
            candidate_learning.KIND,
            candidate_learning.key_for(COMPETENCY_ID, proposed.lesson.slug),
        )
        stored = json.loads(row["value"])
        assert stored["review_state"] == candidate_learning.REVIEW_PROPOSED
        assert stored["reviewer"] is None
        assert stored["consolidated_key"] is None

        assert "warranty line first" not in await _asks_about_the_boiler(ctx)

    async def test_the_shipped_allowlist_does_not_grant_acceptance(self, ctx):
        assert LEARNING_ACTION_ACCEPT not in ctx.identity_context.tool_use_allowlist

    async def test_allowlisting_acceptance_does_not_make_it_reachable(self, ctx):
        """There is no "learning enabled" switch, and this is the test that
        says so: even `default_allowed: true` plus an explicit
        `learning_accept` entry refuses, because acceptance is not gated on
        the allowlist at all."""
        objective_id = _run_the_experience(ctx)
        proposed = await _propose(ctx, objective_id)

        ctx.identity_context = IdentityContext(
            tool_use_default_allowed=True,
            tool_use_allowlist=[LEARNING_ACTION_ACCEPT],
        )
        refused = await _accept(ctx, proposed.lesson.slug)

        assert refused.governance_allowed is False
        assert refused.outcome == LEARNING_OUTCOME_APPROVAL_REQUIRED
        assert await _competency_rows(ctx) == []

    async def test_the_refusal_is_recorded(self, ctx):
        objective_id = _run_the_experience(ctx)
        proposed = await _propose(ctx, objective_id)
        await _accept(ctx, proposed.lesson.slug)

        outcomes = [item["outcome"] for item in await _learning_reflections(ctx)]
        assert outcomes.count(LEARNING_OUTCOME_APPROVAL_REQUIRED) == 1

    async def test_approval_cannot_be_granted_for_a_candidate_that_does_not_exist(self, ctx):
        result = await grant_learning_acceptance_approval(
            ctx,
            competency_id=COMPETENCY_ID,
            slug="lesson_from_objective_999",
            approver=APPROVER,
        )
        assert result.granted is False
        assert result.outcome == LEARNING_OUTCOME_NOT_FOUND

    async def test_approval_is_never_anonymous(self, ctx):
        objective_id = _run_the_experience(ctx)
        proposed = await _propose(ctx, objective_id)

        result = await grant_learning_acceptance_approval(
            ctx,
            competency_id=COMPETENCY_ID,
            slug=proposed.lesson.slug,
            approver="",
        )
        assert result.granted is False
        assert result.outcome == LEARNING_OUTCOME_INVALID
        assert "approver" in (result.reason or "")

        assert (await _accept(ctx, proposed.lesson.slug)).consolidated is False


# ===========================================================================
# D. Accept with explicit authorization
# ===========================================================================
class TestAcceptWithAuthorization:
    async def test_approval_then_acceptance_consolidates_and_is_retrievable(self, ctx):
        objective_id = _run_the_experience(ctx)
        proposed = await _propose(ctx, objective_id)

        approval = await grant_learning_acceptance_approval(
            ctx,
            competency_id=COMPETENCY_ID,
            slug=proposed.lesson.slug,
            approver=APPROVER,
            note="Reasonable, and cheap to be wrong about",
        )
        assert approval.granted is True, approval.reason

        # Bound to the exact candidate, by key and by content.
        assert approval.approval.key() == candidate_learning.key_for(
            COMPETENCY_ID,
            proposed.lesson.slug,
        )
        assert approval.approval.candidate_fingerprint == learning_authorization.fingerprint_for(
            proposed.lesson,
        )

        accepted = await _accept(ctx, proposed.lesson.slug)
        assert accepted.outcome == LEARNING_OUTCOME_ACCEPTED, accepted.reason
        assert accepted.consolidated is True
        assert accepted.lesson.consolidated_kind in COMPETENCY_KINDS

        # ...and a later, unrelated reasoning turn actually retrieves it
        # through the existing competency seam.
        prompt = await _asks_about_the_boiler(ctx)
        assert "Relevant competency" in prompt
        assert "warranty" in prompt.lower()

    async def test_acceptance_records_approver_identity_and_provenance(self, ctx):
        objective_id = _run_the_experience(ctx)
        proposed = await _propose(ctx, objective_id)
        await grant_learning_acceptance_approval(
            ctx,
            competency_id=COMPETENCY_ID,
            slug=proposed.lesson.slug,
            approver=APPROVER,
            note="Cheap to be wrong about",
        )

        # The durable approval record: who, when, on what.
        row = await ctx.mem.get_memory(
            learning_authorization.KIND,
            candidate_learning.key_for(COMPETENCY_ID, proposed.lesson.slug),
        )
        stored = json.loads(row["value"])
        assert stored["approver"] == APPROVER
        assert stored["granted_at"]
        assert stored["note"] == "Cheap to be wrong about"
        assert stored["objective_id"] == objective_id
        assert stored["candidate_fingerprint"]

        # The canonical audit trail: one Reflection for the grant itself.
        reflections = await _learning_reflections(ctx)
        grants = [
            item for item in reflections if item.get("action") == LEARNING_ACTION_APPROVE_ACCEPTANCE
        ]
        assert len(grants) == 1
        assert grants[0]["outcome"] == "approved"
        assert grants[0]["approver"] == APPROVER
        assert grants[0]["consolidated"] is False
        assert grants[0]["lesson"]["objective_id"] == objective_id

        # ...and the acceptance is a separate, later record.
        await _accept(ctx, proposed.lesson.slug)
        accepted = [
            item
            for item in await _learning_reflections(ctx)
            if item.get("outcome") == LEARNING_OUTCOME_ACCEPTED
        ]
        assert len(accepted) == 1
        assert accepted[0]["reviewer"] == REVIEWER
        assert accepted[0]["consolidated"] is True

    async def test_the_approval_is_not_a_standing_grant(self, ctx):
        """Approving one candidate authorises exactly one consolidation, not
        a mode in which learning is switched on."""
        first_objective = _run_the_experience(ctx)
        first = await _propose(ctx, first_objective)
        await grant_learning_acceptance_approval(
            ctx,
            competency_id=COMPETENCY_ID,
            slug=first.lesson.slug,
            approver=APPROVER,
        )
        assert (await _accept(ctx, first.lesson.slug)).outcome == LEARNING_OUTCOME_ACCEPTED

        second_objective = _run_the_experience(ctx, title="Get the gutters cleared")
        second = await _propose(ctx, second_objective, rule=_OTHER_RULE)
        refused = await _accept(ctx, second.lesson.slug)

        assert refused.outcome == LEARNING_OUTCOME_APPROVAL_REQUIRED
        rows = await _competency_rows(ctx)
        assert len(rows) == 1


# ===========================================================================
# E. Approval binding
# ===========================================================================
class TestApprovalBinding:
    async def test_approval_for_candidate_a_cannot_accept_candidate_b(self, ctx):
        a_objective = _run_the_experience(ctx)
        b_objective = _run_the_experience(ctx, title="Get the gutters cleared")
        candidate_a = (await _propose(ctx, a_objective)).lesson
        candidate_b = (await _propose(ctx, b_objective, rule=_OTHER_RULE)).lesson
        assert candidate_a.slug != candidate_b.slug

        await grant_learning_acceptance_approval(
            ctx,
            competency_id=COMPETENCY_ID,
            slug=candidate_a.slug,
            approver=APPROVER,
        )

        refused = await _accept(ctx, candidate_b.slug)
        assert refused.governance_allowed is False
        assert refused.outcome == LEARNING_OUTCOME_APPROVAL_REQUIRED
        assert await _competency_rows(ctx) == []

        # ...and A itself is still acceptable: the binding refuses B, it does
        # not invalidate the approval that was actually granted.
        assert (await _accept(ctx, candidate_a.slug)).outcome == LEARNING_OUTCOME_ACCEPTED

    async def test_changing_the_candidate_invalidates_the_approval(self, ctx):
        """Re-proposing supersedes the candidate at the same key. The
        approval was granted for what the reviewer read, not for whatever
        later occupies that slot."""
        objective_id = _run_the_experience(ctx)
        proposed = await _propose(ctx, objective_id)
        await grant_learning_acceptance_approval(
            ctx,
            competency_id=COMPETENCY_ID,
            slug=proposed.lesson.slug,
            approver=APPROVER,
        )

        replaced = await _propose(ctx, objective_id, rule=_OTHER_RULE)
        assert replaced.lesson.slug == proposed.lesson.slug
        assert replaced.lesson.inferred_rule == _OTHER_RULE

        refused = await _accept(ctx, proposed.lesson.slug)
        assert refused.governance_allowed is False
        assert refused.outcome == LEARNING_OUTCOME_APPROVAL_REQUIRED
        assert "changed since it was approved" in (refused.reason or "")
        assert await _competency_rows(ctx) == []

        # A fresh approval, for what the candidate now says, restores it.
        await grant_learning_acceptance_approval(
            ctx,
            competency_id=COMPETENCY_ID,
            slug=proposed.lesson.slug,
            approver=APPROVER,
        )
        accepted = await _accept(ctx, proposed.lesson.slug)
        assert accepted.outcome == LEARNING_OUTCOME_ACCEPTED
        rows = await _competency_rows(ctx)
        assert len(rows) == 1
        assert _OTHER_RULE in json.dumps(json.loads(rows[0]["value"]))

    def test_the_fingerprint_covers_what_a_reviewer_was_approving(self):
        """Field-by-field, so a later widening of `CandidateLesson` cannot
        silently fall outside the binding."""
        base = candidate_learning.CandidateLesson(
            competency_id=COMPETENCY_ID,
            slug="lesson_from_objective_1",
            source=candidate_learning.SourceExperience(
                objective_id=1,
                supporting_event_ids=[1, 2],
                observations=["fact: a", "action: b"],
            ),
            inferred_rule=_RULE,
            conditions="when the boiler is under warranty",
        )
        original = learning_authorization.fingerprint_for(base)

        for field_name, value in (
            ("inferred_rule", _OTHER_RULE),
            ("conditions", "always"),
            ("classification", "potentially_generalisable"),
            ("confidence", 0.9),
            ("competency_id", "other_competency"),
            ("slug", "other_slug"),
        ):
            mutated = candidate_learning.CandidateLesson.from_dict(base.to_dict())
            setattr(mutated, field_name, value)
            assert learning_authorization.fingerprint_for(mutated) != original, field_name

        # The evidence it stands on is part of the binding too.
        mutated = candidate_learning.CandidateLesson.from_dict(base.to_dict())
        mutated.source.supporting_event_ids = [1, 2, 3]
        assert learning_authorization.fingerprint_for(mutated) != original

        # Review bookkeeping is not: acceptance mutates it, so including it
        # would make every approval invalid at the moment it is used.
        mutated = candidate_learning.CandidateLesson.from_dict(base.to_dict())
        mutated.accept(reviewer=REVIEWER)
        assert learning_authorization.fingerprint_for(mutated) == original

    async def test_a_rejected_candidate_stays_rejected_even_when_approved(self, ctx):
        """An approval is not a way to reopen a terminal review decision."""
        objective_id = _run_the_experience(ctx)
        proposed = await _propose(ctx, objective_id)
        await grant_learning_acceptance_approval(
            ctx,
            competency_id=COMPETENCY_ID,
            slug=proposed.lesson.slug,
            approver=APPROVER,
        )
        await run_candidate_lesson_through_runtime_contract(
            ctx,
            LEARNING_ACTION_REJECT,
            competency_id=COMPETENCY_ID,
            slug=proposed.lesson.slug,
            reviewer=REVIEWER,
        )

        late = await _accept(ctx, proposed.lesson.slug)
        assert late.outcome == LEARNING_OUTCOME_INVALID
        assert "terminal" in (late.reason or "")
        assert await _competency_rows(ctx) == []

    async def test_approval_cannot_be_granted_after_review_is_terminal(self, ctx):
        objective_id = _run_the_experience(ctx)
        proposed = await _propose(ctx, objective_id)
        await run_candidate_lesson_through_runtime_contract(
            ctx,
            LEARNING_ACTION_REJECT,
            competency_id=COMPETENCY_ID,
            slug=proposed.lesson.slug,
            reviewer=REVIEWER,
        )

        result = await grant_learning_acceptance_approval(
            ctx,
            competency_id=COMPETENCY_ID,
            slug=proposed.lesson.slug,
            approver=APPROVER,
        )
        assert result.granted is False
        assert result.outcome == LEARNING_OUTCOME_INVALID


# ===========================================================================
# F. Parking Brake / Governance remain authoritative
# ===========================================================================
class TestBrakeAndGovernanceStayAuthoritative:
    async def test_an_approval_does_not_bypass_the_parking_brake(self, ctx):
        """The one that matters most: explicit approval authorises
        consolidating a lesson, never acting while a broader deny is in
        force."""
        objective_id = _run_the_experience(ctx)
        proposed = await _propose(ctx, objective_id)
        await grant_learning_acceptance_approval(
            ctx,
            competency_id=COMPETENCY_ID,
            slug=proposed.lesson.slug,
            approver=APPROVER,
        )

        store = GovernanceStore(ctx.mem.db_path)
        store.engage("training", reason="test", actor="test")
        ctx.governance_store = store
        try:
            blocked = await _accept(ctx, proposed.lesson.slug)
        finally:
            store.disengage(reason="test", actor="test")
            ctx.governance_store = None

        assert blocked.governance_allowed is False
        assert blocked.outcome == LEARNING_OUTCOME_BRAKE_DENIED
        assert blocked.consolidated is False
        assert await _competency_rows(ctx) == []

        # And once the brake is released, the same approval still works --
        # the brake refused the action, it did not revoke the authorization.
        assert (await _accept(ctx, proposed.lesson.slug)).outcome == LEARNING_OUTCOME_ACCEPTED

    async def test_the_brake_also_blocks_granting_an_approval_from_taking_effect(self, ctx):
        """A brake engaged for the whole training scope leaves the loop
        inert regardless of what has been approved."""
        objective_id = _run_the_experience(ctx)
        proposed = await _propose(ctx, objective_id)

        store = GovernanceStore(ctx.mem.db_path)
        store.engage("global", reason="test", actor="test")
        ctx.governance_store = store
        try:
            await grant_learning_acceptance_approval(
                ctx,
                competency_id=COMPETENCY_ID,
                slug=proposed.lesson.slug,
                approver=APPROVER,
            )
            blocked = await _accept(ctx, proposed.lesson.slug)
        finally:
            store.disengage(reason="test", actor="test")
            ctx.governance_store = None

        assert blocked.governance_allowed is False
        assert blocked.outcome == LEARNING_OUTCOME_BRAKE_DENIED
        assert await _competency_rows(ctx) == []

    async def test_identity_denial_still_refuses_proposal_and_rejection(self, ctx):
        """The allowlist remains authoritative for the two kinds it governs:
        this change grants learning_propose/learning_reject, it does not
        exempt them from Identity policy."""
        objective_id = _run_the_experience(ctx)
        proposed = await _propose(ctx, objective_id)

        ctx.identity_context = IdentityContext(
            tool_use_default_allowed=False,
            tool_use_allowlist=[],
        )
        denied_propose = await _propose(ctx, objective_id)
        denied_reject = await run_candidate_lesson_through_runtime_contract(
            ctx,
            LEARNING_ACTION_REJECT,
            competency_id=COMPETENCY_ID,
            slug=proposed.lesson.slug,
            reviewer=REVIEWER,
        )

        for result in (denied_propose, denied_reject):
            assert result.governance_allowed is False
            assert result.outcome == LEARNING_OUTCOME_GOVERNANCE_DENIED
        assert await _competency_rows(ctx) == []

    async def test_the_approval_record_is_not_reasoning_material(self, ctx):
        """A governance record must never be retrievable as knowledge."""
        assert learning_authorization.KIND not in COMPETENCY_KINDS

        objective_id = _run_the_experience(ctx)
        proposed = await _propose(ctx, objective_id)
        await grant_learning_acceptance_approval(
            ctx,
            competency_id=COMPETENCY_ID,
            slug=proposed.lesson.slug,
            approver=APPROVER,
        )
        assert await _competency_rows(ctx) == []
        assert APPROVER not in await _asks_about_the_boiler(ctx)
