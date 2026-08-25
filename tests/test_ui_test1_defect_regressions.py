"""
Regression tests for the user-facing defects Real-World Test #1 recorded
against the minimal UI, and for the API contract one of them depends on.

Scope note: the product surface here is a single static page
(`bartholomew_api_bridge_v0_1/ui/minimal/index.html`) driven by `fetch`.
There is no JavaScript test runner in this repository and adding one is out
of this sprint's lane, so the page-side assertions below are structural: they
pin the specific broken constructs Test #1 caught, so a reintroduction fails
here rather than in the next live test. The API-side assertion (MF-F001) is a
real HTTP test against the running app.

Defects covered:

* TECH-F001 -- a non-2xx `/api/chat` response rendered as the literal string
  `undefined`, because the page read `j.reply` without ever checking `r.ok`.
* PB-F002  -- dependent governed panels stayed stale after a Parking Brake
  transition until each was refreshed by hand.
* MF-F001  -- drive activations rendered `0.00` and attention fields read
  empty, because the page read field names the API does not emit.
* MF-F003  -- enabled hydration controls called endpoints that do not exist.
"""

from __future__ import annotations

import pathlib
import re

import pytest

UI_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "bartholomew_api_bridge_v0_1"
    / "ui"
    / "minimal"
    / "index.html"
)


@pytest.fixture(scope="module")
def ui_source() -> str:
    return UI_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ui_script(ui_source: str) -> str:
    """Just the <script> body, so prose in HTML comments cannot satisfy a test."""
    match = re.search(r"<script>(.*)</script>", ui_source, re.S)
    assert match, "minimal UI has no <script> block"
    return match.group(1)


def _strip_js_comments(script: str) -> str:
    """Remove // and /* */ comments so explanatory prose is not matched as code."""
    script = re.sub(r"/\*.*?\*/", "", script, flags=re.S)
    return re.sub(r"^\s*//.*$", "", script, flags=re.M)


@pytest.fixture(scope="module")
def ui_code(ui_script: str) -> str:
    return _strip_js_comments(ui_script)


# ---------------------------------------------------------------------------
# TECH-F001 -- truthful chat failure rendering
# ---------------------------------------------------------------------------


def test_chat_checks_response_ok_before_reading_reply(ui_code: str):
    """
    The exact shape of TECH-F001: `await r.json()` straight into `j.reply`
    with no `r.ok` check. fetch() resolves normally on a 503, so the reply
    field is absent and `undefined` reaches the transcript.
    """
    send = _function_body(ui_code, "sendMsg")
    assert "r.ok" in send, "sendMsg() must check response.ok before trusting the body"

    ok_index = send.index("r.ok")
    reply_index = send.index("j.reply")
    assert ok_index < reply_index, "the r.ok check must come before j.reply is read"


def test_chat_never_renders_a_missing_reply_as_a_reply(ui_code: str):
    """A 2xx with no usable reply is a defect to report, not a blank message."""
    send = _function_body(ui_code, "sendMsg")
    assert (
        "typeof j.reply !== 'string'" in send
    ), "sendMsg() must reject a non-string reply rather than rendering it"


def test_chat_failures_render_as_a_distinct_error_turn(ui_code: str, ui_source: str):
    """
    A failure must be visually distinguishable from an answer. The old
    renderer appended every line to one text node in identical monospace.
    """
    send = _function_body(ui_code, "sendMsg")
    assert "appendChatTurn('error'" in send, "chat failures must render as an error turn"
    assert ".turn-error" in ui_source, "error turns need their own styling"


def test_http_failure_description_handles_non_string_detail(ui_code: str):
    """
    FastAPI validation errors carry a *list* of objects in `detail`.
    Stringifying that yields "[object Object]" -- another untruthful render.
    """
    body = _function_body(ui_code, "describeHttpFailure")
    assert "Array.isArray" in body, "detail may be a list; it must not be stringified blindly"


# ---------------------------------------------------------------------------
# PB-F002 -- dependent-state freshness after a brake transition
# ---------------------------------------------------------------------------


def test_brake_transition_revalidates_dependent_panels(ui_code: str):
    """
    A brake transition must invalidate the panels whose content it changes,
    without the user pressing each panel's own Refresh button.
    """
    body = _function_body(ui_code, "revalidateBrakeDependents")
    for dependent in (
        "refreshNotificationSettings",
        "refreshPendingConsent",
        "refreshAwaitingResponse",
        "refreshAudit",
    ):
        assert dependent in body, f"{dependent}() must be revalidated after a brake transition"


