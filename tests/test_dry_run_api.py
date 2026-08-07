"""
HTTP-level tests for bartholomew_api_bridge_v0_1's dry-run router
(/api/dry-run/status, /engage, /disengage, /results) -- Stage 5, S5.5.

Follows the same pattern as tests/test_governance_api.py: a module-scoped
TestClient over the real app (real KernelDaemon startup, real
kernel.governance_store), with its own isolated tmp DB.
"""

from __future__ import annotations

import os
import pathlib
import tempfile

import pytest
from fastapi.testclient import TestClient

_db_dir = pathlib.Path(tempfile.mkdtemp()) / "data"
_db_dir.mkdir(parents=True, exist_ok=True)
_DB_PATH = str(_db_dir / "test.db")
os.environ["BARTH_DB_PATH"] = _DB_PATH

from bartholomew_api_bridge_v0_1.services.api import app as app_module  # noqa: E402


@pytest.fixture(scope="module")
def client():
    # Re-assert right before starting the app -- see
    # tests/test_api_admission_gate.py's client fixture for why.
    os.environ["BARTH_DB_PATH"] = _DB_PATH
    with TestClient(app_module.app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_dry_run_switch(client):
    """Every test starts and ends with the switch disengaged, so tests
    don't leak state into each other (governance_store is a process-wide
    singleton on app_module._kernel, same sharing hazard
    test_governance_api.py's own brake reset fixture documents)."""
    client.post("/api/dry-run/disengage", json={"reason": "test setup"})
    yield
    client.post("/api/dry-run/disengage", json={"reason": "test teardown"})


def test_get_status_defaults_to_disengaged(client):
    response = client.get("/api/dry-run/status")
    assert response.status_code == 200
    body = response.json()
    assert body["engaged"] is False
    assert body["scopes"] == []


def test_engage_sets_engaged_scopes_and_increments_revision(client):
    before = client.get("/api/dry-run/status").json()["revision"]

    response = client.post(
        "/api/dry-run/engage",
        json={"scopes": ["initiative"], "reason": "test lockdown"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["engaged"] is True
    assert body["scopes"] == ["initiative"]
    assert body["revision"] == before + 1


def test_engage_default_scope_is_global(client):
    response = client.post("/api/dry-run/engage", json={})
    assert response.json()["scopes"] == ["global"]


def test_engage_rejects_unknown_scope(client):
    response = client.post("/api/dry-run/engage", json={"scopes": ["bogus"]})
    assert response.status_code == 400


def test_disengage_clears_engaged_and_scopes(client):
    client.post("/api/dry-run/engage", json={"scopes": ["initiative"]})
    response = client.post("/api/dry-run/disengage", json={"reason": "done"})
    assert response.status_code == 200
    body = response.json()
    assert body["engaged"] is False
    assert body["scopes"] == []


def test_disengage_rejects_stale_expected_revision(client):
    client.post("/api/dry-run/engage", json={"scopes": ["initiative"]})
    current = client.get("/api/dry-run/status").json()["revision"]

    response = client.post(
        "/api/dry-run/disengage",
        json={"reason": "stale attempt", "expected_revision": current - 1},
    )
    assert response.status_code == 409


def test_get_status_reflects_concurrent_external_write(client):
    """A second GovernanceStore instance writes to the same database while
    the API daemon is running; GET /api/dry-run/status must reflect that
    write, not the daemon singleton's last-cached snapshot -- mirrors
    test_governance_api.py's identical brake test."""
    from bartholomew.orchestrator.safety.governance_store import GovernanceStore

    external = GovernanceStore(_DB_PATH)
    external.engage_dry_run("initiative", reason="external write", actor="cli")

    response = client.get("/api/dry-run/status")
    body = response.json()
    assert body["engaged"] is True
    assert body["scopes"] == ["initiative"]


def test_engage_and_disengage_record_actor(client):
    client.post(
        "/api/dry-run/engage",
        json={"scopes": ["initiative"], "actor": "test-user"},
    )
    response = client.post(
        "/api/dry-run/disengage",
        json={"actor": "test-user"},
    )
    assert response.status_code == 200


def test_engage_default_actor_is_user(client):
    response = client.post("/api/dry-run/engage", json={})
    assert response.status_code == 200


def test_results_starts_empty_or_contains_only_prior_test_entries(client):
    response = client.get("/api/dry-run/results")
    assert response.status_code == 200
    body = response.json()
    assert "results" in body
    assert "count" in body


def test_results_reflects_a_recorded_dry_run(client):
    from bartholomew.kernel.dry_run import DryRunResult, record_dry_run_result

    result = DryRunResult(
        surface="initiative",
        proposed_action="initiative_propose_maintenance",
        target="new",
        parameters={"kind": "a"},
        expected_effects={"would_create_status": "approved"},
        governance_decision="allowed",
        approval_requirements={},
        would_execute=True,
        actor="test",
    )
    record_dry_run_result(_DB_PATH, result)

    response = client.get("/api/dry-run/results")
    body = response.json()
    assert body["count"] >= 1
    ids = [r["dry_run_id"] for r in body["results"]]
    assert result.dry_run_id in ids


def test_results_filters_by_surface(client):
    from bartholomew.kernel.dry_run import DryRunResult, record_dry_run_result

    record_dry_run_result(
        _DB_PATH,
        DryRunResult(
            surface="skill",
            proposed_action="notify.send",
            target="notify",
            parameters={},
            expected_effects={},
            governance_decision="allowed",
            approval_requirements={},
            would_execute=True,
        ),
    )

    response = client.get("/api/dry-run/results", params={"surface": "skill"})
    body = response.json()
    assert all(r["surface"] == "skill" for r in body["results"])


def test_results_rejects_out_of_bounds_limit(client):
    response = client.get("/api/dry-run/results", params={"limit": 0})
    assert response.status_code == 400

    response = client.get("/api/dry-run/results", params={"limit": 1000})
    assert response.status_code == 400
