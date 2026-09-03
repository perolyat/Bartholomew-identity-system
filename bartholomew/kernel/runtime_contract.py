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
import functools
import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from . import (
    candidate_learning,
    forecast_intents,
    learning_authorization,
    objective_intents,
    objective_store,
    personal_facts,
    policy_engine,
    share_adoption,
    spoken_output,
    task_intents,
    training,
)
from .blocking_executor import run_off_loop
from .competency_reasoning import (
    EMPTY_CONTEXT,
    CompetencyCandidate,
    CompetencyContext,
    build_retrieval_query,
    query_terms,
    render_for_prompt,
    select_relevant,
)
from .memory.privacy_guard import get_consent_handler
from .reflection import ActionReflection, ReflectionWriteOutcome, record_action_reflection

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

    # S5.3: the competency records that informed this proposal, if any.
    # Optional with a default, so every existing construction site (skills,
    # drives, sight, voice, awaiting-response, training) is unaffected.
    # Never surfaced automatically to the user (design Decision E.1); recorded
    # in the Reflection so a future user-requested explanation capability is
    # possible (E.2).
    competency_context: CompetencyContext | None = None

    # Usable POC slice 1: the remembered personal facts recalled for this
    # proposal, if any. Same shape and same posture as `competency_context`
    # above -- optional, never auto-exposed, recorded at explanation grade.
    personal_fact_context: CompetencyContext | None = None


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

    #: Usable POC slice 1: what the governed write path did with each durable
    #: personal fact this turn proposed (see `_capture_personal_facts`).
    #: Empty when the turn proposed none, or when governance denied the turn.
    #: Defaulted so existing construction sites are unaffected.
    personal_facts_captured: list[dict[str, Any]] = field(default_factory=list)

    #: Conversational task control: what this turn's explicit task instruction
    #: actually did, or None when the turn contained none (the common case).
    #: Records the recognised operation, the governed skill outcome, and
    #: whether anything changed -- so a caller, an audit reader and the
    #: Reflection all see the same account of it. Defaulted so existing
    #: construction sites are unaffected.
    task_action: dict[str, Any] | None = None

    #: Golden Path first slice: what this turn's explicit forecast question
    #: did through the governed external-capability path, or None when the
    #: turn contained none (the common case). Records the outcome, the
    #: provider consulted and **exactly what was disclosed to it**, so the
    #: caller, the audit reader and the Reflection all see one account of the
    #: egress. Defaulted so existing construction sites are unaffected.
    forecast_action: dict[str, Any] | None = None

    #: Golden Path slice 2: what this turn's explicit objective instruction
    #: did through the governed objective seam, or None when the turn
    #: contained none (the common case). Records the recognised operation,
    #: the governed outcome and whether anything actually changed.
    #: Defaulted so existing construction sites are unaffected.
    objective_action: dict[str, Any] | None = None

    #: Set when a forecast this turn obtained was recorded as evidence
    #: against a live objective. Names the objective and the event kind, so
    #: the audit reader can see that external content entered an
    #: objective's history and on what footing. None whenever nothing was
    #: attached -- which is most turns.
    objective_evidence: dict[str, Any] | None = None

    #: WP-A2b. True when this turn's Reflection -- on the chat surface the
    #: sole durable record of the governed decision context (S5.3 Decision
    #: E.2's explanation-grade applied-competency/personal-fact record) --
    #: failed to persist. The turn's own outcome (`governance_allowed`,
    #: `response`) is unaffected: a genuinely-produced response is never
    #: reported as failed because its provenance record was lost, and a
    #: turn is never re-run for it -- but it must not present as *full*
    #: success either. Defaulted so existing construction sites are
    #: unaffected. See `DECISIONS.md`, "One Reflection sink, two semantic
    #: roles".
    provenance_degraded: bool = False

    #: The reflection-write failure, verbatim, when `provenance_degraded`.
    provenance_error: str | None = None


def render_objectives_for_prompt(objectives: list[Any]) -> str:
    """The Interpretation-stage block naming what Bartholomew is carrying.

    Golden Path slice 2, and the point at which continuity actually reaches
    a conversation: a later turn knows what the user is trying to achieve
    without the user restating it.

    Only *live* objectives are ever passed in (`_live_objectives()` reads
    `list_live()`, which cannot return a terminal one). That is the second
    of the three independent stops on resurfacing a finished objective: even
    if something else were to raise one, it would not be here to be
    mentioned.

    Deliberately not merged into the existing "Active goals:" line.
    `ExperienceKernel`'s active goals are a redacted `list[str]` of
    process-lifetime state with no outcome, history or completion semantics;
    an objective is durable, has all three, and conflating them would make
    "complete this goal" ambiguous between two stores with different rules.
    """
    if not objectives:
        return ""
    lines = ["Objectives you are carrying for the user (do not invent others):"]
    for objective in objectives:
        horizon = ""
        kind = getattr(objective, "horizon_kind", None)
        if kind == objective_store.HORIZON_BY_DATE and getattr(objective, "horizon_date", None):
            horizon = f" (by {objective.horizon_date})"
        elif kind == objective_store.HORIZON_THIS_WEEK:
            horizon = " (this week)"
        status = ""
        if getattr(objective, "status", None) == objective_store.STATUS_BLOCKED:
            status = " [blocked]"
        lines.append(f"- {objective.title}{horizon}{status}")
    return "\n".join(lines)


