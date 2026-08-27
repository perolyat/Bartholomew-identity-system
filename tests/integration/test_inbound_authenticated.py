"""Inbound capture under enforced authentication (Session D, reqs 2-4).

Everything here runs against a real `bartholomew serve` process with
`BARTH_AUTH_MODE=enforced`, real accounts provisioned through the real CLI,
real sessions obtained through the real login endpoint, and a real runtime
binding. The properties being proven are exactly the ones that would be
catastrophic to get wrong, so none of the boundary is mocked.

The two gates on this route are independent and neither substitutes for the
other:

* **Who is asking** -- the control plane's verified principal, plus this
  process's runtime binding. Decides *whose* Bartholomew an event lands in.
* **Who sent it** -- the inbound source resolver. Provenance only. Verifying
  that a webhook genuinely came from Acme says nothing about whose runtime it
  belongs in, and a source that could name its target runtime would be a
  cross-user write primitive dressed up as provenance.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

from tests.integration.test_always_on_service import (
    REPO_ROOT,
    STARTUP_TIMEOUT,
    TEST_TOKEN,
    _free_port,
)

pytestmark = [pytest.mark.integration]

SOURCE_HEADER = {"X-Bartholomew-Test-Token": TEST_TOKEN}


def _provision(platform_db, username, admin=False):
    """Create a real account through the real operator CLI."""
    env = dict(os.environ)
    env.update({"BARTH_PLATFORM_DB_PATH": str(platform_db), "PYTHONPATH": str(REPO_ROOT)})
    cmd = [sys.executable, "-m", "bartholomew", "accounts", "create", username]
    if admin:
        cmd.append("--admin")
    proc = subprocess.run(  # noqa: S603
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, f"account provisioning failed:\n{proc.stdout}{proc.stderr}"
    user_id = password = None
    for raw_line in proc.stdout.splitlines():
        line = raw_line.strip()
        if line.startswith("user_id:"):
            user_id = line.split(":", 1)[1].strip()
        elif line.startswith("password:"):
            password = line.split(":", 1)[1].strip()
    assert user_id and password, f"could not read credentials from:\n{proc.stdout}"
    return user_id, password


class AuthedService:
    """A real serve process: auth enforced, bound to one user's runtime."""

    def __init__(self, tmp_path, bound_user_id, platform_db, db_name="authed.db"):
        self.port = _free_port()
        self.db_path = tmp_path / db_name
        self.proc = None
        self.env = dict(os.environ)
        self.env.update(
            {
                "BARTH_DB_PATH": str(self.db_path),
                "BARTH_API_PORT": str(self.port),
                "BARTH_PLATFORM_DB_PATH": str(platform_db),
                "PYTHONPATH": str(REPO_ROOT),
                "PYTHONUNBUFFERED": "1",
                "BARTH_AUTH_MODE": "enforced",
                "BARTH_RUNTIME_USER_ID": bound_user_id,
                # The double-gated test-only source resolver. Provenance only:
                # it can say an event came from `test-source`, and nothing else.
                "BARTH_INBOUND_ALLOW_TEST_RESOLVER": "1",
                "BARTH_INBOUND_TEST_TOKEN": TEST_TOKEN,
            },
        )

    def start(self):
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
                status, body = self.request("GET", "/api/health")
                if status == 200 and body["components"]["runtime"]["state"] == "running":
                    return self
            except Exception:
                pass
            time.sleep(0.5)
        raise AssertionError("authenticated service never became healthy")

    def request(self, method, path, body=None, headers=None, cookie=None):
        data = json.dumps(body).encode() if body is not None else None
        hdrs = {"Content-Type": "application/json", **(headers or {})}
        if cookie:
            hdrs["Cookie"] = cookie
        req = urllib.request.Request(  # noqa: S310 - fixed localhost URL
            f"http://127.0.0.1:{self.port}{path}",
            data=data,
            headers=hdrs,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310
                return r.status, json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, {"detail": raw}

    def login(self, username, password) -> str:
        data = json.dumps({"username": username, "password": password}).encode()
        req = urllib.request.Request(  # noqa: S310
            f"http://127.0.0.1:{self.port}/api/auth/login",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310
            set_cookie = r.headers.get("set-cookie", "")
        assert set_cookie, "login returned no session cookie"
        return set_cookie.split(";")[0]

    def rows(self, sql="SELECT COUNT(*) FROM inbound_events"):
        with sqlite3.connect(str(self.db_path)) as conn:
            try:
                return conn.execute(sql).fetchone()[0]
            except sqlite3.OperationalError:
                return 0

    def kill(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=10)


@pytest.fixture
def platform_db(tmp_path):
    return tmp_path / "platform.db"


@pytest.fixture
def owner(platform_db):
    return _provision(platform_db, "owner-user")


@pytest.fixture
def other(platform_db):
    return _provision(platform_db, "other-user")


@pytest.fixture
def service(tmp_path, platform_db, owner):
    user_id, _ = owner
    svc = AuthedService(tmp_path, user_id, platform_db)
    try:
        yield svc.start()
    finally:
        svc.kill()


def _event(event_id, **overrides):
    body = {
        "source_id": "test-source",
        "event_id": event_id,
        "event_type": "generic.event",
        "payload": {"hello": "world"},
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# Requirement 3: enforced authentication on the inbound routes
# ---------------------------------------------------------------------------


def test_a_verified_source_without_a_principal_captures_nothing(service):
    """The whole point of the two gates: provenance is not authorisation.

    The source token here is genuinely valid. It still must not be able to
    write into anyone's runtime on its own.
    """
    status, _ = service.request(
        "POST",
        "/api/inbound/events",
        _event("e-nosession"),
        SOURCE_HEADER,
    )

    assert status in (401, 403), f"an unauthenticated capture returned {status}"
    assert service.rows() == 0


def test_a_valid_principal_plus_a_verified_source_succeeds(service, owner):
    username_password = ("owner-user", owner[1])
    cookie = service.login(*username_password)

    status, body = service.request(
        "POST",
        "/api/inbound/events",
        _event("e-ok"),
        SOURCE_HEADER,
        cookie=cookie,
    )

    assert status == 202, f"authenticated capture failed: {body}"
    assert body["captured"] is True
    assert service.rows() == 1


def test_a_principal_without_a_verified_source_captures_nothing(service, owner):
    """The other direction: being logged in is not being a known sender."""
    cookie = service.login("owner-user", owner[1])

    status, _ = service.request(
        "POST",
        "/api/inbound/events",
        _event("e-nosource"),
        cookie=cookie,
    )

    assert status == 401
    assert service.rows() == 0


def test_a_different_user_is_refused_by_the_runtime_binding(service, other):
    """Cross-user: this process serves one person and must refuse everyone else.

    Handing user B the runtime of user A is the exact disclosure the isolation
    model exists to prevent, and "the kernel that happens to be loaded" is
    never the right answer to "whose Bartholomew is this?".
    """
    cookie = service.login("other-user", other[1])

    status, _ = service.request(
        "POST",
        "/api/inbound/events",
        _event("e-crossuser"),
        SOURCE_HEADER,
        cookie=cookie,
    )

    assert status == 403, "a foreign authenticated identity reached inbound capture"
    assert service.rows() == 0


def test_a_platform_admin_cannot_capture_into_a_personal_runtime(
    tmp_path,
    platform_db,
    owner,
):
    """An administrator has no personal runtime, so this is not their surface."""
    admin_id, admin_password = _provision(platform_db, "admin-user", admin=True)
    svc = AuthedService(tmp_path, owner[0], platform_db, db_name="admin_probe.db")
    try:
        svc.start()
        cookie = svc.login("admin-user", admin_password)
        status, _ = svc.request(
            "POST",
            "/api/inbound/events",
            _event("e-admin"),
            SOURCE_HEADER,
            cookie=cookie,
        )
        assert status == 403
        assert svc.rows() == 0
    finally:
        svc.kill()


def test_reading_captured_events_also_requires_authentication(service, owner):
    status, _ = service.request("GET", "/api/inbound/events")
    assert status in (401, 403)

    cookie = service.login("owner-user", owner[1])
    status, _ = service.request("GET", "/api/inbound/events", cookie=cookie)
    assert status == 200


# ---------------------------------------------------------------------------
# Requirement 2: a source can never choose the runtime
# ---------------------------------------------------------------------------


def test_the_stored_runtime_comes_from_the_principal_not_the_source(service, owner):
    """Provenance and target are different questions with different authorities."""
    user_id, password = owner
    cookie = service.login("owner-user", password)

    service.request(
        "POST",
        "/api/inbound/events",
        _event("e-runtime"),
        SOURCE_HEADER,
        cookie=cookie,
    )

    with sqlite3.connect(str(service.db_path)) as conn:
        runtime_id, verified_by = conn.execute(
            "SELECT runtime_id, verified_by FROM inbound_events WHERE event_id = 'e-runtime'",
        ).fetchone()

    assert runtime_id == user_id, "the stored runtime did not come from the principal"
    # The source is still recorded -- as provenance, which is all it is.
    assert verified_by == "test-resolver"


def test_a_source_cannot_choose_its_target_runtime(tmp_path, platform_db, owner, other):
    """Runtime spoofing by the sender is ignored, not honoured.

    The test resolver deliberately carries a `runtime_id` attribute even
    though the contract has none, so this exercises a real spoofing attempt
    rather than one no code path can express.
    """
    user_id, password = owner
    victim_id, _ = other

    svc = AuthedService(tmp_path, user_id, platform_db, db_name="spoof.db")
    # The source will claim to belong to the *other* user's runtime.
    svc.env["BARTH_INBOUND_TEST_CLAIMED_RUNTIME_ID"] = victim_id
    try:
        svc.start()
        cookie = svc.login("owner-user", password)
        status, _ = svc.request(
            "POST",
            "/api/inbound/events",
            _event("e-spoof"),
            SOURCE_HEADER,
            cookie=cookie,
        )
        assert status == 202

        with sqlite3.connect(str(svc.db_path)) as conn:
            runtime_id = conn.execute(
                "SELECT runtime_id FROM inbound_events WHERE event_id = 'e-spoof'",
            ).fetchone()[0]

        assert runtime_id == user_id, "a source-claimed runtime was honoured"
        assert runtime_id != victim_id
    finally:
        svc.kill()


# ---------------------------------------------------------------------------
# Requirement 4: the brake rule survives the combined branches
# ---------------------------------------------------------------------------


def _governed_mutation_counts(svc) -> tuple[int, int, int]:
    """Inbound rows, this surface's reflections, and nudges."""
    return (
        svc.rows(),
        svc.rows(
            "SELECT COUNT(*) FROM reflections WHERE kind = 'action_reflection' "
            'AND meta LIKE \'%"surface": "inbound"%\'',
        ),
        svc.rows("SELECT COUNT(*) FROM nudges"),
    )


def _brake(svc, *args, platform=False):
    env = dict(svc.env)
    cmd = [sys.executable, "-m", "bartholomew"]
    if platform:
        cmd += ["platform-brake", *args]
        if args and args[0] in ("on", "off"):
            # The platform tier records who halted it; there is no anonymous
            # administrative action.
            cmd += ["--actor", "integration-test"]
    else:
        cmd += ["brake", *args, "--db", str(svc.db_path)]
    return subprocess.run(  # noqa: S603
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_the_personal_brake_refuses_capture_and_mutates_nothing(service, owner):
    """ "Inspect, but do not mutate", under authentication as well.

    Recording a "received and refused" row would itself be a governed-state
    mutation performed while the user has halted mutation -- the exact side
    door a brake exists to close.
    """
    cookie = service.login("owner-user", owner[1])
    service.request(
        "POST",
        "/api/inbound/events",
        _event("e-pre-brake"),
        SOURCE_HEADER,
        cookie=cookie,
    )
    before = _governed_mutation_counts(service)

    result = _brake(service, "on")
    assert result.returncode == 0, result.stderr

    status, body = service.request(
        "POST",
        "/api/inbound/events",
        _event("e-braked"),
        SOURCE_HEADER,
        cookie=cookie,
    )

    assert status == 503, f"a braked capture returned {status}: {body}"
    assert "retry" in json.dumps(body).lower()
    assert (
        _governed_mutation_counts(service) == before
    ), "a braked inbound request mutated governed state"


def test_the_platform_halt_also_refuses_capture_and_mutates_nothing(service, owner):
    """An administrative halt must reach this surface too.

    A capture path that only consulted the personal brake would let inbound
    events keep landing through a platform-wide stop.
    """
    cookie = service.login("owner-user", owner[1])
    before = _governed_mutation_counts(service)

    result = _brake(service, "on", platform=True)
    assert result.returncode == 0, f"platform halt failed:\n{result.stdout}{result.stderr}"
    try:

        status, _ = service.request(
            "POST",
            "/api/inbound/events",
            _event("e-platform-halt"),
            SOURCE_HEADER,
            cookie=cookie,
        )

        assert status in (503, 403), f"a platform halt did not stop inbound capture ({status})"
        assert (
            _governed_mutation_counts(service) == before
        ), "an inbound request mutated governed state during a platform halt"
    finally:
        # Released even on failure: the platform store is shared across this
        # module's services, so a halt left engaged would fail every test
        # that ran afterwards for the wrong reason.
        _brake(service, "off", platform=True)


def test_capture_resumes_once_the_brake_is_released(service, owner):
    cookie = service.login("owner-user", owner[1])
    _brake(service, "on")
    status, _ = service.request(
        "POST",
        "/api/inbound/events",
        _event("e-resume"),
        SOURCE_HEADER,
        cookie=cookie,
    )
    assert status == 503

    assert _brake(service, "off").returncode == 0

    status, body = service.request(
        "POST",
        "/api/inbound/events",
        _event("e-resume"),
        SOURCE_HEADER,
        cookie=cookie,
    )
    assert status == 202, body
    assert body["captured"] is True
