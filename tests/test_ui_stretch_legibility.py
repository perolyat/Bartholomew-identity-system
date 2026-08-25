"""
Stretch work: awaiting-response legibility (F1/F2), first-use orientation
(F3), and low-risk accessibility (F4).

The awaiting-response queue's *authority* is not touched by any of this --
it remains the canonical durable obligation mechanism, written only through
the runtime contract. What changed is how it reads and how it is resolved.

Page-side assertions are structural; see
tests/test_ui_test1_defect_regressions.py for why.
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
def ui_code(ui_source: str) -> str:
    script = re.search(r"<script>(.*)</script>", ui_source, re.S).group(1)
    script = re.sub(r"/\*.*?\*/", "", script, flags=re.S)
    return re.sub(r"^\s*//.*$", "", script, flags=re.M)


def _function_body(script: str, name: str) -> str:
    match = re.search(rf"function\s+{re.escape(name)}\s*\(", script)
    assert match, f"{name}() not found"
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


# ---------------------------------------------------------------------------
# F1 -- awaiting-response legibility
# ---------------------------------------------------------------------------


def test_obligation_status_is_plain_language(ui_code: str):
    """A raw status enum was doing the work of telling someone they were late."""
    assert "AWAITING_STATUS" in ui_code
    for internal, plain in (("open", "waiting"), ("reminded", "reminded you")):
        assert internal in ui_code and plain in ui_code


def test_a_deadline_is_described_in_both_directions(ui_code: str):
    body = _function_body(ui_code, "describeDue")
    assert "overdue by" in body
    assert "due today" in body
    assert "due in" in body


def test_a_missing_deadline_is_not_treated_as_urgent(ui_code: str):
    """Absence of a due date must sort last, not first."""
    body = _function_body(ui_code, "dueMillis")
    assert "MAX_SAFE_INTEGER" in body
    # Infinity would make the comparator's subtraction produce NaN.
    assert "Infinity" not in body


def test_an_unparseable_deadline_does_not_become_a_date(ui_code: str):
    body = _function_body(ui_code, "describeDue")
    assert "Number.isNaN" in body


def test_overdue_obligations_are_ordered_first(ui_code: str):
    body = _function_body(ui_code, "refreshAwaitingResponse")
    assert "entries.sort" in body
    assert "dueMillis" in body


# ---------------------------------------------------------------------------
# F1 -- resolution records what actually happened
# ---------------------------------------------------------------------------


def test_resolution_reason_is_a_real_choice(ui_code: str):
    """
    The store distinguishes reply_received from user_dismissed -- the
    difference between an obligation that was met and one that was dropped.
    Every resolution used to be recorded as user_dismissed regardless.
    """
    body = _function_body(ui_code, "resolveAwaitingResponse")
    assert "resolution ||" in body, "the caller must be able to supply the reason"
    assert "reply_received" in ui_code
    assert "user_dismissed" in ui_code


def test_both_resolutions_are_offered_per_item(ui_code: str):
    body = _function_body(ui_code, "renderAwaitingItem")
    assert "'reply_received'" in body
    assert "'user_dismissed'" in body


def test_there_is_no_bulk_discard(ui_code: str):
    """
    These are genuine obligations. Grouping and clearer status are safe;
    resolving many at once in a single click is not, and is deliberately
    absent.
    """
    for dangerous in ("resolveAll", "dismissAll", "clearAllAwaiting", "bulkResolve"):
        assert dangerous not in ui_code


# ---------------------------------------------------------------------------
# F3 -- first use
# ---------------------------------------------------------------------------


def test_first_use_guidance_is_in_the_conversation_not_a_modal(ui_source: str):
    """
    The page used to open with a full-screen deployment-guidance modal that
    had to be dismissed before the app could be used at all.
    """
    assert 'id="first-use"' in ui_source
    home = ui_source[ui_source.index('<section id="view-home"') :]
    chat_card = home[home.index('id="chat-card"') :]
    assert 'id="first-use"' in chat_card[:3000], "orientation belongs with the conversation"

    # The first-run modal function is deliberately kept (so restoring the
    # interrupt is a one-line change) but must never be *invoked* on load.
    script = re.search(r"<script>(.*)</script>", ui_source, re.S).group(1)
    script = re.sub(r"^\s*//.*$", "", script, flags=re.M)
    calls = [
        m for m in re.finditer(r"maybeShowOnboardingModal\(\)", script)
        if not script[: m.start()].rstrip().endswith("function")
    ]
    assert not calls, "the first-run modal must not open over the page on load"


def test_first_use_names_where_to_talk_state_memory_and_safety(ui_source: str):
    block = ui_source[ui_source.index('id="first-use"') :]
    block = block[: block.index("</div>", block.index("</ul>"))]
    assert "talk to Bartholomew" in block
    assert "Parking Brake" in block
    assert "remembers" in block
    assert "Workshop" in block


def test_first_use_capability_claim_comes_from_a_real_reading(ui_code: str):
    """It must not assert what he can do; it must report what health says."""
    body = _function_body(ui_code, "renderFirstUseCapability")
    assert "presenceHealth" in body
    assert "model_status" in body
    assert "kernel_online" in body


def test_first_use_clears_itself_rather_than_needing_dismissal(ui_code: str):
    body = _function_body(ui_code, "appendChatTurn")
    assert 'getElementById("first-use")' in body
    assert "remove()" in body


# ---------------------------------------------------------------------------
# F4 -- accessibility
# ---------------------------------------------------------------------------


def test_keyboard_focus_is_visible(ui_source: str):
    """
    Several controls here engage or release a safety halt, and one deletes a
    memory permanently. Tabbing to them must be visible.
    """
    assert ":focus-visible" in ui_source
    assert "outline" in ui_source


def test_interactive_controls_are_labelled(ui_source: str):
    for control in ('id="msg"', 'id="memory-search"', 'id="memory-kind"'):
        index = ui_source.index(control)
        tag = ui_source[index - 200 : index + 300]
        assert "aria-label" in tag, f"{control} has no accessible name"


def test_live_regions_announce_changing_state(ui_source: str):
    assert 'id="chatlog"' in ui_source
    chatlog = ui_source[ui_source.index('id="chatlog"') - 60 : ui_source.index('id="chatlog"') + 120]
    assert "aria-live" in chatlog
    presence = ui_source[ui_source.index('id="presence"') : ui_source.index('id="presence"') + 200]
    assert "aria-live" in presence or 'role="status"' in presence


def test_layout_adapts_to_a_narrow_screen(ui_source: str):
    assert "@media (max-width" in ui_source


def _uncommented(source: str) -> str:
    return re.sub(r"<!--.*?-->", "", source, flags=re.S)
