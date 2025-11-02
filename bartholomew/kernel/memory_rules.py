"""
Memory Rules Engine for Bartholomew
Implements rule-based memory governance with privacy classifications
"""

from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass
from typing import Any


try:
    import yaml  # PyYAML
except ImportError:  # pragma: no cover
    yaml = None

RuleMatch = dict[str, Any]
RuleMeta = dict[str, Any]


@dataclass
class MemoryRule:
    """Individual memory governance rule"""

    category: str
    match: RuleMatch
    metadata: RuleMeta

    def matches(self, m: dict[str, Any]) -> bool:
        """
        Check if memory dict matches this rule

        Supported match fields:
        - kind: exact string match
        - key: exact string match
        - speaker: exact string match
        - tags: list intersection (any tag match)
        - content: regex search
        """
        if "kind" in self.match:
            if str(self.match["kind"]) != str(m.get("kind", "")):
                return False

        if "key" in self.match:
            if str(self.match["key"]) != str(m.get("key", "")):
                return False

        if "speaker" in self.match:
            if str(self.match["speaker"]) != str(m.get("speaker", "")):
                return False

        if "tags" in self.match:
            rule_tags = set(self.match.get("tags") or [])
            mem_tags = set(m.get("tags") or [])
            if not rule_tags.intersection(mem_tags):
                return False

        if "content" in self.match:
            pattern = str(self.match["content"])
            content = str(m.get("content", ""))
            try:
                if not re.search(pattern, content):
                    return False
            except re.error:
                # Invalid regex in rule => treat as non-match
                return False

        return True


