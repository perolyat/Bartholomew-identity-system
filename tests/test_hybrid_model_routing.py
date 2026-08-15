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
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")


class TestCloudIsOptIn:
    def test_no_cloud_adapter_without_a_key(self, identity, no_cloud):
        assert ModelRouter(identity_config=identity).cloud_adapter is None

    def test_every_task_type_stays_local_without_a_key(self, identity, no_cloud):
        router = ModelRouter(identity_config=identity)
        for task_type in ("general", "code", "safety_review"):
            route = router.select_route({"task_type": task_type})
            assert route["backend"] == "local", f"{task_type} escaped to {route['backend']}"

    def test_cloud_adapter_built_when_configured(self, identity, with_cloud):
        assert ModelRouter(identity_config=identity).cloud_adapter is not None

    def test_explicit_cloud_backend_refuses_when_unconfigured(self, identity, no_cloud):
        """A hand-routed caller can't smuggle traffic to an unconfigured cloud."""
        router = ModelRouter(identity_config=identity)
        with pytest.raises(ModelBackendError) as exc:
            router.route({"prompt": "hello", "backend": "cloud"})
        assert exc.value.reason == "cloud_not_configured"


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
