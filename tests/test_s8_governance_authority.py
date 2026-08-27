"""
S8: a remote client cannot weaken local Governance.

This is the architectural constraint the whole design is shaped around, and
the one S8 names explicitly. The properties under test:

* a remote client cannot bypass Governance;
* a remote client cannot weaken the Parking Brake;
* authentication failure cannot fail open;
* cloud/network/authentication failure cannot disable the *local* Parking
  Brake.

The last one is the asymmetry that makes the design safe: authentication can
only ever *prevent* a remote request from reaching the brake. It is never on
the path that engages one locally.
"""

from __future__ import annotations

import tempfile

import pytest
from fastapi.testclient import TestClient  # noqa: E402

from bartholomew.platform import accounts, authority  # noqa: E402
from bartholomew.platform.capabilities import (  # noqa: E402
    Capability,
    capabilities_for,
)
from bartholomew.platform.principal import (  # noqa: E402
    AuthorizationError,
    Principal,
    PrincipalKind,
)
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
    tmp = tempfile.mkdtemp(prefix="s8-gov-")
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
def _accounts():
    init_platform_schema()
    made = {}
    for name, kind in (("alice", PrincipalKind.USER), ("ops", PrincipalKind.PLATFORM_ADMIN)):
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


# ---------------------------------------------------------------------------
# T7 -- unauthorised Parking Brake change (the S8 named test)
# ---------------------------------------------------------------------------


def test_an_unauthenticated_caller_cannot_release_the_parking_brake(client):
    """
    T7. `docs/FIRST_REAL_WORLD_TEST.md` §0 records that at the tested
    implementation an unauthenticated `curl` could engage *and disengage* the
    Parking Brake, and that Test #1 was contained by binding 127.0.0.1. This
    is the test that the containment is no longer only a network condition.
    """
    for path in ("/api/governance/brake/engage", "/api/governance/brake/disengage"):
        resp = client.post(path, json={})
        assert resp.status_code in (401, 403), f"{path} answered {resp.status_code}"


def test_an_unauthenticated_caller_cannot_even_read_brake_state(client):
    """T7. Brake state is not public either -- it reveals a user's posture."""
    assert client.get("/api/governance/brake").status_code in (401, 403)


# ---------------------------------------------------------------------------
# T10 -- the two authority tiers are genuinely separate
# ---------------------------------------------------------------------------


def test_a_user_cannot_reach_platform_authority():
    """
    T10. No capability in the ordinary user set reaches the Platform tier.
    Canonically: a user must not be able to override a platform halt through
    personal settings.
    """
    alice = Principal("id", "alice", PrincipalKind.USER, "s")
    assert Capability.PLATFORM_ADMIN not in capabilities_for(alice)


def test_a_platform_admin_cannot_read_personal_memory():
    """
    T10. Administration is a different authority, not a superset. An admin
    holding `memory:read` would make "platform administrator" a euphemism for
    "can read everyone's diary".
    """
    from bartholomew.platform.capabilities import require_capability

    ops = Principal("id", "ops", PrincipalKind.PLATFORM_ADMIN, "s")
    for cap in (
        Capability.MEMORY_READ,
        Capability.MEMORY_WRITE,
        Capability.MEMORY_EXPORT,
        Capability.CHAT,
        Capability.CONSENT_DECIDE,
    ):
        with pytest.raises(AuthorizationError):
            require_capability(ops, cap)


def test_the_platform_tier_is_not_a_scope_on_the_personal_brake():
    """
    T10. `DECISIONS.md` names adding `"platform"` to the `scopes` set as a
    category error, because scopes are cleared by the same `disengage()` any
    user may call. This asserts the tiers really are separate stores: the
    platform halt lives in the control-plane database, and the per-user
    governance scope vocabulary does not contain a platform value.
    """
    from bartholomew_api_bridge_v0_1.services.api.routes.governance import VALID_SCOPES

    assert "platform" not in VALID_SCOPES
    assert "admin" not in VALID_SCOPES


