"""One governed processing pass, end to end (Package A).

Real SQLite, the real capture seam, the real `ObjectiveStore`, the real
`GovernanceStore`, the real interpretation seam and the real governed
objective seam. Nothing is mocked, because every claim here is about what
lands on disk and what Governance said about it, and neither survives a
stand-in.

`process_batch()` is called directly, exactly as the scheduler drive calls
it. `tests/test_event_backbone_drive.py` proves the scheduler is what calls
it in a running system; this file proves what happens when it does.
"""

from __future__ import annotations

import time
from datetime import timezone

import pytest

from bartholomew.kernel import inbound_interpretation as interp
from bartholomew.kernel import inbound_store, objective_store
from bartholomew.kernel.event_processing import processor, store
from bartholomew.kernel.event_processing.adapters import (
    OBSERVATION_NOTE,
    OBSERVATION_STATUS,
)
from bartholomew.kernel.event_processing.config import EventProcessingSettings
from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.objective_store import ObjectiveStore
from bartholomew.kernel.runtime_contract import run_inbound_through_runtime_contract
from bartholomew.orchestrator.safety import governance_store as gs
from bartholomew.orchestrator.safety.governance_store import GovernanceStore
from identity_interpreter.identity_context import IdentityContext

#: Identity.yaml's real entries for this path. `objective_record` is the one
#: the interpretation seam already needed; the two `inbound_event_*` entries
#: are Package A's.
ALLOW_CONTEXT = IdentityContext(
    tool_use_default_allowed=False,
    tool_use_allowlist=[
        "objective_record",
        "inbound_capture",
        "inbound_event_process",
        "inbound_event_processing",
    ],
)

SETTINGS = EventProcessingSettings(
    enabled=True,
    batch_limit=5,
    sweep_limit=100,
    lease_seconds=60,
    max_attempts=3,
    backlog_max=1000,
    deadline_seconds=30.0,
)


class _Ctx:
    """Exactly the attributes the seams read, following the precedent
    `tests/test_inbound_interpretation.py` set."""

    def __init__(self, mem, objectives, governance, identity=ALLOW_CONTEXT):
        self.mem = mem
        self.objective_store = objectives
        self.governance_store = governance
        self.identity_context = identity
        self.blocking_executor = None
        self.tz = timezone.utc
        self.cfg = {}


@pytest.fixture
async def mem(tmp_path):
    store_ = MemoryStore(str(tmp_path / "processing.db"))
    await store_.init()
    gs.ensure_schema(store_.db_path)
    inbound_store.ensure_schema(store_.db_path)
    objective_store.ensure_schema(store_.db_path)
    store.ensure_schema(store_.db_path)
    yield store_
    await store_.close()


@pytest.fixture
def objectives(mem):
    return ObjectiveStore(mem.db_path)


@pytest.fixture
def governance(mem):
    return GovernanceStore(mem.db_path)


@pytest.fixture
def ctx(mem, objectives, governance):
    return _Ctx(mem, objectives, governance)


@pytest.fixture
def roof(objectives):
    return objectives.open(
        title="Get the roof repaired",
        outcome_statement="The roof is repaired and no longer leaking",
    )


async def capture(
    db_path,
    *,
    event_id,
    payload,
    event_type=OBSERVATION_NOTE,
    source_id="src-roofer",
    runtime_id=None,
):
    """Capture through the real, unmodified inbound seam."""
    return await run_inbound_through_runtime_contract(
        db_path=db_path,
        source_id=source_id,
        event_id=event_id,
        event_type=event_type,
        payload=payload,
        verified_by="test-resolver",
        occurred_at="2026-08-30T09:00:00Z",
        runtime_id=runtime_id,
    )


async def run(ctx, *, runtime_id=None, settings=SETTINGS):
    return await processor.process_batch(
        ctx,
        settings=settings,
        runtime_id=runtime_id,
        resolve_runtime=False,
    )


ROOFER = {"subject": "Roof repair", "body": "Roofer confirmed attendance Tuesday."}


# -------------------------------------------- relevant: attaches exactly once


