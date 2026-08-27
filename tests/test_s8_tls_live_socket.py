"""
S8: the exposed deployment actually speaks TLS on a real socket.

Every other exposure test checks configuration. These two start a **real
uvicorn server on a real port** and speak to it over the network, because the
gap this closes was precisely that configuration looked correct while the
socket spoke plaintext: `exposure.py` validated that certificate files
existed, and nothing handed them to the listener.

Marked `slow` -- they bind a port and start a server process.
"""

from __future__ import annotations

import os
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tests.helpers.exposed_posture import establish_exposed_posture
from tests.helpers.tls_fixtures import write_self_signed_cert

pytestmark = pytest.mark.slow


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, proc: subprocess.Popen, timeout: float = 45.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.3)
    return False


@pytest.fixture
def tls_server(tmp_path):
    """
    A real `app.serve()` process, exposed and TLS-configured.

    Uses the canonical serve path deliberately: the point is to prove that the
    supported launch path puts TLS on the socket, not that uvicorn can do TLS.
    """
    cert, key = write_self_signed_cert(tmp_path)
    port = _free_port()

    # A provisioned account and an explicit runtime binding, because an
    # exposed deployment now requires both.
    platform_db = str(tmp_path / "platform.db")
    data_root = str(tmp_path / "data")
    env = {
        **os.environ,
        "BARTH_PLATFORM_DB_PATH": platform_db,
        "BARTH_DATA_ROOT": data_root,
        "BARTH_API_ALLOW_NON_LOOPBACK": "1",
        "BARTH_API_HOST": "127.0.0.1",
        "BARTH_API_TLS_CERTFILE": cert,
        "BARTH_API_TLS_KEYFILE": key,
        "BARTH_API_PORT": str(port),
        "BARTH_AUTH_MODE": "enforced",
    }

    provision = subprocess.run(
        [
            sys.executable,
            "-c",
            "from bartholomew.platform.store import init_platform_schema;"
            "from bartholomew.platform import accounts;"
            "from bartholomew.platform.runtime_registry import runtime_handle_for_user_id;"
            "init_platform_schema();"
            "uid = accounts.create_account('alpha', 'alpha-participant-password');"
            "h = runtime_handle_for_user_id(uid);"
            "print(uid); print(h.db_path); print(h.keyring_service)",
        ],
        check=False,
        env=env,
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert provision.returncode == 0, provision.stderr
    user_id, db_path, keyring_service = provision.stdout.strip().splitlines()[-3:]

    env["BARTH_RUNTIME_USER_ID"] = user_id
    env["BARTH_DB_PATH"] = db_path
    env["BARTHO_MEMORY_KEYRING_SERVICE"] = keyring_service

    proc = subprocess.Popen(
        [sys.executable, "-c", "import app; app.serve()"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    try:
        if not _wait_for_port(port, proc):
            proc.kill()
            raise AssertionError(f"server never listened:\n{proc.communicate()[0]}")
        yield port
    finally:
        proc.kill()
        proc.wait(timeout=15)


def test_the_exposed_serve_path_actually_speaks_https(tls_server):
    """
    **Positive proof.** A real HTTPS request over a real socket reaches the
    application. Anything less than this -- a config assertion, a mocked
    server -- would have passed while the deployment spoke plaintext.
    """
    ctx = ssl.create_default_context()
    # Self-signed: we are proving the transport is TLS, not validating a CA.
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    with urllib.request.urlopen(
        f"https://127.0.0.1:{tls_server}/healthz",
        context=ctx,
        timeout=20,
    ) as resp:
        assert resp.status == 200
        # And it really was TLS, not a redirect or a plaintext fallback.
        assert resp.fp.raw._sock.cipher() is not None


def test_plaintext_to_the_exposed_port_does_not_get_a_usable_response(tls_server):
    """
    **Negative proof.** A plaintext HTTP request to the TLS port must not
    yield a normal application response. A TLS listener answers a plaintext
    request with a TLS alert, which surfaces as a connection/protocol error --
    what must never happen is a 200.
    """
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{tls_server}/healthz",
            timeout=20,
        ) as resp:
            body = resp.read()
            raise AssertionError(
                f"plaintext request was served: status={resp.status} body={body[:200]!r}",
            )
    except urllib.error.HTTPError as exc:  # pragma: no cover - defensive
        assert exc.code != 200, "plaintext request was served a 200"
    except (urllib.error.URLError, ConnectionError, ssl.SSLError, OSError):
        # Expected: the TLS listener refuses to speak plaintext.
        pass


def test_a_handlaunched_plaintext_exposed_process_refuses_requests(monkeypatch, tmp_path):
    """
    The launch path `serve()` cannot control: starting the app through the
    `uvicorn` CLI never calls it, so nothing configures TLS. The request
    boundary must refuse anyway, so the guarantee does not depend on how the
    process started.

    Exercised in-process against the real middleware, since the point is the
    boundary's decision rather than uvicorn's behaviour: a plaintext
    `base_url` is exactly what a hand-launched plaintext server would give.
    """
    from fastapi.testclient import TestClient

    establish_exposed_posture(monkeypatch, tmp_path)

    from bartholomew_api_bridge_v0_1.services.api.app import app

    with TestClient(app, base_url="http://testserver", raise_server_exceptions=False) as c:
        resp = c.get("/healthz")
    assert resp.status_code == 403
    assert "TLS only" in resp.text


def test_uvicorn_tls_kwargs_are_empty_for_a_loopback_deployment(monkeypatch):
    """The local development path must not suddenly require TLS."""
    from bartholomew.platform.exposure import uvicorn_tls_kwargs

    monkeypatch.delenv("BARTH_API_ALLOW_NON_LOOPBACK", raising=False)
    assert uvicorn_tls_kwargs() == {}


def test_uvicorn_tls_kwargs_carry_the_material_when_exposed(monkeypatch, tmp_path):
    """And the exposed path must hand real paths to the socket."""
    from bartholomew.platform.exposure import uvicorn_tls_kwargs

    cert, key = write_self_signed_cert(tmp_path)
    monkeypatch.setenv("BARTH_API_ALLOW_NON_LOOPBACK", "1")
    monkeypatch.setenv("BARTH_API_TLS_CERTFILE", cert)
    monkeypatch.setenv("BARTH_API_TLS_KEYFILE", key)
    assert uvicorn_tls_kwargs() == {"ssl_certfile": cert, "ssl_keyfile": key}
