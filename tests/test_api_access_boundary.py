"""
The API access boundary: the unauthenticated bridge is loopback-only.

Decision (2026-08-26). `DECISIONS.md` and `INTERFACES.md` already stated that
this API has no authentication and must not be exposed beyond localhost; the
Dockerfile bound 0.0.0.0, docker-compose published on every host interface,
and QUICKSTART.md demonstrated a LAN bind. This resolves that contradiction in
favour of the canonical documents.

These tests exercise the real resolver and the real middleware. Only the last
two look at repository files, and they do so because a launch command is a
string in a file -- there is no executable behaviour left to test once the
in-process boundary has already been covered above.

This boundary is NOT authentication. It decides where the API is reachable
from, not who is asking.
"""

from __future__ import annotations

import pathlib

import pytest
from fastapi.testclient import TestClient

from bartholomew_api_bridge_v0_1.services.api.app import (
    ALLOW_NON_LOOPBACK_ENV,
    BIND_HOST_ENV,
    DEFAULT_BIND_HOST,
    app,
    is_loopback_host,
    non_loopback_allowed,
    resolve_bind_host,
)

REPO = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Every test starts from an unconfigured environment."""
    monkeypatch.delenv(ALLOW_NON_LOOPBACK_ENV, raising=False)
    monkeypatch.delenv(BIND_HOST_ENV, raising=False)


# ---------------------------------------------------------------------------
# The default
# ---------------------------------------------------------------------------


def test_default_bind_is_loopback():
    assert DEFAULT_BIND_HOST == "127.0.0.1"
    assert resolve_bind_host() == "127.0.0.1"
    assert is_loopback_host(resolve_bind_host())


def test_default_configuration_cannot_resolve_to_all_interfaces(monkeypatch):
    """The specific failure this decision exists to prevent."""
    monkeypatch.setenv(BIND_HOST_ENV, "0.0.0.0")
    with pytest.raises(RuntimeError, match="not a loopback address"):
        resolve_bind_host()


@pytest.mark.parametrize("host", ["0.0.0.0", "10.0.0.10", "192.168.1.5", "::"])
def test_non_loopback_bind_is_refused_without_explicit_opt_in(monkeypatch, host):
    monkeypatch.setenv(BIND_HOST_ENV, host)
    assert not non_loopback_allowed()
    with pytest.raises(RuntimeError):
        resolve_bind_host()


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.5"])
def test_loopback_forms_are_accepted(monkeypatch, host):
    monkeypatch.setenv(BIND_HOST_ENV, host)
    assert resolve_bind_host() == host


def test_empty_or_unset_host_falls_back_to_loopback(monkeypatch):
    monkeypatch.setenv(BIND_HOST_ENV, "   ")
    assert resolve_bind_host() == "127.0.0.1"


# ---------------------------------------------------------------------------
# The documented override
# ---------------------------------------------------------------------------


def test_explicit_override_permits_a_non_loopback_bind(monkeypatch, capsys):
    monkeypatch.setenv(BIND_HOST_ENV, "0.0.0.0")
    monkeypatch.setenv(ALLOW_NON_LOOPBACK_ENV, "1")

    assert non_loopback_allowed()
    assert resolve_bind_host() == "0.0.0.0"

    warning = capsys.readouterr().err
    assert "NON-LOOPBACK" in warning
    assert "NO AUTHENTICATION" in warning
    assert "personal memory" in warning
    assert ALLOW_NON_LOOPBACK_ENV in warning


def test_override_must_be_deliberate_not_incidental(monkeypatch):
    """An empty or false-y value is not an opt-in."""
    monkeypatch.setenv(BIND_HOST_ENV, "0.0.0.0")
    for value in ["", "0", "false", "no", "off"]:
        monkeypatch.setenv(ALLOW_NON_LOOPBACK_ENV, value)
        assert not non_loopback_allowed(), f"{value!r} must not enable exposure"
        with pytest.raises(RuntimeError):
            resolve_bind_host()


# ---------------------------------------------------------------------------
# The request boundary -- what actually protects Memory Agency
# ---------------------------------------------------------------------------

MEMORY_ROUTES = [
    ("get", "/api/memory"),
    ("get", "/api/memory/kinds"),
    ("get", "/api/memory/export"),
    ("get", "/api/memory/fact/anything"),
    ("put", "/api/memory/fact/anything"),
    ("delete", "/api/memory/fact/anything?confirm=true"),
]


@pytest.mark.parametrize(("method", "path"), MEMORY_ROUTES)
def test_memory_endpoints_refuse_a_non_local_caller(method, path):
    """
    Every Memory Agency route -- list, read, correct, delete, export -- is
    refused for a non-loopback peer. Not route-specific security: the check
    lives in the single admission chokepoint and covers the whole bridge.
    """
    with TestClient(app, client=("192.168.1.50", 51234)) as client:
        # Only PUT carries a body; GET/DELETE reject the argument.
        kwargs = {"json": {"value": "x"}} if method == "put" else {}
        response = getattr(client, method)(path, **kwargs)
    assert response.status_code == 403
    assert "loopback-only" in response.json()["detail"]


def test_the_brake_is_also_refused_for_a_non_local_caller():
    """The brake was reachable unauthenticated too; the boundary covers it."""
    with TestClient(app, client=("192.168.1.50", 51234)) as client:
        response = client.post(
            "/api/governance/brake/disengage",
            json={"reason": "x", "actor": "x", "expected_revision": 1},
        )
    assert response.status_code == 403


def test_exempt_paths_are_not_exempt_from_the_network_boundary():
    """
    /healthz and /ui skip the kernel-readiness gate. They must not skip the
    question of who may reach this process at all.
    """
    with TestClient(app, client=("192.168.1.50", 51234)) as client:
        for path in ["/healthz", "/api/health", "/ui/", "/openapi.json"]:
            assert client.get(path).status_code == 403, path


def test_a_local_caller_is_not_blocked():
    with TestClient(app, client=("127.0.0.1", 51234)) as client:
        assert client.get("/healthz").status_code == 200


def test_forwarded_headers_cannot_spoof_a_local_caller():
    """
    X-Forwarded-For is attacker-controlled and there is no trusted proxy in
    this architecture, so it must not be consulted.
    """
    with TestClient(app, client=("192.168.1.50", 51234)) as client:
        response = client.get(
            "/healthz",
            headers={"X-Forwarded-For": "127.0.0.1", "X-Real-IP": "127.0.0.1"},
        )
    assert response.status_code == 403


def test_the_override_also_opens_the_request_boundary(monkeypatch):
    """The container case: one deliberate switch, both layers."""
    monkeypatch.setenv(ALLOW_NON_LOOPBACK_ENV, "1")
    with TestClient(app, client=("172.17.0.1", 51234)) as client:
        assert client.get("/healthz").status_code == 200


# ---------------------------------------------------------------------------
# Launch paths must agree with the default
# ---------------------------------------------------------------------------


def test_no_launch_path_binds_all_interfaces_outside_a_container():
    """
    The Dockerfile binds 0.0.0.0 inside the container's own namespace, which
    is required for a published port to work at all and is gated by the
    explicit opt-in plus a loopback-only host publish. Nothing else may.
    """
    offenders = []
    for name in ["scripts/start_bartholomew.sh", "QUICKSTART.md", "docker-compose.yml"]:
        text = (REPO / name).read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "0.0.0.0" in stripped or "--host 10." in stripped:
                offenders.append(f"{name}: {stripped}")
    assert not offenders, f"non-loopback bind outside the container: {offenders}"


def test_compose_publishes_to_loopback_only():
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    assert '"127.0.0.1:5173:5173"' in compose
    assert '- "5173:5173"' not in compose, "publishes on every host interface"


def test_container_opt_in_is_explicit_in_the_image():
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
    assert f"ENV {ALLOW_NON_LOOPBACK_ENV}=1" in dockerfile


def test_root_entrypoint_binds_through_the_resolver():
    """`python -m app` must not hardcode a host."""
    source = (REPO / "app.py").read_text(encoding="utf-8")
    assert "resolve_bind_host()" in source
    assert "0.0.0.0" not in source
