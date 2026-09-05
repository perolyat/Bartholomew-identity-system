"""Results are truthful: success, failure and unknown stay distinct.

Acceptance requirement 15, plus the device-side half of 11 (a duplicate
delivery cannot repeat a non-repeatable action on the machine either).

The Win32 layer is substituted here, and only the Win32 layer. That is exactly
what the contract permits mocks for -- "mocks may test OS edge conditions" --
and it is what makes the interesting cases reachable at all: a real Windows
runner cannot be made to fail `SetForegroundWindow` on demand, and cannot be
made to have four Notepad windows and then none. What is *not* substituted is
any part of the argument: the dispatcher, the four device-side checks, the
handlers' verification logic, the ledger and the runner are all real.

Requirement 17's real-Windows verification lives in
`tests/integration/test_windows_action_real.py`, which skips off Windows and
substitutes nothing.
"""

from __future__ import annotations

import sys
from datetime import timedelta

import pytest

from bartholomew.actuation.allowlists import (
    ApplicationAllowlist,
    FilesystemRootAllowlist,
    UrlDomainAllowlist,
)
from bartholomew.actuation.capabilities import ALL_CAPABILITIES, CapabilityKind
from bartholomew.actuation.request import to_iso, utc_now
from bartholomew.actuation.result import (
    DEVICE_REPORTABLE_STATUSES,
    ActionResultStatus,
    ErrorCategory,
    HandlerOutcome,
    bounded_evidence,
)
from bartholomew.windows_actuation import handlers as handlers_module
from bartholomew.windows_actuation import win32
from bartholomew.windows_actuation.config import ActionCompanionConfig
from bartholomew.windows_actuation.dispatch import LeasedAction, dispatch
from bartholomew.windows_actuation.handlers import HandlerContext
from bartholomew.windows_actuation.state import (
    ActionCompanionState,
    ActionStateFile,
    ExecutedEntry,
    LedgerUnreadableError,
)

DEVICE = "desk-pc"
NOTEPAD = "C:\\Windows\\System32\\notepad.exe"


@pytest.fixture
def config(tmp_path):
    documents = tmp_path / "Documents"
    documents.mkdir()
    (documents / "report.pdf").write_text("pretend", encoding="utf-8")
    return ActionCompanionConfig(
        base_url="https://127.0.0.1:5173",
        device_id=DEVICE,
        state_path=tmp_path / "action-state.json",
        applications=ApplicationAllowlist.from_pairs({"notepad": NOTEPAD}),
        url_domains=UrlDomainAllowlist.from_iterable(["example.com"]),
        filesystem_roots=FilesystemRootAllowlist.from_iterable([str(documents)]),
        capabilities=tuple(ALL_CAPABILITIES),
    )


@pytest.fixture
def ctx(config):
    return HandlerContext(config=config)


@pytest.fixture
def state():
    return ActionCompanionState()


def _action(capability, parameters, **overrides):
    payload = {
        "action_id": "act-1",
        "tenant_id": "tenant-a",
        "device_id": DEVICE,
        "capability": capability,
        "capability_version": 1,
        "parameters": parameters,
        "expires_at": to_iso(utc_now() + timedelta(minutes=5)),
        "repeatability": "non_repeatable",
    }
    payload.update(overrides)
    return LeasedAction.from_wire(payload)


def _with_executable(ctx, tmp_path):
    """The same context, with the allowlist pointing at a file that exists.

    Rebuilt rather than mutated: `ApplicationAllowlist` is frozen on purpose,
    and a test that could edit one in place would be testing something the
    runtime cannot do.
    """
    from dataclasses import replace

    executable = tmp_path / "notepad.exe"
    executable.write_text("x", encoding="utf-8")
    return HandlerContext(
        config=replace(
            ctx.config,
            applications=ApplicationAllowlist.from_pairs({"notepad": str(executable)}),
        ),
    )


class _FakeWindow:
    def __init__(self, hwnd=101, pid=2001, title="Untitled - Notepad", image=NOTEPAD):
        self.hwnd = hwnd
        self.process_id = pid
        self.title = title
        self.image_path = image


# ---------------------------------------------------------------------------
# the three outcomes are genuinely three
# ---------------------------------------------------------------------------


def test_a_handler_cannot_report_a_governance_word():
    """`accepted` and `refused` are Governance's, not a device's."""
    assert DEVICE_REPORTABLE_STATUSES == {
        ActionResultStatus.STARTED,
        ActionResultStatus.SUCCEEDED,
        ActionResultStatus.FAILED,
        ActionResultStatus.CANCELLED,
        ActionResultStatus.UNKNOWN,
    }
    for word in (ActionResultStatus.ACCEPTED, ActionResultStatus.REFUSED):
        with pytest.raises(ValueError, match="may not report"):
            HandlerOutcome(word, ErrorCategory.INTERNAL_ERROR, "x")


