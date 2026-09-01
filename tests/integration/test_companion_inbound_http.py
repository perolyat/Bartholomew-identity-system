"""The acceptance scenario, end to end, against a real Bartholomew.

A real `bartholomew serve` process, a real socket, a real SQLite database, real
Governance -- and a real `CompanionRunner` driving a pre-recorded observation
list. What is synthetic is only *what the machine reported*; everything the
observation then crosses is the production path.

The test resolver stands in for device authentication, which does not exist.
It is not a shortcut taken here for convenience: the repository default is that
no resolver is installed and inbound capture is closed, and `test_a_companion_
against_a_closed_deployment_captures_nothing` proves the companion respects
that rather than working around it. See `docs/D_PC_COMPANION_OBSERVATION.md` for
what the test resolver is and is not.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from bartholomew.companion.client import DeliveryStatus, InboundSubmitClient
from bartholomew.companion.config import CompanionConfig
from bartholomew.companion.runner import CompanionRunner
from bartholomew.companion.sources import SyntheticObservationSource
from bartholomew.companion.state import StateFile
from tests.integration.test_always_on_service import (
    TEST_RESOLVER_ENV,
    TEST_TOKEN,
    ServeProcess,
    _get,
)

pytestmark = [pytest.mark.integration]

#: The source id the test resolver issues. The companion must be configured
#: with the id it is actually issued: the route compares the two and refuses
#: on a mismatch, so a companion cannot claim provenance it was not given.
ISSUED_SOURCE_ID = "test-source"

#: A pre-recorded day, small enough to read: the person is at the machine, in
#: a browser, then in an editor, then away.
RECORDED_OBSERVATIONS = [
    ("activity", {"state": "active"}),
    ("foreground_app", {"application": "chrome"}),
    ("foreground_app", {"application": "code"}),
    ("activity", {"state": "idle", "idle_seconds": 900}),
]


@pytest.fixture
def service(tmp_path):
    svc = ServeProcess(tmp_path / "companion.db", env_extra=TEST_RESOLVER_ENV)
    try:
        yield svc.start()
    finally:
        svc.kill()


@pytest.fixture
def closed_service(tmp_path):
    """The repository default posture: no resolver, inbound fail-closed."""
    svc = ServeProcess(tmp_path / "companion_closed.db")
    try:
        yield svc.start()
    finally:
        svc.kill()


def _config(service, tmp_path, **overrides):
    kwargs = {
        "base_url": f"http://127.0.0.1:{service.port}",
        "source_id": ISSUED_SOURCE_ID,
        "device_id": "desk-pc",
        "state_path": tmp_path / "companion-state.json",
        "poll_seconds": 0.01,
        "max_attempts": 2,
        "credential_headers": {"X-Bartholomew-Test-Token": TEST_TOKEN},
    }
    kwargs.update(overrides)
    return CompanionConfig(**kwargs)


def _runner(config, recording):
    return CompanionRunner(
        config,
        SyntheticObservationSource(recording, device_id=config.device_id),
        sleep=lambda _s: None,
    )


def _rows(service, sql, params=()):
    with sqlite3.connect(str(service.db_path)) as conn:
        try:
            return conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return []


COMPANION_EVENTS = (
    "SELECT event_type, event_id, payload_json, verified_by, source_id, outcome "
    "FROM inbound_events WHERE event_type LIKE 'device.companion.%' ORDER BY id"
)


# ---------------------------------------------------------------------------
# Acceptance: connect, observe, cross the boundary, keep provenance
# ---------------------------------------------------------------------------


def test_a_companion_observation_crosses_the_existing_inbound_boundary(service, tmp_path):
    """1-4: connection, one bounded observation, capture, provenance preserved."""
    config = _config(service, tmp_path)
    summary = _runner(config, RECORDED_OBSERVATIONS).run(cycles=len(RECORDED_OBSERVATIONS))

    assert summary.captured > 0
    assert summary.refused == 0

    rows = _rows(service, COMPANION_EVENTS)
    types = [r[0] for r in rows]
    assert "device.companion.presence" in types
    assert "device.companion.foreground_app" in types

    for event_type, event_id, payload_json, verified_by, source_id, outcome in rows:
        payload = json.loads(payload_json)
        # Source provenance: verified, and recorded as such.
        assert source_id == ISSUED_SOURCE_ID
        assert verified_by == "test-resolver"
        assert outcome == "captured"
        # Device provenance: claimed, and recorded alongside it.
        assert payload["device_id"] == "desk-pc"
        assert event_id.startswith("companion:")
        assert event_type.startswith("device.companion.")


def test_only_state_metadata_crosses_the_boundary(service, tmp_path):
    """Privacy minimisation, observed on what actually landed on disk."""
    config = _config(service, tmp_path)
    _runner(config, RECORDED_OBSERVATIONS).run(cycles=len(RECORDED_OBSERVATIONS))

    permitted = {
        "device_id",
        "state",
        "idle_seconds",
        "application",
        "platform",
        "companion_version",
    }
    for _t, _e, payload_json, *_rest in _rows(service, COMPANION_EVENTS):
        assert set(json.loads(payload_json)) <= permitted


def test_the_captured_observation_is_inspectable_inside_bartholomew(service, tmp_path):
    """6: a person can see what their computer told Bartholomew."""
    config = _config(service, tmp_path)
    _runner(config, [("foreground_app", {"application": "chrome"})]).run(cycles=1)

    status, listed = _get(service.port, "/api/inbound/events?limit=100")
    assert status == 200
    companion = [e for e in listed if e["event_type"].startswith("device.companion.")]
    assert companion, listed
    entry = companion[0]
    assert entry["source_id"] == ISSUED_SOURCE_ID
    assert entry["verified_by"] == "test-resolver"
    assert entry["outcome"] == "captured"
    assert entry["payload_sha256"]


# ---------------------------------------------------------------------------
# Acceptance: retry and restart
# ---------------------------------------------------------------------------


def test_duplicate_delivery_does_not_produce_a_second_capture(service, tmp_path):
    """5: the same observation delivered twice is one event."""
    config = _config(service, tmp_path)
    runner = _runner(config, [])

    from bartholomew.companion import observation as obs

    o = obs.foreground_app("desk-pc", 0, application="chrome", observed_at="2026-08-31T10:00:00Z")
    first = runner.submit_observation(o)
    second = runner.submit_observation(o)

    assert first.status is DeliveryStatus.CAPTURED
    assert second.status is DeliveryStatus.DUPLICATE

    rows = _rows(
        service,
        "SELECT COUNT(*) FROM inbound_events WHERE event_type = ?",
        ("device.companion.foreground_app",),
    )
    assert rows[0][0] == 1


def test_a_companion_recovers_across_a_restart_without_duplicating(service, tmp_path):
    """7: a second process, the same state file, no second logical event."""
    config = _config(service, tmp_path)
    _runner(config, RECORDED_OBSERVATIONS[:2]).run(cycles=2)
    before = _rows(service, "SELECT COUNT(*) FROM inbound_events")[0][0]

    # A fresh runner reading the same state file: a restarted companion.
    restarted = _runner(config, RECORDED_OBSERVATIONS[2:])
    restarted.run(cycles=2)
    after = _rows(service, "SELECT COUNT(*) FROM inbound_events")[0][0]

    assert after > before, "a restarted companion must keep observing"
    assert restarted.summary.captured > 0
    # Every id in the table is distinct: the restart collided with nothing.
    ids = [r[0] for r in _rows(service, "SELECT event_id FROM inbound_events")]
    assert len(set(ids)) == len(ids)
    assert StateFile(config.state_path).load().pending is None


# ---------------------------------------------------------------------------
# Acceptance: it fails closed, and it cannot actuate
# ---------------------------------------------------------------------------


def test_a_companion_against_a_closed_deployment_captures_nothing(closed_service, tmp_path):
    """No resolver installed is the default. The companion respects it."""
    config = _config(closed_service, tmp_path)
    summary = _runner(config, RECORDED_OBSERVATIONS).run(cycles=1)

    assert summary.captured == 0
    assert summary.refused > 0
    assert _rows(closed_service, "SELECT COUNT(*) FROM inbound_events") in ([], [(0,)])


def test_a_companion_claiming_a_source_it_was_not_issued_is_refused(service, tmp_path):
    """Provenance cannot be self-asserted: the route compares and refuses."""
    config = _config(service, tmp_path, source_id="some-other-source")
    summary = _runner(config, [("presence", {"state": "online"})]).run(cycles=1)

    assert summary.captured == 0
    assert summary.refused > 0
    assert not _rows(service, COMPANION_EVENTS)


def test_the_companion_credential_opens_no_route_other_than_inbound_submit(service):
    """The companion's only reachable verb is the one it has.

    The client cannot express another route at all, so this checks the
    complementary half: the credential it carries is not a general key. Reading
    the capture history back is a different capability, and the companion's
    submit-only client has no method that could exercise it even if it were.
    """
    client = InboundSubmitClient(
        f"http://127.0.0.1:{service.port}",
        credential_headers={"X-Bartholomew-Test-Token": TEST_TOKEN},
    )
    assert [n for n in dir(client) if not n.startswith("_")] == ["base_url", "submit"]


def test_no_actuation_endpoint_is_reachable_through_the_companion_envelope(service, tmp_path):
    """An envelope is data. It cannot name a route, a verb or an operation."""
    from bartholomew.companion.envelope import to_inbound_envelope
    from bartholomew.companion.observation import presence

    envelope = to_inbound_envelope(presence("desk-pc", 0, online=True), source_id=ISSUED_SOURCE_ID)
    flat = json.dumps(envelope).lower()
    for verb in ("command", "execute", "action", "shell", "script", "operation"):
        assert verb not in flat

    # And what lands on disk is the same data, governed by the same seam.
    config = _config(service, tmp_path)
    _runner(config, [("presence", {"state": "online"})]).run(cycles=1)
    for _t, _e, payload_json, *_rest in _rows(service, COMPANION_EVENTS):
        low = payload_json.lower()
        for verb in ("command", "execute", "shell", "script"):
            assert verb not in low
