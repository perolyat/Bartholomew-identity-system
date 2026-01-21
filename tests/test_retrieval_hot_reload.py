"""
Tests for retrieval config hot-reload functionality
"""

import os
import tempfile
import time

import pytest
import yaml

from conftest import SKIP_WINDOWS_FTS


# Skip all tests in this module on Windows (FTS5/matchinfo not available)
pytestmark = SKIP_WINDOWS_FTS

from bartholomew.kernel.hybrid_retriever import HybridRetriever
from bartholomew.kernel.retrieval_config import RetrievalConfigManager, get_retrieval_config_manager


def test_config_manager_loads_defaults():
    """Config manager should load default values when no file exists"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create manager with non-existent path
        manager = RetrievalConfigManager(
            config_path=os.path.join(tmpdir, "nonexistent.yaml"),
            watch_file=False,
        )

        # Should use defaults
        assert manager.get_fts_tokenizer() == "porter"
        assert manager.get_fts_index_mode() == "external"

        config = manager.get_hybrid_config()
        assert config.fusion_mode == "weighted"
        assert abs(config.weight_fts - 0.6) < 0.01
        assert abs(config.weight_vec - 0.4) < 0.01


def test_config_manager_loads_from_yaml():
    """Config manager should load values from kernel.yaml"""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "kernel.yaml")

        # Write test config
        test_config = {
            "retrieval": {
                "fts_tokenizer": "unicode61",
                "fusion_strategy": "rrf",
                "hybrid_weights": {"fts": 0.7, "vector": 0.3},
                "rrf_k": 80,
                "recency": {"half_life_days": 14},
                "kind_boosts": {"event": 1.5, "preference": 2.0},
            },
        }

        with open(config_path, "w") as f:
            yaml.dump(test_config, f)

        # Create manager
        manager = RetrievalConfigManager(config_path=config_path, watch_file=False)

        # Verify loaded values
        assert manager.get_fts_tokenizer() == "unicode61"

        config = manager.get_hybrid_config()
        assert config.fusion_mode == "rrf"
        assert config.rrf_k == 80
        assert abs(config.half_life_hours - (14 * 24)) < 0.01

        # Weights should be normalized
        weight_sum = config.weight_fts + config.weight_vec
        assert abs(weight_sum - 1.0) < 0.01

        # Check kind boosts
        assert config.kind_boosts["event"] == 1.5
        assert config.kind_boosts["preference"] == 2.0


def test_config_manager_hot_reload():
    """Config manager should detect file changes and reload"""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "kernel.yaml")

        # Write initial config
        initial_config = {
            "retrieval": {
                "fusion_strategy": "weighted",
                "hybrid_weights": {"fts": 0.6, "vector": 0.4},
            },
        }

        with open(config_path, "w") as f:
            yaml.dump(initial_config, f)

        # Create manager without background watcher
        manager = RetrievalConfigManager(config_path=config_path, watch_file=False)

        config = manager.get_hybrid_config()
        assert config.fusion_mode == "weighted"
        old_fts_weight = config.weight_fts

        # Modify config file
        time.sleep(0.1)  # Ensure mtime changes
        updated_config = {
            "retrieval": {
                "fusion_strategy": "rrf",
                "hybrid_weights": {"fts": 0.8, "vector": 0.2},
                "recency": {"half_life_days": 3},
            },
        }

        with open(config_path, "w") as f:
            yaml.dump(updated_config, f)

        # Trigger reload
        manager.reload()

        # Config object should be updated in-place
        assert config.fusion_mode == "rrf"
        assert config.weight_fts != old_fts_weight
        assert abs(config.half_life_hours - (3 * 24)) < 0.01


def test_config_manager_normalizes_weights():
    """Config manager should normalize weights to sum to 1.0"""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "kernel.yaml")

        # Write config with unnormalized weights
        test_config = {"retrieval": {"hybrid_weights": {"fts": 3.0, "vector": 2.0}}}

        with open(config_path, "w") as f:
            yaml.dump(test_config, f)

        manager = RetrievalConfigManager(config_path=config_path, watch_file=False)

        config = manager.get_hybrid_config()

        # Should be normalized to 0.6 and 0.4
        assert abs(config.weight_fts - 0.6) < 0.01
        assert abs(config.weight_vec - 0.4) < 0.01
        assert abs((config.weight_fts + config.weight_vec) - 1.0) < 0.01


def test_hybrid_retriever_uses_config_manager():
    """HybridRetriever should use config manager when no config provided"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")

        # Create minimal database
        import sqlite3

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
        conn.close()

        # Create retriever without explicit config
        retriever = HybridRetriever(db_path)

        # Should have config from manager
        assert retriever.config is not None
        assert hasattr(retriever.config, "fusion_mode")


def test_config_manager_singleton():
    """get_retrieval_config_manager should return singleton"""
    manager1 = get_retrieval_config_manager()
    manager2 = get_retrieval_config_manager()

    assert manager1 is manager2


def test_config_manager_tokenizer_backward_compat():
    """Config manager should support legacy fts.tokenizer location"""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "kernel.yaml")

        # Write config with legacy location only
        test_config = {"fts": {"tokenizer": "ascii"}}

        with open(config_path, "w") as f:
            yaml.dump(test_config, f)

        manager = RetrievalConfigManager(config_path=config_path, watch_file=False)

        # Should load from legacy location
        assert manager.get_fts_tokenizer() == "ascii"


def test_config_manager_new_location_takes_precedence():
    """New retrieval.fts_tokenizer should override legacy fts.tokenizer"""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "kernel.yaml")

        # Write config with both locations
        test_config = {"retrieval": {"fts_tokenizer": "unicode61"}, "fts": {"tokenizer": "porter"}}

        with open(config_path, "w") as f:
            yaml.dump(test_config, f)

        manager = RetrievalConfigManager(config_path=config_path, watch_file=False)

        # Should use new location
        assert manager.get_fts_tokenizer() == "unicode61"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
