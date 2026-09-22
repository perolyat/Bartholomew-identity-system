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
from bartholomew.kernel.redaction_engine import redact_pii
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
    # No wording for IN_PROGRESS here: what it is true to say about approval
    # depends on the steps, so `_in_progress_phrase` reads them. The constant
    # this replaced --- "Nothing runs without your approval." --- was the same
    # false blanket claim as the deliberation account's, one level up, and was
    # printed on every plan including those whose device holds trusted autonomy.
    TaskStatus.COMPLETED: "Every step was carried out and verified.",
    TaskStatus.STOPPED: "Stopped short of finishing.",
    TaskStatus.REFUSED: "I did not act on this.",
}


#: The one approval requirement that no enrolment can waive --- "never eligible
#: for trusted autonomy, at any configuration".
#:
#: `EnrolledDevice.__post_init__` enforces this at construction, so a real
#: enrolled device cannot reach here carrying both. But `select_capability`
#: takes "an `EnrolledDevice` (or anything with the same `declares` /
#: `declared_version` / `autonomous_for` surface)", and `autonomous_for` on its
#: own answers only "is this kind in the trusted set". A device source that is
#: not `EnrolledDevice` --- a future one, a test double --- would therefore have
#: the account tell the person an `always` step is eligible to run unattended,
#: when the envelope will still stop it for an approval: a true approval
#: requirement, concealed, by a sentence nobody would think to re-check.
#:
#: Asking both questions costs one comparison and means the claim is derived
#: from the capability's own governance facts rather than from one device
#: object's honesty.
_APPROVAL_NEVER_WAIVABLE = "always"


def _runs_without_further_approval(step: PlanStep) -> bool:
    """Whether this step is genuinely eligible to run with no further approval.

    Two conditions, and both are governance state rather than wording: this
    device's enrolment grants trusted autonomy for the capability, **and** the
    capability's own descriptor admits autonomy at all.
    """
    return bool(
        step.selection.device_autonomous
        and step.selection.approval_requirement != _APPROVAL_NEVER_WAIVABLE,
    )


def _awaits_your_approval(step: PlanStep) -> bool:
    """Whether the person's approval is what this step is actually waiting on.

    A step that has already run, failed, ended unknown or been blocked is
    waiting on nothing; a step eligible for trusted autonomy is not waiting on
    *them*. This is the predicate the account's approval sentences are built
    from, so that the sentence and the per-step lines cannot disagree.
    """
    if step.status is StepStatus.AWAITING_AUTHORIZATION:
        return not _runs_without_further_approval(step)
    if step.status is StepStatus.PLANNED:
        # Not yet proposed. It will need an approval when it is, unless this
        # device's enrolment says otherwise.
        return not _runs_without_further_approval(step)
    return False


def _step_line(step: PlanStep) -> str:
    phrase = _STATUS_PHRASES[step.status]
    if step.status is StepStatus.AWAITING_AUTHORIZATION and _runs_without_further_approval(step):
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


def _in_progress_phrase(plan: Plan) -> str:
    """ "In progress", and then only what the governance state actually supports.

    The approval half is conditional because it is a claim about this plan and
    not a slogan: it is said when some step is genuinely waiting on the person,
    and replaced when the device's enrolment means a step is not.
    """
    awaiting = [s for s in plan.steps if _awaits_your_approval(s)]
    autonomous = [
        s
        for s in plan.steps
        if not s.terminal
        and s.status is not StepStatus.DISPATCHED
        and _runs_without_further_approval(s)
    ]
    if awaiting and autonomous:
        return (
            "In progress. Some of what is left needs your approval; some of it this "
            "device is enrolled to run without asking you again."
        )
    if awaiting:
        return "In progress. Nothing that is left runs without your approval."
    if autonomous:
        return (
            "In progress. What is left does not need a further approval from you --- "
            "this device is enrolled with trusted autonomy for it."
        )
    return "In progress."


def explain_task(plan: Plan, *, reflections: list[Any] | None = None) -> str:
    """One human-readable account of one task attempt. Never fabricates success."""
    lines: list[str] = [
        f"You asked: {plan.instruction!r}" if plan.instruction else "You asked me to do something.",
    ]
    if plan.status is TaskStatus.IN_PROGRESS:
        lines.append(_in_progress_phrase(plan))
    else:
        lines.append(_TASK_PHRASES.get(plan.status, "Status unclear."))

    if plan.clarification:
        lines.append(f"What I need to know: {plan.clarification}")

    if plan.steps:
        lines.append("What I proposed, and what became of it:")
        lines.extend(f"  {_step_line(step)}" for step in plan.steps)
    else:
        lines.append("I proposed nothing, so nothing was sent to your computer.")

    pending = [
        s
        for s in plan.steps
        if s.status is StepStatus.AWAITING_AUTHORIZATION and _awaits_your_approval(s)
    ]
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

    reasoning = _deliberation_account(plan)
    if reasoning:
        lines.extend(reasoning)

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


#: How many inferred sub-goals the account lists. A bound on prose, not on
#: reasoning: the whole deliberation is in the Reflection's details either way.
MAX_REASONING_LINES = 6


