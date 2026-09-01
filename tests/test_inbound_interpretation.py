"""Session B acceptance: a captured inbound event becoming relevant evidence.

The progression this suite exists to prove, end to end and with real
components throughout -- a real SQLite database, the real capture seam, the
real `ObjectiveStore`, the real governed objective seam:

    captured inbound event -> downstream interpretation -> relevance
    assessment -> existing live objective match -> candidate evidence ->
    objective history -> Executive reasoning can read it back

and, just as importantly, the four things that must *not* happen: an
unrelated event mutating an objective, an ambiguous one becoming fact, an
event reaching a capability, and a redelivery producing a second piece of
evidence.

Nothing is mocked. The point of the slice is what lands on disk and what
Governance says about it, and neither can be proven against a stand-in.
"""

from __future__ import annotations

import sqlite3
from datetime import timezone

import pytest

from bartholomew.kernel import inbound_interpretation as interp
from bartholomew.kernel import inbound_store, objective_intents, objective_store
from bartholomew.kernel.inbound_interpretation import (
    RELEVANCE_IRRELEVANT,
    RELEVANCE_RELEVANT,
    RELEVANCE_UNCERTAIN,
    interpret,
    interpret_captured_event,
)
from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.objective_store import ObjectiveStore
from bartholomew.kernel.runtime_contract import run_inbound_through_runtime_contract
from bartholomew.orchestrator.safety import governance_store as gs
from bartholomew.orchestrator.safety.governance_store import GovernanceStore
from identity_interpreter.identity_context import IdentityContext

#: Identity.yaml's real allowlist, unchanged by this slice: interpretation
#: writes through `objective_record`, which the Objective Continuity slice
#: already allowlisted. No new capability was granted to reach evidence.
ALLOW_CONTEXT = IdentityContext(
    tool_use_default_allowed=False,
    tool_use_allowlist=["objective_record", "inbound_capture"],
)


class _Ctx:
    """Exactly the attributes the objective seam reads. Duck-typed, following
    the precedent `tests/test_objective_continuity.py` set."""

    def __init__(self, mem, objectives, governance=None, identity=ALLOW_CONTEXT):
        self.mem = mem
        self.objective_store = objectives
        self.governance_store = governance
        self.identity_context = identity
        self.blocking_executor = None
        self.tz = timezone.utc
        self.cfg = {}


@pytest.fixture
async def mem(tmp_path):
    store = MemoryStore(str(tmp_path / "inbound_interpretation.db"))
    await store.init()
    gs.ensure_schema(store.db_path)
    inbound_store.ensure_schema(store.db_path)
    yield store
    await store.close()


@pytest.fixture
def objectives(mem):
    return ObjectiveStore(mem.db_path)


@pytest.fixture
def ctx(mem, objectives):
    return _Ctx(mem, objectives, governance=GovernanceStore(mem.db_path))


@pytest.fixture
def roof(objectives):
    """The live objective the acceptance scenario is about."""
    return objectives.open(
        title="Get the roof repaired",
        outcome_statement="The roof is repaired and no longer leaking",
    )


ROOFER_PAYLOAD = {
    "subject": "Roof repair",
    "body": "Roofer confirmed attendance Tuesday.",
}


async def _capture(db_path, *, event_id="evt-roofer-1", payload=None, event_type="msg.received"):
    """Capture through the real, unmodified inbound seam."""
    return await run_inbound_through_runtime_contract(
        db_path=db_path,
        source_id="src-roofer",
        event_id=event_id,
        event_type=event_type,
        payload=ROOFER_PAYLOAD if payload is None else payload,
        verified_by="test-resolver",
        occurred_at="2026-08-30T09:00:00Z",
    )


# ------------------------------------------------------------ acceptance