def _build_interpretation(
    daemon: KernelDaemon,
    observation: Observation,
    competency_block: str = "",
    personal_facts_block: str = "",
    objectives_block: str = "",
) -> Interpretation:
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
    `Orchestrator()` with no `identity_config`, so `ContextBuilder` never
    builds a `MemoryManager` and `build_prompt_context()` always returns
    `""`. (Since 2026-08-17 that build is lazy rather than eager, so the
    keystore is only reached if something actually asks for memory context;
    with no `identity_config` nothing is built either way, and this path is
    unchanged.) Rather than reviving that separate, superseded path (see
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

    # S5.3: competency guidance, already retrieved and rendered by the caller.
    # Passed in rather than fetched here because retrieval is asynchronous and
    # must run off the event loop, while this function is deliberately
    # synchronous and called by every surface.
    if competency_block:
        context_lines.append(competency_block)

    # Usable POC slice 1: recalled personal facts, rendered as their own
    # block so competency guidance and remembered facts stay visibly
    # distinct. Same passed-in-not-fetched reason as the competency block.
    if personal_facts_block:
        context_lines.append(personal_facts_block)

    # Golden Path slice 2: the durable objectives, in their own block for the
    # same passed-in-not-fetched reason as the two above -- the read is
    # asynchronous and must run off the event loop, while this function is
    # deliberately synchronous and called by every surface.
    if objectives_block:
        context_lines.append(objectives_block)

    if not context_lines:
        return Interpretation(observation=observation, prompt=observation.raw_content)

    # No "User:" label here -- respond_fn's own backend (e.g. the chat
    # orchestrator's inject_memory_context() step) applies its own "User: "
    # wrapping around whatever prompt it receives; adding one here too
    # produced a visibly doubled "User: ... User: ..." prefix in the actual
    # response, caught during this change's own live-smoke verification.
    prompt = "\n".join(context_lines) + f"\n\n{observation.raw_content}"
    return Interpretation(observation=observation, prompt=prompt)


#: How many candidates to pull before selection narrows them. Deliberately
#: larger than competency_reasoning.DEFAULT_MAX_RECORDS so selection has real
#: choices to rank, rather than the retriever silently deciding the outcome.
_COMPETENCY_RETRIEVAL_TOP_K = 20


@dataclass(frozen=True)
class ChatMemoryContext:
    """What one chat turn's retrieval produced, kept as two separate
    selections rather than one merged blob.

    Separate on purpose. `select_relevant()` commits to a single domain per
    selection (S5.3 Decision C, no cross-competency transfer), so folding
    personal facts and competencies into one call would make them compete for
    that single slot -- a remembered birthday could silently displace an
    applicable competency, or vice versa. Two independent selections over the
    same retrieved candidates preserve S5.3's behaviour exactly while letting
    slice 1's facts through.
    """

    competency: CompetencyContext = EMPTY_CONTEXT
    personal: CompetencyContext = EMPTY_CONTEXT


EMPTY_CHAT_MEMORY_CONTEXT = ChatMemoryContext()


async def _retrieve_memory_context(
    daemon: KernelDaemon,
    observation: Observation,
) -> ChatMemoryContext:
    """
    Retrieve the stored records relevant to this observation and select which
    of them inform the CandidateAction.

    S5.3 established this for competency records. The Usable POC's slice 1
    widens the retrieval filter to also cover the personal-fact kinds
    (`personal_facts.PERSONAL_FACT_KINDS`), so an ordinary chat turn's
    existing retrieval call sees facts captured from ordinary conversation --
    which is the whole point of the slice: before it, nothing an ordinary
    conversation produced was ever durable *and* retrievable.

    Uses the existing `ConsentGate`-filtered retrieval layer unchanged -- that
    is what decides which records this request may see at all, and it is why
    a fact still sitting in the consent queue (never written to `memories`)
    cannot be recalled. Bodies are then loaded for exactly those permitted
    ids, because `RetrievedItem` carries a snippet rather than the stored
    value and selection needs each record's provenance/classification/
    confidence (S5.3 Decision E.2).

    Retrieval is synchronous (`Retriever.retrieve()` is a plain `def`), so it
    runs through `run_off_loop()` -- the B2/B8 discipline. Putting a
    per-request blocking database read on the event loop is exactly the defect
    class Phase B spent nine stages removing.

    Never raises: memory enrichment must not be able to break a chat turn,
    matching `_build_interpretation()`'s own best-effort pattern. On any
    failure this returns empty contexts and the request proceeds exactly as
    it does today.
    """
    try:
        from .competency import COMPETENCY_KINDS
        from .retrieval import RetrievalFilters, get_retriever

        db_path = daemon.mem.db_path
        # Raw utterances cannot be used as FTS queries -- see
        # competency_reasoning.build_retrieval_query()'s docstring for the
        # measured reason (FTS5's AND semantics would match nothing).
        query = build_retrieval_query(observation.raw_content or "")
        if not query:
            return EMPTY_CHAT_MEMORY_CONTEXT

        kinds = list(COMPETENCY_KINDS) + list(personal_facts.PERSONAL_FACT_KINDS)

        def _search():
            retriever = get_retriever(db_path=db_path, memory_store=daemon.mem)
            return retriever.retrieve(
                query,
                top_k=_COMPETENCY_RETRIEVAL_TOP_K,
                filters=RetrievalFilters(kinds=kinds),
            )

        items = await run_off_loop(
            _search,
            executor=getattr(daemon, "blocking_executor", None),
        )
        if not items:
            return EMPTY_CHAT_MEMORY_CONTEXT

        rows = await daemon.mem.get_memories_by_ids([item.memory_id for item in items])
        by_id = {row["id"]: row for row in rows}

        competency_candidates: list[CompetencyCandidate] = []
        fact_candidates: list[CompetencyCandidate] = []
        for item in items:
            row = by_id.get(item.memory_id)
            if row is None:
                continue
            score = float(getattr(item, "score", 0.0) or 0.0)

            if row["kind"] in personal_facts.PERSONAL_FACT_KINDS:
                fact_record = personal_facts.record_from_row(row)
                if fact_record is None:
                    continue
                fact_candidates.append(
                    CompetencyCandidate(
                        kind=row["kind"],
                        key=row["key"],
                        score=score,
                        record=fact_record,
                    ),
                )
                continue

            record = _parse_competency_row(row)
            if record is None:
                continue
            competency_candidates.append(
                CompetencyCandidate(
                    kind=row["kind"],
                    key=row["key"],
                    score=score,
                    record=record,
                ),
            )

        # The relevance gate, reused unmodified for both families: a record
        # must share meaningful terms with the request to be applicable at
        # all. Being the retriever's best result is not sufficient -- a
        # retriever always returns a nearest neighbour, and an irrelevant
        # record would otherwise be cited in the explanation-grade
        # attribution as the basis of the decision.
        request_terms = query_terms(observation.raw_content or "")
        return ChatMemoryContext(
            competency=select_relevant(competency_candidates, request_terms=request_terms),
            personal=select_relevant(fact_candidates, request_terms=request_terms),
        )
    except Exception:
        logger.exception("Memory retrieval failed; proceeding without retrieved context")
        return EMPTY_CHAT_MEMORY_CONTEXT


def _parse_competency_row(row: dict[str, Any]) -> Any | None:
    """Rebuild an S5.1 record from a stored row, or None if it isn't one.

    Reuses `training.record_from_payload()` rather than duplicating the
    kind->class mapping, so the read path cannot drift from the write path.
    """
    import json as _json

    try:
        data = _json.loads(row["value"])
    except (TypeError, ValueError):
        # Not structured competency JSON (e.g. a summary substitution).
        return None

    if not isinstance(data, dict):
        return None

    key = row.get("key") or ""
    slug = key.split(".", 1)[1] if "." in key else None

    try:
        return training.record_from_payload(row["kind"], data, slug=slug)
    except (ValueError, KeyError, TypeError):
        return None


#: Outcome labels for one proposed personal-fact write. These name what the
#: existing governed write path did with the proposal -- they are not a second
#: policy vocabulary, and nothing here decides any of them.
FACT_OUTCOME_STORED = "stored"
FACT_OUTCOME_QUEUED_FOR_CONSENT = "queued_for_consent"
FACT_OUTCOME_BLOCKED = "blocked"
FACT_OUTCOME_ERROR = "error"


async def _classify_fact_not_stored(
    mem: Any,
    kind: str,
    key: str,
) -> str:
    """
    Explain a `stored=False` personal-fact write by observing resulting state.

    Same reasoning as `_classify_not_stored()` does for the training seam:
    `StoreResult` does not distinguish its not-stored paths, and the
    difference matters. Content queued for consent is waiting in the inbox and
    will land if approved; content blocked by a `never_store` rule or declined
    by a registered consent handler never will. This checks whether a row
    actually appeared in the pending inbox -- an observation of state, not an
    inference about `upsert_memory()`'s control flow.
    """
    try:
        pending = await mem.list_pending_sensitive_writes(limit=50)
    except Exception:
        logger.exception("Failed to read pending consent queue while classifying a fact write")
        return FACT_OUTCOME_QUEUED_FOR_CONSENT

    for entry in pending:
        if entry.get("kind") == kind and entry.get("key") == key:
            return FACT_OUTCOME_QUEUED_FOR_CONSENT
    return FACT_OUTCOME_BLOCKED


async def _notify_fact_captured(daemon: KernelDaemon, outcomes: list[dict[str, Any]]) -> None:
    """
    Deliver one notification summarising what this turn remembered, through
    the existing governed `NotifySkill` path -- not a second notification
    mechanism, and not a direct call to the delivery function.

    `SkillRegistry.execute_action()` runs its own independent Governance pass
    on skill_id="notify" (brake + skill permissions + Identity Policy), and
    `NotifySkill._action_send()` applies S1.3's quiet-hours/mute rules. Both
    are reused exactly as `_notify_awaiting_response()` reuses them.

    Best-effort: a delivery failure must never undo a write that already
    committed, nor break the chat turn.
    """
    stored = [item for item in outcomes if item["outcome"] == FACT_OUTCOME_STORED]
    queued = [item for item in outcomes if item["outcome"] == FACT_OUTCOME_QUEUED_FOR_CONSENT]
    if not stored and not queued:
        return

    if stored:
        message = "Remembered: " + "; ".join(item["value"] for item in stored)
        title = "Bartholomew remembered something"
    else:
        # Deliberately does NOT include the content: it is sitting in the
        # consent queue precisely because it has not been approved for
        # storage, so quoting it in an outbound notification would leak
        # exactly what the gate is holding back.
        message = f"{len(queued)} thing(s) need your review before Bartholomew can remember them."
        title = "Bartholomew needs your consent"

    try:
        await run_skill_through_runtime_contract(
            daemon.skill_registry,
            "notify",
            "send",
            {"message": message, "title": title, "priority": "normal"},
        )
    except Exception:
        logger.exception("Failed to deliver a personal-fact capture notification")


async def _capture_personal_facts(
    daemon: KernelDaemon,
    observation: Observation,
) -> list[dict[str, Any]]:
    """
    Usable POC slice 1: propose durable personal facts found in this turn to
    Memory, through the existing governed write path.

    Every proposal goes through `MemoryStore.upsert_memory()` unchanged --
    same `memory_rules.yaml` evaluation, same `never_store` hard block, same
    `ask_before_store` -> `pending_sensitive_writes` consent queue, same
    `privacy_guard` gate, same redaction/encryption/FTS handling. There is no
    new write path here and no way to bypass one: this function chooses only
    *what to propose*, never whether it may be stored.

    Called only after the Governance stage allowed the turn, so an engaged
    parking brake yields zero writes and zero consent-queue entries.

    Never raises: memory capture must not be able to break a chat turn.
    """
    outcomes: list[dict[str, Any]] = []
    try:
        candidates = personal_facts.extract_facts(observation.raw_content or "")
        if not candidates:
            return outcomes

        ts = datetime.now(timezone.utc).isoformat()
        for fact in candidates:
            record = fact.to_dict()
            try:
                store_result = await daemon.mem.upsert_memory(
                    fact.kind,
                    fact.key,
                    fact.value,
                    ts,
                )
            except Exception:
                logger.exception("Personal-fact write failed for %s/%s", fact.kind, fact.key)
                record["outcome"] = FACT_OUTCOME_ERROR
                outcomes.append(record)
                continue

            if store_result.stored:
                record["outcome"] = FACT_OUTCOME_STORED
                record["memory_id"] = store_result.memory_id
                # Only ever recorded for content the governed path actually
                # stored -- never for content it is holding for consent.
                record["value"] = fact.value
            else:
                record["outcome"] = await _classify_fact_not_stored(
                    daemon.mem,
                    fact.kind,
                    fact.key,
                )
            outcomes.append(record)

        if outcomes and getattr(daemon, "skill_registry", None) is not None:
            await _notify_fact_captured(daemon, outcomes)
    except Exception:
        logger.exception("Personal-fact capture failed; the chat turn is unaffected")

    return outcomes


TASK_OUTCOME_EXECUTED = "executed"
TASK_OUTCOME_NOT_FOUND = "not_found"
TASK_OUTCOME_AMBIGUOUS = "ambiguous"
TASK_OUTCOME_UNSUPPORTED = "unsupported"
TASK_OUTCOME_FAILED = "failed"
TASK_OUTCOME_UNAVAILABLE = "unavailable"


async def _run_task_action(
    daemon: KernelDaemon,
    action: str,
    params: dict[str, Any],
) -> Any:
    """
    Run one task operation through the existing governed skill path.

    `Planner.handle_skill_request()` is the production route into skill
    execution: it validates that the skill and action really exist, then calls
    `run_skill_through_runtime_contract()` ->
    `SkillRegistry.execute_action()`, the single chokepoint where the parking
    brake (`skills` scope), the Identity Context -> Policy Decision on
    `skill_id="tasks"`, "ask"-level consent resolution, the
    `skill_action_audit` row and the unified Reflection all live.

    Nothing here reaches around that. There is no second executor, no direct
    `TasksSkill` call, and no path that skips a gate -- which is why this
    function is three lines long and the interesting code is all in the
    recogniser and the reply rendering.
    """
    planner = getattr(daemon, "planner", None)
    if planner is None:
        return None
    return await planner.handle_skill_request(task_intents.TASKS_SKILL_ID, action, params)


async def _resolve_subject(
    daemon: KernelDaemon,
    intent: task_intents.TaskIntent,
) -> tuple[task_intents.TaskResolution | None, Any]:
    """
    Turn the task title the user spoke into a real stored task.

    The candidate list is read through the governed `list` action, not by
    reading the skill's table -- so the read is gated, permission-checked and
    audited exactly like the write that may follow it. A failed read is
    returned as-is; the caller reports it rather than guessing.
    """
    listing = await _run_task_action(daemon, "list", {"status": "all", "limit": 200})
    if listing is None or not getattr(listing, "success", False):
        return None, listing

    tasks = listing.data if isinstance(listing.data, list) else []
    return task_intents.resolve_task(intent.subject or "", tasks), listing


async def _handle_task_intent(
    daemon: KernelDaemon,
    observation: Observation,
) -> dict[str, Any] | None:
    """
    Conversational task control: carry out an explicit task instruction the
    user gave in ordinary conversation, and report truthfully what happened.

    Returns None when the utterance contained no explicit task instruction --
    the overwhelmingly common case -- and the turn then proceeds exactly as it
    did before this existed. When it returns a dict, the `reply` in it is the
    turn's response: built from what the governed skill actually returned, not
    from what was asked for, so the model is never in a position to narrate an
    action that did not happen.

    **Why the turn's own CandidateAction stays `chat_response`.** The task
    operation is a *nested* governed action, evaluated for real at
    `execute_action()`'s Governance stage against `kind="tasks"` -- the exact
    grain `Identity.yaml`'s `tool_use.allowlist` uses. Re-evaluating the same
    decision at the chat gate as well would not add a gate; it would replace a
    truthful "I'm not permitted to do that, and nothing changed" reply with a
    denial of the whole conversation turn, which is both less informative and
    the blast radius that item 11.2's first attempt at drive gating produced.
    The brake is unaffected either way: chat's own Governance stage already
    fails closed on the `skills` scope before this function is ever reached,
    so a braked system answers nothing at all.

    **Never raises.** Task routing must not be able to break a chat turn. But
    it also never silently swallows: an unexpected failure becomes a truthful
    "it didn't go through" reply rather than a fall-through to the model,
    because falling through is precisely how a fabricated confirmation would
    reach the user.
    """
    try:
        intent = task_intents.parse_intent(observation.raw_content or "")
        if intent is None:
            return None

        record: dict[str, Any] = {
            "requested": intent.described_as,
            "action": intent.action,
            "changed": False,
        }

        # Recognised only so it can be declined truthfully. Nothing is
        # executed, and the reply says so plainly.
        if intent.action == task_intents.INTENT_UNSUPPORTED:
            record["outcome"] = TASK_OUTCOME_UNSUPPORTED
            record["reply"] = task_intents.render_unsupported(intent.described_as)
            return record

        if getattr(daemon, "planner", None) is None:
            record["outcome"] = TASK_OUTCOME_UNAVAILABLE
            record["reply"] = task_intents.render_failure(
                intent.described_as,
                "task management is not available in this session",
            )
            return record

        if intent.action == task_intents.INTENT_CREATE:
            result = await _run_task_action(daemon, "create", dict(intent.params))
            if result is not None and result.success and isinstance(result.data, dict):
                record["outcome"] = TASK_OUTCOME_EXECUTED
                record["changed"] = True
                record["task_id"] = result.data.get("id")
                record["reply"] = task_intents.render_created(result.data)
            else:
                record["outcome"] = TASK_OUTCOME_FAILED
                record["error"] = _task_error(result)
                record["reply"] = task_intents.render_failure(
                    intent.described_as,
                    record["error"],
                )
            return record

        if intent.action == task_intents.INTENT_LIST:
            status = intent.params.get("status", "pending")
            result = await _run_task_action(
                daemon,
                "list",
                {"status": status, "limit": 50},
            )
            if result is not None and result.success and isinstance(result.data, list):
                record["outcome"] = TASK_OUTCOME_EXECUTED
                record["count"] = len(result.data)
                record["reply"] = task_intents.render_list(result.data, status)
            else:
                record["outcome"] = TASK_OUTCOME_FAILED
                record["error"] = _task_error(result)
                record["reply"] = task_intents.render_failure(
                    intent.described_as,
                    record["error"],
                )
            return record

        # complete / update: both need a real task_id, which nobody says out
        # loud. Resolution can decline, and declining is not an action.
        resolution, listing = await _resolve_subject(daemon, intent)
        if resolution is None:
            record["outcome"] = TASK_OUTCOME_FAILED
            record["error"] = _task_error(listing)
            record["reply"] = task_intents.render_failure(intent.described_as, record["error"])
            return record

        if resolution.outcome == task_intents.NOT_FOUND:
            record["outcome"] = TASK_OUTCOME_NOT_FOUND
            record["reply"] = task_intents.render_not_found(intent.subject or "")
            return record

        if resolution.outcome == task_intents.AMBIGUOUS:
            record["outcome"] = TASK_OUTCOME_AMBIGUOUS
            record["candidates"] = [task.get("title") for task in resolution.candidates]
            record["reply"] = task_intents.render_ambiguous(
                intent.subject or "",
                resolution.candidates,
            )
            return record

        task_id = (resolution.task or {}).get("id")
        params = {"task_id": task_id, **intent.params}
        result = await _run_task_action(daemon, intent.action, params)
        record["task_id"] = task_id

        if result is not None and result.success and isinstance(result.data, dict):
            record["outcome"] = TASK_OUTCOME_EXECUTED
            record["changed"] = True
            if intent.action == task_intents.INTENT_COMPLETE:
                record["reply"] = task_intents.render_completed(result.data)
            else:
                record["reply"] = task_intents.render_updated(result.data, intent.params)
        else:
            record["outcome"] = TASK_OUTCOME_FAILED
            record["error"] = _task_error(result)
            record["reply"] = task_intents.render_failure(intent.described_as, record["error"])
        return record
    except Exception:
        logger.exception("Conversational task routing failed; reporting it rather than hiding it")
        return {
            "requested": "carry out a task instruction",
            "action": "unknown",
            "outcome": TASK_OUTCOME_FAILED,
            "changed": False,
            "error": "an internal error interrupted the task operation",
            "reply": task_intents.render_failure(
                "carry out that task instruction",
                "an internal error interrupted it",
            ),
        }


def _task_error(result: Any) -> str | None:
    """The verbatim reason a task operation did not succeed, or None.

    Deliberately reports the governed path's own words (a brake block, an
    Identity policy denial, a permission refusal) rather than paraphrasing
    them into something friendlier: a user told "I'm not permitted to do
    that" needs to be able to find out why.
    """
    if result is None:
        return "the task capability is not available"
    return getattr(result, "error", None) or getattr(result, "message", None) or None


# -----------------------------------------------------------------------------
# Golden Path first slice: the Executive's route to an *external* capability.
#
# Structurally identical to the conversational task control above, and
# deliberately so -- an external provider is reached through exactly the same
# governed path as a local skill, because `DECISIONS.md` clause (c) makes an
# external capability provider a **capability**, however intelligent it is
# internally. There is no separate external-call machinery here, and adding
# any would be the abstraction-before-use failure `docs/TILT.md` exists to
# prevent.
# -----------------------------------------------------------------------------

FORECAST_OUTCOME_OBTAINED = "obtained"
FORECAST_OUTCOME_UNSUPPORTED_PLACE = "unsupported_place"
FORECAST_OUTCOME_FAILED = "failed"
FORECAST_OUTCOME_UNAVAILABLE = "unavailable"
FORECAST_OUTCOME_DENIED = "denied"


async def _run_forecast_action(
    daemon: KernelDaemon,
    params: dict[str, Any],
) -> Any:
    """
    Run one forecast lookup through the existing governed skill path.

    Three lines, for the same reason `_run_task_action()` is three lines:
    everything that matters -- the parking brake on the `skills` scope, the
    Identity policy decision on `skill_id="forecast"`, the "ask"-level
    `network.fetch` consent, the `skill_action_audit` row and the unified
    Reflection -- already lives at `SkillRegistry.execute_action()`, and this
    slice's whole claim is that an external capability needs no path around
    it. Nothing here reaches around that; there is no second executor.
    """
    planner = getattr(daemon, "planner", None)
    if planner is None:
        return None
    return await planner.handle_skill_request(
        forecast_intents.FORECAST_SKILL_ID,
        forecast_intents.INTENT_LOOKUP,
        params,
    )


async def _handle_forecast_intent(
    daemon: KernelDaemon,
    observation: Observation,
) -> dict[str, Any] | None:
    """
    Obtain external evidence for an explicit forecast question and let the
    Executive answer with it.

    Returns None when the utterance was not an explicit forecast request --
    the overwhelmingly common case -- and the turn proceeds exactly as it did
    before this existed. When it returns a dict, the `reply` is built from
    what the governed skill actually returned: a failed, denied or
    unconfigured lookup produces a truthful "I don't have one", never a
    fall-through to the model, because falling through to a model that has
    been asked about tomorrow's weather is precisely how a fabricated
    forecast would reach the user.

    **Why the turn's own CandidateAction stays `chat_response`.** Same reason
    as `_handle_task_intent()`: the lookup is a *nested* governed action,
    evaluated for real against `kind="forecast"` at `execute_action()`'s
    Governance stage -- the grain `Identity.yaml`'s `tool_use.allowlist` uses.
    The brake is unaffected either way: chat's own Governance stage fails
    closed on the `skills` scope before this function is reached, and the
    skill's own gate fails closed again, so a braked system makes no external
    call by either route.

    **The provider does not become the Executive.** It is asked one bounded
    question with typed parameters and its answer comes back as evidence with
    provenance. What that evidence means for what the user was actually
    trying to decide is settled here and in `forecast_intents`, on
    Bartholomew's side of the boundary.
    """
    try:
        intent = forecast_intents.parse_intent(observation.raw_content or "")
        if intent is None:
            return None

        record: dict[str, Any] = {
            "requested": intent.described_as,
            "action": intent.action,
            "disclosed": None,
            "provider_host": None,
        }

        if intent.action == forecast_intents.INTENT_UNSUPPORTED_PLACE:
            # Declined without any external call at all -- the disclosure
            # never happens for a question Bartholomew cannot answer.
            record["outcome"] = FORECAST_OUTCOME_UNSUPPORTED_PLACE
            record["reply"] = forecast_intents.render_unsupported_place(intent)
            return record

        if getattr(daemon, "planner", None) is None or not _forecast_capability_installed(daemon):
            # The capability is not installed in this deployment at all --
            # a different thing from a lookup that was tried and failed, and
            # reported as such. Deliberately still not a fall-through to the
            # model: a Bartholomew without the capability cannot answer a
            # forecast question, and saying so is the whole point.
            record["outcome"] = FORECAST_OUTCOME_UNAVAILABLE
            record["reply"] = forecast_intents.render_unavailable(
                "the forecast capability is not available in this session.",
            )
            return record

        result = await _run_forecast_action(daemon, dict(intent.params))

        if result is None:
            record["outcome"] = FORECAST_OUTCOME_UNAVAILABLE
            record["reply"] = forecast_intents.render_unavailable(
                "the forecast capability is not available in this session.",
            )
            return record

        # Provenance travels with the record whatever the outcome: "we asked
        # this provider and got nothing" is evidence too, and a denial that
        # sent nothing must be distinguishable from a call that failed.
        data = result.data if isinstance(result.data, dict) else {}
        provenance = data.get("provenance") or {}
        record["provider_host"] = provenance.get("provider_host")
        record["disclosed"] = provenance.get("disclosed")
        record["attempted"] = bool(data.get("attempted", provenance.get("succeeded") is not None))

        if getattr(result, "success", False):
            record["outcome"] = FORECAST_OUTCOME_OBTAINED
            record["reply"] = forecast_intents.render_forecast(data, intent)
            record["days"] = len(data.get("days") or [])
            return record

        reason = _forecast_error(result)

        if getattr(result, "status", None) is not None and (
            getattr(result.status, "value", "") == "permission_denied"
        ):
            record["outcome"] = FORECAST_OUTCOME_DENIED
            record["reply"] = forecast_intents.render_denied(f"{reason}.")
            return record

        if data.get("outcome") in ("unconfigured", "host_not_allowed"):
            record["outcome"] = FORECAST_OUTCOME_UNAVAILABLE
            record["reply"] = forecast_intents.render_unavailable(f"{reason}.")
            return record

        record["outcome"] = FORECAST_OUTCOME_FAILED
        record["error"] = reason
        record["reply"] = forecast_intents.render_failure(intent, reason)
        return record
    except Exception:
        logger.exception("Forecast routing failed; reporting it rather than hiding it")
        return {
            "requested": "look up the forecast",
            "action": forecast_intents.INTENT_LOOKUP,
            "outcome": FORECAST_OUTCOME_FAILED,
            "error": "an internal error interrupted the forecast lookup",
            "reply": (
                "I tried to look up the forecast and an internal error interrupted it. "
                "I don't have a forecast to give you, and I'm not going to invent one."
            ),
        }


def _forecast_capability_installed(daemon: KernelDaemon) -> bool:
    """Whether this deployment has the forecast skill loaded at all.

    Checked before dispatching so an uninstalled capability is reported as
    *unavailable* rather than as a failed lookup -- and so the reply never
    relays the registry's internal "Skill not loaded: forecast" wording at a
    user who asked about the weather.
    """
    registry = getattr(daemon, "skill_registry", None)
    if registry is None:
        return False
    try:
        return forecast_intents.FORECAST_SKILL_ID in registry.list_loaded()
    except Exception:  # pragma: no cover - defensive
        return False


def _forecast_error(result: Any) -> str:
    """The governed path's own words for why no forecast came back."""
    if result is None:
        return "the forecast capability is not available"
    return (
        getattr(result, "error", None)
        or getattr(result, "message", None)
        or "the lookup did not succeed"
    )


OBJECTIVE_OUTCOME_EXECUTED = "executed"
OBJECTIVE_OUTCOME_NOT_FOUND = "not_found"
OBJECTIVE_OUTCOME_AMBIGUOUS = "ambiguous"
OBJECTIVE_OUTCOME_UNAVAILABLE = "unavailable"
OBJECTIVE_OUTCOME_FAILED = "failed"
OBJECTIVE_OUTCOME_DENIED = "denied"


async def _live_objectives(daemon: KernelDaemon) -> list[Any]:
    """The objectives Bartholomew is currently carrying.

    One read, `list_live()`, which cannot return a completed or abandoned
    objective. Everything downstream -- listing, matching, the interpretation
    block -- goes through here, so nothing has to remember to filter out
    finished work."""
    store = getattr(daemon, "objective_store", None)
    if store is None:
        return []
    return await run_off_loop(
        store.list_live,
        executor=getattr(daemon, "blocking_executor", None),
    )


def _objective_denied(result: Any) -> bool:
    return not getattr(result, "governance_allowed", True)


async def _handle_objective_intent(
    daemon: KernelDaemon,
    observation: Observation,
) -> dict[str, Any] | None:
    """
    Objective continuity: take on, report, close or drop an objective the
    user stated in ordinary conversation, and say truthfully what happened.

    Returns None when the utterance contained no explicit objective
    instruction -- the overwhelmingly common case -- and the turn proceeds
    exactly as it did before this existed.

    Like task control and the forecast slice, the reply is built from what
    the governed path actually did, never from what was asked for, so the
    model is never in a position to narrate a change that did not happen.

    **Why the turn's own CandidateAction stays `chat_response`.** Each
    objective operation is a *nested* governed transition, evaluated for real
    at `run_objective_through_runtime_contract()`'s two gates against
    `kind="objective_<transition>"` -- the same grain `Identity.yaml`'s
    `tool_use.allowlist` uses. This follows the recorded precedent set by
    `_handle_task_intent`: re-evaluating the same decision at the chat gate
    would replace a truthful "I'm not permitted to record that, and nothing
    changed" with a denial of the whole conversation turn.

    **Never raises.** Objective routing must not be able to break a chat
    turn -- but it never silently swallows either. An unexpected failure
    becomes a truthful "it didn't go through", because falling through to
    the model is exactly how a fabricated confirmation would reach the user.
    """
    try:
        intent = objective_intents.parse_intent(observation.raw_content or "")
        if intent is None:
            return None

        record: dict[str, Any] = {
            "requested": intent.described_as,
            "action": intent.action,
            "changed": False,
        }

        if getattr(daemon, "objective_store", None) is None:
            record["outcome"] = OBJECTIVE_OUTCOME_UNAVAILABLE
            record["reply"] = objective_intents.render_failure(
                intent.described_as,
                "objective tracking is not available in this session",
            )
            return record

        if intent.action == objective_intents.INTENT_LIST:
            objectives = await _live_objectives(daemon)
            record["outcome"] = OBJECTIVE_OUTCOME_EXECUTED
            record["count"] = len(objectives)
            record["reply"] = objective_intents.render_list(objectives)
            return record

        if intent.action == objective_intents.INTENT_OPEN:
            result = await run_objective_through_runtime_contract(
                daemon,
                "open",
                title=intent.title,
                outcome_statement=intent.outcome_statement,
                horizon_kind=intent.horizon_kind,
                horizon_date=intent.horizon_date,
                actor="chat",
            )
            if _objective_denied(result):
                record["outcome"] = OBJECTIVE_OUTCOME_DENIED
                record["error"] = result.reason
                record["reply"] = objective_intents.render_failure(
                    intent.described_as,
                    result.reason,
                )
                return record
            record["outcome"] = OBJECTIVE_OUTCOME_EXECUTED
            record["changed"] = True
            record["objective_id"] = getattr(result.objective, "id", None)
            record["reply"] = objective_intents.render_opened(result.objective)
            return record

        # complete / abandon: both need a real objective, which nobody names
        # by id. Resolution can decline, and declining is not a change.
        objectives = await _live_objectives(daemon)
        matched = objective_intents.match_objective(intent.subject or "", objectives)
        if matched is None:
            candidates = [
                o for o in objectives if objective_intents.relates_to(o, intent.subject or "")
            ]
            if len(candidates) > 1:
                record["outcome"] = OBJECTIVE_OUTCOME_AMBIGUOUS
                record["reply"] = objective_intents.render_ambiguous(
                    intent.subject or "",
                    candidates,
                )
                return record
            record["outcome"] = OBJECTIVE_OUTCOME_NOT_FOUND
            record["reply"] = objective_intents.render_not_found(intent.subject or "")
            return record

        transition = "complete" if intent.action == objective_intents.INTENT_COMPLETE else "abandon"
        result = await run_objective_through_runtime_contract(
            daemon,
            transition,
            objective_id=matched.id,
            resolution=(objective_store.RESOLUTION_ACHIEVED if transition == "complete" else None),
            outcome_note=observation.raw_content,
            actor="chat",
        )
        if _objective_denied(result):
            record["outcome"] = OBJECTIVE_OUTCOME_DENIED
            record["error"] = result.reason
            record["reply"] = objective_intents.render_failure(
                intent.described_as,
                result.reason,
            )
            return record

        record["outcome"] = OBJECTIVE_OUTCOME_EXECUTED
        record["changed"] = True
        record["objective_id"] = matched.id
        record["reply"] = (
            objective_intents.render_completed(result.objective)
            if transition == "complete"
            else objective_intents.render_abandoned(result.objective)
        )
        return record
    except Exception:
        logger.exception("Objective routing failed; reporting it rather than hiding it")
        return {
            "requested": "update what you're working towards",
            "action": "unknown",
            "outcome": OBJECTIVE_OUTCOME_FAILED,
            "changed": False,
            "error": "an internal error interrupted it",
            "reply": (
                "I tried to update what I'm keeping track of and an internal error "
                "interrupted it. Nothing was recorded, so please tell me again."
            ),
        }


async def _attach_forecast_evidence(
    daemon: KernelDaemon,
    observation: Observation,
    forecast_action: dict[str, Any],
) -> dict[str, Any] | None:
    """Attach a forecast this turn obtained to the objective it bears on.

    The external provider contributes **evidence**, and only evidence. It
    does not decide anything, does not become the Executive and cannot reach
    an objective on its own: the Executive matched the utterance to one
    objective and recorded the provider's own provenance block against it,
    with `event_kind="fact"`. The forecast skill is unchanged and has no
    knowledge that objectives exist.

    Bounded deliberately:
      * only when the lookup actually succeeded -- a failed or denied lookup
        is not evidence of anything, and recording "we asked and got nothing"
        against an objective would clutter its history with non-events;
      * only when exactly one live objective matches. Ambiguity records
        nothing, because a fact filed against the wrong objective is read
        back later as if it belonged there;
      * never raises, and never affects the turn's reply. This is
        bookkeeping in service of the *next* interaction.
    """
    if forecast_action.get("outcome") != FORECAST_OUTCOME_OBTAINED:
        return None
    try:
        objectives = await _live_objectives(daemon)
        related = [
            o for o in objectives if objective_intents.relates_to(o, observation.raw_content or "")
        ]
        if len(related) != 1:
            return None
        objective = related[0]

        provenance = {
            "source_kind": "external_capability",
            "provider_host": forecast_action.get("provider_host"),
            "disclosed": forecast_action.get("disclosed"),
            # The same posture DECISIONS.md clause (d) established for the
            # forecast slice, carried into the objective's own history: this
            # is an external assertion, not an established fact.
            "evidence": True,
        }
        result = await run_objective_through_runtime_contract(
            daemon,
            "record",
            objective_id=objective.id,
            event_kind=objective_store.EVENT_FACT,
            summary=(forecast_action.get("reply") or "forecast obtained")[:300],
            provenance=provenance,
            actor="chat:forecast",
        )
        if _objective_denied(result):
            return None
        return {
            "objective_id": objective.id,
            "objective_title": objective.title,
            "event_kind": objective_store.EVENT_FACT,
        }
    except Exception:
        logger.exception("Attaching forecast evidence to an objective failed")
        return None


#: Dispatch names, one per explicit-instruction recogniser. Constants rather
#: than bare strings so the table below and the result-field reads in
#: `run_chat_through_runtime_contract()` cannot drift apart silently.
_DISPATCH_TASK = "task"
_DISPATCH_FORECAST = "forecast"
_DISPATCH_OBJECTIVE = "objective"

#: The chat surface's explicit-instruction recognisers, **in dispatch order**.
#:
#: This is a table, not a framework. There is no registration API, no
#: discovery, no priority negotiation and no way for anything outside this
#: module to add an entry: it is the same ordered `if/elif` chain the chat
#: turn always had, written once so a further recogniser is one line rather
#: than one more level of nesting.
#:
#: Order is behaviour and is pinned by test. Task control comes first because
#: an explicit task instruction is unambiguous and must never be reinterpreted
#: as something else; each handler returns None for anything it does not
#: explicitly claim, so the common case reaches the model unchanged.
_CHAT_DISPATCH: tuple[
    tuple[str, Callable[[KernelDaemon, Observation], Awaitable[dict[str, Any] | None]]],
    ...,
] = (
    (_DISPATCH_TASK, _handle_task_intent),
    (_DISPATCH_FORECAST, _handle_forecast_intent),
    # Golden Path slice 2. Last, deliberately: "add a task to ring the
    # roofer" is a task instruction and "will it rain on Thursday" is a
    # forecast question, and neither should be reinterpreted as establishing
    # an objective. The objective recogniser is the broadest of the three,
    # so it goes where a broad recogniser belongs -- after the narrow ones.
    (_DISPATCH_OBJECTIVE, _handle_objective_intent),
)


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
    #
    # S5.3: this is also where relevant competency retrieval happens, per
    # COGNITIVE_RUNTIME.md's Executive/Interpretation rows. Chat is the only
    # surface that gets it (design Decision B).
    memory_context = await _retrieve_memory_context(daemon, observation)
    competency_context = memory_context.competency
    personal_fact_context = memory_context.personal
    live_objectives = await _live_objectives(daemon)
    interpretation = _build_interpretation(
        daemon,
        observation,
        competency_block=render_for_prompt(competency_context),
        personal_facts_block=personal_facts.render_facts_for_prompt(personal_fact_context),
        objectives_block=render_objectives_for_prompt(live_objectives),
    )

    # Stage 3: Executive -- propose a candidate action, now carrying whichever
    # competencies informed it (S5.3) and whichever remembered personal facts
    # were recalled (Usable POC slice 1). Governance still decides admission;
    # a competency's recorded supervision can only ever ADD a review
    # requirement, never relax one.
    candidate_action = CandidateAction(
        kind="chat_response",
        interpretation=interpretation,
        competency_context=competency_context,
        personal_fact_context=personal_fact_context,
    )

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
    captured_facts: list[dict[str, Any]] = []

    #: What each recogniser in `_CHAT_DISPATCH` produced, keyed by its
    #: dispatch name. At most one entry: the first recogniser to claim the
    #: utterance owns the turn. The per-recogniser result fields on
    #: `RuntimeContractResult` are read back out of this below, so adding a
    #: recogniser never changes this function's control flow.
    recognised: dict[str, dict[str, Any]] = {}
    task_action: dict[str, Any] | None = None
    forecast_action: dict[str, Any] | None = None
    objective_action: dict[str, Any] | None = None
    objective_evidence: dict[str, Any] | None = None

    if governance_allowed:
        # Stage 5+6: Capability + Execution.
        #
        # Explicit-instruction dispatch, in the fixed order _CHAT_DISPATCH
        # declares. The first recogniser that claims the utterance owns the
        # turn's reply, and the model is deliberately not asked for one in
        # that case -- a generated sentence about an action that has already
        # happened (or has just been refused) could contradict it, and the
        # user would have no way to tell which was true. When no recogniser
        # claims it -- the overwhelmingly common case -- the turn falls
        # through to the model exactly as it always has.
        for name, handler in _CHAT_DISPATCH:
            outcome = await handler(daemon, observation)
            if outcome is not None:
                recognised[name] = outcome
                response = outcome["reply"]
                break
        else:
            response = await respond_fn(interpretation.prompt)

        task_action = recognised.get(_DISPATCH_TASK)
        forecast_action = recognised.get(_DISPATCH_FORECAST)
        objective_action = recognised.get(_DISPATCH_OBJECTIVE)

        # Golden Path slice 2: a forecast obtained during this turn becomes
        # evidence on the objective it bears on. After the reply is settled
        # and never able to change it -- continuity is a property of the
        # *next* interaction, and bookkeeping must not be able to alter what
        # the user is told now.
        if forecast_action is not None:
            objective_evidence = await _attach_forecast_evidence(
                daemon,
                observation,
                forecast_action,
            )

        # Stage 7: Reflection -- record the interaction in Working Memory
        # (chat's short-term context buffer; feeds get_context_string()).
        item = daemon.working_memory.add(
            content=f"User: {user_input}\nBartholomew: {response}",
            source="chat",
            tags=["chat", candidate_action.kind],
        )
        working_memory_item_id = item.item_id
        # S5.3 Decision E.2: record the applied competency context here, in
        # the existing shared reflections sink, at explanation grade --
        # per-record identity, provenance, classification and confidence. A
        # decision cannot be reconstructed after the fact, so recording only
        # a count would foreclose a future user-requested explanation
        # capability by omission. This is not exposed to the user (E.1).
        details: dict[str, Any] = {"response_preview": (response or "")[:200]}
        if not competency_context.is_empty():
            details["competency_context"] = competency_context.to_dict()
        if not personal_fact_context.is_empty():
            details["personal_fact_context"] = personal_fact_context.to_dict()

        # Usable POC slice 1: capture durable personal facts from this turn.
        # Deliberately here -- inside the governance-allowed branch, after the
        # response -- so an engaged brake or a policy denial produces zero
        # writes, and so capture can never influence the same turn's answer
        # (recall is a *later*-turn property; see the slice's acceptance bar).
        captured_facts = await _capture_personal_facts(daemon, observation)
        if captured_facts:
            details["personal_facts_captured"] = captured_facts
        if task_action is not None:
            # Explanation-grade, same posture as the competency/personal-fact
            # records above: what was asked for, what the governed path did,
            # and whether anything actually changed. The skill's own
            # `skill_action_audit` row and Reflection still exist and are
            # unchanged -- this is the chat surface's record that the turn
            # routed to one.
            details["task_action"] = task_action
        if forecast_action is not None:
            # Explanation-grade provenance for the chat surface: what was
            # asked, which provider was consulted, **exactly what was
            # disclosed to it**, and what came back. The disclosure record is
            # the point -- an egress nobody recorded is an egress nobody can
            # audit. The skill's own `skill_action_audit` row and unified
            # Reflection still exist and are unchanged.
            details["forecast_action"] = forecast_action
        if objective_action is not None:
            # Explanation-grade, same posture as the records above: what the
            # user asked for, what the governed objective seam did, and
            # whether anything actually changed.
            details["objective_action"] = objective_action
        if objective_evidence is not None:
            # External content entering an objective's durable history is
            # exactly the kind of thing that must be visible afterwards.
            details["objective_evidence"] = objective_evidence

        reflection = ActionReflection(
            surface="chat",
            action=candidate_action.kind,
            outcome="responded",
            summary=f"Chat turn ({candidate_action.kind}): responded",
            details=details,
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
    # denied), closing Exit Gate #4. Never breaks the turn -- but on this
    # surface the Reflection is required provenance (S5.3 E.2: "a decision
    # cannot be reconstructed after the fact"), so WP-A2b: a lost write is
    # carried on the result instead of being swallowed in the sink. Working
    # Memory's own durability stays its concern
    # (WorkingMemoryManager.persist_snapshot() on KernelDaemon.stop()).
    reflection_outcome = await record_action_reflection(daemon.mem, reflection)

    return RuntimeContractResult(
        observation=observation,
        interpretation=interpretation,
        candidate_action=candidate_action,
        governance_allowed=governance_allowed,
        governance_reason=governance_reason,
        response=response,
        working_memory_item_id=working_memory_item_id,
        personal_facts_captured=captured_facts,
        task_action=task_action,
        forecast_action=forecast_action,
        objective_action=objective_action,
        objective_evidence=objective_evidence,
        provenance_degraded=reflection_outcome.error is not None,
        provenance_error=reflection_outcome.error,
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

# Spoken output. Deliberately NOT suffixed "_start" and deliberately a
# separate kind from `_VOICE_STREAM_KIND`: the two are opposite directions
# through the same physical device family, and conflating them would let an
# allowlist entry for speaking read as one for listening. This kind
# authorises one utterance out of this machine's speaker. It authorises no
# capture of any kind -- there is no capture code behind it to authorise.
_VOICE_SPEAK_KIND = "voice_speak"


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

    #: WP-A2b. True when this start attempt's Reflection -- the *only*
    #: persisted record of the governance outcome on the sight/voice
    #: surfaces -- failed to persist. `started`/`outcome` are unaffected: a
    #: capability that genuinely started is never reported as failed for a
    #: lost provenance record, and nothing is retried -- but the attempt
    #: must not present as fully recorded. Defaulted so existing
    #: construction sites are unaffected.
    provenance_degraded: bool = False

    #: The reflection-write failure, verbatim, when `provenance_degraded`.
    provenance_error: str | None = None


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
) -> ReflectionWriteOutcome:
    """Exactly one ActionReflection into the shared Memory sink for a
    voice/sight start attempt -- every outcome (started or any denial/error).

    Never raises and never breaks the surface -- but on these surfaces the
    Reflection is the *sole* persisted record of the governance outcome, so
    WP-A2b: what became of the write is returned for the seam to carry on
    its `DeviceRuntimeResult` instead of being swallowed. Because this
    function owns constructing its own store, a store-construction failure
    is a persistence failure of that sole record and is reported as one; a
    caller that passed no `db_path` at all (duck-typed test configuration)
    attempted no persistence and gets a clean outcome, same as before.
    """
    mem = None
    if db_path:
        try:
            from .memory_store import MemoryStore

            mem = MemoryStore(db_path)
        except Exception as exc:
            logger.exception("Failed to construct MemoryStore for device reflection")
            return ReflectionWriteOutcome(
                error=f"reflection write failed ({surface}): store construction failed: {exc}",
            )
    reflection = ActionReflection(
        surface=surface,
        action=kind,
        outcome=outcome,
        summary=f"{surface.capitalize()} {kind}: {outcome}",
        details={"reason": reason} if reason else {},
    )
    return await record_action_reflection(mem, reflection)


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
      1. ParkingBrake("sight") -- read through `GovernanceStore`, the same
         authority chat/scheduler/skill execution read, and fail-closed on
         an unreadable gate. (This gate previously read the legacy
         `system_flags` row, which nothing has written since Phase B6
         retired the dual-check bridge -- so engaging the brake did not
         stop this seam. See the gate's own comment below.)
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

    # Governance gate 1: ParkingBrake("sight"), read through GovernanceStore.
    #
    # This used to be `ParkingBrake(BrakeStorage(...))`, which reads the
    # legacy `system_flags` "parking_brake" row. Phase B6 retired the
    # dual-check bridge that kept that row in step with the real state, and
    # both writers -- `bartholomew brake on` and the API's
    # /governance/brake/engage route -- write GovernanceStore only. This gate
    # was therefore reading a value nothing updates any more: engaging the
    # brake did not stop this seam. It now reads the same authority chat,
    # scheduler drives and skill execution read.
    #
    # Fails closed on any error: an unreadable safety gate must deny a device
    # start, never wave it through. The previous `except ImportError: pass`
    # tolerance deliberately does not survive -- it existed for a module that
    # might not be importable, and silently continuing past an unreadable
    # brake is not a tolerance a device surface can afford.
    try:
        from bartholomew.orchestrator.safety.governance_store import (
            is_blocked_fail_closed_off_loop,
        )

        if resolved_db_path is not None and await is_blocked_fail_closed_off_loop(
            "sight",
            resolved_db_path,
        ):
            allowed = False
            outcome = "parking_brake_denied"
            reason = "Blocked by parking brake (scope=sight)"
    except Exception:
        logger.exception("Brake check failed for scope=sight; failing closed")
        allowed = False
        outcome = "parking_brake_denied"
        reason = "Parking brake check errored"

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

    device_reflection = await _record_device_reflection(
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
        provenance_degraded=device_reflection.error is not None,
        provenance_error=device_reflection.error,
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

    # Governance gate 1: ParkingBrake("voice"), read through GovernanceStore.
    #
    # This used to be `ParkingBrake(BrakeStorage(...))`, which reads the
    # legacy `system_flags` "parking_brake" row. Phase B6 retired the
    # dual-check bridge that kept that row in step with the real state, and
    # both writers -- `bartholomew brake on` and the API's
    # /governance/brake/engage route -- write GovernanceStore only. This gate
    # was therefore reading a value nothing updates any more: engaging the
    # brake did not stop this seam. It now reads the same authority chat,
    # scheduler drives and skill execution read.
    #
    # Fails closed on any error: an unreadable safety gate must deny a device
    # start, never wave it through. The previous `except ImportError: pass`
    # tolerance deliberately does not survive -- it existed for a module that
    # might not be importable, and silently continuing past an unreadable
    # brake is not a tolerance a device surface can afford.
    try:
        from bartholomew.orchestrator.safety.governance_store import (
            is_blocked_fail_closed_off_loop,
        )

        if resolved_db_path is not None and await is_blocked_fail_closed_off_loop(
            "voice",
            resolved_db_path,
        ):
            allowed = False
            outcome = "parking_brake_denied"
            reason = "Blocked by parking brake (scope=voice)"
    except Exception:
        logger.exception("Brake check failed for scope=voice; failing closed")
        allowed = False
        outcome = "parking_brake_denied"
        reason = "Parking brake check errored"

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

    device_reflection = await _record_device_reflection(
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
        provenance_degraded=device_reflection.error is not None,
        provenance_error=device_reflection.error,
        result=stream_result,
    )


async def run_spoken_output_through_runtime_contract(
    text: str,
    *,
    enabled: bool = False,
    db_path: str | None = None,
    identity_context: IdentityContext | None = None,
    speak_fn: Callable[[str], Any] | None = None,
    blocking_executor: Any | None = None,
) -> DeviceRuntimeResult:
    """
    Say one thing out loud, through the Runtime Contract seam.

    The same shape as the two device seams above -- Observation ->
    Interpretation -> Executive -> Governance -> Capability -> Execution ->
    Reflection -> Memory, one `ActionReflection` into the shared sink for
    every outcome, `DeviceRuntimeResult` returned -- reusing their helpers
    rather than growing a parallel path. There is no second Governance
    authority here and no second reflection sink.

    Governance runs three gates, all strictly before anything is spoken:

      1. **Enablement.** `config/kernel.yaml`'s `voice.spoken_output`, default
         `false`, read in exactly one place (`spoken_output.enabled_for()`)
         and passed in here. Off means silence, and `enabled` defaults to
         `False` so a caller that forgets to pass it gets silence too.
      2. **ParkingBrake("voice")** -- the same scope the voice stream seam
         uses, with the same `ImportError` tolerance. An engaged voice brake
         silences spoken output completely, which is the behaviour the
         sprint's boundaries require.
      3. **Identity Policy Decision** on `_VOICE_SPEAK_KIND` -- additive and
         skipped when no `IdentityContext` is wired in, matching every other
         surface.

    **Why there is no device-consent gate here, and why that is not a
    weakening.** The sight and voice-stream seams always require interactive
    device consent because they *capture*: they are exactly `policy.yaml`'s
    "record audio/video without explicit approval" category. This seam
    captures nothing -- no microphone is opened, no audio is recorded, no
    sensor is read. Reusing `_resolve_device_consent()` would put the words
    "Bartholomew requests to start microphone streaming" in front of a user
    when no microphone is involved, which would be a false statement about
    what the system is doing. What speaking aloud *does* risk is broadcasting
    an answer into a room, and the control for that is gate 1: an operator
    turning `voice.spoken_output` on for this machine, revocable at any time
    and overridable instantly by the brake.

    `speak_fn` is injected, like `capture_fn`/`stream_fn`, so this seam owns
    Governance while `spoken_output.py` owns the capability -- and so the
    capability is reachable only through this path in production.

    Never raises: a speech engine that fails, times out or does not exist
    becomes an "error" outcome with a reason, never an exception into a chat
    turn or a CLI command.
    """
    observation = Observation(source="voice_output", raw_content=_VOICE_SPEAK_KIND)
    interpretation = Interpretation(observation=observation, prompt=observation.raw_content)
    candidate_action = CandidateAction(kind=_VOICE_SPEAK_KIND, interpretation=interpretation)
    resolved_db_path = _resolve_device_db_path(db_path)

    allowed = True
    outcome = "started"
    reason: str | None = None

    # Governance gate 1: enablement. Deliberately first and deliberately
    # cheap -- a disabled capability should not even read the brake.
    if not enabled:
        allowed = False
        outcome = "governance_denied"
        reason = "Spoken output is disabled (config/kernel.yaml: voice.spoken_output)"

    # Governance gate 2: ParkingBrake("voice"), read through GovernanceStore.
    #
    # `is_blocked_fail_closed_off_loop()` -- the same read chat, scheduler
    # drives, skill execution and (since this defect was found and fixed) the
    # sight/voice-stream seams above all use. One brake authority, read the
    # same way everywhere.
    #
    # Fails closed on any error: for a capability whose whole effect is
    # audible, an unreadable safety gate must mean silence.
    if allowed:
        try:
            from bartholomew.orchestrator.safety.governance_store import (
                is_blocked_fail_closed_off_loop,
            )

            if resolved_db_path is not None and await is_blocked_fail_closed_off_loop(
                "voice",
                resolved_db_path,
                executor=blocking_executor,
            ):
                allowed = False
                outcome = "parking_brake_denied"
                reason = "Blocked by parking brake (scope=voice)"
        except Exception:
            logger.exception("Voice brake check failed; failing closed (staying silent)")
            allowed = False
            outcome = "parking_brake_denied"
            reason = "Parking brake check errored"

    # Governance gate 3: Identity Policy Decision (additive; see sight docstring).
    if allowed and identity_context is not None:
        decision = policy_engine.evaluate_tool_policy(identity_context, candidate_action.kind)
        if not decision.allowed:
            allowed = False
            outcome = "governance_denied"
            reason = f"Denied by Identity policy: {decision.reason}"

    # Capability + Execution -- reached only after all three gates allowed.
    governance_allowed = allowed
    started = False
    speech_result: Any = None
    if governance_allowed:
        try:
            if speak_fn is not None:
                speech_result = speak_fn(text)
                if inspect.isawaitable(speech_result):
                    speech_result = await speech_result
            else:
                # Blocking subprocess work, off the event loop (B2/B8).
                speech_result = await run_off_loop(
                    spoken_output.speak_text,
                    text,
                    executor=blocking_executor,
                )
            spoken = bool(getattr(speech_result, "spoken", False))
            if spoken:
                outcome, reason, started = "started", None, True
            else:
                # The engine did not speak. That is an outcome, not a
                # success with a caveat: reporting it as "started" would put
                # a silent machine and a talking one in the same bucket.
                outcome = "error"
                reason = getattr(speech_result, "detail", None) or "speech did not occur"
                started = False
        except Exception as exc:
            logger.exception("Spoken output failed after governance approval")
            outcome, reason, started = "error", str(exc), False

    device_reflection = await _record_device_reflection(
        resolved_db_path,
        "voice_output",
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
        provenance_degraded=device_reflection.error is not None,
        provenance_error=device_reflection.error,
        result=speech_result,
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
# Training surface (Stage 5, S5.2) -- the governed write path by which training
# material becomes stored competency records.
#
# Per docs/S5_2_TRAINING_KNOWLEDGE_ACQUISITION_DESIGN.md (design and Decisions
# A-E approved 2026-08-11). Training enters as an Observation through this
# same seam every other surface uses, and lands in Memory through the existing
# MemoryStore.upsert_memory() chain -- no separate ingestion runtime, no second
# Memory authority, no second Governance path (COGNITIVE_RUNTIME.md: "Training
# as Memory input, not a separate pipeline").
#
# Constraint 1 (design Sec.5.5/9.1): this seam consumes structured,
# provenance-bearing records -- never keystrokes. It must not acquire any
# structural assumption that a human authored the submission. That is what
# lets future conversational / model-assisted / document-extraction paths
# ("Layer 0") feed this same governed write later. Structured manual
# submission is the first client of this seam, not the intended final user
# experience.
# =============================================================================


async def _record_training_reflection(
    daemon: KernelDaemon,
    candidate_action: CandidateAction,
    outcome: str,
    details: dict[str, Any] | None = None,
) -> ReflectionWriteOutcome:
    """Reflection -> Memory tail for one training submission (Exit Gate #4's
    shared sink, extended to the training surface).

    This is also where supersession history lives: `reflections` is a
    separate, append-only table, so overwriting a competency record's
    current state in `memories` does not lose the superseded claim's
    provenance (design Sec.13.4). No new store, no second write authority.
    """
    reflection = ActionReflection(
        surface="training",
        action=candidate_action.kind,
        outcome=outcome,
        summary=f"Training ingestion ({candidate_action.kind}): {outcome}",
        details=details or {},
    )
    return await record_action_reflection(getattr(daemon, "mem", None), reflection)


async def _classify_not_stored(
    mem: Any,
    kind: str,
    key: str,
    allow_store: bool,
) -> tuple[str, str | None]:
    """
    Explain a `stored=False` result, by observing resulting state rather than
    re-deriving `upsert_memory()`'s internal branching.

    `StoreResult` does not distinguish its four not-stored paths, and the
    difference matters to the user: content queued for consent is waiting in
    the inbox and will land once approved, whereas a policy rejection or an
    explicitly declined consent prompt never will. Telling someone "trained"
    (or even just "not stored") without that distinction would misinform them
    about what Bartholomew actually knows.

    `allow_store` comes from a read-only rules evaluation and identifies the
    hard `never_store` block. For everything else this checks whether a row
    actually appeared in the pending inbox -- an observation of state, not an
    inference about control flow, so it stays correct if upsert_memory()'s
    internals change.
    """
    if not allow_store:
        return (
            training.OUTCOME_REJECTED_BY_POLICY,
            "blocked by a never_store governance rule",
        )

    try:
        pending = await mem.list_pending_sensitive_writes(limit=50)
    except Exception:
        logger.exception("Failed to read pending consent queue while classifying training outcome")
        return (
            training.OUTCOME_QUEUED_FOR_CONSENT,
            "not stored; pending-queue state could not be confirmed",
        )

    for entry in pending:
        if entry.get("kind") == kind and entry.get("key") == key:
            return (
                training.OUTCOME_QUEUED_FOR_CONSENT,
                f"awaiting consent in the review inbox (pending_id={entry.get('id')}, "
                f"reason={entry.get('reason')})",
            )

    return (
        training.OUTCOME_DECLINED_BY_CONSENT_HANDLER,
        "a registered consent handler declined this write; it was not queued",
    )


async def run_training_through_runtime_contract(
    daemon: KernelDaemon,
    submission: training.TrainingSubmission,
    *,
    recorded_by: str = "user",
    allow_consolidation_source: bool = False,
    allow_share_adoption_source: bool = False,
) -> training.TrainingRuntimeResult:
    """
    Trace one training submission through the Runtime Contract seam.

    Stages, in order:
      1. Observation  -- source="training", the submitted material
      2. Interpretation -- enriched with the same Experience Kernel state
         every other surface sees
      3. CandidateAction -- kind="training_ingest": a proposal to write
      4. Governance -- fail-closed brake check on the "training" scope,
         BEFORE any record is processed, so a blocked brake yields zero
         writes and zero consent-queue entries
      5. Memory -- each record written via the existing
         MemoryStore.upsert_memory(), per-record independent (Decision C)
      6. Reflection -- one per submission, including any supersession

    `recorded_by` is supplied by the ingestion route, never read from the
    submission or a request body (design Sec.5.3): a caller must not be able
    to claim material came from the user when it came from elsewhere. Its
    companion `recorded_at` is the server clock, for the same reason.

    `allow_consolidation_source` is the S5.4 lift, and is passed by exactly
    one caller: `run_candidate_lesson_through_runtime_contract()`'s accept
    branch, consolidating a human-reviewed candidate lesson. Every
    user-facing ingestion surface leaves it at False, so nothing outside the
    review-gated learning loop can write a record claiming `experience`
    provenance. Consolidation reuses this seam rather than acquiring a second
    governed write path.

    `allow_share_adoption_source` is Package E's equivalent lift for
    `trusted_share`, passed by exactly one caller:
    `run_share_adoption_through_runtime_contract()`'s accept branch. Two flags
    rather than one widened flag, so consolidating a lesson from local
    experience and consolidating a share adopted from a housemate are
    separately authorised and neither caller can reach the other's source
    type.
    """
    import json as _json

    from .competency import PROVENANCE_RECORDED_BY_VALUES
    from .memory_rules import MemoryRulesEngine

    result = training.TrainingRuntimeResult(
        competency_id=submission.competency_id,
        governance_allowed=False,
    )

    if recorded_by not in PROVENANCE_RECORDED_BY_VALUES:
        result.errors.append(
            f"recorded_by must be one of {sorted(PROVENANCE_RECORDED_BY_VALUES)}, "
            f"got {recorded_by!r}",
        )
        return result

    submission_errors = submission.validate(
        allow_consolidation_source=allow_consolidation_source,
        allow_share_adoption_source=allow_share_adoption_source,
    )
    if submission_errors:
        result.errors.extend(submission_errors)
        return result

    observation = Observation(
        source=training.TRAINING_OBSERVATION_SOURCE,
        raw_content=submission.source_detail,
    )
    interpretation = _build_interpretation(daemon, observation)
    candidate_action = CandidateAction(
        kind=training.TRAINING_ACTION_KIND,
        interpretation=interpretation,
    )

    # --- Governance: fail-closed, before any record is processed ---------
    # Same authority every other surface's seam uses (chat, scheduler): the
    # B6 GovernanceStore path, not the retired legacy ParkingBrake writer.
    from bartholomew.orchestrator.safety.governance_store import (
        is_blocked_fail_closed_off_loop,
    )

    blocked = await is_blocked_fail_closed_off_loop(
        training.TRAINING_BRAKE_SCOPE,
        daemon.mem.db_path,
        governance_store=getattr(daemon, "governance_store", None),
        executor=getattr(daemon, "blocking_executor", None),
    )
    if blocked:
        result.governance_allowed = False
        result.governance_reason = "parking brake engaged for scope 'training'"
        for record in submission.records:
            result.outcomes.append(
                training.TrainingRecordOutcome(
                    kind=record.KIND,
                    key=record.key(),
                    outcome=training.OUTCOME_BLOCKED_BY_GOVERNANCE,
                    detail=result.governance_reason,
                ),
            )
        blocked_reflection = await _record_training_reflection(
            daemon,
            candidate_action,
            "blocked_by_governance",
            {"competency_id": submission.competency_id, "reason": result.governance_reason},
        )
        # A blocked submission is not a success of any kind, so there is no
        # "full success" for a lost reflection to contradict -- but the loss
        # is still reported truthfully rather than swallowed.
        result.provenance_degraded = blocked_reflection.error is not None
        result.provenance_error = blocked_reflection.error
        return result

    result.governance_allowed = True

    # --- Memory: per-record independence (Decision C) --------------------
    provenance = training.stamp_provenance(submission, recorded_by=recorded_by)
    rules_engine = MemoryRulesEngine(watch_file=False)
    now_iso = datetime.now(timezone.utc).isoformat()
    supersessions: list[dict[str, Any]] = []

    for record in submission.records:
        kind = record.KIND
        key = record.key()

        record_errors = record.validate()
        if record_errors:
            result.outcomes.append(
                training.TrainingRecordOutcome(
                    kind=kind,
                    key=key,
                    outcome=training.OUTCOME_INVALID,
                    detail="; ".join(record_errors),
                ),
            )
            continue

        # Seam-derived provenance overwrites whatever the caller supplied
        # (design Sec.5.3) -- source_type/detail from the submission,
        # recorded_by/recorded_at from the route and the server clock.
        record.envelope.provenance = provenance
        record.envelope.updated_at = now_iso

        # Supersession: read current state, bump revision, remember the
        # superseded claim for the Reflection (design Sec.13.4).
        superseded_revision: int | None = None
        try:
            existing = await daemon.mem.get_memory(kind, key)
        except Exception:
            logger.exception("Failed to read existing competency record %s/%s", kind, key)
            existing = None

        if existing:
            try:
                prior = _json.loads(existing["value"])
                superseded_revision = int(prior.get("revision", 1))
                record.envelope.revision = superseded_revision + 1
                supersessions.append(
                    {
                        "kind": kind,
                        "key": key,
                        "superseded_revision": superseded_revision,
                        "new_revision": record.envelope.revision,
                        "superseded_provenance": prior.get("provenance"),
                    },
                )
            except (ValueError, TypeError, KeyError):
                # Stored value isn't parseable competency JSON (e.g. a
                # summary substitution). Don't guess a revision -- record
                # the fact instead of inventing history.
                supersessions.append(
                    {
                        "kind": kind,
                        "key": key,
                        "superseded_revision": None,
                        "new_revision": record.envelope.revision,
                        "note": "prior value was not parseable competency JSON",
                    },
                )

        value_json = _json.dumps(record.to_dict())
        evaluated = rules_engine.evaluate({"kind": kind, "key": key, "value": value_json})
        allow_store = bool(evaluated.get("allow_store", True))

        try:
            store_result = await daemon.mem.upsert_memory(
                kind,
                key,
                value_json,
                now_iso,
                summary=record.to_summary_text(),
            )
        except Exception as exc:
            logger.exception("Training write failed for %s/%s", kind, key)
            result.outcomes.append(
                training.TrainingRecordOutcome(
                    kind=kind,
                    key=key,
                    outcome=training.OUTCOME_INVALID,
                    detail=f"write failed: {exc}",
                ),
            )
            continue

        if store_result.stored:
            result.outcomes.append(
                training.TrainingRecordOutcome(
                    kind=kind,
                    key=key,
                    outcome=training.OUTCOME_STORED,
                    memory_id=store_result.memory_id,
                    revision=record.envelope.revision,
                    superseded_revision=superseded_revision,
                ),
            )
        else:
            outcome, detail = await _classify_not_stored(daemon.mem, kind, key, allow_store)
            result.outcomes.append(
                training.TrainingRecordOutcome(
                    kind=kind,
                    key=key,
                    outcome=outcome,
                    detail=detail,
                    revision=record.envelope.revision,
                    superseded_revision=superseded_revision,
                ),
            )

    ingest_reflection = await _record_training_reflection(
        daemon,
        candidate_action,
        "ingested",
        {
            "competency_id": submission.competency_id,
            "source_type": submission.source_type,
            "source_detail": submission.source_detail,
            "recorded_by": recorded_by,
            "outcomes": [item.to_dict() for item in result.outcomes],
            "supersessions": supersessions,
        },
    )
    # WP-A2b: this Reflection is where supersession provenance lives
    # (design Sec.13.4) -- losing it silently loses the superseded claim's
    # history. The per-record outcomes above stand (those writes really
    # happened, and are not retried), but the submission must say the
    # provenance record did not persist.
    result.provenance_degraded = ingest_reflection.error is not None
    result.provenance_error = ingest_reflection.error

    return result


# ---------------------------------------------------------------------------
# Golden Path slice 2: Objective Continuity
# ---------------------------------------------------------------------------

_OBJECTIVE_TRANSITIONS = frozenset(
    {"open", "record", "surface", "block", "unblock", "complete", "abandon"},
)

_OBJECTIVE_OUTCOME_BY_TRANSITION = {
    "open": "opened",
    "record": "recorded",
    "surface": "surfaced",
    "block": "blocked",
    "unblock": "unblocked",
    "complete": "completed",
    "abandon": "abandoned",
}


@dataclass(frozen=True)
class ObjectiveAdmission:
    """Whether one objective transition is admitted, and why not if not.

    `outcome` is the value a refused transition reports
    ("parking_brake_denied" or "governance_denied"), so the caller never has
    to reconstruct it.
    """

    allowed: bool
    outcome: str | None = None
    reason: str | None = None


async def evaluate_objective_admission(ctx: Any, kind: str) -> ObjectiveAdmission:
    """The single Governance authority for every objective mutation.

    Both gates, in one place, so there is exactly one implementation of the
    decision. `run_objective_through_runtime_contract()` calls this
    immediately before it writes, and the re-engagement drive calls it before
    it persists anything of its own -- one authority consulted twice, never
    two implementations that can drift apart.

    **Gate 1: the Parking Brake, engaged AT ALL -- not scoped to `skills`.**
    This is `DECISIONS.md`'s "Parking Brake means inspect, but do not mutate"
    (2026-08-18), clause (d): the gate is the engaged flag itself, not any one
    subsystem scope, so a brake engaged for `voice` alone still refuses an
    objective mutation. An objective is durable user state in the same sense
    the user's memory is -- it belongs to none of the existing subsystem
    scopes (`skills`, `sight`, `voice`, `scheduler`, `training`), so gating it
    on any single one would be arbitrary, which is exactly the reasoning
    `MemoryStore._refuse_mutation_if_braked()` already records for memory.
    This uses the same helper that check uses,
    `engaged_state_fail_closed_off_loop()`, rather than a second one.

    Reading objectives stays allowed under a halt, because seeing what
    Bartholomew is carrying is inspection, and clause (b)'s reasoning applies:
    a halt that hides what the system was about to do defeats the purpose of
    halting. `_live_objectives()` and the Interpretation block are therefore
    deliberately not gated here.

    **Gate 2: Identity Context -> Policy Decision**, per transition kind, so
    permission to record an objective is not permission to close one. Skipped
    entirely when no IdentityContext is wired in -- additive, no new failure
    mode for callers that don't opt in.

    Fails closed: an unreadable governance state refuses the mutation. An
    unreadable safety gate must never wave a write through.
    """
    try:
        from bartholomew.orchestrator.safety.governance_store import (
            engaged_state_fail_closed_off_loop,
        )

        state = await engaged_state_fail_closed_off_loop(
            ctx.mem.db_path,
            governance_store=getattr(ctx, "governance_store", None),
            executor=getattr(ctx, "blocking_executor", None),
        )
        if state.engaged:
            scopes = ", ".join(sorted(state.scopes)) or "global"
            return ObjectiveAdmission(
                False,
                "parking_brake_denied",
                f"Blocked by parking brake (engaged; scopes={scopes})",
            )
    except Exception:
        logger.exception("Governance check failed for %s; failing closed", kind)
        return ObjectiveAdmission(False, "parking_brake_denied", "Governance check errored")

    identity_context = getattr(ctx, "identity_context", None)
    if identity_context is not None:
        decision = policy_engine.evaluate_tool_policy(identity_context, kind)
        if not decision.allowed:
            return ObjectiveAdmission(
                False,
                "governance_denied",
                f"Denied by Identity policy: {decision.reason}",
            )

    return ObjectiveAdmission(True)


@dataclass
class ObjectiveRuntimeResult:
    """Outcome of one objective transition through the Runtime Contract.

    `outcome` is one of the values in `_OBJECTIVE_OUTCOME_BY_TRANSITION`, or
    "parking_brake_denied", "governance_denied", "error"."""

    observation: Observation
    candidate_action: CandidateAction
    governance_allowed: bool
    outcome: str
    reason: str | None
    objective: Any = None
    event: Any = None


async def _record_objective_reflection(
    ctx: Any,
    candidate_action: CandidateAction,
    outcome: str,
    objective: Any,
    reason: str | None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Exactly one ActionReflection per objective transition, into the same
    shared Memory sink every other surface writes to.

    Best-effort in the same sense `_record_awaiting_response_reflection` is:
    `record_action_reflection` swallows and logs its own failure, so a lost
    Reflection never destabilises the transition that produced it. The
    objective's own `objective_events` row is the queryable per-objective
    detail view; this is the cross-surface stream.
    """
    details: dict[str, Any] = dict(extra or {})
    if reason:
        details["reason"] = reason
    if objective is not None:
        details["objective"] = {
            "id": getattr(objective, "id", None),
            "title": getattr(objective, "title", None),
            "status": getattr(objective, "status", None),
        }
    reflection = ActionReflection(
        surface="objective",
        action=candidate_action.kind,
        outcome=outcome,
        summary=f"Objective ({candidate_action.kind}): {outcome}",
        details=details,
    )
    await record_action_reflection(getattr(ctx, "mem", None), reflection)


async def _execute_objective_transition(
    ctx: Any,
    store: Any,
    transition: str,
    *,
    objective_id: int | None,
    title: str | None,
    outcome_statement: str | None,
    horizon_kind: str,
    horizon_date: str | None,
    event_kind: str | None,
    summary: str | None,
    provenance: dict[str, Any] | None,
    reason: str | None,
    resolution: str | None,
    outcome_note: str | None,
    actor: str | None,
) -> tuple[Any, Any]:
    """Run the store call for one transition, off the event loop.

    Returns (objective, event) -- `event` is populated only by "record",
    whose product is the event rather than a status change.
    """
    executor = getattr(ctx, "blocking_executor", None)

    if transition == "open":
        objective = await run_off_loop(
            functools.partial(
                store.open,
                title=title,
                outcome_statement=outcome_statement,
                horizon_kind=horizon_kind,
                horizon_date=horizon_date,
                actor=actor,
            ),
            executor=executor,
        )
        return objective, None

    if transition == "record":
        event = await run_off_loop(
            functools.partial(
                store.record,
                objective_id,
                event_kind=event_kind,
                summary=summary,
                provenance=provenance,
                actor=actor,
            ),
            executor=executor,
        )
        objective = await run_off_loop(store.get, objective_id, executor=executor)
        return objective, event

    if transition == "surface":
        objective = await run_off_loop(
            functools.partial(store.surface, objective_id, actor=actor),
            executor=executor,
        )
        return objective, None

    if transition == "block":
        objective = await run_off_loop(
            functools.partial(store.block, objective_id, reason=reason or "", actor=actor),
            executor=executor,
        )
        return objective, None

    if transition == "unblock":
        objective = await run_off_loop(
            functools.partial(store.unblock, objective_id, actor=actor),
            executor=executor,
        )
        return objective, None

    if transition == "complete":
        objective = await run_off_loop(
            functools.partial(
                store.complete,
                objective_id,
                resolution=resolution or objective_store.RESOLUTION_ACHIEVED,
                outcome_note=outcome_note,
                actor=actor,
            ),
            executor=executor,
        )
        return objective, None

    # abandon -- the only remaining member of _OBJECTIVE_TRANSITIONS.
    objective = await run_off_loop(
        functools.partial(
            store.abandon,
            objective_id,
            outcome_note=outcome_note,
            actor=actor,
        ),
        executor=executor,
    )
    return objective, None


async def run_objective_through_runtime_contract(
    ctx: Any,
    transition: str,
    *,
    objective_id: int | None = None,
    title: str | None = None,
    outcome_statement: str | None = None,
    horizon_kind: str = objective_store.HORIZON_OPEN,
    horizon_date: str | None = None,
    event_kind: str | None = None,
    summary: str | None = None,
    provenance: dict[str, Any] | None = None,
    reason: str | None = None,
    resolution: str | None = None,
    outcome_note: str | None = None,
    actor: str | None = None,
) -> ObjectiveRuntimeResult:
    """
    Trace one objective transition through the Runtime Contract seam.

    `transition` is one of "open" (requires title), "record" (requires
    objective_id, event_kind and summary), or "surface"/"block"/"unblock"/
    "complete"/"abandon" (all require objective_id).

    `ctx` needs `.mem.db_path` and `.objective_store`; `.governance_store`,
    `.blocking_executor` and `.identity_context` are consulted via getattr
    with the same additive fallbacks every other seam function here uses.

    **An objective existing authorises nothing.** This function governs the
    recording of what the user wants and what has happened around it -- and
    nothing else. It sends no message, contacts nobody, spends nothing and
    reaches no external provider. Any action that might advance an
    objective is a separate governed action with its own gates; remembering
    "get the roof repaired" does not become permission to email a roofer.
    That separation is the whole reason this seam writes to a store rather
    than dispatching anything.

    Governance is two independent gates, both before any store write, in the
    shape `run_awaiting_response_through_runtime_contract` established:

      1. ParkingBrake("skills"), fail-closed. An engaged brake produces zero
         writes -- the objective record is governed state, and a braked
         Bartholomew does not quietly keep bookkeeping.
      2. Identity Context -> Policy Decision against the kind
         "objective_<transition>". Deliberately NOT exempted the way
         `_SELF_MAINTENANCE_DRIVES` scheduler drives are: an objective is
         specific user content, not kernel housekeeping. Skipped entirely
         when no IdentityContext is wired in -- additive, no new failure
         mode for callers that don't opt in.

    A caller-input error (unknown objective_id, or any transition against an
    objective already completed or abandoned) raises ObjectiveNotFoundError/
    InvalidTransitionError directly. These are the caller's mistake, not a
    governance denial, so they propagate rather than being folded into
    `outcome` -- and in particular an attempt to touch a finished objective
    is loud, never silently absorbed.
    """
    from .objective_store import InvalidTransitionError, ObjectiveNotFoundError

    if transition not in _OBJECTIVE_TRANSITIONS:
        raise ValueError(
            f"transition must be one of {sorted(_OBJECTIVE_TRANSITIONS)}, got {transition!r}",
        )
    # Malformed-call validation, before any Observation/Governance/store
    # work -- same posture as the awaiting_response seam: a missing required
    # argument is a programming mistake, not a governed event.
    if transition == "open":
        if not title:
            raise ValueError("objective 'open' requires a title")
    elif objective_id is None:
        raise ValueError(f"objective {transition!r} requires objective_id")
    if transition == "record" and (not event_kind or not summary):
        raise ValueError("objective 'record' requires event_kind and summary")

    store = ctx.objective_store
    kind = f"objective_{transition}"
    executor = getattr(ctx, "blocking_executor", None)

    existing = None
    if objective_id is not None:
        existing = await run_off_loop(store.get, objective_id, executor=executor)
    prompt_subject = title or (getattr(existing, "title", None) or "objective")

    observation = Observation(
        source="objective",
        raw_content=f"{transition}:{objective_id if objective_id is not None else 'new'}",
    )
    interpretation = Interpretation(observation=observation, prompt=prompt_subject)
    candidate_action = CandidateAction(kind=kind, interpretation=interpretation)

    admission = await evaluate_objective_admission(ctx, kind)
    governance_allowed = admission.allowed
    outcome = admission.outcome or "governance_denied"
    denial_reason = admission.reason

    objective = existing
    event = None
    if governance_allowed:
        try:
            objective, event = await _execute_objective_transition(
                ctx,
                store,
                transition,
                objective_id=objective_id,
                title=title,
                outcome_statement=outcome_statement,
                horizon_kind=horizon_kind,
                horizon_date=horizon_date,
                event_kind=event_kind,
                summary=summary,
                provenance=provenance,
                reason=reason,
                resolution=resolution,
                outcome_note=outcome_note,
                actor=actor,
            )
        except (ObjectiveNotFoundError, InvalidTransitionError) as exc:
            await _record_objective_reflection(
                ctx,
                candidate_action,
                "rejected",
                existing,
                str(exc),
            )
            raise
        except Exception as exc:
            logger.exception("objective transition %s failed", kind)
            governance_allowed = False
            outcome = "error"
            denial_reason = str(exc)
        else:
            outcome = _OBJECTIVE_OUTCOME_BY_TRANSITION[transition]

    # Reflection -- for every outcome EXCEPT a brake refusal.
    #
    # `record_action_reflection()` writes to the `reflections` table through
    # `MemoryStore.insert_reflection()`, so writing one here while the brake
    # is engaged would be a memory mutation during a halt -- precisely what
    # `MemoryStore._refuse_mutation_if_braked()` refuses for every other
    # memory write, and what "inspect, but do not mutate" forbids. A halted
    # system does no bookkeeping about work it declined to do.
    #
    # Nothing is hidden by this. The refusal is returned to the caller with
    # its reason, and the brake's own engagement is already recorded in the
    # GovernanceStore audit -- which clause (b) of that decision keeps
    # exempt precisely so the halt stays inspectable. What is *not* recorded
    # is a new row in the user's memory, because a refused transition did no
    # work worth recording.
    #
    # An Identity-policy denial is different and still writes: that is an
    # ordinary governed decision, not a halt, and it is the same posture
    # chat and the awaiting_response seam already take for their denials.
    if outcome != "parking_brake_denied":
        await _record_objective_reflection(
            ctx,
            candidate_action,
            outcome,
            objective,
            denial_reason,
            {"event_kind": event_kind} if event_kind else None,
        )

    return ObjectiveRuntimeResult(
        observation=observation,
        candidate_action=candidate_action,
        governance_allowed=governance_allowed,
        outcome=outcome,
        reason=denial_reason,
        objective=objective,
        event=event,
    )


# =============================================================================
# Inbound capture (Session D)
# =============================================================================
#
# The governed seam for events that arrive from outside, rather than from
# Taylor typing, a device, or a scheduler drive. Same shape as every other
# surface in this module: Observation -> Interpretation -> Executive ->
# Governance -> Capability/Execution -> Reflection.
#
# Two things this seam deliberately is NOT:
#
#   * It is not an Executive. Capture records that something arrived; it never
#     decides what the event means, whether it is true, or what Bartholomew
#     should do about it. Nothing here writes Memory, creates an objective,
#     invokes a skill, or schedules work.
#   * It is not domain-aware. `event_type` is an opaque string that is stored
#     and never branched on. There is no `if email`, no `if calendar`. Future
#     provider adapters translate their payloads into this envelope; the
#     ingress path stays blind to what they mean.
#
# Parking Brake semantics here follow the canonical rule -- "inspect, but do
# not mutate" (DECISIONS.md, Governance decision 2026-08-18). A braked inbound
# request therefore writes NOTHING AT ALL: no `inbound_events` row, no
# Reflection. Recording a "received and refused" row would itself be a
# governed-state mutation performed while the user has halted mutation, which
# is exactly the side door a brake exists to close. The caller gets an honest,
# retryable refusal (`ParkingBrakeEngagedError` -> 503) and nothing is
# acknowledged as captured, so the sender's own retry re-delivers the event
# once the brake is released.

#: The single CandidateAction kind for inbound capture. One kind, not one per
#: provider -- the Executive-facing surface must not grow a taxonomy of
#: domains through the back door of governance kinds.
INBOUND_CAPTURE_KIND = "inbound_capture"


@dataclass
class InboundRuntimeResult:
    """Outcome of one inbound event through the Runtime Contract.

    `captured` is the only field that means the event is durably recorded.
    `duplicate` means this (source_id, event_id) was already captured and the
    stored row is being reported again -- a retry from an external system,
    not a second logical event.
    """

    observation: Observation
    candidate_action: CandidateAction
    governance_allowed: bool
    captured: bool
    duplicate: bool
    outcome: str
    reason: str | None
    stored: Any  # StoredInboundEvent | None

    #: True when the Reflection accompanying a capture failed to persist. The
    #: capture itself is unaffected and is never re-run for it -- the row is
    #: the durable record and it exists -- but the event must not present as
    #: fully recorded. Same posture as the device seams (WP-A2b).
    provenance_degraded: bool = False
    provenance_error: str | None = None


async def _record_inbound_reflection(
    db_path: str | None,
    event_type: str,
    source_id: str,
    event_id: str,
    outcome: str,
    reason: str | None,
    payload_sha256: str | None,
) -> ReflectionWriteOutcome:
    """One ActionReflection per *captured* inbound event.

    Only reached after a successful capture. Denials under the Parking Brake
    write nothing (see this section's header); a persistence failure has
    nothing to reflect on, because nothing was captured.

    The payload itself is never put in the Reflection -- the digest is, so the
    audit trail can prove *which* content was accepted without copying
    third-party data into a second store.
    """
    mem = None
    if db_path:
        try:
            from .memory_store import MemoryStore

            mem = MemoryStore(db_path)
        except Exception as exc:
            logger.exception("Failed to construct MemoryStore for inbound reflection")
            return ReflectionWriteOutcome(
                error=f"reflection write failed (inbound): store construction failed: {exc}",
            )
    reflection = ActionReflection(
        surface="inbound",
        action=INBOUND_CAPTURE_KIND,
        outcome=outcome,
        summary=f"Inbound event captured from {source_id} ({event_type})",
        details={
            "source_id": source_id,
            "event_id": event_id,
            "event_type": event_type,
            "payload_sha256": payload_sha256 or "",
            **({"reason": reason} if reason else {}),
        },
    )
    return await record_action_reflection(mem, reflection)


async def run_inbound_through_runtime_contract(
    *,
    db_path: str,
    source_id: str,
    event_id: str,
    event_type: str,
    payload: Any,
    verified_by: str,
    occurred_at: str | None = None,
    runtime_id: str | None = None,
    identity_context: IdentityContext | None = None,
) -> InboundRuntimeResult:
    """Trace one inbound event through the Runtime Contract seam.

    The governed production entry point for the inbound surface. Everything
    upstream of this -- transport, principal verification, payload shape
    validation -- belongs to the API boundary; everything downstream of
    capture belongs to whoever later decides what a captured event means.

    `verified_by` is the identity of *what verified this event's source*,
    supplied by the authenticated control plane. This seam does not
    authenticate anything and must never be called with an unverified caller:
    the API boundary fails closed before reaching here.

    Order matters and is not negotiable:

      1. Parking Brake, read through `GovernanceStore` -- the same authority
         chat, scheduler drives, skill execution and the device seams read,
         and fail-closed on an unreadable gate. Gated on the brake being
         *engaged at all* rather than on a subsystem scope, matching the
         existing memory-mutation gate: capture mutates governed state and
         belongs to none of the existing subsystem scopes.
      2. Identity Policy Decision (additive; skipped when no IdentityContext
         is wired in, matching every other surface).
      3. Capture -- reached only if both gates allowed.

    Raises `ParkingBrakeEngagedError` when the brake is engaged, so the caller
    reports an honest retryable refusal, and `InboundPersistenceError` when
    the write fails. Neither is a success and neither may be reported as one.
    """
    from bartholomew.kernel.inbound_store import (
        OUTCOME_CAPTURED,
        capture_event,
        ensure_schema,
    )
    from bartholomew.orchestrator.safety.governance_store import (
        ParkingBrakeEngagedError,
        engaged_state_fail_closed_off_loop,
    )

    observation = Observation(source=f"inbound:{source_id}", raw_content=event_type)
    interpretation = Interpretation(observation=observation, prompt=event_type)
    candidate_action = CandidateAction(kind=INBOUND_CAPTURE_KIND, interpretation=interpretation)

    # Governance gate 1: the Parking Brake. Before anything is written, and
    # fail-closed.
    #
    # Gated on the brake being engaged *at all* rather than on a subsystem
    # scope, matching the existing memory-mutation gate: capture mutates
    # governed state and belongs to none of the existing scopes.
    #
    # **Both authority tiers are composed here, and this call is how.**
    # `engaged_state_fail_closed` consults the higher-scope check that S8's
    # `install_platform_halt_hook()` registers, so a Platform/Admin halt stops
    # capture through the same read as the Personal brake. This seam
    # deliberately does NOT call `authority.is_blocked()` itself: composition
    # is Governance's job at its own composition point, and doing it again
    # here was both redundant and wrong -- it bypassed the platform tier's
    # `platform_tier_active()` inertness, so a deployment with no control
    # plane (the single-user loopback install, and any test with only a kernel
    # database) read an uninitialised platform store, failed closed, and
    # refused every inbound event with a halt message that was not true.
    #
    # `engaged_state_fail_closed_off_loop` propagates its own read failures,
    # which is the fail-closed behaviour: the exception aborts the caller
    # before capture.
    state = await engaged_state_fail_closed_off_loop(db_path)
    if state.engaged:
        raise ParkingBrakeEngagedError(
            "A parking brake or platform halt is engaged: inbound events are "
            "not being captured. Nothing was recorded, and the sender may "
            "retry once it is released.",
            scopes=state.scopes,
        )

    # Schema before either write path. The Identity-policy branch below
    # records its refusal into the same table a capture uses, so preparing it
    # only on the success path left the denial path writing to a table that
    # did not exist yet.
    #
    # Schema preparation is part of persisting, so a failure here is an
    # inbound persistence failure like any other -- reported as one rather
    # than leaking a raw sqlite3 error the caller would have to guess at.
    try:
        await run_off_loop(ensure_schema, db_path)
    except Exception as e:
        from bartholomew.kernel.inbound_store import InboundPersistenceError

        raise InboundPersistenceError(
            f"Inbound event {source_id}/{event_id} was NOT persisted: "
            f"inbound schema unavailable: {type(e).__name__}: {e}",
        ) from e

    # Governance gate 2: Identity Policy Decision (additive; see docstring).
    if identity_context is not None:
        decision = policy_engine.evaluate_tool_policy(identity_context, candidate_action.kind)
        if not decision.allowed:
            # A policy denial is not a brake halt: mutation is not forbidden,
            # this particular action is. Recording the refusal is therefore
            # both permitted and useful -- but it stays a refusal, never a
            # capture, and `captured` is False.
            reason = f"Denied by Identity policy: {decision.reason}"
            stored = await run_off_loop(
                capture_event,
                db_path,
                source_id=source_id,
                event_id=event_id,
                event_type=event_type,
                occurred_at=occurred_at,
                payload=payload,
                outcome="governance_denied",
                governance_reason=reason,
                verified_by=verified_by,
                runtime_id=runtime_id,
            )
            return InboundRuntimeResult(
                observation=observation,
                candidate_action=candidate_action,
                governance_allowed=False,
                captured=False,
                duplicate=stored.duplicate,
                outcome="governance_denied",
                reason=reason,
                stored=stored,
            )

    # Capability + Execution: durable capture, and nothing else.
    stored = await run_off_loop(
        capture_event,
        db_path,
        source_id=source_id,
        event_id=event_id,
        event_type=event_type,
        occurred_at=occurred_at,
        payload=payload,
        outcome=OUTCOME_CAPTURED,
        governance_reason=None,
        verified_by=verified_by,
        runtime_id=runtime_id,
    )

    if stored.duplicate:
        # A retry. The logical event already exists and is unchanged; writing
        # a second Reflection for it would inflate the audit trail with
        # repeat deliveries that produced no new capture.
        return InboundRuntimeResult(
            observation=observation,
            candidate_action=candidate_action,
            governance_allowed=True,
            captured=stored.outcome == OUTCOME_CAPTURED,
            duplicate=True,
            outcome=stored.outcome,
            reason=stored.governance_reason,
            stored=stored,
        )

    reflection = await _record_inbound_reflection(
        db_path,
        event_type,
        source_id,
        event_id,
        OUTCOME_CAPTURED,
        None,
        stored.payload_sha256,
    )

    return InboundRuntimeResult(
        observation=observation,
        candidate_action=candidate_action,
        governance_allowed=True,
        captured=True,
        duplicate=False,
        outcome=OUTCOME_CAPTURED,
        reason=None,
        stored=stored,
        provenance_degraded=reflection.error is not None,
        provenance_error=reflection.error,
    )


# =============================================================================
# Stage 5 S5.4 (narrow slice): experience -> candidate learning -> review ->
# consolidation.
#
# One governed loop, and only one: a recorded *objective outcome* produces one
# bounded procedural candidate lesson, which a human reviewer accepts or
# rejects, and which -- only if accepted -- is consolidated into the existing
# S5.1 competency substrate through S5.2's existing governed write, where
# S5.3's existing retrieval seam can find it.
#
# What this section deliberately is not: an autonomous learner. Nothing here
# runs on a tick, a drive, or a schedule. Every function below is called by
# something that already had a reason to act, and the accept branch requires a
# named reviewer. `candidate_learning.CandidateLesson.requires_review` is an
# unconditional property, so there is no "high-confidence, low-impact"
# auto-consolidation branch to fall through -- see its docstring for why this
# slice omits the one `COGNITIVE_RUNTIME.md` describes.
#
# Rejection is real, structurally: consolidation happens in exactly one place
# (`_consolidate_accepted_lesson`), it is reachable only from the accept
# branch, and `to_competency_heuristic()` raises for any state but `accepted`.
# A rejected candidate therefore leaves behind only its own audit row, under
# the `candidate_lesson` kind, which is absent from `COMPETENCY_KINDS` and so
# structurally invisible to `_retrieve_memory_context()`'s kind filter.
# =============================================================================

#: Observation source for the learning surface.
LEARNING_OBSERVATION_SOURCE = "learning"

#: Brake scope. Reused from training rather than invented, because these
#: writes *are* training-shaped writes into the same substrate: anyone who has
#: halted training has halted Bartholomew learning from its own experience
#: too, which is the stricter and more obviously correct reading.
LEARNING_BRAKE_SCOPE = training.TRAINING_BRAKE_SCOPE

LEARNING_ACTION_PROPOSE = "learning_propose"
LEARNING_ACTION_ACCEPT = "learning_accept"
LEARNING_ACTION_REJECT = "learning_reject"

_LEARNING_ACTIONS = frozenset(
    {LEARNING_ACTION_PROPOSE, LEARNING_ACTION_ACCEPT, LEARNING_ACTION_REJECT},
)

LEARNING_OUTCOME_PROPOSED = "proposed"
LEARNING_OUTCOME_ACCEPTED = "accepted"
LEARNING_OUTCOME_REJECTED = "rejected"
LEARNING_OUTCOME_INVALID = "invalid"
LEARNING_OUTCOME_NOT_FOUND = "not_found"
LEARNING_OUTCOME_NO_EXPERIENCE = "no_experience"
LEARNING_OUTCOME_NOT_STORED = "not_stored"
LEARNING_OUTCOME_BRAKE_DENIED = "parking_brake_denied"
LEARNING_OUTCOME_GOVERNANCE_DENIED = "governance_denied"
#: Acceptance was refused because no valid, candidate-bound authorization
#: exists. Deliberately distinct from `governance_denied`: "nobody has
#: approved consolidating *this* lesson" is a different fact from "this
#: deployment does not permit learning actions at all", and an operator
#: reading an audit trail needs to be able to tell them apart.
LEARNING_OUTCOME_APPROVAL_REQUIRED = "acceptance_approval_required"

#: The Reflection action kind recorded when an approval is granted. Not a
#: member of `_LEARNING_ACTIONS`: granting authorization is a human act about
#: the loop, not a step of the loop.
LEARNING_ACTION_APPROVE_ACCEPTANCE = "learning_accept_approval_grant"


@dataclass
class CandidateLessonResult:
    """Outcome of one candidate-learning action through the Runtime Contract.

    `lesson` is the candidate as it stands after the action. `consolidation`
    is the `TrainingRuntimeResult` of the accepted lesson's write into the
    competency substrate, and is None for every outcome except a successful
    accept -- which is the result contract's way of saying that a rejection
    consolidated nothing.
    """

    observation: Observation
    candidate_action: CandidateAction
    governance_allowed: bool
    outcome: str
    reason: str | None = None
    lesson: Any = None
    consolidation: training.TrainingRuntimeResult | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def consolidated(self) -> bool:
        """Whether a retrievable competency record now exists for this lesson."""
        return bool(
            self.consolidation is not None and self.consolidation.stored_count > 0,
        )


async def evaluate_learning_admission(
    ctx: Any,
    kind: str,
    *,
    lesson: Any = None,
) -> ObjectiveAdmission:
    """The single Governance authority for every candidate-learning action.

    Deliberately the same `ObjectiveAdmission` result type as
    `evaluate_objective_admission()`, rather than a third variant.

    Gate 1, for every kind: the fail-closed Parking Brake on the `training`
    scope (the same helper the training seam uses). Nothing below can reach
    past it -- an explicit acceptance approval is *not* a brake override, and
    a broader deny stays authoritative.

    Gate 2 depends on the kind, and this is the governance decision itself:

      * `learning_propose` / `learning_reject` -- Identity policy, i.e. the
        `tool_use.allowlist`, exactly like every other seam kind. A standing
        grant is the right shape here: proposing creates a candidate that
        nothing can reason from, and rejecting is conservative by
        construction.
      * `learning_accept` -- a valid `LearningAcceptanceApproval` bound to
        *this exact candidate*, found in Memory by the caller and passed in as
        `lesson`. The Identity allowlist is neither consulted nor sufficient
        here: acceptance is the durable mutation that makes a lesson
        retrievable, so allowlisting `learning_accept` would be a standing
        "learning enabled" switch, which is precisely what this design
        refuses. Bartholomew may conclude it *may* have learned something; it
        may not conclude on its own that the lesson is now trusted.

    Fails closed: an unreadable governance state refuses the action, and a
    missing or mismatched approval refuses acceptance.
    """
    brake = await _evaluate_learning_brake(ctx, kind)
    if not brake.allowed:
        return brake

    if kind == LEARNING_ACTION_ACCEPT:
        approval = await _load_learning_approval(ctx, lesson)
        if approval is None:
            return ObjectiveAdmission(
                False,
                LEARNING_OUTCOME_APPROVAL_REQUIRED,
                (
                    "consolidating a candidate lesson requires explicit authorization "
                    "bound to that candidate; none is recorded"
                ),
            )
        allowed, reason = approval.authorizes(lesson)
        if not allowed:
            return ObjectiveAdmission(False, LEARNING_OUTCOME_APPROVAL_REQUIRED, reason)
        return ObjectiveAdmission(True)

    identity_context = getattr(ctx, "identity_context", None)
    if identity_context is not None:
        decision = policy_engine.evaluate_tool_policy(identity_context, kind)
        if not decision.allowed:
            return ObjectiveAdmission(
                False,
                LEARNING_OUTCOME_GOVERNANCE_DENIED,
                f"Denied by Identity policy: {decision.reason}",
            )

    return ObjectiveAdmission(True)


async def _evaluate_learning_brake(ctx: Any, kind: str) -> ObjectiveAdmission:
    """Gate 1 alone: the fail-closed Parking Brake on the `training` scope.

    Extracted so the accept branch can refuse under a brake *before* it reads
    a candidate, and still run the complete admission (this check included)
    immediately before the mutation itself. Nothing downstream can override
    it: an acceptance approval authorises consolidating one lesson, never
    acting while a broader deny is in force.
    """
    try:
        from bartholomew.orchestrator.safety.governance_store import (
            is_blocked_fail_closed_off_loop,
        )

        blocked = await is_blocked_fail_closed_off_loop(
            LEARNING_BRAKE_SCOPE,
            ctx.mem.db_path,
            governance_store=getattr(ctx, "governance_store", None),
            executor=getattr(ctx, "blocking_executor", None),
        )
        if blocked:
            return ObjectiveAdmission(
                False,
                LEARNING_OUTCOME_BRAKE_DENIED,
                f"parking brake engaged for scope {LEARNING_BRAKE_SCOPE!r}",
            )
    except Exception:
        logger.exception("Governance check failed for %s; failing closed", kind)
        return ObjectiveAdmission(
            False,
            LEARNING_OUTCOME_BRAKE_DENIED,
            "Governance check errored",
        )

    return ObjectiveAdmission(True)


async def _record_learning_reflection(
    ctx: Any,
    candidate_action: CandidateAction,
    outcome: str,
    lesson: Any,
    reason: str | None = None,
    extra: dict[str, Any] | None = None,
) -> ReflectionWriteOutcome:
    """Exactly one ActionReflection per candidate-learning action, into the
    same shared Memory sink every other surface writes to.

    The Reflection is the cross-surface audit trail of *what was decided about
    a lesson*, distinct from the candidate row itself, which is the lesson's
    current state. A review decision that left no trace beyond a mutated row
    would make "was this ever rejected?" unanswerable.
    """
    details: dict[str, Any] = dict(extra or {})
    if reason:
        details["reason"] = reason
    if lesson is not None:
        details["lesson"] = {
            "competency_id": lesson.competency_id,
            "key": lesson.key(),
            "epistemic_status": lesson.epistemic_status,
            "classification": lesson.classification,
            "confidence": lesson.confidence,
            "review_state": lesson.review_state,
            "objective_id": lesson.source.objective_id,
            "supporting_event_ids": list(lesson.source.supporting_event_ids),
        }
    reflection = ActionReflection(
        surface=LEARNING_OBSERVATION_SOURCE,
        action=candidate_action.kind,
        outcome=outcome,
        summary=f"Candidate learning ({candidate_action.kind}): {outcome}",
        details=details,
    )
    return await record_action_reflection(getattr(ctx, "mem", None), reflection)


async def _write_candidate_lesson(ctx: Any, lesson: Any) -> Any:
    """Persist the candidate's current state through `MemoryStore`.

    `upsert_memory()` remains the sole write authority, exactly as it is for
    competency records. The candidate is stored under
    `candidate_learning.KIND`, which the retrieval seam's kind filter does not
    include -- so this write makes the candidate durable and reviewable
    without making it reasoning material.
    """
    import json as _json

    return await ctx.mem.upsert_memory(
        candidate_learning.KIND,
        lesson.key(),
        _json.dumps(lesson.to_dict()),
        datetime.now(timezone.utc).isoformat(),
        summary=lesson.to_summary_text(),
    )


async def _load_candidate_lesson(ctx: Any, competency_id: str, slug: str) -> Any:
    """Read one candidate back, or None.

    `get_memory()` is the by-key read the training seam already uses for
    supersession. It is deliberately *not* the consent-gated retrieval path,
    and this is not a surfacing read: it feeds a review decision about a
    record the reviewer named, never a prompt or a model context.
    """
    import json as _json

    key = candidate_learning.key_for(competency_id, slug)
    try:
        row = await ctx.mem.get_memory(candidate_learning.KIND, key)
    except Exception:
        logger.exception("Failed to read candidate lesson %s", key)
        return None
    if not row:
        return None
    try:
        return candidate_learning.CandidateLesson.from_dict(_json.loads(row["value"]))
    except (TypeError, ValueError, KeyError):
        logger.warning("Stored candidate lesson %s is not parseable", key)
        return None


async def _load_learning_approval(ctx: Any, lesson: Any) -> Any:
    """The recorded acceptance authorization for `lesson`, or None.

    Keyed by the candidate's own key, so there is exactly one live approval
    per candidate and no ambient "learning is approved" state to find. Reads
    fail closed: an unreadable or unparseable approval row is no approval.
    """
    import json as _json

    if lesson is None:
        return None
    key = candidate_learning.key_for(lesson.competency_id, lesson.slug)
    try:
        row = await ctx.mem.get_memory(learning_authorization.KIND, key)
    except Exception:
        logger.exception("Failed to read learning acceptance approval %s", key)
        return None
    if not row:
        return None
    try:
        return learning_authorization.LearningAcceptanceApproval.from_dict(
            _json.loads(row["value"]),
        )
    except (TypeError, ValueError, KeyError):
        logger.warning("Stored learning acceptance approval %s is not parseable", key)
        return None


@dataclass
class LearningApprovalResult:
    """Outcome of one attempt to authorise accepting a specific candidate."""

    granted: bool
    outcome: str
    reason: str | None = None
    approval: Any = None


async def grant_learning_acceptance_approval(
    ctx: Any,
    *,
    competency_id: str,
    slug: str,
    approver: str,
    note: str | None = None,
) -> LearningApprovalResult:
    """Explicitly authorise consolidating one named candidate lesson.

    This is the *only* way `learning_accept` becomes reachable, and it is
    deliberately per-candidate: the approval records who approved it, when,
    and a fingerprint of the candidate's material content, so it authorises
    that lesson and nothing else. Re-proposing over the same key changes the
    fingerprint and silently invalidates the approval -- acceptance then fails
    until someone approves what the candidate now says.

    Called by a human review flow, never by Bartholomew's own seams. It grants
    no standing permission, so calling it twice for two candidates is two
    decisions, not a mode. It is also inert on its own: it writes an approval
    row and an audit Reflection, and consolidates nothing -- every other gate
    (Parking Brake first among them) is still evaluated when acceptance
    actually runs.
    """
    import json as _json

    if not competency_id or not slug:
        return LearningApprovalResult(
            False,
            LEARNING_OUTCOME_INVALID,
            "competency_id and slug are required to approve a candidate lesson",
        )
    if not approver:
        return LearningApprovalResult(
            False,
            LEARNING_OUTCOME_INVALID,
            "an approver is required -- authorization is never anonymous",
        )

    lesson = await _load_candidate_lesson(ctx, competency_id, slug)
    if lesson is None:
        return LearningApprovalResult(
            False,
            LEARNING_OUTCOME_NOT_FOUND,
            f"no candidate lesson {candidate_learning.key_for(competency_id, slug)!r}",
        )
    if lesson.review_state != candidate_learning.REVIEW_PROPOSED:
        return LearningApprovalResult(
            False,
            LEARNING_OUTCOME_INVALID,
            f"cannot approve a candidate in state {lesson.review_state!r}; "
            "review decisions are terminal",
        )

    approval = learning_authorization.LearningAcceptanceApproval(
        competency_id=competency_id,
        slug=slug,
        candidate_fingerprint=learning_authorization.fingerprint_for(lesson),
        approver=approver,
        note=note,
        objective_id=lesson.source.objective_id,
        candidate_revision=lesson.revision,
    )
    errors = approval.validate()
    if errors:
        return LearningApprovalResult(False, LEARNING_OUTCOME_INVALID, "; ".join(errors))

    store_result = await ctx.mem.upsert_memory(
        learning_authorization.KIND,
        approval.key(),
        _json.dumps(approval.to_dict()),
        datetime.now(timezone.utc).isoformat(),
        summary=approval.to_summary_text(),
    )
    if not getattr(store_result, "stored", False):
        return LearningApprovalResult(
            False,
            LEARNING_OUTCOME_NOT_STORED,
            "the acceptance approval was not stored (policy or consent)",
        )

    # Provenance through the existing cross-surface audit authority, so
    # "who authorised this, and when?" is answerable from the Reflection
    # trail alone -- the same place the acceptance itself is recorded.
    observation = Observation(
        source=LEARNING_OBSERVATION_SOURCE,
        raw_content=(
            f"{LEARNING_ACTION_APPROVE_ACCEPTANCE} candidate={approval.key()} "
            f"approver={approver}"
        ),
    )
    interpretation = _build_interpretation(ctx, observation)
    await _record_learning_reflection(
        ctx,
        CandidateAction(
            kind=LEARNING_ACTION_APPROVE_ACCEPTANCE,
            interpretation=interpretation,
        ),
        "approved",
        lesson,
        None,
        {
            "approver": approver,
            "approval_note": note,
            "candidate_fingerprint": approval.candidate_fingerprint,
            "candidate_revision": approval.candidate_revision,
            "granted_at": approval.granted_at,
            "consolidated": False,
        },
    )
    return LearningApprovalResult(True, "approved", None, approval)


async def _consolidate_accepted_lesson(
    ctx: Any,
    lesson: Any,
) -> training.TrainingRuntimeResult:
    """The only path by which a lesson becomes retrievable knowledge.

    Reuses S5.2's governed write verbatim -- same Observation, same
    Governance gate, same `MemoryStore.upsert_memory()`, same consent queue,
    same Reflection -- with the `experience` source type explicitly unlocked
    for this one call. No second write path, no second governance path, and
    no way to reach this function except from a candidate that a named
    reviewer has already accepted.
    """
    heuristic = lesson.to_competency_heuristic()
    submission = training.TrainingSubmission(
        competency_id=lesson.competency_id,
        source_type=candidate_learning.EXPERIENCE_SOURCE_TYPE,
        source_detail=(
            f"Lesson accepted by {lesson.reviewer} from objective "
            f"{lesson.source.objective_id}: {lesson.inferred_rule}"
        ),
        records=[heuristic],
    )
    return await run_training_through_runtime_contract(
        ctx,
        submission,
        recorded_by="reflection",
        allow_consolidation_source=True,
    )


async def run_candidate_lesson_through_runtime_contract(
    ctx: Any,
    action: str,
    *,
    objective_id: int | None = None,
    competency_id: str | None = None,
    slug: str | None = None,
    inferred_rule: str | None = None,
    conditions: str | None = None,
    classification: str = "personal",
    reviewer: str | None = None,
    review_note: str | None = None,
) -> CandidateLessonResult:
    """
    Trace one candidate-learning action through the Runtime Contract seam.

    `action` is one of:

      * ``"learning_propose"`` -- read a *terminal* objective and its evidence
        events, produce one bounded procedural candidate lesson, and store it
        as a proposal. Requires `objective_id` and `competency_id`.
      * ``"learning_accept"`` -- a named reviewer accepts a proposed lesson,
        which is then consolidated into the competency substrate. Requires
        `competency_id`, `slug` and `reviewer`.
      * ``"learning_reject"`` -- a named reviewer rejects it. Nothing is
        consolidated, then or ever.

    Stages, in order: Observation (source="learning") -> Interpretation ->
    CandidateAction -> Governance (fail-closed, before any write) -> Memory
    -> Reflection. Identical in shape to every other surface's seam.

    `ctx` needs `.mem`; `.objective_store`, `.governance_store`,
    `.blocking_executor` and `.identity_context` are consulted via getattr
    with the same additive fallbacks the objective seam uses.

    **Proposing a lesson asserts nothing.** A proposed candidate is not
    knowledge, is not retrievable, and changes no future reasoning. Only the
    accept branch has any effect on what Bartholomew can later recall, and it
    cannot run without a reviewer's name attached to the decision.
    """
    if action not in _LEARNING_ACTIONS:
        raise ValueError(f"unknown candidate-learning action {action!r}")

    observation = Observation(
        source=LEARNING_OBSERVATION_SOURCE,
        raw_content=(f"{action} objective={objective_id} competency={competency_id} slug={slug}"),
    )
    interpretation = _build_interpretation(ctx, observation)
    candidate_action = CandidateAction(kind=action, interpretation=interpretation)

    def _refuse(outcome: str, reason: str | None, *, errors: list[str] | None = None):
        return CandidateLessonResult(
            observation=observation,
            candidate_action=candidate_action,
            governance_allowed=True,
            outcome=outcome,
            reason=reason,
            errors=errors or [],
        )

    async def _refuse_by_governance(admission, lesson=None):
        result = CandidateLessonResult(
            observation=observation,
            candidate_action=candidate_action,
            governance_allowed=False,
            outcome=admission.outcome or LEARNING_OUTCOME_GOVERNANCE_DENIED,
            reason=admission.reason,
            lesson=lesson,
        )
        await _record_learning_reflection(
            ctx,
            candidate_action,
            result.outcome,
            lesson,
            admission.reason,
            {"consolidated": False},
        )
        return result

    # --- Governance gate 1: fail-closed brake, before anything is read ----
    brake = await _evaluate_learning_brake(ctx, action)
    if not brake.allowed:
        return await _refuse_by_governance(brake)

    if action == LEARNING_ACTION_PROPOSE:
        admission = await evaluate_learning_admission(ctx, action)
        if not admission.allowed:
            return await _refuse_by_governance(admission)
        return await _propose_candidate_lesson(
            ctx,
            observation,
            candidate_action,
            objective_id=objective_id,
            competency_id=competency_id,
            inferred_rule=inferred_rule,
            conditions=conditions,
            classification=classification,
        )

    # accept / reject -- both need an existing proposal and a named reviewer.
    if not competency_id or not slug:
        return _refuse(
            LEARNING_OUTCOME_INVALID,
            "competency_id and slug are required to review a candidate lesson",
        )
    if not reviewer:
        return _refuse(
            LEARNING_OUTCOME_INVALID,
            "a review decision requires a reviewer -- review is never anonymous",
        )

    lesson = await _load_candidate_lesson(ctx, competency_id, slug)
    if lesson is None:
        return _refuse(
            LEARNING_OUTCOME_NOT_FOUND,
            f"no candidate lesson {candidate_learning.key_for(competency_id, slug)!r}",
        )

    # --- Governance gate 2, on the candidate as it stands right now -------
    # Evaluated *before* the review transition mutates it, so an acceptance
    # approval is checked against exactly the content it was granted for.
    # For `learning_reject` this is the ordinary Identity allowlist check;
    # for `learning_accept` it is the candidate-bound authorization, which no
    # allowlist entry can stand in for.
    admission = await evaluate_learning_admission(ctx, action, lesson=lesson)
    if not admission.allowed:
        return await _refuse_by_governance(admission, lesson)

    try:
        if action == LEARNING_ACTION_ACCEPT:
            lesson.accept(reviewer=reviewer, note=review_note)
        else:
            lesson.reject(reviewer=reviewer, note=review_note)
    except candidate_learning.ReviewStateError as exc:
        return _refuse(LEARNING_OUTCOME_INVALID, str(exc))

    if action == LEARNING_ACTION_REJECT:
        # Persist the rejection, and stop. Nothing is consolidated here, and
        # `accept()` can no longer be reached for this candidate, so nothing
        # can be consolidated later either.
        await _write_candidate_lesson(ctx, lesson)
        outcome = LEARNING_OUTCOME_REJECTED
        await _record_learning_reflection(
            ctx,
            candidate_action,
            outcome,
            lesson,
            None,
            {"reviewer": reviewer, "review_note": review_note, "consolidated": False},
        )
        return CandidateLessonResult(
            observation=observation,
            candidate_action=candidate_action,
            governance_allowed=True,
            outcome=outcome,
            lesson=lesson,
        )

    # --- Accept: consolidate first, then record what actually happened ----
    consolidation = await _consolidate_accepted_lesson(ctx, lesson)
    if consolidation.stored_count > 0:
        stored = consolidation.outcomes[0]
        lesson.consolidated_kind = stored.kind
        lesson.consolidated_key = stored.key
        outcome = LEARNING_OUTCOME_ACCEPTED
        reason = None
    else:
        # Governance, policy or the consent queue held the write. The
        # acceptance is real and recorded, but the lesson is NOT retrievable,
        # and the result must not claim otherwise.
        detail = consolidation.outcomes[0].detail if consolidation.outcomes else None
        outcome = LEARNING_OUTCOME_NOT_STORED
        reason = (
            consolidation.governance_reason
            or detail
            or "; ".join(consolidation.errors)
            or "the competency write did not land"
        )

    await _write_candidate_lesson(ctx, lesson)
    await _record_learning_reflection(
        ctx,
        candidate_action,
        outcome,
        lesson,
        reason,
        {
            "reviewer": reviewer,
            "review_note": review_note,
            "consolidated": outcome == LEARNING_OUTCOME_ACCEPTED,
            "consolidation": consolidation.to_dict(),
        },
    )
    return CandidateLessonResult(
        observation=observation,
        candidate_action=candidate_action,
        governance_allowed=True,
        outcome=outcome,
        reason=reason,
        lesson=lesson,
        consolidation=consolidation,
    )


async def _propose_candidate_lesson(
    ctx: Any,
    observation: Observation,
    candidate_action: CandidateAction,
    *,
    objective_id: int | None,
    competency_id: str | None,
    inferred_rule: str | None,
    conditions: str | None,
    classification: str,
) -> CandidateLessonResult:
    """The propose branch: recorded outcome -> evidence -> one candidate.

    The evidence comes from `ObjectiveStore.evidence_events()`, which
    structurally excludes `proposal` rows -- so a lesson can never be inferred
    from something Bartholomew only considered doing. Both store reads go
    through `run_off_loop()`, the B2/B8 discipline every other objective read
    here follows.
    """

    def _refuse(outcome: str, reason: str, *, errors: list[str] | None = None):
        return CandidateLessonResult(
            observation=observation,
            candidate_action=candidate_action,
            governance_allowed=True,
            outcome=outcome,
            reason=reason,
            errors=errors or [],
        )

    if not objective_id or not competency_id:
        return _refuse(
            LEARNING_OUTCOME_INVALID,
            "objective_id and competency_id are required to propose a lesson",
        )

    store = getattr(ctx, "objective_store", None)
    if store is None:
        return _refuse(LEARNING_OUTCOME_INVALID, "no objective store is wired in")

    executor = getattr(ctx, "blocking_executor", None)
    objective = await run_off_loop(store.get, objective_id, executor=executor)
    if objective is None:
        return _refuse(
            LEARNING_OUTCOME_NOT_FOUND,
            f"no objective {objective_id}",
        )

    events = await run_off_loop(store.evidence_events, objective_id, executor=executor)

    try:
        lesson = candidate_learning.propose_from_objective(
            objective,
            list(events),
            competency_id=competency_id,
            inferred_rule=inferred_rule,
            conditions=conditions,
            classification=classification,
        )
    except ValueError as exc:
        # Not every experience teaches something. Refusing to invent a lesson
        # is a legitimate outcome, not a failure.
        result = _refuse(LEARNING_OUTCOME_NO_EXPERIENCE, str(exc))
        await _record_learning_reflection(
            ctx,
            candidate_action,
            result.outcome,
            None,
            result.reason,
            {"objective_id": objective_id},
        )
        return result

    errors = lesson.validate()
    if errors:
        return _refuse(
            LEARNING_OUTCOME_INVALID,
            "; ".join(errors),
            errors=errors,
        )

    store_result = await _write_candidate_lesson(ctx, lesson)
    if not getattr(store_result, "stored", False):
        return _refuse(
            LEARNING_OUTCOME_NOT_STORED,
            "the candidate lesson was not stored (policy or consent)",
        )

    reflection = await _record_learning_reflection(
        ctx,
        candidate_action,
        LEARNING_OUTCOME_PROPOSED,
        lesson,
        None,
        {"objective_id": objective_id, "consolidated": False},
    )
    if reflection.row_id is not None:
        # Provenance back into the Reflection authority, written after the
        # fact because the Reflection row does not exist until it is written.
        lesson.reflection_row_id = reflection.row_id
        await _write_candidate_lesson(ctx, lesson)

    return CandidateLessonResult(
        observation=observation,
        candidate_action=candidate_action,
        governance_allowed=True,
        outcome=LEARNING_OUTCOME_PROPOSED,
        lesson=lesson,
    )


# =============================================================================
# Trusted-group share adoption (Package E)
# =============================================================================
#
# The recipient's half of trusted-group sharing. `share_exchange` decided that
# a package may be handed over; this seam decides what the recipient's own
# Bartholomew does about it, and the answer is deliberately modest: adoption
# writes a candidate under a kind the retrieval seam structurally cannot see,
# and nothing else.
#
# The ordering and the gates are the learning loop's, reused rather than
# reimplemented:
#
#   * Gate 1, every action: the fail-closed Parking Brake on the `training`
#     scope, through the same `_evaluate_learning_brake()` helper. Sharing did
#     not acquire a scope of its own -- a halt on learning is a halt on taking
#     someone else's learning too.
#   * Gate 2 for adopt / reject / customise: Identity policy, i.e. the
#     `tool_use.allowlist`. A standing grant is the right shape: all three
#     produce or narrow a candidate that nothing can reason from.
#   * Gate 2 for accept: `evaluate_learning_admission(ctx, LEARNING_ACTION_ACCEPT,
#     lesson=candidate)` -- literally PR #83's authority, not an analogue of
#     it. Acceptance therefore requires a `LearningAcceptanceApproval` bound
#     by fingerprint to this exact candidate, and `share_accept` is absent
#     from the allowlist because adding it there would change nothing.
#
# What a housemate shares is never more than a proposal, and the proposal is
# governed on arrival by the recipient's rules, not the publisher's.

SHARE_OBSERVATION_SOURCE = "trusted_share"

#: The brake scope every share action is halted by -- an alias for the
#: learning loop's, deliberately not a new one: scopes answer *what class of
#: execution is halted*, and adopting someone else's lesson is the same class
#: as forming one. It is an alias rather than a second gate, so the read
#: itself happens in `_evaluate_learning_brake`; the identity is pinned by
#: `tests/test_share_adoption_governance.py` so this cannot quietly diverge
#: into a scope nothing enforces.
SHARE_BRAKE_SCOPE = training.TRAINING_BRAKE_SCOPE

SHARE_ACTION_ADOPT = "share_adopt"
SHARE_ACTION_CUSTOMISE = "share_customise"
SHARE_ACTION_REJECT = "share_reject"
SHARE_ACTION_ACCEPT = "share_accept"

_SHARE_ACTIONS = frozenset(
    {
        SHARE_ACTION_ADOPT,
        SHARE_ACTION_CUSTOMISE,
        SHARE_ACTION_REJECT,
        SHARE_ACTION_ACCEPT,
    },
)

#: Actions whose Identity gate is the ordinary allowlist. `share_accept` is
#: absent on purpose -- see the module note above and Identity.yaml. Read by
#: `evaluate_share_admission`, which refuses any share action that is in
#: neither this set nor the accept branch: a kind nobody classified must not
#: fall through to whichever gate happens to be last.
_SHARE_ALLOWLISTED_ACTIONS = frozenset(
    {SHARE_ACTION_ADOPT, SHARE_ACTION_CUSTOMISE, SHARE_ACTION_REJECT},
)

SHARE_OUTCOME_ADOPTED = "adopted"
SHARE_OUTCOME_CUSTOMISED = "customised"
SHARE_OUTCOME_REJECTED = "rejected"
SHARE_OUTCOME_ACCEPTED = "accepted"
SHARE_OUTCOME_INVALID = "invalid"
SHARE_OUTCOME_NOT_FOUND = "not_found"
SHARE_OUTCOME_NOT_STORED = "not_stored"
SHARE_OUTCOME_REVOKED = "revoked_upstream"

#: The Reflection action kind recorded when a recipient authorises accepting
#: one adopted share. Not a member of `_SHARE_ACTIONS`, for the same reason
#: `LEARNING_ACTION_APPROVE_ACCEPTANCE` is not a learning action: granting
#: authorization is a human act *about* the loop, not a step *of* it.
SHARE_ACTION_APPROVE_ACCEPTANCE = "share_accept_approval_grant"

#: The Reflection action kind recorded when a publisher's withdrawal is
#: carried into a recipient's local candidate. Not a member of
#: `_SHARE_ACTIONS` either: it records something the publisher did, not a
#: decision the recipient took.
SHARE_ACTION_UPSTREAM_REVOKED = "share_upstream_revoked"


@dataclass
class ShareAdoptionResult:
    """Outcome of one share-adoption action through the Runtime Contract.

    `candidate` is the adopted candidate as it stands after the action.
    `consolidation` is the `TrainingRuntimeResult` of an accepted candidate's
    write into the competency substrate, and is None for every outcome except
    a successful accept -- the result contract's way of saying that declining
    or adopting consolidated nothing.
    """

    observation: Observation
    candidate_action: CandidateAction
    governance_allowed: bool
    outcome: str
    reason: str | None = None
    candidate: Any = None
    consolidation: training.TrainingRuntimeResult | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def consolidated(self) -> bool:
        """Whether a retrievable competency record now exists for this share."""
        return bool(self.consolidation is not None and self.consolidation.stored_count > 0)


async def _record_share_reflection(
    ctx: Any,
    candidate_action: CandidateAction,
    outcome: str,
    candidate: Any,
    reason: str | None = None,
    extra: dict[str, Any] | None = None,
) -> ReflectionWriteOutcome:
    """Exactly one ActionReflection per share-adoption action.

    The audit answer to "who took what from whom, and what became of it".
    Records accounts, group, share, revision and content digests -- and
    deliberately **not** the shared content itself, so reading the audit trail
    is never a way around the sanitizer or around a later revocation.
    """
    details: dict[str, Any] = dict(extra or {})
    if reason:
        details["reason"] = reason
    if candidate is not None:
        details["share"] = {
            "competency_id": candidate.competency_id,
            "key": candidate.key(),
            "group_id": candidate.source.group_id,
            "share_id": candidate.source.share_id,
            "publisher_user_id": candidate.source.publisher_user_id,
            "share_revision": candidate.source.share_revision,
            "share_kind": candidate.source.share_kind,
            "content_hash": candidate.source.content_hash,
            "source_candidate_fingerprint": candidate.source.source_candidate_fingerprint,
            "sanitization_policy_revision": candidate.source.sanitization_policy_revision,
            "epistemic_status": candidate.epistemic_status,
            "classification": candidate.classification,
            "confidence": candidate.confidence,
            "review_state": candidate.review_state,
            "local_fork": candidate.local_fork,
            "upstream_revoked_at": candidate.source.revoked_at,
        }
    reflection = ActionReflection(
        surface=SHARE_OBSERVATION_SOURCE,
        action=candidate_action.kind,
        outcome=outcome,
        summary=f"Trusted-group share ({candidate_action.kind}): {outcome}",
        details=details,
    )
    return await record_action_reflection(getattr(ctx, "mem", None), reflection)


async def _write_adopted_share(ctx: Any, candidate: Any) -> Any:
    """Persist an adopted candidate through `MemoryStore`.

    `upsert_memory()` remains the sole write authority. The candidate is
    stored under `share_adoption.KIND`, which the retrieval seam's kind filter
    does not include -- so this makes the adoption durable and reviewable
    without making it reasoning material.
    """
    import json as _json

    return await ctx.mem.upsert_memory(
        share_adoption.KIND,
        candidate.key(),
        _json.dumps(candidate.to_dict()),
        datetime.now(timezone.utc).isoformat(),
        summary=candidate.to_summary_text(),
    )


async def _load_adopted_share(ctx: Any, competency_id: str, slug: str) -> Any:
    """Read one adopted candidate back, or None. Fails closed on both halves."""
    import json as _json

    key = share_adoption.key_for(competency_id, slug)
    try:
        row = await ctx.mem.get_memory(share_adoption.KIND, key)
    except Exception:
        logger.exception("Failed to read adopted share candidate %s", key)
        return None
    if not row:
        return None
    try:
        return share_adoption.AdoptedShareCandidate.from_dict(_json.loads(row["value"]))
    except (TypeError, ValueError, KeyError):
        logger.warning("Stored adopted share candidate %s is not parseable", key)
        return None


async def evaluate_share_admission(
    ctx: Any,
    kind: str,
    *,
    candidate: Any = None,
) -> ObjectiveAdmission:
    """The single Governance authority for every share-adoption action.

    Delegates acceptance to `evaluate_learning_admission()` with
    `LEARNING_ACTION_ACCEPT` -- the same function, the same
    `LearningAcceptanceApproval`, the same fingerprint binding. That is not a
    convenience: requirement is that accepting something a housemate shared
    goes through the *existing* candidate-bound authorization rather than a
    parallel one that could drift from it.

    Every other action is brake-then-allowlist, exactly like `learning_propose`
    and `learning_reject`.
    """
    if kind not in _SHARE_ALLOWLISTED_ACTIONS:
        if kind != SHARE_ACTION_ACCEPT:
            # Neither gate claims this kind. A share action nobody classified
            # is a programming error, not an outcome to report -- the same
            # reading `run_share_adoption_through_runtime_contract` gives an
            # unknown action.
            raise ValueError(f"share action {kind!r} has no Governance gate")
        return await evaluate_learning_admission(ctx, LEARNING_ACTION_ACCEPT, lesson=candidate)

    brake = await _evaluate_learning_brake(ctx, kind)
    if not brake.allowed:
        return brake

    identity_context = getattr(ctx, "identity_context", None)
    if identity_context is not None:
        decision = policy_engine.evaluate_tool_policy(identity_context, kind)
        if not decision.allowed:
            return ObjectiveAdmission(
                False,
                LEARNING_OUTCOME_GOVERNANCE_DENIED,
                f"Denied by Identity policy: {decision.reason}",
            )
    return ObjectiveAdmission(True)


async def grant_share_acceptance_approval(
    ctx: Any,
    *,
    competency_id: str,
    slug: str,
    approver: str,
    note: str | None = None,
) -> LearningApprovalResult:
    """Explicitly authorise accepting one named adopted share.

    Writes the **same** `learning_authorization.LearningAcceptanceApproval`
    record, under the same kind and the same key convention, that PR #83's
    learning loop uses. There is deliberately no second approval type: an
    operator auditing "what has been authorised to become knowledge here?"
    reads one kind and sees everything.

    `objective_id` on the approval is left None, honestly: an adopted share
    stands on no local objective. The binding that actually enforces anything
    is the fingerprint, which covers the shared rule, its conditions, the
    classification and confidence the recipient chose, and the
    `adopted_share` lesson kind -- so an approval for a share can never
    authorise a locally inferred lesson, or the reverse.

    Called by a human review flow, never by Bartholomew's own seams. Inert on
    its own: it consolidates nothing, and every other gate -- the Parking
    Brake first among them -- is still evaluated when acceptance runs.
    """
    import json as _json

    if not competency_id or not slug:
        return LearningApprovalResult(
            False,
            LEARNING_OUTCOME_INVALID,
            "competency_id and slug are required to approve an adopted share",
        )
    if not approver:
        return LearningApprovalResult(
            False,
            LEARNING_OUTCOME_INVALID,
            "an approver is required -- authorization is never anonymous",
        )

    candidate = await _load_adopted_share(ctx, competency_id, slug)
    if candidate is None:
        return LearningApprovalResult(
            False,
            LEARNING_OUTCOME_NOT_FOUND,
            f"no adopted share {share_adoption.key_for(competency_id, slug)!r}",
        )
    if candidate.review_state != share_adoption.REVIEW_PROPOSED:
        return LearningApprovalResult(
            False,
            LEARNING_OUTCOME_INVALID,
            f"cannot approve an adopted share in state {candidate.review_state!r}; "
            "review decisions are terminal",
        )
    if candidate.is_revoked_upstream:
        return LearningApprovalResult(
            False,
            SHARE_OUTCOME_REVOKED,
            "the publisher has withdrawn this share; it cannot be approved for acceptance",
        )

    approval = learning_authorization.LearningAcceptanceApproval(
        competency_id=competency_id,
        slug=slug,
        candidate_fingerprint=learning_authorization.fingerprint_for(candidate),
        approver=approver,
        note=note,
        objective_id=None,
        candidate_revision=candidate.revision,
    )
    errors = approval.validate()
    if errors:
        return LearningApprovalResult(False, LEARNING_OUTCOME_INVALID, "; ".join(errors))

    store_result = await ctx.mem.upsert_memory(
        learning_authorization.KIND,
        approval.key(),
        _json.dumps(approval.to_dict()),
        datetime.now(timezone.utc).isoformat(),
        summary=approval.to_summary_text(),
    )
    if not getattr(store_result, "stored", False):
        return LearningApprovalResult(
            False,
            LEARNING_OUTCOME_NOT_STORED,
            "the acceptance approval was not stored (policy or consent)",
        )

    observation = Observation(
        source=SHARE_OBSERVATION_SOURCE,
        raw_content=(
            f"{SHARE_ACTION_APPROVE_ACCEPTANCE} candidate={approval.key()} approver={approver}"
        ),
    )
    interpretation = _build_interpretation(ctx, observation)
    await _record_share_reflection(
        ctx,
        CandidateAction(
            kind=SHARE_ACTION_APPROVE_ACCEPTANCE,
            interpretation=interpretation,
        ),
        "approved",
        candidate,
        None,
        {
            "approver": approver,
            "approval_note": note,
            "candidate_fingerprint": approval.candidate_fingerprint,
            "candidate_revision": approval.candidate_revision,
            "granted_at": approval.granted_at,
            "consolidated": False,
        },
    )
    return LearningApprovalResult(True, "approved", None, approval)


async def _consolidate_accepted_share(ctx: Any, candidate: Any) -> training.TrainingRuntimeResult:
    """The only path by which an adopted share becomes retrievable knowledge.

    Reuses S5.2's governed write verbatim -- same Observation, same Governance
    gate, same `MemoryStore.upsert_memory()`, same consent queue, same
    Reflection -- with the `trusted_share` source type explicitly unlocked for
    this one call. No second write path and no second governance path.

    `recorded_by="user"` because it was the recipient, a person, who decided
    this belonged in their Bartholomew. `source_detail` names the group, the
    share and the revision, and carries no publisher free text: the sanitizer
    removed that before the package was ever published.
    """
    record = candidate.to_competency_record()
    submission = training.TrainingSubmission(
        competency_id=candidate.competency_id,
        source_type=share_adoption.TRUSTED_SHARE_SOURCE_TYPE,
        source_detail=(
            f"Adopted from trusted group {candidate.source.group_id}, share "
            f"{candidate.source.share_id} rev {candidate.source.share_revision} "
            f"published by {candidate.source.publisher_user_id} "
            f"(content {candidate.source.content_hash}); accepted by {candidate.reviewer}"
            + (" after local customisation" if candidate.local_fork else "")
        ),
        records=[record],
    )
    return await run_training_through_runtime_contract(
        ctx,
        submission,
        recorded_by="user",
        allow_share_adoption_source=True,
    )


async def run_share_adoption_through_runtime_contract(
    ctx: Any,
    action: str,
    *,
    package: Any = None,
    competency_id: str | None = None,
    slug: str | None = None,
    classification: str = "personal",
    reviewer: str | None = None,
    review_note: str | None = None,
    rule: str | None = None,
    conditions: str | None = None,
    steps: list[str] | None = None,
    upstream_revoked_at: str | None = None,
) -> ShareAdoptionResult:
    """
    Trace one trusted-group share-adoption action through the Runtime Contract.

    `action` is one of:

      * ``"share_adopt"``     -- turn an inspected, non-revoked
        `TrustedSharePackage` into one local candidate. Requires `package` and
        `competency_id`.
      * ``"share_customise"`` -- edit the local copy, making it a fork.
        Requires `competency_id`, `slug` and at least one of `rule` /
        `conditions` / `steps`. Every field editable here is one
        consolidation reads, so a customisation cannot be silently discarded
        at acceptance.
      * ``"share_reject"``    -- the recipient declines it locally. Nothing is
        consolidated, then or ever.
      * ``"share_accept"``    -- the recipient accepts it, and it is
        consolidated into their competency substrate. Requires an acceptance
        approval bound to this exact candidate (see
        `grant_share_acceptance_approval`).

    Stages, in order: Observation (source="trusted_share") -> Interpretation ->
    CandidateAction -> Governance (fail-closed, before any write) -> Memory ->
    Reflection. Identical in shape to every other surface's seam.

    `ctx` needs `.mem`; `.governance_store`, `.blocking_executor` and
    `.identity_context` are consulted via getattr with the same additive
    fallbacks the learning seam uses -- no new context attribute is required
    by this package.

    **Adopting a share asserts nothing.** An adopted candidate is stored under
    a kind the retrieval seam structurally cannot see, so it changes no future
    reasoning. Only the accept branch affects what Bartholomew can later
    recall, and it cannot run without a named reviewer *and* an approval bound
    to the candidate's exact content.
    """
    if action not in _SHARE_ACTIONS:
        raise ValueError(f"unknown share-adoption action {action!r}")

    share_id = getattr(package, "share_id", None)
    observation = Observation(
        source=SHARE_OBSERVATION_SOURCE,
        raw_content=(f"{action} share={share_id} competency={competency_id} slug={slug}"),
    )
    interpretation = _build_interpretation(ctx, observation)
    candidate_action = CandidateAction(kind=action, interpretation=interpretation)

    def _refuse(outcome: str, reason: str | None, *, errors: list[str] | None = None):
        return ShareAdoptionResult(
            observation=observation,
            candidate_action=candidate_action,
            governance_allowed=True,
            outcome=outcome,
            reason=reason,
            errors=errors or [],
        )

    async def _refuse_by_governance(admission, candidate=None):
        result = ShareAdoptionResult(
            observation=observation,
            candidate_action=candidate_action,
            governance_allowed=False,
            outcome=admission.outcome or LEARNING_OUTCOME_GOVERNANCE_DENIED,
            reason=admission.reason,
            candidate=candidate,
        )
        await _record_share_reflection(
            ctx,
            candidate_action,
            result.outcome,
            candidate,
            admission.reason,
            {"consolidated": False},
        )
        return result

    # --- Governance gate 1: fail-closed brake, before anything is read ----
    brake = await _evaluate_learning_brake(ctx, action)
    if not brake.allowed:
        return await _refuse_by_governance(brake)

    if action == SHARE_ACTION_ADOPT:
        admission = await evaluate_share_admission(ctx, action)
        if not admission.allowed:
            return await _refuse_by_governance(admission)
        return await _adopt_share(
            ctx,
            observation,
            candidate_action,
            package=package,
            competency_id=competency_id,
            classification=classification,
        )

    if not competency_id or not slug:
        return _refuse(
            SHARE_OUTCOME_INVALID,
            "competency_id and slug are required to act on an adopted share",
        )

    candidate = await _load_adopted_share(ctx, competency_id, slug)
    if candidate is None:
        return _refuse(
            SHARE_OUTCOME_NOT_FOUND,
            f"no adopted share {share_adoption.key_for(competency_id, slug)!r}",
        )

    # A publisher withdrawal is applied to the candidate **in memory** before
    # any decision is taken about it, so a reviewer never accepts something
    # the publisher has taken back while the local row still says otherwise.
    # It is deliberately not persisted here: gate 2 has not run, and this
    # seam's contract is Governance before any write. The action's own write,
    # below, carries it -- and a governance denial therefore leaves the stored
    # candidate exactly as it found it.
    if upstream_revoked_at and not candidate.is_revoked_upstream:
        candidate.mark_upstream_revoked(upstream_revoked_at)

    # Gate 2, on the candidate as it stands -- before any review transition
    # mutates it, so the approval binds to what the reviewer saw.
    admission = await evaluate_share_admission(ctx, action, candidate=candidate)
    if not admission.allowed:
        return await _refuse_by_governance(admission, candidate)

    if action == SHARE_ACTION_CUSTOMISE:
        if rule is None and conditions is None and steps is None:
            return _refuse(
                SHARE_OUTCOME_INVALID,
                "customising requires a replacement rule, conditions or steps",
            )
        try:
            candidate.customise(rule=rule, conditions=conditions, steps=steps)
        except share_adoption.AdoptionStateError as exc:
            return _refuse(SHARE_OUTCOME_INVALID, str(exc))
        errors = candidate.validate()
        if errors:
            return _refuse(SHARE_OUTCOME_INVALID, "; ".join(errors), errors=errors)
        store_result = await _write_adopted_share(ctx, candidate)
        if not getattr(store_result, "stored", False):
            return _refuse(
                SHARE_OUTCOME_NOT_STORED,
                "the customised candidate was not stored (policy or consent)",
            )
        await _record_share_reflection(
            ctx,
            candidate_action,
            SHARE_OUTCOME_CUSTOMISED,
            candidate,
            None,
            {"consolidated": False, "local_fork": True},
        )
        return ShareAdoptionResult(
            observation=observation,
            candidate_action=candidate_action,
            governance_allowed=True,
            outcome=SHARE_OUTCOME_CUSTOMISED,
            candidate=candidate,
        )

    if not reviewer:
        return _refuse(
            SHARE_OUTCOME_INVALID,
            "a review decision requires a reviewer -- review is never anonymous",
        )

    try:
        if action == SHARE_ACTION_REJECT:
            candidate.reject(reviewer=reviewer, note=review_note)
        else:
            candidate.accept(reviewer=reviewer, note=review_note)
    except share_adoption.AdoptionStateError as exc:
        return _refuse(SHARE_OUTCOME_INVALID, str(exc))

    if action == SHARE_ACTION_REJECT:
        store_result = await _write_adopted_share(ctx, candidate)
        if not getattr(store_result, "stored", False):
            return _refuse(
                SHARE_OUTCOME_NOT_STORED,
                "the rejected candidate was not stored (policy or consent)",
            )
        await _record_share_reflection(
            ctx,
            candidate_action,
            SHARE_OUTCOME_REJECTED,
            candidate,
            None,
            {"reviewer": reviewer, "review_note": review_note, "consolidated": False},
        )
        return ShareAdoptionResult(
            observation=observation,
            candidate_action=candidate_action,
            governance_allowed=True,
            outcome=SHARE_OUTCOME_REJECTED,
            candidate=candidate,
        )

    consolidation = await _consolidate_accepted_share(ctx, candidate)
    outcome = SHARE_OUTCOME_ACCEPTED
    reason: str | None = None
    if consolidation.stored_count > 0:
        stored = consolidation.outcomes[0]
        candidate.consolidated_kind = stored.kind
        candidate.consolidated_key = stored.key
    else:
        outcome = SHARE_OUTCOME_NOT_STORED
        reason = (
            consolidation.governance_reason
            or (consolidation.outcomes[0].detail if consolidation.outcomes else None)
            or "; ".join(consolidation.errors)
            or "the accepted share was not consolidated"
        )

    candidate_store = await _write_adopted_share(ctx, candidate)
    if not getattr(candidate_store, "stored", False):
        # The consolidation may well have landed; the candidate row did not.
        # Reporting "accepted" here would be two lies at once: the review
        # surface still shows an unreviewed proposal, and because the row
        # never reached a terminal state the whole accept could be run again.
        # Say what actually happened instead.
        outcome = SHARE_OUTCOME_NOT_STORED
        consolidated_note = (
            "the competency record was written" if consolidation.stored_count > 0 else "nothing"
        )
        reason = (
            "the accepted candidate was not stored (policy or consent), so this "
            f"acceptance is not durably recorded; {consolidated_note} was consolidated"
        )

    await _record_share_reflection(
        ctx,
        candidate_action,
        outcome,
        candidate,
        reason,
        {
            "reviewer": reviewer,
            "review_note": review_note,
            "consolidated": consolidation.stored_count > 0,
            "candidate_stored": bool(getattr(candidate_store, "stored", False)),
            "consolidation": consolidation.to_dict(),
        },
    )
    return ShareAdoptionResult(
        observation=observation,
        candidate_action=candidate_action,
        governance_allowed=True,
        outcome=outcome,
        reason=reason,
        candidate=candidate,
        consolidation=consolidation,
    )


async def record_upstream_revocation(
    ctx: Any,
    *,
    competency_id: str,
    slug: str,
    revoked_at: str,
) -> ShareAdoptionResult | None:
    """Mark a locally adopted candidate as withdrawn by its publisher.

    The wiring behind requirement "revocation remains visibly attached to
    provenance" on the *recipient's* side. `share_exchange.provenance()`
    reports a withdrawal on the exchange; this is what carries it into the
    recipient's own runtime, so the local record's summary line says
    `[withdrawn upstream]` without anyone having to make a second query to
    find out.

    Deliberately narrow. It records a fact the publisher established and
    nothing else: it does not delete the candidate, does not un-consolidate an
    accepted one, and does not change the review state. A publisher who could
    reach further than this would hold a remote delete on another person's
    memory.

    Returns None when there is no such candidate -- a share nobody adopted has
    nothing to mark. Called by a management surface after reading
    `share_exchange.provenance()`; never by Bartholomew's own seams.
    """
    candidate = await _load_adopted_share(ctx, competency_id, slug)
    if candidate is None:
        return None

    observation = Observation(
        source=SHARE_OBSERVATION_SOURCE,
        raw_content=f"share_upstream_revoked candidate={share_adoption.key_for(competency_id, slug)}",
    )
    interpretation = _build_interpretation(ctx, observation)
    candidate_action = CandidateAction(
        kind=SHARE_ACTION_UPSTREAM_REVOKED,
        interpretation=interpretation,
    )

    if candidate.is_revoked_upstream:
        # Already recorded. Idempotent, and it does not re-date the
        # withdrawal: the first time it was seen is the truthful answer.
        return ShareAdoptionResult(
            observation=observation,
            candidate_action=candidate_action,
            governance_allowed=True,
            outcome=SHARE_OUTCOME_REVOKED,
            candidate=candidate,
        )

    candidate.mark_upstream_revoked(revoked_at)
    store_result = await _write_adopted_share(ctx, candidate)
    if not getattr(store_result, "stored", False):
        return ShareAdoptionResult(
            observation=observation,
            candidate_action=candidate_action,
            governance_allowed=True,
            outcome=SHARE_OUTCOME_NOT_STORED,
            reason="the withdrawal was not recorded on the local candidate",
            candidate=candidate,
        )
    await _record_share_reflection(
        ctx,
        candidate_action,
        SHARE_OUTCOME_REVOKED,
        candidate,
        None,
        {"consolidated": False, "upstream_revoked_at": revoked_at},
    )
    return ShareAdoptionResult(
        observation=observation,
        candidate_action=candidate_action,
        governance_allowed=True,
        outcome=SHARE_OUTCOME_REVOKED,
        candidate=candidate,
    )


async def _adopt_share(
    ctx: Any,
    observation: Observation,
    candidate_action: CandidateAction,
    *,
    package: Any,
    competency_id: str | None,
    classification: str,
) -> ShareAdoptionResult:
    """The adopt branch: an inspected package -> one local candidate.

    The package must already have come from `share_exchange.adopt()`, which is
    where membership, revocation and revision are checked against the control
    plane. This branch re-checks revocation anyway rather than trusting its
    input, on the same reasoning `candidate_learning.propose_from_objective`
    re-checks `is_evidence`: the check costs nothing and the alternative is a
    recipient accepting something the publisher has withdrawn.
    """

    def _refuse(outcome: str, reason: str, *, errors: list[str] | None = None):
        return ShareAdoptionResult(
            observation=observation,
            candidate_action=candidate_action,
            governance_allowed=True,
            outcome=outcome,
            reason=reason,
            errors=errors or [],
        )

    if package is None or not competency_id:
        return _refuse(
            SHARE_OUTCOME_INVALID,
            "a share package and a competency_id are required to adopt",
        )

    # Adoption is idempotent for the same package and refused for a different
    # one at the same key. Without this, re-adopting over an already-approved
    # candidate would replace the content `to_competency_record()` reads while
    # leaving the approval in place -- consolidating material the reviewer
    # never saw. `share_adopt` carries a standing Identity grant, so that was
    # reachable without any further authorization.
    #
    # A key is `<competency_id>.adopted_share_<share_id>_r<revision>`, and the
    # exchange is append-only per `(share_id, revision)`: two packages at one
    # key with different content are a contradiction, whichever of them is
    # genuine.
    existing = await _load_adopted_share(
        ctx,
        competency_id,
        share_adoption.slug_for_share(package.share_id, package.revision),
    )
    if existing is not None:
        if existing.review_state != share_adoption.REVIEW_PROPOSED:
            return _refuse(
                SHARE_OUTCOME_INVALID,
                f"this share has already been {existing.review_state} locally; "
                "review decisions are terminal",
            )
        if existing.source.content_hash != package.content_hash():
            return _refuse(
                SHARE_OUTCOME_INVALID,
                "a different package is already adopted at this key; refusing to "
                "replace it, because an acceptance approval already granted for it "
                "would then authorise content nobody reviewed",
            )
        if existing.local_fork:
            return _refuse(
                SHARE_OUTCOME_INVALID,
                "this adoption has been customised locally; re-adopting would discard the fork",
            )

    try:
        candidate = share_adoption.candidate_from_package(
            package,
            competency_id=competency_id,
            classification=classification,
        )
    except ValueError as exc:
        outcome = (
            SHARE_OUTCOME_REVOKED
            if getattr(package, "is_revoked", False)
            else SHARE_OUTCOME_INVALID
        )
        result = _refuse(outcome, str(exc))
        await _record_share_reflection(
            ctx,
            candidate_action,
            result.outcome,
            None,
            result.reason,
            {"share_id": getattr(package, "share_id", None), "consolidated": False},
        )
        return result

    errors = candidate.validate()
    if errors:
        return _refuse(SHARE_OUTCOME_INVALID, "; ".join(errors), errors=errors)

    store_result = await _write_adopted_share(ctx, candidate)
    if not getattr(store_result, "stored", False):
        return _refuse(
            SHARE_OUTCOME_NOT_STORED,
            "the adopted share was not stored (policy or consent)",
        )

    await _record_share_reflection(
        ctx,
        candidate_action,
        SHARE_OUTCOME_ADOPTED,
        candidate,
        None,
        {"consolidated": False},
    )
    return ShareAdoptionResult(
        observation=observation,
        candidate_action=candidate_action,
        governance_allowed=True,
        outcome=SHARE_OUTCOME_ADOPTED,
        candidate=candidate,
    )
