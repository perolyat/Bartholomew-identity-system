"""What to do when a step fails, or when nobody can say whether it did.

There is exactly one thing this module will not produce, and its absence is the
point: **a blind retry.** There is no decision value that means "do it again",
and no branch that re-proposes an action without either a fresh human
authorization or an explicit stop-and-ask first. That is not caution for its
own sake --- W03-B's escalation boundary names an autonomous retry loop acting
without fresh authorization for a `sensitive` or `always`-approval capability
as a reason to stop and report rather than build.

Four decisions, and the difference between the middle two is the whole design:

* `STOP_AND_REPORT` --- the task ends here and the person is told what stands.
* `ASK_FOR_CLARIFICATION` --- the executive raises an `awaiting_response`
  obligation. Used whenever what happened is genuinely unclear, or when
  cautionary evidence says a previous attempt at something like this went
  wrong.
* `RE_PROPOSE` --- a *new* `ActionRequest` may be built for the same step. It
  carries `requires_new_authorization=True` always, because a re-proposal
  changes the parameter fingerprint and a prior approval cannot authorize it
  (W03-C's approval binding). Offered only for a plainly-failed step, on a
  capability whose approval requirement is not `always`, within a bound.
* `ABANDON_STEP` --- the step is left as it is and the plan stops.

An `unknown` never re-proposes. Repeating an action that may already have
happened is how one approval gets spent twice, and it is exactly the case the
envelope's own one-lease guard exists to prevent on the other side.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .verification import FAILED, UNKNOWN

STOP_AND_REPORT = "stop_and_report"
ASK_FOR_CLARIFICATION = "ask_for_clarification"
RE_PROPOSE = "re_propose_with_new_authorization"
ABANDON_STEP = "abandon_step"

#: How many times one step may be proposed in total, re-proposals included.
#: A bound, not a policy: past it the person is asked rather than the machine
#: being asked again.
MAX_STEP_ATTEMPTS = 2

#: Approval requirements that never re-propose automatically. `always` is the
#: envelope's own marker for the three capabilities that read or synthesise
#: content on a person's behalf, and re-proposing one of those without being
#: asked to is precisely the autonomy this wave defers.
NO_AUTOMATIC_REPROPOSAL = frozenset({"always"})


@dataclass(frozen=True)
class RecoveryDecision:
    """One defined next move. Never "retry", never silent."""

    decision: str
    reason: str
    #: True whenever acting on this decision would need a human to authorize
    #: something. `RE_PROPOSE` always sets it: a rebuilt request has a new
    #: fingerprint, and the earlier approval does not carry over.
    requires_new_authorization: bool = False
    #: The question to raise, when the decision is to ask.
    question: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "requires_new_authorization": self.requires_new_authorization,
            "question": self.question,
        }


def decide_recovery(
    *,
    verdict: str,
    capability: str,
    described_as: str,
    approval_requirement: str | None,
    attempts: int,
    cautions: tuple[str, ...] | list[str] = (),
    read_back_code: str | None = None,
) -> RecoveryDecision:
    """The defined next move for a step that did not verify.

    `verdict` is `verification.FAILED` or `verification.UNKNOWN`. A verified
    step never reaches here.
    """
    approval = (approval_requirement or "").strip().lower()

    if verdict == UNKNOWN:
        # The honest case, and the one a retry loop gets wrong. Something may
        # have happened on the person's machine. Nothing is repeated on the
        # strength of not knowing.
        detail = (
            f"the read-back could not settle it ({read_back_code})"
            if read_back_code
            else "no read-back established the resulting state"
        )
        return RecoveryDecision(
            decision=ASK_FOR_CLARIFICATION,
            reason=(
                f"{described_as}: it cannot be established whether this happened --- "
                f"{detail}. Repeating an action that may already have run is refused."
            ),
            requires_new_authorization=True,
            question=(
                f"I proposed to {described_as}, and I cannot tell whether it happened. "
                "Could you look and tell me? I will not repeat it on a guess."
            ),
        )

    if verdict != FAILED:  # pragma: no cover - callers pass one of the two
        return RecoveryDecision(
            decision=STOP_AND_REPORT,
            reason=f"no recovery is defined for verdict {verdict!r}",
        )

    if cautions:
        return RecoveryDecision(
            decision=ASK_FOR_CLARIFICATION,
            reason=(
                f"{described_as} failed, and recalled evidence cautions against this "
                "kind of attempt. Evidence cannot authorize a retry, but it can be a "
                "reason to ask first, and here it is."
            ),
            requires_new_authorization=True,
            question=(
                f"{described_as} failed. Something similar has gone wrong before. "
                "Do you want me to propose it again?"
            ),
        )

    if approval in NO_AUTOMATIC_REPROPOSAL:
        return RecoveryDecision(
            decision=STOP_AND_REPORT,
            reason=(
                f"{described_as} failed, and {capability} requires an explicit approval "
                "for every attempt. The executive does not re-propose one on its own; "
                "ask again and it will be proposed afresh for your approval."
            ),
            requires_new_authorization=True,
        )

    if attempts >= MAX_STEP_ATTEMPTS:
        return RecoveryDecision(
            decision=ABANDON_STEP,
            reason=(
                f"{described_as} failed {attempts} times, which is the bound. The plan "
                "stops here rather than continuing to ask the machine."
            ),
        )

    return RecoveryDecision(
        decision=RE_PROPOSE,
        reason=(
            f"{described_as} failed plainly and {capability} may be proposed again. A "
            "fresh request is built, and it needs its own authorization: rebuilding "
            "changes the parameter fingerprint, so the earlier approval does not "
            "authorize it."
        ),
        requires_new_authorization=True,
    )


__all__ = [
    "ABANDON_STEP",
    "ASK_FOR_CLARIFICATION",
    "MAX_STEP_ATTEMPTS",
    "NO_AUTOMATIC_REPROPOSAL",
    "RE_PROPOSE",
    "STOP_AND_REPORT",
    "RecoveryDecision",
    "decide_recovery",
]
