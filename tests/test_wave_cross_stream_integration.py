"""Cross-stream integration for the A/B/C/D wave (Session E).

Each of the four streams proved its own slice in isolation, against the same
authoritative main, and every one of those suites still passes unchanged.
This file exists for the question none of them could ask: do the four
capabilities *coexist* through the canonical authorities, without a bypass, a
duplicated authority, a fabricated fact, or a weakened gate?

The compound chain exercised here, in one process against one real database:

    an unattended run is recorded (A)
        -> the PC companion emits a bounded observation (D)
        -> it crosses the canonical inbound capture boundary (D -> capture)
        -> a relevant inbound event becomes evidence on a live objective (B)
        -> the objective reaches a recorded outcome
        -> the experience produces a reviewable candidate lesson (C)
        -> a named reviewer accepts it, and only then is it consolidated (C)
        -> the unattended evidence report reconstructs what actually
           happened, including the captured rows (A)

Nothing is mocked: a real `MemoryStore`, the real capture seam, the real
`ObjectiveStore`, the real governed objective and learning seams, the real
companion envelope builder, and A's real evidence store and report.

Two deliberate restraints, because an integration test that forces every
capability to trigger the next one proves less than it appears to:

* The companion's observation is *not* massaged into objective evidence. A
  narrow device signal legitimately means nothing about a household
  objective, and `test_companion_observation_is_not_forced_into_meaning`
  asserts that B says so rather than inventing a match. That is the correct
  integrated behaviour, not a gap.
* Nothing here wires interpretation into ingress. Capture still returns
  "captured, explicitly not processed", and each downstream step is taken by
  this test because it decided to, exactly as a real caller would.
"""

from __future__ import annotations

import sqlite3
from datetime import timezone

import pytest

from bartholomew.companion import envelope as companion_envelope
from bartholomew.companion import observation as companion_observation
from bartholomew.kernel import candidate_learning, inbound_store
from bartholomew.kernel import objective_store as os_mod
from bartholomew.kernel.competency import COMPETENCY_KINDS
from bartholomew.kernel.inbound_interpretation import (
    RELEVANCE_RELEVANT,
    interpret_captured_event,
)
from bartholomew.kernel.memory.privacy_guard import set_consent_handler
from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.objective_store import ObjectiveStore
from bartholomew.kernel.runtime_contract import (
    LEARNING_ACTION_ACCEPT,
    LEARNING_ACTION_PROPOSE,
    LEARNING_ACTION_REJECT,
    LEARNING_OUTCOME_ACCEPTED,
    LEARNING_OUTCOME_PROPOSED,
    LEARNING_OUTCOME_REJECTED,
    run_candidate_lesson_through_runtime_contract,
    run_inbound_through_runtime_contract,
)
from bartholomew.orchestrator.safety import governance_store as gs
from bartholomew.orchestrator.safety.governance_store import GovernanceStore
from bartholomew.runtime import evidence as ev
from bartholomew.runtime import evidence_report
from identity_interpreter.identity_context import IdentityContext

RUN_ID = "wave-e-compound-1"
DEVICE_ID = "desk-01"
COMPANION_SOURCE = "desk-companion"
COMPETENCY_ID = "estate_management"
REVIEWER = "taylor"

#: What the *shipped* `Identity.yaml` grants, as far as this wave is
#: concerned: `objective_record` and `inbound_capture` were both already
#: allowlisted before any of these four streams existed, and `tool_use.
#: default_allowed` is false. Session C's three learning kinds are
#: deliberately NOT here -- see `SHIPPED_CONTEXT`'s use below.
SHIPPED_CONTEXT = IdentityContext(
    tool_use_default_allowed=False,
    tool_use_allowlist=["objective_record", "inbound_capture"],
)

#: The same identity with learning explicitly granted, which is what an
#: operator who has chosen to let Bartholomew learn from its own experience
#: would configure. Session C gates every learning action on
#: `evaluate_tool_policy()` per action kind, and the shipped identity is
#: deny-by-default, so the loop is unreachable until someone says otherwise.
#: That is a governance decision for the operator, not something integration
#: may quietly grant -- Session E deliberately did not add these kinds to
#: `Identity.yaml`. See `test_shipped_identity_does_not_grant_learning`.
LEARNING_CONTEXT = IdentityContext(
    tool_use_default_allowed=False,
    tool_use_allowlist=[
        "objective_record",
        "inbound_capture",
        LEARNING_ACTION_PROPOSE,
        LEARNING_ACTION_ACCEPT,
        LEARNING_ACTION_REJECT,
    ],
)


