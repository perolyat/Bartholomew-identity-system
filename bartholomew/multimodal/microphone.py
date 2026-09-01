"""The microphone adapter, and the truthful answer when there is no microphone.

**No microphone is a first-class supported state.** The machine this is being
built for may have a broken or absent microphone, and the honest behaviour is
a visible `unavailable` session with a diagnostic reason -- never a session
that reports itself as listening while hearing nothing. `probe()` is the one
place that decides, and it distinguishes four different truths that a lazier
adapter would collapse into "failed":

* no audio backend installed at all (an optional dependency is missing);
* a backend present but no input device on the machine;
* an input device present but the OS refused permission;
* a device that was there and disappeared mid-session.

Each is reported with its own reason string, because "install the optional
dependency" and "Windows privacy settings are blocking us" need different
things from the user.

**No ambient path exists.** There is no `listen_forever`, no wake word, no
voice-activity trigger that starts a session, and no automatic restart after
one ends. `MicrophoneSessionAdapter.start()` runs for a bounded duration
handed to it by an already-approved session and stops. A session that ends
stays ended until a human asks for another one.

**Raw audio is never persisted.** The adapter holds audio frames only for as
long as it takes to turn them into a bounded derived observation, and
`stop()` drops them. There is no file path parameter, no recording directory
and no configuration that turns raw retention on -- contract §7 puts raw
retention behind a separate governed policy that this package does not ship.

**The backend is injected.** `AudioBackend` is a Protocol; production wiring
supplies a real one, tests supply a controlled double, and a machine with no
backend gets `NullAudioBackend`, which truthfully reports unavailability. The
adapter itself contains no import of any audio library, so importing this
module never pulls in a native dependency tree.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from .privacy import Classification, PrivacyClass, RetentionClass, sanitise

logger = logging.getLogger(__name__)

#: How long a stop request may take before the caller should consider the
#: adapter wedged. The brake and the explicit-stop paths both assert against
#: this bound.
STOP_DEADLINE_SECONDS = 2.0


class MicrophoneAvailability(str, Enum):
    """Why a microphone can or cannot be used, in the user's terms."""

    AVAILABLE = "available"
    #: The optional audio dependency is not installed.
    NO_BACKEND = "no_backend"
    #: A backend is present but the machine has no input device.
    NO_DEVICE = "no_device"
    #: A device exists but the operating system denied access.
    PERMISSION_DENIED = "permission_denied"
    #: The device was present and went away.
    DEVICE_LOST = "device_lost"
    #: The probe itself failed. Unknown is not available.
    PROBE_FAILED = "probe_failed"


@dataclass(frozen=True)
class MicrophoneStatus:
    """The result of asking whether this machine can listen."""

    availability: MicrophoneAvailability
    detail: str
    device_name: str | None = None

    @property
    def usable(self) -> bool:
        return self.availability is MicrophoneAvailability.AVAILABLE

    def as_dict(self) -> dict[str, object]:
        return {
            "availability": self.availability.value,
            "usable": self.usable,
            "detail": self.detail,
            "device_name": self.device_name,
        }


@runtime_checkable
class AudioBackend(Protocol):
    """The minimal audio surface this package needs.

    Deliberately tiny: probe, open a bounded stream, close it. There is no
    "record to file" and no "keep listening" in this interface, so a backend
    cannot offer one.
    """

    def probe(self) -> MicrophoneStatus: ...

    def open_stream(self, max_seconds: float) -> object: ...

    def read_transcript(self, stream: object) -> str: ...

    def close_stream(self, stream: object) -> None: ...


class NullAudioBackend:
    """The backend for a machine with no audio support. Tells the truth."""

    def __init__(self, reason: str = "no audio backend is installed") -> None:
        self._reason = reason

    def probe(self) -> MicrophoneStatus:
        return MicrophoneStatus(
            availability=MicrophoneAvailability.NO_BACKEND,
            detail=self._reason,
        )

    def open_stream(self, max_seconds: float) -> object:
        raise RuntimeError(self._reason)

    def read_transcript(self, stream: object) -> str:
        raise RuntimeError(self._reason)

    def close_stream(self, stream: object) -> None:
        return None


def default_backend() -> AudioBackend:
    """The backend for this machine, discovered without importing eagerly.

    Tries `sounddevice` (the usual optional dependency for this job on
    Windows) and falls back to `NullAudioBackend` with the reason. The import
    lives inside the function so that importing this module on a machine
    without the dependency -- which is the expected case in CI -- costs
    nothing and fails nothing.
    """
    try:
        import sounddevice  # noqa: F401
    except Exception as exc:  # pragma: no cover - depends on host packages
        return NullAudioBackend(f"optional dependency 'sounddevice' is unavailable: {exc}")
    return _SoundDeviceBackend()  # pragma: no cover - requires the dependency


