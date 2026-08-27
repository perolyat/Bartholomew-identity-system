"""
Golden Path slice 2: recognising and rendering objectives.

The pure half of Objective Continuity, holding to exactly the discipline
`task_intents.py` and `forecast_intents.py` hold to: no I/O, no persistence,
no network, no model call, no clock read. "Today" is always passed in. The
governed reads and writes stay in `runtime_contract.py` and the scheduler
drive, so this module cannot become a second objectives authority or a second
write path.

Two jobs:

1. **Recognition.** Turn an utterance into an explicit objective instruction,
   or -- overwhelmingly the common case -- into nothing at all.
2. **Rendering.** Turn stored objectives and their events into the sentences
   Bartholomew actually says, including the continuity summary that is the
   point of the whole slice.

Why recognition is conservative
-------------------------------
There is deliberately **no model-based objective extraction here.** A model
asked "is this an objective?" will happily say yes to a passing remark, and
the cost of a false positive is not a wrong answer -- it is a durable record
that then nags the user about something they never asked for. That is
precisely the burden Real-World Test #1 found Bartholomew was adding, so this
module refuses anything it cannot recognise from an explicit construction.

The cost of a false negative is one objective the user can still state
plainly. The cost of a false positive is trust. They are not symmetric.

Horizons, and not inventing precision
-------------------------------------
"This week" stays `this_week`. It is not silently converted into a Friday,
because the user did not say Friday, and a reminder attached to a date they
never named is how a helpful system starts feeling wrong. Only an explicit
date becomes `by_date`.

What "what changed" is, and is not
----------------------------------
`render_continuity()` derives its summary from the objective's own event rows
each time it is called. Nothing is stored. A stored summary is a fabrication
the moment the events move on -- and, worse, it is a fabrication that reads
exactly like a real one.

`proposal` events are excluded from the "here's where things stand" section
and, when shown at all, are labelled as ideas rather than as things that
happened. Hypothetical reasoning must never arrive at the user wearing the
clothes of evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from bartholomew.kernel.objective_store import (
    EVENT_ACTION,
    EVENT_DECISION,
    EVENT_FACT,
    EVENT_PROPOSAL,
    HORIZON_BY_DATE,
    HORIZON_OPEN,
    HORIZON_THIS_WEEK,
    RESOLUTION_ACHIEVED,
    RESOLUTION_NO_LONGER_NEEDED,
)

#: Recognised instructions.
INTENT_OPEN = "open"
INTENT_LIST = "list"
INTENT_COMPLETE = "complete"
INTENT_ABANDON = "abandon"

_MAX_TITLE_CHARS = 200
_TRAILING_PUNCT = " \t\r\n.!?,;:\"'"

_MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|november|december"
    "|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)


@dataclass(frozen=True)
class ObjectiveIntent:
    """One recognised objective instruction."""

    action: str
    described_as: str
    title: str | None = None
    outcome_statement: str | None = None
    horizon_kind: str = HORIZON_OPEN
    horizon_date: str | None = None
    subject: str | None = None
    params: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------- patterns

# Explicit establishment. Each of these is a construction in which a person is
# stating an outcome they want, not making an observation.
_OPEN_RES = (
    # "I need to get the roof repaired this week"
    re.compile(
        r"^(?:i\s+)?(?:need|want)\s+to\s+(?P<title>.+)$",
        re.IGNORECASE,
    ),
    # "the roofer needs to come this week" -- the handoff's own example
    re.compile(
        r"^(?:the\s+|my\s+)?(?P<title>.+?\s+needs?\s+to\s+.+)$",
        re.IGNORECASE,
    ),
    # "I'm trying to get the roof fixed"
    re.compile(
        r"^(?:i'?m|i\s+am)\s+trying\s+to\s+(?P<title>.+)$",
        re.IGNORECASE,
    ),
    # Fully explicit, for when the user wants no ambiguity at all.
    re.compile(
        r"^(?:my\s+)?objective\s+is\s+(?:to\s+)?(?P<title>.+)$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:keep\s+track\s+of|stay\s+on\s+top\s+of)\s+(?P<title>.+)$",
        re.IGNORECASE,
    ),
)

#: Asking what Bartholomew is carrying.
#:
#: Deliberately narrow. Broad phrasings like "what's on my plate?" and
#: "what's outstanding?" were tried and removed: they read equally as a
#: question about *tasks*, and claiming them here both stole the turn from
#: task control and stopped an ordinary question reaching the model at all.
#: An utterance has to be asking about objectives specifically to be
#: recognised as asking about objectives.
_LIST_RES = (
    re.compile(r"^what\s+am\s+i\s+working\s+(?:on|towards)\b.*$", re.IGNORECASE),
    re.compile(
        r"^what\s+am\s+i\s+trying\s+to\s+(?:do|achieve|get\s+done)\b.*$",
        re.IGNORECASE,
    ),
    re.compile(r"^(?:list|show)\s+(?:me\s+)?(?:my\s+)?objectives\b.*$", re.IGNORECASE),
    re.compile(
        r"^what\s+(?:objectives|are\s+my\s+objectives)\b.*$",
        re.IGNORECASE,
    ),
)


#: Declaring an outcome reached. This is the sentence that must permanently
#: stop the resurfacing, so its shapes are the plain ones people actually use.
_COMPLETE_RES = (
    re.compile(
        r"^(?:the\s+|my\s+)?(?P<subject>.+?)\s+(?:is|are|has\s+been|have\s+been)\s+"
        r"(?:all\s+)?(?:done|sorted|finished|fixed|completed|handled|taken\s+care\s+of)\b.*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:i(?:'?ve| have))\s+(?:finished|completed|sorted|dealt\s+with)\s+"
        r"(?:the\s+|my\s+)?(?P<subject>.+)$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:the\s+|my\s+)?(?P<subject>.+?)\s+went\s+ahead\b.*$",
        re.IGNORECASE,
    ),
)

#: Declaring an objective no longer wanted. Distinct from completion, because
#: "we're not bothering" and "it's done" are different facts about the world
#: even though both mean stop asking.
_ABANDON_RES = (
    re.compile(
        r"^(?:forget|drop|cancel)\s+(?:about\s+)?(?:the\s+|my\s+)?(?P<subject>.+)$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:we'?re|i'?m)\s+not\s+(?:going\s+to\s+)?(?:bother(?:ing)?\s+with|doing)\s+"
        r"(?:the\s+|my\s+)?(?P<subject>.+?)(?:\s+any\s?more)?$",
        re.IGNORECASE,
    ),
)

#: Horizon phrases, stripped off the title and recorded separately.
_THIS_WEEK_RE = re.compile(r"\s*\b(?:this|the)\s+week\b\s*", re.IGNORECASE)
_BY_DATE_RE = re.compile(
    rf"\s*\bby\s+(?P<when>(?:\d{{1,2}}\s+(?:{_MONTHS})|(?:{_MONTHS})\s+\d{{1,2}}|"
    r"\d{4}-\d{2}-\d{2}))\b\s*",
    re.IGNORECASE,
)

#: Utterances that look like objective establishment but are questions or
#: hypotheticals. Recognising an objective from these is how a passing thought
#: becomes a durable nag.
_NOT_AN_OBJECTIVE = re.compile(
    r"^(?:do\s+i|should\s+i|would\s+i|what\s+if|could\s+i|can\s+i|why\s+do\s+i|"
    r"maybe\s+i|i\s+might|i\s+was\s+thinking)\b",
    re.IGNORECASE,
)

_MONTH_NUMBERS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "october": 10,
    "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}  # fmt: skip


# ---------------------------------------------------------------- helpers


def _clean(text: str) -> str:
    return (text or "").strip().strip(_TRAILING_PUNCT).strip()


def _bounded(text: str) -> str:
    cleaned = _clean(text)
    return cleaned[:_MAX_TITLE_CHARS].strip() if cleaned else ""


def _iso_by_date(when: str, today: date) -> str | None:
    """Turn an explicit 'by <date>' phrase into an ISO date, or refuse.

    Never guesses. A phrase this cannot read unambiguously produces no
    horizon date at all -- the objective simply has a softer horizon, which
    is honest, rather than a precise date the user never named."""
    when = _clean(when).lower()
    iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", when)
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3))).isoformat()
        except ValueError:
            return None

    match = re.fullmatch(rf"(\d{{1,2}})\s+({_MONTHS})", when) or re.fullmatch(
        rf"({_MONTHS})\s+(\d{{1,2}})",
        when,
    )
    if not match:
        return None
    first, second = match.group(1), match.group(2)
    if first.isdigit():
        day, month_name = int(first), second
    else:
        day, month_name = int(second), first
    month = _MONTH_NUMBERS.get(month_name)
    if month is None:
        return None
    try:
        candidate = date(today.year, month, day)
    except ValueError:
        return None
    # A month/day already past this year means next year -- the same
    # roll-forward convention schedule_noticing uses for undated absolute
    # forms, because that is how people state them.
    if candidate < today:
        try:
            candidate = date(today.year + 1, month, day)
        except ValueError:
            return None
    return candidate.isoformat()


def _extract_horizon(title: str, today: date) -> tuple[str, str, str | None]:
    """Split a horizon phrase off a title.

    Returns (title without the horizon, horizon_kind, horizon_date)."""
    horizon_kind = HORIZON_OPEN
    horizon_date: str | None = None

    match = _BY_DATE_RE.search(title)
    if match:
        parsed = _iso_by_date(match.group("when"), today)
        title = _BY_DATE_RE.sub(" ", title, count=1)
        if parsed:
            horizon_kind = HORIZON_BY_DATE
            horizon_date = parsed

    if horizon_kind == HORIZON_OPEN and _THIS_WEEK_RE.search(title):
        title = _THIS_WEEK_RE.sub(" ", title, count=1)
        # Deliberately NOT resolved to a date. See the module docstring.
        horizon_kind = HORIZON_THIS_WEEK

    return _clean(re.sub(r"\s{2,}", " ", title)), horizon_kind, horizon_date


# ------------------------------------------------------------ recognition


def parse_intent(text: str, today: date | None = None) -> ObjectiveIntent | None:
    """
    Recognise an *explicit* objective instruction, or nothing at all.

    Returns None for the overwhelmingly common case, in which the turn
    proceeds exactly as it did before this existed. Never raises.

    Order is deliberate: completion and abandonment are checked before
    establishment, because "the roof is sorted" must never be recognised as a
    new objective to pursue -- that failure mode would create a fresh nag out
    of the very sentence meant to end one.
    """
    utterance = _clean(text)
    if not utterance:
        return None

    today = today or date.today()

    if _NOT_AN_OBJECTIVE.search(utterance):
        return None

    for pattern in _COMPLETE_RES:
        match = pattern.search(utterance)
        if not match:
            continue
        subject = _bounded(match.group("subject"))
        if not subject:
            continue
        return ObjectiveIntent(
            action=INTENT_COMPLETE,
            described_as=f'mark "{subject}" done',
            subject=subject,
        )

    for pattern in _ABANDON_RES:
        match = pattern.search(utterance)
        if not match:
            continue
        subject = _bounded(match.group("subject"))
        if not subject:
            continue
        return ObjectiveIntent(
            action=INTENT_ABANDON,
            described_as=f'stop pursuing "{subject}"',
            subject=subject,
        )

    for pattern in _LIST_RES:
        if pattern.search(utterance):
            return ObjectiveIntent(
                action=INTENT_LIST,
                described_as="list what you're working towards",
            )

    for pattern in _OPEN_RES:
        match = pattern.search(utterance)
        if not match:
            continue
        raw_title = _bounded(match.group("title"))
        if not raw_title:
            continue
        title, horizon_kind, horizon_date = _extract_horizon(raw_title, today)
        if not title:
            continue
        return ObjectiveIntent(
            action=INTENT_OPEN,
            described_as=f'take on "{title}"',
            title=title,
            outcome_statement=utterance[:_MAX_TITLE_CHARS],
            horizon_kind=horizon_kind,
            horizon_date=horizon_date,
        )

    return None


def match_objective(subject: str, objectives: list[Any]) -> Any | None:
    """Find the one live objective a subject phrase refers to, or None.

    Deliberately strict: an exact-ish word-overlap match against exactly one
    candidate. Two plausible candidates produce None, and the caller asks
    rather than guessing -- acting on the wrong objective is worse than
    asking, particularly when the action is "stop pursuing this forever"."""
    key = _match_key(subject)
    if not key:
        return None

    exact = [o for o in objectives if _match_key(getattr(o, "title", "")) == key]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None

    key_words = set(key.split())
    if not key_words:
        return None
    overlapping = [
        o
        for o in objectives
        if key_words & set(_match_key(getattr(o, "title", "")).split())
        and (
            key in _match_key(getattr(o, "title", "")) or _match_key(getattr(o, "title", "")) in key
        )
    ]
    return overlapping[0] if len(overlapping) == 1 else None


def _match_key(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", (text or "").lower()).strip()


def relates_to(objective: Any, utterance: str) -> bool:
    """Whether an utterance plausibly concerns this objective.

    Used only to decide whether evidence obtained during a turn is worth
    attaching to an objective. Conservative on purpose, and never used to
    decide anything the user would notice if it were wrong: a missed
    attachment costs one piece of continuity, while a wrong one puts an
    unrelated fact into an objective's history where it will later be read
    back as if it belonged there.
    """
    key_words = {w for w in _match_key(getattr(objective, "title", "")).split() if len(w) > 3}
    if not key_words:
        return False
    utterance_words = set(_match_key(utterance).split())
    return bool(key_words & utterance_words)


# ------------------------------------------------------------- rendering


def _horizon_phrase(objective: Any) -> str:
    kind = getattr(objective, "horizon_kind", HORIZON_OPEN)
    if kind == HORIZON_BY_DATE and getattr(objective, "horizon_date", None):
        return f" by {objective.horizon_date}"
    if kind == HORIZON_THIS_WEEK:
        return " this week"
    return ""


def render_opened(objective: Any) -> str:
    return (
        f"Right -- I'll keep track of that: {objective.title}{_horizon_phrase(objective)}. "
        "I'll hold on to it and bring it back up rather than making you remember to."
    )


def render_list(objectives: list[Any]) -> str:
    """What Bartholomew is currently carrying. Only live objectives ever
    reach this function; completed ones are gone, not listed as history."""
    if not objectives:
        return "Nothing at the moment -- I'm not carrying any objectives for you right now."
    lines = ["Here's what I'm carrying for you:"]
    for objective in objectives:
        status = ""
        if getattr(objective, "status", "") == "blocked":
            status = " (blocked)"
        lines.append(f"- {objective.title}{_horizon_phrase(objective)}{status}")
    return "\n".join(lines)


def render_completed(objective: Any, resolution: str = RESOLUTION_ACHIEVED) -> str:
    if resolution == RESOLUTION_NO_LONGER_NEEDED:
        return f"Understood -- {objective.title} is off the list. I won't bring it up again."
    return f"Good -- that's {objective.title} done. I've recorded it and I won't raise it again."


def render_abandoned(objective: Any) -> str:
    return (
        f"Alright, I've stopped pursuing {objective.title}. "
        "It won't come back up unless you raise it."
    )


def render_not_found(subject: str) -> str:
    return (
        f'I don\'t have an objective matching "{subject}", so there was nothing to change. '
        "Nothing has been recorded."
    )


def render_ambiguous(subject: str, objectives: list[Any]) -> str:
    titles = ", ".join(f'"{o.title}"' for o in objectives[:5])
    return (
        f'"{subject}" could mean more than one of the things I\'m tracking ({titles}), '
        "so I haven't changed anything. Which one did you mean?"
    )


def render_failure(described_as: str, reason: str | None) -> str:
    return (
        f"I tried to {described_as} and it didn't go through"
        f"{f': {reason}' if reason else ''}. Nothing was recorded."
    )


_EVENT_LEAD = {
    EVENT_FACT: "",
    EVENT_DECISION: "you decided: ",
    EVENT_ACTION: "done: ",
}


def render_continuity(objective: Any, events: list[Any], *, since: str | None = None) -> str:
    """The continuity summary -- the sentence this whole slice exists for.

    Derived from the event rows each time, never stored. `since` is only used
    to phrase it honestly ("since I last mentioned this"): the caller has
    already filtered the events.

    Proposals are excluded. If a proposal were rendered here it would sit in
    a list of things that have happened, and a reader has no way to tell a
    considered idea from a completed one once they share a bullet.
    """
    lines = [f"About {objective.title}{_horizon_phrase(objective)} --"]

    substantive = [e for e in events if getattr(e, "event_kind", None) != EVENT_PROPOSAL]
    if substantive:
        lines.append(
            "since I last mentioned this:" if since else "here's where it stands:",
        )
        for event in substantive:
            lead = _EVENT_LEAD.get(getattr(event, "event_kind", ""), "")
            attribution = _attribution(getattr(event, "provenance", None))
            lines.append(f"- {lead}{event.summary}{attribution}")
    elif since:
        lines.append("nothing has changed since I last mentioned it.")
    else:
        lines.append("nothing has happened on it yet.")

    return "\n".join(lines)


def _attribution(provenance: dict[str, Any] | None) -> str:
    """Name where an external assertion came from.

    Evidence obtained from a provider is reported as that provider's claim,
    not as an established fact -- the same posture `forecast_intents` holds
    to, carried through into the objective's own history."""
    if not isinstance(provenance, dict):
        return ""
    host = provenance.get("provider_host")
    if not host:
        return ""
    return f" (according to {host})"


def render_reengagement(objective: Any, events: list[Any]) -> str:
    """The proactive message. Same continuity body, with the ask at the end.

    Deliberately ends by inviting the user to close it: the fastest way to
    stop hearing about an objective should always be to say it's done."""
    body = render_continuity(objective, events, since=getattr(objective, "last_surfaced_at", None))
    return f"{body}\nStill want me to keep track of this? Tell me if it's sorted."


def reengagement_title(objective: Any) -> str:
    return f"Objective: {objective.title}"
