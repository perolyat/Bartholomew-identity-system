"""The visible status and stop surface (acceptance gates 18, 5, 4).

An ordinary user must be able to see whether Bartholomew is listening,
observing a screen or speaking; which window is in scope; when it started and
expires; and how to stop it. These tests read that surface the way a UI would.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from bartholomew.multimodal.microphone import NullAudioBackend
from bartholomew.multimodal.modality import CaptureScope, Modality, ScopeKind
from bartholomew.multimodal.session import MultimodalSession, SessionState
from bartholomew.multimodal.status import status_snapshot
from bartholomew.multimodal.store import SessionStore


def _session(modality=Modality.MICROPHONE):
    scope = (
        CaptureScope(ScopeKind.WINDOW, window_id="w1", window_title="Q3 report")
        if modality is Modality.SCREEN
        else None
    )
    session = MultimodalSession(
        tenant_id="tenant-1",
        principal_id="user:taylor",
        device_id="device-1",
        modality=modality,
        correlation_id="corr-1",
        scope=scope,
    )
    session.transition(SessionState.AWAITING_APPROVAL)
    session.governance_decision = True
    session.consent_decision = True
    session.approve()
    session.transition(SessionState.ACTIVE)
    return session


@pytest.fixture
def client_and_store():
    from bartholomew_api_bridge_v0_1.services.api import app as app_module
    from bartholomew_api_bridge_v0_1.services.api.routes import multimodal

    store = SessionStore()
    original = multimodal.get_store
    multimodal.get_store = lambda: store
    app_module.app.dependency_overrides = {}
    try:
        yield TestClient(app_module.app), store
    finally:
        multimodal.get_store = original


class TestStatusSnapshot:
    def test_idle_reports_nothing_active(self):
        snapshot = status_snapshot(SessionStore(), microphone_backend=NullAudioBackend())
        assert snapshot["listening"] is False
        assert snapshot["observing_screen"] is False
        assert snapshot["speaking"] is False
        assert "not listening" in snapshot["summary"]

    @pytest.mark.parametrize(
        "modality,flag",
        [
            (Modality.MICROPHONE, "listening"),
            (Modality.SCREEN, "observing_screen"),
            (Modality.SPOKEN_OUTPUT, "speaking"),
        ],
    )
    def test_each_modality_shows_its_own_flag(self, modality, flag):
        store = SessionStore()
        store.add(_session(modality))
        snapshot = status_snapshot(store, microphone_backend=NullAudioBackend())
        assert snapshot[flag] is True
        others = {"listening", "observing_screen", "speaking"} - {flag}
        for other in others:
            assert snapshot[other] is False, "one modality must not imply another"

    def test_a_screen_session_shows_which_window_is_in_scope(self):
        store = SessionStore()
        store.add(_session(Modality.SCREEN))
        snapshot = status_snapshot(store, microphone_backend=NullAudioBackend())
        described = snapshot["active_sessions"][0]
        assert "Q3 report" in described["scope"]
        assert "Q3 report" in described["summary"]

    def test_timing_and_stop_instructions_are_visible(self):
        store = SessionStore()
        session = _session()
        store.add(session)
        described = status_snapshot(store, microphone_backend=NullAudioBackend())[
            "active_sessions"
        ][0]
        assert described["started_at"]
        assert described["expires_at"]
        assert described["seconds_remaining"] > 0
        assert described["stops_automatically_in"].endswith("s")
        assert session.session_id in described["how_to_stop"]

    def test_hardware_unavailability_is_visible(self):
        snapshot = status_snapshot(SessionStore(), microphone_backend=NullAudioBackend())
        assert snapshot["hardware"]["microphone"]["usable"] is False
        assert snapshot["hardware"]["microphone"]["detail"]

    def test_tenant_isolation(self):
        store = SessionStore()
        store.add(_session())
        assert status_snapshot(store, tenant_id="other-tenant")["listening"] is False
        assert status_snapshot(store, tenant_id="tenant-1")["listening"] is True

    def test_a_stopped_session_stops_being_reported_as_live(self):
        store = SessionStore()
        session = _session()
        store.add(session)
        store.stop(session.session_id)
        assert status_snapshot(store, microphone_backend=NullAudioBackend())["listening"] is False


class TestStatusRoutes:
    def test_status_route_reports_idle(self, client_and_store):
        client, _ = client_and_store
        body = client.get("/api/multimodal/status").json()
        assert body["listening"] is False
        assert "summary" in body

    def test_status_route_reports_a_live_session(self, client_and_store):
        client, store = client_and_store
        store.add(_session(Modality.SCREEN))
        body = client.get("/api/multimodal/status").json()
        assert body["observing_screen"] is True
        assert "Q3 report" in body["active_sessions"][0]["scope"]

    def test_stop_route_ends_a_session(self, client_and_store):
        client, store = client_and_store
        session = _session()
        store.add(session)
        body = client.post(f"/api/multimodal/sessions/{session.session_id}/stop").json()
        assert body["stopped"] is True
        assert body["session"]["state"] == "stopped"
        assert client.get("/api/multimodal/status").json()["listening"] is False

    def test_stopping_twice_is_truthful_not_an_error(self, client_and_store):
        client, store = client_and_store
        session = _session()
        store.add(session)
        client.post(f"/api/multimodal/sessions/{session.session_id}/stop")
        body = client.post(f"/api/multimodal/sessions/{session.session_id}/stop").json()
        assert body["stopped"] is False
        assert "already stopped" in body["detail"]

    def test_stopping_an_unknown_session_is_404(self, client_and_store):
        client, _ = client_and_store
        assert client.post("/api/multimodal/sessions/nope/stop").status_code == 404

    def test_stop_all_ends_every_live_session(self, client_and_store):
        client, store = client_and_store
        for modality in (Modality.MICROPHONE, Modality.SCREEN, Modality.SPOKEN_OUTPUT):
            store.add(_session(modality))
        body = client.post("/api/multimodal/sessions/stop-all").json()
        assert body["count"] == 3
        assert store.live() == []

    def test_finished_sessions_remain_inspectable(self, client_and_store):
        client, store = client_and_store
        session = _session()
        store.add(session)
        client.post(f"/api/multimodal/sessions/{session.session_id}/stop")
        listed = client.get("/api/multimodal/sessions").json()
        assert listed["count"] == 1
        assert listed["sessions"][0]["state"] == "stopped"

    def test_diagnostics_route_reports_this_machine(self, client_and_store):
        client, _ = client_and_store
        body = client.get("/api/multimodal/diagnostics").json()
        for key in ("microphone", "spoken_output", "accessibility", "screen_capture"):
            assert key in body
        assert body["notes"]

    def test_the_surface_is_reachable_without_a_live_kernel(self, client_and_store):
        """A stop that 503s when the kernel is down would be the wrong failure."""
        client, _ = client_and_store
        assert client.get("/api/multimodal/status").status_code == 200
