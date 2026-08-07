"""
HTTP-level tests for bartholomew_api_bridge_v0_1's initiative-consent router
(/api/initiatives/consent, .../{category}) -- Stage 5, S5.3. Follows the
same pattern as tests/test_awaiting_response_api.py: a module-scoped
TestClient over the real app (real KernelDaemon startup).
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


def test_list_returns_all_six_categories_default_off(client):
    response = client.get("/api/initiatives/consent")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 6
    categories = {row["category"] for row in body["categories"]}
    assert categories == {
        "check_in",
        "reminder",
        "review",
        "next_best_action",
        "maintenance",
        "wellness",
    }
    # Untouched categories default to allowed=False, muted=False.
    review_row = next(row for row in body["categories"] if row["category"] == "review")
    assert review_row["allowed"] is False
    assert review_row["muted"] is False


def test_get_unknown_category_returns_400(client):
    response = client.get("/api/initiatives/consent/not_a_real_category")
    assert response.status_code == 400


def test_get_single_category_returns_defaults(client):
    response = client.get("/api/initiatives/consent/check_in")
    assert response.status_code == 200
    body = response.json()
    assert body["category"] == "check_in"
    assert body["allowed"] is False
    assert body["muted"] is False


def test_put_unknown_category_returns_400(client):
    response = client.put(
        "/api/initiatives/consent/not_a_real_category",
        json={"allowed": True},
    )
    assert response.status_code == 400


def test_put_empty_body_returns_400(client):
    response = client.put("/api/initiatives/consent/reminder", json={})
    assert response.status_code == 400


def test_put_grants_consent_and_is_reflected_in_get(client):
    response = client.put("/api/initiatives/consent/reminder", json={"allowed": True})
    assert response.status_code == 200
    body = response.json()
    assert body["allowed"] is True
    assert body["muted"] is False

    fetched = client.get("/api/initiatives/consent/reminder").json()
    assert fetched["allowed"] is True


def test_put_partial_update_preserves_untouched_field(client):
    client.put("/api/initiatives/consent/maintenance", json={"allowed": True})

    response = client.put("/api/initiatives/consent/maintenance", json={"muted": True})
    assert response.status_code == 200
    body = response.json()
    assert body["allowed"] is True  # preserved, not reset
    assert body["muted"] is True

    response = client.put("/api/initiatives/consent/maintenance", json={"muted": False})
    assert response.status_code == 200
    body = response.json()
    assert body["allowed"] is True  # still preserved
    assert body["muted"] is False


def test_put_records_actor(client):
    response = client.put(
        "/api/initiatives/consent/wellness",
        json={"allowed": True, "actor": "governance:test"},
    )
    assert response.status_code == 200
    assert response.json()["actor"] == "governance:test"


def test_put_default_actor_is_user(client):
    response = client.put("/api/initiatives/consent/next_best_action", json={"allowed": True})
    assert response.status_code == 200
    assert response.json()["actor"] == "user"
