"""
Integration test: FTS unavailable → vector-only still returns good answers

Validates that when FTS5 is unavailable, hybrid retrieval degrades gracefully
to vector-only mode and maintains acceptable answer quality.
"""

import asyncio
import os
import tempfile
from unittest.mock import patch

import pytest

from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.retrieval import get_retriever
from bartholomew.kernel.vector_store import VectorStore
from tests.helpers.synthetic import create_synthetic_embeddings


def create_quality_corpus(num_groups: int = 30, seed: int = 42):
    """
    Create corpus suitable for quality assessment

    Uses paraphrase-like groups where semantic similarity matters.

    Returns:
        List of dicts with 'group_id', 'variant_idx', 'text', etc
    """
    corpus = []

    templates = [
        [
            "How do I configure the privacy settings?",
            "What's the best way to adjust my privacy preferences?",
            "Where can I change privacy controls?",
        ],
        [
            "The system is running slowly today",
            "Performance seems degraded right now",
            "Everything feels laggy at the moment",
        ],
        [
            "Can I export my data to another format?",
            "Is there a way to download my information?",
            "How do I get a copy of my stored data?",
        ],
        [
            "The notification settings are confusing",
            "I don't understand the alert preferences",
            "How do notifications work here?",
        ],
        [
            "Security update installed successfully",
            "Latest patch applied without errors",
            "System updated to newest secure version",
        ],
    ]

    group_idx = 0
    for template_group in templates:
        for variant_idx, text in enumerate(template_group):
            corpus.append(
                {
                    "group_id": group_idx,
                    "variant_idx": variant_idx,
                    "text": text,
                    "kind": "event",
                    "key": f"event_{group_idx}_{variant_idx}",
                    "ts": f"2025-01-{(group_idx % 28) + 1:02d}T12:00:00Z",
                },
            )
        group_idx += 1

    # Add padding entries to reach target size
    base_count = len(corpus)
    for i in range(base_count, num_groups * 3):
        group_id = i // 3
        variant_idx = i % 3
        text = f"Generic memory entry {i} for diversity in corpus."
        corpus.append(
            {
                "group_id": group_id,
                "variant_idx": variant_idx,
                "text": text,
                "kind": "preference",
                "key": f"pref_{i}",
                "ts": f"2025-01-{(i % 28) + 1:02d}T12:00:00Z",
            },
        )

    return corpus


def calculate_hit_rate(results_list, queries, memory_map):
    """Calculate top-10 hit rate for paraphrase queries"""
    hits = 0
    for query_info, results in zip(queries, results_list, strict=False):
        query_group = query_info["group_id"]
        # Check if any result is from same group (semantically similar)
        for result in results[:10]:
            # Find which group this memory belongs to
            for (group_id, _variant_idx), mem_id in memory_map.items():
                if mem_id == result.memory_id and group_id == query_group:
                    hits += 1
                    break

    return hits / len(queries) if queries else 0.0


