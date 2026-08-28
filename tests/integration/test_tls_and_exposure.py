"""TLS on the real socket, proven against a real server (Session D, req 5).

The claim is that Bartholomew *serves TLS*, so it is tested by speaking TLS to
it -- a real certificate, a real handshake, and a real plaintext request that
must fail. A unit test asserting that `ssl_certfile` was passed would prove the
argument, not the encryption.

There is deliberately no reverse proxy, no UNIX socket and no
`X-Forwarded-*` trust anywhere in this path. Session B's coordination note is
explicit about why: the request boundary treats a peer with no IP address as
local, so putting anything in front of the app converts every caller into a
"local" one and silently neutralises the boundary. TLS terminates in this
process or it does not terminate anywhere this code can vouch for.
"""

from __future__ import annotations

import http.client
import json
import os
import pathlib
import socket
import ssl
import subprocess
import sys
import time

import pytest

from tests.integration.test_always_on_service import (
    REPO_ROOT,
    STARTUP_TIMEOUT,
    _free_port,
)

pytestmark = [pytest.mark.integration]


def _make_self_signed(tmp_path) -> tuple[str, str]:
    """A throwaway certificate for localhost. Test material, never shipped."""
    pytest.importorskip("cryptography")
    import datetime as _dt

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = _dt.datetime.now(_dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _dt.timedelta(minutes=5))
        .not_valid_after(now + _dt.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ),
    )
    return str(cert_path), str(key_path)


class TlsService:
    """A real `serve` process with TLS material and enforced authentication."""

    def __init__(self, tmp_path):
        self.port = _free_port()
        self.cert, self.key = _make_self_signed(tmp_path)
        self.proc: subprocess.Popen | None = None

        # An exposed deployment must also name the personal Bartholomew it
        # serves, and its database and keyring namespace must be that user's
        # (platform/exposure.require_bound_runtime_user). Provisioning a real
        # account here rather than pointing BARTH_DB_PATH at an arbitrary file
        # is what makes this fixture a truthful exposed deployment: it now
        # exercises TLS, enforced authentication and the runtime binding
        # together, which is the only combination `serve` will actually start.
        self.platform_db = tmp_path / "platform.db"
        self.data_root = tmp_path / "data"
        provision_env = dict(os.environ)
        provision_env.update(
            {
                "BARTH_PLATFORM_DB_PATH": str(self.platform_db),
                "BARTH_DATA_ROOT": str(self.data_root),
                "PYTHONPATH": str(REPO_ROOT),
            },
        )
        provisioned = subprocess.run(  # noqa: S603
            [
                sys.executable,
                "-c",
                "from bartholomew.platform.store import init_platform_schema;"
                "from bartholomew.platform import accounts;"
                "from bartholomew.platform.runtime_registry import "
                "runtime_handle_for_user_id;"
                "init_platform_schema();"
                "uid = accounts.create_account('tls-fixture', 'alpha-participant-password');"
                "h = runtime_handle_for_user_id(uid);"
                "print(uid); print(h.db_path); print(h.keyring_service)",
            ],
            cwd=str(REPO_ROOT),
            env=provision_env,
            capture_output=True,
            text=True,
            check=True,
        )
        user_id, user_db, keyring_service = provisioned.stdout.strip().splitlines()[-3:]
        self.db_path = pathlib.Path(user_db)

        self.env = dict(os.environ)
        self.env.update(
            {
                "BARTH_PLATFORM_DB_PATH": str(self.platform_db),
                "BARTH_DATA_ROOT": str(self.data_root),
                "BARTH_RUNTIME_USER_ID": user_id,
                "BARTH_DB_PATH": user_db,
                "BARTHO_MEMORY_KEYRING_SERVICE": keyring_service,
                "BARTH_API_PORT": str(self.port),
                "PYTHONPATH": str(REPO_ROOT),
                "PYTHONUNBUFFERED": "1",
                # A non-loopback bind is what forces authentication and TLS on.
                # Bound to loopback in fact, so the test never listens on a
                # routable interface -- this exercises the *policy*, not an
                # exposure.
                "BARTH_API_ALLOW_NON_LOOPBACK": "1",
                "BARTH_API_HOST": "127.0.0.1",
                "BARTH_API_TLS_CERTFILE": self.cert,
                "BARTH_API_TLS_KEYFILE": self.key,
            },
        )

    def ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context(cafile=self.cert)
        ctx.check_hostname = True
        return ctx

    def start(self) -> TlsService:
        self.proc = subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "bartholomew", "serve"],
            cwd=str(REPO_ROOT),
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.monotonic() + STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError(f"serve exited early: {self.proc.returncode}")
            try:
                status, _ = self.get("/healthz")
                if status == 200:
                    return self
            except Exception:
                pass
            time.sleep(0.5)
        raise AssertionError("TLS service never became reachable")

    def get(self, path: str, timeout: float = 5.0):
        conn = http.client.HTTPSConnection(
            "localhost",
            self.port,
            context=self.ssl_context(),
            timeout=timeout,
        )
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            body = resp.read().decode()
            try:
                return resp.status, json.loads(body)
            except ValueError:
                return resp.status, {"raw": body}
        finally:
            conn.close()

    def kill(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=10)


