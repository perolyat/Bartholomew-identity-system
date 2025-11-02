"""
Integration test: Lexical (FTS) beats vector on exact rare tokens

Validates that exact token matching via FTS outperforms semantic embeddings
when queries contain rare, unique tokens that have no semantic correlation
to the embedded content.
"""

import asyncio
import os
import tempfile

import pytest

from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.retrieval import get_retriever
from bartholomew.kernel.vector_store import VectorStore
from tests.helpers.synthetic import create_uncorrelated_embeddings, make_rare_token


def create_rare_token_corpus(num_memories: int = 70, seed: int = 1337):
    """
    Create corpus with rare tokens embedded in normal text

    Each memory contains a unique rare token that can be matched exactly
    via FTS but has uncorrelated vector embeddings.

    Returns:
        List of dicts with 'token', 'text', 'kind', 'key', 'ts'
    """
    corpus = []

    for i in range(num_memories):
        token = make_rare_token(i, length=10, seed=seed)

        # Embed token in natural language context
        templates = [
            f"The configuration parameter {token} controls the cache behavior.",
            f"Error code {token} indicates a network timeout condition.",
            f"The debug flag {token} enables verbose logging output.",
            f"Reference identifier {token} links to the external database.",
            f"Token {token} is used for authentication purposes.",
        ]

        template_idx = i % len(templates)
        text = templates[template_idx]

        corpus.append(
            {
                "token": token,
                "text": text,
                "kind": "preference",
                "key": f"rare_token_{i}",
                "ts": f"2025-01-{(i % 28) + 1:02d}T12:00:00Z",
            },
        )

    return corpus


