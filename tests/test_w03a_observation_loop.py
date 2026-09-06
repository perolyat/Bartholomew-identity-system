"""W03-A: the active-session observation loop (package-local).

Acceptance criteria covered here, with controlled doubles for the hardware and
the sink and the real session store, state machine and serializers:

1. every tick produces an `ObservationEvent` with distinct observed/inferred
   fields (criterion 1, loop half);
3. the kernel receives observation **content** -- the bounded elements and
   their text -- not merely `element_count` (criterion 3);
5. the brake is re-read on every wake and an engaged brake -- scope or global
   -- ends the session through `stop_all_for_brake` within a bounded interval;
   an unreadable brake ends it fail-closed (criterion 5);
6. no raw image is carried, secret fields are omitted, secret text is
   redacted with the redaction recorded, and truncation is recorded
   (criterion 6);
7. an envelope whose tenant disagrees with the process binding is refused,
   not re-attributed, and the session ends honestly (criterion 7).

The integration tier repeats the brake and ingress claims against the real
GovernanceStore and the real `inbound_events` table
(`tests/test_w03a_integration.py`).
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from bartholomew.integration.multimodal_events import (
    MultimodalIngressError,
    MultimodalObservationPayload,
)
from bartholomew.multimodal.accessibility import NullAccessibilityProvider
from bartholomew.multimodal.events import (
    EVENT_TYPE_ACCESSIBILITY,
    EVENT_TYPE_SCREEN,
    EVENT_TYPE_SESSION_STATE,
    CollectingEventSink,
)
from bartholomew.multimodal.inference import NullInferencer
from bartholomew.multimodal.loop import (
    MAX_CAPTURE_INTERVAL_SECONDS,
    MAX_POLL_SECONDS,
    BrakeUnreadableError,
    ObservationLoop,
    ObservationLoopConfig,
    build_observation_event,
)
from bartholomew.multimodal.modality import CaptureScope, Modality, ScopeKind
from bartholomew.multimodal.observation import (
    MAX_FACT_ELEMENTS,
    OBSERVED_UNAVAILABLE,
    OBSERVED_WINDOW_STATE,
)
from bartholomew.multimodal.privacy import PrivacyClass
from bartholomew.multimodal.runtime import SessionRequest, start_session
from bartholomew.multimodal.screen import capture_with_fallback
from bartholomew.multimodal.session import MultimodalSession, SessionState
from bartholomew.multimodal.status import status_snapshot
from bartholomew.multimodal.store import SessionStore

# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class GoodProvider:
    """A controlled accessibility provider; optionally reports idle time."""

    def __init__(
        self,
        elements=None,
        *,
        complete=True,
        idle_seconds=None,
        title="Q3 report - Notepad",
    ):
        self.elements = (
            elements
            if elements is not None
            else [
                {
                    "role": "Edit",
                    "name": "Document body",
                    "value": "Q3 report draft",
                    "focused": True,
                },
                {"role": "Button", "name": "Save", "value": None},
            ]
        )
        self.complete = complete
        self.idle_seconds = idle_seconds
        self.title = title
        self.reads = 0

    def available(self):
        return True, "ok"

    def read_active_window(self):
        self.reads += 1
        return {
            "application": "notepad.exe",
            "window_id": "w1",
            "window_title": self.title,
            "complete": self.complete,
            "elements": self.elements,
        }

    def read_idle_seconds(self):
        return self.idle_seconds


class CountingScreenBackend:
    def __init__(self):
        self.grabs = 0

    def available(self):
        return True, "ok"

    def grab(self, scope):
        self.grabs += 1
        return object()

    def describe(self, image):
        return "a document window"


class RefusingSink:
    def __init__(self, error):
        self.error = error
        self.attempts = 0

    def submit(self, envelope):
        self.attempts += 1
        raise self.error


class Brake:
    """A brake double: flip `engaged`, or make it `unreadable`."""

    def __init__(self):
        self.engaged = False
        self.unreadable = False
        self.reads = 0

    def __call__(self):
        self.reads += 1
        if self.unreadable:
            raise BrakeUnreadableError("database locked")
        return self.engaged


def _session(scope=None, **kwargs) -> MultimodalSession:
    return MultimodalSession(
        tenant_id=kwargs.pop("tenant_id", "tenant-1"),
        principal_id="user:taylor",
        device_id="device-1",
        modality=Modality.SCREEN,
        correlation_id="corr-1",
        causation_id="cause-1",
        scope=scope or CaptureScope(ScopeKind.WINDOW, window_id="w1", window_title="Q3 report"),
        **kwargs,
    )


def _activate(session: MultimodalSession) -> MultimodalSession:
    session.transition(SessionState.AWAITING_APPROVAL)
    session.governance_decision = True
    session.consent_decision = True
    session.approve()
    session.transition(SessionState.ACTIVE)
    return session


def _loop(store=None, session=None, *, provider=None, sink=None, brake=None, **kwargs):
    store = store or SessionStore()
    session = session or _activate(_session())
    sink = sink if sink is not None else CollectingEventSink()
    loop = ObservationLoop(
        session,
        store,
        accessibility_provider=provider if provider is not None else GoodProvider(),
        sink=sink,
        brake_check=brake or Brake(),
        **kwargs,
    )
    store.add(session, stopper=loop.stop)
    store.attach_observer(session.session_id, loop)
    return loop, store, session, sink


def _wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# ---------------------------------------------------------------------------
# Criterion 1 & 3: tick shape and content
# ---------------------------------------------------------------------------


class TestTickShape:
    def test_a_tick_emits_one_observation_event_with_distinct_fields(self):
        loop, _store, session, sink = _loop(provider=GoodProvider(idle_seconds=12.0))
        result = loop.tick()

        assert result.emitted is True and result.ended is False
        assert result.observed_kind == OBSERVED_WINDOW_STATE
        assert result.event_type == EVENT_TYPE_ACCESSIBILITY
        assert len(sink.events) == 1
        payload = sink.events[0]["payload"]
        assert payload["observed_event"]["kind"] == OBSERVED_WINDOW_STATE
        assert payload["observed_event"]["facts"]["idle_seconds"] == 12.0
        assert "inferred_state" in payload and "confidence" in payload
        assert isinstance(payload["competing_explanations"], list)
        for key in ("source", "occurred_at", "captured_at", "digest"):
            assert payload["provenance"][key]
        assert payload["provenance"]["session_id"] == session.session_id
        assert sink.events[0]["event_id"] == result.event_id

    def test_the_kernel_receives_content_not_merely_a_count(self):
        """Criterion 3: the elements' text is in the payload the handler parses."""
        loop, _store, _session_, sink = _loop()
        loop.tick()
        envelope = sink.events[0]
        facts = envelope["payload"]["observed_event"]["facts"]
        assert facts["element_count"] == 2
        assert [e["name"] for e in facts["elements"]] == ["Document body", "Save"]
        assert facts["elements"][0]["value"] == "Q3 report draft"
        assert facts["focused_element"]["name"] == "Document body"

        # Through the exact parser the backbone registers for this type.
        parsed = MultimodalObservationPayload.parse(envelope)
        assert "Q3 report draft" in parsed.text
        assert "Document body" in parsed.text

    def test_an_inference_when_made_is_separate_and_hedged(self):
        loop, _s, _e, sink = _loop(provider=GoodProvider(idle_seconds=20 * 60))
        loop.tick()
        payload = sink.events[0]["payload"]
        assert payload["inferred_state"] is not None
        assert 0.0 <= payload["confidence"] < 1.0
        assert len(payload["competing_explanations"]) >= 2
        assert "inactive" not in payload["inferred_state"]
        assert "20 minute" in payload["observed_event"]["summary"]

    def test_a_null_inferencer_yields_a_complete_record_with_no_inference(self):
        loop, _s, _e, sink = _loop(
            provider=GoodProvider(idle_seconds=20 * 60),
            inferencer=NullInferencer(),
        )
        loop.tick()
        payload = sink.events[0]["payload"]
        assert payload["inferred_state"] is None
        assert payload["confidence"] is None
        assert payload["competing_explanations"] == []

    def test_an_unavailable_provider_is_an_honest_unavailable_observation(self):
        loop, _s, _e, sink = _loop(provider=NullAccessibilityProvider("no UIA here"))
        result = loop.tick()
        assert result.emitted is True
        payload = sink.events[0]["payload"]
        assert payload["observed_event"]["kind"] == OBSERVED_UNAVAILABLE
        assert "unavailable" in payload["observed_event"]["summary"]
        assert "no UIA here" in payload["observed_event"]["summary"]
        assert payload["inferred_state"] is None

    def test_screenshot_fallback_travels_under_the_screen_type_without_an_image(self):
        backend = CountingScreenBackend()
        loop, _s, _e, sink = _loop(
            provider=NullAccessibilityProvider(),
            screen_backend=backend,
            allow_screenshot_fallback=True,
        )
        result = loop.tick()
        assert backend.grabs == 1
        assert result.event_type == EVENT_TYPE_SCREEN
        rendered = str(sink.events[0]).lower()
        for forbidden in ("image_bytes", "png", "pixels", "base64"):
            assert forbidden not in rendered
        facts = sink.events[0]["payload"]["observed_event"]["facts"]
        assert facts["used_screenshot"] is True
        assert facts["evidence_reference"].startswith("sha256:")

    def test_ticks_are_distinct_events_and_a_retry_is_not(self):
        loop, _s, _e, sink = _loop()
        loop.tick()
        time.sleep(0.002)
        loop.tick()
        assert len(sink.events) == 2
        assert sink.events[0]["event_id"] != sink.events[1]["event_id"]
        # Re-submitting an envelope is the retry case: same id, by construction.
        assert sink.events[0]["event_id"] == sink.events[0]["event_id"]

    def test_counters_are_visible_on_the_status_surface_without_content(self):
        loop, store, session, _sink = _loop()
        loop.tick()
        described = status_snapshot(store, include_hardware=False)["active_sessions"][0]
        assert described["session_id"] == session.session_id
        assert described["observation"]["observations_emitted"] == 1
        assert described["observation"]["last_event_id"] == loop.last_event_id
        assert "Q3 report draft" not in str(described)


