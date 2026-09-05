"""The nine capability handlers. Each one observes its own effect, or says it cannot.

One function per capability, and each is written to the same shape:

1. re-validate the parameters locally, against *this machine's* allowlists;
2. do the narrowest Win32 thing that accomplishes the capability;
3. **read the effect back**, and report `succeeded` only if it is there;
4. report `failed` when the effect is observably absent, and `unknown` when it
   cannot be observed at all.

Step 3 is the one that makes results trustworthy, and it is why every handler
here is longer than the Win32 call it wraps. A handler that returned
`succeeded` because `SetForegroundWindow` did not raise would be reporting the
absence of an exception, and Windows returns `FALSE` from that call routinely
without raising anything.

Re-validating in step 1 is not paranoia about the server: the server cannot
resolve a path on this disk, and this process cannot know which principal
approved anything. The two checks are different, both are needed, and the
stricter allowlist always wins because both must pass.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bartholomew.actuation.allowlists import AllowlistError
from bartholomew.actuation.capabilities import CapabilityKind
from bartholomew.actuation.parameters import (
    EXECUTABLE_EXTENSIONS,
    ParameterError,
    SensitiveContentError,
    ValidationContext,
    validate,
)
from bartholomew.actuation.result import ErrorCategory, HandlerOutcome
from bartholomew.actuation.sensitive import (
    detect_secrets,
    secret_categories,
    sensitive_field_reasons,
)

from . import uia, win32
from .config import ActionCompanionConfig

logger = logging.getLogger(__name__)

#: How long to wait for a window to appear or a focus change to settle.
SETTLE_SECONDS = 0.35

#: How long to wait, in total, for a launched application to show a window.
LAUNCH_WINDOW_WAIT_SECONDS = 4.0


@dataclass(frozen=True)
class HandlerContext:
    """What a handler is given besides its parameters.

    Just the configuration. A handler has no access to the channel, the
    network, the action's governance state or the approving principal: it is
    handed validated parameters and asked what happened.
    """

    config: ActionCompanionConfig

    def validation_context(self) -> ValidationContext:
        """This machine's own allowlists, with the filesystem actually present."""
        return ValidationContext(
            applications=self.config.applications,
            url_domains=self.config.url_domains,
            filesystem_roots=self.config.filesystem_roots,
            filesystem_available=True,
        )


def _revalidate(
    kind: CapabilityKind,
    raw: Any,
    ctx: HandlerContext,
) -> dict[str, Any] | HandlerOutcome:
    """Step 1, shared. Returns canonical parameters, or the refusal to report.

    A secret refusal keeps its own category on the way out, so an audit can
    count "somebody tried to have a credential typed" separately from
    "somebody sent a malformed request". Ordered before the general case
    because `SensitiveContentError` is a `ParameterError`.
    """
    try:
        return dict(validate(kind, raw, ctx.validation_context()).canonical)
    except SensitiveContentError as e:
        return HandlerOutcome.refused(ErrorCategory.SENSITIVE_CONTENT, str(e))
    except ParameterError as e:
        return HandlerOutcome.refused(
            ErrorCategory.PARAMETERS_INVALID,
            f"this device refused the parameters: {e}",
        )
    except AllowlistError as e:  # pragma: no cover - validate wraps these already
        return HandlerOutcome.refused(ErrorCategory.PARAMETERS_INVALID, str(e))


def _windows_for(app_id: str, ctx: HandlerContext) -> list[win32.WindowInfo] | HandlerOutcome:
    """Every visible window belonging to the allowlisted application `app_id`.

    Matched on the process image path, not on the window title: a title is
    content that any page can set, and matching on it would let a web page
    called "Notepad" be mistaken for Notepad.
    """
    try:
        executable = ctx.config.applications.resolve(app_id)
    except AllowlistError as e:
        return HandlerOutcome.refused(ErrorCategory.PARAMETERS_INVALID, str(e))
    try:
        windows = win32.visible_windows()
    except win32.PlatformUnsupportedError as e:
        return HandlerOutcome.refused(ErrorCategory.PLATFORM_UNSUPPORTED, str(e))
    except win32.Win32CallError as e:
        return HandlerOutcome.unverifiable(
            f"the window list could not be read, so nothing is known about the target: {e}",
        )
    target = os.path.normcase(os.path.normpath(executable))
    return [
        w
        for w in windows
        if w.image_path and os.path.normcase(os.path.normpath(w.image_path)) == target
    ]