def test_a_non_success_outcome_must_name_a_category():
    """So an audit can count causes rather than parse prose."""
    with pytest.raises(ValueError, match="error category"):
        HandlerOutcome(ActionResultStatus.FAILED, None, "it did not work")
    HandlerOutcome(ActionResultStatus.SUCCEEDED, None, "it worked")


def test_unknown_is_never_rounded_to_success():
    unknown = HandlerOutcome.unverifiable("could not read the effect back")
    assert unknown.status is ActionResultStatus.UNKNOWN
    assert unknown.error_category is ErrorCategory.EFFECT_UNVERIFIABLE

    from bartholomew.actuation.result import ActionResult

    result = ActionResult(
        action_id="a",
        tenant_id="t",
        device_id="d",
        status=ActionResultStatus.UNKNOWN,
        error_category=ErrorCategory.EFFECT_UNVERIFIABLE,
    )
    assert result.succeeded is False
    assert result.terminal is True


def test_evidence_is_bounded_and_flattened():
    """A handler cannot smuggle a document out inside a nested structure."""
    evidence = bounded_evidence(
        {
            "small": 1,
            "long": "x" * 5000,
            "nested": {"a": ["b" * 5000]},
            **{f"key{i}": i for i in range(40)},
        },
    )
    assert len(evidence) <= 12
    for value in evidence.values():
        assert isinstance(value, (int, float, bool, str)) or value is None
        if isinstance(value, str):
            assert len(value) <= 200


# ---------------------------------------------------------------------------
# the four device-side checks
# ---------------------------------------------------------------------------


def test_an_action_for_another_device_is_refused_here_too(ctx, state):
    outcome = dispatch(
        _action("windows.focus_window", {"app_id": "notepad"}, device_id="somebody-elses-pc"),
        ctx,
        state,
    )
    assert outcome.status is ActionResultStatus.FAILED
    assert outcome.error_category is ErrorCategory.DEVICE_NOT_ENROLLED


def test_an_unknown_capability_is_refused_here_too(ctx, state):
    outcome = dispatch(_action("windows.run_command", {}), ctx, state)
    assert outcome.error_category is ErrorCategory.CAPABILITY_UNSUPPORTED


def test_a_mismatched_version_is_refused_here_too(ctx, state):
    outcome = dispatch(
        _action("windows.focus_window", {"app_id": "notepad"}, capability_version=7),
        ctx,
        state,
    )
    assert outcome.error_category is ErrorCategory.CAPABILITY_UNSUPPORTED


def test_a_capability_this_install_does_not_offer_is_refused(config, state):
    """Configured capabilities are the second, narrower gate."""
    narrowed = HandlerContext(
        config=ActionCompanionConfig(
            **{
                **config.__dict__,
                "capabilities": (CapabilityKind.FOCUS_WINDOW,),
            },
        ),
    )
    outcome = dispatch(_action("windows.type_text", {"text": "hello"}), narrowed, state)
    assert outcome.error_category is ErrorCategory.CAPABILITY_NOT_DECLARED


def test_an_expired_action_is_refused_by_the_devices_own_clock(ctx, state):
    outcome = dispatch(
        _action(
            "windows.focus_window",
            {"app_id": "notepad"},
            expires_at=to_iso(utc_now() - timedelta(seconds=1)),
        ),
        ctx,
        state,
    )
    assert outcome.error_category is ErrorCategory.EXPIRED


@pytest.mark.parametrize("expiry", ["", "not a date", "2026-01-01T00:00:00", "9999"])
def test_an_unreadable_expiry_is_treated_as_expired_not_unbounded(ctx, state, expiry):
    if not expiry:
        with pytest.raises(Exception):
            _action("windows.focus_window", {"app_id": "notepad"}, expires_at=expiry)
        return
    outcome = dispatch(
        _action("windows.focus_window", {"app_id": "notepad"}, expires_at=expiry),
        ctx,
        state,
    )
    assert outcome.error_category is ErrorCategory.EXPIRED


def test_the_ledger_refuses_a_duplicate_delivery_of_a_non_repeatable_action(ctx, state):
    """The server refuses a second lease; this refuses a second execution."""
    state.executed["act-1"] = ExecutedEntry(
        action_id="act-1",
        status="succeeded",
        observed_at=to_iso(utc_now()),
    )
    outcome = dispatch(_action("windows.focus_window", {"app_id": "notepad"}), ctx, state)
    assert outcome.error_category is ErrorCategory.REPLAY_REFUSED
    assert "already ran" in outcome.detail


