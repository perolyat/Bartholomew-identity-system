"""
Model Router
------------
Routes requests to appropriate LLM backends based on configuration.

Backend *routing* + generation only. Identity-policy-driven model
*selection* ("which model does this Identity prefer for this task type?")
belongs to `identity_interpreter.policies.model_router.select_model` --
see that module's docstring, and MASTER_PLAN.md item 11.15, for why these
are two concepts rather than a duplicate pair.
"""

from typing import Any


class ModelBackendError(RuntimeError):
    """
    A real model backend was selected and could not produce a generation.

    Raised instead of returning placeholder text, so that no caller can
    mistake a provider failure for a genuine model response. Carries the
    backend/model/reason so a caller can render a truthful degraded state.
    """

    def __init__(
        self,
        message: str,
        *,
        backend: str,
        model: str | None = None,
        reason: str | None = None,
    ):
        super().__init__(message)
        self.backend = backend
        self.model = model
        self.reason = reason


class ModelRouter:
    """Routes requests to appropriate LLM backends."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        identity_config: Any | None = None,
    ):
        """
        Initialize the model router.

        Args:
            config: Optional configuration dictionary
            identity_config: Optional identity config for LLM adapter
        """
        self.config = config or self._default_config()
        self.identity_config = identity_config
        self.llm_adapter = None
        self.cloud_adapter = None
        self._ledger = None

        # Lazily initialize LLM adapter if identity config provided
        if identity_config:
            try:
                from ..adapters.llm_stub import LLMAdapter

                self.llm_adapter = LLMAdapter(identity_config)
            except Exception:
                # Adapter unavailable, will use stub
                pass

            # Cloud is opt-in: built only when an API key is present, so a
            # deployment that never configures one behaves exactly as it did
            # before this existed. See docs/VISION_AND_PERSONAL_DEPLOYMENT.md
            # §6 -- enabling cloud is the moment personal context first
            # leaves the device, and that stays a deliberate user action.
            try:
                from ..adapters.cloud_llm import CloudLLMAdapter, is_configured

                if is_configured():
                    self.cloud_adapter = CloudLLMAdapter(identity_config)
            except Exception:
                pass

            # Spend ledger, so Identity.yaml's monthly cap is enforced from
            # recorded spend rather than left declarative.
            try:
                from .budget_ledger import BudgetLedger, resolve_ledger_path

                self._ledger = BudgetLedger(resolve_ledger_path())
            except Exception:
                pass

        # With a working adapter, default to the real local backend and take
        # its model from Identity.yaml's own selection policy rather than
        # this module's hardcoded backend table.
        #
        # Both halves are required. Wiring the adapter alone changed nothing,
        # because `default_backend` stayed "stub" and route()'s real-backend
        # branch was never reached. Flipping the backend alone selected the
        # hardcoded "mistral-medium", which is in neither Identity.yaml's
        # model list nor LLMAdapter.model_mapping, so it reached Ollama
        # unmapped and failed the model-existence check.
        #
        # Consulting select_model() here is the narrow, task_type="general"
        # closure of the gap recorded in
        # identity_interpreter/policies/model_router.py's docstring and
        # MASTER_PLAN.md item 11.15: the live runtime previously ignored
        # Identity.yaml's by_task_type policy entirely, and only the CLI and
        # standalone chat.py honoured it. Nothing broader is taken on here --
        # no per-request task typing, no budget/context-window routing.
        #
        # When no identity config is supplied, every line below is skipped
        # and the stub default stands, so existing callers and tests are
        # unaffected.
        if self.llm_adapter is not None and config is None:
            try:
                from ..policies.model_router import select_model

                decision = select_model(identity_config, task_type="general")
                model_name = decision.decision["model"]
                parameters = decision.decision.get("parameters", {})

                self.config["default_backend"] = "local"
                self.config["backends"]["local"] = {
                    "model": model_name,
                    "temperature": parameters.get("temperature", 0.2),
                }
                self.config["default_model"] = model_name
            except Exception:
                # Identity did not yield a usable model selection. Leave the
                # stub default in place rather than guessing a model name --
                # a wrong name would fail at the provider with a confusing
                # error instead of here, quietly.
                pass

    def _default_config(self) -> dict[str, Any]:
        """Return default routing configuration."""
        return {
            "default_backend": "stub",
            "default_model": "stub-llm",
            "backends": {
                "stub": {"model": "stub-llm", "temperature": 0.7},
                "openai": {"model": "gpt-4o-mini", "temperature": 0.2},
                "anthropic": {"model": "sonnet", "temperature": 0.3},
                "local": {"model": "mistral-medium", "temperature": 0.5},
            },
        }

    def budget_snapshot(self):
        """Cloud spend against Identity.yaml's monthly cap, or None when no
        identity/ledger is wired (in which case nothing is capped because
        nothing can reach a paid backend anyway)."""
        if self._ledger is None or self.identity_config is None:
            return None
        try:
            cap = self.identity_config.meta.deployment_profile.budgets.monthly_cloud_spend_usd
        except Exception:
            return None
        return self._ledger.snapshot(float(cap) if cap is not None else None)

    def select_route(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Select the appropriate routing configuration.

        With an identity wired, selection is per-request and Identity-driven:
        `task_type` picks candidate models from `Identity.yaml`'s
        `by_task_type` policy, recorded cloud spend decides whether cloud
        candidates are still affordable, and the chosen model's family
        determines the backend. Without one, the original behaviour stands --
        a `backend` hint or the stub default.

        This is the full form of the gap recorded in
        `identity_interpreter/policies/model_router.py`'s docstring and
        MASTER_PLAN item 11.15: the live runtime previously ignored
        `by_task_type` entirely. An explicit `backend` in `data` still wins,
        so callers that route by hand are unaffected.

        Args:
            data: Request data. Honours `backend` (explicit override) and
                `task_type` (Identity-policy selection; defaults to
                "general").

        Returns:
            Route configuration with backend, model, and parameters.
        """
        explicit_backend = data.get("backend")
        if explicit_backend is None and self.identity_config is not None:
            resolved = self._select_by_task_type(data.get("task_type", "general"))
            if resolved is not None:
                return resolved

        backend = explicit_backend or self.config["default_backend"]
        backend_config = self.config["backends"].get(backend, {})

        return {
            "backend": backend,
            "model": backend_config.get("model", self.config["default_model"]),
            "parameters": {"temperature": backend_config.get("temperature", 0.7)},
        }

    def _select_by_task_type(self, task_type: str) -> dict[str, Any] | None:
        """Identity-policy model selection for one request.

        Budget enforcement lives here rather than in `route()` because
        `select_model()` already accepts `budget_exhausted` and applies
        `low_balance_behavior: force-local` itself -- the policy was written
        for this and simply never had a caller that computed the flag. All
        this does is compute it from recorded spend.
        """
        try:
            from ..policies.model_router import select_model

            snapshot = self.budget_snapshot()
            exhausted = bool(snapshot and snapshot.exhausted)

            decision = select_model(
                self.identity_config,
                task_type=task_type,
                budget_exhausted=exhausted,
            )
            model_name = decision.decision["model"]
            parameters = dict(decision.decision.get("parameters", {}))

            backend = self._backend_for_model(model_name)
            if backend is None:
                return None
            return {
                "backend": backend,
                "model": model_name,
                "parameters": parameters,
                "budget_exhausted": exhausted,
            }
        except Exception:
            return None

    def _backend_for_model(self, model_name: str) -> str | None:
        """Which backend serves this Identity-declared model.

        Cloud only when it is both mappable to a real cloud model *and*
        actually configured -- an unconfigured cloud selection falls back to
        local rather than raising, because Identity may legitimately list a
        cloud model the user has never enabled.
        """
        from ..adapters.cloud_llm import is_configured, map_model_name

        if map_model_name(model_name) is not None:
            return "cloud" if is_configured() else "local"
        if self.llm_adapter is not None:
            return "local"
        return None

    def route(self, data: dict[str, Any]) -> str:
        """
        Execute routing and return LLM response.

        A backend that is *supposed* to produce a real generation must never
        answer with mock text. Before 2026-08-15 this method fell through to
        a `"Mock response for: ..."` placeholder whenever a real backend was
        selected but failed -- an unreachable Ollama, a model that isn't
        pulled, a timeout, or an unlisted backend name. The caller could not
        distinguish that string from a genuine answer, so a provider outage
        surfaced to the user as a fabricated conversational reply, and a
        real-world test session could silently record mock output as real
        model behaviour. Now only the explicit `stub` backend returns stub
        text; every other backend either returns a genuine generation or
        raises ModelBackendError.

        Args:
            data: Request data with prompt and routing information

        Returns:
            LLM response string

        Raises:
            ModelBackendError: the selected backend is a real one and could
                not produce a generation. Callers are expected to surface
                this as a truthful failure/degraded state, never to
                substitute text of their own.
        """
        route = self.select_route(data)
        prompt = data.get("prompt", data.get("user_input", ""))
        backend = route["backend"]

        # The explicit stub backend -- the only path allowed to return text
        # that did not come from a model. Unchanged behaviour, and still the
        # default when no identity config was supplied.
        if backend == "stub":
            return f"[{route['model']}] Mock response for prompt: {prompt[:50]}..."

        # Cloud backend: opt-in, budget-capped, spend recorded.
        if backend == "cloud":
            return self._route_cloud(route, prompt, data)

        # Real backends: local/ollama via the adapter.
        if backend in ("local", "ollama"):
            if self.llm_adapter is None:
                raise ModelBackendError(
                    f"Backend {backend!r} requires an LLM adapter, but none was "
                    "constructed (ModelRouter was built without an identity config).",
                    backend=backend,
                    model=route["model"],
                    reason="adapter_unavailable",
                )
            try:
                result = self.llm_adapter.generate(
                    prompt=prompt,
                    model=route["model"],
                    parameters=route["parameters"],
                    context=data,
                )
            except Exception as exc:  # adapter raised rather than returning
                raise ModelBackendError(
                    f"Backend {backend!r} raised while generating: {exc}",
                    backend=backend,
                    model=route["model"],
                    reason="adapter_exception",
                ) from exc

            if result.get("success"):
                return result.get("response", "")

            # The adapter reports failures as structured data rather than
            # exceptions (connection_failed, model_not_available, timeout,
            # ollama_disabled, empty_prompt). Preserve its own reason.
            raise ModelBackendError(
                result.get("response") or f"Backend {backend!r} failed to generate.",
                backend=backend,
                model=result.get("model", route["model"]),
                reason=result.get("error", "generation_failed"),
            )

        # Any other configured backend (openai/anthropic today) has no
        # implementation. Say so rather than emitting placeholder prose.
        raise ModelBackendError(
            f"Backend {backend!r} is configured but not implemented.",
            backend=backend,
            model=route["model"],
            reason="backend_not_implemented",
        )

    def _route_cloud(self, route: dict[str, Any], prompt: str, data: dict[str, Any]) -> str:
        """Generate via the cloud backend, refusing when the monthly cap is
        spent and recording what the call cost.

        The budget is checked here as well as during selection because an
        explicit `backend="cloud"` hint bypasses `_select_by_task_type()`
        entirely -- without this check, a hand-routed caller could spend past
        a cap that Identity policy declares. Cheaper to refuse than to
        discover the overspend on an invoice.
        """
        snapshot = self.budget_snapshot()
        if snapshot is not None and snapshot.exhausted:
            raise ModelBackendError(
                f"Monthly cloud budget exhausted for {snapshot.period} "
                f"(${snapshot.spent_usd:.2f} of ${snapshot.cap_usd:.2f}). "
                "Identity policy is force-local until the period rolls over.",
                backend="cloud",
                model=route["model"],
                reason="budget_exhausted",
            )

        if self.cloud_adapter is None:
            raise ModelBackendError(
                "Cloud backend selected but not configured.",
                backend="cloud",
                model=route["model"],
                reason="cloud_not_configured",
            )

        try:
            result = self.cloud_adapter.generate(
                prompt=prompt,
                model=route["model"],
                parameters=route["parameters"],
                context=data,
            )
        except Exception as exc:
            raise ModelBackendError(
                f"Cloud backend raised while generating: {exc}",
                backend="cloud",
                model=route["model"],
                reason="adapter_exception",
            ) from exc

        if not result.get("success"):
            raise ModelBackendError(
                result.get("response") or "Cloud backend failed to generate.",
                backend="cloud",
                model=result.get("model", route["model"]),
                reason=result.get("error", "generation_failed"),
            )

        # Record spend before returning. Best-effort: a ledger write failure
        # must not discard a generation the user already paid for. The next
        # snapshot() fails closed anyway, so an unwritable ledger degrades to
        # local-only rather than to silent uncapped spend.
        if self._ledger is not None:
            try:
                self._ledger.record(
                    backend="cloud",
                    model=result.get("model", route["model"]),
                    input_tokens=result.get("input_tokens", 0),
                    output_tokens=result.get("output_tokens", 0),
                )
            except Exception:
                pass

        return result.get("response", "")
