"""
HTTP-level tests for bartholomew_api_bridge_v0_1's notifications router
(/api/notifications/settings, /quiet-hours, /mute, /unmute) -- Stage 1,
S1.3. Follows the same pattern as tests/test_governance_api.py: a
module-scoped TestClient over the real app (real KernelDaemon startup,
real kernel.skill_registry with the "notify" skill auto-loaded via
config/skills/notify.yaml's enabled: true).
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


@pytest.fixture(autouse=True)
def _reset_notifications(client):
    """Every test starts and ends unmuted, so tests don't leak mute state
    into each other (the notify skill instance is a process-wide
    singleton on app_module._kernel, same sharing hazard documented in
    test_self_state_api.py/test_governance_api.py)."""
    client.post("/api/notifications/unmute")
    yield
    client.post("/api/notifications/unmute")


def test_get_settings_returns_quiet_hours_and_mute_status(client):
    response = client.get("/api/notifications/settings")
    assert response.status_code == 200
    body = response.json()
    assert "start" in body["quiet_hours"]
    assert "end" in body["quiet_hours"]
    assert body["muted"] is False


def test_set_quiet_hours_updates_settings(client):
    response = client.put(
        "/api/notifications/quiet-hours",
        json={"start": "21:00", "end": "08:00"},
    )
    assert response.status_code == 200
    assert response.json()["start"] == "21:00"
    assert response.json()["end"] == "08:00"

    settings = client.get("/api/notifications/settings").json()
    assert settings["quiet_hours"]["start"] == "21:00"
    assert settings["quiet_hours"]["end"] == "08:00"


def test_set_quiet_hours_rejects_malformed_time(client):
    response = client.put(
        "/api/notifications/quiet-hours",
        json={"start": "whenever", "end": "08:00"},
    )
    assert response.status_code == 400


def test_mute_and_unmute_round_trip(client):
    mute_response = client.post("/api/notifications/mute", json={})
    assert mute_response.status_code == 200
    assert mute_response.json()["muted"] is True

    settings = client.get("/api/notifications/settings").json()
    assert settings["muted"] is True
    assert settings["effective_muted"] is True

    unmute_response = client.post("/api/notifications/unmute")
    assert unmute_response.status_code == 200
    assert unmute_response.json()["muted"] is False

    settings = client.get("/api/notifications/settings").json()
    assert settings["muted"] is False
    assert settings["effective_muted"] is False
