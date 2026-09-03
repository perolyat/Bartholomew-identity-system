"""
The Learning and Memory Control Centre: the governed operations behind it.

Everything here runs against real authorities -- a real `MemoryStore`, a real
`ObjectiveStore` objective with real evidence events, the real `GovernanceStore`
Parking Brake, and the real `Identity.yaml` this repository ships. Nothing is
mocked, because the claims being made are security and governance claims and a
mocked store proves only that the mock behaved.

The rule these tests exist to hold, unchanged from PR #83 and extended rather
than relaxed by this package:

    Bartholomew may autonomously conclude "I may have learned something".
    Bartholomew may NOT autonomously conclude "this lesson is now trusted
    knowledge" -- and neither may a policy, however it is configured.

Suites, in the order the contract's acceptance requirements run:

  A. Reading a candidate: provenance, supporting experience, classification.
  B. Editing: material vs administrative, fingerprints, approvals, revisions.
  C. Acceptance: still candidate-bound, still exact, still manual.
  D. Rejection: still terminal.
  E. Shadow evaluation: deterministic, explained, and structurally inert.
  F. Policy: versioned, conflict-guarded, and unable to enable acceptance.
  G. Correction, supersession, revocation and retrieval eligibility.
  H. Isolation and compatibility with what already existed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from bartholomew.kernel import candidate_learning, learning_authorization, learning_policy
from bartholomew.kernel import objective_store as os_mod
from bartholomew.kernel.competency import COMPETENCY_KINDS
from bartholomew.kernel.memory.privacy_guard import set_consent_handler
from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.objective_store import ObjectiveStore
from bartholomew.kernel.reflection import REFLECTION_KIND
from bartholomew.kernel.runtime_contract import (
    LEARNING_ACTION_ACCEPT,
    LEARNING_ACTION_REJECT,
    LEARNING_OUTCOME_ACCEPTED,
    LEARNING_OUTCOME_APPROVAL_REQUIRED,
    LEARNING_OUTCOME_EDITED,
    LEARNING_OUTCOME_EVALUATED,
    LEARNING_OUTCOME_INVALID,
    LEARNING_OUTCOME_NOT_STORED,
    LEARNING_OUTCOME_POLICY_UPDATED,
    LEARNING_OUTCOME_QUEUED_FOR_CONSENT,
    LEARNING_OUTCOME_REJECTED,
    LEARNING_OUTCOME_REVISION_CONFLICT,
    LEARNING_OUTCOME_REVOKED,
    LEARNING_OUTCOME_UNCHANGED,
    ShadowWriteViolationError,
    grant_learning_acceptance_approval,
    load_learning_policy,
    run_candidate_edit_through_runtime_contract,
    run_candidate_lesson_through_runtime_contract,
    run_competency_correction_through_runtime_contract,
    run_competency_revocation_through_runtime_contract,
    run_learning_policy_update_through_runtime_contract,
    run_shadow_learning_evaluation_through_runtime_contract,
)
from identity_interpreter.identity_context import IdentityContext

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPETENCY_ID = "estate_management"
REVIEWER = "taylor"
APPROVER = "taylor"
EDITOR = "taylor"


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
    db_path = str(tmp_path / "control_centre.db")
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


async def _propose(ctx, objective_id: int):
    result = await run_candidate_lesson_through_runtime_contract(
        ctx,
        "learning_propose",
        objective_id=objective_id,
        competency_id=COMPETENCY_ID,
    )
    assert result.outcome == "proposed", result.reason
    return result.lesson


async def _stored_candidate(ctx, lesson):
    row = await ctx.mem.get_memory(candidate_learning.KIND, lesson.key())
    assert row is not None
    return candidate_learning.CandidateLesson.from_dict(json.loads(row["value"]))


async def _rows_of_kind(ctx, kind: str):
    return await ctx.mem.list_memories_by_kind([kind], limit=500)


async def _learning_reflections(ctx) -> list[dict]:
    """Every learning-surface Reflection, read from the `reflections` table.

    The same helper `tests/test_learning_acceptance_authorization.py` uses --
    Reflections are their own sink, not `memories` rows, so a kind lookup
    against `MemoryStore` finds nothing.
    """
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


# ===========================================================================
# A. Reading a candidate
# ===========================================================================


async def test_a1_candidate_exposes_its_supporting_experience_and_provenance(ctx):
    """
    Acceptance requirement 3.

    A reviewer deciding whether to accept a lesson must be able to see what it
    stands on without a second query: the objective, the exact evidence event
    ids, the verbatim observations, and where the inference came from.
    """
    objective_id = _run_the_experience(ctx)
    lesson = await _propose(ctx, objective_id)
    stored = await _stored_candidate(ctx, lesson)

    assert stored.source.objective_id == objective_id
    assert stored.source.objective_title
    assert stored.source.resolution == os_mod.RESOLUTION_ACHIEVED
    assert len(stored.source.supporting_event_ids) == 2
    assert len(stored.source.observations) == 2
    assert any("warranty" in text for text in stored.source.observations)

    assert stored.provenance.source_type == candidate_learning.EXPERIENCE_SOURCE_TYPE
    assert stored.provenance.recorded_by == "reflection"
    # Provenance back into the Reflection authority, not a second audit log.
    assert stored.reflection_row_id is not None

    assert stored.epistemic_status == candidate_learning.EPISTEMIC_INFERENCE
    assert stored.confidence == candidate_learning.SINGLE_EXPERIENCE_CONFIDENCE
    assert stored.requires_review is True


async def test_a2_a_proposed_candidate_carries_the_conservative_defaults(ctx):
    """The dimensions nobody has assessed default to the cautious answer."""
    lesson = await _propose(ctx, _run_the_experience(ctx))
    stored = await _stored_candidate(ctx, lesson)

    assert stored.risk_class is None, "risk must be unassessed rather than assumed low"
    assert stored.reversible is None, "reversibility must be unassessed rather than assumed"
    assert stored.affected_applications == []
    assert stored.display_state == candidate_learning.DISPLAY_NORMAL
    # `personal` classification is not shareable; the derivation is explicit.
    assert stored.effective_sharing_eligible is False


# ===========================================================================
# B. Editing
# ===========================================================================


async def test_b1_a_material_edit_changes_the_candidate_fingerprint(ctx):
    """Acceptance requirement 4."""
    lesson = await _propose(ctx, _run_the_experience(ctx))
    before = learning_authorization.fingerprint_for(lesson)

    result = await run_candidate_edit_through_runtime_contract(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        editor=EDITOR,
        expected_revision=lesson.revision,
        inferred_rule="Call the warranty line before booking any paid engineer",
    )

    assert result.outcome == LEARNING_OUTCOME_EDITED
    assert result.material_change is True
    assert result.fingerprint_before == before
    assert result.fingerprint_after != before

    stored = await _stored_candidate(ctx, lesson)
    assert learning_authorization.fingerprint_for(stored) == result.fingerprint_after
    assert stored.revision == lesson.revision + 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("inferred_rule", "A completely different rule"),
        ("conditions", "Only when the boiler is under warranty"),
        ("classification", "potentially_generalisable"),
        ("confidence", 0.9),
        ("risk_class", "low"),
        ("reversible", True),
        ("affected_applications", ["calendar"]),
        ("sharing_eligible", True),
    ],
)
async def test_b2_every_material_dimension_moves_the_fingerprint(ctx, field, value):
    """
    Acceptance requirement 4, across the whole material vocabulary the
    contract names -- rule, conditions, classification, confidence, affected
    application and sharing eligibility. Each is checked on its own so a
    regression names the field that stopped counting.
    """
    lesson = await _propose(ctx, _run_the_experience(ctx))
    before = learning_authorization.fingerprint_for(lesson)

    result = await run_candidate_edit_through_runtime_contract(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        editor=EDITOR,
        expected_revision=lesson.revision,
        **{field: value},
    )
    assert result.material_change is True, f"{field} must be material"
    assert result.fingerprint_after != before


async def test_b3_a_material_edit_invalidates_a_prior_approval(ctx):
    """
    Acceptance requirement 5.

    The approval row is deliberately kept -- it is the record of who approved
    what -- but it no longer authorises the candidate, and acceptance refuses.
    """
    lesson = await _propose(ctx, _run_the_experience(ctx))
    granted = await grant_learning_acceptance_approval(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        approver=APPROVER,
    )
    assert granted.granted is True

    edit = await run_candidate_edit_through_runtime_contract(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        editor=EDITOR,
        expected_revision=lesson.revision,
        inferred_rule="Something the approver never read",
    )
    assert edit.approval_invalidated is True

    # The approval survives as an audit record...
    approval_row = await ctx.mem.get_memory(learning_authorization.KIND, lesson.key())
    assert approval_row is not None

    # ...and no longer authorises anything.
    stored = await _stored_candidate(ctx, lesson)
    approval = learning_authorization.LearningAcceptanceApproval.from_dict(
        json.loads(approval_row["value"]),
    )
    allowed, reason = approval.authorizes(stored)
    assert allowed is False
    assert "changed since it was approved" in reason

    accept = await run_candidate_lesson_through_runtime_contract(
        ctx,
        LEARNING_ACTION_ACCEPT,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        reviewer=REVIEWER,
    )
    assert accept.outcome == LEARNING_OUTCOME_APPROVAL_REQUIRED
    assert accept.consolidated is False


async def test_b4_an_administrative_change_does_not_alter_meaning(ctx):
    """
    Acceptance requirement 6.

    Pinning a candidate to look at later must not read as "this lesson
    changed": the fingerprint holds, the revision holds, and an approval
    granted beforehand still authorises acceptance.
    """
    lesson = await _propose(ctx, _run_the_experience(ctx))
    granted = await grant_learning_acceptance_approval(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        approver=APPROVER,
    )
    assert granted.granted is True
    before = learning_authorization.fingerprint_for(lesson)

    result = await run_candidate_edit_through_runtime_contract(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        editor=EDITOR,
        expected_revision=lesson.revision,
        display_state=candidate_learning.DISPLAY_PINNED,
    )

    assert result.outcome == LEARNING_OUTCOME_UNCHANGED
    assert result.material_change is False
    assert result.fingerprint_after == before
    assert result.approval_invalidated is False

    stored = await _stored_candidate(ctx, lesson)
    assert stored.display_state == candidate_learning.DISPLAY_PINNED
    assert stored.revision == lesson.revision, "an administrative change is not a new revision"

    # The approval still applies, and acceptance still works.
    accept = await run_candidate_lesson_through_runtime_contract(
        ctx,
        LEARNING_ACTION_ACCEPT,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        reviewer=REVIEWER,
    )
    assert accept.outcome == LEARNING_OUTCOME_ACCEPTED


async def test_b5_a_stale_edit_conflicts_instead_of_overwriting(ctx):
    """
    Acceptance requirement 19.

    Two reviewers, one candidate. The second edit names a revision that no
    longer exists, and nothing is written -- neither merged nor overwritten.
    """
    lesson = await _propose(ctx, _run_the_experience(ctx))
    stale_revision = lesson.revision

    first = await run_candidate_edit_through_runtime_contract(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        editor="first-reviewer",
        expected_revision=stale_revision,
        inferred_rule="The first reviewer's wording",
    )
    assert first.outcome == LEARNING_OUTCOME_EDITED

    second = await run_candidate_edit_through_runtime_contract(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        editor="second-reviewer",
        expected_revision=stale_revision,
        inferred_rule="The second reviewer's wording, written from a stale screen",
    )
    assert second.outcome == LEARNING_OUTCOME_REVISION_CONFLICT
    assert second.stored_lesson is not None, "the conflict must hand back what is stored"
    assert "changed since you opened it" in second.reason

    stored = await _stored_candidate(ctx, lesson)
    assert (
        stored.inferred_rule == "The first reviewer's wording"
    ), "the first reviewer's edit must survive a stale second edit"


async def test_b5b_reverting_a_candidate_does_not_revive_a_cancelled_approval(ctx):
    """
    The hole content-only binding leaves, closed.

    An approval invalidated by an edit was revived by editing the candidate
    *back*: the digest matched again, so an approval the reviewer had been
    told in those words no longer applied silently accepted a candidate two
    revisions on from the one they read.

    `LearningAcceptanceApproval.authorizes()` now also requires the revision to
    match. Any material edit increments it and nothing decrements it, so a
    cancelled approval stays cancelled however the content moves.
    """
    lesson = await _propose(ctx, _run_the_experience(ctx))
    original_rule = lesson.inferred_rule

    granted = await grant_learning_acceptance_approval(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        approver=APPROVER,
    )
    assert granted.granted is True
    assert granted.approval.candidate_revision == lesson.revision

    away = await run_candidate_edit_through_runtime_contract(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        editor=EDITOR,
        expected_revision=lesson.revision,
        inferred_rule="Something else entirely",
    )
    assert away.approval_invalidated is True

    back = await run_candidate_edit_through_runtime_contract(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        editor=EDITOR,
        expected_revision=away.lesson.revision,
        inferred_rule=original_rule,
    )
    assert back.outcome == LEARNING_OUTCOME_EDITED
    # The digest is back to what was approved...
    assert back.fingerprint_after == away.fingerprint_before
    # ...and the edit still reports the approval as no longer applying.
    assert back.approval_invalidated is True

    refused = await run_candidate_lesson_through_runtime_contract(
        ctx,
        LEARNING_ACTION_ACCEPT,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        reviewer=REVIEWER,
    )
    assert refused.outcome == LEARNING_OUTCOME_APPROVAL_REQUIRED
    assert "edited since" in refused.reason
    assert "currently reads the same" in refused.reason
    assert refused.consolidated is False
    assert not await _rows_of_kind(ctx, "competency_heuristic")


async def test_b5c_re_proposing_carries_the_revision_forward(ctx):
    """
    `expected_revision` is only a staleness token if it never goes backwards.

    Re-proposing used to reset it to 1, which meant an editor holding a stale
    "1" from before a supersession would pass the check, and an approval
    recorded at revision 1 could re-apply to a differently-sourced candidate
    that happened to digest the same.
    """
    objective_id = _run_the_experience(ctx)
    first = await _propose(ctx, objective_id)
    assert first.revision == 1

    await run_candidate_edit_through_runtime_contract(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=first.slug,
        editor=EDITOR,
        expected_revision=1,
        inferred_rule="Edited once",
    )
    reproposed = await _propose(ctx, objective_id)
    assert reproposed.revision > 2, "a re-proposal must not reset the revision"

    stale = await run_candidate_edit_through_runtime_contract(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=first.slug,
        editor=EDITOR,
        expected_revision=1,
        inferred_rule="Written against a revision that no longer exists",
    )
    assert stale.outcome == LEARNING_OUTCOME_REVISION_CONFLICT


async def test_b6b_an_edit_is_refused_when_the_prior_revision_cannot_be_kept(ctx):
    """
    Losing an edit is recoverable; losing approved wording is not.

    The archive write goes through the same governed path as everything else,
    so it can be refused or held for consent. When it does not land, the edit
    is refused outright rather than overwriting the only copy of what somebody
    approved.
    """
    lesson = await _propose(ctx, _run_the_experience(ctx))
    original_rule = lesson.inferred_rule

    class _RefusingArchive:
        """Wraps the real store, refusing exactly the archive write."""

        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def upsert_memory(self, kind, key, value, ts, **kwargs):
            if kind == learning_policy.CANDIDATE_REVISION_KIND:
                from bartholomew.kernel.memory_store import StoreResult

                return StoreResult(stored=False, outcome="refused")
            return await self._inner.upsert_memory(kind, key, value, ts, **kwargs)

    real = ctx.mem
    ctx.mem = _RefusingArchive(real)
    try:
        result = await run_candidate_edit_through_runtime_contract(
            ctx,
            competency_id=COMPETENCY_ID,
            slug=lesson.slug,
            editor=EDITOR,
            expected_revision=lesson.revision,
            inferred_rule="An edit that would discard the approved wording",
        )
    finally:
        ctx.mem = real

    assert result.outcome == LEARNING_OUTCOME_NOT_STORED
    assert "could not be kept" in result.reason

    stored = await _stored_candidate(ctx, lesson)
    assert stored.inferred_rule == original_rule
    assert stored.revision == lesson.revision


async def test_b6c_an_edit_cannot_land_on_a_recreated_record(ctx):
    """
    What `expected_memory_id` actually guards, stated accurately.

    `upsert_memory()` UPDATEs a row in place, so its id survives an ordinary
    edit and the precondition does not detect a competing *update* -- the
    revision re-check immediately before the write is what narrows that. What
    the row-id precondition does catch is the case a revision check cannot see
    at all: the record deleted and recreated underneath the edit, where the
    revision may legitimately read the same while the row is somebody else's.
    That is the ABA problem `correct_memory()`'s docstring describes, and it is
    why the write carries the id it read.
    """
    from bartholomew.kernel.runtime_contract import _write_candidate_lesson

    lesson = await _propose(ctx, _run_the_experience(ctx))
    stale_memory_id = (await ctx.mem.get_memory(candidate_learning.KIND, lesson.key()))["id"]

    # The record is deleted and something else is written at the same key.
    await ctx.mem.delete_memory(candidate_learning.KIND, lesson.key())
    replacement = await _propose(ctx, _run_the_experience(ctx, title="A replacement objective"))
    replacement.slug = lesson.slug
    await _write_candidate_lesson(ctx, replacement)
    live_id = (await ctx.mem.get_memory(candidate_learning.KIND, lesson.key()))["id"]
    assert live_id != stale_memory_id

    lesson.inferred_rule = "An edit against a row that no longer exists"
    result = await _write_candidate_lesson(ctx, lesson, expected_memory_id=stale_memory_id)
    assert result.stored is False
    assert result.outcome == "precondition_failed"

    live = await _stored_candidate(ctx, lesson)
    assert live.inferred_rule == replacement.inferred_rule


async def test_b6c2_a_stale_revision_is_refused_at_the_write_as_well(ctx):
    """
    The re-check immediately before the write, exercised on its own.

    The check at the top of the edit seam is separated from the write by an
    approval read and an archive write. This one runs at the last moment, so an
    edit whose base revision was superseded while it was in flight is refused
    rather than overwriting.
    """
    lesson = await _propose(ctx, _run_the_experience(ctx))
    base_revision = lesson.revision

    class _EditsUnderneath:
        """Wraps the store and lands somebody else's edit during the archive
        write -- i.e. after the seam's first revision check, before its own."""

        def __init__(self, inner, ctx_ref, slug):
            self._inner = inner
            self._ctx = ctx_ref
            self._slug = slug
            self.interfered = False

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def upsert_memory(self, kind, key, value, ts, **kwargs):
            result = await self._inner.upsert_memory(kind, key, value, ts, **kwargs)
            if kind == learning_policy.CANDIDATE_REVISION_KIND and not self.interfered:
                self.interfered = True
                other = await _load_candidate_lesson(self._ctx, COMPETENCY_ID, self._slug)
                other.inferred_rule = "Somebody else's wording, landed mid-edit"
                other.revision += 1
                await self._inner.upsert_memory(
                    candidate_learning.KIND,
                    other.key(),
                    json.dumps(other.to_dict()),
                    ts,
                    summary=other.to_summary_text(),
                )
            return result

    from bartholomew.kernel.runtime_contract import _load_candidate_lesson

    real = ctx.mem
    ctx.mem = _EditsUnderneath(real, ctx, lesson.slug)
    try:
        result = await run_candidate_edit_through_runtime_contract(
            ctx,
            competency_id=COMPETENCY_ID,
            slug=lesson.slug,
            editor=EDITOR,
            expected_revision=base_revision,
            inferred_rule="My wording, written against a superseded base",
        )
    finally:
        assert ctx.mem.interfered, "the test must actually have interfered"
        ctx.mem = real

    assert result.outcome == LEARNING_OUTCOME_REVISION_CONFLICT
    assert result.stored_lesson is not None

    live = await _stored_candidate(ctx, lesson)
    assert live.inferred_rule == "Somebody else's wording, landed mid-edit"


