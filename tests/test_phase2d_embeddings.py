"""
Phase 2d: Vector Embeddings Tests
Tests for privacy-first vector embeddings in memory storage
"""
import os
import pytest
import numpy as np
from bartholomew.kernel.embedding_engine import (
    EmbeddingEngine,
    LocalSBERTProvider,
)
from bartholomew.kernel.vector_store import VectorStore
from bartholomew.kernel.retrieval import Retriever
from bartholomew.kernel.memory_rules import MemoryRulesEngine  # noqa: F401


class TestEmbeddingEngine:
    """Test embedding engine core functionality"""
    
    def test_default_config(self):
        """Engine uses safe defaults"""
        engine = EmbeddingEngine()
        
        assert engine.config.provider == "local-sbert"
        assert engine.config.model == "BAAI/bge-small-en-v1.5"
        assert engine.config.dim == 384
    
    def test_embed_texts_returns_correct_shape(self):
        """Embed texts returns correct shape and dtype"""
        engine = EmbeddingEngine()
        texts = ["hello world", "test embedding"]
        
        vecs = engine.embed_texts(texts)
        
        assert vecs.shape == (2, 384)
        assert vecs.dtype == np.float32
    
    def test_embed_texts_normalized(self):
        """Embeddings are L2 normalized"""
        engine = EmbeddingEngine()
        texts = ["test normalization"]
        
        vecs = engine.embed_texts(texts)
        
        norm = np.linalg.norm(vecs[0])
        assert abs(norm - 1.0) < 0.01  # Should be close to 1
    
    def test_embed_empty_list(self):
        """Empty input returns empty array"""
        engine = EmbeddingEngine()
        
        vecs = engine.embed_texts([])
        
        assert vecs.shape == (0, 384)
    
    def test_deterministic_fallback(self):
        """Fallback embedder is deterministic"""
        provider = LocalSBERTProvider(dim=128)
        # Force fallback mode
        provider.fallback = True
        
        vecs1 = provider.embed(["test"])
        vecs2 = provider.embed(["test"])
        
        np.testing.assert_array_equal(vecs1, vecs2)


class TestVectorStore:
    """Test vector store operations"""
    
    def test_upsert_and_search(self, tmp_path):
        """Store and retrieve vectors"""
        from tests.helpers import (
            connect_test_db,
            create_minimal_memories_table,
            insert_test_memory
        )
        
        db_path = str(tmp_path / "test.db")
        
        # Create memories table with test data
        conn = connect_test_db(db_path)
        create_minimal_memories_table(conn)
        mem1_id = insert_test_memory(conn, kind="test", key="key1")
        mem2_id = insert_test_memory(conn, kind="test", key="key2")
        conn.close()
        
        store = VectorStore(db_path)
        
        # Create test vectors
        vec1 = np.random.randn(384).astype(np.float32)
        vec1 = vec1 / np.linalg.norm(vec1)
        vec2 = np.random.randn(384).astype(np.float32)
        vec2 = vec2 / np.linalg.norm(vec2)
        
        # Upsert vectors
        store.upsert(mem1_id, vec1, "summary", "local-sbert", "test-model")
        store.upsert(mem2_id, vec2, "summary", "local-sbert", "test-model")
        
        # Search
        results = store.search(vec1, top_k=2)
        
        assert len(results) == 2
        assert results[0][0] == mem1_id  # Best match is memory_id 1
        assert results[0][1] > 0.9  # High score (similar vector)
    
    def test_delete_for_memory(self, tmp_path):
        """Delete embeddings for a memory"""
        from tests.helpers import (
            connect_test_db,
            create_minimal_memories_table,
            insert_test_memory
        )
        
        db_path = str(tmp_path / "test.db")
        
        # Create memories table with test data
        conn = connect_test_db(db_path)
        create_minimal_memories_table(conn)
        mem_id = insert_test_memory(conn, kind="test", key="key1")
        conn.close()
        
        store = VectorStore(db_path)
        
        vec = np.random.randn(384).astype(np.float32)
        vec = vec / np.linalg.norm(vec)
        
        store.upsert(mem_id, vec, "summary", "local-sbert", "test")
        assert store.count() == 1
        
        store.delete_for_memory(mem_id)
        assert store.count() == 0
    
    def test_source_filter(self, tmp_path):
        """Filter by source (summary vs full)"""
        from tests.helpers import (
            connect_test_db,
            create_minimal_memories_table,
            insert_test_memory
        )
        
        db_path = str(tmp_path / "test.db")
        
        # Create memories table with test data
        conn = connect_test_db(db_path)
        create_minimal_memories_table(conn)
        mem1_id = insert_test_memory(conn, kind="test", key="key1")
        mem2_id = insert_test_memory(conn, kind="test", key="key2")
        conn.close()
        
        store = VectorStore(db_path)
        
        vec = np.random.randn(384).astype(np.float32)
        vec = vec / np.linalg.norm(vec)
        
        store.upsert(mem1_id, vec, "summary", "local-sbert", "test")
        store.upsert(mem2_id, vec, "full", "local-sbert", "test")
        
        # Search only summary
        results = store.search(vec, top_k=10, source="summary")
        assert len(results) == 1
        assert results[0][0] == mem1_id