def _one_window(
    app_id: str,
    ctx: HandlerContext,
) -> win32.WindowInfo | HandlerOutcome:
    """Resolve `app_id` to exactly one window, or refuse.

    Ambiguity is a refusal, not a guess. "Focus Chrome" with four Chrome
    windows open has no single right answer, and picking one would mean acting
    on a window the person did not have in mind -- which for a subsequent
    `type_text` is exactly the failure that matters most.
    """
    windows = _windows_for(app_id, ctx)
    if isinstance(windows, HandlerOutcome):
        return windows
    if not windows:
        return HandlerOutcome.failed(
            ErrorCategory.TARGET_NOT_FOUND,
            f"no visible window belongs to {app_id!r} on this machine",
        )
    if len(windows) > 1:
        return HandlerOutcome.failed(
            ErrorCategory.TARGET_AMBIGUOUS,
            f"{len(windows)} visible windows belong to {app_id!r}; an ambiguous "
            "target is refused rather than guessed at",
            window_count=len(windows),
        )
    return windows[0]


# ---------------------------------------------------------------------------
# windows.open_url
# ---------------------------------------------------------------------------


def open_url(params: Any, ctx: HandlerContext) -> HandlerOutcome:
    """Open one allowlisted http/https URL in the default browser.

    **The result is `unknown`, not `succeeded`, and that is correct.**
    `ShellExecuteW` returning above 32 means Windows handed the URL to the
    registered handler; it does not mean a page loaded, or that the browser
    was the one the person expected. Nothing this process can observe closes
    that gap -- reading the browser's address bar would be screen reading,
    which this build does not do -- so the honest report is that the URL was
    handed off and the outcome was not observed.
    """
    canonical = _revalidate(CapabilityKind.OPEN_URL, params, ctx)
    if isinstance(canonical, HandlerOutcome):
        return canonical
    url = canonical["url"]
    try:
        code = win32.shell_open(url)
    except win32.PlatformUnsupportedError as e:
        return HandlerOutcome.refused(ErrorCategory.PLATFORM_UNSUPPORTED, str(e))
    except win32.Win32CallError as e:
        return HandlerOutcome.failed(
            ErrorCategory.OS_CALL_FAILED,
            f"Windows refused to open the URL: {e}",
            shell_execute_code=e.code,
        )
    return HandlerOutcome.unverifiable(
        "the URL was handed to the default browser; whether a page loaded is not "
        "something this companion can observe, so the outcome is reported as unknown "
        "rather than as success",
        shell_execute_code=code,
        url_host=url.split("/")[2] if "//" in url else "",
    )


# ---------------------------------------------------------------------------
# windows.open_path
# ---------------------------------------------------------------------------


def open_path(params: Any, ctx: HandlerContext) -> HandlerOutcome:
    """Open one existing document or folder inside an allowlisted root.

    Read-only in the only sense that matters: the capability's whole effect is
    to ask the shell to show something. It creates nothing, deletes nothing,
    moves nothing and writes nothing, and the validator has already refused
    every extension that would make "open" mean "run".

    Reported as `unknown` for the same reason as `open_url`: the shell
    accepting a document does not tell this process that an application
    displayed it.
    """
    canonical = _revalidate(CapabilityKind.OPEN_PATH, params, ctx)
    if isinstance(canonical, HandlerOutcome):
        return canonical
    path = canonical["path"]

    # Re-checked immediately before the call rather than only in the validator:
    # the file may have been deleted, or replaced by a link, in between.
    #
    # **Every check the validator made on the resolved path is made again**,
    # not just containment. `require_within` re-resolves symlinks and junctions
    # from scratch, so the second resolution can land somewhere the first did
    # not -- and an attacker who can write into an allowlisted root and win the
    # window between the two would otherwise have turned "open a document" into
    # `ShellExecuteW("open", "...\\payload.exe")`. That swap is exactly the
    # "replaced by a link" case this re-check exists for, so it has to test
    # what the link now points at and not only where it lives.
    try:
        resolved = ctx.config.filesystem_roots.require_within(path)
    except AllowlistError as e:
        return HandlerOutcome.refused(ErrorCategory.PARAMETERS_INVALID, str(e))
    if not (os.path.isfile(resolved) or os.path.isdir(resolved)):
        return HandlerOutcome.failed(
            ErrorCategory.TARGET_NOT_FOUND,
            "the path no longer exists",
        )
    suffix = Path(resolved).suffix.lower()
    if suffix in EXECUTABLE_EXTENSIONS:
        return HandlerOutcome.refused(
            ErrorCategory.PARAMETERS_INVALID,
            f"the path now resolves to {suffix!r}, an executable or script extension. "
            "Opening one runs it, and this capability opens documents and folders "
            "only.",
        )

    try:
        code = win32.shell_open(resolved)
    except win32.PlatformUnsupportedError as e:
        return HandlerOutcome.refused(ErrorCategory.PLATFORM_UNSUPPORTED, str(e))
    except win32.Win32CallError as e:
        return HandlerOutcome.failed(
            ErrorCategory.OS_CALL_FAILED,
            f"Windows refused to open the path: {e}",
            shell_execute_code=e.code,
        )
    return HandlerOutcome.unverifiable(
        "the path was handed to the shell; whether an application displayed it is "
        "not something this companion can observe",
        shell_execute_code=code,
        is_directory=os.path.isdir(resolved),
        suffix=Path(resolved).suffix.lower(),
    )


