"""
HTTP-level tests for bartholomew_api_bridge_v0_1's notifications router
(/api/notifications/settings, /quiet-hours, /mute, /unmute) -- Stage 1,
S1.3. Follows the same pattern as tests/test_governance_api.py: a
module-scoped TestClient over the real app (real KernelDaemon startup,
real kernel.skill_registry with the "notify" skill auto-loaded via
config/skills/notify.yaml's enabled: true).
"""

from __future__ import annotations

import os
import pathlib
import tempfile

import pytest
from fastapi.testclient import TestClient

_db_dir = pathlib.Path(tempfile.mkdtemp()) / "data"
_db_dir.mkdir(parents=True, exist_ok=True)
_DB_PATH = str(_db_dir / "test.db")
os.environ["BARTH_DB_PATH"] = _DB_PATH

from bartholomew_api_bridge_v0_1.services.api import app as app_module  # noqa: E402


@pytest.fixture(scope="module")
def client():
    # Re-assert right before starting the app -- see
    # tests/test_api_admission_gate.py's client fixture for why (pytest
    # collects/imports every test module, running each one's own
    # os.environ[...] assignment, before running any test; a
    # later-collected module can overwrite this by the time this fixture
    # actually fires).
    os.environ["BARTH_DB_PATH"] = _DB_PATH
    with TestClient(app_module.app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_notifications(client):
    """Every test starts and ends unmuted, so tests don't leak mute state
    into each other (the notify skill instance is a process-wide
    singleton on app_module._kernel, same sharing hazard documented in
    test_self_state_api.py/test_governance_api.py)."""
    client.post("/api/notifications/unmute")
    yield
    client.post("/api/notifications/unmute")


def test_get_settings_returns_quiet_hours_and_mute_status(client):
    response = client.get("/api/notifications/settings")
    assert response.status_code == 200
    body = response.json()
    assert "start" in body["quiet_hours"]
    assert "end" in body["quiet_hours"]
    assert body["muted"] is False


def test_set_quiet_hours_updates_settings(client):
    response = client.put(
        "/api/notifications/quiet-hours",
        json={"start": "21:00", "end": "08:00"},
    )
    assert response.status_code == 200
    assert response.json()["start"] == "21:00"
    assert response.json()["end"] == "08:00"

    settings = client.get("/api/notifications/settings").json()
    assert settings["quiet_hours"]["start"] == "21:00"
    assert settings["quiet_hours"]["end"] == "08:00"


def test_set_quiet_hours_rejects_malformed_time(client):
    response = client.put(
        "/api/notifications/quiet-hours",
        json={"start": "whenever", "end": "08:00"},
    )
    assert response.status_code == 400


def test_mute_and_unmute_round_trip(client):
    mute_response = client.post("/api/notifications/mute", json={})
    assert mute_response.status_code == 200
    assert mute_response.json()["muted"] is True

    settings = client.get("/api/notifications/settings").json()
    assert settings["muted"] is True
    assert settings["effective_muted"] is True

    unmute_response = client.post("/api/notifications/unmute")
    assert unmute_response.status_code == 200
    assert unmute_response.json()["muted"] is False

    settings = client.get("/api/notifications/settings").json()
    assert settings["muted"] is False
    assert settings["effective_muted"] is False


# ---------------------------------------------------------------------------
# WP-A2 / safety gate S2 -- degraded-audit surfacing at the HTTP boundary
# ---------------------------------------------------------------------------


def _fail_writes_to(table: str):
    """Make inserts into one audit table fail, via a real SQLite trigger.

    Genuine failure injection against the live app's own database -- the
    production INSERT runs and is aborted by SQLite, rather than an
    exception being monkeypatched in.
    """
    import contextlib

    from bartholomew.kernel.db_ctx import connect, set_wal_pragmas

    @contextlib.contextmanager
    def _ctx():
        def run(sql: str) -> None:
            conn = connect(_DB_PATH)
            try:
                set_wal_pragmas(conn)
                conn.execute(sql)
                conn.commit()
            finally:
                conn.close()

        run(
            f"CREATE TRIGGER notif_block_{table} BEFORE INSERT ON {table} "
            f"BEGIN SELECT RAISE(ABORT, 'injected audit failure'); END",
        )
        try:
            yield
        finally:
            run(f"DROP TRIGGER IF EXISTS notif_block_{table}")

    return _ctx()


def test_healthy_quiet_hours_response_is_not_marked_degraded(client):
    """The degraded marker must appear only when something actually failed."""
    response = client.put(
        "/api/notifications/quiet-hours",
        json={"start": "21:00", "end": "08:00"},
    )
    assert response.status_code == 200
    assert "audit_degraded" not in response.json()


def test_lost_audit_write_is_reported_without_failing_the_action(client):
    """S2 at the HTTP boundary.

    The setting really is changed, so the response must not claim failure;
    a required audit row really was lost, so it must not claim full success
    either. Both facts appear in one response.
    """
    with _fail_writes_to("skill_action_audit"):
        response = client.put(
            "/api/notifications/quiet-hours",
            json={"start": "23:15", "end": "06:45"},
        )

    # Fact 1: the action succeeded, and is described normally.
    assert response.status_code == 200, (
        "an action that genuinely executed must not be reported as failed "
        "because its audit write was lost"
    )
    body = response.json()
    assert body["start"] == "23:15"
    assert body["end"] == "06:45"

    # Fact 2: required audit persistence failed, explicitly.
    assert body["audit_degraded"] is True
    assert "skill_action_audit" in body["audit_error"]

    # The change is real and durable -- this is a degraded success, not a
    # silent rollback dressed up as one.
    settings = client.get("/api/notifications/settings").json()
    assert settings["quiet_hours"]["start"] == "23:15"


def test_quiet_hours_failure_reports_its_reason(client):
    """Regression-diagnosis aid.

    `test_set_quiet_hours_updates_settings` asserted only a status code, so
    when it failed intermittently in CI the log recorded `assert 400 == 200`
    and nothing about the cause -- which is why the root cause had to be
    reproduced from scratch rather than read off the failure. Any non-200
    from this route now surfaces the reason it carried.
    """
    response = client.put(
        "/api/notifications/quiet-hours",
        json={"start": "07:30", "end": "22:30"},
    )
    assert response.status_code == 200, (
        f"quiet-hours update failed: {response.status_code} "
        f"detail={response.json().get('detail')!r}"
    )
