"""
S8: unsafe exposure postures must stop the process, not warn.

The failure this prevents is the whole of S8's "explicit network exposure"
clause: an unauthenticated Bartholomew reachable on a routable interface.
Every test here asserts a *refusal*.
"""

from __future__ import annotations

import pytest

from bartholomew.platform.exposure import (
    ALLOW_NON_LOOPBACK_ENV,
    AUTH_MODE_ENV,
    TLS_CERT_ENV,
    TLS_KEY_ENV,
    AuthMode,
    ExposureConfigurationError,
    assert_exposure_is_safe,
    resolve_auth_mode,
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for var in (AUTH_MODE_ENV, ALLOW_NON_LOOPBACK_ENV, TLS_CERT_ENV, TLS_KEY_ENV):
        monkeypatch.delenv(var, raising=False)


def _tls(monkeypatch, tmp_path):
    cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
    cert.write_text("cert")
    key.write_text("key")
    monkeypatch.setenv(TLS_CERT_ENV, str(cert))
    monkeypatch.setenv(TLS_KEY_ENV, str(key))


def test_loopback_only_defaults_to_the_existing_single_user_behaviour():
    """
    The current local development deployment is unchanged: loopback-only, no
    authentication. This is what keeps the repository's existing tests and
    Taylor's own workflow working.
    """
    assert resolve_auth_mode() is AuthMode.DISABLED


def test_a_non_loopback_bind_forces_authentication_on(monkeypatch, tmp_path):
    """The core rule: exposure implies authentication, with no way to opt out."""
    monkeypatch.setenv(ALLOW_NON_LOOPBACK_ENV, "1")
    _tls(monkeypatch, tmp_path)
    assert resolve_auth_mode() is AuthMode.ENFORCED


def test_disabling_auth_on_a_non_loopback_bind_refuses_to_start(monkeypatch, tmp_path):
    """
    The bypass that must be impossible. An operator asking for an
    unauthenticated remote deployment gets a refusal, not a silent upgrade
    and not a warning they will scroll past.
    """
    monkeypatch.setenv(ALLOW_NON_LOOPBACK_ENV, "1")
    monkeypatch.setenv(AUTH_MODE_ENV, "disabled")
    _tls(monkeypatch, tmp_path)
    with pytest.raises(ExposureConfigurationError, match="cannot be combined"):
        assert_exposure_is_safe()


def test_a_non_loopback_bind_without_tls_refuses_to_start(monkeypatch):
    """
    Session cookies are bearer tokens. Without TLS they are readable by
    anything on the path, which would make the replay position in
    sessions.py untrue.
    """
    monkeypatch.setenv(ALLOW_NON_LOOPBACK_ENV, "1")
    with pytest.raises(ExposureConfigurationError, match="requires TLS"):
        assert_exposure_is_safe()


def test_tls_material_that_does_not_exist_refuses_to_start(monkeypatch):
    """A path pointing at nothing is a misconfiguration, not a configuration."""
    monkeypatch.setenv(ALLOW_NON_LOOPBACK_ENV, "1")
    monkeypatch.setenv(TLS_CERT_ENV, "/no/such/cert.pem")
    monkeypatch.setenv(TLS_KEY_ENV, "/no/such/key.pem")
    with pytest.raises(ExposureConfigurationError, match="does not exist"):
        assert_exposure_is_safe()


@pytest.mark.parametrize("typo", ["enfoced", "ENFORCE", "on", "true", "yes", "strict", "0"])
def test_an_unrecognised_auth_mode_refuses_rather_than_defaulting(monkeypatch, typo):
    """
    A typo must never fall back to a mode, least of all the permissive one.
    `BARTH_AUTH_MODE=enfoced` silently meaning "disabled" is exactly the
    class of bug that ships an open service.
    """
    monkeypatch.setenv(AUTH_MODE_ENV, typo)
    with pytest.raises(ExposureConfigurationError):
        resolve_auth_mode()


def test_there_is_no_environment_variable_that_downgrades_the_refusal():
    """
    Structural. If a future change adds a "just warn instead" escape hatch,
    this test is where it should be argued for rather than slipped in.
    """
    import pathlib

    source = pathlib.Path("bartholomew/platform/exposure.py").read_text()
    for hatch in ("SKIP_TLS", "INSECURE", "ALLOW_INSECURE", "DISABLE_TLS", "NO_TLS"):
        assert hatch not in source, f"an exposure escape hatch ({hatch}) has appeared"
