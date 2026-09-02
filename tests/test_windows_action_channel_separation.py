"""Observation and actuation are two trust channels, and cannot become one.

Acceptance requirements 12 and 16: a fake command returned through the
observation endpoint cannot reach dispatch, and the existing observation-only
behaviour remains intact.

The claim is structural, and it is made four ways, because any one of them
alone could be worked around by a future edit that looked innocent:

1. **The import graph.** Neither package can reach the other, transitively.
2. **The observation client's shape.** It still has one verb and still returns
   three scalars, so there is nowhere for a command to land.
3. **A hostile server.** A real HTTP server that answers every observation with
   every shape of actuation instruction, run against the real companion, with
   the real dispatcher watched for any call.
4. **Two resolvers, two globals.** Installing the inbound observation resolver
   does not open the action channel, and the action channel refuses with the
   inbound one installed.
"""

from __future__ import annotations

import ast
import http.server
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OBSERVATION = ROOT / "bartholomew" / "companion"
SERVER_ACTUATION = ROOT / "bartholomew" / "actuation"
DEVICE_ACTUATION = ROOT / "bartholomew" / "windows_actuation"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


# --- 1. the import graph ------------------------------------------------------


def test_the_observation_package_cannot_reach_either_actuation_package():
    """Not "does not call" -- cannot name."""
    for path in sorted(OBSERVATION.glob("*.py")):
        for module in _imports(path):
            assert "actuation" not in module, f"{path.name} imports {module}"


def test_the_actuation_packages_cannot_reach_the_observation_package():
    """The reverse direction matters just as much: no shared credential, no
    shared client, no shared state file, nothing to confuse for the other."""
    for package in (SERVER_ACTUATION, DEVICE_ACTUATION):
        for path in sorted(package.glob("*.py")):
            for module in _imports(path):
                assert "bartholomew.companion" not in module, f"{path.name} imports {module}"


