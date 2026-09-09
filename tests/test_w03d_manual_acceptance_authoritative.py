"""
W03-D acceptance 8 and `W03_TEST_CONTRACTS.md` Sec.7 -- manual acceptance
stays authoritative; and Sec.6's **mechanism** half -- a correction supersedes
prior behaviour and changes a later relevant task.

Acceptance criterion 8, verbatim:

    A fully permissive policy with `requested_execution_mode: auto` and
    `learning_accept` allowlisted still consolidates nothing; consolidation
    is reachable only from the approved accept branch.

`tests/test_shadow_learning_policy.py` already pins the five structural
properties of the policy module in isolation, and
`tests/test_learning_acceptance_authorization.py` pins the acceptance gate.
What was not pinned anywhere is the *combination* the criterion names: the
most permissive configuration a user can actually write, together with the
most permissive identity an operator can actually set, driven end to end
against real stores. A reader should not have to compose two suites in their
head to believe it.

Sec.6's scenario half (Golden Path 4, live on a desktop) belongs to W03-E and
W03-F. The mechanism half is W03-D's, and is the last class here: an approved
correction supersedes, and the *later* turn uses the new value -- while an
unapproved one changes nothing.

Marked `integration` per `W03_TEST_CONTRACTS.md` Sec.8, except the structural
"policy cannot write" checks, which stay in PR Fast.
"""

from __future__ import annotations

import ast
import inspect
import json
import textwrap
from pathlib import Path

import pytest
import yaml

from bartholomew.kernel import candidate_learning, learning_policy
from bartholomew.kernel import objective_store as os_mod
from bartholomew.kernel.competency import COMPETENCY_KINDS
from bartholomew.kernel.memory.privacy_guard import set_consent_handler
from bartholomew.kernel.memory_store import MEMORY_REVISION_KIND, MemoryStore
from bartholomew.kernel.objective_store import ObjectiveStore
from bartholomew.kernel.runtime_contract import (
    LEARNING_ACTION_ACCEPT,
    LEARNING_ACTION_APPROVE_ACCEPTANCE,
    LEARNING_ACTION_PROPOSE,
    LEARNING_OUTCOME_ACCEPTED,
    LEARNING_OUTCOME_APPROVAL_REQUIRED,
    LEARNING_OUTCOME_PROPOSED,
    grant_learning_acceptance_approval,
    run_candidate_lesson_through_runtime_contract,
    run_chat_through_runtime_contract,
    run_learning_policy_update_through_runtime_contract,
)
from identity_interpreter.identity_context import IdentityContext

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPETENCY_ID = "estate_management"
REVIEWER = "taylor"

_RULE = (
    "Before booking a paid boiler engineer, check the manufacturer warranty "
    "and call the warranty line first"
)


@pytest.fixture(autouse=True)
def _reset_consent_handler():
    set_consent_handler(None)
    yield
    set_consent_handler(None)


def _shipped_identity_context() -> IdentityContext:
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
    db_path = str(tmp_path / "w03d_acceptance.db")
    mem = MemoryStore(db_path)
    await mem.init()
    os_mod.ensure_schema(db_path)
    yield _Ctx(mem, ObjectiveStore(db_path), _shipped_identity_context())
    await mem.close(checkpoint=False)


def _run_the_experience(ctx) -> int:
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
        event_kind=os_mod.EVENT_ACTION,
        summary="Called the boiler warranty line and booked a free service visit",
    )
    store.complete(
        objective.id,
        resolution=os_mod.RESOLUTION_ACHIEVED,
        outcome_note="Serviced free under warranty",
    )
    return objective.id


async def _propose(ctx, objective_id: int, rule: str = _RULE):
    return await run_candidate_lesson_through_runtime_contract(
        ctx,
        LEARNING_ACTION_PROPOSE,
        objective_id=objective_id,
        competency_id=COMPETENCY_ID,
        inferred_rule=rule,
    )


async def _accept(ctx, slug: str):
    return await run_candidate_lesson_through_runtime_contract(
        ctx,
        LEARNING_ACTION_ACCEPT,
        competency_id=COMPETENCY_ID,
        slug=slug,
        reviewer=REVIEWER,
    )


async def _competency_rows(ctx) -> list[dict]:
    return await ctx.mem.list_memories_by_kind(list(COMPETENCY_KINDS), limit=50)


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


