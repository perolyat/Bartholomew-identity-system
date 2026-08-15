"""
Model-backend honesty: a real backend must never answer with mock text.

This is the regression guard for the whole real-world test track. Before
2026-08-15, `ModelRouter.route()` fell through to a
`"[model] Mock response for: ..."` placeholder whenever a real backend was
selected but could not generate -- Ollama unreachable, model not pulled,
request timed out, adapter absent, or a backend name with no implementation
at all. That string is indistinguishable from a genuine answer at the call
site, so a provider outage reached the user as a fabricated conversational
reply, and a real-world test session could record mock output as real model
behaviour without anyone noticing.

The rule these tests pin: only the explicit `stub` backend may return text
that did not come from a model. Every other backend either returns a genuine
generation or raises ModelBackendError.
"""

from __future__ import annotations

import pytest

from identity_interpreter.orchestrator.model_router import ModelBackendError, ModelRouter

# Any casing of this must never come back from a real backend.
_MOCK_MARKER = "mock response"


class _FailingAdapter:
    """Adapter that reports failure the way the real one does -- structured
    data with success=False, not an exception."""

    def __init__(self, reason: str = "connection_failed"):
        self.reason = reason

    def generate(self, prompt, model, parameters, context=None):
        return {
            "response": f"[ERROR] Could not connect to Ollama at http://localhost:11434 ({self.reason})",
            "tokens_used": 0,
            "model": model,
            "parameters": parameters,
            "success": False,
            "error": self.reason,
        }


class _RaisingAdapter:
    """Adapter that raises instead of returning a failure dict."""

    def generate(self, prompt, model, parameters, context=None):
        raise ConnectionError("socket closed")


class _WorkingAdapter:
    """Adapter that genuinely generates."""

    def generate(self, prompt, model, parameters, context=None):
        return {
            "response": "The kettle is already on.",
            "tokens_used": 7,
            "model": model,
            "parameters": parameters,
            "success": True,
        }


def _router_with(adapter) -> ModelRouter:
    r = ModelRouter()
    r.llm_adapter = adapter
    return r


class TestRealBackendNeverFabricates:
    def test_provider_unreachable_raises_rather_than_returning_mock(self):
        router = _router_with(_FailingAdapter())
        with pytest.raises(ModelBackendError) as exc:
            router.route({"prompt": "are you there?", "backend": "local"})
        assert exc.value.reason == "connection_failed"
        assert _MOCK_MARKER not in str(exc.value).lower()

    @pytest.mark.parametrize(
        "reason",
        ["connection_failed", "model_not_available", "timeout", "ollama_disabled"],
    )
    def test_every_adapter_failure_mode_is_truthful(self, reason):
        router = _router_with(_FailingAdapter(reason))
        with pytest.raises(ModelBackendError) as exc:
            router.route({"prompt": "hello", "backend": "local"})
        assert exc.value.reason == reason

    def test_adapter_exception_is_not_swallowed_into_mock_text(self):
        router = _router_with(_RaisingAdapter())
        with pytest.raises(ModelBackendError) as exc:
            router.route({"prompt": "hello", "backend": "ollama"})
        assert exc.value.reason == "adapter_exception"

    def test_real_backend_without_adapter_raises(self):
        """The exact shape of the original defect: identity never reached the
        router, so no adapter exists, but a real backend was requested."""
        router = ModelRouter()
        assert router.llm_adapter is None
        with pytest.raises(ModelBackendError) as exc:
            router.route({"prompt": "hello", "backend": "local"})
        assert exc.value.reason == "adapter_unavailable"

    def test_unimplemented_backend_says_so(self):
        """openai/anthropic are configured but have no implementation. They
        previously returned placeholder prose."""
        router = ModelRouter()
        for backend in ("openai", "anthropic"):
            with pytest.raises(ModelBackendError) as exc:
                router.route({"prompt": "hello", "backend": backend})
            assert exc.value.reason == "backend_not_implemented"

    def test_no_real_backend_path_can_return_mock_text(self):
        """Belt and braces: sweep every non-stub backend against every adapter
        state and assert nothing that returns successfully contains the mock
        marker."""
        adapters = [None, _FailingAdapter(), _RaisingAdapter()]
        backends = ["local", "ollama", "openai", "anthropic", "not-a-backend"]
        for adapter in adapters:
            router = _router_with(adapter) if adapter else ModelRouter()
            for backend in backends:
                try:
                    result = router.route({"prompt": "hello", "backend": backend})
                except ModelBackendError:
                    continue  # truthful failure -- the correct outcome
                assert _MOCK_MARKER not in result.lower(), (
                    f"backend={backend!r} adapter={type(adapter).__name__} "
                    f"returned mock text: {result!r}"
                )


class TestStubBackendUnchanged:
    """The stub path is still allowed to stub, and is still the default when
    no identity config was supplied -- so existing tests and CI are
    unaffected by the change above."""

    def test_default_backend_is_still_stub_without_identity(self):
        router = ModelRouter()
        assert router.config["default_backend"] == "stub"

    def test_stub_backend_still_returns_stub_text(self):
        router = ModelRouter()
        result = router.route({"prompt": "hello"})
        assert _MOCK_MARKER in result.lower()

    def test_stub_is_unaffected_by_a_broken_adapter(self):
        router = _router_with(_FailingAdapter())
        result = router.route({"prompt": "hello"})  # no backend hint -> stub
        assert _MOCK_MARKER in result.lower()


class TestSuccessfulGenerationPassesThrough:
    def test_working_adapter_returns_its_own_text(self):
        router = _router_with(_WorkingAdapter())
        result = router.route({"prompt": "is the kettle on?", "backend": "local"})
        assert result == "The kettle is already on."
        assert _MOCK_MARKER not in result.lower()
