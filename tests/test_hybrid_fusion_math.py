"""
Unit tests for hybrid retrieval fusion math

Tests the score normalization and fusion logic without I/O dependencies.
"""

import os
import tempfile

import pytest

from bartholomew.kernel.hybrid_retriever import HybridRetrievalConfig, HybridRetriever


class TestFusionMath:
    """Test fusion score computation"""

    def test_normalize_fts_scores_inversion(self):
        """FTS ranks should be inverted: lower rank = higher score"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")

            config = HybridRetrievalConfig()
            retriever = HybridRetriever(db_path, config=config)

            # FTS results with ranks (lower is better)
            fts_results = [
                {"id": 1, "rank": 0.5},  # Best match
                {"id": 2, "rank": 2.0},
                {"id": 3, "rank": 5.0},  # Worst match
            ]
            filtered_ids = {1, 2, 3}

            normalized = retriever._normalize_fts_scores(fts_results, filtered_ids)

            # Check inversion: lower rank -> higher normalized score
            assert normalized[1] > normalized[2]
            assert normalized[2] > normalized[3]

            # Check normalization to [0, 1]
            assert all(0.0 <= v <= 1.0 for v in normalized.values())

            # Best rank should be 1.0, worst should be 0.0
            assert normalized[1] == 1.0
            assert normalized[3] == 0.0

    def test_normalize_fts_scores_equal_ranks(self):
        """Equal FTS ranks should all get score 1.0"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")

            config = HybridRetrievalConfig()
            retriever = HybridRetriever(db_path, config=config)

            fts_results = [
                {"id": 1, "rank": 1.0},
                {"id": 2, "rank": 1.0},
                {"id": 3, "rank": 1.0},
            ]
            filtered_ids = {1, 2, 3}

            normalized = retriever._normalize_fts_scores(fts_results, filtered_ids)

            # All should be 1.0 when ranks are equal
            assert all(v == 1.0 for v in normalized.values())

    def test_normalize_vec_scores_minmax(self):
        """Vector scores should be min-max normalized"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")

            config = HybridRetrievalConfig()
            retriever = HybridRetriever(db_path, config=config)

            # Vector results with cosine similarities
            vec_results = [
                (1, 0.9),  # Best
                (2, 0.6),
                (3, 0.3),  # Worst
            ]
            filtered_ids = {1, 2, 3}

            normalized = retriever._normalize_vec_scores(vec_results, filtered_ids)

            # Check min-max normalization
            assert normalized[1] == 1.0  # (0.9 - 0.3) / (0.9 - 0.3)
            assert normalized[3] == 0.0  # (0.3 - 0.3) / (0.9 - 0.3)
            assert 0.0 < normalized[2] < 1.0

            # Verify exact value for middle
            expected_mid = (0.6 - 0.3) / (0.9 - 0.3)
            assert abs(normalized[2] - expected_mid) < 1e-6

    def test_normalize_vec_scores_equal(self):
        """Equal vector scores should all get 1.0"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")

            config = HybridRetrievalConfig()
            retriever = HybridRetriever(db_path, config=config)

            vec_results = [
                (1, 0.8),
                (2, 0.8),
                (3, 0.8),
            ]
            filtered_ids = {1, 2, 3}

            normalized = retriever._normalize_vec_scores(vec_results, filtered_ids)

            assert all(v == 1.0 for v in normalized.values())

    def test_fuse_weighted_both_channels(self):
        """Weighted fusion with candidates in both channels"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")

            # Equal weights for simplicity
            config = HybridRetrievalConfig(weight_fts=0.5, weight_vec=0.5)
            retriever = HybridRetriever(db_path, config=config)

            fts_scores = {1: 1.0, 2: 0.5}
            vec_scores = {1: 0.8, 2: 0.2}

            fused = retriever._fuse_weighted(fts_scores, vec_scores)

            # Manual calculation
            expected_1 = 0.5 * 1.0 + 0.5 * 0.8  # 0.9
            expected_2 = 0.5 * 0.5 + 0.5 * 0.2  # 0.35

            assert abs(fused[1] - expected_1) < 1e-6
            assert abs(fused[2] - expected_2) < 1e-6

    def test_fuse_weighted_missing_channel_imputation(self):
        """Missing scores should be treated as 0.0"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")

            config = HybridRetrievalConfig(weight_fts=0.6, weight_vec=0.4)
            retriever = HybridRetriever(db_path, config=config)

            # ID 1 in both, ID 2 only in FTS, ID 3 only in vector
            fts_scores = {1: 1.0, 2: 0.8}
            vec_scores = {1: 0.9, 3: 0.7}

            fused = retriever._fuse_weighted(fts_scores, vec_scores)

            # ID 1: both channels
            expected_1 = 0.6 * 1.0 + 0.4 * 0.9
            assert abs(fused[1] - expected_1) < 1e-6

            # ID 2: only FTS (vec = 0)
            expected_2 = 0.6 * 0.8 + 0.4 * 0.0
            assert abs(fused[2] - expected_2) < 1e-6

            # ID 3: only vector (fts = 0)
            expected_3 = 0.6 * 0.0 + 0.4 * 0.7
            assert abs(fused[3] - expected_3) < 1e-6

            # Ranking should reflect contributions
            assert fused[1] > fused[2] > fused[3]

    def test_weight_normalization(self):
        """Config should normalize weights to sum to 1.0"""
        # Weights that don't sum to 1
        config = HybridRetrievalConfig(weight_fts=3.0, weight_vec=2.0)

        # Should be normalized
        assert abs(config.weight_fts - 0.6) < 1e-6  # 3/5
        assert abs(config.weight_vec - 0.4) < 1e-6  # 2/5

        # Zero weights should default to equal
        config_zero = HybridRetrievalConfig(weight_fts=0.0, weight_vec=0.0)
        assert config_zero.weight_fts == 0.5
        assert config_zero.weight_vec == 0.5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