def test_a_malformed_leased_action_is_refused_rather_than_defaulted(ctx):
    """A missing device_id defaulted to this device would be a targeting failure."""
    from bartholomew.windows_actuation.dispatch import DispatchRefusedError

    for missing in ("action_id", "tenant_id", "device_id", "capability", "expires_at"):
        payload = {
            "action_id": "a",
            "tenant_id": "t",
            "device_id": DEVICE,
            "capability": "windows.focus_window",
            "capability_version": 1,
            "parameters": {},
            "expires_at": to_iso(utc_now() + timedelta(minutes=1)),
        }
        payload.pop(missing)
        with pytest.raises(DispatchRefusedError):
            LeasedAction.from_wire(payload)


def test_a_crashed_handler_becomes_unknown_not_failed(ctx, state, monkeypatch):
    """A handler that died part-way may or may not have had an effect."""

    def _boom(_params, _ctx):
        raise RuntimeError("something went wrong mid-call")

    monkeypatch.setitem(handlers_module.HANDLERS, CapabilityKind.FOCUS_WINDOW, _boom)
    outcome = dispatch(_action("windows.focus_window", {"app_id": "notepad"}), ctx, state)
    assert outcome.status is ActionResultStatus.UNKNOWN
    assert outcome.error_category is ErrorCategory.EFFECT_UNVERIFIABLE


# ---------------------------------------------------------------------------
# handler outcomes, with the Win32 layer substituted
# ---------------------------------------------------------------------------


def test_launch_reports_success_only_when_the_process_is_observed_running(
    ctx,
    monkeypatch,
    tmp_path,
):
    ctx = _with_executable(ctx, tmp_path)
    executable = ctx.config.applications.resolve("notepad")
    monkeypatch.setattr(
        win32,
        "start_process",
        lambda _p: win32.StartedProcess(process_id=4321, running=True),
    )
    monkeypatch.setattr(win32, "process_image_name", lambda _pid: str(executable))
    monkeypatch.setattr(win32, "visible_windows", lambda: [])

    outcome = handlers_module.launch_app({"app_id": "notepad"}, ctx)
    assert outcome.status is ActionResultStatus.SUCCEEDED
    assert outcome.evidence["process_id"] == 4321
    assert outcome.evidence["window_observed"] is False


def test_a_process_that_exited_immediately_is_a_failure_not_a_launch(
    ctx,
    monkeypatch,
    tmp_path,
):
    ctx = _with_executable(ctx, tmp_path)
    monkeypatch.setattr(
        win32,
        "start_process",
        lambda _p: win32.StartedProcess(process_id=4321, running=False),
    )
    outcome = handlers_module.launch_app({"app_id": "notepad"}, ctx)
    assert outcome.status is ActionResultStatus.FAILED
    assert "already exited" in outcome.detail


def test_a_missing_executable_is_a_failure_before_any_call(config, monkeypatch, tmp_path):
    """An allowlisted path that is not on the disk. Refused before any call.

    The allowlist points somewhere that cannot exist on *either* platform.
    Pointing it at `C:\\Windows\\System32\\notepad.exe` -- as this did -- made
    the test pass on Linux for the wrong reason and then really launch Notepad
    on the Windows runner, where the file is exactly where the allowlist said.
    """
    from dataclasses import replace

    absent = tmp_path / "not-installed" / "ghost.exe"
    ctx = HandlerContext(
        config=replace(
            config,
            applications=ApplicationAllowlist.from_pairs({"notepad": str(absent)}),
        ),
    )
    called = []
    monkeypatch.setattr(win32, "start_process", lambda p: called.append(p))
    outcome = handlers_module.launch_app({"app_id": "notepad"}, ctx)
    assert outcome.error_category is ErrorCategory.TARGET_NOT_FOUND
    assert called == [], "the process starter was never reached"


def test_focus_reports_success_only_when_the_foreground_reads_back(ctx, monkeypatch):
    window = _FakeWindow()
    monkeypatch.setattr(win32, "visible_windows", lambda: [window])
    monkeypatch.setattr(win32, "is_minimized", lambda _h: False)
    monkeypatch.setattr(win32, "set_foreground", lambda _h: True)
    monkeypatch.setattr(win32, "foreground_window", lambda: window.hwnd)

    outcome = handlers_module.focus_window({"app_id": "notepad"}, ctx)
    assert outcome.status is ActionResultStatus.SUCCEEDED
    assert outcome.evidence["hwnd"] == window.hwnd


