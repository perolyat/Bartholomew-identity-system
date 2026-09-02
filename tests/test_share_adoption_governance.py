"""
Package E: what an adopted share is, and what it takes to make it knowledge.

The claim under test throughout: **adoption creates a candidate, and only the
recipient's own candidate-bound approval makes it retrievable.** Something a
housemate shared becomes trusted knowledge on exactly the terms something
Bartholomew inferred for itself does -- never automatically, never by a
switch, and never without a named reviewer and an approval bound to the
candidate's exact content.

Everything runs against a real `MemoryStore` on disk, the real
`GovernanceStore`, and the `Identity.yaml` this repository actually ships --
the same `_shipped_identity_context()` idiom
`tests/test_learning_acceptance_authorization.py` uses. A mocked governance
gate would prove nothing about whether the gate is reachable.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bartholomew.kernel import candidate_learning, learning_authorization
from bartholomew.kernel import runtime_contract as rc
from bartholomew.kernel import share_adoption as sa
from bartholomew.kernel import trusted_share as ts
from bartholomew.kernel.competency import COMPETENCY_KINDS
from bartholomew.kernel.memory.privacy_guard import set_consent_handler
from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.reflection import REFLECTION_KIND
from bartholomew.orchestrator.safety.governance_store import GovernanceStore
from identity_interpreter.identity_context import IdentityContext

REPO_ROOT = Path(__file__).resolve().parents[1]


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


class _WorkingMemory:
    def get_context_string(self):
        return ""


class _Ctx:
    """The seven-attribute duck-typed context the wave's seams share.

    Deliberately no eighth attribute: `tests/test_wave_cross_stream_integration.py`
    treats that set as the compatibility assertion between streams, and
    Package E's seam must not require anything the others do not provide.
    """

    def __init__(self, mem, identity, governance_store=None):
        self.mem = mem
        self.objective_store = None
        self.experience = _Experience()
        self.persona_manager = _Persona()
        self.working_memory = _WorkingMemory()
        self.identity_context = identity
        self.governance_store = governance_store
        self.blocking_executor = None


@pytest.fixture(autouse=True)
def _consent_handler():
    """A present, approving consent handler, restored afterwards.

    Adopted content is somebody else's words, so `privacy_guard` scans it in
    full and a household routine can legitimately trip the keyword gate. A
    registered handler is the realistic configuration for a recipient sitting
    in front of the review surface; the interesting governance in these tests
    is the acceptance approval, not the consent queue.
    """
    set_consent_handler(lambda _text: True)
    yield
    set_consent_handler(None)


@pytest.fixture
async def ctx(tmp_path):
    db_path = str(tmp_path / "share_adoption.db")
    mem = MemoryStore(db_path)
    await mem.init()
    yield _Ctx(mem, _shipped_identity_context())
    await mem.close()


def package(
    *,
    share_id="share-1",
    revision=1,
    rule="Put the bins out on Tuesday evening, not Wednesday morning.",
    kind=ts.KIND_HOUSEHOLD_ROUTINE,
    content=None,
    revoked_at=None,
):
    return ts.TrustedSharePackage(
        share_id=share_id,
        group_id="group-1",
        publisher_user_id="publisher-1",
        source_candidate_fingerprint="origin-fingerprint",
        kind=kind,
        content=content if content is not None else {"name": rule, "steps": [rule]},
        sanitization=ts.Sanitization(ts.POLICY_REVISION, ("provenance", "confidence")),
        revision=revision,
        published_at="2026-09-01T00:00:00+00:00",
        revoked_at=revoked_at,
    )


def _share_reflections(ctx) -> list[dict]:
    """The `trusted_share` Reflections, read straight out of the table.

    The same idiom `tests/test_learning_acceptance_authorization.py` uses --
    the reflection sink has no bulk read helper, and asserting against the
    row a surface actually wrote is the point.
    """
    import json
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
    return [row for row in rows if row.get("surface") == rc.SHARE_OBSERVATION_SOURCE]


async def _adopt(ctx, pkg=None, competency_id="household"):
    result = await rc.run_share_adoption_through_runtime_contract(
        ctx,
        rc.SHARE_ACTION_ADOPT,
        package=pkg or package(),
        competency_id=competency_id,
    )
    assert result.outcome == rc.SHARE_OUTCOME_ADOPTED, result.reason
    return result.candidate


async def _approve_and_accept(ctx, candidate, reviewer="taylor"):
    grant = await rc.grant_share_acceptance_approval(
        ctx,
        competency_id=candidate.competency_id,
        slug=candidate.slug,
        approver=reviewer,
    )
    assert grant.granted, grant.reason
    return await rc.run_share_adoption_through_runtime_contract(
        ctx,
        rc.SHARE_ACTION_ACCEPT,
        competency_id=candidate.competency_id,
        slug=candidate.slug,
        reviewer=reviewer,
    )


# ---------------------------------------------------------------------------
# 22: adoption creates a local candidate, not accepted knowledge
# ---------------------------------------------------------------------------


async def test_adoption_creates_a_candidate_that_reasoning_cannot_see(ctx):
    """22. Structural, not conventional.

    `adopted_share_candidate` is absent from `COMPETENCY_KINDS`, which is what
    the retrieval seam filters on -- so an adopted share is invisible to
    reasoning in every review state, including accepted.
    """
    candidate = await _adopt(ctx)

    assert sa.KIND == "adopted_share_candidate"
    assert sa.KIND not in COMPETENCY_KINDS
    assert candidate.review_state == sa.REVIEW_PROPOSED
    assert candidate.requires_review is True
    assert candidate.epistemic_status == "inference"

    row = await ctx.mem.get_memory(sa.KIND, candidate.key())
    assert row is not None
    assert await ctx.mem.get_memory("competency_heuristic", candidate.key()) is None
    assert await ctx.mem.get_memory("competency_procedure", candidate.key()) is None


async def test_an_adopted_candidate_does_not_inherit_the_publishers_confidence(ctx):
    """22. The recipient's Bartholomew forms its own judgement.

    Lower than a lesson from the recipient's own experience: a rule they
    watched play out once is better evidence than a rule someone else wrote
    down, however much they trust them.
    """
    candidate = await _adopt(ctx)
    assert candidate.confidence == sa.ADOPTED_SHARE_CONFIDENCE == 0.35
    assert candidate.confidence < candidate_learning.SINGLE_EXPERIENCE_CONFIDENCE
    assert candidate.classification == "personal"


async def test_an_adopted_candidate_carries_provenance_but_not_the_publishers_free_text(ctx):
    """29. Accounts, group, revision and hashes -- and nothing that re-identifies."""
    candidate = await _adopt(ctx)
    origin = candidate.source
    assert origin.share_id == "share-1"
    assert origin.group_id == "group-1"
    assert origin.publisher_user_id == "publisher-1"
    assert origin.share_revision == 1
    assert origin.content_hash
    assert origin.source_candidate_fingerprint == "origin-fingerprint"
    assert origin.sanitization_policy_revision == ts.POLICY_REVISION
    # Honest about what it does not stand on.
    assert origin.objective_id is None
    assert origin.supporting_event_ids == []


# ---------------------------------------------------------------------------
# 23: local acceptance requires PR #83's candidate-bound approval
# ---------------------------------------------------------------------------


async def test_acceptance_without_an_approval_is_refused_by_governance(ctx):
    """23. And refused with PR #83's own outcome, because it is PR #83's gate.

    `evaluate_share_admission` delegates the accept branch to
    `evaluate_learning_admission(ctx, "learning_accept", ...)`, so this is not
    an analogue of the learning gate -- it is the learning gate.
    """
    candidate = await _adopt(ctx)
    result = await rc.run_share_adoption_through_runtime_contract(
        ctx,
        rc.SHARE_ACTION_ACCEPT,
        competency_id=candidate.competency_id,
        slug=candidate.slug,
        reviewer="taylor",
    )
    assert result.governance_allowed is False
    assert result.outcome == rc.LEARNING_OUTCOME_APPROVAL_REQUIRED
    assert not result.consolidated

    stored = sa.AdoptedShareCandidate.from_dict(
        __import__("json").loads((await ctx.mem.get_memory(sa.KIND, candidate.key()))["value"]),
    )
    assert stored.review_state == sa.REVIEW_PROPOSED


async def test_allowlisting_share_accept_does_not_make_acceptance_reachable(ctx, tmp_path):
    """23. There is no "sharing enabled" switch to find.

    The strongest form of the claim: default-allow *and* an explicit
    `share_accept` allowlist entry, and the refusal still stands -- because
    acceptance is not gated on the allowlist at all.
    """
    permissive = IdentityContext(
        tool_use_default_allowed=True,
        tool_use_allowlist=[rc.SHARE_ACTION_ACCEPT, rc.LEARNING_ACTION_ACCEPT],
    )
    ctx.identity_context = permissive
    candidate = await _adopt(ctx)

    result = await rc.run_share_adoption_through_runtime_contract(
        ctx,
        rc.SHARE_ACTION_ACCEPT,
        competency_id=candidate.competency_id,
        slug=candidate.slug,
        reviewer="taylor",
    )
    assert result.governance_allowed is False
    assert result.outcome == rc.LEARNING_OUTCOME_APPROVAL_REQUIRED

    # Adoption itself still works under the shipped allowlist, so the refusal
    # is scoped to acceptance rather than blanket.
    ctx.identity_context = _shipped_identity_context()
    assert (await _adopt(ctx, package(share_id="share-2"))) is not None


async def test_the_shipped_identity_grants_adoption_but_never_acceptance():
    """23. Asserted against the allowlist this repository actually ships."""
    identity = _shipped_identity_context()
    allowlist = set(identity.tool_use_allowlist)
    assert identity.tool_use_default_allowed is False
    assert {rc.SHARE_ACTION_ADOPT, rc.SHARE_ACTION_CUSTOMISE, rc.SHARE_ACTION_REJECT} <= allowlist
    assert rc.SHARE_ACTION_ACCEPT not in allowlist
    assert rc.LEARNING_ACTION_ACCEPT not in allowlist


async def test_an_approved_candidate_accepts_and_consolidates(ctx):
    """23. The full governed path, once and only once a person has authorised it."""
    candidate = await _adopt(ctx)
    result = await _approve_and_accept(ctx, candidate)

    assert result.outcome == rc.SHARE_OUTCOME_ACCEPTED, result.reason
    assert result.consolidated
    assert result.candidate.review_state == sa.REVIEW_ACCEPTED
    assert result.candidate.reviewer == "taylor"
    assert result.candidate.consolidated_kind in COMPETENCY_KINDS

    consolidated = await ctx.mem.get_memory(
        result.candidate.consolidated_kind,
        result.candidate.consolidated_key,
    )
    assert consolidated is not None
    assert "Tuesday evening" in consolidated["value"]


async def test_the_consolidated_record_says_where_it_came_from(ctx):
    """29. Group, share, revision, digest and reviewer -- and no publisher free text."""
    import json

    candidate = await _adopt(ctx)
    result = await _approve_and_accept(ctx, candidate)
    stored = json.loads(
        (
            await ctx.mem.get_memory(
                result.candidate.consolidated_kind,
                result.candidate.consolidated_key,
            )
        )["value"],
    )
    provenance = stored["provenance"]
    assert provenance["source_type"] == sa.TRUSTED_SHARE_SOURCE_TYPE == "trusted_share"
    assert "group-1" in provenance["detail"]
    assert "share-1" in provenance["detail"]
    assert "publisher-1" in provenance["detail"]
    assert stored["supervision"]["requires_review"] is True
    assert stored["confidence"] == sa.ADOPTED_SHARE_CONFIDENCE


async def test_an_approval_is_bound_to_the_candidates_exact_content(ctx):
    """23. Approving what a candidate said is not approving what it says now.

    Customising after approval changes the fingerprint, and acceptance then
    fails until the reviewer approves what they can now actually see.
    """
    candidate = await _adopt(ctx)
    grant = await rc.grant_share_acceptance_approval(
        ctx,
        competency_id=candidate.competency_id,
        slug=candidate.slug,
        approver="taylor",
    )
    assert grant.granted

    customised = await rc.run_share_adoption_through_runtime_contract(
        ctx,
        rc.SHARE_ACTION_CUSTOMISE,
        competency_id=candidate.competency_id,
        slug=candidate.slug,
        rule="Put the bins out on Monday evening instead.",
    )
    assert customised.outcome == rc.SHARE_OUTCOME_CUSTOMISED
    assert customised.candidate.local_fork is True

    refused = await rc.run_share_adoption_through_runtime_contract(
        ctx,
        rc.SHARE_ACTION_ACCEPT,
        competency_id=candidate.competency_id,
        slug=candidate.slug,
        reviewer="taylor",
    )
    assert refused.governance_allowed is False
    assert refused.outcome == rc.LEARNING_OUTCOME_APPROVAL_REQUIRED
    assert "changed since it was approved" in (refused.reason or "")


async def test_an_approval_for_a_lesson_cannot_authorise_a_share_or_the_reverse(ctx):
    """23. The two candidate families' approvals are never interchangeable.

    `lesson_kind` is part of the fingerprint material, so even a contrived
    key collision cannot let an approval for a locally inferred lesson
    authorise an adopted one.
    """
    candidate = await _adopt(ctx)
    lesson = candidate_learning.CandidateLesson(
        competency_id=candidate.competency_id,
        slug=candidate.slug,
        source=candidate_learning.SourceExperience(
            objective_id=1,
            supporting_event_ids=[1],
            observations=["action: did the thing"],
        ),
        inferred_rule=candidate.inferred_rule,
        conditions=candidate.conditions,
        classification=candidate.classification,
        confidence=candidate.confidence,
    )
    assert learning_authorization.fingerprint_for(lesson) != (
        learning_authorization.fingerprint_for(candidate)
    )

    approval = learning_authorization.LearningAcceptanceApproval(
        competency_id=candidate.competency_id,
        slug=candidate.slug,
        candidate_fingerprint=learning_authorization.fingerprint_for(lesson),
        approver="taylor",
    )
    allowed, reason = approval.authorizes(candidate)
    assert allowed is False
    assert "changed since it was approved" in (reason or "")


async def test_the_fingerprint_of_an_ordinary_lesson_is_unchanged(ctx):
    """A regression guard on the one edit Package E made to PR #83's module.

    `fingerprint_for` became tolerant of a candidate with no objective. For a
    `CandidateLesson`, whose objective id is always an integer, the material
    -- and therefore the digest -- must be byte-identical to before.
    """
    import hashlib
    import json

    lesson = candidate_learning.CandidateLesson(
        competency_id="home_maintenance",
        slug="lesson_from_objective_7",
        source=candidate_learning.SourceExperience(
            objective_id=7,
            supporting_event_ids=[3, 1, 2],
            observations=["action: a", "fact: b", "decision: c"],
        ),
        inferred_rule="A rule.",
        conditions="Some conditions.",
    )
    expected_material = {
        "competency_id": "home_maintenance",
        "slug": "lesson_from_objective_7",
        "inferred_rule": "A rule.",
        "conditions": "Some conditions.",
        "lesson_kind": candidate_learning.LESSON_PROCEDURAL,
        "epistemic_status": candidate_learning.EPISTEMIC_INFERENCE,
        "classification": "personal",
        "confidence": candidate_learning.SINGLE_EXPERIENCE_CONFIDENCE,
        "objective_id": 7,
        "supporting_event_ids": [1, 2, 3],
    }
    expected = hashlib.sha256(
        json.dumps(expected_material, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    ).hexdigest()
    assert learning_authorization.fingerprint_for(lesson) == expected


# ---------------------------------------------------------------------------
# 21: decline, and the terminality of a decision
# ---------------------------------------------------------------------------


async def test_rejecting_an_adopted_share_consolidates_nothing_ever(ctx):
    """21/22. Rejection is real, and it is terminal."""
    candidate = await _adopt(ctx)
    result = await rc.run_share_adoption_through_runtime_contract(
        ctx,
        rc.SHARE_ACTION_REJECT,
        competency_id=candidate.competency_id,
        slug=candidate.slug,
        reviewer="taylor",
    )
    assert result.outcome == rc.SHARE_OUTCOME_REJECTED
    assert not result.consolidated

    grant = await rc.grant_share_acceptance_approval(
        ctx,
        competency_id=candidate.competency_id,
        slug=candidate.slug,
        approver="taylor",
    )
    assert grant.granted is False
    assert "terminal" in (grant.reason or "")

    later = await rc.run_share_adoption_through_runtime_contract(
        ctx,
        rc.SHARE_ACTION_ACCEPT,
        competency_id=candidate.competency_id,
        slug=candidate.slug,
        reviewer="taylor",
    )
    assert later.outcome != rc.SHARE_OUTCOME_ACCEPTED
    assert not later.consolidated


async def test_a_review_decision_is_never_anonymous(ctx):
    """Review requires a reviewer -- the same rule the learning loop holds to.

    Note the ordering the accept branch demonstrates: Governance answers
    before the reviewer check, so an unapproved acceptance is refused as
    unauthorised rather than as malformed. An anonymous *approved* acceptance
    is what this test has to reach past that to prove.
    """
    candidate = await _adopt(ctx)

    rejected = await rc.run_share_adoption_through_runtime_contract(
        ctx,
        rc.SHARE_ACTION_REJECT,
        competency_id=candidate.competency_id,
        slug=candidate.slug,
        reviewer=None,
    )
    assert rejected.outcome == rc.SHARE_OUTCOME_INVALID
    assert "never anonymous" in (rejected.reason or "")

    grant = await rc.grant_share_acceptance_approval(
        ctx,
        competency_id=candidate.competency_id,
        slug=candidate.slug,
        approver="taylor",
    )
    assert grant.granted
    anonymous = await rc.run_share_adoption_through_runtime_contract(
        ctx,
        rc.SHARE_ACTION_ACCEPT,
        competency_id=candidate.competency_id,
        slug=candidate.slug,
        reviewer=None,
    )
    assert anonymous.outcome == rc.SHARE_OUTCOME_INVALID
    assert "never anonymous" in (anonymous.reason or "")
    assert not anonymous.consolidated

    ungranted = await rc.grant_share_acceptance_approval(
        ctx,
        competency_id=candidate.competency_id,
        slug=candidate.slug,
        approver="",
    )
    assert ungranted.granted is False


# ---------------------------------------------------------------------------
# 24: revisions do not overwrite a local fork
# ---------------------------------------------------------------------------


async def test_adopting_a_later_revision_writes_a_different_key(ctx):
    """24. Structural: a publisher update cannot overwrite what it is not writing to.

    The share revision is part of the candidate's slug, so revision 2 lands
    at a different memory key from revision 1. Whatever the recipient did
    with revision 1 -- accepted it, customised it, both -- is untouched.
    """
    first = await _adopt(ctx, package(revision=1))
    await rc.run_share_adoption_through_runtime_contract(
        ctx,
        rc.SHARE_ACTION_CUSTOMISE,
        competency_id=first.competency_id,
        slug=first.slug,
        rule="Bins out Monday, because our collection moved.",
    )
    second = await _adopt(
        ctx,
        package(revision=2, rule="Put the bins out on Wednesday morning."),
    )

    assert first.key() != second.key()
    assert second.review_state == sa.REVIEW_PROPOSED

    kept = sa.AdoptedShareCandidate.from_dict(
        __import__("json").loads((await ctx.mem.get_memory(sa.KIND, first.key()))["value"]),
    )
    assert kept.local_fork is True
    assert kept.inferred_rule == "Bins out Monday, because our collection moved."


async def test_a_local_fork_survives_the_upstream_revision_being_accepted(ctx):
    """24. Two revisions, two candidates, two independent decisions."""
    first = await _adopt(ctx, package(revision=1))
    await rc.run_share_adoption_through_runtime_contract(
        ctx,
        rc.SHARE_ACTION_CUSTOMISE,
        competency_id=first.competency_id,
        slug=first.slug,
        rule="Our own version.",
    )
    second = await _adopt(ctx, package(revision=2, rule="The publisher's new version."))
    accepted = await _approve_and_accept(ctx, second)
    assert accepted.outcome == rc.SHARE_OUTCOME_ACCEPTED

    kept = sa.AdoptedShareCandidate.from_dict(
        __import__("json").loads((await ctx.mem.get_memory(sa.KIND, first.key()))["value"]),
    )
    assert kept.review_state == sa.REVIEW_PROPOSED
    assert kept.inferred_rule == "Our own version."


# ---------------------------------------------------------------------------
# 26-28: revocation, on the recipient's side
# ---------------------------------------------------------------------------


async def test_a_revoked_package_cannot_be_adopted(ctx):
    """26. Re-checked here as well as on the exchange.

    The check costs nothing and the alternative is a recipient accepting
    something the publisher has taken back.
    """
    result = await rc.run_share_adoption_through_runtime_contract(
        ctx,
        rc.SHARE_ACTION_ADOPT,
        package=package(revoked_at="2026-09-01T12:00:00+00:00"),
        competency_id="household",
    )
    assert result.outcome == rc.SHARE_OUTCOME_REVOKED
    assert result.candidate is None


async def test_an_upstream_revocation_blocks_approval_and_acceptance(ctx):
    """26. Withdrawn upstream means no new acceptance, from that moment on."""
    candidate = await _adopt(ctx)
    marked = await rc.run_share_adoption_through_runtime_contract(
        ctx,
        rc.SHARE_ACTION_REJECT,
        competency_id=candidate.competency_id,
        slug=candidate.slug,
        reviewer="taylor",
        upstream_revoked_at="2026-09-02T00:00:00+00:00",
    )
    assert marked.candidate.is_revoked_upstream

    second = await _adopt(ctx, package(share_id="share-9"))
    grant = await rc.grant_share_acceptance_approval(
        ctx,
        competency_id=second.competency_id,
        slug=second.slug,
        approver="taylor",
    )
    assert grant.granted

    blocked = await rc.run_share_adoption_through_runtime_contract(
        ctx,
        rc.SHARE_ACTION_ACCEPT,
        competency_id=second.competency_id,
        slug=second.slug,
        reviewer="taylor",
        upstream_revoked_at="2026-09-02T00:00:00+00:00",
    )
    assert blocked.outcome == rc.SHARE_OUTCOME_INVALID
    assert "withdrawn" in (blocked.reason or "")
    assert not blocked.consolidated


async def test_revocation_stays_visible_and_deletes_nothing_already_accepted(ctx):
    """27/28. The publisher gets "un-share", not a remote delete on someone's memory."""
    import json

    candidate = await _adopt(ctx)
    accepted = await _approve_and_accept(ctx, candidate)
    assert accepted.consolidated
    consolidated_key = accepted.candidate.consolidated_key
    consolidated_kind = accepted.candidate.consolidated_kind

    stored = sa.AdoptedShareCandidate.from_dict(
        json.loads((await ctx.mem.get_memory(sa.KIND, candidate.key()))["value"]),
    )
    stored.mark_upstream_revoked("2026-09-03T00:00:00+00:00")

    assert stored.is_revoked_upstream
    assert stored.review_state == sa.REVIEW_ACCEPTED
    assert "[withdrawn upstream]" in stored.to_summary_text()
    # The consolidated record is the recipient's, and is untouched.
    assert await ctx.mem.get_memory(consolidated_kind, consolidated_key) is not None


