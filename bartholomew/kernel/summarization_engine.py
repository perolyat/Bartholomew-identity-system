"""
Summarization Engine for Bartholomew
Implements automatic summarization for longer memory content
"""

from __future__ import annotations

import logging
import re


logger = logging.getLogger(__name__)

# Default settings (aligned with Phase 2c requirements)
LENGTH_THRESHOLD = 1000  # Characters
TARGET_SUMMARY_LENGTH = 900  # Characters (~100-150 words)
DEFAULT_MODE = "summary_also"

# Auto-summarize these kinds when content is long
AUTO_SUMMARIZE_KINDS = {
    "conversation.transcript",
    "recording.transcript",
    "article.ingested",
    "code.diff",
    "chat",
}


class SummarizationEngine:
    """
    Orchestrates content summarization based on rules and heuristics

    Supports three modes:
    - summary_only: Only the summary is stored
    - summary_also: Both original and summary are stored (default)
    - full_always: No summarization
    """

    def __init__(
        self,
        length_threshold: int = LENGTH_THRESHOLD,
        target_length: int = TARGET_SUMMARY_LENGTH,
    ) -> None:
        """
        Initialize summarization engine

        Args:
            length_threshold: Minimum content length to trigger auto-summarization
            target_length: Target summary length in characters
        """
        self.length_threshold = length_threshold
        self.target_length = target_length

    def should_summarize(self, meta: dict, value: str, kind: str) -> bool:
        """
        Determine if content should be summarized

        Triggers when:
        - Explicit summarize: true in metadata AND mode != full_always
        - OR auto-trigger: kind in AUTO_KINDS AND length > threshold

        Args:
            meta: Evaluated memory metadata from rules engine
            value: Memory content to potentially summarize
            kind: Memory kind/type

        Returns:
            True if summarization should be applied
        """
        summary_mode = meta.get("summary_mode", DEFAULT_MODE)

        # Never summarize if mode is full_always
        if summary_mode == "full_always":
            return False

        # Explicit rule-based summarization
        if meta.get("summarize") is True:
            return True

        # Auto-summarization for long content of certain kinds
        if kind in AUTO_SUMMARIZE_KINDS and len(value) > self.length_threshold:
            return True

        return False

    def _truncate_fallback(self, value: str, target: int) -> str:
        """Fallback truncation when sentence extraction is not useful.

        Ensures we respect the target length (plus ellipsis) and try to
        break on a word boundary when possible.
        """
        snippet = value[:target].rstrip()
        # Prefer to cut on a word boundary if we have spaces and the
        # boundary is not too early in the snippet.
        last_space = snippet.rfind(" ")
        if last_space > target // 2:
            snippet = snippet[:last_space]
        return snippet + "..."

    def summarize(self, value: str, target_length: int | None = None) -> str:
        """
        Generate a summary of the content

        Current implementation: Naive extractive summarizer
        - Splits into sentences
        - Takes first N sentences up to target length
        - Future: Can be upgraded to use LLM or advanced NLP

        Args:
            value: Content to summarize
            target_length: Target summary length (defaults to engine setting)

        Returns:
            Summary string
        """
        if not value or len(value) < 300:
            # Too short to meaningfully summarize
            return value

        target = target_length or self.target_length

        # Split into sentences using regex
        # Matches ., !, ? followed by space or end of string
        sentences = re.split(r"(?<=[.!?])\s+", value)

        summary = ""
        for sentence in sentences:
            # Check if adding this sentence would exceed target
            if len(summary) + len(sentence) + 1 > target:
                break
            summary += sentence + " "

        result = summary.strip()

        # Fallback: if the extractive summary is very short or we effectively
        # had a single giant sentence, truncate to the target and append
        # an ellipsis.
        if len(result) < 100 or (len(sentences) == 1 and len(value) > target):
            result = self._truncate_fallback(value, target)

        logger.debug(f"Summarized {len(value)} chars to {len(result)} chars")
        return result


# Module-level singleton for shared access
_summarization_engine = SummarizationEngine()