def test_focus_reports_failure_when_windows_keeps_the_foreground(ctx, monkeypatch):
    """No keystroke-injection fallback. It says it did not get focus."""
    window = _FakeWindow()
    monkeypatch.setattr(win32, "visible_windows", lambda: [window])
    monkeypatch.setattr(win32, "is_minimized", lambda _h: False)
    monkeypatch.setattr(win32, "set_foreground", lambda _h: False)
    monkeypatch.setattr(win32, "foreground_window", lambda: 999)

    outcome = handlers_module.focus_window({"app_id": "notepad"}, ctx)
    assert outcome.status is ActionResultStatus.FAILED
    assert "No keystroke-injection fallback" in outcome.detail


def test_focus_reports_unknown_when_the_foreground_cannot_be_read_back(ctx, monkeypatch):
    window = _FakeWindow()
    monkeypatch.setattr(win32, "visible_windows", lambda: [window])
    monkeypatch.setattr(win32, "is_minimized", lambda _h: False)
    monkeypatch.setattr(win32, "set_foreground", lambda _h: True)

    def _unreadable():
        raise win32.Win32CallError("GetForegroundWindow", 5)

    monkeypatch.setattr(win32, "foreground_window", _unreadable)
    outcome = handlers_module.focus_window({"app_id": "notepad"}, ctx)
    assert outcome.status is ActionResultStatus.UNKNOWN


def test_an_ambiguous_window_is_refused_rather_than_guessed_at(ctx, monkeypatch):
    """Four Notepad windows has no single right answer, so it is not answered."""
    monkeypatch.setattr(
        win32,
        "visible_windows",
        lambda: [_FakeWindow(hwnd=h) for h in (1, 2, 3, 4)],
    )
    outcome = handlers_module.focus_window({"app_id": "notepad"}, ctx)
    assert outcome.error_category is ErrorCategory.TARGET_AMBIGUOUS
    assert outcome.evidence["window_count"] == 4


def test_no_window_at_all_is_a_target_not_found(ctx, monkeypatch):
    monkeypatch.setattr(win32, "visible_windows", lambda: [])
    outcome = handlers_module.focus_window({"app_id": "notepad"}, ctx)
    assert outcome.error_category is ErrorCategory.TARGET_NOT_FOUND


def test_a_window_of_another_application_is_never_matched(ctx, monkeypatch):
    """Matched on the process image, not the title, so a title cannot lie."""
    monkeypatch.setattr(
        win32,
        "visible_windows",
        lambda: [_FakeWindow(title="Untitled - Notepad", image="C:\\Evil\\pretend.exe")],
    )
    outcome = handlers_module.focus_window({"app_id": "notepad"}, ctx)
    assert outcome.error_category is ErrorCategory.TARGET_NOT_FOUND


def test_a_minimize_is_verified_by_reading_the_state_back(ctx, monkeypatch):
    window = _FakeWindow()
    monkeypatch.setattr(win32, "visible_windows", lambda: [window])
    monkeypatch.setattr(win32, "show_window", lambda _h, _c: True)
    monkeypatch.setattr(win32, "is_minimized", lambda _h: True)
    monkeypatch.setattr(win32, "is_maximized", lambda _h: False)

    outcome = handlers_module.manage_window(
        {"app_id": "notepad", "operation": "minimize"},
        ctx,
    )
    assert outcome.status is ActionResultStatus.SUCCEEDED
    assert outcome.evidence["minimized"] is True


def test_a_minimize_that_did_not_take_is_reported_as_failed(ctx, monkeypatch):
    window = _FakeWindow()
    monkeypatch.setattr(win32, "visible_windows", lambda: [window])
    monkeypatch.setattr(win32, "show_window", lambda _h, _c: True)
    monkeypatch.setattr(win32, "is_minimized", lambda _h: False)
    monkeypatch.setattr(win32, "is_maximized", lambda _h: False)

    outcome = handlers_module.manage_window(
        {"app_id": "notepad", "operation": "minimize"},
        ctx,
    )
    assert outcome.status is ActionResultStatus.FAILED


def test_a_move_is_clamped_to_the_visible_desktop(ctx, monkeypatch):
    window = _FakeWindow()
    monkeypatch.setattr(win32, "visible_windows", lambda: [window])
    monkeypatch.setattr(win32, "virtual_screen", lambda: (0, 0, 1920, 1080))
    moved: list = []

    def _move(hwnd, x, y):
        moved.append((hwnd, x, y))
        return True

    monkeypatch.setattr(win32, "move_window", _move)
    monkeypatch.setattr(win32, "window_rect", lambda _h: (1919, 1079, 2000, 1200))

    outcome = handlers_module.manage_window(
        {"app_id": "notepad", "operation": "move", "x": 30000, "y": 30000},
        ctx,
    )
    assert moved == [(window.hwnd, 1919, 1079)]
    assert outcome.status is ActionResultStatus.SUCCEEDED
    assert outcome.evidence["clamped"] is True


