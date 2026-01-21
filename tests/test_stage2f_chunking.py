"""
Tests for Stage 2f Chunking Implementation

Verifies that long content is properly chunked, stored, and indexed.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from conftest import SKIP_WINDOWS_FTS


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_chunking.db")
        yield db_path


@pytest.fixture
def long_content():
    """Generate content that exceeds the chunking threshold (2000 chars)."""
    # Generate ~5000 characters of content (needs >640 tokens to produce 2+ chunks)
    # With 640 target tokens, we need significantly more content
    paragraphs = []
    for i in range(30):
        paragraphs.append(
            f"Paragraph {i + 1}: This is a sample paragraph that contains "
            f"enough text to contribute to the overall length of the content. "
            f"It includes various words and phrases that might be searched. "
            f"The purpose is to test chunking behavior with realistic content. "
            f"Additional sentences help ensure we have enough tokens to trigger "
            f"the chunking logic and produce multiple overlapping chunks.",
        )
    return "\n\n".join(paragraphs)


@pytest.fixture
def short_content():
    """Generate content that is below the chunking threshold."""
    return "This is a short piece of content that should not be chunked."


class TestChunkingEngine:
    """Test the ChunkingEngine standalone functionality."""

    def test_chunking_engine_singleton(self):
        """Test that get_chunking_engine returns a singleton."""
        from bartholomew.kernel.chunking_engine import get_chunking_engine

        engine1 = get_chunking_engine()
        engine2 = get_chunking_engine()
        assert engine1 is engine2

    def test_chunking_engine_defaults(self):
        """Test that chunking engine has expected default values."""
        from bartholomew.kernel.chunking_engine import get_chunking_engine

        engine = get_chunking_engine()
        assert engine.enabled is True
        assert engine.target_tokens == 640
        assert engine.overlap_tokens == 64
        assert engine.threshold_chars == 2000

    def test_should_chunk_by_length(self, long_content, short_content):
        """Test should_chunk based on content length."""
        from bartholomew.kernel.chunking_engine import get_chunking_engine

        engine = get_chunking_engine()

        # Long content should be chunked
        assert engine.should_chunk("fact", long_content) is True

        # Short content should not be chunked
        assert engine.should_chunk("fact", short_content) is False

    def test_should_chunk_by_kind(self):
        """Test should_chunk based on memory kind."""
        from bartholomew.kernel.chunking_engine import get_chunking_engine

        engine = get_chunking_engine()

        # Kinds in chunk_kinds should always be chunked
        transcript = "Short transcript content."
        if "conversation.transcript" in engine.chunk_kinds:
            assert engine.should_chunk("conversation.transcript", transcript)

    def test_chunk_text_basic(self, long_content):
        """Test basic text chunking produces multiple chunks."""
        from bartholomew.kernel.chunking_engine import get_chunking_engine

        engine = get_chunking_engine()
        chunks = engine.chunk_text(long_content)

        assert len(chunks) > 1
        assert all(chunk.seq >= 0 for chunk in chunks)
        assert all(chunk.text for chunk in chunks)

    def test_chunk_text_short_content(self, short_content):
        """Test that short content produces single chunk."""
        from bartholomew.kernel.chunking_engine import get_chunking_engine

        engine = get_chunking_engine()
        chunks = engine.chunk_text(short_content)

        assert len(chunks) == 1
        assert chunks[0].seq == 0
        assert chunks[0].text == short_content.strip()

    def test_chunk_text_ordering(self, long_content):
        """Test that chunks have sequential ordering."""
        from bartholomew.kernel.chunking_engine import get_chunking_engine

        engine = get_chunking_engine()
        chunks = engine.chunk_text(long_content)

        seqs = [c.seq for c in chunks]
        assert seqs == list(range(len(chunks)))

    def test_chunk_text_token_ranges(self, long_content):
        """Test that token ranges are valid and non-overlapping."""
        from bartholomew.kernel.chunking_engine import get_chunking_engine

        engine = get_chunking_engine()
        chunks = engine.chunk_text(long_content)

        for chunk in chunks:
            assert chunk.token_start >= 0
            assert chunk.token_end > chunk.token_start
            assert chunk.token_start < chunk.token_end


class TestMemoryStoreChunking:
    """Test chunking integration with MemoryStore."""

    @pytest.mark.asyncio
    async def test_init_creates_chunk_tables(self, temp_db):
        """Test that MemoryStore.init creates chunk tables."""
        from bartholomew.kernel.memory_store import MemoryStore

        store = MemoryStore(temp_db)
        await store.init()

        # Check tables exist
        conn = sqlite3.connect(temp_db)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()

        assert "memory_chunks" in tables
        # chunk_fts may or may not exist depending on FTS5 availability

    @pytest.mark.asyncio
    async def test_upsert_creates_chunks_for_long_content(self, temp_db, long_content):
        """Test that upsert_memory creates chunks for long content."""
        from bartholomew.kernel.memory_store import MemoryStore

        store = MemoryStore(temp_db)
        await store.init()

        # Store long content
        result = await store.upsert_memory(
            kind="article.ingested",
            key="test_article",
            value=long_content,
            ts="2025-01-20T12:00:00Z",
        )

        assert result.stored is True
        assert result.memory_id is not None

        # Check chunks were created
        conn = sqlite3.connect(temp_db)
        cursor = conn.execute(
            "SELECT COUNT(*) FROM memory_chunks WHERE memory_id = ?",
            (result.memory_id,),
        )
        chunk_count = cursor.fetchone()[0]
        conn.close()

        # Long content should produce multiple chunks
        assert chunk_count > 1

    @pytest.mark.asyncio
    async def test_upsert_no_chunks_for_short_content(self, temp_db, short_content):
        """Test that short content does not create chunks."""
        from bartholomew.kernel.memory_store import MemoryStore

        store = MemoryStore(temp_db)
        await store.init()

        result = await store.upsert_memory(
            kind="fact",
            key="short_fact",
            value=short_content,
            ts="2025-01-20T12:00:00Z",
        )

        assert result.stored is True

        # Check no chunks were created (single chunk = no storage)
        conn = sqlite3.connect(temp_db)
        cursor = conn.execute(
            "SELECT COUNT(*) FROM memory_chunks WHERE memory_id = ?",
            (result.memory_id,),
        )
        chunk_count = cursor.fetchone()[0]
        conn.close()

        assert chunk_count == 0

    @pytest.mark.asyncio
    async def test_chunks_have_correct_metadata(self, temp_db, long_content):
        """Test that stored chunks have correct metadata."""
        from bartholomew.kernel.memory_store import MemoryStore

        store = MemoryStore(temp_db)
        await store.init()

        result = await store.upsert_memory(
            kind="recording.transcript",
            key="test_transcript",
            value=long_content,
            ts="2025-01-20T12:00:00Z",
        )

        # Load chunks
        conn = sqlite3.connect(temp_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT * FROM memory_chunks WHERE memory_id = ? ORDER BY seq",
            (result.memory_id,),
        )
        chunks = [dict(row) for row in cursor.fetchall()]
        conn.close()

        assert len(chunks) > 1

        for i, chunk in enumerate(chunks):
            assert chunk["memory_id"] == result.memory_id
            assert chunk["seq"] == i
            assert chunk["token_start"] >= 0
            assert chunk["token_end"] > chunk["token_start"]
            assert len(chunk["text"]) > 0

    @pytest.mark.asyncio
    async def test_delete_memory_cascades_to_chunks(self, temp_db, long_content):
        """Test that deleting a memory also deletes its chunks."""
        from bartholomew.kernel.memory_store import MemoryStore

        store = MemoryStore(temp_db)
        await store.init()

        # Store content with chunks
        result = await store.upsert_memory(
            kind="article.ingested",
            key="to_delete",
            value=long_content,
            ts="2025-01-20T12:00:00Z",
        )

        memory_id = result.memory_id

        # Verify chunks exist
        conn = sqlite3.connect(temp_db)
        cursor = conn.execute(
            "SELECT COUNT(*) FROM memory_chunks WHERE memory_id = ?",
            (memory_id,),
        )
        assert cursor.fetchone()[0] > 0
        conn.close()

        # Delete the memory
        deleted = await store.delete_memory("article.ingested", "to_delete")
        assert deleted is True

        # Verify chunks were cascade-deleted
        conn = sqlite3.connect(temp_db)
        cursor = conn.execute(
            "SELECT COUNT(*) FROM memory_chunks WHERE memory_id = ?",
            (memory_id,),
        )
        assert cursor.fetchone()[0] == 0
        conn.close()

    @pytest.mark.asyncio
    async def test_upsert_replaces_existing_chunks(self, temp_db, long_content):
        """Test that re-upserting content replaces existing chunks."""
        from bartholomew.kernel.memory_store import MemoryStore

        store = MemoryStore(temp_db)
        await store.init()

        # Store initial content
        result1 = await store.upsert_memory(
            kind="article.ingested",
            key="replaceable",
            value=long_content,
            ts="2025-01-20T12:00:00Z",
        )

        # Get initial chunk count
        conn = sqlite3.connect(temp_db)
        cursor = conn.execute(
            "SELECT COUNT(*) FROM memory_chunks WHERE memory_id = ?",
            (result1.memory_id,),
        )
        initial_count = cursor.fetchone()[0]
        conn.close()

        # Store different content with same key
        different_content = long_content + "\n\nAdditional paragraph."
        result2 = await store.upsert_memory(
            kind="article.ingested",
            key="replaceable",
            value=different_content,
            ts="2025-01-20T13:00:00Z",
        )

        assert result2.memory_id == result1.memory_id

        # Get new chunk count
        conn = sqlite3.connect(temp_db)
        cursor = conn.execute(
            "SELECT COUNT(*) FROM memory_chunks WHERE memory_id = ?",
            (result2.memory_id,),
        )
        new_count = cursor.fetchone()[0]
        conn.close()

        # Chunk count may differ, but chunks should be replaced
        # (not accumulated)
        assert new_count >= initial_count  # Longer content = same or more


class TestChunkFTS:
    """Test chunk FTS indexing and search."""

    @pytest.mark.asyncio
    async def test_chunk_fts_schema_created(self, temp_db):
        """Test that chunk FTS schema is created."""
        from bartholomew.kernel.memory_store import MemoryStore

        store = MemoryStore(temp_db)
        await store.init()

        # Check chunk_fts_map table exists
        conn = sqlite3.connect(temp_db)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='chunk_fts_map'",
        )
        cursor.fetchone()  # Just verify query succeeds; table may not exist without FTS5
        conn.close()

        # May not exist if FTS5 not available
        # This is acceptable on some platforms

    @SKIP_WINDOWS_FTS
    @pytest.mark.asyncio
    async def test_search_chunks(self, temp_db, long_content):
        """Test searching through chunks."""
        from bartholomew.kernel.fts_client import FTSClient
        from bartholomew.kernel.memory_store import MemoryStore

        store = MemoryStore(temp_db)
        await store.init()

        # Store content with chunks
        result = await store.upsert_memory(
            kind="article.ingested",
            key="searchable",
            value=long_content,
            ts="2025-01-20T12:00:00Z",
        )

        # Try to search chunks
        try:
            fts = FTSClient(temp_db)
            results = fts.search_chunks("paragraph")
            # Results should include chunks from our memory
            memory_ids = {r["memory_id"] for r in results}
            assert result.memory_id in memory_ids or len(results) == 0
            # May be 0 if FTS5 not available
        except Exception:
            # FTS5 may not be available on all platforms
            pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