# ---------------------------------------------------------------------------
# windows.launch_app
# ---------------------------------------------------------------------------


def launch_app(params: Any, ctx: HandlerContext) -> HandlerOutcome:
    """Start one allowlisted application, with no arguments, and check it lives.

    This one *can* be verified, and is: `CreateProcessW` returns a process id,
    and the handler then confirms the process is still running and that its
    image path is the allowlisted executable and not something that replaced
    it. A process that exited immediately is reported as `failed`, not as a
    successful launch.
    """
    canonical = _revalidate(CapabilityKind.LAUNCH_APP, params, ctx)
    if isinstance(canonical, HandlerOutcome):
        return canonical
    app_id = canonical["app_id"]
    try:
        executable = ctx.config.applications.resolve(app_id)
    except AllowlistError as e:
        return HandlerOutcome.refused(ErrorCategory.PARAMETERS_INVALID, str(e))
    if not os.path.isfile(executable):
        return HandlerOutcome.failed(
            ErrorCategory.TARGET_NOT_FOUND,
            f"the allowlisted executable for {app_id!r} is not present on this machine",
        )

    try:
        started = win32.start_process(executable)
    except win32.PlatformUnsupportedError as e:
        return HandlerOutcome.refused(ErrorCategory.PLATFORM_UNSUPPORTED, str(e))
    except win32.Win32CallError as e:
        category = (
            ErrorCategory.PERMISSION_DENIED if e.code in (5, 740) else ErrorCategory.OS_CALL_FAILED
        )
        return HandlerOutcome.failed(
            category,
            f"the application did not start: {e}",
            create_process_error=e.code,
        )

    if not started.running:
        return HandlerOutcome.failed(
            ErrorCategory.OS_CALL_FAILED,
            f"{app_id!r} was created but had already exited when it was checked",
            process_id=started.process_id,
        )

    # The second observation: the running process is the executable we asked
    # for. Cheap, and it catches the case where the allowlisted path resolved
    # to something else between the check above and the call.
    image = win32.process_image_name(started.process_id)
    if image and os.path.normcase(os.path.normpath(image)) != os.path.normcase(
        os.path.normpath(executable),
    ):
        return HandlerOutcome.failed(
            ErrorCategory.OS_CALL_FAILED,
            "the started process is not the allowlisted executable",
            process_id=started.process_id,
        )

    # A window is a nicer confirmation but is not required: a background
    # application is still launched. Its absence is reported, not treated as
    # failure.
    deadline = time.monotonic() + LAUNCH_WINDOW_WAIT_SECONDS
    window_seen = False
    while time.monotonic() < deadline:
        windows = _windows_for(app_id, ctx)
        if isinstance(windows, HandlerOutcome):
            break
        if any(w.process_id == started.process_id for w in windows):
            window_seen = True
            break
        time.sleep(SETTLE_SECONDS)

    return HandlerOutcome.succeeded(
        f"{app_id!r} is running",
        app_id=app_id,
        process_id=started.process_id,
        window_observed=window_seen,
    )


