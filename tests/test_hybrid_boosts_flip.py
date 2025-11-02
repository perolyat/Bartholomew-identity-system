"""
Tests demonstrating how boosts flip top-1 rankings

Tests that kind boosts and recency boosts can meaningfully alter
rankings between near-equal items, demonstrating the boost mechanics
work as expected in real scenarios.
"""

import gc
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from bartholomew.kernel.hybrid_retriever import HybridRetrievalConfig, HybridRetriever


class TestBoostsFlipRankings:
    """Test that boosts can flip top-1 between near-equal items"""

    def test_kind_boost_flips_top1_weighted(self):
        """Kind boost should flip top-1 when base scores are near-equal"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")

            # Create minimal database
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

            now = datetime.now(timezone.utc).isoformat()

            # Memory 1: kind="general", slightly higher base score
            conn.execute(
                """
                INSERT INTO memories (id, kind, key, value, summary, ts)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (1, "general", "k1", "content A", "test", now),
            )

            # Memory 2: kind="preference", slightly lower base score
            # but will win with kind boost
            conn.execute(
                """
                INSERT INTO memories (id, kind, key, value, summary, ts)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (2, "preference", "k2", "content B", "test", now),
            )

            conn.commit()
            conn.close()

            # Configure with kind boost for "preference"
            config = HybridRetrievalConfig(
                fts_candidates=0,
                vec_candidates=0,
                half_life_hours=0.0,  # Disable recency
                kind_boosts={"preference": 1.5},  # 50% boost
            )
            retriever = HybridRetriever(db_path, config=config)

            # Simulate near-equal normalized scores from FTS
            # Memory 1 has slight edge: 0.51 vs 0.50
            fts_scores = {1: 0.51, 2: 0.50}
            vec_scores = {}

            metadata = retriever._load_metadata([1, 2])
            rules_data = {}

            # Apply boosts
            boosted_fts, boosted_vec = retriever._apply_boosts(
                fts_scores,
                vec_scores,
                metadata,
                rules_data,
            )

            # Memory 1: 0.51 * 1.0 (no boost) = 0.51
            # Memory 2: 0.50 * 1.5 (kind boost) = 0.75
            assert boosted_fts[1] == pytest.approx(0.51, abs=0.001)
            assert boosted_fts[2] == pytest.approx(0.75, abs=0.001)

            # Fuse (weighted, but only FTS scores present)
            fused = retriever._fuse_weighted(boosted_fts, boosted_vec)

            # Clean up retriever connections for Windows
            del retriever
            gc.collect()  # Force cleanup of connections

            # Memory 2 should now be top-1
            assert fused[2] > fused[1]

            # Rank to confirm
            ranked = sorted(fused.items(), key=lambda x: -x[1])
            assert ranked[0][0] == 2  # Memory 2 wins
            assert ranked[1][0] == 1  # Memory 1 second

    def test_recency_boost_flips_top1_weighted(self):
        """Recency boost should flip top-1 when base scores near-equal"""
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

            # Memory 1: old (30 days ago), slightly higher base score
            ts_old = (now - timedelta(days=30)).isoformat()
            conn.execute(
                """
                INSERT INTO memories (id, kind, key, value, summary, ts)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (1, "fact", "k1", "old content", "test", ts_old),
            )

            # Memory 2: recent (1 hour ago), slightly lower base score
            # but will win with recency boost
            ts_recent = (now - timedelta(hours=1)).isoformat()
            conn.execute(
                """
                INSERT INTO memories (id, kind, key, value, summary, ts)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (2, "fact", "k2", "recent content", "test", ts_recent),
            )

            conn.commit()
            conn.close()

            # Configure with recency boost (7-day half-life)
            config = HybridRetrievalConfig(
                fts_candidates=0,
                vec_candidates=0,
                half_life_hours=168.0,  # 7 days
            )
            retriever = HybridRetriever(db_path, config=config)

            # Simulate near-equal normalized scores
            # Memory 1 has slight edge: 0.51 vs 0.50
            fts_scores = {1: 0.51, 2: 0.50}
            vec_scores = {}

            metadata = retriever._load_metadata([1, 2])
            rules_data = {}

            # Apply boosts
            boosted_fts, boosted_vec = retriever._apply_boosts(
                fts_scores,
                vec_scores,
                metadata,
                rules_data,
            )

            # Memory 1 (30 days old): significant decay
            # At 30 days with 7-day half-life: 2^(-30/7) ≈ 0.046
            # 0.51 * 0.046 ≈ 0.023

            # Memory 2 (1 hour old): minimal decay
            # At 1 hour with 7-day half-life: 2^(-1/168) ≈ 0.996
            # 0.50 * 0.996 ≈ 0.498

            assert boosted_fts[2] > boosted_fts[1]

            # Fuse
            fused = retriever._fuse_weighted(boosted_fts, boosted_vec)

            # Clean up retriever connections for Windows
            del retriever
            gc.collect()  # Force cleanup of connections

            # Memory 2 should be top-1 due to recency
            assert fused[2] > fused[1]

            # Rank to confirm
            ranked = sorted(fused.items(), key=lambda x: -x[1])
            assert ranked[0][0] == 2  # Recent memory wins
            assert ranked[1][0] == 1  # Old memory second

    def test_combined_boosts_flip_top1(self):
        """Combined kind + recency boosts should flip rankings"""
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

            # Memory 1: general kind, old, high base score
            ts_old = (now - timedelta(days=30)).isoformat()
            conn.execute(
                """
                INSERT INTO memories (id, kind, key, value, summary, ts)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (1, "general", "k1", "old general", "test", ts_old),
            )

            # Memory 2: preference kind, recent, lower base score
            # Will win with combined boosts
            ts_recent = (now - timedelta(hours=1)).isoformat()
            conn.execute(
                """
                INSERT INTO memories (id, kind, key, value, summary, ts)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (2, "preference", "k2", "recent pref", "test", ts_recent),
            )

            conn.commit()
            conn.close()

            # Configure with both boosts
            config = HybridRetrievalConfig(
                fts_candidates=0,
                vec_candidates=0,
                half_life_hours=168.0,
                kind_boosts={"preference": 1.3},
            )
            retriever = HybridRetriever(db_path, config=config)

            # Memory 1 has higher base score
            fts_scores = {1: 0.55, 2: 0.50}
            vec_scores = {}

            metadata = retriever._load_metadata([1, 2])
            rules_data = {}

            # Apply boosts
            boosted_fts, boosted_vec = retriever._apply_boosts(
                fts_scores,
                vec_scores,
                metadata,
                rules_data,
            )

            # Memory 1: 0.55 * ~0.046 (recency) * 1.0 (kind) ≈ 0.025
            # Memory 2: 0.50 * ~0.996 (recency) * 1.3 (kind) ≈ 0.647

            assert boosted_fts[2] > boosted_fts[1]

            fused = retriever._fuse_weighted(boosted_fts, boosted_vec)

            # Clean up retriever connections for Windows
            del retriever
            gc.collect()  # Force cleanup of connections

            # Memory 2 wins with combined boosts
            ranked = sorted(fused.items(), key=lambda x: -x[1])
            assert ranked[0][0] == 2
            assert ranked[1][0] == 1

    def test_kind_boost_flips_top1_rrf(self):
        """Kind boost should flip top-1 in RRF mode"""
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

            now = datetime.now(timezone.utc).isoformat()

            # Memory 1: general kind, rank 1 in both sources
            conn.execute(
                """
                INSERT INTO memories (id, kind, key, value, summary, ts)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (1, "general", "k1", "content A", "test", now),
            )

            # Memory 2: preference kind, rank 2 in both sources
            # Will win with kind boost in RRF
            conn.execute(
                """
                INSERT INTO memories (id, kind, key, value, summary, ts)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (2, "preference", "k2", "content B", "test", now),
            )

            conn.commit()
            conn.close()

            # Configure RRF with kind boost
            config = HybridRetrievalConfig(
                fusion_mode="rrf",
                rrf_k=60,
                half_life_hours=0.0,  # Disable recency
                kind_boosts={"preference": 1.8},  # Large boost
            )
            retriever = HybridRetriever(db_path, config=config)

            # Simulate ranked results
            fts_results = [{"id": 1, "rank": 1}, {"id": 2, "rank": 2}]
            vec_results = [(1, 0.9), (2, 0.8)]  # Rank 1  # Rank 2

            filtered_ids = {1, 2}
            metadata = retriever._load_metadata([1, 2])
            rules_data = {}

            # Compute RRF scores with boosts
            fused = retriever._fuse_rrf(
                fts_results,
                vec_results,
                filtered_ids,
                metadata,
                rules_data,
            )

            # Clean up retriever connections for Windows
            del retriever
            gc.collect()  # Force cleanup of connections

            # Without boost:
            # Mem 1: 1/(60+1) + 1/(60+1) = 2/61 ≈ 0.0328
            # Mem 2: 1/(60+2) + 1/(60+2) = 2/62 ≈ 0.0323

            # With boost (kind * recency=1.0):
            # Mem 1: 0.0328 * 1.0 = 0.0328
            # Mem 2: 0.0323 * 1.8 = 0.0581

            assert fused[2] > fused[1]

            ranked = sorted(fused.items(), key=lambda x: -x[1])
            assert ranked[0][0] == 2  # Boosted preference wins
            assert ranked[1][0] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