async def test_b6d_the_material_field_vocabulary_is_enforced_not_documented(ctx):
    """
    `MATERIAL_CANDIDATE_FIELDS` declares the partition; this makes it true.

    A constant naming the material set that nothing checks is documentation
    wearing code's clothes -- it can drift from `fingerprint_for()` without
    anything noticing. Here every editable material field is edited and must
    move the fingerprint, and every administrative one must not.
    """
    from bartholomew.kernel.runtime_contract import (
        ADMINISTRATIVE_CANDIDATE_FIELDS,
        MATERIAL_CANDIDATE_FIELDS,
    )

    editable_material = {
        "inferred_rule": "A different rule",
        "conditions": "Different conditions",
        "classification": "potentially_generalisable",
        "confidence": 0.77,
        "risk_class": "moderate",
        "reversible": True,
        "affected_applications": ["calendar"],
        "sharing_eligible": True,
    }
    # The rest of the material set is fixed at proposal time (it names the
    # experience the lesson stands on), so it is not editable here -- but it
    # must still be covered by the digest.
    not_editable = MATERIAL_CANDIDATE_FIELDS - set(editable_material)
    assert not_editable == {
        "lesson_kind",
        "epistemic_status",
        "objective_id",
        "supporting_event_ids",
        "competency_id",
    }

    for field_name, value in editable_material.items():
        lesson = await _propose(
            ctx,
            _run_the_experience(ctx, title=f"Objective for {field_name}"),
        )
        before = learning_authorization.fingerprint_for(lesson)
        result = await run_candidate_edit_through_runtime_contract(
            ctx,
            competency_id=COMPETENCY_ID,
            slug=lesson.slug,
            editor=EDITOR,
            expected_revision=lesson.revision,
            **{field_name: value},
        )
        assert result.material_change is True, f"{field_name} must be material"
        assert result.fingerprint_after != before

    assert ADMINISTRATIVE_CANDIDATE_FIELDS == {"display_state"}
    lesson = await _propose(ctx, _run_the_experience(ctx, title="Objective for display state"))
    admin = await run_candidate_edit_through_runtime_contract(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        editor=EDITOR,
        expected_revision=lesson.revision,
        display_state=candidate_learning.DISPLAY_PINNED,
    )
    assert admin.material_change is False