def _inferred_step_approval_sentence(plan: Plan) -> str:
    """What is *actually* true about approval for the steps the executive inferred.

    This sentence used to be a constant: "every one of them still needs your
    approval." It was reassuring, it was the shape a safety sentence is supposed
    to have, and on a plan whose steps had already run, or whose device holds
    trusted autonomy for them, it was false --- while the per-step lines two
    lines below said the opposite. An account that contradicts itself teaches
    the person to read neither half.

    So it is derived, from the same `PlanStep` and `CapabilitySelection` state
    the step lines are derived from, and it is allowed to be several sentences
    or none. Both directions are held: it never claims an approval is required
    where the governance state says it is not, and it never omits one where the
    state says it is.
    """
    if not plan.steps:
        return ""

    awaiting = [s for s in plan.steps if _awaits_your_approval(s)]
    autonomous = [
        s
        for s in plan.steps
        if not s.terminal
        and s.status is not StepStatus.DISPATCHED
        and _runs_without_further_approval(s)
    ]
    finished = [s for s in plan.steps if s.reached_a_machine]
    blocked = [s for s in plan.steps if s.status is StepStatus.BLOCKED]
    total = len(plan.steps)

    clauses = [
        clause
        for clause in (
            _clause(len(awaiting), total, "still needs", "still need", "your approval"),
            _clause(
                len(autonomous),
                total,
                "will run",
                "will run",
                "without asking you again --- this device is enrolled with trusted autonomy for it",
            ),
            _clause(
                len(finished),
                total,
                "has",
                "have",
                "already been sent to your computer, so approving it again is not "
                "something I am waiting for",
            ),
            _clause(len(blocked), total, "was", "were", "never proposed at all"),
        )
        if clause
    ]

    if not clauses:
        return ""
    if total == 1:
        # One step: "Of that step, that step ..." reads as a stutter, so the
        # clause is the whole sentence.
        sentence = "; and ".join(clauses)
        return sentence[0].upper() + sentence[1:] + "."
    if len(clauses) == 1:
        return f"Of these steps, {clauses[0]}."
    return "Of these steps, " + "; ".join(clauses[:-1]) + f"; and {clauses[-1]}."


def _clause(n: int, total: int, singular: str, plural: str, rest: str) -> str:
    """One clause of the approval sentence, or `""` when it is about no steps.

    Counts are plain and never rounded, and the verb agrees with the count,
    because a sentence a person has to squint at is one they stop reading ---
    and this particular sentence is the one they decide on.
    """
    if n == 0:
        return ""
    if total == 1:
        return f"that step {singular} {rest}"
    if n == total:
        return f"all of them {plural} {rest}"
    if n == 1:
        return f"one of them {singular} {rest}"
    return f"{n} of them {plural} {rest}"


def _deliberation_account(plan: Plan) -> list[str]:
    """What the person is told about reasoning the executive did on their behalf.

    A step the person did not ask for in so many words is a step they are owed
    an account of *before* they approve it. Said plainly, and said as reasoning
    rather than as fact: "I took you to mean" is the honest verb for a reading
    of an outcome, and it invites the correction that "you asked me to" would
    not.

    What it says about approval is read off the plan; see
    `_inferred_step_approval_sentence`.
    """
    record = plan.deliberation or {}
    if not record.get("used"):
        return []
    deliberation = record.get("deliberation") or {}
    lines = [
        "You described what you wanted rather than the steps, so I worked the steps "
        "out. Nothing here is something you named.",
    ]
    approval = _inferred_step_approval_sentence(plan)
    if approval:
        lines.append(f"  {approval}")
    objective = deliberation.get("objective")
    if objective:
        lines.append(f"  I took you to mean: {objective}")
    situation = deliberation.get("situation")
    if situation:
        lines.append(f"  What I assumed about right now: {situation}")
    sub_goals = [g for g in (deliberation.get("sub_goals") or []) if g][:MAX_REASONING_LINES]
    if sub_goals:
        lines.append("  To get there I judged these necessary: " + "; ".join(sub_goals))
    return lines


def _redacted(value: Any) -> Any:
    """`value` with every string inside it put through `redact_pii`, at any depth.

    `ActionReflection.to_memory_row` redacts the *top-level* string values of
    `details` and then spreads them into `meta`. A nested structure therefore
    passes through untouched, and the deliberation record is nested --- so a
    model that echoed an address or a passphrase out of the person's own
    instruction wrote it to the audit row in clear.

    Redacting here rather than widening the Reflection's own rule keeps the
    change local to the thing that introduced the nesting.
    """
    if isinstance(value, str):
        return redact_pii(value)
    if isinstance(value, dict):
        return {key: _redacted(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redacted(item) for item in value]
    return value


def explanation_details(plan: Plan) -> dict[str, Any]:
    """The same account in structured form, for a Reflection's `details`.

    `deliberation` is included whenever the executive reasoned its way to the
    steps. This is the durable provenance of a cognition decision: the
    Reflection is the audit trail, and a reviewer asking "did a model
    contribute to this proposal, and what did it actually say?" answers it from
    here rather than from a log line.
    """
    return {
        "task_id": plan.task_id,
        "status": plan.status.value,
        "deliberation": _redacted(plan.deliberation),
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


__all__ = [
    "MAX_REASONING_LINES",
    "MAX_REFLECTION_LINES",
    "explain_task",
    "explanation_details",
    "load_task_reflections",
]
