import time
import tempfile
import pathlib
import os
import pytest
from fastapi.testclient import TestClient

# Set env vars before importing app
os.environ["BARTH_SPEED_FACTOR"] = "0.01"
_db_dir = pathlib.Path(tempfile.mkdtemp()) / "data"
_db_dir.mkdir(parents=True, exist_ok=True)
os.environ["BARTH_DB_PATH"] = str(_db_dir / "test.db")

from bartholomew_api_bridge_v0_1.services.api.app import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_liveness_endpoints(client):
    for path in ("/api/liveness/ticks", "/api/liveness/nudges",
                 "/api/liveness/reflections"):
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
