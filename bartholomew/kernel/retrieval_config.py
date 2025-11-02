"""
Retrieval Configuration Manager for Bartholomew

Loads retrieval.* configuration from kernel.yaml with hot-reload support.
Mirrors the memory_rules.py watcher pattern.
"""

from __future__ import annotations

import logging
import os
import threading

from bartholomew.kernel.hybrid_retriever import HybridRetrievalConfig


try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

logger = logging.getLogger(__name__)


class RetrievalConfigManager:
    """
    Manages retrieval configuration with hot-reload support

    Watches config/kernel.yaml and propagates retrieval.* changes
    by mutating HybridRetrievalConfig in-place, allowing existing
    retriever instances to see updates without recreation.
    """

    DEFAULT_CONFIG_PATHS = [
        os.path.join("config", "kernel.yaml"),
        os.path.join("bartholomew", "..", "config", "kernel.yaml"),
    ]

    def __init__(self, config_path: str | None = None, watch_file: bool = True) -> None:
        """
        Initialize retrieval config manager

        Args:
            config_path: Optional path to kernel.yaml
            watch_file: Enable background file watching for auto-reload
        """
        self.config_path = config_path
        self._hybrid_config = HybridRetrievalConfig()
        self._fts_tokenizer = "porter"
        self._fts_index_mode = "external"
        self._logger = logging.getLogger(__name__)
        self._last_mtime: float | None = None
        self._watch_thread: threading.Thread | None = None
        self._watch_file = watch_file
        self._stop_watching = threading.Event()

        # Initial load
        self._load_config()

        # Initialize last modification time
        path = self._find_path()
        if path and os.path.exists(path):
            self._last_mtime = os.path.getmtime(path)

        # Start background watcher if enabled
        if self._watch_file:
            self._start_watcher()

    def _find_path(self) -> str | None:
        """Find kernel.yaml in default locations"""
        if self.config_path and os.path.exists(self.config_path):
            return self.config_path
        for p in self.DEFAULT_CONFIG_PATHS:
            if os.path.exists(p):
                return p
        return None

    def _load_config(self) -> None:
        """Load and parse retrieval.* from kernel.yaml"""
        path = self._find_path()
        if not path or not yaml:
            self._logger.debug("No config file found, using defaults")
            return

        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            self._logger.warning(f"Failed to load config: {e}")
            return

        retrieval = data.get("retrieval", {})

        # Extract candidate pool sizes (performance guardrails)
        fts_candidates = int(retrieval.get("fts_candidates", 200))
        vec_candidates = int(retrieval.get("vec_candidates", 200))
        top_k = int(retrieval.get("top_k", 20))

        # Extract FTS settings
        self._fts_tokenizer = retrieval.get("fts_tokenizer", "porter")
        self._fts_index_mode = retrieval.get("fts_index_mode", "external")

        # Extract hybrid fusion settings
        fusion_strategy = retrieval.get("fusion_strategy", "weighted")
        if fusion_strategy == "rrf":
            fusion_mode = "rrf"
        else:
            fusion_mode = "weighted"

        # Extract hybrid weights
        weights = retrieval.get("hybrid_weights", {})
        weight_fts = float(weights.get("fts", 0.6))
        weight_vec = float(weights.get("vector", 0.4))

        # Extract RRF constant
        rrf_k = int(retrieval.get("rrf_k", 60))

        # Extract recency settings
        recency = retrieval.get("recency", {})
        half_life_days = float(recency.get("half_life_days", 7.0))
        half_life_hours = half_life_days * 24.0

        # Extract kind boosts
        kind_boosts = dict(retrieval.get("kind_boosts", {}))

        # Update hybrid config in-place (so existing references see changes)
        self._hybrid_config.fts_candidates = fts_candidates
        self._hybrid_config.vec_candidates = vec_candidates
        self._hybrid_config.default_top_k = top_k
        self._hybrid_config.fusion_mode = fusion_mode
        self._hybrid_config.weight_fts = weight_fts
        self._hybrid_config.weight_vec = weight_vec
        self._hybrid_config.rrf_k = rrf_k
        self._hybrid_config.half_life_hours = half_life_hours
        self._hybrid_config.kind_boosts = kind_boosts

        # Re-normalize weights (handled by __post_init__ logic)
        weight_sum = self._hybrid_config.weight_fts + self._hybrid_config.weight_vec
        if weight_sum > 0:
            self._hybrid_config.weight_fts /= weight_sum
            self._hybrid_config.weight_vec /= weight_sum
        else:
            self._hybrid_config.weight_fts = 0.5
            self._hybrid_config.weight_vec = 0.5

        self._logger.debug(
            f"Loaded retrieval config: "
            f"fts_cand={fts_candidates}, vec_cand={vec_candidates}, "
            f"top_k={top_k}, fusion={fusion_mode}, "
            f"weights=({self._hybrid_config.weight_fts:.2f}, "
            f"{self._hybrid_config.weight_vec:.2f}), "
            f"half_life={half_life_days}d, "
            f"tokenizer={self._fts_tokenizer}",
        )

    def reload(self) -> None:
        """
        Manually reload retrieval config from disk

        Updates HybridRetrievalConfig in-place so existing retriever
        instances see the changes.
        """
        old_fusion = self._hybrid_config.fusion_mode
        old_weights = (self._hybrid_config.weight_fts, self._hybrid_config.weight_vec)
        old_half_life = self._hybrid_config.half_life_hours
        old_tokenizer = self._fts_tokenizer
        old_index_mode = self._fts_index_mode

        # Re-load config
        self._load_config()

        # Update modification time
        path = self._find_path()
        if path and os.path.exists(path):
            self._last_mtime = os.path.getmtime(path)

        # Log changes
        changes = []
        if self._hybrid_config.fusion_mode != old_fusion:
            changes.append(
                f"fusion_strategy: {old_fusion} -> {self._hybrid_config.fusion_mode}",
            )

        new_weights = (self._hybrid_config.weight_fts, self._hybrid_config.weight_vec)
        if new_weights != old_weights:
            changes.append(
                f"weights: ({old_weights[0]:.2f}, {old_weights[1]:.2f}) -> "
                f"({new_weights[0]:.2f}, {new_weights[1]:.2f})",
            )

        if self._hybrid_config.half_life_hours != old_half_life:
            changes.append(
                f"half_life_hours: {old_half_life:.1f} -> "
                f"{self._hybrid_config.half_life_hours:.1f}",
            )

        if self._fts_tokenizer != old_tokenizer:
            changes.append(f"fts_tokenizer: {old_tokenizer} -> {self._fts_tokenizer}")
            self._logger.warning(
                "FTS tokenizer changed. Existing FTS index will continue "
                "using old tokenizer. Run scripts/backfill_fts.py or "
                "FTSClient.rebuild_index() to rebuild with new tokenizer.",
            )

        if self._fts_index_mode != old_index_mode:
            changes.append(f"fts_index_mode: {old_index_mode} -> {self._fts_index_mode}")
            if self._fts_index_mode != "external":
                self._logger.warning(
                    f"FTS index mode '{self._fts_index_mode}' is not "
                    "currently supported. Continuing with 'external' mode.",
                )

        if changes:
            self._logger.info(f"Reloaded retrieval config: {', '.join(changes)}")
        else:
            self._logger.debug("Reloaded retrieval config (no changes)")

    def check_and_reload_if_needed(self) -> None:
        """
        Check if config file has changed and reload if necessary

        Called automatically by background watcher and can be called
        manually for on-demand reload checks.
        """
        path = self._find_path()
        if not path or not os.path.exists(path):
            return

        try:
            current_mtime = os.path.getmtime(path)
            if self._last_mtime is None or current_mtime != self._last_mtime:
                self.reload()
        except Exception as e:
            self._logger.error(f"Failed to check file modification time: {e}")

    def _start_watcher(self) -> None:
        """Start background thread to watch for file changes"""

        def watch_loop():
            while not self._stop_watching.is_set():
                try:
                    self.check_and_reload_if_needed()
                except Exception as e:
                    self._logger.error(f"Error in watch loop: {e}")

                # Sleep for 10 seconds or until stop signal
                self._stop_watching.wait(10)

        self._watch_thread = threading.Thread(target=watch_loop, daemon=True)
        self._watch_thread.start()
        self._logger.debug("Started background file watcher for retrieval config")

    def stop_watcher(self) -> None:
        """Stop the background watcher thread"""
        if self._watch_thread:
            self._stop_watching.set()
            self._watch_thread.join(timeout=1)
            self._logger.debug("Stopped background file watcher")

    def get_hybrid_config(self) -> HybridRetrievalConfig:
        """
        Get current HybridRetrievalConfig

        Returns a live reference that will reflect hot-reloaded changes.
        """
        return self._hybrid_config

    def get_fts_tokenizer(self) -> str:
        """Get current FTS tokenizer setting"""
        return self._fts_tokenizer

    def get_fts_index_mode(self) -> str:
        """Get current FTS index mode setting"""
        return self._fts_index_mode


# Module-level singleton for shared access
_retrieval_config_manager: RetrievalConfigManager | None = None


def get_retrieval_config_manager() -> RetrievalConfigManager:
    """
    Get or create retrieval config manager singleton

    Returns:
        RetrievalConfigManager instance
    """
    global _retrieval_config_manager
    if _retrieval_config_manager is None:
        _retrieval_config_manager = RetrievalConfigManager()
    return _retrieval_config_manager
