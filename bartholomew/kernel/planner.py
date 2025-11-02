from __future__ import annotations

from typing import Any

from .state_model import WorldState


class Planner:
    def __init__(self, policy: dict[str, Any], drives: dict[str, Any], mem) -> None:
        self.policy = policy
        self.drives = {d["id"]: d for d in drives.get("drives", [])}
        self.mem = mem

    async def decide(self, state: WorldState) -> dict[str, Any] | None:
        # Planner now delegates proactive nudges to the scheduler
        # This method can be extended for reactive decision-making
        # based on user interactions or external events
        return None
