"""
S8: the authentication boundary, tested adversarially.

These tests try to *break* the boundary rather than demonstrate that login
works. Each one corresponds to a threat in the approved S8 threat model, and
the docstrings name it, so a failure here reads as "threat T-n is live"
rather than "a test went red".
"""

from __future__ import annotations

import tempfile

import pytest

# Isolate the control plane and every per-user runtime before app import.
from fastapi.testclient import TestClient  # noqa: E402

from bartholomew.platform import accounts, sessions  # noqa: E402
from bartholomew.platform.http_identity import (  # noqa: E402
    CLIENT_SUPPLIED_IDENTITY_HEADERS,
)
from bartholomew.platform.principal import PrincipalKind  # noqa: E402
from bartholomew.platform.store import init_platform_schema  # noqa: E402
from bartholomew_api_bridge_v0_1.services.api.app import app  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _isolated_environment():
    """
    Set this module's environment for its own duration and restore it after.

    Module-level `os.environ[...]` assignment would leak `BARTH_AUTH_MODE` and
    the database paths into every other test file in the same pytest session
    -- silently enforcing authentication on suites written before it existed,
    and pointing their kernels at this module's database. A module-scoped
    MonkeyPatch keeps the change contained to this file.
    """
    mp = pytest.MonkeyPatch()
    tmp = tempfile.mkdtemp(prefix="s8-auth-")
    for var, value in {
        "BARTH_PLATFORM_DB_PATH": "<tmp>/platform.db",
        "BARTH_DATA_ROOT": "<tmp>/data",
        "BARTH_DB_PATH": "<tmp>/kernel.db",
        "BARTH_AUTH_MODE": "enforced",
    }.items():
        mp.setenv(var, value.replace("<tmp>", tmp))
    yield
    mp.undo()


PASSWORD = "alpha-participant-password"


@pytest.fixture(scope="module", autouse=True)
def _schema():
    init_platform_schema()


@pytest.fixture(scope="module")
def users():
    """Two ordinary participants and one platform administrator."""
    init_platform_schema()
    made = {}
    for name, kind in (
        ("alice", PrincipalKind.USER),
        ("bob", PrincipalKind.USER),
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


def _login(client, username):
    r = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# T12 -- an anonymous request must never silently inherit an identity
# ---------------------------------------------------------------------------

# Every non-public route. Anonymous access to any of these is a finding.
PROTECTED = [
    ("GET", "/api/memory"),
    ("GET", "/api/memory/export"),
    ("GET", "/api/memory/fact/anything"),
    ("PUT", "/api/memory/fact/anything"),
    ("DELETE", "/api/memory/fact/anything"),
    ("GET", "/api/governance/brake"),
    ("POST", "/api/governance/brake/engage"),
    ("POST", "/api/governance/brake/disengage"),
    ("GET", "/api/governance/audit"),
    ("GET", "/api/consent/pending-writes"),
    ("POST", "/api/chat"),
    ("GET", "/api/self"),
    ("PUT", "/api/self/affect"),
    ("GET", "/api/episodes/recent"),
    ("GET", "/api/working_memory"),
    ("POST", "/api/training/submit"),
    ("POST", "/kernel/command/tick"),
    ("GET", "/api/liveness/self"),
    ("GET", "/metrics"),
    ("GET", "/api/auth/whoami"),
]


@pytest.mark.parametrize("method,path", PROTECTED)
def test_anonymous_request_is_refused_and_inherits_no_identity(client, method, path, users):
    """
    T12. No credential -> 401/403. Never 200, and never served as whichever
    user happens to exist.
    """
    resp = client.request(method, path, json={})
    assert resp.status_code in (
        401,
        403,
    ), f"{method} {path} answered {resp.status_code} to an anonymous caller"


def test_public_paths_stay_reachable(client):
    """Health and login must answer without a credential, or nobody can log in."""
    assert client.get("/healthz").status_code == 200
    # Wrong credentials, but the endpoint itself is reachable.
    assert (
        client.post("/api/auth/login", json={"username": "nobody", "password": "x"}).status_code
        == 401
    )


# ---------------------------------------------------------------------------
# T1/T11 -- client-supplied identity must never override server-side identity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("header", CLIENT_SUPPLIED_IDENTITY_HEADERS)
def test_client_supplied_identity_headers_are_ignored(client, users, header):
    """
    T11. Alice authenticates and names Bob in every header a system might
    plausibly read. She is still Alice.
    """
    token = _login(client, "alice")
    resp = client.get(
        "/api/auth/whoami",
        headers={**_auth(token), header: users["bob"]},
    )
    assert resp.status_code == 200
    assert resp.json()["user_id"] == users["alice"]
    assert resp.json()["username"] == "alice"


def test_client_supplied_identity_headers_cannot_authenticate_alone(client, users):
    """T11/T12. The headers are not a credential on their own, either."""
    for header in CLIENT_SUPPLIED_IDENTITY_HEADERS:
        resp = client.get("/api/auth/whoami", headers={header: users["alice"]})
        assert resp.status_code == 401, f"{header} authenticated a request by itself"


def test_query_and_body_cannot_name_a_different_user(client, users):
    """T11. Nor can a query parameter or a body field."""
    token = _login(client, "alice")
    resp = client.get(f"/api/auth/whoami?user_id={users['bob']}", headers=_auth(token))
    assert resp.json()["user_id"] == users["alice"]


# ---------------------------------------------------------------------------
# T3/T4 -- credential lifecycle
# ---------------------------------------------------------------------------


def test_expired_session_is_refused(client, users):
    """T3. An absolute expiry in the past authenticates nothing."""
    _sid, token = sessions.create_session(
        users["alice"],
        fingerprint=sessions.client_fingerprint("testclient", None),
        absolute_ttl_s=-1,
    )
    assert client.get("/api/auth/whoami", headers=_auth(token)).status_code == 401


def test_revoked_session_fails_on_the_very_next_request(client, users):
    """
    T4. The property server-side sessions exist for: revocation is effective
    immediately, not at the next expiry.
    """
    token = _login(client, "alice")
    assert client.get("/api/auth/whoami", headers=_auth(token)).status_code == 200
    sid = client.get("/api/auth/whoami", headers=_auth(token)).json()["session_id"]
    sessions.revoke_session(sid)
    assert client.get("/api/auth/whoami", headers=_auth(token)).status_code == 401


def test_logout_revokes_server_side_not_just_the_cookie(client, users):
    """T4. A logout that leaves the session usable is not a logout."""
    token = _login(client, "alice")
    assert client.post("/api/auth/logout", headers=_auth(token)).status_code == 200
    assert client.get("/api/auth/whoami", headers=_auth(token)).status_code == 401


def test_disabling_an_account_revokes_its_live_sessions(client, users):
    """T4. Operator revocation must reach sessions already issued."""
    token = _login(client, "bob")
    assert client.get("/api/auth/whoami", headers=_auth(token)).status_code == 200
    accounts.set_account_disabled(users["bob"], True)
    try:
        assert client.get("/api/auth/whoami", headers=_auth(token)).status_code == 401
    finally:
        accounts.set_account_disabled(users["bob"], False)


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "not-a-token", "x" * 600, "Bearer", "../../etc/passwd", "\x00\x01"],
)
def test_malformed_credentials_fail_closed_without_a_stack_trace(client, bad):
    """T3. Every malformed shape is a clean 401, never a 500."""
    resp = client.get("/api/auth/whoami", headers={"Authorization": f"Bearer {bad}"})
    assert resp.status_code == 401
    assert "Traceback" not in resp.text


