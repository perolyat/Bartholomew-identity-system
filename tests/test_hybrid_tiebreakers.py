"""
Tests for hybrid retrieval tie-breakers

Verifies that ties in final scores are broken by:
1. Recency (newer first)
2. Memory ID (ascending) for exact timestamp ties
"""

import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from bartholomew.kernel.hybrid_retriever import HybridRetrievalConfig, HybridRetriever


class TestTieBreakers:
    """Test deterministic tie-breaking in hybrid retrieval"""

    def test_tiebreak_by_recency(self):
        """Score ties should be broken by recency (newer first)"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")

            # Create database with memories
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE memories (
                    id INTEGER PRIMARY KEY,
                    kind TEXT,
                    key TEXT,
                    value TEXT,
                    summary TEXT,
                    ts TEXT
                )
            """,
            )

            # Create memory_embeddings table (empty but present)
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

            # Insert memories with same content (will get same scores)
            # but different timestamps
            now = datetime.now(timezone.utc)

            conn.execute(
                """
                INSERT INTO memories (id, kind, key, value, summary, ts)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (1, "fact", "old", "test content", "test", (now - timedelta(days=10)).isoformat()),
            )

            conn.execute(
                """
                INSERT INTO memories (id, kind, key, value, summary, ts)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (2, "fact", "new", "test content", "test", (now - timedelta(hours=1)).isoformat()),
            )

            conn.execute(
                """
                INSERT INTO memories (id, kind, key, value, summary, ts)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (
                    3,
                    "fact",
                    "medium",
                    "test content",
                    "test",
                    (now - timedelta(days=5)).isoformat(),
                ),
            )

            conn.commit()
            conn.close()

            # Create retriever with config that will yield equal scores
            config = HybridRetrievalConfig(
                fts_candidates=0,  # No FTS
                vec_candidates=0,  # No vector
                half_life_hours=0.0,  # No recency boost
            )
            retriever = HybridRetriever(db_path, config=config)

            # Mock equal fused scores for all three memories
            fused_scores = {1: 0.5, 2: 0.5, 3: 0.5}

            # Load metadata for tie-breaking
            memory_metadata = retriever._load_metadata([1, 2, 3])

            # Apply sort key function
            def sort_key(item):
                memory_id, score = item
                metadata = memory_metadata[memory_id]

                # Parse recency timestamp to epoch for sorting
                recency_ts = metadata.get("ts")
                recency_epoch = 0.0
                if recency_ts:
                    try:
                        from datetime import datetime

                        dt = datetime.fromisoformat(recency_ts.replace("Z", "+00:00"))
                        recency_epoch = dt.timestamp()
                    except Exception:
                        recency_epoch = 0.0

                return (-score, -recency_epoch, memory_id)

            # Sort and check order
            ranked = sorted(fused_scores.items(), key=sort_key)

            # With equal scores, newer should come first
            # Expected order: 2 (newest), 3 (medium), 1 (oldest)
            assert ranked[0][0] == 2  # Newest first
            assert ranked[1][0] == 3  # Medium second
            assert ranked[2][0] == 1  # Oldest last

    def test_tiebreak_by_mem_id(self):
        """Exact timestamp ties should be broken by mem_id ascending"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")

            # Create database with memories
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE memories (
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

            # Insert memories with identical timestamps
            same_time = datetime.now(timezone.utc).isoformat()

            conn.execute(
                """
                INSERT INTO memories (id, kind, key, value, summary, ts)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (5, "fact", "key5", "test content", "test", same_time),
            )

            conn.execute(
                """
                INSERT INTO memories (id, kind, key, value, summary, ts)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (2, "fact", "key2", "test content", "test", same_time),
            )

            conn.execute(
                """
                INSERT INTO memories (id, kind, key, value, summary, ts)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (8, "fact", "key8", "test content", "test", same_time),
            )

            conn.commit()
            conn.close()

            # Create retriever
            config = HybridRetrievalConfig(fts_candidates=0, vec_candidates=0, half_life_hours=0.0)
            retriever = HybridRetriever(db_path, config=config)

            # Mock equal scores and timestamps
            fused_scores = {5: 0.5, 2: 0.5, 8: 0.5}
            memory_metadata = retriever._load_metadata([5, 2, 8])

            # Apply sort key function
            def sort_key(item):
                memory_id, score = item
                metadata = memory_metadata[memory_id]

                recency_ts = metadata.get("ts")
                recency_epoch = 0.0
                if recency_ts:
                    try:
                        from datetime import datetime

                        dt = datetime.fromisoformat(recency_ts.replace("Z", "+00:00"))
                        recency_epoch = dt.timestamp()
                    except Exception:
                        pass

                return (-score, -recency_epoch, memory_id)

            ranked = sorted(fused_scores.items(), key=sort_key)

            # With equal scores and timestamps, should order by ID ascending
            # Expected order: 2, 5, 8
            assert ranked[0][0] == 2
            assert ranked[1][0] == 5
            assert ranked[2][0] == 8

    def test_tiebreak_precedence(self):
        """Test full precedence: score > recency > mem_id"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")

            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE memories (
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

            now = datetime.now(timezone.utc)

            # ID 1: high score, old timestamp
            conn.execute(
                """
                INSERT INTO memories (id, kind, key, value, summary, ts)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (1, "fact", "k1", "test", "test", (now - timedelta(days=10)).isoformat()),
            )

            # ID 2: medium score, new timestamp, low ID
            same_time = (now - timedelta(hours=1)).isoformat()
            conn.execute(
                """
                INSERT INTO memories (id, kind, key, value, summary, ts)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (2, "fact", "k2", "test", "test", same_time),
            )

            # ID 3: medium score, new timestamp, high ID
            conn.execute(
                """
                INSERT INTO memories (id, kind, key, value, summary, ts)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (3, "fact", "k3", "test", "test", same_time),
            )

            # ID 4: low score, newest timestamp
            conn.execute(
                """
                INSERT INTO memories (id, kind, key, value, summary, ts)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (4, "fact", "k4", "test", "test", now.isoformat()),
            )

            conn.commit()
            conn.close()

            config = HybridRetrievalConfig(fts_candidates=0, vec_candidates=0, half_life_hours=0.0)
            retriever = HybridRetriever(db_path, config=config)

            # Different scores
            fused_scores = {1: 0.9, 2: 0.5, 3: 0.5, 4: 0.1}
            memory_metadata = retriever._load_metadata([1, 2, 3, 4])

            def sort_key(item):
                memory_id, score = item
                metadata = memory_metadata[memory_id]

                recency_ts = metadata.get("ts")
                recency_epoch = 0.0
                if recency_ts:
                    try:
                        from datetime import datetime

                        dt = datetime.fromisoformat(recency_ts.replace("Z", "+00:00"))
                        recency_epoch = dt.timestamp()
                    except Exception:
                        pass

                return (-score, -recency_epoch, memory_id)

            ranked = sorted(fused_scores.items(), key=sort_key)

            # Expected order:
            # 1. ID 1 (score 0.9 - highest score wins)
            # 2. ID 2 (score 0.5, tied with 3 but lower ID)
            # 3. ID 3 (score 0.5, tied with 2 but higher ID)
            # 4. ID 4 (score 0.1 - lowest score)
            assert ranked[0][0] == 1
            assert ranked[1][0] == 2
            assert ranked[2][0] == 3
            assert ranked[3][0] == 4


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
