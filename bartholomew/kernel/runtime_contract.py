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
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from . import policy_engine
from .memory.privacy_guard import get_consent_handler
from .reflection import ActionReflection, record_action_reflection

if TYPE_CHECKING:
    from identity_interpreter.identity_context import IdentityContext

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

    # Stage 4: Governance -- fail-closed Parking Brake "skills" check, same
    # gate skill-execution uses. Reads through the daemon's shared
    # GovernanceStore (Phase B stage B4); the B4-B6 dual-check bridge
    # against the legacy system_flags value was retired in B6, once
    # bartholomew/cli.py's `brake on`/`brake off` moved onto GovernanceStore.
    # The chat orchestrator's own handle_input() no longer runs a redundant
    # second check on this path -- see app.py's _respond() closure.
    governance_allowed = True
    governance_reason: str | None = None
    try:
        from bartholomew.orchestrator.safety.governance_store import (
            is_blocked_fail_closed_off_loop,
        )

        blocked = await is_blocked_fail_closed_off_loop(
            "skills",
            daemon.mem.db_path,
            governance_store=getattr(daemon, "governance_store", None),
            executor=getattr(daemon, "blocking_executor", None),
        )
        if blocked:
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

    # Stage 4: Governance -- Parking Brake, unchanged from the pre-existing
    # check (still raises on block; see docstring above). Reads through
    # the shared GovernanceStore when ctx has one (Phase B stage B4); the
    # B4-B6 dual-check bridge against the legacy system_flags value was
    # retired in B6.
    try:
        from bartholomew.orchestrator.safety.governance_store import (
            is_blocked_fail_closed_off_loop,
        )

        # ctx may be the minimal duck-typed context scheduler/loop.py's own
        # tests use (just .mem.db_path, optionally .identity_context) --
        # getattr falls back to is_blocked_fail_closed_off_loop()'s own
        # temporary-instance/asyncio.to_thread() fallbacks rather than
        # requiring every such ctx to grow governance_store/
        # blocking_executor attributes.
        blocked = await is_blocked_fail_closed_off_loop(
            "scheduler",
            ctx.mem.db_path,
            governance_store=getattr(ctx, "governance_store", None),
            executor=getattr(ctx, "blocking_executor", None),
        )
        if blocked:
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


# =============================================================================
# Voice / Sight device surfaces (MASTER_PLAN.md item 11.21, closing Exit Gate
# questions #1-3 for the two remaining surfaces).
#
# Distinct, stable action kinds for a SINGLE device-start attempt. Deliberately
# NOT generic skill kinds, and deliberately suffixed "_start" to encode that
# governance approval here authorizes exactly one capture/stream *initiation* --
# never indefinite or continuing microphone/camera access. Continuous sessions,
# consent renewal, revocation, stop/teardown semantics, streaming lifecycle and
# device-driver behaviour are all Stage 6 work (see COGNITIVE_RUNTIME.md's
# "Device surfaces" note). In particular, safely *stopping* a future active
# capture session must never depend on obtaining permission to *continue*
# capturing -- a Stage 6 requirement recorded, not implemented here.
# =============================================================================

_SIGHT_CAPTURE_KIND = "sight_capture_start"
_VOICE_STREAM_KIND = "voice_stream_start"


@dataclass
class DeviceRuntimeResult:
    """Outcome of one voice/sight start attempt through the Runtime Contract.

    `governance_allowed` is whether every Governance gate (parking brake,
    Identity Policy, device consent) passed. `started` is whether the
    underlying capability then actually executed to completion -- these differ
    only when execution itself raised after governance approved (outcome
    "error"). `outcome` is one of: "started", "parking_brake_denied",
    "governance_denied", "consent_denied", "error".
    """

    observation: Observation
    candidate_action: CandidateAction
    governance_allowed: bool
    started: bool
    outcome: str
    reason: str | None
    result: Any


def _resolve_device_db_path(db_path: str | None) -> str | None:
    """The db path used for the parking-brake read and the Reflection write.
    Mirrors the pre-existing stubs' own `db_path or _default_db_path()`
    resolution, kept lazy to avoid importing daemon at module load."""
    if db_path:
        return db_path
    try:
        from .daemon import _default_db_path

        return _default_db_path()
    except Exception:
        logger.exception("Failed to resolve default db path for device surface")
        return None


