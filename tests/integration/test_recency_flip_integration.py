"""
Integration test: Recency boosts flip close calls as expected

Validates that recency shaping influences hybrid retrieval rankings
when base scores are near-equal, proving the recency mechanism works
end-to-end with real data ingestion and retrieval.
"""
import pytest
import asyncio
import tempfile
import os
from datetime import datetime, timedelta, timezone

from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.vector_store import VectorStore
from bartholomew.kernel.retrieval import get_retriever
from bartholomew.kernel.hybrid_retriever import HybridRetrievalConfig
from tests.helpers.synthetic import create_synthetic_embeddings


def create_recency_corpus(
    num_groups: int = 25,
    variants_per_group: int = 2,
    seed: int = 42
):
    """
    Create corpus with near-duplicate memories at different timestamps
    
    Each group has 2 variants: one old, one recent. Text is identical
    or nearly identical, embeddings are near-equal.
    
    Returns:
        List of dicts with 'group_id', 'text', 'kind', 'key', 'ts'
    """
    corpus = []
    base_now = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    
    for group_id in range(num_groups):
        # Old variant: 30 days ago
        ts_old = (base_now - timedelta(days=30)).isoformat()
        
        # Recent variant: 1 hour ago
        ts_recent = (base_now - timedelta(hours=1)).isoformat()
        
        # Text is same or minimal variation
        text = (
            f"User preference for theme configuration in group {group_id}. "
            f"Dark mode enabled with accent color blue."
        )
        
        corpus.append({
            'group_id': group_id,
            'variant': 'old',
            'text': text,
            'kind': 'preference',
            'key': f'pref_{group_id}_old',
            'ts': ts_old
        })
        
        corpus.append({
            'group_id': group_id,
            'variant': 'recent',
            'text': text,
            'kind': 'preference',
            'key': f'pref_{group_id}_recent',
            'ts': ts_recent
        })
    
    return corpus


@pytest.mark.asyncio
async def test_recency_boost_flips_rankings_weighted():
    """
    Recent memories should rank above older ones in weighted mode
    when base scores are near-equal
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_recency_weighted.db")
        
        # Create corpus
        corpus = create_recency_corpus(num_groups=25, seed=42)
        
        # Initialize store
        os.environ["BARTHO_EMBED_ENABLED"] = "0"
        store = MemoryStore(db_path)
        await store.init()
        
        # Ingest memories
        memory_map = {}  # (group_id, variant) -> memory_id
        for item in corpus:
            result = await store.upsert_memory(
                kind=item['kind'],
                key=item['key'],
                value=item['text'],
                ts=item['ts']
            )
            memory_map[(item['group_id'], item['variant'])] = (
                result.memory_id
            )
        
        await store.close()
        
        # Add near-equal embeddings (same group = nearly identical)
        vec_store = VectorStore(db_path)
        for item in corpus:
            memory_id = memory_map[(item['group_id'], item['variant'])]
            group_id = item['group_id']
            # Both variants get same centroid, minimal noise
            variant_idx = 0 if item['variant'] == 'old' else 1
            vec = create_synthetic_embeddings(
                group_id, variant_idx, dim=384, seed=42
            )
            vec_store.upsert(
                memory_id=memory_id,
                vec=vec,
                source="full",
                provider="local-sbert",
                model="BAAI/bge-small-en-v1.5"
            )
        
        # Query each group's text
        os.environ["BARTHO_DB_PATH"] = db_path
        
        # Use hybrid retriever with recency enabled
        config = HybridRetrievalConfig(
            fusion_mode="weighted",
            weight_fts=0.5,
            weight_vec=0.5,
            half_life_hours=168  # 7 days
        )
        
        retriever = get_retriever(mode="hybrid", db_path=db_path)
        retriever.config = config  # Override config
        
        recent_wins = 0
        for group_id in range(25):
            query = (
                f"User preference for theme configuration in "
                f"group {group_id}. "
                f"Dark mode enabled with accent color blue."
            )
            
            try:
                results = retriever.retrieve(query, top_k=5)
                if not results:
                    continue
                
                # Find positions of old and recent variants
                old_id = memory_map.get((group_id, 'old'))
                recent_id = memory_map.get((group_id, 'recent'))
                
                old_pos = None
                recent_pos = None
                
                for pos, result in enumerate(results):
                    if result.memory_id == old_id:
                        old_pos = pos
                    if result.memory_id == recent_id:
                        recent_pos = pos
                
                # If both found, recent should rank higher (lower position)
                if old_pos is not None and recent_pos is not None:
                    if recent_pos < old_pos:
                        recent_wins += 1
            except Exception:
                pass
        
        # Calculate flip rate
        flip_rate = recent_wins / 25
        
        print(f"\nRecency flip rate (weighted): {flip_rate:.2%}")
        print(f"  Recent ranked above old: {recent_wins}/25 groups")
        
        # Recency should flip rankings in majority of cases
        assert flip_rate >= 0.75, (
            f"Recency should flip ≥75% of rankings, got {flip_rate:.2%}"
        )


@pytest.mark.asyncio
async def test_recency_boost_flips_rankings_rrf():
    """
    Recent memories should rank above older ones in RRF mode
    after boosts are applied
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_recency_rrf.db")
        
        # Create smaller corpus for RRF test
        corpus = create_recency_corpus(num_groups=20, seed=2024)
        
        # Initialize store
        os.environ["BARTHO_EMBED_ENABLED"] = "0"
        store = MemoryStore(db_path)
        await store.init()
        
        # Ingest memories
        memory_map = {}
        for item in corpus:
            result = await store.upsert_memory(
                kind=item['kind'],
                key=item['key'],
                value=item['text'],
                ts=item['ts']
            )
            memory_map[(item['group_id'], item['variant'])] = (
                result.memory_id
            )
        
        await store.close()
        
        # Add near-equal embeddings
        vec_store = VectorStore(db_path)
        for item in corpus:
            memory_id = memory_map[(item['group_id'], item['variant'])]
            group_id = item['group_id']
            variant_idx = 0 if item['variant'] == 'old' else 1
            vec = create_synthetic_embeddings(
                group_id, variant_idx, dim=384, seed=2024
            )
            vec_store.upsert(
                memory_id=memory_id,
                vec=vec,
                source="full",
                provider="local-sbert",
                model="BAAI/bge-small-en-v1.5"
            )
        
        # Query each group's text
        os.environ["BARTHO_DB_PATH"] = db_path
        
        # Use hybrid retriever with RRF and recency
        config = HybridRetrievalConfig(
            fusion_mode="rrf",
            rrf_k=60,
            half_life_hours=168  # 7 days
        )
        
        retriever = get_retriever(mode="hybrid", db_path=db_path)
        retriever.config = config
        
        recent_wins = 0
        for group_id in range(20):
            query = (
                f"User preference for theme configuration in "
                f"group {group_id}. "
                f"Dark mode enabled with accent color blue."
            )
            
            try:
                results = retriever.retrieve(query, top_k=5)
                if not results:
                    continue
                
                # Find positions
                old_id = memory_map.get((group_id, 'old'))
                recent_id = memory_map.get((group_id, 'recent'))
                
                old_pos = None
                recent_pos = None
                
                for pos, result in enumerate(results):
                    if result.memory_id == old_id:
                        old_pos = pos
                    if result.memory_id == recent_id:
                        recent_pos = pos
                
                # Recent should rank higher
                if old_pos is not None and recent_pos is not None:
                    if recent_pos < old_pos:
                        recent_wins += 1
            except Exception:
                pass
        
        # Calculate flip rate
        flip_rate = recent_wins / 20
        
        print(f"\nRecency flip rate (RRF): {flip_rate:.2%}")
        print(f"  Recent ranked above old: {recent_wins}/20 groups")
        
        # Recency should flip rankings in majority of cases
        assert flip_rate >= 0.70, (
            f"Recency should flip ≥70% of rankings in RRF, "
            f"got {flip_rate:.2%}"
        )


