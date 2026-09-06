"""W03-A: the governed read-back primitive (acceptance criterion 4).

Given a window/target, `read_back` returns the current UI-state text through
the same consent / brake / identity governance as any observation -- or an
honest "unavailable" that names why. It never starts a session, never widens
a scope, never serves a content principal, and records what it read on the
same backbone as any other observation. W03-C consumes it for Verify.
"""

from __future__ import annotations

import pytest

from bartholomew.multimodal import readback as rb
from bartholomew.multimodal.accessibility import NullAccessibilityProvider
from bartholomew.multimodal.events import CollectingEventSink
from bartholomew.multimodal.loop import BrakeUnreadableError, ObservationLoop
from bartholomew.multimodal.modality import CaptureScope, Modality, ScopeKind
from bartholomew.multimodal.observation import OBSERVED_READ_BACK
from bartholomew.multimodal.readback import ReadBackResult, read_back
from bartholomew.multimodal.session import MultimodalSession, SessionState
from bartholomew.multimodal.store import SessionStore, _set_default_store_for_tests

WINDOW = CaptureScope(ScopeKind.WINDOW, window_id="w1", window_title="Q3 report")
OTHER_WINDOW = CaptureScope(ScopeKind.WINDOW, window_id="w2", window_title="Mail")


class GoodProvider:
    def __init__(self, elements=None):
        self.elements = elements or [
            {"role": "Edit", "name": "Recipient", "value": "taylor@example.com", "focused": True},
            {"role": "Button", "name": "Send"},
        ]
        self.reads = 0

    def available(self):
        return True, "ok"

    def read_active_window(self):
        self.reads += 1
        return {
            "application": "mail.exe",
            "window_id": "w1",
            "window_title": "New message",
            "complete": True,
            "elements": self.elements,
        }


class Brake:
    def __init__(self, engaged=False, unreadable=False):
        self.engaged = engaged
        self.unreadable = unreadable
        self.reads = 0

    def __call__(self):
        self.reads += 1
        if self.unreadable:
            raise BrakeUnreadableError("locked")
        return self.engaged


def _session(scope=WINDOW, tenant="tenant-1", device="device-1", modality=Modality.SCREEN):
    return MultimodalSession(
        tenant_id=tenant,
        principal_id="user:taylor",
        device_id=device,
        modality=modality,
        correlation_id="corr-1",
        scope=scope if modality is Modality.SCREEN else None,
    )


def _activate(session):
    session.transition(SessionState.AWAITING_APPROVAL)
    session.governance_decision = True
    session.consent_decision = True
    session.approve()
    session.transition(SessionState.ACTIVE)
    return session


def _live_store(*, provider=None, sink=None, brake=None, scope=WINDOW):
    """A store holding one ACTIVE, consented screen session driven by a loop."""
    store = SessionStore()
    session = _activate(_session(scope))
    loop = ObservationLoop(
        session,
        store,
        accessibility_provider=provider if provider is not None else GoodProvider(),
        sink=sink if sink is not None else CollectingEventSink(),
        brake_check=brake or Brake(),
    )
    store.add(session, stopper=loop.stop)
    store.attach_observer(session.session_id, loop)
    return store, session, loop


class TestHonestUnavailable:
    def test_no_session_means_unavailable_and_no_session_is_started(self):
        store = SessionStore()
        result = read_back(tenant_id="tenant-1", device_id="device-1", target=WINDOW, store=store)
        assert result.available is False
        assert result.code == rb.UNAVAILABLE_NO_CONSENTED_SESSION
        assert "never starts one" in result.reason
        assert store.all() == [], "read-back must not create a session"

    def test_a_session_for_another_device_does_not_serve(self):
        store, _session_, _loop = _live_store()
        result = read_back(tenant_id="tenant-1", device_id="device-9", target=WINDOW, store=store)
        assert result.code == rb.UNAVAILABLE_NO_CONSENTED_SESSION

    def test_a_session_for_another_tenant_does_not_serve(self):
        store, _session_, _loop = _live_store()
        result = read_back(tenant_id="tenant-2", device_id="device-1", target=WINDOW, store=store)
        assert result.code == rb.UNAVAILABLE_NO_CONSENTED_SESSION

    def test_a_target_outside_the_consented_scope_is_refused_not_approximated(self):
        store, _session_, loop = _live_store()
        result = read_back(
            tenant_id="tenant-1",
            device_id="device-1",
            target=OTHER_WINDOW,
            store=store,
        )
        assert result.available is False
        assert result.code == rb.UNAVAILABLE_OUTSIDE_SCOPE
        assert loop.accessibility_provider.reads == 0, "nothing was read"

    def test_a_display_scope_does_not_serve_a_window_target(self):
        store, _s, _l = _live_store(scope=CaptureScope(ScopeKind.DISPLAY, display_id="1"))
        result = read_back(tenant_id="tenant-1", device_id="device-1", target=WINDOW, store=store)
        assert result.code == rb.UNAVAILABLE_OUTSIDE_SCOPE

    @pytest.mark.parametrize(
        "principal",
        ["model:claude", "assistant:b", "event:inbound-1", "inbound:x", "companion:pc", "system:s"],
    )
    def test_a_content_principal_cannot_read_a_screen(self, principal):
        store, _s, loop = _live_store()
        result = read_back(
            tenant_id="tenant-1",
            device_id="device-1",
            target=WINDOW,
            requested_by=principal,
            store=store,
        )
        assert result.code == rb.UNAVAILABLE_REQUESTER_REFUSED
        assert loop.accessibility_provider.reads == 0

    def test_an_engaged_brake_refuses_and_stops_the_session(self):
        brake = Brake()
        store, session, loop = _live_store(brake=brake)
        brake.engaged = True
        result = read_back(tenant_id="tenant-1", device_id="device-1", target=WINDOW, store=store)
        assert result.code == rb.UNAVAILABLE_BRAKE_ENGAGED
        assert session.state is SessionState.STOPPED
        assert "parking brake" in session.outcome_reason
        assert loop.accessibility_provider.reads == 0

    def test_an_unreadable_brake_refuses_fail_closed(self):
        store, _s, loop = _live_store(brake=Brake(unreadable=True))
        result = read_back(tenant_id="tenant-1", device_id="device-1", target=WINDOW, store=store)
        assert result.code == rb.UNAVAILABLE_BRAKE_UNREADABLE
        assert loop.accessibility_provider.reads == 0

    def test_an_unreadable_window_is_unavailable_with_the_reason_and_the_evidence(self):
        sink = CollectingEventSink()
        store, _s, _l = _live_store(provider=NullAccessibilityProvider("no UIA"), sink=sink)
        result = read_back(tenant_id="tenant-1", device_id="device-1", target=WINDOW, store=store)
        assert result.available is False
        assert result.code == rb.UNAVAILABLE_PROVIDER
        assert "no UIA" in result.reason
        assert result.text is None
        # The honest "unavailable" is itself recorded as an observation.
        assert result.event is not None and result.event_id
        assert sink.events[0]["payload"]["observed_event"]["kind"] == OBSERVED_READ_BACK

    def test_a_stopped_session_no_longer_serves(self):
        store, session, _l = _live_store()
        store.stop(session.session_id)
        result = read_back(tenant_id="tenant-1", device_id="device-1", target=WINDOW, store=store)
        assert result.code == rb.UNAVAILABLE_NO_CONSENTED_SESSION


