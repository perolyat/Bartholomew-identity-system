"""Hardware that isn't there, hardware that goes away, and permission denial.

Acceptance gates covered: 7 (microphone absence produces a visible unavailable
state), 8 (device disappearance and OS permission denial are handled
truthfully), 13 (raw audio is not persisted by default), 18 (active state is
visible through the required surface).

The user's own microphone may be broken or absent, so that is treated here as
a first-class supported state with its own tests -- not as an error path.
"""

from __future__ import annotations

import pytest

from bartholomew.multimodal.devices import StaticCapabilityResolver
from bartholomew.multimodal.microphone import (
    MicrophoneAvailability,
    MicrophoneSessionAdapter,
    MicrophoneStatus,
    MicrophoneUnavailableError,
    NullAudioBackend,
)
from bartholomew.multimodal.modality import CAPABILITY_KIND, Modality
from bartholomew.multimodal.runtime import SessionRequest, start_session
from bartholomew.multimodal.session import SessionState
from bartholomew.multimodal.status import status_snapshot
from bartholomew.multimodal.store import SessionStore


class FakeBackend:
    """A controlled audio backend. Simulated, never real hardware."""

    def __init__(self, status: MicrophoneStatus, transcript: str = "", fail=None):
        self._status = status
        self._transcript = transcript
        self._fail = fail
        self.opened = 0
        self.closed = 0
        self.open_failure: Exception | None = None

    def probe(self):
        return self._status

    def open_stream(self, max_seconds):
        if self.open_failure:
            raise self.open_failure
        self.opened += 1
        return {"stream": True}

    def read_transcript(self, stream):
        if self._fail:
            raise self._fail
        return self._transcript

    def close_stream(self, stream):
        self.closed += 1


def _available(name="Microphone Array"):
    return MicrophoneStatus(MicrophoneAvailability.AVAILABLE, "present", device_name=name)


async def _start(store, backend, resolver=None, modality=Modality.MICROPHONE):
    resolver = resolver or StaticCapabilityResolver()
    resolver.declare("device-1", list(CAPABILITY_KIND.values()))

    async def seam(m, **kwargs):
        from bartholomew.kernel.runtime_contract import DeviceRuntimeResult

        return DeviceRuntimeResult(
            observation=None,
            candidate_action=None,
            governance_allowed=True,
            started=False,
            outcome="started",
            reason=None,
            result=None,
        )

    return await start_session(
        SessionRequest(
            tenant_id="tenant-1",
            principal_id="user:taylor",
            device_id="device-1",
            modality=modality,
            correlation_id="corr-1",
            max_duration_seconds=1,
        ),
        store=store,
        capability_resolver=resolver,
        microphone_backend=backend,
        seam=seam,
    )


class TestMissingMicrophone:
    """Gate 7. This is a supported state, not an exception."""

    @pytest.mark.parametrize(
        "availability,detail",
        [
            (MicrophoneAvailability.NO_BACKEND, "no audio library installed"),
            (MicrophoneAvailability.NO_DEVICE, "no input device found"),
            (MicrophoneAvailability.PERMISSION_DENIED, "Windows denied microphone access"),
            (MicrophoneAvailability.PROBE_FAILED, "the audio subsystem errored"),
        ],
    )
    async def test_each_unavailable_reason_yields_a_visible_unavailable_session(
        self,
        availability,
        detail,
    ):
        store = SessionStore()
        backend = FakeBackend(MicrophoneStatus(availability, detail))
        result = await _start(store, backend)

        assert result.outcome == "unavailable"
        assert result.session.state is SessionState.UNAVAILABLE
        assert result.session.state is not SessionState.ACTIVE
        assert detail in result.session.outcome_reason
        assert availability.value in result.session.outcome_reason
        assert backend.opened == 0, "an absent microphone must never be opened"

    async def test_unavailable_session_never_reports_as_listening(self):
        """Gate 18: the status surface must not claim to be listening."""
        store = SessionStore()
        await _start(
            store,
            FakeBackend(MicrophoneStatus(MicrophoneAvailability.NO_DEVICE, "none")),
        )
        snapshot = status_snapshot(store, microphone_backend=NullAudioBackend())
        assert snapshot["listening"] is False
        assert snapshot["active_session_count"] == 0
        assert "not listening" in snapshot["summary"]

    def test_null_backend_probe_is_truthful(self):
        status = MicrophoneSessionAdapter(NullAudioBackend()).probe()
        assert status.usable is False
        assert status.availability is MicrophoneAvailability.NO_BACKEND

    def test_starting_an_unavailable_adapter_raises_rather_than_faking(self):
        adapter = MicrophoneSessionAdapter(NullAudioBackend())
        with pytest.raises(MicrophoneUnavailableError):
            adapter.start(1.0)

    def test_a_probe_that_explodes_is_unusable_not_available(self):
        class Exploding:
            def probe(self):
                raise RuntimeError("driver crash")

        status = MicrophoneSessionAdapter(Exploding()).probe()
        assert status.usable is False
        assert status.availability is MicrophoneAvailability.PROBE_FAILED

    def test_hardware_status_is_visible_with_no_session_running(self):
        snapshot = status_snapshot(SessionStore(), microphone_backend=NullAudioBackend())
        assert snapshot["hardware"]["microphone"]["usable"] is False
        assert snapshot["hardware"]["microphone"]["detail"]


