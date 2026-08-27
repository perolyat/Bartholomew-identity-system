"""Bounded retrieval-quality measurement.

Produces evidence about how retrieval actually behaves -- per mode, per query
category -- so that a decision about relevance thresholds can be made from
measurements rather than from intuition. OP-W003's second branch asks for
exactly this: a fallback embedder is acceptable only if approved "with measured
quality", and no measurement existed.

**This harness measures; it does not gate.** It deliberately exposes no pass
mark and no target score. A number produced here describes the retrieval stack
as it is currently configured -- which embedder is loaded, which mode is
resolved -- and is meaningless without that context, so every report carries the
embedding status that produced it.

The corpus and cases live in `tests/fixtures/retrieval_eval_corpus.py`, next to
the tests, because they are fixture data rather than product configuration.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

#: Modes worth reporting side by side. "vector" is included even though it is
#: rarely the production choice, because comparing it against "fts" is what
#: shows whether the embedder is contributing anything at all.
EVAL_MODES = ("fts", "vector", "hybrid")


@dataclass
class CaseResult:
    """One query's outcome under one mode."""

    category: str
    query: str
    expected: tuple[int, ...]
    returned: list[int]
    top1_hit: bool
    top3_hit: bool
    #: For irrelevant cases: whether anything was returned at all.
    returned_anything: bool


@dataclass
class ModeReport:
    """Aggregated behaviour of one retrieval mode over the whole fixture."""

    mode: str
    #: None when the mode could not run at all (e.g. vector-only with no
    #: embedder). Recorded as "could not run", never as a score of zero.
    error: str | None = None
    cases: list[CaseResult] = field(default_factory=list)

    @property
    def answerable(self) -> list[CaseResult]:
        """Cases that have a correct answer in the corpus."""
        return [c for c in self.cases if c.expected]

    @property
    def irrelevant(self) -> list[CaseResult]:
        """Cases where returning nothing is the correct behaviour."""
        return [c for c in self.cases if not c.expected]

    @property
    def top1(self) -> float | None:
        answerable = self.answerable
        if not answerable:
            return None
        return sum(c.top1_hit for c in answerable) / len(answerable)

    @property
    def top3(self) -> float | None:
        answerable = self.answerable
        if not answerable:
            return None
        return sum(c.top3_hit for c in answerable) / len(answerable)

    def by_category(self) -> dict[str, tuple[int, int, int]]:
        """`category -> (cases, top1 hits, top3 hits)`."""
        out: dict[str, tuple[int, int, int]] = {}
        for case in self.answerable:
            cases, t1, t3 = out.get(case.category, (0, 0, 0))
            out[case.category] = (cases + 1, t1 + case.top1_hit, t3 + case.top3_hit)
        return out


def seed_corpus(db_path: str, corpus: dict[int, tuple[str, str, str]]) -> None:
    """Write the fixture corpus into a database and index it in FTS and vectors.

    Deliberately bypasses `MemoryStore.upsert_memory()`: this measures the
    retrieval stack, and going through the governed write path would entangle
    the measurement with consent and redaction behaviour that is tested
    elsewhere and is not what is being measured here.
    """
    from bartholomew.kernel.embedding_engine import get_embedding_engine
    from bartholomew.kernel.fts_client import FTSClient
    from bartholomew.kernel.vector_store import VectorStore

    now = datetime.now(timezone.utc).isoformat()

    fts = FTSClient(db_path)
    fts.init_schema()

    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY,
                kind TEXT, key TEXT, value TEXT, summary TEXT, ts TEXT
            );
            CREATE TABLE IF NOT EXISTS memory_consent (
                memory_id INTEGER PRIMARY KEY,
                consent_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                source TEXT
            );
            """,
        )
        for memory_id, (kind, key, text) in corpus.items():
            conn.execute(
                "INSERT OR REPLACE INTO memories (id, kind, key, value, summary, ts) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (memory_id, kind, key, text, text, now),
            )
            # The fixture corpus is synthetic and consented by construction.
            # Consent behaviour is tested in its own suite; leaving it
            # unpopulated here would measure the consent gate rather than
            # retrieval quality.
            conn.execute(
                "INSERT OR IGNORE INTO memory_consent (memory_id, source) VALUES (?, ?)",
                (memory_id, "retrieval_eval_fixture"),
            )
        conn.commit()
    for memory_id, (_, _, text) in corpus.items():
        fts.upsert(memory_id, text)

    engine = get_embedding_engine()
    # Effective identity, so the seeded vectors land in the population the
    # retriever will actually search.
    provider, model, embedder_kind = engine.storage_identity
    store = VectorStore(db_path)
    for memory_id, (_, _, text) in corpus.items():
        vec = engine.embed_texts([text])[0]
        store.upsert(memory_id, vec, "summary", provider, model, embedder_kind)


def _returned_ids(result) -> list[int]:
    """Memory IDs out of whichever result shape a retriever returned."""
    ids: list[int] = []
    for item in result or []:
        memory_id = getattr(item, "memory_id", None)
        if memory_id is None and isinstance(item, dict):
            memory_id = item.get("memory_id") or item.get("id")
        if memory_id is not None:
            ids.append(int(memory_id))
    return ids


def evaluate_mode(
    db_path: str,
    mode: str,
    cases: list[tuple[str, str, tuple[int, ...]]],
    top_k: int = 3,
) -> ModeReport:
    """Run every case under one retrieval mode."""
    from bartholomew.kernel.retrieval import get_retriever

    report = ModeReport(mode=mode)

    try:
        retriever = get_retriever(mode=mode, db_path=db_path)
    except Exception as e:
        # "Could not run" is a distinct outcome from "scored zero", and
        # collapsing them would misreport an unavailable embedder as a quality
        # result.
        report.error = str(e)
        return report

    for category, query, expected in cases:
        try:
            returned = _returned_ids(retriever.retrieve(query, top_k=top_k))
        except Exception as e:
            logger.warning("Query %r failed under mode %s: %s", query, mode, e)
            returned = []

        report.cases.append(
            CaseResult(
                category=category,
                query=query,
                expected=expected,
                returned=returned,
                top1_hit=bool(expected) and bool(returned) and returned[0] in expected,
                top3_hit=bool(expected) and any(mid in expected for mid in returned[:3]),
                returned_anything=bool(returned),
            ),
        )

    return report


def run_evaluation(
    db_path: str,
    corpus: dict[int, tuple[str, str, str]],
    cases: list[tuple[str, str, tuple[int, ...]]],
    modes: tuple[str, ...] = EVAL_MODES,
) -> dict[str, object]:
    """Seed the corpus and measure every mode. Returns a reportable dict."""
    from bartholomew.kernel.retrieval import describe_retrieval

    seed_corpus(db_path, corpus)

    reports = {mode: evaluate_mode(db_path, mode, cases) for mode in modes}

    return {
        # The context without which every number below is meaningless.
        "retrieval": describe_retrieval(),
        "corpus_size": len(corpus),
        "case_count": len(cases),
        "reports": reports,
    }