def _maximally_permissive_policy(stored_revision: int) -> learning_policy.LearningPolicy:
    """
    The most permissive configuration the policy model can express.

    Every dimension set to the value that would wave a candidate through:
    every category enabled and none excluded, unbounded risk, no
    reversibility requirement, a single supporting experience (which is
    exactly what this wave's candidates carry), a confidence floor of zero,
    contradictions ignored where the validator permits it, no capability or
    application ceiling, no excluded privacy class or classification,
    sharing-eligible lessons allowed -- and `requested_execution_mode: auto`,
    the user saying in as many words that they want automatic acceptance.
    """
    return learning_policy.LearningPolicy(
        revision=stored_revision,
        enabled_categories=list(candidate_learning.LESSON_KINDS),
        excluded_categories=[],
        max_risk="critical",
        require_reversible=False,
        min_supporting_experiences=1,
        min_confidence=0.0,
        max_affected_capabilities=999,
        max_affected_applications=999,
        excluded_privacy_classes=[],
        excluded_classifications=[],
        exclude_sharing_eligible=False,
        expires_after_days=None,
        review_interval_days=None,
        requested_execution_mode=learning_policy.REQUESTED_MODE_AUTO,
    )


# ---------------------------------------------------------------------------
# 1. The criterion, end to end.
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestPermissivePolicyStillConsolidatesNothing:
    async def test_the_most_permissive_policy_this_system_can_hold_is_recorded(self, ctx):
        """Precondition. The permissive policy is genuinely stored -- so the
        tests below are about what a *real* permissive configuration does,
        not about one the validator quietly rejected."""
        result = await run_learning_policy_update_through_runtime_contract(
            ctx,
            _maximally_permissive_policy(0),
            expected_revision=0,
            updated_by=REVIEWER,
        )
        assert result.policy is not None, result.reason
        assert result.policy.requested_execution_mode == learning_policy.REQUESTED_MODE_AUTO

    async def test_it_still_reports_shadow_as_its_execution_mode(self, ctx):
        await run_learning_policy_update_through_runtime_contract(
            ctx,
            _maximally_permissive_policy(0),
            expected_revision=0,
            updated_by=REVIEWER,
        )
        row = await ctx.mem.get_memory(learning_policy.POLICY_KIND, learning_policy.POLICY_KEY)
        assert row is not None
        stored = learning_policy.LearningPolicy.from_dict(json.loads(row["value"]))
        assert stored.requested_execution_mode == learning_policy.REQUESTED_MODE_AUTO
        assert stored.execution_mode == learning_policy.SHIPPED_EXECUTION_MODE == "shadow"

    async def test_a_permissive_policy_plus_a_permissive_identity_consolidates_nothing(
        self,
        ctx,
    ):
        """
        The acceptance criterion itself.

        Both permissive levers pulled at once -- the policy a user can write
        and the allowlist an operator can set -- against a real candidate
        from a real completed objective. Nothing is consolidated, nothing
        becomes retrievable, and the later relevant turn is unchanged.
        """
        await run_learning_policy_update_through_runtime_contract(
            ctx,
            _maximally_permissive_policy(0),
            expected_revision=0,
            updated_by=REVIEWER,
        )

        objective_id = _run_the_experience(ctx)
        proposed = await _propose(ctx, objective_id)
        assert proposed.outcome == LEARNING_OUTCOME_PROPOSED, proposed.reason

        ctx.identity_context = IdentityContext(
            tool_use_default_allowed=True,
            tool_use_allowlist=[LEARNING_ACTION_ACCEPT, LEARNING_ACTION_APPROVE_ACCEPTANCE],
        )
        refused = await _accept(ctx, proposed.lesson.slug)

        assert refused.governance_allowed is False
        assert refused.outcome == LEARNING_OUTCOME_APPROVAL_REQUIRED
        assert refused.consolidated is False
        assert await _competency_rows(ctx) == []
        assert "warranty line first" not in await _asks_about_the_boiler(ctx)

    async def test_consolidation_is_reachable_only_from_the_approved_accept_branch(self, ctx):
        """
        Non-vacuity for the test above, and the second half of the criterion.

        The *same* candidate, the same permissive policy, the same permissive
        allowlist -- plus one explicit, candidate-bound human approval. Now it
        consolidates, and the later turn changes. So the refusal above is
        about the missing approval and nothing else.
        """
        await run_learning_policy_update_through_runtime_contract(
            ctx,
            _maximally_permissive_policy(0),
            expected_revision=0,
            updated_by=REVIEWER,
        )
        objective_id = _run_the_experience(ctx)
        proposed = await _propose(ctx, objective_id)

        approval = await grant_learning_acceptance_approval(
            ctx,
            competency_id=COMPETENCY_ID,
            slug=proposed.lesson.slug,
            approver=REVIEWER,
        )
        assert approval.granted, approval.reason

        accepted = await _accept(ctx, proposed.lesson.slug)
        assert accepted.outcome == LEARNING_OUTCOME_ACCEPTED, accepted.reason
        assert accepted.consolidated is True
        assert await _competency_rows(ctx) != []
        assert "warranty line first" in await _asks_about_the_boiler(ctx)

    async def test_the_shipped_execution_mode_is_shadow_whatever_is_configured(self, ctx):
        """The invariant stated as itself: no stored configuration changes it."""
        assert learning_policy.SHIPPED_EXECUTION_MODE == "shadow"
        permissive = _maximally_permissive_policy(0)
        assert permissive.execution_mode == "shadow"