@pytest.mark.asyncio
async def test_a_relevant_event_attaches_once_with_complete_provenance(ctx, mem, objectives, roof):
    captured = await capture(mem.db_path, event_id="evt-1", payload=ROOFER)
    assert captured.captured is True
    # Capture alone changes nothing about the objective. The backbone is what
    # closes that gap, and this asserts the gap was really there first.
    assert objectives.evidence_events(roof.id) == []

    outcome = await run(ctx)
    assert outcome.swept == 1
    assert outcome.claimed == 1
    assert outcome.processed == 1
    assert outcome.errors == []

    evidence = objectives.evidence_events(roof.id)
    assert len(evidence) == 1
    written = evidence[0]
    assert written.event_kind == objective_store.EVENT_FACT
    assert "Roofer confirmed attendance Tuesday." in written.summary
    assert written.provenance["source_kind"] == interp.SOURCE_KIND_INBOUND
    assert written.provenance["source_id"] == "src-roofer"
    assert written.provenance["event_id"] == "evt-1"
    assert written.provenance["payload_sha256"] == captured.stored.payload_sha256
    assert written.provenance["verified_by"] == "test-resolver"
    assert written.provenance["received_at"] == captured.stored.received_at
    assert written.provenance["evidence"] is True
    assert written.actor == "inbound:src-roofer"

    record = store.get(mem.db_path, "src-roofer", "evt-1")
    assert record.state == store.STATE_PROCESSED
    assert record.disposition_reason == "evidence_recorded"
    assert record.result["objective_id"] == roof.id
    assert record.result["recorded_now"] is True


@pytest.mark.asyncio
async def test_a_second_pass_over_the_same_event_writes_no_second_evidence(
    ctx,
    mem,
    objectives,
    roof,
):
    await capture(mem.db_path, event_id="evt-1", payload=ROOFER)
    await run(ctx)
    assert len(objectives.evidence_events(roof.id)) == 1

    # Nothing is left to claim, so a second pass is a no-op on its own.
    second = await run(ctx)
    assert second.claimed == 0
    assert len(objectives.evidence_events(roof.id)) == 1


@pytest.mark.asyncio
async def test_a_duplicate_delivery_produces_no_duplicate_evidence(ctx, mem, objectives, roof):
    first = await capture(mem.db_path, event_id="evt-1", payload=ROOFER)
    assert first.duplicate is False
    redelivery = await capture(mem.db_path, event_id="evt-1", payload=ROOFER)
    assert redelivery.duplicate is True

    outcome = await run(ctx)
    # One logical event, one processing row, one piece of evidence.
    assert outcome.swept == 1
    assert outcome.processed == 1
    assert len(objectives.evidence_events(roof.id)) == 1
    assert len(inbound_store.recent_events(mem.db_path, limit=10)) == 1


# --------------------------------------------------- other dispositions


@pytest.mark.asyncio
async def test_an_irrelevant_event_reaches_a_terminal_irrelevant_disposition(
    ctx,
    mem,
    objectives,
    roof,
):
    await capture(
        mem.db_path,
        event_id="evt-parcel",
        payload={"body": "Your parcel was delivered to the front door."},
    )
    outcome = await run(ctx)
    assert outcome.irrelevant == 1
    assert outcome.processed == 0

    record = store.get(mem.db_path, "src-roofer", "evt-parcel")
    assert record.state == store.STATE_IRRELEVANT
    assert record.disposition_reason == "no_matching_live_objective"
    assert record.terminal is True
    assert objectives.evidence_events(roof.id) == []
    # Terminal means terminal: a later pass does not reconsider it.
    assert (await run(ctx)).claimed == 0


@pytest.mark.asyncio
async def test_an_uncertain_event_is_refused_rather_than_called_irrelevant(
    ctx,
    mem,
    objectives,
    roof,
):
    """An external party asking Bartholomew to act is not evidence, and it is
    not "nothing to do with anything" either. Recording it as irrelevant would
    manufacture a verdict the interpretation seam explicitly declined to give.
    """
    await capture(
        mem.db_path,
        event_id="evt-directive",
        payload={"body": "Please book the roof repair for Tuesday."},
    )
    outcome = await run(ctx)
    assert outcome.refused == 1

    record = store.get(mem.db_path, "src-roofer", "evt-directive")
    assert record.state == store.STATE_REFUSED
    assert record.disposition_reason == "external_directive_not_evidence"
    assert objectives.evidence_events(roof.id) == []


