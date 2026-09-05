"""What Bartholomew cannot do to a computer, asserted over the source.

Acceptance requirements 13 and 14: arbitrary executable, shell and PowerShell
requests are structurally impossible, and secret-field and prohibited-action
attempts fail closed.

Modelled on `tests/test_companion_no_actuation.py`, which makes the same kind
of argument about the observation package -- but the claim here is different
and harder. That package's argument is "there is no actuation code at all";
this one's is "there is actuation code, and it is exactly nine things". So
these assertions are about *shape*: what may be imported, what may be called,
what a function's signature may accept, and what the dispatch table may be
built from.

A behavioural test proves the system does not do something on the paths the
test happens to walk. A source assertion proves there is no such path to walk.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

from bartholomew.actuation import parameters
from bartholomew.actuation.capabilities import ALL_CAPABILITIES, CapabilityKind
from bartholomew.actuation.sensitive import (
    detect_secrets,
    final_action_reason,
    secret_categories,
    sensitive_field_reasons,
)
from bartholomew.windows_actuation import win32
from bartholomew.windows_actuation.handlers import HANDLERS

ROOT = Path(__file__).resolve().parents[1]
SERVER_PACKAGE = ROOT / "bartholomew" / "actuation"
DEVICE_PACKAGE = ROOT / "bartholomew" / "windows_actuation"
SERVER_SOURCES = sorted(SERVER_PACKAGE.glob("*.py"))
DEVICE_SOURCES = sorted(DEVICE_PACKAGE.glob("*.py"))
ALL_SOURCES = SERVER_SOURCES + DEVICE_SOURCES

#: Modules that would let these packages run a program of somebody else's
#: choosing, drive a browser outside the capability model, capture the screen,
#: or turn data into code.
FORBIDDEN_IMPORTS = frozenset(
    {
        "subprocess",
        "pty",
        "shlex",
        "commands",
        "popen2",
        "importlib",
        "imp",
        "runpy",
        "pickle",
        "marshal",
        "shelve",
        "code",
        "codeop",
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
        "pywinauto",
        "webbrowser",
        "socketserver",
        "telnetlib",
        "paramiko",
        "winreg",
    },
)

#: Attribute calls that execute something, mutate the filesystem, or resolve a
#: name at runtime. `os` itself is permitted -- paths and atomic renames need
#: it -- so the check is on the attribute, not the module.
FORBIDDEN_ATTRIBUTE_CALLS = frozenset(
    {
        "system",
        "popen",
        "spawn",
        "spawnl",
        "spawnv",
        "spawnve",
        "execv",
        "execve",
        "execl",
        "execlp",
        "startfile",
        "kill",
        "remove",
        "rmtree",
        "rmdir",
        "rename",
        "renames",
        "copy",
        "copy2",
        "copyfile",
        "move",
        "chmod",
        "chown",
        "truncate",
        # `os.unlink` is permitted in exactly one place -- see
        # `test_the_only_file_removal_is_the_state_files_own_temporary_file`,
        # which pins it far more tightly than a name ban could.
        "import_module",
        "load_module",
        "check_output",
        "check_call",
        "call",
        "run_command",
    },
)

#: Builtins that turn data into running code, or a string into a callable.
#: `getattr` is included because reflection-based dispatch is exactly the
#: escape hatch the capability model exists to close.
FORBIDDEN_BUILTIN_CALLS = frozenset(
    {"exec", "eval", "compile", "__import__", "globals", "locals", "vars"},
)

#: Vocabulary that would constitute a generic actuation tunnel. Searched as
#: whole words over the *code* (docstrings and comments stripped), so a field,
#: key, parameter or literal with any of these names fails.
FORBIDDEN_VOCABULARY = (
    "powershell",
    "pwsh",
    "cmd.exe",
    "bash",
    "wscript",
    "cscript",
    "mshta",
    "rundll32",
    "regsvr32",
    "certutil",
    "bitsadmin",
    "argv",
    "cmdline",
    "commandline",
    "shellcode",
    "screenshot",
    "screen_capture",
    "microphone",
    "webcam",
    "remote_desktop",
)

#: `argv` is banned everywhere except the entry point's own
#: `_process_arguments()`, which reads *this process's* command line rather
#: than constructing one for something else. See that function's docstring.
_ARGV_EXEMPT = {"__main__.py"}

#: Win32 symbols that would make these packages capable of something the
#: capability model does not name. Every one is a real API somebody could
#: reach for; naming them says what is being defended against, not only what
#: is allowed.
FORBIDDEN_WIN32_SYMBOLS = (
    "WinExec",
    "ShellExecuteExW",
    "CreateProcessAsUserW",
    "CreateProcessWithLogonW",
    "CreateRemoteThread",
    "WriteProcessMemory",
    "VirtualAllocEx",
    "TerminateProcess",
    "OpenProcessToken",
    "AdjustTokenPrivileges",
    "keybd_event",
    "mouse_event",
    "SendMessageW",
    "PostMessageW",
    "SetWindowsHookExW",
    "AttachThreadInput",
    "AllowSetForegroundWindow",
    "LockSetForegroundWindow",
    "BitBlt",
    "PrintWindow",
    "GetDC",
    "RegCreateKeyExW",
    "RegSetValueExW",
    "DeleteFileW",
    "MoveFileW",
    "CopyFileW",
    "CreateFileW",
    "SHFileOperationW",
    "InitiateSystemShutdownW",
    "ExitWindowsEx",
)

#: Parameter names that would be a generic escape hatch in the wire format.
FORBIDDEN_PARAMETER_NAMES = (
    "command",
    "cmd",
    "commands",
    "script",
    "shell",
    "args",
    "argv",
    "arguments",
    "argument",
    "exe",
    "executable",
    "binary",
    "program",
    "code",
    "payload",
    "eval",
    "expression",
    "query",
    "sql",
    "verb",
    "flags",
    "env",
    "environment",
    "cwd",
    "working_directory",
)


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _code_only(path: Path) -> str:
    """Source with every docstring stripped.

    These modules deliberately *name* the things they must not do ("no
    PowerShell", "no arbitrary executable path"), which is exactly the prose a
    naive grep would trip over. Stripping docstrings means the vocabulary
    checks assert something about the code rather than about the documentation.
    Comments are stripped too, because `ast.unparse` does not emit them.
    """
    tree = ast.parse(_source(path))
    for node in ast.walk(tree):
        if isinstance(
            node,
            (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
        ):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                node.body.pop(0)
    return ast.unparse(tree) if tree.body else ""


def test_there_are_packages_to_check():
    """A guard on the guards: an empty glob would pass every test below."""
    assert len(SERVER_SOURCES) >= 9, [p.name for p in SERVER_SOURCES]
    assert len(DEVICE_SOURCES) >= 8, [p.name for p in DEVICE_SOURCES]


# --- 13a. nothing can run a program of somebody else's choosing ---------------


@pytest.mark.parametrize("path", ALL_SOURCES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_no_process_launching_or_code_loading_module_is_imported(path):
    tree = ast.parse(_source(path))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    assert not (imported & FORBIDDEN_IMPORTS), sorted(imported & FORBIDDEN_IMPORTS)


@pytest.mark.parametrize("path", ALL_SOURCES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_no_code_execution_reflection_or_destructive_call(path):
    tree = ast.parse(_source(path))
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in FORBIDDEN_ATTRIBUTE_CALLS:
            found.add(func.attr)
        elif isinstance(func, ast.Name) and func.id in FORBIDDEN_BUILTIN_CALLS:
            found.add(func.id)
    assert not found, sorted(found)


@pytest.mark.parametrize("path", ALL_SOURCES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_no_shell_or_interpreter_vocabulary_in_the_code(path):
    code = _code_only(path).lower()
    vocabulary = [
        w for w in FORBIDDEN_VOCABULARY if not (w == "argv" and path.name in _ARGV_EXEMPT)
    ]
    hits = [w for w in vocabulary if re.search(rf"(?<![a-z0-9_]){re.escape(w)}(?![a-z0-9_])", code)]
    assert not hits, f"{path.name} names {hits} in code"


@pytest.mark.parametrize("symbol", FORBIDDEN_WIN32_SYMBOLS)
def test_no_forbidden_win32_symbol_appears_anywhere(symbol):
    """Checked against code only: the docstrings name these deliberately."""
    for path in ALL_SOURCES:
        assert symbol not in _code_only(path), f"{path.name} names {symbol}"


def test_the_only_deserialisation_is_json():
    """`loads` is permitted, but only ever `json.loads`.

    Tighter than banning the name: `pickle` and `marshal` are already
    un-importable (see `FORBIDDEN_IMPORTS`), and this pins the remaining call
    sites to the one module that cannot execute what it reads.
    """
    for path in ALL_SOURCES:
        tree = ast.parse(_source(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "loads"
            ):
                assert isinstance(node.func.value, ast.Name), f"{path.name}: opaque .loads()"
                assert (
                    node.func.value.id == "json"
                ), f"{path.name} deserialises with {node.func.value.id}.loads"


def test_the_only_file_removal_is_the_state_files_own_temporary_file():
    """One `os.unlink`, in one function, on a temp file that function created.

    The action companion writes its ledger atomically -- temp file, fsync,
    rename -- and cleans the temp file up if the write fails. That is the only
    deletion in either package, and it can only ever remove a path
    `tempfile.mkstemp` just returned. Pinned here rather than banned, because
    banning it would mean a half-written ledger left behind on every failure.
    """
    for path in ALL_SOURCES:
        tree = ast.parse(_source(path))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("unlink", "remove")
            ):
                continue
            assert path.name == "state.py", f"{path.name} removes a file"
            (target,) = node.args
            assert (
                isinstance(target, ast.Name) and target.id == "tmp"
            ), "the only removable path is the temporary file this function created"


def test_ctypes_is_confined_to_the_one_win32_module():
    """One file may touch the OS API surface, and the tests know which one."""
    for path in ALL_SOURCES:
        if path.name == "win32.py":
            continue
        assert "ctypes" not in _code_only(path), f"{path.name} must not use ctypes"


def test_the_server_side_package_touches_no_operating_system_at_all():
    """The deciding half has no window handle, no clipboard and no keystroke."""
    for path in SERVER_SOURCES:
        code = _code_only(path).lower()
        for forbidden in ("ctypes", "windll", "user32", "kernel32", "shell32", "sendinput"):
            assert forbidden not in code, f"{path.name} reaches for {forbidden}"


# --- 13b. the process starter cannot be given an argument ---------------------


def test_the_process_starter_takes_exactly_one_parameter():
    """One parameter, and it is a path from an allowlist. Nowhere for an argv."""
    signature = inspect.signature(win32.start_process)
    assert list(signature.parameters) == ["executable_path"]


def test_the_shell_opener_takes_exactly_one_parameter():
    """No verb, no parameter string. Nowhere for an argument."""
    signature = inspect.signature(win32.shell_open)
    assert list(signature.parameters) == ["target"]


def test_create_process_is_called_with_a_null_command_line():
    """The argument vector is literally `None`, at the one call site."""
    tree = ast.parse(_source(DEVICE_PACKAGE / "win32.py"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "CreateProcessW"
    ]
    assert len(calls) == 1, "CreateProcessW must have exactly one call site"
    (call,) = calls
    # Positional argument 1 is lpCommandLine.
    assert isinstance(call.args[1], ast.Constant) and call.args[1].value is None


def test_shell_execute_is_called_with_a_null_parameter_string():
    tree = ast.parse(_source(DEVICE_PACKAGE / "win32.py"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "ShellExecuteW"
    ]
    assert len(calls) == 1, "ShellExecuteW must have exactly one call site"
    (call,) = calls
    # Positional argument 3 is lpParameters.
    assert isinstance(call.args[3], ast.Constant) and call.args[3].value is None
    # And the verb is the literal "open", not something a caller chose.
    assert isinstance(call.args[1], ast.Constant) and call.args[1].value == "open"


def test_typing_synthesises_characters_and_never_a_virtual_key():
    """`wVk = 0` is what makes this incapable of pressing Enter."""
    code = _code_only(DEVICE_PACKAGE / "win32.py")
    assert "KEYEVENTF_UNICODE" in code
    assert "item.union.ki.wVk = 0" in code
    for virtual_key in ("VK_RETURN", "VK_TAB", "VK_ESCAPE", "VK_MENU", "VK_CONTROL", "VK_LWIN"):
        assert virtual_key not in code
    assert "INPUT_MOUSE" not in code


def test_there_is_no_mouse_input_anywhere():
    for path in ALL_SOURCES:
        code = _code_only(path).lower()
        for forbidden in ("mouse_event", "input_mouse", "mouseeventf", "setcursorpos"):
            assert forbidden not in code, f"{path.name} names {forbidden}"


# --- 13c. dispatch is a literal table, never a lookup by name -----------------


def test_the_handler_table_is_exactly_the_nine_capabilities():
    assert set(HANDLERS) == set(ALL_CAPABILITIES)
    assert len(HANDLERS) == 9


def test_the_handler_table_is_built_from_a_literal_dict_of_enum_members():
    """Not a registry: no `register()`, no entry point, no runtime name lookup."""
    tree = ast.parse(_source(DEVICE_PACKAGE / "handlers.py"))
    assignments = [
        node
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "HANDLERS"
    ]
    assert len(assignments) == 1, "HANDLERS must be assigned exactly once, at module level"
    literal = assignments[0].value
    assert isinstance(literal, ast.Dict), "HANDLERS must be a literal dict"
    for key in literal.keys:
        assert isinstance(key, ast.Attribute), "every key must be a CapabilityKind member"
        assert isinstance(key.value, ast.Name) and key.value.id == "CapabilityKind"
    for value in literal.values:
        assert isinstance(value, ast.Name), "every value must be a named function"


def test_the_validator_table_is_built_the_same_way():
    tree = ast.parse(_source(SERVER_PACKAGE / "parameters.py"))
    assignments = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_VALIDATORS" for t in node.targets)
    ]
    assert len(assignments) == 1
    literal = assignments[0].value
    assert isinstance(literal, ast.Dict)
    assert len(literal.keys) == 9


def test_nothing_in_the_dispatch_path_resolves_a_name_at_runtime():
    for name in ("dispatch.py", "handlers.py", "channel.py", "runner.py"):
        code = _code_only(DEVICE_PACKAGE / name)
        for forbidden in ("getattr(", "setattr(", "globals(", "locals(", "vars(", "eval(", "exec("):
            assert forbidden not in code, f"{name} uses {forbidden}"


# --- 13d. the wire format has no escape hatch ---------------------------------


@pytest.mark.parametrize("kind", ALL_CAPABILITIES)
@pytest.mark.parametrize("forbidden", FORBIDDEN_PARAMETER_NAMES)
def test_no_capability_accepts_an_escape_hatch_parameter(kind, forbidden):
    """Every capability refuses these names, including ones it validates fine."""
    with pytest.raises(parameters.ParameterError):
        parameters.validate(kind, {forbidden: "anything at all"})


def test_the_union_of_every_capabilitys_parameters_names_no_escape_hatch():
    """The whole wire vocabulary, in one assertion."""
    names = {
        "url",
        "path",
        "app_id",
        "operation",
        "x",
        "y",
        "width",
        "height",
        "text",
        "element_name",
    }
    tree = ast.parse(_source(SERVER_PACKAGE / "parameters.py"))
    declared = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_closed"
        ):
            permitted = node.args[1]
            assert isinstance(permitted, ast.Tuple), "the permitted set must be a literal tuple"
            for element in permitted.elts:
                assert isinstance(element, ast.Constant)
                declared.add(element.value)
    assert declared == names
    assert not (declared & set(FORBIDDEN_PARAMETER_NAMES))


def test_a_capability_named_like_execution_does_not_exist():
    for kind in ALL_CAPABILITIES:
        assert kind.value.startswith("windows.")
        for forbidden in ("run", "exec", "shell", "script", "command", "install", "delete"):
            assert forbidden not in kind.value


# --- 14. secrets and prohibited actions fail closed ---------------------------


#: Synthetic credential-shaped strings for the detector to recognise.
#:
#: **Assembled from fragments rather than written as literals**, and that is
#: not squeamishness: a repository's own secret scanner reads source files and
#: cannot tell a test fixture from a leak, so a literal here blocks the push
#: and trains everyone to click "allow this secret". Joining the pieces at
#: runtime keeps the string the detector sees exactly the same while leaving no
#: line in the file that matches a scanner signature.
#:
#: Every value below is invented. None is, or resembles, a real credential.
def _synthetic(*parts: str) -> str:
    return "".join(parts)


SECRET_SHAPED = [
    _synthetic("-----BEGIN ", "RSA", " PRIVATE KEY-----\nMIIB"),
    _synthetic("AKIA", "IOSFODNN", "7EXAMPLE"),
    _synthetic("gh", "p_", "1234567890abcdefghijklmnopqrstuvwxyzAB"),
    _synthetic("xox", "b-", "123456789012", "-abcdefghijklmno"),
    _synthetic("AIza", "SyA1234567890abcdefghijklmnopqrstuv"),
    _synthetic("sk", "_live_", "abcdefghij1234567890"),
    _synthetic(
        "eyJhbGciOiJIUzI1NiJ9.",
        "eyJzdWIiOiIxMjM0NTY3ODkwIn0.",
        "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    ),
    _synthetic("Authorization: ", "Bearer ", "abcdefghijklmnopqrstuvwx"),
    _synthetic("password", " = ", "hunter2correcthorse"),
    _synthetic("api", "_key: ", "sk-", "abcdefghijklmnopqrstuvwxyz"),
    _synthetic("https://user:", "hunter2", "@internal.example.com/"),
    _synthetic("4111 ", "1111 ", "1111 ", "1111"),
    _synthetic("pwd", "=", "verysecretvalue"),
]


@pytest.mark.parametrize("text", SECRET_SHAPED, ids=lambda t: t[:16])
def test_the_secret_detector_recognises_credential_material(text):
    assert detect_secrets(text), f"nothing fired for {text[:40]!r}"


@pytest.mark.parametrize(
    "text",
    [
        "remind me to call the plumber on Tuesday",
        "the roof repair quote was 2400 dollars",
        "meeting notes: agreed to defer the migration",
        "",
    ],
)
def test_the_secret_detector_leaves_ordinary_text_alone(text):
    assert detect_secrets(text) == ()


def test_a_finding_never_carries_the_matched_value():
    """A detector that logged what it caught would be a second copy of it."""
    value = _synthetic("sk-", "abcdefghijklmnopqrstuvwxyz012345")
    findings = detect_secrets(_synthetic("api", "_key = ", value))
    assert findings
    for finding in findings:
        rendered = finding.describe() + repr(finding)
        assert value not in rendered


@pytest.mark.parametrize("kind", [CapabilityKind.TYPE_TEXT, CapabilityKind.CLIPBOARD_WRITE])
def test_typing_or_copying_a_secret_is_refused_at_validation(kind):
    secret = _synthetic("api", "_key = ", "sk-", "abcdefghijklmnopqrstuvwxyz012345")
    with pytest.raises(parameters.ParameterError) as excinfo:
        parameters.validate(kind, {"text": secret})
    assert "credential material" in str(excinfo.value)


def test_an_unreadable_focused_field_is_a_reason_to_refuse():
    """Unknown is handled as sensitive, never as fine."""
    reasons = sensitive_field_reasons(is_password=None)
    assert "focused_field_unreadable" in reasons


def test_a_password_field_is_a_reason_to_refuse():
    assert "focused_field_is_password" in sensitive_field_reasons(is_password=True)


@pytest.mark.parametrize(
    "label",
    [
        "Password",
        "Confirm password",
        "PIN",
        "Enter your PIN",
        "One-time code",
        "CVV",
        "Card number",
        "Security code",
        "API key",
        "Recovery phrase",
        "Social security number",
        "IBAN",
        "Contraseña",
    ],
)
def test_a_sensitive_looking_field_label_is_a_reason_to_refuse(label):
    reasons = sensitive_field_reasons(is_password=False, name=label)
    assert reasons, f"{label!r} was not recognised as sensitive"


@pytest.mark.parametrize("label", ["Search", "Message body", "Notes", "Shipping address"])
def test_an_ordinary_field_label_is_not_refused(label):
    assert sensitive_field_reasons(is_password=False, name=label) == ()


@pytest.mark.parametrize(
    "control",
    [
        "Send",
        "Submit",
        "Confirm",
        "Purchase",
        "Buy now",
        "Pay",
        "Place order",
        "Delete",
        "Remove",
        "Transfer",
        "Withdraw",
        "Publish",
        "Post",
        "Install",
        "Authorize",
        "Sign in",
    ],
)
def test_a_final_action_control_is_recognised(control):
    assert final_action_reason(control) is not None


@pytest.mark.parametrize("control", ["Details", "More options", "Advanced settings"])
def test_an_ordinary_control_is_not_a_final_action(control):
    assert final_action_reason(control) is None


def test_secret_categories_are_stable_labels_not_prose():
    categories = secret_categories(
        _synthetic("AKIA", "IOSFODNN", "7EXAMPLE") + " and " + _synthetic("gh", "p_") + "a" * 36,
    )
    assert categories
    assert all(re.fullmatch(r"[a-z0-9_]+", c) for c in categories)
