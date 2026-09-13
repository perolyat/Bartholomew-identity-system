"""EXEC-01: the cognition port over Bartholomew's existing model router.

The adapter is small on purpose, and these tests are mostly about what it
*cannot* do. Two properties carry the weight:

* it satisfies `DeliberationPort` structurally, so the executive never imports
  this package and never acquires a model client of its own;
* it fails loudly rather than inventively. `ModelRouter` was changed in
  2026-08-15 to raise instead of returning mock text, precisely so a provider
  outage could not be mistaken for an answer, and this adapter holds that line.
"""

from __future__ import annotations

import pytest

from bartholomew.executive.deliberation import DeliberationPort, deliberate_task
from bartholomew.integration.deliberation_adapter import (
    DELIBERATION_TASK_TYPE,
    DeliberationUnavailableError,
    ModelRouterDeliberationPort,
    install_deliberation_port,
)


class _Router:
    """Stands in for `ModelRouter`, with its published `route(dict) -> str` shape."""

    def __init__(self, answer="{}", raises=None):
        self.answer = answer
        self.raises = raises
        self.calls: list[dict] = []

    def route(self, data):
        self.calls.append(data)
        if self.raises is not None:
            raise self.raises
        return self.answer


class _Ctx:
    pass


class TestTheAdapterSatisfiesThePortWithoutTheExecutiveKnowingIt:
    def test_it_is_a_deliberation_port(self):
        assert isinstance(ModelRouterDeliberationPort(_Router()), DeliberationPort)

    def test_the_executive_package_does_not_import_this_adapter(self):
        """The import edge runs one way. The executive declares; this implements."""
        import ast
        from pathlib import Path

        package = Path(__file__).resolve().parents[1] / "bartholomew" / "executive"
        for path in package.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                module = None
                if isinstance(node, ast.Import):
                    module = " ".join(a.name for a in node.names)
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                if module and "integration" in module:
                    raise AssertionError(f"{path.name} imports {module}")

    def test_the_prompt_reaches_the_router_unchanged(self):
        router = _Router(answer="ok")
        port = ModelRouterDeliberationPort(router)
        assert port.deliberate("reason about this") == "ok"
        assert router.calls[0]["prompt"] == "reason about this"
        assert router.calls[0]["task_type"] == DELIBERATION_TASK_TYPE


class TestItFailsLoudlyRatherThanInventively:
    def test_a_provider_failure_raises_rather_than_returning_text(self):
        router = _Router(raises=RuntimeError("ollama unreachable"))
        with pytest.raises(DeliberationUnavailableError):
            ModelRouterDeliberationPort(router).deliberate("reason about this")

    def test_the_stub_backend_is_refused_rather_than_parsed(self):
        """ "Mock response for prompt: ..." must never look like a cognition answer."""
        router = _Router(answer="[llama3] Mock response for prompt: start a shopping...")
        with pytest.raises(DeliberationUnavailableError):
            ModelRouterDeliberationPort(router).deliberate("start a shopping list")

    @pytest.mark.parametrize("answer", [None, 42, {"steps": []}, b"bytes"])
    def test_a_non_string_answer_is_refused(self, answer):
        with pytest.raises(DeliberationUnavailableError):
            ModelRouterDeliberationPort(_Router(answer=answer)).deliberate("x")

    @pytest.mark.parametrize("prompt", ["", "   ", None])
    def test_it_refuses_to_ask_a_model_about_nothing(self, prompt):
        router = _Router()
        with pytest.raises(DeliberationUnavailableError):
            ModelRouterDeliberationPort(router).deliberate(prompt)
        assert router.calls == [], "no request should have been made at all"

    def test_it_does_not_retry(self):
        router = _Router(raises=RuntimeError("timeout"))
        with pytest.raises(DeliberationUnavailableError):
            ModelRouterDeliberationPort(router).deliberate("x")
        assert len(router.calls) == 1


class TestAFailingPortLeavesTheExecutiveAskingAQuestion:
    """The end-to-end consequence: an outage degrades to a question, never an action."""

    def test_a_raising_port_produces_no_steps(self):
        from bartholomew.actuation import devices
        from bartholomew.actuation.allowlists import ApplicationAllowlist
        from bartholomew.actuation.capabilities import CapabilityKind

        device = devices.EnrolledDevice(
            device_id="desk-pc",
            tenant_id="t",
            platform="windows",
            enrolled=True,
            capabilities=(devices.DeclaredCapability(kind=CapabilityKind.LAUNCH_APP, version=1),),
            applications=ApplicationAllowlist.from_pairs({"notepad": "C:\\notepad.exe"}),
        )
        port = ModelRouterDeliberationPort(_Router(raises=RuntimeError("down")))
        intent = deliberate_task("Start a shopping list for me.", device=device, port=port)
        assert not intent.actionable
        assert intent.steps == ()
        assert intent.ambiguities


class TestInstallation:
    def test_installing_hangs_the_port_on_the_context(self):
        ctx = _Ctx()
        installed = install_deliberation_port(ctx, _Router())
        assert ctx.deliberation_port is installed
        assert isinstance(installed, DeliberationPort)

    def test_no_router_installs_nothing_and_does_not_raise(self):
        """A deployment with no model keeps the deterministic recogniser."""
        ctx = _Ctx()
        assert install_deliberation_port(ctx, None) is None
        assert not hasattr(ctx, "deliberation_port")
