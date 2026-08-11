"""
S5.2 Step 3 (surfaces + demonstration) tests.

Two things:

1. The API surface (Decision B) forwards to the one governed seam and sets
   `recorded_by` itself rather than trusting the request body.
2. The Estate Management end-to-end demonstration `ROADMAP.md`'s "Estate
   Management as architecture acceptance test" sequence calls for at S5.2:
   train a real worked competency through the real governed path.

The demonstration deliberately trains Estate Management *as a competency*.
No Estate-specific kind, table, executive, or code path exists or is
created -- that is the acceptance principle the roadmap states, and this is
where it is exercised rather than merely asserted.

The API fixture follows `tests/test_consent_api.py`'s established pattern
(module-level BARTH_DB_PATH + a real app startup via TestClient's context
manager) rather than injecting a fake kernel: B7's admission middleware
gates every ingress on a real `lifecycle_state`, so a fake would be testing
a different app than production runs.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_db_dir = Path(tempfile.mkdtemp(prefix="bartho_training_api_"))
_DB_PATH = str(_db_dir / "test.db")
os.environ["BARTH_DB_PATH"] = _DB_PATH

from bartholomew.kernel import training  # noqa: E402
from bartholomew.kernel.competency import (  # noqa: E402
    CompetencyEnvelope,
    CompetencyEvidence,
    CompetencyHeuristic,
    CompetencyKnowledge,
)
from bartholomew.kernel.memory.privacy_guard import set_consent_handler  # noqa: E402
from bartholomew.kernel.memory_store import MemoryStore  # noqa: E402
from bartholomew.kernel.runtime_contract import (  # noqa: E402
    run_training_through_runtime_contract,
)
from bartholomew_api_bridge_v0_1.services.api import app as app_module  # noqa: E402

COMPETENCY_ID = "estate_management"


@pytest.fixture(autouse=True)
def _reset_consent_handler():
    set_consent_handler(None)
    yield
    set_consent_handler(None)


# ---------------------------------------------------------------------------
# 1. API surface.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def client():
    # Re-assert immediately before app startup -- see test_consent_api.py's
    # own fixture for why (another test module's import-time assignment can
    # land in between).
    os.environ["BARTH_DB_PATH"] = _DB_PATH
    with TestClient(app_module.app) as c:
        yield c


def _read_memory(kind: str, key: str):
    """Read a memory from sync test code. MemoryStore opens a connection per
    call, so a fresh loop here is safe."""
    return asyncio.run(app_module._kernel.mem.get_memory(kind, key))


class TestTrainingAPI:
    def test_source_types_endpoint_reports_the_boundary(self, client):
        response = client.get("/api/training/source-types")
        assert response.status_code == 200

        body = response.json()
        assert set(body["allowed"]) == set(training.ALLOWED_TRAINING_SOURCE_TYPES)
        assert set(body["reserved_for_s5_4"]) == set(training.S5_4_RESERVED_SOURCE_TYPES)
        assert len(body["kinds"]) == 5

    def test_submit_stores_a_record(self, client):
        response = client.post(
            "/api/training/submit",
            json={
                "competency_id": COMPETENCY_ID,
                "source_type": "user_instruction",
                "source_detail": "stated by the user",
                "records": [
                    {
                        "kind": "competency_heuristic",
                        "slug": "check_warranty",
                        "data": {"rule": "Check warranty before replacing"},
                    },
                ],
            },
        )
        assert response.status_code == 200, response.text

        body = response.json()
        assert body["summary"]["stored"] == 1
        assert body["outcomes"][0]["key"] == f"{COMPETENCY_ID}.check_warranty"

    def test_request_body_cannot_forge_recorded_by(self, client):
        """The route sets recorded_by itself (design Sec.5.3); body fields
        claiming otherwise must have no effect."""
        response = client.post(
            "/api/training/submit",
            json={
                "competency_id": COMPETENCY_ID,
                "source_type": "user_instruction",
                "source_detail": "stated by the user",
                "recorded_by": "reflection",
                "records": [
                    {
                        "kind": "competency_heuristic",
                        "slug": "forge_attempt",
                        "data": {
                            "rule": "some rule",
                            "provenance": {
                                "source_type": "user_instruction",
                                "recorded_by": "reflection",
                                "recorded_at": "1999-01-01T00:00:00Z",
                            },
                        },
                    },
                ],
            },
        )
        assert response.status_code == 200, response.text

        row = _read_memory("competency_heuristic", f"{COMPETENCY_ID}.forge_attempt")
        assert row is not None

        provenance = json.loads(row["value"])["provenance"]
        assert provenance["recorded_by"] == "user", "request body forged recorded_by"
        assert not provenance["recorded_at"].startswith("1999"), "request body backdated the write"

    def test_s5_4_reserved_source_type_is_refused(self, client):
        response = client.post(
            "/api/training/submit",
            json={
                "competency_id": COMPETENCY_ID,
                "source_type": "experience",
                "source_detail": "observed outcome",
                "records": [
                    {"kind": "competency_heuristic", "slug": "s", "data": {"rule": "r"}},
                ],
            },
        )
        assert response.status_code == 400
        assert "S5.4" in response.text

    def test_unknown_kind_is_refused(self, client):
        response = client.post(
            "/api/training/submit",
            json={
                "competency_id": COMPETENCY_ID,
                "source_type": "user_instruction",
                "source_detail": "d",
                "records": [{"kind": "estate_special_case", "slug": "s", "data": {}}],
            },
        )
        assert response.status_code == 400
        assert "unknown competency kind" in response.text

    def test_empty_submission_is_refused(self, client):
        response = client.post(
            "/api/training/submit",
            json={
                "competency_id": COMPETENCY_ID,
                "source_type": "user_instruction",
                "source_detail": "d",
                "records": [],
            },
        )
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# 2. Estate Management end-to-end demonstration.
# ---------------------------------------------------------------------------
class _DemoDaemon:
    def __init__(self, mem: MemoryStore):
        self.mem = mem


@pytest.fixture
async def daemon(tmp_path):
    mem = MemoryStore(str(tmp_path / "estate_demo.db"))
    await mem.init()
    yield _DemoDaemon(mem)
    await mem.close()


class TestEstateManagementDemonstration:
    """
    ROADMAP.md's sequence: "Train Residential Estate Management into it
    (S5.2 onward)". This trains a real worked competency through the real
    governed seam -- knowledge, a heuristic, and evidence -- and reads it
    back.

    Deliberately NOT included: any Estate Management production
    functionality (reading property documents, contacting contractors,
    taking real-world action). ROADMAP.md places that explicitly out of
    scope for S5.1-S5.4.
    """

    def _envelope(self, classification="personal"):
        return CompetencyEnvelope(competency_id=COMPETENCY_ID, classification=classification)

    async def test_estate_management_trains_end_to_end(self, daemon):
        records = [
            CompetencyKnowledge(
                envelope=self._envelope(),
                slug="boiler_service_interval",
                topic="Heating systems",
                content="The boiler should be serviced annually, before winter.",
            ),
            CompetencyHeuristic(
                envelope=self._envelope(classification="potentially_generalisable"),
                slug="check_warranty_before_replace",
                rule="Check the warranty before recommending replacement.",
                conditions="Any repair-vs-replace decision on a covered appliance.",
            ),
            CompetencyEvidence(
                envelope=self._envelope(),
                slug="boiler_2026_repair",
                situation="Boiler stopped producing hot water in June 2026.",
                action_taken="Compared three quotes; checked warranty first.",
                outcome="Repaired under warranty at no cost.",
                judgement_was_correct=True,
                lesson="Checking warranty first avoided an unnecessary replacement.",
            ),
        ]

        submission = training.TrainingSubmission(
            competency_id=COMPETENCY_ID,
            source_type="user_instruction",
            source_detail="Estate Management worked example, trained via the S5.2 seam",
            records=records,
        )

        result = await run_training_through_runtime_contract(daemon, submission)

        assert result.governance_allowed is True
        assert result.errors == []
        assert result.stored_count == 3, result.to_dict()

        knowledge = await daemon.mem.get_memory(
            "competency_knowledge",
            f"{COMPETENCY_ID}.boiler_service_interval",
        )
        restored = CompetencyKnowledge.from_dict(
            json.loads(knowledge["value"]),
            slug="boiler_service_interval",
        )
        assert restored.validate() == []
        assert restored.topic == "Heating systems"

        evidence = await daemon.mem.get_memory(
            "competency_evidence",
            f"{COMPETENCY_ID}.boiler_2026_repair",
        )
        restored_evidence = CompetencyEvidence.from_dict(
            json.loads(evidence["value"]),
            slug="boiler_2026_repair",
        )
        assert restored_evidence.judgement_was_correct is True
        assert restored_evidence.envelope.provenance.recorded_by == "user"

    async def test_classification_is_recorded_verbatim_and_acts_on_nothing(self, daemon):
        """`potentially_generalisable` is a candidacy marker only: stored as
        written, triggering no promotion, export, or transport (S5.1 design
        Sec.5; CONSTITUTION.md)."""
        record = CompetencyHeuristic(
            envelope=self._envelope(classification="potentially_generalisable"),
            slug="generalisable_rule",
            rule="Compare at least three quotes before accepting one.",
        )
        submission = training.TrainingSubmission(
            competency_id=COMPETENCY_ID,
            source_type="user_instruction",
            source_detail="worked example",
            records=[record],
        )

        await run_training_through_runtime_contract(daemon, submission)

        row = await daemon.mem.get_memory(
            "competency_heuristic",
            f"{COMPETENCY_ID}.generalisable_rule",
        )
        assert json.loads(row["value"])["classification"] == "potentially_generalisable"

    async def test_the_seam_never_infers_a_broader_classification(self, daemon):
        """The seam must never upgrade a record's classification on its own
        (design Sec.7.3)."""
        record = CompetencyHeuristic(
            envelope=self._envelope(),  # defaults to "personal"
            slug="stays_personal",
            rule="This contractor is reliable.",
        )
        submission = training.TrainingSubmission(
            competency_id=COMPETENCY_ID,
            source_type="user_instruction",
            source_detail="worked example",
            records=[record],
        )

        await run_training_through_runtime_contract(daemon, submission)

        row = await daemon.mem.get_memory(
            "competency_heuristic",
            f"{COMPETENCY_ID}.stays_personal",
        )
        assert json.loads(row["value"])["classification"] == "personal"

    async def test_no_estate_specific_kind_was_introduced(self):
        """The acceptance principle: Estate Management is a competency, not
        a special case in the data model."""
        assert set(training.RECORD_CLASSES) == {
            "competency",
            "competency_knowledge",
            "competency_procedure",
            "competency_heuristic",
            "competency_evidence",
        }
