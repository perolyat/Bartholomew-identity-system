"""
Tests for bartholomew.kernel.scheduler.cadence (S5.2 Typed Cadence). See
docs/S5_2_TYPED_CADENCE_DESIGN.md for the design this implements.
Sec 13's verify plan is this file's first bullet: parse() round-trips for
all four types including malformed-input rejection for the two new ones,
compute_next_run() correctness for daily/weekly including a DST-transition
case, and compute_next_run() raising when tz=None is passed with a
daily/weekly cadence.
"""

from __future__ import annotations

import datetime

import pytest
from dateutil import tz as dateutil_tz

from bartholomew.kernel.scheduler import cadence

BRISBANE = dateutil_tz.gettz("Australia/Brisbane")  # no DST -- deterministic
NEW_YORK = dateutil_tz.gettz("America/New_York")  # observes DST


class TestParse:
    def test_every_unchanged(self):
        assert cadence.parse("every:900") == ("every", 900)

    def test_window_unchanged(self):
        assert cadence.parse("window:3600:2") == ("window", 3600, 2)

    def test_daily(self):
        assert cadence.parse("daily:08:00") == ("daily", 8, 0)
        assert cadence.parse("daily:23:59") == ("daily", 23, 59)
        assert cadence.parse("daily:00:00") == ("daily", 0, 0)

    def test_weekly(self):
        assert cadence.parse("weekly:0:08:00") == ("weekly", 0, 8, 0)
        assert cadence.parse("weekly:6:23:59") == ("weekly", 6, 23, 59)

    @pytest.mark.parametrize(
        "bad",
        [
            "daily:24:00",  # hour out of range
            "daily:08:60",  # minute out of range
            "daily:-1:00",  # negative hour
            "daily:08",  # missing minute
            "daily:08:00:00",  # too many parts
            "daily:aa:bb",  # non-numeric
        ],
    )
    def test_daily_rejects_malformed(self, bad):
        with pytest.raises(ValueError):
            cadence.parse(bad)

    @pytest.mark.parametrize(
        "bad",
        [
            "weekly:7:08:00",  # day_of_week out of range
            "weekly:-1:08:00",  # negative day_of_week
            "weekly:0:24:00",  # hour out of range
            "weekly:0:08:60",  # minute out of range
            "weekly:0:08",  # missing minute
            "weekly:0",  # missing hour/minute
        ],
    )
    def test_weekly_rejects_malformed(self, bad):
        with pytest.raises(ValueError):
            cadence.parse(bad)

    def test_unknown_type_rejected(self):
        with pytest.raises(ValueError):
            cadence.parse("monthly:1:08:00")

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            cadence.parse("")


class TestComputeNextRunBackwardCompat:
    def test_every_unaffected_by_tz_param(self):
        import random

        random.seed(1)
        without_tz = cadence.compute_next_run(None, None, "every:900", 1000, None)
        random.seed(1)
        with_tz_none = cadence.compute_next_run(None, None, "every:900", 1000, None, tz=None)
        assert without_tz == with_tz_none

    def test_window_unaffected_by_tz_param(self):
        without_tz = cadence.compute_next_run(None, None, "window:3600:2", 1000, None)
        with_tz_none = cadence.compute_next_run(None, None, "window:3600:2", 1000, None, tz=None)
        assert without_tz == with_tz_none