# ---------------------------------------------------------------------------
# Criterion 5: governance on every wake
# ---------------------------------------------------------------------------


class TestBrakeAndExpiry:
    def test_the_brake_is_read_before_every_capture(self):
        brake = Brake()
        provider = GoodProvider()
        loop, _s, _e, _sink = _loop(provider=provider, brake=brake)
        loop.tick()
        loop.tick()
        assert brake.reads == 2 and provider.reads == 2

    def test_an_engaged_scope_brake_ends_the_session_via_stop_all_for_brake(self):
        brake = Brake()
        loop, store, session, sink = _loop(brake=brake)
        loop.tick()
        brake.engaged = True
        result = loop.tick()

        assert result.ended is True and result.emitted is False
        assert session.state is SessionState.STOPPED
        assert "parking brake" in session.outcome_reason
        assert "sight" in session.outcome_reason
        assert store.live() == []
        assert len(sink.events) == 1, "nothing was captured under an engaged brake"
        assert loop.stop_requested, "the store signalled the loop's stopper"

    def test_an_engaged_brake_stops_a_running_loop_within_a_bounded_interval(self):
        brake = Brake()
        loop, _store, session, _sink = _loop(
            brake=brake,
            config=ObservationLoopConfig(poll_seconds=0.05, capture_interval_seconds=0.05),
        )
        thread = loop.start_thread()
        assert _wait_until(lambda: loop.emitted >= 1)

        began = time.monotonic()
        brake.engaged = True
        thread.join(timeout=2.0)
        elapsed = time.monotonic() - began

        assert not thread.is_alive()
        assert elapsed < 2.0, "an engaged brake must end a live session within the bound"
        assert session.state is SessionState.STOPPED
        assert "parking brake" in session.outcome_reason

    def test_an_unreadable_brake_ends_the_session_fail_closed(self):
        brake = Brake()
        brake.unreadable = True
        loop, _store, session, sink = _loop(brake=brake)
        result = loop.tick()
        assert result.ended is True
        assert session.state is SessionState.FAILED
        assert "unreadable" in session.outcome_reason
        assert "fail-closed" in session.outcome_reason
        assert sink.events == []

    def test_a_brake_check_that_explodes_is_treated_as_engaged(self):
        def explode():
            raise RuntimeError("disk gone")

        loop, _store, session, sink = _loop(brake=explode)
        loop.tick()
        assert session.state is SessionState.FAILED
        assert "disk gone" in session.outcome_reason
        assert sink.events == []

    def test_expiry_ends_the_session_without_a_capture(self):
        loop, _store, session, sink = _loop()
        session.expires_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        result = loop.tick()
        assert result.ended is True
        assert session.state is SessionState.EXPIRED
        assert sink.events == []

    def test_an_explicit_stop_ends_a_running_loop_promptly(self):
        loop, store, session, _sink = _loop(
            config=ObservationLoopConfig(poll_seconds=0.05, capture_interval_seconds=0.05),
        )
        thread = loop.start_thread()
        assert _wait_until(lambda: loop.emitted >= 1)
        assert store.stop(session.session_id, "stopped by user") is True
        thread.join(timeout=2.0)
        assert not thread.is_alive()
        assert session.state is SessionState.STOPPED

    def test_a_terminal_session_is_never_observed_again(self):
        loop, store, session, sink = _loop()
        store.stop(session.session_id)
        result = loop.tick()
        assert result.ended is True and "stopped" in result.end_reason
        assert sink.events == []

    def test_intervals_cannot_be_loosened_past_the_ceiling(self):
        config = ObservationLoopConfig(poll_seconds=600, capture_interval_seconds=3600)
        assert config.poll_seconds == MAX_POLL_SECONDS
        assert config.capture_interval_seconds == MAX_CAPTURE_INTERVAL_SECONDS
        with pytest.raises(ValueError):
            ObservationLoopConfig(poll_seconds=0)

    def test_the_loop_runs_screen_sessions_only(self):
        mic = MultimodalSession(
            tenant_id="t",
            principal_id="user:x",
            device_id="d",
            modality=Modality.MICROPHONE,
            correlation_id="c",
        )
        with pytest.raises(ValueError, match="screen sessions only"):
            ObservationLoop(mic, SessionStore(), brake_check=Brake())