@pytest.fixture
def tls_service(tmp_path):
    svc = TlsService(tmp_path)
    try:
        yield svc.start()
    finally:
        svc.kill()


def test_https_succeeds_against_the_real_socket(tls_service):
    """A real TLS handshake, verified against the certificate it was given."""
    status, body = tls_service.get("/healthz")
    assert status == 200
    assert body["status"] == "ok"


def test_plaintext_http_does_not_work_against_a_tls_listener(tls_service):
    """The other half of the claim: cleartext must not be served.

    Without this, "TLS is on" is satisfied by a listener that happily answers
    both -- which is not encryption, it is an option.
    """
    conn = http.client.HTTPConnection("127.0.0.1", tls_service.port, timeout=5.0)
    with pytest.raises((http.client.HTTPException, ConnectionError, socket.timeout, OSError)):
        conn.request("GET", "/healthz")
        resp = conn.getresponse()
        # If a response somehow came back, it must not be a working plaintext
        # answer from the API.
        assert resp.status >= 400, "the TLS listener served a plaintext request"
    conn.close()


def test_the_startup_line_reports_https_only_when_tls_is_really_on(tls_service):
    """A startup banner claiming https over a plaintext socket is how a
    deployment gets trusted when it should not be."""
    # The process is up and serving TLS (proven above); its own startup line
    # must agree.
    assert tls_service.proc.poll() is None
    # Read what it printed without blocking on the still-open stream.
    import fcntl

    fd = tls_service.proc.stdout.fileno()
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    try:
        output = tls_service.proc.stdout.read() or ""
    except Exception:
        output = ""
    assert "https://" in output, f"startup line did not report https: {output[:500]}"
    assert "TLS on" in output


def test_authentication_is_enforced_whenever_tls_is(tls_service):
    """The two are forced on together and neither can be turned off alone."""
    status, body = tls_service.get("/api/health")
    assert status == 200
    # A capability-bearing route is refused without credentials.
    status, _ = tls_service.get("/api/memory")
    assert status in (401, 403), "an authenticated deployment served memory unauthenticated"


def test_a_non_loopback_bind_without_tls_refuses_to_start(tmp_path):
    """Fail at launch, not on the first request -- by then it is listening."""
    from bartholomew.runtime.serve import EXIT_BAD_CONFIG

    env = dict(os.environ)
    env.update(
        {
            "BARTH_DB_PATH": str(tmp_path / "notls.db"),
            "BARTH_API_PORT": str(_free_port()),
            "PYTHONPATH": str(REPO_ROOT),
            "BARTH_API_ALLOW_NON_LOOPBACK": "1",
            "BARTH_API_HOST": "127.0.0.1",
        },
    )
    for var in ("BARTH_API_TLS_CERTFILE", "BARTH_API_TLS_KEYFILE"):
        env.pop(var, None)

    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "bartholomew", "serve"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert proc.returncode == EXIT_BAD_CONFIG
    assert "requires TLS" in proc.stderr
    assert not (tmp_path / "notls.db.lock").exists(), "it started far enough to take the lock"
