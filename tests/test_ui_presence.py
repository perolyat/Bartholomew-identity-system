"""
Capability E: Bartholomew has a persistent presence, and it tells the truth.

The presence area is the one part of the interface that represents *him*
rather than the application around him, so its correctness property is
strict: every state it displays must come from a real reading. A presence
indicator that looks calm while the kernel is unreachable, or that shows
"speaking" because a reply arrived rather than because anything is actually
speaking, would be worse than no presence at all.

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
# Capability E -- presence must be truthful
# ---------------------------------------------------------------------------


def test_presence_is_outside_both_views(ui_source: str):
    """He is present throughout the application, not owned by one panel."""
    header = ui_source[ui_source.index("<header") : ui_source.index("</header>")]
    assert 'id="presence"' in header


def test_presence_never_defaults_to_available_when_nothing_is_known(ui_code: str):
    """
    The failure that would matter: a calm-looking indicator while the system
    has no idea what state it is in.
    """
    body = _function_body(ui_code, "resolvePresence")
    assert "presenceHealth === null" in body
    unknown_index = body.index("presenceHealth === null")
    available_index = body.rindex('state: "available"')
    assert (
        unknown_index < available_index
    ), "the unknown case must be resolved before falling through to available"


def test_presence_reports_a_halted_system_as_paused(ui_code: str):
    body = _function_body(ui_code, "resolvePresence")
    assert "presenceBrake.engaged" in body
    assert '"paused"' in body


def test_presence_reports_an_unreadable_safety_state_as_unknown(ui_code: str):
    body = _function_body(ui_code, "resolvePresence")
    assert "presenceBrake.unreachable" in body
    brake_unknown = body[body.index("presenceBrake.unreachable") :]
    assert '"unknown"' in brake_unknown[:200]


def test_presence_reports_an_unreachable_model_as_degraded(ui_code: str):
    """model_status is the field health() says to gate on, not model_real."""
    body = _function_body(ui_code, "resolvePresence")
    assert "model_status" in body
    assert '"degraded"' in body


def test_safety_state_outranks_activity_state(ui_code: str):
    """
    A brake that is engaged must not be masked by a chat request happening to
    be in flight.
    """
    body = _function_body(ui_code, "resolvePresence")
    assert body.index("presenceBrake.engaged") < body.index('presenceOverride === "thinking"')


def test_presence_mark_and_label_read_the_same_state(ui_code: str):
    """They cannot disagree if they are set from one resolved value."""
    body = _function_body(ui_code, "renderPresence")
    assert "resolvePresence()" in body
    assert "stateEl.dataset.state = state" in body
    assert "avatar.dataset.state = state" in body


def test_speaking_state_is_never_asserted_without_a_voice_backend(ui_code: str):
    """
    Voice output belongs to the parallel capability sprint. The branch exists
    as a wiring seam, but nothing may set it today -- showing "speaking" on
    reply delivery would invent a distinction the system cannot make.
    """
    assert 'setPresence("speaking")' not in ui_code
    assert "setPresence('speaking')" not in ui_code


def test_presence_animation_respects_reduced_motion(ui_source: str):
    """
    The presence mark is the one thing on the page that moves on its own.
    There are several reduced-motion blocks; at least one must cover it.
    """
    blocks = [
        ui_source[m.end() : m.end() + 300]
        for m in re.finditer(r"prefers-reduced-motion", ui_source)
    ]
    assert blocks, "no reduced-motion handling at all"
    assert any(
        "presence" in b for b in blocks
    ), "the presence animation is not disabled under prefers-reduced-motion"