class TestPermissionDenialAndDeviceLoss:
    """Gate 8."""

    def test_os_permission_denial_at_open_is_reported_as_unavailable(self):
        backend = FakeBackend(_available())
        backend.open_failure = PermissionError("access to the microphone was denied")
        adapter = MicrophoneSessionAdapter(backend)
        with pytest.raises(MicrophoneUnavailableError) as caught:
            adapter.start(1.0)
        assert caught.value.status.availability is MicrophoneAvailability.PERMISSION_DENIED
        assert "denied" in caught.value.status.detail

    def test_device_loss_mid_session_fails_the_session_truthfully(self):
        backend = FakeBackend(_available(), fail=OSError("device disconnected"))
        adapter = MicrophoneSessionAdapter(backend)
        from bartholomew.multimodal.microphone import MicrophoneCaptureFailedError

        with pytest.raises(MicrophoneCaptureFailedError, match="device disconnected"):
            adapter.start(0.2)
        assert backend.closed == 1, "the stream must be released even on failure"

    def test_stream_is_released_on_every_path(self):
        backend = FakeBackend(_available(), transcript="hello")
        adapter = MicrophoneSessionAdapter(backend)
        adapter.stop()
        adapter.start(0.2)
        assert backend.opened == 1
        assert backend.closed == 1


class TestNoRawAudioPersistence:
    """Gate 13."""

    def test_observation_carries_text_only(self):
        backend = FakeBackend(_available(), transcript="the meeting is at three")
        adapter = MicrophoneSessionAdapter(backend)
        adapter.stop()
        observation = adapter.start(0.2)

        assert observation.text == "the meeting is at three"
        for forbidden in ("audio", "frames", "samples", "pcm", "wav", "path", "bytes"):
            assert not hasattr(observation, forbidden)

    def test_adapter_holds_no_stream_after_a_session(self):
        backend = FakeBackend(_available(), transcript="x")
        adapter = MicrophoneSessionAdapter(backend)
        adapter.stop()
        adapter.start(0.2)
        assert adapter._stream is None

    def test_no_module_writes_audio_to_disk(self):
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[1] / "bartholomew" / "multimodal" / "microphone.py"
        ).read_text()
        for forbidden in ("open(", "write_bytes", "wave.", ".wav", "soundfile"):
            assert forbidden not in source

    def test_transcripts_are_redacted_and_bounded(self):
        backend = FakeBackend(_available(), transcript="my password: hunter2")
        adapter = MicrophoneSessionAdapter(backend)
        adapter.stop()
        observation = adapter.start(0.2)
        assert "hunter2" not in observation.text
        assert observation.classification.redactions


class TestStopIsPrompt:
    def test_stop_is_idempotent_and_immediate(self):
        adapter = MicrophoneSessionAdapter(FakeBackend(_available()))
        adapter.stop()
        adapter.stop()
        assert adapter.stopped is True

    def test_a_running_session_ends_promptly_on_stop(self):
        import threading
        import time

        backend = FakeBackend(_available(), transcript="ok")
        adapter = MicrophoneSessionAdapter(backend)
        done = threading.Event()

        def run():
            adapter.start(30.0)  # far longer than the test will wait
            done.set()

        threading.Thread(target=run, daemon=True).start()
        time.sleep(0.1)
        began = time.monotonic()
        adapter.stop()
        assert done.wait(timeout=2.0), "stop must take effect within the deadline"
        assert time.monotonic() - began < 2.0
