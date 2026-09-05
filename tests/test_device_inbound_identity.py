"""
Package E: a verified device identity, end to end through the real ingress.

`bartholomew/companion/envelope.py` states the rule this file exists to
enforce: *"`payload['device_id']` is claimed provenance ... it is not
authenticated, and nothing in Bartholomew may treat it as though it were."*
That is still true of the payload label. What has changed is that a
*different*, registry-issued device identity now exists alongside it, and
these tests prove the two never get confused.

Everything below runs against the real FastAPI app, the real inbound route,
the real Runtime Contract seam, the real `inbound_events` table and the real
control-plane device registry. The resolver is installed through
`inbound_auth.install_resolver()` -- the production call site its module
docstring names -- rather than through the environment, so the test exercises
the same object a deployment would install.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile

import pytest
from fastapi.testclient import TestClient

from bartholomew.platform import accounts, device_inbound, devices  # noqa: E402
from bartholomew.platform.store import init_platform_schema  # noqa: E402
from bartholomew_api_bridge_v0_1.services.api import inbound_auth  # noqa: E402
from bartholomew_api_bridge_v0_1.services.api.app import app  # noqa: E402
from bartholomew_api_bridge_v0_1.services.api.db import resolve_db_path  # noqa: E402

PASSWORD = "alpha-participant-password"

MANIFEST = {
    "platform": "windows",
    "companion_version": "0.1.0-prototype",
    "capabilities": [{"kind": "windows.open_url", "version": 1}],
}


@pytest.fixture(scope="module", autouse=True)
def _isolated_environment():
    """
    Contain this module's control-plane and kernel paths.

    Authentication is left at its loopback-development default here on
    purpose: with no principal in play, the **only** thing deciding whether an
    event is admitted is the device resolver, which is exactly the boundary
    under test. `tests/integration/test_inbound_authenticated.py` covers the
    principal half against a real server.
    """
    mp = pytest.MonkeyPatch()
    tmp = tempfile.mkdtemp(prefix="e-device-inbound-")
    for var, value in {
        "BARTH_PLATFORM_DB_PATH": "<tmp>/platform.db",
        "BARTH_DATA_ROOT": "<tmp>/data",
        "BARTH_DB_PATH": "<tmp>/kernel.db",
        "BARTHO_DB_PATH": "<tmp>/kernel.db",
    }.items():
        mp.setenv(var, value.replace("<tmp>", tmp))
    yield
    mp.undo()


@pytest.fixture(scope="module", autouse=True)
def _schema():
    init_platform_schema()


@pytest.fixture(scope="module")
def enrolled():
    """Two accounts, each with one fully enrolled device."""
    init_platform_schema()
    made = {}
    for name in ("alice", "bob"):
        try:
            user_id = accounts.create_account(name, PASSWORD)
        except accounts.AccountError:
            user_id = next(a["user_id"] for a in accounts.list_accounts() if a["username"] == name)
        device_id = devices.create_pending_enrolment(user_id, f"{name}-pc", platform="windows")
        issued = devices.approve_enrolment(device_id, approver="ops")
        _verified, credential = devices.complete_enrolment(issued.secret, dict(MANIFEST))
        made[name] = {
            "user_id": user_id,
            "device_id": device_id,
            "secret": credential.secret,
        }
    return made


@pytest.fixture
def client():
    """The real app, with the real device resolver installed for the test.

    Returned to fail-closed afterwards: `clear_resolver()` is the state every
    deployment that has not made a decision is in, and leaving a resolver
    installed would silently open the surface for whatever runs next.
    """
    with TestClient(app, raise_server_exceptions=False) as c:
        device_inbound.install_device_resolver()
        try:
            yield c
        finally:
            inbound_auth.clear_resolver()


def _envelope(source_id, *, event_id, claimed_device_id=None):
    payload = {"kind": "system_state", "companion_version": "0.1.0-prototype"}
    if claimed_device_id is not None:
        payload["device_id"] = claimed_device_id
    return {
        "source_id": source_id,
        "event_id": event_id,
        "event_type": "companion.system_state",
        "payload": payload,
        "occurred_at": "2026-09-01T00:00:00Z",
    }


def _stored_rows():
    with sqlite3.connect(resolve_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in conn.execute("SELECT * FROM inbound_events")]
        except sqlite3.OperationalError:
            return []


def _post(client, body, *, secret=None):
    headers = {}
    if secret is not None:
        headers[device_inbound.DEVICE_CREDENTIAL_HEADER] = secret
    return client.post("/api/inbound/events", content=json.dumps(body), headers=headers)


# ---------------------------------------------------------------------------
# The resolver itself
# ---------------------------------------------------------------------------


def test_a_verified_device_becomes_a_verified_inbound_source(enrolled):
    """The one thing this adapter contributes: a credential -> a source id."""
    device = enrolled["alice"]
    verified = devices.verify_device_credential(device["secret"])
    source = device_inbound.VerifiedDeviceSource(verified)

    assert source.source_id == f"device:{device['device_id']}"
    assert source.verified_by == device_inbound.DEVICE_VERIFIED_BY == "device-credential"
    # The protocol surface is two attributes. A source cannot choose a runtime,
    # and there is no attribute here by which it could try.
    assert not hasattr(source, "runtime_id")


def test_the_resolver_refuses_a_credential_that_no_longer_works(enrolled):
    """Rotated, revoked, absent and malformed all resolve to None -- a 401."""
    import asyncio

    class _Request:
        def __init__(self, headers):
            self.headers = headers

    device = enrolled["bob"]
    resolver = device_inbound.DeviceCredentialResolver()

    async def _resolve(secret):
        headers = {} if secret is None else {device_inbound.DEVICE_CREDENTIAL_HEADER: secret}
        return await resolver.resolve(_Request(headers), b"{}")

    assert asyncio.run(_resolve(device["secret"])) is not None
    assert asyncio.run(_resolve(None)) is None
    assert asyncio.run(_resolve("")) is None
    assert asyncio.run(_resolve("not-a-credential")) is None

    rotated = devices.rotate_device_credential(device["device_id"], actor="ops")
    assert asyncio.run(_resolve(device["secret"])) is None
    assert asyncio.run(_resolve(rotated.secret)) is not None

    devices.revoke_device(device["device_id"], actor="ops", reason="test")
    assert asyncio.run(_resolve(rotated.secret)) is None


def test_the_device_resolver_refuses_to_run_alongside_the_test_resolver(monkeypatch):
    """A deployment configured with both has said two contradictory things.

    A contradiction about how a surface authenticates is resolved by stopping,
    not by picking one -- so this raises at install time rather than silently
    replacing whichever was there.
    """
    monkeypatch.setenv(inbound_auth.ALLOW_TEST_RESOLVER_ENV, "1")
    inbound_auth.install_test_resolver("a-test-token")
    try:
        with pytest.raises(RuntimeError, match="both"):
            device_inbound.install_device_resolver()
    finally:
        inbound_auth.clear_resolver()


def test_the_resolver_is_off_unless_the_environment_asks_for_it(monkeypatch):
    """Opening a capture surface is a deployment decision, not an import side effect."""
    inbound_auth.clear_resolver()
    monkeypatch.delenv(device_inbound.DEVICE_INBOUND_AUTH_ENV, raising=False)
    assert device_inbound.maybe_install_device_resolver_from_env() is False
    assert inbound_auth.get_resolver() is None

    monkeypatch.setenv(device_inbound.DEVICE_INBOUND_AUTH_ENV, "1")
    try:
        assert device_inbound.maybe_install_device_resolver_from_env() is True
        assert isinstance(inbound_auth.get_resolver(), device_inbound.DeviceCredentialResolver)
    finally:
        inbound_auth.clear_resolver()


# ---------------------------------------------------------------------------
# 12: through the real route
# ---------------------------------------------------------------------------


def test_an_unauthenticated_device_captures_nothing(client, enrolled):
    """The route's fail-closed default is unchanged by this adapter."""
    before = len(_stored_rows())
    response = _post(
        client,
        _envelope(f"device:{enrolled['alice']['device_id']}", event_id="evt-anon"),
    )
    assert response.status_code == 401
    assert len(_stored_rows()) == before


