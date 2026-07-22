"""Test metrics endpoint production mode behavior."""

import sys

import pytest
from httpx import ASGITransport, AsyncClient


_APP_MODULE_NAMES = ("bartholomew_api_bridge_v0_1.services.api.app", "app")


@pytest.fixture(autouse=True)
def _restore_app_module_identity():
    """
    Every test below deletes bartholomew_api_bridge_v0_1.services.api.app
    (and its "app" alias) from sys.modules to force a fresh re-import that
    picks up a changed METRICS_INTERNAL_ONLY env var -- but none of them
    ever restored the original module object afterward. That's a real,
    previously-undiscovered hazard: any other test file that captured its
    own reference to this module before these tests ran (e.g. a
    module-scoped `from ... import app as app_module` at collection time --
    collection happens for every file before any test executes, so that
    binding is fixed well before these tests run) keeps pointing at the
    *original* module object, including its already-started FastAPI `app`
    instance. Meanwhile any *runtime* `from ... import _kernel`-style lookup
    elsewhere (self_state.py's _get_kernel(), by design, so it always sees a
    live value) resolves via sys.modules at call time -- and after these
    tests run, sys.modules has been left pointing at whichever fresh module
    the *last* test here happened to construct, a third object matching
    neither the original nor whatever the TestClient actually started.
    Simply deleting the entries again afterward doesn't fix this -- it just
    forces yet another fresh import next time, still mismatched against the
    original. Found via bisection: this exact split-brain made every test in
    tests/test_self_state_api.py fail with 503s when the full suite ran,
    since "test_metrics_production_mode" sorts alphabetically before
    "test_self_state_api".

    The actual fix: snapshot whatever's in sys.modules for these names
    before each test (present or absent), and put exactly that back
    afterward -- not a re-delete, a genuine restore -- so every other
    consumer's already-captured reference stays valid.
    """
    original = {name: sys.modules.get(name) for name in _APP_MODULE_NAMES}
    yield
    for name, module in original.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


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