def test_opening_a_url_is_reported_as_unknown_not_success(ctx, monkeypatch):
    """The shell accepted it. Whether a page loaded is not observable."""
    monkeypatch.setattr(win32, "shell_open", lambda _t: 42)
    outcome = handlers_module.open_url({"url": "https://example.com/page"}, ctx)
    assert outcome.status is ActionResultStatus.UNKNOWN
    assert "can observe" in outcome.detail
    assert outcome.error_category is ErrorCategory.EFFECT_UNVERIFIABLE


def test_a_url_outside_the_devices_own_allowlist_is_refused_before_any_call(ctx, monkeypatch):
    """The device re-validates: the server's allowlist is not the only one."""
    called = []
    monkeypatch.setattr(win32, "shell_open", lambda t: called.append(t))
    outcome = handlers_module.open_url({"url": "https://elsewhere.test/page"}, ctx)
    assert outcome.error_category is ErrorCategory.PARAMETERS_INVALID
    assert called == []


def test_opening_a_path_that_vanished_is_a_failure(ctx, monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(win32, "shell_open", lambda t: called.append(t))
    missing = tmp_path / "Documents" / "gone.pdf"
    outcome = handlers_module.open_path({"path": str(missing)}, ctx)
    assert outcome.error_category is ErrorCategory.PARAMETERS_INVALID
    assert called == []


def test_a_clipboard_write_is_verified_by_reading_it_back(ctx, monkeypatch):
    written: dict = {}
    monkeypatch.setattr(
        win32,
        "write_clipboard_text",
        lambda t: written.setdefault("text", t) or True,
    )
    monkeypatch.setattr(win32, "read_clipboard_text", lambda: written.get("text"))

    outcome = handlers_module.clipboard_write({"text": "an ordinary sentence"}, ctx)
    assert outcome.status is ActionResultStatus.SUCCEEDED
    assert "text" not in outcome.evidence
    assert outcome.evidence["text_length"] == len("an ordinary sentence")


def test_a_clipboard_write_that_did_not_stick_is_a_failure(ctx, monkeypatch):
    monkeypatch.setattr(win32, "write_clipboard_text", lambda _t: True)
    monkeypatch.setattr(win32, "read_clipboard_text", lambda: "something else entirely")
    outcome = handlers_module.clipboard_write({"text": "an ordinary sentence"}, ctx)
    assert outcome.status is ActionResultStatus.FAILED


def test_clipboard_content_does_not_leave_the_machine_by_default(ctx, monkeypatch):
    monkeypatch.setattr(win32, "read_clipboard_text", lambda: "a private note to self")
    assert ctx.config.clipboard_return_content is False
    outcome = handlers_module.clipboard_read({}, ctx)
    assert outcome.status is ActionResultStatus.SUCCEEDED
    assert "text" not in outcome.evidence
    assert outcome.evidence["text_length"] == len("a private note to self")
    assert outcome.evidence["content_returned"] is False


def test_clipboard_content_is_returned_only_on_an_explicit_opt_in(config, monkeypatch):
    from dataclasses import replace

    opted_in = HandlerContext(config=replace(config, clipboard_return_content=True))
    monkeypatch.setattr(win32, "read_clipboard_text", lambda: "a private note to self")
    outcome = handlers_module.clipboard_read({}, opted_in)
    assert outcome.evidence["text"] == "a private note to self"


def test_a_clipboard_holding_a_secret_is_refused_not_returned(ctx, monkeypatch):
    # Assembled from fragments so no line in this file matches a secret
    # scanner's signature; the detector under test sees the same string.
    synthetic = "AKIA" + "IOSFODNN" + "7EXAMPLE"
    monkeypatch.setattr(win32, "read_clipboard_text", lambda: synthetic)
    outcome = handlers_module.clipboard_read({}, ctx)
    assert outcome.status is ActionResultStatus.FAILED
    assert outcome.error_category is ErrorCategory.SENSITIVE_CONTENT
    assert synthetic not in repr(outcome.evidence)
    assert synthetic not in outcome.detail


def test_an_empty_clipboard_is_a_truthful_success(ctx, monkeypatch):
    monkeypatch.setattr(win32, "read_clipboard_text", lambda: None)
    outcome = handlers_module.clipboard_read({}, ctx)
    assert outcome.status is ActionResultStatus.SUCCEEDED
    assert outcome.evidence["has_text"] is False


# --- typing -------------------------------------------------------------------


def _field(monkeypatch, **kwargs):
    from bartholomew.windows_actuation import uia

    defaults = {
        "is_password": False,
        "name": "Search",
        "automation_id": "searchBox",
        "help_text": None,
        "control_type": uia.UIA_EDIT_CONTROL_TYPE,
    }
    defaults.update(kwargs)
    monkeypatch.setattr(uia, "focused_field", lambda: uia.FocusedField(**defaults))


def test_typing_refuses_when_the_accessibility_tree_cannot_be_read(ctx, monkeypatch):
    """A companion that cannot see where it is typing does not type."""
    from bartholomew.windows_actuation import uia

    monkeypatch.setattr(
        uia,
        "focused_field",
        lambda: uia.FocusedField(is_password=None, unavailable_reason="comtypes is absent"),
    )
    sent = []
    monkeypatch.setattr(win32, "send_unicode_text", lambda t: sent.append(t))

    outcome = handlers_module.type_text({"text": "hello there"}, ctx)
    assert outcome.error_category is ErrorCategory.ACCESSIBILITY_UNAVAILABLE
    assert sent == []


def test_typing_into_a_password_field_is_refused(ctx, monkeypatch):
    _field(monkeypatch, is_password=True, name="Password")
    sent = []
    monkeypatch.setattr(win32, "send_unicode_text", lambda t: sent.append(t))
    outcome = handlers_module.type_text({"text": "hello there"}, ctx)
    assert outcome.error_category is ErrorCategory.SENSITIVE_FIELD
    assert sent == []


@pytest.mark.parametrize(
    "label",
    ["PIN", "CVV", "Card number", "One-time code", "API key", "Recovery phrase"],
)
def test_typing_into_a_sensitive_looking_field_is_refused(ctx, monkeypatch, label):
    _field(monkeypatch, name=label)
    sent = []
    monkeypatch.setattr(win32, "send_unicode_text", lambda t: sent.append(t))
    outcome = handlers_module.type_text({"text": "hello there"}, ctx)
    assert outcome.error_category is ErrorCategory.SENSITIVE_FIELD
    assert sent == []


def test_typing_into_a_button_is_refused(ctx, monkeypatch):
    """Typing into a button is pressing it, not typing."""
    _field(monkeypatch, control_type=50000, name="Send")
    sent = []
    monkeypatch.setattr(win32, "send_unicode_text", lambda t: sent.append(t))
    outcome = handlers_module.type_text({"text": "hello there"}, ctx)
    assert outcome.error_category is ErrorCategory.SENSITIVE_FIELD
    assert sent == []


def test_typing_a_secret_is_refused_before_the_field_is_even_looked_at(ctx, monkeypatch):
    sent = []
    monkeypatch.setattr(win32, "send_unicode_text", lambda t: sent.append(t))
    outcome = handlers_module.type_text(
        {"text": "api" + "_key = " + "sk-" + "abcdefghijklmnopqrstuvwxyz012345"},
        ctx,
    )
    assert outcome.error_category is ErrorCategory.SENSITIVE_CONTENT
    assert sent == []


def test_a_full_type_is_reported_as_unknown_not_success(ctx, monkeypatch):
    """Windows accepted every event; where they landed is not observable."""
    _field(monkeypatch)
    monkeypatch.setattr(win32, "send_unicode_text", lambda t: len(t) * 2)
    outcome = handlers_module.type_text({"text": "hello"}, ctx)
    assert outcome.status is ActionResultStatus.UNKNOWN
    assert outcome.evidence["events_sent"] == 10
    assert "text" not in outcome.evidence


def test_a_partial_type_is_reported_as_failed(ctx, monkeypatch):
    _field(monkeypatch)
    monkeypatch.setattr(win32, "send_unicode_text", lambda _t: 4)
    outcome = handlers_module.type_text({"text": "hello"}, ctx)
    assert outcome.status is ActionResultStatus.FAILED
    assert "only 4 of 10" in outcome.detail


def test_accessibility_refuses_cleanly_when_the_adapter_is_unavailable(ctx, monkeypatch):
    from bartholomew.windows_actuation import uia

    monkeypatch.setattr(win32, "visible_windows", lambda: [_FakeWindow()])

    def _unavailable(**_kwargs):
        raise uia.AccessibilityUnavailableError("comtypes is absent")

    monkeypatch.setattr(uia, "perform", _unavailable)
    outcome = handlers_module.accessibility_action(
        {"app_id": "notepad", "operation": "expand", "element_name": "Details"},
        ctx,
    )
    assert outcome.error_category is ErrorCategory.ACCESSIBILITY_UNAVAILABLE


def test_accessibility_reports_the_adapters_own_refusal_truthfully(ctx, monkeypatch):
    from bartholomew.windows_actuation import uia

    monkeypatch.setattr(win32, "visible_windows", lambda: [_FakeWindow()])
    monkeypatch.setattr(
        uia,
        "perform",
        lambda **_k: (False, "3 elements are named 'Details'; refused rather than guessed"),
    )
    outcome = handlers_module.accessibility_action(
        {"app_id": "notepad", "operation": "expand", "element_name": "Details"},
        ctx,
    )
    assert outcome.status is ActionResultStatus.FAILED
    assert "guessed" in outcome.detail


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "this asserts the platform guard, which by definition does not fire on "
        "Windows -- where the handlers genuinely work and are covered instead by "
        "tests/integration/test_windows_action_real.py"
    ),
)
def test_every_handler_refuses_cleanly_off_windows(ctx, state):
    """No handler crashes, and none claims success, on a machine it cannot act on.

    Skipped on Windows, where the premise is false: `launch_app` really does
    launch, which is the point of the capability. Running it there asserted
    that a working handler had failed -- and started a real Notepad on the CI
    runner to do it.
    """
    parameters = {
        CapabilityKind.OPEN_URL: {"url": "https://example.com/x"},
        CapabilityKind.OPEN_PATH: {"path": str(ctx.config.filesystem_roots.roots[0])},
        CapabilityKind.LAUNCH_APP: {"app_id": "notepad"},
        CapabilityKind.FOCUS_WINDOW: {"app_id": "notepad"},
        CapabilityKind.MANAGE_WINDOW: {"app_id": "notepad", "operation": "maximize"},
        CapabilityKind.CLIPBOARD_READ: {},
        CapabilityKind.CLIPBOARD_WRITE: {"text": "an ordinary sentence"},
        CapabilityKind.TYPE_TEXT: {"text": "an ordinary sentence"},
        CapabilityKind.ACCESSIBILITY_ACTION: {
            "app_id": "notepad",
            "operation": "expand",
            "element_name": "Details",
        },
    }
    assert set(parameters) == set(ALL_CAPABILITIES)
    for kind, params in parameters.items():
        fresh = ActionCompanionState()
        outcome = dispatch(_action(kind.value, params), ctx, fresh)
        assert outcome.status is not ActionResultStatus.SUCCEEDED, kind.value
        assert outcome.error_category is not None, kind.value


