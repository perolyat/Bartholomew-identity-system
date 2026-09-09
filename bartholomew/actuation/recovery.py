"""What to do next after an action did not succeed. A policy, not a retry loop.

`result.py` insists that `failed` and `unknown` are different things. This
module is what makes that distinction *do* something: it turns a recorded
outcome into one of three next steps, so a caller -- the executive (W03-B), an
operator surface, or a person reading the row -- acts on the difference instead
of re-deriving it.

Three outcomes, and the boundaries between them are the whole content
-------------------------------------------------------------------

* **`NONE`** -- nothing to do. The action succeeded, or it was withdrawn.
* **`RETRY_ELIGIBLE`** -- a fresh attempt is *permitted to be proposed*. Two
  conditions, both required and neither sufficient: the capability is declared
  **idempotent**, so running it again cannot compound an effect that may
  already have landed; and the failure was the machine not cooperating rather
  than governance saying no.
* **`SURFACE`** -- a person is told. Everything else lands here, including
  every governance refusal and every `unknown` on a non-repeatable action.

`RETRY_ELIGIBLE` never means "retried"
--------------------------------------
This module returns advice and holds no ability to act on it, and that is
structural rather than stylistic. A retry is a **new action**: a new request
through the whole envelope, a new parameter fingerprint, and a new approval
bound to it. The store makes that unavoidable -- a terminal action has no
transition out of it, so nothing can re-lease the row this plan describes --
and `requires_new_approval` is `True` on every retry-eligible plan to say so at
the surface as well as in the state machine. There is no code path in this
package that consumes a `RecoveryPlan` and dispatches anything.

Why `unknown` is not simply retried
-----------------------------------
`unknown` means the effect may or may not have landed. Retrying a
non-repeatable action in that state is how one message gets sent twice and one
file gets opened twice, and the honest answer -- "I do not know whether this
happened; here is what I tried" -- is more useful than a second attempt that
might double it. For an *idempotent* capability the same uncertainty is
harmless by definition, which is exactly what declaring it idempotent asserts,
so those are retry-eligible.

Why an abort is never retry-eligible
------------------------------------
`aborted_by_brake` means somebody engaged a halt. Proposing a fresh attempt
because a safety control fired is the loop a safety control exists to break, so
an abort always surfaces to a person however idempotent the capability is.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .result import ActionResultStatus, ErrorCategory

#: Failure categories that are the machine not cooperating, rather than
#: Governance declining. Only these can make an outcome retry-eligible, and
#: only alongside an idempotent capability.
#:
#: Deliberately an allowlist. A denylist of "governance categories" would make
#: every category added later retryable by default, which is the wrong default
#: for a list whose next entry might be a new refusal.
TRANSIENT_CATEGORIES: frozenset[ErrorCategory] = frozenset(
    {
        ErrorCategory.OS_CALL_FAILED,
        ErrorCategory.TARGET_NOT_FOUND,
        ErrorCategory.TARGET_AMBIGUOUS,
        ErrorCategory.TIMED_OUT,
        ErrorCategory.ACCESSIBILITY_UNAVAILABLE,
        ErrorCategory.EFFECT_UNVERIFIABLE,
        ErrorCategory.INTERNAL_ERROR,
    },
)

#: Categories that mean governance, policy or content refused the action. A
#: refusal is never a transient, and re-proposing the same thing after one is
#: how a refusal becomes a rate limit.
REFUSAL_CATEGORIES: frozenset[ErrorCategory] = frozenset(
    {
        ErrorCategory.GOVERNANCE_DENIED,
        ErrorCategory.PARKING_BRAKE,
        ErrorCategory.APPROVAL_MISSING,
        ErrorCategory.APPROVAL_INVALID,
        ErrorCategory.REPLAY_REFUSED,
        ErrorCategory.DEVICE_NOT_ENROLLED,
        ErrorCategory.CAPABILITY_NOT_DECLARED,
        ErrorCategory.CAPABILITY_UNSUPPORTED,
        ErrorCategory.PARAMETERS_INVALID,
        ErrorCategory.PERMISSION_DENIED,
        ErrorCategory.SENSITIVE_CONTENT,
        ErrorCategory.SENSITIVE_FIELD,
        ErrorCategory.PLATFORM_UNSUPPORTED,
        ErrorCategory.EXPIRED,
        ErrorCategory.CANCELLED,
    },
)

#: The repeatability value that permits a second attempt at all. Matches
#: `request.Repeatability.IDEMPOTENT`, compared as a string so this module
#: needs nothing from the request envelope.
IDEMPOTENT = "idempotent"


class RecoveryOutcome(str, Enum):
    """The three next steps. Closed, so a caller can exhaust them."""

    NONE = "none"
    RETRY_ELIGIBLE = "retry_eligible"
    SURFACE = "surface"


@dataclass(frozen=True)
class RecoveryPlan:
    """What follows from one recorded outcome."""

    outcome: RecoveryOutcome
    reason: str
    #: Always `True` for a retry-eligible plan. A retry is a new action through
    #: the whole envelope; there is no re-authorisation shortcut, and saying so
    #: in the payload keeps a client from inventing one.
    requires_new_approval: bool = False

    @property
    def retryable(self) -> bool:
        return self.outcome is RecoveryOutcome.RETRY_ELIGIBLE

    def as_dict(self) -> dict[str, object]:
        return {
            "outcome": self.outcome.value,
            "reason": self.reason,
            "requires_new_approval": self.requires_new_approval,
        }


def plan_recovery(
    *,
    status: ActionResultStatus,
    error_category: ErrorCategory | None = None,
    repeatability: str = "non_repeatable",
) -> RecoveryPlan:
    """Turn one outcome into its defined next step.

    Pure: it reads no database, holds no state, and its answer depends only on
    the three facts passed in. That makes it the same answer wherever it is
    asked -- the result endpoint, the action read, a later audit -- rather than
    a judgement each surface makes for itself.
    """
    idempotent = str(repeatability or "").strip().lower() == IDEMPOTENT

    if status in (ActionResultStatus.SUCCEEDED, ActionResultStatus.ACCEPTED):
        return RecoveryPlan(RecoveryOutcome.NONE, "the action succeeded; there is nothing to do")
    if status is ActionResultStatus.STARTED:
        return RecoveryPlan(
            RecoveryOutcome.NONE,
            "the action is still running; its outcome is still awaited",
        )
    if status is ActionResultStatus.CANCELLED:
        return RecoveryPlan(
            RecoveryOutcome.NONE,
            "the action was withdrawn; a withdrawal is a decision, not a fault to recover from",
        )
    if status is ActionResultStatus.ABORTED_BY_BRAKE:
        return RecoveryPlan(
            RecoveryOutcome.SURFACE,
            (
                "a parking brake stopped this action after it was leased. Nothing is "
                "proposed again while a halt is the reason something stopped; a person "
                "decides what happens next"
            ),
        )
    if status is ActionResultStatus.REFUSED:
        return RecoveryPlan(
            RecoveryOutcome.SURFACE,
            "governance refused this action; re-proposing it unchanged would be refused again",
        )

    # `failed` and `unknown`: the two the criterion is about, and they differ
    # only in what an idempotent retry would be recovering from.
    if error_category in REFUSAL_CATEGORIES:
        return RecoveryPlan(
            RecoveryOutcome.SURFACE,
            (
                f"the action ended {status.value} because of {error_category.value}, "
                "which is a refusal rather than a machine that did not cooperate"
            ),
        )
    if not idempotent:
        return RecoveryPlan(
            RecoveryOutcome.SURFACE,
            (
                f"the action ended {status.value} and is not declared idempotent, so a "
                "second attempt could compound an effect that may already have landed"
            ),
        )
    if error_category is not None and error_category not in TRANSIENT_CATEGORIES:
        return RecoveryPlan(
            RecoveryOutcome.SURFACE,
            (
                f"{error_category.value} is not a category this policy treats as "
                "transient, so a retry is not proposed for it"
            ),
        )
    return RecoveryPlan(
        RecoveryOutcome.RETRY_ELIGIBLE,
        (
            f"the action ended {status.value} on an idempotent capability, so a fresh "
            "attempt may be proposed. It is a new action: a new request through the "
            "whole envelope, a new parameter fingerprint, and a new approval bound to "
            "it. Nothing re-runs the action this plan describes"
        ),
        requires_new_approval=True,
    )


__all__ = [
    "IDEMPOTENT",
    "REFUSAL_CATEGORIES",
    "TRANSIENT_CATEGORIES",
    "RecoveryOutcome",
    "RecoveryPlan",
    "plan_recovery",
]
