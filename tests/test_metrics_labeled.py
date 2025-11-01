import pytest
from httpx import AsyncClient, ASGITransport
from app import app


@pytest.mark.anyio
async def test_metrics_exposes_drive_labeled_counter_and_uptime():
    # Simulate a tick with a specific drive
    from bartholomew_api_bridge_v0_1.services.api.app import set_last_tick
    set_last_tick(drive="reflection_micro")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/metrics")
    assert r.status_code == 200
    body = r.text

    # Uptime gauge should appear
    assert "kernel_uptime_seconds" in body

    # Drive-labeled tick counter should appear with our label
    assert 'kernel_ticks_total' in body
    assert 'drive="reflection_micro"' in body
