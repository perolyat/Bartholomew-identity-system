"""
Tests for a real, previously-unfixed retrieval bug: `requires_consent`
memories were excluded unconditionally by every retriever's own rules-engine
layer (bartholomew.kernel.retrieval.Retriever._should_include,
bartholomew.kernel.retrieval.FTSOnlyRetriever._evaluate_rules,
bartholomew.kernel.hybrid_retriever.HybridRetriever._evaluate_rules),
ignoring the `memory_consent` table entirely -- even though
bartholomew.kernel.consent_gate.ConsentGate already reads that table
correctly and is applied one layer down, inside FTSClient.search() and
VectorStore.search().

Net effect before this fix: a user could grant consent for an
ask_before_store memory (recording a real memory_consent row, e.g. via
MemoryStore.upsert_memory()'s embedding flow), and it would still never be
retrievable through any of the three retriever modes (vector/fts/hybrid),
because each mode's own rules-engine pass re-excluded anything flagged
requires_consent regardless of the memory_consent table. Fail-closed (no
privacy leak), but the consent-granting feature was silently non-functional
for retrieval purposes.

These tests exercise the fixed methods directly, with a mocked rules_engine
so the assertion is about the requires_consent/consented_ids wiring itself,
not memory_rules.yaml's pattern-matching.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import Mock

import pytest

from bartholomew.kernel.hybrid_retriever import HybridRetriever
from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.retrieval import FTSOnlyRetriever, Retriever
from bartholomew.kernel.vector_store import VectorStore


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except (PermissionError, FileNotFoundError):
        pass


@pytest.fixture
async def initialized_db(temp_db):
    """A DB with schema initialized (memories, memory_consent, etc.)."""
    store = MemoryStore(temp_db)
    await store.init()
    return temp_db


def _consenting_rules_engine() -> Mock:
    engine = Mock()
    engine.evaluate.return_value = {"allow_store": True, "requires_consent": True}
    return engine


class TestRetrieverShouldInclude:
    """bartholomew.kernel.retrieval.Retriever._should_include()"""

    def test_excludes_requires_consent_without_a_consent_record(self, initialized_db):
        vector_store = VectorStore(initialized_db)
        retriever = Retriever(rules_engine=Mock(), vector_store=vector_store)

        evaluated = {"allow_store": True, "requires_consent": True}

        assert retriever._should_include(evaluated, memory_id=42, consented_ids=set()) is False
        assert retriever._should_include(evaluated, memory_id=42, consented_ids=None) is False

    def test_includes_requires_consent_with_a_consent_record(self, initialized_db):
        vector_store = VectorStore(initialized_db)
        retriever = Retriever(rules_engine=Mock(), vector_store=vector_store)

        evaluated = {"allow_store": True, "requires_consent": True}

        assert retriever._should_include(evaluated, memory_id=42, consented_ids={42}) is True

    def test_never_store_stays_excluded_even_with_consent(self, initialized_db):
        vector_store = VectorStore(initialized_db)
        retriever = Retriever(rules_engine=Mock(), vector_store=vector_store)

        evaluated = {"allow_store": False, "requires_consent": True}

        assert retriever._should_include(evaluated, memory_id=42, consented_ids={42}) is False


class TestFTSOnlyRetrieverEvaluateRules:
    """bartholomew.kernel.retrieval.FTSOnlyRetriever._evaluate_rules()"""

    def test_excludes_requires_consent_without_a_consent_record(self, initialized_db):
        retriever = FTSOnlyRetriever(
            db_path=initialized_db,
            rules_engine=_consenting_rules_engine(),
        )
        metadata = {7: {"id": 7, "kind": "user"}}

        rules_data = retriever._evaluate_rules(metadata, {7}, consented_ids=set())

        assert rules_data[7]["include"] is False

    def test_includes_requires_consent_with_a_consent_record(self, initialized_db):
        retriever = FTSOnlyRetriever(
            db_path=initialized_db,
            rules_engine=_consenting_rules_engine(),
        )
        metadata = {7: {"id": 7, "kind": "user"}}

        rules_data = retriever._evaluate_rules(metadata, {7}, consented_ids={7})

        assert rules_data[7]["include"] is True

    def test_defaults_to_excluded_when_consented_ids_omitted(self, initialized_db):
        """No behavior change for any caller that doesn't pass consented_ids."""
        retriever = FTSOnlyRetriever(
            db_path=initialized_db,
            rules_engine=_consenting_rules_engine(),
        )
        metadata = {7: {"id": 7, "kind": "user"}}

        rules_data = retriever._evaluate_rules(metadata, {7})

        assert rules_data[7]["include"] is False


class TestHybridRetrieverEvaluateRules:
    """bartholomew.kernel.hybrid_retriever.HybridRetriever._evaluate_rules()"""

    def test_excludes_requires_consent_without_a_consent_record(self, initialized_db):
        retriever = HybridRetriever(db_path=initialized_db, rules_engine=_consenting_rules_engine())
        metadata = {9: {"id": 9, "kind": "user"}}

        rules_data = retriever._evaluate_rules(metadata, {9}, consented_ids=set())

        assert rules_data[9]["include"] is False

    def test_includes_requires_consent_with_a_consent_record(self, initialized_db):
        retriever = HybridRetriever(db_path=initialized_db, rules_engine=_consenting_rules_engine())
        metadata = {9: {"id": 9, "kind": "user"}}

        rules_data = retriever._evaluate_rules(metadata, {9}, consented_ids={9})

        assert rules_data[9]["include"] is True


@pytest.mark.asyncio
class TestEndToEndConsentRecordUnblocksRetrieval:
    """A real memory_consent row (as MemoryStore.upsert_memory() would write
    via its embedding flow) is what _get_consented_ids()/ConsentGate reads --
    proving the fix's data path against the real table, not just a mocked
    set."""

    async def test_consent_gate_and_retriever_agree_on_a_real_consent_row(self, initialized_db):
        import aiosqlite

        async with aiosqlite.connect(initialized_db) as db:
            await db.execute(
                "INSERT OR IGNORE INTO memory_consent (memory_id, source) VALUES (?, ?)",
                (99, "test"),
            )
            await db.commit()

        vector_store = VectorStore(initialized_db)
        retriever = Retriever(rules_engine=Mock(), vector_store=vector_store)
        consented_ids = retriever._get_consented_ids()

        assert 99 in consented_ids
        evaluated = {"allow_store": True, "requires_consent": True}
        assert (
            retriever._should_include(evaluated, memory_id=99, consented_ids=consented_ids) is True
        )
        assert (
            retriever._should_include(evaluated, memory_id=100, consented_ids=consented_ids)
            is False
        )
