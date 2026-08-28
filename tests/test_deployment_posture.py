"""The deployment files must keep the properties they claim (Session D).

Deployment configuration is the one part of a system that is never exercised
by ordinary tests and is edited under time pressure during an incident. These
assertions are cheap and they pin the properties that would be expensive to
discover were missing: published only to loopback, TLS actually configured,
the healthcheck really speaking HTTPS, and no environment variable that
downgrades any of it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE = REPO_ROOT / "docker-compose.yml"
UNIT = REPO_ROOT / "deploy" / "bartholomew.service"
DOCKERFILE = REPO_ROOT / "Dockerfile"


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


@pytest.fixture(scope="module")
def api_service(compose) -> dict:
    return compose["services"]["api"]


def _env_map(service: dict) -> dict[str, str]:
    env = service.get("environment", [])
    if isinstance(env, dict):
        return {k: str(v) for k, v in env.items()}
    out = {}
    for entry in env:
        key, _, value = str(entry).partition("=")
        out[key] = value
    return out


# ---------------------------------------------------------------------------
# Compose: an authenticated, TLS, runtime-bound proof
# ---------------------------------------------------------------------------


def test_ports_are_published_to_loopback_only(api_service):
    """A bare "5173:5173" publishes on every host interface."""
    for mapping in api_service["ports"]:
        assert str(mapping).startswith(
            "127.0.0.1:",
        ), f"port mapping {mapping!r} is not confined to the host's loopback"


def test_authentication_is_enforced(api_service):
    env = _env_map(api_service)
    assert env.get("BARTH_AUTH_MODE") == "enforced"
    assert env.get("BARTH_API_ALLOW_NON_LOOPBACK") == "1"


def test_tls_material_is_configured_and_mounted(api_service):
    env = _env_map(api_service)
    cert = env.get("BARTH_API_TLS_CERTFILE")
    key = env.get("BARTH_API_TLS_KEYFILE")
    assert cert and key, "the container declares no TLS material"

    mount_targets = [str(v).split(":")[1] for v in api_service["volumes"] if ":" in str(v)]
    assert any(
        cert.startswith(target) for target in mount_targets
    ), f"the certificate {cert!r} is not on any mounted volume"
    assert any(key.startswith(target) for target in mount_targets)

    # Read-only: a container that can rewrite its own private key is a
    # container whose key is only as protected as the process inside it.
    key_mount = next(v for v in api_service["volumes"] if "/run/secrets" in str(v))
    assert str(key_mount).endswith(":ro")


def test_the_runtime_binding_is_explicit_and_has_no_default(api_service):
    """Whose Bartholomew this serves must be stated, not inferred.

    A process bound to one user refuses every other authenticated identity, so
    a container that guessed its binding would refuse the wrong people.
    """
    env = _env_map(api_service)
    binding = env.get("BARTH_RUNTIME_USER_ID", "")
    assert binding, "the container does not declare which runtime it serves"
    # `:?` is compose's "required, no default" form -- `:-` would silently
    # substitute a fallback.
    assert ":?" in binding, f"runtime binding {binding!r} has a silent default"


def test_the_healthcheck_speaks_https_and_verifies_the_certificate(api_service):
    test = api_service["healthcheck"]["test"]
    joined = " ".join(test) if isinstance(test, list) else str(test)

    assert "https://" in joined, "the healthcheck still probes plaintext HTTP"
    assert "--cacert" in joined, "the healthcheck does not verify the certificate"
    # -k/--insecure would pass against a misconfigured certificate, which is
    # most of what this healthcheck exists to catch.
    assert " -k" not in joined and "--insecure" not in joined


def test_there_is_no_topology_assertion_bypass(api_service):
    """No variable may declare the deployment safe and thereby skip auth/TLS.

    Session B's note offered such a flag as an option. It is deliberately not
    taken: from inside the process it is indistinguishable from a genuinely
    exposed deployment, which is exactly what makes it a quiet bypass.
    """
    env = _env_map(api_service)
    for suspicious in ("BARTH_LOCAL_ONLY", "BARTH_DEPLOYMENT_LOCAL", "BARTH_SKIP_TLS"):
        assert suspicious not in env
    assert env.get("BARTH_AUTH_MODE") != "disabled"


def test_the_container_supervises_and_shuts_down_gracefully(api_service):
    assert api_service["restart"] == "unless-stopped"
    # Must exceed serve.SHUTDOWN_BUDGET_SECONDS or Docker SIGKILLs a daemon
    # partway through its WAL checkpoint.
    from bartholomew.runtime.serve import SHUTDOWN_BUDGET_SECONDS

    grace = str(api_service["stop_grace_period"]).rstrip("s")
    assert int(grace) > SHUTDOWN_BUDGET_SECONDS


def test_generated_certificates_are_not_committed():
    """A private key in the repository is a private key on every clone."""
    ignore = (REPO_ROOT / "deploy" / ".gitignore").read_text()
    assert "certs/" in ignore
    assert not list(
        (REPO_ROOT / "deploy").glob("certs/*.pem"),
    ), "certificate material is present in the working tree"


# ---------------------------------------------------------------------------
# systemd: loopback-safe by default
# ---------------------------------------------------------------------------


def test_the_unit_does_not_expose_the_service_by_default():
    """The server-centric unit must not quietly become network-reachable.

    Exposure is a deliberate decision that brings authentication and TLS with
    it; it is not something a unit file should arrive pre-set.
    """
    unit = UNIT.read_text()
    active = [
        line for line in unit.splitlines() if line.strip() and not line.strip().startswith("#")
    ]
    for line in active:
        assert not line.startswith(
            "Environment=BARTH_API_ALLOW_NON_LOOPBACK",
        ), "the unit file enables a non-loopback bind by default"
        assert not line.startswith("Environment=BARTH_AUTH_MODE=disabled")


def test_the_unit_restarts_on_component_failure_but_not_on_bad_config():
    from bartholomew.runtime import serve, supervision

    unit = UNIT.read_text()
    assert "Restart=on-failure" in unit
    prevented_line = next(
        line for line in unit.splitlines() if line.startswith("RestartPreventExitStatus=")
    )
    prevented = {int(c) for c in prevented_line.split("=", 1)[1].split()}
    assert supervision.EXIT_RUNTIME_FAILURE not in prevented
    assert {serve.EXIT_LOCK_HELD, serve.EXIT_BAD_CONFIG} <= prevented


def test_the_dockerfile_does_not_claim_the_api_is_unauthenticated():
    """It said so when that was true; under S8 a non-loopback bind forces
    authentication on, and a stale security comment misleads the next reader."""
    text = DOCKERFILE.read_text()
    assert "NO AUTHENTICATION" not in text