# ---------------------------------------------------------------------------
# the ledger, and recording before reporting
# ---------------------------------------------------------------------------


def test_an_unreadable_ledger_refuses_everything_rather_than_starting_empty(tmp_path):
    """An empty ledger means "I have run nothing", which is a different claim."""
    path = tmp_path / "action-state.json"
    path.write_text("{not json at all", encoding="utf-8")
    with pytest.raises(LedgerUnreadableError) as excinfo:
        ActionStateFile(path).load()
    assert "not an empty one" in str(excinfo.value)


def test_an_absent_ledger_is_a_genuinely_fresh_install(tmp_path):
    state = ActionStateFile(tmp_path / "never-written.json").load()
    assert state.executed == {}


def test_the_ledger_round_trips_and_is_bounded(tmp_path):
    from bartholomew.windows_actuation.state import MAX_LEDGER_ENTRIES

    path = tmp_path / "action-state.json"
    file = ActionStateFile(path)
    state = ActionCompanionState()
    for i in range(MAX_LEDGER_ENTRIES + 50):
        state.executed[f"act-{i}"] = ExecutedEntry(f"act-{i}", "succeeded", "now")
    file.save(state)
    reloaded = file.load()
    assert len(reloaded.executed) == MAX_LEDGER_ENTRIES
    assert "act-0" not in reloaded.executed
    assert f"act-{MAX_LEDGER_ENTRIES + 49}" in reloaded.executed