def test_a_verified_device_is_captured_under_the_identity_the_platform_issued(
    client,
    enrolled,
):
    """12. The durable row records the registry's device, and what verified it."""
    device = enrolled["alice"]
    source_id = f"device:{device['device_id']}"
    response = _post(
        client,
        _envelope(source_id, event_id="evt-verified"),
        secret=device["secret"],
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["captured"] is True

    row = next(r for r in _stored_rows() if r["event_id"] == "evt-verified")
    assert row["source_id"] == source_id
    assert row["verified_by"] == device_inbound.DEVICE_VERIFIED_BY


def test_a_claimed_payload_device_id_cannot_override_the_verified_identity(
    client,
    enrolled,
):
    """12. The whole point of the registry, proved through the real ingress.

    Alice's companion submits a payload claiming to be Bob's machine. The
    durable row is recorded against the device the *credential* named, the
    claimed label is stored as the inert provenance it has always been, and
    nothing about Bob's device is touched.
    """
    alice, bob = enrolled["alice"], enrolled["bob"]
    response = _post(
        client,
        _envelope(
            f"device:{alice['device_id']}",
            event_id="evt-claimed",
            claimed_device_id=bob["device_id"],
        ),
        secret=alice["secret"],
    )
    assert response.status_code == 202, response.text

    row = next(r for r in _stored_rows() if r["event_id"] == "evt-claimed")
    assert row["source_id"] == f"device:{alice['device_id']}"
    assert bob["device_id"] not in row["source_id"]
    assert row["verified_by"] == device_inbound.DEVICE_VERIFIED_BY
    # Bob's device is untouched: a claim is not a contact.
    assert devices.get_device(bob["device_id"])["user_id"] == bob["user_id"]


def test_a_submitted_source_id_naming_another_device_is_refused(client, enrolled):
    """12. The route's existing `source_id` comparison does the rest.

    A companion cannot claim provenance for, or collide idempotency keys
    with, a device it is not -- and the refusal captures nothing.
    """
    alice, bob = enrolled["alice"], enrolled["bob"]
    before = len(_stored_rows())
    response = _post(
        client,
        _envelope(f"device:{bob['device_id']}", event_id="evt-spoofed"),
        secret=alice["secret"],
    )
    assert response.status_code == 403
    assert len(_stored_rows()) == before


def test_a_revoked_device_captures_nothing(client, enrolled):
    """6/12. Revocation reaches the ingress on the very next request."""
    user_id = enrolled["alice"]["user_id"]
    device_id = devices.create_pending_enrolment(user_id, "throwaway-pc", platform="windows")
    issued = devices.approve_enrolment(device_id, approver="ops")
    _verified, credential = devices.complete_enrolment(issued.secret, dict(MANIFEST))

    ok = _post(
        client,
        _envelope(f"device:{device_id}", event_id="evt-before-revoke"),
        secret=credential.secret,
    )
    assert ok.status_code == 202, ok.text

    devices.revoke_device(device_id, actor="ops", reason="lost")

    before = len(_stored_rows())
    refused = _post(
        client,
        _envelope(f"device:{device_id}", event_id="evt-after-revoke"),
        secret=credential.secret,
    )
    assert refused.status_code == 401
    assert len(_stored_rows()) == before