@pytest.mark.asyncio
async def test_the_acceptance_scenario(ctx, mem, objectives, roof):
    """The whole progression, in one test.

    1. captured through the existing inbound path;
    2. provenance survives;
    3. interpretation happens downstream of capture;
    4. the existing live objective is matched;
    5. the information appears as new evidence in that objective's history;
    6. subsequent Executive reasoning can read it back.
    """
    # 1. Capture, through the seam Session D shipped, unchanged.
    captured = await _capture(mem.db_path)
    assert captured.captured is True
    assert captured.duplicate is False

    # 2. Provenance is on the durable row before anything interprets it.
    row = captured.stored
    assert row.source_id == "src-roofer"
    assert row.verified_by == "test-resolver"
    assert row.occurred_at == "2026-08-30T09:00:00Z"
    assert row.payload_sha256

    # Capture did not, by itself, touch the objective. Interpretation is a
    # separate, later step and this asserts that it really is separate.
    assert objectives.evidence_events(roof.id) == []

    # 3 + 4 + 5. Interpretation, downstream, against the live objective set.
    payload = inbound_store.get_event_payload(mem.db_path, row.source_id, row.event_id)
    result = await interpret_captured_event(ctx, stored=row, payload=payload)

    assert result.interpretation.relevance == RELEVANCE_RELEVANT
    assert result.interpretation.objective_id == roof.id
    assert result.recorded is True
    assert result.governance_allowed is True

    evidence = objectives.evidence_events(roof.id)
    assert len(evidence) == 1
    written = evidence[0]
    assert written.event_kind == objective_store.EVENT_FACT
    assert "Roofer confirmed attendance Tuesday." in written.summary

    # The capture's provenance survives onto the objective event intact --
    # including the digest, so the history points back at exactly the content
    # that was accepted.
    assert written.provenance["source_kind"] == interp.SOURCE_KIND_INBOUND
    assert written.provenance["source_id"] == "src-roofer"
    assert written.provenance["event_id"] == "evt-roofer-1"
    assert written.provenance["payload_sha256"] == row.payload_sha256
    assert written.provenance["verified_by"] == "test-resolver"
    assert written.provenance["evidence"] is True
    assert written.actor == "inbound:src-roofer"

    # 6. Subsequent Executive reasoning reaches it through the ordinary
    # continuity read -- no special inbound-aware path required.
    rendered = objective_intents.render_continuity(
        objectives.get(roof.id),
        objectives.evidence_events(roof.id),
    )
    assert "Roofer confirmed attendance Tuesday." in rendered


# ------------------------------------------------- what must NOT happen


@pytest.mark.asyncio
async def test_unrelated_event_does_not_mutate_the_objective(ctx, mem, objectives, roof):
    captured = await _capture(
        mem.db_path,
        event_id="evt-unrelated",
        payload={"body": "Your parcel was delivered to the front door."},
    )
    result = await interpret_captured_event(
        ctx,
        stored=captured.stored,
        payload={"body": "Your parcel was delivered to the front door."},
    )

    assert result.interpretation.relevance == RELEVANCE_IRRELEVANT
    assert result.interpretation.reason == "no_matching_live_objective"
    assert result.recorded is False
    assert objectives.events(roof.id, kinds={objective_store.EVENT_FACT}) == []
    # The objective itself is untouched, not merely un-evidenced.
    assert objectives.get(roof.id).status == objective_store.STATUS_ACTIVE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("The roof work might happen Tuesday, we think.", "hedged_or_speculative"),
        ("Is the roof access clear on Tuesday?", "question_not_assertion"),
        ("Please book the roof scaffolding for Tuesday.", "external_directive_not_evidence"),
    ],
)
async def test_ambiguous_events_never_become_fact(ctx, mem, objectives, roof, body, reason):
    """Hedged, interrogative and imperative content all stop short of fact.

    Each one matches the objective by subject and would be plausible-looking
    evidence. None of them says that anything happened."""
    payload = {"subject": "Roof repair", "body": body}
    captured = await _capture(mem.db_path, event_id=f"evt-{reason}", payload=payload)
    result = await interpret_captured_event(ctx, stored=captured.stored, payload=payload)

    assert result.interpretation.relevance == RELEVANCE_UNCERTAIN
    assert result.interpretation.reason == reason
    assert result.recorded is False
    assert objectives.evidence_events(roof.id) == []
    # Not recorded as a proposal either: an uncertain external assertion is
    # not a step Bartholomew is considering, and parking it in the history
    # under any kind is still putting it in the history.
    assert objectives.events(roof.id, kinds={objective_store.EVENT_PROPOSAL}) == []