async def _record_device_reflection(
    db_path: str | None,
    surface: str,
    kind: str,
    outcome: str,
    reason: str | None,
) -> None:
    """Exactly one ActionReflection into the shared Memory sink for a
    voice/sight start attempt -- every outcome (started or any denial/error).
    Best-effort: `record_action_reflection` swallows and logs any failure, so
    a missing/uninitialised store never breaks the surface (same posture as
    `_record_drive_reflection`)."""
    mem = None
    if db_path:
        try:
            from .memory_store import MemoryStore

            mem = MemoryStore(db_path)
        except Exception:
            logger.exception("Failed to construct MemoryStore for device reflection")
            mem = None
    reflection = ActionReflection(
        surface=surface,
        action=kind,
        outcome=outcome,
        summary=f"{surface.capitalize()} {kind}: {outcome}",
        details={"reason": reason} if reason else {},
    )
    await record_action_reflection(mem, reflection)


async def _resolve_device_consent(device_label: str) -> tuple[bool, str, str | None]:
    """Fail-closed device consent for a single voice/sight start attempt.

    Returns (allowed, outcome, reason). Reuses the one interactive consent
    channel (`privacy_guard.get_consent_handler()`) that skill "ask"
    permissions also use -- not a second consent mechanism. An absent handler,
    a declined request, or an unresolved (falsy) ask result all deny. Shared
    by both device seams *only* for this fail-closed check itself; each seam
    still runs its own brake/policy gates inline. Grants authorize a single
    start attempt only -- never continuing access (see the module note above)."""
    handler = get_consent_handler()
    if handler is None:
        return False, "consent_denied", "No consent handler registered (fail-closed)"
    approved = handler(f"Bartholomew requests to start {device_label} (single start attempt)")
    if inspect.isawaitable(approved):
        approved = await approved
    if not approved:
        return False, "consent_denied", "Device consent declined or unresolved"
    return True, "started", None


async def run_sight_through_runtime_contract(
    *,
    db_path: str | None = None,
    capture_fn: Callable[[], Any] | None = None,
    identity_context: IdentityContext | None = None,
) -> DeviceRuntimeResult:
    """
    Trace a single sight-capture *start* through the Runtime Contract seam:
    Observation -> Interpretation -> Executive -> Governance -> Capability ->
    Execution -> Reflection -> Memory. The governed production entry point for
    the sight surface (item 11.21).

    Governance runs three gates, all strictly before `capture_fn` is ever
    called:
      1. ParkingBrake("sight") -- unchanged from the pre-existing stub's own
         check, including its `except ImportError: pass` tolerance.
      2. Identity Policy Decision (`evaluate_tool_policy`, kind
         "sight_capture_start"). Additive: skipped when no `IdentityContext`
         is wired in, matching chat/scheduler/skill. Under real `Identity.yaml`
         (`tool_use.allowlist = [web_fetch, browser_action]`, default_allowed
         false), this kind is denied by default -- the safe outcome, since
         nothing live calls this path yet.
      3. Device consent -- ALWAYS required for a device start (these are
         exactly `policy.yaml`'s `safety.do_not: record audio/video without
         explicit approval` category). Fail-closed via the one interactive
         consent channel (`privacy_guard.get_consent_handler()`) that skill
         "ask" permissions also reuse: an absent handler, a declined request,
         or an unresolved (falsy) result all deny.

    Only if all three pass does `capture_fn` run, exactly once. Every outcome
    -- success or any denial/error -- produces exactly one ActionReflection
    into the shared sink.

    `capture_fn` is injected (like chat's `respond_fn` / drive's `drive_fn`)
    rather than imported, so this seam owns Governance while the surface owns
    its (currently inert) capability -- and so the capability is reachable
    *only* through this governed path.
    """
    observation = Observation(source="sight", raw_content=_SIGHT_CAPTURE_KIND)
    interpretation = Interpretation(observation=observation, prompt=observation.raw_content)
    candidate_action = CandidateAction(kind=_SIGHT_CAPTURE_KIND, interpretation=interpretation)
    resolved_db_path = _resolve_device_db_path(db_path)

    allowed = True
    outcome = "started"
    reason: str | None = None

    # Governance gate 1: ParkingBrake("sight"), preserving the pre-existing
    # ImportError tolerance exactly.
    try:
        from bartholomew.orchestrator.safety.parking_brake import (
            BrakeStorage,
            construct_parking_brake_off_loop,
        )

        if resolved_db_path is not None:
            # No owning daemon instance here -- construct_parking_brake_off_loop
            # falls back to a one-off asyncio.to_thread() (see
            # run_off_loop()'s docstring), still off the event loop.
            brake = await construct_parking_brake_off_loop(BrakeStorage(resolved_db_path))
            if brake.is_blocked("sight"):
                allowed = False
                outcome = "parking_brake_denied"
                reason = "Blocked by parking brake (scope=sight)"
    except ImportError:
        pass

    # Governance gate 2: Identity Policy Decision (additive; see docstring).
    if allowed and identity_context is not None:
        decision = policy_engine.evaluate_tool_policy(identity_context, candidate_action.kind)
        if not decision.allowed:
            allowed = False
            outcome = "governance_denied"
            reason = f"Denied by Identity policy: {decision.reason}"

    # Governance gate 3: device consent -- always required, fail-closed.
    if allowed:
        allowed, outcome, reason = await _resolve_device_consent("camera capture")

    # Capability + Execution -- reached only after all three gates allowed.
    governance_allowed = allowed
    started = False
    capture_result: Any = None
    if governance_allowed:
        try:
            capture_result = capture_fn() if capture_fn is not None else None
            if inspect.isawaitable(capture_result):
                capture_result = await capture_result
            outcome, reason, started = "started", None, True
        except Exception as exc:
            logger.exception("Sight capture failed after governance approval")
            outcome, reason, started = "error", str(exc), False

    await _record_device_reflection(
        resolved_db_path,
        "sight",
        candidate_action.kind,
        outcome,
        reason,
    )

    return DeviceRuntimeResult(
        observation=observation,
        candidate_action=candidate_action,
        governance_allowed=governance_allowed,
        started=started,
        outcome=outcome,
        reason=reason,
        result=capture_result,
    )


