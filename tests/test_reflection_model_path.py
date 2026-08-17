"""
Tests for the reflection *model* path (2026-08-17).

Companion to `test_reflection_ownership.py` / `test_reflection_narrative_
integration.py`, which pin *who composes* a reflection. These pin *what
composes it*: that a real model can, that a stub cannot be mistaken for
one, and that a provider outage is never stored as a success.

Two independent defects made every reflection in the repository's history
template-composed, and neither was visible from the test suite:

1. `daemon.py` hard-coded `backend="stub"` in both reflection paths, so
   Identity's model policy never applied to reflection at all. The stub's
   mock text tripped the generator's own red-line check, the redraft tripped
   it again, and the result fell through to a fallback template every time.
2. `ReflectionGenerator.__init__` could not run on a headless host --
   `Orchestrator(identity_config=...)` built a `ContextBuilder`, which built
   a `MemoryManager`, which required the OS keystore and raised. `conftest.py`
   installs an in-memory keyring backend for the whole session, which is why
   CI never saw this; `test_headless_construction_needs_no_keystore` below
   removes that backend so the real condition is exercised.

A third issue was found while fixing these and is pinned here too:
`_generate_with_safety()` labelled every success `generator: "llm"`,
including stub output. Reflections are persisted, and the weekly audit is
pinned, so a short-enough prompt could have written mock text into the
user's permanent reflection history labelled as model-composed.
"""

from __future__ import annotations

import ast
import inspect
from datetime import datetime
from pathlib import Path

import pytest

from bartholomew.kernel import daemon as daemon_module
from identity_interpreter.adapters.reflection_generator import ReflectionGenerator
from identity_interpreter.orchestrator.model_router import ModelBackendError

_DAEMON_SOURCE = Path(daemon_module.__file__).read_text(encoding="utf-8")


def _reflection_call_keywords(method_name: str, callee: str) -> set[str]:
    """Keyword argument names the daemon passes to a reflection call."""
    tree = ast.parse(_DAEMON_SOURCE)
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        if node.name != method_name:
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            if isinstance(func, ast.Attribute) and func.attr == callee:
                return {kw.arg for kw in call.keywords if kw.arg}
    raise AssertionError(f"No call to {callee}() found in {method_name}()")


class TestDaemonDoesNotPinTheBackend:
    """Requirement 2: the stub is not hard-coded by the daemon."""

    @pytest.mark.parametrize(
        ("method", "callee"),
        [
            ("_run_daily_reflection", "generate_daily_reflection"),
            ("_run_weekly_reflection", "generate_weekly_audit"),
        ],
    )
    def test_daemon_passes_no_backend_override(self, method, callee):
        keywords = _reflection_call_keywords(method, callee)
        assert "backend" not in keywords, (
            f"{method}() pins the model backend for reflection. Omit the "
            "argument so Identity.yaml's model policy applies -- pinning it "
            'to "stub" is what made every historical reflection '
            "template-composed."
        )

    @pytest.mark.parametrize(
        "method",
        ["generate_daily_reflection", "generate_weekly_audit"],
    )
    def test_generator_default_is_identity_routing_not_stub(self, method):
        default = (
            inspect.signature(getattr(ReflectionGenerator, method)).parameters["backend"].default
        )
        assert default is None, (
            f"{method}()'s backend default is {default!r}; it must be None so "
            "the default path is Identity-driven routing."
        )


class TestRealBackendIsSelectable:
    """Requirement 1: a real backend can be selected for reflection."""

    def test_default_route_resolves_to_a_real_backend(self):
        generator = ReflectionGenerator(identity_path="Identity.yaml")
        route = generator.orchestrator.router.select_route(
            {
                "prompt": "x",
                "session_id": "system_reflection",
                "task_type": ReflectionGenerator.REFLECTION_TASK_TYPE,
            },
        )
        assert route["backend"] != "stub"
        assert route["model"] != "stub-llm"

    def test_reflection_task_type_stays_local(self):
        """Reflection reads stored personal memory; the local/cloud data
        boundary is unratified, so it must not route to a cloud model."""
        generator = ReflectionGenerator(identity_path="Identity.yaml")
        route = generator.orchestrator.router.select_route(
            {
                "prompt": "x",
                "session_id": "system_reflection",
                "task_type": ReflectionGenerator.REFLECTION_TASK_TYPE,
            },
        )
        assert route["backend"] == "local"