async def test_b6_the_prior_revision_is_preserved(ctx):
    """
    Acceptance requirement: "preserve the prior revision and audit history".

    "What exactly did I approve?" must stay answerable after an edit.
    """
    lesson = await _propose(ctx, _run_the_experience(ctx))
    original_rule = lesson.inferred_rule

    result = await run_candidate_edit_through_runtime_contract(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        editor=EDITOR,
        expected_revision=lesson.revision,
        inferred_rule="Rewritten",
    )
    assert result.archived_revision_key == f"{lesson.key()}@r{lesson.revision}"

    archived = await ctx.mem.get_memory(
        learning_policy.CANDIDATE_REVISION_KIND,
        result.archived_revision_key,
    )
    assert archived is not None
    assert json.loads(archived["value"])["inferred_rule"] == original_rule


async def test_b7_a_terminal_candidate_cannot_be_edited(ctx):
    """A review decision is terminal, and editing is not a way back into it."""
    lesson = await _propose(ctx, _run_the_experience(ctx))
    rejected = await run_candidate_lesson_through_runtime_contract(
        ctx,
        LEARNING_ACTION_REJECT,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        reviewer=REVIEWER,
    )
    assert rejected.outcome == LEARNING_OUTCOME_REJECTED

    stored = await _stored_candidate(ctx, lesson)
    result = await run_candidate_edit_through_runtime_contract(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        editor=EDITOR,
        expected_revision=stored.revision,
        inferred_rule="Reopening a closed decision",
    )
    assert result.outcome == LEARNING_OUTCOME_INVALID
    assert "terminal" in result.reason


async def test_b8_an_edit_is_audited(ctx):
    """Every edit leaves a Reflection naming the editor and both fingerprints."""
    lesson = await _propose(ctx, _run_the_experience(ctx))
    await run_candidate_edit_through_runtime_contract(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        editor=EDITOR,
        expected_revision=lesson.revision,
        inferred_rule="Edited",
    )

    edits = [
        item
        for item in await _learning_reflections(ctx)
        if item.get("action") == "learning_candidate_edit"
    ]
    assert edits, "an edit must be recorded through the existing Reflection authority"
    entry = edits[0]
    assert entry["editor"] == EDITOR
    assert entry["fingerprint_before"] != entry["fingerprint_after"]
    assert entry["material_change"] is True
    assert entry["consolidated"] is False


# ===========================================================================
# C. Acceptance
# ===========================================================================


async def test_c1_manual_acceptance_still_requires_candidate_bound_approval(ctx):
    """
    Acceptance requirement 7, restated against the control centre's own path.

    Everything Package D adds -- editing, previewing, configuring a policy --
    leaves this exactly where PR #83 put it.
    """
    lesson = await _propose(ctx, _run_the_experience(ctx))

    unapproved = await run_candidate_lesson_through_runtime_contract(
        ctx,
        LEARNING_ACTION_ACCEPT,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        reviewer=REVIEWER,
    )
    assert unapproved.outcome == LEARNING_OUTCOME_APPROVAL_REQUIRED
    assert unapproved.consolidated is False

    await grant_learning_acceptance_approval(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        approver=APPROVER,
    )
    approved = await run_candidate_lesson_through_runtime_contract(
        ctx,
        LEARNING_ACTION_ACCEPT,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        reviewer=REVIEWER,
    )
    assert approved.outcome == LEARNING_OUTCOME_ACCEPTED
    assert approved.consolidated is True