def test_the_runner_records_before_it_reports(config, monkeypatch):
    """A crash between acting and reporting must not lose the fact of acting."""
    from bartholomew.windows_actuation.channel import ChannelResult, ChannelStatus
    from bartholomew.windows_actuation.runner import ActionCompanionRunner

    order: list[str] = []

    class _Client:
        device_id = DEVICE

        def lease(self, *, limit):
            return ChannelResult(ChannelStatus.OK, 200, {"actions": []}), [], []

        def report(self, *, action_id, outcome, observed_at):
            order.append("reported")
            assert (config.state_path).exists(), "the ledger was written first"
            return ChannelResult(ChannelStatus.OK, 200, {}, "")

    runner = ActionCompanionRunner(config, client=_Client(), sleep=lambda _s: None)
    original = runner._record_executed

    def _watched(action_id, outcome):
        order.append("recorded")
        return original(action_id, outcome)

    monkeypatch.setattr(runner, "_record_executed", _watched)
    runner.run_action(_action("windows.focus_window", {"app_id": "notepad"}))
    assert order == ["recorded", "reported"]


def test_an_unreported_outcome_is_resent_verbatim_and_never_upgraded(config):
    """A later process has no more information than the one that recorded it."""
    from bartholomew.windows_actuation.channel import ChannelResult, ChannelStatus
    from bartholomew.windows_actuation.runner import ActionCompanionRunner

    file = ActionStateFile(config.state_path)
    state = ActionCompanionState()
    state.executed["act-unreported"] = ExecutedEntry(
        action_id="act-unreported",
        status="unknown",
        observed_at=to_iso(utc_now()),
        reported=False,
    )
    file.save(state)

    sent: list = []

    class _Client:
        device_id = DEVICE

        def lease(self, *, limit):
            return ChannelResult(ChannelStatus.OK, 200, {"actions": []}), [], []

        def report(self, *, action_id, outcome, observed_at):
            sent.append((action_id, outcome.status))
            return ChannelResult(ChannelStatus.OK, 200, {}, "")

    runner = ActionCompanionRunner(config, client=_Client(), sleep=lambda _s: None)
    assert runner.resend_unreported() == 1
    assert sent == [("act-unreported", ActionResultStatus.UNKNOWN)]
    assert file.load().executed["act-unreported"].reported is True


