"""W03-E acceptance criterion 4, without a server: the account never overstates.

`explain_outcome` is the one function in the operator console that turns a
status into a sentence, so it is the one place an `unknown` could quietly become
"done". This file pins it exhaustively -- against the envelope's own enums, not
against a list this test wrote down -- and pins the direction of every edge:
`unknown` reads as unknown, `effect_unverifiable` reads as unverified, a device's
optimistic `succeeded` with nothing read back reads as unconfirmed, and a status
the console has never heard of reads as unknown rather than as anything else.

Runs in the PR Fast tier: it needs no server and it is the structural half of
what `test_path_1_open_spotify.py` proves over the wire.
"""

from __future__ import annotations

import pytest

from bartholomew.actuation.result import ActionResultStatus
from bartholomew.actuation.store import ActionState
from bartholomew.cli_operator import _OUTCOME_SENTENCES, explain_outcome

#: The claims a sentence may make only when the effect was actually confirmed.
#: Phrases, not words: "nobody can say whether it worked" is an honest sentence
#: that contains the word "worked", and a word-level check would refuse it.
SUCCESS_CLAIMS = ("was confirmed", "did what was asked")


def _claims_success(sentence: str) -> bool:
    lowered = sentence.lower()
    return any(claim in lowered for claim in SUCCESS_CLAIMS)


def _every_status() -> set[str]:
    return {s.value for s in ActionResultStatus} | {s.value for s in ActionState}


def test_the_console_has_a_sentence_for_every_status_the_envelope_can_report():
    """Exhaustive against the enums. A ninth member fails here, not in production."""
    missing = sorted(_every_status() - set(_OUTCOME_SENTENCES))
    assert not missing, f"no sentence for: {missing}"


@pytest.mark.parametrize("status", sorted(_every_status()))
def test_only_succeeded_may_read_as_a_confirmed_effect(status):
    if status == "succeeded":
        # Confirmed only with a recorded read-back that agreed.
        assert _claims_success(explain_outcome(status, verified=True))
        assert not _claims_success(explain_outcome(status))
    else:
        assert not _claims_success(explain_outcome(status)), status
        assert not _claims_success(explain_outcome(status, verified=True)), status


def test_unknown_is_never_presented_as_success():
    sentence = explain_outcome("unknown")
    assert "NOT success" in sentence
    assert "nobody can say whether it worked" in sentence


def test_effect_unverifiable_is_never_presented_as_success():
    sentence = explain_outcome("unknown", verify_method="unavailable")
    assert "NOT success" in sentence


def test_a_succeeded_report_with_nothing_read_back_is_not_confirmed():
    """Issued is not succeeded, even when the device says it is."""
    for evidence in (
        {"verified": False},
        {"verify_method": "unavailable"},
        {"verified": False, "verify_method": "unavailable"},
    ):
        sentence = explain_outcome("succeeded", **evidence)
        assert "NOT confirmed success" in sentence, (evidence, sentence)


def test_a_succeeded_report_that_was_read_back_is_confirmed():
    """Non-vacuity for the test above: the confirmation exists when earned."""
    sentence = explain_outcome("succeeded", verified=True, verify_method="uia_value_read_back")
    assert "confirmed by reading the machine back" in sentence
    assert "NOT" not in sentence


def test_a_succeeded_report_with_no_verification_recorded_is_not_confirmed():
    """The third fact behind `succeeded`: nobody looked. Not the same as
    "somebody looked and it had happened", and never rendered as if it were."""
    sentence = explain_outcome("succeeded")
    assert "NOT confirmed success" in sentence
    assert "no verification was recorded" in sentence


def test_verified_none_is_not_verified_false():
    """ "Nobody recorded a verification" and "somebody looked and it had not
    happened" are different facts, and the account keeps them apart."""
    unrecorded = explain_outcome("succeeded", verified=None)
    looked = explain_outcome("succeeded", verified=False)
    assert unrecorded != looked
    assert "NOT confirmed" in looked


def test_a_status_the_console_does_not_know_reads_as_unknown():
    """A new vocabulary word arriving from a newer server is not guessed at."""
    for status in ("done", "ok", "completed", "SUCCESS", "", None, "aborted_by_operator"):
        sentence = explain_outcome(status)
        assert "unknown" in sentence.lower(), (status, sentence)
        assert "confirmed" not in sentence.lower()


def test_the_aborted_by_brake_status_reads_as_a_halt_not_a_failure():
    """W03-C's eighth status. Present in the table ahead of the package, on
    purpose: an integrated head must not render a halt as "not understood"."""
    sentence = explain_outcome("aborted_by_brake")
    assert "Parking Brake" in sentence
    assert "did not do what was asked" not in sentence


def test_refusals_and_withdrawals_say_nothing_ran():
    for status in ("refused", "cancelled", "expired"):
        sentence = explain_outcome(status).lower()
        assert "never ran" in sentence or "can never run" in sentence or "withdrawn" in sentence


def test_no_sentence_in_the_table_calls_an_unconfirmed_outcome_done():
    """A guard on the table itself, so a later edit cannot soften a row."""
    for status, sentence in _OUTCOME_SENTENCES.items():
        if status == "succeeded":
            continue
        assert not _claims_success(sentence), (status, sentence)
    # And the succeeded sentence itself is reachable only through a recorded,
    # agreeing read-back -- never from the table directly.
    assert _claims_success(_OUTCOME_SENTENCES["succeeded"])
    assert not _claims_success(explain_outcome("succeeded", verified=None))
