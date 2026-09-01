"""Envelope shape, idempotency, retry and restart recovery.

Deterministic throughout: a synthetic observation source and a recording fake
client, so these tests say the same thing on every machine. The HTTP boundary
itself is proven separately, against a real server, in
`tests/integration/test_companion_inbound_http.py`.
"""

from __future__ import annotations

import pytest

from bartholomew.companion import observation as obs
from bartholomew.companion.client import DeliveryResult, DeliveryStatus
from bartholomew.companion.config import CompanionConfig
from bartholomew.companion.envelope import derive_event_id, to_inbound_envelope
from bartholomew.companion.runner import CompanionRunner
from bartholomew.companion.sources import SyntheticObservationSource
from bartholomew.companion.state import StateFile


class FakeClient:
    """Records every envelope and answers with a scripted status.

    Models the inbound route's idempotency the way the route really behaves:
    a repeat of an `event_id` it has already accepted comes back 200/duplicate,
    not 202, so a test cannot mistake a re-delivery for a second capture.
    """

    def __init__(self, statuses=None):
        self.envelopes = []
        self.captured_ids = set()
        self._statuses = list(statuses or [])

    def submit(self, envelope):
        self.envelopes.append(dict(envelope))
        if self._statuses:
            forced = self._statuses.pop(0)
            if forced is not None:
                return forced
        event_id = envelope["event_id"]
        if event_id in self.captured_ids:
            return DeliveryResult(DeliveryStatus.DUPLICATE, 200, "already captured")
        self.captured_ids.add(event_id)
        return DeliveryResult(DeliveryStatus.CAPTURED, 202, "captured")


@pytest.fixture
def config(tmp_path):
    return CompanionConfig(
        base_url="http://127.0.0.1:1",
        source_id="desk-companion",
        device_id="desk-pc",
        state_path=tmp_path / "state.json",
        poll_seconds=0.01,
        max_attempts=3,
    )


def _runner(config, script, client, **kw):
    return CompanionRunner(
        config,
        SyntheticObservationSource(script, device_id=config.device_id),
        client=client,
        sleep=lambda _s: None,
        **kw,
    )


# --- envelope ---------------------------------------------------------------


def test_the_envelope_is_exactly_the_existing_inbound_contract():
    """No new field. The companion adds nothing to the inbound envelope."""
    o = obs.foreground_app("desk-pc", 7, application="chrome", observed_at="2026-08-31T10:00:00Z")
    envelope = to_inbound_envelope(o, source_id="desk-companion")
    assert set(envelope) == {"source_id", "event_id", "event_type", "payload", "occurred_at"}
    assert envelope["source_id"] == "desk-companion"
    assert envelope["event_type"] == "device.companion.foreground_app"
    assert envelope["occurred_at"] == "2026-08-31T10:00:00Z"
    assert envelope["payload"] == {"device_id": "desk-pc", "application": "chrome"}


def test_device_provenance_travels_with_every_observation():
    for o in (
        obs.presence("desk-pc", 0, online=True),
        obs.activity("desk-pc", 1, active=True),
        obs.foreground_app("desk-pc", 2, application="chrome"),
        obs.system_state("desk-pc", 3, platform_name="linux", companion_version="x"),
    ):
        assert to_inbound_envelope(o, source_id="s")["payload"]["device_id"] == "desk-pc"


def test_event_ids_are_stable_for_the_same_observation_and_distinct_otherwise():
    a = obs.activity("desk-pc", 5, active=True, observed_at="2026-08-31T10:00:00Z")
    same = obs.activity("desk-pc", 5, active=True, observed_at="2026-08-31T10:00:00Z")
    later = obs.activity("desk-pc", 6, active=True, observed_at="2026-08-31T10:00:00Z")
    other_device = obs.activity("laptop", 5, active=True, observed_at="2026-08-31T10:00:00Z")

    assert derive_event_id(a) == derive_event_id(same)
    assert derive_event_id(a) != derive_event_id(later)
    assert derive_event_id(a) != derive_event_id(other_device)


# --- idempotency and retry ---------------------------------------------------


