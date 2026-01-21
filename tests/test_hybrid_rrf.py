"""
Unit tests for hybrid retrieval RRF (Reciprocal Rank Fusion)

Tests RRF fusion mode and kind/rule boost integration.
"""

import os
import tempfile

import pytest

from conftest import SKIP_WINDOWS_FTS


# Skip all tests in this module on Windows (FTS5/matchinfo not available)
pytestmark = SKIP_WINDOWS_FTS

from bartholomew.kernel.hybrid_retriever import HybridRetrievalConfig, HybridRetriever


class TestRRF:
    """Test Reciprocal Rank Fusion"""

    def test_rrf_formula(self):
        """RRF should use formula: 1/(k+rank)"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")

            config = HybridRetrievalConfig(fusion_mode="rrf", rrf_k=60)
            retriever = HybridRetriever(db_path, config=config)

            # Create mock data structures
            fts_results = [
                {"id": 1, "rank": 0.5},  # Rank 1
                {"id": 2, "rank": 1.0},  # Rank 2
            ]
            vec_results = [
                (1, 0.9),  # Rank 1
                (3, 0.8),  # Rank 2
            ]
            filtered_ids = {1, 2, 3}

            # Mock metadata (no boosts)
            metadata = {
                1: {"id": 1, "kind": "fact", "ts": None},
                2: {"id": 2, "kind": "fact", "ts": None},
                3: {"id": 3, "kind": "fact", "ts": None},
            }

            # Empty rules (no boosts)
            rules_data = {}

            fused = retriever._fuse_rrf(
                fts_results,
                vec_results,
                filtered_ids,
                metadata,
                rules_data,
            )

            # Calculate expected RRF scores
            k = config.rrf_k

            # ID 1: in both lists at rank 1
            expected_1 = (1 / (k + 1)) + (1 / (k + 1))

            # ID 2: only in FTS at rank 2
            expected_2 = 1 / (k + 2)

            # ID 3: only in vector at rank 2
            expected_3 = 1 / (k + 2)

            assert abs(fused[1] - expected_1) < 1e-6
            assert abs(fused[2] - expected_2) < 1e-6
            assert abs(fused[3] - expected_3) < 1e-6

            # Verify ranking
            assert fused[1] > fused[2]
            assert fused[2] == pytest.approx(fused[3])

    def test_rrf_k_effect(self):
        """Different rrf_k values should affect fusion"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")

            fts_results = [{"id": 1, "rank": 0.5}]
            vec_results = [(1, 0.9)]
            filtered_ids = {1}
            metadata = {1: {"id": 1, "kind": "fact", "ts": None}}
            rules_data = {}

            # Small k
            config_small = HybridRetrievalConfig(fusion_mode="rrf", rrf_k=10)
            retriever_small = HybridRetriever(db_path, config=config_small)
            fused_small = retriever_small._fuse_rrf(
                fts_results,
                vec_results,
                filtered_ids,
                metadata,
                rules_data,
            )

            # Large k
            config_large = HybridRetrievalConfig(fusion_mode="rrf", rrf_k=100)
            retriever_large = HybridRetriever(db_path, config=config_large)
            fused_large = retriever_large._fuse_rrf(
                fts_results,
                vec_results,
                filtered_ids,
                metadata,
                rules_data,
            )

            # Smaller k gives higher scores
            assert fused_small[1] > fused_large[1]

    def test_kind_boosts(self):
        """Kind boosts should multiply final scores"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")

            # Configure kind boosts
            config = HybridRetrievalConfig(kind_boosts={"important": 2.0, "regular": 1.0})
            retriever = HybridRetriever(db_path, config=config)

            # Same base scores, different kinds
            fts_scores = {1: 0.5, 2: 0.5}
            vec_scores = {1: 0.5, 2: 0.5}

            metadata = {
                1: {"id": 1, "kind": "important", "ts": None},
                2: {"id": 2, "kind": "regular", "ts": None},
            }

            rules_data = {}

            boosted_fts, boosted_vec = retriever._apply_boosts(
                fts_scores,
                vec_scores,
                metadata,
                rules_data,
            )

            # ID 1 should have 2x boost from kind
            assert boosted_fts[1] == pytest.approx(1.0)  # 0.5 * 2.0
            assert boosted_fts[2] == pytest.approx(0.5)  # 0.5 * 1.0

            # Boost applies to both channels
            assert boosted_vec[1] == pytest.approx(1.0)
            assert boosted_vec[2] == pytest.approx(0.5)

    def test_rule_boosts(self):
        """Rules engine boost should multiply scores"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")

            config = HybridRetrievalConfig()
            retriever = HybridRetriever(db_path, config=config)

            fts_scores = {1: 0.8, 2: 0.8}
            vec_scores = {1: 0.6, 2: 0.6}

            metadata = {
                1: {"id": 1, "kind": "fact", "ts": None},
                2: {"id": 2, "kind": "fact", "ts": None},
            }

            # ID 1 gets 1.5x boost from rules
            rules_data = {
                1: {"boost": 1.5},
                2: {"boost": 1.0},
            }

            boosted_fts, boosted_vec = retriever._apply_boosts(
                fts_scores,
                vec_scores,
                metadata,
                rules_data,
            )

            # ID 1 should have rule boost applied
            assert boosted_fts[1] == pytest.approx(1.2)  # 0.8 * 1.5
            assert boosted_fts[2] == pytest.approx(0.8)  # 0.8 * 1.0

    def test_combined_boosts(self):
        """Recency, kind, and rule boosts should multiply together"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")

            from datetime import datetime, timedelta, timezone

            # Configure with kind boosts and recency
            config = HybridRetrievalConfig(kind_boosts={"important": 2.0}, half_life_hours=168.0)
            retriever = HybridRetriever(db_path, config=config)

            now = datetime.now(timezone.utc)
            ts_recent = (now - timedelta(hours=1)).isoformat()

            fts_scores = {1: 0.5}
            vec_scores = {1: 0.5}

            metadata = {1: {"id": 1, "kind": "important", "ts": ts_recent}}

            rules_data = {1: {"boost": 1.5}}

            boosted_fts, boosted_vec = retriever._apply_boosts(
                fts_scores,
                vec_scores,
                metadata,
                rules_data,
            )

            # Should be: 0.5 * recency_boost * 2.0 (kind) * 1.5 (rule)
            # recency_boost ~= 1.0 for 1 hour ago
            # Total: 0.5 * 1.0 * 2.0 * 1.5 = 1.5
            assert boosted_fts[1] > 1.4  # Allow for recency variance
            assert boosted_fts[1] < 1.6

    def test_missing_kind_boost(self):
        """Unknown kind should get default boost of 1.0"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")

            config = HybridRetrievalConfig(kind_boosts={"known": 2.0})
            retriever = HybridRetriever(db_path, config=config)

            fts_scores = {1: 0.5}
            vec_scores = {1: 0.5}

            metadata = {1: {"id": 1, "kind": "unknown", "ts": None}}

            rules_data = {}

            boosted_fts, boosted_vec = retriever._apply_boosts(
                fts_scores,
                vec_scores,
                metadata,
                rules_data,
            )

            # Unknown kind should not boost
            assert boosted_fts[1] == pytest.approx(0.5)

    def test_rrf_with_boosts(self):
        """RRF should apply boosts after rank fusion"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")

            config = HybridRetrievalConfig(
                fusion_mode="rrf",
                rrf_k=60,
                kind_boosts={"important": 2.0},
            )
            retriever = HybridRetriever(db_path, config=config)

            fts_results = [
                {"id": 1, "rank": 0.5},
                {"id": 2, "rank": 1.0},
            ]
            vec_results = [(1, 0.9), (2, 0.8)]
            filtered_ids = {1, 2}

            metadata = {
                1: {"id": 1, "kind": "important", "ts": None},
                2: {"id": 2, "kind": "regular", "ts": None},
            }

            rules_data = {}

            fused = retriever._fuse_rrf(
                fts_results,
                vec_results,
                filtered_ids,
                metadata,
                rules_data,
            )

            # Calculate base RRF for both
            k = config.rrf_k
            base_rrf_1 = (1 / (k + 1)) + (1 / (k + 1))
            base_rrf_2 = (1 / (k + 2)) + (1 / (k + 2))

            # With kind boost: ID 1 gets 2x
            expected_1 = base_rrf_1 * 2.0
            expected_2 = base_rrf_2 * 1.0

            assert abs(fused[1] - expected_1) < 1e-6
            assert abs(fused[2] - expected_2) < 1e-6

            # ID 1 should rank higher due to boost
            assert fused[1] > fused[2]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
