"""Reading a narrow slice of local machine state. Read-only, by construction.

A probe answers two questions and no others: *is the person idle, and for how
long*, and *which application has focus, by name*. It has no method that changes
anything on the machine, and the protocol below has no room for one.

**No process launching, anywhere in this package.** `subprocess`, `os.system`
and `os.popen` are not imported here or anywhere else under
`bartholomew/companion/`, and `tests/test_companion_no_actuation.py` asserts
that over the package's source. That rules out the usual shape of an accidental
actuation tunnel -- a probe that shells out to a helper tool and grows an
argument the caller can influence.

The Windows probe uses `ctypes` against a **fixed, allowlisted** set of
documented read-only Win32 calls (`GetForegroundWindow`,
`GetWindowThreadProcessId`, `GetLastInputInfo`, `GetTickCount`, `OpenProcess`
with query-only access, `QueryFullProcessImageNameW`, `CloseHandle`). The test
suite asserts that allowlist over the source, so a later edit that reached for
`SendInput`, `PostMessage`, `keybd_event` or `CreateProcess` fails CI rather
than shipping.

On any platform without a probe the honest answer is `None` -- "the companion
does not know" -- which the runner reports by simply not emitting that
observation. It never guesses, and it never falls back to a broader collection
method.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from typing import Any, Protocol

logger = logging.getLogger(__name__)

#: The maximum idle reading the companion will report. Beyond this the exact
#: number stops being state and starts being a record of how long someone was
#: away, which is more than this slice needs.
IDLE_CLAMP_SECONDS = 3600


class HostProbe(Protocol):
    """Everything a companion may ask the local machine. Two read-only questions."""

    @property
    def name(self) -> str:
        """Human-readable probe identity, for logs and the operator."""
        ...

    def idle_seconds(self) -> int | None:
        """Seconds since the last user input, or None if unknown."""
        ...

    def foreground_application(self) -> str | None:
        """The focused application's name, or None if unknown."""
        ...


class NullProbe:
    """Knows nothing, and says so. The default on every unsupported platform.

    Not a stub for a future implementation: "unknown" is a correct and complete
    answer, and a companion running with this probe still delivers presence and
    system-state observations. It exists so an unsupported platform degrades to
    less observation rather than to a broader collection method.
    """

    name = "null"

    def idle_seconds(self) -> int | None:
        return None

    def foreground_application(self) -> str | None:
        return None


class WindowsProbe:
    """Foreground application name and input idle time on Windows.

    Reads the focused window's owning process image name, then discards
    everything but the base name (`observation.normalise_application` does the
    discarding) -- the window *title* is never read, so document names, page
    titles and URLs are not merely filtered out, they are never fetched.
    """

    name = "windows-user32"

    def __init__(self) -> None:
        if sys.platform != "win32":  # pragma: no cover - platform guard
            raise RuntimeError("WindowsProbe is only available on Windows")
        # `windll` exists only on Windows, which the guard above has already
        # established. Resolved once, so the probe holds two library handles
        # and no mechanism for looking up a third.
        self._user32: Any = ctypes.windll.user32  # type: ignore[attr-defined]
        self._kernel32: Any = ctypes.windll.kernel32  # type: ignore[attr-defined]

    def idle_seconds(self) -> int | None:  # pragma: no cover - Windows only
        class LastInputInfo(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = LastInputInfo()
        info.cbSize = ctypes.sizeof(info)
        if not self._user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        millis = self._kernel32.GetTickCount() - info.dwTime
        if millis < 0:
            return None
        return min(int(millis // 1000), IDLE_CLAMP_SECONDS)

    def foreground_application(self) -> str | None:  # pragma: no cover - Windows only
        hwnd = self._user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = ctypes.c_uint(0)
        self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None
        #: Query-only. Deliberately not PROCESS_ALL_ACCESS or anything that
        #: would permit writing to, or terminating, the process.
        process_query_limited_information = 0x1000
        handle = self._kernel32.OpenProcess(process_query_limited_information, False, pid.value)
        if not handle:
            return None
        try:
            size = ctypes.c_uint(260)
            buf = ctypes.create_unicode_buffer(size.value)
            if not self._kernel32.QueryFullProcessImageNameW(
                handle,
                0,
                buf,
                ctypes.byref(size),
            ):
                return None
            return buf.value or None
        finally:
            self._kernel32.CloseHandle(handle)


def default_probe() -> HostProbe:
    """The probe for this machine, or `NullProbe` when there isn't one.

    Never raises: a companion that cannot read local state is still a useful
    companion (it reports presence and system state), and refusing to start
    would trade a small capability for none at all.
    """
    if sys.platform == "win32":
        try:
            return WindowsProbe()
        except Exception:  # pragma: no cover - defensive
            logger.warning("Windows probe unavailable; reporting unknown local state")
    return NullProbe()


def platform_name() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform.startswith("linux"):
        return "linux"
    return "unknown"
