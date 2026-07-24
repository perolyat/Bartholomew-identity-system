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

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from . import policy_engine
from .reflection import ActionReflection, record_action_reflection


if TYPE_CHECKING:
    from .daemon import KernelDaemon
    from .skill_base import SkillResult
    from .skill_registry import SkillRegistry

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

# Scheduler-drive task_ids that are kernel self-maintenance functions
# (bartholomew/kernel/scheduler/drives.py's REGISTRY), not tool/skill
# invocations proposed on a user's behalf. Same reasoning as
# _CONVERSATIONAL_KINDS above, for the surface item 11.2's *first* attempt
# actually broke: evaluate_tool_policy()'s `tool_name` param means "a
# skill_id or scheduler drive task_id", but Identity.yaml's real
# tool_use.allowlist (["web_fetch", "browser_action"], default_allowed:
# false) has never listed a drive task_id and isn't meant to -- these run on
# a kernel-internal cadence, not in response to a user request. Gating them
# against it unconditionally denied every registered drive by default the
# instant identity_path was passed, and because the scheduler's retry loop
# has no backoff on denial, busy-looped the asyncio event loop badly enough
# that /healthz never answered in production (see DECISIONS.md's "tool_use.
# allowlist gates skill/capability execution, not scheduler drives" entry).
# Kinds in this set stay exempt from the Policy Decision check below,
# preserving that already-verified-safe behavior exactly; a *future*
# scheduler-originated action outside this known self-maintenance set (e.g.
# a drive that sends something on the user's behalf) is still evaluated for
# real -- the same room _CONVERSATIONAL_KINDS leaves for a future
# tool-shaped chat action.
_SELF_MAINTENANCE_DRIVES = frozenset(
    {"self_check", "curiosity_probe", "reflection_micro", "fts_optimize"},
)


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

    Also folds in recent conversation history from Working Memory
    (`daemon.working_memory.get_context_string()`). This was a real,
    previously-unnoticed gap: item 11.4 wired goals/persona into the prompt,
    but nothing ever read prior turns' own content back out of Working
    Memory before this -- despite the Reflection stage (below) writing every
    turn into it. `identity_interpreter.orchestrator.context_builder.
    ContextBuilder` was meant to be the thing that injects prior
    conversational memory into the prompt, but it's dead code in production
    today: `bartholomew_api_bridge_v0_1/services/api/app.py` constructs
    `Orchestrator()` with no `identity_config`, so `ContextBuilder.__init__`
    never builds a `MemoryManager` and `build_prompt_context()` always
    returns `""`. Rather than reviving that separate, superseded path (see
    DECISIONS.md's "One authority per architectural concept" entry -- this
    is the same duplicated-concept shape as the four pairs item 11.1 already
    found, just for conversational memory injection specifically), this
    reads it from Working Memory instead -- the authoritative Experience
    owner per COGNITIVE_RUNTIME.md's ownership table, and the same store
    the Reflection stage below already writes every turn into. Called
    *before* this turn's own Reflection write, so it only ever contains
    prior turns.

    Never raises -- if Experience Kernel/persona/working-memory state can't
    be read for any reason, falls back to the raw input unchanged, since a
    missing context enrichment shouldn't ever be able to break the chat
    response itself.
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

    try:
        wm_context = daemon.working_memory.get_context_string()
        if wm_context:
            context_lines.append(f"Recent conversation:\n{wm_context}")
    except Exception:
        logger.exception("Failed to read working memory context for chat interpretation")

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
        # (chat's short-term context buffer; feeds get_context_string()).
        item = daemon.working_memory.add(
            content=f"User: {user_input}\nBartholomew: {response}",
            source="chat",
            tags=["chat", candidate_action.kind],
        )
        working_memory_item_id = item.item_id
        reflection = ActionReflection(
            surface="chat",
            action=candidate_action.kind,
            outcome="responded",
            summary=f"Chat turn ({candidate_action.kind}): responded",
            details={"response_preview": (response or "")[:200]},
        )
    else:
        reflection = ActionReflection(
            surface="chat",
            action=candidate_action.kind,
            outcome="governance_denied",
            summary=f"Chat turn ({candidate_action.kind}): denied by governance",
            details={"reason": governance_reason},
        )

    # Stage 7 (cont.) + Stage 8: Reflection -> Memory. Emit the canonical
    # per-action Reflection into the single shared Memory sink
    # (MemoryStore.reflections) that skill execution also writes -- the same
    # Reflection shape through one sink, for every outcome (responded or
    # denied), closing Exit Gate #4. Best-effort: never breaks the turn.
    # Working Memory's own durability stays its concern
    # (WorkingMemoryManager.persist_snapshot() on KernelDaemon.stop()).
    await record_action_reflection(daemon.mem, reflection)

    return RuntimeContractResult(
        observation=observation,
        interpretation=interpretation,
        candidate_action=candidate_action,
        governance_allowed=governance_allowed,
        governance_reason=governance_reason,
        response=response,
        working_memory_item_id=working_memory_item_id,
    )


