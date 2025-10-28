"""
Pipeline
--------
Defines ordered orchestration steps executed sequentially.
"""
from typing import Any, Dict, Callable, List


class Pipeline:
    """Defines ordered orchestration steps executed sequentially."""

    def __init__(self):
        self.steps: List[Callable[[Dict[str, Any]], Dict[str, Any]]] = []

    def add_step(self, step: Callable[[Dict[str, Any]], Dict[str, Any]]):
        """Add a step to the pipeline."""
        self.steps.append(step)

    def execute(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute all pipeline steps sequentially."""
        for step in self.steps:
            data = step(data)
        return data
