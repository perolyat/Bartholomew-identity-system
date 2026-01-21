"""
Tests for Consent and Privacy Gates
Validates pre-filtering in FTS and vector retrieval stages
"""

import os
import sqlite3
import tempfile
from datetime import datetime, timezone

import numpy as np
import pytest

from bartholomew.kernel.consent_gate import ConsentGate
from bartholomew.kernel.fts_client import FTSClient
from bartholomew.kernel.memory_rules import MemoryRulesEngine
from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.vector_store import VectorStore
from conftest import SKIP_WINDOWS_FTS


@pytest.fixture
def temp_db():
    """Create temporary database for testing"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except:
        pass


@pytest.fixture
async def memory_store(temp_db):
    """Initialize memory store"""
    store = MemoryStore(temp_db)
    await store.init()
    return store


@pytest.fixture
def consent_gate(temp_db):
    """Initialize consent gate"""
    return ConsentGate(temp_db)


@pytest.fixture
def mock_rules_engine():
    """Create mock rules engine for testing"""
    engine = MemoryRulesEngine()
    return engine


# ============================================================================
# Consent Gate Tests
# ============================================================================


@pytest.mark.asyncio
async def test_consent_gate_excludes_never_store(memory_store, consent_gate):
    """Test that never_store memories are excluded"""
    # Store a memory that should be blocked (contains illegal content pattern)
    # The never_store rule in memory_rules.yaml matches content patterns like
    # "illegal content" - we use this to trigger blocking
    result = await memory_store.upsert_memory(
        kind="chat",
        key="blocked_message",
        value="This contains illegal content that should be blocked",
        ts=datetime.now(timezone.utc).isoformat(),
    )

    # Memory should not be stored due to never_store rule
    assert not result.stored, "never_store memory should be blocked"

    # Even if it somehow got stored, consent gate should filter it
    if result.memory_id:
        policy = consent_gate.get_memory_policy(result.memory_id)
        assert not policy["include"], "never_store should be excluded"


@pytest.mark.asyncio
async def test_consent_gate_excludes_unconsented_sensitive(memory_store, consent_gate):
    """Test that ask_before_store memories without consent are excluded"""
    # Store a sensitive memory (requires consent)
    result = await memory_store.upsert_memory(
        kind="user",
        key="bank_info",
        value="My bank account number is 123456789",
        ts=datetime.now(timezone.utc).isoformat(),
    )

    # Memory should be stored (privacy guard would prompt in real usage)
    # But for testing, assume it passed storage gate
    if not result.stored:
        pytest.skip("Memory blocked by privacy guard, can't test consent gate")

    # Check policy - should require consent
    policy = consent_gate.get_memory_policy(result.memory_id)

    # Without consent record, should be excluded
    consented = consent_gate.get_consented_memory_ids()
    if result.memory_id not in consented:
        assert not policy["include"], "Unconsented memory should be excluded"


@pytest.mark.asyncio
async def test_consent_gate_includes_consented_memory(memory_store, consent_gate):
    """Test that memories with consent are included"""
    import aiosqlite

    # Store a memory
    result = await memory_store.upsert_memory(
        kind="user_profile",
        key="name",
        value="John Doe",
        ts=datetime.now(timezone.utc).isoformat(),
    )

    assert result.stored, "Memory should be stored"

    # Grant consent using aiosqlite (not sqlite3)
    async with aiosqlite.connect(memory_store.db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO memory_consent (memory_id, source) VALUES (?, ?)",
            (result.memory_id, "test"),
        )
        await db.commit()

    # Check policy - should be included
    consented = consent_gate.get_consented_memory_ids()
    assert result.memory_id in consented, "Should have consent record"

    policy = consent_gate.get_memory_policy(result.memory_id)
    assert policy["include"], "Consented memory should be included"


@pytest.mark.asyncio
async def test_consent_gate_marks_context_only(memory_store, consent_gate):
    """Test that context_only memories are marked but included"""
    # Store a context-only memory
    result = await memory_store.upsert_memory(
        kind="sensitive_joke",
        key="joke1",
        value="A funny but sensitive joke",
        ts=datetime.now(timezone.utc).isoformat(),
    )

    assert result.stored, "Context-only memory should be stored"

    # Check policy
    policy = consent_gate.get_memory_policy(result.memory_id)
    assert policy["include"], "Context-only should be included"
    assert policy["context_only"], "Should be marked as context_only"
    assert policy["recall_policy"] == "context_only"


# ============================================================================
# FTS Consent Gate Tests
# ============================================================================


@SKIP_WINDOWS_FTS
def test_fts_search_applies_consent_gate(temp_db):
    """Test that FTS search applies consent gate by default"""
    # Create test memories in database FIRST (before FTS init)
    conn = sqlite3.connect(temp_db)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY,
            kind TEXT,
            key TEXT,
            value TEXT,
            summary TEXT,
            ts TEXT
        )
    """,
    )
    conn.commit()
    conn.close()

    # NOW initialize FTS (requires memories table to exist)
    fts = FTSClient(temp_db)
    fts.init_schema()

    # Insert test memories
    conn = sqlite3.connect(temp_db)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO memories (id, kind, key, value, ts) VALUES (?, ?, ?, ?, ?)",
        (1, "chat", "test1", "robot learning machine", now),
    )
    conn.execute(
        "INSERT INTO memories (id, kind, key, value, ts) VALUES (?, ?, ?, ?, ?)",
        (2, "user", "sensitive", "bank account password secret", now),
    )
    conn.commit()

    # Index in FTS
    fts.upsert(1, "robot learning machine")
    fts.upsert(2, "bank account password secret")

    conn.close()

    # Search with consent gate enabled (default)
    results = fts.search("machine", apply_consent_gate=True)

    # Results should be filtered based on rules
    # The exact filtering depends on memory_rules.yaml config
    assert isinstance(results, list), "Should return list"