def test_a_refused_channel_is_terminal_and_visible(config):
    """A credential problem is not retried into an outage."""
    from bartholomew.windows_actuation.channel import ChannelResult, ChannelStatus
    from bartholomew.windows_actuation.runner import ActionCompanionRunner

    attempts = []

    class _Client:
        device_id = DEVICE

        def lease(self, *, limit):
            attempts.append("lease")
            return (
                ChannelResult(ChannelStatus.REFUSED, 401, None, "no resolver installed"),
                [],
                [],
            )

        def report(self, **_kwargs):  # pragma: no cover - never reached
            raise AssertionError("nothing was leased, so nothing is reported")

    runner = ActionCompanionRunner(config, client=_Client(), sleep=lambda _s: None)
    summary = runner.run(cycles=2)
    assert attempts == ["lease", "lease"]
    assert summary.channel_refusals == 2
    assert summary.leased == 0


# ---------------------------------------------------------------------------
# the one structure whose layout cannot be checked on the CI that runs this
# ---------------------------------------------------------------------------


def test_the_input_structure_layout_is_arithmetically_right_on_both_architectures():
    """`sizeof(INPUT)` must be 40 on x64 and 28 on x86, or SendInput rejects everything.

    `ctypes.wintypes.DWORD` is 32 bits on Windows and 64 on Linux, so the real
    structure's size cannot be computed here -- which is exactly why this is
    worth checking. Rebuilt below from *explicitly sized* types, once per
    architecture, so the layout reasoning is verified on any platform and the
    Windows-only self-check in `win32.INPUT_STRUCT_SIZE` has something behind
    it.

    The union's largest member is MOUSEINPUT, not KEYBDINPUT. A union declaring
    only the keyboard member is eight bytes short on x64 and makes every
    keystroke fail.
    """
    import ctypes

    for pointer_size, expected in ((8, 40), (4, 28)):
        ulong_ptr = {8: ctypes.c_uint64, 4: ctypes.c_uint32}[pointer_size]

        class _Keyboard(ctypes.Structure):
            _fields_ = [
                ("wVk", ctypes.c_uint16),
                ("wScan", ctypes.c_uint16),
                ("dwFlags", ctypes.c_uint32),
                ("time", ctypes.c_uint32),
                ("dwExtraInfo", ulong_ptr),
            ]

        class _Unused(ctypes.Structure):
            _fields_ = [
                ("_a", ctypes.c_int32),
                ("_b", ctypes.c_int32),
                ("_c", ctypes.c_uint32),
                ("_d", ctypes.c_uint32),
                ("_e", ctypes.c_uint32),
                ("_f", ulong_ptr),
            ]

        class _Union(ctypes.Union):
            _fields_ = [("ki", _Keyboard), ("_unused", _Unused)]

        class _Input(ctypes.Structure):
            _fields_ = [("type", ctypes.c_uint32), ("union", _Union)]

        assert ctypes.sizeof(_Input) == expected, (
            f"INPUT is {ctypes.sizeof(_Input)} bytes with a {pointer_size}-byte "
            f"pointer; Win32 defines {expected}"
        )
        assert ctypes.sizeof(_Union) > ctypes.sizeof(
            _Keyboard,
        ), "the union must be larger than KEYBDINPUT alone, or SendInput rejects every event"


def test_the_expected_input_size_matches_this_architecture():
    import ctypes

    from bartholomew.windows_actuation.win32 import INPUT_STRUCT_SIZE

    assert INPUT_STRUCT_SIZE == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
