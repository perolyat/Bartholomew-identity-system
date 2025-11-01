import pytest
from httpx import AsyncClient, ASGITransport
from datetime import datetime
from app import app


@pytest.mark.anyio
async def test_liveness_self_ok():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/liveness/self")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["uptime"], int)
    assert data["uptime"] >= 0
    assert isinstance(data["drives"], list)
    assert all(isinstance(d, str) for d in data["drives"])
    # ISO8601 with trailing Z is acceptable when coerced:
    datetime.fromisoformat(data["last_tick"].replace("Z", "+00:00"))
