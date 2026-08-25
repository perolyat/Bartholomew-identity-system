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

**Deliberate narrowing from the planning note (recorded, not silent).** The
note anticipated "explicit dates *and a small set of relative forms*". Only
absolute forms are implemented. A stored fact's text is a frozen quotation of
what the user said at capture time, so a bare relative form ("on Friday",
"next week") is only meaningful relative to *that* moment -- resolving it
against notice-time "today" would silently drift, and would produce reminders
for dates the user never named. Resolving it against the row's capture
timestamp is possible (the row carries `ts`) and is real future work; it is
not guessed at here. See §7's provisional-constants posture: this is
scaffolding tuned from real use, not a frozen boundary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

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

# Absolute date forms only -- see the module docstring for why relative forms
# are deliberately absent.
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


def parse_due_date(text: str, today: date) -> date | None:
    """
    Find the due date a stored fact names, or None.

    Deterministic: the same text and the same `today` always yield the same
    answer. Returns None for anything not matching an absolute form -- never
    a guess, never a nearest-neighbour date.

    Args:
        text: the stored fact's text, e.g. "Car rego: on 5 June".
        today: the caller's notion of the current local date. Passed in, never
            read from the clock, so this stays pure and testable.
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

        due = parse_due_date(text, today)
        if due is None:
            continue

        # Overdue items are deliberately not surfaced. With undated facts
        # resolved to their next occurrence, "overdue" only arises for a date
        # the user pinned to a specific past year -- and a reminder for a date
        # that has already gone is noise, not help. Nothing is lost: the fact
        # stays stored and recallable by asking.
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
