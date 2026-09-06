"""The account the person reads: proposed, authorized, executed, verified.

Every task attempt produces one of these, including the attempts that produced
nothing --- a refusal, a question, a plan whose first step is still waiting on
an approval. That is deliberate: an executive that only explains itself when it
succeeded is an executive whose silence means "something went wrong and you get
to guess what".

The four columns are the four different things, and they are never collapsed:

* **proposed** --- an `ActionRequest` exists and the envelope admitted it.
* **authorized** --- a human approved that exact action at the host boundary.
* **executed** --- the device leased it and reported an outcome.
* **verified** --- the resulting Windows state was read back and matched.

The rule the wording holds to is that **a claim is only made where evidence
supports it.** A step the device called `succeeded` with nothing read back is
written as "reported succeeded; not verified", not as "done". There is no
sentence this module can produce that says a thing happened on the strength of
the request having been made.

Sourcing. The account is built from the plan's own step rows --- which are
written from the envelope's `ActionSeamResult`s --- and from the
`ActionReflection` records this package and the actuation seam wrote for the
same decisions. Both are the repository's existing provenance; nothing here
invents a second audit trail.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from bartholomew.kernel.db_ctx import wal_db
from bartholomew.kernel.reflection import REFLECTION_KIND

from .plan import Plan, PlanStep, StepStatus, TaskStatus
from .verification import FAILED, UNKNOWN, VERIFIED

#: How many `ActionReflection` rows one account may quote. A bound: an
#: explanation is an account, not a log dump.
MAX_REFLECTION_LINES = 12

#: What each step status is called in the account. Plain words, and none of
#: them is a synonym for success that is not one.
_STATUS_PHRASES = {
    StepStatus.PLANNED: "planned, not yet proposed",
    StepStatus.AWAITING_AUTHORIZATION: "proposed and waiting for your approval --- nothing has run",
    StepStatus.DISPATCHED: "approved and handed to the device; no outcome recorded yet",
    StepStatus.VERIFIED: "done, and verified by reading the machine's state back",
    StepStatus.FAILED: "failed",
    StepStatus.UNKNOWN: "issued, and I cannot establish whether it happened",
    StepStatus.BLOCKED: "not proposed",
}

_TASK_PHRASES = {
    TaskStatus.AWAITING_CLARIFICATION: "I did not act. I need an answer first.",
    TaskStatus.IN_PROGRESS: "In progress. Nothing runs without your approval.",
    TaskStatus.COMPLETED: "Every step was carried out and verified.",
    TaskStatus.STOPPED: "Stopped short of finishing.",
    TaskStatus.REFUSED: "I did not act on this.",
}


def _step_line(step: PlanStep) -> str:
    phrase = _STATUS_PHRASES[step.status]
    if step.status is StepStatus.AWAITING_AUTHORIZATION and step.selection.device_autonomous:
        # Truthfulness beats a reassuring default. This device's enrolment
        # grants configured trusted autonomy for this capability, so telling
        # the person it is waiting for their approval would be false. It is
        # still true that nothing has run: the device has not leased it yet,
        # and every gate runs again when it does.
        phrase = (
            "proposed; this device is enrolled with trusted autonomy for this "
            "capability, so it is eligible to run without a further approval --- "
            "nothing has run yet"
        )
    parts = [f"{step.index + 1}. {step.described_as} --- {phrase}"]
    if step.action_id:
        parts.append(f"(action {step.action_id})")
    if step.refusal_reason:
        parts.append(f"Reason: {step.refusal_reason}")
    if step.verification is not None:
        verdict = step.verification.verdict
        if verdict == VERIFIED:
            parts.append(f"Verified: {step.verification.detail}")
        elif verdict == FAILED:
            parts.append(f"Not done: {step.verification.detail}")
        elif verdict == UNKNOWN:
            parts.append(f"Unverified: {step.verification.detail}")
    for recovery in step.recoveries:
        parts.append(f"Next: {recovery.get('reason')}")
    return " ".join(p for p in parts if p)


def explain_task(plan: Plan, *, reflections: list[Any] | None = None) -> str:
    """One human-readable account of one task attempt. Never fabricates success."""
    lines: list[str] = [
        f"You asked: {plan.instruction!r}" if plan.instruction else "You asked me to do something.",
    ]
    lines.append(_TASK_PHRASES.get(plan.status, "Status unclear."))

    if plan.clarification:
        lines.append(f"What I need to know: {plan.clarification}")

    if plan.steps:
        lines.append("What I proposed, and what became of it:")
        lines.extend(f"  {_step_line(step)}" for step in plan.steps)
    else:
        lines.append("I proposed nothing, so nothing was sent to your computer.")

    pending = [s for s in plan.steps if s.status is StepStatus.AWAITING_AUTHORIZATION]
    if pending:
        lines.append(
            "Waiting on you: "
            + ", ".join(f"action {s.action_id}" for s in pending if s.action_id)
            + ". I cannot approve these myself, and nothing runs until you do.",
        )

    unverified = [s for s in plan.steps if s.status is StepStatus.UNKNOWN]
    if unverified:
        lines.append(
            "Please note: "
            + "; ".join(f"{s.described_as} was issued but is unverified" for s in unverified)
            + ". I am not calling that success.",
        )

    if plan.notes:
        lines.append(
            "Context I recalled (evidence only --- it authorized nothing): "
            + "; ".join(plan.notes),
        )
    if plan.cautions:
        lines.append("Cautions I recalled: " + "; ".join(plan.cautions))
    if plan.evidence_refused:
        lines.append(
            "Recalled and not used: "
            + "; ".join(f"{entry.get('reason')}" for entry in plan.evidence_refused[:4]),
        )

    if reflections:
        lines.append("Recorded decisions, from the audit trail:")
        lines.extend(f"  - {line}" for line in reflections[:MAX_REFLECTION_LINES])

    return "\n".join(lines)


def load_task_reflections(db_path: str, task_id: str) -> list[str]:
    """The `ActionReflection` rows for one task, as one line each.

    This is where "sourced from `ActionReflection`" is literal rather than
    figurative: the account's audit section is read back out of the *shared*
    Reflection sink --- the same `reflections` table the actuation seam writes
    every governed decision to --- rather than reconstructed from what this
    process happens to remember. The envelope stamps `correlation_id` with the
    task id on every action it admits (see `seam._propose`), so a task's whole
    decision trail is one query, and it includes decisions this process did not
    make: an approval granted at the host boundary and a result recorded by the
    device both appear here.

    Never raises. An unreadable audit trail is a reason to say less, not a
    reason for an explanation to fail.
    """
    try:
        with wal_db(db_path, timeout=5.0, label="executive_explanation") as conn:
            conn.execute("PRAGMA busy_timeout = 3000")
            rows = conn.execute(
                "SELECT content, meta, ts FROM reflections WHERE kind = ? ORDER BY id ASC",
                (REFLECTION_KIND,),
            ).fetchall()
    except (sqlite3.Error, OSError):
        return []

    lines: list[str] = []
    for content, meta_raw, ts in rows:
        try:
            meta = json.loads(meta_raw) if isinstance(meta_raw, str) else dict(meta_raw or {})
        except (TypeError, ValueError):
            continue
        correlation = str(meta.get("correlation_id") or "")
        context = str(meta.get("context_ref") or "")
        if task_id not in (correlation, context) and meta.get("task_id") != task_id:
            continue
        surface = meta.get("surface", "?")
        outcome = meta.get("outcome", "?")
        lines.append(f"[{ts}] {surface}: {content} ({outcome})")
    return lines


def explanation_details(plan: Plan) -> dict[str, Any]:
    """The same account in structured form, for a Reflection's `details`."""
    return {
        "task_id": plan.task_id,
        "status": plan.status.value,
        "steps": [
            {
                "index": s.index,
                "capability": s.capability,
                "status": s.status.value,
                "action_id": s.action_id,
                "verified": bool(s.verification and s.verification.verified),
            }
            for s in plan.steps
        ],
        "clarification": plan.clarification,
        "evidence_admitted": len(plan.notes) + len(plan.cautions),
        "evidence_refused": len(plan.evidence_refused),
    }


__all__ = ["MAX_REFLECTION_LINES", "explain_task", "explanation_details", "load_task_reflections"]
