"""Adapter stubs for external integrations"""

from .consent_terminal import ConsentAdapter
from .llm_stub import LLMAdapter
from .metrics_logger import MetricsLogger
from .storage import StorageAdapter
from .tools_stub import ToolsAdapter

__all__ = [
    "LLMAdapter",
    "ToolsAdapter",
    "ConsentAdapter",
    "MetricsLogger",
    "StorageAdapter",
]
