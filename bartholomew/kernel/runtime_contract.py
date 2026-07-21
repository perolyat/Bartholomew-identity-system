"""
Runtime Contract
================

The Observation -> Interpretation -> Executive -> Governance -> Capability ->
Execution -> Reflection -> Memory pipeline shape, established as a real code
seam. Per MASTER_PLAN.md's "P2.5 -- Runtime Convergence" (item 11.3,
Principle Zero/Principle One): every interaction is meant to traverse this
same shape, regardless of origin.

This module wires the chat input surface through the seam first, per the
milestone's plan. Other surfaces (voice, sight, scheduler-originated
initiatives) can plug into the same stage sequence later without a
redesign -- `run_chat_through_runtime_contract()` takes a `respond_fn`
dependency rather than a hard-coded chat backend for exactly this reason.

Scope note (learned from item 11.2's live-scheduler regression -- see
DECISIONS.md's "tool_use.allowlist gates skill/capability execution, not
scheduler drives" entry): this seam is exposed as an explicit, directly
callable function here, not wired as `/api/chat`'s default live behavior in
this same change. Flipping a live production default needs its own
live-smoke-verification pass, the same discipline that caught the scheduler
regression -- that's a deliberate, tracked follow-up, not an oversight.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from . import policy_engine


if TYPE_CHECKING:
    from .daemon import KernelDaemon

logger = logging.getLogger(__name__)

# CandidateAction kinds that are plain conversation, not a tool/skill
# invocation. evaluate_tool_policy()'s `tool_name` param means "a skill_id or
# scheduler drive task_id" (see policy_engine.py's docstring) -- gating
# ordinary chat replies against Identity.yaml's tool_use.allowlist would be
# the same category error item 11.2's scheduler-drive wiring made and had to
# revert (confirmed by direct reading of Identity.yaml: tool_use.allowlist is
# only ["web_fetch", "browser_action"] with default_allowed: false, and the
# live API bridge already passes identity_path="Identity.yaml" by default --
# so an unconditional check here would deny 100% of chat turns in production
# the moment this landed). Kinds in this set are exempt from the Policy
# Decision check below; anything else (a future tool/skill-shaped candidate
# action proposed during a chat turn) is evaluated for real.
_CONVERSATIONAL_KINDS = frozenset({"chat_response"})


@dataclass(frozen=True)
class Observation:
    """Stage 1: a raw external stimulus, wrapped with provenance."""

    source: str
    raw_content: str
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class Interpretation:
    """Stage 2: the observation, given structure/context."""

    observation: Observation
    prompt: str


@dataclass(frozen=True)
class CandidateAction:
    """Stage 3 (Executive): a proposed action, not yet governed or executed."""

    kind: str
    interpretation: Interpretation


@dataclass
class RuntimeContractResult:
    """Everything produced by one full pass through the Runtime Contract."""

    observation: Observation
    interpretation: Interpretation
    candidate_action: CandidateAction
    governance_allowed: bool
    governance_reason: str | None
    response: str | None
    working_memory_item_id: str | None


def _build_interpretation(daemon: KernelDaemon, observation: Observation) -> Interpretation:
    """
    Stage 2: give the raw observation structure/context -- specifically,
    persisted Experience Kernel state (active goals, active persona) that
    previously never reached chat at all. This is what makes "a chat turn
    can reference persisted persona/goal state from a previous turn" (item
    11.4's acceptance criterion) genuinely true, not just structurally
    possible: the enriched prompt is what actually gets sent to `respond_fn`.

    Never raises -- if Experience Kernel/persona state can't be read for any
    reason, falls back to the raw input unchanged, since a missing context
    enrichment shouldn't ever be able to break the chat response itself.
    """
    context_lines: list[str] = []
    try:
        goals = daemon.experience.get_active_goals()
        if goals:
            context_lines.append(f"Active goals: {', '.join(goals)}")
    except Exception:
        logger.exception("Failed to read active goals for chat interpretation")

    try:
        pack_id = daemon.persona_manager.get_active_pack_id()
        if pack_id:
            context_lines.append(f"Active persona: {pack_id}")
    except Exception:
        logger.exception("Failed to read active persona for chat interpretation")

    if not context_lines:
        return Interpretation(observation=observation, prompt=observation.raw_content)

    # No "User:" label here -- respond_fn's own backend (e.g. the chat
    # orchestrator's inject_memory_context() step) applies its own "User: "
    # wrapping around whatever prompt it receives; adding one here too
    # produced a visibly doubled "User: ... User: ..." prefix in the actual
    # response, caught during this change's own live-smoke verification.
    prompt = "\n".join(context_lines) + f"\n\n{observation.raw_content}"
    return Interpretation(observation=observation, prompt=prompt)


async def run_chat_through_runtime_contract(
    daemon: KernelDaemon,
    user_input: str,
    respond_fn: Callable[[str], Awaitable[str]],
) -> RuntimeContractResult:
    """
    Trace a chat message through the full Runtime Contract seam:
    Observation -> Interpretation -> Executive -> Governance -> Capability ->
    Execution -> Reflection -> Memory.

    Args:
        daemon: the KernelDaemon whose ParkingBrake/ExperienceKernel/
            PersonaPackManager/WorkingMemory this pass consults and updates.
        user_input: the raw chat message.
        respond_fn: an async callable (prompt) -> response implementing the
            Capability/Execution stages for chat (e.g. a model-routing
            function). Injected rather than hard-imported so this seam
            doesn't couple to -- or duplicate -- the chat pipeline's own
            mock-LLM/real-LLM routing concerns.

    Returns:
        RuntimeContractResult capturing every stage's output.
    """
    # Stage 1: Observation
    observation = Observation(source="chat", raw_content=user_input)

    # Stage 2: Interpretation -- enriched with persisted Experience Kernel
    # state (active goals, active persona), so it can genuinely be
    # referenced by the response, not just theoretically available.
    interpretation = _build_interpretation(daemon, observation)

    # Stage 3: Executive -- propose a candidate action
    candidate_action = CandidateAction(kind="chat_response", interpretation=interpretation)

    # Stage 4: Governance -- fail-closed ParkingBrake("skills") check, same
    # gate skill-execution and the chat orchestrator's own handle_input()
    # already use.
    governance_allowed = True
    governance_reason: str | None = None
    try:
        from bartholomew.orchestrator.safety.parking_brake import BrakeStorage, ParkingBrake

        storage = BrakeStorage(daemon.mem.db_path)
        brake = ParkingBrake(storage)
        if brake.is_blocked("skills"):
            governance_allowed = False
            governance_reason = "Blocked by parking brake (scope=skills)"
    except Exception:
        logger.exception("Governance check failed; failing closed")
        governance_allowed = False
        governance_reason = "Governance check errored"

    # Governance -- Identity Context -> Executive -> Policy Decision (item
    # 11.2's mechanism, now genuinely consulted by chat's Governance stage
    # too, closing the gap COGNITIVE_RUNTIME.md's Exit Gate table named).
    # Skipped for plain conversational kinds (see _CONVERSATIONAL_KINDS)
    # and, like SkillRegistry.execute_action(), skipped entirely when no
    # IdentityContext was wired in -- additive, not a new failure mode for
    # callers that don't opt in.
    if (
        governance_allowed
        and daemon.identity_context is not None
        and candidate_action.kind not in _CONVERSATIONAL_KINDS
    ):
        policy_decision = policy_engine.evaluate_tool_policy(
            daemon.identity_context,
            candidate_action.kind,
        )
        if not policy_decision.allowed:
            governance_allowed = False
            governance_reason = f"Denied by Identity policy: {policy_decision.reason}"

    response: str | None = None
    working_memory_item_id: str | None = None

    if governance_allowed:
        # Stage 5+6: Capability + Execution
        response = await respond_fn(interpretation.prompt)

        # Stage 7: Reflection -- record the interaction in Working Memory
        item = daemon.working_memory.add(
            content=f"User: {user_input}\nBartholomew: {response}",
            source="chat",
            tags=["chat", candidate_action.kind],
        )
        working_memory_item_id = item.item_id

    # Stage 8: Memory -- durability is Working Memory's own concern
    # (WorkingMemoryManager.persist_snapshot(), invoked by KernelDaemon.stop());
    # no separate write here.

    return RuntimeContractResult(
        observation=observation,
        interpretation=interpretation,
        candidate_action=candidate_action,
        governance_allowed=governance_allowed,
        governance_reason=governance_reason,
        response=response,
        working_memory_item_id=working_memory_item_id,
    )
