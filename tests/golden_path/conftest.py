"""The Golden Path harness: what is real here, and what is not.

**Real, in every scenario below:** a `bartholomew serve` process started as a
service manager starts it, real sockets, real SQLite, the real `GovernanceStore`
and the Parking Brake, the real route policy and admission gate, the real action
envelope with its ordered checks and six-fact approval binding, the real device
action channel, the real memory store and consent gate, and the real candidate
lesson loop with its candidate-bound authorization. **No governance authority is
mocked anywhere in this package**, which is W03-E acceptance criterion 3.

**Not real, and stated here rather than left to be discovered:** there is no
Windows. `Device` below speaks the real device action channel over real HTTP --
it leases exactly as the companion leases and reports exactly as the companion
reports -- but the Win32 handler that would move a window does not run, so what
it reports is what a test chose to report. That is the correct boundary for this
tier: the envelope, the approval binding, the brake and the verdict vocabulary
are all exercised for real, and the live desktop run of paths 1 and 4 is a W03-F
real-world-test acceptance target (`W03_E_CONTRACT.md`, Testing).

The operator surface is driven as a **subprocess running the installed console
script**, not by importing its functions. Acceptance criterion 1 is about what a
person at a keyboard can do, and a test that called the module's functions would
prove something weaker than the criterion asks for.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.integration.test_always_on_service import REPO_ROOT, ServeProcess, _get, _post

DEVICE = "golden-path-pc"
TENANT_ANY = "local"
ACTION_TOKEN = "golden-path-device-token"

#: Both gates of the double-gated test resolver. Neither variable exists in any
#: deployed configuration -- see `device_action_auth`. The channel being open at
#: all in these tests is therefore itself an explicit, test-only act.
ACTION_RESOLVER_ENV = {
    "BARTH_ACTION_ALLOW_TEST_RESOLVER": "1",
    "BARTH_ACTION_TEST_TOKEN": ACTION_TOKEN,
    "BARTH_ACTION_TEST_DEVICE_ID": DEVICE,
}
DEVICE_AUTH = {"X-Bartholomew-Device-Token": ACTION_TOKEN}

SPOTIFY = "C:\\Users\\t\\AppData\\Roaming\\Spotify\\Spotify.exe"
NOTEPAD = "C:\\Windows\\System32\\notepad.exe"
DOCUMENTS = "C:\\Users\\t\\Documents"
BARTHOLOMEW_FOLDER = "C:\\Users\\t\\Documents\\Bartholomew"


def enrolment_file(tmp_path: Path, **overrides) -> Path:
    """A real enrolment record, written as an operator would write one.

    The capability list is deliberately the set W03-C actually ships. Nothing in
    this suite adds a capability to make a scenario succeed: Golden Path 3 runs
    into the absence of a file-move capability and records it as a named stop,
    which is `W03_E_CONTRACT.md`'s prescribed behaviour and not a gap to paper
    over.
    """
    record = {
        "device_id": DEVICE,
        "tenant_id": TENANT_ANY,
        "platform": "windows",
        "enrolled": True,
        "capabilities": [
            "windows.launch_app",
            "windows.focus_window",
            "windows.manage_window",
            "windows.open_path",
            "windows.open_url",
            "windows.type_text",
        ],
        "applications": {"spotify": SPOTIFY, "notepad": NOTEPAD},
        "url_domains": ["example.com"],
        "filesystem_roots": [DOCUMENTS],
        "trusted_autonomy": [],
    }
    record.update(overrides)
    path = tmp_path / "golden_path_enrolment.json"
    path.write_text(json.dumps({"devices": [record]}), encoding="utf-8")
    return path


@pytest.fixture
def service(tmp_path):
    """A running Bartholomew with the action channel reachable by a test device."""
    svc = ServeProcess(
        tmp_path / "golden_path.db",
        env_extra={
            **ACTION_RESOLVER_ENV,
            "BARTH_ACTION_DEVICE_ENROLMENT": str(enrolment_file(tmp_path)),
        },
    )
    try:
        yield svc.start()
    finally:
        svc.kill()


# ---------------------------------------------------------------------------
# The operator console, driven the way a person drives it
# ---------------------------------------------------------------------------


@dataclass
class ConsoleResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        return self.stdout + self.stderr

    def ok(self) -> ConsoleResult:
        assert self.returncode == 0, f"{' '.join(self.argv)} failed:\n{self.output}"
        return self

    def json(self) -> object:
        """The first JSON document in stdout, for the commands that emit one."""
        start = self.stdout.find("{")
        assert start >= 0, f"no JSON in output of {' '.join(self.argv)}:\n{self.output}"
        return json.loads(self.stdout[start:])


class Console:
    """`bartholomew operator ...`, run as a subprocess against a live server.

    Deliberately not a `CliRunner`: acceptance criterion 1 says a non-developer
    drives this surface, and in-process invocation would skip the console script
    entry point, the argument parsing and the real HTTP client -- the three
    things a person actually meets.
    """

    def __init__(self, port: int):
        self.base_url = f"http://127.0.0.1:{port}"

    def run(self, *args: str, base_url: bool = True, timeout: float = 90.0) -> ConsoleResult:
        """Run one console command. `base_url=False` for the commands that read
        the kernel database directly and therefore take no server address --
        `consent pending` is the only one, and it is that way because the ask's
        nonce lives only in that file."""
        argv = [
            sys.executable,
            "-m",
            "bartholomew",
            "operator",
            *args,
            *(["--base-url", self.base_url] if base_url else []),
        ]
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            argv,
            check=False,
            cwd=str(REPO_ROOT),
            env={**os.environ, "COLUMNS": "200"},
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return ConsoleResult(argv, proc.returncode, proc.stdout, proc.stderr)


@pytest.fixture
def console(service) -> Console:
    return Console(service.port)


# ---------------------------------------------------------------------------
# The device, speaking the real channel
# ---------------------------------------------------------------------------


class Device:
    """The enrolled machine's half of the loop, over the real device channel.

    It leases through `POST /api/device-actions/lease` and reports through
    `POST /api/device-actions/{id}/result`, both separately authenticated by the
    device credential. What it *does* between the two is nothing: there is no
    Windows here. So `report()` takes the status explicitly, and every scenario
    that uses it says in its own docstring which half it is proving.
    """

    def __init__(self, port: int):
        self.port = port

    def lease(self, limit: int = 5) -> tuple[int, dict]:
        return _post(
            self.port,
            "/api/device-actions/lease",
            {"device_id": DEVICE, "limit": limit},
            headers=DEVICE_AUTH,
        )

    def report(
        self,
        action_id: str,
        *,
        status: str,
        detail: str,
        error_category: str | None = None,
        evidence: dict | None = None,
    ) -> tuple[int, dict]:
        from datetime import datetime, timezone

        return _post(
            self.port,
            f"/api/device-actions/{action_id}/result",
            {
                "device_id": DEVICE,
                "status": status,
                "error_category": error_category,
                "detail": detail,
                "evidence": evidence or {},
                "observed_at": datetime.now(timezone.utc).isoformat(),
            },
            headers=DEVICE_AUTH,
        )


@pytest.fixture
def device(service) -> Device:
    return Device(service.port)


# ---------------------------------------------------------------------------
# Small helpers every scenario shares
# ---------------------------------------------------------------------------


def request_action(
    port: int,
    *,
    capability: str,
    parameters: dict,
    **overrides,
) -> tuple[int, dict]:
    """Propose one action through the real envelope.

    Used by the scenarios whose interpret leg (W03-B) is not in this tree: the
    envelope half is identical either way, and the scenario names the leg it had
    to enter at.
    """
    body = {
        "device_id": DEVICE,
        "capability": capability,
        "capability_version": 1,
        "parameters": parameters,
    }
    body.update(overrides)
    return _post(port, "/api/actions", body)


def read_action(port: int, action_id: str) -> dict:
    status, payload = _get(port, f"/api/actions/{action_id}")
    assert status == 200, payload
    return payload


#: The named stop for a tree that carries the executive but whose `app.py` has
#: not yet registered `routes/operator.py`. Registration is W03-F's by the
#: manifest, so on the W03-E branch *and* on a bare composition of the frozen
#: heads the routes are absent, and a scenario must say which of the two things
#: it ran into: no executive, or no door to it.
OPERATOR_ROUTES_UNSERVED = (
    "NAMED STOP -- this build does not serve /api/operator/*: registering "
    "`routes/operator.py` in `app.py` is W03-F's step (W03_MANIFEST.yaml, "
    "W03_E_CONTRACT.md Ownership). The interpret leg is entered at the envelope "
    "instead, and `bartholomew operator task run` reports the absence in words."
)


def require_operator_routes(port: int) -> None:
    """Skip with the named stop when this server does not serve the router.

    Lives here rather than in a scenario so `test_named_stops.py`'s rule -- a
    scenario never calls `pytest.skip` itself -- holds for this stop as well.
    """
    if not operator_routes_served(port):
        pytest.skip(OPERATOR_ROUTES_UNSERVED)


def operator_routes_served(port: int) -> bool:
    """Whether this server registers the operator router.

    Probed against the running server rather than inferred from the tree: the
    executive package can be present (a composed head) while the router is not
    registered (W03-F has not run), and only the server knows which.
    """
    try:
        status, _ = _get(port, "/api/operator/overview")
    except urllib.error.HTTPError as e:
        return e.code != 404
    except Exception:
        return False
    return status == 200


def arm_channel(port: int) -> dict:
    """Make sure the channel is armed for the test device, and prove it.

    The arm route requires the *companion* credential, which no keyring on this
    runner holds; the device action token is a different credential and is
    refused here (correctly -- opening actuation is not the same act as leasing
    an action). The channel these tests lease through is therefore the one the
    double-gated test resolver arms at startup (`app.py`), and this helper
    asserts that it is open rather than assuming it. A scenario that disarmed it
    cannot silently pass on a closed channel.
    """
    _post(
        port,
        "/api/actions/channel/arm",
        {"device_id": DEVICE, "reason": "golden path"},
        headers=DEVICE_AUTH,
    )
    status, payload = _get(port, "/api/actions/channel")
    assert status == 200 and payload["channel"]["armed"] is True, payload
    return payload["channel"]


def engage_brake(port: int, *scopes: str, reason: str = "golden path halt") -> tuple[int, dict]:
    return _post(
        port,
        "/api/governance/brake/engage",
        {"scopes": list(scopes) or ["global"], "reason": reason},
    )


def disengage_brake(port: int) -> tuple[int, dict]:
    return _post(port, "/api/governance/brake/disengage", {"reason": "golden path resume"})


__all__ = [
    "ACTION_TOKEN",
    "BARTHOLOMEW_FOLDER",
    "Console",
    "ConsoleResult",
    "DEVICE",
    "DEVICE_AUTH",
    "DOCUMENTS",
    "Device",
    "NOTEPAD",
    "SPOTIFY",
    "arm_channel",
    "disengage_brake",
    "engage_brake",
    "enrolment_file",
    "OPERATOR_ROUTES_UNSERVED",
    "operator_routes_served",
    "require_operator_routes",
    "read_action",
    "request_action",
    "urllib",
]