@pytest.mark.asyncio
async def test_recency_disabled_no_flip():
    """
    With recency disabled, rankings should be random/unstable
    (not consistently favoring recent)
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_no_recency.db")
        
        # Create small corpus
        corpus = create_recency_corpus(num_groups=15, seed=1337)
        
        # Initialize store
        os.environ["BARTHO_EMBED_ENABLED"] = "0"
        store = MemoryStore(db_path)
        await store.init()
        
        # Ingest memories
        memory_map = {}
        for item in corpus:
            result = await store.upsert_memory(
                kind=item['kind'],
                key=item['key'],
                value=item['text'],
                ts=item['ts']
            )
            memory_map[(item['group_id'], item['variant'])] = (
                result.memory_id
            )
        
        await store.close()
        
        # Add near-equal embeddings
        vec_store = VectorStore(db_path)
        for item in corpus:
            memory_id = memory_map[(item['group_id'], item['variant'])]
            group_id = item['group_id']
            variant_idx = 0 if item['variant'] == 'old' else 1
            vec = create_synthetic_embeddings(
                group_id, variant_idx, dim=384, seed=1337
            )
            vec_store.upsert(
                memory_id=memory_id,
                vec=vec,
                source="full",
                provider="local-sbert",
                model="BAAI/bge-small-en-v1.5"
            )
        
        # Query with recency DISABLED
        os.environ["BARTHO_DB_PATH"] = db_path
        
        config = HybridRetrievalConfig(
            fusion_mode="weighted",
            weight_fts=0.5,
            weight_vec=0.5,
            half_life_hours=0.0  # Disable recency
        )
        
        retriever = get_retriever(mode="hybrid", db_path=db_path)
        retriever.config = config
        
        recent_wins = 0
        for group_id in range(15):
            query = (
                f"User preference for theme configuration in "
                f"group {group_id}. "
                f"Dark mode enabled with accent color blue."
            )
            
            try:
                results = retriever.retrieve(query, top_k=5)
                if not results:
                    continue
                
                # Find positions
                old_id = memory_map.get((group_id, 'old'))
                recent_id = memory_map.get((group_id, 'recent'))
                
                old_pos = None
                recent_pos = None
                
                for pos, result in enumerate(results):
                    if result.memory_id == old_id:
                        old_pos = pos
                    if result.memory_id == recent_id:
                        recent_pos = pos
                
                if old_pos is not None and recent_pos is not None:
                    if recent_pos < old_pos:
                        recent_wins += 1
            except Exception:
                pass
        
        flip_rate = recent_wins / 15
        
        print(f"\nRecency disabled flip rate: {flip_rate:.2%}")
        
        # Without recency, flip rate should NOT be consistently high
        # It should be closer to 50% (random tie-breaking)
        assert flip_rate < 0.80, (
            f"Without recency, flip rate should be <80%, got {flip_rate:.2%}"
        )


if __name__ == "__main__":
    asyncio.run(test_recency_boost_flips_rankings_weighted())
    asyncio.run(test_recency_boost_flips_rankings_rrf())
    asyncio.run(test_recency_disabled_no_flip())
    print("Recency flip integration tests passed!")
