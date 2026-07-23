"""
Model selection policy engine
=============================
Selects which model to use for a given *task type*, driven by `Identity.yaml`'s
`meta.deployment_profile.model_policies.selection.by_task_type` (plus budget and
availability filtering). This is Identity-policy-driven model *selection*: "for a
'code' task, which model does this Identity prefer, and with what parameters?"
It is the authoritative owner of that concept -- this module is the only code in
the repo that reads `by_task_type`.

NOT a duplicate of `identity_interpreter.orchestrator.model_router.ModelRouter`
(despite the shared filename): that class is a different concern -- runtime
*backend routing* + generation. It maps a `backend` hint to a hardcoded backend
config and actually calls the LLM; it never reads `Identity.yaml`'s
`by_task_type`. The 2026-07-21 audit (MASTER_PLAN.md item 11.1) briefly labeled
these two a single "model routing" duplicate pair; item 11.15 corrected that --
selection and routing answer different questions and neither subsumes the other,
so this module is not deprecated.

Known gap (tracked, not a duplicate): the live runtime path
(`Orchestrator.route_model()` -> `ModelRouter`) does not currently consult this
task-type selection policy at all -- only this module's callers (CLI `explain`,
standalone `chat.py`) honor it. Teaching the live router to respect it is future
work (MASTER_PLAN.md item 11.15), not a deletion.
"""

from ..models import Decision, Identity
from ..normalizer import get_available_models, get_model_parameters


def select_model(
    identity: Identity,
    task_type: str = "general",
    budget_exhausted: bool = False,
    required_context_window: int | None = None,
) -> Decision:
    """
    Select appropriate model for task

    Args:
        identity: Identity configuration
        task_type: Type of task (general, code, safety_review, etc.)
        budget_exhausted: Whether budget has been exhausted
        required_context_window: Minimum required context window

    Returns:
        Decision with selected model and rationale
    """
    rationale = []

    # Get task-specific model preferences
    selection = identity.meta.deployment_profile.model_policies.selection
    by_task = selection.get("by_task_type", {})

    if task_type in by_task:
        candidates = by_task[task_type]
        rationale.append(
            f"meta.deployment_profile.model_policies.selection.by_task_type.{task_type}",
        )
    else:
        candidates = by_task.get("general", [])
        rationale.append(
            "meta.deployment_profile.model_policies.selection.by_task_type.general (fallback)",
        )

    # Filter by budget availability
    available = get_available_models(identity, budget_exhausted)
    if budget_exhausted:
        rationale.append(
            "meta.deployment_profile.budgets.low_balance_behavior",
        )

    # Filter candidates by availability
    filtered_candidates = []
    for candidate in candidates:
        # Check if cloud optional marker
        is_cloud = "(cloud_optional)" in candidate
        model_name = candidate.replace(" (cloud_optional)", "").strip()

        if is_cloud and model_name in available["cloud"]:
            filtered_candidates.append(model_name)
        elif not is_cloud and model_name in available["local"]:
            filtered_candidates.append(model_name)

    # If no candidates remain, use local primary
    if not filtered_candidates:
        selected = identity.meta.deployment_profile.models.local_primary
        rationale.append(
            "meta.deployment_profile.models.local_primary (fallback)",
        )
    else:
        selected = filtered_candidates[0]

    # Get effective parameters
    params = get_model_parameters(identity, selected)
    rationale.append(
        "meta.deployment_profile.model_policies.parameters",
    )

    # Check context window requirements
    if required_context_window:
        model_context = params.get("max_context_window", 8192)
        if required_context_window > model_context:
            rationale.append(
                f"Warning: Required context ({required_context_window}) "
                f"exceeds model capacity ({model_context})",
            )

    return Decision(
        decision={
            "model": selected,
            "parameters": params,
            "budget_mode": identity.meta.deployment_profile.budget_mode,
        },
        rationale=rationale,
    )


def get_task_type_models(identity: Identity) -> dict:
    """
    Get all task type to model mappings

    Returns:
        Dict mapping task types to model lists
    """
    selection = identity.meta.deployment_profile.model_policies.selection
    return selection.get("by_task_type", {})
