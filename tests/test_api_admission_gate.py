"""
Phase B stage B7: the app.py HTTP admission middleware -- the single
chokepoint gating real external ingress (chat, self-state, etc.) while
leaving health/liveness/metrics/docs endpoints always-responsive. Against
the actual live app (TestClient + real KernelDaemon), not just the
RequestAdmission primitive in isolation (see
tests/test_request_admission.py) or the daemon-level drain sequence (see
tests/test_daemon_admission_drain.py). See
docs/B7_EXTERNAL_REQUEST_ADMISSION.md.
"""

from __future__ import annotations

import os
import pathlib
import tempfile

import pytest
from fastapi.testclient import TestClient

# Isolated DB per test module -- see test_api_chat_runtime_contract.py's
# same established pattern (a shared default DB path across test modules
# that each start a KernelDaemon deadlocks on Phase B stage B6's process
# lock, not just file locks).
_db_dir = pathlib.Path(tempfile.mkdtemp()) / "data"
_db_dir.mkdir(parents=True, exist_ok=True)
os.environ["BARTH_DB_PATH"] = str(_db_dir / "test.db")

from bartholomew.kernel.daemon import DaemonLifecycleState  # noqa: E402
from bartholomew_api_bridge_v0_1.services.api import app as app_module  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app_module.app) as c:
        yield c


def test_gated_route_responds_normally_while_running(client):
    response = client.get("/api/self")
    assert response.status_code == 200
    assert app_module._kernel.admission.admitted_count == 0  # released after the request


def test_admission_count_returns_to_zero_after_each_request(client):
    for _ in range(5):
        response = client.get("/api/liveness/self")
        assert response.status_code == 200
    assert app_module._kernel.admission.admitted_count == 0


def test_healthz_responds_even_when_admission_is_closed(client):
    app_module._kernel.admission.close()
    try:
        response = client.get("/healthz")
        assert response.status_code == 200
    finally:
        app_module._kernel.admission._closed = False


def test_liveness_route_responds_even_when_admission_is_closed(client):
    app_module._kernel.admission.close()
    try:
        response = client.get("/api/liveness/self")
        assert response.status_code == 200
    finally:
        app_module._kernel.admission._closed = False


def test_metrics_route_responds_even_when_lifecycle_state_is_not_running(client, monkeypatch):
    monkeypatch.setattr(app_module._kernel, "lifecycle_state", DaemonLifecycleState.STOPPING)
    response = client.get("/metrics")
    assert response.status_code == 200


def test_gated_route_refused_when_admission_is_closed(client):
    app_module._kernel.admission.close()
    try:
        response = client.get("/api/self")
        assert response.status_code == 503
    finally:
        app_module._kernel.admission._closed = False

    # Confirms the route works normally again once admission reopens.
    response = client.get("/api/self")
    assert response.status_code == 200


def test_gated_route_refused_when_lifecycle_state_is_not_running(client, monkeypatch):
    """Regression for the exact race this stage closes: a bare
    "_kernel is not None" check would let this through during STARTING or
    STOPPING -- the middleware must check lifecycle_state, not just
    presence."""
    monkeypatch.setattr(app_module._kernel, "lifecycle_state", DaemonLifecycleState.STOPPING)
    response = client.get("/api/self")
    assert response.status_code == 503


def test_chat_route_refused_when_admission_is_closed(client):
    app_module._kernel.admission.close()
    try:
        response = client.post("/api/chat", json={"message": "are you there?"})
        assert response.status_code == 503
    finally:
        app_module._kernel.admission._closed = False


def test_docs_route_responds_when_lifecycle_state_is_not_running(client, monkeypatch):
    monkeypatch.setattr(app_module._kernel, "lifecycle_state", DaemonLifecycleState.STOPPING)
    response = client.get("/openapi.json")
    assert response.status_code == 200
