"""
Schedule noticing (Usable POC, slice 2)
=======================================

Implements the pure half of `docs/POC_SLICE_2_PROACTIVE_REMINDERS.md`: turn
stored date-bearing personal facts into the small set of reminders that are
genuinely due soon.

Deliberately pure data and logic -- no persistence, no retrieval, no I/O, no
model call, no clock read -- the same discipline `personal_facts.py`,
`competency.py` and `training.py` hold to. "Today" is always passed in by the
caller. The governed reads (`MemoryStore` + `ConsentGate`) and the governed
notification both stay with the scheduler drive in
`kernel/scheduler/drives.py`, so this module cannot become a second Memory
authority or a second delivery path.

What this module does NOT do
-----------------------------
- It does not decide whether a fact may be *read*. The drive gates every row
  through the existing `ConsentGate` before it reaches `select_due()`.
- It does not decide whether a reminder may be *sent*. That is Governance's
  job, at the two existing gates the drive goes through.
- It never guesses a date. A fact whose "when" this module cannot parse
  unambiguously is simply not noticed -- never surfaced wrongly, and recall
  by asking is unaffected.

**Relative forms, resolved against capture time (2026-08-27).** The slice 2
note anticipated "explicit dates *and a small set of relative forms*" and
shipped only the absolute ones, recording the relative half as real future
work to be resolved against the row's own capture timestamp rather than
against notice-time "today". That is what is now implemented, and the
distinction is the whole point: a stored fact's text is a frozen quotation of
what the user said at capture time, so "on Friday" means the Friday after
*that* moment. Resolving it against notice-time today would silently drift and
produce reminders for dates the user never named.

Consequences that are deliberate, not incidental:

- A relative form is resolved **once**, from the capture date, and never
  rolls forward. An undated absolute form ("5 June") does roll forward to its
  next occurrence, because that is how people state recurring things; a
  relative form does not, because "tomorrow" said three weeks ago is a date in
  the past, and `select_due()` drops it. A stale relative fact therefore goes
  quiet rather than firing wrongly.
- A row with **no usable capture timestamp** resolves no relative form at all.
  Without the anchor there is no honest answer, and this module never guesses.
- The relative set is small and unambiguous by construction. Forms whose
  meaning genuinely varies between speakers -- "next Friday" (this coming one,
  or the one after?), "next week" (which day?) -- are **refused**, not
  approximated. Refusing costs a reminder the user can still get by asking;
  guessing costs their trust in every reminder.

See §7's provisional-constants posture: this is scaffolding tuned from real
use, not a frozen boundary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

#: Provisional constants (planning note §7 -- POC scaffolding, never frozen
#: here). Tuned from real use, not from argument.
DEFAULT_LOOK_AHEAD_DAYS = 3
DEFAULT_MAX_REMINDERS_PER_TICK = 3

#: The stored kinds this module notices. `user_schedule` is the kind slice 1
#: captures date-bearing commitments onto; `user_profile` is included solely
#: for the one key below, because a birthday is a date-bearing fact that lands
#: on the profile kind rather than the schedule kind.
NOTICED_KINDS: tuple[str, ...] = ("user_schedule", "user_profile")

#: The only `user_profile` key that is date-bearing. Every other profile fact
#: ("my doctor is Dr Smith") is a standing attribute, not something that falls
#: due, and must never produce a reminder.
NOTICED_PROFILE_KEYS: frozenset[str] = frozenset({"birthday"})

#: Containment allowlist key for the nudges this noticing produces. Lives here
#: with the noticing so the drive and `scheduler/containment.py` cannot drift
#: apart on the spelling.
REMINDER_REASON = "schedule_reminder"

#: Nudge kind for a schedule reminder. Distinct from `system_health` and
#: `curiosity` so containment eligibility (keyed on `reason`) and any future
#: queue UI can tell the three apart.
REMINDER_KIND = "schedule_reminder"

_MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

_MONTHS: dict[str, int] = {}
for _index, _name in enumerate(_MONTH_NAMES, start=1):
    _MONTHS[_name.lower()] = _index
    _MONTHS[_name.lower()[:3]] = _index
_MONTHS["sept"] = 9

# Absolute date forms.
_ISO_RE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_DAY_MONTH_RE = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([A-Za-z]{3,9})\b(?:,?\s+(\d{4}))?",
    re.IGNORECASE,
)
_MONTH_DAY_RE = re.compile(
    r"\b([A-Za-z]{3,9})\s+(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s+(\d{4}))?",
    re.IGNORECASE,
)
# Day-first, deliberately: the deployment's configured timezone is
# Australia/Brisbane (`config/kernel.yaml`), where 5/6 is 5 June. This is a
# provisional constant like every other in §7, not a claim about every locale.
# A numeric pair that cannot be read day-first (13/25) yields None rather than
# being retried month-first -- guessing which way round a user meant it is
# exactly the wrong-reminder failure this module refuses to risk.
_SLASH_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?\b")


# --------------------------------------------------------------------------
# Relative forms, anchored to the row's capture date.
# --------------------------------------------------------------------------

_WEEKDAY_NAMES = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

_WEEKDAYS: dict[str, int] = {}
for _index, _name in enumerate(_WEEKDAY_NAMES):
    _WEEKDAYS[_name.lower()] = _index
    _WEEKDAYS[_name.lower()[:3]] = _index
_WEEKDAYS["tues"] = 1
_WEEKDAYS["thurs"] = 3
_WEEKDAYS["thur"] = 3

#: Small integer words, so "in two days" reads the same as "in 2 days".
_NUMBER_WORDS: dict[str, int] = {
    "one": 1,
    "a": 1,
    "an": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}

_COUNT_PATTERN = r"(\d{1,2}|" + "|".join(_NUMBER_WORDS) + r")"

_TODAY_RE = re.compile(r"\b(?:today|tonight|this\s+evening|this\s+afternoon)\b", re.IGNORECASE)
_DAY_AFTER_TOMORROW_RE = re.compile(r"\bday\s+after\s+tomorrow\b", re.IGNORECASE)
_TOMORROW_RE = re.compile(r"\btomorrow\b", re.IGNORECASE)
_IN_DAYS_RE = re.compile(r"\bin\s+" + _COUNT_PATTERN + r"\s+days?\b", re.IGNORECASE)
_IN_WEEKS_RE = re.compile(r"\bin\s+" + _COUNT_PATTERN + r"\s+weeks?\b", re.IGNORECASE)
_IN_FORTNIGHT_RE = re.compile(r"\bin\s+(?:a\s+)?fortnight\b", re.IGNORECASE)

#: A weekday only counts when something marks it as *when the thing happens*.
#: A bare "Friday" in a sentence is as likely to be describing something as
#: scheduling it, and this module would rather notice nothing than notice the
#: wrong day.
_WEEKDAY_RE = re.compile(
    r"\b(?:on|this|by|due|coming)\s+(?:the\s+)?"
    r"(mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun"
    r"|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)

#: "next Friday" and "next week" mean different things to different people --
#: this coming Friday, or the one after; some day next week, but which? Both
#: are refused outright rather than resolved to a guess. Matching here
#: suppresses the whole relative pass for that text, so a bare "on Friday"
#: elsewhere in the same sentence cannot be used to answer a question the user
#: actually asked in the ambiguous form.
_AMBIGUOUS_RE = re.compile(
    r"\bnext\s+(?:week|month|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun"
    r"|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)

#: An ordinal day-of-month ("the 5th"). Its presence alongside a weekday means
#: the text names a specific calendar day this module could not parse
#: absolutely, so resolving the weekday would risk contradicting it.
_ORDINAL_DAY_RE = re.compile(r"\b\d{1,2}(?:st|nd|rd|th)\b", re.IGNORECASE)


def _count_from(raw: str) -> int | None:
    lowered = raw.lower()
    if lowered.isdigit():
        return int(lowered)
    return _NUMBER_WORDS.get(lowered)


def parse_relative_due_date(text: str, captured_on: date) -> date | None:
    """Resolve a relative "when" against the date the fact was captured.

    Returns None -- never a guess -- for text with no relative form, for the
    forms whose meaning genuinely varies between speakers, and for a weekday
    that sits next to an ordinal day-of-month this module could not read.

    Args:
        text: the stored fact's text, e.g. "Dentist appointment: on Friday".
        captured_on: the local date on which the fact was stored. This is the
            anchor the user's words were spoken against; notice-time "today"
            is deliberately not used here.
    """
    if not text or not text.strip():
        return None

    if _AMBIGUOUS_RE.search(text):
        return None

    if _DAY_AFTER_TOMORROW_RE.search(text):
        return captured_on + timedelta(days=2)

    if _TOMORROW_RE.search(text):
        return captured_on + timedelta(days=1)

    if _TODAY_RE.search(text):
        return captured_on

    if _IN_FORTNIGHT_RE.search(text):
        return captured_on + timedelta(days=14)

    match = _IN_DAYS_RE.search(text)
    if match:
        count = _count_from(match.group(1))
        if count is not None:
            return captured_on + timedelta(days=count)

    match = _IN_WEEKS_RE.search(text)
    if match:
        count = _count_from(match.group(1))
        if count is not None:
            return captured_on + timedelta(weeks=count)

    match = _WEEKDAY_RE.search(text)
    if match and not _ORDINAL_DAY_RE.search(text):
        target = _WEEKDAYS.get(match.group(1).lower())
        if target is not None:
            # Strictly ahead: "on Friday" said on a Friday means the next one,
            # not the day that is already most of the way through.
            delta = (target - captured_on.weekday()) % 7 or 7
            return captured_on + timedelta(days=delta)

    return None


def capture_date(raw_ts: object, tz: timezone | None = None) -> date | None:
    """The local calendar date a stored row was written on, or None.

    Pure: the timezone is passed in, never read from configuration or from the
    host. `MemoryStore` writes `ts` as an ISO-8601 UTC string, but rows written
    by older or duck-typed callers carry other shapes, so anything unparseable
    yields None -- and a row with no capture date resolves no relative form
    rather than falling back to an anchor that would be wrong.
    """
    if isinstance(raw_ts, (int, float)):
        moment = datetime.fromtimestamp(float(raw_ts), timezone.utc)
        return moment.astimezone(tz or timezone.utc).date()

    if not isinstance(raw_ts, str) or not raw_ts.strip():
        return None

    text = raw_ts.strip()
    if text.endswith(("Z", "z")):
        # `datetime.fromisoformat()` did not accept the "Z" suffix before
        # Python 3.11, and this project supports 3.10.
        text = text[:-1] + "+00:00"

    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        # An epoch-seconds string is the one other shape seen in this
        # repository (`drives.py` writes `str(int(time.time()))`).
        try:
            moment = datetime.fromtimestamp(float(text), timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(tz or timezone.utc).date()


def format_due_date(due: date) -> str:
    """Render a due date for a user-facing reminder.

    Built by hand rather than with `strftime("%-d %B %Y")`: the `%-d`
    no-pad flag is a glibc extension and is not available on Windows, which
    this project's test matrix covers (`test_fixtures_windows.py`).
    """
    return f"{due.day} {_MONTH_NAMES[due.month - 1]} {due.year}"


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _resolve_year(month: int, day: int, today: date, explicit_year: int | None) -> date | None:
    """Pick the year for a date the user stated without one.

    An undated "June 5" means the next 5 June that has not already passed.
    That is the only reading that does not require inventing intent: a fact
    the user stated is either still ahead this year, or comes round again
    next year (rego, birthdays, renewals -- the whole population of things
    people state this way).
    """
    if explicit_year is not None:
        return _safe_date(explicit_year, month, day)

    candidate = _safe_date(today.year, month, day)
    if candidate is not None and candidate >= today:
        return candidate
    return _safe_date(today.year + 1, month, day)


def _normalise_two_digit_year(raw: str) -> int:
    year = int(raw)
    if len(raw) == 2:
        return 2000 + year
    return year


def parse_due_date(text: str, today: date, captured_on: date | None = None) -> date | None:
    """
    Find the due date a stored fact names, or None.

    Deterministic: the same inputs always yield the same answer. Returns None
    for anything not matching a form this module reads unambiguously -- never
    a guess, never a nearest-neighbour date.

    Absolute forms are tried first and win outright. Only if none matches is
    the relative pass attempted, and only when `captured_on` is known: a
    relative form means nothing without the moment it was spoken against, so
    without that anchor it is not resolved at all.

    Args:
        text: the stored fact's text, e.g. "Car rego: on 5 June".
        today: the caller's notion of the current local date. Passed in, never
            read from the clock, so this stays pure and testable. Used only to
            roll an undated absolute form ("5 June") forward to its next
            occurrence -- never to anchor a relative form.
        captured_on: the local date the fact was stored on, if known. Omitted,
            relative forms yield None and behaviour is exactly what it was
            before relative parsing existed.
    """
    if not text or not text.strip():
        return None

    match = _ISO_RE.search(text)
    if match:
        parsed = _safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if parsed is not None:
            return parsed

    match = _DAY_MONTH_RE.search(text)
    if match:
        month = _MONTHS.get(match.group(2).lower())
        if month is not None:
            year = _normalise_two_digit_year(match.group(3)) if match.group(3) else None
            parsed = _resolve_year(month, int(match.group(1)), today, year)
            if parsed is not None:
                return parsed

    match = _MONTH_DAY_RE.search(text)
    if match:
        month = _MONTHS.get(match.group(1).lower())
        if month is not None:
            year = _normalise_two_digit_year(match.group(3)) if match.group(3) else None
            parsed = _resolve_year(month, int(match.group(2)), today, year)
            if parsed is not None:
                return parsed

    match = _SLASH_RE.search(text)
    if match:
        day = int(match.group(1))
        month = int(match.group(2))
        year = _normalise_two_digit_year(match.group(3)) if match.group(3) else None
        if 1 <= month <= 12:
            parsed = _resolve_year(month, day, today, year)
            if parsed is not None:
                return parsed

    if captured_on is not None:
        return parse_relative_due_date(text, captured_on)

    return None


@dataclass(frozen=True)
class NoticedReminder:
    """One upcoming, date-bearing stored fact, ready to be surfaced.

    Carries the identity the containment policy keys on. It is `(kind, key,
    due date)` -- deliberately *not* the message text -- so restating the
    underlying fact (which rewords the message via `upsert_memory()`) cannot
    produce a second unresolved reminder for the same commitment, and so a
    genuinely different due date always is a distinct one.
    """

    kind: str
    key: str
    text: str
    due_date: date
    memory_id: int | None = None

    @property
    def identity(self) -> str:
        return f"{self.kind}:{self.key}:{self.due_date.isoformat()}"

    @property
    def message(self) -> str:
        return f"Reminder: {self.text} — due {format_due_date(self.due_date)}"

    @property
    def title(self) -> str:
        return "Bartholomew reminder"

    def days_until(self, today: date) -> int:
        return (self.due_date - today).days


def is_noticeable_row(kind: str | None, key: str | None) -> bool:
    """True if a stored row is one this module is willing to look at.

    `user_profile` is narrowed to its one date-bearing key, so a standing
    attribute ("my car is a Corolla") can never be read as something that
    falls due.
    """
    if kind == "user_schedule":
        return True
    if kind == "user_profile":
        return (key or "") in NOTICED_PROFILE_KEYS
    return False


def select_due(
    rows,
    today: date,
    look_ahead_days: int = DEFAULT_LOOK_AHEAD_DAYS,
    limit: int = DEFAULT_MAX_REMINDERS_PER_TICK,
    tz: timezone | None = None,
) -> list[NoticedReminder]:
    """
    Select the stored facts falling due inside the look-ahead window.

    Args:
        rows: stored `memories` rows (dicts with `kind`, `key`, and
            `summary`/`value`). The caller is responsible for having gated
            these through `ConsentGate` first -- this function applies no
            privacy policy of its own and must never be given ungated rows.
        today: the caller's current local date.
        look_ahead_days: how far ahead a due date triggers a reminder.
        limit: at most this many reminders per call, closest-due first. The
            rest are not discarded -- they are simply not noticed *this* tick
            and, still being due, are noticed on the next one.
        tz: the timezone each row's `ts` is converted into to get the local
            date it was captured on, which is the anchor a relative form
            ("on Friday") resolves against. Passed in, never read from
            configuration, so this stays pure. Omitted, UTC is used -- never
            the host's local time, so behaviour never depends on an unstated
            machine setting.

    Returns:
        Reminders sorted closest-due first, then by identity for a stable
        order when two fall on the same day.
    """
    horizon = today + timedelta(days=max(0, look_ahead_days))
    noticed: list[NoticedReminder] = []

    for row in rows or []:
        kind = row.get("kind")
        key = row.get("key") or ""
        if not is_noticeable_row(kind, key):
            continue

        text = row.get("summary") or row.get("value") or ""
        if not isinstance(text, str) or not text.strip():
            continue
        text = text.strip()

        due = parse_due_date(text, today, capture_date(row.get("ts"), tz))
        if due is None:
            continue

        # Overdue items are deliberately not surfaced. Undated absolute forms
        # resolve to their next occurrence, so "overdue" arises only for a
        # date the user pinned to a specific past year, or for a relative form
        # whose anchor has since gone by ("tomorrow", captured three weeks
        # ago). Both are noise rather than help -- and for the relative case,
        # dropping it here is precisely what stops a stale fact from drifting
        # forward into a reminder for a date the user never named. Nothing is
        # lost: the fact stays stored and recallable by asking.
        if due < today or due > horizon:
            continue

        noticed.append(
            NoticedReminder(
                kind=kind,
                key=key,
                text=text,
                due_date=due,
                memory_id=row.get("id"),
            ),
        )

    noticed.sort(key=lambda item: (item.due_date, item.identity))
    return noticed[: max(0, limit)]
