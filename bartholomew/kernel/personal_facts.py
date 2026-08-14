"""
Personal memory capture and recall (Usable POC, slice 1)
=========================================================

Implements the pure half of `docs/POC_SLICE_1_MEMORY_CAPTURE_RECALL.md`
(approved as planning 2026-08-14, implementation approved separately): turn
an ordinary chat utterance into a *candidate* durable personal fact, and
adapt a stored personal-fact row into something S5.3's existing relevance
gate can score.

Deliberately pure data and logic: no persistence, no retrieval, no I/O of any
kind -- the same discipline `competency.py`, `training.py` and
`competency_reasoning.py` hold to. The governed write itself
(`MemoryStore.upsert_memory()`) and the retrieval read both stay with the
seam in `runtime_contract.py`, so this module cannot become a second Memory
authority.

What this module does NOT do
-----------------------------
- It does not decide whether a fact may be stored. Every extracted fact is a
  *proposal*; `memory_rules.yaml` (never_store / ask_before_store) and
  `privacy_guard` decide, through the one existing governed write path.
- It introduces no new memory kind. Facts land on `user_profile` /
  `user_schedule`, the categories `memory_rules.yaml` has always governed.
- It does not judge relevance. `competency_reasoning.select_relevant()` does
  that, unmodified, via the adapter below.

**Scaffolding status (approval clarification, 2026-08-14).** The pattern set
in `extract_facts()` is explicitly POC scaffolding, NOT the intended
long-term boundary of Bartholomew's memory-capture capability. It exists to
get slice 1 to a testable end-to-end loop. Broadening what counts as a
capturable fact is real, expected future work, informed by what real use
surfaces -- not scoped here. Nothing downstream may assume this pattern set
is the definition of "a personal fact".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .competency import Provenance, Supervision

#: The memory kinds slice 1 captures onto. Both already carry `always_keep`
#: entries in `memory_rules.yaml` (`privacy_class`, `recall_policy: always`,
#: and `encrypt: standard` for `user_profile`) -- no new kind, no new rule.
PERSONAL_FACT_KINDS: tuple[str, ...] = ("user_profile", "user_schedule")

#: Synthetic domain id for personal facts.
#:
#: `select_relevant()` commits to a single domain per selection (S5.3
#: Decision C, no cross-competency transfer). Giving every personal fact the
#: same id means facts never compete with each other for that single slot --
#: and because personal facts are selected in their own separate pass (see
#: `runtime_contract._retrieve_memory_context`), they never compete with real
#: competencies either. This is a grouping label, not a competency: no
#: `competency` record is created, and nothing here is promoted into the
#: competency model.
PERSONAL_MEMORY_ID = "personal_memory"

#: Provenance source for a fact stated in ordinary conversation. Reuses
#: `competency.PROVENANCE_SOURCE_TYPES`' existing vocabulary rather than
#: inventing a parallel one.
_FACT_SOURCE_TYPE = "user_instruction"

#: Bounds. A chat turn proposes at most a handful of short facts; anything
#: beyond that is far more likely to be a parse accident than a real fact.
_MAX_FACTS_PER_MESSAGE = 3
_MAX_VALUE_CHARS = 120
_MAX_SLUG_TOKENS = 4

_SLUG_STRIP_RE = re.compile(r"['’]s\b", re.IGNORECASE)
_SLUG_NONWORD_RE = re.compile(r"[^a-z0-9]+")
_TRAILING_PUNCT = " \t\r\n.!?,;:\"'"

#: Where a captured value stops. Without this, "my doctor is Dr Smith and I
#: have a review on Friday" stores the whole tail as the doctor's name.
#: Deterministic and deliberately crude -- clause splitting is a language
#: problem this POC does not attempt to solve (see the scaffolding note).
_CLAUSE_BOUNDARY_RE = re.compile(
    r"\s+(?:and|but|so|because|although|while|however)\s+|[;]",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Retrieval adapter: a stored personal-fact row, shaped for the S5.3 gate.
#
# `competency_reasoning.select_relevant()` types its `record` as `Any` and
# reads exactly four things: `record.envelope.{competency_id, classification,
# confidence, supervision, provenance}` and `record.to_summary_text()`. This
# supplies those and nothing else, so the gate is reused byte-for-byte
# instead of being generalised, copied, or parameterised.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PersonalFactEnvelope:
    """The envelope fields the S5.3 selection gate reads.

    `confidence=None` deliberately means *unknown*, not *low*: `select_relevant()`
    keeps such records (ranking them below evidenced ones) rather than
    excluding them at the confidence floor. A fact the user stated plainly has
    no evidence score to report, and inventing one would be a fabricated
    number in explanation-grade output.

    `supervision` is a plain default (`requires_review=False`). Supervision
    may only ever become stricter in `select_relevant()`; nothing here can
    clear a review requirement another record asked for.
    """

    competency_id: str = PERSONAL_MEMORY_ID
    classification: str = "personal"
    confidence: float | None = None
    provenance: Provenance = field(
        default_factory=lambda: Provenance(
            source_type=_FACT_SOURCE_TYPE,
            detail="stated in conversation",
        ),
    )
    supervision: Supervision = field(default_factory=Supervision)


@dataclass(frozen=True)
class PersonalFactRecord:
    """One stored personal fact, adapted for selection.

    `text` is the stored fact sentence (e.g. "Birthday: 3rd March") -- the
    same text FTS indexed and the same text the relevance gate measures
    overlap against, so a term can never count for retrieval and not for
    relevance.
    """

    kind: str
    key: str
    text: str
    envelope: PersonalFactEnvelope = field(default_factory=PersonalFactEnvelope)

    def to_summary_text(self) -> str:
        return self.text


def record_from_row(row: dict[str, Any]) -> PersonalFactRecord | None:
    """Rebuild a `PersonalFactRecord` from a stored `memories` row, or None.

    Personal facts are stored as plain text, deliberately: they are short,
    human-readable statements, and storing them as JSON would (a) make them
    indistinguishable from competency records at read time and (b) expose
    schema key names to `privacy_guard`'s content scan for no benefit.

    Returns None for anything that does not look like a personal fact row, so
    a competency record can never be mistaken for one.
    """
    kind = row.get("kind")
    if kind not in PERSONAL_FACT_KINDS:
        return None

    text = row.get("summary") or row.get("value") or ""
    if not isinstance(text, str) or not text.strip():
        return None

    return PersonalFactRecord(kind=kind, key=row.get("key") or "", text=text.strip())


# ---------------------------------------------------------------------------
# Extraction (POC scaffolding -- see the module docstring)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExtractedFact:
    """A candidate durable personal fact, not yet proposed to Memory.

    `value` is the full text that will be written to `memories.value`. It is
    a rendered sentence rather than the bare captured fragment so that both
    the subject and the fact survive into the FTS index and the relevance
    gate -- "3rd March" alone would never be retrievable from a later "when
    is my birthday?".
    """

    kind: str
    key: str
    value: str
    fact_type: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "key": self.key,
            "fact_type": self.fact_type,
        }


def _slugify(text: str) -> str:
    """A stable, deterministic key fragment.

    Stability matters more than prettiness: restating the same fact must
    produce the same key so `upsert_memory()` updates the existing row rather
    than accumulating near-duplicates.
    """
    cleaned = _SLUG_STRIP_RE.sub("", text.lower())
    tokens = [token for token in _SLUG_NONWORD_RE.split(cleaned) if token]
    return "_".join(tokens[:_MAX_SLUG_TOKENS])


def _clean_value(text: str) -> str:
    value = " ".join((text or "").split())
    value = _CLAUSE_BOUNDARY_RE.split(value, maxsplit=1)[0]
    value = value.strip(_TRAILING_PUNCT)
    if len(value) > _MAX_VALUE_CHARS:
        value = value[:_MAX_VALUE_CHARS].rstrip()
    return value


def _label(text: str) -> str:
    text = " ".join((text or "").split())
    return text[:1].upper() + text[1:] if text else ""


#: Subjects that match the generic "my X is Y" shape but are conversational
#: framing rather than durable facts. Deliberately tiny and deliberately
#: incomplete -- this is a noise filter for the POC, not a semantic model.
#: `birthday` is here because it has its own dedicated pattern.
_BLOCKED_SUBJECT_SLUGS: frozenset[str] = frozenset(
    {
        "birthday",
        "point",
        "question",
        "problem",
        "issue",
        "concern",
        "guess",
        "understanding",
        "impression",
        "answer",
        "view",
        "opinion",
        "advice",
        "worry",
        "thinking",
        "hope",
        "fear",
        "sense",
        "feeling",
        "bad",
        "fault",
    },
)

_BIRTHDAY_RE = re.compile(
    r"\bmy\s+birthday\s+(?:is|was|falls\s+on)\s+(?P<value>.+)",
    re.IGNORECASE,
)

_SCHEDULE_RE = re.compile(
    r"\bi\s+have\s+(?:an?\s+|my\s+)?(?P<event>[a-z][a-z'’\s-]{1,40}?)\s+"
    r"(?P<when>(?:on|at)\s+.+)",
    re.IGNORECASE,
)

_PREFERENCE_RE = re.compile(
    r"\bi\s+(?:always\s+)?prefer\s+(?P<value>.+)",
    re.IGNORECASE,
)

_PROFILE_RE = re.compile(
    r"\bmy\s+(?P<subject>[a-z][a-z'’\s-]{1,40}?)\s+(?:is|are)\s+(?P<value>.+)",
    re.IGNORECASE,
)


def extract_facts(text: str) -> list[ExtractedFact]:
    """
    Find candidate durable personal facts in one chat utterance.

    Deterministic: keyword/regex patterns only, no model call, no new
    dependency, same input always yields the same output. Content matching no
    pattern is left entirely alone -- this is not a general-purpose fact
    extractor and is not trying to be (see the module docstring's scaffolding
    note).

    Every returned fact is a *proposal*. Nothing here decides whether it may
    be stored; that stays with the governed write path.
    """
    if not text or not text.strip():
        return []

    facts: list[ExtractedFact] = []
    seen: set[tuple[str, str]] = set()

    def _add(kind: str, key: str, label: str, value: str, fact_type: str) -> None:
        value = _clean_value(value)
        label = _label(label)
        if not value or not label or not key:
            return
        if not any(char.isalnum() for char in value):
            return
        identity = (kind, key)
        if identity in seen:
            return
        seen.add(identity)
        facts.append(
            ExtractedFact(
                kind=kind,
                key=key,
                value=f"{label}: {value}",
                fact_type=fact_type,
            ),
        )

    match = _BIRTHDAY_RE.search(text)
    if match:
        _add("user_profile", "birthday", "Birthday", match.group("value"), "birthday")

    match = _SCHEDULE_RE.search(text)
    if match:
        event = _clean_value(match.group("event"))
        slug = _slugify(event)
        if slug and slug not in _BLOCKED_SUBJECT_SLUGS:
            _add("user_schedule", slug, event, match.group("when"), "schedule")

    match = _PREFERENCE_RE.search(text)
    if match:
        value = _clean_value(match.group("value"))
        slug = _slugify(value)
        if slug:
            _add("user_profile", f"preference.{slug}", "Preference", value, "preference")

    match = _PROFILE_RE.search(text)
    if match:
        subject = _clean_value(match.group("subject"))
        slug = _slugify(subject)
        if slug and slug not in _BLOCKED_SUBJECT_SLUGS:
            _add("user_profile", slug, subject, match.group("value"), "profile_detail")

    return facts[:_MAX_FACTS_PER_MESSAGE]


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------


def render_facts_for_prompt(context: Any) -> str:
    """
    Render selected personal facts as their own labelled prompt block,
    separate from S5.3's competency block, so the two stay visibly distinct
    in the Interpretation the Executive sees.

    Returns "" when nothing applies, so a turn with no relevant fact behaves
    exactly as it does today -- the same posture as
    `competency_reasoning.render_for_prompt()`.

    Like that function, this is for the model's context only. It is never
    surfaced to the user as "I recalled these facts" (S5.3 Decision E.1,
    which slice 1 does not relax).
    """
    if context is None or context.is_empty():
        return ""

    lines = ["Known personal facts:"]
    for item in context.applied:
        lines.append(f"- {item.summary or item.key}")
    return "\n".join(lines)
