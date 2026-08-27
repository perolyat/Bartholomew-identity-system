"""
Conversational forecast requests -- intent recognition and rendering (pure)
==========================================================================

The Executive half of the Golden Path first slice. This module decides *that*
an external capability is needed and *how the returned evidence is spoken
about*; it never decides anything on the provider's behalf and never touches
the network.

Deliberately pure data and logic -- no persistence, no skill execution, no
I/O, no model call -- the same discipline `task_intents.py` and
`personal_facts.py` hold to. Governed execution stays with the seam in
`runtime_contract.py`, which routes through `Planner.handle_skill_request()`
-> `run_skill_through_runtime_contract()` -> `SkillRegistry.execute_action()`.
There is no second tool executor here and no way to get one.

Three rules this module exists to enforce
------------------------------------------

1. **Ambiguous intent never becomes an external disclosure.** Every pattern
   below requires the user to have asked for a forecast outright. Merely
   *mentioning* rain or a season matches nothing. Because matching sends a
   location to a third party, the bar for matching is deliberately higher
   than it would be for a local action -- a false positive here is a
   disclosure, not just a wasted call.

2. **A named place is not a location.** Bartholomew has no geocoder and this
   slice does not add one. A request for somewhere other than the configured
   location is recognised only so it can be *declined truthfully*
   (`INTENT_UNSUPPORTED_PLACE`), because the alternative -- quietly answering
   for the configured location instead -- would be a confidently wrong answer
   about a different place.

3. **The provider never speaks in Bartholomew's voice.** Every renderer below
   attributes the numbers to the provider and says when they were fetched.
   The provider supplied evidence; the sentence, the judgement, and the
   user's objective remain Bartholomew's. `render_forecast()` states the
   evidence and, where it is clear-cut, what it means for the thing the user
   actually asked about -- which is the Executive using the result, not
   relaying it.

**Scaffolding status.** Like slice 1's extractor and slice 2's recogniser,
this pattern set is POC scaffolding, not the intended long-term boundary of
what Bartholomew understands. Nothing downstream may treat it as the
definition of "a forecast request".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

#: The skill every intent here routes to. One skill_id, matching the grain
#: `evaluate_tool_policy()` and `Identity.yaml`'s `tool_use.allowlist`
#: operate on.
FORECAST_SKILL_ID = "forecast"

#: The one action authorised for conversational use.
INTENT_LOOKUP = "lookup"

#: Recognised so it can be refused truthfully -- never executed.
INTENT_UNSUPPORTED_PLACE = "unsupported_place"

#: Bound on the horizon a sentence can ask for. Mirrors the skill's own.
MAX_DAYS = 7

_MAX_LABEL_CHARS = 60

# An explicit forecast request: a weather word plus a request verb. Both are
# required -- "it's freezing in here" contains a weather word and asks for
# nothing.
_WEATHER_WORDS = r"(?:weather|forecast|rain|raining|sunny|temperature|hot|cold|wet|dry|snow)"
_REQUEST_VERBS = (
    r"(?:what(?:'s| is| will)?|how(?:'s| is| will)?|is it|will it|are we|"
    r"check|look up|tell me|going to|gonna|do you know)"
)

# A bare noun phrase counts only as a question ("weather forecast for
# tomorrow?"), never as a statement. Without the question mark, "a
# half-assembled forecast is a fabricated forecast" reads as a request --
# and a false positive here is a *disclosure*, not a wasted call (rule 1).
_EXPLICIT_REQUEST = re.compile(
    rf"\b{_REQUEST_VERBS}\b[^.?!]*\b{_WEATHER_WORDS}\b"
    rf"|\b(?:weather|rain)\b[^.!]*\b(?:forecast|outlook)\b[^.!]*\?",
    re.IGNORECASE,
)

# A place other than "here". Matched only to decline; never used to look
# anything up.
_NAMED_PLACE = re.compile(
    r"\b(?:in|for|at|over|around)\s+((?:[A-Z][\w'-]*)(?:[ -](?:[A-Z][\w'-]*|of|the|upon)){0,3})",
)

_HERE_WORDS = re.compile(
    r"\b(?:here|home|outside|out there|my (?:place|area|suburb|town|city))\b",
    re.IGNORECASE,
)

# Horizon phrases, longest-first so "this week" is not shadowed by "today".
_HORIZON_PATTERNS: tuple[tuple[re.Pattern[str], int, int], ...] = (
    # (pattern, day offset from today, number of days)
    (re.compile(r"\bday after tomorrow\b", re.IGNORECASE), 2, 1),
    (re.compile(r"\bnext (\d+) days?\b", re.IGNORECASE), 0, 0),  # count from the match
    (re.compile(r"\bthis week\b|\bcoming week\b|\bnext few days\b", re.IGNORECASE), 0, 7),
    (re.compile(r"\bweekend\b", re.IGNORECASE), 0, 7),
    (re.compile(r"\btomorrow\b", re.IGNORECASE), 1, 1),
    (re.compile(r"\btoday\b|\btonight\b|\bright now\b|\bat the moment\b", re.IGNORECASE), 0, 1),
)

#: Words that mean the user cares about rain specifically. Used only to
#: choose which evidence to lead with -- never to change what is requested.
_RAIN_FOCUS = re.compile(r"\brain|wet|dry|umbrella|snow\b", re.IGNORECASE)


@dataclass(frozen=True)
class ForecastIntent:
    """One recognised, explicit forecast request."""

    action: str
    #: What the user asked, bounded, for truthful reporting back.
    described_as: str
    #: Parameters for the governed skill action. Typed values only -- no free
    #: text is ever placed here, because everything here is a candidate for
    #: egress.
    params: dict[str, Any] = field(default_factory=dict)
    #: A place name the user asked about that Bartholomew cannot resolve.
    #: Present only for INTENT_UNSUPPORTED_PLACE.
    place: str | None = None
    #: Whether the user's question was about rain specifically.
    rain_focused: bool = False


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _bounded(text: str) -> str:
    cleaned = _clean(text)
    return cleaned[:_MAX_LABEL_CHARS].strip()


def parse_intent(text: str, today: date | None = None) -> ForecastIntent | None:
    """
    Recognise an *explicit* forecast request, or nothing at all.

    Returns None for the overwhelmingly common case of an utterance that is
    not a forecast request, in which case the turn proceeds exactly as it did
    before this existed. Never raises.
    """
    utterance = _clean(text)
    if not utterance:
        return None

    if not _EXPLICIT_REQUEST.search(utterance):
        return None

    today = today or date.today()
    described_as = _bounded(utterance)
    rain_focused = bool(_RAIN_FOCUS.search(utterance))

    place = _named_place(utterance)
    if place is not None:
        # Declined, not redirected. See rule 2 in the module docstring.
        return ForecastIntent(
            action=INTENT_UNSUPPORTED_PLACE,
            described_as=described_as,
            place=place,
            rain_focused=rain_focused,
        )

    offset, days = _horizon(utterance)
    start_date = today + timedelta(days=offset)

    return ForecastIntent(
        action=INTENT_LOOKUP,
        described_as=described_as,
        params={
            "start_date": start_date.isoformat(),
            "days": days,
        },
        rain_focused=rain_focused,
    )


def _named_place(utterance: str) -> str | None:
    """Extract a place name the user asked about, if it is not 'here'."""
    match = _NAMED_PLACE.search(utterance)
    if not match:
        return None
    candidate = _clean(match.group(1))
    if not candidate or _HERE_WORDS.fullmatch(candidate):
        return None
    return _bounded(candidate)


def _horizon(utterance: str) -> tuple[int, int]:
    """Return (day offset from today, number of days). Defaults to today."""
    for pattern, offset, days in _HORIZON_PATTERNS:
        match = pattern.search(utterance)
        if not match:
            continue
        if days == 0 and match.groups():
            try:
                requested = int(match.group(1))
            except (TypeError, ValueError):  # pragma: no cover - regex guarantees digits
                requested = 1
            return offset, max(1, min(requested, MAX_DAYS))
        return offset, days
    return 0, 1


# -----------------------------------------------------------------------------
# Rendering. Every one of these attributes the evidence to its source; none of
# them can produce a number that did not come back from the provider.
# -----------------------------------------------------------------------------


def _attribution(provenance: dict[str, Any] | None) -> str:
    host = (provenance or {}).get("provider_host") or "an external provider"
    return f"via {host}"


def _describe_day(day: dict[str, Any], rain_focused: bool) -> str:
    parts: list[str] = []
    low = day.get("temperature_min_c")
    high = day.get("temperature_max_c")
    if low is not None and high is not None:
        parts.append(f"{low}-{high}°C")
    chance = day.get("precipitation_probability_pct")
    amount = day.get("precipitation_mm")
    if chance is not None:
        rain = f"{chance}% chance of rain"
        if amount:
            rain += f" ({amount} mm)"
        parts.append(rain)
    elif amount is not None:
        parts.append(f"{amount} mm rain")
    if rain_focused and not parts:  # pragma: no cover - defensive
        parts.append("no precipitation figures returned")
    return ", ".join(parts) if parts else "no figures returned"


def render_forecast(evidence: dict[str, Any], intent: ForecastIntent) -> str:
    """
    State the evidence, attributed, and say what it means for the objective.

    The judgement sentence is added only where the evidence is unambiguous.
    Bartholomew declining to draw a conclusion is preferable to a confident
    one the numbers do not support -- and either way the numbers themselves
    are shown, so the user is never asked to take the judgement on trust.
    """
    days = evidence.get("days") or []
    provenance = evidence.get("provenance") or {}
    if not days:  # pragma: no cover - a successful lookup always has days
        return render_failure(intent, "no forecast data was returned")

    lines = [
        f"{day.get('date')}: {_describe_day(day, intent.rain_focused)}" for day in days[:MAX_DAYS]
    ]
    body = "\n".join(f"- {line}" for line in lines)

    verdict = _verdict(days, intent)
    tail = f"\n\n{verdict}" if verdict else ""

    return (
        f"Here's the forecast ({_attribution(provenance)}, fetched just now):\n{body}"
        f"{tail}\n\nThat's the provider's figure, not something I know independently."
    )


def _verdict(days: list[dict[str, Any]], intent: ForecastIntent) -> str | None:
    """
    Bartholomew's own reading of the evidence -- the Executive step.

    Only emitted for a clear-cut, single-day, rain-focused question, which is
    the case where a bare table genuinely fails to answer what was asked.
    Everything else gets the figures and no verdict.
    """
    if not intent.rain_focused or len(days) != 1:
        return None
    chance = days[0].get("precipitation_probability_pct")
    if not isinstance(chance, (int, float)):
        return None
    if chance >= 60:
        return "On those numbers I'd plan for rain."
    if chance <= 20:
        return "On those numbers rain looks unlikely."
    return None


def render_unsupported_place(intent: ForecastIntent) -> str:
    return (
        f"I can't look up {intent.place} — I can only check the location "
        f"configured for this deployment, and I'd rather say so than answer for "
        f"somewhere else."
    )


def render_unavailable(reason: str) -> str:
    return f"I couldn't get a forecast: {reason} I haven't guessed one."


def render_denied(reason: str) -> str:
    return (
        f"I didn't look that up: {reason} Nothing was sent to the forecast provider."
    )


def render_failure(intent: ForecastIntent, reason: str | None) -> str:
    detail = f" {reason}" if reason else ""
    return (
        f"I tried to look up the forecast and it didn't come back.{detail}"
        f" I don't have a forecast to give you, and I'm not going to invent one."
    )
