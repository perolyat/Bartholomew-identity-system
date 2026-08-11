"""
S5.2 Step 2 (governed write seam) tests.

Per `docs/S5_2_TRAINING_KNOWLEDGE_ACQUISITION_DESIGN.md` Sec.12, covering the
seam itself, governance, consent, provenance integrity, round-trip, and
supersession.

`TestNonVacuity` is the required control, mirroring the precedent in
`tests/test_voice_sight_runtime_contract_seam.py`: it neutralises the brake
(in the test only) and asserts writes then DO happen, proving the governance
tests would fail if the gate were bypassed rather than passing vacuously.

`TestPrivacyGuardNameKeyFalsePositive` documents a **pre-existing defect**
S5.2 deliberately did not work around -- see that class's docstring.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from bartholomew.kernel import runtime_contract as rc
from bartholomew.kernel import training
from bartholomew.kernel.competency import (
    CompetencyEnvelope,
    CompetencyEvidence,
    CompetencyHeuristic,
    CompetencyKnowledge,
    CompetencyProcedure,
    CompetencyRecord,
    Provenance,
)
from bartholomew.kernel.memory.privacy_guard import set_consent_handler
from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.runtime_contract import run_training_through_runtime_contract
from bartholomew.orchestrator.safety.parking_brake import BrakeStorage, ParkingBrake

COMPETENCY_ID = "estate_management"
HEURISTIC_KEY = f"{COMPETENCY_ID}.check_warranty_before_replace"
PROCEDURE_KEY = f"{COMPETENCY_ID}.quote_comparison"


@pytest.fixture(autouse=True)
def _reset_consent_handler():
    set_consent_handler(None)
    yield
    set_consent_handler(None)


class _FakeExperience:
    def get_active_goals(self):
        return ["keep the estate maintained"]


class _FakePersona:
    def get_active_pack_id(self):
        return "default"


class _FakeWorkingMemory:
    def get_context_string(self):
        return ""


class _FakeDaemon:
    """Minimal duck-typed daemon: the seam needs .mem plus the Experience
    Kernel state _build_interpretation() reads."""

    def __init__(self, mem: MemoryStore):
        self.mem = mem
        self.experience = _FakeExperience()
        self.persona_manager = _FakePersona()
        self.working_memory = _FakeWorkingMemory()


@pytest.fixture
async def daemon(tmp_path):
    mem = MemoryStore(str(tmp_path / "training.db"))
    await mem.init()
    yield _FakeDaemon(mem)
    await mem.close()


def _envelope() -> CompetencyEnvelope:
    return CompetencyEnvelope(competency_id=COMPETENCY_ID)


def _heuristic(rule: str = "Check warranty before recommending replacement") -> CompetencyHeuristic:
    """Default happy-path record. A heuristic is used rather than a procedure
    because procedures currently trip privacy_guard on their own JSON key
    name -- see TestPrivacyGuardNameKeyFalsePositive."""
    return CompetencyHeuristic(
        envelope=_envelope(),
        slug="check_warranty_before_replace",
        rule=rule,
        conditions="Any repair-vs-replace decision",
    )


def _procedure(slug: str = "quote_comparison", steps=None) -> CompetencyProcedure:
    return CompetencyProcedure(
        envelope=_envelope(),
        slug=slug,
        name="Quote comparison",
        steps=steps or ["Get at least three quotes", "Compare scope, price and warranty"],
        when_to_use="Any repair job above a trivial cost",
    )


def _submission(records=None, source_type="user_instruction") -> training.TrainingSubmission:
    return training.TrainingSubmission(
        competency_id=COMPETENCY_ID,
        source_type=source_type,
        source_detail="user stated this in conversation",
        records=records if records is not None else [_heuristic()],
    )


# ---------------------------------------------------------------------------
# 1. The seam.
# ---------------------------------------------------------------------------
class TestSeam:
    async def test_submission_stores_records_through_upsert_memory(self, daemon):
        result = await run_training_through_runtime_contract(daemon, _submission())

        assert result.governance_allowed is True
        assert result.errors == []
        assert result.all_stored is True
        assert result.stored_count == 1

        outcome = result.outcomes[0]
        assert outcome.kind == "competency_heuristic"
        assert outcome.key == HEURISTIC_KEY
        assert outcome.memory_id is not None

    async def test_stored_record_round_trips_as_a_valid_s5_1_object(self, daemon):
        await run_training_through_runtime_contract(daemon, _submission())

        row = await daemon.mem.get_memory("competency_heuristic", HEURISTIC_KEY)
        assert row is not None

        restored = CompetencyHeuristic.from_dict(
            json.loads(row["value"]),
            slug="check_warranty_before_replace",
        )
        assert restored.validate() == []
        assert restored.rule == "Check warranty before recommending replacement"
        assert restored.envelope.competency_id == COMPETENCY_ID

    async def test_all_non_tripping_kinds_land(self, daemon):
        records = [
            _heuristic(),
            CompetencyKnowledge(
                envelope=_envelope(),
                slug="boiler_basics",
                topic="Boilers",
                content="Bleed radiators annually before winter.",
            ),
            CompetencyEvidence(
                envelope=_envelope(),
                slug="boiler_2026",
                situation="Boiler stopped heating",
                action_taken="Compared three quotes",
                outcome="Repaired under warranty",
            ),
        ]
        result = await run_training_through_runtime_contract(daemon, _submission(records))

        assert result.stored_count == 3, result.to_dict()
        assert {o.kind for o in result.outcomes} == {
            "competency_heuristic",
            "competency_knowledge",
            "competency_evidence",
        }

    async def test_a_reflection_is_written_for_the_submission(self, daemon):
        from bartholomew.kernel.reflection import REFLECTION_KIND

        await run_training_through_runtime_contract(daemon, _submission())

        reflection = await daemon.mem.latest_reflection(REFLECTION_KIND)
        assert reflection is not None
        assert "training" in reflection["content"].lower()


# ---------------------------------------------------------------------------
# 2. Governance: fail-closed, before any write.
# ---------------------------------------------------------------------------
class TestGovernance:
    @pytest.mark.parametrize("scope", ["training", "global"])
    async def test_engaged_brake_blocks_ingestion_entirely(self, daemon, scope):
        brake = ParkingBrake(BrakeStorage(daemon.mem.db_path))
        brake.engage(scope)
        try:
            result = await run_training_through_runtime_contract(daemon, _submission())
        finally:
            brake.disengage()

        assert result.governance_allowed is False
        assert result.stored_count == 0
        assert all(o.outcome == training.OUTCOME_BLOCKED_BY_GOVERNANCE for o in result.outcomes)

        # Zero writes AND zero consent-queue entries (design Sec.13.4).
        assert await daemon.mem.get_memory("competency_heuristic", HEURISTIC_KEY) is None
        assert await daemon.mem.list_pending_sensitive_writes() == []

    async def test_unrelated_scope_does_not_block_training(self, daemon):
        """A dedicated training scope is only meaningful if engaging a
        different one leaves training alone (Decision D's whole point)."""
        brake = ParkingBrake(BrakeStorage(daemon.mem.db_path))
        brake.engage("skills")
        try:
            result = await run_training_through_runtime_contract(daemon, _submission())
        finally:
            brake.disengage()

        assert result.governance_allowed is True
        assert result.stored_count == 1


class TestNonVacuity:
    """Control: neutralise the brake and prove writes then happen, so the
    denial tests above cannot pass vacuously."""

    async def test_neutralised_brake_allows_the_write(self, daemon, monkeypatch):
        brake = ParkingBrake(BrakeStorage(daemon.mem.db_path))
        brake.engage("training")

        async def _never_blocked(*args, **kwargs):
            return False

        monkeypatch.setattr(
            "bartholomew.orchestrator.safety.governance_store.is_blocked_fail_closed_off_loop",
            _never_blocked,
        )
        try:
            result = await run_training_through_runtime_contract(daemon, _submission())
        finally:
            brake.disengage()

        assert result.governance_allowed is True
        assert result.stored_count == 1


# ---------------------------------------------------------------------------
# 3. Provenance integrity (design Sec.5.3) and the S5.2/S5.4 boundary.
# ---------------------------------------------------------------------------
class TestProvenanceIntegrity:
    async def test_caller_supplied_recorded_by_and_at_are_overwritten(self, daemon):
        record = _heuristic()
        record.envelope.provenance = Provenance(
            source_type="user_instruction",
            detail="caller's own claim",
            recorded_by="reflection",  # a lie: not from reflection
            recorded_at="1999-01-01T00:00:00Z",  # backdated
        )

        await run_training_through_runtime_contract(
            daemon,
            _submission([record]),
            recorded_by="user",
        )

        row = await daemon.mem.get_memory("competency_heuristic", HEURISTIC_KEY)
        provenance = json.loads(row["value"])["provenance"]

        assert provenance["recorded_by"] == "user", "caller was able to forge recorded_by"
        assert not provenance["recorded_at"].startswith(
            "1999",
        ), "caller was able to backdate recorded_at"
        # Caller-owned fields survive, as designed.
        assert provenance["source_type"] == "user_instruction"
        assert provenance["detail"] == "user stated this in conversation"

    @pytest.mark.parametrize("source_type", ["experience", "system_observation"])
    async def test_s5_4_reserved_source_types_are_rejected(self, daemon, source_type):
        result = await run_training_through_runtime_contract(
            daemon,
            _submission(source_type=source_type),
        )

        assert result.errors, "an S5.4-reserved source type was accepted by the S5.2 seam"
        assert any("S5.4" in error for error in result.errors)
        assert result.outcomes == []
        assert await daemon.mem.get_memory("competency_heuristic", HEURISTIC_KEY) is None

    async def test_invalid_recorded_by_is_rejected(self, daemon):
        result = await run_training_through_runtime_contract(
            daemon,
            _submission(),
            recorded_by="not_a_valid_actor",
        )
        assert result.errors
        assert result.outcomes == []

    async def test_records_must_belong_to_the_named_competency(self, daemon):
        stray = CompetencyHeuristic(
            envelope=CompetencyEnvelope(competency_id="vehicle_management"),
            slug="oil_change",
            rule="Change oil every 10000km",
        )
        result = await run_training_through_runtime_contract(daemon, _submission([stray]))

        assert result.errors
        assert result.outcomes == []


# ---------------------------------------------------------------------------
# 4. Consent: Decision C's per-record independence.
# ---------------------------------------------------------------------------
class TestConsent:
    async def test_consent_tripping_record_is_queued_not_stored(self, daemon):
        sensitive = CompetencyHeuristic(
            envelope=_envelope(),
            slug="utility_account",
            rule="The electricity account number is 12345678 and the password is hunter2",
        )
        result = await run_training_through_runtime_contract(daemon, _submission([sensitive]))

        assert result.stored_count == 0
        assert result.queued_count == 1
        assert result.outcomes[0].outcome == training.OUTCOME_QUEUED_FOR_CONSENT

        pending = await daemon.mem.list_pending_sensitive_writes()
        assert any(entry["key"] == f"{COMPETENCY_ID}.utility_account" for entry in pending)

    async def test_one_flagged_record_does_not_block_the_others(self, daemon):
        """Decision C: per-record independence -- an innocuous record must not
        be held hostage to an unrelated record awaiting consent."""
        sensitive = CompetencyHeuristic(
            envelope=_envelope(),
            slug="utility_account",
            rule="The account number is 12345678 and the password is hunter2",
        )
        result = await run_training_through_runtime_contract(
            daemon,
            _submission([_heuristic(), sensitive]),
        )

        assert result.stored_count == 1
        assert result.queued_count == 1
        assert result.all_stored is False

        # The innocuous one really did land.
        assert await daemon.mem.get_memory("competency_heuristic", HEURISTIC_KEY) is not None

    async def test_queued_outcome_is_distinguished_from_stored(self, daemon):
        """A user told 'trained' about a record sitting in the consent inbox
        would be misinformed about what Bartholomew actually knows."""
        sensitive = CompetencyHeuristic(
            envelope=_envelope(),
            slug="pw",
            rule="the password is hunter2",
        )
        result = await run_training_through_runtime_contract(daemon, _submission([sensitive]))

        assert result.to_dict()["summary"]["queued_for_consent"] == 1
        assert result.to_dict()["summary"]["stored"] == 0
        assert result.outcomes[0].detail and "inbox" in result.outcomes[0].detail


# ---------------------------------------------------------------------------
# 5. Supersession (design Sec.13.4).
# ---------------------------------------------------------------------------
class TestSupersession:
    async def test_retraining_increments_revision_and_records_supersession(self, daemon):
        await run_training_through_runtime_contract(daemon, _submission())

        revised = _heuristic(rule="Check warranty AND service history before replacing")
        result = await run_training_through_runtime_contract(daemon, _submission([revised]))

        outcome = result.outcomes[0]
        assert outcome.superseded_revision == 1
        assert outcome.revision == 2

        row = await daemon.mem.get_memory("competency_heuristic", HEURISTIC_KEY)
        stored = json.loads(row["value"])
        assert stored["revision"] == 2
        assert stored["rule"] == "Check warranty AND service history before replacing"

    async def test_first_write_is_revision_one_with_no_supersession(self, daemon):
        result = await run_training_through_runtime_contract(daemon, _submission())
        assert result.outcomes[0].revision == 1
        assert result.outcomes[0].superseded_revision is None

    async def test_superseded_provenance_is_preserved_in_the_reflection(self, daemon):
        """`memories` is current-state-only, so the superseded claim's
        provenance survives only in the append-only reflections table."""
        from bartholomew.kernel.reflection import REFLECTION_KIND

        await run_training_through_runtime_contract(daemon, _submission())
        await run_training_through_runtime_contract(
            daemon,
            _submission([_heuristic(rule="revised rule")]),
        )

        reflection = await daemon.mem.latest_reflection(REFLECTION_KIND)
        meta = reflection["meta"]
        if isinstance(meta, str):
            meta = json.loads(meta)
        # ActionReflection.to_memory_row() flattens `details` into meta.
        supersessions = meta["supersessions"]

        assert len(supersessions) == 1
        assert supersessions[0]["superseded_revision"] == 1
        assert supersessions[0]["new_revision"] == 2
        assert supersessions[0]["superseded_provenance"] is not None


# ---------------------------------------------------------------------------
# 6. Pre-existing defect, deliberately NOT worked around.
# ---------------------------------------------------------------------------
class TestPrivacyGuardNameKeyFalsePositive:
    """
    `privacy_guard.is_sensitive()` substring-scans the entire serialised
    value with no structural awareness, and `SENSITIVE_KEYWORDS` contains
    "name". The `competency` and `competency_procedure` JSON shapes both have
    a `"name"` key, so **every** record of those two kinds is treated as
    sensitive regardless of content, and is queued for consent rather than
    stored.

    Pre-existing (found and documented during S5.1, which used
    `skip_privacy_guard=True` in its *tests* and explicitly did not address
    it). S5.2 does NOT work around it: passing `skip_privacy_guard=True` from
    the production seam would be a consent bypass, which the approved design
    forbids outright. These tests pin the real current behaviour so it is
    visible rather than surprising, and will need updating when the
    underlying defect is fixed under its own authorisation.
    """

    @pytest.mark.parametrize(
        ("record_factory", "kind", "key"),
        [
            (lambda: _procedure(), "competency_procedure", PROCEDURE_KEY),
            (
                lambda: CompetencyRecord(envelope=_envelope(), name="Estate Management"),
                "competency",
                COMPETENCY_ID,
            ),
        ],
    )
    async def test_name_bearing_kinds_are_queued_not_stored(
        self,
        daemon,
        record_factory,
        kind,
        key,
    ):
        result = await run_training_through_runtime_contract(
            daemon,
            _submission([record_factory()]),
        )

        assert result.stored_count == 0
        assert result.queued_count == 1
        assert await daemon.mem.get_memory(kind, key) is None

        pending = await daemon.mem.list_pending_sensitive_writes()
        entry = next(e for e in pending if e["key"] == key)
        assert entry["reason"] == "privacy_guard"

    def test_the_trigger_really_is_the_json_key_not_the_content(self):
        """Pins the cause, so a future fix can be verified against the right
        thing: it is the literal `"name"` JSON key, not user content."""
        from bartholomew.kernel.memory.privacy_guard import SENSITIVE_KEYWORDS, is_sensitive

        assert "name" in SENSITIVE_KEYWORDS

        benign = _procedure(steps=["Get three quotes"])
        serialised = json.dumps(benign.to_dict())
        assert '"name"' in serialised
        assert is_sensitive(serialised) is True

        # The same record's actual human-meaningful content is not sensitive.
        assert is_sensitive("Quote comparison Get three quotes") is False


# ---------------------------------------------------------------------------
# 7. Structural invariants.
# ---------------------------------------------------------------------------
class TestStructuralInvariants:
    def test_training_module_performs_no_io(self):
        """training.py is pure data and validation, mirroring competency.py's
        discipline -- MemoryStore stays the only writer."""
        source = Path(rc.__file__).parent / "training.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))

        forbidden = {"sqlite3", "aiosqlite", "os", "requests", "httpx"}
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])

        assert not (imported & forbidden), (
            f"training.py imports I/O machinery {sorted(imported & forbidden)} -- it must "
            "stay pure data/validation so MemoryStore remains the sole write authority"
        )

    def test_seam_does_not_assume_a_human_author(self):
        """Constraint 1: the seam consumes structured records, never
        keystrokes. TrainingSubmission must expose no field that only a
        human-driven UI could supply."""
        fields = training.TrainingSubmission.__dataclass_fields__
        assert set(fields) == {"competency_id", "source_type", "source_detail", "records"}

    def test_reserved_source_types_are_disjoint_from_allowed(self):
        assert not (training.ALLOWED_TRAINING_SOURCE_TYPES & training.S5_4_RESERVED_SOURCE_TYPES)

    def test_seam_never_passes_skip_privacy_guard(self):
        """The production seam must not bypass the consent gate -- an
        explicit invariant of the approved design."""
        source = Path(rc.__file__).read_text(encoding="utf-8")
        seam_start = source.index("async def run_training_through_runtime_contract")
        seam_source = source[seam_start:]

        assert "skip_privacy_guard" not in seam_source
        assert "skip_rule_consent" not in seam_source
