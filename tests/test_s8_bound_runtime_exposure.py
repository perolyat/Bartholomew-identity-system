"""
S8: an exposed deployment must know whose Bartholomew it is serving.

The gap this closes: `BARTH_RUNTIME_USER_ID` was optional, so an exposed
process without it made the ownership check a no-op and every authenticated
account reached the one global kernel the process happened to be running.

There is now **no unbound remote personal-runtime mode**. Startup requires a
bound, provisioned, enabled user and verifies that the database and keyring
namespace actually in use are that user's; the request boundary refuses an
unbound exposed process as defence in depth.
"""

from __future__ import annotations

import tempfile
import uuid

import pytest

from bartholomew.platform import accounts
from bartholomew.platform.exposure import (
    ExposureConfigurationError,
    assert_exposure_is_safe,
    require_bound_runtime_user,
)
from bartholomew.platform.principal import (
    AuthorizationError,
    Principal,
    PrincipalKind,
)
from bartholomew.platform.runtime_registry import (
    RUNTIME_USER_ID_ENV,
    assert_principal_owns_this_process,
    runtime_handle_for_user_id,
)
from bartholomew.platform.store import init_platform_schema

PASSWORD = "alpha-participant-password"


@pytest.fixture
def exposed(monkeypatch, tmp_path):
    """An exposed, TLS-satisfied deployment with two provisioned accounts."""
    cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
    cert.write_text("cert")
    key.write_text("key")
    tmp = tempfile.mkdtemp(prefix="s8-bound-")
    monkeypatch.setenv("BARTH_PLATFORM_DB_PATH", f"{tmp}/platform.db")
    monkeypatch.setenv("BARTH_DATA_ROOT", f"{tmp}/data")
    monkeypatch.setenv("BARTH_API_ALLOW_NON_LOOPBACK", "1")
    monkeypatch.setenv("BARTH_API_TLS_CERTFILE", str(cert))
    monkeypatch.setenv("BARTH_API_TLS_KEYFILE", str(key))
    init_platform_schema()
    alice = accounts.create_account("alice", PASSWORD)
    bob = accounts.create_account("bob", PASSWORD)
    ops = accounts.create_account("ops", PASSWORD, kind=PrincipalKind.PLATFORM_ADMIN)
    return {"alice": alice, "bob": bob, "ops": ops, "monkeypatch": monkeypatch}


def _bind(mp, user_id):
    """Bind the process to `user_id`, consistently."""
    handle = runtime_handle_for_user_id(user_id)
    mp.setenv(RUNTIME_USER_ID_ENV, user_id)
    mp.setenv("BARTH_DB_PATH", handle.db_path)
    mp.setenv("BARTHO_MEMORY_KEYRING_SERVICE", handle.keyring_service)
    return handle


# ---------------------------------------------------------------------------
# Startup: no unbound remote mode
# ---------------------------------------------------------------------------


def test_an_exposed_deployment_refuses_to_start_without_a_bound_user(exposed):
    with pytest.raises(ExposureConfigurationError, match="must name the personal"):
        assert_exposure_is_safe()


def test_a_correctly_bound_exposed_deployment_starts(exposed):
    _bind(exposed["monkeypatch"], exposed["alice"])
    assert require_bound_runtime_user() == exposed["alice"]
    assert_exposure_is_safe()


def test_a_binding_that_names_no_account_is_refused(exposed):
    mp = exposed["monkeypatch"]
    stranger = str(uuid.uuid4())
    handle = runtime_handle_for_user_id(stranger)
    mp.setenv(RUNTIME_USER_ID_ENV, stranger)
    mp.setenv("BARTH_DB_PATH", handle.db_path)
    mp.setenv("BARTHO_MEMORY_KEYRING_SERVICE", handle.keyring_service)
    with pytest.raises(ExposureConfigurationError, match="no provisioned account"):
        assert_exposure_is_safe()


def test_a_disabled_account_cannot_be_the_bound_runtime(exposed):
    _bind(exposed["monkeypatch"], exposed["alice"])
    accounts.set_account_disabled(exposed["alice"], True)
    with pytest.raises(ExposureConfigurationError, match="disabled account"):
        assert_exposure_is_safe()


def test_a_platform_admin_cannot_be_the_bound_runtime(exposed):
    """An administrator has no personal Bartholomew to serve."""
    mp = exposed["monkeypatch"]
    handle = runtime_handle_for_user_id(exposed["ops"])
    mp.setenv(RUNTIME_USER_ID_ENV, exposed["ops"])
    mp.setenv("BARTH_DB_PATH", handle.db_path)
    mp.setenv("BARTHO_MEMORY_KEYRING_SERVICE", handle.keyring_service)
    with pytest.raises(ExposureConfigurationError, match="no personal Bartholomew"):
        assert_exposure_is_safe()