# ---------------------------------------------------------------------------
# windows.focus_window
# ---------------------------------------------------------------------------


def focus_window(params: Any, ctx: HandlerContext) -> HandlerOutcome:
    """Bring one allowlisted application's single window to the foreground.

    Verified by reading `GetForegroundWindow` back. Windows refuses
    `SetForegroundWindow` from a background process under several documented
    conditions, and when it does this reports `failed` -- it does **not**
    attach to the target's input queue or synthesise an ALT keystroke to
    defeat the foreground lock. Those workarounds are input synthesis directed
    at a window nobody approved, and their absence is asserted in
    `tests/test_windows_action_prohibitions.py`.
    """
    canonical = _revalidate(CapabilityKind.FOCUS_WINDOW, params, ctx)
    if isinstance(canonical, HandlerOutcome):
        return canonical
    window = _one_window(canonical["app_id"], ctx)
    if isinstance(window, HandlerOutcome):
        return window
    return _focus(window, canonical["app_id"])


def _focus(window: win32.WindowInfo, app_id: str) -> HandlerOutcome:
    try:
        if win32.is_minimized(window.hwnd):
            # A minimised window cannot take the foreground until it is
            # restored. Restoring is within `manage_window`'s own vocabulary,
            # so this is not a capability the focus handler is borrowing.
            win32.show_window(window.hwnd, win32.SW_RESTORE)
        accepted = win32.set_foreground(window.hwnd)
    except win32.PlatformUnsupportedError as e:
        return HandlerOutcome.refused(ErrorCategory.PLATFORM_UNSUPPORTED, str(e))
    except win32.Win32CallError as e:
        return HandlerOutcome.failed(
            ErrorCategory.OS_CALL_FAILED,
            f"the window could not be focused: {e}",
        )

    time.sleep(SETTLE_SECONDS)
    try:
        actual = win32.foreground_window()
    except win32.Win32CallError:
        return HandlerOutcome.unverifiable(
            "the focus request was made but the foreground window could not be read "
            "back, so it is not known whether it took effect",
            app_id=app_id,
        )
    if actual == window.hwnd:
        return HandlerOutcome.succeeded(
            f"{app_id!r} has the foreground",
            app_id=app_id,
            hwnd=window.hwnd,
        )
    return HandlerOutcome.failed(
        ErrorCategory.PERMISSION_DENIED if not accepted else ErrorCategory.OS_CALL_FAILED,
        "Windows did not give the window the foreground. No keystroke-injection "
        "fallback is attempted.",
        app_id=app_id,
        hwnd=window.hwnd,
        foreground_hwnd=actual,
    )


# ---------------------------------------------------------------------------
# windows.manage_window
# ---------------------------------------------------------------------------


