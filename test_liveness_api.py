import pytest
from fastapi.testclient import TestClient

from bartholomew_api_bridge_v0_1.services.api.app import app


@pytest.fixture(scope="module")
def client():
    # Must enter as a context manager to run the app's startup/shutdown
    # lifespan -- that's what starts the kernel daemon and creates its
    # schema (reflections, nudges, etc tables). A bare TestClient(app)
    # never triggers it, so every query below would 404/error against a
    # schema-less DB.
    with TestClient(app) as c:
        yield c


def test_liveness_ticks_endpoint(client):
    r = client.get("/api/liveness/ticks?limit=5&offset=0")
    assert r.status_code == 200, f"Unexpected status: {r.status_code}"
    data = r.json()
    assert isinstance(data, list)


def test_liveness_nudges_endpoint(client):
    r = client.get("/api/liveness/nudges?limit=5&offset=0")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)


def test_liveness_reflections_endpoint(client):
    r = client.get("/api/liveness/reflections?limit=5&offset=0")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