# ---------------------------------------------------------------------------
# Criterion 6: privacy and retention
# ---------------------------------------------------------------------------


class TestPrivacy:
    def test_a_password_field_is_omitted_and_recorded(self):
        provider = GoodProvider(
            elements=[
                {"role": "Edit", "name": "Password", "value": "hunter2", "focused": True},
                {"role": "Edit", "name": "Username", "value": "taylor"},
            ],
        )
        loop, _s, _e, sink = _loop(provider=provider)
        loop.tick()
        envelope = sink.events[0]
        assert "hunter2" not in str(envelope)
        facts = envelope["payload"]["observed_event"]["facts"]
        assert facts["omitted_secret_fields"] == 1
        assert facts["elements"][0]["value"] is None
        assert envelope["privacy_class"] == PrivacyClass.RESTRICTED.value
        rules = {r["rule"] for r in envelope["payload"]["classification"]["redactions"]}
        assert "secret_field_omitted" in rules

    def test_secret_text_in_an_ordinary_field_is_redacted_and_recorded(self):
        provider = GoodProvider(
            elements=[{"role": "Edit", "name": "Notes", "value": "token: sk-abcdefghijklmnopqrst"}],
        )
        loop, _s, _e, sink = _loop(provider=provider)
        loop.tick()
        envelope = sink.events[0]
        assert "sk-abcdefghijklmnopqrst" not in str(envelope)
        assert "[redacted]" in str(envelope)
        assert envelope["payload"]["classification"]["redactions"], "the redaction is recorded"

    def test_element_truncation_is_recorded_never_silent(self):
        many = [{"role": "Text", "name": f"item {i}", "value": "x"} for i in range(200)]
        loop, _s, _e, sink = _loop(provider=GoodProvider(elements=many))
        loop.tick()
        payload = sink.events[0]["payload"]
        facts = payload["observed_event"]["facts"]
        assert len(facts["elements"]) == MAX_FACT_ELEMENTS
        assert facts["elements_truncated"] is True
        assert payload["classification"]["truncated"] is True
        rules = {r["rule"] for r in payload["classification"]["redactions"]}
        assert "event_element_bound" in rules

    def test_a_long_summary_is_bounded_and_the_bound_recorded(self):
        provider = GoodProvider(
            elements=[{"role": "Edit", "name": "n" * 190, "value": "v", "focused": True}],
            title="t" * 190,
        )
        loop, _s, _e, sink = _loop(provider=provider)
        loop.tick()
        payload = sink.events[0]["payload"]
        assert len(payload["observed_event"]["summary"]) <= 300
        rules = {r["rule"] for r in payload["classification"]["redactions"]}
        assert "length_bound" in rules

    def test_the_observation_record_never_carries_raw_material(self):
        observation = capture_with_fallback(
            approved_scope=_session().scope,
            allow_screenshot_fallback=True,
            accessibility_provider=NullAccessibilityProvider(),
            screen_backend=CountingScreenBackend(),
        )
        event = build_observation_event(observation, session=_session())
        for forbidden in ("image", "png", "bytes", "path", "pixels", "audio"):
            assert forbidden not in event.observed_event.facts


