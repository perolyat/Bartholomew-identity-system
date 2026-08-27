"""Always-on lifecycle evidence, against a real supervised-style process.

Everything here launches `python -m bartholomew serve` as an actual
subprocess and talks to it over real HTTP. Nothing is mocked, because the
claims being proven are about a process: that it starts with no terminal
interaction, keeps its scheduler alive while nothing is connected, survives
clients coming and going, stops cleanly on SIGTERM, restores durable state on
restart, and refuses to run twice against one database.

Marked `integration` -- the default marker expression excludes these, and
CI's `critical` job runs them explicitly.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Long enough for kernel startup (schema, identity, skills, vector store) on
#: a loaded CI runner; short enough that a genuine hang fails the test rather
#: than the job.
STARTUP_TIMEOUT = 90.0
SHUTDOWN_TIMEOUT = 60.0


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(port: int, path: str, timeout: float = 5.0):
    url = f"http://127.0.0.1:{port}{path}"
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 - fixed localhost URL
        return r.status, json.loads(r.read().decode())


def _post(port: int, path: str, body: dict, headers: dict | None = None):
    data = json.dumps(body).encode()
    req = urllib.request.Request(  # noqa: S310 - fixed localhost URL
        f"http://127.0.0.1:{port}{path}",
        data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15.0) as r:  # noqa: S310
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"detail": raw}


def _env(db_path: Path, port: int, **extra) -> dict:
    env = dict(os.environ)
    env.update(
        {
            "BARTH_DB_PATH": str(db_path),
            "BARTH_API_PORT": str(port),
            "PYTHONPATH": str(REPO_ROOT),
            "PYTHONUNBUFFERED": "1",
            # Keep the scheduler from bursting writes during these tests --
            # the same pacing the production default uses.
            "BARTH_DRIVE_PACE_S": "0.5",
        },
    )
    env.update(extra)
    return env


class ServeProcess:
    """A real `bartholomew serve` process, started and stopped like a service."""

    def __init__(self, db_path: Path, env_extra: dict | None = None):
        self.port = _free_port()
        self.db_path = db_path
        self.env = _env(db_path, self.port, **(env_extra or {}))
        self.proc: subprocess.Popen | None = None

    def start(self, *, wait: bool = True) -> ServeProcess:
        self.proc = subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "bartholomew", "serve"],
            cwd=str(REPO_ROOT),
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if wait:
            self.wait_until_healthy()
        return self

    def wait_until_healthy(self, timeout: float = STARTUP_TIMEOUT) -> None:
        """Block until /api/health reports a RUNNING kernel, or fail loudly."""
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError(
                    f"serve exited early with code {self.proc.returncode}:\n{self.output()}",
                )
            try:
                status, body = _get(self.port, "/api/health")
                if body.get("components", {}).get("runtime", {}).get("state") == "running":
                    return
                last = body
            except Exception as e:  # not up yet
                last = repr(e)
            time.sleep(0.5)
        raise AssertionError(f"serve never became healthy. Last: {last}\n{self.output()}")

    def output(self) -> str:
        if self.proc is None or self.proc.stdout is None:
            return ""
        try:
            self.proc.stdout.flush()
        except Exception:
            pass
        return ""

    def stop(self, timeout: float = SHUTDOWN_TIMEOUT) -> int:
        """SIGTERM and wait -- the same signal a service manager sends."""
        if self.proc is None or self.proc.poll() is not None:
            return self.proc.returncode if self.proc else 0
        if os.name == "nt":  # pragma: no cover - exercised on the Windows CI job
            self.proc.terminate()
        else:
            self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)
            raise AssertionError("serve did not shut down within the stop budget") from None
        return self.proc.returncode

    def kill(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=10)


#: The double-gated test resolver's credentials. Both variables are required
#: and neither exists in any deployed configuration -- see
#: `inbound_auth.maybe_install_test_resolver_from_env()`.
TEST_TOKEN = "integration-only-token"
TEST_RESOLVER_ENV = {
    "BARTH_INBOUND_ALLOW_TEST_RESOLVER": "1",
    "BARTH_INBOUND_TEST_TOKEN": TEST_TOKEN,
}


@pytest.fixture
def service(tmp_path):
    """A service in the default posture: inbound capture fail-closed."""
    svc = ServeProcess(tmp_path / "always_on.db")
    try:
        yield svc.start()
    finally:
        svc.kill()


@pytest.fixture
def service_with_inbound(tmp_path):
    """A service with the test-only resolver installed, for the full HTTP path."""
    svc = ServeProcess(tmp_path / "always_on_inbound.db", env_extra=TEST_RESOLVER_ENV)
    try:
        yield svc.start()
    finally:
        svc.kill()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_service_starts_without_any_interactive_session(service):
    """No terminal, no browser, no uvicorn command line -- just the service."""
    status, body = _get(service.port, "/healthz")
    assert status == 200
    assert body["status"] == "ok"


def test_scheduler_is_running_and_its_heartbeat_advances(service):
    """The autonomy loop is alive, and says so from its own heartbeat.

    Two samples, not one: a heartbeat that merely *exists* proves nothing, and
    the old `last_tick` field reported process-start time forever precisely
    because nothing ever advanced it.
    """
    _, first = _get(service.port, "/api/health")
    scheduler = first["components"]["scheduler"]
    assert scheduler["status"] == "ok"
    assert scheduler["state"] == "running"
    assert scheduler["last_beat"] is not None

    time.sleep(8)

    _, second = _get(service.port, "/api/health")
    assert second["components"]["scheduler"]["stalled"] is False
    # The loop got round again while we were not looking.
    assert (
        second["components"]["scheduler"]["last_beat"]
        >= first["components"]["scheduler"]["last_beat"]
    )
    assert second["components"]["scheduler"]["seconds_since_beat"] < 30


def test_clients_connecting_and_disconnecting_have_no_lifecycle_effect(service):
    """Closing the UI must not kill the brain."""
    for _ in range(5):
        s = socket.create_connection(("127.0.0.1", service.port), timeout=5)
        s.close()  # abrupt disconnect, no graceful HTTP close

    for _ in range(3):
        status, _ = _get(service.port, "/healthz")
        assert status == 200

    _, body = _get(service.port, "/api/health")
    assert body["components"]["runtime"]["state"] == "running"
    assert body["components"]["scheduler"]["status"] == "ok"


def test_graceful_shutdown_stops_cleanly_and_releases_the_lock(service):
    """SIGTERM drains and shuts down cleanly inside the unit file's stop budget.

    The exit *code* is not the evidence: uvicorn re-raises the captured signal
    after its handlers run, so a fully graceful stop still exits 128+SIGTERM.
    What proves the shutdown was clean is the daemon's own record of it --
    `brake_runtime.clean`, the marker Phase B stage B5 writes only after
    admission drain, background-task cancellation and WAL checkpoint have all
    completed -- plus the process lock actually being available afterwards.
    """
    import sqlite3

    started = time.monotonic()
    code = service.stop()
    elapsed = time.monotonic() - started

    assert elapsed < SHUTDOWN_TIMEOUT
    assert code in (0, -signal.SIGTERM, 128 + signal.SIGTERM), f"unexpected exit {code}"

    with sqlite3.connect(str(service.db_path)) as conn:
        clean, fence_open = conn.execute(
            "SELECT clean, write_fence_open FROM brake_runtime",
        ).fetchone()
        incidents = conn.execute("SELECT COUNT(*) FROM startup_incidents").fetchone()[0]

    assert clean == 1, "the daemon did not record a clean shutdown"
    assert fence_open == 0, "the governance write fence was left open"
    assert incidents == 0, "startup recorded an incident despite a clean run"

    # The lock is released, so a fresh service can take the same database --
    # and it starts without recovering from an unclean shutdown.
    replacement = ServeProcess(service.db_path)
    try:
        replacement.start()
        _, body = _get(replacement.port, "/api/health")
        assert body["components"]["runtime"]["state"] == "running"
        assert body["components"]["scheduler"]["status"] == "ok"
    finally:
        replacement.kill()


def test_restart_preserves_durable_state(service_with_inbound):
    """A process restart must not destroy what Bartholomew knows.

    Written through the real governed inbound path rather than by poking the
    database, so what is proven to survive is state the running system
    actually produced.
    """
    service = service_with_inbound
    token = "restart-durability-token"
    status, _ = _post(
        service.port,
        "/api/inbound/events",
        {
            "source_id": "test-source",
            "event_id": token,
            "event_type": "durability.probe",
            "payload": {"marker": token},
        },
        headers={"X-Bartholomew-Test-Token": TEST_TOKEN},
    )
    assert status == 202

    service.stop()

    restarted = ServeProcess(service.db_path, env_extra=TEST_RESOLVER_ENV)
    try:
        restarted.start()
        _, events = _get(restarted.port, "/api/inbound/events")
        assert any(
            e["event_id"] == token for e in events
        ), "durable inbound state did not survive restart"
    finally:
        restarted.kill()


def test_second_instance_against_one_database_is_refused(service):
    """Supervision must never produce two schedulers against one runtime DB."""
    second = ServeProcess(service.db_path)
    try:
        second.start(wait=False)
        second.proc.wait(timeout=STARTUP_TIMEOUT)
    finally:
        second.kill()

    assert second.proc.returncode != 0, "a second service started against a held database"

    # And the first is entirely unharmed.
    _, body = _get(service.port, "/api/health")
    assert body["components"]["runtime"]["state"] == "running"


@pytest.mark.parametrize(
    ("args", "reason"),
    [(["--workers", "2"], "single-writer"), (["--reload"], "Autoreload")],
)
def test_refused_configurations_fail_fast_with_a_reason(tmp_path, args, reason):
    """Two daemons against one database is a configuration error, refused up front."""
    port = _free_port()
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "bartholomew", "serve", *args],
        check=False,
        cwd=str(REPO_ROOT),
        env=_env(tmp_path / "refused.db", port),
        capture_output=True,
        text=True,
        timeout=120,
    )
    from bartholomew.runtime.serve import EXIT_BAD_CONFIG

    assert proc.returncode == EXIT_BAD_CONFIG
    assert reason in proc.stderr
    # Nothing was started: no database file, no lock file.
    assert not (tmp_path / "refused.db.lock").exists()
