"""
Hybrid model routing: Identity-policy task-type selection, opt-in cloud,
and an enforced monthly budget.

Three properties this pins, each of which was previously untrue:

1. `Identity.yaml`'s `by_task_type` policy drives live routing. Before this,
   the runtime ignored it entirely and only `select_model()`'s standalone
   callers honoured it (MASTER_PLAN item 11.15).
2. Cloud is off unless explicitly configured. This is the privacy boundary
   from docs/VISION_AND_PERSONAL_DEPLOYMENT.md §6 -- enabling cloud is the
   moment personal context first leaves the device, so it must never happen
   by default or by accident.
3. The monthly cap is enforced from recorded spend. `select_model()` has
   accepted a `budget_exhausted` flag since it was written, but nothing ever
   computed it, so `monthly_cloud_spend_usd` was decorative.
"""

from __future__ import annotations

import pathlib
import tempfile

import pytest

from identity_interpreter.adapters import cloud_llm
from identity_interpreter.adapters.cloud_llm import CloudLLMAdapter, map_model_name
from identity_interpreter.loader import load_identity
from identity_interpreter.orchestrator.budget_ledger import (
    BudgetLedger,
    current_period,
    estimate_usd,
)
from identity_interpreter.orchestrator.model_router import ModelBackendError, ModelRouter


@pytest.fixture(scope="module")
def identity():
    return load_identity("Identity.yaml")


@pytest.fixture
def ledger_path():
    d = pathlib.Path(tempfile.mkdtemp())
    return str(d / "ledger.db")