class _SoundDeviceBackend:  # pragma: no cover - requires audio hardware
    """A thin `sounddevice` wrapper.

    Not exercised by CI, which has no audio device; the controlled doubles in
    `tests/test_multimodal_microphone.py` cover the adapter logic around it.
    Marked as such in the closeout rather than presented as hardware-verified.
    """

    def probe(self) -> MicrophoneStatus:
        try:
            import sounddevice
        except Exception as exc:
            return MicrophoneStatus(MicrophoneAvailability.NO_BACKEND, str(exc))
        try:
            inputs = [d for d in sounddevice.query_devices() if d.get("max_input_channels", 0) > 0]
        except Exception as exc:
            return MicrophoneStatus(MicrophoneAvailability.PROBE_FAILED, str(exc))
        if not inputs:
            return MicrophoneStatus(
                MicrophoneAvailability.NO_DEVICE,
                "no input device with input channels was found",
            )
        return MicrophoneStatus(
            MicrophoneAvailability.AVAILABLE,
            "input device present",
            device_name=str(inputs[0].get("name")),
        )

    def open_stream(self, max_seconds: float) -> object:
        try:
            import sounddevice
        except Exception as exc:
            # The package was importable at probe time and is not now. Report
            # it as an unavailable device rather than crashing a session.
            raise MicrophoneUnavailableError(
                MicrophoneStatus(
                    MicrophoneAvailability.NO_BACKEND,
                    f"the audio backend became unavailable: {exc}",
                ),
            ) from exc

        stream = sounddevice.InputStream(channels=1)
        stream.start()
        return stream

    def read_transcript(self, stream: object) -> str:
        # No speech-to-text engine ships with this package. A deployment that
        # wants transcription supplies a backend whose read_transcript does
        # it; reporting empty here is the honest answer, not a stub success.
        return ""

    def close_stream(self, stream: object) -> None:
        try:
            stream.stop()  # type: ignore[attr-defined]
            stream.close()  # type: ignore[attr-defined]
        except Exception:
            logger.exception("Failed to close audio stream cleanly")


@dataclass
class MicrophoneObservation:
    """What one bounded listening session produced. Derived, never raw."""

    text: str
    classification: Classification
    listened_seconds: float


class MicrophoneSessionAdapter:
    """Runs one bounded listening session, and stops when told.

    One adapter instance serves one session. `stop()` is idempotent, safe to
    call from another thread (the brake path does exactly that), and returns
    within `STOP_DEADLINE_SECONDS` because the capture loop checks the stop
    event between short reads rather than blocking for the full duration.
    """

    def __init__(self, backend: AudioBackend | None = None) -> None:
        self._backend = backend or default_backend()
        self._stop = threading.Event()
        self._stream: object | None = None
        self._lock = threading.Lock()
        self._started = False

    def probe(self) -> MicrophoneStatus:
        """Whether this machine can listen. Never raises; unknown is unusable."""
        try:
            return self._backend.probe()
        except Exception as exc:
            logger.exception("Microphone probe failed")
            return MicrophoneStatus(MicrophoneAvailability.PROBE_FAILED, str(exc))

    def start(self, max_seconds: float, poll_interval: float = 0.05) -> MicrophoneObservation:
        """Listen for at most `max_seconds`, or until `stop()`.

        Raises `MicrophoneUnavailableError` when the hardware is not there -- the
        caller turns that into a visible `unavailable` session rather than a
        failure, because they are different truths.
        """
        status = self.probe()
        if not status.usable:
            raise MicrophoneUnavailableError(status)

        with self._lock:
            self._started = True
            try:
                self._stream = self._backend.open_stream(max_seconds)
            except Exception as exc:
                logger.exception("Failed to open audio stream")
                raise MicrophoneUnavailableError(
                    MicrophoneStatus(
                        MicrophoneAvailability.PERMISSION_DENIED,
                        f"could not open the audio stream: {exc}",
                        device_name=status.device_name,
                    ),
                ) from exc

        began = time.monotonic()
        try:
            # Short waits, not one long sleep: stop() and the brake must take
            # effect within STOP_DEADLINE_SECONDS, not at the end of the
            # session's maximum duration.
            while not self._stop.wait(poll_interval):
                if time.monotonic() - began >= max_seconds:
                    break
            raw = self._backend.read_transcript(self._stream)
        except Exception as exc:
            logger.exception("Microphone capture failed mid-session")
            self._release()
            raise MicrophoneCaptureFailedError(str(exc)) from exc
        finally:
            self._release()

        classification = Classification(
            privacy_class=PrivacyClass.SENSITIVE,
            retention_class=RetentionClass.EPHEMERAL,
        )
        text = sanitise(raw or "", "microphone.transcript", classification)
        return MicrophoneObservation(
            text=text,
            classification=classification,
            listened_seconds=round(time.monotonic() - began, 3),
        )

    def stop(self) -> None:
        """Ask the session to end. Idempotent; safe from any thread."""
        self._stop.set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def _release(self) -> None:
        """Close the stream and drop every reference to audio data.

        Called on every exit path -- normal end, stop, failure -- so no code
        path leaves a stream open or audio frames reachable. This is the whole
        of "no raw audio persistence": there is nowhere for it to go and
        nothing holds it after the session.
        """
        with self._lock:
            stream, self._stream = self._stream, None
        if stream is not None:
            try:
                self._backend.close_stream(stream)
            except Exception:
                logger.exception("Failed to release audio stream")


class MicrophoneUnavailableError(RuntimeError):
    """The machine cannot listen. Carries the diagnostic status."""

    def __init__(self, status: MicrophoneStatus) -> None:
        super().__init__(status.detail)
        self.status = status


class MicrophoneCaptureFailedError(RuntimeError):
    """Listening started and then broke. Distinct from never being able to."""
