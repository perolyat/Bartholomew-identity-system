"""The governed plan: an ordered sequence of proposals, and the rule that paces it.

A plan here is not a schedule and not a script. It is the ordered list of
things the person asked for, each one already resolved to a capability their
device declares, each one still needing to travel the envelope's eleven gates
and each one still needing a human authorization before anything happens.

**The pacing rule is the whole of the multi-step design, and it is one
sentence:** a step becomes actionable only when every earlier step is
`VERIFIED`. Not proposed, not approved, not dispatched, not "the device said
succeeded" --- verified, in the sense `verification.py` defines, where a device
report with nothing read back is `unknown` rather than success. A plan that
advanced on issuance would be a plan that typed the second sentence into
whatever window the first step failed to open.

Nothing in this module writes anything, reaches an operating system, or decides
governance. It is the shape the seam moves through, and the place the pacing
rule lives so that it is one function rather than an assumption spread over a
loop.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .evidence import AdmittedEvidence
from .intent import IntentStep, TaskIntent
from .selection import CapabilitySelection, select_capability
from .verification import Verification


class StepStatus(str, Enum):
    """Where one step stands. Seven values, and none of them means "probably"."""

    #: Resolved to a declared capability; nothing has been proposed yet.
    PLANNED = "planned"
    #: An `ActionRequest` exists and the envelope admitted it. **Nothing has
    #: run.** This is where a step waits for a human at the host boundary.
    AWAITING_AUTHORIZATION = "awaiting_authorization"
    #: The action was approved and leased to the device.
    DISPATCHED = "dispatched"
    #: Executed *and* the resulting state was read back and matched.
    VERIFIED = "verified"
    #: The device reported a failure, or the read-back contradicted it.
    FAILED = "failed"
    #: Issued, and what actually happened cannot be established. Deliberately
    #: not merged into `FAILED`: a retry decision differs between "it did not
    #: happen" and "we cannot tell whether it happened".
    UNKNOWN = "unknown"
    #: The executive stopped here --- refused by governance, refused by
    #: selection, or abandoned by a recovery decision.
    BLOCKED = "blocked"


#: A step in one of these is finished; the plan never moves it again.
STEP_TERMINAL_STATUSES = frozenset(
    {StepStatus.VERIFIED, StepStatus.FAILED, StepStatus.UNKNOWN, StepStatus.BLOCKED},
)


class TaskStatus(str, Enum):
    """Where the whole task stands."""

    #: A question was raised instead of an action. The person has to answer.
    AWAITING_CLARIFICATION = "awaiting_clarification"
    #: At least one step is proposed and waiting on a human authorization.
    IN_PROGRESS = "in_progress"
    #: Every step verified.
    COMPLETED = "completed"
    #: Stopped short: a failure, an `unknown`, or a governance refusal.
    STOPPED = "stopped"
    #: Refused before anything was proposed (brake, policy, no capability).
    REFUSED = "refused"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PlanStep:
    """One proposal-shaped step. Mutable, because its status is what moves."""

    index: int
    capability: str
    parameters: dict[str, Any]
    described_as: str
    selection: CapabilitySelection
    status: StepStatus = StepStatus.PLANNED
    #: The envelope's action id, once one exists. `None` until proposed --- and
    #: a step with no action id has, by construction, reached no machine.
    action_id: str | None = None
    #: The envelope's own refusal, verbatim, when it refused.
    refusal_category: str | None = None
    refusal_reason: str | None = None
    verification: Verification | None = None
    #: Recovery decisions taken on this step, in order. Never empty for a step
    #: that failed or ended unknown --- a blind retry has no entry here because
    #: there is no code path that performs one.
    recoveries: list[dict[str, Any]] = field(default_factory=list)
    #: How many times this step has been proposed. Bounded in `recovery.py`.
    attempts: int = 0

    @property
    def terminal(self) -> bool:
        return self.status in STEP_TERMINAL_STATUSES

    @property
    def reached_a_machine(self) -> bool:
        """Whether anything about this step could have touched the device."""
        return self.status in (
            StepStatus.DISPATCHED,
            StepStatus.VERIFIED,
            StepStatus.FAILED,
            StepStatus.UNKNOWN,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "capability": self.capability,
            "described_as": self.described_as,
            "status": self.status.value,
            "action_id": self.action_id,
            "risk": self.selection.risk,
            "approval_requirement": self.selection.approval_requirement,
            "refusal_category": self.refusal_category,
            "refusal_reason": self.refusal_reason,
            "verification": self.verification.as_dict() if self.verification else None,
            "recoveries": list(self.recoveries),
            "attempts": self.attempts,
        }


@dataclass
class Plan:
    """One task's whole governed plan."""

    task_id: str
    tenant_id: str
    device_id: str
    requested_by: str
    instruction: str
    steps: list[PlanStep] = field(default_factory=list)
    status: TaskStatus = TaskStatus.IN_PROGRESS
    #: The question raised instead of acting, when there is one.
    clarification: str | None = None
    #: The `awaiting_response` entry the clarification was opened as.
    clarification_entry_id: int | None = None
    #: Narrative context from admitted evidence. Read by the explanation; never
    #: read when a capability, parameter, device or approval is decided.
    notes: list[str] = field(default_factory=list)
    #: Admitted cautionary evidence. Consulted by `recovery.py`, and only ever
    #: to make it stop and ask rather than continue.
    cautions: list[str] = field(default_factory=list)
    #: Evidence that was recalled and refused, with the reason, so the
    #: explanation can say so rather than silently omitting it.
    evidence_refused: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def step(self, index: int) -> PlanStep | None:
        for candidate in self.steps:
            if candidate.index == index:
                return candidate
        return None

    def step_for_action(self, action_id: str) -> PlanStep | None:
        for candidate in self.steps:
            if candidate.action_id and candidate.action_id == action_id:
                return candidate
        return None

    def touch(self) -> None:
        self.updated_at = _now()

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "tenant_id": self.tenant_id,
            "device_id": self.device_id,
            "requested_by": self.requested_by,
            "instruction": self.instruction,
            "status": self.status.value,
            "clarification": self.clarification,
            "clarification_entry_id": self.clarification_entry_id,
            "notes": list(self.notes),
            "cautions": list(self.cautions),
            "evidence_refused": list(self.evidence_refused),
            "steps": [s.as_dict() for s in self.steps],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def new_task_id() -> str:
    return f"exec-{uuid.uuid4().hex[:16]}"