@pytest.fixture
def no_cloud(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def with_cloud(monkeypatch):
    """A cloud deployment that can actually serve a request.

    Both halves are required. `anthropic` is an optional dependency and is
    declared in neither `requirements.txt` nor `pyproject.toml`, so
    `HAS_ANTHROPIC_SDK` is False in CI and in most developer environments.
    Setting only the key used to be enough for these tests because
    `is_configured()` ignored the SDK entirely -- which meant the tests
    below were asserting a cloud route that a real deployment in the same
    state could never have served. Patching both makes "with cloud" mean a
    deployment where cloud genuinely works; `cloud_key_without_sdk` covers
    the state that was previously conflated with it.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setattr(cloud_llm, "HAS_ANTHROPIC_SDK", True)


@pytest.fixture
def cloud_key_without_sdk(monkeypatch):
    """Cloud enabled by the user, but not servable: key set, SDK missing."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setattr(cloud_llm, "HAS_ANTHROPIC_SDK", False)


class TestCloudIsOptIn:
    def test_no_cloud_adapter_without_a_key(self, identity, no_cloud):
        assert ModelRouter(identity_config=identity).cloud_adapter is None

    def test_every_task_type_stays_local_without_a_key(self, identity, no_cloud):
        """Backend *and* model must both be local-servable.

        The original version of this test asserted only the backend, and so
        passed while `safety_review` returned backend="local" with
        model="Anthropic" -- a cloud provider name handed to the local
        adapter, which then asked Ollama for a model called "Anthropic".
        Found by review on PR #53; the assertion on the model is the part
        that was missing.
        """
        router = ModelRouter(identity_config=identity)
        for task_type in ("general", "code", "safety_review"):
            route = router.select_route({"task_type": task_type})
            assert route["backend"] == "local", f"{task_type} escaped to {route['backend']}"
            assert (
                map_model_name(route["model"]) is None
            ), f"{task_type} routed to local but kept cloud model {route['model']!r}"
            assert ":" in router.llm_adapter._map_model_name(route["model"]), (
                f"{task_type} selected {route['model']!r}, which the local "
                "adapter cannot map to an Ollama model"
            )

    def test_cloud_adapter_built_when_configured(self, identity, with_cloud):
        assert ModelRouter(identity_config=identity).cloud_adapter is not None

    def test_explicit_cloud_backend_refuses_when_unconfigured(self, identity, no_cloud):
        """A hand-routed caller can't smuggle traffic to an unconfigured cloud."""
        router = ModelRouter(identity_config=identity)
        with pytest.raises(ModelBackendError) as exc:
            router.route({"prompt": "hello", "backend": "cloud"})
        assert exc.value.reason == "cloud_not_configured"


class TestCloudReadinessIsDistinctFromConfiguration:
    """Cloud has three states, not two (2026-08-17).

    `is_configured()` answers "has the user enabled cloud?" and
    `is_ready()` answers "can a request be served right now?". They differ
    whenever a key is set but the optional `anthropic` SDK is absent -- a
    state a real user reaches simply by pasting in a key before running
    `pip install anthropic`. Routing used to consult the first predicate and
    then fail at the provider, instead of taking the local candidate
    Identity declares beside the cloud one.
    """

    def test_three_states_are_distinguishable(
        self,
        no_cloud,
        monkeypatch,
    ):
        monkeypatch.setattr(cloud_llm, "HAS_ANTHROPIC_SDK", True)
        assert cloud_llm.readiness() == cloud_llm.CLOUD_DISABLED

        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
        assert cloud_llm.readiness() == cloud_llm.CLOUD_READY

        monkeypatch.setattr(cloud_llm, "HAS_ANTHROPIC_SDK", False)
        assert cloud_llm.readiness() == cloud_llm.CLOUD_UNAVAILABLE

    def test_key_without_sdk_is_configured_but_not_ready(self, cloud_key_without_sdk):
        assert cloud_llm.is_configured() is True
        assert cloud_llm.is_ready() is False
        assert cloud_llm.unreadiness_reason() == "sdk_unavailable"

    def test_ready_deployment_reports_no_unreadiness_reason(self, with_cloud):
        assert cloud_llm.is_ready() is True
        assert cloud_llm.unreadiness_reason() is None

    def test_unservable_cloud_falls_back_to_identitys_local_candidate(
        self,
        identity,
        cloud_key_without_sdk,
    ):
        """The defect this distinction exists to fix.

        Identity routes safety_review to Anthropic first and the local
        Mistral second. With a key but no SDK, selecting cloud produced a
        hard `sdk_unavailable` failure; it must take the second candidate
        instead.
        """
        router = ModelRouter(identity_config=identity)
        route = router.select_route({"task_type": "safety_review"})

        assert route["backend"] == "local"
        assert map_model_name(route["model"]) is None

    def test_unservable_cloud_still_refuses_an_explicit_cloud_hint_truthfully(
        self,
        identity,
        cloud_key_without_sdk,
    ):
        """Falling back is for *policy* selection. A caller who explicitly
        demands cloud still gets a truthful failure rather than being
        silently downgraded to a different model than it asked for."""
        router = ModelRouter(identity_config=identity)
        with pytest.raises(ModelBackendError) as exc:
            router.route({"prompt": "hello", "backend": "cloud"})

        assert exc.value.reason == "sdk_unavailable"
        assert "mock response" not in str(exc.value).lower()


class TestIdentityPolicyRouting:
    def test_task_type_changes_the_selected_model(self, identity, with_cloud):
        """Identity.yaml sends safety_review to a cloud model and general to
        the local primary -- the whole point of by_task_type."""
        router = ModelRouter(identity_config=identity)
        assert router.select_route({"task_type": "safety_review"})["backend"] == "cloud"
        assert router.select_route({"task_type": "general"})["backend"] == "local"

    def test_explicit_backend_hint_still_wins(self, identity, with_cloud):
        router = ModelRouter(identity_config=identity)
        assert router.select_route({"backend": "stub"})["backend"] == "stub"

    def test_no_identity_leaves_original_behaviour(self, no_cloud):
        router = ModelRouter()
        assert router.select_route({})["backend"] == "stub"
        assert router.cloud_adapter is None

    def test_explicit_cloud_route_gets_a_cloud_model(self, identity, with_cloud):
        """An explicit backend="cloud" bypasses task-type selection, so the
        cloud backend needs its own model entry. Without one it fell through
        to `default_model` -- which construction sets to the *local* model --
        and sent a Mistral name to a cloud provider. Found by review on
        PR #53."""
        route = ModelRouter(identity_config=identity).select_route({"backend": "cloud"})
        assert route["backend"] == "cloud"
        assert (
            map_model_name(route["model"]) is not None
        ), f"explicit cloud route resolved {route['model']!r}, which is not a cloud model"


class TestLowBalanceBehaviour:
    """`low_balance_behavior` is a three-value policy. Refusing
    unconditionally at the cap made two of them ineffective -- this class
    enforces the user's declared policy, it does not substitute a stricter
    one. Found by review on PR #53."""

    @staticmethod
    def _exhausted_router(identity, ledger_path, behavior):
        router = ModelRouter(identity_config=identity)
        router._ledger = BudgetLedger(ledger_path)
        router._ledger.record(
            backend="cloud",
            model="claude-opus-5",
            input_tokens=100_000_000,
            output_tokens=0,
        )
        router._low_balance_behavior = lambda: behavior
        return router

    def test_force_local_refuses(self, identity, with_cloud, ledger_path):
        router = self._exhausted_router(identity, ledger_path, "force-local")
        with pytest.raises(ModelBackendError) as exc:
            router.route({"prompt": "hello", "backend": "cloud"})
        assert exc.value.reason == "budget_exhausted"
        assert "force-local" in str(exc.value)

    @pytest.mark.parametrize("behavior", ["warn", "continue"])
    def test_non_blocking_policies_are_not_refused(
        self,
        identity,
        with_cloud,
        ledger_path,
        behavior,
    ):
        """Past the cap under warn/continue, the request must reach the
        adapter rather than being refused for budget."""
        router = self._exhausted_router(identity, ledger_path, behavior)
        with pytest.raises(ModelBackendError) as exc:
            router.route({"prompt": "hello", "backend": "cloud"})
        assert exc.value.reason != "budget_exhausted"

    def test_unreadable_policy_defaults_to_strict(self, identity, with_cloud):
        """An unreadable policy must not become permission to keep spending."""
        router = ModelRouter(identity_config=identity)
        router.identity_config = object()  # no deployment_profile
        assert router._low_balance_behavior() == "force-local"


class TestBudgetEnforcement:
    def test_spend_accumulates_within_the_period(self, ledger_path):
        ledger = BudgetLedger(ledger_path)
        ledger.record(backend="cloud", model="claude-opus-5", input_tokens=1000, output_tokens=500)
        ledger.record(backend="cloud", model="claude-opus-5", input_tokens=2000, output_tokens=100)
        assert ledger.spent_this_period() > 0
        assert ledger.snapshot(cap_usd=1000.0).exhausted is False

    def test_snapshot_reports_exhausted_past_the_cap(self, ledger_path):
        ledger = BudgetLedger(ledger_path)
        ledger.record(
            backend="cloud",
            model="claude-opus-5",
            input_tokens=10_000_000,
            output_tokens=0,
        )
        assert ledger.snapshot(cap_usd=1.0).exhausted is True

    def test_unknown_model_is_never_free(self):
        """An unpriced model must not be costed at zero -- that would let it
        run uncapped forever."""
        assert estimate_usd("some-unlisted-model", 1_000_000, 1_000_000) > 0

    def test_unreadable_ledger_fails_closed(self, monkeypatch):
        """A broken ledger must degrade to local-only, never to uncapped
        cloud spend."""

        def _boom(self):
            raise OSError("ledger unreadable")

        ledger = BudgetLedger("/nonexistent-directory/ledger.db")
        monkeypatch.setattr(BudgetLedger, "spent_this_period", _boom)
        assert ledger.snapshot(cap_usd=25.0).exhausted is True

    def test_no_cap_means_not_exhausted(self, ledger_path):
        assert BudgetLedger(ledger_path).snapshot(cap_usd=None).exhausted is False

    def test_router_refuses_cloud_once_the_cap_is_spent(self, identity, with_cloud, ledger_path):
        router = ModelRouter(identity_config=identity)
        router._ledger = BudgetLedger(ledger_path)
        # Spend far past Identity.yaml's $25/month cap.
        router._ledger.record(
            backend="cloud",
            model="claude-opus-5",
            input_tokens=100_000_000,
            output_tokens=0,
        )
        with pytest.raises(ModelBackendError) as exc:
            router.route({"prompt": "hello", "backend": "cloud"})
        assert exc.value.reason == "budget_exhausted"

    def test_exhausted_budget_forces_local_selection(self, identity, with_cloud, ledger_path):
        """Identity's own low_balance_behavior: force-local, now actually
        driven by recorded spend."""
        router = ModelRouter(identity_config=identity)
        router._ledger = BudgetLedger(ledger_path)
        assert router.select_route({"task_type": "safety_review"})["backend"] == "cloud"
        router._ledger.record(
            backend="cloud",
            model="claude-opus-5",
            input_tokens=100_000_000,
            output_tokens=0,
        )
        assert router.select_route({"task_type": "safety_review"})["backend"] == "local"

    def test_period_key_is_a_calendar_month(self):
        assert len(current_period()) == 7 and current_period()[4] == "-"


class TestCloudAdapterContract:
    """The adapter must return the same result shape as the local one, so
    route()'s honesty guarantee covers both without special-casing."""

    def test_identity_provider_name_maps_to_a_real_model(self):
        assert map_model_name("Anthropic") == "claude-opus-5"
        assert map_model_name("not-a-provider") is None

    def test_unconfigured_returns_structured_failure_not_an_exception(self, no_cloud):
        result = CloudLLMAdapter().generate("hello", "Anthropic", {})
        assert result["success"] is False
        assert result["error"] == "cloud_not_configured"
        assert "mock response" not in result["response"].lower()

    def test_empty_prompt_is_rejected(self, with_cloud):
        result = CloudLLMAdapter().generate("   ", "Anthropic", {})
        assert result["success"] is False
        assert result["error"] == "empty_prompt"

    def test_sampling_parameters_are_not_forwarded(self, with_cloud, monkeypatch):
        """Identity.yaml carries temperature 0.2 / top_p 0.9, and current
        Claude models reject both with a 400. Forwarding them -- the obvious
        thing to do, and what the local adapter does -- would make every
        cloud request fail."""
        captured = {}

        class _FakeMessages:
            def create(self, **kwargs):
                captured.update(kwargs)
                raise RuntimeError("stop here -- we only care about the kwargs")

        class _FakeClient:
            messages = _FakeMessages()

        def _fake_client():
            return _FakeClient()

        adapter = CloudLLMAdapter()
        monkeypatch.setattr(adapter, "_get_client", _fake_client)
        adapter.generate("hello", "Anthropic", {"temperature": 0.2, "top_p": 0.9})

        assert "temperature" not in captured
        assert "top_p" not in captured
        assert "top_k" not in captured
        assert captured["model"] == "claude-opus-5"
        assert captured["max_tokens"] > 0
