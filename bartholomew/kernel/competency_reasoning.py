"""
Competency reasoning core (Stage 5, S5.3)
==========================================

Implements the selection half of
`docs/S5_3_EXECUTIVE_COMPETENCY_REASONING_DESIGN.md` (design and Decisions
A-E approved 2026-08-11): given competency records the governed retrieval
layer has already returned, decide which are relevant enough to inform a
`CandidateAction`, aggregate their supervision requirement, and render them
for the Interpretation prompt.

Deliberately pure data and logic: no persistence, no retrieval, no I/O of any
kind -- the same discipline `competency.py` and `training.py` hold to, and
asserted structurally by `tests/test_competency_reasoning_selection.py`.
Retrieval (the I/O) stays with the existing `ConsentGate`-filtered retrieval
layer and is performed by the seam in `runtime_contract.py`.

Scope (design Sec.6.1) -- this is NOT the final Executive architecture
------------------------------------------------------------------------
This module provides competency **retrieval support, selection, and
supervision propagation** only. It does not plan, deliberate between
conflicting records, project consequences, or learn from outcomes. Those are
later, separately-approved capabilities.

The seam must therefore remain *replaceable*: `select_relevant()` is a named
function returning **structured data**, never a pre-rendered string baked
into a prompt, so a future deliberative Executive can consume the same
candidates and reach a different answer without tearing this out.

Boundaries this module enforces mechanically
---------------------------------------------
- **Supervision may only become stricter** (`requires_review` is an OR across
  applied records). Nothing here can clear or relax a review requirement.
- **No cross-competency transfer** (Decision C): when candidates span several
  competencies, selection commits to a single one rather than mixing records
  from different domains into one context.
- **Explanation-grade output** (Decision E.2): every applied record keeps its
  identity, provenance, classification and confidence, so a future
  user-requested explanation capability is not foreclosed by omission. This
  is a record of *which stored, governed records were applied* -- never a
  chain-of-thought.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Tunable S5.3 defaults (approved 2026-08-11).
#
# Explicitly tunable defaults, NOT permanent architectural or product
# constants. Both are expected to be revisited once there is real usage.
# ---------------------------------------------------------------------------

#: Records whose confidence is explicitly below this are excluded.
#: `confidence is None` means *unknown*, not *low*: such records are kept and
#: ranked below evidenced ones (see `_sort_key`).
DEFAULT_CONFIDENCE_FLOOR: float = 0.3

#: Hard cap on records folded into one request's context. Grounded in
#: `RISKS.md`'s standing prompt-bloat / provider-rate-limit entry: competency
#: retrieval is exactly the kind of feature that grows a prompt silently.
DEFAULT_MAX_RECORDS: int = 5


#: Very common words that carry no retrieval signal. Deliberately tiny --
#: this is a noise filter, not a linguistic model.
_QUERY_STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "you",
        "your",
        "are",
        "was",
        "were",
        "have",
        "has",
        "had",
        "can",
        "should",
        "would",
        "could",
        "what",
        "when",
        "where",
        "how",
        "why",
        "who",
        "did",
        "does",
        "about",
        "from",
        "there",
        "their",
        "them",
        "then",
        "than",
        "into",
        "out",
        "any",
        "some",
        "please",
        "just",
        "now",
        "get",
        "got",
        "want",
        "need",
        "know",
        "think",
    },
)

#: Bound on query size, so an unusually long message cannot produce an
#: unbounded MATCH expression.
_QUERY_MAX_TOKENS: int = 12

_QUERY_TOKEN_RE = re.compile(r"[a-z0-9]+")


def build_retrieval_query(text: str) -> str:
    """
    Turn a natural-language message into an FTS-usable query.

    **This is load-bearing, not cosmetic.** SQLite FTS5 applies AND semantics
    across the terms of a MATCH expression, so passing a raw utterance
    through would require the stored record to contain *every* word the user
    typed. Measured during S5.3 implementation: with a stored heuristic about
    replacing a boiler, `"boiler"` matched it and `"should I replace the
    boiler?"` matched nothing. Passing raw text would have shipped competency
    retrieval as a silently dead feature.

    So: extract word tokens, drop very short ones and a small stopword set,
    cap the count, and join with `OR`. Deterministic, no model call (design
    Sec.6).

    Tokenising also sanitises: a raw message containing FTS5 syntax (quotes,
    `NEAR`, `*`, `^`) can otherwise error or be silently reinterpreted as
    operators. Only `[a-z0-9]+` runs survive.

    Returns "" when nothing useful remains, which the caller treats as "no
    retrieval" -- the request then behaves exactly as it does today.
    """
    if not text:
        return ""

    tokens: list[str] = []
    for token in _QUERY_TOKEN_RE.findall(text.lower()):
        if len(token) < 3 or token in _QUERY_STOPWORDS:
            continue
        if token not in tokens:
            tokens.append(token)
        if len(tokens) >= _QUERY_MAX_TOKENS:
            break

    return " OR ".join(tokens)


@dataclass(frozen=True)
class CompetencyCandidate:
    """
    One competency record the retrieval layer returned, paired with its
    relevance score.

    `record` is an S5.1 record object (`CompetencyRecord`,
    `CompetencyKnowledge`, `CompetencyProcedure`, `CompetencyHeuristic`, or
    `CompetencyEvidence`). Typed as `Any` so this module imports no
    competency machinery it does not need -- it only ever reads
    `record.envelope` and calls `record.to_summary_text()`.
    """

    kind: str
    key: str
    score: float
    record: Any


@dataclass(frozen=True)
class AppliedRecord:
    """
    One record that actually informed a CandidateAction.

    Explanation-grade by construction (design Decision E.2): identity,
    provenance, classification and confidence all survive, so a future
    "why did you recommend that?" capability has something real to render.
    A decision cannot be reconstructed after the fact, so recording less
    here would foreclose that capability by omission.
    """

    kind: str
    key: str
    competency_id: str
    classification: str
    confidence: float | None
    provenance: dict[str, Any]
    requires_review: bool
    review_reason: str | None
    summary: str
    score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "key": self.key,
            "competency_id": self.competency_id,
            "classification": self.classification,
            "confidence": self.confidence,
            "provenance": dict(self.provenance),
            "requires_review": self.requires_review,
            "review_reason": self.review_reason,
            "score": self.score,
        }


@dataclass(frozen=True)
class CompetencyContext:
    """
    The structured result of competency selection for one request.

    Carried on `CandidateAction` and recorded in the per-action Reflection.
    Never exposed automatically in a user-facing response (Decision E.1).
    """

    applied: tuple[AppliedRecord, ...] = ()
    competency_id: str | None = None
    requires_review: bool = False
    review_reasons: tuple[str, ...] = ()
    considered: int = 0
    excluded_low_confidence: int = 0
    excluded_other_competencies: int = 0
    truncated: int = 0

    def is_empty(self) -> bool:
        return not self.applied

    def to_dict(self) -> dict[str, Any]:
        """Explanation-grade record, for the Reflection sink."""
        return {
            "competency_id": self.competency_id,
            "requires_review": self.requires_review,
            "review_reasons": list(self.review_reasons),
            "applied": [item.to_dict() for item in self.applied],
            "selection": {
                "considered": self.considered,
                "applied": len(self.applied),
                "excluded_low_confidence": self.excluded_low_confidence,
                "excluded_other_competencies": self.excluded_other_competencies,
                "truncated": self.truncated,
            },
        }


EMPTY_CONTEXT = CompetencyContext()


def _sort_key(candidate: CompetencyCandidate) -> tuple[float, int, float]:
    """
    Rank by relevance first; among comparable candidates, prefer evidenced
    records over unknown-confidence ones.

    `confidence is None` means "not yet evidenced", which is NOT the same as
    "low confidence" -- such a record is still usable, just ranked after one
    that carries evidence.
    """
    confidence = _confidence_of(candidate)
    has_confidence = 0 if confidence is not None else 1
    return (-candidate.score, has_confidence, -(confidence or 0.0))


def _confidence_of(candidate: CompetencyCandidate) -> float | None:
    envelope = getattr(candidate.record, "envelope", None)
    return getattr(envelope, "confidence", None)


def _competency_id_of(candidate: CompetencyCandidate) -> str:
    envelope = getattr(candidate.record, "envelope", None)
    return getattr(envelope, "competency_id", "") or ""


def _dominant_competency(candidates: list[CompetencyCandidate]) -> str | None:
    """
    Pick the single competency this request is about (Decision C).

    Cross-competency transfer is an explicit S5.3 non-goal:
    `COGNITIVE_RUNTIME.md`'s "Transfer boundaries" requires domain boundaries
    to hold ("a plumbing-contractor heuristic does not transfer to travel
    booking just because both involve comparing vendor quotes"), and the
    machinery for judging domain-appropriateness does not exist yet. Rather
    than implement a weak version of transfer, selection commits to the
    best-scoring competency and drops the rest.

    "Best-scoring" is by the highest single relevance score, with total score
    as the tie-break -- so one strongly-matching record wins over several
    weak ones, which is the behaviour a specific question deserves.
    """
    if not candidates:
        return None

    best: dict[str, tuple[float, float]] = {}
    for candidate in candidates:
        competency_id = _competency_id_of(candidate)
        if not competency_id:
            continue
        top, total = best.get(competency_id, (float("-inf"), 0.0))
        best[competency_id] = (max(top, candidate.score), total + candidate.score)

    if not best:
        return None
    return max(best.items(), key=lambda item: (item[1][0], item[1][1]))[0]


def select_relevant(
    candidates: list[CompetencyCandidate],
    *,
    confidence_floor: float | None = DEFAULT_CONFIDENCE_FLOOR,
    max_records: int = DEFAULT_MAX_RECORDS,
) -> CompetencyContext:
    """
    Choose which retrieved competency records inform this request.

    Order of operations, each deliberate:
      1. drop records whose confidence is explicitly below the floor
         (`None` is unknown, not low -- kept);
      2. commit to one competency (Decision C: no cross-competency transfer);
      3. rank by relevance, preferring evidenced records;
      4. cap at `max_records` (prompt-bloat guard).

    Supervision is aggregated across whatever survives, and can only ever
    become stricter.
    """
    considered = len(candidates)
    if not candidates:
        return EMPTY_CONTEXT

    surviving: list[CompetencyCandidate] = []
    excluded_low_confidence = 0
    for candidate in candidates:
        confidence = _confidence_of(candidate)
        if (
            confidence_floor is not None
            and confidence is not None
            and confidence < confidence_floor
        ):
            excluded_low_confidence += 1
            continue
        surviving.append(candidate)

    if not surviving:
        return CompetencyContext(
            considered=considered,
            excluded_low_confidence=excluded_low_confidence,
        )

    competency_id = _dominant_competency(surviving)
    in_domain = [c for c in surviving if _competency_id_of(c) == competency_id]
    excluded_other_competencies = len(surviving) - len(in_domain)

    ranked = sorted(in_domain, key=_sort_key)
    selected = ranked[: max(0, max_records)]
    truncated = len(ranked) - len(selected)

    applied = tuple(_to_applied(candidate) for candidate in selected)
    review_reasons = tuple(
        item.review_reason for item in applied if item.requires_review and item.review_reason
    )

    return CompetencyContext(
        applied=applied,
        competency_id=competency_id,
        # Supervision may only become stricter: an OR across applied records.
        # There is deliberately no branch by which a record clears this.
        requires_review=any(item.requires_review for item in applied),
        review_reasons=review_reasons,
        considered=considered,
        excluded_low_confidence=excluded_low_confidence,
        excluded_other_competencies=excluded_other_competencies,
        truncated=truncated,
    )


def _to_applied(candidate: CompetencyCandidate) -> AppliedRecord:
    envelope = getattr(candidate.record, "envelope", None)
    supervision = getattr(envelope, "supervision", None)
    provenance = getattr(envelope, "provenance", None)

    try:
        summary = candidate.record.to_summary_text()
    except Exception:
        summary = ""

    return AppliedRecord(
        kind=candidate.kind,
        key=candidate.key,
        competency_id=_competency_id_of(candidate),
        classification=getattr(envelope, "classification", "personal"),
        confidence=_confidence_of(candidate),
        provenance=provenance.to_dict() if provenance is not None else {},
        requires_review=bool(getattr(supervision, "requires_review", False)),
        review_reason=getattr(supervision, "reason", None),
        summary=summary,
        score=candidate.score,
    )


#: Human-readable labels for the prompt rendering, so a competency line reads
#: as guidance rather than as raw storage detail.
_KIND_LABELS: dict[str, str] = {
    "competency": "competency",
    "competency_knowledge": "knowledge",
    "competency_procedure": "procedure",
    "competency_heuristic": "rule of thumb",
    "competency_evidence": "prior case",
}


def render_for_prompt(context: CompetencyContext) -> str:
    """
    Render the applied records as labelled lines for the Interpretation
    prompt, matching `_build_interpretation()`'s existing style.

    Returns "" when nothing applies, so the caller adds no context block at
    all -- a request with no relevant competency behaves exactly as it does
    today (Decision D: low/no-confidence retrieval changes nothing
    user-visible).

    This rendering is for the model's context only. It is never surfaced to
    the user as "I consulted these competencies" (Decision E.1); the
    user-facing explanation capability E.2 preserves would be built from
    `CompetencyContext.to_dict()`, not from this string.
    """
    if context.is_empty():
        return ""

    lines = [f"Relevant competency ({context.competency_id}):"]
    for item in context.applied:
        label = _KIND_LABELS.get(item.kind, item.kind)
        summary = item.summary or item.key
        lines.append(f"- [{label}] {summary}")

    if context.requires_review:
        reasons = "; ".join(context.review_reasons) if context.review_reasons else "unspecified"
        lines.append(
            f"Note: this competency is recorded as requiring review before acting ({reasons}).",
        )

    return "\n".join(lines)
