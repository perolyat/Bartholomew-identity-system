"""The action boundary over real HTTP, against a real running service.

The boundary being proven here *is* the boundary, so none of it is mocked: a
real `bartholomew serve` process, real sockets, real SQLite, real Governance,
real route policy. A test that mocked the HTTP layer would prove nothing about
the two doors this package exists to build.

Reuses the service harness from `test_always_on_service.py` rather than
building a second one.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from tests.integration.test_always_on_service import (
    ServeProcess,
    _get,
    _post,
)

pytestmark = [pytest.mark.integration]

DEVICE = "desk-pc"
ACTION_TOKEN = "integration-only-device-token"
NOTEPAD = "C:\\Windows\\System32\\notepad.exe"

#: Both gates, neither of which exists in any deployed configuration.
ACTION_RESOLVER_ENV = {
    "BARTH_ACTION_ALLOW_TEST_RESOLVER": "1",
    "BARTH_ACTION_TEST_TOKEN": ACTION_TOKEN,
    "BARTH_ACTION_TEST_DEVICE_ID": DEVICE,
}

DEVICE_AUTH = {"X-Bartholomew-Device-Token": ACTION_TOKEN}


def _enrolment(tmp_path, **overrides):
    """A real enrolment file, written by this test as an operator would."""
    record = {
        "device_id": DEVICE,
        "tenant_id": "local",
        "platform": "windows",
        "enrolled": True,
        "capabilities": ["windows.focus_window", "windows.open_url", "windows.type_text"],
        "applications": {"notepad": NOTEPAD},
        "url_domains": ["example.com"],
        "filesystem_roots": ["C:\\Users\\t\\Documents"],
        "trusted_autonomy": [],
    }
    record.update(overrides)
    path = tmp_path / "enrolment.json"
    path.write_text(json.dumps({"devices": [record]}), encoding="utf-8")
    return path


@pytest.fixture
def service(tmp_path):
    """A service with the action channel open on the double-gated test resolver."""
    svc = ServeProcess(
        tmp_path / "actions_http.db",
        env_extra={
            **ACTION_RESOLVER_ENV,
            "BARTH_ACTION_DEVICE_ENROLMENT": str(_enrolment(tmp_path)),
        },
    )
    try:
        yield svc.start()
    finally:
        svc.kill()


@pytest.fixture
def closed_service(tmp_path):
    """The shipped posture: no device resolver installed, so nothing dispatches."""
    svc = ServeProcess(
        tmp_path / "actions_closed.db",
        env_extra={"BARTH_ACTION_DEVICE_ENROLMENT": str(_enrolment(tmp_path))},
    )
    try:
        yield svc.start()
    finally:
        svc.kill()


def _request(port, **overrides):
    body = {
        "device_id": DEVICE,
        "capability": "windows.focus_window",
        "capability_version": 1,
        "parameters": {"app_id": "notepad"},
    }
    body.update(overrides)
    return _post(port, "/api/actions", body)


# --- the shipped default is closed --------------------------------------------


def test_the_action_channel_is_closed_with_no_resolver_installed(closed_service):
    """The default posture dispatches nothing, including from localhost."""
    status, body = _post(
        closed_service.port,
        "/api/device-actions/lease",
        {"device_id": DEVICE, "limit": 5},
    )
    assert status == 401
    assert "no device resolver is installed" in body["detail"]


def test_an_unauthenticated_device_cannot_lease(service):
    status, body = _post(
        service.port,
        "/api/device-actions/lease",
        {"device_id": DEVICE, "limit": 5},
    )
    assert status == 401


def test_a_wrong_device_token_cannot_lease(service):
    status, _ = _post(
        service.port,
        "/api/device-actions/lease",
        {"device_id": DEVICE, "limit": 5},
        headers={"X-Bartholomew-Device-Token": "not-the-token"},
    )
    assert status == 401


def test_an_observation_credential_does_not_open_the_action_channel(service):
    """The two resolvers are separate globals; the inbound token is not this one."""
    status, _ = _post(
        service.port,
        "/api/device-actions/lease",
        {"device_id": DEVICE, "limit": 5},
        headers={"X-Bartholomew-Test-Token": "integration-only-token"},
    )
    assert status == 401


# --- the full path, over real HTTP --------------------------------------------


def test_request_approve_lease_and_report_over_http(service):
    status, requested = _request(service.port)
    assert status == 201
    assert requested["status"] == "accepted"
    assert requested["action"]["state"] == "pending_approval"
    action_id = requested["action"]["action_id"]

    # Nothing is dispatchable before approval.
    status, leased = _post(
        service.port,
        "/api/device-actions/lease",
        {"device_id": DEVICE, "limit": 5},
        headers=DEVICE_AUTH,
    )
    assert status == 200
    assert leased["actions"] == []

    status, approved = _post(service.port, f"/api/actions/{action_id}/approve", {})
    assert status == 200
    assert approved["action"]["state"] == "approved"

    status, leased = _post(
        service.port,
        "/api/device-actions/lease",
        {"device_id": DEVICE, "limit": 5},
        headers=DEVICE_AUTH,
    )
    assert status == 200
    assert [a["action_id"] for a in leased["actions"]] == [action_id]
    assert leased["actions"][0]["parameters"] == {"app_id": "notepad"}
    assert leased["verified_by"] == "action-test-resolver"

    # A second lease of a non-repeatable action gets nothing.
    status, again = _post(
        service.port,
        "/api/device-actions/lease",
        {"device_id": DEVICE, "limit": 5},
        headers=DEVICE_AUTH,
    )
    assert status == 200
    assert again["actions"] == []

    status, recorded = _post(
        service.port,
        f"/api/device-actions/{action_id}/result",
        {
            "device_id": DEVICE,
            "status": "unknown",
            "error_category": "effect_unverifiable",
            "detail": "the foreground could not be read back",
            "evidence": {"hwnd": 0},
            "observed_at": "2026-09-01T12:00:00Z",
        },
        headers=DEVICE_AUTH,
    )
    assert status == 200
    assert recorded["status"] == "unknown"
    assert recorded["action"]["state"] == "unknown"

    status, read_back = _get(service.port, f"/api/actions/{action_id}")
    assert status == 200
    assert read_back["action"]["status"] == "unknown"
    assert [r["status"] for r in read_back["results"]] == ["unknown"]


def test_a_late_result_does_not_overwrite_an_outcome(service):
    _, requested = _request(service.port)
    action_id = requested["action"]["action_id"]
    _post(service.port, f"/api/actions/{action_id}/approve", {})
    _post(
        service.port,
        "/api/device-actions/lease",
        {"device_id": DEVICE, "limit": 5},
        headers=DEVICE_AUTH,
    )
    _post(
        service.port,
        f"/api/device-actions/{action_id}/result",
        {
            "device_id": DEVICE,
            "status": "failed",
            "error_category": "target_not_found",
            "detail": "no window",
            "evidence": {},
            "observed_at": "2026-09-01T12:00:00Z",
        },
        headers=DEVICE_AUTH,
    )
    status, late = _post(
        service.port,
        f"/api/device-actions/{action_id}/result",
        {
            "device_id": DEVICE,
            "status": "succeeded",
            "error_category": None,
            "detail": "actually it worked",
            "evidence": {},
            "observed_at": "2026-09-01T12:01:00Z",
        },
        headers=DEVICE_AUTH,
    )
    assert status == 409
    assert late["action"]["state"] == "failed"


def test_a_device_cannot_report_for_another_device(service):
    _, requested = _request(service.port)
    action_id = requested["action"]["action_id"]
    status, _ = _post(
        service.port,
        f"/api/device-actions/{action_id}/result",
        {
            "device_id": "somebody-elses-pc",
            "status": "succeeded",
            "detail": "",
            "evidence": {},
            "observed_at": "2026-09-01T12:00:00Z",
        },
        headers=DEVICE_AUTH,
    )
    assert status == 403


def test_a_device_cannot_lease_on_behalf_of_another_device(service):
    status, _ = _post(
        service.port,
        "/api/device-actions/lease",
        {"device_id": "somebody-elses-pc", "limit": 5},
        headers=DEVICE_AUTH,
    )
    assert status == 403


# --- refusals over HTTP --------------------------------------------------------


@pytest.mark.parametrize(
    "capability,parameters",
    [
        ("windows.run_command", {}),
        ("windows.shell", {"command": "dir"}),
        ("windows.open_url", {"url": "file:///C:/Windows/win.ini"}),
        ("windows.open_url", {"url": "javascript:alert(1)"}),
        ("windows.open_url", {"url": "https://user:pw@example.com/"}),
        ("windows.open_url", {"url": "https://elsewhere.test/"}),
        ("windows.focus_window", {"app_id": "cmd"}),
        ("windows.focus_window", {"app_id": "notepad", "args": "/k whoami"}),
        ("windows.type_text", {"text": "press\nenter"}),
    ],
)
def test_a_prohibited_request_is_refused_at_the_door(service, capability, parameters):
    status, body = _request(service.port, capability=capability, parameters=parameters)
    assert status in (403, 422), body
    assert body.get("status") == "refused" or status == 422


def test_an_undeclared_capability_is_refused(service):
    status, body = _request(service.port, capability="windows.clipboard_read", parameters={})
    assert status == 403
    assert body["error_category"] == "capability_not_declared"


def test_an_unenrolled_device_is_refused(service):
    status, body = _request(service.port, device_id="somebody-elses-pc")
    assert status == 403
    assert body["error_category"] == "device_not_enrolled"


def test_a_parking_brake_refuses_a_request_with_503(service):
    status, _ = _post(
        service.port,
        "/api/governance/brake/engage",
        {"scopes": ["actuation"], "reason": "integration test", "actor": "test"},
    )
    assert status == 200
    try:
        status, body = _request(service.port)
        assert status == 503
    finally:
        _, current = _get(service.port, "/api/governance/brake")
        _post(
            service.port,
            "/api/governance/brake/disengage",
            {
                "reason": "integration test",
                "actor": "test",
                "expected_revision": current.get("revision"),
            },
        )


def test_a_cancelled_action_cannot_be_leased(service):
    _, requested = _request(service.port)
    action_id = requested["action"]["action_id"]
    _post(service.port, f"/api/actions/{action_id}/approve", {})
    status, cancelled = _post(service.port, f"/api/actions/{action_id}/cancel", {})
    assert status == 200
    assert cancelled["status"] == "cancelled"

    status, leased = _post(
        service.port,
        "/api/device-actions/lease",
        {"device_id": DEVICE, "limit": 5},
        headers=DEVICE_AUTH,
    )
    assert leased["actions"] == []


# --- the durable record --------------------------------------------------------


def test_the_typed_text_is_never_in_the_durable_record(service):
    secret_ish = "the quick brown fox"
    status, requested = _request(
        service.port,
        capability="windows.type_text",
        parameters={"text": secret_ish},
    )
    assert status == 201
    assert secret_ish not in json.dumps(requested)

    status, listed = _get(service.port, "/api/actions")
    assert secret_ish not in json.dumps(listed)

    with sqlite3.connect(str(service.db_path)) as conn:
        redacted = conn.execute(
            "SELECT parameters_redacted_json FROM windows_action_requests",
        ).fetchall()
        reflections = conn.execute(
            "SELECT content, meta FROM reflections WHERE kind = 'action_reflection'",
        ).fetchall()
    assert all(secret_ish not in r[0] for r in redacted)
    for content, meta in reflections:
        assert secret_ish not in (content or "")
        assert secret_ish not in (meta or "")


def test_the_response_says_plainly_that_nothing_ran(service):
    _, requested = _request(service.port)
    assert "Nothing has been dispatched" in requested["detail"]


def test_the_inbound_response_never_carries_an_action(service):
    """Requirement 12, at the HTTP layer: the observation door answers with no action."""
    status, body = _post(
        service.port,
        "/api/inbound/events",
        {"source_id": "x", "event_id": "e1", "event_type": "t", "payload": {}},
    )
    # 401 with no inbound resolver installed -- and, whatever the code, the
    # body carries none of the action channel's vocabulary.
    rendered = json.dumps(body)
    for forbidden in ("actions", "capability", "action_id", "parameters"):
        assert forbidden not in rendered, f"the inbound response mentions {forbidden}"
    assert status in (401, 403, 202, 200)


def test_the_health_surface_says_whether_the_action_channel_is_open(service):
    """An operator must never be unsure whether actions can be dispatched."""
    status, body = _get(service.port, "/api/health")
    assert status == 200
    channel = body["components"]["device_actions"]
    assert channel["open"] is True
    # And it says loudly that this is the test resolver, not authentication.
    assert channel["test_resolver_active"] is True
    assert channel["registry"]["interim"] is True
    assert channel["registry"]["replaced_by"] == "Session E device/group registry"
    assert channel["registry"]["device_count"] == 1


def test_the_health_surface_says_so_when_the_channel_is_closed(closed_service):
    status, body = _get(closed_service.port, "/api/health")
    channel = body["components"]["device_actions"]
    assert channel["open"] is False
    assert channel["test_resolver_active"] is False
    assert "Nothing is dispatched" in channel["detail"]
