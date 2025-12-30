"""
Fake orchestrator harness for testing Parking Brake integration.

Provides a minimal test double that simulates skill execution routing
through the parking brake safety gate.
"""

from collections.abc import Callable
from typing import Any

from bartholomew.orchestrator.safety.parking_brake import ParkingBrake


class FakeOrchestrator:
    """
    Minimal test harness for orchestrator skill execution.

    Simulates the pattern of checking ParkingBrake before executing skills.
    """

    def __init__(self, brake: ParkingBrake, skills: dict[str, Callable[..., Any]]):
        """
        Initialize fake orchestrator.

        Args:
            brake: ParkingBrake instance to gate skill execution
            skills: Registry of skill callables by name
        """
        self.brake = brake
        self.skills = skills

    def call_skill(self, name: str, *args, **kwargs):
        """
        Execute a skill if not blocked by parking brake.

        Args:
            name: Skill name to execute
            *args: Positional arguments for the skill
            **kwargs: Keyword arguments for the skill

        Returns:
            Result from the skill callable

        Raises:
            RuntimeError: If skills are blocked by parking brake
        """
        if self.brake.is_blocked(scope="skills"):
            raise RuntimeError("Skills blocked by Parking Brake")
        return self.skills[name](*args, **kwargs)