async def test_c2_an_approval_for_an_earlier_revision_cannot_accept_a_later_one(ctx):
    """
    Acceptance requirement 8.

    The binding is the fingerprint, so this holds without the acceptance path
    needing to know that a revision number exists.
    """
    lesson = await _propose(ctx, _run_the_experience(ctx))
    await grant_learning_acceptance_approval(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        approver=APPROVER,
    )
    approval_row = await ctx.mem.get_memory(learning_authorization.KIND, lesson.key())
    approved_revision = json.loads(approval_row["value"])["candidate_revision"]

    await run_candidate_edit_through_runtime_contract(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        editor=EDITOR,
        expected_revision=lesson.revision,
        confidence=0.95,
    )
    stored = await _stored_candidate(ctx, lesson)
    assert stored.revision > approved_revision

    refused = await run_candidate_lesson_through_runtime_contract(
        ctx,
        LEARNING_ACTION_ACCEPT,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        reviewer=REVIEWER,
    )
    assert refused.outcome == LEARNING_OUTCOME_APPROVAL_REQUIRED
    assert refused.consolidated is False
    assert not await _rows_of_kind(ctx, "competency_heuristic")


async def test_c3_accept_produces_retrievable_knowledge_only_after_valid_approval(ctx):
    """
    Acceptance requirement 9.

    "Retrievable" is checked structurally: the consolidated record must land
    under a kind the chat retrieval seam's filter actually includes.
    """
    lesson = await _propose(ctx, _run_the_experience(ctx))

    await run_candidate_lesson_through_runtime_contract(
        ctx,
        LEARNING_ACTION_ACCEPT,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        reviewer=REVIEWER,
    )
    assert not await _rows_of_kind(ctx, "competency_heuristic")

    await grant_learning_acceptance_approval(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        approver=APPROVER,
    )
    accepted = await run_candidate_lesson_through_runtime_contract(
        ctx,
        LEARNING_ACTION_ACCEPT,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        reviewer=REVIEWER,
    )
    assert accepted.consolidated is True

    rows = await _rows_of_kind(ctx, "competency_heuristic")
    assert len(rows) == 1
    assert rows[0]["key"] == lesson.key()
    assert "competency_heuristic" in COMPETENCY_KINDS


# ===========================================================================
# D. Rejection
# ===========================================================================


async def test_d1_reject_remains_terminal(ctx):
    """
    Acceptance requirement 10.

    A rejected candidate cannot be approved, cannot be accepted, and cannot be
    edited back into a proposal -- and it consolidates nothing, then or ever.
    """
    lesson = await _propose(ctx, _run_the_experience(ctx))
    rejected = await run_candidate_lesson_through_runtime_contract(
        ctx,
        LEARNING_ACTION_REJECT,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        reviewer=REVIEWER,
    )
    assert rejected.outcome == LEARNING_OUTCOME_REJECTED

    granted = await grant_learning_acceptance_approval(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        approver=APPROVER,
    )
    assert granted.granted is False
    assert "terminal" in granted.reason

    accepted = await run_candidate_lesson_through_runtime_contract(
        ctx,
        LEARNING_ACTION_ACCEPT,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        reviewer=REVIEWER,
    )
    assert accepted.outcome != LEARNING_OUTCOME_ACCEPTED
    assert not await _rows_of_kind(ctx, "competency_heuristic")

    # The rejection itself survives, under a kind nothing can reason from.
    stored = await _stored_candidate(ctx, lesson)
    assert stored.review_state == candidate_learning.REVIEW_REJECTED
    assert candidate_learning.KIND not in COMPETENCY_KINDS


async def test_d2_shadow_evaluating_a_rejected_candidate_does_not_revive_it(ctx):
    """A preview is a preview even for a decision already made."""
    lesson = await _propose(ctx, _run_the_experience(ctx))
    await run_candidate_lesson_through_runtime_contract(
        ctx,
        LEARNING_ACTION_REJECT,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        reviewer=REVIEWER,
    )

    result = await run_shadow_learning_evaluation_through_runtime_contract(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
    )
    assert result.outcome == LEARNING_OUTCOME_EVALUATED
    assert result.decision.candidate_review_state == candidate_learning.REVIEW_REJECTED

    stored = await _stored_candidate(ctx, lesson)
    assert stored.review_state == candidate_learning.REVIEW_REJECTED
    assert not await _rows_of_kind(ctx, "competency_heuristic")


# ===========================================================================
# E. Shadow evaluation
# ===========================================================================


async def _permissive_policy(ctx, updated_by="taylor"):
    """The most permissive policy the schema allows, saved for real.

    Every threshold at its loosest, every exclusion emptied, and
    `requested_execution_mode` set to `auto` -- the configuration a user
    would write if they genuinely wanted automatic acceptance. It is used by
    the tests below precisely because it must still consolidate nothing.
    """
    policy = learning_policy.LearningPolicy(
        enabled_categories=sorted(candidate_learning.LESSON_KINDS),
        excluded_categories=[],
        max_risk="critical",
        require_reversible=False,
        min_supporting_experiences=1,
        min_confidence=0.0,
        contradiction_behaviour=learning_policy.CONTRADICTION_ESCALATE,
        max_affected_capabilities=99,
        max_affected_applications=99,
        excluded_privacy_classes=[],
        excluded_classifications=[],
        exclude_sharing_eligible=False,
        expires_after_days=None,
        review_interval_days=None,
        requested_execution_mode=learning_policy.REQUESTED_MODE_AUTO,
    )
    stored = await load_learning_policy(ctx)
    result = await run_learning_policy_update_through_runtime_contract(
        ctx,
        policy,
        expected_revision=stored.revision,
        updated_by=updated_by,
    )
    assert result.outcome == LEARNING_OUTCOME_POLICY_UPDATED, result.reason
    return result.policy


async def test_e1_shadow_decisions_are_one_of_exactly_three_and_deterministic(ctx):
    """Acceptance requirement 11."""
    lesson = await _propose(ctx, _run_the_experience(ctx))

    first = await run_shadow_learning_evaluation_through_runtime_contract(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
    )
    second = await run_shadow_learning_evaluation_through_runtime_contract(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
    )

    assert first.decision.decision in learning_policy.DECISIONS
    assert first.decision.decision == second.decision.decision
    assert [r.rule_id for r in first.decision.matched_rules] == [
        r.rule_id for r in second.decision.matched_rules
    ]
    assert first.decision.reasons == second.decision.reasons


async def test_e2_shadow_decisions_explain_their_matched_rules_and_reasons(ctx):
    """Acceptance requirement 12: understandable to an ordinary user."""
    lesson = await _propose(ctx, _run_the_experience(ctx))
    result = await run_shadow_learning_evaluation_through_runtime_contract(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
    )
    decision = result.decision

    assert decision.decision == learning_policy.DECISION_WOULD_REFUSE
    assert decision.matched_rules, "a refusal must name the rules that refused"
    assert len(decision.reasons) == len(decision.matched_rules)
    for rule in decision.matched_rules:
        assert rule.rule_id in learning_policy.RULE_ORDER
        assert rule.effect in (learning_policy.EFFECT_REFUSE, learning_policy.EFFECT_ESCALATE)
        # Plain language, not a rule identifier repeated back at the user.
        assert rule.reason.endswith(".")
        assert rule.rule_id not in rule.reason

    payload = decision.to_dict()
    for field in (
        "candidate_key",
        "candidate_fingerprint",
        "policy_revision",
        "decision",
        "matched_rules",
        "reasons",
        "evaluated_at",
    ):
        assert field in payload, f"an inspectable result must carry {field}"
    assert payload["authorizes_acceptance"] is False
    assert payload["execution_mode"] == learning_policy.SHIPPED_EXECUTION_MODE


async def test_e2b_the_privacy_exclusion_rule_actually_fires(ctx):
    """
    A configured exclusion that silently does nothing is worse than no control.

    The candidate's privacy class has to be *evaluated* over the stored row:
    `get_memory()` returns no governance metadata, so an earlier version read
    an always-absent key and the `privacy_class_excluded` rule could never
    fire. A user could exclude a class from automatic acceptance and the
    preview would ignore them.
    """
    from bartholomew.kernel.runtime_contract import _privacy_class_for_candidate

    lesson = await _propose(ctx, _run_the_experience(ctx))
    stored = await _stored_candidate(ctx, lesson)

    # The shipped rules classify a candidate lesson, and the seam sees it.
    privacy_class = await _privacy_class_for_candidate(ctx, stored)
    assert privacy_class == "user.competency"

    # A policy excluding that class refuses on exactly that ground.
    policy = learning_policy.LearningPolicy(
        revision=1,
        enabled_categories=["procedural"],
        max_risk="critical",
        require_reversible=False,
        min_supporting_experiences=1,
        min_confidence=0.0,
        max_affected_capabilities=9,
        max_affected_applications=9,
        excluded_privacy_classes=[privacy_class],
        excluded_classifications=[],
        exclude_sharing_eligible=False,
        expires_after_days=None,
        review_interval_days=None,
    )
    facts = learning_policy.facts_from_lesson(
        stored,
        learning_authorization.fingerprint_for(stored),
        risk_class="low",
        reversible=True,
        privacy_class=privacy_class,
        sharing_eligible=False,
    )
    decision = learning_policy.evaluate(policy, facts, evaluated_at="2026-09-01T00:00:00+00:00")
    assert decision.decision == learning_policy.DECISION_WOULD_REFUSE
    assert [rule.rule_id for rule in decision.matched_rules] == ["privacy_class_excluded"]


