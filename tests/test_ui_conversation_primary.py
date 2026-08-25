"""
Capability D: conversation is the primary way to use Bartholomew.

The property under test throughout is that the panel must not claim more than
the system can actually support -- a conversation surface implying a
capability the backend lacks is worse than a plainer one that tells the
truth.

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
# Capability D -- conversation first
# ---------------------------------------------------------------------------


def test_conversation_is_the_first_thing_in_the_ordinary_view(ui_source: str):
    """The primary control surface should be what an ordinary session opens on."""
    home = ui_source[ui_source.index('<section id="view-home"') :]
    home = home[: home.index("</section>")]
    # The first card in the ordinary view must be the chat card itself.
    first_card = re.search(r'<div class="card"[^>]*>', home)
    assert first_card, "the ordinary view has no cards"
    assert 'id="chat-card"' in first_card.group(0), (
        f"the first card in the ordinary view is not the conversation: {first_card.group(0)}"
    )


def test_composer_supports_more_than_one_line(ui_source: str):
    """A single-line <input> quietly discourages saying anything substantial."""
    assert '<textarea id="msg"' in ui_source


def test_enter_sends_and_shift_enter_makes_a_newline(ui_code: str):
    body = _function_body(ui_code, "chatKeydown")
    assert "event.shiftKey" in body
    assert "preventDefault" in body
    assert "sendMsg()" in body


def test_composing_with_an_ime_does_not_send_early(ui_code: str):
    """Enter during IME composition selects a candidate; it must not send."""
    assert "isComposing" in _function_body(ui_code, "chatKeydown")


def test_a_pending_request_shows_a_thinking_state_that_always_clears(ui_code: str):
    """
    The indicator must reflect a genuinely outstanding request, and must clear
    on the failure path too -- a stuck "thinking" would be its own untruth.
    """
    send = _function_body(ui_code, "sendMsg")
    assert "setThinking(true)" in send
    assert "finally" in send
    finally_block = send[send.rindex("finally") :]
    assert "setThinking(false)" in finally_block


def test_waiting_items_are_surfaced_beside_the_conversation(ui_code: str):
    """
    Routine interaction should not require hunting through panels to notice
    something is waiting.
    """
    body = _function_body(ui_code, "renderChatAttention")
    assert "consent" in body and "nudges" in body and "awaiting" in body
    # Hidden when there is nothing waiting, rather than showing a zero.
    assert 'el.style.display = "none"' in body