# ---------------------------------------------------------------------------
# Criterion 7 and the emit path
# ---------------------------------------------------------------------------


class TestEmitRefusals:
    def test_a_tenant_mismatch_is_refused_and_the_session_fails_honestly(self):
        error = MultimodalIngressError(
            "the multimodal session's tenant does not match this runtime's binding; "
            "refusing to capture rather than re-attribute it",
        )
        sink = RefusingSink(error)
        loop, _store, session, _ = _loop(sink=sink)
        result = loop.tick()
        assert sink.attempts == 1
        assert result.ended is True and result.emitted is False
        assert session.state is SessionState.FAILED
        assert "could not be recorded" in session.outcome_reason
        assert "tenant" in session.outcome_reason
        assert loop.last_error and "re-attribute" in loop.last_error

    def test_a_failed_write_is_never_reported_as_recorded(self):
        sink = RefusingSink(RuntimeError("disk full"))
        loop, _store, session, _ = _loop(sink=sink)
        result = loop.tick()
        assert result.emitted is False
        assert loop.emitted == 0
        assert session.state is SessionState.FAILED

    def test_a_capture_that_explodes_fails_the_session_with_the_reason(self):
        class Exploding:
            def available(self):
                raise RuntimeError("UIA crashed")

            def read_active_window(self):
                raise RuntimeError("UIA crashed")

        # `observe_active_window` reports an exploding provider as unavailable
        # rather than raising, so the loop keeps going honestly.
        loop, _store, session, sink = _loop(provider=Exploding())
        result = loop.tick()
        assert result.emitted is True
        assert sink.events[0]["payload"]["observed_event"]["kind"] == OBSERVED_UNAVAILABLE
        assert session.state is SessionState.ACTIVE


