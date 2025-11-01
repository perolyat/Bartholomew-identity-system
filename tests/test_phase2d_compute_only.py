"""
Phase 2d Compute-only Embeddings Tests
Tests for ephemeral (non-persisted) embedding computation
"""
import pytest
import numpy as np
from bartholomew.kernel.memory_store import MemoryStore, StoreResult
from bartholomew.kernel.vector_store import VectorStore


class TestComputeOnly:
    """Test compute-only (ephemeral) embedding behavior"""
    
    @pytest.mark.asyncio
    async def test_embed_compute_only_does_not_persist_rows_and_returns_vectors(
        self, tmp_path, monkeypatch
    ):
        """Embeddings computed but not persisted when embed_store=False"""
        # Enable embeddings
        monkeypatch.setenv("BARTHO_EMBED_ENABLED", "1")
        monkeypatch.setenv("BARTHO_EMBED_RELOAD", "0")
        
        # Mock rules: embed=summary, embed_store=False
        def mock_evaluate(memory_dict):
            return {
                "allow_store": True,
                "embed": "summary",
                "embed_store": False,  # Compute-only
                "kind": memory_dict.get("kind"),
                "key": memory_dict.get("key"),
                "content": memory_dict.get("value"),
                "matched_categories": [],
                "matched_rules": [],
            }
        
        from bartholomew.kernel import memory_rules
        original_evaluate = memory_rules._rules_engine.evaluate
        memory_rules._rules_engine.evaluate = mock_evaluate
        
        try:
            db_path = str(tmp_path / "test.db")
            store = MemoryStore(db_path)
            await store.init()
            
            # Create memory
            content = "Test content. " * 50
            result = await store.upsert_memory(
                kind="test",
                key="test1",
                value=content,
                ts="2024-01-01T00:00:00Z"
            )
            
            # Check result
            assert isinstance(result, StoreResult)
            assert result.stored is True
            assert result.memory_id is not None
            assert len(result.ephemeral_embeddings) == 1
            
            # Verify vector properties
            src, vec = result.ephemeral_embeddings[0]
            assert src == "summary"
            assert vec.dtype == np.float32
            assert vec.ndim == 1
            assert vec.shape[0] == 384  # Default dim
            assert np.isclose(np.linalg.norm(vec), 1.0, atol=1e-5)
            
            # Verify NOT persisted to database
            vec_store = VectorStore(db_path)
            assert vec_store.count() == 0, (
                "Vectors should not be persisted when embed_store=False"
            )
            
            # Verify memory is not retrievable via vector search
            from bartholomew.kernel.retrieval import Retriever
            from bartholomew.kernel.embedding_engine import get_embedding_engine
            
            retriever = Retriever(
                rules_engine=memory_rules._rules_engine,
                vector_store=vec_store,
                embedding_engine=get_embedding_engine(),
                memory_store=store
            )
            
            results = retriever.query("test content", top_k=5)
            assert len(results) == 0, (
                "Memory should not be retrievable via vector search"
            )
        finally:
            memory_rules._rules_engine.evaluate = original_evaluate
            monkeypatch.delenv("BARTHO_EMBED_ENABLED", raising=False)
            monkeypatch.delenv("BARTHO_EMBED_RELOAD", raising=False)
    
    @pytest.mark.asyncio
    async def test_embed_compute_only_with_both_sources_returns_two_vectors(
        self, tmp_path, monkeypatch
    ):
        """Compute-only with embed=both returns two vectors"""
        monkeypatch.setenv("BARTHO_EMBED_ENABLED", "1")
        monkeypatch.setenv("BARTHO_EMBED_RELOAD", "0")
        
        # Mock rules: embed=both, embed_store=False
        def mock_evaluate(memory_dict):
            return {
                "allow_store": True,
                "embed": "both",
                "embed_store": False,
                "kind": memory_dict.get("kind"),
                "key": memory_dict.get("key"),
                "content": memory_dict.get("value"),
                "matched_categories": [],
                "matched_rules": [],
            }
        
        from bartholomew.kernel import memory_rules
        original_evaluate = memory_rules._rules_engine.evaluate
        memory_rules._rules_engine.evaluate = mock_evaluate
        
        try:
            db_path = str(tmp_path / "test.db")
            store = MemoryStore(db_path)
            await store.init()
            
            content = "Test content. " * 50
            result = await store.upsert_memory(
                kind="test",
                key="test1",
                value=content,
                ts="2024-01-01T00:00:00Z"
            )
            
            # Should have two ephemeral embeddings
            assert len(result.ephemeral_embeddings) == 2
            sources = [src for src, _ in result.ephemeral_embeddings]
            assert "summary" in sources
            assert "full" in sources
            
            # No persistence
            vec_store = VectorStore(db_path)
            assert vec_store.count() == 0
        finally:
            memory_rules._rules_engine.evaluate = original_evaluate
            monkeypatch.delenv("BARTHO_EMBED_ENABLED", raising=False)
            monkeypatch.delenv("BARTHO_EMBED_RELOAD", raising=False)
    
    @pytest.mark.asyncio
    async def test_compute_only_then_persist_after_consent(
        self, tmp_path, monkeypatch
    ):
        """Compute-only first, then persist after consent granted"""
        monkeypatch.setenv("BARTHO_EMBED_ENABLED", "1")
        monkeypatch.setenv("BARTHO_EMBED_RELOAD", "0")
        
        # Mock rules: initially embed_store=False
        def mock_evaluate_no_store(memory_dict):
            return {
                "allow_store": True,
                "embed": "summary",
                "embed_store": False,
                "kind": memory_dict.get("kind"),
                "key": memory_dict.get("key"),
                "content": memory_dict.get("value"),
                "matched_categories": [],
                "matched_rules": [],
            }
        
        from bartholomew.kernel import memory_rules
        original_evaluate = memory_rules._rules_engine.evaluate
        memory_rules._rules_engine.evaluate = mock_evaluate_no_store
        
        try:
            db_path = str(tmp_path / "test.db")
            store = MemoryStore(db_path)
            await store.init()
            
            # Create memory (compute-only)
            content = "Test content. " * 50
            result = await store.upsert_memory(
                kind="test",
                key="test1",
                value=content,
                ts="2024-01-01T00:00:00Z"
            )
            
            assert len(result.ephemeral_embeddings) == 1
            vec_store = VectorStore(db_path)
            assert vec_store.count() == 0
            
            # Now grant consent and persist
            def mock_evaluate_allow(memory_dict):
                return {
                    "allow_store": True,
                    "embed": "summary",
                    "embed_store": True,  # Now allowed
                    "kind": memory_dict.get("kind"),
                    "key": memory_dict.get("key"),
                    "content": memory_dict.get("value"),
                    "matched_categories": [],
                    "matched_rules": [],
                }
            
            memory_rules._rules_engine.evaluate = mock_evaluate_allow
            
            count = await store.persist_embeddings_for(result.memory_id)
            
            assert count > 0
            assert vec_store.count() > 0
        finally:
            memory_rules._rules_engine.evaluate = original_evaluate
            monkeypatch.delenv("BARTHO_EMBED_ENABLED", raising=False)
            monkeypatch.delenv("BARTHO_EMBED_RELOAD", raising=False)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
