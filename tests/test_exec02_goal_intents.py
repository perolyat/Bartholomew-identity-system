"""
EXEC-02: the bounded conversational-goal decision seam.

`goal_intents.parse_intent` is the one place where Bartholomew decides that a
sentence typed into ordinary conversation is a request for an *outcome on the
person's machine* rather than something to talk about. The whole value of
putting that decision in a pure, regex-bounded module instead of asking a model
"is this a task?" is that it can be pinned, read and argued with -- so this
suite pins it.

What it asserts, in the order the package brief asks for it:

  * ordinary conversation is not a goal (the large, boring, important half);
  * an outcome-level goal is recognised, including the brief's own sentence;
  * recognition needs **both** a request construction and a device referent,
    and neither alone is enough;
  * surfaces that already own a turn -- memory recall, questions about the
    world, opinions -- keep it;
  * the person's own words reach the Executive unrewritten;
  * every renderer states what happened and none of them can say the work is
    finished.

The Executive is not involved here and is not imported: this module has no I/O,
no clock, no model and no persistence, exactly as `task_intents.py`,
`forecast_intents.py` and `objective_intents.py` have none.
"""

from __future__ import annotations

import pytest

from bartholomew.kernel import goal_intents


class TestOrdinaryConversationStaysConversation:
    """The default answer, and the one a false positive would destroy."""

    @pytest.mark.parametrize(
        "utterance",
        [
            "How are you today?",
            "Good morning.",
            "What is the capital of France?",
            "What do you think about the Booker shortlist?",
            "Tell me about the Antikythera mechanism.",
            "Explain how a heat pump works.",
            "I had a rough day.",
            "That's interesting, say more.",
            "Thanks, that helped.",
            "The files are a mess.",
            "My desktop is a disaster area.",
            "I should really sort out those files one day.",
            "Sort out what you think about Tolstoy first.",
            "How do I open a file on Windows?",
            "Do you remember the document I mentioned yesterday?",
            "What did I say about the shopping list?",
            "Remind me what a clipboard manager is.",
            "",
            "   ",
        ],
    )
    def test_is_not_a_goal(self, utterance):
        assert goal_intents.parse_intent(utterance) is None


class TestOutcomeLevelGoalsAreRecognised:
    """The class of request that today falls through to conversational text."""

    @pytest.mark.parametrize(
        ("utterance", "referent"),
        [
            ("Sort these files out for me.", "files"),
            ("Start a shopping list for me.", "shopping list"),
            ("Can you get my shopping list started?", "shopping list"),
            ("Tidy up my desktop please", "desktop"),
            ("Please tidy the desktop", "desktop"),
            ("Could you open notepad for me?", "notepad"),
            ("I need you to draft a note about the roofer", "note"),
            ("Organise the documents in my downloads", "documents"),
        ],
    )
    def test_is_a_goal(self, utterance, referent):
        intent = goal_intents.parse_intent(utterance)
        assert intent is not None
        assert intent.referent == referent

    def test_the_brief_s_own_sentence_is_recognised(self):
        assert goal_intents.parse_intent("Sort these files out for me.") is not None

    def test_the_person_s_own_words_are_what_reaches_the_executive(self):
        """Never a paraphrase. A plan must answer the sentence that was said."""
        intent = goal_intents.parse_intent("  Sort these   files out for me.  ")
        assert intent.instruction == "Sort these files out for me."

    def test_a_wall_of_text_is_not_an_instruction(self):
        assert goal_intents.parse_intent("please tidy the files " * 60) is None


class TestAnAuxiliaryIsNotAnInstruction:
    """An ask must name something Bartholomew can *do*, not merely address him.

    Raised by review on PR #115 and confirmed: an earlier cut matched the
    auxiliary alone, so "can you" plus any device noun anywhere later in the
    sentence was a goal. That made questions into tasks — the exact false
    positive this module exists to avoid — and would have suppressed the
    ordinary conversational reply in favour of asking the Executive to plan a
    machine action for someone who had asked a question.

    The correction is that `_ACTION_VERBS` is one closed vocabulary required by
    every construction, so "can you X" and a bare "X" are held to the same
    standard and cannot drift apart.
    """

    @pytest.mark.parametrize(
        "utterance",
        [
            # The four the review named.
            "Can you recommend a browser?",
            "Would you say this app is good?",
            "Can you tell me where my files are?",
            "Will you remember my notes?",
            # The same shape, other verbs that ask for words, opinion or memory.
            "Could you explain the clipboard?",
            "Can you find my files?",
            "Would you describe the document?",
            "Can you check whether my notes are any good?",
            "Could you suggest a note app?",
            "Can you remind me about the files?",
        ],
    )
    def test_an_auxiliary_without_an_action_verb_is_conversation(self, utterance):
        assert goal_intents.parse_intent(utterance) is None

    @pytest.mark.parametrize(
        ("utterance", "trigger"),
        [
            ("Can you sort these files out?", "can you sort"),
            ("Could you open notepad for me?", "could you open"),
            ("Can you get my shopping list started?", "can you get"),
            ("I need you to draft a note about the roofer", "i need you to draft"),
            ("help me clear the desktop", "help me clear"),
        ],
    )
    def test_an_auxiliary_with_an_action_verb_is_still_a_goal(self, utterance, trigger):
        intent = goal_intents.parse_intent(utterance)
        assert intent is not None
        assert intent.trigger == trigger

    def test_one_action_vocabulary_serves_every_construction(self):
        """The property, not an example of it: whatever a bare imperative
        accepts, the addressed form accepts, and vice versa. Two lists would
        drift; one cannot."""
        for verb in ("sort", "tidy", "open", "draft", "delete"):
            bare = goal_intents.parse_intent(f"{verb} the files")
            addressed = goal_intents.parse_intent(f"could you {verb} the files")
            assert (bare is None) == (addressed is None)
            assert bare is not None

        for verb in ("recommend", "explain", "tell", "remember"):
            assert goal_intents.parse_intent(f"{verb} the files") is None
            assert goal_intents.parse_intent(f"could you {verb} the files") is None