class TestMemoryStoreIntegration:
    """Integration with kernel memory store"""
    
    @pytest.mark.asyncio
    async def test_embeddings_disabled_by_default(self, tmp_path):
        """Embeddings not stored when disabled"""
        from bartholomew.kernel.memory_store import MemoryStore
        
        # Ensure env var is not set
        os.environ.pop("BARTHO_EMBED_ENABLED", None)
        
        db_path = str(tmp_path / "test.db")
        store = MemoryStore(db_path)
        await store.init()
        
        await store.upsert_memory(
            kind="test",
            key="test1",
            value="Test content",
            ts="2024-01-01T00:00:00Z"
        )
        
        # Check no embeddings stored
        vec_store = VectorStore(db_path)
        assert vec_store.count() == 0
    
    @pytest.mark.asyncio
    async def test_embeddings_with_env_and_rule(
        self, tmp_path, monkeypatch
    ):
        """Embeddings stored when enabled via env + rule"""
        from bartholomew.kernel.memory_store import MemoryStore
        from bartholomew.kernel.memory_rules import MemoryRulesEngine
        
        # Enable embeddings
        monkeypatch.setenv("BARTHO_EMBED_ENABLED", "1")
        
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
            
            long_value = "This is a test content. " * 50
            
            await store.upsert_memory(
                kind="test",
                key="test1",
                value=long_value,
                ts="2024-01-01T00:00:00Z"
            )
            
            # Check embeddings were stored
            vec_store = VectorStore(db_path)
            assert vec_store.count() > 0
        finally:
            memory_rules._rules_engine.evaluate = original_evaluate
            monkeypatch.delenv("BARTHO_EMBED_ENABLED", raising=False)


class TestRetriever:
    """Test retrieval engine"""
    
    def test_retriever_basic_query(self, tmp_path):
        """Basic retrieval query"""
        from bartholomew.kernel.memory_store import MemoryStore
        from tests.helpers import (
            connect_test_db,
            create_minimal_memories_table,
            insert_test_memory
        )
        
        db_path = str(tmp_path / "test.db")
        
        # Create memories table with test data
        conn = connect_test_db(db_path)
        create_minimal_memories_table(conn)
        mem_id = insert_test_memory(
            conn, kind="test", key="key1", value="hello world"
        )
        conn.close()
        
        # Set up vector store with test data
        vec_store = VectorStore(db_path)
        engine = EmbeddingEngine()
        
        # Create test embedding
        test_vec = engine.embed_texts(["hello world"])[0]
        vec_store.upsert(mem_id, test_vec, "summary", "local-sbert", "test")
        
        # Create retriever
        rules_engine = MemoryRulesEngine()
        mem_store = MemoryStore(db_path)
        
        retriever = Retriever(
            rules_engine=rules_engine,
            vector_store=vec_store,
            embedding_engine=engine,
            memory_store=mem_store
        )
        
        # Query (may not return results without actual memories)
        results = retriever.query("hello world", top_k=5)
        
        # Should not crash
        assert isinstance(results, list)


