"""
Redaction Engine for Bartholomew
Implements rule-based content redaction with multiple strategies
"""

import logging
import re
from typing import Any


logger = logging.getLogger(__name__)


def mask_sensitive(text: str, pattern: str) -> str:
    """
    Mask sensitive content by replacing matches with asterisks

    Args:
        text: Input text to redact
        pattern: Regex pattern to match sensitive content

    Returns:
        Text with matches replaced by ****
    """
    try:
        return re.sub(pattern, "****", text, flags=re.IGNORECASE)
    except re.error as e:
        logger.error(f"Invalid regex pattern '{pattern}': {e}")
        return text


def remove_sensitive(text: str, pattern: str) -> str:
    """
    Remove sensitive content by deleting matches

    Args:
        text: Input text to redact
        pattern: Regex pattern to match sensitive content

    Returns:
        Text with matches removed
    """
    try:
        return re.sub(pattern, "", text, flags=re.IGNORECASE)
    except re.error as e:
        logger.error(f"Invalid regex pattern '{pattern}': {e}")
        return text


def replace_sensitive(text: str, pattern: str, replacement: str) -> str:
    """
    Replace sensitive content with a custom string

    Args:
        text: Input text to redact
        pattern: Regex pattern to match sensitive content
        replacement: String to replace matches with

    Returns:
        Text with matches replaced by replacement string
    """
    try:
        return re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    except re.error as e:
        logger.error(f"Invalid regex pattern '{pattern}': {e}")
        return text


def apply_redaction(text: str, rule: dict[str, Any]) -> str:
    """
    Apply redaction to text based on rule configuration

    Supports three redaction strategies:
    - mask: Replace matches with ****
    - remove: Delete matches entirely
    - replace:<text>: Replace matches with custom text

    Args:
        text: Input text to redact
        rule: Rule dict containing 'content' (regex pattern) and
              'redact_strategy' (strategy type)

    Returns:
        Redacted text, or original text if pattern missing or invalid

    Examples:
        >>> rule = {"content": r"\\d{3}-\\d{2}-\\d{4}",
        ...         "redact_strategy": "mask"}
        >>> apply_redaction("SSN: 123-45-6789", rule)
        'SSN: ****'

        >>> rule = {"content": r"password",
        ...         "redact_strategy": "replace:[REDACTED]"}
        >>> apply_redaction("password: hunter2", rule)
        '[REDACTED]: hunter2'
    """
    pattern = rule.get("content")
    if not pattern:
        # No pattern specified, return original text
        return text

    strategy = rule.get("redact_strategy", "mask")

    if strategy == "mask":
        return mask_sensitive(text, pattern)
    elif strategy == "remove":
        return remove_sensitive(text, pattern)
    elif strategy.startswith("replace:"):
        # Extract replacement text after "replace:" prefix
        parts = strategy.split("replace:", 1)
        replacement = parts[1] if len(parts) > 1 else ""
        return replace_sensitive(text, pattern, replacement)
    else:
        # Unknown strategy - log warning and return original
        logger.warning(f"Unknown redaction strategy '{strategy}', returning original text")
        return text
