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

#: Minimum number of meaningful terms a record's own text must share with the
#: request before that record may be treated as applicable.
#:
#: **This is the relevance criterion, and it is deliberately not a score
#: threshold.** Chosen by measuring the real retrieval behaviour across
#: clearly-relevant, paraphrased, partially-related, unrelated, multi-
#: competency and sparse-corpus cases (see this constant's rationale in
#: `docs/S5_3_EXECUTIVE_COMPETENCY_REASONING_DESIGN.md` Sec.12):
#:
#: - Retriever scores are **not comparable across modes**. The same corpus and
#:   queries produced top scores of 1.000 (FTS, normalised), 0.009-0.128
#:   (vector) and 0.25-0.94 (hybrid). No single numeric floor can hold across
#:   the three supported modes.
#: - Vector scores were measured **anti-correlated** with relevance under the
#:   deterministic fallback embedder used when `sentence-transformers` is
#:   absent: "play some music" scored 0.128 against an estate-management
#:   corpus while "how should I handle the boiler quotes?" scored 0.009. A
#:   threshold derived from those numbers would encode noise.
#: - Lexical overlap separated every measured category cleanly and does so
#:   **identically in every mode**, because it is computed from the request
#:   and the record's own text rather than from the retriever's ranking.
#:
#: Set to 0 to disable the filter. Tunable, not a permanent constant.
DEFAULT_MIN_SHARED_TERMS: int = 1


def _tokenise(text: str) -> list[str]:
    """Meaningful lowercase word tokens, in order, deduplicated by the caller.

    One tokeniser for both query construction and relevance measurement, so a
    term can never count for one and not the other.
    """
    return [
        token
        for token in _QUERY_TOKEN_RE.findall((text or "").lower())
        if len(token) >= 3 and token not in _QUERY_STOPWORDS
    ]


def query_terms(text: str) -> frozenset[str]:
    """The set of meaningful terms in a request, for relevance measurement."""
    return frozenset(_tokenise(text))


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
    for token in _tokenise(text):
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
    #: The request terms this record actually shares -- the evidence that it
    #: was applicable at all. Recorded because a future explanation should be
    #: able to say *why* a record was considered relevant, not merely that it
    #: was used (Decision E.2).
    matched_terms: tuple[str, ...] = ()

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
            "matched_terms": list(self.matched_terms),
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
    excluded_not_relevant: int = 0
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
                "excluded_not_relevant": self.excluded_not_relevant,
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


def _dominant_competency(
    candidates: list[CompetencyCandidate],
    matched: dict[int, tuple[str, ...]] | None = None,
) -> str | None:
    """
    Pick the single competency this request is about (Decision C).

    Cross-competency transfer is an explicit S5.3 non-goal:
    `COGNITIVE_RUNTIME.md`'s "Transfer boundaries" requires domain boundaries
    to hold ("a plumbing-contractor heuristic does not transfer to travel
    booking just because both involve comparing vendor quotes"), and the
    machinery for judging domain-appropriateness does not exist yet. Rather
    than implement a weak version of transfer, selection commits to one
    competency and drops the rest.

    **Which one is decided by lexical overlap, not by retriever score.** The
    characterisation behind the relevance gate showed retriever scores are
    not comparable across modes and are anti-correlated with relevance under
    the fallback embedder, so using them here would let an irrelevant
    competency win a domain contest on the strength of a meaningless number
    -- e.g. a three-record estate corpus outranking a travel record that
    shares four terms with "compare airline fares for the flight". Ranking
    is therefore: strongest single overlap, then total overlap, then
    retriever score only as a final tie-break.
    """
    if not candidates:
        return None

    matched = matched or {}

    best: dict[str, tuple[int, int, float]] = {}
    for candidate in candidates:
        competency_id = _competency_id_of(candidate)
        if not competency_id:
            continue
        overlap = len(matched.get(id(candidate), ()))
        top_overlap, total_overlap, top_score = best.get(
            competency_id,
            (0, 0, float("-inf")),
        )
        best[competency_id] = (
            max(top_overlap, overlap),
            total_overlap + overlap,
            max(top_score, candidate.score),
        )

    if not best:
        return None
    return max(best.items(), key=lambda item: item[1])[0]


