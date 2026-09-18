"""
EXEC-02: recognising an outcome-level goal in ordinary conversation, and
rendering truthfully what the Executive did with it.

The pure half of the conversational-goal seam, holding to exactly the
discipline `task_intents.py`, `forecast_intents.py` and `objective_intents.py`
hold to: no I/O, no persistence, no network, no model call, no clock read. The
governed call into `bartholomew/executive/` stays in `runtime_contract.py`, so
this module cannot become a second executive, a second planner, or a second
authority on anything.

Two jobs, the same two those siblings have:

1. **Recognition.** Decide whether an utterance is an *outcome-level goal
   directed at Bartholomew* — the class of request that today falls through to
   ordinary conversational generation despite asking for something to be done —
   or, overwhelmingly the common case, nothing at all.
2. **Rendering.** Turn what the Executive actually reported into the sentence
   Bartholomew says, with the state named rather than smoothed over.

What this recogniser is, and what it deliberately is not
---------------------------------------------------------
It is **not** a planner, and it is not a classifier of what is possible. It
answers one bounded question — "is this person asking for an outcome on their
machine?" — and hands the whole of the deciding to the Executive, which already
owns literal recognition (`executive/intent.py`), deliberation
(`executive/deliberation.py`), capability selection, validation and refusal.

So the recogniser is deliberately *narrow and syntactic*. A goal is claimed
only when **both** halves are present:

* a **request construction** — an imperative opening, or an explicit ask
  ("can you", "please", "I need you to", "help me"); and
* a **device-domain referent** — one of a closed noun set naming something on
  the machine (a file, a document, a window, the clipboard, a browser tab).

Both, never either. "Sort these files out for me" has both. "Sort out what you
think about Tolstoy" has a request construction and no device referent, so it
stays conversation. "The files are a mess" has a referent and no request, so it
stays conversation. This is the bounded, understandable decision seam the
package asked for, in preference to asking a model whether a sentence is a
task — which is precisely the "everything goes to the planner" behaviour that
turns conversation into an interrogation.

Why claiming a request the Executive will *refuse* is correct
--------------------------------------------------------------
"Delete these files for me" is claimed here and refused there, by
`intent.py`'s out-of-vocabulary rule, in the Executive's own words. That is the
point. The alternative — declining to claim it, so it falls through to the
model — is the one outcome that must never happen: a generated sentence about
a deletion that never occurred, which the person has no way to tell from one
that did.

Asymmetry of errors
-------------------
A false negative costs one goal the person can restate more plainly, or take to
the operator console. A false positive costs a conversational turn answered
with a plan the person did not ask for. They are not symmetric, so every
ambiguous construction resolves to *not a goal*.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: The longest utterance this recogniser will look at. A goal is a sentence or
#: two; a wall of text pasted into chat is not an instruction, and treating it
#: as one would put an unbounded string in front of cognition.
MAX_GOAL_CHARS = 400

#: Outcomes this seam reports back on the chat surface. Distinct values,
#: because "it needs your approval", "I had to ask you something" and "the
#: brake is on" are three different non-events and must never render alike.
GOAL_OUTCOME_PROPOSED = "proposed"
GOAL_OUTCOME_CLARIFICATION = "clarification_requested"
GOAL_OUTCOME_REFUSED = "refused"
GOAL_OUTCOME_BRAKE = "parking_brake_denied"
GOAL_OUTCOME_UNAVAILABLE = "unavailable"
GOAL_OUTCOME_FAILED = "failed"

#: The Executive's own outcome strings (`executive/seam.py`), mapped onto the
#: six above. Kept as data rather than a chain of `if`s so a new Executive
#: outcome is a visible omission — `map_outcome` falls to `failed`, which is
#: the safe direction — rather than a silent pass-through of a word the chat
#: surface has no rendering for.
_EXECUTIVE_OUTCOMES = {
    "proposed": GOAL_OUTCOME_PROPOSED,
    "clarification_requested": GOAL_OUTCOME_CLARIFICATION,
    "refused": GOAL_OUTCOME_REFUSED,
    "parking_brake_denied": GOAL_OUTCOME_BRAKE,
}

_TRAILING_PUNCT = " \t\r\n.!?,;:\"'"

# ---------------------------------------------------------------------------
# Recognition
# ---------------------------------------------------------------------------

#: Constructions in which a person asks Bartholomew to bring something about.
#: Anchored at the start, because "I wonder if you could sort the files out" is
#: musing and "could you sort the files out" is asking, and the difference is
#: where the ask sits in the sentence.
_REQUEST_RES = (
    re.compile(
        r"^\s*(?:hey\s+|ok(?:ay)?\s+)?(?:bartholomew|barth)?[\s,]*"
        r"(?:please\s+)?(?:can|could|would|will)\s+you\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:hey\s+|ok(?:ay)?\s+)?(?:bartholomew|barth)?[\s,]*"
        r"(?:i(?:'d| would)\s+like\s+you\s+to|i\s+(?:need|want)\s+you\s+to|"
        r"i\s+need\s+(?:a|an|my|the|these|those|this|that)\b)",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:hey\s+|ok(?:ay)?\s+)?(?:bartholomew|barth)?[\s,]*"
        r"(?:please|help\s+me|for\s+me[, ])\b",
        re.IGNORECASE,
    ),
    #: A bare imperative. The verb set is closed and every member of it is a
    #: verb of *doing something to something*, which is why "tell", "explain",
    #: "remind" and "remember" are absent: they ask for words or for memory,
    #: both of which are already somebody else's surface.
    re.compile(
        r"^\s*(?:hey\s+|ok(?:ay)?\s+)?(?:bartholomew|barth)?[\s,]*"
        r"(?:sort|tidy|organise|organize|clean|clear|start|set\s+up|"
        r"make|create|write|draft|open|launch|put|copy|paste|close|"
        r"arrange|prepare|fix|get|"
        # Consequential verbs, claimed **so that they are refused in the
        # Executive's own words** rather than falling through to a model that
        # has no way to do them and every way to sound as though it did.
        # `executive/intent.py`'s out-of-vocabulary rule owns that refusal, and
        # deliberation is forbidden from reopening one.
        r"delete|remove|erase|uninstall|install|download|send|email|e-mail|"
        r"post|publish|buy|purchase|pay|order|rename|move|overwrite)\b",
        re.IGNORECASE,
    ),
)

#: Closed set of nouns naming something that lives on the person's machine.
#: A goal must name one. The set is small on purpose: every entry widens what
#: chat will hand to the Executive, so each is a deliberate addition rather
#: than a category that grows by itself.
_DEVICE_REFERENTS = (
    "file",
    "files",
    "folder",
    "folders",
    "directory",
    "document",
    "documents",
    "doc",
    "docs",
    "note",
    "notes",
    "notepad",
    "wordpad",
    "editor",
    "text file",
    "shopping list",
    "list",
    "window",
    "windows",
    "desktop",
    "screen",
    "clipboard",
    "browser",
    "tab",
    "web page",
    "webpage",
    "website",
    "url",
    "link",
    "app",
    "application",
    "program",
    "spreadsheet",
    "downloads",
)

_REFERENT_RES = tuple(
    (referent, re.compile(rf"\b{re.escape(referent)}\b", re.IGNORECASE))
    for referent in _DEVICE_REFERENTS
)

#: Constructions that are never a goal however device-shaped the rest of the
#: sentence is. Each is a surface that already exists and must keep its turn:
#: memory recall, a question about the world, an opinion, a greeting. Checked
#: before anything else, so an overlap resolves to *not a goal*.
_NOT_A_GOAL = re.compile(
    r"\b(?:"
    r"do\s+you\s+remember|remember\s+when|what\s+did\s+i|"
    r"remind\s+me|what\s+do\s+you\s+think|how\s+do\s+you\s+feel|"
    r"what\s+is\s+a|what\s+are\s+the|what\s+does\s+.{0,40}\bmean\b|"
    r"how\s+do\s+i\b|how\s+would\s+i\b|how\s+does\s+.{0,40}\bwork\b|"
    r"what(?:'s| is)\s+the\s+(?:best|point|difference)|"
    r"tell\s+me\s+about|explain\b|"
    r"how\s+(?:are|was)\s+you(?:r|rs)?\b|"
    r"good\s+(?:morning|afternoon|evening)\b"
    r")",
    re.IGNORECASE,
)

#: A trailing question mark on something with no request construction of its
#: own is a question. "Can you tidy the desktop?" keeps its request opening and
#: is still a goal; "the files?" is not.
_BARE_QUESTION = re.compile(
    r"^\s*(?:what|who|when|where|why|how|which|is|are|was|were|do|does|did)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GoalIntent:
    """One recognised outcome-level goal, and why it was recognised.

    `instruction` is the person's own words, bounded and stripped — never a
    paraphrase. The Executive reads instructions, and handing it a rewritten
    one would mean the plan answered a sentence nobody said. `described_as` is
    the same words with trailing punctuation removed, for quoting back.
    """

    instruction: str
    described_as: str
    #: The device-domain noun that made this a goal, and the request
    #: construction that made it a request. Carried so a recognition can be
    #: explained afterwards, and asserted on in tests, without re-deriving it.
    referent: str
    trigger: str


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def parse_intent(text: str) -> GoalIntent | None:
    """Recognise an outcome-level goal, or — far more often — return None.

    Returning None is the normal outcome and means "this is conversation":
    the chat turn then proceeds exactly as it did before EXEC-02, reaching the
    model unchanged.
    """
    cleaned = _clean(text)
    if not cleaned or len(cleaned) > MAX_GOAL_CHARS:
        return None

    if _NOT_A_GOAL.search(cleaned):
        return None

    trigger: str | None = None
    for pattern in _REQUEST_RES:
        match = pattern.match(cleaned)
        if match:
            trigger = _clean(match.group(0)).lower() or pattern.pattern[:24]
            break
    if trigger is None:
        return None

    # A question opening that survived the request test ("Would you..." does
    # not open with one of these) is a question, not an instruction.
    if _BARE_QUESTION.match(cleaned):
        return None

    referent: str | None = None
    for name, pattern in _REFERENT_RES:
        if pattern.search(cleaned):
            referent = name
            break
    if referent is None:
        return None

    return GoalIntent(
        instruction=cleaned,
        described_as=cleaned.rstrip(_TRAILING_PUNCT),
        referent=referent,
        trigger=trigger,
    )


def map_outcome(executive_outcome: Any) -> str:
    """The Executive's outcome word, as this surface names it.

    An outcome this surface does not know is reported as `failed`, never as
    success: an unrecognised state is not a completed one.
    """
    if not isinstance(executive_outcome, str):
        return GOAL_OUTCOME_FAILED
    return _EXECUTIVE_OUTCOMES.get(executive_outcome, GOAL_OUTCOME_FAILED)


# ---------------------------------------------------------------------------
# Rendering
#
# Every renderer here states what actually happened and, where it matters,
# what did *not*. The one sentence this module must never be able to produce
# is a claim that work is finished, because at this point in the Runtime
# Contract nothing has run: the Executive stops at `pending_approval` and the
# envelope refuses dispatch without an approval regardless. A chat surface that
# said "done" here would be describing a state the system has not got to.
# ---------------------------------------------------------------------------


def render_proposed(explanation: str, *, action_ids: list[str] | None = None) -> str:
    """A bounded plan exists and is waiting for the person to authorise it."""
    ids = list(action_ids or [])
    head = "I've worked out how to do that. Nothing has run yet."
    if ids:
        head += (
            " The first step is proposed and waiting for your approval — it will not "
            "run until you approve it."
        )
    body = (explanation or "").strip()
    return f"{head}\n\n{body}" if body else head


def render_clarification(question: str, explanation: str = "") -> str:
    """Understood in part, and stopped on a question. Nothing was proposed."""
    asked = (question or "").strip() or "I need to know more before I can plan this."
    body = (explanation or "").strip()
    head = f"Before I plan anything: {asked}\n\nI haven't proposed or run anything."
    return f"{head}\n\n{body}" if body else head


def render_refused(reason: str | None, explanation: str = "") -> str:
    """Declined, in the Executive's own words. Nothing was proposed."""
    why = (reason or "").strip() or "I can't turn that into something I'm able to do."
    body = (explanation or "").strip()
    head = f"I haven't done that, and I haven't proposed anything. {why}"
    return f"{head}\n\n{body}" if body else head