async def test_e2c_the_new_kinds_are_classified_by_the_memory_rules(ctx):
    """
    An archive of a lesson must not be governed more loosely than the lesson.

    `candidate_lesson_revision` holds the same content one edit ago. Without a
    rule it would carry no privacy class at all, while the live candidate
    carries `user.competency` -- the sort of gap an archive quietly becomes if
    nobody writes the rule down.
    """
    from bartholomew.kernel.memory_rules import MemoryRulesEngine

    engine = MemoryRulesEngine(watch_file=False)
    candidate_class = engine.evaluate(
        {"kind": candidate_learning.KIND, "key": "c.s", "value": "{}"},
    ).get("privacy_class")
    assert candidate_class

    for kind in (
        learning_policy.CANDIDATE_REVISION_KIND,
        learning_policy.POLICY_KIND,
        learning_policy.EVALUATION_KIND,
    ):
        evaluated = engine.evaluate({"kind": kind, "key": "c.s", "value": "{}"})
        assert (
            evaluated.get("privacy_class") == candidate_class
        ), f"{kind} is classified more loosely than the candidate it relates to"
        assert evaluated.get("recall_policy") == "context_only"


async def test_e3_shadow_evaluation_cannot_create_an_approval(ctx):
    """Acceptance requirement 14."""
    lesson = await _propose(ctx, _run_the_experience(ctx))
    await _permissive_policy(ctx)

    result = await run_shadow_learning_evaluation_through_runtime_contract(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
    )
    assert result.decision.decision == learning_policy.DECISION_WOULD_ACCEPT

    assert not await _rows_of_kind(ctx, learning_authorization.KIND)
    assert await ctx.mem.get_memory(learning_authorization.KIND, lesson.key()) is None


async def test_e4_shadow_evaluation_cannot_change_the_candidate_review_state(ctx):
    """Acceptance requirement 15."""
    lesson = await _propose(ctx, _run_the_experience(ctx))
    await _permissive_policy(ctx)
    before = await _stored_candidate(ctx, lesson)

    await run_shadow_learning_evaluation_through_runtime_contract(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
    )

    after = await _stored_candidate(ctx, lesson)
    assert after.review_state == before.review_state == candidate_learning.REVIEW_PROPOSED
    assert after.revision == before.revision
    assert after.reviewer is None and after.reviewed_at is None
    assert after.to_dict() == before.to_dict(), "the candidate row must be untouched"


async def test_e5_shadow_evaluation_cannot_write_accepted_competency_kinds(ctx):
    """Acceptance requirement 16."""
    lesson = await _propose(ctx, _run_the_experience(ctx))
    await _permissive_policy(ctx)

    await run_shadow_learning_evaluation_through_runtime_contract(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
    )

    for kind in COMPETENCY_KINDS:
        assert not await _rows_of_kind(ctx, kind), f"shadow evaluation wrote a {kind} record"


async def test_e6_a_permissive_identity_policy_cannot_turn_preview_into_acceptance(ctx):
    """
    Acceptance requirement 17.

    `learning_accept` is added to the allowlist -- the exact change someone
    would make if they believed the allowlist were the switch -- and
    acceptance is still refused, because the gate is the candidate-bound
    approval and not the allowlist.
    """
    permissive = IdentityContext(
        tool_use_default_allowed=True,
        tool_use_allowlist=[
            "learning_propose",
            "learning_reject",
            "learning_accept",
            "learning_shadow_evaluate",
            "learning_policy_update",
            "learning_candidate_edit",
        ],
    )
    ctx.identity_context = permissive

    lesson = await _propose(ctx, _run_the_experience(ctx))
    await _permissive_policy(ctx)

    preview = await run_shadow_learning_evaluation_through_runtime_contract(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
    )
    assert preview.decision.decision == learning_policy.DECISION_WOULD_ACCEPT
    assert preview.authorizes_acceptance is False
    assert preview.consolidated is False

    refused = await run_candidate_lesson_through_runtime_contract(
        ctx,
        LEARNING_ACTION_ACCEPT,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        reviewer=REVIEWER,
    )
    assert refused.outcome == LEARNING_OUTCOME_APPROVAL_REQUIRED
    assert not await _rows_of_kind(ctx, "competency_heuristic")


async def test_e7_a_configured_auto_accept_rule_still_consolidates_nothing(ctx):
    """
    Acceptance requirement 18.

    The user has asked for automatic acceptance in the plainest way the
    schema allows. Nothing accepts.
    """
    lesson = await _propose(ctx, _run_the_experience(ctx))
    policy = await _permissive_policy(ctx)

    assert policy.requested_execution_mode == learning_policy.REQUESTED_MODE_AUTO
    assert policy.execution_mode == learning_policy.SHIPPED_EXECUTION_MODE == "shadow"
    assert policy.auto_acceptance_enabled is False

    for _ in range(3):
        result = await run_shadow_learning_evaluation_through_runtime_contract(
            ctx,
            competency_id=COMPETENCY_ID,
            slug=lesson.slug,
        )
        assert result.decision.decision == learning_policy.DECISION_WOULD_ACCEPT

    stored = await _stored_candidate(ctx, lesson)
    assert stored.review_state == candidate_learning.REVIEW_PROPOSED
    assert not await _rows_of_kind(ctx, learning_authorization.KIND)
    for kind in COMPETENCY_KINDS:
        assert not await _rows_of_kind(ctx, kind)


async def test_e8_a_shadow_evaluation_is_not_reasoning_material(ctx):
    """
    The evaluation record is stored under a kind the retrieval seam's filter
    does not include, so no later turn can cite "would_accept" as knowledge.
    """
    lesson = await _propose(ctx, _run_the_experience(ctx))
    await run_shadow_learning_evaluation_through_runtime_contract(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
    )

    rows = await _rows_of_kind(ctx, learning_policy.EVALUATION_KIND)
    assert rows, "the preview must leave an inspectable record"
    assert learning_policy.EVALUATION_KIND not in COMPETENCY_KINDS
    assert learning_policy.POLICY_KIND not in COMPETENCY_KINDS
    assert learning_policy.CANDIDATE_REVISION_KIND not in COMPETENCY_KINDS


async def test_e9_the_shadow_writer_refuses_every_forbidden_kind(ctx):
    """
    The enumerated prohibition, exercised directly.

    A future change that routed a candidate, an approval or a competency
    record through the shadow write helper fails here rather than quietly
    consolidating a lesson.
    """
    from bartholomew.kernel.runtime_contract import _write_shadow_record

    for kind in sorted(learning_policy.FORBIDDEN_SHADOW_WRITE_KINDS):
        with pytest.raises(ShadowWriteViolationError):
            await _write_shadow_record(ctx, kind, "k", {"a": 1}, "summary")

    # And anything else it was never given permission for.
    with pytest.raises(ShadowWriteViolationError):
        await _write_shadow_record(ctx, "user_profile", "k", {"a": 1}, "summary")


# ===========================================================================
# F. Policy
# ===========================================================================


async def test_f1_a_policy_revision_affects_later_evaluations_only(ctx):
    """
    Acceptance requirement 13.

    Revision 1 refuses; revision 2 accepts. Both evaluation records survive,
    each naming the revision it ran under -- the later policy does not rewrite
    the earlier verdict.
    """
    lesson = await _propose(ctx, _run_the_experience(ctx))

    strict = await run_shadow_learning_evaluation_through_runtime_contract(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
    )
    assert strict.decision.decision == learning_policy.DECISION_WOULD_REFUSE
    strict_revision = strict.policy_revision
    strict_key = strict.decision.key()

    await _permissive_policy(ctx)

    relaxed = await run_shadow_learning_evaluation_through_runtime_contract(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
    )
    assert relaxed.decision.decision == learning_policy.DECISION_WOULD_ACCEPT
    assert relaxed.policy_revision > strict_revision

    # The earlier record is still exactly what it was.
    earlier = await ctx.mem.get_memory(learning_policy.EVALUATION_KIND, strict_key)
    assert earlier is not None
    earlier_payload = json.loads(earlier["value"])
    assert earlier_payload["decision"] == learning_policy.DECISION_WOULD_REFUSE
    assert earlier_payload["policy_revision"] == strict_revision

    # And nothing about the policy change accepted anything retrospectively.
    stored = await _stored_candidate(ctx, lesson)
    assert stored.review_state == candidate_learning.REVIEW_PROPOSED
    for kind in COMPETENCY_KINDS:
        assert not await _rows_of_kind(ctx, kind)


