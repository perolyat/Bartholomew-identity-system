"""
Usable POC slice 2 -- the pure noticing logic
(`bartholomew.kernel.schedule_noticing`).

This module is the half of the slice that decides *what is due*. It is pure:
no clock, no I/O, no model. These tests pin the two properties that matter
most for a capability whose whole output is an unprompted interruption:

  1. **It never guesses.** Text it cannot parse as an absolute date yields no
     reminder at all. A wrongly-dated reminder is worse than a missing one --
     it trains the user to distrust every reminder.
  2. **Its identity is the commitment, not the wording.** Two renderings of
     the same (fact, due date) share an identity; two different due dates do
     not. That is what stops the containment policy from either splitting one
     obligation across two queue rows or merging two into one.

The drive that consumes this -- registration, governance, delivery and the
delivery-outcome record -- is tested in tests/test_schedule_reminder_drive.py.
"""

from __future__ import annotations

from datetime import date

import pytest

from bartholomew.kernel import schedule_noticing as sn

TODAY = date(2026, 6, 1)


def _row(kind: str, key: str, text: str, memory_id: int = 1) -> dict:
    return {"id": memory_id, "kind": kind, "key": key, "value": text, "summary": None}


class TestAbsoluteDateParsing:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            # The shapes slice 1's extractor actually produces, verified
            # against personal_facts._SCHEDULE_RE's rendering
            # ("<Event>: on <when>").
            ("Car rego: on 5 June", date(2026, 6, 5)),
            ("Car rego: due June 5", date(2026, 6, 5)),
            ("Dentist appointment: on the 5th of June", date(2026, 6, 5)),
            ("Rego: on June 5th", date(2026, 6, 5)),
            ("Rego: on 5th June 2026", date(2026, 6, 5)),
            ("Rego: on June 5, 2026", date(2026, 6, 5)),
            ("Inspection: on 2026-09-01", date(2026, 9, 1)),
            ("Rego: on 5 Jun", date(2026, 6, 5)),
            ("Conference: on 12 Sept", date(2026, 9, 12)),
            # Day-first, matching the deployment's configured timezone.
            ("Rego: on 5/6", date(2026, 6, 5)),
            ("Rego: on 5/6/2026", date(2026, 6, 5)),
            ("Rego: on 5/6/26", date(2026, 6, 5)),
            ("Rego: on 25/12", date(2026, 12, 25)),
        ],
    )
    def test_parses_absolute_forms(self, text, expected):
        assert sn.parse_due_date(text, TODAY) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "   ",
            # Relative forms are deliberately NOT parsed -- see the module
            # docstring. A frozen quotation of "on Friday" is only meaningful
            # relative to when it was said.
            "Dentist: on Friday",
            "Dentist: tomorrow",
            "Dentist: next week",
            "Standup: at 5pm",
            "Gym: every Tuesday",
            # Not a date, and must not be coerced into one.
            "My car is a Corolla",
            "Rego: on Junuary 5",
            # Unreadable day-first, so no answer rather than a retry the
            # other way round.
            "Rego: on 25/13",
        ],
    )
    def test_returns_none_rather_than_guessing(self, text):
        assert sn.parse_due_date(text, TODAY) is None

    def test_undated_month_day_resolves_to_the_next_occurrence(self):
        """An undated "3rd March" stated in June means next March, not a
        date four months in the past."""
        assert sn.parse_due_date("Birthday: 3rd March", TODAY) == date(2027, 3, 3)

    def test_undated_date_still_ahead_this_year_stays_this_year(self):
        assert sn.parse_due_date("Rego: on 5 June", TODAY) == date(2026, 6, 5)

    def test_today_itself_counts_as_still_ahead(self):
        assert sn.parse_due_date("Rego: on 1 June", TODAY) == TODAY

    def test_an_explicit_past_year_is_honoured_not_rolled_forward(self):
        """Year inference applies only where the user stated no year. A date
        the user pinned is reported as stated -- select_due() then declines to
        surface it, which is a different decision made in a different place."""
        assert sn.parse_due_date("Rego: on 5 June 2020", TODAY) == date(2020, 6, 5)

    def test_impossible_calendar_dates_yield_nothing(self):
        assert sn.parse_due_date("Rego: on 31 February", TODAY) is None
        assert sn.parse_due_date("Rego: on 2026-02-30", TODAY) is None

    def test_parsing_is_deterministic(self):
        first = sn.parse_due_date("Car rego: on 5 June", TODAY)
        second = sn.parse_due_date("Car rego: on 5 June", TODAY)
        assert first == second


class TestWhichRowsAreEvenLookedAt:
    def test_user_schedule_is_noticeable(self):
        assert sn.is_noticeable_row("user_schedule", "car_rego") is True

    def test_user_profile_is_narrowed_to_its_one_date_bearing_key(self):
        assert sn.is_noticeable_row("user_profile", "birthday") is True
        # A standing attribute must never be read as something falling due,
        # even when its text happens to contain a date-like fragment.
        assert sn.is_noticeable_row("user_profile", "car") is False
        assert sn.is_noticeable_row("user_profile", "preference.tea") is False

    def test_unrelated_kinds_are_ignored(self):
        assert sn.is_noticeable_row("competency", "anything") is False
        assert sn.is_noticeable_row(None, None) is False

    def test_a_dated_profile_attribute_produces_no_reminder(self):
        rows = [_row("user_profile", "lease_note", "Lease note: signed 5 June")]
        assert sn.select_due(rows, TODAY) == []


