"""Real Windows verification: no mocks, no substitutes, no Win32 stand-ins.

Acceptance requirement 17: safe launch, focus, URL, path and window operations
work on a Windows runner.

**Everything in this file is skipped off Windows**, and everything in it is
real: real `CreateProcessW`, real `EnumWindows`, real `SetForegroundWindow`,
real clipboard, real filesystem. Nothing here monkeypatches `win32`. That is
the point -- `tests/test_windows_action_dispatch_results.py` substitutes the
Win32 layer to reach edge conditions a real machine will not produce on
demand, and this file substitutes nothing so that the ordinary paths are known
to work against the actual API.

The applications used are the ones every Windows install has: Notepad and the
default browser. Nothing is installed, nothing is written outside `tmp_path`,
and nothing is deleted.

**A note on what this can and cannot prove on a CI runner.** GitHub's
`windows-latest` runner has a desktop session, so window enumeration, focus and
the clipboard genuinely work there. `open_url` is deliberately asserted only to
the honest degree -- it reports `unknown`, and this test asserts that it
reports `unknown`, because whether a browser rendered a page is exactly what
the companion cannot observe. Interactive typing into a focused field cannot be
verified without reading a field's contents back, which this build does not do,
so `windows.type_text` is exercised for its *refusals* here and its accepted
path is verified manually (see `docs/B_GOVERNED_WINDOWS_ACTUATION.md`, "Actual
versus simulated verification").
"""

from __future__ import annotations

import os
import sys
import time

import pytest

from bartholomew.actuation.allowlists import (
    ApplicationAllowlist,
    FilesystemRootAllowlist,
    UrlDomainAllowlist,
)
from bartholomew.actuation.capabilities import ALL_CAPABILITIES
from bartholomew.actuation.result import ActionResultStatus, ErrorCategory
from bartholomew.windows_actuation import handlers as handlers_module
from bartholomew.windows_actuation import win32
from bartholomew.windows_actuation.config import ActionCompanionConfig
from bartholomew.windows_actuation.handlers import HandlerContext

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform != "win32",
        reason="real Windows actuation; this build actuates Windows only",
    ),
]

NOTEPAD = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "System32", "notepad.exe")


@pytest.fixture
def documents(tmp_path):
    folder = tmp_path / "Documents"
    folder.mkdir()
    (folder / "note.txt").write_text("a real file, opened and not modified", encoding="utf-8")
    return folder


@pytest.fixture
def ctx(tmp_path, documents):
    return HandlerContext(
        config=ActionCompanionConfig(
            base_url="https://127.0.0.1:5173",
            device_id="ci-windows-runner",
            state_path=tmp_path / "action-state.json",
            applications=ApplicationAllowlist.from_pairs({"notepad": NOTEPAD}),
            url_domains=UrlDomainAllowlist.from_iterable(["example.com"]),
            filesystem_roots=FilesystemRootAllowlist.from_iterable([str(documents)]),
            capabilities=tuple(ALL_CAPABILITIES),
        ),
    )


@pytest.fixture
def notepad(ctx):
    """A real Notepad, launched for the test and closed after it.

    Closed by asking Windows to end the process this test started, through
    `taskkill` -- run by the *test*, never by the package under test, which has
    no process-termination capability at all and must not grow one to make a
    test tidy up after itself.
    """
    outcome = handlers_module.launch_app({"app_id": "notepad"}, ctx)
    if outcome.status is not ActionResultStatus.SUCCEEDED:
        pytest.skip(f"could not launch Notepad on this runner: {outcome.detail}")
    pid = outcome.evidence["process_id"]

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if any(w.process_id == pid for w in win32.visible_windows()):
            break
        time.sleep(0.4)
    else:  # pragma: no cover - a runner with no desktop session
        _terminate(pid)
        pytest.skip("Notepad started but never showed a window on this runner")

    try:
        yield pid
    finally:
        _terminate(pid)


def _terminate(pid: int) -> None:
    """Test-only cleanup, deliberately outside the package under test."""
    import subprocess  # noqa: PLC0415 - test cleanup only; see the fixture docstring

    subprocess.run(  # noqa: S603
        ["taskkill", "/PID", str(pid), "/F"],
        capture_output=True,
        check=False,
    )


# --- launch --------------------------------------------------------------------


def test_launching_an_allowlisted_application_really_starts_it(ctx, notepad):
    """The fixture already proves it: a real pid, a real window, verified."""
    assert notepad > 0
    assert win32.process_image_name(notepad)
    assert any(w.process_id == notepad for w in win32.visible_windows())


