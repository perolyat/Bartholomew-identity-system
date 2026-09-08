"""Task interpretation: an ordinary sentence -> named capabilities, or a question.

Deliberately pure. No persistence, no model call, no I/O, no governance and no
actuation --- the same discipline `kernel/task_intents.py` and
`kernel/personal_facts.py` hold to, and for the same reason: the governed
execution belongs to a seam, and a recogniser that could reach a store is a
recogniser that has to be reviewed as if it could act.

Three rules this module exists to enforce
------------------------------------------

1. **The vocabulary is closed, and closed the same way the envelope's is.**
   Every step this module can produce names a `CapabilityKind` from
   `bartholomew.actuation.capabilities`. There is no branch here that can emit
   "run a command", because there is no capability that means it. A sentence
   asking for something outside the vocabulary is recorded as *unsupported*
   and declined truthfully --- never mapped onto "the nearest thing", which is
   how a narrow capability becomes a broad one.

2. **Ambiguity becomes a question, never the most likely reading.** "Open it"
   names no referent. This module reports a `referent_unresolved` ambiguity;
   the seam turns that into an `awaiting_response` obligation. A recogniser
   that guessed here would be a recogniser that opened the wrong thing on
   somebody's computer, and W03's research contract (`W03_TEST_CONTRACTS.md`
   §4) requires the uncertainty to survive into behaviour, not merely into a
   log line.

3. **Nothing here fills a slot from memory.** A parameter is present because
   the person said it. `evidence.py` explains why recalled memory is never
   allowed to supply one.

**Scaffolding status.** As with `task_intents.py`, the pattern set is POC
scaffolding, not the definition of what Bartholomew understands. Broadening it
is expected work. Nothing downstream may treat this pattern set as the boundary
of the executive: the boundary is the capability vocabulary, which is enforced
in `selection.py` against what the device actually declares.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from bartholomew.actuation.capabilities import CapabilityKind
from bartholomew.actuation.parameters import (
    ACCESSIBILITY_OPERATIONS,
    MAX_TYPED_CHARS,
    WINDOW_OPERATIONS,
)

#: How the seam names an ambiguity when it opens an obligation.
AMBIGUITY_REFERENT = "referent_unresolved"
AMBIGUITY_NO_CAPABILITY = "no_capability"
AMBIGUITY_PARAMETER_MISSING = "parameter_missing"
AMBIGUITY_MULTIPLE_READINGS = "multiple_readings"

#: Why a recognised-but-refusable request is declined rather than translated.
UNSUPPORTED_OUT_OF_VOCABULARY = "out_of_vocabulary"

#: Bare pronouns and demonstratives that name nothing on their own. A sentence
#: whose object is one of these is the canonical ambiguous instruction.
_BARE_REFERENTS = frozenset(
    {
        "it",
        "that",
        "this",
        "them",
        "those",
        "these",
        "the file",
        "the thing",
        "the app",
        "the window",
        "the document",
        "the one",
        # The same words with the article already stripped. "Open the file" and
        # "open file" name a referent exactly as precisely as each other, which
        # is to say not at all, and the recogniser must not become less careful
        # because an article was removed on the way here.
        "file",
        "thing",
        "app",
        "window",
        "document",
        "one",
        "stuff",
        "everything",
    },
)

#: Verbs whose object is a thing to be *opened*. Kept apart from the launch
#: verbs below because "open notepad" and "open the report" are different
#: capabilities with different risk, and conflating them would let a path
#: request be served by starting a program.
_OPEN_VERBS = r"(?:open|bring up|pull up|show me|show)"
_LAUNCH_VERBS = r"(?:launch|start|run|open)"

#: Requests that are unambiguously outside the capability vocabulary. Matched
#: only so they can be declined in the user's own words: with the executive now
#: genuinely able to propose actions, a plausible-sounding conversational reply
#: implying a deletion that never happened is the failure this prevents (the
#: reasoning `task_intents.py` records for its own `INTENT_UNSUPPORTED`).
_OUT_OF_VOCABULARY = (
    (re.compile(r"\b(?:delete|remove|erase|rm|uninstall|wipe)\b", re.I), "delete anything"),
    (
        re.compile(r"\b(?:run|execute)\s+(?:a\s+)?(?:command|script|shell|powershell|cmd)\b", re.I),
        "run a command, script or shell",
    ),
    (re.compile(r"\b(?:install|download and (?:run|install))\b", re.I), "install software"),
    (re.compile(r"\b(?:send|email|e-mail|post|publish|tweet|submit)\b", re.I), "send or publish"),
    (re.compile(r"\b(?:buy|purchase|pay|order)\b", re.I), "buy or pay for anything"),
    (
        re.compile(r"\b(?:rename|move|copy|overwrite|edit)\s+(?:the\s+)?file\b", re.I),
        "create, move, rename or edit a file",
    ),
)

#: Refusals that must be read *before* the general set above, because the
#: general set would otherwise claim them for the wrong reason. "Click the Send
#: button" is a request to press a control, and declining it as "send or
#: publish" would be a true refusal with a false explanation.
_OUT_OF_VOCABULARY_FIRST = (
    (
        re.compile(r"\b(?:click|press|push|tap)\s+(?:the\s+)?\S+", re.I),
        "press a button",
    ),
)

_URL = re.compile(r"\bhttps?://\S+", re.I)
#: A bare Windows path: a drive letter, or a UNC share. Deliberately not a
#: guess at a relative path --- a relative path resolves against whatever
#: directory something happened to be started in, and the envelope refuses one.
_PATH = re.compile(r"(?:[A-Za-z]:\\\\?|\\\\\\\\)[^\s\"']*")
_QUOTED = re.compile(r"[\"“]([^\"”]{1,2000})[\"”]|'([^']{1,2000})'")

_APP_TOKEN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

#: Which window operation an ordinary word means. Only the six the envelope
#: implements; anything else is not translated.
_WINDOW_WORDS = {
    "focus": "focus",
    "foreground": "focus",
    "front": "focus",
    "minimise": "minimize",
    "minimize": "minimize",
    "maximise": "maximize",
    "maximize": "maximize",
    "restore": "restore",
    "unminimise": "restore",
    "unminimize": "restore",
}

_ACCESSIBILITY_WORDS = {
    "expand": "expand",
    "collapse": "collapse",
    "scroll up": "scroll_up",
    "scroll down": "scroll_down",
}

#: Sentence separators for a multi-step instruction. Bounded and explicit: a
#: plan is a sequence the user actually asked for, never a decomposition the
#: executive invented.
_SEQUENCE = re.compile(
    r"\s*(?:,\s*(?:and\s+)?then\s+|\s+and\s+then\s+|\s*;\s*|\s+then\s+)\s*",
    re.I,
)

#: A plan may not be longer than this. A bound rather than a policy: an
#: instruction that expands into dozens of governed actions is one the person
#: should be asked about, not one the executive should quietly start.
MAX_PLAN_STEPS = 6


@dataclass(frozen=True)
class IntentStep:
    """One recognised capability request, before any device is consulted."""

    capability: str
    parameters: dict[str, Any]
    described_as: str

    def __post_init__(self) -> None:
        # A step that does not name a capability in the closed vocabulary
        # cannot be constructed at all. The check is here, on the value type,
        # so no later edit to the patterns can produce one.
        CapabilityKind(self.capability)


@dataclass(frozen=True)
class Ambiguity:
    """Something the executive will not decide on the person's behalf."""

    code: str
    question: str
    subject: str | None = None