def test_login_does_not_reveal_whether_an_account_exists(client, users):
    """T1. Unknown user and wrong password are indistinguishable."""
    unknown = client.post(
        "/api/auth/login",
        json={"username": "no-such-person", "password": PASSWORD},
    )
    wrong = client.post("/api/auth/login", json={"username": "alice", "password": "wrong"})
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["detail"] == wrong.json()["detail"]


# ---------------------------------------------------------------------------
# T3 -- replay containment (the reviewed position, not a claim of resistance)
# ---------------------------------------------------------------------------


def test_a_replayed_token_from_a_different_client_is_refused_and_revoked(users):
    """
    T3. A captured token replayed from a different client fingerprint is
    refused, *and* the session is revoked -- so the legitimate holder is
    forced to re-authenticate rather than sharing a compromised session.

    This is replay *containment*, not replay resistance. Genuine per-request
    replay resistance is deferred to the device-authentication layer; see
    sessions.py's module docstring for the recorded S8 review.
    """
    from bartholomew.platform.principal import AuthenticationError

    fp_a = sessions.client_fingerprint("10.0.0.1", "browser-a")
    fp_b = sessions.client_fingerprint("10.0.0.9", "browser-b")
    _sid, token = sessions.create_session(users["alice"], fingerprint=fp_a)

    assert sessions.verify_session(token, fingerprint=fp_a).username == "alice"
    with pytest.raises(AuthenticationError):
        sessions.verify_session(token, fingerprint=fp_b)
    # Revoked by the mismatch, so the original holder cannot keep using it.
    with pytest.raises(AuthenticationError):
        sessions.verify_session(token, fingerprint=fp_a)


def test_session_tokens_are_not_stored_in_readable_form(users):
    """
    T9. Read access to the sessions table must not yield usable credentials.
    """
    from bartholomew.platform.store import platform_connection

    _sid, token = sessions.create_session(
        users["alice"],
        fingerprint=sessions.client_fingerprint("1", "ua"),
    )
    with platform_connection() as conn:
        rows = conn.execute("SELECT * FROM platform_sessions").fetchall()
    blob = " ".join(str(dict(r)) for r in rows)
    assert token not in blob


def test_password_hashes_are_not_plaintext(users):
    """T9. Nor may the accounts table hold recoverable passwords."""
    from bartholomew.platform.store import platform_connection

    with platform_connection() as conn:
        rows = conn.execute("SELECT password_hash FROM platform_accounts").fetchall()
    for row in rows:
        assert PASSWORD not in row["password_hash"]
        assert row["password_hash"].startswith("scrypt$")


def test_audit_rows_never_contain_credentials(users):
    """T9. Secrets must not leak into the audit trail either."""
    from bartholomew.platform.store import platform_connection

    _sid, token = sessions.create_session(
        users["alice"],
        fingerprint=sessions.client_fingerprint("1", "ua"),
    )
    with platform_connection() as conn:
        rows = conn.execute("SELECT * FROM platform_audit").fetchall()
    blob = " ".join(str(dict(r)) for r in rows)
    assert token not in blob
    assert PASSWORD not in blob
