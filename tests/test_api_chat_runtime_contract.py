"""
End-to-end test for MASTER_PLAN.md's "P2.5 -- Runtime Convergence" item
11.4, against the *actual live* /api/chat route (not just the underlying
run_chat_through_runtime_contract() function) -- learned directly from item
11.2's regression, which pytest alone didn't catch because it only exercised
the underlying function, not the wired-up live app.
"""

from __future__ import annotations

import os
import pathlib
import tempfile

import pytest
from fastapi.testclient import TestClient

# Set env vars before importing app -- gives this test module its own
# isolated DB, matching tests/test_stage0_alive.py's established pattern
# (a shared default DB path across test modules that each start a
# KernelDaemon deadlocks on file locks -- see MASTER_PLAN.md's P0 write-up).
_db_dir = pathlib.Path(tempfile.mkdtemp()) / "data"
_db_dir.mkdir(parents=True, exist_ok=True)
_DB_PATH = str(_db_dir / "test.db")
os.environ["BARTH_DB_PATH"] = _DB_PATH

from bartholomew_api_bridge_v0_1.services.api import app as app_module  # noqa: E402


@pytest.fixture(scope="module")
def client():
    # Re-assert right before starting the app -- see
    # tests/test_api_admission_gate.py's client fixture for why (pytest
    # collects/imports every test module, running each one's own
    # os.environ[...] assignment, before running any test; a
    # later-collected module can overwrite this by the time this fixture
    # actually fires).
    os.environ["BARTH_DB_PATH"] = _DB_PATH
    with TestClient(app_module.app) as c:
        # Pin the model backend to the stub for this module.
        #
        # startup() now rebuilds the orchestrator with the Identity the
        # daemon loaded, so chat reaches a real local model when one is
        # available. These tests were written against the stub and
        # deliberately depend on its behaviour -- specifically that it echoes
        # its prompt back, which is what lets
        # test_chat_references_persisted_experience_kernel_state observe the
        # persisted goal in the HTTP response.
        #
        # Without this pin the module would pass in CI (no Ollama) and fail
        # on exactly the machines where the real model path is being
        # exercised -- the tester's. Pinning keeps the assertion about
        # end-to-end wiring, which is what it is really testing, rather than
        # about which model happens to be installed. Real-model behaviour is
        # covered separately by tests/test_real_model_path_wiring.py and
        # tests/test_model_backend_honesty.py.
        from identity_interpreter.orchestrator.orchestrator import Orchestrator

        app_module.orch = Orchestrator()
        yield c


@pytest.mark.smoke
def test_chat_endpoint_responds_and_updates_working_memory(client):
    wm_count_before = len(app_module._kernel.working_memory.get_all())

    response = client.post("/api/chat", json={"message": "hello Bartholomew"})

    assert response.status_code == 200
    body = response.json()
    assert body["reply"]

    wm_count_after = len(app_module._kernel.working_memory.get_all())
    assert wm_count_after == wm_count_before + 1


def test_chat_references_persisted_experience_kernel_state(client):
    app_module._kernel.experience.add_goal("prepare the release notes")

    response = client.post("/api/chat", json={"message": "what's on my plate?"})

    assert response.status_code == 200
    # The stub orchestrator/mock-LLM echoes its prompt, so the goal we just
    # persisted -- via a route Working Memory has no direct knowledge of --
    # shows up in the actual HTTP response, proving live end-to-end wiring.
    assert "prepare the release notes" in response.json()["reply"]


def test_chat_returns_503_when_parking_brake_engaged(client):
    # Phase B stage B6: the runtime's Governance check reads through
    # GovernanceStore directly (the B4-B6 dual-check bridge against the
    # legacy ParkingBrake/system_flags path was retired) -- engage the
    # daemon's actual shared instance, not a standalone legacy brake.
    store = app_module._kernel.governance_store
    store.engage("skills")
    try:
        response = client.post("/api/chat", json={"message": "are you there?"})
        assert response.status_code == 503
    finally:
        store.disengage()

    # Confirm normal service resumes after disengage.
    response = client.post("/api/chat", json={"message": "back online?"})
    assert response.status_code == 200
