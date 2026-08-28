"""Inbound capture over real HTTP, against a real running service.

The boundary being proven here *is* the boundary, so none of it is mocked:
a real `bartholomew serve` process, real sockets, real SQLite, real
Governance. A test that mocked the HTTP layer would prove nothing about the
door this slice exists to build.

Reuses the service harness from `test_always_on_service.py` rather than
building a second one.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request

import pytest

from tests.integration.test_always_on_service import (
    REPO_ROOT,
    STARTUP_TIMEOUT,
    TEST_RESOLVER_ENV,
    TEST_TOKEN,
    ServeProcess,
    _env,
    _get,
    _post,
)

pytestmark = [pytest.mark.integration]

AUTH = {"X-Bartholomew-Test-Token": TEST_TOKEN}


def _event(event_id: str, **overrides) -> dict:
    body = {
        "source_id": "test-source",
        "event_id": event_id,
        "event_type": "generic.event",
        "payload": {"hello": "world"},
    }
    body.update(overrides)
    return body


@pytest.fixture
def service(tmp_path):
    svc = ServeProcess(tmp_path / "inbound_http.db", env_extra=TEST_RESOLVER_ENV)
    try:
        yield svc.start()
    finally:
        svc.kill()


@pytest.fixture
def closed_service(tmp_path):
    """A service in the default posture: no resolver, inbound fail-closed."""
    svc = ServeProcess(tmp_path / "inbound_closed.db")
    try:
        yield svc.start()
    finally:
        svc.kill()


def _rows(service, sql="SELECT COUNT(*) FROM inbound_events"):
    with sqlite3.connect(str(service.db_path)) as conn:
        try:
            return conn.execute(sql).fetchone()[0]
        except sqlite3.OperationalError:
            return 0


#: Reflections written by *this* surface. The scheduler is running throughout
#: these tests and writes its own `action_reflection` rows, so an unscoped
#: count measures the autonomy loop, not inbound capture.
INBOUND_REFLECTIONS = (
    "SELECT COUNT(*) FROM reflections "
    "WHERE kind = 'action_reflection' AND meta LIKE '%\"surface\": \"inbound\"%'"
)


# ---------------------------------------------------------------------------
# The happy path, end to end
# ---------------------------------------------------------------------------


def test_authorised_event_is_accepted_and_captured(service):
    status, body = _post(service.port, "/api/inbound/events", _event("e-1"), AUTH)

    assert status == 202
    assert body["captured"] is True
    assert body["duplicate"] is False
    # 202 says persisted, and says explicitly that it does not say processed.
    assert "Not processed" in body["detail"]

    event = body["event"]
    assert event["source_id"] == "test-source"
    assert event["outcome"] == "captured"
    assert event["verified_by"] == "test-resolver"
    assert len(event["payload_sha256"]) == 64
    assert event["received_at"].endswith("Z")

    assert _rows(service) == 1


def test_provenance_survives_and_is_inspectable(service):
    _post(service.port, "/api/inbound/events", _event("e-prov"), AUTH)

    _, listed = _get(service.port, "/api/inbound/events")
    match = next(e for e in listed if e["event_id"] == "e-prov")

    # Every provenance question, answerable from the inspection surface.
    assert match["source_id"] == "test-source"
    assert match["verified_by"] == "test-resolver"
    assert match["event_type"] == "generic.event"
    assert match["received_at"].endswith("Z")
    assert match["outcome"] == "captured"


def test_duplicate_delivery_returns_200_and_creates_no_second_event(service):
    first_status, first = _post(service.port, "/api/inbound/events", _event("e-dup"), AUTH)
    second_status, second = _post(service.port, "/api/inbound/events", _event("e-dup"), AUTH)

    assert first_status == 202
    assert second_status == 200
    assert second["duplicate"] is True
    assert second["event"]["id"] == first["event"]["id"]
    assert _rows(service) == 1


def test_retry_with_a_different_payload_still_does_not_duplicate(service):
    """External systems retry, sometimes with re-serialised bodies."""
    _post(service.port, "/api/inbound/events", _event("e-retry"), AUTH)
    status, body = _post(
        service.port,
        "/api/inbound/events",
        _event("e-retry", payload={"hello": "world", "retry": True}),
        AUTH,
    )
    assert status == 200
    assert body["duplicate"] is True
    assert _rows(service) == 1


# ---------------------------------------------------------------------------
# Authentication: fail closed, and never on the strength of being local
# ---------------------------------------------------------------------------


def test_unauthenticated_request_from_loopback_is_refused(service):
    """Local-peer status grants reachability, never authority."""
    status, body = _post(service.port, "/api/inbound/events", _event("e-noauth"))

    assert status == 401
    assert "localhost" in body["detail"] or "verified" in body["detail"]
    assert _rows(service) == 0


def test_wrong_credential_from_loopback_is_refused(service):
    status, _ = _post(
        service.port,
        "/api/inbound/events",
        _event("e-badauth"),
        {"X-Bartholomew-Test-Token": "not-the-token"},
    )
    assert status == 401
    assert _rows(service) == 0


def test_inbound_is_closed_when_no_resolver_is_installed(closed_service):
    """The default posture: no authentication means no capture, from anywhere."""
    _, health = _get(closed_service.port, "/api/health")
    assert health["components"]["inbound"]["open"] is False
    assert health["components"]["inbound"]["test_resolver_active"] is False

    status, body = _post(closed_service.port, "/api/inbound/events", _event("e-closed"), AUTH)
    assert status == 401
    assert "closed" in body["detail"]
    assert _rows(closed_service) == 0


def test_a_caller_cannot_claim_another_source(service):
    """A caller-supplied source_id is not authentication and cannot impersonate."""
    status, _ = _post(
        service.port,
        "/api/inbound/events",
        _event("e-spoof", source_id="somebody-elses-source"),
        AUTH,
    )
    assert status == 403
    assert _rows(service) == 0


def test_non_loopback_opt_in_cannot_produce_an_unauthenticated_surface(tmp_path):
    """Relaxing the *network* boundary must not open the *authentication* one.

    `BARTH_API_ALLOW_NON_LOOPBACK=1` exists so a container can publish its
    port. It decides where the API is reachable from, and it has never decided
    who may capture events.

    Under S8 the guarantee is stronger than it was, and this asserts the
    stronger form: that variable now *forces* authentication and TLS on, and
    the process refuses to start without TLS material at all. So there is no
    configuration in which the opt-in yields a reachable, unauthenticated
    inbound surface -- not one that refuses callers, but one that cannot be
    brought up.

    The other half -- that with TLS and enforced authentication a verified
    source still captures nothing without a principal -- is proven against a
    real authenticated server in `test_inbound_authenticated.py`.
    """
    from bartholomew.runtime.serve import EXIT_BAD_CONFIG

    svc = ServeProcess(
        tmp_path / "inbound_nonloopback.db",
        env_extra={"BARTH_API_ALLOW_NON_LOOPBACK": "1", "BARTH_API_HOST": "127.0.0.1"},
    )
    for var in ("BARTH_API_TLS_CERTFILE", "BARTH_API_TLS_KEYFILE"):
        svc.env.pop(var, None)

    try:
        svc.start(wait=False)
        svc.proc.wait(timeout=STARTUP_TIMEOUT)
    finally:
        svc.kill()

    assert svc.proc.returncode == EXIT_BAD_CONFIG, "a non-loopback bind started without TLS"
    assert _rows(svc) == 0


def test_test_resolver_cannot_enable_itself_from_one_variable(tmp_path):
    """Test-only auth needs both gates; neither alone turns it on."""
    for env_extra in (
        {"BARTH_INBOUND_ALLOW_TEST_RESOLVER": "1"},  # gate, no token
        {"BARTH_INBOUND_TEST_TOKEN": TEST_TOKEN},  # token, no gate
    ):
        svc = ServeProcess(tmp_path / f"half_{len(env_extra)}_{id(env_extra)}.db", env_extra)
        try:
            svc.start()
            _, health = _get(svc.port, "/api/health")
            assert health["components"]["inbound"]["open"] is False
            assert health["components"]["inbound"]["test_resolver_active"] is False
        finally:
            svc.kill()


def test_the_test_resolver_announces_itself_when_active(service):
    """A service admitting events on test credentials can never do so quietly."""
    _, health = _get(service.port, "/api/health")
    assert health["components"]["inbound"]["open"] is True
    assert health["components"]["inbound"]["test_resolver_active"] is True


# ---------------------------------------------------------------------------
# Malformed input
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"event_id": "x", "event_type": "t"},  # no source_id
        {"source_id": "test-source", "event_type": "t"},  # no event_id
        {"source_id": "test-source", "event_id": "x"},  # no event_type
        {"source_id": "test-source", "event_id": "  ", "event_type": "t"},  # blank
        {"source_id": "test-source", "event_id": "x", "event_type": "t", "payload": "nope"},
    ],
)
def test_malformed_envelope_is_rejected_and_captures_nothing(service, body):
    status, _ = _post(service.port, "/api/inbound/events", body, AUTH)
    assert status == 422
    assert _rows(service) == 0


def test_non_json_body_is_rejected(service):
    req = urllib.request.Request(  # noqa: S310 - fixed localhost URL
        f"http://127.0.0.1:{service.port}/api/inbound/events",
        data=b"this is not json",
        headers={"Content-Type": "application/json", **AUTH},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(req, timeout=15)  # noqa: S310
    assert excinfo.value.code == 422
    assert _rows(service) == 0


def test_oversized_payload_is_refused(service):
    status, _ = _post(
        service.port,
        "/api/inbound/events",
        _event("e-big", payload={"blob": "x" * 100_000}),
        AUTH,
    )
    assert status == 413
    assert _rows(service) == 0


# ---------------------------------------------------------------------------
# Governance
# ---------------------------------------------------------------------------


def _brake(service, *args):
    """Engage/disengage the brake through the real CLI, as an operator would."""
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "bartholomew", "brake", *args, "--db", str(service.db_path)],
        check=False,
        cwd=str(REPO_ROOT),
        env=_env(service.db_path, service.port),
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_parking_brake_refuses_capture_and_mutates_nothing(service):
    """ "Inspect, but do not mutate": a braked request writes nothing at all.

    Not even a refusal row. Recording "received and refused" would itself be a
    governed-state mutation performed while the user has halted mutation --
    the exact side door the brake exists to close.
    """
    # One captured event first, so "nothing changed" is a real comparison
    # rather than a comparison against zero.
    _post(service.port, "/api/inbound/events", _event("e-before-brake"), AUTH)
    before = _rows(service)
    before_reflections = _rows(service, INBOUND_REFLECTIONS)

    result = _brake(service, "on")
    assert result.returncode == 0, result.stderr

    status, body = _post(service.port, "/api/inbound/events", _event("e-braked"), AUTH)

    # Retryable refusal, honestly stated -- nothing acknowledged as captured.
    assert status == 503
    assert "brake" in body["detail"].lower()
    assert "retry" in body["detail"].lower()

    assert _rows(service) == before, "a braked request wrote an inbound_events row"
    assert (
        _rows(service, INBOUND_REFLECTIONS) == before_reflections
    ), "a braked request wrote a Reflection"


def test_inspection_still_works_under_the_brake(service):
    """A halt must not hide what is already stored."""
    _post(service.port, "/api/inbound/events", _event("e-inspect"), AUTH)
    _brake(service, "on")

    status, listed = _get(service.port, "/api/inbound/events")
    assert status == 200
    assert any(e["event_id"] == "e-inspect" for e in listed)


def test_capture_resumes_after_the_brake_is_released(service):
    """The refusal is retryable: the sender's own retry succeeds afterwards."""
    _brake(service, "on")
    status, _ = _post(service.port, "/api/inbound/events", _event("e-resume"), AUTH)
    assert status == 503

    result = _brake(service, "off")
    assert result.returncode == 0, result.stderr

    status, body = _post(service.port, "/api/inbound/events", _event("e-resume"), AUTH)
    assert status == 202
    assert body["captured"] is True


