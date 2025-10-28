"""Adapter stubs for external integrations"""

from .consent_terminal import ConsentAdapter
from .kill_switch import KillSwitch
from .llm_stub import LLMAdapter
from .metrics_logger import MetricsLogger
from .storage import StorageAdapter
from .tools_stub import ToolsAdapter


__all__ = [
    "LLMAdapter",
    "ToolsAdapter",
    "ConsentAdapter",
    "MetricsLogger",
    "KillSwitch",
    "StorageAdapter",
]
