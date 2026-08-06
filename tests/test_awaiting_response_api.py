"""
HTTP-level tests for bartholomew_api_bridge_v0_1's awaiting-response router
(/api/awaiting-response, .../{id}/resolve, .../{id}/audit) -- Stage 1, S1.4.
Follows the same pattern as tests/test_consent_api.py: a module-scoped
TestClient over the real app (real KernelDaemon startup, real
Identity.yaml -- proving the S1.4 tool_use.allowlist additions are actually
sufficient for these routes to work against production config, not just a
permissive test IdentityContext).
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
    # tests/test_api_admission_gate.py's client fixture for why (pytest
    # collects/imports every test module, running each one's own
    # os.environ[...] assignment, before running any test; a
    # later-collected module can overwrite this by the time this fixture
    # actually fires).
    os.environ["BARTH_DB_PATH"] = _DB_PATH
    with TestClient(app_module.app) as c:
        yield c


def _open(client, subject: str, **kwargs) -> dict:
    body = {"subject": subject, "origin_surface": "chat", "actor": "user", **kwargs}
    response = client.post("/api/awaiting-response", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_list_starts_empty_or_contains_only_prior_test_entries(client):
    response = client.get("/api/awaiting-response")
    assert response.status_code == 200
    body = response.json()
    assert "entries" in body
    assert "count" in body


def test_open_creates_an_entry_reachable_via_list(client):
    entry = _open(client, "api reply about the trip")
    assert entry["status"] == "open"
    assert entry["subject"] == "api reply about the trip"

    entries = client.get("/api/awaiting-response?status=open").json()["entries"]
    assert any(e["id"] == entry["id"] for e in entries)


def test_open_rejects_unknown_origin_surface(client):
    response = client.post(
        "/api/awaiting-response",
        json={"subject": "x", "origin_surface": "voice"},
    )
    assert response.status_code == 400


def test_open_rejects_empty_subject(client):
    """PR #38 review finding: an empty subject previously reached the
    Runtime Contract seam's own precondition (a bare ValueError), turning
    ordinary invalid client input into an HTTP 500 instead of a 4xx."""
    response = client.post(
        "/api/awaiting-response",
        json={"subject": "", "origin_surface": "chat"},
    )
    assert response.status_code == 422


def test_open_rejects_whitespace_only_subject(client):
    response = client.post(
        "/api/awaiting-response",
        json={"subject": "   ", "origin_surface": "chat"},
    )
    assert response.status_code == 422


def test_open_rejects_malformed_due_at(client):
    """PR #38 review finding: a malformed due_at was previously accepted
    and persisted unchanged, then silently excluded from
    list_due_for_transition() forever (since _parse_iso() couldn't parse
    it) -- now rejected outright at the request boundary."""
    response = client.post(
        "/api/awaiting-response",
        json={"subject": "x", "origin_surface": "chat", "due_at": "not-a-timestamp"},
    )
    assert response.status_code == 422


def test_open_normalizes_due_at_with_explicit_offset(client):
    """A valid RFC 3339 timestamp using an explicit offset (rather than the
    store's exact 'Z'-suffixed form) must be normalized, not silently
    accepted-and-then-ignored (PR #38 review finding)."""
    entry = _open(client, "api due_at offset", due_at="2026-08-07T12:00:00+00:00")
    assert entry["due_at"] == "2026-08-07T12:00:00Z"


def test_resolve_clears_entry_from_open_list(client):
    entry = _open(client, "api resolve me")

    response = client.post(
        f"/api/awaiting-response/{entry['id']}/resolve",
        json={"resolution": "user_dismissed", "actor": "user"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "resolved"
    assert body["resolution"] == "user_dismissed"

    open_entries = client.get("/api/awaiting-response?status=open").json()["entries"]
    assert all(e["id"] != entry["id"] for e in open_entries)


def test_resolve_rejects_invalid_resolution_value(client):
    entry = _open(client, "api bad resolution")
    response = client.post(
        f"/api/awaiting-response/{entry['id']}/resolve",
        json={"resolution": "not_a_real_resolution"},
    )
    assert response.status_code == 400


def test_resolve_unknown_entry_returns_404(client):
    response = client.post(
        "/api/awaiting-response/999999/resolve",
        json={"resolution": "manual"},
    )
    assert response.status_code == 404


def test_resolve_already_resolved_entry_returns_409(client):
    entry = _open(client, "api double resolve")
    client.post(
        f"/api/awaiting-response/{entry['id']}/resolve",
        json={"resolution": "manual"},
    )

    response = client.post(
        f"/api/awaiting-response/{entry['id']}/resolve",
        json={"resolution": "manual"},
    )
    assert response.status_code == 409


def test_audit_trail_records_open_and_resolve(client):
    entry = _open(client, "api audit me")
    client.post(
        f"/api/awaiting-response/{entry['id']}/resolve",
        json={"resolution": "manual"},
    )

    response = client.get(f"/api/awaiting-response/{entry['id']}/audit")
    assert response.status_code == 200
    body = response.json()
    transitions = [row["transition"] for row in body["entries"]]
    assert transitions == ["resolved", "opened"]


def test_list_rejects_out_of_bounds_limit(client):
    response = client.get("/api/awaiting-response?limit=0")
    assert response.status_code == 400
    response = client.get("/api/awaiting-response?limit=101")
    assert response.status_code == 400


def test_list_rejects_unknown_status(client):
    response = client.get("/api/awaiting-response?status=not_a_real_status")
    assert response.status_code == 400