class TestSuccessfulRead:
    def test_returns_the_current_ui_state_text_and_records_it(self):
        sink = CollectingEventSink()
        brake = Brake()
        store, session, _l = _live_store(sink=sink, brake=brake)
        result = read_back(
            tenant_id="tenant-1",
            device_id="device-1",
            target=WINDOW,
            requested_by="user:taylor",
            store=store,
        )
        assert result.available is True
        assert "taylor@example.com" in result.text
        assert "Recipient" in result.text and "[focused]" in result.text
        assert result.session_id == session.session_id
        assert brake.reads == 1, "the brake was re-read at the moment of the read"

        assert result.event.observed_event.kind == OBSERVED_READ_BACK
        assert result.event.inferred_state is None, "a read-back carries no inference"
        assert result.event_id and sink.events[0]["event_id"] == result.event_id
        assert sink.events[0]["payload"]["observed_event"]["facts"]["elements"][0]["value"] == (
            "taylor@example.com"
        )
        assert result.provenance_degraded is False

    def test_no_target_means_whatever_the_session_is_consented_to(self):
        store, _s, _l = _live_store()
        result = read_back(tenant_id="tenant-1", device_id="device-1", store=store)
        assert result.available is True

    def test_a_secret_field_is_omitted_from_the_text(self):
        provider = GoodProvider(
            elements=[{"role": "Edit", "name": "Password", "value": "hunter2", "focused": True}],
        )
        store, _s, _l = _live_store(provider=provider)
        result = read_back(tenant_id="tenant-1", device_id="device-1", store=store)
        assert result.available is True
        assert "hunter2" not in result.text
        assert "hunter2" not in str(result.as_dict())

    def test_a_failed_record_is_reported_as_degraded_not_hidden(self):
        class Refusing:
            def submit(self, envelope):
                raise RuntimeError("ingress down")

        store, _s, _l = _live_store(sink=Refusing())
        result = read_back(tenant_id="tenant-1", device_id="device-1", store=store)
        assert result.available is True and result.text
        assert result.event_id is None
        assert result.provenance_degraded is True
        assert "ingress down" in result.provenance_error

    def test_emit_can_be_declined_by_the_caller(self):
        sink = CollectingEventSink()
        store, _s, _l = _live_store(sink=sink)
        result = read_back(tenant_id="tenant-1", device_id="device-1", store=store, emit=False)
        assert result.available is True and sink.events == []

    def test_an_explicit_provider_for_this_read_wins(self):
        store, _s, loop = _live_store()
        other = GoodProvider(elements=[{"role": "Text", "name": "Only here", "value": None}])
        result = read_back(
            tenant_id="tenant-1",
            device_id="device-1",
            store=store,
            accessibility_provider=other,
        )
        assert "Only here" in result.text
        assert loop.accessibility_provider.reads == 0

    def test_the_process_wide_store_is_the_default(self):
        store = _set_default_store_for_tests(None)
        try:
            session = _activate(_session())
            loop = ObservationLoop(
                session,
                store,
                accessibility_provider=GoodProvider(),
                sink=CollectingEventSink(),
                brake_check=Brake(),
            )
            store.add(session, stopper=loop.stop)
            store.attach_observer(session.session_id, loop)
            result = read_back(tenant_id="tenant-1", device_id="device-1")
            assert result.available is True
        finally:
            _set_default_store_for_tests(None)

    def test_a_session_without_a_loop_is_read_through_a_loop_shaped_reader(self):
        store = SessionStore()
        session = _activate(_session())
        store.add(session)
        result = read_back(
            tenant_id="tenant-1",
            device_id="device-1",
            store=store,
            accessibility_provider=GoodProvider(),
            sink=CollectingEventSink(),
            brake_check=Brake(),
        )
        assert result.available is True

    def test_result_shape_is_serializable(self):
        import json

        store, _s, _l = _live_store()
        result = read_back(tenant_id="tenant-1", device_id="device-1", store=store)
        json.dumps(result.as_dict())
        assert isinstance(result, ReadBackResult)