class MemoryRulesEngine:
    """
    Rule-based memory governance engine

    Loads memory_rules.yaml and applies matching rules to memory entries.
    Returns enriched metadata including privacy_class, recall_policy, etc.
    """

    DEFAULT_PATHS = [
        os.path.join("bartholomew", "config", "memory_rules.yaml"),
        os.path.join("config", "memory_rules.yaml"),
    ]

    # Priority order (highest to lowest)
    PRIORITY = ["never_store", "ask_before_store", "always_keep", "auto_expire", "context_only"]

    def __init__(self, config_path: str | None = None, watch_file: bool = True) -> None:
        """
        Initialize rules engine

        Args:
            config_path: Optional path to memory_rules.yaml
            watch_file: Enable background file watching for auto-reload
        """
        self.config_path = config_path
        self.rules_by_category: dict[str, list[MemoryRule]] = {}
        self._logger = logging.getLogger(__name__)
        self._last_mtime: float | None = None
        self._watch_thread: threading.Thread | None = None
        self._watch_file = watch_file
        self._stop_watching = threading.Event()

        self._load_rules()

        # Initialize last modification time
        path = self._find_path()
        if path and os.path.exists(path):
            self._last_mtime = os.path.getmtime(path)

        # Start background watcher if enabled
        if self._watch_file:
            self._start_watcher()

    def _find_path(self) -> str | None:
        """Find memory_rules.yaml in default locations"""
        if self.config_path and os.path.exists(self.config_path):
            return self.config_path
        for p in self.DEFAULT_PATHS:
            if os.path.exists(p):
                return p
        return None

    def _load_rules(self) -> None:
        """Load and parse memory_rules.yaml"""
        self.rules_by_category = {c: [] for c in self.PRIORITY}

        path = self._find_path()
        if not path or not yaml:
            return  # Fall back to empty rules => permissive defaults

        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception:
            return

        for category in self.PRIORITY:
            for item in data.get(category) or []:
                match = item.get("match") or {}
                meta = item.get("metadata") or {}
                self.rules_by_category[category].append(
                    MemoryRule(category=category, match=match, metadata=meta),
                )

    def reload(self) -> None:
        """
        Manually reload memory rules from disk

        Clears internal cache and re-reads memory_rules.yaml
        """
        # Clear rule cache
        self.rules_by_category = {c: [] for c in self.PRIORITY}

        # Re-load rules
        self._load_rules()

        # Update modification time
        path = self._find_path()
        if path and os.path.exists(path):
            self._last_mtime = os.path.getmtime(path)
            self._logger.info(f"Reloaded memory rules from {path}")
        else:
            self._last_mtime = None
            self._logger.info("Reloaded memory rules (no config file found)")

    def check_and_reload_if_needed(self) -> None:
        """
        Check if config file has changed and reload if necessary

        Called automatically before rule evaluation and by background watcher
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
        self._logger.debug("Started background file watcher for memory rules")

    def stop_watcher(self) -> None:
        """Stop the background watcher thread"""
        if self._watch_thread:
            self._stop_watching.set()
            self._watch_thread.join(timeout=1)
            self._logger.debug("Stopped background file watcher")

    @staticmethod
    def _normalize_memory_dict(mem: dict[str, Any]) -> dict[str, Any]:
        """
        Normalize memory dict to consistent format

        Accepts both identity-side MemoryEntry dicts and kernel upsert dicts
        """
        # Extract tags from either top-level or nested metadata
        tags = mem.get("tags") or mem.get("metadata", {}).get("tags", []) or []
        speaker = mem.get("speaker") or mem.get("metadata", {}).get("speaker")

        return {
            "kind": mem.get("kind") or mem.get("modality") or mem.get("type"),
            "key": mem.get("key") or mem.get("id"),
            "content": mem.get("content") or mem.get("value") or "",
            "tags": list(tags),
            "speaker": speaker,
            "ts": mem.get("ts") or mem.get("timestamp"),
            "metadata": dict(mem.get("metadata") or {}),
        }

    def evaluate(self, memory: dict[str, Any]) -> dict[str, Any]:
        """
        Evaluate rules against memory and return enriched metadata

        Args:
            memory: Memory dict (MemoryEntry or upsert format)

        Returns:
            Enriched memory dict with metadata fields:
            - allow_store: bool
            - requires_consent: bool
            - privacy_class: str (optional)
            - recall_policy: str (optional)
            - expires_in: str (optional)
            - matched_categories: list of matched category names
            - matched_rules: list of (category, match) tuples
        """
        # Check for file changes before evaluation
        self.check_and_reload_if_needed()

        m = self._normalize_memory_dict(memory)

        result_meta: dict[str, Any] = {
            "allow_store": True,
            "requires_consent": False,
        }
        matched_categories: list[str] = []
        matched_rules: list[tuple[str, RuleMatch]] = []

        # Apply rules in priority order
        for category in self.PRIORITY:
            for rule in self.rules_by_category.get(category, []):
                if rule.matches(m):
                    matched_categories.append(category)
                    matched_rules.append((category, rule.match))

                    # Merge metadata without clobbering higher-priority fields
                    for k, v in rule.metadata.items():
                        if k not in result_meta:
                            result_meta[k] = v

        # Build enriched result
        enriched = dict(m)
        enriched.update(result_meta)
        enriched["matched_categories"] = matched_categories
        enriched["matched_rules"] = matched_rules

        # Handle redaction defaults: if redact is true but no strategy set,
        # default to "mask"
        if enriched.get("redact") and not enriched.get("redact_strategy"):
            enriched["redact_strategy"] = "mask"

        # Phase 2d+: Handle embed_store defaulting (single source of truth)
        # If env gate is ON and embed != "none" and embed_store is missing,
        # default to True to avoid empty retrieval when embeddings are enabled
        import os

        if os.getenv("BARTHO_EMBED_ENABLED") == "1":
            embed_mode = enriched.get("embed", "summary")
            if embed_mode != "none" and "embed_store" not in enriched:
                enriched["embed_store"] = True

        return enriched

    def should_store(self, memory: dict[str, Any]) -> bool:
        """
        Check if memory should be stored

        Args:
            memory: Memory dict

        Returns:
            True if memory should be stored
        """
        evaluated = self.evaluate(memory)
        return bool(evaluated.get("allow_store", True))

    def requires_consent(self, memory: dict[str, Any]) -> bool:
        """
        Check if memory requires user consent before storage

        Args:
            memory: Memory dict

        Returns:
            True if user consent is required
        """
        evaluated = self.evaluate(memory)
        return bool(evaluated.get("requires_consent", False))


# Module-level singleton for shared access
_rules_engine = MemoryRulesEngine()