class TestComputeNextRunDailyWeekly:
    def test_daily_requires_tz(self):
        with pytest.raises(ValueError):
            cadence.compute_next_run(None, None, "daily:08:00", 1000, tz=None)

    def test_weekly_requires_tz(self):
        with pytest.raises(ValueError):
            cadence.compute_next_run(None, None, "weekly:0:08:00", 1000, tz=None)

    def test_daily_first_run_before_target_time_today(self):
        ref = datetime.datetime(2026, 8, 6, 6, 0, 0, tzinfo=BRISBANE)
        next_ts, window_state = cadence.compute_next_run(
            None,
            None,
            "daily:08:00",
            int(ref.timestamp()),
            tz=BRISBANE,
        )
        next_dt = datetime.datetime.fromtimestamp(next_ts, tz=BRISBANE)
        assert next_dt.date() == ref.date()
        assert (next_dt.hour, next_dt.minute, next_dt.second) == (8, 0, 0)
        assert window_state is None

    def test_daily_first_run_after_target_time_today_rolls_to_tomorrow(self):
        ref = datetime.datetime(2026, 8, 6, 10, 0, 0, tzinfo=BRISBANE)
        next_ts, _ = cadence.compute_next_run(
            None,
            None,
            "daily:08:00",
            int(ref.timestamp()),
            tz=BRISBANE,
        )
        next_dt = datetime.datetime.fromtimestamp(next_ts, tz=BRISBANE)
        assert next_dt.date() == ref.date() + datetime.timedelta(days=1)

    def test_daily_subsequent_run_always_steps_one_day_from_scheduled_ts(self):
        scheduled = datetime.datetime(2026, 8, 6, 8, 0, 0, tzinfo=BRISBANE)
        scheduled_ts = int(scheduled.timestamp())
        # now_ts is irrelevant once scheduled_ts is set -- anchored to
        # scheduled_ts to avoid drift, mirroring the `every` branch.
        next_ts, _ = cadence.compute_next_run(
            scheduled_ts,
            scheduled_ts,
            "daily:08:00",
            scheduled_ts + 300,
            tz=BRISBANE,
        )
        next_dt = datetime.datetime.fromtimestamp(next_ts, tz=BRISBANE)
        assert next_dt.date() == scheduled.date() + datetime.timedelta(days=1)
        assert (next_dt.hour, next_dt.minute) == (8, 0)

    def test_daily_never_returns_a_time_in_the_past(self):
        ref = datetime.datetime(2026, 8, 6, 6, 0, 0, tzinfo=BRISBANE)
        next_ts, _ = cadence.compute_next_run(
            None,
            None,
            "daily:08:00",
            int(ref.timestamp()),
            tz=BRISBANE,
        )
        assert next_ts > int(ref.timestamp())

    def test_weekly_first_run_lands_on_requested_day_of_week(self):
        thursday = datetime.datetime(2026, 8, 6, 6, 0, 0, tzinfo=BRISBANE)
        assert thursday.weekday() == 3  # sanity check on the fixture date
        next_ts, _ = cadence.compute_next_run(
            None,
            None,
            "weekly:0:08:00",
            int(thursday.timestamp()),
            tz=BRISBANE,
        )
        next_dt = datetime.datetime.fromtimestamp(next_ts, tz=BRISBANE)
        assert next_dt.weekday() == 0  # Monday
        assert next_dt > thursday

    def test_weekly_first_run_same_day_before_target_time_stays_today(self):
        monday_early = datetime.datetime(2026, 8, 3, 6, 0, 0, tzinfo=BRISBANE)
        assert monday_early.weekday() == 0
        next_ts, _ = cadence.compute_next_run(
            None,
            None,
            "weekly:0:08:00",
            int(monday_early.timestamp()),
            tz=BRISBANE,
        )
        next_dt = datetime.datetime.fromtimestamp(next_ts, tz=BRISBANE)
        assert next_dt.date() == monday_early.date()

    def test_weekly_first_run_same_day_after_target_time_rolls_a_full_week(self):
        monday_late = datetime.datetime(2026, 8, 3, 10, 0, 0, tzinfo=BRISBANE)
        assert monday_late.weekday() == 0
        next_ts, _ = cadence.compute_next_run(
            None,
            None,
            "weekly:0:08:00",
            int(monday_late.timestamp()),
            tz=BRISBANE,
        )
        next_dt = datetime.datetime.fromtimestamp(next_ts, tz=BRISBANE)
        assert (next_dt.date() - monday_late.date()).days == 7

    def test_weekly_subsequent_run_always_steps_seven_days(self):
        monday = datetime.datetime(2026, 8, 3, 8, 0, 0, tzinfo=BRISBANE)
        scheduled_ts = int(monday.timestamp())
        next_ts, _ = cadence.compute_next_run(
            scheduled_ts,
            scheduled_ts,
            "weekly:0:08:00",
            scheduled_ts + 300,
            tz=BRISBANE,
        )
        next_dt = datetime.datetime.fromtimestamp(next_ts, tz=BRISBANE)
        assert (next_dt.date() - monday.date()).days == 7

    def test_daily_no_jitter_exact_to_the_minute(self):
        """S5.2 design doc Sec 4: deliberately no jitter/BARTH_SPEED_FACTOR
        scaling for daily/weekly -- the result must be exact, not a range."""
        scheduled = datetime.datetime(2026, 8, 6, 8, 0, 0, tzinfo=BRISBANE)
        scheduled_ts = int(scheduled.timestamp())
        results = {
            cadence.compute_next_run(
                scheduled_ts,
                scheduled_ts,
                "daily:08:00",
                scheduled_ts + 1,
                tz=BRISBANE,
            )[0]
            for _ in range(5)
        }
        assert len(results) == 1  # deterministic, not jittered

    def test_daily_across_a_dst_spring_forward_transition(self):
        """America/New_York springs forward 2026-03-08 02:00 -> 03:00.
        A daily:02:30 cadence scheduled the day before must still land on
        a valid, later wall-clock time the next day (accepted DST
        limitation, design doc Sec 4 -- not required to be exactly 24h
        later in absolute terms, just a valid forward step)."""
        before_dst = datetime.datetime(2026, 3, 7, 2, 30, 0, tzinfo=NEW_YORK)
        scheduled_ts = int(before_dst.timestamp())
        next_ts, _ = cadence.compute_next_run(
            scheduled_ts,
            scheduled_ts,
            "daily:02:30",
            scheduled_ts + 300,
            tz=NEW_YORK,
        )
        next_dt = datetime.datetime.fromtimestamp(next_ts, tz=NEW_YORK)
        assert next_dt > before_dst
        assert next_dt.date() == before_dst.date() + datetime.timedelta(days=1)

    def test_weekly_across_a_dst_transition(self):
        before_dst = datetime.datetime(2026, 3, 2, 8, 0, 0, tzinfo=NEW_YORK)  # a Monday
        assert before_dst.weekday() == 0
        scheduled_ts = int(before_dst.timestamp())
        next_ts, _ = cadence.compute_next_run(
            scheduled_ts,
            scheduled_ts,
            "weekly:0:08:00",
            scheduled_ts + 300,
            tz=NEW_YORK,
        )
        next_dt = datetime.datetime.fromtimestamp(next_ts, tz=NEW_YORK)
        assert next_dt.weekday() == 0
        assert (next_dt.date() - before_dst.date()).days == 7
