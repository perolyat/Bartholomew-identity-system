"""
Cloud LLM adapter (Anthropic).

The counterpart to `llm_stub.py`'s local Ollama adapter, for the small,
high-value slice of work `docs/VISION_AND_PERSONAL_DEPLOYMENT.md` §6
reserves for cloud: hard reasoning that a 4-bit quantised local model
handles poorly. Local remains the default and carries the ambient,
routine, and personal-fact-bearing traffic.

**Off unless explicitly configured.** No API key means no cloud backend,
and `ModelRouter` keeps selecting local. That is the privacy decision left
in the user's hands: enabling cloud is a deliberate act, because it is the
point at which personal context leaves the device for the first time.

Provider choice: Anthropic. `Identity.yaml` lists three cloud options as
`cloud_optional` ("OpenAI GPT", "Anthropic", "Google"); this implements
one, chosen because it is the one with a verified current SDK contract. The
`openai` entry in `ModelRouter`'s backend table remains an unimplemented
placeholder and continues to raise `backend_not_implemented` rather than
pretending to work -- adding a second provider is a separate, additive
change against this same seam.

The `anthropic` SDK is imported lazily and is **not** a hard dependency,
matching how `llm_stub.py` treats the optional `ollama` client. A
deployment that never enables cloud never needs the package installed.
"""

from __future__ import annotations

import os
from typing import Any

# Optional dependency, imported the same way llm_stub.py imports the
# optional `ollama` client: a deployment that never enables cloud never
# needs the package.
try:
    import anthropic

    HAS_ANTHROPIC_SDK = True
except ImportError:
    HAS_ANTHROPIC_SDK = False

# Identity.yaml names cloud providers in prose ("Anthropic"), not as model
# IDs -- the same shape as llm_stub.py's model_mapping for Ollama. Map the
# declared name to a concrete current model.
#
# Claude Opus 5 is the default: this backend exists for the work the local
# model cannot do, so the capable model is the point. Sonnet/Haiku are
# available for callers that want to trade capability for cost.
PROVIDER_MODEL_MAPPING: dict[str, str] = {
    "Anthropic": "claude-opus-5",
    "Anthropic (cloud_optional)": "claude-opus-5",
    "claude-opus-5": "claude-opus-5",
    "claude-sonnet-5": "claude-sonnet-5",
    "claude-haiku-4-5": "claude-haiku-4-5",
}

API_KEY_ENV = "ANTHROPIC_API_KEY"

# Ceiling for a single reply. On current Claude models max_tokens caps
# thinking *and* response text together, so this is deliberately generous:
# a tight value truncates mid-answer rather than merely shortening it.
DEFAULT_MAX_TOKENS = 8192


# Cloud has three distinguishable states, not two. Collapsing them was a
# real defect: `is_configured()` answered "is there a key?", and
# `ModelRouter._select_by_task_type()` used that to decide cloud-vs-local.
# With a key present but the optional `anthropic` package absent, Identity's
# cloud candidate was selected, the request reached `_route_cloud()`, and it
# failed `sdk_unavailable` -- instead of falling back to the local model
# Identity declares immediately beside it for that same task type. The
# failure was truthful but entirely avoidable, and it broke exactly the
# deployment most likely to exist in practice: a user who has pasted in an
# API key but not yet installed the SDK.
CLOUD_DISABLED = "disabled"  # user has not enabled cloud
CLOUD_READY = "ready"  # enabled and servable right now
CLOUD_UNAVAILABLE = "unavailable"  # enabled, but cannot currently serve


def is_configured() -> bool:
    """Whether the user has *enabled* cloud. No key means no cloud.

    Enablement only. This deliberately does not consider whether a request
    could actually be served -- use `is_ready()` for that.
    """
    return bool(os.getenv(API_KEY_ENV, "").strip())


def is_ready() -> bool:
    """Whether a cloud generation could actually be served right now.

    Enabled *and* the optional SDK importable. This is the predicate that
    should gate routing a request to cloud.
    """
    return is_configured() and HAS_ANTHROPIC_SDK


def readiness() -> str:
    """Cloud state as one of CLOUD_DISABLED / CLOUD_READY / CLOUD_UNAVAILABLE.

    For truthful status reporting -- a user must be able to tell "I have not
    turned cloud on" apart from "I turned it on and it cannot run", because
    the second is a fixable misconfiguration and the first is a choice.
    """
    if not is_configured():
        return CLOUD_DISABLED
    return CLOUD_READY if HAS_ANTHROPIC_SDK else CLOUD_UNAVAILABLE


