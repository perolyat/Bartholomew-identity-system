#!/usr/bin/env python3
"""
Smoke test for FTS backfill script
"""
import asyncio
import os
import sys
import tempfile

import pytest

from bartholomew.kernel.memory_store import MemoryStore
from scripts.backfill_fts import backfill_fts


# Add scripts to path for direct import
sys.path.insert(0, os.path.dirname(__file__))


@pytest.mark.asyncio
async def test_backfill_smoke():
    """Test backfill script with sample data"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_backfill.db")

        # Create test database with sample memories
        print("Creating test database...")
        store = MemoryStore(db_path)
        await store.init()

        # Insert test memories
        await store.upsert_memory(
            "fact",
            "test_key_1",
            "The robot can understand natural language",
            "2025-01-01T12:00:00Z",
        )

        await store.upsert_memory(
            "preference",
            "test_key_2",
            "User prefers direct communication without jargon",
            "2025-01-01T12:05:00Z",
        )

        await store.upsert_memory(
            "fact",
            "test_key_3",
            "AI systems should prioritize privacy and consent",
            "2025-01-01T12:10:00Z",
        )

        await store.close()
        print("Test database created with 3 memories")

        # Test dry-run mode
        print("\nRunning backfill in dry-run mode...")
        exit_code = backfill_fts(
            db_path=db_path,
            batch_size=10,
            optimize=False,
            dry_run=True,
            verbose=False,
        )

        if exit_code != 0:
            print(f"❌ Dry-run failed with exit code {exit_code}")
            return False

        print("✓ Dry-run completed successfully")

        # Test actual backfill
        print("\nRunning actual backfill...")
        exit_code = backfill_fts(
            db_path=db_path,
            batch_size=10,
            optimize=True,
            dry_run=False,
            verbose=False,
        )

        if exit_code != 0:
            print(f"❌ Backfill failed with exit code {exit_code}")
            return False

        print("✓ Backfill completed successfully")

        # Verify FTS index works
        print("\nVerifying FTS search...")
        from bartholomew.kernel.fts_client import FTSClient

        fts = FTSClient(db_path)

        results = fts.search("robot")
        if len(results) != 1:
            print(f"❌ Expected 1 result for 'robot', got {len(results)}")
            return False

        results = fts.search("privacy")
        if len(results) != 1:
            print(f"❌ Expected 1 result for 'privacy', got {len(results)}")
            return False

        print("✓ FTS search working correctly")

        # Close all database connections for proper cleanup on Windows
        import gc

        gc.collect()

        return True


if __name__ == "__main__":
    success = asyncio.run(test_backfill_smoke())
    if success:
        print("\n" + "=" * 60)
        print("✓ All smoke tests passed!")
        print("=" * 60)
        sys.exit(0)
    else:
        print("\n" + "=" * 60)
        print("❌ Smoke tests failed")
        print("=" * 60)
        sys.exit(1)