@pytest.mark.asyncio
async def test_lexical_beats_vector_on_exact_rare_tokens():
    """
    FTS should vastly outperform vector search on exact rare token queries
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_rare_tokens.db")

        # Create corpus
        corpus = create_rare_token_corpus(num_memories=70, seed=1337)

        # Initialize store
        os.environ["BARTHO_EMBED_ENABLED"] = "0"
        store = MemoryStore(db_path)
        await store.init()

        # Ingest memories
        memory_map = {}  # token -> memory_id
        for item in corpus:
            result = await store.upsert_memory(
                kind=item["kind"],
                key=item["key"],
                value=item["text"],
                ts=item["ts"],
            )
            memory_map[item["token"]] = result.memory_id

        await store.close()

        # Add uncorrelated embeddings
        vec_store = VectorStore(db_path)
        for i, item in enumerate(corpus):
            memory_id = memory_map[item["token"]]
            vec = create_uncorrelated_embeddings(i, dim=384, seed=1337)
            vec_store.upsert(
                memory_id=memory_id,
                vec=vec,
                source="full",
                provider="local-sbert",
                model="BAAI/bge-small-en-v1.5",
            )

        # Test retrieval modes on exact token queries
        # Use every 5th token as query (14 queries)
        test_queries = [(corpus[i]["token"], corpus[i]["token"]) for i in range(0, len(corpus), 5)]

        os.environ["BARTHO_DB_PATH"] = db_path

        # FTS-only retrieval
        fts_retriever = get_retriever(mode="fts", db_path=db_path)
        fts_hits = 0
        for query_token, expected_token in test_queries:
            try:
                results = fts_retriever.retrieve(query_token, top_k=10)
                # Check if expected token's memory is in top-1
                expected_id = memory_map[expected_token]
                if results and results[0].memory_id == expected_id:
                    fts_hits += 1
            except Exception:
                pass

        fts_top1_accuracy = fts_hits / len(test_queries)

        # Vector-only retrieval
        vec_retriever = get_retriever(mode="vector", db_path=db_path)
        vec_hits = 0
        for query_token, expected_token in test_queries:
            try:
                results = vec_retriever.retrieve(query_token, top_k=10)
                # Check if expected token's memory is in top-1
                expected_id = memory_map[expected_token]
                if results and results[0].memory_id == expected_id:
                    vec_hits += 1
            except Exception:
                pass

        vec_top1_accuracy = vec_hits / len(test_queries)

        # Hybrid retrieval
        hybrid_retriever = get_retriever(mode="hybrid", db_path=db_path)
        hybrid_hits = 0
        for query_token, expected_token in test_queries:
            try:
                results = hybrid_retriever.retrieve(query_token, top_k=10)
                # Check if expected token's memory is in top-1
                expected_id = memory_map[expected_token]
                if results and results[0].memory_id == expected_id:
                    hybrid_hits += 1
            except Exception:
                pass

        hybrid_top1_accuracy = hybrid_hits / len(test_queries)

        print("\nRare token top-1 accuracy:")
        print(f"  FTS: {fts_top1_accuracy:.2%}")
        print(f"  Vector: {vec_top1_accuracy:.2%}")
        print(f"  Hybrid: {hybrid_top1_accuracy:.2%}")

        # Assertions
        # FTS should have high accuracy on exact tokens
        assert fts_top1_accuracy >= 0.90, (
            f"FTS top-1 accuracy ({fts_top1_accuracy:.2%}) should be ≥90% "
            f"on exact rare token queries"
        )

        # Vector should have low accuracy (uncorrelated embeddings)
        assert vec_top1_accuracy <= 0.20, (
            f"Vector top-1 accuracy ({vec_top1_accuracy:.2%}) should be ≤20% "
            f"on rare tokens with uncorrelated embeddings"
        )

        # Hybrid should be at least as good as vector
        assert (
            hybrid_top1_accuracy >= vec_top1_accuracy
        ), f"Hybrid ({hybrid_top1_accuracy:.2%}) should be ≥ vector ({vec_top1_accuracy:.2%})"

        # Hybrid should benefit from FTS component
        assert hybrid_top1_accuracy >= 0.70, (
            f"Hybrid ({hybrid_top1_accuracy:.2%}) should be ≥70% "
            f"leveraging FTS on exact token queries"
        )


@pytest.mark.asyncio
async def test_lexical_top_k_coverage_on_rare_tokens():
    """
    FTS should find rare tokens in top-k results consistently
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_rare_tokens_topk.db")

        # Create smaller corpus for faster test
        corpus = create_rare_token_corpus(num_memories=50, seed=2024)

        # Initialize store
        os.environ["BARTHO_EMBED_ENABLED"] = "0"
        store = MemoryStore(db_path)
        await store.init()

        # Ingest memories
        memory_map = {}
        for item in corpus:
            result = await store.upsert_memory(
                kind=item["kind"],
                key=item["key"],
                value=item["text"],
                ts=item["ts"],
            )
            memory_map[item["token"]] = result.memory_id

        await store.close()

        # Add uncorrelated embeddings
        vec_store = VectorStore(db_path)
        for i, item in enumerate(corpus):
            memory_id = memory_map[item["token"]]
            vec = create_uncorrelated_embeddings(i, dim=384, seed=2024)
            vec_store.upsert(
                memory_id=memory_id,
                vec=vec,
                source="full",
                provider="local-sbert",
                model="BAAI/bge-small-en-v1.5",
            )

        # Test with every 4th token (12-13 queries)
        test_queries = [(corpus[i]["token"], corpus[i]["token"]) for i in range(0, len(corpus), 4)]

        os.environ["BARTHO_DB_PATH"] = db_path

        # FTS-only retrieval with top-k=5
        fts_retriever = get_retriever(mode="fts", db_path=db_path)
        fts_topk_hits = 0
        for query_token, expected_token in test_queries:
            try:
                results = fts_retriever.retrieve(query_token, top_k=5)
                # Check if expected token appears in top-5
                for result in results:
                    if result.memory_id == memory_map[expected_token]:
                        fts_topk_hits += 1
                        break
            except Exception:
                pass

        fts_topk_recall = fts_topk_hits / len(test_queries)

        print(f"\nFTS top-5 recall on rare tokens: {fts_topk_recall:.2%}")

        # FTS should find rare tokens in top-5
        assert fts_topk_recall >= 0.92, f"FTS top-5 recall ({fts_topk_recall:.2%}) should be ≥92%"


if __name__ == "__main__":
    asyncio.run(test_lexical_beats_vector_on_exact_rare_tokens())
    asyncio.run(test_lexical_top_k_coverage_on_rare_tokens())
    print("Lexical > vector tests passed!")