@dataclass(frozen=True)
class Unsupported:
    """Something asked for that no capability can express. Declined, not mapped."""

    code: str
    described_as: str
    said: str


@dataclass(frozen=True)
class TaskIntent:
    """What one instruction was understood to be.

    Exactly one of these is true of a well-formed result, and the seam branches
    on it in this order: `unsupported` is declined, `ambiguities` become a
    question, `steps` become a plan. `steps` and `ambiguities` can both be
    non-empty --- "open notepad and then open it" --- and the ambiguity wins,
    because half of an instruction is not the instruction.
    """

    instruction: str
    steps: tuple[IntentStep, ...] = ()
    ambiguities: tuple[Ambiguity, ...] = ()
    unsupported: tuple[Unsupported, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def actionable(self) -> bool:
        """Whether this may become a plan at all."""
        return bool(self.steps) and not self.ambiguities and not self.unsupported

    def as_dict(self) -> dict[str, Any]:
        return {
            "instruction": self.instruction,
            "steps": [
                {
                    "capability": s.capability,
                    "parameters": dict(s.parameters),
                    "described_as": s.described_as,
                }
                for s in self.steps
            ],
            "ambiguities": [
                {"code": a.code, "question": a.question, "subject": a.subject}
                for a in self.ambiguities
            ],
            "unsupported": [
                {"code": u.code, "described_as": u.described_as, "said": u.said}
                for u in self.unsupported
            ],
            "notes": list(self.notes),
        }


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _quoted(text: str) -> str | None:
    match = _QUOTED.search(text)
    if match is None:
        return None
    return match.group(1) if match.group(1) is not None else match.group(2)


def _app_id(word: str | None) -> str | None:
    """An allowlist key, or None. Never a path and never an executable name.

    The value is a *key* the device's application allowlist resolves to a path;
    the path never comes from the sentence. A token that does not look like a
    key is refused here rather than passed on to be refused later, so the
    refusal names the right thing.
    """
    if not word:
        return None
    token = word.strip().strip(".!?").lower()
    if not token or not _APP_TOKEN.match(token):
        return None
    if "\\" in token or "/" in token or token.endswith(".exe"):
        return None
    return token


def _bare_referent(fragment: str) -> str | None:
    stripped = _clean(fragment).lower().rstrip(".!?")
    if stripped in _BARE_REFERENTS:
        return stripped
    return None


def _unsupported_for(fragment: str) -> Unsupported | None:
    for pattern, described in _OUT_OF_VOCABULARY_FIRST + _OUT_OF_VOCABULARY:
        if pattern.search(fragment):
            return Unsupported(
                code=UNSUPPORTED_OUT_OF_VOCABULARY,
                described_as=described,
                said=fragment,
            )
    return None


def _parse_type_text(fragment: str) -> IntentStep | Ambiguity | None:
    match = re.search(r"\b(?:type|enter|write)\b\s*(?:out\s+)?(.*)$", fragment, re.I)
    if match is None:
        return None
    rest = match.group(1).strip()
    quoted = _quoted(rest)
    if quoted is None:
        return Ambiguity(
            code=AMBIGUITY_PARAMETER_MISSING,
            question=(
                "What exactly should I type? Quote the text and I will propose it "
                "for your approval --- I will not guess at wording that goes into "
                "your machine."
            ),
            subject=rest or None,
        )
    if len(quoted) > MAX_TYPED_CHARS:
        return Ambiguity(
            code=AMBIGUITY_PARAMETER_MISSING,
            question=(
                f"That text is longer than the {MAX_TYPED_CHARS}-character bound on a "
                "single typing action. Shorten it and I will propose it."
            ),
            subject=None,
        )
    return IntentStep(
        capability=CapabilityKind.TYPE_TEXT.value,
        parameters={"text": quoted},
        described_as="type the quoted text into the focused control",
    )


def _parse_clipboard(fragment: str) -> IntentStep | Ambiguity | None:
    if re.search(
        r"\b(?:read|check|look at|what(?:'s| is) (?:on|in))\b.*\bclipboard\b",
        fragment,
        re.I,
    ):
        return IntentStep(
            capability=CapabilityKind.CLIPBOARD_READ.value,
            parameters={},
            described_as="read the clipboard once",
        )
    if re.search(r"\b(?:copy|put)\b.*\b(?:to|on|into)\s+(?:the\s+)?clipboard\b", fragment, re.I):
        quoted = _quoted(fragment)
        if quoted is None:
            return Ambiguity(
                code=AMBIGUITY_PARAMETER_MISSING,
                question=(
                    "What exactly should I put on the clipboard? Quote it and I will "
                    "propose it for your approval."
                ),
                subject=None,
            )
        return IntentStep(
            capability=CapabilityKind.CLIPBOARD_WRITE.value,
            parameters={"text": quoted},
            described_as="put the quoted text on the clipboard",
        )
    return None


def _parse_window(fragment: str) -> IntentStep | Ambiguity | None:
    lowered = fragment.lower()
    operation: str | None = None
    for word, canonical in _WINDOW_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            operation = canonical
            break
    if operation is None:
        return None
    if not re.search(r"\bwindow\b", lowered) and operation == "focus":
        # "focus notepad" without the word "window" is still a window
        # operation --- there is nothing else to focus --- but "focus" used as
        # a noun ("let's focus on the report") is not, and needs a target.
        pass
    match = re.search(
        rf"\b(?:{'|'.join(re.escape(w) for w in _WINDOW_WORDS)})\b\s+(?:the\s+)?([\w.\-]+)",
        lowered,
    )
    target = match.group(1) if match else None
    if target in {"window", "windows"} and match is not None:
        follow = re.search(rf"{re.escape(target)}\s+(?:for\s+|of\s+)?([\w.\-]+)", lowered)
        target = follow.group(1) if follow else None
    referent = _bare_referent(target or "")
    if referent is not None or target is None:
        return Ambiguity(
            code=AMBIGUITY_REFERENT,
            question=(
                f"Which window should I {operation}? Name the application and I will "
                "propose it for your approval."
            ),
            subject=target,
        )
    app_id = _app_id(target)
    if app_id is None:
        return Ambiguity(
            code=AMBIGUITY_REFERENT,
            question=(
                f"I could not tell which application's window to {operation} from "
                f"{target!r}. Name it as an allowlisted application."
            ),
            subject=target,
        )
    if operation == "focus":
        return IntentStep(
            capability=CapabilityKind.FOCUS_WINDOW.value,
            parameters={"app_id": app_id},
            described_as=f"bring {app_id}'s window to the foreground",
        )
    return IntentStep(
        capability=CapabilityKind.MANAGE_WINDOW.value,
        parameters={"app_id": app_id, "operation": operation},
        described_as=f"{operation} {app_id}'s window",
    )


def _parse_accessibility(fragment: str) -> IntentStep | Ambiguity | None:
    lowered = fragment.lower()
    operation: str | None = None
    for word, canonical in _ACCESSIBILITY_WORDS.items():
        if word in lowered:
            operation = canonical
            break
    if operation is None:
        return None
    app_id = _app_id(_last_word_after(lowered, r"\bin\b|\bof\b|\bon\b"))
    element = _quoted(fragment)
    if app_id is None:
        return Ambiguity(
            code=AMBIGUITY_REFERENT,
            question=(
                f"Which application should I {operation.replace('_', ' ')} in? Name it "
                "and quote the control, and I will propose it for your approval."
            ),
            subject=None,
        )
    if operation in ("expand", "collapse", "focus_element") and not element:
        return Ambiguity(
            code=AMBIGUITY_PARAMETER_MISSING,
            question=(
                f"Which control should I {operation}? Quote its name exactly --- I "
                "will not pick a control for you."
            ),
            subject=app_id,
        )
    parameters: dict[str, Any] = {"app_id": app_id, "operation": operation}
    if element:
        parameters["element_name"] = element
    return IntentStep(
        capability=CapabilityKind.ACCESSIBILITY_ACTION.value,
        parameters=parameters,
        described_as=f"{operation.replace('_', ' ')} in {app_id}",
    )


def _last_word_after(lowered: str, joiner: str) -> str | None:
    match = re.search(rf"(?:{joiner})\s+(?:the\s+)?([\w.\-]+)", lowered)
    return match.group(1) if match else None


def _parse_open(fragment: str) -> IntentStep | Ambiguity | None:
    if not re.search(rf"\b{_OPEN_VERBS}\b|\b{_LAUNCH_VERBS}\b", fragment, re.I):
        return None

    url = _URL.search(fragment)
    if url is not None:
        return IntentStep(
            capability=CapabilityKind.OPEN_URL.value,
            parameters={"url": url.group(0).rstrip(".,;)")},
            described_as="open the named URL in the default browser",
        )

    quoted = _quoted(fragment)
    path_source = quoted if (quoted and _PATH.match(quoted.strip())) else fragment
    path = _PATH.search(path_source)
    if path is not None:
        return IntentStep(
            capability=CapabilityKind.OPEN_PATH.value,
            parameters={"path": (quoted or path.group(0)).strip().rstrip(".,;")},
            described_as="open the named file or folder",
        )

    match = re.search(
        rf"\b(?:{_OPEN_VERBS}|{_LAUNCH_VERBS})\b\s+(?:up\s+)?(.+)$",
        fragment,
        re.I,
    )
    # The article is kept for the referent check and stripped only afterwards.
    # "Open the file" names nothing; stripping "the" first would leave "file",
    # which looks enough like an allowlist key to be launched.
    said = _clean(match.group(1)) if match else ""
    target = _clean(re.sub(r"^(?:the|my|a|an)\s+", "", said, flags=re.I))
    if _bare_referent(said) is not None or _bare_referent(target) is not None or not target:
        return Ambiguity(
            code=AMBIGUITY_REFERENT,
            question=(
                "Open what, exactly? Name the application, the file path or the URL. "
                "I will not guess which one you meant."
            ),
            subject=target or None,
        )
    # Two conservative rules, and both prefer a question to a reading:
    #
    #   * an article means the person is naming a *thing* ("open the report"),
    #     not an application by its key ("open notepad"). Which thing --- a
    #     file, a page, a program --- is exactly what is unresolved.
    #   * more than one word is not an allowlist key, and taking the first word
    #     of one would be picking a target out of a phrase.
    #
    # "Open my notepad" therefore asks, and that is the intended trade: a
    # small friction on an unusual phrasing, in exchange for never opening
    # something on the strength of a guess.
    words = target.split()
    if said != target or len(words) != 1:
        return Ambiguity(
            code=AMBIGUITY_REFERENT,
            question=(
                f"Is {said!r} an application, a file path, or a URL? Name it precisely "
                "--- I will not pick one reading and act on it."
            ),
            subject=said or None,
        )
    app_id = _app_id(words[0])
    if app_id is None:
        return Ambiguity(
            code=AMBIGUITY_REFERENT,
            question=(
                f"I could not tell whether {target!r} is an application, a file or a "
                "URL. Name it precisely and I will propose it for your approval."
            ),
            subject=target,
        )
    return IntentStep(
        capability=CapabilityKind.LAUNCH_APP.value,
        parameters={"app_id": app_id},
        described_as=f"start the allowlisted application {app_id}",
    )


#: Order matters, and it is the order of specificity. The typing and clipboard
#: recognisers run before the open/launch one because "type 'notepad'" is not a
#: request to open Notepad, and a less specific pattern that ran first would
#: turn one into the other.
_RECOGNISERS = (
    _parse_type_text,
    _parse_clipboard,
    _parse_accessibility,
    _parse_window,
    _parse_open,
)


def parse_task(instruction: str) -> TaskIntent:
    """Read one instruction. Never raises, never guesses, never reaches anything.

    Returns a `TaskIntent` whose `steps` are in the order the person said them.
    A fragment that matches nothing at all contributes a `no_capability`
    ambiguity rather than being dropped: silently ignoring half a sentence is
    how an executive ends up doing something other than what was asked.
    """
    text = _clean(instruction)
    if not text:
        return TaskIntent(
            instruction="",
            ambiguities=(
                Ambiguity(
                    code=AMBIGUITY_NO_CAPABILITY,
                    question="What would you like me to do?",
                ),
            ),
        )

    fragments = [f for f in (_clean(p) for p in _SEQUENCE.split(text)) if f]
    steps: list[IntentStep] = []
    ambiguities: list[Ambiguity] = []
    unsupported: list[Unsupported] = []
    notes: list[str] = []

    if len(fragments) > MAX_PLAN_STEPS:
        return TaskIntent(
            instruction=text,
            ambiguities=(
                Ambiguity(
                    code=AMBIGUITY_MULTIPLE_READINGS,
                    question=(
                        f"That is {len(fragments)} separate actions, more than the "
                        f"{MAX_PLAN_STEPS} I will plan at once. Tell me the first few "
                        "and I will propose those."
                    ),
                ),
            ),
        )

    for fragment in fragments:
        refused = _unsupported_for(fragment)
        if refused is not None:
            unsupported.append(refused)
            continue
        outcome: IntentStep | Ambiguity | None = None
        for recogniser in _RECOGNISERS:
            outcome = recogniser(fragment)
            if outcome is not None:
                break
        if outcome is None:
            ambiguities.append(
                Ambiguity(
                    code=AMBIGUITY_NO_CAPABILITY,
                    question=(
                        f"I do not have a capability that does {fragment!r}. Tell me "
                        "which application, file or URL you mean and I will say "
                        "whether I can propose it."
                    ),
                    subject=fragment,
                ),
            )
        elif isinstance(outcome, Ambiguity):
            ambiguities.append(outcome)
        else:
            steps.append(outcome)

    if steps and (ambiguities or unsupported):
        notes.append(
            "part of the instruction was understood, but the executive proposes an "
            "instruction whole or not at all",
        )

    return TaskIntent(
        instruction=text,
        steps=tuple(steps),
        ambiguities=tuple(ambiguities),
        unsupported=tuple(unsupported),
        notes=tuple(notes),
    )


def clarification_subject(intent: TaskIntent) -> str:
    """The one-line subject an `awaiting_response` obligation is opened with.

    Every ambiguity and every refusal is named, so the question the person sees
    is the whole question rather than the first half of it.
    """
    parts: list[str] = []
    for refusal in intent.unsupported:
        parts.append(f"I cannot {refusal.described_as}, so I have not proposed it.")
    for ambiguity in intent.ambiguities:
        parts.append(ambiguity.question)
    if not parts:
        parts.append("I need one more detail before I can propose anything.")
    return " ".join(parts)[:500]


__all__ = [
    "ACCESSIBILITY_OPERATIONS",
    "AMBIGUITY_MULTIPLE_READINGS",
    "AMBIGUITY_NO_CAPABILITY",
    "AMBIGUITY_PARAMETER_MISSING",
    "AMBIGUITY_REFERENT",
    "MAX_PLAN_STEPS",
    "UNSUPPORTED_OUT_OF_VOCABULARY",
    "WINDOW_OPERATIONS",
    "Ambiguity",
    "IntentStep",
    "TaskIntent",
    "Unsupported",
    "clarification_subject",
    "parse_task",
]
