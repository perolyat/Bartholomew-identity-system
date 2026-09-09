"""The Executive: intention -> governed plan -> action-envelope proposal.

**Wave 3 package W03-B.** This package is the in-process path from something a
person asked for to a *proposal* travelling the one canonical action envelope,
and it is deliberately the only such path. Before it, `Planner.decide()`
returned `None`, intent recognition was single-shot skill routing, and
`bartholomew/actuation/seam.py` was imported by exactly one caller: the HTTP
route. A Windows action could therefore be created only by an external POST,
never by cognition.

What this package may reach, and what it may not
------------------------------------------------
It reaches the operating system through **one** module and no other:
`bartholomew.actuation.seam` (the `action-envelope` shared contract, owned by
W03-C). There is no import of `bartholomew.windows_actuation` anywhere under
this package, no `subprocess`, no `ctypes`, no input synthesis, and no second
channel of any kind. `tests/test_w03b_no_bypass.py` proves that structurally --
by reading this package's own syntax tree, not by trusting this paragraph.

Three postures the modules below exist to hold
----------------------------------------------
1. **It proposes; it never authorizes.** Every Windows step is an
   `ActionRequest` admitted by the envelope's eleven ordered gates, and it
   stops at `pending_approval`. Nothing in this package calls
   `grant_action_approval`: an approval is a human act at the host boundary,
   and an executive that could mint one would be an executive that could
   approve itself. See `seam.py`.
2. **Uncertainty is preserved, not resolved by guessing.** A materially
   ambiguous instruction becomes an `awaiting_response` obligation --- a
   question --- and never an action on the most likely reading. See
   `intent.py` and `seam.py`.
3. **Recalled memory is evidence, never authority.** Evidence may *narrow* a
   plan (a caution that turns a recovery into a question) or *explain* one (a
   note in the account the user reads). It may never select a capability, fill
   a parameter, widen a scope, or remove an approval. That direction ---
   restrictive or narrative only --- is what makes a poisoned memory unable to
   do anything a benign one could not. See `evidence.py`.

And one posture about truth: **issued is not succeeded.** A step advances only
when the previous step's result was observed *and* verified; a device that
reported success with nothing to read back leaves the step `unknown`, and an
`unknown` is never presented as a success. See `verification.py`,
`recovery.py` and `explanation.py`.
"""

from __future__ import annotations

from .evidence import (
    EVIDENCE_FRAME_CLOSE,
    EVIDENCE_FRAME_OPEN,
    EvidenceRecord,
    EvidenceVerdict,
    admit_evidence,
    render_evidence_for_prompt,
)
from .explanation import explain_task
from .intent import (
    Ambiguity,
    IntentStep,
    TaskIntent,
    parse_task,
)
from .plan import (
    STEP_TERMINAL_STATUSES,
    Plan,
    PlanStep,
    StepStatus,
    TaskStatus,
    build_plan,
    next_actionable_step,
)
from .recovery import RecoveryDecision, decide_recovery
from .seam import (
    EXECUTIVE_BRAKE_SCOPE,
    EXECUTIVE_KIND_ADVANCE,
    EXECUTIVE_KIND_TASK,
    ExecutiveTaskResult,
    advance_executive_task_through_runtime_contract,
    run_executive_task_through_runtime_contract,
)
from .selection import CapabilitySelection, select_capability
from .verification import Verification, verify_step

__all__ = [
    "EVIDENCE_FRAME_CLOSE",
    "EVIDENCE_FRAME_OPEN",
    "EXECUTIVE_BRAKE_SCOPE",
    "EXECUTIVE_KIND_ADVANCE",
    "EXECUTIVE_KIND_TASK",
    "STEP_TERMINAL_STATUSES",
    "Ambiguity",
    "CapabilitySelection",
    "EvidenceRecord",
    "EvidenceVerdict",
    "ExecutiveTaskResult",
    "IntentStep",
    "Plan",
    "PlanStep",
    "RecoveryDecision",
    "StepStatus",
    "TaskIntent",
    "TaskStatus",
    "Verification",
    "admit_evidence",
    "advance_executive_task_through_runtime_contract",
    "build_plan",
    "decide_recovery",
    "explain_task",
    "next_actionable_step",
    "parse_task",
    "render_evidence_for_prompt",
    "run_executive_task_through_runtime_contract",
    "select_capability",
    "verify_step",
]