async def test_f2_a_stale_policy_update_conflicts_instead_of_overwriting(ctx):
    """Acceptance requirement 19, on the policy surface."""
    first = await _permissive_policy(ctx, updated_by="first")

    conflicting = learning_policy.LearningPolicy(enabled_categories=["procedural"])
    result = await run_learning_policy_update_through_runtime_contract(
        ctx,
        conflicting,
        expected_revision=0,
        updated_by="second",
    )
    assert result.outcome == LEARNING_OUTCOME_REVISION_CONFLICT
    assert result.stored_policy is not None
    assert result.stored_policy.revision == first.revision

    live = await load_learning_policy(ctx)
    assert live.revision == first.revision
    assert live.updated_by == "first", "the stale update must not have landed"


async def test_f3_superseded_policy_revisions_are_preserved(ctx):
    """A shadow decision names a revision; that revision must stay readable."""
    first = await _permissive_policy(ctx, updated_by="first")
    second_policy = learning_policy.LearningPolicy(
        enabled_categories=["procedural"],
        # Left empty on purpose: naming a privacy class puts the row through
        # the consent queue (see test_f7), which is a different property and
        # is pinned there rather than tangled into this one.
        excluded_privacy_classes=[],
    )
    second = await run_learning_policy_update_through_runtime_contract(
        ctx,
        second_policy,
        expected_revision=first.revision,
        updated_by="second",
    )
    assert second.outcome == LEARNING_OUTCOME_POLICY_UPDATED, second.reason

    archived = await ctx.mem.get_memory(
        learning_policy.POLICY_KIND,
        f"{learning_policy.POLICY_KEY}@r{first.revision}",
    )
    assert archived is not None
    assert json.loads(archived["value"])["updated_by"] == "first"


async def test_f4_the_default_policy_is_safe_and_the_mode_is_fixed(ctx):
    """A runtime nobody has configured refuses everything, and says shadow."""
    policy = await load_learning_policy(ctx)
    assert policy.revision == 0
    assert policy.enabled_categories == []
    assert policy.execution_mode == "shadow"
    assert policy.auto_acceptance_enabled is False
    assert policy.min_confidence > candidate_learning.SINGLE_EXPERIENCE_CONFIDENCE


async def test_f5_a_policy_update_is_audited_and_states_the_mode(ctx):
    """An auditor reading only Reflections must see that the mode was shadow."""
    await _permissive_policy(ctx, updated_by="taylor")

    updates = [
        item
        for item in await _learning_reflections(ctx)
        if item.get("action") == "learning_policy_update"
        and item.get("outcome") == LEARNING_OUTCOME_POLICY_UPDATED
    ]
    assert updates
    entry = updates[0]
    assert entry["execution_mode"] == "shadow"
    assert entry["auto_acceptance_enabled"] is False
    assert entry["requested_execution_mode"] == learning_policy.REQUESTED_MODE_AUTO
    assert entry["consolidated"] is False


async def test_f6_an_unparseable_policy_falls_back_to_the_safe_default(ctx):
    """
    A corrupted policy revision must not open anything.

    The unreadable row is left in place for inspection and the conservative
    built-in default is used, rather than the read failing or -- far worse --
    a partially-parsed permissive policy being assembled from it.
    """
    await ctx.mem.upsert_memory(
        learning_policy.POLICY_KIND,
        learning_policy.POLICY_KEY,
        "{not json at all",
        "2026-09-01T00:00:00+00:00",
        summary="corrupted policy",
    )
    policy = await load_learning_policy(ctx)
    assert policy.revision == 0
    assert policy.enabled_categories == []
    assert policy.execution_mode == "shadow"

    row = await ctx.mem.get_memory(learning_policy.POLICY_KIND, learning_policy.POLICY_KEY)
    assert row is not None, "the corrupted revision must be preserved for inspection"


async def test_f6c_an_unreadable_policy_survives_the_next_update(ctx):
    """
    "Left in place for inspection" has to survive an update, not just a read.

    `load_learning_policy()` falls back to the default when the row cannot be
    parsed, and the default's revision is 0 -- so an update with
    `expected_revision=0` passes the conflict check, skips the archive branch
    (there is no revision to archive) and overwrites the corrupted row. The
    docstring said the row was preserved; it was destroyed by the very next
    save.
    """
    await ctx.mem.upsert_memory(
        learning_policy.POLICY_KIND,
        learning_policy.POLICY_KEY,
        "{not json at all",
        "2026-09-01T00:00:00+00:00",
        summary="corrupted policy",
    )

    result = await run_learning_policy_update_through_runtime_contract(
        ctx,
        learning_policy.LearningPolicy(
            enabled_categories=["procedural"],
            excluded_privacy_classes=[],
        ),
        expected_revision=0,
        updated_by=REVIEWER,
    )
    assert result.outcome == LEARNING_OUTCOME_POLICY_UPDATED, result.reason

    kept = await ctx.mem.get_memory(
        learning_policy.POLICY_KIND,
        f"{learning_policy.POLICY_KEY}@unreadable",
    )
    assert kept is not None, "the unreadable revision must be kept aside"
    assert kept["value"] == "{not json at all"

    live = await load_learning_policy(ctx)
    assert live.revision == 1
    assert live.enabled_categories == ["procedural"]


async def test_f6d_a_policy_update_is_refused_when_the_prior_revision_cannot_be_kept(ctx):
    """
    A recorded shadow decision names the revision it ran under.

    If the archive of that revision does not land, the next write destroys it
    and the decision becomes unexplainable. Refuse rather than lose it -- the
    same rule the candidate edit seam follows.
    """
    first = await _permissive_policy(ctx, updated_by="first")
    assert first.revision == 1

    class _RefusingArchive:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def upsert_memory(self, kind, key, value, ts, **kwargs):
            if kind == learning_policy.POLICY_KIND and str(key).endswith("@r1"):
                from bartholomew.kernel.memory_store import StoreResult

                return StoreResult(stored=False, outcome="refused")
            return await self._inner.upsert_memory(kind, key, value, ts, **kwargs)

    real = ctx.mem
    ctx.mem = _RefusingArchive(real)
    try:
        result = await run_learning_policy_update_through_runtime_contract(
            ctx,
            learning_policy.LearningPolicy(
                enabled_categories=[],
                excluded_privacy_classes=[],
            ),
            expected_revision=first.revision,
            updated_by="second",
        )
    finally:
        ctx.mem = real

    assert result.outcome == LEARNING_OUTCOME_NOT_STORED
    assert "could not be kept" in result.reason

    live = await load_learning_policy(ctx)
    assert live.revision == first.revision
    assert live.updated_by == "first"


@pytest.mark.parametrize("corrupt", ["[]", '"a string"', "42", "null"])
async def test_f6b_a_policy_row_that_is_not_an_object_also_falls_back_safely(ctx, corrupt):
    """
    Valid JSON that is not a policy.

    `[].get(...)` raises `AttributeError`, which is not a `ValueError` -- so a
    row containing a JSON list would have escaped the parse guard and taken
    the whole control centre down. Checked rather than caught.
    """
    await ctx.mem.upsert_memory(
        learning_policy.POLICY_KIND,
        learning_policy.POLICY_KEY,
        corrupt,
        "2026-09-01T00:00:00+00:00",
        summary="not a policy",
    )
    policy = await load_learning_policy(ctx)
    assert policy.revision == 0
    assert policy.execution_mode == "shadow"
    assert policy.enabled_categories == []


async def test_f7_a_policy_naming_privacy_classes_is_held_for_consent_truthfully(ctx):
    """
    The privacy gate is not weakened for the settings screen.

    A policy that excludes `user.health` material contains the word "health",
    and `privacy_guard.is_sensitive()` scans stored values in full. So the row
    is held in the existing pending-consent inbox exactly like any other write
    that mentions it, the previous policy stays in force, and the result says
    so rather than reporting a save that did not happen.

    This is a false positive in the sense that no health data is in the row.
    It is kept because the alternatives -- exempting this kind from the guard,
    or encoding the vocabulary so it no longer reads as itself -- both trade a
    governance default for the convenience of a form.
    """
    policy = learning_policy.LearningPolicy(
        enabled_categories=["procedural"],
        excluded_privacy_classes=["user.health"],
    )
    result = await run_learning_policy_update_through_runtime_contract(
        ctx,
        policy,
        expected_revision=0,
        updated_by="taylor",
    )

    assert result.outcome == LEARNING_OUTCOME_QUEUED_FOR_CONSENT
    assert result.queued_for_consent is True
    assert result.updated is False
    assert "Pending Memory Consent" in result.reason

    # The previous policy is still the live one.
    live = await load_learning_policy(ctx)
    assert live.revision == 0

    # And it really is in the inbox the UI already shows, not lost.
    pending = await ctx.mem.list_pending_sensitive_writes(limit=50)
    assert any(item["kind"] == learning_policy.POLICY_KIND for item in pending)


