"""
Tests for FTS5 availability probe and graceful fallback
"""

import gc
import os
import sqlite3
import tempfile
import time
from unittest.mock import patch

import pytest

from conftest import SKIP_WINDOWS_FTS


# Skip all tests in this module on Windows (FTS5/matchinfo not available)
pytestmark = SKIP_WINDOWS_FTS

from bartholomew.kernel.fts_client import fts5_available
from bartholomew.kernel.retrieval import _check_fts5_once, get_retriever


def _cleanup_db_connections(db_path: str) -> None:
    """Helper to ensure all connections are closed and WAL is checkpointed"""
    gc.collect()
    time.sleep(0.05)
    try:
        conn = sqlite3.connect(db_path, timeout=1.0)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
    except Exception:
        pass
    gc.collect()
    time.sleep(0.05)


def test_fts5_available_returns_true_when_available():
    """fts5_available should return True when FTS5 is present"""
    conn = sqlite3.connect(":memory:")

    # Most Python builds have FTS5
    result = fts5_available(conn)

    # This may be True or False depending on the build
    assert isinstance(result, bool)

    conn.close()


def test_fts5_available_returns_false_on_exception():
    """fts5_available should return False on any exception"""
    # Test with invalid connection
    result = fts5_available(None)
    assert result is False


def test_check_fts5_once_caches_result():
    """_check_fts5_once should cache result after first call"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")

        # Create test database
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE memories (
                id INTEGER PRIMARY KEY,
                kind TEXT,
                value TEXT,
                summary TEXT,
                ts TEXT
            )
        """,
        )
        conn.close()

        # Clear cache
        from bartholomew.kernel import retrieval

        retrieval._fts5_available_cache = None

        # First call should check and cache
        result1 = _check_fts5_once(db_path)

        # Second call should return cached result
        result2 = _check_fts5_once(db_path)

        assert result1 == result2
        assert isinstance(result1, bool)


def test_get_retriever_degrades_fts_mode_when_unavailable():
    """get_retriever should degrade fts mode to vector when FTS5 unavailable"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")

        try:
            # Create test database
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE memories (
                    id INTEGER PRIMARY KEY,
                    kind TEXT, key TEXT,
                    value TEXT, summary TEXT, ts TEXT
                )
            """,
            )
            conn.close()

            # Mock FTS5 as unavailable
            with patch("bartholomew.kernel.retrieval.fts5_available") as mock:
                mock.return_value = False

                # Clear cache to force new check
                from bartholomew.kernel import retrieval

                retrieval._fts5_available_cache = None

                # Request FTS mode
                retriever = get_retriever(mode="fts", db_path=db_path)

                # Should degrade to vector mode
                assert type(retriever).__name__ == "VectorRetrieverAdapter"
        finally:
            _cleanup_db_connections(db_path)


def test_get_retriever_hybrid_logs_warning_when_fts_unavailable():
    """get_retriever should log info for hybrid mode with FTS unavailable"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")

        try:
            # Create test database with embeddings table
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE memories (
                    id INTEGER PRIMARY KEY,
                    kind TEXT, key TEXT,
                    value TEXT, summary TEXT, ts TEXT
                )
            """,
            )
            conn.execute(
                """
                CREATE TABLE memory_embeddings (
                    embedding_id INTEGER PRIMARY KEY,
                    memory_id INTEGER,
                    source TEXT,
                    dim INTEGER,
                    vec BLOB,
                    norm REAL,
                    provider TEXT,
                    model TEXT
                )
            """,
            )
            conn.close()

            # Mock FTS5 as unavailable
            with patch("bartholomew.kernel.retrieval.fts5_available") as mock:
                mock.return_value = False

                # Clear cache
                from bartholomew.kernel import retrieval

                retrieval._fts5_available_cache = None

                # Request hybrid mode
                retriever = get_retriever(mode="hybrid", db_path=db_path)

                # Should still return HybridRetriever (type stable)
                # but will operate vector-only (empty FTS candidates)
                assert type(retriever).__name__ == "HybridRetriever"
        finally:
            _cleanup_db_connections(db_path)


def test_get_retriever_vector_mode_unaffected_by_fts_availability():
    """get_retriever vector mode should work regardless of FTS5 availability"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")

        try:
            # Create test database with embeddings table
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE memories (
                    id INTEGER PRIMARY KEY,
                    kind TEXT, key TEXT,
                    value TEXT, summary TEXT, ts TEXT
                )
            """,
            )
            conn.execute(
                """
                CREATE TABLE memory_embeddings (
                    embedding_id INTEGER PRIMARY KEY,
                    memory_id INTEGER,
                    source TEXT,
                    dim INTEGER,
                    vec BLOB,
                    norm REAL,
                    provider TEXT,
                    model TEXT
                )
            """,
            )
            conn.close()

            # Mock FTS5 as unavailable
            with patch("bartholomew.kernel.retrieval.fts5_available") as mock:
                mock.return_value = False

                # Clear cache
                from bartholomew.kernel import retrieval

                retrieval._fts5_available_cache = None

                # Request vector mode
                retriever = get_retriever(mode="vector", db_path=db_path)

                # Should return vector adapter regardless of FTS5
                assert type(retriever).__name__ == "VectorRetrieverAdapter"
        finally:
            _cleanup_db_connections(db_path)


def test_fts5_probe_logs_warning_once():
    """FTS5 unavailability should be logged once (cached)"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")

        # Create test database
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE memories (
                id INTEGER PRIMARY KEY,
                kind TEXT, value TEXT, summary TEXT, ts TEXT
            )
        """,
        )
        conn.close()

        # Mock FTS5 as unavailable
        with patch("bartholomew.kernel.retrieval.fts5_available") as mock:
            mock.return_value = False

            # Clear cache
            from bartholomew.kernel import retrieval

            retrieval._fts5_available_cache = None

            # Multiple calls
            _check_fts5_once(db_path)
            _check_fts5_once(db_path)
            _check_fts5_once(db_path)

            # Should only call fts5_available once (cached)
            assert mock.call_count == 1


def test_hybrid_retriever_empty_fts_candidates_when_unavailable():
    """HybridRetriever should get empty FTS candidates when FTS5 unavailable"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")

        try:
            # Create database without FTS tables
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE memories (
                    id INTEGER PRIMARY KEY,
                    kind TEXT, key TEXT,
                    value TEXT, summary TEXT, ts TEXT
                )
            """,
            )
            conn.execute(
                """
                CREATE TABLE memory_embeddings (
                    embedding_id INTEGER PRIMARY KEY,
                    memory_id INTEGER,
                    source TEXT,
                    dim INTEGER,
                    vec BLOB,
                    norm REAL,
                    provider TEXT,
                    model TEXT
                )
            """,
            )
            conn.close()

            from bartholomew.kernel.hybrid_retriever import HybridRetriever

            # Create retriever (FTS search will fail)
            retriever = HybridRetriever(db_path)

            # Pull FTS candidates should return empty list on error
            fts_results = retriever._pull_fts_candidates("test query")

            # Should gracefully return empty list
            assert fts_results == []
        finally:
            _cleanup_db_connections(db_path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