class _Ctx:
    """The duck-typed context every `runtime_contract` seam takes.

    One context for all four streams on purpose: if B's interpretation and
    C's learning loop needed materially different runtime wiring, that
    incompatibility would show up here as an attribute this class cannot
    satisfy.
    """

    def __init__(self, mem, objectives, governance, identity=LEARNING_CONTEXT):
        self.mem = mem
        self.objective_store = objectives
        self.governance_store = governance
        self.identity_context = identity
        self.blocking_executor = None
        self.tz = timezone.utc
        self.cfg = {}


@pytest.fixture(autouse=True)
def _reset_consent_handler():
    set_consent_handler(None)
    yield
    set_consent_handler(None)


@pytest.fixture(autouse=True)
def _reset_evidence(monkeypatch):
    """A's recorder is process-global; keep it from leaking between tests."""
    ev._reset_for_tests()
    monkeypatch.delenv(ev.RUN_ID_ENV, raising=False)
    yield
    ev._reset_for_tests()


@pytest.fixture
async def mem(tmp_path):
    store = MemoryStore(str(tmp_path / "wave_integration.db"))
    await store.init()
    gs.ensure_schema(store.db_path)
    inbound_store.ensure_schema(store.db_path)
    os_mod.ensure_schema(store.db_path)
    yield store
    await store.close()


@pytest.fixture
def ctx(mem):
    return _Ctx(mem, ObjectiveStore(mem.db_path), GovernanceStore(mem.db_path))


def _companion_envelope(sequence: int = 1):
    """A bounded observation, built by D's own envelope code."""
    obs = companion_observation.foreground_app(
        DEVICE_ID,
        sequence,
        application="Outlook",
        observed_at="2026-08-30T08:55:00Z",
    )
    return companion_envelope.to_inbound_envelope(obs, source_id=COMPANION_SOURCE)


async def _capture(db_path, body, *, verified_by="test-resolver"):
    """Through the canonical capture seam. No companion-specific path."""
    return await run_inbound_through_runtime_contract(
        db_path=db_path,
        source_id=body["source_id"],
        event_id=body["event_id"],
        event_type=body["event_type"],
        payload=body["payload"],
        occurred_at=body.get("occurred_at"),
        verified_by=verified_by,
    )


ROOFER_BODY = {
    "source_id": "src-roofer",
    "event_id": "evt-roofer-compound-1",
    "event_type": "msg.received",
    "payload": {
        "subject": "Roof repair",
        "body": "Roofer confirmed attendance Tuesday to repair the roof.",
    },
    "occurred_at": "2026-08-30T09:00:00Z",
}