def build_plan(
    *,
    intent: TaskIntent,
    tenant_id: str,
    device_id: str,
    requested_by: str,
    device: Any,
    evidence: AdmittedEvidence | None = None,
    task_id: str | None = None,
) -> Plan:
    """Turn a recognised intent into a plan against one enrolled device.

    Every step is resolved through `select_capability`; a step the device does
    not declare is `BLOCKED` here, with the selection's own reason, rather than
    proposed and refused later. Both are refusals; refusing here means the
    person is told *why* in terms of their machine's enrolment rather than in
    terms of an envelope error category.

    Evidence contributes `notes` and `cautions` and nothing else. There is no
    parameter, capability, device or approval on this function's output that
    was read from an evidence record --- which is the property
    `tests/test_w03b_evidence.py` proves by substituting a poisoned corpus for
    a benign one and asserting the plans are identical.
    """
    admitted = evidence or AdmittedEvidence()
    plan = Plan(
        task_id=task_id or new_task_id(),
        tenant_id=tenant_id,
        device_id=device_id,
        requested_by=requested_by,
        instruction=intent.instruction,
        notes=list(admitted.notes),
        cautions=list(admitted.cautions),
        evidence_refused=[
            {"content": record.bounded_content(), "reason": why} for record, why in admitted.refused
        ],
    )
    for position, step in enumerate(intent.steps):
        plan.steps.append(_plan_step(position, step, device))
    if plan.steps and all(s.status is StepStatus.BLOCKED for s in plan.steps):
        plan.status = TaskStatus.REFUSED
    return plan


def _plan_step(position: int, step: IntentStep, device: Any) -> PlanStep:
    selection = select_capability(step.capability, device)
    planned = PlanStep(
        index=position,
        capability=step.capability,
        parameters=dict(step.parameters),
        described_as=step.described_as,
        selection=selection,
    )
    if not selection.available:
        planned.status = StepStatus.BLOCKED
        planned.refusal_category = selection.refusal_code
        planned.refusal_reason = selection.refusal_reason
    return planned


def next_actionable_step(plan: Plan) -> PlanStep | None:
    """The one step that may be proposed now, or `None`.

    **This function is the pacing rule.** It returns a step only when every
    earlier step is `VERIFIED`. An earlier step that is proposed, approved,
    dispatched, failed, unknown or blocked stops the plan here --- which means
    a caller cannot advance a plan by looping over `steps` and skipping the
    awkward one, because there is no other function that says what is next.
    """
    for step in plan.steps:
        if step.status is StepStatus.VERIFIED:
            continue
        if step.status is StepStatus.PLANNED:
            return step
        # Anything else --- awaiting authorization, dispatched, failed,
        # unknown, blocked --- is a reason the plan does not move.
        return None
    return None


def plan_status(plan: Plan) -> TaskStatus:
    """Recompute the task status from its steps. Never optimistic."""
    if plan.clarification:
        return TaskStatus.AWAITING_CLARIFICATION
    if not plan.steps:
        return TaskStatus.REFUSED
    if all(s.status is StepStatus.VERIFIED for s in plan.steps):
        return TaskStatus.COMPLETED
    if any(s.status in (StepStatus.FAILED, StepStatus.UNKNOWN) for s in plan.steps):
        return TaskStatus.STOPPED
    if all(s.status is StepStatus.BLOCKED for s in plan.steps):
        return TaskStatus.REFUSED
    if any(s.status is StepStatus.BLOCKED for s in plan.steps) and not any(
        s.status in (StepStatus.PLANNED, StepStatus.AWAITING_AUTHORIZATION, StepStatus.DISPATCHED)
        for s in plan.steps
    ):
        return TaskStatus.STOPPED
    return TaskStatus.IN_PROGRESS


__all__ = [
    "STEP_TERMINAL_STATUSES",
    "Plan",
    "PlanStep",
    "StepStatus",
    "TaskStatus",
    "build_plan",
    "new_task_id",
    "next_actionable_step",
    "plan_status",
]