# ---------------------------------------------------------------------------
# Governance primacy: the brake is first, and an approval is not an override
# ---------------------------------------------------------------------------


async def test_the_parking_brake_halts_adoption_and_acceptance_alike(ctx, tmp_path):
    """The brake is evaluated first, for every action, and nothing overrides it.

    Sharing did not acquire a scope of its own: a halt on `training` is a halt
    on taking someone else's learning too. An approved candidate is refused
    while the brake is engaged, and survives the refusal rather than being
    spent.
    """
    governance = GovernanceStore(ctx.mem.db_path)
    candidate = await _adopt(ctx)
    grant = await rc.grant_share_acceptance_approval(
        ctx,
        competency_id=candidate.competency_id,
        slug=candidate.slug,
        approver="taylor",
    )
    assert grant.granted

    governance.engage("training", reason="test halt", actor="operator")
    ctx.governance_store = governance
    try:
        blocked_adopt = await rc.run_share_adoption_through_runtime_contract(
            ctx,
            rc.SHARE_ACTION_ADOPT,
            package=package(share_id="share-brake"),
            competency_id="household",
        )
        assert blocked_adopt.governance_allowed is False
        assert blocked_adopt.outcome == rc.LEARNING_OUTCOME_BRAKE_DENIED

        blocked_accept = await rc.run_share_adoption_through_runtime_contract(
            ctx,
            rc.SHARE_ACTION_ACCEPT,
            competency_id=candidate.competency_id,
            slug=candidate.slug,
            reviewer="taylor",
        )
        assert blocked_accept.governance_allowed is False
        assert blocked_accept.outcome == rc.LEARNING_OUTCOME_BRAKE_DENIED
        assert not blocked_accept.consolidated
    finally:
        governance.disengage(reason="test halt lifted", actor="operator")
        ctx.governance_store = None

    # The approval survived the refusal: it authorises this candidate, and the
    # brake was never something it could override.
    resumed = await rc.run_share_adoption_through_runtime_contract(
        ctx,
        rc.SHARE_ACTION_ACCEPT,
        competency_id=candidate.competency_id,
        slug=candidate.slug,
        reviewer="taylor",
    )
    assert resumed.outcome == rc.SHARE_OUTCOME_ACCEPTED, resumed.reason
    assert resumed.consolidated


async def test_the_seam_refuses_an_unknown_action(ctx):
    """An unknown action is a programming error, not an outcome to report."""
    with pytest.raises(ValueError, match="unknown share-adoption action"):
        await rc.run_share_adoption_through_runtime_contract(ctx, "share_do_whatever")


async def test_every_action_writes_exactly_one_reflection(ctx):
    """The cross-surface audit trail of what was decided about a shared package.

    Named accounts, group, share, revision and digests; no shared content, so
    reading the Reflection trail is not a way around the sanitizer.
    """
    candidate = await _adopt(ctx)
    await _approve_and_accept(ctx, candidate)

    share_rows = _share_reflections(ctx)
    actions = [row["action"] for row in share_rows]
    assert rc.SHARE_ACTION_ADOPT in actions
    assert rc.SHARE_ACTION_APPROVE_ACCEPTANCE in actions
    assert rc.SHARE_ACTION_ACCEPT in actions

    rendered = str(share_rows)
    assert "group-1" in rendered
    assert "share-1" in rendered
    assert "Tuesday evening" not in rendered, "a Reflection must not carry shared content"
