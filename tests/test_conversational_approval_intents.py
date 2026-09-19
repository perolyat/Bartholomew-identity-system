"""
R-EXEC02-2, the decision seam: what counts as a human granting permission, and
the much longer list of things that look like one and are not.

Pure tests over `bartholomew/kernel/approval_intents.py`. No database, no
daemon, no authority --- this module decides nothing except whether a string is,
in its entirety, a decision. The governed half is
`tests/test_conversational_approval.py`.

The property that matters here is asymmetric: a false negative costs the person
one more word, and a false positive causes something to happen on their machine
that they did not authorise. Every ambiguous construction must resolve to *not
a decision*.
"""

from __future__ import annotations

import pytest

from bartholomew.kernel import approval_intents as ai
from bartholomew.kernel import goal_intents, task_intents


class TestAnAffirmativeIsRecognised:
    @pytest.mark.parametrize(
        "text",
        [
            "yes",
            "Yes",
            "yes please",
            "Yes, please.",
            "yep",
            "yeah",
            "go ahead",
            "Go ahead.",
            "please go ahead",
            "do it",
            "Do it!",
            "do that",
            "please do",
            "go for it",
            "approve",
            "approve it",
            "approved",
            "I approve",
            "yes go ahead",
            "yes do it",
            "Bartholomew, yes",
            "hey bartholomew, go ahead",
            "yes thanks",
            "go ahead then",
        ],
    )
    def test_it_is_an_approval(self, text):
        decision = ai.parse_decision(text)
        assert decision is not None, text
        assert decision.kind == ai.DECISION_APPROVE

    def test_the_persons_own_words_are_carried_unaltered(self):
        decision = ai.parse_decision("  Yes,   please. ")
        # Whitespace is collapsed; nothing else is. The utterance is evidence
        # of the decision and a paraphrase of it would be Bartholomew's account
        # of what he was told rather than what he was told.
        assert decision.utterance == "Yes, please."
        assert decision.matched == "yes"


class TestARejectionIsRecognised:
    @pytest.mark.parametrize(
        "text",
        [
            "no",
            "No.",
            "no thanks",
            "no thank you",
            "nope",
            "don't",
            "dont",
            "do not",
            "don't do it",
            "do not do that",
            "cancel",
            "cancel it",
            "cancel that",
            "reject",
            "reject it",
            "forget it",
            "leave it",
            "drop it",
            "never mind",
            "nevermind",
            "no don't",
        ],
    )
    def test_it_is_a_rejection(self, text):
        decision = ai.parse_decision(text)
        assert decision is not None, text
        assert decision.kind == ai.DECISION_REJECT


class TestAffirmativeWordsAreNotApproval:
    """The central safety property. Every one of these contains an
    affirmative, or the shape of one, and none of them authorises anything."""

    @pytest.mark.parametrize(
        "text",
        [
            # Questions about approving.
            "If I say yes, what happens?",
            "Do I have to say yes?",
            "Should I approve it?",
            "What does approving actually do?",
            "yes?",
            "Can I just say yes to these?",
            # Quotation and discussion of the word.
            'Does "yes" approve it?',
            'Is saying "go ahead" enough?',
            # Hypothetical and qualified.
            "I would say yes but I want to think about it",
            "Yes, if you can do it without closing my other windows",
            "Yes and no, really",
            "yes - but change the folder first",
            # An affirmative answering something else entirely.
            "Yes, I remember that file",
            "yes that's the one I meant earlier",
            "no idea what that does",
            "No, the roofer is on Tuesday",
            # Ordinary conversation containing the words.
            "I need to approve my expenses this week",
            "Go ahead and tell me more about how you plan things",
            "Cancel culture is exhausting",
            "Don't worry about it, I was just curious",
            # Discussion of the proposal itself, which must stay discussion.
            "What is that step going to do exactly?",
            "Why did you pick notepad?",
            "That seems reasonable",
            "Interesting, tell me more",
        ],
    )
    def test_it_does_not_authorise_anything(self, text):
        assert ai.parse_decision(text) is None, text

    def test_a_wall_of_text_containing_yes_is_not_a_decision(self):
        assert ai.parse_decision("yes " * 40) is None

    def test_an_empty_turn_is_not_a_decision(self):
        assert ai.parse_decision("") is None
        assert ai.parse_decision("   ") is None
        assert ai.parse_decision(None) is None


class TestTheStatusQuestionIsNotADecision:
    @pytest.mark.parametrize(
        "text",
        [
            "did it work?",
            "Did it work",
            "did that work?",
            "is it done?",
            "has it run?",
            "what happened?",
            "how did that go?",
            "did you do it?",
        ],
    )
    def test_it_asks_rather_than_decides(self, text):
        decision = ai.parse_decision(text)
        assert decision is not None, text
        assert decision.kind == ai.DECISION_STATUS

    def test_asking_authorises_nothing_and_withdraws_nothing(self):
        # Stated as a property of the vocabulary rather than of one string: the
        # status set and the two decision sets are disjoint, so no question can
        # ever be read as a grant.
        assert not (ai._STATUS_PHRASES & ai._APPROVE_PHRASES)
        assert not (ai._STATUS_PHRASES & ai._REJECT_PHRASES)
        assert not (ai._APPROVE_PHRASES & ai._REJECT_PHRASES)