def test_brake_revalidation_is_driven_by_observed_revision(ui_code: str):
    """
    Revalidating only after this page's own button click would miss a
    transition made from another tab, the CLI, or curl. The trigger must be
    the observed `revision` changing.
    """
    body = _function_body(ui_code, "refreshBrake")
    assert "brakeRevisionRendered" in body
    assert "revalidateBrakeDependents" in body


def test_brake_reading_is_timestamped(ui_code: str):
    """
    UI-SYNC001b leaves the numeric critical-state freshness bound
    deliberately unresolved. Rather than inventing one, the page stamps the
    reading with when it was taken, so its age is visible.
    """
    assert "stampBrakeReading" in ui_code
    body = _function_body(ui_code, "stampBrakeReading")
    assert "toLocaleTimeString" in body


def test_unreadable_brake_state_is_not_rendered_as_disengaged(ui_code: str):
    """
    The one rendering a safety control must never produce: an unreachable
    status shown in the green "all clear" state.
    """
    body = _function_body(ui_code, "refreshBrake")
    catch = body[body.index("catch") :]
    assert "UNKNOWN" in catch
    assert "kernel-offline" in catch


# ---------------------------------------------------------------------------
# MF-F001 -- contract/render mapping
# ---------------------------------------------------------------------------


def test_ui_reads_the_attention_field_names_the_api_emits(ui_code: str):
    """
    AttentionState.to_dict() emits focus_target / focus_type /
    focus_intensity. The page read target / type / intensity and silently
    rendered its fallbacks for all three.
    """
    body = _function_body(ui_code, "refreshSelfState")
    assert "attention.focus_target" in body
    assert "attention.focus_type" in body
    assert "attention.focus_intensity" in body


def test_drive_activation_never_silently_defaults_to_zero(ui_code: str):
    """
    `d.effective_activation || d.activation || 0` read two names the API did
    not emit and fell through to 0, so every drive displayed 0.00 while the
    kernel held real activations.
    """
    body = _function_body(ui_code, "driveActivation")
    assert "effective_activation" in body
    # The stored fields must be a real fallback, not another dead name.
    assert "current_activation" in body
    assert "context_boost" in body


def test_drive_state_effective_activation_is_a_derived_method_not_a_field():
    """
    Pins the root cause: the persistence serialiser cannot emit this, which
    is why the API layer has to add it.
    """
    from bartholomew.kernel.experience_kernel import DriveState

    drive = DriveState(drive_id="d", current_activation=0.5, context_boost=0.07)
    assert "effective_activation" not in drive.to_dict()
    assert drive.effective_activation() == pytest.approx(0.57)


# ---------------------------------------------------------------------------
# MF-F003 -- dead hydration controls
# ---------------------------------------------------------------------------


def test_ordinary_ui_has_no_hydration_controls(ui_source: str, ui_code: str):
    """
    D4 puts hydration outside ordinary scope. The controls called endpoints
    that do not exist: every click 404'd and the total read "undefined ml".
    """
    for dead in ("addWater(", "refreshWater()", "api/water/today", "api/water/log"):
        assert dead not in ui_code, f"{dead} is a dead hydration control and must not be callable"
    assert 'id="total"' not in ui_source, "the hydration total element must be gone"


def test_hydration_endpoints_are_still_absent_from_the_api():
    """
    A4 removes the *controls*. It does not add the endpoints, drop tables, or
    delete historical rows -- that disposition is separate under D4.
    """
    from bartholomew_api_bridge_v0_1.services.api.app import app

    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/api/water/today" not in paths
    assert "/api/water/log" not in paths


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _function_body(script: str, name: str) -> str:
    """
    Return the source of a top-level `function name(...)` declaration by
    brace matching, so assertions cannot accidentally match a neighbour.
    """
    match = re.search(rf"function\s+{re.escape(name)}\s*\(", script)
    assert match, f"{name}() not found in the UI script"
    start = script.index("{", match.end() - 1)
    depth = 0
    for i in range(start, len(script)):
        if script[i] == "{":
            depth += 1
        elif script[i] == "}":
            depth -= 1
            if depth == 0:
                return script[start : i + 1]
    raise AssertionError(f"unbalanced braces in {name}()")