@pytest.mark.parametrize("hostile", ["", "   ", "../bob", "not-a-uuid", "/etc/passwd"])
def test_a_malformed_binding_is_refused(exposed, hostile):
    exposed["monkeypatch"].setenv(RUNTIME_USER_ID_ENV, hostile)
    with pytest.raises(ExposureConfigurationError):
        assert_exposure_is_safe()


# ---------------------------------------------------------------------------
# The identity/persistence agreement -- the cross-user case
# ---------------------------------------------------------------------------


def test_bound_to_alice_but_pointed_at_bobs_database_is_refused(exposed):
    """
    The disclosure this prevents: a process that authenticates Alice
    correctly and then serves Bob's memory, because its database path was
    never checked against its binding.
    """
    mp = exposed["monkeypatch"]
    _bind(mp, exposed["alice"])
    mp.setenv("BARTH_DB_PATH", runtime_handle_for_user_id(exposed["bob"]).db_path)
    with pytest.raises(ExposureConfigurationError, match="not the database of the bound user"):
        assert_exposure_is_safe()


def test_bound_to_alice_but_using_bobs_keyring_namespace_is_refused(exposed):
    """
    The same disclosure one layer down: the right database, decrypted with
    another participant's key namespace.
    """
    mp = exposed["monkeypatch"]
    _bind(mp, exposed["alice"])
    mp.setenv(
        "BARTHO_MEMORY_KEYRING_SERVICE",
        runtime_handle_for_user_id(exposed["bob"]).keyring_service,
    )
    with pytest.raises(ExposureConfigurationError, match="keyring namespace"):
        assert_exposure_is_safe()


def test_an_exposed_deployment_must_set_the_database_explicitly(exposed):
    mp = exposed["monkeypatch"]
    mp.setenv(RUNTIME_USER_ID_ENV, exposed["alice"])
    mp.delenv("BARTH_DB_PATH", raising=False)
    with pytest.raises(ExposureConfigurationError, match="must set BARTH_DB_PATH"):
        assert_exposure_is_safe()


# ---------------------------------------------------------------------------
# Request boundary: defence in depth
# ---------------------------------------------------------------------------


def test_a_bound_process_refuses_every_other_authenticated_identity(exposed):
    mp = exposed["monkeypatch"]
    _bind(mp, exposed["alice"])
    alice = Principal(exposed["alice"], "alice", PrincipalKind.USER, "s")
    bob = Principal(exposed["bob"], "bob", PrincipalKind.USER, "s")

    assert_principal_owns_this_process(alice)
    with pytest.raises(AuthorizationError):
        assert_principal_owns_this_process(bob)


def test_an_exposed_unbound_process_refuses_everyone(exposed):
    """
    Defence in depth for the launch path startup cannot police -- a hand
    started server, an embedding harness, a future entry point. An exposed
    process with no binding must refuse rather than no-op.
    """
    exposed["monkeypatch"].delenv(RUNTIME_USER_ID_ENV, raising=False)
    for user_id, name in ((exposed["alice"], "alice"), (exposed["bob"], "bob")):
        with pytest.raises(AuthorizationError, match="not bound"):
            assert_principal_owns_this_process(
                Principal(user_id, name, PrincipalKind.USER, "s"),
            )


def test_a_local_unbound_process_is_still_allowed(monkeypatch):
    """
    The single-user loopback development deployment is unchanged: unbound is
    a no-op there, because there is no second identity to confuse it with.
    """
    monkeypatch.delenv("BARTH_API_ALLOW_NON_LOOPBACK", raising=False)
    monkeypatch.delenv(RUNTIME_USER_ID_ENV, raising=False)
    assert_principal_owns_this_process(
        Principal(str(uuid.uuid4()), "solo", PrincipalKind.USER, "s"),
    )


def test_the_control_plane_database_follows_the_data_root(monkeypatch, tmp_path):
    """
    The control-plane default must honour `BARTH_DATA_ROOT`.

    Hardcoding the repository's `data/` directory meant a caller that had
    isolated every other persistence surface -- which is what the test suite
    does -- still shared one physical control-plane file, reintroducing the
    cross-caller sharing that fresh-per-call resolution exists to prevent.
    """
    from bartholomew.platform.store import resolve_platform_db_path

    monkeypatch.delenv("BARTH_PLATFORM_DB_PATH", raising=False)
    monkeypatch.setenv("BARTH_DATA_ROOT", str(tmp_path))
    assert resolve_platform_db_path() == str(tmp_path / "platform.db")


def test_an_explicit_control_plane_path_still_wins(monkeypatch, tmp_path):
    from bartholomew.platform.store import resolve_platform_db_path

    explicit = str(tmp_path / "explicit.db")
    monkeypatch.setenv("BARTH_DATA_ROOT", str(tmp_path / "elsewhere"))
    monkeypatch.setenv("BARTH_PLATFORM_DB_PATH", explicit)
    assert resolve_platform_db_path() == explicit
