"""Accessibility first, screenshots only when justified, and no raw images.

Acceptance gates covered: 10 (accessibility observation is preferred over
screenshots), 11 (screenshot fallback is explicitly authorized and its reason
recorded), 13 (raw images are not persisted by default), 14 (derived
observations contain source, device, session, time, privacy and retention
provenance).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bartholomew.multimodal.accessibility import (
    NullAccessibilityProvider,
    observe_active_window,
)
from bartholomew.multimodal.events import (
    EVENT_TYPES,
    NullEventSink,
    serialize_accessibility,
    serialize_microphone,
    serialize_screen,
    serialize_session_state,
    serialize_speech,
)
from bartholomew.multimodal.modality import CaptureScope, Modality, ScopeKind
from bartholomew.multimodal.privacy import PrivacyClass, RetentionClass
from bartholomew.multimodal.screen import ScreenObservation, capture_with_fallback
from bartholomew.multimodal.session import MultimodalSession


class GoodProvider:
    """A controlled accessibility provider returning a complete tree."""

    def __init__(self, elements=None, complete=True):
        self.elements = (
            elements
            if elements is not None
            else [
                {"role": "Edit", "name": "Document body", "value": "Q3 report", "focused": True},
                {"role": "Button", "name": "Save", "value": None},
            ]
        )
        self.complete = complete

    def available(self):
        return True, "ok"

    def read_active_window(self):
        return {
            "application": "notepad.exe",
            "window_id": "w1",
            "window_title": "Q3 report - Notepad",
            "complete": self.complete,
            "elements": self.elements,
        }


class CountingScreenBackend:
    def __init__(self, available=True):
        self._available = available
        self.grabs = 0

    def available(self):
        return self._available, "ok" if self._available else "no display"

    def grab(self, scope):
        self.grabs += 1
        return object()

    def describe(self, image):
        return "a document window"


def _session(modality=Modality.SCREEN):
    scope = (
        CaptureScope(ScopeKind.WINDOW, window_id="w1", window_title="Q3 report")
        if modality is Modality.SCREEN
        else None
    )
    return MultimodalSession(
        tenant_id="tenant-1",
        principal_id="user:taylor",
        device_id="device-1",
        modality=modality,
        correlation_id="corr-1",
        causation_id="cause-1",
        scope=scope,
    )


class TestAccessibilityIsPreferred:
    """Gate 10."""

    def test_a_sufficient_tree_means_no_screenshot_is_taken(self):
        backend = CountingScreenBackend()
        observation = capture_with_fallback(
            approved_scope=CaptureScope(ScopeKind.WINDOW, window_id="w1"),
            allow_screenshot_fallback=True,  # permitted, and still not used
            accessibility_provider=GoodProvider(),
            screen_backend=backend,
        )
        assert observation.used_screenshot is False
        assert backend.grabs == 0, "pixels must not be touched when structure sufficed"
        assert "sufficient" in observation.detail

    def test_the_tree_is_read_before_any_fallback_decision(self):
        observation = capture_with_fallback(
            approved_scope=CaptureScope(ScopeKind.WINDOW, window_id="w1"),
            accessibility_provider=GoodProvider(),
            screen_backend=CountingScreenBackend(),
        )
        assert observation.accessibility.available is True
        assert observation.accessibility.window_title.startswith("Q3 report")

    @pytest.mark.parametrize(
        "provider,expected",
        [
            (NullAccessibilityProvider(), "unavailable"),
            (GoodProvider(complete=False), "incomplete"),
            (GoodProvider(elements=[]), "no readable controls"),
        ],
    )
    def test_insufficiency_reasons_are_specific(self, provider, expected):
        observation = observe_active_window(provider)
        assert observation.sufficient_for() is False
        assert expected in observation.insufficiency_reason()

    def test_a_provider_that_explodes_is_unavailable_not_blank(self):
        class Exploding:
            def available(self):
                return True, "ok"

            def read_active_window(self):
                raise RuntimeError("UIA timeout")

        observation = observe_active_window(Exploding())
        assert observation.available is False
        assert "UIA timeout" in observation.detail


class TestScreenshotFallback:
    """Gate 11."""

    def test_fallback_requires_explicit_authorisation(self):
        backend = CountingScreenBackend()
        observation = capture_with_fallback(
            approved_scope=CaptureScope(ScopeKind.WINDOW, window_id="w1"),
            allow_screenshot_fallback=False,
            accessibility_provider=NullAccessibilityProvider(),
            screen_backend=backend,
        )
        assert observation.used_screenshot is False
        assert backend.grabs == 0
        assert "did not authorise" in observation.fallback_refused_reason

    def test_an_authorised_fallback_records_why_it_was_needed(self):
        backend = CountingScreenBackend()
        observation = capture_with_fallback(
            approved_scope=CaptureScope(ScopeKind.WINDOW, window_id="w1"),
            allow_screenshot_fallback=True,
            accessibility_provider=NullAccessibilityProvider(),
            screen_backend=backend,
        )
        assert observation.used_screenshot is True
        assert backend.grabs == 1
        assert observation.fallback_reason, "the reason must be recorded"
        assert "accessibility" in observation.fallback_reason
        assert observation.evidence_reference.startswith("sha256:")

    def test_which_scope_was_captured_is_recorded(self):
        observation = capture_with_fallback(
            approved_scope=CaptureScope(
                ScopeKind.REGION,
                display_id="1",
                rect=(0, 0, 640, 480),
            ),
            allow_screenshot_fallback=True,
            accessibility_provider=NullAccessibilityProvider(),
            screen_backend=CountingScreenBackend(),
        )
        assert "region 640x480" in observation.scope_description

    def test_an_unavailable_backend_is_truthful_not_a_silent_no_op(self):
        observation = capture_with_fallback(
            approved_scope=CaptureScope(ScopeKind.WINDOW, window_id="w1"),
            allow_screenshot_fallback=True,
            accessibility_provider=NullAccessibilityProvider(),
            screen_backend=CountingScreenBackend(available=False),
        )
        assert observation.used_screenshot is False
        assert "unavailable" in observation.fallback_refused_reason

    def test_a_failing_grab_is_reported_not_swallowed(self):
        class Failing(CountingScreenBackend):
            def grab(self, scope):
                raise RuntimeError("display disconnected")

        observation = capture_with_fallback(
            approved_scope=CaptureScope(ScopeKind.WINDOW, window_id="w1"),
            allow_screenshot_fallback=True,
            accessibility_provider=NullAccessibilityProvider(),
            screen_backend=Failing(),
        )
        assert observation.used_screenshot is False
        assert "display disconnected" in observation.fallback_refused_reason


class TestNoRawImageRetention:
    """Gate 13."""

    def test_observation_has_no_image_field(self):
        observation = capture_with_fallback(
            approved_scope=CaptureScope(ScopeKind.WINDOW, window_id="w1"),
            allow_screenshot_fallback=True,
            accessibility_provider=NullAccessibilityProvider(),
            screen_backend=CountingScreenBackend(),
        )
        for forbidden in ("image", "png", "bytes", "path", "raw", "pixels"):
            assert not hasattr(observation, forbidden)

    def test_the_dataclass_declares_no_image_field(self):
        assert "image" not in ScreenObservation.__dataclass_fields__
        assert "path" not in ScreenObservation.__dataclass_fields__

    def test_the_module_never_opens_a_file_for_writing(self):
        source = (
            Path(__file__).resolve().parents[1] / "bartholomew" / "multimodal" / "screen.py"
        ).read_text()
        for forbidden in ("write_bytes", "imwrite", ".save(", "open("):
            assert forbidden not in source


class TestSecretsAreNotCarried:
    def test_a_password_field_value_is_never_read(self):
        provider = GoodProvider(
            elements=[
                {"role": "Edit", "name": "Password", "value": "hunter2", "focused": True},
                {"role": "Edit", "name": "Username", "value": "taylor"},
            ],
        )
        observation = observe_active_window(provider)
        password, username = observation.elements
        assert password.value is None
        assert username.value == "taylor"
        assert observation.omitted_secret_fields == 1
        assert observation.classification.privacy_class is PrivacyClass.RESTRICTED
        assert "hunter2" not in str(observation.as_dict())

    @pytest.mark.parametrize(
        "name",
        ["Password", "PIN", "card number", "api_key", "Recovery Code", "CVV"],
    )
    def test_secret_field_names_are_recognised(self, name):
        observation = observe_active_window(
            GoodProvider(elements=[{"role": "Edit", "name": name, "value": "x"}]),
        )
        assert observation.elements[0].value is None

    def test_secret_text_inside_an_ordinary_field_is_redacted(self):
        observation = observe_active_window(
            GoodProvider(
                elements=[
                    {"role": "Edit", "name": "Notes", "value": "token: sk-abcdefghijklmnopqrst"},
                ],
            ),
        )
        assert "sk-abcdefghijklmnopqrst" not in str(observation.as_dict())
        assert observation.classification.redactions

    def test_elements_are_bounded(self):
        many = [{"role": "Text", "name": f"item {i}", "value": "x"} for i in range(500)]
        observation = observe_active_window(GoodProvider(elements=many))
        assert len(observation.elements) <= 40
        assert observation.classification.truncated is True


class TestDerivedEventProvenance:
    """Gate 14: every derived observation carries full provenance."""

    def _assert_envelope(self, envelope):
        for field in (
            "schema_version",
            "event_id",
            "event_type",
            "tenant_id",
            "source",
            "occurred_at",
            "captured_at",
            "correlation_id",
            "causation_id",
            "payload",
            "payload_sha256",
            "privacy_class",
            "retention_class",
        ):
            assert field in envelope, f"missing §3.1 field: {field}"
        assert envelope["source"]["device_id"] == "device-1"
        assert envelope["source"]["principal_id"] == "user:taylor"
        assert envelope["source"]["verification"] == "claimed"
        assert envelope["event_type"] in EVENT_TYPES
        assert envelope["payload"]["session_id"].startswith("mms_")
        assert envelope["privacy_class"] in {p.value for p in PrivacyClass}
        assert envelope["retention_class"] in {r.value for r in RetentionClass}

    def test_screen_event_provenance(self):
        session = _session()
        observation = capture_with_fallback(
            approved_scope=session.scope,
            allow_screenshot_fallback=True,
            accessibility_provider=NullAccessibilityProvider(),
            screen_backend=CountingScreenBackend(),
        )
        envelope = serialize_screen(session, observation)
        self._assert_envelope(envelope)
        assert envelope["payload"]["used_screenshot"] is True
        assert envelope["payload"]["fallback_reason"]

    def test_accessibility_event_provenance(self):
        envelope = serialize_accessibility(_session(), observe_active_window(GoodProvider()))
        self._assert_envelope(envelope)

    def test_microphone_event_provenance(self):
        from bartholomew.multimodal.microphone import MicrophoneObservation
        from bartholomew.multimodal.privacy import Classification

        session = _session(Modality.MICROPHONE)
        envelope = serialize_microphone(
            session,
            MicrophoneObservation("hello", Classification(), 2.0),
        )
        self._assert_envelope(envelope)
        assert envelope["privacy_class"] == "sensitive"
        assert envelope["retention_class"] == "ephemeral"

    def test_speech_event_provenance(self):
        from bartholomew.multimodal.speech import SpeechOutcome

        envelope = serialize_speech(
            _session(Modality.SPOKEN_OUTPUT),
            SpeechOutcome(spoken=True, detail=None),
        )
        self._assert_envelope(envelope)

    def test_session_state_event_is_audit_class(self):
        envelope = serialize_session_state(_session())
        self._assert_envelope(envelope)
        assert envelope["retention_class"] == "audit"
        assert envelope["privacy_class"] == "context_only"

    def test_captured_at_is_left_for_ingress(self):
        """§3.1: ingress assigns captured_at. Inventing one would be a lie."""
        assert serialize_session_state(_session())["captured_at"] is None

    def test_event_ids_are_content_derived_and_stable(self):
        session = _session()
        first = serialize_accessibility(session, observe_active_window(GoodProvider()))
        second = serialize_accessibility(session, observe_active_window(GoodProvider()))
        assert first["event_id"] == second["event_id"]

    def test_different_content_yields_different_ids(self):
        session = _session()
        a = serialize_accessibility(session, observe_active_window(GoodProvider()))
        b = serialize_accessibility(
            session,
            observe_active_window(GoodProvider(elements=[{"role": "Button", "name": "Other"}])),
        )
        assert a["event_id"] != b["event_id"]

    def test_an_unregistered_event_type_is_refused(self):
        from bartholomew.multimodal.events import build_envelope
        from bartholomew.multimodal.privacy import Classification

        with pytest.raises(ValueError, match="unregistered"):
            build_envelope(_session(), "multimodal.webcam.frame", {}, Classification())

    def test_an_oversized_payload_is_refused_not_truncated(self):
        from bartholomew.multimodal.events import (
            EVENT_TYPE_SESSION_STATE,
            build_envelope,
        )
        from bartholomew.multimodal.privacy import Classification

        with pytest.raises(ValueError, match="exceeds"):
            build_envelope(
                _session(),
                EVENT_TYPE_SESSION_STATE,
                {"blob": "x" * 40_000},
                Classification(),
            )

    def test_the_default_sink_owns_no_delivery_path(self):
        """C must not become an event authority: the default sink drops."""
        sink = NullEventSink()
        assert sink.submit(serialize_session_state(_session())) is None