async def run_voice_through_runtime_contract(
    *,
    db_path: str | None = None,
    stream_fn: Callable[[], Any] | None = None,
    identity_context: IdentityContext | None = None,
) -> DeviceRuntimeResult:
    """
    Trace a single voice-stream *start* through the Runtime Contract seam --
    the governed production entry point for the voice surface (item 11.21).

    Identical governance shape to `run_sight_through_runtime_contract()`
    (parking brake, then additive Identity Policy, then always-required
    fail-closed device consent -- all before `stream_fn` runs), for the
    "voice" scope / "voice_stream_start" kind / "microphone" device. The
    per-surface gates (brake scope, policy kind) are written inline in each
    seam rather than shared, so a change to one surface's gate cannot silently
    weaken the other's; only the fail-closed consent *primitive*
    (`_resolve_device_consent`) is shared, since getting fail-closed exactly
    right in one place is safer than duplicating it.
    """
    observation = Observation(source="voice", raw_content=_VOICE_STREAM_KIND)
    interpretation = Interpretation(observation=observation, prompt=observation.raw_content)
    candidate_action = CandidateAction(kind=_VOICE_STREAM_KIND, interpretation=interpretation)
    resolved_db_path = _resolve_device_db_path(db_path)

    allowed = True
    outcome = "started"
    reason: str | None = None

    # Governance gate 1: ParkingBrake("voice"), preserving ImportError tolerance.
    try:
        from bartholomew.orchestrator.safety.parking_brake import (
            BrakeStorage,
            construct_parking_brake_off_loop,
        )

        if resolved_db_path is not None:
            # No owning daemon instance here -- construct_parking_brake_off_loop
            # falls back to a one-off asyncio.to_thread() (see
            # run_off_loop()'s docstring), still off the event loop.
            brake = await construct_parking_brake_off_loop(BrakeStorage(resolved_db_path))
            if brake.is_blocked("voice"):
                allowed = False
                outcome = "parking_brake_denied"
                reason = "Blocked by parking brake (scope=voice)"
    except ImportError:
        pass

    # Governance gate 2: Identity Policy Decision (additive; see sight docstring).
    if allowed and identity_context is not None:
        decision = policy_engine.evaluate_tool_policy(identity_context, candidate_action.kind)
        if not decision.allowed:
            allowed = False
            outcome = "governance_denied"
            reason = f"Denied by Identity policy: {decision.reason}"

    # Governance gate 3: device consent -- always required, fail-closed.
    if allowed:
        allowed, outcome, reason = await _resolve_device_consent("microphone streaming")

    # Capability + Execution -- reached only after all three gates allowed.
    governance_allowed = allowed
    started = False
    stream_result: Any = None
    if governance_allowed:
        try:
            stream_result = stream_fn() if stream_fn is not None else None
            if inspect.isawaitable(stream_result):
                stream_result = await stream_result
            outcome, reason, started = "started", None, True
        except Exception as exc:
            logger.exception("Voice stream failed after governance approval")
            outcome, reason, started = "error", str(exc), False

    await _record_device_reflection(
        resolved_db_path,
        "voice",
        candidate_action.kind,
        outcome,
        reason,
    )

    return DeviceRuntimeResult(
        observation=observation,
        candidate_action=candidate_action,
        governance_allowed=governance_allowed,
        started=started,
        outcome=outcome,
        reason=reason,
        result=stream_result,
    )