def select_relevant(
    candidates: list[CompetencyCandidate],
    *,
    request_terms: frozenset[str] | None = None,
    min_shared_terms: int = DEFAULT_MIN_SHARED_TERMS,
    confidence_floor: float | None = DEFAULT_CONFIDENCE_FLOOR,
    max_records: int = DEFAULT_MAX_RECORDS,
) -> CompetencyContext:
    """
    Choose which retrieved competency records inform this request.

    Order of operations, each deliberate:
      1. **drop records with no lexical connection to the request** -- the
         relevance gate;
      2. drop records whose confidence is explicitly below the floor
         (`None` is unknown, not low -- kept);
      3. commit to one competency (Decision C: no cross-competency transfer);
      4. rank by relevance, preferring evidenced records;
      5. cap at `max_records` (prompt-bloat guard).

    Step 1 comes first on purpose, and enforces the governing invariant:
    **being the best available retrieval result is not sufficient to make a
    competency applicable.** A retriever always returns a nearest neighbour,
    so without this gate an unrelated request applies whatever competency
    happens to exist -- and that irrelevant record would then be cited in the
    explanation-grade attribution (Decision E.2) as the basis of a decision it
    had nothing to do with. Ranking cannot fix this: it only orders what it is
    given, and something is always ranked first.

    `request_terms` comes from `query_terms()` and is supplied by the seam.
    When it is None the gate cannot be evaluated and is skipped, so direct
    callers that have already established relevance are unaffected.

    Supervision is aggregated across whatever survives, and can only ever
    become stricter.
    """
    considered = len(candidates)
    if not candidates:
        return EMPTY_CONTEXT

    # --- 1. Relevance gate -------------------------------------------------
    matched: dict[int, tuple[str, ...]] = {}
    relevant: list[CompetencyCandidate] = []
    excluded_not_relevant = 0
    for candidate in candidates:
        shared = _shared_terms(candidate, request_terms)
        if request_terms is not None and min_shared_terms > 0 and len(shared) < min_shared_terms:
            excluded_not_relevant += 1
            continue
        matched[id(candidate)] = shared
        relevant.append(candidate)

    if not relevant:
        return CompetencyContext(
            considered=considered,
            excluded_not_relevant=excluded_not_relevant,
        )

    # --- 2. Confidence floor ----------------------------------------------
    surviving: list[CompetencyCandidate] = []
    excluded_low_confidence = 0
    for candidate in relevant:
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
            excluded_not_relevant=excluded_not_relevant,
            excluded_low_confidence=excluded_low_confidence,
        )

    competency_id = _dominant_competency(surviving, matched)
    in_domain = [c for c in surviving if _competency_id_of(c) == competency_id]
    excluded_other_competencies = len(surviving) - len(in_domain)

    ranked = sorted(in_domain, key=_sort_key)
    selected = ranked[: max(0, max_records)]
    truncated = len(ranked) - len(selected)

    applied = tuple(
        _to_applied(candidate, matched.get(id(candidate), ())) for candidate in selected
    )
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
        excluded_not_relevant=excluded_not_relevant,
        excluded_low_confidence=excluded_low_confidence,
        excluded_other_competencies=excluded_other_competencies,
        truncated=truncated,
    )


def _shared_terms(
    candidate: CompetencyCandidate,
    request_terms: frozenset[str] | None,
) -> tuple[str, ...]:
    """
    Terms the request and this record's own text have in common.

    Measured against `to_summary_text()` -- the record's human-meaningful
    rendering, and the same text the retrieval layer indexes -- rather than
    the raw serialised record, whose schema keys ("name", "steps") would
    produce spurious overlaps.
    """
    if not request_terms:
        return ()
    try:
        text = candidate.record.to_summary_text()
    except Exception:
        return ()
    return tuple(sorted(request_terms.intersection(_tokenise(text))))


def _to_applied(
    candidate: CompetencyCandidate,
    matched_terms: tuple[str, ...] = (),
) -> AppliedRecord:
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
        matched_terms=matched_terms,
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
