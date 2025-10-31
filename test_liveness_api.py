from fastapi.testclient import TestClient
from bartholomew_api_bridge_v0_1.services.api.app import app

client = TestClient(app)


def test_liveness_ticks_endpoint():
    r = client.get("/api/liveness/ticks?limit=5&offset=0")
    assert r.status_code == 200, f"Unexpected status: {r.status_code}"
    data = r.json()
    assert isinstance(data, dict)
    assert "items" in data
    assert "total" in data
    assert "next_offset" in data


def test_liveness_nudges_endpoint():
    r = client.get("/api/liveness/nudges?limit=5&offset=0")
    assert r.status_code == 200
    data = r.json()
    assert "items" in data


def test_liveness_reflections_endpoint():
    r = client.get("/api/liveness/reflections?limit=5&offset=0")
    assert r.status_code == 200
    data = r.json()
    assert "items" in data