async def _record_drive_reflection(
    ctx: Any,
    candidate_action: CandidateAction,
    outcome: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Best-effort Reflection -> Memory tail for a drive tick (Exit Gate #4's
    shared sink, extended to the scheduler surface). `record_action_reflection`
    already swallows and logs any failure, so this is safe to call with a
    minimal duck-typed `ctx` (e.g. tests) that has no real MemoryStore."""
    reflection = ActionReflection(
        surface="scheduler",
        action=candidate_action.kind,
        outcome=outcome,
        summary=f"Scheduler drive ({candidate_action.kind}): {outcome}",
        details=details or {},
    )
    await record_action_reflection(getattr(ctx, "mem", None), reflection)


async def run_drive_through_runtime_contract(
    ctx: Any,
    task_id: str,
    drive_fn: Callable[[Any], Awaitable[Any]],
    *,
    timeout: float,
) -> tuple[Any, int]:
    """
    Trace a scheduler drive through the Runtime Contract seam: Observation ->
    Interpretation -> Executive -> Governance -> Capability -> Execution ->
    Reflection -> Memory. Closes Exit Gate questions #1-3 for the scheduler
    surface (see COGNITIVE_RUNTIME.md's Exit Gate status table): drives
    previously had their own ParkingBrake check but never constructed an
    Observation, were never modeled as a CandidateAction, and never
    consulted the Identity Context -> Policy Decision path at all.

    `ctx` is typed `Any` rather than `KernelDaemon` because
    scheduler/loop.py's own tests exercise this with a minimal duck-typed
    context (just `.mem.db_path`, optionally `.identity_context`) -- the
    same contract `_run_drive` already had.

    Governance is two independent checks:
      1. ParkingBrake("scheduler") -- unchanged from the pre-existing check
         this replaces. Still *raises* RuntimeError on block rather than
         returning a denial, since that's an operator-engaged emergency
         stop, not a routine policy decision -- scheduler/loop.py's
         run_scheduler() and the existing parking-brake integration tests
         both depend on that propagating.
      2. Identity Context -> Policy Decision, skipped for
         `_SELF_MAINTENANCE_DRIVES` (see that constant's docstring for why)
         and skipped entirely when no `identity_context` is wired in --
         additive, no new failure mode for callers that don't opt in, same
         posture as chat's Governance stage (item 11.6).

    Returns (nudge_or_result, success_flag) -- `_run_drive`'s pre-existing
    return shape, preserved so scheduler/loop.py's call site (and the tests
    that import `_run_drive` directly) don't need to change.
    """
    # Stage 1: Observation
    observation = Observation(source="scheduler", raw_content=task_id)

    # Stage 2: Interpretation -- a drive carries no natural-language prompt;
    # its task_id is the whole content.
    interpretation = Interpretation(observation=observation, prompt=task_id)

    # Stage 3: Executive -- propose a candidate action
    candidate_action = CandidateAction(kind=task_id, interpretation=interpretation)

    # Stage 4: Governance -- ParkingBrake, unchanged from the pre-existing
    # check (still raises on block; see docstring above).
    try:
        from bartholomew.orchestrator.safety.parking_brake import BrakeStorage, ParkingBrake

        storage = BrakeStorage(ctx.mem.db_path)
        brake = ParkingBrake(storage)
        if brake.is_blocked("scheduler"):
            raise RuntimeError("ParkingBrake: scheduler blocked")
    except ImportError:
        # Parking brake module not available, continue normally
        pass

    # Governance (cont.) -- Identity Context -> Executive -> Policy Decision,
    # exempting known self-maintenance drives (see _SELF_MAINTENANCE_DRIVES).
    identity_context = getattr(ctx, "identity_context", None)
    if identity_context is not None and candidate_action.kind not in _SELF_MAINTENANCE_DRIVES:
        policy_decision = policy_engine.evaluate_tool_policy(
            identity_context,
            candidate_action.kind,
        )
        if not policy_decision.allowed:
            logger.warning(
                "Scheduler drive %s denied by Identity policy: %s",
                task_id,
                policy_decision.reason,
            )
            await _record_drive_reflection(
                ctx,
                candidate_action,
                outcome="governance_denied",
                details={"reason": policy_decision.reason},
            )
            return None, 0

    # Stage 5+6: Capability + Execution
    try:
        result = await asyncio.wait_for(drive_fn(ctx), timeout=timeout)
        await _record_drive_reflection(ctx, candidate_action, outcome="completed")
        return result, 1
    except asyncio.TimeoutError:
        log_msg = "Drive %s timed out after %.2fs"
        logger.warning(log_msg, task_id, timeout)
        await _record_drive_reflection(ctx, candidate_action, outcome="timeout")
        return None, 0
    except Exception:
        logger.exception("Drive %s crashed", task_id)
        await _record_drive_reflection(ctx, candidate_action, outcome="error")
        return None, 0


async def run_skill_through_runtime_contract(
    registry: SkillRegistry,
    skill_id: str,
    action: str,
    params: dict[str, Any] | None = None,
) -> SkillResult:
    """
    Trace a skill execution through the Runtime Contract seam, by name --
    the production entry point every skill invocation is meant to go
    through (MASTER_PLAN.md item 11.19, closing Exit Gate questions #1-2
    for the skill surface; see COGNITIVE_RUNTIME.md's Exit Gate table).

    Unlike chat (item 11.3, `run_chat_through_runtime_contract()`) and
    scheduler drives (item 11.17, `run_drive_through_runtime_contract()`),
    skill execution already had a real, single choke-point before this --
    `SkillRegistry.execute_action()` -- so there is nothing to reimplement
    here: Observation/Interpretation/CandidateAction construction, the
    Governance stage (ParkingBrake + Identity Context -> Policy Decision,
    evaluated against the constructed CandidateAction), and the unified
    Reflection write into the shared Memory sink all now live *inside*
    `execute_action()` itself (see that method's docstring for the detail).

    This function exists so every surface's production entry point shares
    the same `run_*_through_runtime_contract()` naming convention --
    mirroring `scheduler/loop.py`'s `_run_drive()`, which is exactly this
    shape: a thin, named wrapper around the surface's real seam function.
    `Planner.handle_skill_request()` is this function's sole production
    caller; `execute_action()` remains directly callable (and directly
    tested by ~5 existing test files) as the primitive underneath, not a
    parallel/competing path.
    """
    return await registry.execute_action(skill_id, action, params)