@pytest.mark.asyncio
async def test_hedged_content_never_becomes_recorded_fact(ctx, mem, objectives, roof):
    await capture(
        mem.db_path,
        event_id="evt-hedge",
        payload={"body": "The roof repair might happen Tuesday."},
    )
    await run(ctx)
    assert store.get(mem.db_path, "src-roofer", "evt-hedge").state == store.STATE_REFUSED
    assert objectives.evidence_events(roof.id) == []


@pytest.mark.asyncio
async def test_an_unknown_event_type_fails_safely_and_visibly(ctx, mem, objectives, roof):
    await capture(
        mem.db_path,
        event_id="evt-unknown",
        payload=ROOFER,
        event_type="mail.received",
    )
    outcome = await run(ctx)
    assert outcome.refused == 1
    assert outcome.quarantined == 0

    record = store.get(mem.db_path, "src-roofer", "evt-unknown")
    assert record.state == store.STATE_REFUSED
    assert record.disposition_reason == processor.REASON_UNKNOWN_TYPE
    assert "mail.received" in record.result["detail"]
    # Visible, not dropped: it is still in the record and still countable.
    assert objectives.evidence_events(roof.id) == []
    # And recoverable once the type is registered.
    assert store.requeue(mem.db_path, source_id="src-roofer", event_id="evt-unknown") == 1


@pytest.mark.asyncio
async def test_a_payload_that_is_not_what_its_type_promises_is_refused_not_retried(
    ctx,
    mem,
    objectives,
    roof,
):
    await capture(
        mem.db_path,
        event_id="evt-deep",
        payload={"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": "too deep"}}}}}}}},
        event_type=OBSERVATION_STATUS,
    )
    outcome = await run(ctx)
    assert outcome.refused == 1
    record = store.get(mem.db_path, "src-roofer", "evt-deep")
    assert record.state == store.STATE_REFUSED
    assert record.disposition_reason == processor.REASON_INVALID_PAYLOAD
    # Refused on its first attempt: a deterministic malformation must not
    # burn three retries and then be filed as a fault.
    assert record.attempts == 1


@pytest.mark.asyncio
async def test_a_payload_altered_after_capture_is_refused(ctx, mem, objectives, roof):
    await capture(mem.db_path, event_id="evt-1", payload=ROOFER)
    import sqlite3

    conn = sqlite3.connect(mem.db_path)
    try:
        conn.execute(
            "UPDATE inbound_events SET payload_json = ? WHERE event_id = ?",
            ('{"body":"a different claim entirely"}', "evt-1"),
        )
        conn.commit()
    finally:
        conn.close()

    outcome = await run(ctx)
    assert outcome.refused == 1
    record = store.get(mem.db_path, "src-roofer", "evt-1")
    assert record.disposition_reason == processor.REASON_DIGEST_MISMATCH
    assert objectives.evidence_events(roof.id) == []


# ---------------------------------------------- crash and lease recovery


@pytest.mark.asyncio
async def test_a_crash_after_claiming_loses_nothing_and_duplicates_nothing(
    ctx,
    mem,
    objectives,
    roof,
):
    """The claim survives the process that took it, and the effect happens once.

    Two halves, and both are needed. The first is that a claimed event is
    still there afterwards. The second is that re-running the work is safe --
    which is what actually protects against the worst case, where the effect
    landed but the settle did not.
    """
    await capture(mem.db_path, event_id="evt-1", payload=ROOFER)
    store.sweep_captured(mem.db_path, limit=10)

    # A pass claims the event and the process dies here.
    claimed = store.claim_batch(
        mem.db_path,
        runtime_id=None,
        limit=1,
        lease_seconds=1,
        max_attempts=3,
    )
    assert len(claimed) == 1
    assert store.get(mem.db_path, "src-roofer", "evt-1").state == store.STATE_CLAIMED

    # While the lease holds, nothing else takes it -- it is held, not lost.
    assert (await run(ctx)).claimed == 0
    assert objectives.evidence_events(roof.id) == []

    time.sleep(1.1)
    recovered = await run(ctx)
    assert recovered.claimed == 1
    assert recovered.processed == 1
    assert len(objectives.evidence_events(roof.id)) == 1


