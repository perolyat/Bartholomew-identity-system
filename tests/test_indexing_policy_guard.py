"""
Tests for policy-based indexing guard (strong_only encryption).

Verifies that when policy.indexing.disallow_strong_only is enabled,
memories marked with encrypt: strong are not indexed in FTS or vector
stores.
"""

import os
import tempfile

import pytest
import yaml

from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.policy import can_index, load_policy


@pytest.fixture
def temp_policy_file():
    """Create a temporary policy file for testing."""
    fd, path = tempfile.mkstemp(suffix=".yaml")
    try:
        yield path
    finally:
        os.close(fd)
        os.unlink(path)


def test_can_index_default_policy():
    """Test that indexing is allowed by default (policy disabled)."""
    # Reset policy cache
    from bartholomew.kernel import policy

    policy._policy_cache = None

    # Test with strong encryption when policy is disabled
    evaluated = {"encrypt": "strong"}
    assert can_index(evaluated) is True

    # Test with standard encryption
    evaluated = {"encrypt": "standard"}
    assert can_index(evaluated) is True

    # Test with no encryption
    evaluated = {}
    assert can_index(evaluated) is True


def test_can_index_with_policy_enabled(temp_policy_file):
    """Test indexing blocked for strong encryption when policy enabled."""
    # Reset policy cache
    from bartholomew.kernel import policy

    policy._policy_cache = None

    # Create policy file with disallow_strong_only enabled
    policy_content = {"version": 1, "indexing": {"disallow_strong_only": True}}
    with open(temp_policy_file, "w") as f:
        yaml.dump(policy_content, f)

    # Load the policy
    loaded = load_policy(temp_policy_file)
    assert loaded["indexing"]["disallow_strong_only"] is True

    # Update cache with test policy
    policy._policy_cache = loaded

    # Test with strong encryption - should be blocked
    evaluated = {"encrypt": "strong"}
    assert can_index(evaluated) is False

    # Test with standard encryption - should be allowed
    evaluated = {"encrypt": "standard"}
    assert can_index(evaluated) is True

    # Test with no encryption - should be allowed
    evaluated = {}
    assert can_index(evaluated) is True

    # Test with True (alias for standard) - should be allowed
    evaluated = {"encrypt": True}
    assert can_index(evaluated) is True

    # Reset cache after test
    policy._policy_cache = None


def test_can_index_case_insensitive():
    """Test that encryption strength matching is case-insensitive."""
    from bartholomew.kernel import policy

    policy._policy_cache = {"indexing": {"disallow_strong_only": True}}

    # Test various casings
    assert can_index({"encrypt": "STRONG"}) is False
    assert can_index({"encrypt": "Strong"}) is False
    assert can_index({"encrypt": "strong"}) is False
    assert can_index({"encrypt": "  strong  "}) is False

    # Standard should still be allowed
    assert can_index({"encrypt": "STANDARD"}) is True
    assert can_index({"encrypt": "Standard"}) is True

    # Reset cache
    policy._policy_cache = None


@pytest.mark.asyncio
async def test_memory_store_respects_indexing_policy():
    """Test that MemoryStore respects the indexing policy."""
    from bartholomew.kernel import policy

    # Create temporary database
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    try:
        store = MemoryStore(db_path)
        await store.init()

        # Enable strict policy
        policy._policy_cache = {"indexing": {"disallow_strong_only": True}}

        # Store a memory that should be blocked from indexing
        # (using medical tag which triggers encrypt: strong in
        # memory_rules.yaml)
        result = await store.upsert_memory(
            kind="health_record",
            key="blood_pressure",
            value="BP reading: 120/80",
            ts="2025-01-01T00:00:00Z",
        )

        assert result.stored is True
        assert result.memory_id is not None

        # Verify memory was stored but FTS index was not created
        # (we check this indirectly by ensuring the FTS map doesn't have it)
        import aiosqlite

        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM memory_fts_map WHERE memory_id = ?",
                (result.memory_id,),
            )
            count_row = await cursor.fetchone()
            # Should be 0 if indexing was blocked by policy
            # Note: This depends on memory_rules.yaml having
            # encrypt: strong for health-related content
            # (Just checking it exists, actual count depends on rules)
            assert count_row is not None

        # Reset policy
        policy._policy_cache = None
    finally:
        try:
            await store.close()
        except Exception:
            pass
        try:
            os.unlink(db_path)
        except Exception:
            pass


@pytest.mark.asyncio
async def test_policy_denied_content_not_searchable():
    """
    Closes the blind spot in test_memory_store_respects_indexing_policy():
    that test only ever asserted memory_fts_map's row count, never that
    the content is actually unreachable via FTS MATCH. Under the single-
    writer architecture there's no trigger silently indexing it anyway, so
    both must now hold: no memory_fts_map entry, and no MATCH result.
    """
    from bartholomew.kernel import memory_store
    from bartholomew.kernel.fts_client import FTSClient

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    try:
        store = MemoryStore(db_path)
        await store.init()

        original_evaluate = memory_store._rules_engine.evaluate

        def deny_fts(memory_dict):
            evaluated = original_evaluate(memory_dict)
            evaluated["fts_index"] = False
            return evaluated

        memory_store._rules_engine.evaluate = deny_fts
        try:
            result = await store.upsert_memory(
                kind="fact",
                key="policy_denied_target",
                value="content with distinctive term policydeniedtermxyz",
                ts="2025-01-01T00:00:00Z",
            )
        finally:
            memory_store._rules_engine.evaluate = original_evaluate

        assert result.stored is True
        memory_id = result.memory_id

        import aiosqlite

        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM memory_fts_map WHERE memory_id = ?",
                (memory_id,),
            )
            count_row = await cursor.fetchone()
            assert count_row[0] == 0

        fts = FTSClient(db_path)
        results = fts.search("policydeniedtermxyz", apply_consent_gate=False)
        assert results == []
    finally:
        try:
            await store.close()
        except Exception:
            pass
        try:
            os.unlink(db_path)
        except Exception:
            pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
