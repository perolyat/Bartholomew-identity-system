"""
Retrieval tests for competency `kind` values (S5.1): confirm the existing,
unmodified retrieval machinery (`RetrievalFilters(kinds=...)`,
`ConsentGate`) already works for competency content with no changes, per
docs/S5_1_COMPETENCY_ARCHITECTURE_DESIGN.md Sec 3/4.8.
"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import Mock

import pytest

from bartholomew.kernel.competency import (
    COMPETENCY_KINDS,
    CompetencyEnvelope,
    CompetencyEvidence,
    CompetencyProcedure,
    Provenance,
)
from bartholomew.kernel.consent_gate import ConsentGate
from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.retrieval import RetrievalFilters, get_retriever
from conftest import SKIP_WINDOWS_FTS

TS = "2026-08-09T00:00:00Z"


def _envelope(**overrides) -> CompetencyEnvelope:
    defaults = {
        "competency_id": "estate_management",
        "classification": "personal",
        "provenance": Provenance(source_type="experience"),
        "confidence": 0.5,
    }
    defaults.update(overrides)
    return CompetencyEnvelope(**defaults)


@pytest.fixture
async def store(tmp_path):
    s = MemoryStore(str(tmp_path / "competency_retrieval.db"))
    await s.init()
    return s


@SKIP_WINDOWS_FTS
@pytest.mark.asyncio
async def test_kind_filter_returns_only_competency_records(store) -> None:
    procedure = CompetencyProcedure(
        envelope=_envelope(),
        slug="quote_comparison",
        name="Quote Comparison",
        steps=["Obtain three independent contractor quotes before deciding"],
    )
    await store.upsert_memory(
        kind=procedure.KIND,
        key=procedure.key(),
        value=json.dumps(procedure.to_dict()),
        ts=TS,
        summary=procedure.to_summary_text(),
        # See tests/test_competency_memory_shapes.py's
        # test_each_kind_round_trips_through_upsert_memory comment:
        # is_sensitive() fires on CompetencyProcedure's own `"name"` JSON
        # key. Pre-existing, unrelated to S5.1.
        skip_privacy_guard=True,
    )

    # A non-competency memory sharing the same searchable keyword, to prove
    # the kind filter -- not just the search term -- is what excludes it.
    await store.upsert_memory(
        kind="fact",
        key="unrelated_quote_fact",
        value="The quote from the movie was memorable.",
        ts=TS,
    )

    retriever = get_retriever(mode="fts", db_path=store.db_path, memory_store=store)
    results = retriever.retrieve(
        "quote",
        top_k=10,
        filters=RetrievalFilters(kinds=list(COMPETENCY_KINDS)),
    )

    assert len(results) >= 1
    assert all(r.kind in COMPETENCY_KINDS for r in results)
    assert all(r.kind != "fact" for r in results)


@SKIP_WINDOWS_FTS
@pytest.mark.asyncio
async def test_exact_competency_lookup_via_key_prefix(store) -> None:
    """Design doc Sec 4.7: 'all records for competency X' is an exact
    kind+key-prefix lookup, not a relevance search -- confirms the
    (kind, key) unique index serves this without a schema change."""
    proc_a = CompetencyProcedure(
        envelope=_envelope(competency_id="estate_management"),
        slug="quote_comparison",
        name="Quote Comparison",
        steps=["step"],
    )
    proc_b = CompetencyProcedure(
        envelope=_envelope(competency_id="vehicle_management"),
        slug="service_scheduling",
        name="Service Scheduling",
        steps=["step"],
    )
    for record in (proc_a, proc_b):
        await store.upsert_memory(
            kind=record.KIND,
            key=record.key(),
            value=json.dumps(record.to_dict()),
            ts=TS,
            summary=record.to_summary_text(),
            skip_privacy_guard=True,  # see test_kind_filter_returns_only_competency_records
        )

    conn = sqlite3.connect(store.db_path)
    try:
        rows = conn.execute(
            "SELECT key FROM memories WHERE kind = ? AND key LIKE ?",
            (CompetencyProcedure.KIND, "estate_management.%"),
        ).fetchall()
    finally:
        conn.close()

    keys = {r[0] for r in rows}
    assert keys == {"estate_management.quote_comparison"}


def _consenting_rules_engine():
    """Mirrors tests/test_retrieval_consent_enforcement.py's
    `_consenting_rules_engine()`: a mocked rules_engine whose evaluate()
    unconditionally reports requires_consent=True, so the assertion below is
    about ConsentGate's memory_consent-table wiring itself, not about
    memory_rules.yaml's content pattern-matching.

    Found while implementing: a real ask_before_store write gets its content
    redacted in storage (see test_competency_memory_shapes.py's
    test_redaction_of_json_embedded_content_preserves_valid_json), so
    re-evaluating rules against the already-redacted stored value no longer
    reliably re-matches the original pattern -- an unrelated, pre-existing
    property of how rule re-evaluation works, not something to route around
    silently. Mocking the rules_engine sidesteps that confound entirely."""
    engine = Mock()
    engine.evaluate.return_value = {"allow_store": True, "requires_consent": True}
    return engine


@pytest.mark.asyncio
async def test_ask_before_store_competency_evidence_excluded_until_consent(store) -> None:
    """ConsentGate-level proof: a memory flagged requires_consent=True by the
    rules engine is excluded from retrieval before a memory_consent row
    exists, and included once one is recorded -- exercised on a
    competency_evidence record whose classification is
    potentially_generalisable, to prove classification plays no role in
    either outcome and governance is untouched by S5.1."""
    record = CompetencyEvidence(
        envelope=_envelope(classification="potentially_generalisable"),
        slug="mentions_bank_detail",
        situation="Contractor asked for our bank account number for a deposit.",
    )
    result = await store.upsert_memory(
        kind=record.KIND,
        key=record.key(),
        value=json.dumps(record.to_dict()),
        ts=TS,
        summary=record.to_summary_text(),
        skip_privacy_guard=True,
        skip_rule_consent=True,
    )
    assert result.stored is True
    memory_id = result.memory_id

    gate = ConsentGate(store.db_path, rules_engine=_consenting_rules_engine())

    # No memory_consent row yet -- excluded, using the same
    # filter_memory_ids() entry point the retrievers themselves call.
    excluded = gate.filter_memory_ids([memory_id], consented_ids=set())
    assert excluded[memory_id]["include"] is False

    # Recording consent -- the same memory_consent table every kind uses --
    # flips it to included. INSERT OR IGNORE (not plain INSERT): if
    # BARTHO_EMBED_ENABLED is set, upsert_memory()'s own embedding flow may
    # already have inserted a memory_consent row for this memory_id.
    conn = sqlite3.connect(store.db_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO memory_consent (memory_id, source) VALUES (?, ?)",
            (memory_id, "test_consent"),
        )
        conn.commit()
    finally:
        conn.close()

    policy = gate.get_memory_policy(memory_id)
    assert policy["include"] is True