# ---------------------------------------------------------------------------
# 2. Structural: the policy path cannot write knowledge (PR Fast).
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestPolicyCannotWriteKnowledge:
    def test_the_execution_mode_property_has_no_setter(self):
        prop = learning_policy.LearningPolicy.execution_mode
        assert isinstance(prop, property)
        assert prop.fset is None, "execution_mode must not be settable"

    def test_the_forbidden_write_kinds_still_cover_every_route_to_knowledge(self):
        forbidden = set(learning_policy.FORBIDDEN_SHADOW_WRITE_KINDS)
        assert candidate_learning.KIND in forbidden
        assert learning_policy.APPROVAL_KIND in forbidden
        assert set(COMPETENCY_KINDS) <= forbidden

    def test_w03d_added_no_new_route_from_a_shadow_decision_to_acceptance(self):
        """
        W03-D touched the memory and learning packages. It must not have
        given a `ShadowDecision` a way to authorize anything -- the property
        `test_shadow_learning_policy.py` pins for the module in isolation,
        re-asserted here against the wave's own changes.
        """
        decision_source = inspect.getsource(learning_policy.ShadowDecision)
        assert "authorizes_acceptance" in decision_source

        # Not "the type is never mentioned" -- the docstring names it
        # deliberately, to say the object cannot produce one. What must stay
        # true is that nothing here *constructs* it, and that the property
        # answering "may this be accepted?" is a hard-coded False.
        tree = ast.parse(textwrap.dedent(decision_source))
        constructed = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "LearningAcceptanceApproval" not in constructed

        assert learning_policy.ShadowDecision.authorizes_acceptance.fset is None

    def test_the_revision_archive_kinds_are_not_retrievable_knowledge(self):
        """
        W03-D added `MEMORY_REVISION_KIND` alongside the existing
        `COMPETENCY_REVISION_KIND` and `CANDIDATE_REVISION_KIND`. All three
        are archives of things that are no longer true, so none may be a
        competency kind -- a superseded belief must not be retrievable as a
        current one.
        """
        for kind in (
            MEMORY_REVISION_KIND,
            learning_policy.COMPETENCY_REVISION_KIND,
            learning_policy.CANDIDATE_REVISION_KIND,
            learning_policy.EVALUATION_KIND,
            learning_policy.POLICY_KIND,
        ):
            assert kind not in COMPETENCY_KINDS, kind


# ---------------------------------------------------------------------------
# 3. Sec.6's mechanism half: a correction changes a later task.
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCorrectionChangesALaterTask:
    async def test_an_approved_correction_supersedes_and_the_later_turn_uses_it(self, ctx):
        """
        The learn leg, closed, without automatic acceptance:

        1. a preference is stored;
        2. the user corrects it;
        3. the correction supersedes the prior value, archiving it;
        4. a *later*, separate turn recalls the new value and not the old.
        """
        await ctx.mem.upsert_memory(
            "user_profile",
            "preference.engineer_choice",
            "Preference: always book the cheapest engineer",
            "2026-09-01T09:00:00+00:00",
        )

        before = await _asks_about_the_boiler(ctx)
        assert "cheapest engineer" in before

        result = await ctx.mem.correct_memory(
            "user_profile",
            "preference.engineer_choice",
            "Preference: always check the warranty before booking an engineer",
        )
        assert result.stored, result

        after = await _asks_about_the_boiler(ctx)
        assert "check the warranty before booking an engineer" in after
        assert (
            "cheapest engineer" not in after
        ), "the superseded preference is still being recalled as current"

        # And the obsolete claim survives as history, under a kind nothing
        # can retrieve it from.
        archived = await ctx.mem.list_memories_by_kind([MEMORY_REVISION_KIND], limit=50)
        assert any("cheapest engineer" in row["value"] for row in archived)

    async def test_an_unapproved_correction_changes_nothing(self, ctx):
        """
        The control the contract asks for by name: a candidate lesson that
        was proposed but never approved leaves the later turn exactly as it
        was. Proposing is not learning.
        """
        objective_id = _run_the_experience(ctx)
        proposed = await _propose(ctx, objective_id)
        assert proposed.outcome == LEARNING_OUTCOME_PROPOSED

        assert proposed.consolidated is False
        assert await _competency_rows(ctx) == []
        assert "warranty line first" not in await _asks_about_the_boiler(ctx)
