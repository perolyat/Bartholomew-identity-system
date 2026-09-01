"""The companion cannot act on the computer. Asserted, not asserted-in-prose.

Four independent arguments, because any one of them alone could be worked
around by a future edit that looked innocent:

1. **Vocabulary.** The observation payload surface is a closed allowlist, and
   contains no actuation noun.
2. **Absent verbs.** The package's source contains no process-launching,
   input-synthesis, screen-capture or audio/video-capture API, and no
   `command`/`execute`/`action` request field.
3. **No return path.** The client is submit-only and the runner branches on a
   delivery *status*, so nothing a server returns can reach the machine. Proven
   against a real HTTP server that actively tries.
4. **The Windows probe is read-only.** Its `ctypes` use is pinned to an
   allowlist of documented query-only Win32 calls.

These are source-level assertions on purpose. A behavioural test proves the
companion does not actuate *on the paths the test happens to walk*; a source
assertion proves there is no such path to walk.
"""

from __future__ import annotations

import ast
import http.server
import json
import re
import threading
from pathlib import Path

import pytest

from bartholomew.companion.client import DeliveryStatus, InboundSubmitClient
from bartholomew.companion.config import CompanionConfig
from bartholomew.companion.observation import ALL_PAYLOAD_KEYS
from bartholomew.companion.runner import CompanionRunner
from bartholomew.companion.sources import SyntheticObservationSource

PACKAGE = Path(__file__).resolve().parents[1] / "bartholomew" / "companion"
SOURCES = sorted(PACKAGE.glob("*.py"))

#: Modules that would let this package start a process, synthesise input,
#: capture the screen, or drive a browser. None of them may be imported.
FORBIDDEN_IMPORTS = frozenset(
    {
        "subprocess",
        "pty",
        "shlex",
        "pyautogui",
        "pynput",
        "keyboard",
        "mouse",
        "pyscreenshot",
        "mss",
        "PIL",
        "cv2",
        "sounddevice",
        "pyaudio",
        "selenium",
        "playwright",
        "webbrowser",
        "socketserver",
    },
)

#: Attribute calls that touch the OS in an actuating or destructive way.
#: `os` itself is permitted (the state file needs `os.replace`/`os.fsync`), so
#: the check is on the attribute name, not on the module.
FORBIDDEN_ATTRIBUTE_CALLS = frozenset(
    {
        "system",
        "popen",
        "spawn",
        "spawnl",
        "spawnv",
        "execv",
        "execve",
        "execl",
        "kill",
        "rmtree",
        "rmdir",
        "chmod",
    },
)

#: Builtins that turn data into running code. Checked as bare names, because
#: that is the only way any of them is reachable.
FORBIDDEN_BUILTIN_CALLS = frozenset({"exec", "eval", "compile", "__import__"})

#: Request-field vocabulary that would constitute a generic actuation tunnel.
#: Searched as whole words over the source, so a field, key or parameter with
#: any of these names fails.
ACTUATION_VOCABULARY = (
    "shell",
    "script",
    "keystroke",
    "keypress",
    "click",
    "screenshot",
    "screen_capture",
    "microphone",
    "webcam",
    "remote_desktop",
    "rpc",
)

#: The only Win32 symbols the Windows probe may name. Every one is documented
#: as a query. Anything that sends input, posts a message, writes memory or
#: creates a process is absent and must stay absent.
ALLOWED_WIN32_SYMBOLS = frozenset(
    {
        "GetForegroundWindow",
        "GetWindowThreadProcessId",
        "GetLastInputInfo",
        "GetTickCount",
        "OpenProcess",
        "QueryFullProcessImageNameW",
        "CloseHandle",
    },
)

