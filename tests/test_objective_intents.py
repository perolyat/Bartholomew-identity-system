"""
Recognising objectives in ordinary speech -- and, mostly, not recognising them.

The asymmetry this suite exists to pin: a missed objective costs the user one
sentence to restate. A wrongly-recognised objective becomes a durable record
that then interrupts them about something they never asked for -- which is
exactly the burden Real-World Test #1 found Bartholomew adding. So the
recogniser is conservative, and the "does not fire" cases matter at least as
much as the ones that do.
"""

from __future__ import annotations

from datetime import date

import pytest

from bartholomew.kernel import objective_intents as oi
from bartholomew.kernel.objective_store import (
    EVENT_ACTION,
    EVENT_DECISION,
    EVENT_FACT,
    EVENT_PROPOSAL,
    HORIZON_BY_DATE,
    HORIZON_OPEN,
    HORIZON_THIS_WEEK,
)

TODAY = date(2026, 8, 27)


class _Stub:
    """Minimal stand-in for a stored Objective/ObjectiveEvent."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def _objective(title, **kw):
    return _Stub(
        title=title,
        horizon_kind=kw.pop("horizon_kind", HORIZON_OPEN),
        horizon_date=kw.pop("horizon_date", None),
        status=kw.pop("status", "active"),
        last_surfaced_at=kw.pop("last_surfaced_at", None),
        **kw,
    )


def _event(kind, summary, provenance=None):
    return _Stub(event_kind=kind, summary=summary, provenance=provenance or {})


class TestEstablishing:
    @pytest.mark.parametrize(
        "utterance",
        [
            "I need to get the roof repaired",
            "I want to get the roof repaired",
            "I'm trying to get the roof repaired",
            "my objective is to get the roof repaired",
            "keep track of getting the roof repaired",
        ],
    )
    def test_explicit_establishment_is_recognised(self, utterance):
        intent = oi.parse_intent(utterance, TODAY)
        assert intent is not None
        assert intent.action == oi.INTENT_OPEN
        assert "roof" in intent.title

    def test_the_handoffs_own_example(self):
        """'The roofer needs to come this week' -- the sentence this whole
        slice was specified against."""
        intent = oi.parse_intent("The roofer needs to come this week", TODAY)
        assert intent is not None
        assert intent.action == oi.INTENT_OPEN
        assert "roofer" in intent.title
        assert intent.horizon_kind == HORIZON_THIS_WEEK
        # The horizon is lifted out of the title, not left duplicated in it.
        assert "this week" not in intent.title.lower()

    def test_the_users_own_words_are_kept_as_the_outcome_statement(self):
        intent = oi.parse_intent("The roofer needs to come this week", TODAY)
        assert intent.outcome_statement == "The roofer needs to come this week"


class TestHorizons:
    def test_this_week_stays_this_week(self):
        """Not silently converted to a date the user never named."""
        intent = oi.parse_intent("I need to get the roof repaired this week", TODAY)
        assert intent.horizon_kind == HORIZON_THIS_WEEK
        assert intent.horizon_date is None

    @pytest.mark.parametrize(
        ("phrase", "expected"),
        [
            ("by 5 September", "2026-09-05"),
            ("by September 5", "2026-09-05"),
            ("by 2026-09-05", "2026-09-05"),
        ],
    )
    def test_an_explicit_date_becomes_a_date(self, phrase, expected):
        intent = oi.parse_intent(f"I need to get the roof repaired {phrase}", TODAY)
        assert intent.horizon_kind == HORIZON_BY_DATE
        assert intent.horizon_date == expected

    def test_a_past_month_day_rolls_forward_to_next_year(self):
        intent = oi.parse_intent("I need to get the roof repaired by 5 March", TODAY)
        assert intent.horizon_date == "2027-03-05"

    def test_an_unreadable_date_produces_no_date_rather_than_a_guess(self):
        intent = oi.parse_intent("I need to get the roof repaired by the end of winter", TODAY)
        assert intent is not None
        assert intent.horizon_kind == HORIZON_OPEN
        assert intent.horizon_date is None

    def test_an_impossible_date_is_refused(self):
        intent = oi.parse_intent("I need to sort the roof by 31 February", TODAY)
        assert intent.horizon_date is None

    def test_no_horizon_at_all_is_fine(self):
        intent = oi.parse_intent("I need to get the roof repaired", TODAY)
        assert intent.horizon_kind == HORIZON_OPEN


class TestNotFiring:
    @pytest.mark.parametrize(
        "utterance",
        [
            "hello there",
            "what's the weather like tomorrow?",
            "add a task to ring the roofer",
            "thanks, that's helpful",
            "the roof is leaking again",
            "roofers are expensive these days",
            "",
            "   ",
        ],
    )
    def test_ordinary_conversation_produces_nothing(self, utterance):
        assert oi.parse_intent(utterance, TODAY) is None

    @pytest.mark.parametrize(
        "utterance",
        [
            "should I get the roof repaired?",
            "do I need to get the roof repaired?",
            "what if I need to get the roof repaired",
            "maybe I need to get the roof repaired",
            "I was thinking I need to get the roof repaired",
            "I might need to get the roof repaired",
        ],
    )
    def test_questions_and_musings_are_not_objectives(self, utterance):
        """Wondering aloud is not commissioning work. Recognising these is
        precisely how a passing thought becomes a durable nag."""
        assert oi.parse_intent(utterance, TODAY) is None


class TestClosing:
    @pytest.mark.parametrize(
        "utterance",
        [
            "the roof is done",
            "the roof is sorted",
            "the roof has been fixed",
            "I've finished the roof",
            "I have sorted the roof",
            "the roof went ahead",
        ],
    )
    def test_completion_is_recognised(self, utterance):
        intent = oi.parse_intent(utterance, TODAY)
        assert intent is not None
        assert intent.action == oi.INTENT_COMPLETE
        assert "roof" in intent.subject

    def test_completion_is_never_mistaken_for_a_new_objective(self):
        """The worst possible failure: the sentence that ends an objective
        creating a fresh one to nag about instead."""
        for utterance in ("the roof is sorted", "I've finished the roof"):
            assert oi.parse_intent(utterance, TODAY).action == oi.INTENT_COMPLETE

    @pytest.mark.parametrize(
        "utterance",
        [
            "forget the roof",
            "drop the roof",
            "cancel the roof",
            "we're not bothering with the roof",
            "I'm not doing the roof any more",
        ],
    )
    def test_abandonment_is_recognised_and_distinct_from_completion(self, utterance):
        intent = oi.parse_intent(utterance, TODAY)
        assert intent is not None
        assert intent.action == oi.INTENT_ABANDON


class TestAsking:
    @pytest.mark.parametrize(
        "utterance",
        [
            "what am I working on?",
            "what am I working towards?",
            "what am I trying to achieve?",
            "what am I trying to get done?",
            "list my objectives",
            "show me my objectives",
            "what are my objectives?",
        ],
    )
    def test_asking_what_is_outstanding_is_recognised(self, utterance):
        intent = oi.parse_intent(utterance, TODAY)
        assert intent is not None
        assert intent.action == oi.INTENT_LIST

    @pytest.mark.parametrize(
        "utterance",
        [
            "what's on my plate?",
            "what's outstanding?",
            "where are things up to?",
        ],
    )
    def test_task_shaped_questions_are_left_for_task_control_and_the_model(self, utterance):
        """These read equally as questions about tasks. Claiming them here
        both steals the turn from task control and stops an ordinary
        question ever reaching the model -- a regression this pinned after
        `test_api_chat_runtime_contract.py` caught it."""
        assert oi.parse_intent(utterance, TODAY) is None


class TestMatching:
    def test_an_exact_title_matches(self):
        objectives = [_objective("get the roof repaired"), _objective("book the car service")]
        assert oi.match_objective("get the roof repaired", objectives).title == (
            "get the roof repaired"
        )

    def test_a_containment_subject_matches_the_one_candidate(self):
        objectives = [_objective("get the roof repaired"), _objective("book the car service")]
        matched = oi.match_objective("get the roof", objectives)
        assert matched is not None
        assert matched.title == "get the roof repaired"

    def test_two_equally_plausible_candidates_match_nothing(self):
        """Asking beats guessing, especially when the action is 'stop
        pursuing this forever'."""
        objectives = [_objective("get the roof repaired"), _objective("get the roof repaired")]
        assert oi.match_objective("get the roof repaired", objectives) is None

    def test_a_subject_matching_two_titles_by_containment_matches_nothing(self):
        objectives = [_objective("fix the roof"), _objective("fix the roof")]
        assert oi.match_objective("fix the roof", objectives) is None

    def test_an_unrelated_subject_matches_nothing(self):
        objectives = [_objective("get the roof repaired")]
        assert oi.match_objective("book the car service", objectives) is None

    def test_no_candidates_match_nothing(self):
        assert oi.match_objective("anything", []) is None
        assert oi.match_objective("", [_objective("x")]) is None


