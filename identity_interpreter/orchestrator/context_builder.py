"""
Context Builder
---------------
Builds conversational context for the LLM using stored memories.

STATUS (2026-07-21): unreachable in production -- `Orchestrator()` is
constructed with no `identity_config` in
`bartholomew_api_bridge_v0_1/services/api/app.py`, so `self.memory` here is
always `None` and `build_prompt_context()`/`inject_context()` are no-ops.
`bartholomew.kernel.runtime_contract`'s Interpretation stage now injects
recent conversation history from Working Memory instead. See
`identity_interpreter/adapters/memory_manager.py`'s module docstring for
the full writeup.

CONSTRUCTION (2026-08-17): the `MemoryManager` is built lazily, on first
use, rather than in `__init__`. `MemoryManager.__init__` reaches the OS
keystore for its encryption key and raises when none is available, so
building it eagerly made *constructing* anything that owns a ContextBuilder
impossible on a headless host. That is what stopped `ReflectionGenerator`
-- which needs `build_prompt_context()` and therefore cannot simply switch
to `Orchestrator(model_identity_config=...)` -- from being constructed at
all outside a desktop session. Deferring the build moves the keystore
requirement from "any construction" to "actually asking for memory
context", where it belongs and where this class already degrades to `""`.
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
        self._identity_config = identity_config
        #: Resolved on first `memory` access. `None` means "not yet built";
        #: `False` means "tried and failed, do not retry".
        self._memory: Any | None | bool = None if identity_config else False

    @property
    def memory(self) -> Any | None:
        """The MemoryManager, built on first use, or None when unavailable.

        Failure is cached: a missing keystore does not become one retry --
        and one log line -- per reflection.
        """
        if self._memory is False:
            return None
        if self._memory is None:
            from identity_interpreter.adapters.memory_manager import MemoryManager

            try:
                self._memory = MemoryManager(self._identity_config)
            except Exception:
                self._memory = False
                return None
        return self._memory

    def build_prompt_context(self, session_id: str, limit: int = 10) -> str:
        """
        Collects and formats recent memories into an LLM-ready prompt block.

        Args:
            session_id: The session identifier for memory retrieval
            limit: Maximum number of memories to retrieve

        Returns:
            Formatted context string ready for LLM prompt, or "" when no
            memory source is available.
        """
        memory = self.memory
        if not memory:
            return ""

        try:
            return memory.build_context(limit=limit)
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