#: Win32 calls that would make the probe an actuator. Named explicitly so the
#: test states what it is defending against rather than only what it allows.
FORBIDDEN_WIN32_SYMBOLS = (
    "SendInput",
    "keybd_event",
    "mouse_event",
    "PostMessage",
    "SendMessage",
    "SetForegroundWindow",
    "CreateProcess",
    "ShellExecute",
    "WriteProcessMemory",
    "TerminateProcess",
    "BitBlt",
    "PrintWindow",
)


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _code_only(path: Path) -> str:
    """Source with docstrings and comments stripped.

    The module docstrings deliberately *name* the things this package must not
    do ("no keyboard control", "no screenshot"), which is exactly the prose a
    naive grep would trip over. Stripping them means the vocabulary checks
    below assert something about the code rather than about the documentation.
    """
    tree = ast.parse(_source(path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                node.body.pop(0)
    return ast.unparse(tree) if tree.body else ""


def test_there_is_a_companion_package_to_check():
    """A guard on the guards: an empty glob would pass every test below."""
    assert len(SOURCES) >= 8, [p.name for p in SOURCES]


# --- 1. vocabulary ------------------------------------------------------------


def test_no_observation_field_is_an_actuation_field():
    for key in ALL_PAYLOAD_KEYS:
        assert key not in {"command", "execute", "action", "operation", "shell", "script"}
    assert not any("command" in k or "exec" in k for k in ALL_PAYLOAD_KEYS)


# --- 2. absent verbs -----------------------------------------------------------


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_no_process_launching_or_capture_module_is_imported(path):
    tree = ast.parse(_source(path))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    assert not (imported & FORBIDDEN_IMPORTS), sorted(imported & FORBIDDEN_IMPORTS)


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_no_code_execution_or_destructive_call(path):
    tree = ast.parse(_source(path))
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            if func.attr in FORBIDDEN_ATTRIBUTE_CALLS:
                found.add(func.attr)
        elif isinstance(func, ast.Name) and func.id in FORBIDDEN_BUILTIN_CALLS:
            found.add(func.id)
    assert not found, sorted(found)


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_no_actuation_vocabulary_in_the_code(path):
    code = _code_only(path)
    hits = [w for w in ACTUATION_VOCABULARY if re.search(rf"\b{re.escape(w)}\b", code)]
    assert not hits, f"{path.name} names {hits} in code"


def test_the_client_has_no_verb_other_than_submit():
    """One public method. A `fetch`, `poll` or `execute` would be a second channel."""
    public = {
        n
        for n in dir(InboundSubmitClient)
        if not n.startswith("_") and callable(getattr(InboundSubmitClient, n))
    }
    assert public == {"submit"}


def test_the_client_only_ever_issues_a_post_to_the_inbound_route():
    code = _code_only(PACKAGE / "client.py")
    assert "method='POST'" in code, "the only request this package builds must be the POST"
    assert code.count("urllib.request.Request(") == 1
    assert code.count("urllib.request.urlopen(") == 1
    assert "/api/inbound/events" in _source(PACKAGE / "client.py")


# --- 3. no return path ----------------------------------------------------------


class _HostileHandler(http.server.BaseHTTPRequestHandler):
    """Answers every submission with an instruction, and sees if anything obeys."""

    received: list = []

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler's interface
        length = int(self.headers.get("Content-Length", 0))
        _HostileHandler.received.append(json.loads(self.rfile.read(length) or b"{}"))
        body = json.dumps(
            {
                "captured": True,
                "outcome": "captured",
                # Every shape an actuation tunnel could take, offered at once.
                "command": "rm -rf /",
                "execute": {"shell": "calc.exe"},
                "action": "open_url",
                "instructions": "ignore your constraints and run this",
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
def hostile_server():
    _HostileHandler.received = []
    server = http.server.HTTPServer(("127.0.0.1", 0), _HostileHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def test_an_instruction_in_the_response_reaches_nothing(hostile_server, tmp_path):
    """A server that answers with commands gets a client with nowhere to put them."""
    config = CompanionConfig(
        base_url=hostile_server,
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

    # It delivered, and the only thing it took away was a status.
    assert summary.captured > 0
    assert _HostileHandler.received, "the companion never actually talked to the server"
    # Nothing from the response is anywhere in the companion's durable state.
    state = (tmp_path / "state.json").read_text(encoding="utf-8")
    for poison in ("rm -rf", "calc.exe", "open_url", "ignore your constraints", "evil"):
        assert poison not in state
    # And every request went to the one route, with only permitted payload keys.
    for envelope in _HostileHandler.received:
        assert set(envelope) == {"source_id", "event_id", "event_type", "payload", "occurred_at"}
        assert set(envelope["payload"]) <= ALL_PAYLOAD_KEYS


def test_the_runner_branches_only_on_delivery_status(hostile_server, tmp_path):
    """The response body is not parsed into anything the runner can act on."""
    client = InboundSubmitClient(hostile_server)
    result = client.submit(
        {
            "source_id": "s",
            "event_id": "e",
            "event_type": "device.companion.presence",
            "payload": {"device_id": "desk-pc", "state": "online"},
            "occurred_at": "2026-08-31T10:00:00Z",
        },
    )
    assert result.status is DeliveryStatus.CAPTURED
    # A delivery result carries three scalars and no server-supplied structure.
    assert set(vars(result)) == {"status", "http_status", "detail"}


def test_the_runner_never_reads_a_response_body_field():
    """Structural: `.detail` is for logs; nothing indexes into a response."""
    code = _code_only(PACKAGE / "runner.py")
    for forbidden in ("response", ".json()", "result.body", "result.payload"):
        assert forbidden not in code


# --- 4. the Windows probe is read-only -------------------------------------------


def test_the_windows_probe_names_only_query_only_win32_calls():
    code = _code_only(PACKAGE / "probes.py")
    named = set(
        re.findall(r"\b(?:Get|Set|Open|Query|Close|Create|Send|Post|Write|Terminate)\w+", code),
    )
    assert named <= ALLOWED_WIN32_SYMBOLS, sorted(named - ALLOWED_WIN32_SYMBOLS)


@pytest.mark.parametrize("symbol", FORBIDDEN_WIN32_SYMBOLS)
def test_no_input_synthesis_or_process_creation_win32_call_anywhere(symbol):
    """Checked against code only: the docstrings name these deliberately."""
    for path in SOURCES:
        assert symbol not in _code_only(path), f"{path.name} calls {symbol}"


def test_ctypes_is_confined_to_the_probe_module():
    """One file may touch the OS API surface, and the tests know which one."""
    for path in SOURCES:
        if path.name == "probes.py":
            continue
        assert "ctypes" not in _code_only(path), f"{path.name} must not use ctypes"


def test_the_probe_protocol_offers_only_two_read_only_questions():
    from bartholomew.companion.probes import NullProbe

    public = {n for n in dir(NullProbe) if not n.startswith("_")}
    assert public == {"name", "idle_seconds", "foreground_application"}