class TestEmbedStoreDefaults:
    """Test embed_store defaulting behavior"""
    
    @pytest.mark.asyncio
    async def test_embed_store_defaults_to_true_when_embed_set(
        self, tmp_path, monkeypatch
    ):
        """embed_store defaults to True when embed != 'none'"""
        from bartholomew.kernel.memory_store import MemoryStore
        
        # Enable embeddings
        monkeypatch.setenv("BARTHO_EMBED_ENABLED", "1")
        
        # Mock rules: embed='summary' but NO embed_store key
        def mock_evaluate(memory_dict):
            return {
                "allow_store": True,
                "embed": "summary",  # Set but no embed_store
                # embed_store is MISSING - should default to True
                "summary_enabled": True,  # Enable summarization
                "summary_mode": "summary_also",
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
            
            long_value = "This is test content. " * 50
            
            await store.upsert_memory(
                kind="test",
                key="test1",
                value=long_value,
                ts="2024-01-01T00:00:00Z"
            )
            
            # Should have embeddings despite no explicit embed_store
            from bartholomew.kernel.vector_store import VectorStore
            vec_store = VectorStore(db_path)
            assert vec_store.count() > 0, (
                "embed_store should default to True when embed != 'none'"
            )
        finally:
            memory_rules._rules_engine.evaluate = original_evaluate
            monkeypatch.delenv("BARTHO_EMBED_ENABLED", raising=False)


class TestForeignKeyCascade:
    """Test FK cascade deletion"""
    
    @pytest.mark.asyncio
    async def test_deleting_memory_removes_embeddings(
        self, tmp_path, monkeypatch
    ):
        """Deleting a memory cascades to remove its embeddings"""
        from bartholomew.kernel.memory_store import MemoryStore
        from bartholomew.kernel.vector_store import VectorStore
        import aiosqlite
        
        # Enable embeddings
        monkeypatch.setenv("BARTHO_EMBED_ENABLED", "1")
        
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
            
            # Create memory with embeddings
            await store.upsert_memory(
                kind="test",
                key="test1",
                value="Test content. " * 50,
                ts="2024-01-01T00:00:00Z"
            )
            
            # Verify embeddings exist
            vec_store = VectorStore(db_path)
            assert vec_store.count() > 0
            
            # Delete the memory
            async with aiosqlite.connect(db_path) as db:
                await db.execute("PRAGMA foreign_keys = ON")
                await db.execute(
                    "DELETE FROM memories WHERE kind=? AND key=?",
                    ("test", "test1")
                )
                await db.commit()
            
            # Embeddings should be cascade deleted
            assert vec_store.count() == 0, (
                "FK cascade should delete embeddings when memory deleted"
            )
        finally:
            memory_rules._rules_engine.evaluate = original_evaluate
            monkeypatch.delenv("BARTHO_EMBED_ENABLED", raising=False)


class TestLifecycleMethods:
    """Test persist_embeddings_for and reembed_memory"""
    
    @pytest.mark.asyncio
    async def test_persist_embeddings_for_after_consent(
        self, tmp_path, monkeypatch
    ):
        """Persist embeddings after consent granted"""
        from bartholomew.kernel.memory_store import MemoryStore
        from bartholomew.kernel.vector_store import VectorStore
        
        # Enable embeddings
        monkeypatch.setenv("BARTHO_EMBED_ENABLED", "1")
        
        # Mock rules to block initial storage
        def mock_evaluate_no_store(memory_dict):
            return {
                "allow_store": True,
                "embed": "summary",
                "embed_store": False,  # Blocked initially
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
            
            # Create memory (no embeddings due to embed_store=False)
            await store.upsert_memory(
                kind="test",
                key="test1",
                value="Test content. " * 50,
                ts="2024-01-01T00:00:00Z"
            )
            
            vec_store = VectorStore(db_path)
            assert vec_store.count() == 0
            
            # Now grant consent and persist embeddings
            import aiosqlite
            async with aiosqlite.connect(db_path) as db:
                cursor = await db.execute(
                    "SELECT id FROM memories WHERE kind=? AND key=?",
                    ("test", "test1")
                )
                row = await cursor.fetchone()
                memory_id = row[0]
            
            # Change rules to allow storage
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
            
            # Persist embeddings
            count = await store.persist_embeddings_for(memory_id)
            
            assert count > 0
            assert vec_store.count() > 0
        finally:
            memory_rules._rules_engine.evaluate = original_evaluate
            monkeypatch.delenv("BARTHO_EMBED_ENABLED", raising=False)
    
    @pytest.mark.asyncio
    async def test_reembed_memory_replaces_vectors(
        self, tmp_path, monkeypatch
    ):
        """Re-embed replaces existing vectors"""
        from bartholomew.kernel.memory_store import MemoryStore
        from bartholomew.kernel.vector_store import VectorStore
        
        # Enable embeddings
        monkeypatch.setenv("BARTHO_EMBED_ENABLED", "1")
        
        # Mock rules
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
            
            # Create memory with embeddings
            await store.upsert_memory(
                kind="test",
                key="test1",
                value="Original content. " * 50,
                ts="2024-01-01T00:00:00Z"
            )
            
            vec_store = VectorStore(db_path)
            initial_count = vec_store.count()
            assert initial_count > 0
            
            # Get memory ID
            import aiosqlite
            async with aiosqlite.connect(db_path) as db:
                cursor = await db.execute(
                    "SELECT id FROM memories WHERE kind=? AND key=?",
                    ("test", "test1")
                )
                row = await cursor.fetchone()
                memory_id = row[0]
            
            # Re-embed (simulating summary change)
            count = await store.reembed_memory(memory_id)
            
            assert count > 0
            # Count should be same (replaced, not added)
            assert vec_store.count() == initial_count
        finally:
            memory_rules._rules_engine.evaluate = original_evaluate
            monkeypatch.delenv("BARTHO_EMBED_ENABLED", raising=False)


class TestConfigAndRules:
    """Test configuration and rules integration"""
    
    def test_embeddings_yaml_exists(self):
        """embeddings.yaml config file exists"""
        config_path = os.path.join(
            "bartholomew", "config", "embeddings.yaml"
        )
        assert os.path.exists(config_path)
    
    def test_embeddings_yaml_structure(self):
        """embeddings.yaml has expected structure"""
        import yaml
        config_path = os.path.join(
            "bartholomew", "config", "embeddings.yaml"
        )
        
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        assert "embeddings" in config
        emb_config = config["embeddings"]
        
        assert "default_provider" in emb_config
        assert "default_model" in emb_config
        assert "default_dim" in emb_config
        assert emb_config["default_provider"] == "local-sbert"
        assert emb_config["default_dim"] == 384


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
