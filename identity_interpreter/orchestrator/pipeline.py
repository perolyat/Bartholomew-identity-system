"""
Pipeline
--------
Defines ordered orchestration steps executed sequentially.
"""

from collections.abc import Callable
from typing import Any


class Pipeline:
    """Defines ordered orchestration steps executed sequentially."""

    def __init__(self):
        self.steps: list[Callable[[dict[str, Any]], dict[str, Any]]] = []

    def add_step(self, step: Callable[[dict[str, Any]], dict[str, Any]]):
        """Add a step to the pipeline."""
        self.steps.append(step)

    def execute(self, data: dict[str, Any]) -> dict[str, Any]:
        """Execute all pipeline steps sequentially."""
        for step in self.steps:
            data = step(data)
        return data
