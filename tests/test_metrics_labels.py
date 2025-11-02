from fastapi.testclient import TestClient

from bartholomew_api_bridge_v0_1.services.api.app import app


client = TestClient(app)


def test_metrics_has_tick_counter():
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "bartholomew_ticks_total" in r.text