class TestBothHalvesAreRequired:
    """A request construction, and a device-domain referent. Never either."""

    @pytest.mark.parametrize(
        "request_without_referent",
        [
            "Can you cheer me up?",
            "Please be brief.",
            "Sort out my priorities for me.",
            "Could you think about that a bit more?",
        ],
    )
    def test_a_request_with_no_device_referent_is_conversation(self, request_without_referent):
        assert goal_intents.parse_intent(request_without_referent) is None

    @pytest.mark.parametrize(
        "referent_without_request",
        [
            "The document is still open.",
            "My clipboard has something odd in it.",
            "There are twelve windows open.",
            "That browser tab has been there a week.",
        ],
    )
    def test_a_device_referent_with_no_request_is_conversation(self, referent_without_request):
        assert goal_intents.parse_intent(referent_without_request) is None

    def test_both_together_are_a_goal(self):
        assert goal_intents.parse_intent("Please close those windows") is not None


class TestConsequentialVerbsAreClaimedSoTheyCanBeRefused:
    """Claimed here on purpose, and refused by the Executive in its own words.

    The alternative -- declining to claim them, so they fall through to the
    model -- is the one outcome that must never happen: a generated sentence
    about a deletion that did not occur, which the person cannot tell from one
    that did. `executive/intent.py`'s out-of-vocabulary rule owns the refusal,
    and `executive/deliberation.py` is forbidden from reopening it.
    """

    @pytest.mark.parametrize(
        "utterance",
        [
            "Delete these files for me",
            "Install a browser for me",
            "Send my notes to Dave",
            "Rename that document",
        ],
    )
    def test_is_claimed_rather_than_left_to_the_model(self, utterance):
        assert goal_intents.parse_intent(utterance) is not None


class TestOutcomeMapping:
    def test_every_executive_outcome_this_surface_renders_is_mapped(self):
        assert goal_intents.map_outcome("proposed") == goal_intents.GOAL_OUTCOME_PROPOSED
        assert (
            goal_intents.map_outcome("clarification_requested")
            == goal_intents.GOAL_OUTCOME_CLARIFICATION
        )
        assert goal_intents.map_outcome("refused") == goal_intents.GOAL_OUTCOME_REFUSED
        assert goal_intents.map_outcome("parking_brake_denied") == goal_intents.GOAL_OUTCOME_BRAKE

    @pytest.mark.parametrize("unknown", ["advanced", "completed", "", None, 7, "something_new"])
    def test_an_outcome_this_surface_does_not_know_is_never_success(self, unknown):
        """An unrecognised state is not a completed one. It fails safe."""
        assert goal_intents.map_outcome(unknown) == goal_intents.GOAL_OUTCOME_FAILED


#: Words that would assert the work is over. No renderer on this surface may
#: produce one, because at this point in the Runtime Contract nothing has run.
_COMPLETION_CLAIMS = (
    "i've sorted",
    "i have sorted",
    "i've done",
    "i have done",
    "all done",
    "that's done",
    "completed it",
    "i tidied",
    "i deleted",
    "i sent",
)


class TestRenderingIsTruthful:
    @pytest.mark.parametrize(
        "rendered",
        [
            goal_intents.render_proposed("Understood as: open an editor.", action_ids=["a-1"]),
            goal_intents.render_proposed("", action_ids=[]),
            goal_intents.render_clarification("Which files did you mean?", "x"),
            goal_intents.render_refused("I can't delete anything.", "x"),
            goal_intents.render_brake("Blocked by parking brake"),
            goal_intents.render_unavailable("Sort these files out for me"),
            goal_intents.render_failure("Sort these files out for me", "the model was unreachable"),
        ],
    )
    def test_no_renderer_can_claim_the_work_is_finished(self, rendered):
        lowered = rendered.lower()
        for claim in _COMPLETION_CLAIMS:
            assert claim not in lowered, f"{claim!r} in {rendered!r}"

    def test_a_proposal_says_nothing_has_run_and_names_the_approval(self):
        rendered = goal_intents.render_proposed("Understood as: launch notepad.", action_ids=["a"])
        assert "nothing has run" in rendered.lower()
        assert "approv" in rendered.lower()
        assert "Understood as: launch notepad." in rendered

    def test_a_clarification_says_nothing_was_proposed(self):
        rendered = goal_intents.render_clarification("Which files?")
        assert "Which files?" in rendered
        assert "haven't proposed" in rendered.lower()

    def test_a_refusal_carries_the_executive_s_own_reason(self):
        rendered = goal_intents.render_refused("I can't delete anything.")
        assert "I can't delete anything." in rendered
        assert "haven't done that" in rendered.lower()

    def test_the_brake_is_named_as_the_brake(self):
        rendered = goal_intents.render_brake("Blocked by parking brake (scope=skills)")
        assert "parking brake" in rendered.lower()
        assert "haven't planned anything" in rendered.lower()

    def test_a_failure_says_so_rather_than_falling_silent(self):
        rendered = goal_intents.render_failure("Sort the files", "the backend was unreachable")
        assert "the backend was unreachable" in rendered
        assert "nothing has run" in rendered.lower()