def render_brake(reason: str | None) -> str:
    """The Parking Brake is engaged, so nothing was planned at all."""
    why = (reason or "").strip() or "The Parking Brake is engaged."
    return (
        f"I haven't planned anything for that. {why} "
        "Release the Parking Brake if you want me to work on it."
    )


def render_unavailable(described_as: str) -> str:
    """Recognised as a goal, with no Executive in this runtime to take it."""
    return (
        f"I understood {described_as!r} as something to work out and do, but the part "
        "of me that works out how to do things isn't available in this session, so I "
        "haven't planned or run anything."
    )


def render_failure(described_as: str, reason: str | None) -> str:
    """Something went wrong on the way. Truthfully, and without a plan."""
    why = (reason or "").strip() or "an internal error interrupted it"
    return (
        f"I tried to work out how to do {described_as!r} and could not: {why}. "
        "Nothing was proposed and nothing has run."
    )


__all__ = [
    "GOAL_OUTCOME_BRAKE",
    "GOAL_OUTCOME_CLARIFICATION",
    "GOAL_OUTCOME_FAILED",
    "GOAL_OUTCOME_PROPOSED",
    "GOAL_OUTCOME_REFUSED",
    "GOAL_OUTCOME_UNAVAILABLE",
    "MAX_GOAL_CHARS",
    "GoalIntent",
    "map_outcome",
    "parse_intent",
    "render_brake",
    "render_clarification",
    "render_failure",
    "render_proposed",
    "render_refused",
    "render_unavailable",
]
