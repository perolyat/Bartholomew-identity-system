"""
FND-03: the consent inbox's review surface, traced end-to-end over HTTP.

The consent route module used to carry a note saying this API bridge has "no
authentication today". That had stopped being true: `route_policy.py`
classifies all three consent endpoints as `Capability.CONSENT_DECIDE`, and
`http_identity.authenticate_and_authorize()` is installed as the API
bridge's request boundary in `app.py`.

So the repair here is a *proof*, not a new mechanism. Adding route-local
bearer parsing would mean two competing authentication systems, and the
second one is always the one that gets it wrong. These tests pin the
authoritative boundary's behaviour on the raw-review surface specifically --
reading the inbox is reading the ORIGINAL, pre-redaction payload, which is
why listing is capability-gated exactly as deciding is.

The loopback development mode (authentication not enforced) is asserted in
`tests/test_s8_exposure_failclosed.py`, which also pins the structural
reason it cannot become an exposed deployment: a non-loopback bind forces
authentication and TLS on, and refuses to start otherwise. This module
covers the enforced side.
"""

from __future__ import annotations

import tempfile

import pytest
from fastapi.testclient import TestClient

from bartholomew.platform import accounts, runtime_registry
from bartholomew.platform.capabilities import Capability, capabilities_for
from bartholomew.platform.principal import Principal, PrincipalKind
from bartholomew.platform.route_policy import capability_for
from bartholomew.platform.store import init_platform_schema
from bartholomew_api_bridge_v0_1.services.api.app import app

PASSWORD = "consent-review-password"

CONSENT_ROUTES = [
    ("GET", "/api/consent/pending-writes", "/api/consent/pending-writes"),
    (
        "POST",
        "/api/consent/pending-writes/1/approve",
        "/api/consent/pending-writes/{pending_id}/approve",
    ),
    (
        "POST",
        "/api/consent/pending-writes/1/deny",
        "/api/consent/pending-writes/{pending_id}/deny",
    ),
]


@pytest.fixture(scope="module", autouse=True)
def _isolated_environment():
    """Contain BARTH_AUTH_MODE and the database paths to this module.

    Same reasoning as tests/test_s8_auth_boundary.py: a bare
    `os.environ[...]` here would enforce authentication across every suite
    collected afterwards.
    """
    mp = pytest.MonkeyPatch()
    tmp = tempfile.mkdtemp(prefix="fnd03-consent-auth-")
    for var, value in {
        "BARTH_PLATFORM_DB_PATH": f"{tmp}/platform.db",
        "BARTH_DATA_ROOT": f"{tmp}/data",
        "BARTH_DB_PATH": f"{tmp}/kernel.db",
        "BARTH_AUTH_MODE": "enforced",
    }.items():
        mp.setenv(var, value)
    init_platform_schema()
    yield
    mp.undo()


@pytest.fixture(scope="module")
def users():
    init_platform_schema()
    made = {}
    for name, kind in (
        ("owner", PrincipalKind.USER),
        ("other", PrincipalKind.USER),
        ("ops", PrincipalKind.PLATFORM_ADMIN),
    ):
        try:
            made[name] = accounts.create_account(name, PASSWORD, kind=kind)
        except accounts.AccountError:
            made[name] = next(
                a["user_id"] for a in accounts.list_accounts() if a["username"] == name
            )
    return made


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _login(client, username: str) -> str:
    r = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_every_consent_route_is_classified_as_consent_decide() -> None:
    """Including the listing route: what it returns IS the raw payload."""
    for method, _path, template in CONSENT_ROUTES:
        assert capability_for(method, template) is Capability.CONSENT_DECIDE


@pytest.mark.parametrize("method,path,_template", CONSENT_ROUTES)
def test_anonymous_consent_request_is_refused(client, users, method, path, _template):
    """21. No credential, no access to the raw-review surface."""
    resp = client.request(method, path, json={})
    assert resp.status_code == 401, f"{method} {path} answered {resp.status_code}"


@pytest.mark.parametrize("method,path,_template", CONSENT_ROUTES)
def test_principal_without_consent_decide_is_refused(client, users, method, path, _template):
    """22. A platform administrator is authenticated and holds no
    CONSENT_DECIDE -- administering the platform is not the same authority as
    reading someone's unredacted pending memories."""
    assert Capability.CONSENT_DECIDE not in capabilities_for(
        Principal(
            user_id=users["ops"],
            username="ops",
            kind=PrincipalKind.PLATFORM_ADMIN,
            session_id="s",
        ),
    )
    token = _login(client, "ops")
    resp = client.request(method, path, headers=_auth(token), json={})
    assert resp.status_code == 403, f"{method} {path} answered {resp.status_code}"


@pytest.mark.parametrize("method,path,_template", CONSENT_ROUTES)
def test_authorised_principal_for_another_runtime_is_refused(
    client, users, monkeypatch, method, path, _template,
):
    """23. `other` holds CONSENT_DECIDE, but not over `owner`'s Bartholomew.
    A consent inbox is per-runtime by construction."""
    monkeypatch.setenv(runtime_registry.RUNTIME_USER_ID_ENV, users["owner"])
    token = _login(client, "other")
    resp = client.request(method, path, headers=_auth(token), json={})
    assert resp.status_code == 403, f"{method} {path} answered {resp.status_code}"


def test_authorised_owner_can_review_and_resolve_their_own_inbox(client, users, monkeypatch):
    """24. The boundary refuses everyone else and admits the right person --
    a boundary that refused everybody would pass every test above and be
    useless."""
    monkeypatch.setenv(runtime_registry.RUNTIME_USER_ID_ENV, users["owner"])
    token = _login(client, "owner")

    listing = client.get("/api/consent/pending-writes", headers=_auth(token))
    assert listing.status_code == 200
    assert "entries" in listing.json()

    # Deciding is reachable too: a missing id is a 404 from the handler,
    # which means the request got past authentication and authorisation.
    decided = client.post(
        "/api/consent/pending-writes/999999/deny",
        headers=_auth(token),
    )
    assert decided.status_code == 404


def test_no_client_supplied_header_reaches_the_consent_inbox(client, users, monkeypatch):
    """E: no security by UI, and no security by header either."""
    monkeypatch.setenv(runtime_registry.RUNTIME_USER_ID_ENV, users["owner"])
    for header in ("x-user-id", "x-principal", "x-forwarded-user"):
        resp = client.get(
            "/api/consent/pending-writes",
            headers={header: users["owner"]},
        )
        assert resp.status_code == 401, f"{header} authenticated a consent request"
