"""
Quiet hours are evaluated in Bartholomew's configured timezone, not the host's.

`NotifySkill._is_quiet_hours()` read a naive `datetime.now()`, so quiet hours
followed whatever zone the machine was clocked to. With `config/kernel.yaml`
set to Australia/Brisbane and a host on UTC -- an ordinary server setup, and
the exact configuration of this development container -- that is ten hours
out. A 09:22 reminder was suppressed as though it were 23:22.

This matters beyond tidiness: quiet hours gate whether the slice 2 proactive
reminder is *delivered* or *deferred*, so an attended Band 0 checkpoint
measuring "did the reminder arrive when I expected" would have been measuring
the host's clock rather than the behaviour under test.

These tests pin the timezone, so they do not depend on when they are run --
the failure mode PR #66 already demonstrated once.
"""

from __future__ import annotations

import pathlib
from datetime import datetime, timezone

import pytest
from dateutil import tz

from bartholomew.kernel import time_utils
from bartholomew.skills.notify import NotifySkill

BRISBANE = tz.gettz("Australia/Brisbane")  # UTC+10, no DST


@pytest.fixture(autouse=True)
def _clear_cache():
    time_utils.reset_timezone_cache()
    yield
    time_utils.reset_timezone_cache()


def _skill(start: str = "22:00", end: str = "07:00") -> NotifySkill:
    skill = NotifySkill.__new__(NotifySkill)
    skill._quiet_hours_start = start
    skill._quiet_hours_end = end
    return skill


def _at(monkeypatch, moment: datetime) -> None:
    """Pin the configured-zone clock to an exact instant."""
    monkeypatch.setattr(
        "bartholomew.skills.notify.now_in_configured_timezone",
        lambda: moment,
    )


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------


def test_quiet_hours_follow_the_configured_zone_not_the_host(monkeypatch):
    """
    The reproduction. 23:22 UTC is 09:22 in Brisbane: inside quiet hours by
    the host's clock, plainly outside them by the user's.
    """
    utc_moment = datetime(2026, 8, 26, 23, 22, tzinfo=timezone.utc)
    brisbane_moment = utc_moment.astimezone(BRISBANE)

    assert brisbane_moment.strftime("%H:%M") == "09:22"
    assert utc_moment.strftime("%H:%M") == "23:22"

    _at(monkeypatch, brisbane_moment)
    assert (
        _skill()._is_quiet_hours() is False
    ), "09:22 in the user's zone is not quiet hours, whatever the host clock says"


def test_quiet_hours_are_honoured_in_the_configured_zone(monkeypatch):
    """The converse: 13:00 UTC is 23:00 Brisbane -- genuinely quiet hours."""
    moment = datetime(2026, 8, 26, 13, 0, tzinfo=timezone.utc).astimezone(BRISBANE)
    assert moment.strftime("%H:%M") == "23:00"

    _at(monkeypatch, moment)
    assert _skill()._is_quiet_hours() is True


def test_the_skill_does_not_read_a_naive_clock():
    """Pins the fix: a bare datetime.now() must not return to this method."""
    import inspect

    source = inspect.getsource(NotifySkill._is_quiet_hours)
    body = source.split('"""')[-1]
    assert "datetime.now()" not in body
    assert "now_in_configured_timezone()" in body


# ---------------------------------------------------------------------------
# Window arithmetic, in the configured zone
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("local_time", "expected"),
    [
        ("21:59", False),  # just before
        ("22:00", True),  # boundary, inclusive
        ("23:30", True),  # inside, before midnight
        ("03:00", True),  # inside, after midnight
        ("06:59", True),  # last quiet minute
        ("07:00", False),  # boundary, exclusive
        ("12:00", False),  # plainly daytime
    ],
)
def test_overnight_window_boundaries(monkeypatch, local_time, expected):
    hour, minute = (int(part) for part in local_time.split(":"))
    moment = datetime(2026, 8, 26, hour, minute, tzinfo=BRISBANE)
    _at(monkeypatch, moment)
    assert _skill()._is_quiet_hours() is expected


@pytest.mark.parametrize(
    ("local_time", "expected"),
    [("12:30", True), ("11:59", False), ("14:00", False)],
)
def test_same_day_window_still_works(monkeypatch, local_time, expected):
    """A non-overnight window (12:00-14:00) uses the other branch."""
    hour, minute = (int(part) for part in local_time.split(":"))
    moment = datetime(2026, 8, 26, hour, minute, tzinfo=BRISBANE)
    _at(monkeypatch, moment)
    assert _skill("12:00", "14:00")._is_quiet_hours() is expected


# ---------------------------------------------------------------------------
# The timezone authority itself
# ---------------------------------------------------------------------------


def test_configured_timezone_reads_the_canonical_config_key():
    name = time_utils.configured_timezone_name()
    config = (pathlib.Path(__file__).resolve().parents[1] / "config" / "kernel.yaml").read_text(
        encoding="utf-8",
    )
    assert name is not None
    assert name in config, "must come from config/kernel.yaml, not be invented"
    assert time_utils.now_in_configured_timezone().tzinfo is not None


def test_fallback_is_utc_never_the_host_zone(monkeypatch):
    """
    An unreadable or missing config must give a stated answer, not one that
    varies with the machine. Same rule drives.py::_today_for() states.
    """
    monkeypatch.setattr(time_utils, "configured_timezone_name", lambda: None)
    time_utils.reset_timezone_cache()
    assert time_utils.configured_timezone() is timezone.utc


def test_an_unresolvable_zone_name_falls_back_to_utc(monkeypatch):
    monkeypatch.setattr(time_utils, "configured_timezone_name", lambda: "Not/AZone")
    time_utils.reset_timezone_cache()
    assert time_utils.configured_timezone() is timezone.utc


def test_configured_timezone_tracks_a_changed_config(monkeypatch):
    """The memo must not pin a stale zone after the config changes."""
    monkeypatch.setattr(time_utils, "configured_timezone_name", lambda: "Australia/Brisbane")
    time_utils.reset_timezone_cache()
    first = time_utils.configured_timezone()

    monkeypatch.setattr(time_utils, "configured_timezone_name", lambda: "Europe/London")
    second = time_utils.configured_timezone()

    assert first is not second, "a changed config must not return the memoised zone"