def test_the_observation_module_graph_transitively_excludes_actuation():
    """Import the observation package alone, in a clean interpreter, and look.

    A **subprocess**, not `sys.modules` surgery in this one. Evicting modules
    from the running interpreter would leave later tests monkeypatching a
    module object that the code under test no longer holds a reference to --
    which is a nasty, order-dependent failure and, worse, a weaker proof: a
    re-import inside a process that has already imported everything can be
    served from caches this test is supposed to be looking past. A fresh
    interpreter that imports only the observation companion is the actual
    question.
    """
    probe = (
        "import sys, importlib;"
        "importlib.import_module('bartholomew.companion.runner');"
        "importlib.import_module('bartholomew.companion.client');"
        "importlib.import_module('bartholomew.companion.probes');"
        "importlib.import_module('bartholomew.companion.envelope');"
        "print(repr(sorted(m for m in sys.modules if 'actuation' in m)))"
    )
    result = subprocess.run(  # noqa: S603 - fixed argv, this interpreter
        [sys.executable, "-c", probe],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert (
        result.returncode == 0
    ), f"importing the observation companion alone failed:\n{result.stderr}"
    loaded = ast.literal_eval(result.stdout.strip())
    assert loaded == [], f"importing the observation companion pulled in {loaded}"


def test_the_server_side_actuation_package_never_imports_the_device_side():
    """The deciding half cannot call the acting half, even by accident."""
    for path in sorted(SERVER_ACTUATION.glob("*.py")):
        for module in _imports(path):
            assert "windows_actuation" not in module, f"{path.name} imports {module}"


def test_the_two_packages_read_different_environment_prefixes():
    """A configuration mistake cannot point one process at the other's role."""
    from bartholomew.companion.config import ENV_PREFIX as OBSERVE_PREFIX
    from bartholomew.windows_actuation.config import ENV_PREFIX as ACT_PREFIX

    assert OBSERVE_PREFIX == "BARTH_COMPANION_"
    assert ACT_PREFIX == "BARTH_ACTION_"
    assert OBSERVE_PREFIX != ACT_PREFIX


def test_the_two_channels_use_different_paths_and_different_headers():
    from bartholomew.companion.client import INBOUND_PATH
    from bartholomew.windows_actuation.channel import LEASE_PATH, RESULT_PATH_TEMPLATE

    assert INBOUND_PATH == "/api/inbound/events"
    assert LEASE_PATH.startswith("/api/device-actions")
    assert RESULT_PATH_TEMPLATE.startswith("/api/device-actions")
    assert not LEASE_PATH.startswith(INBOUND_PATH)


# --- 2. the observation client still has nowhere to put a command -------------


def test_the_observation_client_still_has_exactly_one_verb():
    """Requirement 16: the existing behaviour is intact, not merely untouched."""
    from bartholomew.companion.client import InboundSubmitClient

    public = {
        n
        for n in dir(InboundSubmitClient)
        if not n.startswith("_") and callable(getattr(InboundSubmitClient, n))
    }
    assert public == {"submit"}


def test_the_observation_vocabulary_still_names_no_action():
    from bartholomew.companion.observation import ALL_PAYLOAD_KEYS

    assert ALL_PAYLOAD_KEYS == frozenset(
        {"device_id", "state", "idle_seconds", "application", "platform", "companion_version"},
    )
    for key in ALL_PAYLOAD_KEYS:
        assert key not in {"command", "execute", "action", "operation", "capability"}


def test_the_action_channel_client_has_exactly_two_verbs():
    """An `execute` or a generic `post` would be a third, wider surface."""
    from bartholomew.windows_actuation.channel import ActionChannelClient

    public = {
        n
        for n in dir(ActionChannelClient)
        if not n.startswith("_") and callable(getattr(ActionChannelClient, n))
    }
    assert public == {"lease", "report"}


# --- 3. a hostile server, against the real companion --------------------------


class _HostileInboundHandler(http.server.BaseHTTPRequestHandler):
    """Answers every observation with an action, and sees if anything acts."""

    received: list = []

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler's interface
        length = int(self.headers.get("Content-Length", 0))
        _HostileInboundHandler.received.append(json.loads(self.rfile.read(length) or b"{}"))
        body = json.dumps(
            {
                "captured": True,
                "outcome": "captured",
                # Every shape the real action channel uses, offered through the
                # observation channel at once.
                "actions": [
                    {
                        "action_id": "act-forged",
                        "tenant_id": "tenant-a",
                        "device_id": "desk-pc",
                        "capability": "windows.launch_app",
                        "capability_version": 1,
                        "parameters": {"app_id": "notepad"},
                        "expires_at": "2099-01-01T00:00:00Z",
                        "repeatability": "idempotent",
                    },
                ],
                "capability": "windows.type_text",
                "parameters": {"text": "forged"},
                "command": "rm -rf /",
                "execute": {"shell": "calc.exe"},
                "action": "open_url",
                "next_poll_url": "http://127.0.0.1:1/evil",
            },
        ).encode()
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # noqa: A003 - silence the test server
        pass


@pytest.fixture
def hostile_inbound_server():
    _HostileInboundHandler.received = []
    server = http.server.HTTPServer(("127.0.0.1", 0), _HostileInboundHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def test_a_forged_action_in_an_observation_response_reaches_no_dispatcher(
    hostile_inbound_server,
    tmp_path,
    monkeypatch,
):
    """The real companion, a real hostile server, and a watched dispatcher."""
    from bartholomew.companion.config import CompanionConfig
    from bartholomew.companion.runner import CompanionRunner
    from bartholomew.companion.sources import SyntheticObservationSource
    from bartholomew.windows_actuation import dispatch as dispatch_module
    from bartholomew.windows_actuation import handlers as handlers_module

    calls: list = []

    def _tripwire(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("the dispatcher was reached from the observation path")

    monkeypatch.setattr(dispatch_module, "dispatch", _tripwire)
    for kind in list(handlers_module.HANDLERS):
        monkeypatch.setitem(handlers_module.HANDLERS, kind, _tripwire)

    config = CompanionConfig(
        base_url=hostile_inbound_server,
        source_id="desk-companion",
        device_id="desk-pc",
        state_path=tmp_path / "state.json",
        poll_seconds=0.01,
    )
    runner = CompanionRunner(
        config,
        SyntheticObservationSource(
            [("foreground_app", {"application": "chrome"})],
            device_id="desk-pc",
        ),
        sleep=lambda _s: None,
    )
    summary = runner.run(cycles=1)

    assert summary.captured > 0, "the companion really did talk to the hostile server"
    assert _HostileInboundHandler.received
    assert calls == [], "nothing dispatched"

    # And nothing from the response survives anywhere the companion writes.
    state = (tmp_path / "state.json").read_text(encoding="utf-8")
    for poison in (
        "act-forged",
        "windows.launch_app",
        "windows.type_text",
        "rm -rf",
        "calc.exe",
        "open_url",
        "evil",
    ):
        assert poison not in state


def test_the_observation_runner_still_branches_only_on_a_delivery_status():
    """Structural: the response body is not parsed into anything actionable."""
    tree = ast.parse((OBSERVATION / "runner.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        for name in ("Module", "ClassDef", "FunctionDef", "AsyncFunctionDef"):
            if isinstance(node, getattr(ast, name)) and node.body:
                first = node.body[0]
                if (
                    isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)
                ):
                    node.body.pop(0)
    code = ast.unparse(tree)
    for forbidden in ("response", ".json()", "result.body", "result.payload", "actions"):
        assert forbidden not in code


# --- 4. two resolvers, two globals --------------------------------------------


@pytest.fixture
def resolvers_cleared():
    from bartholomew_api_bridge_v0_1.services.api import device_action_auth, inbound_auth

    inbound_auth.clear_resolver()
    device_action_auth.clear_resolver()
    yield
    inbound_auth.clear_resolver()
    device_action_auth.clear_resolver()


def test_the_two_resolvers_are_independent_module_globals(resolvers_cleared, monkeypatch):
    """Opening observation capture does not open actuation."""
    from bartholomew_api_bridge_v0_1.services.api import device_action_auth, inbound_auth

    monkeypatch.setenv(inbound_auth.ALLOW_TEST_RESOLVER_ENV, "1")
    inbound_auth.install_test_resolver("observation-token")

    assert inbound_auth.get_resolver() is not None
    assert (
        device_action_auth.get_resolver() is None
    ), "installing the observation resolver opened the action channel"


def test_the_action_resolver_does_not_open_observation_capture(resolvers_cleared, monkeypatch):
    from bartholomew_api_bridge_v0_1.services.api import device_action_auth, inbound_auth

    monkeypatch.setenv(device_action_auth.ALLOW_TEST_RESOLVER_ENV, "1")
    device_action_auth.install_test_resolver("action-token")

    assert device_action_auth.get_resolver() is not None
    assert inbound_auth.get_resolver() is None


def test_the_action_channel_is_closed_by_default(resolvers_cleared):
    from bartholomew_api_bridge_v0_1.services.api import device_action_auth

    assert device_action_auth.get_resolver() is None
    assert device_action_auth.resolver_is_test_only() is False


def test_the_action_test_resolver_cannot_enable_itself(resolvers_cleared, monkeypatch):
    from bartholomew_api_bridge_v0_1.services.api import device_action_auth

    monkeypatch.delenv(device_action_auth.ALLOW_TEST_RESOLVER_ENV, raising=False)
    with pytest.raises(RuntimeError) as excinfo:
        device_action_auth.install_test_resolver("token")
    assert "not authentication" in str(excinfo.value)

    # And one variable alone is not enough, either.
    monkeypatch.setenv(device_action_auth.TEST_RESOLVER_TOKEN_ENV, "token")
    assert device_action_auth.maybe_install_test_resolver_from_env() is False
    assert device_action_auth.get_resolver() is None


def test_a_device_cannot_choose_its_own_tenant(resolvers_cleared, monkeypatch):
    """A resolver that claims a tenant is ignored entirely."""
    from bartholomew_api_bridge_v0_1.services.api import device_action_auth

    monkeypatch.setenv(device_action_auth.ALLOW_TEST_RESOLVER_ENV, "1")
    device_action_auth.install_test_resolver(
        "token",
        device_id="desk-pc",
        claimed_tenant_id="somebody-elses-tenant",
    )

    class _Request:
        class state:  # noqa: N801 - mirrors starlette's request.state
            principal = None

    monkeypatch.delenv("BARTH_RUNTIME_USER_ID", raising=False)
    resolved = device_action_auth.resolved_tenant_id(_Request())
    assert resolved == device_action_auth.LOCAL_TENANT
    assert resolved != "somebody-elses-tenant"

    # The contract has no tenant on it at all, which is why the claim is inert.
    assert not hasattr(device_action_auth.VerifiedDevice, "tenant_id")


def test_the_tenant_comes_from_the_principal_when_there_is_one(resolvers_cleared):
    from bartholomew_api_bridge_v0_1.services.api import device_action_auth

    class _Principal:
        user_id = "11111111-2222-3333-4444-555555555555"

    class _Request:
        class state:  # noqa: N801 - mirrors starlette's request.state
            principal = _Principal()

    assert device_action_auth.resolved_tenant_id(_Request()) == _Principal.user_id


# --- the existing observation suite still holds -------------------------------


def test_the_existing_observation_structural_suite_is_still_present():
    """Requirement 16, said plainly: the old guard is still there to pass."""
    existing = ROOT / "tests" / "test_companion_no_actuation.py"
    assert existing.exists()
    source = existing.read_text(encoding="utf-8")
    assert "SendInput" in source
    assert "test_the_client_has_no_verb_other_than_submit" in source
