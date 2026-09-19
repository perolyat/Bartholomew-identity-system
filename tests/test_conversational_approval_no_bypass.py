"""R-EXEC02-2: conversation reaches the approval authority, and reaches
nothing else.

The structural companion to `tests/test_conversational_approval.py`, in the
mould `tests/test_w03b_no_bypass.py` set for the Executive package: these tests
read the modules' own syntax trees rather than importing them and inspecting
what they happen to expose, because an import-time check can be defeated by a
lazy import and a docstring can be defeated by an edit.

Three properties, each of which would be a real defect if lost:

1. The conversational-approval seam reaches a machine through the action
   envelope's *approval and cancel* functions and through nothing else. No
   dispatch, no lease, no result recording, no `windows_actuation`, no
   subprocess, no second HTTP client.
2. The recogniser is genuinely pure: it consults no store, no clock and no
   model, so nothing outside the person's own words can influence whether an
   utterance is read as permission.
3. `bartholomew/executive/` still never mints an approval --- the invariant
   W03-B established and this package must not have quietly widened. That one
   is enforced by `test_w03b_no_bypass.py`; it is re-asserted here from the
   other direction, that the only callers of `grant_action_approval` in the
   whole repository are the ones that should be.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ADAPTER = REPO / "bartholomew" / "integration" / "conversational_approval.py"
RECOGNISER = REPO / "bartholomew" / "kernel" / "approval_intents.py"

#: Imports that would give this seam its own route to a machine.
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
    "socket",
    "http",
    "urllib",
    "requests",
    "httpx",
    "aiohttp",
)

#: Envelope functions that dispatch, lease, abort or record a result. Every one
#: of them is somebody else's to call: a device leases, a device reports, and
#: the Executive advances. A conversational surface that could call any of them
#: would be a chat-to-execution path, which is exactly what must not exist.
FORBIDDEN_ENVELOPE_FUNCTIONS = frozenset(
    {
        "run_action_dispatch_through_runtime_contract",
        "record_action_result_through_runtime_contract",
        "evaluate_action_abort_through_runtime_contract",
        "evaluate_dispatch_admission",
        "try_lease",
        "dispatchable_action_ids",
        "mark_approved",
        "mark_refused",
        "mark_cancelled",
        "mark_aborted_by_brake",
        "record_result",
        "build_approval",
    },
)

#: The two the adapter *is* allowed, and must actually use --- otherwise the
#: test above is vacuous and the seam is not routing anything anywhere.
REQUIRED_AUTHORITY_CALLS = frozenset(
    {
        "grant_action_approval",
        "cancel_action_through_runtime_contract",
    },
)

#: Anything that makes the recogniser's answer depend on something other than
#: the string it was handed.
IMPURE_FOR_A_RECOGNISER = (
    "sqlite3",
    "aiosqlite",
    "datetime",
    "time",
    "os",
    "random",
    "asyncio",
    "bartholomew.kernel.memory_store",
    "bartholomew.actuation",
    "bartholomew.integration",
    "bartholomew.orchestrator",
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_names(tree: ast.Module) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _called_names(tree: ast.Module) -> list[str]:
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if name:
                names.append(name)
    return names


def _dynamic_import_arguments(tree: ast.Module) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        if name not in ("import_module", "__import__"):
            continue
        found.extend(
            a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)
        )
    return found


# ---------------------------------------------------------------------------
# 1. There is no chat-to-execution path
# ---------------------------------------------------------------------------


def test_the_adapter_imports_nothing_that_reaches_a_machine_directly():
    tree = _tree(ADAPTER)
    offences = [
        f"imports {name}"
        for name in _imported_names(tree) + _dynamic_import_arguments(tree)
        for forbidden in FORBIDDEN_IMPORT_PREFIXES
        if name == forbidden or name.startswith(forbidden + ".")
    ]
    assert not offences, (
        "the conversational approval seam must reach a machine only through the "
        "action envelope: " + "; ".join(offences)
    )


def test_the_adapter_cannot_dispatch_lease_or_record_a_result():
    tree = _tree(ADAPTER)
    offences = [
        f"calls {name}()" for name in _called_names(tree) if name in FORBIDDEN_ENVELOPE_FUNCTIONS
    ]
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            offences.extend(
                f"imports {alias.name}"
                for alias in node.names
                if alias.name in FORBIDDEN_ENVELOPE_FUNCTIONS
            )
    assert not offences, (
        "approving is not executing: the conversational seam may not dispatch, "
        "lease, abort, move an action's state or record a result: " + "; ".join(offences)
    )


def test_the_adapter_actually_routes_to_the_authority():
    """Non-vacuity. The permitted doors are used, so the test above is
    discriminating rather than trivially satisfied by an empty module."""
    called = set(_called_names(_tree(ADAPTER)))
    missing = REQUIRED_AUTHORITY_CALLS - called
    assert not missing, f"the seam does not reach the approval authority at all: {missing}"


def test_the_adapter_does_not_write_the_action_table_itself():
    """No SQL, and no state transition of its own. The action row's state is
    the authority's to move; a second writer would be a second authority."""
    source = ADAPTER.read_text(encoding="utf-8")
    for fragment in ("UPDATE windows_action", "INSERT INTO windows_action", "wal_db("):
        assert fragment not in source, f"the seam writes action state directly: {fragment!r}"


