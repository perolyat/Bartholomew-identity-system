"""
Capability B: the ordinary Bartholomew experience is separated from
engineering/admin instrumentation.

The minimal UI used to be one flat column of identically styled cards, with
the conversation sitting between a pending-nudge queue and a raw
drive-activation readout. These tests pin the separation, and -- more
importantly -- pin that safety information was *not* demoted by it.

As in tests/test_ui_test1_defect_regressions.py, the page-side assertions are
structural because this repository has no JavaScript test runner; see that
module's docstring.
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


def _section(source: str, section_id: str) -> str:
    """Return the markup of one top-level <section id=...> by tag matching."""
    match = re.search(rf'<section id="{re.escape(section_id)}"', source)
    assert match, f"section {section_id} not found"
    depth = 0
    i = match.start()
    for tag in re.finditer(r"</?section\b", source[i:]):
        depth += 1 if not tag.group(0).startswith("</") else -1
        if depth == 0:
            return source[i : i + tag.end()]
    raise AssertionError(f"unbalanced <section> for {section_id}")


# ---------------------------------------------------------------------------
# The two views exist and are addressable
# ---------------------------------------------------------------------------


def test_both_views_exist(ui_source: str):
    assert '<section id="view-home"' in ui_source
    assert '<section id="view-workshop"' in ui_source


def test_workshop_is_hidden_by_default(ui_source: str):
    """An ordinary session must open on Bartholomew, not on the instrumentation."""
    match = re.search(r'<section id="view-workshop"[^>]*>', ui_source)
    assert match and "hidden" in match.group(0)


def test_views_are_addressable_by_hash(ui_source: str):
    """A view should survive a reload and be linkable."""
    assert "viewFromHash" in ui_source
    assert "hashchange" in ui_source


# ---------------------------------------------------------------------------
# What belongs where
# ---------------------------------------------------------------------------


def test_conversation_is_in_the_ordinary_view(ui_source: str):
    home = _section(ui_source, "view-home")
    assert 'id="chatlog"' in home
    assert 'id="chat-send"' in home


def test_engineering_internals_are_not_in_the_ordinary_view(ui_source: str):
    """
    Raw drive internals, affect sliders, episode/persona instrumentation,
    database and health JSON do not help a user accomplish anything.
    """
    home = _section(ui_source, "view-home")
    for internal in (
        'id="drives-list"',  # raw drive activations
        'id="affect-valence-fill"',  # affect sliders
        'id="episodes-list"',  # episode instrumentation
        'id="persona-list"',  # persona switching
        'id="health"',  # raw health JSON
        'id="audit-list"',  # governance event log
    ):
        assert internal not in home, f"{internal} belongs in Workshop, not the ordinary view"


def test_engineering_internals_are_still_reachable_in_workshop(ui_source: str):
    """Separation of attention, not deletion. Nothing was removed."""
    workshop = _section(ui_source, "view-workshop")
    for internal in (
        'id="drives-list"',
        'id="affect-valence-fill"',
        'id="episodes-list"',
        'id="persona-list"',
        'id="health"',
        'id="audit-list"',
    ):
        assert internal in workshop, f"{internal} must still be reachable in Workshop"


# ---------------------------------------------------------------------------
# Safety must not be demoted -- the constraint that matters most here
# ---------------------------------------------------------------------------


def test_parking_brake_stays_in_the_ordinary_view(ui_source: str):
    """
    Explicitly not hidden or weakened: the brake control and its state stay
    on the surface an ordinary user actually opens.
    """
    home = _section(ui_source, "view-home")
    assert 'id="brake-status"' in home
    assert "engageBrake()" in home
    assert "disengageBrake()" in home
    assert 'class="brake-scope"' in home


def test_brake_state_is_visible_from_every_view(ui_source: str):
    """
    The presence strip lives in the page header, outside both sections, so
    brake state is on screen even while looking at Workshop.
    """
    assert 'id="presence-safety"' in ui_source
    for section_id in ("view-home", "view-workshop"):
        assert 'id="presence-safety"' not in _section(ui_source, section_id), (
            "presence must sit outside the view sections so it shows on both"
        )


def test_privacy_controls_stay_in_the_ordinary_view(ui_source: str):
    """Consent inbox and notification/quiet-hours control are user controls."""
    home = _section(ui_source, "view-home")
    assert 'id="pending-consent-list"' in home
    assert 'id="notif-status"' in home


def test_brake_scopes_are_explained_in_plain_language(ui_source: str):
    """
    HU-F006: the scope checkboxes' effects were unclear, including that
    ticking nothing engages globally.
    """
    home = _section(ui_source, "view-home")
    assert "What do these scopes stop?" in home
    assert "globally" in home