class TestWeakAcknowledgementsAreExcludedOnPurpose:
    """A person saying "ok" is often still thinking. This is a deliberate
    false negative and it is pinned so nobody "improves" it later."""

    @pytest.mark.parametrize(
        "text",
        ["ok", "okay", "sure", "fine", "sounds good", "right", "mm", "alright"],
    )
    def test_it_is_not_an_approval(self, text):
        assert ai.parse_decision(text) is None, text


class TestItCannotStealTurnsFromTheOtherRecognisers:
    """The approval entry sits first in `_CHAT_DISPATCH`, which is only safe
    because a decision is the *whole* utterance. Anything carrying content of
    its own falls through to the recogniser that has always owned it."""

    @pytest.mark.parametrize(
        "text",
        [
            "add a task to ring the roofer",
            "delete the shopping task",
            "yes, open notepad for me",
            "no, sort these files out instead",
            "cancel the roofer task",
            "Start a shopping list for me.",
        ],
    )
    def test_a_turn_with_content_is_not_claimed_here(self, text):
        assert ai.parse_decision(text) is None, text

    def test_an_instruction_still_reaches_its_own_recogniser(self):
        assert task_intents.parse_intent("add a task to ring the roofer") is not None
        assert goal_intents.parse_intent("Start a shopping list for me.") is not None


class TestRenderingIsTruthful:
    #: Phrases no renderer on this surface may produce. Approving is not
    #: executing, executing is not succeeding, and a device that could not
    #: observe its own effect leaves the action `unknown`.
    COMPLETION_CLAIMS = (
        "done for you",
        "it has worked",
        "has already run",
        "i've done",
        "i have done",
        "all set",
        "sorted for you",
        "successfully",
        "that's finished",
    )

    def _every_renderer_output(self):
        return [
            ai.render_armed("act-1"),
            ai.render_not_armed(),
            ai.render_ambiguous(2),
            ai.render_stale("it changed."),
            ai.render_approved("act-1", "pc-1"),
            ai.render_rejected("act-1"),
            ai.render_rejected_after_lease("act-1"),
            ai.render_refused("because"),
            ai.render_brake(),
            ai.render_ineligible("because"),
            ai.render_nothing_to_report(),
            *[ai.render_status("act-1", state) for state in ai._STATUS_SENTENCES],
            ai.render_status("act-1", "a state nobody has heard of"),
        ]

    def test_no_renderer_claims_work_is_finished_merely_because_it_was_approved(self):
        for text in self._every_renderer_output():
            lowered = text.lower()
            for claim in self.COMPLETION_CLAIMS:
                assert claim not in lowered, f"{claim!r} appears in: {text}"

    def test_approving_is_explicitly_distinguished_from_running(self):
        text = ai.render_approved("act-1", "pc-1").lower()
        assert "recording it is not running it" in text
        assert "won't tell you it worked" in text

    def test_approving_does_not_assert_the_action_has_not_run(self):
        """Approving makes an action eligible to be leased, and a polling device
        can lease and finish it between the authority's UPDATE and this sentence
        reaching the person. Claiming it "has not run yet" would be an assertion
        about the world the approval result does not establish --- the same class
        of untruth this surface exists to refuse, pointing the other way."""
        text = ai.render_approved("act-1", "pc-1").lower()
        for claim in ("has not run", "hasn't run", "nothing has happened", "not started"):
            assert claim not in text, claim
        # What it may say is what is actually guaranteed.
        assert "may pick it up at any moment" in text

    def test_withdrawing_after_a_lease_does_not_promise_it_cannot_run(self):
        """`store.mark_cancelled` accepts a leased action and its own docstring
        says doing so cannot stop the device. "Can never run" would be false
        reassurance about something possibly underway on the person's machine."""
        text = ai.render_rejected_after_lease("act-1").lower()
        assert "can never run" not in text
        assert "may have started" in text
        assert "can't reach out and stop a machine" in text
        # The narrower thing withdrawal *does* buy is still stated.
        assert "never be run again" in text

    def test_the_cancelled_status_does_not_claim_nothing_happened(self):
        """The state column cannot tell a withdrawal-before-lease from one
        after, so the sentence must not assert either."""
        text = ai.render_status("act-1", "cancelled").lower()
        assert "nothing happened" not in text
        assert "may have started" in text

    def test_unknown_is_never_rendered_as_success(self):
        text = ai.render_status("act-1", "unknown").lower()
        assert "could not observe" in text
        assert "i do not know whether it worked" in text
        assert "succeeded" not in text

    def test_failure_is_rendered_as_failure(self):
        text = ai.render_status("act-1", "failed").lower()
        assert "did not take effect" in text
        assert "nothing changed" in text

    def test_only_a_verified_effect_reads_as_done(self):
        text = ai.render_status("act-1", "succeeded").lower()
        assert "the effect was observed" in text

    def test_an_unknown_state_is_reported_as_unknown_rather_than_guessed(self):
        text = ai.render_status("act-1", "something_new")
        assert "don't have words for" in text

    def test_a_rejection_is_kept_as_evidence_rather_than_erased(self):
        assert "haven't deleted it" in ai.render_rejected("act-1")

    def test_every_state_the_action_store_can_hold_has_a_sentence(self):
        from bartholomew.actuation.store import ActionState

        for state in ActionState:
            assert state.value in ai._STATUS_SENTENCES, state