def manage_window(params: Any, ctx: HandlerContext) -> HandlerOutcome:
    """Focus, minimise, maximise, restore, or bounded move/resize one window.

    Every operation is verified by reading the resulting state back:
    `IsIconic` after a minimise, `IsZoomed` after a maximise,
    `GetWindowRect` after a move or resize. A move is additionally clamped to
    the virtual desktop, so a window cannot be parked off-screen where the
    person cannot get it back.
    """
    canonical = _revalidate(CapabilityKind.MANAGE_WINDOW, params, ctx)
    if isinstance(canonical, HandlerOutcome):
        return canonical
    app_id = canonical["app_id"]
    operation = canonical["operation"]

    window = _one_window(app_id, ctx)
    if isinstance(window, HandlerOutcome):
        return window

    if operation == "focus":
        return _focus(window, app_id)

    try:
        if operation in ("minimize", "maximize", "restore"):
            command = {
                "minimize": win32.SW_MINIMIZE,
                "maximize": win32.SW_MAXIMIZE,
                "restore": win32.SW_RESTORE,
            }[operation]
            win32.show_window(window.hwnd, command)
            time.sleep(SETTLE_SECONDS)
            minimised = win32.is_minimized(window.hwnd)
            maximised = win32.is_maximized(window.hwnd)
            expected = {
                "minimize": minimised,
                "maximize": maximised,
                "restore": not minimised and not maximised,
            }[operation]
            if expected:
                return HandlerOutcome.succeeded(
                    f"{app_id!r} is {operation}d",
                    app_id=app_id,
                    hwnd=window.hwnd,
                    minimized=minimised,
                    maximized=maximised,
                )
            return HandlerOutcome.failed(
                ErrorCategory.OS_CALL_FAILED,
                f"the window did not end up {operation}d",
                app_id=app_id,
                minimized=minimised,
                maximized=maximised,
            )

        x0, y0, width, height = win32.virtual_screen()
        if operation == "move":
            # Clamped, not refused: the request already passed a bounds check
            # against a plausible desktop, and this is the real one.
            target_x = max(x0, min(int(canonical["x"]), x0 + width - 1))
            target_y = max(y0, min(int(canonical["y"]), y0 + height - 1))
            win32.move_window(window.hwnd, target_x, target_y)
            time.sleep(SETTLE_SECONDS)
            left, top, _right, _bottom = win32.window_rect(window.hwnd)
            if (left, top) == (target_x, target_y):
                return HandlerOutcome.succeeded(
                    f"{app_id!r} moved",
                    app_id=app_id,
                    hwnd=window.hwnd,
                    left=left,
                    top=top,
                    clamped=(target_x, target_y) != (canonical["x"], canonical["y"]),
                )
            return HandlerOutcome.failed(
                ErrorCategory.OS_CALL_FAILED,
                "the window did not move to the requested position",
                app_id=app_id,
                left=left,
                top=top,
            )

        target_w = max(1, min(int(canonical["width"]), width))
        target_h = max(1, min(int(canonical["height"]), height))
        win32.resize_window(window.hwnd, target_w, target_h)
        time.sleep(SETTLE_SECONDS)
        left, top, right, bottom = win32.window_rect(window.hwnd)
        if (right - left, bottom - top) == (target_w, target_h):
            return HandlerOutcome.succeeded(
                f"{app_id!r} resized",
                app_id=app_id,
                hwnd=window.hwnd,
                width=right - left,
                height=bottom - top,
                clamped=(target_w, target_h) != (canonical["width"], canonical["height"]),
            )
        return HandlerOutcome.failed(
            ErrorCategory.OS_CALL_FAILED,
            "the window did not take the requested size",
            app_id=app_id,
            width=right - left,
            height=bottom - top,
        )
    except win32.PlatformUnsupportedError as e:
        return HandlerOutcome.refused(ErrorCategory.PLATFORM_UNSUPPORTED, str(e))
    except win32.Win32CallError as e:
        return HandlerOutcome.unverifiable(
            f"the window operation could not be completed or read back: {e}",
            app_id=app_id,
        )


# ---------------------------------------------------------------------------
# windows.clipboard_read
# ---------------------------------------------------------------------------


def clipboard_read(params: Any, ctx: HandlerContext) -> HandlerOutcome:
    """Read the clipboard once, and refuse to hand back anything sensitive.

    **The content does not leave this machine by default.** With
    `BARTH_ACTION_CLIPBOARD_RETURN_CONTENT` unset -- which is the shipped
    configuration -- the result carries the text's digest, its length and
    whether the secret detector fired, and the text itself is never
    transmitted or stored anywhere. An operator who needs the content must
    turn that on deliberately, and even then a detected secret is refused
    rather than returned.
    """
    canonical = _revalidate(CapabilityKind.CLIPBOARD_READ, params, ctx)
    if isinstance(canonical, HandlerOutcome):
        return canonical
    try:
        text = win32.read_clipboard_text()
    except win32.PlatformUnsupportedError as e:
        return HandlerOutcome.refused(ErrorCategory.PLATFORM_UNSUPPORTED, str(e))
    except win32.Win32CallError as e:
        return HandlerOutcome.unverifiable(
            f"the clipboard could not be read: {e}",
        )
    if text is None:
        return HandlerOutcome.succeeded(
            "the clipboard holds no text",
            has_text=False,
            text_length=0,
        )

    categories = secret_categories(text)
    if categories:
        return HandlerOutcome.refused(
            ErrorCategory.SENSITIVE_CONTENT,
            "the clipboard holds what looks like credential material "
            f"({', '.join(categories)}); it was not returned and was not stored",
            has_text=True,
            text_length=len(text),
            sensitive=True,
            sensitive_categories=",".join(categories),
        )

    evidence: dict[str, Any] = {
        "has_text": True,
        "text_length": len(text),
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "sensitive": False,
        "content_returned": ctx.config.clipboard_return_content,
    }
    if ctx.config.clipboard_return_content:
        evidence["text"] = text
    return HandlerOutcome.succeeded("the clipboard was read", **evidence)