def test_tiers_compose_restrictively_and_neither_release_implies_the_other():
    """
    T7/T10. Execution proceeds only if neither tier blocks. Releasing the
    platform halt must not release a personal one, and vice versa.
    """
    init_platform_schema()
    authority.disengage(actor="test-reset", reason="clean slate")

    # Platform halt alone blocks.
    authority.engage("skills", actor="ops", reason="systemic defect")
    assert authority.is_blocked("skills", personal_blocked=False) is True
    # An unrelated scope is unaffected.
    assert authority.is_blocked("voice", personal_blocked=False) is False
    # Personal halt alone blocks, independently of the platform tier.
    assert authority.is_blocked("voice", personal_blocked=True) is True

    # Releasing the platform tier does not release the personal one.
    authority.disengage(actor="ops", reason="fixed")
    assert authority.is_blocked("skills", personal_blocked=True) is True
    assert authority.is_blocked("skills", personal_blocked=False) is False


def test_the_platform_halt_fails_closed_when_its_store_is_unreadable(monkeypatch):
    """
    T7. An unreadable safety halt is treated as a halt. The inverse -- "we
    could not read it, so carry on" -- is how a safety control becomes
    decorative.
    """
    monkeypatch.setenv("BARTH_PLATFORM_DB_PATH", "/proc/1/definitely/not/a/db")
    assert authority.is_blocked("skills", personal_blocked=False) is True


# ---------------------------------------------------------------------------
# T8 -- local Governance survives the authentication layer failing entirely
# ---------------------------------------------------------------------------


def test_the_local_parking_brake_works_with_authentication_unavailable(monkeypatch, tmp_path):
    """
    T8. The property that makes the whole architecture acceptable: with the
    control-plane database gone, a user can still engage their own Parking
    Brake locally, through the CLI path, with no network and no session.

    A cloud or authentication outage must never leave local autonomous
    execution unstoppable -- canonically required, and the reason the
    Personal tier lives in the user's own database rather than behind the
    control plane.
    """
    from bartholomew.orchestrator.safety.governance_store import GovernanceStore

    monkeypatch.setenv("BARTH_PLATFORM_DB_PATH", "/proc/1/definitely/not/a/db")

    user_db = str(tmp_path / "alice.db")
    store = GovernanceStore(user_db)
    store.engage("global", reason="user halts locally during an outage", actor="cli")
    assert store.refresh().engaged is True


def test_engaging_the_local_brake_never_consults_the_control_plane():
    """
    T8, structurally. `GovernanceStore` must not import or reach the platform
    package: if it ever did, a control-plane outage could block a user from
    stopping their own Bartholomew.
    """
    import pathlib

    source = pathlib.Path("bartholomew/orchestrator/safety/governance_store.py").read_text()
    assert "bartholomew.platform" not in source
    assert "platform_connection" not in source

    brake = pathlib.Path("bartholomew/orchestrator/safety/parking_brake.py").read_text()
    assert "bartholomew.platform" not in brake


# ---------------------------------------------------------------------------
# T-fail-open -- authentication being unavailable must never admit a request
# ---------------------------------------------------------------------------


def test_authentication_unavailable_yields_503_not_200(client, monkeypatch):
    """
    The single most important negative test. If the control-plane store
    cannot answer, the request is refused with 503 -- never served, never
    downgraded to an anonymous identity.
    """
    monkeypatch.setenv("BARTH_PLATFORM_DB_PATH", "/proc/1/definitely/not/a/db")
    resp = client.get("/api/memory", headers={"Authorization": "Bearer anything-at-all"})
    assert resp.status_code == 503
    assert resp.status_code != 200


def test_there_is_no_anonymous_principal_kind():
    """
    Structural. Fail-open is unrepresentable: no `PrincipalKind` means
    "unauthenticated", so no handler can be handed one.
    """
    values = {k.value for k in PrincipalKind}
    assert values == {"user", "platform_admin"}
    for forbidden in ("anonymous", "guest", "public", "system", "default"):
        assert forbidden not in values
