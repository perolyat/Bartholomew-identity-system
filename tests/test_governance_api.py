"""
HTTP-level tests for bartholomew_api_bridge_v0_1's governance router
(/api/governance/brake, /api/governance/brake/engage,
/api/governance/brake/disengage) -- Stage 1, S1.1.

Follows the same pattern as tests/test_self_state_api.py: a module-scoped
TestClient over the real app (real KernelDaemon startup, real
kernel.governance_store), with its own isolated tmp DB so it doesn't
collide with other test modules' shared BARTH_DB_PATH state.
"""

from __future__ import annotations

import os
import pathlib
import sqlite3
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
    with TestClient(app_module.app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_brake(client):
    """Every test starts and ends with the brake disengaged, so tests
    don't leak brake state into each other or into other test modules
    (governance_store is a process-wide singleton on app_module._kernel,
    same sharing hazard test_self_state_api.py's goal tests document)."""
    client.post("/api/governance/brake/disengage", json={"reason": "test setup"})
    yield
    client.post("/api/governance/brake/disengage", json={"reason": "test teardown"})


def test_get_brake_status_defaults_to_disengaged(client):
    response = client.get("/api/governance/brake")
    assert response.status_code == 200
    body = response.json()
    assert body["engaged"] is False
    assert body["scopes"] == []


def test_engage_sets_engaged_scopes_and_increments_revision(client):
    before = client.get("/api/governance/brake").json()["revision"]

    response = client.post(
        "/api/governance/brake/engage",
        json={"scopes": ["skills", "sight"], "reason": "test lockdown"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["engaged"] is True
    assert body["scopes"] == ["sight", "skills"]
    assert body["revision"] == before + 1


def test_engage_default_scope_is_global(client):
    response = client.post("/api/governance/brake/engage", json={})
    assert response.status_code == 200
    assert response.json()["scopes"] == ["global"]


def test_engage_rejects_unknown_scope(client):
    response = client.post(
        "/api/governance/brake/engage",
        json={"scopes": ["not_a_real_scope"]},
    )
    assert response.status_code == 400


def test_disengage_clears_engaged_and_scopes(client):
    client.post("/api/governance/brake/engage", json={"scopes": ["skills"]})

    response = client.post(
        "/api/governance/brake/disengage",
        json={"reason": "incident resolved"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["engaged"] is False
    assert body["scopes"] == []


def test_disengage_rejects_stale_expected_revision(client):
    client.post("/api/governance/brake/engage", json={"scopes": ["skills"]})
    current = client.get("/api/governance/brake").json()["revision"]

    response = client.post(
        "/api/governance/brake/disengage",
        json={"reason": "stale attempt", "expected_revision": current - 1},
    )

    assert response.status_code == 409


def test_engage_and_disengage_record_actor_in_audit_trail(client):
    client.post(
        "/api/governance/brake/engage",
        json={"scopes": ["skills"], "reason": "actor check", "actor": "test-actor"},
    )

    conn = sqlite3.connect(_DB_PATH)
    try:
        row = conn.execute(
            "SELECT action, actor FROM governance_audit WHERE reason = 'actor check'",
        ).fetchone()
    finally:
        conn.close()
    assert row == ("engaged", "test-actor")


def test_engage_default_actor_is_user(client):
    client.post(
        "/api/governance/brake/engage",
        json={"scopes": ["skills"], "reason": "default actor check"},
    )

    conn = sqlite3.connect(_DB_PATH)
    try:
        actor = conn.execute(
            "SELECT actor FROM governance_audit WHERE reason = 'default actor check'",
        ).fetchone()[0]
    finally:
        conn.close()
    assert actor == "user"