# ===========================================================================
# G. Correction, supersession, revocation
# ===========================================================================


async def _accept_one(ctx):
    lesson = await _propose(ctx, _run_the_experience(ctx))
    await grant_learning_acceptance_approval(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        approver=APPROVER,
    )
    result = await run_candidate_lesson_through_runtime_contract(
        ctx,
        LEARNING_ACTION_ACCEPT,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        reviewer=REVIEWER,
    )
    assert result.consolidated is True
    return lesson, result.lesson.consolidated_kind, result.lesson.consolidated_key


async def test_g1_accepted_knowledge_can_be_corrected_and_supersedes(ctx):
    """Acceptance requirement 20, correction half."""
    _, kind, key = await _accept_one(ctx)

    before = json.loads((await ctx.mem.get_memory(kind, key))["value"])
    result = await run_competency_correction_through_runtime_contract(
        ctx,
        kind=kind,
        key=key,
        corrected_by=REVIEWER,
        expected_revision=before["revision"],
        updates={"rule": "Always ring the warranty line first, before any engineer"},
    )
    assert result.stored_count == 1, result.errors
    outcome = result.outcomes[0]
    assert outcome.superseded_revision == before["revision"]
    assert outcome.revision == before["revision"] + 1

    after = json.loads((await ctx.mem.get_memory(kind, key))["value"])
    assert after["rule"].startswith("Always ring the warranty line first")
    assert after["provenance"]["source_type"] == "correction"


async def test_g2_a_stale_correction_conflicts_instead_of_overwriting(ctx):
    """Acceptance requirement 19, on accepted knowledge."""
    _, kind, key = await _accept_one(ctx)
    stale = json.loads((await ctx.mem.get_memory(kind, key))["value"])["revision"]

    first = await run_competency_correction_through_runtime_contract(
        ctx,
        kind=kind,
        key=key,
        corrected_by="first",
        expected_revision=stale,
        updates={"rule": "First correction"},
    )
    assert first.stored_count == 1

    second = await run_competency_correction_through_runtime_contract(
        ctx,
        kind=kind,
        key=key,
        corrected_by="second",
        expected_revision=stale,
        updates={"rule": "Second correction, written from a stale screen"},
    )
    assert second.stored_count == 0
    assert any("changed since you opened it" in err for err in second.errors)

    live = json.loads((await ctx.mem.get_memory(kind, key))["value"])
    assert live["rule"] == "First correction"


async def test_g3_revocation_removes_retrieval_eligibility_and_keeps_the_audit(ctx):
    """
    Acceptance requirement 20, revoke/forget half.

    The knowledge goes; the record that it was proposed, approved and
    accepted stays. "Did I ever agree to this?" must remain answerable.
    """
    lesson, kind, key = await _accept_one(ctx)

    result = await run_competency_revocation_through_runtime_contract(
        ctx,
        kind=kind,
        key=key,
        revoked_by=REVIEWER,
        reason="The warranty expired, so this no longer applies",
    )
    assert result.outcome == LEARNING_OUTCOME_REVOKED
    assert result.removed is True

    # Gone from the retrievable substrate.
    assert await ctx.mem.get_memory(kind, key) is None
    assert not await _rows_of_kind(ctx, kind)

    # Every audit surface survives.
    assert await ctx.mem.get_memory(candidate_learning.KIND, lesson.key()) is not None
    assert await ctx.mem.get_memory(learning_authorization.KIND, lesson.key()) is not None
    revocations = [
        item
        for item in await _learning_reflections(ctx)
        if item.get("action") == "learning_competency_revoke"
    ]
    assert revocations, "revocation must be recorded through the Reflection authority"
    assert revocations[0]["revoked_by"] == REVIEWER
    assert revocations[0]["key"] == key


async def test_g4_only_accepted_knowledge_can_be_revoked(ctx):
    """
    A candidate is rejected, not revoked.

    Routing one through here would delete the audit row that makes the
    rejection legible, so the seam refuses by kind.
    """
    lesson = await _propose(ctx, _run_the_experience(ctx))
    result = await run_competency_revocation_through_runtime_contract(
        ctx,
        kind=candidate_learning.KIND,
        key=lesson.key(),
        revoked_by=REVIEWER,
    )
    assert result.outcome == LEARNING_OUTCOME_INVALID
    assert result.removed is False
    assert await ctx.mem.get_memory(candidate_learning.KIND, lesson.key()) is not None

    approval_result = await run_competency_revocation_through_runtime_contract(
        ctx,
        kind=learning_authorization.KIND,
        key=lesson.key(),
        revoked_by=REVIEWER,
    )
    assert approval_result.outcome == LEARNING_OUTCOME_INVALID


# ===========================================================================
# H. Isolation and compatibility
# ===========================================================================


async def test_h1_learning_state_is_scoped_to_its_own_runtime(ctx, tmp_path):
    """
    Acceptance requirement 1.

    Isolation here is the database file, which is the model
    `bartholomew.platform.runtime_registry` documents: one personal
    Bartholomew is one isolated runtime with its own database. A second
    runtime sees none of the first's candidates, approvals, evaluations or
    policy -- and there is no argument on any of these seams through which
    one could reach the other.
    """
    lesson = await _propose(ctx, _run_the_experience(ctx))
    await grant_learning_acceptance_approval(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        approver=APPROVER,
    )
    await _permissive_policy(ctx)
    await run_shadow_learning_evaluation_through_runtime_contract(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
    )

    other_db = str(tmp_path / "other_tenant.db")
    other_mem = MemoryStore(other_db)
    await other_mem.init()
    os_mod.ensure_schema(other_db)
    other = _Ctx(other_mem, ObjectiveStore(other_db), _shipped_identity_context())
    try:
        # Tenant B gets learning state of its own, so this is not a test that a
        # freshly created empty database is empty -- which would pass against
        # any implementation, correct or broken. Both runtimes hold records;
        # the question is whether either can see the other's.
        b_lesson = await _propose(
            other,
            _run_the_experience(other, title="Repair the tenant B fence"),
        )
        await grant_learning_acceptance_approval(
            other,
            competency_id=COMPETENCY_ID,
            slug=b_lesson.slug,
            approver="tenant-b",
        )
        await run_shadow_learning_evaluation_through_runtime_contract(
            other,
            competency_id=COMPETENCY_ID,
            slug=b_lesson.slug,
        )
        assert await other.mem.list_memories_by_kind(
            [candidate_learning.KIND],
            limit=100,
        ), "tenant B must actually hold records for this test to mean anything"

        # Keys are per-tenant, so they collide by *name*: each runtime numbers
        # its own objectives from 1, so both candidates land at
        # "estate_management.lesson_from_objective_1". That is correct, and it
        # is why isolation has to be checked on content -- comparing keys would
        # fail against two perfectly isolated runtimes.
        a_candidates = str(await _rows_of_kind(ctx, candidate_learning.KIND))
        b_candidates = str(
            await other.mem.list_memories_by_kind([candidate_learning.KIND], limit=100),
        )
        assert "Get the boiler serviced" in a_candidates
        assert "tenant B fence" not in a_candidates
        assert "tenant B fence" in b_candidates
        assert "Get the boiler serviced" not in b_candidates

        # Each runtime holds exactly its own record of each kind, not both.
        for kind in (
            candidate_learning.KIND,
            learning_authorization.KIND,
            learning_policy.EVALUATION_KIND,
        ):
            assert (
                len(await other.mem.list_memories_by_kind([kind], limit=100)) == 1
            ), f"tenant B should hold exactly its own {kind} record"

        # A's approver never appears in B's records, and B's never in A's.
        a_approvals = str(await _rows_of_kind(ctx, learning_authorization.KIND))
        b_approvals = str(
            await other.mem.list_memories_by_kind([learning_authorization.KIND], limit=100),
        )
        assert APPROVER in a_approvals and "tenant-b" not in a_approvals
        assert "tenant-b" in b_approvals and APPROVER not in b_approvals

        # A configured a policy; B's is still the untouched default, and B has
        # no policy row of its own to confuse with A's.
        assert (await load_learning_policy(ctx)).revision > 0
        assert (await load_learning_policy(other)).revision == 0
        assert not await other.mem.list_memories_by_kind(
            [learning_policy.POLICY_KIND],
            limit=100,
        )

        # Evaluating the colliding key in B previews B's *own* lesson, never
        # A's. The keys are identical strings; the records are not.
        collided = await run_shadow_learning_evaluation_through_runtime_contract(
            other,
            competency_id=COMPETENCY_ID,
            slug=lesson.slug,
        )
        assert collided.outcome == LEARNING_OUTCOME_EVALUATED
        assert "tenant B fence" in collided.lesson.inferred_rule
        assert "Get the boiler serviced" not in collided.lesson.inferred_rule

        # And a key that exists in neither is simply not found.
        missing = await run_shadow_learning_evaluation_through_runtime_contract(
            other,
            competency_id=COMPETENCY_ID,
            slug="lesson_from_objective_9999",
        )
        assert missing.outcome == "not_found"
    finally:
        await other_mem.close()