# ---------------------------------------------------------------------------
# windows.clipboard_write
# ---------------------------------------------------------------------------


def clipboard_write(params: Any, ctx: HandlerContext) -> HandlerOutcome:
    """Replace the clipboard with bounded, ordinary text, and read it back."""
    canonical = _revalidate(CapabilityKind.CLIPBOARD_WRITE, params, ctx)
    if isinstance(canonical, HandlerOutcome):
        return canonical
    text = canonical["text"]

    findings = detect_secrets(text)
    if findings:
        return HandlerOutcome.refused(
            ErrorCategory.SENSITIVE_CONTENT,
            "the text looks like credential material "
            f"({', '.join(f.category for f in findings)}); nothing was copied",
        )

    try:
        win32.write_clipboard_text(text)
    except win32.PlatformUnsupportedError as e:
        return HandlerOutcome.refused(ErrorCategory.PLATFORM_UNSUPPORTED, str(e))
    except win32.Win32CallError as e:
        return HandlerOutcome.failed(
            ErrorCategory.OS_CALL_FAILED,
            f"the clipboard could not be written: {e}",
        )

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    try:
        written = win32.read_clipboard_text()
    except win32.Win32CallError:
        return HandlerOutcome.unverifiable(
            "the clipboard was written but could not be read back, so it is not "
            "known whether the write took effect",
            text_sha256=digest,
            text_length=len(text),
        )
    if written == text:
        return HandlerOutcome.succeeded(
            "the clipboard now holds the requested text",
            text_sha256=digest,
            text_length=len(text),
        )
    return HandlerOutcome.failed(
        ErrorCategory.OS_CALL_FAILED,
        "the clipboard does not hold the requested text after the write",
        text_sha256=digest,
        text_length=len(text),
    )


# ---------------------------------------------------------------------------
# windows.type_text
# ---------------------------------------------------------------------------


def type_text(params: Any, ctx: HandlerContext) -> HandlerOutcome:
    """Type bounded, ordinary text into the focused field -- if it is safe to.

    Three refusals stand between a request and a keystroke, and all three are
    fail-closed:

    1. The text itself must not look like credential material.
    2. The focused element must be **readable** through UI Automation. If the
       accessibility tree cannot be reached, nothing is known about where the
       caret is, and typing blind is refused. This is why `comtypes` is a hard
       requirement for this capability rather than a nicety.
    3. The focused element must not be a password box, must not be *labelled*
       like a password, PIN, token, card, or other sensitive field, and must be
       a control a person could actually type into.

    The text can contain no newline, carriage return or tab -- the validator
    refuses them -- and `win32.send_unicode_text` cannot send a virtual-key
    code at all, so there is no way for this capability to press Send, Submit,
    Confirm, Purchase or Delete.
    """
    canonical = _revalidate(CapabilityKind.TYPE_TEXT, params, ctx)
    if isinstance(canonical, HandlerOutcome):
        return canonical
    text = canonical["text"]

    findings = detect_secrets(text)
    if findings:
        return HandlerOutcome.refused(
            ErrorCategory.SENSITIVE_CONTENT,
            "the text looks like credential material "
            f"({', '.join(f.category for f in findings)}); nothing was typed",
        )

    field = uia.focused_field()
    reasons = sensitive_field_reasons(
        is_password=field.is_password,
        name=field.name,
        automation_id=field.automation_id,
        help_text=field.help_text,
    )
    if reasons:
        return HandlerOutcome.refused(
            (
                ErrorCategory.SENSITIVE_FIELD
                if field.readable
                else ErrorCategory.ACCESSIBILITY_UNAVAILABLE
            ),
            "the focused field is not one Bartholomew may type into "
            f"({', '.join(reasons)})"
            + (f": {field.unavailable_reason}" if field.unavailable_reason else ""),
            field_readable=field.readable,
        )
    if not field.typeable_control:
        return HandlerOutcome.refused(
            ErrorCategory.SENSITIVE_FIELD,
            f"the focused control (type {field.control_type}) is not a text field; "
            "typing into a button or a menu is pressing it, not typing",
            control_type=field.control_type,
        )

    try:
        sent = win32.send_unicode_text(text)
    except win32.PlatformUnsupportedError as e:
        return HandlerOutcome.refused(ErrorCategory.PLATFORM_UNSUPPORTED, str(e))
    except win32.Win32CallError as e:
        return HandlerOutcome.failed(
            ErrorCategory.OS_CALL_FAILED,
            f"the text could not be typed: {e}",
        )

    # Two keyboard events per character (down and up), which is what
    # `send_unicode_text` builds. Anything else means the injection was
    # partially blocked, and a partially typed string is not a success.
    expected = len(text) * 2
    evidence = {
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "text_length": len(text),
        "events_sent": sent,
    }
    if sent == expected:
        # Deliberately `unknown` rather than `succeeded`. Windows accepted
        # every event, but whether the characters landed in the field -- rather
        # than being swallowed by a focus change between the check and the
        # injection -- is not something this process can read back without
        # reading the field's contents, which would be reading the person's
        # screen. Accepting the honest answer here costs nothing and is the
        # difference between a truthful log and a confident one.
        return HandlerOutcome.unverifiable(
            "every keystroke was accepted by Windows; whether the characters landed "
            "in the intended field is not observable without reading the field back, "
            "which this build does not do",
            **evidence,
        )
    return HandlerOutcome.failed(
        ErrorCategory.OS_CALL_FAILED,
        f"only {sent} of {expected} keyboard events were accepted",
        **evidence,
    )