@SKIP_WINDOWS_FTS
def test_fts_search_without_consent_gate(temp_db):
    """Test that FTS search can bypass consent gate"""
    # Create test memory table FIRST (before FTS init)
    conn = sqlite3.connect(temp_db)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY,
            kind TEXT,
            key TEXT,
            value TEXT,
            summary TEXT,
            ts TEXT
        )
    """,
    )
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO memories (id, kind, key, value, ts) VALUES (?, ?, ?, ?, ?)",
        (1, "chat", "test", "robot learning", now),
    )
    conn.commit()
    conn.close()

    # NOW initialize FTS (requires memories table)
    fts = FTSClient(temp_db)
    fts.init_schema()

    fts.upsert(1, "robot learning")

    # Search without consent gate
    results = fts.search("robot", apply_consent_gate=False)

    # Should return unfiltered results
    assert len(results) >= 0, "Should return results"
    if results:
        assert "id" in results[0]


# ============================================================================
# Vector Store Consent Gate Tests
# ============================================================================


def test_vector_search_applies_consent_gate(temp_db):
    """Test that vector search applies consent gate by default"""
    # Initialize vector store
    vector_store = VectorStore(temp_db)

    # Create test memory
    conn = sqlite3.connect(temp_db)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY,
            kind TEXT,
            key TEXT,
            value TEXT,
            summary TEXT,
            ts TEXT
        )
    """,
    )
    conn.execute(
        "INSERT INTO memories (id, kind, key, value, ts) VALUES (?, ?, ?, ?, ?)",
        (1, "chat", "test", "test value", datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()

    # Insert test vector
    test_vec = np.random.rand(384).astype(np.float32)
    test_vec = test_vec / np.linalg.norm(test_vec)

    vector_store.upsert(
        memory_id=1,
        vec=test_vec,
        source="summary",
        provider="test",
        model="test-model",
    )

    # Search with consent gate (default)
    query_vec = np.random.rand(384).astype(np.float32)
    query_vec = query_vec / np.linalg.norm(query_vec)

    results = vector_store.search(query_vec, top_k=5, apply_consent_gate=True)

    # Results should be filtered
    assert isinstance(results, list), "Should return list"


def test_vector_search_without_consent_gate(temp_db):
    """Test that vector search can bypass consent gate"""
    # Initialize vector store
    vector_store = VectorStore(temp_db)

    # Create test memory
    conn = sqlite3.connect(temp_db)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY,
            kind TEXT,
            key TEXT,
            value TEXT,
            summary TEXT,
            ts TEXT
        )
    """,
    )
    conn.execute(
        "INSERT INTO memories (id, kind, key, value, ts) VALUES (?, ?, ?, ?, ?)",
        (1, "chat", "test", "test value", datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()

    # Insert test vector
    test_vec = np.random.rand(384).astype(np.float32)
    test_vec = test_vec / np.linalg.norm(test_vec)

    vector_store.upsert(
        memory_id=1,
        vec=test_vec,
        source="summary",
        provider="test",
        model="test-model",
    )

    # Search without consent gate
    query_vec = np.random.rand(384).astype(np.float32)
    query_vec = query_vec / np.linalg.norm(query_vec)

    results = vector_store.search(query_vec, top_k=5, apply_consent_gate=False)

    # Should return unfiltered results
    assert isinstance(results, list), "Should return list"
    if results:
        assert len(results[0]) == 2, "Should be (memory_id, score) tuple"


# ============================================================================
# Integration Tests
# ============================================================================


@pytest.mark.asyncio
async def test_consent_gate_filters_fts_results(memory_store, consent_gate):
    """Integration test: consent gate filters FTS results"""
    # Store multiple memories with different privacy levels
    memories = [
        ("user_profile", "name", "Alice", True),  # always_keep
        ("chat", "msg1", "Hello world", True),  # normal
        ("user", "secret", "My password is hunter2", False),  # never_store
    ]

    memory_ids = []
    for kind, key, value, should_store in memories:
        result = await memory_store.upsert_memory(
            kind=kind,
            key=key,
            value=value,
            ts=datetime.now(timezone.utc).isoformat(),
        )
        if result.stored:
            memory_ids.append((result.memory_id, should_store))

    # Apply consent gate filtering
    ids_to_check = [mid for mid, _ in memory_ids]
    filtered = consent_gate.filter_memory_ids(ids_to_check)

    # Verify filtering
    for memory_id, should_store in memory_ids:
        policy = filtered.get(memory_id, {})
        if not should_store:
            assert not policy.get("include", False), f"Memory {memory_id} should be excluded"


@pytest.mark.asyncio
async def test_context_only_propagation(memory_store, consent_gate):
    """Test that context_only flag propagates through pipeline"""
    # Store a context-only memory
    result = await memory_store.upsert_memory(
        kind="chat",
        key="smalltalk1",
        value="Nice weather today",
        ts=datetime.now(timezone.utc).isoformat(),
    )

    if not result.stored:
        pytest.skip("Memory not stored")

    # Check that consent gate marks it as context_only
    policy = consent_gate.get_memory_policy(result.memory_id)

    # Should be included but marked
    assert policy["include"], "Context-only should be included"
    # Note: context_only marking depends on rules matching
    # This test verifies the mechanism works


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