@pytest.mark.asyncio
async def test_re_processing_an_event_whose_effect_already_landed_is_idempotent(
    ctx,
    mem,
    objectives,
    roof,
):
    """The crash-after-effect-before-settle case, forced deterministically."""
    await capture(mem.db_path, event_id="evt-1", payload=ROOFER)
    await run(ctx)
    assert len(objectives.evidence_events(roof.id)) == 1

    # Put the settled event back, as a lost settle would leave it.
    assert store.requeue(mem.db_path, from_states=(store.STATE_PROCESSED,)) == 1

    outcome = await run(ctx)
    assert outcome.processed == 1
    record = store.get(mem.db_path, "src-roofer", "evt-1")
    assert record.state == store.STATE_PROCESSED
    assert record.disposition_reason == "already_recorded"
    assert record.result["recorded_now"] is False
    # The effect happened exactly once, across both passes.
    assert len(objectives.evidence_events(roof.id)) == 1


# ------------------------------------------------------- the Parking Brake


@pytest.mark.asyncio
async def test_an_engaged_brake_prevents_mutation_and_preserves_the_backlog(
    ctx,
    mem,
    objectives,
    governance,
    roof,
):
    await capture(mem.db_path, event_id="evt-1", payload=ROOFER)
    store.sweep_captured(mem.db_path, limit=10)
    before = store.get(mem.db_path, "src-roofer", "evt-1")

    governance.engage("global", reason="test", actor="test")
    outcome = await run(ctx)

    assert outcome.deferred == processor.DEFERRED_PARKING_BRAKE
    assert outcome.claimed == 0
    assert outcome.processed == 0
    assert objectives.evidence_events(roof.id) == []

    after = store.get(mem.db_path, "src-roofer", "evt-1")
    # Byte-for-byte the same row: the backlog is preserved, not merely
    # un-processed. Nothing was claimed and no attempt was spent.
    assert after.state == store.STATE_CAPTURED
    assert after.attempts == before.attempts == 0
    assert after.claim_token is None


@pytest.mark.asyncio
async def test_releasing_the_brake_lets_the_preserved_backlog_resume(
    ctx,
    mem,
    objectives,
    governance,
    roof,
):
    await capture(mem.db_path, event_id="evt-1", payload=ROOFER)
    governance.engage("global", reason="test", actor="test")
    assert (await run(ctx)).deferred == processor.DEFERRED_PARKING_BRAKE

    governance.disengage(reason="test", actor="test")
    outcome = await run(ctx)
    assert outcome.deferred is None
    assert outcome.processed == 1
    assert len(objectives.evidence_events(roof.id)) == 1


@pytest.mark.asyncio
async def test_a_scoped_brake_that_does_not_name_the_scheduler_still_stops_mutation(
    ctx,
    mem,
    objectives,
    governance,
    roof,
):
    """The gate is the engaged flag, not a subsystem scope.

    This is the case the pass-level check exists for: a brake engaged for
    `voice` alone does not stop the scheduler drive from running, so without
    it the pass would claim events, spend attempts, and only then be refused
    at the write.
    """
    await capture(mem.db_path, event_id="evt-1", payload=ROOFER)
    store.sweep_captured(mem.db_path, limit=10)
    governance.engage("voice", reason="test", actor="test")

    outcome = await run(ctx)
    assert outcome.deferred == processor.DEFERRED_PARKING_BRAKE
    assert objectives.evidence_events(roof.id) == []
    assert store.get(mem.db_path, "src-roofer", "evt-1").attempts == 0


@pytest.mark.asyncio
async def test_a_brake_engaged_mid_batch_returns_the_event_with_its_attempt_refunded(
    ctx,
    mem,
    objectives,
    governance,
    roof,
    monkeypatch,
):
    """The seam refuses at the write; the event must go back untouched."""
    await capture(mem.db_path, event_id="evt-1", payload=ROOFER)
    store.sweep_captured(mem.db_path, limit=10)

    engaged_after_claim = {"done": False}
    real_claim = store.claim_batch

    def claim_then_engage(*args, **kwargs):
        claimed = real_claim(*args, **kwargs)
        if claimed and not engaged_after_claim["done"]:
            engaged_after_claim["done"] = True
            governance.engage("global", reason="mid-batch", actor="test")
        return claimed

    monkeypatch.setattr(store, "claim_batch", claim_then_engage)
    outcome = await processor.process_batch(
        ctx,
        settings=SETTINGS,
        runtime_id=None,
        resolve_runtime=False,
    )
    assert outcome.released == 1
    assert outcome.processed == 0
    assert objectives.evidence_events(roof.id) == []

    record = store.get(mem.db_path, "src-roofer", "evt-1")
    assert record.state == store.STATE_CAPTURED
    assert record.attempts == 0


