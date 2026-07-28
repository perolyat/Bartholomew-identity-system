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


# mask_sensitive()/apply_redaction() above require the caller to supply a
# specific regex pattern (driven by memory_rules.yaml's per-kind rules).
# Experience Kernel / Narrator accept arbitrary free text (attention
# targets, goal descriptions, affect labels, observation/reflection
# content) with no per-field rule of their own, so they need a
# general-purpose pattern instead of one tailored to a specific memory
# kind.
#
# Deliberately NOT reusing memory.privacy_guard.SENSITIVE_KEYWORDS here
# (name/address/location/phone/email/bank/password/routine/health/
# private/account) -- that list is a *consent-prompt* trigger (a human
# confirms before storing), which tolerates false positives fine. This
# function is *silent, automatic* redaction with no human in the loop, and
# those keywords are also just ordinary vocabulary a wellness-focused
# assistant's own self-model legitimately uses constantly (e.g. a "health"
# or "routine"-related goal/attention target) -- using them here mangled
# entirely benign content in testing (confirmed: "Answer health question"
# became "Answer **** question"). So this matches only concrete,
# unambiguous PII *shapes* instead -- narrower, but far fewer false
# positives for content this subsystem is expected to legitimately handle.
_EMAIL_PATTERN = r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}"
_PHONE_PATTERN = r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b"
_SSN_PATTERN = r"\b\d{3}-\d{2}-\d{4}\b"

PII_PATTERN = re.compile(
    "|".join([_EMAIL_PATTERN, _SSN_PATTERN, _PHONE_PATTERN]),
    re.IGNORECASE,
)


def redact_pii(text: str | None) -> str | None:
    """
    General-purpose PII redaction for free text with no per-field rule.

    Intended for Experience Kernel / Narrator call sites that accept
    arbitrary caller-supplied text (attention targets, goal descriptions,
    affect labels, observation/reflection content) and need a safe default
    rather than requiring every call site to supply its own regex.

    Returns text unchanged if empty/None (so callers can pass through
    optional fields, e.g. set_attention(target=None), without a None check).
    """
    if not text:
        return text
    return PII_PATTERN.sub("****", text)