def unreadiness_reason() -> str | None:
    """Why cloud is enabled but unservable, or None when that isn't the case."""
    if readiness() != CLOUD_UNAVAILABLE:
        return None
    return "sdk_unavailable"


def map_model_name(name: str) -> str | None:
    """Resolve an Identity.yaml cloud model name to a concrete model ID."""
    return PROVIDER_MODEL_MAPPING.get(name)


class CloudLLMAdapter:
    """Anthropic adapter, mirroring LLMAdapter's contract.

    Returns the same result-dict shape as `llm_stub.LLMAdapter.generate()`
    -- `response`/`tokens_used`/`model`/`success`/`error` -- so ModelRouter
    treats local and cloud identically and the honesty guarantee in
    `ModelRouter.route()` (a real backend never answers with mock text)
    holds for both without special-casing.
    """

    def __init__(self, identity_config: Any = None):
        self.identity = identity_config
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        api_key = os.getenv(API_KEY_ENV, "").strip()
        if not api_key or not HAS_ANTHROPIC_SDK:
            return None
        self._client = anthropic.Anthropic(api_key=api_key)
        return self._client

    def generate(
        self,
        prompt: str,
        model: str,
        parameters: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generate a response. Never raises for expected failures -- returns
        `success: False` with a reason, the same contract the local adapter
        uses, so callers handle both identically."""
        resolved = map_model_name(model) or model

        if not prompt or not prompt.strip():
            return self._failure(resolved, parameters, "empty_prompt", "[ERROR] Empty prompt")

        client = self._get_client()
        if client is None:
            if not is_configured():
                return self._failure(
                    resolved,
                    parameters,
                    "cloud_not_configured",
                    f"[ERROR] Cloud backend not configured -- set {API_KEY_ENV} to enable it.",
                )
            return self._failure(
                resolved,
                parameters,
                "sdk_unavailable",
                "[ERROR] The 'anthropic' package is not installed. Run: pip install anthropic",
            )

        # Sampling parameters are deliberately NOT forwarded.
        #
        # Identity.yaml's model_policies.parameters carries temperature 0.2
        # and top_p 0.9, and select_model() returns them for every model. On
        # current Claude models those parameters are removed and a request
        # carrying them is rejected outright with a 400 -- so passing the
        # Identity-declared parameters through, which is the obvious thing
        # to do and what the local adapter does, would make every cloud
        # request fail. Model behaviour is steered by the prompt instead,
        # which is where this runtime's persona and governance context
        # already live.
        max_tokens = int(parameters.get("max_tokens") or DEFAULT_MAX_TOKENS)

        try:
            message = client.messages.create(
                model=resolved,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:  # noqa: BLE001 -- classified below
            return self._failure(
                resolved,
                parameters,
                self._classify(exc),
                f"[ERROR] Cloud backend request failed: {exc}",
            )

        # A safety refusal arrives as a normal successful response with
        # stop_reason "refusal" and possibly empty content -- not an
        # exception. Reported as a truthful failure rather than an empty
        # answer, so it can never be mistaken for the model having nothing
        # to say.
        if getattr(message, "stop_reason", None) == "refusal":
            return self._failure(
                resolved,
                parameters,
                "refusal",
                "[ERROR] The cloud model declined to answer this request.",
            )

        text = "".join(
            block.text for block in message.content if getattr(block, "type", None) == "text"
        )
        if not text.strip():
            return self._failure(
                resolved,
                parameters,
                "empty_response",
                "[ERROR] Cloud backend returned no text content.",
            )

        usage = getattr(message, "usage", None)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)

        return {
            "response": text,
            "tokens_used": output_tokens,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "model": resolved,
            "parameters": parameters,
            "success": True,
        }

    @staticmethod
    def _classify(exc: Exception) -> str:
        """Map an SDK exception to a stable reason string, without importing
        the SDK at module scope (it is optional)."""
        name = type(exc).__name__
        return {
            "RateLimitError": "rate_limited",
            "AuthenticationError": "auth_failed",
            "PermissionDeniedError": "auth_failed",
            "NotFoundError": "model_not_available",
            "BadRequestError": "invalid_request",
            "APIConnectionError": "connection_failed",
            "APITimeoutError": "timeout",
            "InternalServerError": "provider_error",
        }.get(name, "generation_failed")

    @staticmethod
    def _failure(model: str, parameters: dict[str, Any], reason: str, message: str) -> dict:
        return {
            "response": message,
            "tokens_used": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "model": model,
            "parameters": parameters,
            "success": False,
            "error": reason,
        }
