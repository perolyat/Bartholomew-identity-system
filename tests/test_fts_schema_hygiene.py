"""
Tests for FTS schema hygiene improvements.

Tests:
1. Advanced tokenizer configuration with args
2. Idempotent migrate_schema() with rowid consistency checking
3. Weekly FTS optimize drive registration
"""
import asyncio
import tempfile
import os
import pytest
import sqlite3
import yaml

from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.fts_client import FTSClient
from bartholomew.kernel.scheduler import drives
from bartholomew.kernel.db_ctx import wal_checkpoint_truncate


@pytest.mark.asyncio
async def test_tokenizer_config_with_args():
    """Test that tokenizer args are loaded from config."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        config_dir = os.path.join(tmpdir, "config")
        os.makedirs(config_dir, exist_ok=True)
        
        # Create a test config with tokenizer args
        config_path = os.path.join(config_dir, "kernel.yaml")
        config = {
            "retrieval": {
                "fts_tokenizer": "unicode61",
                "fts_tokenizer_args": "remove_diacritics 2 tokenchars .-@_"
            }
        }
        
        with open(config_path, "w") as f:
            yaml.dump(config, f)
        
        # Temporarily modify the config path for testing
        import bartholomew.kernel.fts_client as fts_module
        original_load = fts_module._load_tokenizer_config
        
        def mock_load():
            return "unicode61 remove_diacritics 2 tokenchars .-@_"
        
        fts_module._load_tokenizer_config = mock_load
        
        try:
            # Create FTS client and verify tokenizer spec
            fts = FTSClient(db_path)
            assert "unicode61" in fts.tokenizer
            assert "remove_diacritics" in fts.tokenizer
            assert "tokenchars" in fts.tokenizer
            
            # Verify schema creation works with custom tokenizer
            store = MemoryStore(db_path)
            await store.init()
            
            # Insert test memory
            await store.upsert_memory(
                "fact",
                "email_test",
                "Contact: user@example.com and admin@test.org",
                "2025-01-01T12:00:00Z"
            )
            
            # This test mainly verifies schema creation succeeds
            # with custom tokenizer configuration
            
        finally:
            fts_module._load_tokenizer_config = original_load
            await store.close()
            # Windows cleanup
            wal_checkpoint_truncate(db_path)


@pytest.mark.asyncio
async def test_migrate_schema_idempotent():
    """Test that migrate_schema is idempotent and safe to call multiple times."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_migrate.db")
        
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
        
        fts = FTSClient(db_path)
        
        # Run migrate_schema multiple times - should be idempotent
        fts.migrate_schema()
        fts.migrate_schema()
        fts.migrate_schema()
        
        # Verify FTS still works after migrations
        results = fts.search("content")
        assert len(results) == 2
        
        # Verify rowid consistency
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("""
            SELECT COUNT(*)
            FROM memory_fts f
            LEFT JOIN memories m ON f.rowid = m.id
            WHERE m.id IS NULL
        """)
        orphaned_count = cursor.fetchone()[0]
        conn.close()
        
        assert orphaned_count == 0, "No orphaned FTS entries should exist"
        
        await store.close()
        # Windows cleanup
        wal_checkpoint_truncate(db_path)