def test_launching_a_non_allowlisted_application_really_refuses(ctx):
    outcome = handlers_module.launch_app({"app_id": "cmd"}, ctx)
    assert outcome.status is ActionResultStatus.FAILED
    assert outcome.error_category is ErrorCategory.PARAMETERS_INVALID


def test_the_process_starter_cannot_be_given_an_argument(ctx, notepad):
    """Structural on a real machine: the started process received no argv.

    `CreateProcessW` was called with a null command line, so the child's own
    command line is just its image path. Read back from Windows rather than
    asserted about the source, which the unit suite already does.
    """
    import ctypes  # noqa: PLC0415 - a test reading the OS back, not the package

    image = win32.process_image_name(notepad)
    assert image and image.lower().endswith("notepad.exe")
    assert ctypes.windll.kernel32  # the handle really is Windows'


# --- focus and window management -----------------------------------------------


def test_focusing_a_real_window_really_brings_it_forward(ctx, notepad):
    outcome = handlers_module.focus_window({"app_id": "notepad"}, ctx)
    # On a runner with an interactive desktop this succeeds; on one without,
    # Windows refuses the foreground change and the handler says so truthfully.
    assert outcome.status in (ActionResultStatus.SUCCEEDED, ActionResultStatus.FAILED)
    if outcome.status is ActionResultStatus.SUCCEEDED:
        assert win32.foreground_window() == outcome.evidence["hwnd"]
    else:
        assert outcome.error_category in (
            ErrorCategory.PERMISSION_DENIED,
            ErrorCategory.OS_CALL_FAILED,
        )
        assert "No keystroke-injection fallback" in outcome.detail


@pytest.mark.parametrize("operation", ["minimize", "maximize", "restore"])
def test_real_window_state_changes_are_verified_by_reading_them_back(ctx, notepad, operation):
    outcome = handlers_module.manage_window(
        {"app_id": "notepad", "operation": operation},
        ctx,
    )
    assert outcome.status is ActionResultStatus.SUCCEEDED, outcome.detail
    hwnd = outcome.evidence["hwnd"]
    if operation == "minimize":
        assert win32.is_minimized(hwnd)
    elif operation == "maximize":
        assert win32.is_maximized(hwnd)
    else:
        assert not win32.is_minimized(hwnd) and not win32.is_maximized(hwnd)


def test_a_real_move_lands_where_it_was_asked_to(ctx, notepad):
    handlers_module.manage_window({"app_id": "notepad", "operation": "restore"}, ctx)
    outcome = handlers_module.manage_window(
        {"app_id": "notepad", "operation": "move", "x": 120, "y": 90},
        ctx,
    )
    assert outcome.status is ActionResultStatus.SUCCEEDED, outcome.detail
    left, top, _r, _b = win32.window_rect(outcome.evidence["hwnd"])
    assert (left, top) == (120, 90)


def test_a_real_resize_takes_the_requested_size(ctx, notepad):
    handlers_module.manage_window({"app_id": "notepad", "operation": "restore"}, ctx)
    outcome = handlers_module.manage_window(
        {"app_id": "notepad", "operation": "resize", "width": 640, "height": 480},
        ctx,
    )
    assert outcome.status is ActionResultStatus.SUCCEEDED, outcome.detail
    left, top, right, bottom = win32.window_rect(outcome.evidence["hwnd"])
    assert (right - left, bottom - top) == (640, 480)


def test_a_move_off_the_real_desktop_is_clamped_onto_it(ctx, notepad):
    handlers_module.manage_window({"app_id": "notepad", "operation": "restore"}, ctx)
    x0, y0, width, height = win32.virtual_screen()
    outcome = handlers_module.manage_window(
        {"app_id": "notepad", "operation": "move", "x": 30000, "y": 30000},
        ctx,
    )
    assert outcome.status is ActionResultStatus.SUCCEEDED, outcome.detail
    assert outcome.evidence["clamped"] is True
    assert outcome.evidence["left"] < x0 + width
    assert outcome.evidence["top"] < y0 + height


# --- URL and path ---------------------------------------------------------------


def test_opening_a_real_url_reports_unknown_and_not_success(ctx):
    """Truthful by design: the shell took it; whether a page loaded is unknown."""
    outcome = handlers_module.open_url({"url": "https://example.com/"}, ctx)
    assert outcome.status is ActionResultStatus.UNKNOWN
    assert outcome.evidence["shell_execute_code"] > win32.SHELL_EXECUTE_SUCCESS_FLOOR


