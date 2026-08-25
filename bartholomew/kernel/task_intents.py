"""
Conversational task control -- intent recognition (pure)
========================================================

Turns an ordinary chat utterance into an *explicit* task-management request,
or into nothing at all. `TasksSkill` has existed since Stage 4 and has always
been reachable through `Planner.handle_skill_request()`, but nothing ever
turned "add a task to renew the rego" into that call, so in practice
Bartholomew could only discuss tasks, never do them.

Deliberately pure data and logic -- no persistence, no skill execution, no
I/O, no model call -- the same discipline `personal_facts.py` and
`schedule_noticing.py` hold to. The governed execution stays with the seam in
`runtime_contract.py`, which routes every operation through
`Planner.handle_skill_request()` -> `run_skill_through_runtime_contract()` ->
`SkillRegistry.execute_action()`: the one existing chokepoint, with its
parking brake, skill permissions, Identity policy and audit trail. There is
no second tool executor here and no way to get one.

Three rules this module exists to enforce
------------------------------------------

1. **Ambiguous intent never becomes an action.** Every pattern below requires
   the user to have named a task operation outright ("add a task…", "mark …
   as done", "list my tasks"). Merely *mentioning* tasks matches nothing, and
   anything that matches nothing is left entirely alone for ordinary
   conversation to answer. This is a recogniser for explicit instructions, not
   an inferrer of desires.
2. **Nothing destructive is inferred.** Deletion is deliberately not
   implemented. It is recognised only so it can be *declined truthfully*
   (`INTENT_UNSUPPORTED`), because the alternative -- with task creation now
   genuinely working -- is a plausible-sounding conversational reply implying
   a deletion that never happened.
3. **Naming a task is not the same as identifying one.** `TasksSkill` keys
   `complete`/`update` on a UUID nobody says out loud, so a spoken title has
   to be resolved against real stored tasks. `resolve_task()` returns
   NOT_FOUND or AMBIGUOUS rather than picking a task, and the seam turns those
   into a question, never into an action.

**Scaffolding status.** Like slice 1's extractor, the pattern set here is POC
scaffolding, not the intended long-term boundary of what Bartholomew
understands. Broadening it is real, expected work informed by real use.
Nothing downstream may treat this pattern set as the definition of "a task
request".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from . import schedule_noticing

#: The skill every intent here routes to. One skill_id, matching the grain
#: `evaluate_tool_policy()` and `Identity.yaml`'s `tool_use.allowlist`
#: operate on.
TASKS_SKILL_ID = "tasks"

#: The four operations authorised for conversational control.
INTENT_CREATE = "create"
INTENT_LIST = "list"
INTENT_COMPLETE = "complete"
INTENT_UPDATE = "update"

#: Recognised so it can be refused truthfully -- never executed.
INTENT_UNSUPPORTED = "unsupported"

#: Resolution outcomes for a spoken task title.
RESOLVED = "resolved"
NOT_FOUND = "not_found"
AMBIGUOUS = "ambiguous"

_MAX_TITLE_CHARS = 200
_PRIORITIES = ("low", "medium", "high")
_TRAILING_PUNCT = " \t\r\n.!?,;:\"'"

_STATUS_WORDS = {
    "pending": "pending",
    "open": "pending",
    "outstanding": "pending",
    "completed": "completed",
    "complete": "completed",
    "done": "completed",
    "finished": "completed",
    "overdue": "overdue",
    "late": "overdue",
    "all": "all",
}


@dataclass(frozen=True)
class TaskIntent:
    """One recognised, explicit task instruction.

    `action` is a `TasksSkill` action name (or `INTENT_UNSUPPORTED`).
    `params` are the parameters that can be filled from the utterance alone;
    anything needing a lookup (a `task_id`) is left to `resolve_task()`.
    `subject` is the task title the user spoke, when the operation names one.
    """

    action: str
    params: dict[str, Any] = field(default_factory=dict)
    subject: str | None = None
    #: Human-readable summary of what the user asked for, for the audit
    #: record and for a truthful "I couldn't do that" reply.
    described_as: str = ""


@dataclass(frozen=True)
class TaskResolution:
    """The result of matching a spoken title against real stored tasks."""

    outcome: str
    task: dict[str, Any] | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Patterns. Every one of them requires an explicit task verb or the words
# "task"/"to-do list" -- see rule 1 in the module docstring.
# ---------------------------------------------------------------------------

_DELETE_RE = re.compile(
    r"\b(?:delete|remove|erase|get\s+rid\s+of|throw\s+out)\b[^.?!]*\btasks?\b",
    re.IGNORECASE,
)

_CREATE_RES = (
    re.compile(
        r"\b(?:add|create|make|start)\s+(?:a\s+|an\s+|another\s+)?(?:new\s+)?task\b"
        r"(?:\s+(?:to|for|called|named|titled|about))?\s*[:\-]?\s*(?P<title>.+)",
        re.IGNORECASE,
    ),
    re.compile(r"\bnew\s+task\s*[:\-]\s*(?P<title>.+)", re.IGNORECASE),
    re.compile(
        r"\b(?:add|put)\s+(?P<title>.+?)\s+(?:on|to)\s+(?:my\s+)?"
        r"(?:task\s+list|to-?\s?do\s+list)\b",
        re.IGNORECASE,
    ),
)

_LIST_RES = (
    # The determiner is deliberately allowed on either side of the status
    # word and deliberately does NOT include "all": "list all tasks" names a
    # filter, and a determiner alternation that swallowed it would silently
    # turn it into the default (pending) filter -- reporting a subset while
    # answering a question about everything.
    re.compile(
        r"\b(?:list|show(?:\s+me)?)\s+(?:my\s+|the\s+)?"
        r"(?:(?P<status>pending|completed|complete|done|finished|overdue|open|outstanding|late|all)\s+)?"
        r"(?:my\s+|the\s+)?tasks\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bwhat(?:'s|\s+is|\s+are)?\s+(?:on\s+)?(?:my\s+)?(?:task\s+list|to-?\s?do\s+list)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bwhat\s+tasks\s+(?:do\s+i\s+have|are\s+"
        r"(?P<status>pending|completed|overdue|open|outstanding|late|due))\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:do\s+i\s+have|have\s+i\s+got)\s+any\s+(?:tasks|to-?\s?dos)\b", re.IGNORECASE),
)

_COMPLETE_RES = (
    re.compile(
        r"\b(?:complete|finish|close|tick\s+off|check\s+off)\s+(?:the\s+|my\s+)?"
        r"(?:task\s+)?(?:called\s+|named\s+)?[\"']?(?P<title>.+?)[\"']?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bmark\s+(?:the\s+|my\s+)?(?:task\s+)?[\"']?(?P<title>.+?)[\"']?\s+"
        r"(?:as\s+)?(?:complete|completed|done|finished)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:i(?:'ve|\s+have)?\s+)?(?:finished|completed)\s+(?:the\s+|my\s+)?"
        r"[\"']?(?P<title>.+?)[\"']?\s+task\b",
        re.IGNORECASE,
    ),
)

_RENAME_RE = re.compile(
    r"\brename\s+(?:the\s+|my\s+)?(?:task\s+)?[\"']?(?P<subject>.+?)[\"']?\s+to\s+"
    r"[\"']?(?P<title>.+?)[\"']?\s*$",
    re.IGNORECASE,
)

_PRIORITY_RES = (
    re.compile(
        r"\b(?:set|change|update)\s+(?:the\s+)?priority\s+(?:of|on|for)\s+(?:the\s+|my\s+)?"
        r"(?:task\s+)?[\"']?(?P<subject>.+?)[\"']?\s+to\s+(?P<priority>low|medium|high)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bmake\s+(?:the\s+|my\s+)?(?:task\s+)?[\"']?(?P<subject>.+?)[\"']?\s+"
        r"(?P<priority>low|medium|high)[-\s]priority\b",
        re.IGNORECASE,
    ),
)

_DUE_DATE_RE = re.compile(
    r"\b(?:set|change|update|move)\s+(?:the\s+)?due\s*date\s+(?:of|on|for)\s+"
    r"(?:the\s+|my\s+)?(?:task\s+)?[\"']?(?P<subject>.+?)[\"']?\s+to\s+(?P<when>.+)$",
    re.IGNORECASE,
)

# Fragments stripped out of a title when building a `create`.
_INLINE_PRIORITY_RE = re.compile(
    r"[,;]?\s*\b(?:with\s+|at\s+)?(?P<priority>low|medium|high)[-\s]priority\b",
    re.IGNORECASE,
)
_INLINE_DUE_RE = re.compile(r"[,;]?\s*\bdue\s+(?:on\s+|by\s+)?(?P<when>.+)$", re.IGNORECASE)
_LEADING_ARTICLE_RE = re.compile(r"^(?:the|a|an|my)\s+", re.IGNORECASE)
_TRAILING_TASK_RE = re.compile(r"\s+tasks?$", re.IGNORECASE)


def _clean(text: str) -> str:
    return " ".join((text or "").split()).strip(_TRAILING_PUNCT)


def _bounded_title(text: str) -> str:
    title = _clean(text)
    if len(title) > _MAX_TITLE_CHARS:
        title = title[:_MAX_TITLE_CHARS].rstrip()
    return title


def _iso_due(when: str, today: date) -> str | None:
    """Parse a spoken due date, reusing slice 2's absolute-date parser.

    One date-parsing authority, not two: whatever `schedule_noticing`
    understands is exactly what a task due date understands, and whatever it
    refuses to guess at is refused here too. `TasksSkill` compares `due_date`
    lexically against an ISO-with-Z timestamp, so the returned value is shaped
    to sort correctly against one.
    """
    parsed = schedule_noticing.parse_due_date(when, today)
    if parsed is None:
        return None
    return f"{parsed.isoformat()}T00:00:00Z"


def _normalise_status(word: str | None) -> str:
    return _STATUS_WORDS.get((word or "").lower(), "pending")


def parse_intent(text: str, today: date | None = None) -> TaskIntent | None:
    """
    Recognise an explicit task instruction in one utterance, or return None.

    None means "this is ordinary conversation" and is by far the common case:
    the caller then behaves exactly as it did before this module existed.

    Order is deliberate. Destructive phrasing is checked first so that
    "delete the shopping task" can never fall through into a pattern that
    would act on it; `complete` is checked before the broad `create` shapes
    because "finish the rego task" and "add a task" are different verbs and
    must not compete.

    Args:
        text: the raw chat utterance.
        today: the caller's current local date, for due-date parsing. Passed
            in, never read from the clock, so this stays pure.
    """
    if not text or not text.strip():
        return None

    today = today or date.today()
    utterance = text.strip()

    if _DELETE_RE.search(utterance):
        return TaskIntent(
            action=INTENT_UNSUPPORTED,
            described_as="delete a task",
        )

    for pattern in _COMPLETE_RES:
        match = pattern.search(utterance)
        if not match:
            continue
        subject = _bounded_title(match.group("title"))
        if not subject:
            continue
        return TaskIntent(
            action=INTENT_COMPLETE,
            subject=subject,
            described_as=f'complete "{subject}"',
        )

    match = _RENAME_RE.search(utterance)
    if match:
        subject = _bounded_title(match.group("subject"))
        new_title = _bounded_title(match.group("title"))
        if subject and new_title:
            return TaskIntent(
                action=INTENT_UPDATE,
                params={"title": new_title},
                subject=subject,
                described_as=f'rename "{subject}" to "{new_title}"',
            )

    for pattern in _PRIORITY_RES:
        match = pattern.search(utterance)
        if not match:
            continue
        subject = _bounded_title(match.group("subject"))
        priority = match.group("priority").lower()
        if subject:
            return TaskIntent(
                action=INTENT_UPDATE,
                params={"priority": priority},
                subject=subject,
                described_as=f'set "{subject}" to {priority} priority',
            )

    match = _DUE_DATE_RE.search(utterance)
    if match:
        subject = _bounded_title(match.group("subject"))
        due = _iso_due(match.group("when"), today)
        if subject and due:
            return TaskIntent(
                action=INTENT_UPDATE,
                params={"due_date": due},
                subject=subject,
                described_as=f'change the due date of "{subject}"',
            )

    for pattern in _CREATE_RES:
        match = pattern.search(utterance)
        if not match:
            continue
        raw_title = match.group("title")
        params: dict[str, Any] = {}

        priority_match = _INLINE_PRIORITY_RE.search(raw_title)
        if priority_match:
            params["priority"] = priority_match.group("priority").lower()
            raw_title = raw_title[: priority_match.start()] + raw_title[priority_match.end() :]

        due_match = _INLINE_DUE_RE.search(raw_title)
        if due_match:
            due = _iso_due(due_match.group("when"), today)
            if due:
                params["due_date"] = due
                raw_title = raw_title[: due_match.start()]

        title = _bounded_title(raw_title)
        if not title:
            continue
        params["title"] = title
        return TaskIntent(
            action=INTENT_CREATE,
            params=params,
            subject=title,
            described_as=f'create "{title}"',
        )

    for pattern in _LIST_RES:
        match = pattern.search(utterance)
        if not match:
            continue
        groups = match.groupdict()
        status = _normalise_status(groups.get("status"))
        return TaskIntent(
            action=INTENT_LIST,
            params={"status": status},
            described_as=f"list {status} tasks",
        )

    return None


def _match_key(text: str) -> str:
    """Normalise a title for comparison: an utterance says "the milk task"
    where the stored title is "buy milk"."""
    cleaned = _clean(text).lower()
    cleaned = _LEADING_ARTICLE_RE.sub("", cleaned)
    cleaned = _TRAILING_TASK_RE.sub("", cleaned)
    return cleaned.strip()


def resolve_task(subject: str, tasks: list[dict[str, Any]]) -> TaskResolution:
    """
    Match a spoken task title against real stored tasks.

    Returns AMBIGUOUS rather than picking one when more than one task fits,
    and NOT_FOUND rather than creating one when none does. Neither outcome is
    an action -- the seam turns both into a question or a truthful "I
    couldn't find that", which is what "ambiguous intent must not silently
    become an action" means in practice.

    Matching widens in strict order and stops at the first tier that yields
    exactly one task, so an exact title always wins over a partial one.

    Args:
        subject: the title the user spoke.
        tasks: candidate task dicts (`TasksSkill`'s `to_dict()` shape). The
            caller is responsible for having obtained these through the
            governed `list` action.
    """
    key = _match_key(subject)
    if not key or not tasks:
        return TaskResolution(outcome=NOT_FOUND, candidates=[])

    exact = [task for task in tasks if _match_key(task.get("title", "")) == key]
    if len(exact) == 1:
        return TaskResolution(outcome=RESOLVED, task=exact[0])
    if len(exact) > 1:
        return TaskResolution(outcome=AMBIGUOUS, candidates=exact)

    contains = [task for task in tasks if key in _match_key(task.get("title", ""))]
    if len(contains) == 1:
        return TaskResolution(outcome=RESOLVED, task=contains[0])
    if len(contains) > 1:
        return TaskResolution(outcome=AMBIGUOUS, candidates=contains)

    # The other direction: "finish the buy milk and bread task" naming a
    # stored task titled "buy milk".
    contained_by = [
        task
        for task in tasks
        if _match_key(task.get("title", "")) and _match_key(task.get("title", "")) in key
    ]
    if len(contained_by) == 1:
        return TaskResolution(outcome=RESOLVED, task=contained_by[0])
    if len(contained_by) > 1:
        return TaskResolution(outcome=AMBIGUOUS, candidates=contained_by)

    return TaskResolution(outcome=NOT_FOUND, candidates=[])


# ---------------------------------------------------------------------------
# Reply rendering. Presentation only -- every sentence below is built from
# what the governed skill actually returned, never from what was requested.
# ---------------------------------------------------------------------------


def _due_phrase(due_date: str | None) -> str:
    if not due_date:
        return ""
    try:
        parsed = date.fromisoformat(due_date[:10])
    except ValueError:
        return ""
    return f", due {schedule_noticing.format_due_date(parsed)}"


def render_created(task: dict[str, Any]) -> str:
    priority = task.get("priority")
    extra = _due_phrase(task.get("due_date"))
    if priority and priority != "medium":
        extra += f" ({priority} priority)"
    return f'Added task: "{task.get("title")}"{extra}.'


def render_completed(task: dict[str, Any]) -> str:
    return f'Marked "{task.get("title")}" as complete.'


def render_updated(task: dict[str, Any], changed: dict[str, Any]) -> str:
    parts = []
    if "title" in changed:
        parts.append(f'renamed to "{task.get("title")}"')
    if "priority" in changed:
        parts.append(f"priority set to {task.get('priority')}")
    if "due_date" in changed:
        due = _due_phrase(task.get("due_date")).lstrip(", ")
        parts.append(due or "due date updated")
    detail = "; ".join(parts) if parts else "updated"
    return f'Updated "{task.get("title")}" — {detail}.'


def render_list(tasks: list[dict[str, Any]], status: str) -> str:
    label = "" if status == "all" else f"{status} "
    if not tasks:
        return f"You have no {label}tasks."

    lines = [f"You have {len(tasks)} {label}task{'s' if len(tasks) != 1 else ''}:"]
    for task in tasks:
        line = f"- {task.get('title')}"
        due = _due_phrase(task.get("due_date"))
        if due:
            line += due
        priority = task.get("priority")
        if priority and priority != "medium":
            line += f" ({priority} priority)"
        if status == "all" and task.get("status"):
            line += f" [{task['status']}]"
        lines.append(line)
    return "\n".join(lines)


def render_not_found(subject: str) -> str:
    return (
        f"I couldn't find a task called \"{subject}\", so I haven't changed anything. "
        "Ask me to list your tasks if you'd like to see what's there."
    )


def render_ambiguous(subject: str, candidates: list[dict[str, Any]]) -> str:
    names = "\n".join(f"- {task.get('title')}" for task in candidates)
    return (
        f'More than one task matches "{subject}", so I haven\'t changed anything:\n'
        f"{names}\nWhich one do you mean?"
    )


def render_unsupported(described_as: str) -> str:
    return (
        f"I can't {described_as} from a conversation — deleting tasks isn't something "
        "I'm set up to do this way, so nothing has changed. I can create, list, "
        "complete and update tasks."
    )


def render_failure(described_as: str, error: str | None) -> str:
    reason = f" ({error})" if error else ""
    return f"I tried to {described_as} but it didn't go through{reason}. Nothing has changed."
