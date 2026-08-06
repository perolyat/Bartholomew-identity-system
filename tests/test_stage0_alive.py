import os
import pathlib
import tempfile
import time

import pytest
from fastapi.testclient import TestClient

# Set env vars before importing app
os.environ["BARTH_SPEED_FACTOR"] = "0.01"
_db_dir = pathlib.Path(tempfile.mkdtemp()) / "data"
_db_dir.mkdir(parents=True, exist_ok=True)
_DB_PATH = str(_db_dir / "test.db")
os.environ["BARTH_DB_PATH"] = _DB_PATH

from bartholomew_api_bridge_v0_1.services.api.app import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    # Re-assert right before starting the app -- see
    # tests/test_api_admission_gate.py's client fixture for why (pytest
    # collects/imports every test module, running each one's own
    # os.environ[...] assignment, before running any test; a
    # later-collected module can overwrite this by the time this fixture
    # actually fires).
    os.environ["BARTH_DB_PATH"] = _DB_PATH
    with TestClient(app) as c:
        yield c


@pytest.mark.smoke
def test_liveness_endpoints(client):
    for path in ("/api/liveness/ticks", "/api/liveness/nudges", "/api/liveness/reflections"):
        r = client.get(path)
        assert r.status_code == 200
        assert isinstance(r.json(), list)


def test_ticks_observed_for_active_drives(client):
    deadline = time.time() + 12
    seen = set()
    while time.time() < deadline:
        r = client.get("/api/liveness/ticks")
        assert r.status_code == 200
        for t in r.json():
            if isinstance(t, dict) and "drive_id" in t:
                seen.add(t["drive_id"])
        if {"self_check", "curiosity_probe", "reflection_micro"} & seen:
            break
        time.sleep(0.5)
    assert seen, "No ticks observed"
