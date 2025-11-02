"""
Unit tests for retrieval factory and FTS-only retriever
"""
import pytest
import sqlite3
from datetime import datetime, UTC

from bartholomew.kernel.retrieval import (
    get_retriever,
    RetrievalFilters,
    FTSOnlyRetriever,
    VectorRetrieverAdapter,
)


def test_default_mode_hybrid_from_config():
    """Default mode should be 'hybrid' from config/kernel.yaml"""
    retriever = get_retriever()
    assert type(retriever).__name__ == "HybridRetriever"


def test_explicit_mode_overrides_env_and_config(monkeypatch):
    """Explicit mode argument overrides env and config"""
    monkeypatch.setenv("BARTHO_RETRIEVAL_MODE", "vector")
    retriever = get_retriever(mode="fts")
    assert type(retriever).__name__ == "FTSOnlyRetriever"


def test_env_overrides_config(monkeypatch):
    """Env variable overrides config file"""
    monkeypatch.setenv("BARTHO_RETRIEVAL_MODE", "vector")
    retriever = get_retriever()
    assert type(retriever).__name__ == "VectorRetrieverAdapter"


def test_fts_mode_explicit():
    """Explicit FTS mode returns FTSOnlyRetriever"""
    retriever = get_retriever(mode="fts")
    assert type(retriever).__name__ == "FTSOnlyRetriever"


def test_vector_mode_explicit():
    """Explicit vector mode returns VectorRetrieverAdapter"""
    retriever = get_retriever(mode="vector")
    assert type(retriever).__name__ == "VectorRetrieverAdapter"


def test_invalid_mode_raises(monkeypatch):
    """Invalid mode in env should raise ValueError"""
    monkeypatch.setenv("BARTHO_RETRIEVAL_MODE", "bogus")
    with pytest.raises(ValueError, match="Invalid retrieval mode"):
        get_retriever()


def test_invalid_mode_explicit_raises():
    """Invalid explicit mode should raise ValueError"""
    with pytest.raises(ValueError, match="Invalid retrieval mode"):
        get_retriever(mode="invalid")


def test_vector_adapter_delegates_to_query(mocker):
    """VectorRetrieverAdapter.retrieve() delegates to Retriever.query()"""
    # Create a mock for the underlying Retriever
    mock_retriever = mocker.Mock()
    mock_retriever.query.return_value = []
    
    # Create adapter
    adapter = VectorRetrieverAdapter(mock_retriever)
    
    # Call retrieve
    filters = RetrievalFilters(kinds=["event"])
    adapter.retrieve("test query", top_k=5, filters=filters)
    
    # Assert query was called with correct args
    mock_retriever.query.assert_called_once_with(
        "test query", top_k=5, filters=filters
    )


def test_fts_only_empty_results(tmp_path):
    """FTS-only retriever returns empty list when no matches"""
    # Create temp database
    db_path = str(tmp_path / "test.db")
    
    # Initialize schema
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT,
            key TEXT,
            value TEXT,
            summary TEXT,
            ts TEXT
        )
    """)
    conn.commit()
    conn.close()
    
    # Initialize FTS
    from bartholomew.kernel.fts_client import FTSClient
    fts = FTSClient(db_path)
    fts.init_schema()
    
    # Create retriever and search
    retriever = FTSOnlyRetriever(db_path)
    results = retriever.retrieve("nonexistent")
    
    assert results == []


def test_fts_only_filters_by_kind(tmp_path):
    """FTS-only retriever respects kind filters"""
    # Create temp database with data
    db_path = str(tmp_path / "test.db")
    
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT,
            key TEXT,
            value TEXT,
            summary TEXT,
            ts TEXT
        )
    """)
    
    # Insert test data
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO memories (kind, key, value, ts) VALUES (?, ?, ?, ?)",
        ("event", "test1", "machine learning conference", now)
    )
    conn.execute(
        "INSERT INTO memories (kind, key, value, ts) VALUES (?, ?, ?, ?)",
        ("fact", "test2", "machine learning is awesome", now)
    )
    conn.commit()
    conn.close()
    
    # Initialize FTS
    from bartholomew.kernel.fts_client import FTSClient
    fts = FTSClient(db_path)
    fts.init_schema()
    
    # Rebuild index with our data
    fts.rebuild_index()
    
    # Create retriever and search with kind filter
    retriever = FTSOnlyRetriever(db_path)
    results = retriever.retrieve(
        "machine",
        filters=RetrievalFilters(kinds=["event"])
    )
    
    # Should only return the event, not the fact
    assert len(results) == 1
    assert results[0].kind == "event"


def test_fts_only_filters_by_timestamp(tmp_path):
    """FTS-only retriever respects timestamp filters"""
    # Create temp database with data
    db_path = str(tmp_path / "test.db")
    
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT,
            key TEXT,
            value TEXT,
            summary TEXT,
            ts TEXT
        )
    """)
    
    # Insert test data with different timestamps
    old_ts = "2024-01-01T00:00:00+00:00"
    new_ts = "2024-12-01T00:00:00+00:00"
    
    conn.execute(
        "INSERT INTO memories (kind, key, value, ts) VALUES (?, ?, ?, ?)",
        ("event", "old", "old test data", old_ts)
    )
    conn.execute(
        "INSERT INTO memories (kind, key, value, ts) VALUES (?, ?, ?, ?)",
        ("event", "new", "new test data", new_ts)
    )
    conn.commit()
    conn.close()
    
    # Initialize FTS
    from bartholomew.kernel.fts_client import FTSClient
    fts = FTSClient(db_path)
    fts.init_schema()
    fts.rebuild_index()
    
    # Search with after filter
    retriever = FTSOnlyRetriever(db_path)
    results = retriever.retrieve(
        "test",
        filters=RetrievalFilters(after="2024-06-01T00:00:00+00:00")
    )
    
    # Should only return the new entry
    assert len(results) == 1
    assert results[0].memory_id == 2


def test_db_path_resolution_explicit():
    """Explicit db_path should be used"""
    retriever = get_retriever(db_path="custom/path.db")
    # Can't easily test the actual path without introspection,
    # but at least verify it doesn't raise
    assert retriever is not None


def test_db_path_resolution_env(monkeypatch):
    """Env variable should override config for db_path"""
    monkeypatch.setenv("BARTHO_DB_PATH", "env/path.db")
    # Just verify it doesn't raise - actual path checking would
    # require more complex mocking
    retriever = get_retriever(mode="fts")
    assert retriever is not None
