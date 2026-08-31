"""What the companion may and may not observe.

These tests are about the *vocabulary*, which is where privacy minimisation
actually lives in this package: a field that cannot be expressed cannot be
collected by a careless collector later.
"""

from __future__ import annotations

import pytest

from bartholomew.companion import observation as obs
from bartholomew.companion.observation import (
    ALL_PAYLOAD_KEYS,
    ALLOWED_PAYLOAD_KEYS,
    DeviceObservation,
    ObservationError,
    ObservationKind,
)


def test_the_permitted_payload_surface_is_exactly_this():
    """The whole privacy surface, pinned. Widening it must fail this test."""
    assert ALL_PAYLOAD_KEYS == {
        "device_id",
        "state",
        "idle_seconds",
        "application",
        "platform",
        "companion_version",
    }
    assert set(ALLOWED_PAYLOAD_KEYS) == set(ObservationKind)


def test_an_observation_cannot_carry_a_key_outside_its_kind():
    with pytest.raises(ObservationError):
        DeviceObservation(
            kind=ObservationKind.FOREGROUND_APP,
            device_id="desk-pc",
            sequence=0,
            observed_at=obs.utc_now_iso(),
            values={"application": "chrome", "window_title": "Bank statement"},
        )


def test_foreground_app_reduces_to_a_bare_application_name():
    """A path leaks an account name and a title leaks content. Neither survives."""
    o = obs.foreground_app("desk-pc", 0, application=r"C:\Users\taylor\AppData\Chrome.exe")
    assert o.payload() == {"device_id": "desk-pc", "application": "chrome"}


def test_a_window_title_smuggled_into_the_application_field_is_reduced_to_a_token():
    o = obs.foreground_app("desk-pc", 0, application="Quarterly results - private - Chrome")
    app = o.payload()["application"]
    assert " " not in app
    assert len(app) <= obs.MAX_APPLICATION_NAME


def test_idle_seconds_is_only_carried_when_the_state_is_idle_in_the_live_source():
    from bartholomew.companion.sources import LiveObservationSource

    class Probe:
        name = "fake"

        def __init__(self, idle):
            self._idle = idle

        def idle_seconds(self):
            return self._idle

        def foreground_application(self):
            return None

    source = LiveObservationSource(device_id="desk-pc", probe=Probe(2), idle_threshold_seconds=300)
    (active,) = source.poll(0)
    assert active.payload() == {"device_id": "desk-pc", "state": "active", "idle_seconds": None}

    source = LiveObservationSource(
        device_id="desk-pc",
        probe=Probe(900),
        idle_threshold_seconds=300,
    )
    (idle,) = source.poll(0)
    assert idle.payload()["state"] == "idle"
    assert idle.payload()["idle_seconds"] == 900


def test_invalid_states_are_refused():
    for kind, values in (
        (ObservationKind.PRESENCE, {"state": "maybe"}),
        (ObservationKind.ACTIVITY, {"state": "asleep"}),
        (ObservationKind.SYSTEM_STATE, {"platform": "beos", "companion_version": "x"}),
    ):
        with pytest.raises(ObservationError):
            DeviceObservation(
                kind=kind,
                device_id="desk-pc",
                sequence=0,
                observed_at=obs.utc_now_iso(),
                values=values,
            )


def test_the_null_probe_reports_unknown_rather_than_guessing():
    from bartholomew.companion.probes import NullProbe
    from bartholomew.companion.sources import LiveObservationSource

    probe = NullProbe()
    assert probe.idle_seconds() is None
    assert probe.foreground_application() is None
    # An unsupported platform emits nothing rather than a fabricated state.
    assert LiveObservationSource(device_id="desk-pc", probe=probe).poll(0) == []


def test_live_source_reports_transitions_not_every_sample():
    """Sampling continuously and sending every sample would be a day-log."""
    from bartholomew.companion.sources import LiveObservationSource

    class Probe:
        name = "fake"
        idle = 1
        app = "chrome"

        def idle_seconds(self):
            return self.idle

        def foreground_application(self):
            return self.app

    probe = Probe()
    source = LiveObservationSource(device_id="desk-pc", probe=probe)
    first = source.poll(0)
    assert len(first) == 2  # activity + foreground app
    assert source.poll(2) == []  # nothing changed

    probe.app = "code"
    (changed,) = source.poll(2)
    assert changed.payload()["application"] == "code"