# ---------------------------------------------------------------------------
# windows.accessibility_action
# ---------------------------------------------------------------------------


def accessibility_action(params: Any, ctx: HandlerContext) -> HandlerOutcome:
    """One allowlisted, non-consequential accessibility operation.

    Expand, collapse, scroll or focus -- and nothing else. `Invoke` is not in
    `uia.ACTUATION_PATTERNS`, so pressing a control is not something this
    handler can do even for a control that would happily be pressed, and the
    validator has already refused any element whose *name* reads like a final
    action.

    Scoped to one window of one allowlisted application, so the element search
    cannot wander into another program.
    """
    canonical = _revalidate(CapabilityKind.ACCESSIBILITY_ACTION, params, ctx)
    if isinstance(canonical, HandlerOutcome):
        return canonical
    app_id = canonical["app_id"]

    window = _one_window(app_id, ctx)
    if isinstance(window, HandlerOutcome):
        return window

    try:
        done, detail = uia.perform(
            hwnd=window.hwnd,
            element_name=canonical["element_name"],
            operation=canonical["operation"],
        )
    except uia.AccessibilityUnavailableError as e:
        return HandlerOutcome.refused(
            ErrorCategory.ACCESSIBILITY_UNAVAILABLE,
            str(e),
            app_id=app_id,
        )
    if done:
        return HandlerOutcome.succeeded(
            detail,
            app_id=app_id,
            hwnd=window.hwnd,
            operation=canonical["operation"],
        )
    return HandlerOutcome.failed(
        ErrorCategory.TARGET_NOT_FOUND,
        detail,
        app_id=app_id,
        operation=canonical["operation"],
    )


#: The one handler table. A literal mapping from a closed enum to a named
#: function, built at import and never mutated.
#:
#: **Not a registry.** There is no `register()`, no entry-point group, no
#: plugin discovery and no name-to-function lookup: `getattr`, `globals()`,
#: `importlib` and `eval` appear nowhere in this package's dispatch path, and
#: `tests/test_windows_action_prohibitions.py` asserts that. A capability with
#: no entry here cannot be run, and a string that is not a `CapabilityKind`
#: cannot become a key.
HANDLERS: dict[CapabilityKind, Callable[[Any, HandlerContext], HandlerOutcome]] = {
    CapabilityKind.OPEN_URL: open_url,
    CapabilityKind.OPEN_PATH: open_path,
    CapabilityKind.LAUNCH_APP: launch_app,
    CapabilityKind.FOCUS_WINDOW: focus_window,
    CapabilityKind.MANAGE_WINDOW: manage_window,
    CapabilityKind.CLIPBOARD_READ: clipboard_read,
    CapabilityKind.CLIPBOARD_WRITE: clipboard_write,
    CapabilityKind.TYPE_TEXT: type_text,
    CapabilityKind.ACCESSIBILITY_ACTION: accessibility_action,
}
