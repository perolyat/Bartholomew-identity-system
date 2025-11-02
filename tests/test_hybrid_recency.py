"""
Unit tests for hybrid retrieval recency shaping

Tests the recency boost computation with exponential decay.
"""

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from bartholomew.kernel.hybrid_retriever import HybridRetrievalConfig, HybridRetriever


class TestRecencyShaping:
    """Test recency boost computation"""

    def test_recency_boost_monotonic_decay(self):
        """Newer timestamps should have higher boost than older ones"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")

            # 7-day half-life
            config = HybridRetrievalConfig(half_life_hours=168.0)
            retriever = HybridRetriever(db_path, config=config)

            now = datetime.now(timezone.utc)

            # Recent (1 hour ago)
            ts_recent = (now - timedelta(hours=1)).isoformat()
            boost_recent = retriever._compute_recency_boost(ts_recent)

            # Medium (7 days ago, at half-life)
            ts_medium = (now - timedelta(days=7)).isoformat()
            boost_medium = retriever._compute_recency_boost(ts_medium)

            # Old (30 days ago)
            ts_old = (now - timedelta(days=30)).isoformat()
            boost_old = retriever._compute_recency_boost(ts_old)

            # Check monotonic decrease
            assert boost_recent > boost_medium > boost_old

            # At half-life, boost should be ~0.5
            assert 0.45 < boost_medium < 0.55

            # Recent should be close to 1.0
            assert boost_recent > 0.95

    def test_recency_boost_half_life_effect(self):
        """Different half-life values should affect decay rate"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")

            now = datetime.now(timezone.utc)
            ts_week_ago = (now - timedelta(days=7)).isoformat()

            # Short half-life (24 hours)
            config_short = HybridRetrievalConfig(half_life_hours=24.0)
            retriever_short = HybridRetriever(db_path, config=config_short)
            boost_short = retriever_short._compute_recency_boost(ts_week_ago)

            # Long half-life (30 days)
            config_long = HybridRetrievalConfig(half_life_hours=720.0)
            retriever_long = HybridRetriever(db_path, config=config_long)
            boost_long = retriever_long._compute_recency_boost(ts_week_ago)

            # With longer half-life, same age gets higher boost
            assert boost_long > boost_short

            # Short half-life should decay significantly after 7 days
            assert boost_short < 0.1

            # Long half-life should still be high after 7 days
            assert boost_long > 0.8

    def test_recency_boost_no_timestamp(self):
        """Missing timestamp should return neutral boost of 1.0"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")

            config = HybridRetrievalConfig()
            retriever = HybridRetriever(db_path, config=config)

            # None timestamp
            boost_none = retriever._compute_recency_boost(None)
            assert boost_none == 1.0

            # Empty string
            boost_empty = retriever._compute_recency_boost("")
            assert boost_empty == 1.0

    def test_recency_boost_invalid_timestamp(self):
        """Invalid timestamp format should return neutral boost of 1.0"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")

            config = HybridRetrievalConfig()
            retriever = HybridRetriever(db_path, config=config)

            # Invalid format
            boost = retriever._compute_recency_boost("not-a-timestamp")
            assert boost == 1.0

    def test_recency_boost_disabled(self):
        """Zero half-life should disable recency boost (return 1.0)"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")

            # Disable recency with half_life_hours = 0
            config = HybridRetrievalConfig(half_life_hours=0.0)
            retriever = HybridRetriever(db_path, config=config)

            now = datetime.now(timezone.utc)
            ts_old = (now - timedelta(days=365)).isoformat()

            boost = retriever._compute_recency_boost(ts_old)
            assert boost == 1.0

    def test_recency_boost_future_timestamp(self):
        """Future timestamps should be clamped to age=0, boost=1.0"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")

            config = HybridRetrievalConfig(half_life_hours=168.0)
            retriever = HybridRetriever(db_path, config=config)

            # Future timestamp (1 day from now)
            now = datetime.now(timezone.utc)
            ts_future = (now + timedelta(days=1)).isoformat()

            boost = retriever._compute_recency_boost(ts_future)

            # Age is clamped to 0 for future dates, so boost = 2^0 = 1.0
            assert boost == 1.0

    def test_recency_boost_formula(self):
        """Verify exact formula: 2^(-(age_seconds) / (half_life_seconds))"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")

            half_life_hours = 24.0
            config = HybridRetrievalConfig(half_life_hours=half_life_hours)
            retriever = HybridRetriever(db_path, config=config)

            now = datetime.now(timezone.utc)

            # Exactly at half-life
            ts_half = (now - timedelta(hours=half_life_hours)).isoformat()
            boost_half = retriever._compute_recency_boost(ts_half)

            # Should be exactly 0.5 (2^-1)
            assert abs(boost_half - 0.5) < 0.01

            # Double the half-life period
            ts_double = (now - timedelta(hours=2 * half_life_hours)).isoformat()
            boost_double = retriever._compute_recency_boost(ts_double)

            # Should be 0.25 (2^-2)
            assert abs(boost_double - 0.25) < 0.01


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
