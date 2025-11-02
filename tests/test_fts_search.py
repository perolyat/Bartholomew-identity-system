"""
Tests for FTS5 full-text search functionality.
"""
import asyncio
import tempfile
import os
import pytest

from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.fts_client import FTSClient


@pytest.mark.asyncio
async def test_fts_basic_search():
    """Test basic FTS search functionality."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_fts.db")
        
        # Initialize memory store (which creates FTS tables)
        store = MemoryStore(db_path)
        await store.init()
        
        # Insert test memories
        await store.upsert_memory(
            "fact",
            "robot_capabilities",
            "The robot can understand natural language and respond "
            "to queries about privacy and consent",
            "2025-01-01T12:00:00Z"
        )
        
        await store.upsert_memory(
            "fact",
            "ai_ethics",
            "Artificial intelligence systems should prioritize user "
            "privacy and data protection",
            "2025-01-01T12:05:00Z"
        )
        
        await store.upsert_memory(
            "preference",
            "communication_style",
            "User prefers direct communication without excessive "
            "formality or jargon",
            "2025-01-01T12:10:00Z"
        )
        
        # Test FTS search
        fts = FTSClient(db_path)
        
        # Search for "privacy"
        results = fts.search("privacy")
        assert len(results) == 2
        assert any("robot" in r["value"].lower() for r in results)
        assert any("artificial" in r["value"].lower() for r in results)
        
        # Search for "robot"
        results = fts.search("robot")
        assert len(results) == 1
        assert "robot_capabilities" in results[0]["key"]
        
        # Search with boolean operators
        results = fts.search("privacy AND consent")
        assert len(results) == 1
        assert "robot" in results[0]["value"].lower()
        
        # Search with phrase
        results = fts.search('"natural language"')
        assert len(results) == 1
        assert "robot_capabilities" in results[0]["key"]
        
        # Clean up WAL files before directory cleanup
        await store.close()


@pytest.mark.asyncio
async def test_fts_snippet():
    """Test FTS snippet generation with highlighting."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_fts_snippet.db")
        
        # Initialize memory store
        store = MemoryStore(db_path)
        await store.init()
        
        # Insert a test memory
        result = await store.upsert_memory(
            "fact",
            "test_content",
            "The quick brown fox jumps over the lazy dog. "
            "This is a test of the full-text search system.",
            "2025-01-01T12:00:00Z"
        )
        
        # Generate snippet
        fts = FTSClient(db_path)
        snippet = fts.snippet(
            result.memory_id,
            column="value",
            start_mark="**",
            end_mark="**"
        )
        
        # Note: snippet requires an active search query context
        # So this will return the full text if no search is active
        assert snippet is not None
        assert "quick brown fox" in snippet or "test" in snippet
        
        # Clean up WAL files before directory cleanup
        await store.close()


@pytest.mark.asyncio
async def test_fts_update_and_delete():
    """Test FTS index updates when memories are modified or deleted."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_fts_update.db")
        
        # Initialize memory store
        store = MemoryStore(db_path)
        await store.init()
        
        # Insert initial memory
        await store.upsert_memory(
            "fact",
            "test_key",
            "Original content with keyword alpha",
            "2025-01-01T12:00:00Z"
        )
        
        fts = FTSClient(db_path)
        
        # Search for original content
        results = fts.search("alpha")
        assert len(results) == 1
        
        # Update memory
        await store.upsert_memory(
            "fact",
            "test_key",
            "Updated content with keyword beta",
            "2025-01-01T12:05:00Z"
        )
        
        # Old keyword should not be found
        results = fts.search("alpha")
        assert len(results) == 0
        
        # New keyword should be found
        results = fts.search("beta")
        assert len(results) == 1
        
        # Clean up WAL files before directory cleanup
        await store.close()


@pytest.mark.asyncio
async def test_fts_tokenizer_config():
    """Test FTS tokenizer configuration loading."""
    fts = FTSClient(":memory:")
    
    # Default should be 'porter'
    assert fts.tokenizer == "porter"
    
    # Test custom tokenizer
    fts_custom = FTSClient(":memory:", tokenizer="unicode61")
    assert fts_custom.tokenizer == "unicode61"


@pytest.mark.asyncio
async def test_fts_rebuild_index():
    """Test FTS index rebuild functionality."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_fts_rebuild.db")
        
        # Initialize memory store
        store = MemoryStore(db_path)
        await store.init()
        
        # Insert test memories
        await store.upsert_memory(
            "fact", "key1", "Content one", "2025-01-01T12:00:00Z"
        )
        await store.upsert_memory(
            "fact", "key2", "Content two", "2025-01-01T12:01:00Z"
        )
        await store.upsert_memory(
            "fact", "key3", "Content three", "2025-01-01T12:02:00Z"
        )
        
        # Rebuild index
        fts = FTSClient(db_path)
        count = fts.rebuild_index()
        
        # Should have indexed all 3 memories
        assert count == 3
        
        # Verify search still works
        results = fts.search("content")
        assert len(results) == 3


if __name__ == "__main__":
    # Run tests
    asyncio.run(test_fts_basic_search())
    asyncio.run(test_fts_snippet())
    asyncio.run(test_fts_update_and_delete())
    asyncio.run(test_fts_tokenizer_config())
    asyncio.run(test_fts_rebuild_index())
    print("All FTS tests passed!")
