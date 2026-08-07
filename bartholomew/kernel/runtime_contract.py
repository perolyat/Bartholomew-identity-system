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
import calendar
import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from . import policy_engine
from .blocking_executor import run_off_loop
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
#
# "initiative_sweep" (S5.2, docs/S5_2_TYPED_CADENCE_DESIGN.md Sec 6) joins
# this set for the same reason: it only walks the Initiative store for rows
# already past their expires_at and transitions them to "expired" -- it
# never proposes new outbound contact, only closes out old rows. Unlike
# "awaiting_response_check" below, this is the *drive's own tick* being
# exempted here; the `expire` transition it dispatches has its own,
# separate exemption from Identity Policy inside
# run_initiative_through_runtime_contract() itself
# (_SELF_MAINTENANCE_INITIATIVE_TRANSITIONS), since a scheduler drive being
# self-maintenance-shaped and an individual Initiative transition being
# self-maintenance-shaped are different questions answered in different
# places (see that constant's own docstring).
#
# "initiative_delivery_check" (S5.3, docs/S5_3_DEFAULT_OFF_CONSENT_AND_MUTE_
# DESIGN.md Sec 4/8) joins this set for the same drive-tick-level reasoning
# as "initiative_sweep" above: deciding *whether to check* an initiative's
# delivery eligibility is not itself outbound contact. This is deliberately
# NOT the same question as whether the `deliver`/`defer`/`cancel`
# transitions this drive dispatches are exempt -- they are not (only
# `expire` has a transition-level exemption, via
# _SELF_MAINTENANCE_INITIATIVE_TRANSITIONS below), so every actual
# proposal/delivery decision this drive makes still passes through
# Identity Policy and consent for real.
_SELF_MAINTENANCE_DRIVES = frozenset(
    {
        "self_check",
        "curiosity_probe",
        "reflection_micro",
        "fts_optimize",
        "initiative_sweep",
        "initiative_delivery_check",
    },
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

        # Stage 1, S1.4 (design doc Sec 7): a chat reply is one of the two
        # awaiting_response resolution paths. Best-effort and additive --
        # never breaks the chat turn itself -- gated by the daemon actually
        # having an awaiting_response_store wired in (start()-time only), so
        # existing duck-typed test contexts are unaffected.
        if getattr(daemon, "awaiting_response_store", None) is not None:
            await _maybe_auto_resolve_awaiting_response(daemon, item.item_id)
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


# =============================================================================
# awaiting_response obligation queue (Stage 1, S1.4; see
# docs/S1_4_AWAITING_RESPONSE_DESIGN.md). Every open/remind/escalate/resolve
# transition traverses this same Observation -> Interpretation -> Executive ->
# Governance -> Capability -> Execution -> Reflection -> Memory shape --
# COGNITIVE_RUNTIME.md's requirement that "creating/escalating/resolving an
# entry traverses the full Runtime Contract, not a side channel."
# =============================================================================

_AWAITING_RESPONSE_TRANSITIONS = frozenset({"open", "remind", "escalate", "resolve"})

_AWAITING_RESPONSE_OUTCOME_BY_TRANSITION = {
    "open": "opened",
    "remind": "reminded",
    "escalate": "escalated",
    "resolve": "resolved",
}


@dataclass
class AwaitingResponseRuntimeResult:
    """Outcome of one awaiting_response transition through the Runtime
    Contract. `outcome` is one of: "opened", "reminded", "escalated",
    "resolved", "parking_brake_denied", "governance_denied", "error"."""

    observation: Observation
    candidate_action: CandidateAction
    governance_allowed: bool
    outcome: str
    reason: str | None
    entry: Any


async def _record_awaiting_response_reflection(
    ctx: Any,
    candidate_action: CandidateAction,
    outcome: str,
    entry: Any,
    reason: str | None,
) -> None:
    """Exactly one ActionReflection per transition, into the shared Memory
    sink -- closes COGNITIVE_RUNTIME.md's "every transition remains
    auditable" requirement without a bespoke audit mechanism competing with
    the one Reflection already provides (the awaiting_response_audit table
    is the queryable per-entry detail view; this is the cross-surface
    stream). Best-effort: record_action_reflection swallows and logs any
    failure of its own."""
    details: dict[str, Any] = {}
    if entry is not None:
        details["entry_id"] = entry.id
        details["status"] = entry.status
    if reason:
        details["reason"] = reason
    reflection = ActionReflection(
        surface="awaiting_response",
        action=candidate_action.kind,
        outcome=outcome,
        summary=f"Awaiting-response ({candidate_action.kind}): {outcome}",
        details=details,
    )
    await record_action_reflection(getattr(ctx, "mem", None), reflection)


async def _notify_awaiting_response(ctx: Any, entry: Any, transition: str) -> None:
    """Reminder/escalation delivery delegates to the existing governed
    NotifySkill path (design doc Sec 5) rather than a second notification
    mechanism -- reusing S1.3's quiet-hours/mute enforcement.
    SkillRegistry.execute_action() runs its own independent Governance pass
    on skill_id="notify" (brake + skill_permissions.py + Identity Policy on
    "notify" itself); that is additive to, not a substitute for, this
    seam's own kind-based check above. Best-effort: a delivery failure must
    not undo the state transition that already committed."""
    if entry is None:
        return
    priority = "high" if transition == "escalate" else "normal"
    title = (
        "Still waiting on your reply"
        if transition == "escalate"
        else "Bartholomew is waiting on your reply"
    )
    try:
        await run_skill_through_runtime_contract(
            ctx.skill_registry,
            "notify",
            "send",
            {"message": entry.subject, "title": title, "priority": priority},
        )
    except Exception:
        logger.exception(
            "Failed to deliver awaiting_response %s notification for entry %s",
            transition,
            getattr(entry, "id", None),
        )


async def _execute_awaiting_response_transition(
    ctx: Any,
    store: Any,
    transition: str,
    *,
    entry_id: int | None,
    subject: str | None,
    origin_surface: str | None,
    context_ref: str | None,
    due_at: str | None,
    resolution: str | None,
    actor: str | None,
) -> Any:
    executor = getattr(ctx, "blocking_executor", None)
    if transition == "open":
        if not subject or not origin_surface:
            raise ValueError("awaiting_response 'open' requires subject and origin_surface")
        return await run_off_loop(
            store.open,
            subject=subject,
            origin_surface=origin_surface,
            context_ref=context_ref,
            due_at=due_at,
            actor=actor,
            executor=executor,
        )
    if entry_id is None:
        raise ValueError(f"awaiting_response {transition!r} requires entry_id")
    if transition == "remind":
        return await run_off_loop(store.remind, entry_id, actor=actor, executor=executor)
    if transition == "escalate":
        return await run_off_loop(store.escalate, entry_id, actor=actor, executor=executor)
    return await run_off_loop(
        store.resolve,
        entry_id,
        resolution=resolution or "manual",
        actor=actor,
        executor=executor,
    )


async def run_awaiting_response_through_runtime_contract(
    ctx: Any,
    transition: str,
    *,
    entry_id: int | None = None,
    subject: str | None = None,
    origin_surface: str | None = None,
    context_ref: str | None = None,
    due_at: str | None = None,
    resolution: str | None = None,
    actor: str | None = None,
) -> AwaitingResponseRuntimeResult:
    """
    Trace one awaiting_response transition through the Runtime Contract
    seam. `transition` is one of "open" (requires subject + origin_surface),
    "remind"/"escalate"/"resolve" (require entry_id; "resolve" also takes
    `resolution`).

    `ctx` needs `.mem.db_path` and `.awaiting_response_store`
    (bartholomew.kernel.awaiting_response_store.AwaitingResponseStore) at
    minimum -- typically a KernelDaemon instance, once S1.4's start()
    wiring constructs one. `.governance_store`/`.blocking_executor`/
    `.identity_context`/`.skill_registry` are consulted via getattr with
    the same additive fallbacks every other seam function here uses.

    Governance is two independent gates, both before any store write:
      1. ParkingBrake("skills") -- reuses the skills scope (design doc Sec
         8 Q1): delivery already gates through NotifySkill's own
         "skills"-scoped check, so no dedicated brake scope is needed here.
      2. Identity Context -> Policy Decision, evaluated against the kind
         "awaiting_response_<transition>" -- deliberately NOT exempted the
         way `_SELF_MAINTENANCE_DRIVES` scheduler drives are: unlike
         self_check/curiosity_probe, a reminder/escalation is genuine
         outbound contact about specific user content (design doc Sec 5).
         Additive: skipped entirely when no IdentityContext is wired in.

    A caller-input error (unknown entry_id, or a transition attempted
    against an already-resolved entry) raises
    AwaitingResponseNotFoundError/InvalidTransitionError directly -- these
    are the caller's mistake, not a governance denial or an execution
    failure, so they propagate rather than being folded into `outcome`.
    """
    from .awaiting_response_store import AwaitingResponseNotFoundError, InvalidTransitionError

    if transition not in _AWAITING_RESPONSE_TRANSITIONS:
        raise ValueError(
            f"transition must be one of {sorted(_AWAITING_RESPONSE_TRANSITIONS)}, got {transition!r}",
        )
    # Malformed-call validation, before any Observation/Governance/store
    # work: a missing required argument is the caller's programming
    # mistake, not a governed event or an execution failure, so it's
    # raised immediately rather than folded into `outcome` or recorded as
    # a Reflection.
    if transition == "open":
        if not subject or not origin_surface:
            raise ValueError("awaiting_response 'open' requires subject and origin_surface")
    elif entry_id is None:
        raise ValueError(f"awaiting_response {transition!r} requires entry_id")

    store = ctx.awaiting_response_store
    kind = f"awaiting_response_{transition}"

    existing = None
    if entry_id is not None:
        existing = await run_off_loop(
            store.get,
            entry_id,
            executor=getattr(ctx, "blocking_executor", None),
        )
    prompt_subject = subject or (existing.subject if existing is not None else "awaiting_response")

    observation = Observation(
        source="awaiting_response",
        raw_content=f"{transition}:{entry_id if entry_id is not None else 'new'}",
    )
    interpretation = Interpretation(observation=observation, prompt=prompt_subject)
    candidate_action = CandidateAction(kind=kind, interpretation=interpretation)

    governance_allowed = True
    outcome = "governance_denied"
    reason: str | None = None

    # Governance gate 1: ParkingBrake("skills"), fail-closed.
    try:
        from bartholomew.orchestrator.safety.governance_store import (
            is_blocked_fail_closed_off_loop,
        )

        blocked = await is_blocked_fail_closed_off_loop(
            "skills",
            ctx.mem.db_path,
            governance_store=getattr(ctx, "governance_store", None),
            executor=getattr(ctx, "blocking_executor", None),
        )
        if blocked:
            governance_allowed = False
            outcome = "parking_brake_denied"
            reason = "Blocked by parking brake (scope=skills)"
    except Exception:
        logger.exception("Governance check failed for %s; failing closed", kind)
        governance_allowed = False
        outcome = "parking_brake_denied"
        reason = "Governance check errored"

    # Governance gate 2: Identity Policy Decision, evaluated for real (see
    # docstring above for why this is NOT in an exempt set).
    identity_context = getattr(ctx, "identity_context", None)
    if governance_allowed and identity_context is not None:
        decision = policy_engine.evaluate_tool_policy(identity_context, kind)
        if not decision.allowed:
            governance_allowed = False
            outcome = "governance_denied"
            reason = f"Denied by Identity policy: {decision.reason}"

    entry = existing
    if governance_allowed:
        try:
            entry = await _execute_awaiting_response_transition(
                ctx,
                store,
                transition,
                entry_id=entry_id,
                subject=subject,
                origin_surface=origin_surface,
                context_ref=context_ref,
                due_at=due_at,
                resolution=resolution,
                actor=actor,
            )
        except (AwaitingResponseNotFoundError, InvalidTransitionError) as exc:
            await _record_awaiting_response_reflection(
                ctx,
                candidate_action,
                "rejected",
                None,
                str(exc),
            )
            raise
        except Exception as exc:
            logger.exception("awaiting_response transition %s failed", kind)
            governance_allowed = False
            outcome = "error"
            reason = str(exc)
        else:
            outcome = _AWAITING_RESPONSE_OUTCOME_BY_TRANSITION[transition]
            if (
                transition in ("remind", "escalate")
                and getattr(ctx, "skill_registry", None) is not None
            ):
                await _notify_awaiting_response(ctx, entry, transition)

    await _record_awaiting_response_reflection(ctx, candidate_action, outcome, entry, reason)

    return AwaitingResponseRuntimeResult(
        observation=observation,
        candidate_action=candidate_action,
        governance_allowed=governance_allowed,
        outcome=outcome,
        reason=reason,
        entry=entry,
    )


def _parse_awaiting_response_opened_at(opened_at: str) -> float | None:
    """Parse an awaiting_response entry's `opened_at`
    ("YYYY-MM-DDTHH:MM:SSZ", see awaiting_response_store._now_iso()) to a
    Unix epoch second count, comparable against WorkingMemoryItem.added_at
    .timestamp(). Returns None for an unparseable value -- treated as "an
    adjacency claim can't be proven" by the caller, not as "no constraint"."""
    try:
        return calendar.timegm(time.strptime(opened_at, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return None


async def _maybe_auto_resolve_awaiting_response(
    daemon: KernelDaemon,
    current_wm_item_id: str,
) -> None:
    """Narrow MVP auto-resolution on reply (design doc Sec 7): resolves an
    open chat-origin awaiting_response entry only when exactly one such
    entry exists AND this chat turn is genuinely the next one since the
    entry opened -- not merely some later turn that happens to land while
    it's still the sole open entry (a real gap a PR review on #38 caught:
    without an adjacency check, an unrelated reply arbitrarily later would
    silently resolve an entry that was never actually replied to).
    Adjacency is proven via Working Memory's own chat-item timestamps: if
    any other "chat"-sourced item was added after the entry's `opened_at`
    (excluding this turn's own item, `current_wm_item_id`), a reply has
    already happened since -- this is not that reply, so the entry stays
    open rather than guessing.

    This codebase's WorkingMemoryManager is a single rolling buffer with no
    per-session/thread partitioning (confirmed by direct read), so "same
    session/thread" (design doc Sec 7) degenerates to "the chat surface as
    a whole, in chronological order" here -- the narrowest faithful reading
    of that heuristic given the current architecture, not a loosening of
    it. Two or more open entries remains an ambiguous match and always
    fails closed to "stays open." Best-effort: never breaks the chat turn
    that triggered it.
    """
    try:
        executor = getattr(daemon, "blocking_executor", None)
        open_entries = await run_off_loop(
            daemon.awaiting_response_store.list_open_by_origin,
            "chat",
            executor=executor,
        )
        if len(open_entries) != 1:
            return
        entry = open_entries[0]

        opened_ts = _parse_awaiting_response_opened_at(entry.opened_at)
        if opened_ts is None:
            return

        chat_items = daemon.working_memory.get_by_source("chat")
        intervening_reply_exists = any(
            item.item_id != current_wm_item_id and item.added_at.timestamp() > opened_ts
            for item in chat_items
        )
        if intervening_reply_exists:
            return

        await run_awaiting_response_through_runtime_contract(
            daemon,
            "resolve",
            entry_id=entry.id,
            resolution="reply_received",
            actor="chat_auto_resolve",
        )
    except Exception:
        logger.exception("Failed to auto-resolve awaiting_response from a chat reply")


# =============================================================================
# Stage 5, S5.1/S5.2: the Initiative Engine (see
# docs/S5_1_INITIATIVE_ENGINE_ARCHITECTURE_DESIGN.md and
# docs/S5_2_TYPED_CADENCE_DESIGN.md). Every propose/defer/deliver/resolve/
# expire/cancel/supersede transition traverses this same Observation ->
# Interpretation -> Executive -> Governance -> Capability -> Execution ->
# Reflection -> Memory shape -- the generic chassis every future proactive
# behaviour (check-in, reminder, review, next-best-action, maintenance,
# wellness) is built on, instead of each getting its own feature-specific
# implementation.
# =============================================================================

_INITIATIVE_TRANSITIONS = frozenset(
    {"propose", "defer", "deliver", "resolve", "expire", "cancel", "supersede"},
)

# Transitions that can never represent new outbound contact -- by
# construction, not convenience (S5.2 design doc Sec 7, the narrow fix
# approved by the project owner 2026-08-06): "expire" only closes out an
# initiative already past its TTL. Exempted from BOTH the Identity Policy
# gate and the per-category consent gate below -- extending the approved
# fix to the consent gate for the identical "stuck row" reasoning it was
# written for: if a user revokes a category's consent after one of its
# initiatives was already approved, that initiative's own `expire`
# transition would otherwise be blocked by the very gate it exists to stop
# being subject to, the same failure mode the approved fix closed for
# Identity Policy.
#
# "cancel" joins this set for the identical reason, found while building
# S5.3's initiative_delivery_check drive (docs/S5_3_DEFAULT_OFF_CONSENT_
# AND_MUTE_DESIGN.md Sec 4) and confirmed by a failing test, not by
# inspection: that drive's whole reason for calling `cancel` on an
# initiative is that its category's consent was just revoked -- but
# `cancel`, unexempted, re-evaluates gate 3 (consent) for itself, which
# denies the very `cancel` call meant to close the initiative out because
# consent is (correctly) no longer granted. The initiative would be
# permanently stuck in `approved`, never reachable by any transition,
# exactly the failure mode the `expire` exemption was written to prevent.
# `cancel` qualifies for the same reasoning `expire` did (S5.2 Sec 7): by
# construction it never constitutes new outbound contact either -- it only
# ever withdraws a pending initiative, the same as `expire` only ever
# closes one out. Exempting it changes nothing when a category IS
# consented/allowed (gates 2/3 would pass regardless); it only ever
# unblocks the specific case where governance state itself is what
# `cancel` exists to react to. `propose`, `defer`, `deliver`, `resolve`,
# and `supersede` remain evaluated for real, every time, no exemption --
# `defer` in particular is not at risk of the same stuck-row shape, since
# its only current caller (the muted branch of initiative_delivery_check)
# is reached only when consent is already confirmed present.
_SELF_MAINTENANCE_INITIATIVE_TRANSITIONS = frozenset({"expire", "cancel"})


@dataclass(frozen=True)
class ProactiveIntent:
    """Stage 2.5, between Interpretation and Executive (S5.1 design doc Sec
    7): structured classification of a proposed Initiative, so Governance's
    category-scoped gates have something stable to key off of instead of
    free text. In this implementation, classification means accepting and
    normalising the caller-supplied category/urgency/sensitivity --
    deriving them from content instead is future work, not designed here."""

    category: str
    urgency: str = "normal"
    sensitivity: bool = False


def classify_proactive_intent(
    category: str,
    urgency: str = "normal",
    sensitivity: bool = False,
) -> ProactiveIntent:
    from . import initiative_store as initiative_store_module

    if category not in initiative_store_module.VALID_CATEGORIES:
        raise ValueError(
            f"category must be one of {sorted(initiative_store_module.VALID_CATEGORIES)}, "
            f"got {category!r}",
        )
    if urgency not in {"low", "normal", "high"}:
        raise ValueError(f"urgency must be one of low/normal/high, got {urgency!r}")
    return ProactiveIntent(category=category, urgency=urgency, sensitivity=sensitivity)


@dataclass
class InitiativeRuntimeResult:
    """Outcome of one Initiative transition through the Runtime Contract.
    `outcome` is either the initiative's resulting status ("approved",
    "denied", "deferred", "delivered", "accepted", "dismissed", "snoozed",
    "expired", "cancelled", "superseded") or one of "parking_brake_denied",
    "governance_denied", "rejected" (caller-input error, also raised),
    "error"."""

    observation: Observation
    candidate_action: CandidateAction
    governance_allowed: bool
    outcome: str
    reason: str | None
    initiative: Any
    dry_run_result: Any = None
    """Stage 5, S5.5: populated only when this call was actually
    simulated (see `run_initiative_through_runtime_contract()`'s `dry_run`
    param) -- a `bartholomew.kernel.dry_run.DryRunResult`, never a real
    `Initiative`. `None` for every ordinary live call, unchanged from
    before S5.5."""


async def _record_initiative_reflection(
    ctx: Any,
    candidate_action: CandidateAction,
    outcome: str,
    initiative: Any,
    reason: str | None,
) -> None:
    """Exactly one ActionReflection per transition -- mirrors
    _record_awaiting_response_reflection()'s rationale: the
    initiative_audit table is the queryable per-initiative detail view;
    this is the cross-surface stream."""
    details: dict[str, Any] = {}
    if initiative is not None:
        details["initiative_id"] = initiative.id
        details["category"] = initiative.category
        details["priority"] = initiative.priority
        details["confidence"] = initiative.confidence
        details["governance_decision"] = initiative.governance_decision
    if reason:
        details["reason"] = reason
    reflection = ActionReflection(
        surface="initiative",
        action=candidate_action.kind,
        outcome=outcome,
        summary=f"Initiative ({candidate_action.kind}): {outcome}",
        details=details,
    )
    await record_action_reflection(getattr(ctx, "mem", None), reflection)


async def _deliver_initiative_notification(
    ctx: Any,
    initiative: Any,
    notify_overrides: dict[str, Any] | None = None,
) -> None:
    """Delivery delegates to the existing governed NotifySkill path (S5.1
    design doc Sec 7), reused exactly as awaiting_response's remind/
    escalate transitions already do -- no new delivery channel.
    SkillRegistry.execute_action() runs its own independent Governance pass
    on skill_id="notify"; that is additive to, not a substitute for, this
    seam's own gates above. Best-effort: a delivery failure must not undo
    the state transition that already committed.

    `notify_overrides` (S5.4 design doc Sec 3) lets the caller merge
    additional/replacement params -- e.g. `{"priority": "urgent"}` for an
    `immediate`/`critical_override` delivery policy, `{"sound": False}` for
    `silent` -- into the params sent to NotifySkill, without this function
    (or the seam) needing to know what those delivery policies mean. That
    meaning lives entirely in the caller (drive_initiative_delivery_check)."""
    if initiative is None:
        return
    try:
        params = {
            "message": initiative.rationale,
            "title": f"Bartholomew: {initiative.kind}",
            "priority": "high" if initiative.priority == "high" else "normal",
        }
        if notify_overrides:
            params.update(notify_overrides)
        await run_skill_through_runtime_contract(
            ctx.skill_registry,
            "notify",
            "send",
            params,
        )
    except Exception:
        logger.exception(
            "Failed to deliver initiative %s notification for id %s",
            initiative.kind,
            getattr(initiative, "id", None),
        )


def _record_initiative_working_memory_note(ctx: Any, initiative: Any) -> None:
    """On deliver, add a Working Memory item tagged source="initiative"
    (S5.1 design doc Sec 10), mirroring chat's own source="chat" tagging,
    so a delivered proactive suggestion feeds back into Experience Kernel
    continuity instead of being invisible to it. Best-effort, additive --
    absent working_memory is a silent no-op."""
    working_memory = getattr(ctx, "working_memory", None)
    if working_memory is None or initiative is None:
        return
    try:
        working_memory.add(
            content=f"Bartholomew (proactive, {initiative.kind}): {initiative.rationale}",
            source="initiative",
            tags=["initiative", initiative.kind],
        )
    except Exception:
        logger.exception(
            "Failed to record Working Memory item for initiative %s",
            initiative.id,
        )


async def run_initiative_through_runtime_contract(
    ctx: Any,
    transition: str,
    *,
    initiative_id: int | None = None,
    kind: str | None = None,
    category: str | None = None,
    urgency: str = "normal",
    sensitivity: bool = False,
    priority: str = "normal",
    confidence: float | None = None,
    rationale: str | None = None,
    payload: dict[str, Any] | None = None,
    origin_drive: str | None = None,
    due_at: str | None = None,
    expires_at: str | None = None,
    parent_initiative_id: int | None = None,
    depends_on: list[int] | None = None,
    resolution: str | None = None,
    reason: str | None = None,
    actor: str | None = None,
    delivery_policy: str = "standard",
    suppress_notification: bool = False,
    notify_overrides: dict[str, Any] | None = None,
    coalesced: bool = False,
    batch_id: str | None = None,
    batch_size: int = 1,
    dry_run: bool = False,
) -> InitiativeRuntimeResult:
    """
    Trace one Initiative transition through the Runtime Contract seam.
    `transition` is one of "propose" (requires kind, category, confidence,
    rationale, origin_drive, expires_at), "defer" (requires initiative_id;
    `reason` recommended), "deliver"/"cancel"/"supersede" (require
    initiative_id), "resolve" (requires initiative_id + resolution;
    `due_at` required when resolution="snoozed"), "expire" (requires
    initiative_id).

    S5.4 design doc Sec 2/5/10 -- `delivery_policy` is consulted only by
    "propose" (forwarded to `store.propose()`; see
    `initiative_store.VALID_DELIVERY_POLICIES`). `suppress_notification`,
    `notify_overrides`, `coalesced`, `batch_id`, `batch_size` are consulted
    only by "deliver": `suppress_notification=True` skips this call's own
    auto-notify (the caller -- typically `drive_initiative_delivery_check`
    coalescing several deliveries into one digest -- is responsible for
    notifying instead); `notify_overrides` merges into the params sent to
    NotifySkill when the auto-notify does fire (e.g. `{"priority":
    "urgent"}`, `{"sound": False}`); `coalesced`/`batch_id`/`batch_size` are
    forwarded to `store.deliver()` for `initiative_audit` only -- they carry
    no behavioural effect here. The seam itself stays policy-agnostic: it
    doesn't need to know what any given `delivery_policy` value means, only
    how to thread these through.

    S5.5 design doc (docs/S5_5_DRY_RUN_MODE_DESIGN.md) -- `dry_run` is
    consulted only for "propose"/"deliver" (every other transition ignores
    it; out of scope for this pass, see that document's own scope note).
    The effective dry-run decision is `dry_run OR <the global dry-run
    switch's "initiative" scope>` -- the caller's own request can only
    push toward simulation, and so can the global switch; neither can push
    away from it once the other has. All three Governance gates below
    still evaluate for real regardless of dry-run -- only the store write
    (`propose`)/(`deliver`'s store write + NotifySkill call + Working
    Memory note) is replaced with a `bartholomew.kernel.dry_run.
    DryRunResult`, recorded to its own `dry_run_results` table, never to
    `initiatives`/`initiative_audit`/the unified Reflection sink/Working
    Memory. If resolving the global switch itself errors and the caller
    did not already request `dry_run=True`, the call is denied outright
    (fail-closed) rather than guessing whether the switch would have
    forced simulation -- see `is_dry_run_engaged_fail_closed_off_loop()`'s
    docstring.

    `ctx` needs `.mem.db_path` and `.initiative_store`
    (bartholomew.kernel.initiative_store.InitiativeStore) at minimum --
    typically a KernelDaemon instance. `.governance_store`/
    `.blocking_executor`/`.identity_context`/`.skill_registry`/
    `.working_memory` are consulted via getattr with the same additive
    fallbacks every other seam function here uses.

    Governance is three independent gates, all before any store write
    (S5.1 design doc Sec 8), except for `expire`
    (see _SELF_MAINTENANCE_INITIATIVE_TRANSITIONS's own docstring for why):
      1. ParkingBrake("initiative") -- a scope dedicated to Initiative
         Engine proposals, distinct from "scheduler" (which already gates
         whether the originating drive tick runs at all). Applies even to
         `expire`: an operator-engaged emergency stop must still hold.
      2. Identity Context -> Policy Decision, evaluated against
         f"allow_proactive.{category}" -- deliberately NOT exempted for any
         transition except `expire`.
      3. Per-category user consent (initiative_consent table,
         default-off) -- an end-user-level opt-in, distinct from gate 2's
         operator-level allowlist. No UI/API exists yet to grant it (S5.3),
         so every category is effectively denied here until then -- the
         intended "default-OFF consent... a prerequisite for live
         delivery" behaviour, not a bug in the absence of that UI.

    A caller-input error (unknown initiative_id, a transition attempted
    against an initiative not in that transition's allowed pre-state, or
    an invalid category/priority/confidence/resolution) raises
    InitiativeNotFoundError/InvalidTransitionError directly -- the caller's
    mistake, not a governance denial or an execution failure.
    """
    from . import initiative_store as initiative_store_module

    if transition not in _INITIATIVE_TRANSITIONS:
        raise ValueError(
            f"transition must be one of {sorted(_INITIATIVE_TRANSITIONS)}, got {transition!r}",
        )

    store = ctx.initiative_store
    executor = getattr(ctx, "blocking_executor", None)

    existing = None
    if initiative_id is not None:
        existing = await run_off_loop(store.get, initiative_id, executor=executor)

    intent: ProactiveIntent | None = None
    if transition == "propose":
        if not kind or not origin_drive or not rationale or confidence is None or not expires_at:
            raise ValueError(
                "initiative 'propose' requires kind, category, confidence, rationale, "
                "origin_drive, and expires_at",
            )
        intent = classify_proactive_intent(category or "", urgency, sensitivity)
        prompt = rationale
    else:
        if initiative_id is None:
            raise ValueError(f"initiative {transition!r} requires initiative_id")
        if existing is None:
            # Caller's mistake, not a governance denial: every non-propose
            # transition needs a real row both to execute and to know
            # which category gates 2/3 below should evaluate. Raised here,
            # before any Governance work, rather than left to surface (or
            # be masked by an "unknown"-category gate denial) once the
            # store call itself is reached.
            from . import initiative_store as initiative_store_module

            raise initiative_store_module.InitiativeNotFoundError(
                f"No initiative {initiative_id}",
            )
        prompt = existing.rationale
        intent = ProactiveIntent(category=existing.category)

    action_category = intent.category if intent is not None else "unknown"
    action_kind = f"initiative_{transition}_{action_category}"
    action_ref = initiative_id if initiative_id is not None else (kind or "new")

    observation = Observation(source="scheduler", raw_content=f"{transition}:{action_ref}")
    interpretation = Interpretation(observation=observation, prompt=prompt)
    candidate_action = CandidateAction(kind=action_kind, interpretation=interpretation)

    governance_allowed = True
    governance_decision = "denied"
    outcome = "governance_denied"
    reason_out: str | None = None
    exempt = transition in _SELF_MAINTENANCE_INITIATIVE_TRANSITIONS

    # S5.5: resolve dry-run mode, for "propose"/"deliver" only, before any
    # Governance gate runs -- the gates below evaluate identically either
    # way (design doc Sec 2/9: Governance is never weakened by dry-run).
    # The caller's own `dry_run=True` is trusted unconditionally (it can
    # only ever push toward simulation); the global switch is then OR'd
    # in. If reading the global switch itself errors while the caller
    # asked for a live call, deny outright rather than risk a real
    # execution the switch might have forbidden -- if the caller already
    # asked for dry_run=True, the switch's own read failure changes
    # nothing (still simulate, as the caller already required).
    effective_dry_run = dry_run
    dry_run_resolution_failed = False
    if transition in ("propose", "deliver"):
        try:
            from bartholomew.orchestrator.safety.governance_store import (
                is_dry_run_engaged_fail_closed_off_loop,
            )

            globally_engaged = await is_dry_run_engaged_fail_closed_off_loop(
                "initiative",
                ctx.mem.db_path,
                governance_store=getattr(ctx, "governance_store", None),
                executor=executor,
            )
            effective_dry_run = effective_dry_run or globally_engaged
        except Exception:
            if not dry_run:
                logger.exception(
                    "Dry-run state resolution failed for %s; denying (fail-closed)",
                    action_kind,
                )
                # S5.5 correction: this is an infrastructure/safety-
                # resolution failure, not a Governance verdict. Reusing
                # "denied" here (and letting propose's own "always writes
                # a row" behaviour below run as normal) would leave a
                # real, terminal Initiative row that reads exactly like a
                # legitimate Governance denial when it isn't one --
                # Governance was never actually consulted. `outcome =
                # "error"` reuses this seam's own existing vocabulary for
                # "failed before any verdict was reached" (see the
                # propose/dispatch except blocks below, which already use
                # it identically). `dry_run_resolution_failed` additionally
                # skips the whole dispatch block entirely, so no
                # Initiative row -- denied or otherwise -- and no
                # DryRunResult are written for this path at all. See
                # docs/S5_5_DRY_RUN_MODE_DESIGN.md Sec 10.
                governance_allowed = False
                outcome = "error"
                reason_out = "Dry-run state check errored"
                dry_run_resolution_failed = True

    # Gate 1: ParkingBrake("initiative"), fail-closed. Applies unconditionally.
    # S5.5 correction: `gate_evidence` records each gate's own real verdict
    # as it's evaluated, independent of `reason_out` (a single string the
    # first denying gate already overwrites) -- this is what lets a dry
    # run's `approval_requirements` (below) report genuine per-gate
    # provenance instead of a coarse summary. Read-only bookkeeping: it
    # never feeds back into `governance_allowed` and changes no gate's own
    # decision -- Governance's authority is reported here, not duplicated
    # or re-decided.
    gate_evidence: dict[str, Any] = {
        "parking_brake": {"scope": "initiative", "checked": False, "blocked": None},
        "identity_policy": {
            "exempt": exempt,
            "checked": False,
            "allowed": None,
            "reason": None,
        },
        "consent": {
            "exempt": exempt,
            "category": action_category,
            "checked": False,
            "consented": None,
        },
    }
    if governance_allowed:
        try:
            from bartholomew.orchestrator.safety.governance_store import (
                is_blocked_fail_closed_off_loop,
            )

            blocked = await is_blocked_fail_closed_off_loop(
                "initiative",
                ctx.mem.db_path,
                governance_store=getattr(ctx, "governance_store", None),
                executor=executor,
            )
            gate_evidence["parking_brake"]["checked"] = True
            gate_evidence["parking_brake"]["blocked"] = blocked
            if blocked:
                governance_allowed = False
                outcome = "parking_brake_denied"
                reason_out = "Blocked by parking brake (scope=initiative)"
        except Exception:
            logger.exception("Governance check failed for %s; failing closed", action_kind)
            gate_evidence["parking_brake"]["checked"] = True
            gate_evidence["parking_brake"]["error"] = "Governance check errored"
            governance_allowed = False
            outcome = "parking_brake_denied"
            reason_out = "Governance check errored"

    # Gate 2: Identity Policy, skipped for exempt transitions.
    identity_context = getattr(ctx, "identity_context", None)
    if governance_allowed and not exempt and identity_context is not None:
        policy_kind = f"allow_proactive.{action_category}"
        decision = policy_engine.evaluate_tool_policy(identity_context, policy_kind)
        gate_evidence["identity_policy"]["checked"] = True
        gate_evidence["identity_policy"]["allowed"] = decision.allowed
        gate_evidence["identity_policy"]["reason"] = decision.reason
        if not decision.allowed:
            governance_allowed = False
            outcome = "governance_denied"
            reason_out = f"Denied by Identity policy: {decision.reason}"

    # Gate 3: per-category user consent, skipped for exempt transitions.
    if governance_allowed and not exempt:
        consented = await run_off_loop(
            store.is_category_consented,
            action_category,
            executor=executor,
        )
        gate_evidence["consent"]["checked"] = True
        gate_evidence["consent"]["consented"] = consented
        if not consented:
            governance_allowed = False
            outcome = "governance_denied"
            reason_out = f"No user consent granted for category {action_category!r}"

    if governance_allowed:
        governance_decision = "allowed"

    initiative = existing
    dry_result = None
    if dry_run_resolution_failed:
        # S5.5 correction: represent the infrastructure failure and stop
        # here -- no real store write (denied or otherwise), and no
        # simulated DryRunResult either, since we were never able to
        # determine whether this call should have been live or simulated,
        # let alone what Governance would have said.
        pass
    elif effective_dry_run and transition in ("propose", "deliver"):
        # S5.5: simulate the entire transaction instead of writing/calling
        # for real -- for both an allowed and a denied outcome (design doc
        # Sec 1/17: a dry run reports Governance's real verdict truthfully,
        # it does not only simulate the happy path). No store.propose()/
        # store.deliver() call, no NotifySkill call, no Working Memory
        # note -- see docs/S5_5_DRY_RUN_MODE_DESIGN.md Sec 4.
        from .dry_run import DryRunResult, record_dry_run_result

        approval_requirements: dict[str, Any] = {
            "parking_brake": gate_evidence["parking_brake"],
            "identity_policy": gate_evidence["identity_policy"],
            "consent": gate_evidence["consent"],
            "reason": reason_out,
        }
        if transition == "propose":
            target = "new"
            parameters = {
                "kind": kind,
                "category": intent.category if intent is not None else category,
                "priority": priority,
                "confidence": confidence,
                "rationale": rationale,
                "payload": payload,
                "origin_drive": origin_drive,
                "due_at": due_at,
                "expires_at": expires_at,
                "delivery_policy": delivery_policy,
            }
            expected_effects: dict[str, Any] = {
                "would_create_status": "approved" if governance_allowed else "denied",
            }
            initiative = None
        else:  # deliver
            target = str(initiative_id)
            parameters = {
                "coalesced": coalesced,
                "batch_id": batch_id,
                "batch_size": batch_size,
            }
            expected_effects = {"current_status": existing.status if existing else None}
            if existing is not None:
                # Informational only -- category mute (S5.3) is evaluated
                # by the initiative_delivery_check drive before it ever
                # calls `deliver`, not by this seam's own three gates
                # (design doc Sec 8). Reading it here doesn't gate
                # anything and never overrides governance_allowed; it's
                # additional truthful context for "would this actually
                # have reached the user," per your request to preserve
                # mute state where applicable.
                muted = await run_off_loop(
                    store.is_category_muted,
                    existing.category,
                    executor=executor,
                )
                approval_requirements["category_mute"] = {
                    "category": existing.category,
                    "muted": muted,
                }
            if governance_allowed and existing is not None:
                expected_effects["would_transition"] = f"{existing.status} -> delivered"
                if getattr(ctx, "skill_registry", None) is not None and not suppress_notification:
                    notify_params = {
                        "message": existing.rationale,
                        "title": f"Bartholomew: {existing.kind}",
                        "priority": "high" if existing.priority == "high" else "normal",
                    }
                    if notify_overrides:
                        notify_params.update(notify_overrides)
                    expected_effects["would_call"] = "notify.send"
                    expected_effects["notify_params"] = notify_params
            initiative = existing

        dry_result = DryRunResult(
            surface="initiative",
            proposed_action=action_kind,
            target=target,
            parameters=parameters,
            expected_effects=expected_effects,
            governance_decision=governance_decision,
            approval_requirements=approval_requirements,
            would_execute=governance_allowed,
            actor=actor,
        )
        await run_off_loop(
            record_dry_run_result,
            ctx.mem.db_path,
            dry_result,
            executor=executor,
        )
        outcome = f"dry_run_{'approved' if governance_allowed else 'denied'}"
    elif transition == "propose":
        # propose always writes a row -- denied is a real, audited,
        # terminal outcome (design doc Sec 5), not a no-op.
        try:
            initiative = await run_off_loop(
                store.propose,
                kind=kind,
                category=intent.category,
                priority=priority,
                confidence=confidence,
                rationale=rationale,
                payload=payload,
                origin_drive=origin_drive,
                due_at=due_at,
                expires_at=expires_at,
                parent_initiative_id=parent_initiative_id,
                depends_on=depends_on,
                governance_decision=governance_decision,
                governance_reason=reason_out,
                actor=actor,
                delivery_policy=delivery_policy,
                executor=executor,
            )
        except initiative_store_module.InvalidTransitionError as exc:
            await _record_initiative_reflection(ctx, candidate_action, "rejected", None, str(exc))
            raise
        except Exception as exc:
            logger.exception("initiative propose failed")
            governance_allowed = False
            outcome = "error"
            reason_out = str(exc)
        else:
            outcome = initiative.status
    elif governance_allowed:
        try:
            if transition == "defer":
                initiative = await run_off_loop(
                    store.defer,
                    initiative_id,
                    reason=reason or "unspecified",
                    actor=actor,
                    executor=executor,
                )
            elif transition == "deliver":
                initiative = await run_off_loop(
                    store.deliver,
                    initiative_id,
                    actor=actor,
                    coalesced=coalesced,
                    batch_id=batch_id,
                    batch_size=batch_size,
                    executor=executor,
                )
            elif transition == "resolve":
                initiative = await run_off_loop(
                    store.resolve,
                    initiative_id,
                    resolution=resolution or "dismissed",
                    due_at=due_at,
                    actor=actor,
                    executor=executor,
                )
            elif transition == "expire":
                initiative = await run_off_loop(
                    store.expire,
                    initiative_id,
                    actor=actor,
                    executor=executor,
                )
            elif transition == "cancel":
                initiative = await run_off_loop(
                    store.cancel,
                    initiative_id,
                    actor=actor,
                    executor=executor,
                )
            else:  # supersede
                initiative = await run_off_loop(
                    store.supersede,
                    initiative_id,
                    actor=actor,
                    executor=executor,
                )
        except (
            initiative_store_module.InitiativeNotFoundError,
            initiative_store_module.InvalidTransitionError,
        ) as exc:
            await _record_initiative_reflection(ctx, candidate_action, "rejected", None, str(exc))
            raise
        except Exception as exc:
            logger.exception("initiative transition %s failed", action_kind)
            governance_allowed = False
            outcome = "error"
            reason_out = str(exc)
        else:
            outcome = initiative.status
            if transition == "deliver" and getattr(ctx, "skill_registry", None) is not None:
                # S5.4 design doc Sec 5/10: suppress_notification=True skips
                # only the actual NotifySkill call -- the coalescing caller
                # sends one combined notification of its own instead -- but
                # the Working Memory note is still recorded per-initiative,
                # since this initiative was genuinely delivered either way.
                if not suppress_notification:
                    await _deliver_initiative_notification(
                        ctx,
                        initiative,
                        notify_overrides=notify_overrides,
                    )
                _record_initiative_working_memory_note(ctx, initiative)

    if dry_result is None and not dry_run_resolution_failed:
        # S5.5: a simulated call never reaches the real Reflection sink, and
        # neither does an infrastructure resolution failure -- dry_result
        # alone is not a sufficient guard, since it is also None on that
        # failure path (S5.5 correction: no real action ground truth may be
        # recorded when we never even determined whether this call should
        # have been live or simulated, let alone what Governance would have
        # said).
        await _record_initiative_reflection(ctx, candidate_action, outcome, initiative, reason_out)

    return InitiativeRuntimeResult(
        observation=observation,
        candidate_action=candidate_action,
        governance_allowed=governance_allowed,
        outcome=outcome,
        reason=reason_out,
        initiative=initiative,
        dry_run_result=dry_result,
    )
