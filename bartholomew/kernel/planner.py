from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .skill_base import SkillResult
from .state_model import WorldState


if TYPE_CHECKING:
    from .skill_registry import SkillRegistry


class Planner:
    def __init__(
        self,
        policy: dict[str, Any],
        drives: dict[str, Any],
        mem,
        skill_registry: SkillRegistry | None = None,
    ) -> None:
        self.policy = policy
        self.drives = {d["id"]: d for d in drives.get("drives", [])}
        self.mem = mem
        self._skill_registry = skill_registry

    async def decide(self, state: WorldState) -> dict[str, Any] | None:
        # Planner now delegates proactive nudges to the scheduler
        # This method can be extended for reactive decision-making
        # based on user interactions or external events
        return None

    def set_skill_registry(self, skill_registry: SkillRegistry | None) -> None:
        """
        Attach (or detach) a SkillRegistry after construction.

        Needed because daemon.py constructs Planner before the Stage 3/4
        modules (SkillRegistry depends on ExperienceKernel/GlobalWorkspace,
        which are constructed after Planner in the current init order).
        """
        self._skill_registry = skill_registry

    async def handle_skill_request(
        self,
        skill_id: str,
        action: str,
        params: dict[str, Any] | None = None,
    ) -> SkillResult:
        """
        prompt -> decide -> tool call (with consent) -> persisted + audited.

        This is the "reactive decision-making based on user interactions"
        path decide()'s own docstring already anticipated, but nothing
        previously implemented: Planner never called into skill_registry at
        all, so there was no route from a user-facing request to an
        actual skill execution. The "decide" step here is routing/
        validation -- confirming the request names a real, loaded skill
        and action before it goes any further -- while the actual
        Observation/CandidateAction construction, Governance (parking-brake
        check, Identity policy decision), consent-resolution, execution, and
        audit trail all live in SkillRegistry.execute_action() (the single
        choke-point every skill execution flows through, regardless of
        caller).

        Calls `runtime_contract.run_skill_through_runtime_contract()` --
        not `SkillRegistry.execute_action()` directly -- so this, the one
        production route into skill execution, goes through the same named
        Runtime Contract seam chat and scheduler drives use (MASTER_PLAN.md
        item 11.19).

        Args:
            skill_id: ID of the skill to invoke
            action: Action name to execute on that skill
            params: Parameters for the action

        Returns:
            SkillResult describing the outcome
        """
        if self._skill_registry is None:
            return SkillResult.fail("No skill registry configured")

        manifest = self._skill_registry.get_manifest(skill_id)
        if manifest is None:
            return SkillResult.fail(f"Unknown skill: {skill_id}")

        if skill_id not in self._skill_registry.list_loaded():
            return SkillResult.fail(f"Skill not loaded: {skill_id}")

        if manifest.get_action(action) is None:
            return SkillResult.fail(f"Unknown action '{action}' for skill '{skill_id}'")

        from .runtime_contract import run_skill_through_runtime_contract

        return await run_skill_through_runtime_contract(
            self._skill_registry,
            skill_id,
            action,
            params,
        )
