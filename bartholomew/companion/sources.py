"""Where observations come from: a live host, or a pre-recorded set of observations.

Two sources, one interface. `LiveObservationSource` reads the machine through a
`HostProbe`; `SyntheticObservationSource` replays a fixed list. The runner is
written against the interface and cannot tell them apart, which is what makes
the deterministic tests evidence about the real path rather than about a
parallel one.

Both are **pull-only**: the runner asks, the source answers. Neither has a
method that acts on the machine, and neither receives anything from Bartholomew
-- an observation source has no inbound channel at all, so there is nowhere for
a server response to become a local instruction.

*Change-only emission.* `LiveObservationSource` reports activity state and
foreground application only when they differ from what it last reported.
Sampling every few seconds and sending every sample would build a
second-by-second log of someone's day out of fields that were each individually
minimal; reporting transitions keeps the durable record proportionate to what
this slice actually needs.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol

from . import observation as obs
from .observation import DeviceObservation
from .probes import HostProbe, NullProbe, platform_name

#: Seconds of no input after which the person is reported idle. Coarse on
#: purpose: this is a state, not a measurement of anyone's attention.
DEFAULT_IDLE_THRESHOLD_SECONDS = 300


class ObservationSource(Protocol):
    """Produces zero or more observations per poll. Never acts."""

    def poll(self, next_sequence: int) -> Sequence[DeviceObservation]:
        """Observations available now, numbered from `next_sequence`."""
        ...


class SyntheticObservationSource:
    """Replays a pre-recorded observation list. Deterministic and offline.

    Built from plain descriptions rather than `DeviceObservation` instances so a
    test can state what it wants observed without also restating the sequence
    numbering the runner owns. Touches no host state whatsoever, so a test using
    it is reproducible on any machine and in CI.
    """

    def __init__(self, recording: Iterable[tuple[str, dict]], *, device_id: str):
        self._recording = list(recording)
        self._device_id = device_id
        self._index = 0

    @property
    def exhausted(self) -> bool:
        return self._index >= len(self._recording)

    def poll(self, next_sequence: int) -> Sequence[DeviceObservation]:
        if self.exhausted:
            return []
        kind, values = self._recording[self._index]
        self._index += 1
        return [_build(obs.ObservationKind(kind), self._device_id, next_sequence, values)]


class LiveObservationSource:
    """Reads the real machine, through a probe, within the permitted vocabulary."""

    def __init__(
        self,
        *,
        device_id: str,
        probe: HostProbe | None = None,
        idle_threshold_seconds: int = DEFAULT_IDLE_THRESHOLD_SECONDS,
    ):
        self._device_id = device_id
        self._probe = probe or NullProbe()
        self._idle_threshold = idle_threshold_seconds
        self._last_activity_state: str | None = None
        self._last_application: str | None = None

    def poll(self, next_sequence: int) -> Sequence[DeviceObservation]:
        out: list[DeviceObservation] = []
        seq = next_sequence

        idle = self._probe.idle_seconds()
        if idle is not None:
            state = "idle" if idle >= self._idle_threshold else "active"
            if state != self._last_activity_state:
                self._last_activity_state = state
                out.append(
                    obs.activity(
                        self._device_id,
                        seq,
                        active=(state == "active"),
                        # Only meaningful while idle; while active it would be a
                        # near-continuous readout of typing rhythm.
                        idle_seconds=int(idle) if state == "idle" else None,
                    ),
                )
                seq += 1

        raw_app = self._probe.foreground_application()
        if raw_app:
            try:
                app = obs.normalise_application(raw_app)
            except obs.ObservationError:
                app = None
            if app and app != self._last_application:
                self._last_application = app
                out.append(obs.foreground_app(self._device_id, seq, application=app))
                seq += 1

        return out


def _build(kind: obs.ObservationKind, device_id: str, sequence: int, values: dict):
    """Construct one observation of `kind` through its own constructor.

    Routed through the per-kind constructors rather than `DeviceObservation`
    directly so a synthetic observation passes exactly the validation and
    normalisation a live one does -- a synthetic source that could express a
    payload a live source cannot would make the deterministic tests weaker than
    they look.
    """
    if kind is obs.ObservationKind.PRESENCE:
        return obs.presence(device_id, sequence, online=values["state"] == "online")
    if kind is obs.ObservationKind.ACTIVITY:
        return obs.activity(
            device_id,
            sequence,
            active=values["state"] == "active",
            idle_seconds=values.get("idle_seconds"),
        )
    if kind is obs.ObservationKind.FOREGROUND_APP:
        return obs.foreground_app(device_id, sequence, application=values["application"])
    return obs.system_state(
        device_id,
        sequence,
        platform_name=values.get("platform", platform_name()),
        companion_version=values.get("companion_version", "synthetic"),
    )
