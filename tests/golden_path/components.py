"""Which legs of the Wave 3 loop this tree actually carries, and what to do about it.

W03-E's committed diff is its own bounded package: it consumes W03-A through
W03-D by their published surfaces and merges none of them. On the W03-E branch
alone, three of the five Golden Paths therefore reach a leg that is not in the
tree, and the whole point of this module is that they must say so **by name**
rather than quietly reporting a pass.

Two rules hold everywhere in this suite:

1. **A missing leg is a named stop, never a silent omission.** A scenario that
   cannot run the interpret leg because `bartholomew/executive/` is not in this
   tree skips with the frozen package that owns it named in the reason, and the
   skip reason is the same string a reader of the handoff will look for.
2. **A present leg is never simulated.** If the package is here, the scenario
   runs against it for real. There is no fallback path that approximates a leg
   with a double, because a scenario that could pass either way would prove
   nothing about the one that shipped.

`probe()` answers only "is the module importable and does it carry the
published symbol". It deliberately does not check versions or behaviour: a leg
that is present but broken must fail the scenario loudly, not be skipped.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass

import pytest


@dataclass(frozen=True)
class Component:
    """One leg of the loop, the package that owns it, and how to detect it."""

    key: str
    owner: str
    module: str
    symbol: str
    #: What a scenario loses without it, in the words the handoff uses.
    provides: str


#: Every leg a Golden Path can depend on, keyed by the frozen builder that owns
#: it. The `module`/`symbol` pairs are each session's own published surface, as
#: recorded in its handoff -- not a guess at an internal name.
COMPONENTS: dict[str, Component] = {
    "observation_event": Component(
        key="observation_event",
        owner="W03-A",
        module="bartholomew.multimodal.observation",
        symbol="ObservationEvent",
        provides="the observed-vs-inferred observation event",
    ),
    "read_back": Component(
        key="read_back",
        owner="W03-A",
        module="bartholomew.multimodal.readback",
        symbol="read_back",
        provides="the governed read-back the Verify leg consumes",
    ),
    "executive": Component(
        key="executive",
        owner="W03-B",
        module="bartholomew.executive.seam",
        symbol="run_executive_task_through_runtime_contract",
        provides="interpret -> capability selection -> envelope proposal",
    ),
    "action_abort": Component(
        key="action_abort",
        owner="W03-C",
        module="bartholomew.actuation.seam",
        symbol="evaluate_action_abort_through_runtime_contract",
        provides="stop-after-lease, so an engaged brake halts a leased action",
    ),
    "verify_evidence": Component(
        key="verify_evidence",
        owner="W03-C",
        module="bartholomew.actuation.result",
        symbol="VERIFY_METHODS",
        provides=(
            "the `verified` / `verify_method` evidence keys, without which the "
            "envelope's evidence allowlist drops a device's verification report"
        ),
    ),
    "retrieval_verdict": Component(
        key="retrieval_verdict",
        owner="W03-D",
        module="bartholomew.kernel.consent_gate",
        symbol="ValidityVerdict",
        provides="the retrieval-side validity verdict recalled memory carries",
    ),
}


def probe(key: str) -> bool:
    """Whether this tree carries the named leg. Unknown keys are always False."""
    component = COMPONENTS.get(key)
    if component is None:
        return False
    try:
        module = importlib.import_module(component.module)
    except ImportError:
        return False
    return hasattr(module, component.symbol)


def missing(*keys: str) -> list[Component]:
    """The named legs this tree does not carry, in the order asked for."""
    return [COMPONENTS[key] for key in keys if key in COMPONENTS and not probe(key)]


def named_stop(*keys: str) -> str | None:
    """The skip reason for a scenario needing `keys`, or `None` if all are here.

    The reason names the frozen package that owns each missing leg, so a run
    log records *which builder's head* a scenario was waiting on rather than
    "skipped".
    """
    absent = missing(*keys)
    if not absent:
        return None
    parts = [f"{c.owner} ({c.provides}) is not in this tree" for c in absent]
    return (
        "NAMED STOP -- this Golden Path leg needs a frozen builder head that "
        "W03-E does not merge: " + "; ".join(parts) + ". W03-F composes the heads; "
        "this scenario runs for real there and in W03-E's composition worktree."
    )


def require(*keys: str) -> None:
    """Skip with a named stop, or return and let the scenario run for real."""
    reason = named_stop(*keys)
    if reason:
        pytest.skip(reason)