# ------------------------------------------------------- Identity policy


@pytest.mark.asyncio
async def test_a_policy_that_does_not_allow_processing_preserves_the_backlog(
    mem,
    objectives,
    governance,
    roof,
):
    denied = _Ctx(
        mem,
        objectives,
        governance,
        identity=IdentityContext(
            tool_use_default_allowed=False,
            tool_use_allowlist=["objective_record", "inbound_capture"],
        ),
    )
    await capture(mem.db_path, event_id="evt-1", payload=ROOFER)
    store.sweep_captured(mem.db_path, limit=10)

    outcome = await run(denied)
    assert outcome.deferred == processor.DEFERRED_POLICY
    assert outcome.claimed == 0
    record = store.get(mem.db_path, "src-roofer", "evt-1")
    assert record.state == store.STATE_CAPTURED
    assert record.attempts == 0
    assert objectives.evidence_events(roof.id) == []


@pytest.mark.asyncio
async def test_a_policy_denial_at_the_objective_write_is_a_terminal_refusal(
    mem,
    objectives,
    governance,
    roof,
):
    """Processing is allowed; writing to the objective is not.

    A different question from the one above, and it gets a different answer:
    the pass ran, the event was examined, and the write was refused. That is a
    decision, so it is terminal -- and requeueable once the policy changes.
    """
    ctx = _Ctx(
        mem,
        objectives,
        governance,
        identity=IdentityContext(
            tool_use_default_allowed=False,
            tool_use_allowlist=["inbound_capture", "inbound_event_process"],
        ),
    )
    await capture(mem.db_path, event_id="evt-1", payload=ROOFER)
    outcome = await run(ctx)

    assert outcome.refused == 1
    record = store.get(mem.db_path, "src-roofer", "evt-1")
    assert record.state == store.STATE_REFUSED
    assert record.disposition_reason == "governance_denied"
    assert "objective_record" in record.result["governance_reason"]
    assert objectives.evidence_events(roof.id) == []


@pytest.mark.asyncio
async def test_processing_runs_when_no_identity_context_is_wired_in(
    mem,
    objectives,
    governance,
    roof,
):
    """Additive: a context with no Identity is not a denied one."""
    ctx = _Ctx(mem, objectives, governance, identity=None)
    await capture(mem.db_path, event_id="evt-1", payload=ROOFER)
    outcome = await run(ctx)
    assert outcome.processed == 1
    assert len(objectives.evidence_events(roof.id)) == 1


# ------------------------------------------------------- tenant isolation


@pytest.mark.asyncio
async def test_another_tenants_event_is_never_claimed_processed_or_attached(
    ctx,
    mem,
    objectives,
    roof,
):
    await capture(
        mem.db_path,
        event_id="evt-theirs",
        payload=ROOFER,
        runtime_id="another-user",
    )
    outcome = await run(ctx, runtime_id=None)
    assert outcome.claimed == 0
    assert objectives.evidence_events(roof.id) == []
    assert store.get(mem.db_path, "src-roofer", "evt-theirs").state == store.STATE_CAPTURED

    # And a process bound to a third tenant cannot reach it either.
    assert (await run(ctx, runtime_id="a-third-user")).claimed == 0
    assert store.get(mem.db_path, "src-roofer", "evt-theirs").state == store.STATE_CAPTURED


