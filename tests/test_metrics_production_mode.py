"""Test metrics endpoint production mode behavior."""
import pytest
import os
from httpx import AsyncClient, ASGITransport


@pytest.mark.anyio
async def test_metrics_default_dev_mode(monkeypatch):
    """Test default behavior: /metrics is exposed (dev/test mode)."""
    # Ensure env is not set
    monkeypatch.delenv("METRICS_INTERNAL_ONLY", raising=False)
    
    # Import app after env is cleared to pick up the default
    from app import app
    
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/metrics")
    
    assert r.status_code == 200
    assert "kernel_uptime_seconds" in r.text


@pytest.mark.anyio
async def test_metrics_production_mode(monkeypatch):
    """Test production mode: /metrics returns 404, /internal/metrics works."""
    # Set production mode
    monkeypatch.setenv("METRICS_INTERNAL_ONLY", "1")
    
    # Force reload of app module to pick up env change
    import sys
    if "bartholomew_api_bridge_v0_1.services.api.app" in sys.modules:
        del sys.modules["bartholomew_api_bridge_v0_1.services.api.app"]
    if "app" in sys.modules:
        del sys.modules["app"]
    
    from app import app
    
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # /metrics should 404
        r1 = await ac.get("/metrics")
        assert r1.status_code == 404
        
        # /internal/metrics should work
        r2 = await ac.get("/internal/metrics")
        assert r2.status_code == 200
        assert "kernel_uptime_seconds" in r2.text


@pytest.mark.anyio
async def test_metrics_production_mode_truthy_values(monkeypatch):
    """Test various truthy values for METRICS_INTERNAL_ONLY."""
    truthy_values = ["1", "true", "True", "TRUE", "yes", "Yes", "YES", "on", "ON"]
    
    for value in truthy_values:
        monkeypatch.setenv("METRICS_INTERNAL_ONLY", value)
        
        # Force reload
        import sys
        if "bartholomew_api_bridge_v0_1.services.api.app" in sys.modules:
            del sys.modules["bartholomew_api_bridge_v0_1.services.api.app"]
        if "app" in sys.modules:
            del sys.modules["app"]
        
        from app import app
        
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/internal/metrics")
        
        assert r.status_code == 200, f"Failed for value: {value}"


@pytest.mark.anyio
async def test_metrics_production_mode_falsy_values(monkeypatch):
    """Test falsy values keep default behavior."""
    falsy_values = ["0", "false", "False", "no", "off", ""]
    
    for value in falsy_values:
        monkeypatch.setenv("METRICS_INTERNAL_ONLY", value)
        
        # Force reload
        import sys
        if "bartholomew_api_bridge_v0_1.services.api.app" in sys.modules:
            del sys.modules["bartholomew_api_bridge_v0_1.services.api.app"]
        if "app" in sys.modules:
            del sys.modules["app"]
        
        from app import app
        
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/metrics")
        
        assert r.status_code == 200, f"Failed for value: {value}"
