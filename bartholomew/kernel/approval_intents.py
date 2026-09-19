"""
R-EXEC02-2: recognising a human's authority decision in ordinary conversation,
and rendering truthfully what the approval authority did with it.

The pure half of the conversational-approval seam, holding to exactly the
discipline `task_intents.py`, `forecast_intents.py`, `objective_intents.py` and
`goal_intents.py` hold to: no I/O, no persistence, no network, no model call,
no clock read. The governed call into `bartholomew/actuation/seam.py` --- the
one approval authority --- lives in
`bartholomew/integration/conversational_approval.py`, so this module cannot
become a second authority on anything.

Conversation may *transport* an authority decision. It does not *become* an
authority
-----------------------------------------------------------------------------
This module answers one bounded question about one string: **is this utterance,
in its entirety, a human granting or withdrawing permission?** It does not know
what is pending, it cannot find out, and it returns the same answer for "yes"
whether or not anything is waiting. Everything else --- which proposal is meant,
whether that proposal is still the one the person saw, whether the identity,
tenant, device, capability class and Parking Brake all still permit it ---
happens downstream, in the authority that already owns those questions.

**A model is never asked whether permission was granted.** It may explain a
proposal and it may explain a result. If the decision of whether a human
authorised something were a generated inference, then a sufficiently persuasive
page of text in the model's context would be an approval, and the whole
`pending_approval` gate would be decorative.

Why whole-utterance matching, and nothing looser
-------------------------------------------------
A decision is claimed only when the *entire* utterance, once trimmed of a lead-in
and a politeness, is a member of a closed phrase set. Not "contains yes".
Not "starts with yes". The whole of it.

That single rule is what makes every one of these stay conversation:

=====================================  =========================================
utterance                              why it is not an approval
=====================================  =========================================
`If I say yes, what happens?`          a question about approving
`Does "yes" approve it?`               a question, and quoted
`I would say yes but I want to think`  discussion; not the whole utterance
`Yes, I remember that file`            an answer to something else
`Yes --- but change the folder first`  a qualified reply, so not unqualified
`no idea what that does`               "no" is not the whole utterance
=====================================  =========================================

A qualified, hedged, quoted or embedded affirmative is *not* an unambiguous
grant of permission, and the safe reading of an ambiguous one is that nothing
was authorised. The person can always say "yes" on its own.

Asymmetry of errors
-------------------
A false negative costs the person one more word. A false positive causes
something to happen on their machine that they did not authorise. They are not
remotely symmetric, so every ambiguous construction resolves to *not a
decision*, and the phrase sets are deliberately short rather than generous.
Weak acknowledgements --- "ok", "sure", "fine", "sounds good" --- are
**excluded on purpose**: they are what a person says while still thinking.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The longest utterance that can be a bare decision. A decision is a word or
#: a short phrase; anything longer is a sentence with content in it, and a
#: sentence with content in it is conversation.
MAX_DECISION_CHARS = 64

#: The three things this recogniser can claim.
DECISION_APPROVE = "approve"
DECISION_REJECT = "reject"
#: Not a decision at all --- a person asking what became of something they
#: already decided about. It authorises nothing and withdraws nothing; it is
#: here because the surface that carries an approval must also be able to
#: report the truth about what followed, including `unknown`.
DECISION_STATUS = "status"

_TRAILING_PUNCT = " \t\r\n.!,;:"

#: A quotation mark anywhere means the utterance is *about* words rather than
#: made of them. The apostrophe is deliberately absent: "don't" is a decision.
_QUOTED = re.compile(r"[\"“”‘’`]")

#: An optional lead-in and an optional politeness, stripped before matching so
#: "Bartholomew, yes please" and "yes" are the same decision. Nothing here can
#: *make* a decision --- every one of them is optional, and what remains must
#: still be a whole phrase from a closed set below.
_LEAD = re.compile(
    r"^(?:(?:hey|ok|okay)\s+)?(?:bartholomew|barth)?[\s,]*",
    re.IGNORECASE,
)
_POLITENESS = re.compile(
    r"[\s,]*(?:please|thanks|thank\s+you|mate|then)?$",
    re.IGNORECASE,
)

#: Granting permission, and nothing weaker. Each entry is a complete utterance.
_APPROVE_PHRASES = frozenset(
    {
        "yes",
        "yes please",
        "yep",
        "yeah",
        "yes go ahead",
        "yes do it",
        "yes do that",
        "go ahead",
        "go right ahead",
        "please go ahead",
        "do it",
        "do that",
        "please do",
        "please do it",
        "please do that",
        "go for it",
        "approve",
        "approve it",
        "approve that",
        "approved",
        "i approve",
        "i approve it",
    },
)

#: Withdrawing permission. `stop` and `abort` are deliberately absent: those
#: are halt words, the Parking Brake owns halting, and a word that means two
#: controls means neither reliably.
_REJECT_PHRASES = frozenset(
    {
        "no",
        "no thanks",
        "no thank you",
        "no don't",
        "no dont",
        "nope",
        "don't",
        "dont",
        "do not",
        "don't do it",
        "dont do it",
        "do not do it",
        "do not do that",
        "don't do that",
        "dont do that",
        "reject",
        "reject it",
        "reject that",
        "cancel",
        "cancel it",
        "cancel that",
        "forget it",
        "forget that",
        "leave it",
        "drop it",
        "never mind",
        "nevermind",
    },
)

#: Asking what became of a decision already made. Unlike the two above, a
#: question mark is expected here rather than disqualifying.
_STATUS_PHRASES = frozenset(
    {
        "did it work",
        "did that work",
        "did it run",
        "did that run",
        "did you do it",
        "did you do that",
        "is it done",
        "is that done",
        "is it finished",
        "has it run",
        "has that run",
        "what happened",
        "what happened with that",
        "how did that go",
        "how did it go",
        "what's the status",
        "whats the status",
        "any news",
    },
)


@dataclass(frozen=True)
class ConversationalDecision:
    """One recognised human decision, and the words it was recognised from.

    `utterance` is the person's own words, bounded and stripped --- never a
    paraphrase and never a normalisation. It is what the audit record carries
    as the evidence of the decision, so it must be what was actually said.
    """

    kind: str
    utterance: str
    #: The normalised phrase that matched, for explaining a recognition
    #: afterwards and asserting on it in tests without re-deriving it.
    matched: str


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def parse_decision(text: str) -> ConversationalDecision | None:
    """Recognise a bare authority decision, or --- far more often --- None.

    None is the normal answer and means "this is not a decision": the chat turn
    then proceeds exactly as it would have, reaching the other recognisers and
    then the model unchanged.

    This function has no idea whether anything is pending. It deliberately
    cannot find out. Claiming an utterance here is not an approval; it is the
    start of a request to the approval authority, which then decides.
    """
    cleaned = _normalise(text)
    if not cleaned or len(cleaned) > MAX_DECISION_CHARS:
        return None
    if _QUOTED.search(cleaned):
        return None

    # Trailing punctuation is stripped **before** the politeness, or "Yes,
    # please." leaves the politeness pattern anchored behind a full stop and
    # an ordinary, obviously-decisive sentence goes unrecognised.
    stripped = cleaned.rstrip(_TRAILING_PUNCT)
    trimmed = _POLITENESS.sub("", _LEAD.sub("", stripped, count=1), count=1)
    trimmed = _normalise(trimmed).rstrip(_TRAILING_PUNCT).strip().lower()
    if not trimmed:
        return None

    if trimmed.rstrip("?") in _STATUS_PHRASES:
        return ConversationalDecision(
            kind=DECISION_STATUS,
            utterance=cleaned,
            matched=trimmed.rstrip("?"),
        )

    # A question is never a grant of permission, however affirmative it reads.
    # "Should I say yes?" and "yes?" are both asking, not deciding.
    if "?" in cleaned:
        return None

    if trimmed in _APPROVE_PHRASES:
        return ConversationalDecision(
            kind=DECISION_APPROVE,
            utterance=cleaned,
            matched=trimmed,
        )
    if trimmed in _REJECT_PHRASES:
        return ConversationalDecision(
            kind=DECISION_REJECT,
            utterance=cleaned,
            matched=trimmed,
        )
    return None


# ---------------------------------------------------------------------------
# Rendering
#
# The one sentence no renderer here may produce is "it worked" on the strength
# of an approval. Approving is not executing, executing is not succeeding, and
# a device reporting success it could not observe is `unknown`. Each of those
# is a separate state below, and each reads as itself.
# ---------------------------------------------------------------------------


def render_armed(action_id: str) -> str:
    """Appended to a proposal the person may now authorise by replying.

    Names the action id, because the person's "yes" is going to be bound to
    exactly this proposal and an audit they read later will name it too.
    """
    return (
        f"Say **yes** and I'll put that one step ({action_id}) forward for approval, "
        "or **no** to drop it. Nothing runs until you do, and I'll only ever act on "
        "the step exactly as described above — if it changes, I'll ask again."
    )


def render_not_armed() -> str:
    """An affirmative with nothing safely identifiable to attach it to."""
    return (
        "I haven't got anything waiting for your approval, so there's nothing for "
        "that to authorise. Nothing has been approved and nothing has run."
    )


def render_ambiguous(count: int) -> str:
    """More than one plausible target. Ambiguity fails safe, always."""
    return (
        f"I can't tell which of {count} proposals you mean, so I haven't approved "
        "anything. Approve the one you want from the operator console, where each "
        "is named individually."
    )


def render_stale(reason: str) -> str:
    """The proposal the person saw is no longer the proposal that exists."""
    return (
        f"I haven't approved anything: {reason} Nothing has run, and nothing will "
        "on the strength of that reply."
    )


def render_approved(action_id: str, device_id: str, summary: str = "") -> str:
    """Authorised and recorded. Says what approving *did*, not what the machine has not.

    **It deliberately does not claim the action has not run.** Approving makes an
    action eligible to be leased, and a device that is polling can lease and even
    complete it in the moment between the authority's UPDATE and this sentence
    reaching the person. An earlier draft said "it has not run yet", which is an
    assertion about the world that the approval result does not establish and
    which could already be false as it was displayed --- the same class of
    untruth, pointing the other way, that this surface exists to refuse. Raised
    by automated review on PR #117, confirmed, and pinned by
    `TestRenderingIsTruthful::test_approving_does_not_assert_the_action_has_not_run`.

    What it says instead is exactly what is guaranteed: the approval is recorded,
    approving is not running, the machine may act at any moment, and no claim of
    success will be made without an observed and verified effect.
    """
    body = (summary or "").strip()
    head = (
        f"Approved — that's your authorisation recorded against {action_id}, and "
        f"nothing else. Recording it is not running it: {device_id} may pick it up "
        "at any moment from now. Ask me how it went and I'll read back the state; "
        "I won't tell you it worked until the machine has reported and the effect "
        "has been checked independently."
    )
    return f"{head}\n\n{body}" if body else head


def render_rejected(action_id: str) -> str:
    """Withdrawn before any device had it. It genuinely can never run."""
    return (
        f"Dropped — {action_id} is withdrawn on your instruction and can never run. "
        "That decision is on the record as yours; I haven't deleted it."
    )


def render_rejected_after_lease(action_id: str) -> str:
    """Withdrawn, but a device already had it. Says so rather than reassuring.

    `store.mark_cancelled` accepts a `leased` action on purpose, and its own
    docstring is explicit that doing so "does not reach out and stop a device --
    nothing here can". What withdrawal buys in that case is real but narrower:
    the action can never be leased again and any result the device later reports
    is refused, so it can never be *recorded* as having succeeded. What it does
    not buy is the thing a person hears in "it can never run".

    Saying "nothing happened" here would be false reassurance about something
    that may be happening on their machine as they read it. Raised by automated
    review on PR #117, confirmed against `mark_cancelled`'s accepted from-states,
    and pinned by `TestTheExactProposalIsRejected::
    test_withdrawing_an_already_leased_action_does_not_claim_nothing_happened`.
    """
    return (
        f"Withdrawn — but be aware your PC had already picked {action_id} up before "
        "you said so, so it may have started. I can't reach out and stop a machine "
        "mid-action; what withdrawing does is make sure it can never be run again "
        "and that no result for it will be recorded. Check the machine itself if it "
        "matters whether it got as far as taking effect."
    )


def render_refused(reason: str) -> str:
    """The authority refused. Verbatim, in its words, without improvement."""
    return (
        f"I couldn't record that approval: {reason} "
        "Nothing has been approved and nothing has run."
    )


def render_brake() -> str:
    """The Parking Brake outranks an approval at every stage, including this one."""
    return (
        "The Parking Brake is engaged, so I haven't recorded that approval. A "
        "proposal made before the brake went on does not get to run after it. "
        "Release the brake and tell me again if you still want it."
    )


def render_ineligible(reason: str) -> str:
    """This class of action is not authorisable from this surface."""
    return (
        f"I can't take that approval here: {reason}. Nothing has been approved. "
        "The operator console is the surface for it."
    )


#: How each terminal or in-flight action state is reported back in conversation.
#: `unknown` is a first-class answer, never smoothed into either success or
#: failure --- it is what "the machine cannot tell" honestly sounds like.
_STATUS_SENTENCES = {
    "pending_approval": ("is still waiting for your approval. Nothing has run."),
    "approved": ("is approved and waiting for your PC to pick it up. It has not run yet."),
    "leased": (
        "has been picked up by your PC and is running now. There's no result yet, "
        "so I can't tell you whether it worked."
    ),
    "succeeded": ("ran, and the effect was observed. That one is genuinely done."),
    "failed": ("ran and did not take effect. Your PC is clear that it failed, so nothing changed."),
    "unknown": (
        "ran, and your PC could not observe whether it actually took effect. I do "
        "not know whether it worked, and I'm not going to guess — check the machine "
        "itself before assuming either way."
    ),
    # Deliberately not "so nothing happened": an action can be withdrawn *after*
    # a device leased it, and the state column alone cannot tell the two apart
    # afterwards. What is true of every cancelled action is that it can never run
    # again and can never be recorded as a success.
    "cancelled": (
        "was withdrawn, so it can never run again and no result for it will be "
        "recorded. If your PC had already picked it up before you withdrew it, it "
        "may have started; the state alone cannot tell you which."
    ),
    "refused": "was refused by governance and never ran.",
    "aborted_by_brake": (
        "was stopped by the Parking Brake after your PC had picked it up. Some of "
        "it may have started before the halt took effect."
    ),
}


def render_status(action_id: str, state: str, detail: str | None = None) -> str:
    """What became of the action, in the state's own terms.

    An unrecognised state is reported as unrecognised rather than guessed at.
    There is no branch that rounds a state up.
    """
    sentence = _STATUS_SENTENCES.get(state)
    if sentence is None:
        return (
            f"{action_id} is in a state I don't have words for ({state!r}), so I'm "
            "not going to characterise it. Check the operator console."
        )
    body = (detail or "").strip()
    head = f"{action_id} {sentence}"
    return f"{head}\n\n{body}" if body else head


def render_nothing_to_report() -> str:
    """Asked what happened, with no decision of theirs to report on."""
    return (
        "There's nothing I'm waiting on for you at the moment, so I have nothing to "
        "report. If you approved something in the operator console, its result is "
        "there."
    )


__all__ = [
    "DECISION_APPROVE",
    "DECISION_REJECT",
    "DECISION_STATUS",
    "MAX_DECISION_CHARS",
    "ConversationalDecision",
    "parse_decision",
    "render_ambiguous",
    "render_approved",
    "render_armed",
    "render_brake",
    "render_ineligible",
    "render_not_armed",
    "render_nothing_to_report",
    "render_refused",
    "render_rejected",
    "render_rejected_after_lease",
    "render_stale",
    "render_status",
]