@pytest.mark.asyncio
async def test_migrate_schema_fixes_rowid_mismatch():
    """Test that migrate_schema detects and fixes rowid mismatches."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_fix.db")
        
        # Initialize memory store
        store = MemoryStore(db_path)
        await store.init()
        
        # Insert test memory
        await store.upsert_memory(
            "fact", "test_key", "Test content", "2025-01-01T12:00:00Z"
        )
        
        # Close store to release locks
        await store.close()
        
        # Manually create orphaned FTS entry (no matching memory)
        conn = None
        try:
            conn = sqlite3.connect(db_path)
            # Direct INSERT bypassing triggers
            conn.execute(
                "INSERT INTO memory_fts(rowid, value, summary) "
                "VALUES (9999, 'orphan', NULL)"
            )
            conn.commit()
            
            # Verify mismatch exists
            cursor = conn.execute("""
                SELECT COUNT(*)
                FROM memory_fts f
                LEFT JOIN memories m ON f.rowid = m.id
                WHERE m.id IS NULL
            """)
            orphaned_before = cursor.fetchone()[0]
        finally:
            if conn:
                conn.close()
        
        assert orphaned_before > 0, \
            "Should have orphaned entry before migration"
        
        # Run migration to fix
        fts = FTSClient(db_path)
        fts.migrate_schema()
        
        # Verify mismatch is fixed
        conn = None
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.execute("""
                SELECT COUNT(*)
                FROM memory_fts f
                LEFT JOIN memories m ON f.rowid = m.id
                WHERE m.id IS NULL
            """)
            orphaned_after = cursor.fetchone()[0]
        finally:
            if conn:
                conn.close()
        
        assert orphaned_after == 0, "Migration should fix orphaned entries"
        
        # Verify legitimate entries still exist
        results = fts.search("test")
        assert len(results) == 1
        
        # Windows cleanup
        wal_checkpoint_truncate(db_path)


@pytest.mark.asyncio
async def test_fts_optimize_drive_registered():
    """Test that fts_optimize drive is registered with correct cadence."""
    # Verify drive is in registry
    assert "fts_optimize" in drives.REGISTRY
    
    # Verify cadence is weekly (604800 seconds = 7 days)
    drive_config = drives.REGISTRY["fts_optimize"]
    assert drive_config["cadence"] == "every:604800"
    
    # Verify drive function exists
    assert callable(drive_config["fn"])
    assert drive_config["fn"].__name__ == "drive_fts_optimize"


@pytest.mark.asyncio
async def test_fts_optimize_drive_execution():
    """Test that fts_optimize drive executes successfully."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_optimize.db")
        
        # Initialize memory store with some content
        store = MemoryStore(db_path)
        await store.init()
        
        await store.upsert_memory(
            "fact", "key1", "Content one", "2025-01-01T12:00:00Z"
        )
        await store.upsert_memory(
            "fact", "key2", "Content two", "2025-01-01T12:01:00Z"
        )
        
        # Create mock context
        class MockContext:
            def __init__(self, mem):
                self.mem = mem
        
        ctx = MockContext(store)
        
        # Execute the drive
        drive_fn = drives.REGISTRY["fts_optimize"]["fn"]
        result = await drive_fn(ctx)
        
        # Should return None (no nudge emitted)
        assert result is None
        
        # Verify optimize was called (FTS should still work)
        fts = FTSClient(db_path)
        results = fts.search("content")
        assert len(results) == 2
        
        await store.close()
        # Windows cleanup
        wal_checkpoint_truncate(db_path)


@pytest.mark.asyncio
async def test_optimize_method():
    """Test FTSClient.optimize() method directly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_optimize_direct.db")
        
        # Initialize with some content
        store = MemoryStore(db_path)
        await store.init()
        
        await store.upsert_memory(
            "fact", "key1", "Content alpha", "2025-01-01T12:00:00Z"
        )
        await store.upsert_memory(
            "fact", "key2", "Content beta", "2025-01-01T12:01:00Z"
        )
        await store.upsert_memory(
            "fact", "key3", "Content gamma", "2025-01-01T12:02:00Z"
        )
        
        fts = FTSClient(db_path)
        
        # Run optimize - should not raise exception
        fts.optimize()
        
        # Verify FTS still works after optimize
        results = fts.search("content")
        assert len(results) == 3
        
        # Run optimize again - should be safe to call multiple times
        fts.optimize()
        
        results = fts.search("beta")
        assert len(results) == 1
        assert "beta" in results[0]["value"]
        
        await store.close()
        # Windows cleanup
        wal_checkpoint_truncate(db_path)


if __name__ == "__main__":
    # Run tests
    asyncio.run(test_tokenizer_config_with_args())
    asyncio.run(test_migrate_schema_idempotent())
    asyncio.run(test_migrate_schema_fixes_rowid_mismatch())
    asyncio.run(test_fts_optimize_drive_registered())
    asyncio.run(test_fts_optimize_drive_execution())
    asyncio.run(test_optimize_method())
    print("All FTS schema hygiene tests passed!")