async def test_h2_never_store_material_never_becomes_a_candidate_record(ctx):
    """
    Acceptance requirement 21, at the write boundary.

    `never_store` is enforced by `upsert_memory()` before anything reaches a
    row, so material the rules refuse has no record for the control centre to
    show -- and therefore none to export.
    """
    refused = await ctx.mem.upsert_memory(
        candidate_learning.KIND,
        f"{COMPETENCY_ID}.illegal",
        json.dumps({"inferred_rule": "how to obtain illegal content"}),
        "2026-09-01T00:00:00+00:00",
        summary="Candidate lesson",
    )
    assert refused.stored is False
    assert refused.outcome == "refused"
    assert await ctx.mem.get_memory(candidate_learning.KIND, f"{COMPETENCY_ID}.illegal") is None


async def test_h3_existing_retrieval_and_competency_reasoning_still_work(ctx):
    """
    Acceptance requirement 22.

    The consolidated record is still an ordinary `competency_heuristic` that
    S5.3's selection layer accepts, and the new kinds are still invisible to
    the retrieval filter.
    """
    from bartholomew.kernel.competency_reasoning import (
        CompetencyCandidate,
        query_terms,
        select_relevant,
    )
    from bartholomew.kernel.runtime_contract import _parse_competency_row

    _, kind, key = await _accept_one(ctx)
    row = await ctx.mem.get_memory(kind, key)
    record = _parse_competency_row(row)
    assert record is not None, "the consolidated record must parse as an S5.1 record"

    selected = select_relevant(
        [CompetencyCandidate(kind=kind, key=key, score=0.9, record=record)],
        request_terms=query_terms("boiler warranty engineer"),
    )
    assert selected.applied, "an accepted lesson must remain selectable by the reasoning seam"
    assert selected.applied[0].key == key

    for new_kind in (
        learning_policy.POLICY_KIND,
        learning_policy.EVALUATION_KIND,
        learning_policy.CANDIDATE_REVISION_KIND,
    ):
        assert new_kind not in COMPETENCY_KINDS


async def test_h4_the_new_kinds_are_registered_as_structural_schema(ctx):
    """
    Their schema key names are structure, not content.

    Without registration a policy row would trip the privacy guard on its own
    field names (`excluded_privacy_classes` contains `private`), sending every
    configuration change to the consent queue for no reason a user could
    understand. Values are still scanned in full.
    """
    from bartholomew.kernel.memory.privacy_guard import _SCHEMA_KEYS_BY_KIND

    assert learning_policy.POLICY_KIND in _SCHEMA_KEYS_BY_KIND
    assert learning_policy.EVALUATION_KIND in _SCHEMA_KEYS_BY_KIND
    assert "excluded_privacy_classes" in _SCHEMA_KEYS_BY_KIND[learning_policy.POLICY_KIND]


async def test_h5_every_package_d_action_kind_is_on_the_shipped_allowlist(ctx):
    """
    The blocking-discovery check S1.3, S1.4 and Session D each had to make.

    Without an entry, `evaluate_tool_policy()` denies the seam outright
    against the identity this repository actually ships.
    """
    from bartholomew.kernel import policy_engine

    identity = _shipped_identity_context()
    for kind in (
        "learning_shadow_evaluate",
        "learning_policy_update",
        "learning_candidate_edit",
        "learning_competency_revoke",
    ):
        decision = policy_engine.evaluate_tool_policy(identity, kind)
        assert decision.allowed, f"{kind} is not reachable on the shipped identity"

    # And the one that must stay off it.
    assert not policy_engine.evaluate_tool_policy(identity, "learning_accept").allowed


# ===========================================================================
# I. The audit trail, and Governance over the new seams
# ===========================================================================


async def test_i1_every_preview_records_that_nothing_was_consolidated(ctx):
    """
    The audit claim, checked against the Reflection trail rather than the
    result object.

    An operator reading only Reflections must be able to see that a preview
    ran, what it decided, which policy revision it ran under, and that nothing
    was consolidated -- without having to know that shadow evaluation is
    incapable of consolidating.
    """
    lesson = await _propose(ctx, _run_the_experience(ctx))
    await _permissive_policy(ctx)
    await run_shadow_learning_evaluation_through_runtime_contract(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        requested_by="taylor",
    )

    previews = [
        item
        for item in await _learning_reflections(ctx)
        if item.get("action") == "learning_shadow_evaluate"
    ]
    assert previews, "a preview must be recorded through the Reflection authority"
    entry = previews[-1]
    assert entry["outcome"] == LEARNING_OUTCOME_EVALUATED
    assert entry["shadow_decision"] == learning_policy.DECISION_WOULD_ACCEPT
    assert entry["execution_mode"] == learning_policy.SHIPPED_EXECUTION_MODE
    assert entry["authorizes_acceptance"] is False
    assert entry["consolidated"] is False
    assert entry["requested_by"] == "taylor"
    assert entry["policy_revision"] > 0


async def test_i2_the_parking_brake_refuses_every_new_mutating_seam(ctx):
    """
    Governance primacy, restated for the operations Package D adds.

    Editing a candidate, changing the policy and revoking knowledge are all
    mutations of governed state, so a brake engaged on the `training` scope
    refuses each of them -- and previewing, which writes an inert record,
    is refused with it. "Inspect, but do not mutate" is not weakened by this
    package having more things to mutate.
    """
    from bartholomew.kernel.runtime_contract import LEARNING_OUTCOME_BRAKE_DENIED
    from bartholomew.orchestrator.safety.governance_store import GovernanceStore

    lesson, kind, key = await _accept_one(ctx)
    fresh = await _propose(ctx, _run_the_experience(ctx, title="A second objective"))

    store = GovernanceStore(ctx.mem.db_path)
    store.engage("training", reason="test", actor="test")
    ctx.governance_store = store
    try:
        edit = await run_candidate_edit_through_runtime_contract(
            ctx,
            competency_id=COMPETENCY_ID,
            slug=fresh.slug,
            editor=EDITOR,
            expected_revision=fresh.revision,
            inferred_rule="Edited under a brake",
        )
        assert edit.governance_allowed is False
        assert edit.outcome == LEARNING_OUTCOME_BRAKE_DENIED

        preview = await run_shadow_learning_evaluation_through_runtime_contract(
            ctx,
            competency_id=COMPETENCY_ID,
            slug=fresh.slug,
        )
        assert preview.governance_allowed is False
        assert preview.outcome == LEARNING_OUTCOME_BRAKE_DENIED
        assert preview.decision is None

        policy = await run_learning_policy_update_through_runtime_contract(
            ctx,
            learning_policy.LearningPolicy(enabled_categories=["procedural"]),
            expected_revision=0,
            updated_by=REVIEWER,
        )
        assert policy.outcome == LEARNING_OUTCOME_BRAKE_DENIED

        revoke = await run_competency_revocation_through_runtime_contract(
            ctx,
            kind=kind,
            key=key,
            revoked_by=REVIEWER,
        )
        assert revoke.governance_allowed is False
        assert revoke.outcome == LEARNING_OUTCOME_BRAKE_DENIED
        assert revoke.removed is False
    finally:
        store.disengage(reason="test", actor="test")
        ctx.governance_store = None

    # Nothing landed while the brake was engaged.
    still_stored = await _stored_candidate(ctx, fresh)
    assert still_stored.inferred_rule == fresh.inferred_rule
    assert not await _rows_of_kind(ctx, learning_policy.EVALUATION_KIND)
    assert (await load_learning_policy(ctx)).revision == 0
    assert await ctx.mem.get_memory(kind, key) is not None

    # And the same operations work once it is released -- the brake refused
    # the actions, it did not break them.
    released = await run_candidate_edit_through_runtime_contract(
        ctx,
        competency_id=COMPETENCY_ID,
        slug=fresh.slug,
        editor=EDITOR,
        expected_revision=fresh.revision,
        inferred_rule="Edited after release",
    )
    assert released.outcome == LEARNING_OUTCOME_EDITED
    assert lesson.key() != fresh.key()