class TestWindowSelection:
    def test_a_fact_inside_the_window_is_noticed(self):
        rows = [_row("user_schedule", "car_rego", "Car rego: on 3 June")]
        noticed = sn.select_due(rows, TODAY, look_ahead_days=3)
        assert len(noticed) == 1
        assert noticed[0].due_date == date(2026, 6, 3)

    def test_a_fact_beyond_the_window_is_not(self):
        rows = [_row("user_schedule", "car_rego", "Car rego: on 30 June")]
        assert sn.select_due(rows, TODAY, look_ahead_days=3) == []

    def test_the_window_boundary_is_inclusive_on_both_ends(self):
        today_row = _row("user_schedule", "a", "A: on 1 June", memory_id=1)
        edge_row = _row("user_schedule", "b", "B: on 4 June", memory_id=2)
        just_past = _row("user_schedule", "c", "C: on 5 June", memory_id=3)
        noticed = sn.select_due([today_row, edge_row, just_past], TODAY, look_ahead_days=3)
        assert [item.key for item in noticed] == ["a", "b"]

    def test_overdue_items_are_not_surfaced(self):
        rows = [_row("user_schedule", "old", "Old: on 5 June 2020")]
        assert sn.select_due(rows, TODAY, look_ahead_days=3) == []

    def test_unparseable_facts_are_skipped_without_affecting_the_rest(self):
        rows = [
            _row("user_schedule", "vague", "Vague: on Friday", memory_id=1),
            _row("user_schedule", "rego", "Rego: on 3 June", memory_id=2),
        ]
        noticed = sn.select_due(rows, TODAY, look_ahead_days=3)
        assert [item.key for item in noticed] == ["rego"]

    def test_results_are_closest_due_first(self):
        rows = [
            _row("user_schedule", "later", "Later: on 3 June", memory_id=1),
            _row("user_schedule", "sooner", "Sooner: on 2 June", memory_id=2),
        ]
        noticed = sn.select_due(rows, TODAY, look_ahead_days=3)
        assert [item.key for item in noticed] == ["sooner", "later"]

    def test_the_per_tick_cap_defers_rather_than_discards(self):
        """Overflow is not dropped: it is simply not noticed this tick, and
        the same call with a larger cap still sees every one of them."""
        rows = [
            _row("user_schedule", f"item{n}", f"Item {n}: on {n + 1} June", memory_id=n)
            for n in range(1, 4)
        ]
        capped = sn.select_due(rows, TODAY, look_ahead_days=5, limit=2)
        assert len(capped) == 2
        uncapped = sn.select_due(rows, TODAY, look_ahead_days=5, limit=10)
        assert len(uncapped) == 3

    def test_summary_is_preferred_over_value_when_present(self):
        row = _row("user_schedule", "rego", "ignored")
        row["summary"] = "Car rego: on 3 June"
        noticed = sn.select_due([row], TODAY, look_ahead_days=3)
        assert noticed[0].text == "Car rego: on 3 June"

    def test_empty_input_is_handled(self):
        assert sn.select_due([], TODAY) == []
        assert sn.select_due(None, TODAY) == []


class TestReminderIdentityAndRendering:
    def _reminder(self, key: str, text: str) -> sn.NoticedReminder:
        rows = [_row("user_schedule", key, text)]
        noticed = sn.select_due(rows, TODAY, look_ahead_days=5)
        assert noticed, f"expected {text!r} to be noticed"
        return noticed[0]

    def test_identity_is_fact_plus_due_date_not_message_text(self):
        """Restating a fact rewords it in place (`upsert_memory()`), which
        must not manufacture a second obligation for the same commitment."""
        first = self._reminder("car_rego", "Car rego: on 3 June")
        reworded = self._reminder("car_rego", "Car registration renewal: on 3 June")
        assert first.identity == reworded.identity
        assert first.message != reworded.message

    def test_a_different_due_date_is_a_different_obligation(self):
        june = self._reminder("car_rego", "Car rego: on 3 June")
        july = sn.select_due(
            [_row("user_schedule", "car_rego", "Car rego: on 3 July")],
            TODAY,
            look_ahead_days=40,
        )[0]
        assert june.identity != july.identity

    def test_a_different_fact_on_the_same_day_is_a_different_obligation(self):
        rego = self._reminder("car_rego", "Car rego: on 3 June")
        dentist = self._reminder("dentist", "Dentist: on 3 June")
        assert rego.identity != dentist.identity

    def test_identity_is_stable_across_calls(self):
        assert (
            self._reminder("car_rego", "Car rego: on 3 June").identity
            == self._reminder("car_rego", "Car rego: on 3 June").identity
        )

    def test_message_quotes_the_stored_fact_and_names_the_date(self):
        reminder = self._reminder("car_rego", "Car rego: on 3 June")
        assert reminder.message == "Reminder: Car rego: on 3 June — due 3 June 2026"

    def test_date_rendering_avoids_platform_specific_strftime(self):
        """`%-d` is a glibc extension; this project's matrix covers Windows."""
        assert sn.format_due_date(date(2026, 6, 3)) == "3 June 2026"
        assert sn.format_due_date(date(2026, 12, 25)) == "25 December 2026"

    def test_days_until_is_reported_from_the_caller_s_today(self):
        assert self._reminder("car_rego", "Car rego: on 3 June").days_until(TODAY) == 2