# ---------------------------------------------------------------------------
# The compound scenario.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compound_wave_scenario(ctx, mem, monkeypatch):
    """All four capabilities, one database, one chain of custody."""
    db = mem.db_path

    # --- A: the unattended run opens an incarnation -----------------------
    monkeypatch.setenv(ev.RUN_ID_ENV, RUN_ID)
    started = ev.record_process_start(db, runtime_id="runtime-incarnation-1")
    assert started is not None, "the unattended run must have an identity"
    assert started[0] == RUN_ID

    # --- D: a bounded companion observation crosses canonical capture -----
    body = _companion_envelope()
    assert body["event_type"].startswith(companion_observation.EVENT_TYPE_PREFIX)
    # The companion adds no field to the inbound contract.
    assert set(body) == {"source_id", "event_id", "event_type", "payload", "occurred_at"}

    companion_captured = await _capture(db, body)
    assert companion_captured.captured is True
    # Device provenance survives capture, unaltered.
    assert companion_captured.stored.source_id == COMPANION_SOURCE
    assert companion_captured.stored.event_id.startswith("companion:")
    assert companion_captured.stored.payload_sha256

    # --- The live objective the wave is about -----------------------------
    objectives = ctx.objective_store
    roof = objectives.open(
        title="Get the roof repaired",
        outcome_statement="The roof is repaired and no longer leaking",
    )

    # --- B: a relevant inbound event becomes evidence on that objective ---
    captured = await _capture(db, ROOFER_BODY)
    assert captured.captured is True
    # Capture alone mutates no objective: the boundary really is a boundary.
    assert objectives.evidence_events(roof.id) == []

    payload = inbound_store.get_event_payload(
        db,
        ROOFER_BODY["source_id"],
        ROOFER_BODY["event_id"],
    )
    interpreted = await interpret_captured_event(ctx, stored=captured.stored, payload=payload)
    assert interpreted.interpretation.relevance == RELEVANCE_RELEVANT
    assert interpreted.interpretation.objective_id == roof.id
    assert interpreted.recorded is True

    # Provenance survived all the way into objective history.
    events = objectives.evidence_events(roof.id)
    assert len(events) == 1
    provenance = events[0].provenance
    assert provenance["source_id"] == ROOFER_BODY["source_id"]
    assert provenance["event_id"] == ROOFER_BODY["event_id"]
    assert provenance["evidence"] is True

    # --- The objective reaches a recorded outcome -------------------------
    objectives.record(
        roof.id,
        event_kind=os_mod.EVENT_DECISION,
        summary="Decided to confirm the roofer's Tuesday slot rather than seek more quotes",
    )
    objectives.record(
        roof.id,
        event_kind=os_mod.EVENT_ACTION,
        summary="Confirmed the roofer for Tuesday and the roof was repaired",
    )
    objectives.complete(
        roof.id,
        resolution=os_mod.RESOLUTION_ACHIEVED,
        outcome_note="Roof repaired on the Tuesday the roofer confirmed",
    )

    # --- C: experience -> candidate lesson --------------------------------
    proposed = await run_candidate_lesson_through_runtime_contract(
        ctx,
        LEARNING_ACTION_PROPOSE,
        objective_id=roof.id,
        competency_id=COMPETENCY_ID,
    )
    assert proposed.outcome == LEARNING_OUTCOME_PROPOSED
    lesson = proposed.lesson
    assert lesson is not None

    # A proposal is inference, not knowledge, and is not retrievable.
    assert lesson.epistemic_status != "observed"
    assert candidate_learning.KIND not in COMPETENCY_KINDS

    # --- C: governed human review consolidates it -------------------------
    accepted = await run_candidate_lesson_through_runtime_contract(
        ctx,
        LEARNING_ACTION_ACCEPT,
        competency_id=COMPETENCY_ID,
        slug=lesson.slug,
        reviewer=REVIEWER,
    )
    assert accepted.outcome == LEARNING_OUTCOME_ACCEPTED
    assert accepted.consolidated is True
    # The consolidated record lives in the competency substrate, where later
    # reasoning can retrieve it.
    assert accepted.consolidation.outcomes[0].kind in COMPETENCY_KINDS

    # --- A: the evidence report reconstructs what actually occurred -------
    ev.record_process_stop(db, end_kind=ev.END_CLEAN, detail=None)
    frozen = evidence_report.freeze(db, RUN_ID)
    record = frozen["record"]
    assert frozen["digest"]
    # Deterministic: freezing the same evidence twice yields the same digest.
    assert evidence_report.freeze(db, RUN_ID)["digest"] == frozen["digest"]

    inbound = record["sources"]["inbound_events"]
    assert inbound["available"] is True
    captured_ids = {i["event_id"] for i in inbound["items"]}
    # Both the companion observation and the roofer message are reconstructable.
    assert ROOFER_BODY["event_id"] in captured_ids
    assert any(e.startswith("companion:") for e in captured_ids)


# ---------------------------------------------------------------------------
# Negative paths. Each one is a way the integrated system could quietly lie.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_companion_observation_is_not_forced_into_meaning(ctx, mem):
    """A bounded device signal does not become objective evidence.

    The B<->D seam's real guarantee. A foreground-application name says
    nothing about whether a roof got fixed, and interpretation must decline
    rather than manufacture a match to make the chain look continuous.
    """
    objectives = ctx.objective_store
    roof = objectives.open(
        title="Get the roof repaired",
        outcome_statement="The roof is repaired and no longer leaking",
    )
    body = _companion_envelope()
    captured = await _capture(mem.db_path, body)
    assert captured.captured is True

    payload = inbound_store.get_event_payload(mem.db_path, body["source_id"], body["event_id"])
    result = await interpret_captured_event(ctx, stored=captured.stored, payload=payload)

    assert result.recorded is False
    assert result.interpretation.relevance != RELEVANCE_RELEVANT
    assert objectives.evidence_events(roof.id) == []