@pytest.mark.asyncio
async def test_vector_quality_maintained_when_fts_unavailable():
    """
    Hybrid mode with FTS unavailable should match vector-only quality
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_fts_unavailable_quality.db")

        # Create corpus
        corpus = create_quality_corpus(num_groups=30, seed=42)

        # Initialize store
        os.environ["BARTHO_EMBED_ENABLED"] = "0"
        store = MemoryStore(db_path)
        await store.init()

        # Ingest memories
        memory_map = {}  # (group_id, variant_idx) -> memory_id
        for item in corpus:
            result = await store.upsert_memory(
                kind=item["kind"],
                key=item["key"],
                value=item["text"],
                ts=item["ts"],
            )
            memory_map[(item["group_id"], item["variant_idx"])] = result.memory_id

        await store.close()

        # Add clustered embeddings (same group = similar vectors)
        vec_store = VectorStore(db_path)
        for item in corpus:
            key = (item["group_id"], item["variant_idx"])
            memory_id = memory_map[key]
            vec = create_synthetic_embeddings(
                item["group_id"],
                item["variant_idx"],
                dim=384,
                seed=42,
            )
            vec_store.upsert(
                memory_id=memory_id,
                vec=vec,
                source="full",
                provider="local-sbert",
                model="BAAI/bge-small-en-v1.5",
            )

        # Create test queries (use first variant of first 10 groups)
        queries = []
        for group_id in range(min(10, len(set(item["group_id"] for item in corpus)))):
            # Find first variant of this group
            for item in corpus:
                if item["group_id"] == group_id and item["variant_idx"] == 0:
                    queries.append({"text": item["text"], "group_id": group_id})
                    break

        os.environ["BARTHO_DB_PATH"] = db_path

        # Baseline: vector-only retrieval
        vec_retriever = get_retriever(mode="vector", db_path=db_path)
        vec_results_list = []
        for query in queries:
            try:
                results = vec_retriever.retrieve(query["text"], top_k=10)
                vec_results_list.append(results)
            except Exception:
                vec_results_list.append([])

        vec_hit_rate = calculate_hit_rate(vec_results_list, queries, memory_map)

        # Test: hybrid with FTS unavailable
        with patch("bartholomew.kernel.retrieval.fts5_available") as mock:
            mock.return_value = False

            # Clear cache to force new check
            from bartholomew.kernel import retrieval

            retrieval._fts5_available_cache = None

            hybrid_retriever = get_retriever(mode="hybrid", db_path=db_path)
            hybrid_results_list = []
            for query in queries:
                try:
                    results = hybrid_retriever.retrieve(query["text"], top_k=10)
                    hybrid_results_list.append(results)
                except Exception:
                    hybrid_results_list.append([])

            hybrid_hit_rate = calculate_hit_rate(hybrid_results_list, queries, memory_map)

        print("\nHit rates with FTS unavailable:")
        print(f"  Vector baseline: {vec_hit_rate:.2%}")
        print(f"  Hybrid (FTS unavailable): {hybrid_hit_rate:.2%}")
        print(f"  Difference: {abs(hybrid_hit_rate - vec_hit_rate):.2%}")

        # Assertions
        # Hybrid with FTS unavailable should match vector baseline closely
        assert abs(hybrid_hit_rate - vec_hit_rate) <= 0.02, (
            f"Hybrid with FTS unavailable ({hybrid_hit_rate:.2%}) should "
            f"match vector baseline ({vec_hit_rate:.2%}) within 2%"
        )

        # Both should maintain acceptable quality (≥60%)
        assert (
            hybrid_hit_rate >= 0.60
        ), f"Hybrid with FTS unavailable ({hybrid_hit_rate:.2%}) should maintain ≥60% hit rate"

        assert vec_hit_rate >= 0.60, f"Vector baseline ({vec_hit_rate:.2%}) should be ≥60%"


@pytest.mark.asyncio
async def test_hybrid_type_stable_when_fts_unavailable():
    """
    Hybrid retriever should remain type-stable when FTS unavailable
    (returns HybridRetriever, not VectorRetrieverAdapter)
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_type_stable.db")

        # Create minimal DB
        corpus = create_quality_corpus(num_groups=5, seed=1337)

        os.environ["BARTHO_EMBED_ENABLED"] = "0"
        store = MemoryStore(db_path)
        await store.init()

        for item in corpus:
            await store.upsert_memory(
                kind=item["kind"],
                key=item["key"],
                value=item["text"],
                ts=item["ts"],
            )

        await store.close()

        os.environ["BARTHO_DB_PATH"] = db_path

        # Mock FTS as unavailable
        with patch("bartholomew.kernel.retrieval.fts5_available") as mock:
            mock.return_value = False

            from bartholomew.kernel import retrieval

            retrieval._fts5_available_cache = None

            retriever = get_retriever(mode="hybrid", db_path=db_path)

            # Should still be HybridRetriever (type stable)
            assert (
                type(retriever).__name__ == "HybridRetriever"
            ), f"Expected HybridRetriever, got {type(retriever).__name__}"


@pytest.mark.asyncio
async def test_no_crash_on_fts_queries_when_unavailable():
    """
    Hybrid retriever should not crash on queries when FTS unavailable
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_no_crash.db")

        # Create minimal corpus
        corpus = create_quality_corpus(num_groups=5, seed=2024)

        os.environ["BARTHO_EMBED_ENABLED"] = "0"
        store = MemoryStore(db_path)
        await store.init()

        memory_map = {}
        for item in corpus:
            result = await store.upsert_memory(
                kind=item["kind"],
                key=item["key"],
                value=item["text"],
                ts=item["ts"],
            )
            memory_map[(item["group_id"], item["variant_idx"])] = result.memory_id

        await store.close()

        # Add embeddings
        vec_store = VectorStore(db_path)
        for item in corpus:
            key = (item["group_id"], item["variant_idx"])
            memory_id = memory_map[key]
            vec = create_synthetic_embeddings(
                item["group_id"],
                item["variant_idx"],
                dim=384,
                seed=2024,
            )
            vec_store.upsert(
                memory_id=memory_id,
                vec=vec,
                source="full",
                provider="local-sbert",
                model="BAAI/bge-small-en-v1.5",
            )

        os.environ["BARTHO_DB_PATH"] = db_path

        # Mock FTS unavailable
        with patch("bartholomew.kernel.retrieval.fts5_available") as mock:
            mock.return_value = False

            from bartholomew.kernel import retrieval

            retrieval._fts5_available_cache = None

            retriever = get_retriever(mode="hybrid", db_path=db_path)

            # Try various query types - should not crash
            test_queries = [
                "privacy settings",
                "system performance",
                '"exact phrase"',  # Lexical syntax
                "kind:event",  # Field filter
                "how AND where",  # Boolean operator
                "",  # Empty query
            ]

            for query in test_queries:
                try:
                    results = retriever.retrieve(query, top_k=5)
                    # Should return results or empty list, not crash
                    assert isinstance(results, list), f"Query '{query}' should return list"
                except Exception as e:
                    pytest.fail(f"Query '{query}' crashed with FTS unavailable: {e}")


if __name__ == "__main__":
    asyncio.run(test_vector_quality_maintained_when_fts_unavailable())
    asyncio.run(test_hybrid_type_stable_when_fts_unavailable())
    asyncio.run(test_no_crash_on_fts_queries_when_unavailable())
    print("FTS unavailable quality tests passed!")
