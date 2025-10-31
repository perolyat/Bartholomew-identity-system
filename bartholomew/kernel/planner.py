from __future__ import annotations
from typing import Dict, Any, Optional

from .state_model import WorldState


class Planner:
    def __init__(
        self,
        policy: Dict[str, Any],
        drives: Dict[str, Any],
        mem
    ) -> None:
        self.policy = policy
        self.drives = {d["id"]: d for d in drives.get("drives", [])}
        self.mem = mem

    async def decide(
        self, state: WorldState
    ) -> Optional[Dict[str, Any]]:
        # Planner now delegates proactive nudges to the scheduler
        # This method can be extended for reactive decision-making
        # based on user interactions or external events
        return None
