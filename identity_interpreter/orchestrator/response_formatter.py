"""
Response Formatter
------------------
Formats LLM responses with optional emotion tags and tone shaping.
"""

from typing import Any


class ResponseFormatter:
    """Formats responses with emotion tags and tone shaping."""

    # Supported tones and emotions
    TONES = ["neutral", "empathetic", "authoritative", "playful"]
    EMOTIONS = ["warm", "neutral", "serious", "enthusiastic"]

    def __init__(self, mode: str = "tags"):
        """
        Initialize the response formatter.

        Args:
            mode: Format mode - "tags" (default) or "structured"
        """
        self.mode = mode

    def format(
        self,
        output: str,
        tone: str | None = None,
        emotion: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """
        Format LLM output with tone and emotion annotations.

        Args:
            output: Raw LLM output string
            tone: Optional tone identifier (neutral, empathetic, etc.)
            emotion: Optional emotion identifier (warm, neutral, etc.)
            metadata: Optional additional metadata

        Returns:
            Formatted string (tags mode) or dict (structured mode)
        """
        if self.mode == "structured":
            return self._format_structured(output, tone, emotion, metadata)
        return self._format_tags(output, tone, emotion)

    def _format_tags(self, output: str, tone: str | None, emotion: str | None) -> str:
        """Format with bracket tags prepended to text."""
        tags = []

        if tone and tone in self.TONES:
            tags.append(f"[tone: {tone}]")

        if emotion and emotion in self.EMOTIONS:
            tags.append(f"[emotion: {emotion}]")

        if tags:
            return f"{' '.join(tags)} {output}"
        return output

    def _format_structured(
        self,
        output: str,
        tone: str | None,
        emotion: str | None,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Format as structured dictionary."""
        return {
            "text": output,
            "tone": tone if tone in self.TONES else "neutral",
            "emotion": emotion if emotion in self.EMOTIONS else "neutral",
            "metadata": metadata or {},
        }

    def set_mode(self, mode: str):
        """Change the formatting mode."""
        if mode in ["tags", "structured"]:
            self.mode = mode
