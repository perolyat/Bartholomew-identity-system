"""
Normalizer for Identity configuration
Computes derived values and applies defaults
"""

import re
from typing import Any

from .models import Identity


def normalize_identity(identity: Identity) -> Identity:
    """
    Normalize identity by computing derived values

    - Resolves dynamic_resize expressions
    - Applies model parameter overlays
    - Computes budget-driven model availability

    Returns:
        Identity with derived values applied
    """
    # Clone to avoid mutation
    data = identity.model_dump()

    # Resolve working_memory.dynamic_resize
    dynamic_resize = data["identity"]["self_model"]["working_memory"].get(
        "dynamic_resize",
    )
    if dynamic_resize and "match_model_context_window" in dynamic_resize:
        # Extract multiplier from expression like "match_model_context_window * 0.75"
        match = re.search(r"\*\s*([\d.]+)", dynamic_resize)
        if match:
            multiplier = float(match.group(1))

            # Get primary model context window
            primary_model = data["meta"]["deployment_profile"]["models"]["local_primary"]
            per_model_params = data["meta"]["deployment_profile"]["model_policies"][
                "parameters"
            ].get("per_model", {})

            if primary_model in per_model_params:
                context_window = per_model_params[primary_model].get("max_context_window")
                if context_window:
                    computed_size = int(context_window * multiplier)
                    data["identity"]["self_model"]["working_memory"][
                        "size_tokens_target"
                    ] = computed_size

    return Identity(**data)


def get_model_parameters(
    identity: Identity,
    model_name: str,
) -> dict[str, Any]:
    """
    Get effective parameters for a model
    Applies defaults then per-model overlays
    """
    params = identity.meta.deployment_profile.model_policies.parameters

    # Start with defaults
    effective_params = params.get("default", {}).copy()

    # Apply per-model overrides
    per_model = params.get("per_model", {})
    if model_name in per_model:
        effective_params.update(per_model[model_name])

    return effective_params


def get_available_models(
    identity: Identity,
    budget_exhausted: bool = False,
) -> dict[str, list]:
    """
    Get available models based on budget state

    Returns:
        Dict with 'local' and 'cloud' model lists
    """
    models = identity.meta.deployment_profile.models
    available = {
        "local": [models.local_primary] + models.local_fallbacks,
        "cloud": [],
    }

    # Check budget behavior
    low_balance = identity.meta.deployment_profile.budgets.low_balance_behavior

    if not budget_exhausted or low_balance != "force-local":
        available["cloud"] = models.cloud_optional

    return available