class TestRelevance:
    def test_an_utterance_sharing_a_substantive_word_relates(self):
        objective = _objective("get the roof repaired")
        assert oi.relates_to(objective, "will it rain on the roof job tomorrow?") is True

    def test_short_words_alone_do_not_make_a_match(self):
        objective = _objective("get the roof repaired")
        assert oi.relates_to(objective, "get the milk") is False

    def test_an_unrelated_utterance_does_not_relate(self):
        objective = _objective("get the roof repaired")
        assert oi.relates_to(objective, "what's the capital of France?") is False


class TestContinuityRendering:
    def test_the_summary_is_built_from_the_events(self):
        objective = _objective("get the roof repaired", horizon_kind=HORIZON_THIS_WEEK)
        events = [
            _event(EVENT_DECISION, "going with the second quote"),
            _event(EVENT_ACTION, "rang the roofer"),
        ]
        rendered = oi.render_continuity(objective, events)
        assert "get the roof repaired" in rendered
        assert "this week" in rendered
        assert "going with the second quote" in rendered
        assert "rang the roofer" in rendered

    def test_a_proposal_is_never_rendered_as_something_that_happened(self):
        """A considered idea and a completed action must never share a
        bullet -- once they do, the reader cannot tell them apart."""
        objective = _objective("get the roof repaired")
        events = [
            _event(EVENT_ACTION, "rang the roofer"),
            _event(EVENT_PROPOSAL, "could ring a second roofer"),
        ]
        rendered = oi.render_continuity(objective, events)
        assert "rang the roofer" in rendered
        assert "could ring a second roofer" not in rendered

    def test_external_evidence_is_attributed_not_asserted(self):
        objective = _objective("get the roof repaired")
        events = [
            _event(
                EVENT_FACT,
                "rain likely Thursday",
                {"provider_host": "api.open-meteo.com", "evidence": True},
            ),
        ]
        rendered = oi.render_continuity(objective, events)
        assert "according to api.open-meteo.com" in rendered

    def test_nothing_new_says_so_rather_than_padding(self):
        objective = _objective("get the roof repaired")
        rendered = oi.render_continuity(objective, [], since="2026-08-26T00:00:00Z")
        assert "nothing has changed" in rendered.lower()

    def test_a_fresh_objective_says_nothing_has_happened_yet(self):
        rendered = oi.render_continuity(_objective("get the roof repaired"), [])
        assert "nothing has happened" in rendered.lower()

    def test_the_reengagement_invites_the_user_to_close_it(self):
        """The fastest way to stop hearing about an objective should always
        be to say it's done."""
        rendered = oi.render_reengagement(_objective("get the roof repaired"), [])
        assert "sorted" in rendered.lower()


