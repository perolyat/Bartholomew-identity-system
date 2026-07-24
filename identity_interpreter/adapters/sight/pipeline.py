"""
Sight pipeline stub.

The visual-capture *capability* remains an inert placeholder (`_perform_capture`)
-- real camera/computer-vision/device-lifecycle work is Stage 6. What changed
(MASTER_PLAN.md item 11.21): `start_capture()` is now a thin compatibility
wrapper that delegates *exclusively* to the governed Runtime Contract seam
(`bartholomew.kernel.runtime_contract.run_sight_through_runtime_contract`), so
every sight-capture start constructs an Observation/CandidateAction and passes
through the same Governance path (parking brake + Identity Policy + fail-closed
device consent) and Reflection as every other surface. The placeholder is
reachable ONLY through that seam (passed as `capture_fn`); it is never invoked
directly in production.
"""

import asyncio
from typing import Any


def _perform_capture() -> dict[str, Any]:
    """
    Inert placeholder for the actual visual-capture capability.

    Reachable ONLY through `run_sight_through_runtime_contract()` (which
    receives it as `capture_fn` and calls it exactly once, and only after
    governance approves a single start). Never invoked directly anywhere in
    production -- a structural test enforces that. Stage 6 replaces this body
    with real capture; doing so must not route around the seam above.
    """
    return {"status": "capturing"}


def start_capture(db_path: str = None) -> dict[str, Any]:
    """
    Public compatibility entry point for starting visual capture.

    Delegates exclusively to the governed Runtime Contract seam. Preserves the
    historical return shape: ``{"blocked": True}`` when any governance gate
    (parking brake, Identity Policy, or device consent) denies the single
    start attempt, and ``{"blocked": False, "status": "capturing"}`` when the
    start is approved and the (inert) capability runs.

    Args:
        db_path: Optional database path for the brake check / reflection sink.

    Returns:
        Dict with capture status or blocked flag (see above).
    """
    from bartholomew.kernel.runtime_contract import run_sight_through_runtime_contract

    result = asyncio.run(
        run_sight_through_runtime_contract(db_path=db_path, capture_fn=_perform_capture),
    )
    if result.started:
        return {"blocked": False, **(result.result or {})}
    return {"blocked": True}