def test_the_only_action_store_function_it_touches_is_the_read_one():
    """It looks the action up; it never changes it.

    Attribute references rather than calls, because the adapter hands
    `action_store.get_action` to `run_off_loop` rather than calling it
    directly --- and a mutating function handed off the same way would be just
    as much a second writer.
    """
    referenced = {
        node.attr
        for node in ast.walk(_tree(ADAPTER))
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "action_store"
    }
    assert referenced == {"get_action"}, referenced


# ---------------------------------------------------------------------------
# 2. The recogniser is pure, so only the person's words decide
# ---------------------------------------------------------------------------


def test_the_recogniser_consults_nothing_but_the_string_it_was_given():
    tree = _tree(RECOGNISER)
    offences = [
        f"imports {name}"
        for name in _imported_names(tree) + _dynamic_import_arguments(tree)
        for forbidden in IMPURE_FOR_A_RECOGNISER
        if name == forbidden or name.startswith(forbidden + ".")
    ]
    assert not offences, (
        "whether a person granted permission must depend on their words and "
        "nothing else: " + "; ".join(offences)
    )


def test_no_model_is_consulted_about_whether_permission_was_granted():
    """Neither module may reach a model, a router or a deliberation port. If a
    generated inference could decide this, a persuasive page of text in a
    prompt would be an approval."""
    for path in (RECOGNISER, ADAPTER):
        source = path.read_text(encoding="utf-8").lower()
        called = set(_called_names(_tree(path)))
        assert "deliberate" not in called, path.name
        for fragment in ("modelrouter", "deliberationport", "respond_fn", "llm"):
            # Prose in a docstring is allowed to *discuss* models; a name
            # being called or imported is not.
            assert fragment not in {n.lower() for n in _imported_names(_tree(path))}, path.name
        assert "openai" not in source and "anthropic" not in source, path.name


# ---------------------------------------------------------------------------
# 3. Cognition still never authorises
# ---------------------------------------------------------------------------


def test_only_the_intended_callers_grant_an_approval_anywhere_in_the_tree():
    """The whole-repository form of the invariant. `grant_action_approval` is
    defined in the envelope and called by exactly three things: the operator
    HTTP route, this package's conversational adapter, and tests. Anything
    else appearing here is a new approval surface that nobody reviewed."""
    permitted = {
        "bartholomew/actuation/seam.py",
        "bartholomew/integration/conversational_approval.py",
        "bartholomew_api_bridge_v0_1/services/api/routes/operator.py",
        # The pre-existing device/action HTTP surface. Named rather than
        # excluded by a pattern, so a fourth caller appearing is a test change
        # somebody has to write and justify.
        "bartholomew_api_bridge_v0_1/services/api/routes/actions.py",
    }
    callers = set()
    for path in REPO.rglob("*.py"):
        rel = path.relative_to(REPO).as_posix()
        if rel.startswith(("tests/", "scripts/", ".venv/", "build/")):
            continue
        try:
            tree = _tree(path)
        except SyntaxError:  # pragma: no cover - not our file to parse
            continue
        if "grant_action_approval" in _called_names(tree):
            callers.add(rel)
    assert callers <= permitted, f"an unreviewed approval surface exists: {callers - permitted}"
    assert "bartholomew/integration/conversational_approval.py" in callers


def test_the_executive_package_still_mints_no_approval():
    """Re-asserted here so this package's own suite fails if a later change
    moves approval-granting into cognition."""
    package = REPO / "bartholomew" / "executive"
    offences = []
    for path in sorted(package.rglob("*.py")):
        tree = _tree(path)
        if "grant_action_approval" in _called_names(tree):
            offences.append(path.name)
    assert not offences, "cognition may propose; it never authorises: " + "; ".join(offences)


def test_the_chat_handler_does_not_reach_the_envelope_itself():
    """`runtime_contract.py` recognises and renders. Every governed call goes
    through the adapter, so there is one place to audit rather than two."""
    tree = _tree(REPO / "bartholomew" / "kernel" / "runtime_contract.py")
    called = set(_called_names(tree))
    assert "grant_action_approval" not in called
    assert "cancel_action_through_runtime_contract" not in called
    assert not (called & FORBIDDEN_ENVELOPE_FUNCTIONS)


def test_the_guard_is_not_vacuous(tmp_path):
    """The machinery catches a bypass when there is one to catch."""
    bypass = tmp_path / "bypass.py"
    bypass.write_text(
        "import subprocess\n"
        "from bartholomew.actuation.store import try_lease\n"
        "def go():\n"
        "    try_lease()\n"
        "    subprocess.run(['notepad'])\n",
        encoding="utf-8",
    )
    tree = _tree(bypass)
    names = _imported_names(tree)
    assert any(n.startswith("subprocess") for n in names)
    assert "try_lease" in set(_called_names(tree)) & FORBIDDEN_ENVELOPE_FUNCTIONS