class TestBackendFailureIsNotSuccess:
    """Requirement 3: a failed backend never masquerades as a reflection."""

    @staticmethod
    def _failing_generator(monkeypatch):
        generator = ReflectionGenerator(identity_path="Identity.yaml")

        def _raise(_data):
            raise ModelBackendError(
                "Backend 'local' failed to generate.",
                backend="local",
                model="mistral:7b-instruct",
                reason="connection_failed",
            )

        monkeypatch.setattr(generator.orchestrator.router, "route", _raise)
        return generator

    def test_daily_failure_reports_failure(self, monkeypatch):
        generator = self._failing_generator(monkeypatch)

        result = generator.generate_daily_reflection(
            metrics={"nudges_count": 0, "pending_nudges": 0},
            date=datetime(2026, 8, 17),
            timezone_str="UTC",
        )

        assert result["success"] is False
        assert result["meta"]["generator"] == "template"
        assert "connection_failed" in result["meta"]["error"]
        assert "Mock response" not in result["content"]

    def test_weekly_failure_reports_failure(self, monkeypatch):
        generator = self._failing_generator(monkeypatch)

        result = generator.generate_weekly_audit(
            weekly_scope={"reflections_count": 0, "policy_checks": 0},
            iso_week=33,
            year=2026,
        )

        assert result["success"] is False
        assert result["meta"]["generator"] == "template"
        assert "Mock response" not in result["content"]


class TestStubProvenanceIsTruthful:
    """Stub output may exist, but must never claim a model composed it."""

    def test_stub_success_is_labelled_stub_not_llm(self):
        generator = ReflectionGenerator(identity_path="Identity.yaml")

        # A short prompt whose echo clears the red-line check -- the case
        # that would previously have been stored as generator: "llm".
        result = generator._generate_with_safety(
            prompt="Write a short daily reflection.",
            backend="stub",
            reflection_type="daily",
        )

        assert "Mock response" in result["content"], (
            "Precondition: this prompt is expected to survive the safety "
            "check, which is exactly why provenance matters here."
        )
        assert result["meta"]["generator"] == "stub"
        assert result["meta"]["generator"] != "llm"


class TestHeadlessConstruction:
    """Requirement 4: construction must not require an OS keystore."""

    def test_headless_construction_needs_no_keystore(self, monkeypatch):
        """Constructing must succeed with no keyring backend at all.

        `conftest.py` installs an in-memory keyring for the session, so this
        removes it for the duration of the test -- otherwise the very
        condition that broke headless hosts is masked.
        """
        import keyring

        from identity_interpreter.adapters import memory_manager

        class _NoKeystore:
            @staticmethod
            def get_password(*_args, **_kwargs):
                raise keyring.errors.NoKeyringError("no backend available")

            @staticmethod
            def set_password(*_args, **_kwargs):
                raise keyring.errors.NoKeyringError("no backend available")

        monkeypatch.setattr(memory_manager, "keyring", _NoKeystore)

        generator = ReflectionGenerator(identity_path="Identity.yaml")

        # And the memory context it needs degrades rather than raising.
        assert generator.orchestrator.context.build_prompt_context("system_reflection") == ""

    def test_composition_still_works_without_a_keystore(self, monkeypatch):
        """A keystore-less host still produces a reflection, via the
        documented fallback rather than an exception."""
        import keyring

        from identity_interpreter.adapters import memory_manager

        class _NoKeystore:
            @staticmethod
            def get_password(*_args, **_kwargs):
                raise keyring.errors.NoKeyringError("no backend available")

            @staticmethod
            def set_password(*_args, **_kwargs):
                raise keyring.errors.NoKeyringError("no backend available")

        monkeypatch.setattr(memory_manager, "keyring", _NoKeystore)

        generator = ReflectionGenerator(identity_path="Identity.yaml")
        result = generator.generate_daily_reflection(
            metrics={"nudges_count": 0, "pending_nudges": 0},
            date=datetime(2026, 8, 17),
            timezone_str="UTC",
            backend="stub",
        )

        assert "Daily Reflection" in result["content"]