@pytest.mark.asyncio
async def test_two_plausible_objectives_produce_no_attachment(ctx, mem, objectives):
    """Ambiguity of *target* is refused just as ambiguity of content is."""
    a = objectives.open(title="Get the roof repaired")
    b = objectives.open(title="Insure the roof against storm damage")
    payload = {"body": "Roof inspection completed Tuesday."}
    captured = await _capture(mem.db_path, event_id="evt-ambiguous-target", payload=payload)
    result = await interpret_captured_event(ctx, stored=captured.stored, payload=payload)

    assert result.interpretation.relevance == RELEVANCE_UNCERTAIN
    assert result.interpretation.reason == "ambiguous_objective_match"
    assert result.interpretation.objective_id is None
    assert set(result.interpretation.candidate_objective_ids) == {a.id, b.id}
    assert objectives.evidence_events(a.id) == []
    assert objectives.evidence_events(b.id) == []


@pytest.mark.asyncio
async def test_an_event_cannot_cause_skill_execution_or_new_objectives(ctx, mem, objectives, roof):
    """Noticing is not acting.

    The strongest available structural assertion: interpreting an event that
    reads as an instruction, with a skill registry deliberately absent from
    the context, produces no error -- because nothing on this path ever looks
    for one. No objective is created, and none changes lifecycle state."""
    assert not hasattr(ctx, "skill_registry")

    before = {o.id for o in objectives.list()}
    payload = {"body": "Please email the roof surveyor and pay the deposit today."}
    captured = await _capture(mem.db_path, event_id="evt-instruction", payload=payload)
    result = await interpret_captured_event(ctx, stored=captured.stored, payload=payload)

    assert result.recorded is False
    assert result.interpretation.relevance == RELEVANCE_UNCERTAIN
    assert {o.id for o in objectives.list()} == before
    assert objectives.get(roof.id).status == objective_store.STATUS_ACTIVE
    # No nudge was queued and no memory written by this path.
    with sqlite3.connect(mem.db_path) as conn:
        for table in ("nudges", "memories"):
            try:
                assert (
                    conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
                )  # noqa: S608
            except sqlite3.OperationalError:
                pass


@pytest.mark.asyncio
async def test_duplicate_capture_produces_no_second_evidence(ctx, mem, objectives, roof):
    """Canonical idempotency, honoured at both layers."""
    first = await _capture(mem.db_path)
    payload = inbound_store.get_event_payload(mem.db_path, "src-roofer", "evt-roofer-1")
    recorded = await interpret_captured_event(ctx, stored=first.stored, payload=payload)
    assert recorded.recorded is True

    # The sender retries. Capture reports the existing row.
    again = await _capture(mem.db_path)
    assert again.duplicate is True
    dup = await interpret_captured_event(ctx, stored=again.stored, payload=payload)
    assert dup.recorded is False
    assert dup.already_recorded is True

    # And re-running interpretation over the original (non-duplicate) row is
    # equally safe -- this is the case a caller reaches by accident.
    replay = await interpret_captured_event(ctx, stored=first.stored, payload=payload)
    assert replay.recorded is False
    assert replay.already_recorded is True
    assert replay.outcome == "already_recorded"

    assert len(objectives.evidence_events(roof.id)) == 1


