from __future__ import annotations

import logging
import os
from typing import Any

import yaml


logger = logging.getLogger(__name__)

# Cache for policy to avoid repeated file reads
_policy_cache: dict[str, Any] | None = None


def load_policy(path: str = None) -> dict[str, Any]:
    """
    Load policy from YAML file with caching.

    Args:
        path: Path to policy.yaml. If None, uses default location.

    Returns:
        Policy dictionary
    """
    global _policy_cache

    if path is None:
        # Default location
        path = os.path.join(os.path.dirname(__file__), "..", "..", "config", "policy.yaml")

    if _policy_cache is None:
        try:
            with open(path, encoding="utf-8") as f:
                _policy_cache = yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning(f"Failed to load policy.yaml: {e}, using empty policy")
            _policy_cache = {}

    return _policy_cache


def can_index(evaluated_meta: dict[str, Any]) -> bool:
    """
    Check if a memory can be indexed based on policy and encryption.

    This implements an optional stricter rule: if
    policy.indexing.disallow_strong_only is True, memories marked
    with encrypt: strong are not indexed (neither FTS nor vector).

    Args:
        evaluated_meta: Evaluated metadata from memory rules engine containing
                       encryption and other governance fields

    Returns:
        True if indexing is allowed, False if blocked by policy
    """
    policy = load_policy()

    # Check if stricter indexing policy is enabled
    disallow_strong = policy.get("indexing", {}).get("disallow_strong_only", False)

    if not disallow_strong:
        # Policy not enabled, indexing allowed
        return True

    # Check encryption strength from evaluated metadata
    encrypt = evaluated_meta.get("encrypt")

    # Block indexing if encrypt is explicitly "strong"
    if isinstance(encrypt, str) and encrypt.lower().strip() == "strong":
        logger.info(
            "Indexing blocked by policy: encrypt=strong with disallow_strong_only enabled",
        )
        return False

    # All other cases: allow indexing
    return True
