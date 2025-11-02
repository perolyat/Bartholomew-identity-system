"""
Tests for bm25 UDF fallback using matchinfo('pcx')
"""

import os
import tempfile

import pytest

from bartholomew.kernel.fts_client import FTSClient
from bartholomew.kernel.memory_store import MemoryStore


@pytest.mark.asyncio
async def test_bm25_fallback_with_env_var():
    """Test FTS search works with forced fallback to matchinfo('pcx')"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_fallback.db")

        # Initialize memory store
        store = MemoryStore(db_path)
        await store.init()

        # Insert test memories
        await store.upsert_memory(
            "fact",
            "test_key_1",
            "The robot can understand natural language queries",
            "2025-01-01T12:00:00Z",
        )

        await store.upsert_memory(
            "fact",
            "test_key_2",
            "Machine learning enables pattern recognition",
            "2025-01-01T12:05:00Z",
        )

        await store.upsert_memory(
            "preference",
            "test_key_3",
            "User prefers concise communication style",
            "2025-01-01T12:10:00Z",
        )

        # Force fallback mode
        os.environ["BARTHO_FORCE_BM25_FALLBACK"] = "1"

        try:
            fts = FTSClient(db_path)

            # Search should work with fallback
            results = fts.search("robot")

            # Should return results without crashing
            assert len(results) > 0
            assert any("robot" in r["value"].lower() for r in results)

            # Verify rank field exists
            for r in results:
                assert "rank" in r
                assert isinstance(r["rank"], (int, float))

            # Test another query
            results2 = fts.search("machine learning")
            assert len(results2) > 0
            assert any("machine" in r["value"].lower() for r in results2)

        finally:
            # Clean up env var
            if "BARTHO_FORCE_BM25_FALLBACK" in os.environ:
                del os.environ["BARTHO_FORCE_BM25_FALLBACK"]

            await store.close()


@pytest.mark.asyncio
async def test_fallback_ranking_order():
    """Test that fallback maintains reasonable ranking order"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_ranking.db")

        # Initialize memory store
        store = MemoryStore(db_path)
        await store.init()

        # Insert memories with varying relevance
        await store.upsert_memory(
            "fact",
            "high_rel",
            "privacy privacy privacy is important",  # High term frequency
            "2025-01-01T12:00:00Z",
        )

        await store.upsert_memory(
            "fact",
            "med_rel",
            "privacy concerns should be addressed",  # Medium relevance
            "2025-01-01T12:05:00Z",
        )

        await store.upsert_memory(
            "fact",
            "low_rel",
            "general information about data",  # No matching terms
            "2025-01-01T12:10:00Z",
        )

        # Force fallback mode
        os.environ["BARTHO_FORCE_BM25_FALLBACK"] = "1"

        try:
            fts = FTSClient(db_path)

            # Search for "privacy"
            results = fts.search("privacy", order_by_rank=True)

            # High relevance doc should rank better (lower rank value)
            # than medium relevance doc
            if len(results) >= 2:
                # Find our specific docs
                high_doc = next((r for r in results if r["key"] == "high_rel"), None)
                med_doc = next((r for r in results if r["key"] == "med_rel"), None)

                if high_doc and med_doc:
                    # Lower rank = better match (when order_by_rank=True)
                    assert high_doc["rank"] < med_doc["rank"]

        finally:
            if "BARTHO_FORCE_BM25_FALLBACK" in os.environ:
                del os.environ["BARTHO_FORCE_BM25_FALLBACK"]

            await store.close()


@pytest.mark.asyncio
async def test_fallback_boolean_queries():
    """Test fallback handles FTS5 boolean queries"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_boolean.db")

        # Initialize memory store
        store = MemoryStore(db_path)
        await store.init()

        # Insert test memories
        await store.upsert_memory(
            "fact",
            "both",
            "AI and machine learning are related",
            "2025-01-01T12:00:00Z",
        )

        await store.upsert_memory(
            "fact",
            "ai_only",
            "Artificial intelligence is advancing",
            "2025-01-01T12:05:00Z",
        )

        await store.upsert_memory(
            "fact",
            "ml_only",
            "Machine learning requires data",
            "2025-01-01T12:10:00Z",
        )

        # Force fallback mode
        os.environ["BARTHO_FORCE_BM25_FALLBACK"] = "1"

        try:
            fts = FTSClient(db_path)

            # AND query
            results_and = fts.search("machine AND learning")
            assert len(results_and) > 0

            # OR query
            results_or = fts.search("AI OR machine")
            assert len(results_or) > 0

            # Phrase query
            results_phrase = fts.search('"machine learning"')
            assert len(results_phrase) > 0

        finally:
            if "BARTHO_FORCE_BM25_FALLBACK" in os.environ:
                del os.environ["BARTHO_FORCE_BM25_FALLBACK"]

            await store.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