@pytest.mark.asyncio
async def test_parking_brake_refuses_the_attachment(ctx, mem, objectives, roof):
    """Governance stays authoritative over the downstream write.

    Interpretation adds no gate of its own and, critically, bypasses none:
    the write goes through the existing objective seam, so an engaged brake
    refuses it exactly as it refuses every other objective mutation. Reading
    the objectives is still allowed -- inspection is what a halt must not
    hide."""
    captured = await _capture(mem.db_path)
    payload = inbound_store.get_event_payload(mem.db_path, "src-roofer", "evt-roofer-1")

    ctx.governance_store.engage("skills", reason="test", actor="test")
    try:
        result = await interpret_captured_event(ctx, stored=captured.stored, payload=payload)
    finally:
        ctx.governance_store.disengage(reason="test", actor="test")

    assert result.interpretation.relevance == RELEVANCE_RELEVANT
    assert result.governance_allowed is False
    assert result.recorded is False
    assert objectives.evidence_events(roof.id) == []

    # Once released, the same event still attaches: the brake refused, it did
    # not consume.
    after = await interpret_captured_event(ctx, stored=captured.stored, payload=payload)
    assert after.recorded is True


@pytest.mark.asyncio
async def test_identity_policy_denial_refuses_the_attachment(mem, objectives, roof):
    deny = IdentityContext(tool_use_default_allowed=False, tool_use_allowlist=[])
    ctx = _Ctx(mem, objectives, governance=GovernanceStore(mem.db_path), identity=deny)
    captured = await _capture(mem.db_path)
    payload = inbound_store.get_event_payload(mem.db_path, "src-roofer", "evt-roofer-1")

    result = await interpret_captured_event(ctx, stored=captured.stored, payload=payload)

    assert result.governance_allowed is False
    assert result.recorded is False
    assert objectives.evidence_events(roof.id) == []


@pytest.mark.asyncio
async def test_a_terminal_objective_is_out_of_reach(ctx, mem, objectives):
    """A completed objective cannot be resurrected by an inbound event.

    `list_live()` is the only read, so a terminal objective is structurally
    invisible here rather than filtered out somewhere a caller could forget."""
    done = objectives.open(title="Get the roof repaired")
    objectives.complete(done.id, resolution=objective_store.RESOLUTION_ACHIEVED)

    captured = await _capture(mem.db_path)
    payload = inbound_store.get_event_payload(mem.db_path, "src-roofer", "evt-roofer-1")
    result = await interpret_captured_event(ctx, stored=captured.stored, payload=payload)

    assert result.interpretation.relevance == RELEVANCE_IRRELEVANT
    assert result.recorded is False
    assert objectives.evidence_events(done.id) == []


# ---------------------------------------------------- the pure half alone


class TestPureInterpretation:
    """`interpret()` has no I/O, so its behaviour is assertable directly."""

    class _Obj:
        def __init__(self, id_, title):
            self.id = id_
            self.title = title

    def test_event_type_is_never_matched_on(self):
        """A sender cannot steer the match by naming its event type.

        The domain-blind invariant, from the other direction: `event_type` is
        an opaque captured property and is not part of the corpus."""
        objectives = [self._Obj(1, "Get the roof repaired")]
        # The payload says nothing about a roof; only an event_type would.
        assert (
            interpret({"body": "Nothing to report."}, objectives).relevance == RELEVANCE_IRRELEVANT
        )

    def test_text_is_found_wherever_it_sits(self):
        """No key is privileged, so no provider's field naming is load-bearing."""
        objectives = [self._Obj(1, "Get the roof repaired")]
        nested = {"data": {"items": [{"whatever": "Roof work completed Tuesday."}]}}
        result = interpret(nested, objectives)
        assert result.relevance == RELEVANCE_RELEVANT
        assert result.summary == "Roof work completed Tuesday."

    def test_empty_payload_is_irrelevant_not_uncertain(self):
        objectives = [self._Obj(1, "Get the roof repaired")]
        result = interpret({"count": 3, "ok": True}, objectives)
        assert result.relevance == RELEVANCE_IRRELEVANT
        assert result.reason == "no_interpretable_content"

    def test_no_live_objectives_means_irrelevant(self):
        result = interpret({"body": "Roof work completed."}, [])
        assert result.relevance == RELEVANCE_IRRELEVANT
        assert result.objective_id is None

    def test_summary_is_bounded(self):
        objectives = [self._Obj(1, "Get the roof repaired")]
        long = "Roof " + ("x" * 5000)
        result = interpret({"body": long}, objectives)
        assert len(result.summary) <= interp.MAX_SUMMARY_CHARS