@pytest.mark.asyncio
async def test_companion_retry_produces_no_duplicate_capture_or_evidence(ctx, mem):
    """A companion redelivery is one logical event, on both sides of capture.

    D derives a stable `event_id`, so a retry after a restart re-sends the
    same envelope. Capture's UNIQUE constraint makes the second one a
    duplicate, and interpretation refuses a duplicate outright -- so neither
    the capture table nor an objective's history gains a second row.
    """
    db = mem.db_path
    body = _companion_envelope()

    first = await _capture(db, body)
    second = await _capture(db, body)

    assert first.captured is True
    assert first.duplicate is False
    assert second.duplicate is True

    with sqlite3.connect(db) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM inbound_events WHERE source_id = ? AND event_id = ?",
            (body["source_id"], body["event_id"]),
        ).fetchone()[0]
    assert count == 1, "a retry must not create a second captured row"

    # And the duplicate cannot become evidence, even if a caller interprets it.
    payload = inbound_store.get_event_payload(db, body["source_id"], body["event_id"])
    result = await interpret_captured_event(ctx, stored=second.stored, payload=payload)
    assert result.recorded is False


@pytest.mark.asyncio
async def test_duplicate_relevant_event_does_not_duplicate_objective_evidence(ctx, mem):
    """Re-interpreting an already-attached event adds no second evidence row."""
    db = mem.db_path
    objectives = ctx.objective_store
    roof = objectives.open(
        title="Get the roof repaired",
        outcome_statement="The roof is repaired and no longer leaking",
    )
    captured = await _capture(db, ROOFER_BODY)
    payload = inbound_store.get_event_payload(
        db,
        ROOFER_BODY["source_id"],
        ROOFER_BODY["event_id"],
    )

    first = await interpret_captured_event(ctx, stored=captured.stored, payload=payload)
    assert first.recorded is True
    assert len(objectives.evidence_events(roof.id)) == 1

    # Same captured row, interpreted again.
    again = await interpret_captured_event(ctx, stored=captured.stored, payload=payload)
    assert again.recorded is False
    assert len(objectives.evidence_events(roof.id)) == 1


@pytest.mark.asyncio
async def test_rejected_lesson_never_becomes_retrievable_learning(ctx, mem):
    """A rejection consolidates nothing, then or ever."""
    objectives = ctx.objective_store
    objective = objectives.open(
        title="Get the boiler serviced before winter",
        outcome_statement="A working boiler with a valid service record",
    )
    objectives.record(
        objective.id,
        event_kind=os_mod.EVENT_FACT,
        summary="The boiler is still inside its manufacturer warranty period",
    )
    objectives.record(
        objective.id,
        event_kind=os_mod.EVENT_ACTION,
        summary="Called the boiler warranty line and booked a free service visit",
    )
    objectives.complete(
        objective.id,
        resolution=os_mod.RESOLUTION_ACHIEVED,
        outcome_note="Serviced free under warranty",
    )

    proposed = await run_candidate_lesson_through_runtime_contract(
        ctx,
        LEARNING_ACTION_PROPOSE,
        objective_id=objective.id,
        competency_id=COMPETENCY_ID,
    )
    assert proposed.outcome == LEARNING_OUTCOME_PROPOSED
    slug = proposed.lesson.slug

    rejected = await run_candidate_lesson_through_runtime_contract(
        ctx,
        LEARNING_ACTION_REJECT,
        competency_id=COMPETENCY_ID,
        slug=slug,
        reviewer=REVIEWER,
    )
    assert rejected.outcome == LEARNING_OUTCOME_REJECTED
    assert rejected.consolidated is False
    assert rejected.consolidation is None

    # Accepting afterwards is refused: rejection is terminal.
    late = await run_candidate_lesson_through_runtime_contract(
        ctx,
        LEARNING_ACTION_ACCEPT,
        competency_id=COMPETENCY_ID,
        slug=slug,
        reviewer=REVIEWER,
    )
    assert late.outcome != LEARNING_OUTCOME_ACCEPTED
    assert late.consolidated is False


