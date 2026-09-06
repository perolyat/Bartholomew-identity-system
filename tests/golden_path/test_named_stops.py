"""The named-stop mechanism is itself honest, and every scenario declares its legs.

A skip is the easiest way to make a suite look green. This file makes sure the
suite cannot do that quietly: the component probe answers False for a leg that
is not here and for a leg that does not exist, the skip reason names the owning
frozen package, and each scenario file that depends on a leg declares it through
`components.require` rather than through a bare `pytest.skip`.

Runs in the PR Fast tier.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from tests.golden_path import components

HERE = pathlib.Path(__file__).parent


def test_every_component_names_a_frozen_owner_and_a_published_symbol():
    for key, component in components.COMPONENTS.items():
        assert component.key == key
        assert component.owner in {"W03-A", "W03-B", "W03-C", "W03-D"}
        assert component.module.startswith("bartholomew.")
        assert component.symbol
        assert component.provides


def test_an_unknown_component_is_never_present():
    assert components.probe("a_leg_nobody_built") is False
    assert components.missing("a_leg_nobody_built") == []
    # And asking for nothing stops nothing.
    assert components.named_stop() is None


def test_a_present_component_is_reported_present():
    """Non-vacuity: the probe is not a constant False."""
    components.COMPONENTS["_self_test"] = components.Component(
        key="_self_test",
        owner="W03-C",
        module="bartholomew.actuation.capabilities",
        symbol="CapabilityKind",
        provides="a leg that is certainly here",
    )
    try:
        assert components.probe("_self_test") is True
        assert components.named_stop("_self_test") is None
    finally:
        del components.COMPONENTS["_self_test"]


def test_a_missing_component_names_its_owner_in_the_stop():
    components.COMPONENTS["_absent"] = components.Component(
        key="_absent",
        owner="W03-B",
        module="bartholomew.no_such_package.seam",
        symbol="nothing",
        provides="a leg that is certainly not here",
    )
    try:
        reason = components.named_stop("_absent")
        assert reason is not None
        assert reason.startswith("NAMED STOP")
        assert "W03-B" in reason
        assert "a leg that is certainly not here" in reason
        with pytest.raises(pytest.skip.Exception):
            components.require("_absent")
    finally:
        del components.COMPONENTS["_absent"]


def test_the_probe_reports_what_this_tree_actually_carries():
    """Recorded, not asserted: the run log shows which legs were present.

    This test always passes; its value is the printed line, which is what a
    reader of a CI log uses to tell a composed run from a branch-only run.
    """
    present = sorted(k for k in components.COMPONENTS if components.probe(k))
    absent = sorted(k for k in components.COMPONENTS if not components.probe(k))
    print(f"\nGolden Path legs present: {present}\nGolden Path legs absent:  {absent}")


def test_no_scenario_skips_without_naming_the_leg():
    """A bare `pytest.skip(...)` in a scenario is a silent omission. Refused."""
    offending: list[str] = []
    for path in sorted(HERE.glob("test_path_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                f"{getattr(func.value, 'id', '')}.{func.attr}"
                if isinstance(func, ast.Attribute)
                else getattr(func, "id", "")
            )
            if name in {"pytest.skip", "skip"}:
                offending.append(f"{path.name}:{node.lineno}")
    assert not offending, f"scenarios must stop by name via components.require: {offending}"


def test_every_scenario_file_carries_a_docstring_naming_what_is_real():
    """The harness's honesty contract, checked per file."""
    for path in sorted(HERE.glob("test_path_*.py")):
        doc = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or ""
        assert "Golden Path" in doc, path.name
        assert len(doc) > 200, f"{path.name} does not say what is real and what is not"