@pytest.mark.asyncio
async def test_a_mismatched_row_is_refused_even_if_it_reaches_a_claim(
    ctx,
    mem,
    objectives,
    roof,
):
    """Defence in depth on the property that matters most.

    The claim filter is the real protection; this proves the second check
    behind it also holds, so a future refactor that loses the filter fails
    closed rather than silently attaching one tenant's event to another's
    objectives.
    """
    await capture(mem.db_path, event_id="evt-theirs", payload=ROOFER, runtime_id="another-user")
    store.sweep_captured(mem.db_path, limit=10)
    claimed = store.claim_batch(
        mem.db_path,
        runtime_id="another-user",
        limit=1,
        lease_seconds=60,
        max_attempts=3,
    )

    result = processor.ProcessingPassResult()
    await processor._process_one(
        ctx,
        mem.db_path,
        claimed[0],
        runtime_id=None,
        settings=SETTINGS,
        result=result,
    )
    assert result.refused == 1
    record = store.get(mem.db_path, "src-roofer", "evt-theirs")
    assert record.state == store.STATE_REFUSED
    assert record.disposition_reason == processor.REASON_TENANT_MISMATCH
    assert objectives.evidence_events(roof.id) == []


# ------------------------------------------------------------ bounds


@pytest.mark.asyncio
async def test_a_pass_claims_no_more_than_its_batch_limit(ctx, mem, objectives, roof):
    for i in range(7):
        await capture(
            mem.db_path,
            event_id=f"evt-{i}",
            payload={"body": f"Your parcel {i} was delivered."},
        )
    outcome = await run(ctx, settings=EventProcessingSettings(batch_limit=3, deadline_seconds=30.0))
    assert outcome.claimed == 3
    assert store.pending_count(mem.db_path) == 4


@pytest.mark.asyncio
async def test_a_disabled_backbone_does_nothing_at_all(ctx, mem, objectives, roof):
    await capture(mem.db_path, event_id="evt-1", payload=ROOFER)
    outcome = await run(ctx, settings=EventProcessingSettings(enabled=False))
    assert outcome.deferred == processor.DEFERRED_DISABLED
    assert outcome.swept == 0
    assert outcome.claimed == 0
    # Not even swept: a disabled backbone leaves capture exactly as it was.
    assert store.get(mem.db_path, "src-roofer", "evt-1") is None
    assert objectives.evidence_events(roof.id) == []


@pytest.mark.asyncio
async def test_the_batch_deadline_returns_unprocessed_claims_with_their_attempt_refunded(
    ctx,
    mem,
    objectives,
    roof,
):
    for i in range(3):
        await capture(
            mem.db_path,
            event_id=f"evt-{i}",
            payload={"body": f"Your parcel {i} was delivered."},
        )
    # A zero budget is the smallest one that makes the mechanism deterministic:
    # the first event is always attempted (a pass that could do nothing at all
    # would never drain), and everything after it is returned.
    outcome = await run(
        ctx,
        settings=EventProcessingSettings(batch_limit=3, deadline_seconds=0.0),
    )
    assert outcome.claimed == 3
    assert outcome.released == 2
    assert outcome.irrelevant == 1
    # Nothing that was put back moved closer to quarantine.
    for record in store.list_by_state(mem.db_path, store.STATE_CAPTURED, limit=10):
        assert record.attempts == 0


@pytest.mark.asyncio
async def test_a_handler_fault_costs_an_attempt_and_quarantines_when_they_run_out(
    ctx,
    mem,
    objectives,
    roof,
    monkeypatch,
):
    from bartholomew.kernel.event_processing import adapters, registry

    async def always_fails(ctx_, event, payload):
        raise adapters.TransientProcessingError("the downstream store is unreachable")

    # Substituted in the registry rather than on the registration, which is
    # frozen: a registration cannot be mutated in place, by a test or by
    # anything else.
    monkeypatch.setitem(
        registry._REGISTRY,
        OBSERVATION_NOTE,
        adapters.RegisteredEventType(
            event_type=OBSERVATION_NOTE,
            parse=adapters.ObservationPayload.parse,
            handler=always_fails,
            description="always fails",
        ),
    )

    await capture(mem.db_path, event_id="evt-poison", payload=ROOFER)
    for attempt in range(1, 4):
        outcome = await run(ctx)
        assert outcome.claimed == 1
        record = store.get(mem.db_path, "src-roofer", "evt-poison")
        if attempt < 3:
            assert record.state == store.STATE_CAPTURED
            assert outcome.retried == 1
        else:
            assert record.state == store.STATE_QUARANTINED
            assert outcome.quarantined == 1

    assert (await run(ctx)).claimed == 0
    assert objectives.evidence_events(roof.id) == []
