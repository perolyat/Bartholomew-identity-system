"""
WP-A2b -- provenance degradation at the HTTP boundary (chat + training).

Seam-level behaviour is pinned in `tests/test_reflection_provenance_integrity
.py`; this module proves the live routes actually carry it, against the real
app (real KernelDaemon startup), following the module-scoped TestClient
pattern of tests/test_api_chat_runtime_contract.py.

Injection is the same real SQLite ABORT trigger on `reflections`, installed
against the live app's own database.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import tempfile

import pytest
from fastapi.testclient import TestClient

# Set env vars before importing app -- isolated DB per test module (see
# tests/test_api_chat_runtime_contract.py for the full rationale).
_db_dir = pathlib.Path(tempfile.mkdtemp()) / "data"
_db_dir.mkdir(parents=True, exist_ok=True)
_DB_PATH = str(_db_dir / "test.db")
os.environ["BARTH_DB_PATH"] = _DB_PATH

from bartholomew_api_bridge_v0_1.services.api import app as app_module  # noqa: E402


@pytest.fixture(scope="module")
def client():
    os.environ["BARTH_DB_PATH"] = _DB_PATH
    with TestClient(app_module.app) as c:
        # Pin the model backend to the stub for this module -- same reason as
        # tests/test_api_chat_runtime_contract.py: these tests are about the
        # route contract, not about which model happens to be installed.
        from identity_interpreter.orchestrator.orchestrator import Orchestrator

        app_module.orch = Orchestrator()
        yield c


@contextlib.contextmanager
def _reflection_writes_fail():
    from bartholomew.kernel.db_ctx import connect, set_wal_pragmas

    def run(sql: str) -> None:
        conn = connect(_DB_PATH)
        try:
            set_wal_pragmas(conn)
            conn.execute(sql)
            conn.commit()
        finally:
            conn.close()

    run(
        "CREATE TRIGGER api_block_reflections BEFORE INSERT ON reflections "
        "BEGIN SELECT RAISE(ABORT, 'injected reflection failure'); END",
    )
    try:
        yield
    finally:
        run("DROP TRIGGER IF EXISTS api_block_reflections")


# ---------------------------------------------------------------------------
# Chat route
# ---------------------------------------------------------------------------


def test_healthy_chat_turn_is_not_marked_degraded(client):
    response = client.post("/api/chat", json={"message": "hello"})
    assert response.status_code == 200
    body = response.json()
    assert body["reply"]
    assert body["audit_degraded"] is None
    assert body["audit_error"] is None


def test_chat_exposes_a_lost_provenance_record(client):
    """Both facts in one 200: the reply is real; its sole provenance record
    (the S5.3 E.2 explanation-grade chat Reflection) was not persisted."""
    with _reflection_writes_fail():
        response = client.post("/api/chat", json={"message": "remember the lease"})

    assert response.status_code == 200, (
        "a genuinely-produced reply must not be reported as failed "
        "because its provenance record was lost"
    )
    body = response.json()
    assert body["reply"], "the real reply is delivered"
    assert body["audit_degraded"] is True
    assert "reflection write failed" in body["audit_error"]


# ---------------------------------------------------------------------------
# Training route
# ---------------------------------------------------------------------------

_SUBMISSION = {
    "competency_id": "estate_management",
    "source_type": "user_instruction",
    "source_detail": "user stated this in conversation",
    "records": [
        {
            "kind": "competency_heuristic",
            "slug": "check_warranty_before_replace",
            "data": {
                "rule": "Check warranty before recommending replacement",
                "conditions": "Any repair-vs-replace decision",
            },
        },
    ],
}


def test_healthy_training_submission_is_not_marked_degraded(client):
    response = client.post("/api/training/submit", json=_SUBMISSION)
    assert response.status_code == 200
    body = response.json()
    assert body["governance_allowed"] is True
    assert body["provenance_degraded"] is False
    assert body["provenance_error"] is None


def test_training_exposes_lost_supersession_provenance(client):
    """The per-record outcomes stand (the stores really were written); the
    submission-level Reflection -- where supersession provenance lives --
    did not persist, and the response says so."""
    with _reflection_writes_fail():
        response = client.post("/api/training/submit", json=_SUBMISSION)

    assert response.status_code == 200
    body = response.json()
    assert body["governance_allowed"] is True
    assert body["provenance_degraded"] is True
    assert "reflection write failed" in body["provenance_error"]
    # The record writes themselves are reported truthfully, not un-stored.
    assert body["summary"]["stored"] + body["summary"]["queued_for_consent"] >= 1