def test_a_retried_delivery_does_not_become_a_second_capture(config):
    """The first attempt times out; the retry carries the same id."""
    client = FakeClient(statuses=[DeliveryResult(DeliveryStatus.RETRYABLE, None, "timeout")])
    runner = _runner(config, [("presence", {"state": "online"})], client)

    runner.poll_once()

    assert len(client.envelopes) == 2
    assert client.envelopes[0]["event_id"] == client.envelopes[1]["event_id"]
    assert runner.summary.captured == 1
    assert runner.summary.duplicates == 0


def test_a_duplicate_is_never_counted_as_a_capture(config):
    client = FakeClient()
    o = obs.presence("desk-pc", 0, online=True, observed_at="2026-08-31T10:00:00Z")
    runner = _runner(config, [], client)

    first = runner.submit_observation(o)
    second = runner.submit_observation(o)

    assert first.status is DeliveryStatus.CAPTURED
    assert second.status is DeliveryStatus.DUPLICATE
    assert second.delivered  # recorded, but not newly captured
    assert runner.summary.captured == 1
    assert runner.summary.duplicates == 1


def test_a_refusal_is_terminal_and_is_not_retried(config):
    refusal = DeliveryResult(DeliveryStatus.REFUSED, 401, "source could not be verified")
    client = FakeClient(statuses=[refusal, refusal, refusal])
    runner = _runner(config, [("presence", {"state": "online"})], client)

    runner.poll_once()

    assert len(client.envelopes) == 1  # not max_attempts
    assert runner.summary.refused == 1
    assert runner.summary.captured == 0


def test_a_brake_refusal_is_retried_because_it_genuinely_resolves(config):
    """503 is the Parking Brake or an unavailable store: retrying is honest."""
    unavailable = DeliveryResult(DeliveryStatus.RETRYABLE, 503, "parking brake engaged")
    client = FakeClient(statuses=[unavailable, unavailable, unavailable])
    runner = _runner(config, [("presence", {"state": "online"})], client)

    runner.poll_once()

    assert len(client.envelopes) == config.max_attempts
    assert runner.summary.undelivered == 1
    assert runner.summary.captured == 0


# --- restart ------------------------------------------------------------------


def test_a_companion_killed_mid_flight_redelivers_the_same_event_on_restart(config):
    """The in-doubt envelope is re-sent, and lands on the existing row."""
    first_client = FakeClient(statuses=[DeliveryResult(DeliveryStatus.RETRYABLE, None, "died")] * 3)
    first = _runner(config, [("foreground_app", {"application": "chrome"})], first_client)
    first.poll_once()

    in_flight = StateFile(config.state_path).load().pending
    assert in_flight is not None, "an undelivered observation must stay pending"

    # A new process, new client -- but the same state file. The service had in
    # fact captured it; the acknowledgement is what was lost.
    second_client = FakeClient()
    second_client.captured_ids.add(in_flight["event_id"])
    second = _runner(config, [], second_client)
    result = second.resume_pending()

    assert result.status is DeliveryStatus.DUPLICATE
    assert second.summary.captured == 0
    assert StateFile(config.state_path).load().pending is None


def test_sequence_numbers_do_not_restart_after_a_restart(config):
    client = FakeClient()
    _runner(config, [("presence", {"state": "online"})] * 2, client).run(cycles=2)
    after_first = StateFile(config.state_path).load().sequence
    assert after_first > 0

    _runner(config, [("presence", {"state": "online"})], client).run(cycles=1)
    assert StateFile(config.state_path).load().sequence > after_first
    # Every id across both runs is distinct: no restart collision.
    ids = [e["event_id"] for e in client.envelopes]
    assert len(set(ids)) == len(ids)


def test_corrupt_state_does_not_reuse_ids_already_delivered(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    assert StateFile(path).load().sequence > 0


def test_a_full_run_announces_presence_and_system_state(config):
    client = FakeClient()
    summary = _runner(config, [("activity", {"state": "active"})], client).run(cycles=1)

    types = [e["event_type"] for e in client.envelopes]
    assert types[0] == "device.companion.presence"
    assert "device.companion.system_state" in types
    assert types[-1] == "device.companion.presence"
    assert client.envelopes[-1]["payload"]["state"] == "offline"
    assert summary.captured == len(client.envelopes)
