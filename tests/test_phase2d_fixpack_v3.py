"""
Phase 2d Fix Pack v3 Tests
Tests for summary fallback, retrieval boost, and strict model matching
"""
import pytest
import numpy as np
from bartholomew.kernel.embedding_engine import EmbeddingEngine
from bartholomew.kernel.vector_store import VectorStore
from bartholomew.kernel.retrieval import Retriever
from bartholomew.kernel.memory_rules import MemoryRulesEngine
from tests.helpers import (
    connect_test_db,
    create_minimal_memories_table,
    insert_test_memory
)


class TestSummaryFallback:
    """Test summary-missing fallback in memory_store"""
    
    @pytest.mark.asyncio
    async def test_embed_falls_back_when_summary_missing(
        self, tmp_path, monkeypatch
    ):
        """Embeddings use redacted content fallback when summary missing"""
        from bartholomew.kernel.memory_store import MemoryStore
        
        # Enable embeddings
        monkeypatch.setenv("BARTHO_EMBED_ENABLED", "1")
        
        # Mock summarizer to return empty
        from bartholomew.kernel import summarization_engine
        original_should = summarization_engine._summarization_engine.should_summarize  # noqa: E501
        original_summarize = summarization_engine._summarization_engine.summarize  # noqa: E501
        
        def mock_should(evaluated, value, kind):
            return True  # Say we should summarize
        
        def mock_summarize(value):
            return ""  # But return empty
        
        summarization_engine._summarization_engine.should_summarize = mock_should  # noqa: E501
        summarization_engine._summarization_engine.summarize = mock_summarize  # noqa: E501
        
        # Mock rules to enable embedding
        def mock_evaluate(memory_dict):
            return {
                "allow_store": True,
                "embed": "summary",
                "embed_store": True,
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
            
            # Create memory with content
            content = "Test content. " * 50
            await store.upsert_memory(
                kind="test",
                key="test1",
                value=content,
                ts="2024-01-01T00:00:00Z"
            )
            
            # Check embeddings were stored (fallback to content)
            from bartholomew.kernel.vector_store import VectorStore
            vec_store = VectorStore(db_path)
            assert vec_store.count() > 0, (
                "Should have embeddings despite missing summary "
                "(fallback to content)"
            )
        finally:
            summarization_engine._summarization_engine.should_summarize = original_should  # noqa: E501
            summarization_engine._summarization_engine.summarize = original_summarize  # noqa: E501
            memory_rules._rules_engine.evaluate = original_evaluate
            monkeypatch.delenv("BARTHO_EMBED_ENABLED", raising=False)


class TestRetrievalBoost:
    """Test retrieval.boost scoring multiplier"""
    
    def test_boost_affects_ranking(self, tmp_path):
        """Retrieval boost multiplies scores and affects ranking"""
        from bartholomew.kernel.memory_store import MemoryStore
        
        db_path = str(tmp_path / "test.db")
        
        # Create memories table
        conn = connect_test_db(db_path)
        create_minimal_memories_table(conn)
        
        # Insert two memories
        mem1_id = insert_test_memory(
            conn, kind="normal", key="key1", value="hello world"
        )
        mem2_id = insert_test_memory(
            conn, kind="boosted", key="key2", value="hello world"
        )
        conn.close()
        
        # Create embeddings with similar vectors
        vec_store = VectorStore(db_path)
        engine = EmbeddingEngine()
        
        vec1 = engine.embed_texts(["hello world"])[0]
        vec2 = engine.embed_texts(["hello world"])[0]
        
        vec_store.upsert(
            mem1_id, vec1, "summary", "local-sbert", "test-model"
        )
        vec_store.upsert(
            mem2_id, vec2, "summary", "local-sbert", "test-model"
        )
        
        # Mock rules to apply boost to mem2
        def mock_evaluate(memory_dict):
            boost = 2.0 if memory_dict.get("kind") == "boosted" else 1.0
            return {
                "allow_store": True,
                "retrieval": {"boost": boost},
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
            # Create retriever
            rules_engine = MemoryRulesEngine()
            mem_store = MemoryStore(db_path)
            
            retriever = Retriever(
                rules_engine=rules_engine,
                vector_store=vec_store,
                embedding_engine=engine,
                memory_store=mem_store
            )
            
            # Query
            results = retriever.query("hello world", top_k=2)
            
            # Boosted memory should rank first
            assert len(results) == 2
            assert results[0].kind == "boosted", (
                "Boosted memory should rank first"
            )
            assert results[0].score > results[1].score, (
                "Boosted score should be higher"
            )
        finally:
            memory_rules._rules_engine.evaluate = original_evaluate


class TestStrictModelMatching:
    """Test strict provider/model/dim matching in search"""
    
    def test_strict_excludes_mismatches(self, tmp_path):
        """Search excludes vectors with different provider/model/dim"""
        db_path = str(tmp_path / "test.db")
        
        # Create memories table
        conn = connect_test_db(db_path)
        create_minimal_memories_table(conn)
        
        mem1_id = insert_test_memory(conn, kind="test", key="key1")
        mem2_id = insert_test_memory(conn, kind="test", key="key2")
        conn.close()
        
        # Create vector store and insert with different providers
        vec_store = VectorStore(db_path)
        
        vec = np.random.randn(384).astype(np.float32)
        vec = vec / np.linalg.norm(vec)
        
        vec_store.upsert(
            mem1_id, vec, "summary", "local-sbert", "model-a"
        )
        vec_store.upsert(
            mem2_id, vec, "summary", "openai", "model-b"
        )
        
        # Search with strict provider/model
        results = vec_store.search(
            vec,
            top_k=10,
            provider="local-sbert",
            model="model-a",
            dim=384,
            allow_mismatch=False
        )
        
        # Should only return mem1
        assert len(results) == 1
        assert results[0][0] == mem1_id
    
    def test_allow_mismatch_includes_all(self, tmp_path):
        """allow_mismatch=True includes vectors from different providers"""
        db_path = str(tmp_path / "test.db")
        
        # Create memories table
        conn = connect_test_db(db_path)
        create_minimal_memories_table(conn)
        
        mem1_id = insert_test_memory(conn, kind="test", key="key1")
        mem2_id = insert_test_memory(conn, kind="test", key="key2")
        conn.close()
        
        # Create vector store
        vec_store = VectorStore(db_path)
        
        vec = np.random.randn(384).astype(np.float32)
        vec = vec / np.linalg.norm(vec)
        
        vec_store.upsert(
            mem1_id, vec, "summary", "local-sbert", "model-a"
        )
        vec_store.upsert(
            mem2_id, vec, "summary", "openai", "model-b"
        )
        
        # Search with allow_mismatch
        results = vec_store.search(
            vec,
            top_k=10,
            provider="local-sbert",
            model="model-a",
            dim=384,
            allow_mismatch=True
        )
        
        # Should return both
        assert len(results) == 2


class TestPolicyFlags:
    """Test policy_flags in RetrievedItem"""
    
    def test_context_only_flag_set(self, tmp_path):
        """policy_flags includes 'context_only' when policy matches"""
        from bartholomew.kernel.memory_store import MemoryStore
        
        db_path = str(tmp_path / "test.db")
        
        # Create memory
        conn = connect_test_db(db_path)
        create_minimal_memories_table(conn)
        mem_id = insert_test_memory(
            conn, kind="context", key="key1", value="test content"
        )
        conn.close()
        
        # Create embedding
        vec_store = VectorStore(db_path)
        engine = EmbeddingEngine()
        vec = engine.embed_texts(["test content"])[0]
        vec_store.upsert(
            mem_id, vec, "summary", "local-sbert", "test-model"
        )
        
        # Mock rules to return context_only policy
        def mock_evaluate(memory_dict):
            return {
                "allow_store": True,
                "recall_policy": "context_only",
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
            # Create retriever
            rules_engine = MemoryRulesEngine()
            mem_store = MemoryStore(db_path)
            
            retriever = Retriever(
                rules_engine=rules_engine,
                vector_store=vec_store,
                embedding_engine=engine,
                memory_store=mem_store
            )
            
            # Query
            results = retriever.query("test content", top_k=1)
            
            assert len(results) == 1
            assert "context_only" in results[0].policy_flags
            assert results[0].context_only is True
        finally:
            memory_rules._rules_engine.evaluate = original_evaluate


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
