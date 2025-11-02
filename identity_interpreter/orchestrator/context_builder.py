"""
Context Builder
---------------
Builds conversational context for the LLM using stored memories.
"""

from typing import Any


class ContextBuilder:
    """Builds conversational context for the LLM using stored memories."""

    def __init__(self, identity_config: Any | None = None):
        """
        Initialize context builder.

        Args:
            identity_config: Optional identity configuration for memory
        """
        self.memory = None
        if identity_config:
            from identity_interpreter.adapters.memory_manager import MemoryManager

            self.memory = MemoryManager(identity_config)

    def build_prompt_context(self, session_id: str, limit: int = 10) -> str:
        """
        Collects and formats recent memories into an LLM-ready prompt block.

        Args:
            session_id: The session identifier for memory retrieval
            limit: Maximum number of memories to retrieve

        Returns:
            Formatted context string ready for LLM prompt
        """
        if not self.memory:
            return ""

        try:
            return self.memory.build_context(limit=limit)
        except Exception:
            return ""

    def inject_context(self, raw_input: str, session_id: str) -> str:
        """
        Prepends memory context to the raw user input for the orchestrator.

        Args:
            raw_input: The raw user input string
            session_id: The session identifier for memory retrieval

        Returns:
            Context-enriched input string
        """
        context = self.build_prompt_context(session_id)
        return f"{context}\n\nUser: {raw_input}"