# ---------------------------------------------------------------------------
# Domain blindness
# ---------------------------------------------------------------------------


def test_ingress_is_blind_to_what_the_event_means(service):
    """Any event_type, any payload shape -- the door does not read the mail."""
    for i, event_type in enumerate(
        ["a.completely.unknown.type", "x", "vendor:weird/type#1", "🙂.emoji.type"],
    ):
        status, body = _post(
            service.port,
            "/api/inbound/events",
            _event(f"e-blind-{i}", event_type=event_type, payload={"arbitrary": [1, {"n": None}]}),
            AUTH,
        )
        assert status == 202, f"{event_type} was not accepted"
        assert body["event"]["event_type"] == event_type


def test_payload_is_stored_verbatim(service):
    payload = {"z": 1, "a": {"nested": ["x", None, 2.5]}, "unicode": "café"}
    _post(service.port, "/api/inbound/events", _event("e-verbatim", payload=payload), AUTH)

    with sqlite3.connect(str(service.db_path)) as conn:
        raw = conn.execute(
            "SELECT payload_json FROM inbound_events WHERE event_id = 'e-verbatim'",
        ).fetchone()[0]
    assert json.loads(raw) == payload


def test_capture_creates_no_memory_and_no_nudge(service):
    """Ingestion does not decide what an event means."""
    _post(service.port, "/api/inbound/events", _event("e-nomem"), AUTH)

    with sqlite3.connect(str(service.db_path)) as conn:
        memories = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        nudges = conn.execute("SELECT COUNT(*) FROM nudges").fetchone()[0]
    assert memories == 0
    assert nudges == 0


# ---------------------------------------------------------------------------
# Runtime availability
# ---------------------------------------------------------------------------


def test_inbound_is_gated_by_kernel_admission():
    """Capture is not exempt from the admission gate, so it cannot run headless.

    Asserted against the exemption lists rather than by racing a real startup
    window: the race is inherently timing-dependent, while the property that
    actually matters -- that `/api/inbound/events` is *not* on the list of
    paths allowed through without a RUNNING kernel -- is exact.

    Health, liveness and static UI are exempt because they must answer during
    startup and shutdown and touch no governed state. Capture writes governed
    state, so it must be refused in exactly those windows.
    """
    from bartholomew_api_bridge_v0_1.services.api.app import _admission_exempt

    assert _admission_exempt("/healthz") is True
    assert _admission_exempt("/api/liveness/self") is True
    assert _admission_exempt("/api/inbound/events") is False
