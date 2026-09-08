"""The Runtime Contract seam for executive task orchestration.

One entry point per direction and no others:

* `run_executive_task_through_runtime_contract()` --- a person's instruction
  becomes a governed plan and, at most, **one** action-envelope proposal.
* `advance_executive_task_through_runtime_contract()` --- an already-proposed
  step's outcome is observed, verified, and either followed by the next step or
  handed to a defined recovery decision.

What this module may reach, and what it may not
------------------------------------------------
It reaches the operating system through exactly one import:
`bartholomew.actuation.seam` --- the `action-envelope` shared contract owned by
W03-C. That is the whole of the executive's route to a machine, and
`tests/test_w03b_no_bypass.py` proves it structurally by reading this package's
syntax tree rather than trusting this sentence.

Three things this seam deliberately does not do
------------------------------------------------

1. **It never approves.** `grant_action_approval` is not called anywhere in
   this package. A proposal stops at `pending_approval` and waits for a human
   at the host boundary. An executive that could approve its own proposal would
   have collapsed the authorization step into the proposing step, which is the
   one thing the envelope's eleven gates exist to keep apart.

2. **It never mints a governance authority of its own.** The gate below is the
   *existing* Parking Brake, read fail-closed through the same composed helper
   every other seam uses. The Identity policy is evaluated by the envelope, on
   the envelope's own kinds --- `windows_action_request` and
   `windows_action_cancel`, both already allowlisted --- so no entry is added
   to `Identity.yaml` by this session and none is needed. The executive's own
   two kinds are *cognition*: planning and reading a result. They are exempt
   from `tool_use.allowlist` on exactly the recorded precedent
   `_CONVERSATIONAL_KINDS` sets in `runtime_contract.py`, because that
   allowlist's grain is "a skill_id or scheduler drive task_id", and an
   exemption there authorizes nothing --- every action the plan contains is
   still evaluated for real, one gate later. `tests/test_w03b_governance.py`
   pins that: with the executive kinds absent from a restrictive
   `Identity.yaml`, planning still works and the proposal is still refused when
   `windows_action_request` is removed.

3. **It never advances on issuance.** A step moves only when the previous one
   verified, in `verification.py`'s sense, where a device's own report of
   success with nothing read back is `unknown`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from bartholomew.actuation import seam as action_seam
from bartholomew.actuation import store as action_store
from bartholomew.actuation.store import ActionState
from bartholomew.kernel.blocking_executor import run_off_loop
from bartholomew.kernel.reflection import ActionReflection, record_action_reflection
from bartholomew.kernel.runtime_contract import (
    CandidateAction,
    Interpretation,
    Observation,
)
from bartholomew.orchestrator.safety.governance_store import (
    engaged_state_fail_closed_off_loop,
)

from . import store as executive_store
from .evidence import AdmittedEvidence, admit_evidence
from .explanation import explain_task, explanation_details, load_task_reflections
from .intent import TaskIntent, clarification_subject, parse_task
from .plan import (
    Plan,
    PlanStep,
    StepStatus,
    TaskStatus,
    build_plan,
    next_actionable_step,
    plan_status,
)
from .recovery import (
    ABANDON_STEP,
    ASK_FOR_CLARIFICATION,
    RE_PROPOSE,
    decide_recovery,
)
from .verification import FAILED, VERIFIED, ReadBackPort, Verification, verify_step

logger = logging.getLogger(__name__)

#: The seam kinds. Two, and both are cognition: one plans, one reads a result.
#: Neither authorizes anything --- see the module docstring, item 2.
EXECUTIVE_KIND_TASK = "executive_task"
EXECUTIVE_KIND_ADVANCE = "executive_task_advance"

#: Kinds exempt from `tool_use.allowlist`, on the recorded precedent
#: `runtime_contract._CONVERSATIONAL_KINDS` sets. Adding a kind here that could
#: reach a machine would be a governance change; neither of these can.
_COGNITIVE_KINDS = frozenset({EXECUTIVE_KIND_TASK, EXECUTIVE_KIND_ADVANCE})

#: The Parking Brake scope named in a refusal. The gate itself reads the
#: composed "engaged at all" state, so *any* engagement --- global, `skills`,
#: `actuation`, `sight` --- stops the executive. Deliberately the most
#: restrictive reading available: planning somebody's PC actions while any part
#: of Bartholomew is halted is exactly what a halt is for.
EXECUTIVE_BRAKE_SCOPE = "executive"

#: The surface every Reflection this seam writes is recorded under.
REFLECTION_SURFACE = "executive"

#: How long a proposed action stays valid. Bounded so an approval sitting
#: unanswered for an afternoon cannot be spent on a machine whose state has
#: moved on. The envelope bounds it again regardless of what is asked for.
DEFAULT_ACTION_TTL_SECONDS = 900

#: Outcomes on the result contract. Distinct values, because "I asked you a
#: question" and "the brake is on" are not the same non-event.
OUTCOME_PROPOSED = "proposed"
OUTCOME_CLARIFICATION = "clarification_requested"
OUTCOME_REFUSED = "refused"
OUTCOME_BRAKE = "parking_brake_denied"
OUTCOME_ADVANCED = "advanced"
OUTCOME_WAITING = "waiting_on_authorization"
OUTCOME_COMPLETED = "completed"
OUTCOME_STOPPED = "stopped"
OUTCOME_ERROR = "error"


@dataclass
class ExecutiveTaskResult:
    """Everything produced by one pass through this seam.

    Shaped like the other Runtime Contract results --- observation, candidate
    action, a governance verdict, an outcome and a reason --- plus the plan and
    the account a person reads. `provenance_degraded` carries the same meaning
    it does on the action and device seams: the governed decision happened and
    the state exists, but its Reflection did not persist, so a caller must not
    present it as fully recorded.
    """

    observation: Observation
    candidate_action: CandidateAction
    governance_allowed: bool
    outcome: str
    reason: str | None = None
    plan: Plan | None = None
    explanation: str = ""
    #: Action ids this pass proposed. Empty when it proposed nothing --- which
    #: is every refusal and every clarification.
    proposed_action_ids: list[str] = field(default_factory=list)
    provenance_degraded: bool = False
    provenance_error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "governance_allowed": self.governance_allowed,
            "reason": self.reason,
            "explanation": self.explanation,
            "proposed_action_ids": list(self.proposed_action_ids),
            "plan": self.plan.as_dict() if self.plan else None,
            "provenance_degraded": self.provenance_degraded,
            "provenance_error": self.provenance_error,
        }


# ---------------------------------------------------------------------------
# Governance
# ---------------------------------------------------------------------------


def _ctx_db_path(ctx: Any) -> str:
    path = getattr(getattr(ctx, "mem", None), "db_path", None) or getattr(ctx, "db_path", None)
    if not path:
        raise executive_store.ExecutivePersistenceError(
            "no database path is reachable from the runtime context; refusing rather "
            "than guessing where executive task state lives",
        )
    return str(path)


def _observation(kind: str, instruction: str) -> tuple[Observation, CandidateAction]:
    """The Runtime Contract preamble, built the way every other seam builds it."""
    observation = Observation(source=f"executive:{kind}", raw_content=instruction[:500])
    interpretation = Interpretation(observation=observation, prompt=instruction[:500])
    return observation, CandidateAction(kind=kind, interpretation=interpretation)


async def evaluate_executive_admission(ctx: Any, kind: str) -> tuple[bool, str | None]:
    """The fail-closed gate on one executive pass. Not a new authority.

    **The brake, composed and fail-closed.** `engaged_state_fail_closed_off_loop`
    is the helper objective mutation, consent resolution, inbound capture and
    the actuation seam already use; it composes the platform tier's "engaged at
    all" answer. Any engagement stops the executive, and an unreadable brake
    stops it too: an unreadable safety gate is not evidence of the absence of a
    safety gate.

    **The Identity policy**, for any kind that is not cognition. Both of this
    seam's own kinds are, so in practice this is the room left for a future
    executive kind that is not --- the same room `_CONVERSATIONAL_KINDS` leaves
    in `runtime_contract.py`. Every action the plan proposes is evaluated
    against `tool_use.allowlist` by the envelope, on `windows_action_request`,
    one gate later and regardless of what happens here.
    """
    db_path = _ctx_db_path(ctx)
    try:
        state = await engaged_state_fail_closed_off_loop(
            db_path,
            governance_store=getattr(ctx, "governance_store", None),
            executor=getattr(ctx, "blocking_executor", None),
        )
    except Exception:
        logger.exception("Governance check failed for %s; failing closed", kind)
        return False, "the parking brake state could not be read, so nothing was planned"
    if state.engaged:
        scopes = ", ".join(sorted(state.scopes)) or "global"
        return False, (
            f"a parking brake or platform halt is engaged (scopes={scopes}); the "
            "executive plans nothing and proposes nothing while it is"
        )

    if kind not in _COGNITIVE_KINDS:
        admission = action_seam.check_identity_policy(ctx, kind)
        if not admission.allowed:
            return False, admission.reason
    return True, None


async def _record(
    ctx: Any,
    candidate_action: CandidateAction,
    outcome: str,
    plan: Plan | None,
    reason: str | None,
) -> tuple[bool, str | None]:
    """One `ActionReflection` per pass, through the one shared Memory sink.

    Redacted by construction: nothing here writes a parameter value. A step is
    named by its capability and its action id, and the text somebody asked to
    have typed never appears --- the envelope's own Reflection already records
    it as a digest, and a second copy in cleartext here would undo that.
    """
    details: dict[str, Any] = {"kind": candidate_action.kind}
    if plan is not None:
        details.update(explanation_details(plan))
    if reason:
        details["reason"] = reason
    reflection = ActionReflection(
        surface=REFLECTION_SURFACE,
        action=candidate_action.kind,
        outcome=outcome,
        summary=f"Executive ({candidate_action.kind}): {outcome}",
        details=details,
    )
    write = await record_action_reflection(getattr(ctx, "mem", None), reflection)
    return bool(getattr(write, "error", None)), getattr(write, "error", None)


async def _open_clarification(ctx: Any, plan: Plan, question: str) -> None:
    """Raise the question as a real `awaiting_response` obligation.

    Uses the existing seam, so the obligation is reminded, escalated and
    resolved by the machinery that already does that --- there is no second
    question queue here. A context with no obligation store degrades honestly:
    the question is still on the plan and in the explanation, it simply is not
    tracked as an obligation, and the plan says so.
    """
    plan.clarification = question
    store_obj = getattr(ctx, "awaiting_response_store", None)
    if store_obj is None:
        plan.notes.append(
            "the question was not tracked as an obligation: this runtime has no "
            "awaiting-response store wired in",
        )
        return
    try:
        from bartholomew.kernel.runtime_contract import (  # noqa: PLC0415
            run_awaiting_response_through_runtime_contract,
        )

        result = await run_awaiting_response_through_runtime_contract(
            ctx,
            "open",
            subject=question[:500],
            origin_surface="executive",
            context_ref=plan.task_id,
            actor=plan.requested_by,
        )
        entry = getattr(result, "entry", None)
        if entry is not None:
            plan.clarification_entry_id = getattr(entry, "id", None)
    except Exception:
        logger.exception("Could not open an awaiting_response obligation for %s", plan.task_id)
        plan.notes.append(
            "the question could not be recorded as an obligation; it is on this plan "
            "and in the explanation regardless",
        )


# ---------------------------------------------------------------------------
# Proposing
# ---------------------------------------------------------------------------


async def _propose(
    ctx: Any,
    plan: Plan,
    step: PlanStep,
    *,
    registry: Any = None,
    ttl_seconds: int | None = None,
) -> str | None:
    """Build one `ActionRequest` and put it through the envelope. Nothing runs.

    This is the **only** call in this package that reaches the action envelope
    with something to admit, and there is no other way for a step to acquire an
    `action_id`. A step whose `action_id` is `None` has, by construction, never
    been anywhere near a machine.
    """
    step.attempts += 1
    try:
        result = await action_seam.run_action_request_through_runtime_contract(
            ctx,
            tenant_id=plan.tenant_id,
            device_id=plan.device_id,
            requested_by=plan.requested_by,
            capability=step.capability,
            capability_version=step.selection.version,
            parameters=dict(step.parameters),
            correlation_id=plan.task_id,
            causation_id=f"{plan.task_id}:{step.index}",
            ttl_seconds=ttl_seconds or DEFAULT_ACTION_TTL_SECONDS,
            registry=registry,
        )
    except Exception as exc:
        logger.exception("The action envelope raised while proposing step %s", step.index)
        step.status = StepStatus.BLOCKED
        step.refusal_category = "internal_error"
        step.refusal_reason = (
            f"the action envelope could not admit this step ({type(exc).__name__}); "
            "nothing was proposed"
        )
        return None

    if result.action is not None and result.governance_allowed:
        step.action_id = result.action.action_id
        step.status = StepStatus.AWAITING_AUTHORIZATION
        step.refusal_category = None
        step.refusal_reason = None
        return step.action_id

    step.status = StepStatus.BLOCKED
    step.refusal_category = getattr(result.category, "value", None) or (
        str(result.category) if result.category else None
    )
    step.refusal_reason = result.reason or "the action envelope refused this step"
    if result.action is not None:
        step.action_id = result.action.action_id
    return None


# ---------------------------------------------------------------------------
# The two entry points
# ---------------------------------------------------------------------------


async def run_executive_task_through_runtime_contract(
    ctx: Any,
    *,
    tenant_id: str,
    device_id: str,
    requested_by: str,
    instruction: str,
    evidence: Any = None,
    registry: Any = None,
    task_id: str | None = None,
    ttl_seconds: int | None = None,
) -> ExecutiveTaskResult:
    """Turn one instruction into a governed plan and at most one proposal.

    `tenant_id`, `device_id` and `requested_by` come from the caller's
    resolution of the platform's own authority, exactly as they do for the
    action envelope. This function has no default for any of them and reads
    none of them from `instruction`.

    `evidence` is whatever the memory-retrieval side recalled --- rows,
    mappings or `EvidenceRecord`s. Every one of them is put through
    `admit_evidence` (the retrieval-side validity verdict) and then used only
    for notes and cautions. No capability, parameter, device or approval on the
    returned plan was read from any of it.

    Returns without proposing anything when the instruction is ambiguous or
    asks for something outside the capability vocabulary: the question becomes
    an `awaiting_response` obligation, and the person is told what was and was
    not understood.
    """
    kind = EXECUTIVE_KIND_TASK
    observation, candidate_action = _observation(kind, instruction or "")
    db_path = _ctx_db_path(ctx)

    allowed, denial = await evaluate_executive_admission(ctx, kind)
    if not allowed:
        result = ExecutiveTaskResult(
            observation=observation,
            candidate_action=candidate_action,
            governance_allowed=False,
            outcome=OUTCOME_BRAKE,
            reason=denial,
            explanation=(
                f"I did not plan anything for {instruction!r}. {denial}"
                if instruction
                else str(denial)
            ),
        )
        degraded, error = await _record(ctx, candidate_action, OUTCOME_BRAKE, None, denial)
        result.provenance_degraded, result.provenance_error = degraded, error
        return result

    await run_off_loop(
        executive_store.ensure_schema,
        db_path,
        executor=getattr(ctx, "blocking_executor", None),
    )

    intent: TaskIntent = parse_task(instruction or "")
    admitted: AdmittedEvidence = (
        evidence if isinstance(evidence, AdmittedEvidence) else admit_evidence(evidence)
    )

    device, admission = action_seam.resolve_device(
        tenant_id=tenant_id,
        device_id=device_id,
        registry=registry,
    )

    plan = build_plan(
        intent=intent,
        tenant_id=tenant_id,
        device_id=device_id,
        requested_by=requested_by,
        device=device,
        evidence=admitted,
        task_id=task_id,
    )

    # Ambiguity and refusal come first, and they come before any device
    # question: a question about what was meant is not improved by first
    # telling somebody their machine is not enrolled for the wrong reading.
    if intent.ambiguities or intent.unsupported:
        question = clarification_subject(intent)
        await _open_clarification(ctx, plan, question)
        plan.status = TaskStatus.AWAITING_CLARIFICATION
        # Steps that were understood are not proposed. Half an instruction is
        # not the instruction, and proposing the understood half would act on a
        # reading the person has not confirmed.
        for step in plan.steps:
            if step.status is StepStatus.PLANNED:
                step.status = StepStatus.BLOCKED
                step.refusal_category = "awaiting_clarification"
                step.refusal_reason = (
                    "understood, but not proposed: the rest of the instruction is "
                    "unresolved and the executive proposes an instruction whole"
                )
        return await _finish(
            ctx,
            plan,
            candidate_action,
            observation,
            db_path,
            outcome=OUTCOME_CLARIFICATION,
            reason=question,
            governance_allowed=True,
        )

    if device is None:
        plan.status = TaskStatus.REFUSED
        for step in plan.steps:
            step.status = StepStatus.BLOCKED
            step.refusal_category = getattr(admission.category, "value", None)
            step.refusal_reason = admission.reason
        return await _finish(
            ctx,
            plan,
            candidate_action,
            observation,
            db_path,
            outcome=OUTCOME_REFUSED,
            reason=admission.reason,
            governance_allowed=False,
        )

    proposed: list[str] = []
    step = next_actionable_step(plan)
    if step is not None:
        action_id = await _propose(ctx, plan, step, registry=registry, ttl_seconds=ttl_seconds)
        if action_id:
            proposed.append(action_id)

    plan.status = plan_status(plan)
    outcome = OUTCOME_PROPOSED if proposed else OUTCOME_REFUSED
    reason = None if proposed else _first_refusal(plan)
    return await _finish(
        ctx,
        plan,
        candidate_action,
        observation,
        db_path,
        outcome=outcome,
        reason=reason,
        governance_allowed=bool(proposed),
        proposed=proposed,
    )


async def advance_executive_task_through_runtime_contract(
    ctx: Any,
    *,
    tenant_id: str,
    task_id: str,
    registry: Any = None,
    read_back_port: ReadBackPort | None = None,
    read_back_target: Any = None,
    multimodal_store: Any = None,
    ttl_seconds: int | None = None,
) -> ExecutiveTaskResult:
    """Observe what became of the outstanding step, verify it, and continue or recover.

    The order is fixed and it is the point: **read the action's own state, then
    verify against the machine, then decide.** A step is never advanced past
    because it was approved, dispatched or reported successful --- only because
    it verified.
    """
    kind = EXECUTIVE_KIND_ADVANCE
    observation, candidate_action = _observation(kind, task_id)
    db_path = _ctx_db_path(ctx)
    executor = getattr(ctx, "blocking_executor", None)

    allowed, denial = await evaluate_executive_admission(ctx, kind)
    if not allowed:
        result = ExecutiveTaskResult(
            observation=observation,
            candidate_action=candidate_action,
            governance_allowed=False,
            outcome=OUTCOME_BRAKE,
            reason=denial,
            explanation=str(denial),
        )
        degraded, error = await _record(ctx, candidate_action, OUTCOME_BRAKE, None, denial)
        result.provenance_degraded, result.provenance_error = degraded, error
        return result

    await run_off_loop(executive_store.ensure_schema, db_path, executor=executor)
    plan = await run_off_loop(
        executive_store.load_plan,
        db_path,
        tenant_id=tenant_id,
        task_id=task_id,
        executor=executor,
    )
    if plan is None:
        return ExecutiveTaskResult(
            observation=observation,
            candidate_action=candidate_action,
            governance_allowed=False,
            outcome=OUTCOME_ERROR,
            reason=f"no executive task {task_id!r} for this tenant",
            explanation=f"I have no record of task {task_id!r}.",
        )

    outstanding = _outstanding_step(plan)
    if outstanding is None:
        plan.status = plan_status(plan)
        return await _finish(
            ctx,
            plan,
            candidate_action,
            observation,
            db_path,
            outcome=(OUTCOME_COMPLETED if plan.status is TaskStatus.COMPLETED else OUTCOME_STOPPED),
            reason=None,
            governance_allowed=True,
        )

    stored = await run_off_loop(
        action_store.get_action,
        db_path,
        tenant_id=plan.tenant_id,
        action_id=outstanding.action_id,
        executor=executor,
    )
    if stored is None:
        outstanding.status = StepStatus.BLOCKED
        outstanding.refusal_category = "action_missing"
        outstanding.refusal_reason = (
            "the action this step proposed is no longer readable; the plan stops "
            "rather than proposing it again on the strength of not finding it"
        )
        plan.status = plan_status(plan)
        return await _finish(
            ctx,
            plan,
            candidate_action,
            observation,
            db_path,
            outcome=OUTCOME_STOPPED,
            reason=outstanding.refusal_reason,
            governance_allowed=True,
        )

    if stored.state in (ActionState.PENDING_APPROVAL, ActionState.APPROVED):
        outstanding.status = StepStatus.AWAITING_AUTHORIZATION
        plan.status = plan_status(plan)
        return await _finish(
            ctx,
            plan,
            candidate_action,
            observation,
            db_path,
            outcome=OUTCOME_WAITING,
            reason=(
                f"action {stored.action_id} is {stored.state.value}; nothing has run and "
                "nothing will until it is authorized at the host boundary"
            ),
            governance_allowed=True,
        )

    if stored.state is ActionState.LEASED:
        outstanding.status = StepStatus.DISPATCHED
        plan.status = plan_status(plan)
        return await _finish(
            ctx,
            plan,
            candidate_action,
            observation,
            db_path,
            outcome=OUTCOME_WAITING,
            reason=(
                f"action {stored.action_id} is with the device and no outcome has been "
                "reported yet. Issued is not succeeded."
            ),
            governance_allowed=True,
        )

    if stored.state is ActionState.REFUSED:
        outstanding.status = StepStatus.BLOCKED
        outstanding.refusal_category = "governance_denied"
        outstanding.refusal_reason = stored.state_reason or "the envelope refused this action"
        plan.status = plan_status(plan)
        return await _finish(
            ctx,
            plan,
            candidate_action,
            observation,
            db_path,
            outcome=OUTCOME_STOPPED,
            reason=outstanding.refusal_reason,
            governance_allowed=True,
        )

    verification = verify_step(
        capability=outstanding.capability,
        parameters=outstanding.parameters,
        device_status=stored.status.value,
        tenant_id=plan.tenant_id,
        device_id=plan.device_id,
        requested_by=plan.requested_by,
        db_path=db_path,
        read_back_port=read_back_port,
        read_back_target=read_back_target,
        store=multimodal_store,
    )
    outstanding.verification = verification

    if verification.verdict == VERIFIED:
        outstanding.status = StepStatus.VERIFIED
        proposed: list[str] = []
        nxt = next_actionable_step(plan)
        if nxt is not None:
            action_id = await _propose(ctx, plan, nxt, registry=registry, ttl_seconds=ttl_seconds)
            if action_id:
                proposed.append(action_id)
        plan.status = plan_status(plan)
        return await _finish(
            ctx,
            plan,
            candidate_action,
            observation,
            db_path,
            outcome=(
                OUTCOME_COMPLETED if plan.status is TaskStatus.COMPLETED else OUTCOME_ADVANCED
            ),
            reason=verification.detail,
            governance_allowed=True,
            proposed=proposed,
        )

    outstanding.status = StepStatus.FAILED if verification.verdict == FAILED else StepStatus.UNKNOWN
    return await _recover(
        ctx,
        plan,
        outstanding,
        verification,
        candidate_action,
        observation,
        db_path,
        registry=registry,
        ttl_seconds=ttl_seconds,
    )


async def _recover(
    ctx: Any,
    plan: Plan,
    step: PlanStep,
    verification: Verification,
    candidate_action: CandidateAction,
    observation: Observation,
    db_path: str,
    *,
    registry: Any,
    ttl_seconds: int | None,
) -> ExecutiveTaskResult:
    """Apply the one defined recovery decision. There is no retry branch here."""
    decision = decide_recovery(
        verdict=verification.verdict,
        capability=step.capability,
        described_as=step.described_as,
        approval_requirement=step.selection.approval_requirement,
        attempts=step.attempts,
        cautions=tuple(plan.cautions),
        read_back_code=verification.read_back_code,
    )
    step.recoveries.append(decision.as_dict())

    proposed: list[str] = []
    if decision.decision == RE_PROPOSE:
        # A *fresh* request, not a repeat of the old one: no `action_id` is
        # carried over, so the envelope builds a new parameter fingerprint and
        # the previous approval cannot authorize it. That is what makes this a
        # re-proposal rather than a retry.
        step.status = StepStatus.PLANNED
        step.action_id = None
        step.verification = None
        action_id = await _propose(ctx, plan, step, registry=registry, ttl_seconds=ttl_seconds)
        if action_id:
            proposed.append(action_id)
    elif decision.decision == ASK_FOR_CLARIFICATION:
        await _open_clarification(ctx, plan, decision.question or decision.reason)
    elif decision.decision == ABANDON_STEP:
        step.status = StepStatus.BLOCKED
        step.refusal_category = "abandoned"
        step.refusal_reason = decision.reason

    plan.status = plan_status(plan)
    outcome = OUTCOME_PROPOSED if proposed else OUTCOME_STOPPED
    if plan.status is TaskStatus.AWAITING_CLARIFICATION:
        outcome = OUTCOME_CLARIFICATION
    return await _finish(
        ctx,
        plan,
        candidate_action,
        observation,
        db_path,
        outcome=outcome,
        reason=decision.reason,
        governance_allowed=True,
        proposed=proposed,
    )


def _outstanding_step(plan: Plan) -> PlanStep | None:
    """The one step with an action in flight, if any."""
    for step in plan.steps:
        if step.action_id and step.status in (
            StepStatus.AWAITING_AUTHORIZATION,
            StepStatus.DISPATCHED,
        ):
            return step
    return None


def _first_refusal(plan: Plan) -> str | None:
    for step in plan.steps:
        if step.refusal_reason:
            return step.refusal_reason
    return None


async def _finish(
    ctx: Any,
    plan: Plan,
    candidate_action: CandidateAction,
    observation: Observation,
    db_path: str,
    *,
    outcome: str,
    reason: str | None,
    governance_allowed: bool,
    proposed: list[str] | None = None,
) -> ExecutiveTaskResult:
    """Persist the plan, write one Reflection, and build the account.

    The order matters: the plan is durable before the explanation claims
    anything about it, and a failed persistence is a refusal rather than a
    silently in-memory plan that a later `advance` would not find.
    """
    plan.status = plan.status if plan.clarification is None else TaskStatus.AWAITING_CLARIFICATION
    try:
        await run_off_loop(
            executive_store.save_plan,
            db_path,
            plan,
            executor=getattr(ctx, "blocking_executor", None),
        )
    except executive_store.ExecutivePersistenceError as e:
        logger.exception("Executive task state could not be persisted")
        result = ExecutiveTaskResult(
            observation=observation,
            candidate_action=candidate_action,
            governance_allowed=False,
            outcome=OUTCOME_ERROR,
            reason=str(e),
            plan=plan,
            explanation=(
                f"I could not record this task's state, so I am not treating it as started: {e}"
            ),
        )
        await _record(ctx, candidate_action, OUTCOME_ERROR, plan, str(e))
        return result

    # The Reflection for *this* pass is written before the account is built, so
    # the account quotes a trail that already includes it. The trail is read
    # back out of the shared sink rather than remembered here --- see
    # `explanation.load_task_reflections`.
    degraded, error = await _record(ctx, candidate_action, outcome, plan, reason)
    reflections = await run_off_loop(
        load_task_reflections,
        db_path,
        plan.task_id,
        executor=getattr(ctx, "blocking_executor", None),
    )
    explanation = explain_task(plan, reflections=reflections)
    return ExecutiveTaskResult(
        observation=observation,
        candidate_action=candidate_action,
        governance_allowed=governance_allowed,
        outcome=outcome,
        reason=reason,
        plan=plan,
        explanation=explanation,
        proposed_action_ids=list(proposed or []),
        provenance_degraded=degraded,
        provenance_error=error,
    )


__all__ = [
    "DEFAULT_ACTION_TTL_SECONDS",
    "EXECUTIVE_BRAKE_SCOPE",
    "EXECUTIVE_KIND_ADVANCE",
    "EXECUTIVE_KIND_TASK",
    "OUTCOME_ADVANCED",
    "OUTCOME_BRAKE",
    "OUTCOME_CLARIFICATION",
    "OUTCOME_COMPLETED",
    "OUTCOME_ERROR",
    "OUTCOME_PROPOSED",
    "OUTCOME_REFUSED",
    "OUTCOME_STOPPED",
    "OUTCOME_WAITING",
    "REFLECTION_SURFACE",
    "ExecutiveTaskResult",
    "advance_executive_task_through_runtime_contract",
    "evaluate_executive_admission",
    "run_executive_task_through_runtime_contract",
]