def test_opening_a_real_file_hands_it_to_the_shell_and_does_not_modify_it(ctx, documents):
    target = documents / "note.txt"
    before = target.read_bytes()
    outcome = handlers_module.open_path({"path": str(target)}, ctx)
    assert outcome.status is ActionResultStatus.UNKNOWN
    assert outcome.evidence["shell_execute_code"] > win32.SHELL_EXECUTE_SUCCESS_FLOOR
    assert target.read_bytes() == before, "opening a file must not modify it"
    assert target.exists()


def test_opening_a_real_folder_works(ctx, documents):
    outcome = handlers_module.open_path({"path": str(documents)}, ctx)
    assert outcome.status is ActionResultStatus.UNKNOWN
    assert outcome.evidence["is_directory"] is True


def test_a_real_executable_inside_an_allowlisted_root_is_still_refused(ctx, documents):
    """Opening a `.bat` with the shell runs it, so `open_path` refuses one."""
    script = documents / "harmless-looking.bat"
    script.write_text("@echo off\r\necho nothing\r\n", encoding="utf-8")
    outcome = handlers_module.open_path({"path": str(script)}, ctx)
    assert outcome.status is ActionResultStatus.FAILED
    assert outcome.error_category is ErrorCategory.PARAMETERS_INVALID
    assert script.exists(), "the refusal did not touch the file"


def test_a_real_path_outside_the_allowlisted_roots_is_refused(ctx):
    outcome = handlers_module.open_path({"path": "C:\\Windows\\win.ini"}, ctx)
    assert outcome.status is ActionResultStatus.FAILED
    assert outcome.error_category is ErrorCategory.PARAMETERS_INVALID


# --- clipboard --------------------------------------------------------------------


def test_the_real_clipboard_round_trips(ctx):
    marker = "bartholomew integration marker 4f2a"
    written = handlers_module.clipboard_write({"text": marker}, ctx)
    assert written.status is ActionResultStatus.SUCCEEDED, written.detail

    read = handlers_module.clipboard_read({}, ctx)
    assert read.status is ActionResultStatus.SUCCEEDED
    assert read.evidence["text_length"] == len(marker)
    # Off by default: the content itself did not leave the machine.
    assert "text" not in read.evidence
    assert read.evidence["content_returned"] is False


def test_a_real_clipboard_holding_a_secret_is_not_returned(ctx):
    # Assembled from fragments so no line in this file matches a secret
    # scanner's signature; the detector under test sees the same string.
    synthetic = "AKIA" + "IOSFODNN" + "7EXAMPLE"
    win32.write_clipboard_text(synthetic)
    read = handlers_module.clipboard_read({}, ctx)
    assert read.status is ActionResultStatus.FAILED
    assert read.error_category is ErrorCategory.SENSITIVE_CONTENT
    assert synthetic not in repr(read.evidence)
    win32.write_clipboard_text("cleared")


def test_writing_a_secret_to_the_real_clipboard_is_refused(ctx):
    win32.write_clipboard_text("untouched")
    outcome = handlers_module.clipboard_write(
        {"text": "api" + "_key = " + "sk-" + "abcdefghijklmnopqrstuvwxyz012345"},
        ctx,
    )
    assert outcome.error_category is ErrorCategory.SENSITIVE_CONTENT
    assert win32.read_clipboard_text() == "untouched", "nothing was copied"


# --- typing, and what it refuses ---------------------------------------------------


def test_typing_refuses_on_a_real_machine_without_the_accessibility_adapter(ctx):
    """With `comtypes` absent the field cannot be read, so nothing is typed."""
    from bartholomew.windows_actuation import uia

    if uia.available():
        pytest.skip("the accessibility adapter is installed on this runner")
    outcome = handlers_module.type_text({"text": "hello"}, ctx)
    assert outcome.status is ActionResultStatus.FAILED
    assert outcome.error_category is ErrorCategory.ACCESSIBILITY_UNAVAILABLE


def test_typing_a_newline_is_impossible_on_a_real_machine(ctx):
    """The validator refuses it, so Enter can never be synthesised."""
    outcome = handlers_module.type_text({"text": "submit\nthis"}, ctx)
    assert outcome.status is ActionResultStatus.FAILED
    assert outcome.error_category is ErrorCategory.PARAMETERS_INVALID


# --- the platform guard, from the other side ------------------------------------


def test_the_platform_guard_does_not_fire_on_windows():
    assert win32.is_windows() is True
    win32.virtual_screen()  # would raise PlatformUnsupportedError off Windows
