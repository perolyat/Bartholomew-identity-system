"""The entire operating-system surface of Bartholomew's actuation. One file.

Every Win32 call this system can make is named below, once, in a function with
a fixed signature. Nothing else in the repository touches `ctypes`, `windll` or
a Windows API, and `tests/test_windows_action_prohibitions.py` asserts that --
so auditing what Bartholomew can do to a computer means reading this file, not
searching the tree.

Three properties are load-bearing and are asserted rather than described:

1. **`start_process()` takes exactly one parameter.** It calls `CreateProcessW`
   with `lpApplicationName` set to an allowlisted absolute path and
   `lpCommandLine` set to `NULL`. There is no parameter for arguments, no
   parameter for a working directory, no parameter for an environment, and no
   shell involved -- so "run this program with these arguments" is not a thing
   this function can be asked to do. `CreateProcessW` rather than
   `ShellExecuteW` for exactly that reason: `ShellExecuteW` resolves a verb
   through the registry and accepts a parameter string, and `CreateProcessW`
   with a null command line does neither.

2. **`shell_open()` takes exactly one parameter.** It calls `ShellExecuteW`
   with the verb `"open"` and a null parameter string, for a URL or a document
   path that the caller has already validated. Its one argument is the thing
   to open; there is nowhere to put an argument for it.

3. **`send_unicode_text()` synthesises characters, never keys.** It uses
   `SendInput` with `KEYEVENTF_UNICODE`, which delivers a character by its
   code point and carries no virtual-key code at all. There is no path in this
   file that can press Enter, Tab, a function key, a modifier, or a mouse
   button: `_key_event()` is not a function that exists, and the caller has
   already refused any control character.

Off Windows every entry point raises `PlatformUnsupportedError`. That is the
honest answer -- this build actuates Windows only -- and it is raised rather
than returned so no caller can mistake it for a failed action.
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes  # noqa: F401 - resolved lazily; see _libs()
from dataclasses import dataclass
from typing import Any

from bartholomew.actuation.parameters import MAX_CLIPBOARD_CHARS as _MAX_CLIPBOARD_CHARS

#: `ShellExecuteW` returns a value greater than 32 on success. Anything at or
#: below 32 is one of its documented error codes.
SHELL_EXECUTE_SUCCESS_FLOOR = 32

#: `ShowWindow` commands. Five, matching the five window operations.
SW_HIDE = 0
SW_SHOWNORMAL = 1
SW_SHOWMINIMIZED = 2
SW_MAXIMIZE = 3
SW_SHOWNOACTIVATE = 4
SW_MINIMIZE = 6
SW_RESTORE = 9

#: `SetWindowPos` flags. `SWP_NOZORDER | SWP_NOACTIVATE` on every call: moving
#: a window must not also raise it above others or steal focus.
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010

#: `GetSystemMetrics` indices for the virtual desktop's bounding box.
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

#: Query-only process access. Deliberately not `PROCESS_ALL_ACCESS` and
#: nothing that would permit writing to, or terminating, a process.
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

#: `WaitForSingleObject` returned this means the process is still running,
#: which is the observation `start_process` needs to report a real success.
WAIT_TIMEOUT = 0x00000102

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

INPUT_KEYBOARD = 1
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_KEYUP = 0x0002

#: `sizeof(INPUT)` as Win32 defines it: 40 bytes on x64, 28 on x86.
#:
#: Checked at the call site rather than trusted, because this is the one
#: structure whose layout cannot be verified anywhere but on Windows --
#: `ctypes.wintypes.DWORD` is 32 bits on Windows and 64 on Linux, so the CI
#: that runs everything else cannot compute it. `SendInput` validates `cbSize`
#: and returns 0 for a mismatch, which surfaces as "no keystrokes were
#: accepted" with no indication why; the explicit check below turns that into
#: a message naming the actual problem.
INPUT_STRUCT_SIZE = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28

#: Longest window title this module will read. A title is used to tell two
#: windows of the same application apart, not to be reported anywhere.
MAX_TITLE_CHARS = 256

#: Longest clipboard read this module will return. The clipboard is untrusted
#: input from every program on the machine, so the read is bounded here as well
#: as validated by the caller. The same bound `parameters.py` applies to a
#: clipboard *write*, imported rather than restated so the two cannot drift.
MAX_CLIPBOARD_CHARS = _MAX_CLIPBOARD_CHARS


class PlatformUnsupportedError(RuntimeError):
    """This build actuates Windows, and this is not Windows."""


class Win32CallError(RuntimeError):
    """A Win32 call failed. Carries the OS error code for the audit row."""

    def __init__(self, call: str, code: int | None = None):
        super().__init__(f"{call} failed" + (f" (error {code})" if code is not None else ""))
        self.call = call
        self.code = code


def _last_error() -> int:
    """The OS error from the most recent foreign call on this thread.

    `ctypes.get_last_error()` rather than `kernel32.GetLastError()`: the latter
    is itself a call, so anything between the failure and it -- a `sleep`, a
    `CloseHandle`, another binding -- overwrites the value first. With
    `use_last_error=True` on the library handles, ctypes captures the error the
    instant each call returns, and this reads that capture.
    """
    return int(ctypes.get_last_error())


def is_windows() -> bool:
    return sys.platform == "win32"


def _require_windows() -> None:
    if not is_windows():
        raise PlatformUnsupportedError(
            "Bartholomew's actuation targets Windows only. There is no macOS, Linux, "
            "Android or iOS actuation in this build, and this is not a gap to be "
            "filled by falling back to something else.",
        )


#: `ULONG_PTR`, which `ctypes.wintypes` does not define. Pointer-sized, so 8
#: bytes on x64 and 4 on x86 -- and getting that wrong silently corrupts every
#: structure that contains one.
_ULONG_PTR = ctypes.c_size_t


class _Libs:
    """The three library handles this module uses, with every prototype declared.

    Held on an instance rather than as module globals so that importing this
    module on a non-Windows machine -- which every test run on CI does -- costs
    nothing and touches no OS API.

    **Every function gets an explicit `restype`, and that is load-bearing.**
    `ctypes` defaults an undeclared return type to `c_int`, which is 32 bits.
    On 64-bit Windows a window handle, a process handle, a global memory handle
    and a locked pointer are all 64 bits, so an undeclared return truncates them
    -- and a truncated handle does not raise, it silently becomes a different
    handle or a null one. That failure mode is invisible on the Linux CI, where
    none of this runs, so the prototypes below are the only thing standing
    between "the tests pass" and "focus verification always fails on a real
    machine because `GetForegroundWindow()` came back with its top 32 bits
    missing".

    `argtypes` are declared for the same reason and one more: with them, passing
    a Python `int` where a `HANDLE` belongs is converted correctly rather than
    marshalled as a 32-bit value.
    """

    def __init__(self) -> None:
        _require_windows()
        # `windll` exists only on Windows, which `_require_windows` has just
        # established. Three handles, and no mechanism for looking up a fourth.
        # `use_last_error=True` is what makes the error codes in the audit
        # trail mean anything. Without it, `GetLastError()` reads the thread's
        # last error *now* -- and any intervening call overwrites it, so the
        # retry loop in `_Clipboard.__enter__` reported whatever `Sleep` left
        # behind rather than why `OpenClipboard` failed. With it, ctypes saves
        # the error immediately after each foreign call and
        # `ctypes.get_last_error()` returns that one.
        #
        # It matters behaviourally as well as for the audit: `launch_app`
        # chooses `PERMISSION_DENIED` over `OS_CALL_FAILED` from the code.
        self.user32: Any = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
        self.kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        self.shell32: Any = ctypes.WinDLL("shell32", use_last_error=True)  # type: ignore[attr-defined]
        self._declare()

    def _declare(self) -> None:  # pragma: no cover - Windows only
        """Declare every prototype this module uses. Nothing else is called."""
        k, u, sh = self.kernel32, self.user32, self.shell32

        # --- kernel32 ---------------------------------------------------
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL
        k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k.WaitForSingleObject.restype = wintypes.DWORD
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        k.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        k.GlobalAlloc.restype = wintypes.HGLOBAL
        k.GlobalLock.argtypes = [wintypes.HGLOBAL]
        k.GlobalLock.restype = wintypes.LPVOID
        k.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        k.GlobalUnlock.restype = wintypes.BOOL
        k.GlobalSize.argtypes = [wintypes.HGLOBAL]
        k.GlobalSize.restype = ctypes.c_size_t
        k.GlobalFree.argtypes = [wintypes.HGLOBAL]
        k.GlobalFree.restype = wintypes.HGLOBAL
        # CreateProcessW's argtypes are declared at its call site, where the
        # two structures it needs are defined.

        # --- user32: windows --------------------------------------------
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.IsWindowVisible.restype = wintypes.BOOL
        u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        u.GetWindowTextLengthW.restype = ctypes.c_int
        u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetWindowTextW.restype = ctypes.c_int
        u.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        u.GetForegroundWindow.argtypes = []
        u.GetForegroundWindow.restype = wintypes.HWND
        u.SetForegroundWindow.argtypes = [wintypes.HWND]
        u.SetForegroundWindow.restype = wintypes.BOOL
        u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        u.ShowWindow.restype = wintypes.BOOL
        u.IsIconic.argtypes = [wintypes.HWND]
        u.IsIconic.restype = wintypes.BOOL
        u.IsZoomed.argtypes = [wintypes.HWND]
        u.IsZoomed.restype = wintypes.BOOL
        u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u.GetWindowRect.restype = wintypes.BOOL
        u.GetSystemMetrics.argtypes = [ctypes.c_int]
        u.GetSystemMetrics.restype = ctypes.c_int
        u.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        u.SetWindowPos.restype = wintypes.BOOL
        # EnumWindows' argtypes are declared at its call site, with the
        # callback type it needs.

        # --- user32: clipboard -------------------------------------------
        u.OpenClipboard.argtypes = [wintypes.HWND]
        u.OpenClipboard.restype = wintypes.BOOL
        u.CloseClipboard.argtypes = []
        u.CloseClipboard.restype = wintypes.BOOL
        u.EmptyClipboard.argtypes = []
        u.EmptyClipboard.restype = wintypes.BOOL
        u.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
        u.IsClipboardFormatAvailable.restype = wintypes.BOOL
        u.GetClipboardData.argtypes = [wintypes.UINT]
        u.GetClipboardData.restype = wintypes.HANDLE
        u.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        u.SetClipboardData.restype = wintypes.HANDLE

        # --- shell32 -------------------------------------------------------
        sh.ShellExecuteW.argtypes = [
            wintypes.HWND,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            ctypes.c_int,
        ]
        sh.ShellExecuteW.restype = wintypes.HINSTANCE


#: The resolved library handles, in a holder rather than a bare module global
#: so that resolution mutates a container instead of rebinding a module
#: attribute -- the same shape `governance_store._HALT_AUTHORITY` uses.
_HANDLES: dict[str, _Libs | None] = {"libs": None}


def _lib() -> _Libs:
    libs = _HANDLES["libs"]
    if libs is None:
        libs = _Libs()
        _HANDLES["libs"] = libs
    return libs


# ---------------------------------------------------------------------------
# Starting a program: exactly one parameter, and it is a path from an allowlist
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StartedProcess:
    """What was observed after asking Windows to start a program."""

    process_id: int
    #: True when the process was still alive a moment after it was created.
    #: This is the observation that lets a handler claim success rather than
    #: merely claiming that a call returned.
    running: bool


def start_process(executable_path: str) -> StartedProcess:
    """Start one program. One parameter, and it is the whole interface.

    `CreateProcessW(lpApplicationName=executable_path, lpCommandLine=NULL, ...)`.
    A null command line means the new process receives no arguments at all --
    not an empty string that a program might parse, but nothing. There is no
    shell, no verb resolution, no `cmd /c`, no `powershell -Command`, and no
    second parameter this function could be given even by a caller that wanted
    to.

    The caller must already have resolved `executable_path` through the
    application allowlist; this function does not know what an allowlist is,
    which is why it must never be called with anything else.
    """
    _require_windows()
    lib = _lib()

    class _StartupInfoW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class _ProcessInformation(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    startup = _StartupInfoW()
    startup.cb = ctypes.sizeof(startup)
    info = _ProcessInformation()

    lib.kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPCWSTR,
        ctypes.POINTER(_StartupInfoW),
        ctypes.POINTER(_ProcessInformation),
    ]
    lib.kernel32.CreateProcessW.restype = wintypes.BOOL

    created = lib.kernel32.CreateProcessW(
        executable_path,
        # lpCommandLine. None, always. This is the argument vector, and there
        # is no parameter of this function that could reach it.
        None,
        None,  # lpProcessAttributes
        None,  # lpThreadAttributes
        False,  # bInheritHandles: the new process inherits none of our handles
        0,  # dwCreationFlags
        None,  # lpEnvironment: inherit, never construct
        None,  # lpCurrentDirectory: inherit, never choose
        ctypes.byref(startup),
        ctypes.byref(info),
    )
    if not created:
        raise Win32CallError("CreateProcessW", _last_error())

    try:
        # The observation: is it still alive a beat later? A process that
        # exited immediately did not start in any sense the person meant.
        status = lib.kernel32.WaitForSingleObject(info.hProcess, 250)
        running = status == WAIT_TIMEOUT
        return StartedProcess(process_id=int(info.dwProcessId), running=running)
    finally:
        lib.kernel32.CloseHandle(info.hThread)
        lib.kernel32.CloseHandle(info.hProcess)


def shell_open(target: str) -> int:
    """Open one URL or document with its registered handler. One parameter.

    `ShellExecuteW(NULL, "open", target, NULL, NULL, SW_SHOWNORMAL)`. The
    fourth argument -- `lpParameters`, the argument string -- is `NULL` and
    there is no parameter of this function that could set it.

    The caller must already have established that `target` is either an
    http/https URL from an allowlisted domain or a non-executable document
    inside an allowlisted root. `ShellExecuteW` will happily run a `.bat`, so
    the validation in `bartholomew/actuation/parameters.py` is not a nicety.
    """
    _require_windows()
    lib = _lib()
    result = lib.shell32.ShellExecuteW(
        None,
        "open",
        target,
        None,  # lpParameters: no arguments, ever
        None,  # lpDirectory: inherit
        SW_SHOWNORMAL,
    )
    # `HINSTANCE` is pointer-sized, and the documented return is a small integer
    # smuggled through it. Read the pointer's value rather than the pointer.
    code = int(ctypes.cast(result, ctypes.c_void_p).value or 0)
    if code <= SHELL_EXECUTE_SUCCESS_FLOOR:
        raise Win32CallError("ShellExecuteW", code)
    return code


def process_image_name(process_id: int) -> str | None:
    """The full image path of a running process, or None. Query-only access."""
    _require_windows()
    lib = _lib()
    handle = lib.kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        int(process_id),
    )
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if not lib.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        return buf.value or None
    finally:
        lib.kernel32.CloseHandle(handle)


# ---------------------------------------------------------------------------
# Windows: enumerate, focus, show, move, resize. All bounded.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WindowInfo:
    """One visible top-level window, as this module sees it."""

    hwnd: int
    process_id: int
    title: str
    image_path: str | None


def visible_windows() -> list[WindowInfo]:
    """Every visible top-level window with a title. Read-only enumeration."""
    _require_windows()
    lib = _lib()
    found: list[WindowInfo] = []

    enum_proc = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HWND,
        wintypes.LPARAM,
    )
    lib.user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    lib.user32.EnumWindows.restype = wintypes.BOOL

    def _callback(hwnd, _lparam):  # pragma: no cover - Windows only
        if not lib.user32.IsWindowVisible(hwnd):
            return True
        length = lib.user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(min(length + 1, MAX_TITLE_CHARS))
        lib.user32.GetWindowTextW(hwnd, buf, len(buf))
        pid = wintypes.DWORD(0)
        lib.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return True
        found.append(
            WindowInfo(
                hwnd=int(hwnd),
                process_id=int(pid.value),
                title=buf.value,
                image_path=process_image_name(int(pid.value)),
            ),
        )
        return True

    # Held in a local, not passed inline: a temporary callback object can be
    # collected while Windows is still calling it, which crashes the process
    # somewhere unrelated and at random.
    callback = enum_proc(_callback)
    if not lib.user32.EnumWindows(callback, 0):
        raise Win32CallError("EnumWindows", _last_error())
    return found


def foreground_window() -> int:
    """The handle of the window that currently has focus. 0 if there is none.

    This is the read-back that makes a focus claim honest: a handler asks for
    focus, then asks this, then reports what it actually got.
    """
    _require_windows()
    return int(_lib().user32.GetForegroundWindow() or 0)


def set_foreground(hwnd: int) -> bool:
    """Ask Windows to bring one window to the front. Returns what it said.

    **There is deliberately no fallback here.** Windows refuses
    `SetForegroundWindow` from a process that does not currently own the
    foreground, and the well-known workarounds -- attaching to another
    thread's input queue, or synthesising an ALT keystroke to unlock the
    foreground lock -- are input synthesis aimed at a window nobody approved.
    When Windows says no, the handler reports that it did not get focus.
    """
    _require_windows()
    return bool(_lib().user32.SetForegroundWindow(int(hwnd)))


def show_window(hwnd: int, command: int) -> bool:
    """`ShowWindow`, restricted to the four state commands this build uses."""
    _require_windows()
    if command not in (SW_MINIMIZE, SW_MAXIMIZE, SW_RESTORE, SW_SHOWNORMAL):
        raise Win32CallError(f"ShowWindow(command={command})")
    return bool(_lib().user32.ShowWindow(int(hwnd), int(command)))


def is_minimized(hwnd: int) -> bool:
    _require_windows()
    return bool(_lib().user32.IsIconic(int(hwnd)))


def is_maximized(hwnd: int) -> bool:
    _require_windows()
    return bool(_lib().user32.IsZoomed(int(hwnd)))


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """`(left, top, right, bottom)` in virtual-screen coordinates."""
    _require_windows()
    rect = wintypes.RECT()
    if not _lib().user32.GetWindowRect(int(hwnd), ctypes.byref(rect)):
        raise Win32CallError("GetWindowRect", _last_error())
    return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)


def virtual_screen() -> tuple[int, int, int, int]:
    """`(x, y, width, height)` of the whole desktop across every monitor.

    The bound a move or resize is clamped against, so a window cannot be sent
    somewhere the person cannot see it and then left there.
    """
    _require_windows()
    lib = _lib()
    return (
        int(lib.user32.GetSystemMetrics(SM_XVIRTUALSCREEN)),
        int(lib.user32.GetSystemMetrics(SM_YVIRTUALSCREEN)),
        int(lib.user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)),
        int(lib.user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)),
    )


def move_window(hwnd: int, x: int, y: int) -> bool:
    """Move a window without resizing, raising or focusing it."""
    _require_windows()
    return bool(
        _lib().user32.SetWindowPos(
            int(hwnd),
            None,
            int(x),
            int(y),
            0,
            0,
            SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE,
        ),
    )


def resize_window(hwnd: int, width: int, height: int) -> bool:
    """Resize a window without moving, raising or focusing it."""
    _require_windows()
    return bool(
        _lib().user32.SetWindowPos(
            int(hwnd),
            None,
            0,
            0,
            int(width),
            int(height),
            SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE,
        ),
    )


# ---------------------------------------------------------------------------
# Clipboard
# ---------------------------------------------------------------------------


class _Clipboard:
    """`OpenClipboard`/`CloseClipboard` as a context manager, so it always closes.

    A clipboard left open by a crashed handler blocks every other application
    on the machine from using it, which is a far worse failure than the action
    not happening.

    **A documented caveat, and the evidence against it.** `OpenClipboard(NULL)`
    associates the clipboard with this thread rather than a window, and
    `EmptyClipboard`'s documentation says that leaves the owner NULL, "which
    causes `SetClipboardData` to fail". Some clipboard libraries create a
    throwaway window to avoid it. This build does not, because
    `tests/integration/test_windows_action_real.py::test_the_real_clipboard_round_trips`
    performs exactly this sequence against a real Windows runner and the text
    round-trips -- documentation and observed behaviour disagree here, and the
    test is the stronger evidence for the platform we actually run on. It is
    also the guard: if a future Windows build behaves as the documentation
    describes, that test goes red rather than the capability silently failing
    for a person.
    """

    def __init__(self, retries: int = 5):
        self._retries = retries

    def __enter__(self) -> Any:
        _require_windows()
        lib = _lib()
        for attempt in range(self._retries):
            if lib.user32.OpenClipboard(None):
                return lib
            time.sleep(0.05 * (attempt + 1))
        raise Win32CallError("OpenClipboard", _last_error())

    def __exit__(self, *_exc: object) -> None:
        _lib().user32.CloseClipboard()


def read_clipboard_text() -> str | None:
    """The clipboard's current text, or None when it holds no text.

    None is a real answer -- the clipboard may hold an image, a file list, or
    nothing -- and is reported as such rather than as an empty string, which
    would be indistinguishable from "the clipboard is empty text".
    """
    _require_windows()
    with _Clipboard() as lib:
        if not lib.user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        handle = lib.user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        pointer = lib.kernel32.GlobalLock(handle)
        if not pointer:
            return None
        try:
            # `GlobalLock` returns an address. `c_wchar_p(address)` is a type
            # error; casting the address to `c_wchar_p` is what reads the
            # string at it.
            text = ctypes.cast(pointer, ctypes.c_wchar_p).value
        finally:
            lib.kernel32.GlobalUnlock(handle)
        if text is None:
            return None
        # Bounded by the caller's own limit rather than by whatever another
        # application put on the clipboard: this is untrusted input from every
        # program on the machine.
        return text[:MAX_CLIPBOARD_CHARS]


def write_clipboard_text(text: str) -> bool:
    """Replace the clipboard with `text`. Returns whether Windows accepted it.

    The caller reads it back afterwards; this returning True is a call
    succeeding, not an effect observed.
    """
    _require_windows()
    encoded = str(text)
    # Sized from the buffer itself, never from `len(text)`. `len()` counts code
    # points; `c_wchar` is 2 bytes on Windows and an astral character needs two
    # of them, so `(len + 1) * 2` under-allocates for any such character and
    # `memmove` would then copy the surrogate pair while dropping the NUL
    # terminator -- publishing an unterminated block that every application on
    # the machine reads past, into whatever heap memory follows.
    #
    # `_ordinary_text` refuses astral characters upstream, so this is currently
    # unreachable. It is written correctly anyway: "unreachable" is a property
    # of today's validator, and a buffer overrun is not the thing to leave
    # depending on one.
    buffer = ctypes.create_unicode_buffer(encoded)
    size = ctypes.sizeof(buffer)
    with _Clipboard() as lib:
        if not lib.user32.EmptyClipboard():
            raise Win32CallError("EmptyClipboard", _last_error())
        handle = lib.kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not handle:
            raise Win32CallError("GlobalAlloc", _last_error())
        # Freed on every path that does not hand it to the clipboard.
        # Ownership transfers to the system only when `SetClipboardData`
        # succeeds, so without this a failed write leaked its buffer -- and a
        # companion that polls forever leaks it on every attempt.
        owned = True
        try:
            pointer = lib.kernel32.GlobalLock(handle)
            if not pointer:
                raise Win32CallError("GlobalLock", _last_error())
            try:
                ctypes.memmove(pointer, buffer, size)
            finally:
                lib.kernel32.GlobalUnlock(handle)
            if not lib.user32.SetClipboardData(CF_UNICODETEXT, handle):
                raise Win32CallError("SetClipboardData", _last_error())
            # The clipboard owns the memory now; freeing it would be a
            # double free.
            owned = False
        finally:
            if owned:
                lib.kernel32.GlobalFree(handle)
        return True


# ---------------------------------------------------------------------------
# Typing: characters, never keys
# ---------------------------------------------------------------------------


def send_unicode_text(text: str) -> int:
    """Type `text` as a sequence of characters. Returns how many events landed.

    Every event is `KEYEVENTF_UNICODE` with `wVk = 0`: a character delivered by
    code point, carrying no virtual-key code. That is why this function cannot
    press Enter, Tab, Escape, a function key, a modifier or a mouse button --
    not "does not", *cannot*: there is no field in what it sends that names a
    key. The caller has separately refused every control character, so a
    newline never reaches here either.

    There is no mouse function anywhere in this module. `mouse_event`,
    `SendInput` with `INPUT_MOUSE`, and every click, drag and scroll are
    absent, and `tests/test_windows_action_prohibitions.py` asserts their
    absence over this file.
    """
    _require_windows()
    lib = _lib()

    class _KeyboardInput(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", _ULONG_PTR),
        ]

    class _UnusedUnionSlot(ctypes.Structure):
        """Padding to the real `INPUT` union's size. Never read, never written.

        `SendInput` validates its `cbSize` argument against the actual
        `sizeof(INPUT)` and returns 0 with `ERROR_INVALID_PARAMETER` if it does
        not match. The real union's largest member is `MOUSEINPUT`, which is 8
        bytes larger than `KEYBDINPUT` on x64 -- so a union declaring only the
        keyboard member makes **every** call fail, silently and on a real
        machine only.

        Declared with `MOUSEINPUT`'s field *types* so `ctypes` computes exactly
        the same size and alignment on x86 and x64 without this module having
        to hard-code either, and with neutral field names so there is nothing
        here anything could meaningfully set. `INPUT.type` is never anything but
        `INPUT_KEYBOARD`, so this slot is never the active union member.
        """

        _fields_ = [
            ("_reserved_a", wintypes.LONG),
            ("_reserved_b", wintypes.LONG),
            ("_reserved_c", wintypes.DWORD),
            ("_reserved_d", wintypes.DWORD),
            ("_reserved_e", wintypes.DWORD),
            ("_reserved_f", _ULONG_PTR),
        ]

    class _InputUnion(ctypes.Union):
        _fields_ = [("ki", _KeyboardInput), ("_unused", _UnusedUnionSlot)]

    class _Input(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("union", _InputUnion)]

    lib.user32.SendInput.argtypes = [
        wintypes.UINT,
        ctypes.POINTER(_Input),
        ctypes.c_int,
    ]
    lib.user32.SendInput.restype = wintypes.UINT

    # The second fence, and the one nearest the danger. `wScan` is a 16-bit
    # field and `ctypes` truncates into it silently, so a character above
    # U+FFFF would arrive as its low sixteen bits -- and U+1000D truncates to
    # Enter, U+10009 to Tab. The validator refuses these already; refusing them
    # again here means a future caller that reaches this function by another
    # route cannot reintroduce the injection.
    astral = next((c for c in str(text) if ord(c) > 0xFFFF), None)
    if astral is not None:
        raise Win32CallError(
            f"refusing to type U+{ord(astral):04X}: a character outside the Basic "
            "Multilingual Plane does not fit the 16-bit field Windows carries it in, "
            "and some of them truncate to Enter or Tab",
        )

    events: list[Any] = []
    for char in str(text):
        for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
            item = _Input()
            item.type = INPUT_KEYBOARD
            # wVk = 0 is what makes this a character and not a key. Set
            # explicitly rather than left to the zeroed structure, so the
            # property is visible at the point it matters.
            item.union.ki.wVk = 0
            item.union.ki.wScan = ord(char)
            item.union.ki.dwFlags = flags
            item.union.ki.time = 0
            item.union.ki.dwExtraInfo = 0
            events.append(item)

    if ctypes.sizeof(_Input) != INPUT_STRUCT_SIZE:  # pragma: no cover - Windows only
        raise Win32CallError(
            f"the INPUT structure is {ctypes.sizeof(_Input)} bytes and Win32 expects "
            f"{INPUT_STRUCT_SIZE}; SendInput would reject every event. This is a "
            "build problem in win32.py, not a problem with the request.",
        )

    if not events:
        return 0
    array = (_Input * len(events))(*events)
    sent = lib.user32.SendInput(len(events), array, ctypes.sizeof(_Input))
    if int(sent) != len(events):
        raise Win32CallError("SendInput", _last_error())
    return int(sent)
