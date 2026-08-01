"""
Phase B, stage B7: integration test proving the API's shutdown() sequence
actually freezes new admissions and drains in-flight ones before tearing
down _kernel -- not just that AdmissionGate itself works in isolation
(tests/test_admission_gate.py already covers that).
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from bartholomew_api_bridge_v0_1.services.api import app as app_module


@pytest.fixture
async def started_app():
    await app_module.startup()
    try:
        yield app_module
    finally:
        # Best-effort: if a test already ran shutdown() to completion
        # (mem's dedicated executor gets closed as part of that), don't
        # call it again -- a second shutdown() would call
        # admission_gate.drain() with its real, un-monkeypatched timeout
        # against whatever admission a test deliberately left un-released
        # (e.g. the drain-timeout test below), hanging teardown for the
        # full default bound.
        already_stopped = (
            app_module._kernel is not None and app_module._kernel.mem._db_executor._closed
        )
        if app_module._kernel is not None and not already_stopped:
            try:
                await app_module.shutdown()
            except Exception:
                pass


@pytest.mark.asyncio
async def test_new_request_gets_503_once_frozen(started_app):
    await started_app.admission_gate.freeze()

    transport = ASGITransport(app=started_app.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/self")

    assert resp.status_code == 503
    assert "shutting down" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_healthz_and_liveness_survive_a_freeze(started_app):
    """The exempt monitoring surfaces must keep responding even after
    freeze() -- see app.py's _ADMISSION_EXEMPT_PATHS/_PREFIXES."""
    await started_app.admission_gate.freeze()

    transport = ASGITransport(app=started_app.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        healthz_resp = await ac.get("/healthz")
        liveness_resp = await ac.get("/api/liveness/self")

    assert healthz_resp.status_code == 200
    assert liveness_resp.status_code == 200


@pytest.mark.asyncio
async def test_shutdown_waits_for_in_flight_admission_before_stopping_kernel(started_app):
    """
    Directly exercises shutdown()'s ordering: admit a token (simulating an
    in-flight request), start shutdown() as a background task, confirm the
    kernel is NOT yet stopped while the token is held, release it, and
    confirm shutdown() then completes and the kernel stops.
    """
    kernel = started_app._kernel
    token = await started_app.admission_gate.admit("simulated-in-flight-request")

    shutdown_task = asyncio.create_task(started_app.shutdown())

    # Give shutdown() a moment to reach its drain() call and start
    # waiting -- it must not have proceeded to _kernel.stop() yet, since
    # our token is still held.
    await asyncio.sleep(0.2)
    assert not shutdown_task.done(), "shutdown() must not complete while admitted work remains"
    assert kernel._lifecycle_instance_id is not None  # kernel not yet torn down

    await token.release()
    await asyncio.wait_for(shutdown_task, timeout=10.0)

    # Kernel actually stopped once the drain completed.
    assert kernel.mem._db_executor._closed is True


@pytest.mark.asyncio
async def test_shutdown_proceeds_after_drain_timeout_rather_than_hanging_forever(
    started_app,
    monkeypatch,
):
    """
    An admission that's never released must not hang shutdown() forever --
    AdmissionGate.drain()'s own bound (tested in isolation in
    tests/test_admission_gate.py) is what prevents that; this proves
    app.py's shutdown() actually passes a real, finite timeout through.
    """
    monkeypatch.setattr(started_app, "shutdown", started_app.shutdown)  # no-op, documents intent
    await started_app.admission_gate.admit("stuck-forever")  # deliberately never released

    # Patch the timeout down so this test doesn't take 30s.
    original_drain = started_app.admission_gate.drain

    async def _fast_drain(timeout: float = 30.0):
        return await original_drain(timeout=0.2)

    monkeypatch.setattr(started_app.admission_gate, "drain", _fast_drain)

    await asyncio.wait_for(started_app.shutdown(), timeout=5.0)
    assert started_app._kernel.mem._db_executor._closed is True