@pytest.mark.asyncio
async def test_parking_brake_halts_the_whole_integrated_chain(ctx, mem):
    """One brake, honoured by every stream that can mutate governed state.

    The composition check that matters: neither B's interpretation nor C's
    learning loop acquired a gate of its own, and neither bypasses the
    existing one. With the brake engaged, an event that *is* relevant records
    nothing and a lesson is not proposed.
    """
    db = mem.db_path
    objectives = ctx.objective_store
    roof = objectives.open(
        title="Get the roof repaired",
        outcome_statement="The roof is repaired and no longer leaking",
    )
    captured = await _capture(db, ROOFER_BODY)
    assert captured.captured is True
    payload = inbound_store.get_event_payload(
        db,
        ROOFER_BODY["source_id"],
        ROOFER_BODY["event_id"],
    )

    ctx.governance_store.engage("global", reason="integration test halt", actor="test")

    interpreted = await interpret_captured_event(ctx, stored=captured.stored, payload=payload)
    assert interpreted.recorded is False
    assert objectives.evidence_events(roof.id) == []

    learning = await run_candidate_lesson_through_runtime_contract(
        ctx,
        LEARNING_ACTION_PROPOSE,
        objective_id=roof.id,
        competency_id=COMPETENCY_ID,
    )
    assert learning.governance_allowed is False
    assert learning.consolidated is False


def test_the_wave_added_no_second_device_actuation_path():
    """The companion package still cannot act on the computer after integration.

    Session D asserts this within its own package. This re-asserts it against
    the *integrated* tree, because the risk a wave introduces is a new module
    quietly importing an actuation surface next to the companion.
    """
    import ast
    from pathlib import Path

    package = Path(__file__).resolve().parents[1] / "bartholomew" / "companion"
    banned = {
        "subprocess",
        "pyautogui",
        "keyboard",
        "mouse",
        "pyscreenshot",
    }
    sources = sorted(package.glob("*.py"))
    assert sources, "the companion package must exist for this guard to mean anything"

    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert (
                        alias.name.split(".")[0] not in banned
                    ), f"{path.name} imports {alias.name}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
                assert root not in banned, f"{path.name} imports from {node.module}"


@pytest.mark.asyncio
async def test_shipped_identity_does_not_grant_learning(mem):
    """Learning is unreachable until an operator grants it. Deliberate.

    Session C gates each learning action on `evaluate_tool_policy()` at the
    action-kind grain, and the shipped `Identity.yaml` is
    `tool_use.default_allowed: false` without any `learning_*` entry. So on
    the identity this repository actually ships, the learning loop is denied
    at its Governance stage.

    Session E left it that way on purpose. Every comparable seam kind
    (`notify`, `awaiting_response_*`, `inbound_capture`, `objective_record`)
    needed an explicit `Identity.yaml` entry before it could run, and adding
    three more here would grant Bartholomew standing permission to learn from
    its own experience as a side effect of *integration*. That is an
    operator's decision to make deliberately, not a merge's to make quietly,
    and deny-by-default is the safer end state for a capability whose whole
    risk is self-modification.

    This test exists so the limitation is recorded as behaviour rather than
    prose, and so that granting it later is a visible, intentional change.
    """
    ctx = _Ctx(
        mem,
        ObjectiveStore(mem.db_path),
        GovernanceStore(mem.db_path),
        identity=SHIPPED_CONTEXT,
    )
    objective = ctx.objective_store.open(
        title="Get the roof repaired",
        outcome_statement="The roof is repaired and no longer leaking",
    )
    ctx.objective_store.record(
        objective.id,
        event_kind=os_mod.EVENT_ACTION,
        summary="Confirmed the roofer and the roof was repaired",
    )
    ctx.objective_store.complete(
        objective.id,
        resolution=os_mod.RESOLUTION_ACHIEVED,
        outcome_note="Repaired",
    )

    result = await run_candidate_lesson_through_runtime_contract(
        ctx,
        LEARNING_ACTION_PROPOSE,
        objective_id=objective.id,
        competency_id=COMPETENCY_ID,
    )
    assert result.governance_allowed is False
    assert result.consolidated is False

    # Capture and objective recording, which the shipped identity *does*
    # grant, are unaffected: the denial is scoped to learning, not blanket.
    captured = await _capture(mem.db_path, ROOFER_BODY)
    assert captured.captured is True
