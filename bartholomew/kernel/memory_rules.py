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
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    import yaml  # PyYAML
except ImportError:  # pragma: no cover
    yaml = None

RuleMatch = dict[str, Any]
RuleMeta = dict[str, Any]


# ---------------------------------------------------------------------------
# W03-D: making `auto_expire` mean something
#
# `memory_rules.yaml` has declared an `auto_expire` category with `expires_in`
# values since v1.0 -- "2h" for a drive output, "7d" for an environment
# observation, "3d" for a transient mood. **No code path read any of them.**
# The rules engine returned `expires_in` in its evaluated metadata and every
# caller ignored it, so a Windows observation captured once was recalled
# forever, exactly as readily as a fact the user stated a minute ago.
#
# These two functions are the interpretation half of the fix, and they live
# here rather than with the store or the gate for one reason: this module is
# the authority on what a rule *means*. `memory_store.upsert_memory()` uses
# them to record a validity window at write time, and `consent_gate` uses them
# to reach the same verdict for a row written before the columns existed. Both
# get the same answer because both ask the same function.
# ---------------------------------------------------------------------------

_DURATION_UNITS: dict[str, int] = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
    "w": 604800,
}

_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhdw])\s*$", re.IGNORECASE)


def parse_duration_seconds(spec: Any) -> float | None:
    """
    Parse a `memory_rules.yaml` `expires_in` spec ("2h", "7d", "30m") into
    seconds, or None if it is absent or unparseable.

    Returning None for an unparseable spec is not a silent pass: the callers
    treat "this record declares an expiry that cannot be read" as
    `expired`, not as "keeps forever" (see `consent_gate.validity_verdict`).
    A typo in a retention rule must never quietly extend retention.
    """
    if spec is None:
        return None
    if isinstance(spec, (int, float)) and not isinstance(spec, bool):
        return float(spec) if spec >= 0 else None
    match = _DURATION_RE.match(str(spec))
    if not match:
        return None
    return float(match.group(1)) * _DURATION_UNITS[match.group(2).lower()]


def _parse_iso(value: Any) -> datetime | None:
    """Parse an ISO8601 timestamp into an aware UTC datetime, or None."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def expiry_from_rules(evaluated: dict[str, Any], ts: str) -> str | None:
    """
    Derive the `valid_to` an `auto_expire` rule implies for a record written
    at `ts`, or None when no rule declares one.

    `memory_rules.yaml`'s `auto_expire` category has declared `expires_in`
    since v1.0 and, until W03-D, **no code path read it** -- a
    `drive_output` marked "expires_in: 2h" and an `environment_observation`
    marked "7d" were both kept and recalled forever. This is the function
    that makes the declaration mean something.

    A declared-but-unparseable `expires_in` yields the record's own `ts`,
    i.e. "already expired". Fail-closed: a retention rule nobody can read
    must not be read as "no retention rule".
    """
    spec = evaluated.get("expires_in")
    if spec is None:
        return None
    seconds = parse_duration_seconds(spec)
    written = _parse_iso(ts) or datetime.now(timezone.utc)
    if seconds is None:
        return written.isoformat()
    return (written + timedelta(seconds=seconds)).isoformat()


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
        # Delegate non-singleton instances to the module-level
        # singleton. This ensures tests that monkeypatch
        # memory_rules._rules_engine.evaluate affect all engines,
        # including fresh MemoryRulesEngine() instances created in
        # tests.
        if self is not _rules_engine:
            return _rules_engine.evaluate(memory)

        # Check for file changes before evaluation
        self.check_and_reload_if_needed()

        m = self._normalize_memory_dict(memory)

        # Start with empty metadata and let rules populate fields in
        # priority order. Defaults for allow_store / requires_consent are
        # applied after rule evaluation so that high-priority rules can
        # override them.
        result_meta: dict[str, Any] = {}
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

        # Apply default flags if no rule set them
        if "allow_store" not in result_meta:
            result_meta["allow_store"] = True
        if "requires_consent" not in result_meta:
            result_meta["requires_consent"] = False

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
        if os.getenv("BARTHO_EMBED_ENABLED") == "1":
            embed_mode = enriched.get("embed", "summary")
            if embed_mode != "none" and "embed_store" not in enriched:
                enriched["embed_store"] = True

        return enriched

    def should_store(self, memory: dict[str, Any]) -> bool:
        """
        Check if memory should be stored

        Args:
            memory: Memory dict or already-evaluated policy dict

        Returns:
            True if memory should be stored
        """
        # Accept either a raw memory dict or an already-evaluated dict
        # (as returned by evaluate). This keeps call-sites simple while
        # allowing us to centralize storage policy.
        if isinstance(memory, dict) and ("allow_store" in memory or "requires_consent" in memory):
            evaluated = memory
        else:
            evaluated = self.evaluate(memory)

        # Block never_store memories outright
        if not evaluated.get("allow_store", True):
            return False

        # Block memories that require consent until explicit consent is
        # captured and a separate promotion path is used.
        if evaluated.get("requires_consent", False):
            return False

        return True

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
