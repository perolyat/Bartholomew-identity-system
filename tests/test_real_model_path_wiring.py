"""
The live chat path must reach a real model backend, and must degrade
truthfully when it can't.

Two independent defects kept /api/chat on the stub, and fixing either alone
changed nothing:

1. `app.py` constructed `Orchestrator()` with no identity config, so
   `ModelRouter` built no LLM adapter (`ContextBuilder`'s module docstring
   recorded this same root cause for conversational memory back in
   2026-07-21).
2. `ModelRouter._default_config()` hardcoded `default_backend: "stub"`, and
   `Orchestrator.route_model()` passes no `backend` hint, so the real-backend
   branch in `route()` was unreachable regardless of the adapter.

A third, quieter one: with the backend forced to "local", the model name came
from this module's hardcoded backend table ("mistral-medium") rather than
Identity.yaml, so it reached Ollama unmapped and failed the existence check.

These tests pin the wiring without requiring a running Ollama.
"""

from __future__ import annotations

import re

import pytest

from identity_interpreter.loader import load_identity
from identity_interpreter.orchestrator.model_router import ModelBackendError, ModelRouter


@pytest.fixture(scope="module")
def identity():
    return load_identity("Identity.yaml")


class TestIdentityWiredRouting:
    def test_identity_selects_real_backend_not_stub(self, identity):
        router = ModelRouter(identity_config=identity)
        assert router.llm_adapter is not None
        assert router.config["default_backend"] == "local"

    def test_model_name_comes_from_identity_not_the_hardcoded_table(self, identity):
        """The hardcoded table says "mistral-medium"; Identity.yaml's
        local_primary is the real answer."""
        router = ModelRouter(identity_config=identity)
        model = router.config["backends"]["local"]["model"]
        assert model != "mistral-medium"
        assert model == identity.meta.deployment_profile.models.local_primary

    def test_selected_model_maps_to_a_real_ollama_name(self, identity):
        """A model name the adapter cannot map would fail at the provider with
        a confusing error. Pin that the selected name is one the adapter's
        mapping actually knows."""
        router = ModelRouter(identity_config=identity)
        model = router.config["backends"]["local"]["model"]
        mapped = router.llm_adapter._map_model_name(model)
        assert mapped != model, f"{model!r} passed through the mapping unmapped"
        assert ":" in mapped  # ollama-style name, e.g. mistral:7b-instruct

    def test_no_identity_still_defaults_to_stub(self):
        """Existing callers and tests must be untouched."""
        router = ModelRouter()
        assert router.llm_adapter is None
        assert router.config["default_backend"] == "stub"

    def test_explicit_config_is_not_overridden_by_identity(self, identity):
        """Passing an explicit config means the caller is in charge."""
        custom = {
            "default_backend": "stub",
            "default_model": "stub-llm",
            "backends": {"stub": {"model": "stub-llm", "temperature": 0.1}},
        }
        router = ModelRouter(config=custom, identity_config=identity)
        assert router.config["default_backend"] == "stub"


class TestTruthfulDegradation:
    def test_unreachable_provider_raises_instead_of_answering(self, identity, monkeypatch):
        """The wired router must raise, never return prose, when the provider
        cannot serve.

        OLLAMA_HOST is pinned to a closed port rather than relying on "no
        Ollama is running here". Otherwise this test passes in CI for the
        wrong reason and silently inverts on any machine that does have
        Ollama up -- including the tester's, which is exactly where the real
        model path is meant to be exercised.
        """
        monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9")  # discard port
        router = ModelRouter(identity_config=identity)
        with pytest.raises(ModelBackendError) as exc:
            router.route({"prompt": "hello"})
        assert exc.value.reason in {
            "connection_failed",
            "model_not_available",
            "timeout",
            "adapter_exception",
        }
        assert "mock response" not in str(exc.value).lower()


class TestReplyParsingPreservesModelOutput:
    """_parse_reply previously stripped every bracketed span in the reply."""

    @staticmethod
    def _parse(raw):
        from bartholomew_api_bridge_v0_1.services.api.app import _parse_reply

        return _parse_reply(raw)

    def test_leading_tags_are_still_extracted_and_removed(self):
        reply, tone, emotion = self._parse("[tone: warm] [emotion: helpful] Hello there.")
        assert (tone, emotion) == ("warm", "helpful")
        assert reply == "Hello there."

    @pytest.mark.parametrize(
        "body",
        [
            "Use items[0] to get the first element.",
            "See [the docs](https://example.com) for more.",
            "Logs show [INFO] startup complete.",
            "In Python, a[1:3] is a slice.",
            "Citations look like [1] and [2].",
        ],
    )
    def test_brackets_inside_the_body_survive(self, body):
        reply, _, _ = self._parse(f"[tone: neutral] {body}")
        assert reply == body

    def test_body_without_tags_is_returned_unchanged(self):
        body = "No tags here, but a[0] bracket."
        reply, tone, emotion = self._parse(body)
        assert reply == body
        assert tone is None and emotion is None

    def test_no_bracketed_span_is_lost_from_a_realistic_code_answer(self):
        raw = (
            "[tone: neutral] [emotion: helpful] Here you go:\n\n"
            "```python\nvalues = data['rows'][0]\nprint(values[1:3])\n```\n"
            "See [the guide](https://example.com/guide)."
        )
        reply, _, _ = self._parse(raw)
        # Every bracketed span in the body must still be present.
        for span in re.findall(r"\[[^\]]*\]", reply):
            assert span in reply
        assert "data['rows'][0]" in reply
        assert "values[1:3]" in reply
        assert "[the guide]" in reply
        assert not reply.startswith("[tone:")