# ---------------------------------------------------------------------------
# start_session drives the loop
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, outcome="started", allowed=True):
        self.outcome = outcome
        self.governance_allowed = allowed
        self.reason = None
        self.provenance_degraded = False
        self.provenance_error = None


async def _granting_seam(*_args, **_kwargs):
    return _Result()


@pytest.fixture
def resolver():
    from bartholomew.multimodal.devices import StaticCapabilityResolver
    from bartholomew.multimodal.modality import CAPABILITY_KIND

    r = StaticCapabilityResolver()
    r.declare("device-1", list(CAPABILITY_KIND.values()))
    return r


def _request(**kwargs):
    return SessionRequest(
        tenant_id="tenant-1",
        principal_id="user:taylor",
        device_id="device-1",
        modality=Modality.SCREEN,
        correlation_id="corr-1",
        scope=CaptureScope(ScopeKind.WINDOW, window_id="w1", window_title="Q3 report"),
        **kwargs,
    )


class TestStartSessionRunsTheLoop:
    async def test_an_active_screen_session_observes_and_emits_on_its_own(self, resolver):
        store = SessionStore()
        sink = CollectingEventSink()
        brake = Brake()
        result = await start_session(
            _request(),
            store=store,
            capability_resolver=resolver,
            seam=_granting_seam,
            accessibility_provider=GoodProvider(),
            event_sink=sink,
            brake_check=brake,
            loop_config=ObservationLoopConfig(poll_seconds=0.05, capture_interval_seconds=0.05),
        )
        assert result.allowed and result.session.state is SessionState.ACTIVE
        assert result.extra["observation"]["brake_scope"] == "sight"
        assert "_loop" not in result.as_dict(), "in-process handles never reach the wire"

        loop = result.extra["_loop"]
        assert _wait_until(lambda: loop.emitted >= 2)
        observations = [e for e in sink.events if e["event_type"] == EVENT_TYPE_ACCESSIBILITY]
        assert len(observations) >= 2
        assert observations[0]["payload"]["observed_event"]["facts"]["elements"]

        assert store.stop(result.session.session_id) is True
        result.extra["_thread"].join(timeout=2.0)
        assert result.session.state is SessionState.STOPPED
        lifecycle = [e for e in sink.events if e["event_type"] == EVENT_TYPE_SESSION_STATE]
        assert [e["payload"]["state"] for e in lifecycle] == ["active", "stopped"]

    async def test_the_brake_sweep_ends_a_session_start_session_started(self, resolver):
        store = SessionStore()
        result = await start_session(
            _request(),
            store=store,
            capability_resolver=resolver,
            seam=_granting_seam,
            accessibility_provider=GoodProvider(),
            event_sink=CollectingEventSink(),
            brake_check=Brake(),
            loop_config=ObservationLoopConfig(poll_seconds=0.05, capture_interval_seconds=0.05),
        )
        assert store.stop_all_for_brake("sight") == [result.session.session_id]
        result.extra["_thread"].join(timeout=2.0)
        assert not result.extra["_thread"].is_alive()
        assert result.session.state is SessionState.STOPPED

    async def test_a_caller_may_drive_ticks_itself(self, resolver):
        store = SessionStore()
        sink = CollectingEventSink()
        result = await start_session(
            _request(),
            store=store,
            capability_resolver=resolver,
            seam=_granting_seam,
            accessibility_provider=GoodProvider(),
            event_sink=sink,
            brake_check=Brake(),
            run_observation_loop=False,
        )
        assert result.extra["_thread"] is None
        assert store.observer(result.session.session_id) is result.extra["_loop"]
        tick = result.extra["_loop"].tick()
        assert tick.emitted and len(sink.events) == 1

    async def test_a_refused_session_starts_no_loop(self, resolver):
        async def denying_seam(*_a, **_k):
            return _Result(outcome="parking_brake_denied", allowed=False)

        store = SessionStore()
        result = await start_session(
            _request(),
            store=store,
            capability_resolver=resolver,
            seam=denying_seam,
            accessibility_provider=GoodProvider(),
            event_sink=CollectingEventSink(),
            brake_check=Brake(),
        )
        assert result.allowed is False
        assert result.session.state is SessionState.REFUSED
        assert store.observer(result.session.session_id) is None
        assert "_loop" not in result.extra

    async def test_the_loop_thread_is_a_daemon_named_for_its_session(self, resolver):
        store = SessionStore()
        result = await start_session(
            _request(),
            store=store,
            capability_resolver=resolver,
            seam=_granting_seam,
            accessibility_provider=GoodProvider(),
            event_sink=CollectingEventSink(),
            brake_check=Brake(),
            loop_config=ObservationLoopConfig(poll_seconds=0.05, capture_interval_seconds=0.05),
        )
        thread: threading.Thread = result.extra["_thread"]
        assert thread.daemon and result.session.session_id in thread.name
        store.stop(result.session.session_id)
        thread.join(timeout=2.0)