class TestListRendering:
    def test_an_empty_list_says_so_plainly(self):
        assert "Nothing at the moment" in oi.render_list([])

    def test_live_objectives_are_listed_with_their_horizons(self):
        rendered = oi.render_list(
            [
                _objective("get the roof repaired", horizon_kind=HORIZON_THIS_WEEK),
                _objective("book the car service", status="blocked"),
            ],
        )
        assert "get the roof repaired this week" in rendered
        assert "book the car service (blocked)" in rendered


class TestPurity:
    def test_the_module_performs_no_io(self):
        """Same discipline task_intents and forecast_intents hold to."""
        import inspect

        source = inspect.getsource(oi)
        for forbidden in ("import sqlite3", "import requests", "urllib", "open(", "httpx"):
            assert forbidden not in source

    def test_parsing_is_deterministic_for_a_given_today(self):
        """'Today' is a parameter, not a clock read, so the same utterance
        on the same nominal date always yields the same horizon."""
        first = oi.parse_intent("I need to sort the roof by 5 September", date(2026, 8, 27))
        second = oi.parse_intent("I need to sort the roof by 5 September", date(2026, 8, 27))
        third = oi.parse_intent("I need to sort the roof by 5 September", date(2027, 1, 1))
        assert first.horizon_date == second.horizon_date == "2026-09-05"
        assert third.horizon_date == "2027-09-05"
