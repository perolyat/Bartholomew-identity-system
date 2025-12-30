"""
Chunking Engine for Long Content

Splits long memory content into overlapping chunks for better FTS and vector
indexing. Operates on redacted plaintext before encryption.

Key features:
- Simple token-based splitting (whitespace as token proxy)
- Configurable chunk size and overlap
- Preserves sentence boundaries where possible
- Skips chunking for encrypted memories
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

import yaml


logger = logging.getLogger(__name__)


@dataclass
class Chunk:
    """A chunk of text with sequential ordering and token range"""

    seq: int
    token_start: int
    token_end: int
    text: str


class ChunkingEngine:
    """
    Simple token-based text chunking engine

    Splits text into overlapping chunks with configurable parameters.
    Uses whitespace tokenization as a simple proxy for token counting.
    """

    def __init__(self):
        """Initialize with config-driven parameters"""
        config = self._load_chunking_config()
        self.enabled = config.get("enabled", True)
        self.target_tokens = config.get("target_tokens", 640)
        self.overlap_tokens = config.get("overlap_tokens", 64)
        self.threshold_chars = config.get("threshold_chars", 2000)
        self.chunk_kinds = set(config.get("chunk_kinds", []))

        logger.debug(
            f"ChunkingEngine initialized: enabled={self.enabled}, "
            f"target={self.target_tokens}, overlap={self.overlap_tokens}, "
            f"threshold={self.threshold_chars}",
        )

    def _load_chunking_config(self) -> dict:
        """Load chunking configuration from kernel.yaml"""
        try:
            config_path = os.path.join(
                os.path.dirname(__file__),
                "..",
                "..",
                "config",
                "kernel.yaml",
            )
            if os.path.exists(config_path):
                with open(config_path) as f:
                    config = yaml.safe_load(f)
                    if config and "chunking" in config:
                        return config["chunking"]
        except Exception as e:
            logger.debug(f"Could not load chunking config: {e}")

        # Return defaults if config not found
        return {
            "enabled": True,
            "target_tokens": 640,
            "overlap_tokens": 64,
            "threshold_chars": 2000,
            "chunk_kinds": [
                "conversation.transcript",
                "recording.transcript",
                "article.ingested",
                "code.diff",
            ],
        }

    def should_chunk(self, kind: str, text: str) -> bool:
        """
        Determine if content should be chunked

        Args:
            kind: Memory kind
            text: Text content (redacted, not encrypted)

        Returns:
            True if content should be chunked
        """
        if not self.enabled:
            return False

        # Chunk specific kinds OR long content
        if kind in self.chunk_kinds:
            return True

        if len(text) > self.threshold_chars:
            return True

        return False

    def chunk_text(self, text: str) -> list[Chunk]:
        """
        Split text into overlapping chunks

        Uses simple whitespace tokenization as proxy for tokens.
        Preserves sentence boundaries where possible.

        Args:
            text: Text to chunk (redacted plaintext)

        Returns:
            List of Chunk objects with sequential ordering
        """
        if not text or not text.strip():
            return []

        # Simple tokenization: split on whitespace
        tokens = text.split()

        if len(tokens) <= self.target_tokens:
            # No chunking needed
            return [
                Chunk(
                    seq=0,
                    token_start=0,
                    token_end=len(tokens),
                    text=text.strip(),
                ),
            ]

        chunks = []
        seq = 0
        start = 0

        while start < len(tokens):
            # Determine end position
            end = min(start + self.target_tokens, len(tokens))

            # Try to break on sentence boundaries if not at document end
            if end < len(tokens):
                # Look for sentence endings in last 20% of chunk
                search_start = max(start, end - int(self.target_tokens * 0.2))
                sentence_end = self._find_sentence_boundary(tokens, search_start, end)
                if sentence_end > start:
                    end = sentence_end

            # Extract chunk text
            chunk_tokens = tokens[start:end]
            chunk_text = " ".join(chunk_tokens)

            chunks.append(
                Chunk(
                    seq=seq,
                    token_start=start,
                    token_end=end,
                    text=chunk_text.strip(),
                ),
            )

            # Move start forward with overlap
            if end >= len(tokens):
                break

            start = end - self.overlap_tokens
            # Ensure progress (avoid infinite loop)
            start = min(end, start)

            seq += 1

        logger.debug(f"Chunked text into {len(chunks)} chunks (original tokens: {len(tokens)})")
        return chunks

    def _find_sentence_boundary(self, tokens: list[str], start: int, end: int) -> int:
        """
        Find sentence boundary in token range

        Looks for tokens ending with sentence terminators (. ! ?)

        Args:
            tokens: List of tokens
            start: Start index to search
            end: End index to search

        Returns:
            Index after sentence boundary, or -1 if not found
        """
        sentence_pattern = re.compile(r"[.!?]+$")

        # Search backwards from end
        for i in range(end - 1, start - 1, -1):
            token = tokens[i]
            if sentence_pattern.search(token):
                # Found sentence boundary, return position after it
                return i + 1

        return -1


# Module-level singleton for reuse
_chunking_engine: ChunkingEngine | None = None


def get_chunking_engine() -> ChunkingEngine:
    """
    Get or create chunking engine singleton

    Returns:
        ChunkingEngine instance
    """
    global _chunking_engine
    if _chunking_engine is None:
        _chunking_engine = ChunkingEngine()
    return _chunking_engine
