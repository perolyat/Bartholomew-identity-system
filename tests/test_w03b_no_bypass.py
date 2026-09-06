"""W03-B: the executive reaches the operating system through one door, or none.

`W03_TEST_CONTRACTS.md` §1 requires this as a **PR Fast structural test**: an
import/AST check that `bartholomew/executive/` reaches actuation only through
`bartholomew/actuation/seam.py` --- no `windows_actuation` import, no
`subprocess`, no input synthesis.

The tests read the package's own syntax tree. They do not import it and then
inspect what it happens to expose, because an import-time check can be defeated
by a lazy import and a docstring can be defeated by an edit. Every assertion
below fails if the property is removed, which is the non-vacuity standard the
contract sets: `test_the_guard_is_not_vacuous` proves the machinery catches a
bypass by running it over a module that has one.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "bartholomew" / "executive"

#: The one module in this repository through which the executive may reach a
#: machine. Named once, here, so a second permitted door would have to be added
#: to a test rather than merely to the package.
PERMITTED_ACTUATION_MODULE = "bartholomew.actuation.seam"

#: Modules the executive may import from the actuation package at all. `seam`
#: is the envelope; the other three are its value types --- a capability
#: vocabulary, an action's stored state and its result statuses --- none of
#: which can dispatch anything.
PERMITTED_ACTUATION_MODULES = frozenset(
    {
        "bartholomew.actuation.seam",
        "bartholomew.actuation.store",
        "bartholomew.actuation.capabilities",
        "bartholomew.actuation.parameters",
        "bartholomew.actuation.result",
        "bartholomew.actuation",
    },
)

#: Imports that would give the executive its own route to the machine. Each is
#: a real primitive somebody could reach for while making a step "just work".
FORBIDDEN_IMPORT_PREFIXES = (
    "bartholomew.windows_actuation",
    "subprocess",
    "ctypes",
    "win32api",
    "win32gui",
    "win32con",
    "pyautogui",
    "pynput",
    "keyboard",
    "mouse",
    "pywinauto",
    "comtypes",
    "webbrowser",
)

#: Call names that reach an operating system without importing anything
#: obviously actuation-shaped.
FORBIDDEN_CALLS = frozenset(
    {
        "system",
        "popen",
        "spawn",
        "spawnl",
        "spawnv",
        "execv",
        "execl",
        "startfile",
        "fork",
    },
)

#: The one function in the envelope this package must never call. An approval
#: is a human act at the host boundary; an executive that could grant one would
#: have collapsed authorization into proposing.
FORBIDDEN_ENVELOPE_FUNCTIONS = frozenset({"grant_action_approval"})


def _modules() -> list[tuple[Path, ast.Module]]:
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        found.append((path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))))
    assert found, "the executive package has no modules to check, which is itself a failure"
    return found


def _imported_names(tree: ast.Module) -> list[str]:
    """Every module name imported anywhere in the tree, function bodies included."""
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _dynamic_import_arguments(tree: ast.Module) -> list[str]:
    """String constants handed to `importlib.import_module` or `__import__`."""
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        name = getattr(target, "attr", None) or getattr(target, "id", None)
        if name not in ("import_module", "__import__"):
            continue
        for argument in node.args:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                found.append(argument.value)
    return found


def test_the_executive_imports_nothing_that_reaches_the_os_directly():
    offences = []
    for path, tree in _modules():
        for name in _imported_names(tree) + _dynamic_import_arguments(tree):
            for forbidden in FORBIDDEN_IMPORT_PREFIXES:
                if name == forbidden or name.startswith(forbidden + "."):
                    offences.append(f"{path.name} imports {name}")
    assert (
        not offences
    ), "the executive must reach a machine only through the action envelope; found: " + "; ".join(
        offences,
    )


def test_every_actuation_import_is_a_permitted_one():
    offences = []
    for path, tree in _modules():
        for name in _imported_names(tree):
            if not name.startswith("bartholomew.actuation"):
                continue
            # `from bartholomew.actuation import seam` yields both the package
            # and the dotted member; either form must name a permitted module.
            head = ".".join(name.split(".")[:3])
            if head not in PERMITTED_ACTUATION_MODULES:
                offences.append(f"{path.name} imports {name}")
    assert not offences, "unexpected actuation imports: " + "; ".join(offences)


def test_the_envelope_seam_is_actually_imported():
    """Non-vacuity: the permitted door is used, so the test above is discriminating."""
    seen = False
    for _path, tree in _modules():
        for name in _imported_names(tree):
            if name.startswith(PERMITTED_ACTUATION_MODULE) or name in (
                "bartholomew.actuation",
                "bartholomew.actuation.seam",
            ):
                seen = True
    assert seen, "the executive does not import the action envelope at all"


def test_no_os_level_execution_call_appears_anywhere():
    offences = []
    for path, tree in _modules():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if name in FORBIDDEN_CALLS:
                offences.append(f"{path.name} calls {name}()")
    assert not offences, "OS execution calls in the executive: " + "; ".join(offences)


def test_the_executive_never_grants_an_approval():
    """It proposes. Approving is a human act at the host boundary."""
    offences = []
    for path, tree in _modules():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                if name in FORBIDDEN_ENVELOPE_FUNCTIONS:
                    offences.append(f"{path.name} calls {name}()")
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in FORBIDDEN_ENVELOPE_FUNCTIONS:
                        offences.append(f"{path.name} imports {alias.name}")
    assert (
        not offences
    ), "the executive must never mint an approval for its own proposal: " + "; ".join(offences)


def test_the_executive_opens_no_socket_and_no_second_http_client():
    offences = []
    for path, tree in _modules():
        for name in _imported_names(tree):
            if name.split(".")[0] in ("socket", "http", "urllib", "requests", "httpx", "aiohttp"):
                offences.append(f"{path.name} imports {name}")
    assert not offences, (
        "the executive proposes in process; it does not reach the action surface over "
        "HTTP or open a socket of its own: " + "; ".join(offences)
    )


@pytest.mark.parametrize(
    "source,expected",
    [
        ("import subprocess\n", "subprocess"),
        ("from bartholomew.windows_actuation import channel\n", "bartholomew.windows_actuation"),
        ("import ctypes\n", "ctypes"),
        ("import importlib\nimportlib.import_module('subprocess')\n", "subprocess"),
    ],
)
def test_the_guard_is_not_vacuous(source, expected):
    """The machinery catches a bypass. A test that cannot fail is not coverage."""
    tree = ast.parse(source)
    names = _imported_names(tree) + _dynamic_import_arguments(tree)
    caught = [
        n
        for n in names
        for forbidden in FORBIDDEN_IMPORT_PREFIXES
        if n == forbidden or n.startswith(forbidden + ".")
    ]
    assert expected in caught


def test_the_no_bypass_check_would_notice_a_hidden_os_call():
    tree = ast.parse("import os\nos.system('calc')\n")
    calls = [getattr(n.func, "attr", None) for n in ast.walk(tree) if isinstance(n, ast.Call)]
    assert "system" in calls
    assert "system" in FORBIDDEN_CALLS
