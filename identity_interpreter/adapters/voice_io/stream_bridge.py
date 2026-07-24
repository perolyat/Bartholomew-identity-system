"""
Voice stream bridge stub.

The voice-streaming *capability* remains an inert placeholder (`_perform_stream`)
-- real microphone/transcription/streaming-lifecycle/device work is Stage 6.
What changed (MASTER_PLAN.md item 11.21): `start_stream()` is now a thin
compatibility wrapper that delegates *exclusively* to the governed Runtime
Contract seam (`bartholomew.kernel.runtime_contract.run_voice_through_runtime_contract`),
so every voice-stream start constructs an Observation/CandidateAction and
passes through the same Governance path (parking brake + Identity Policy +
fail-closed device consent) and Reflection as every other surface. The
placeholder is reachable ONLY through that seam (passed as `stream_fn`); it is
never invoked directly in production.
"""

import asyncio


def _perform_stream() -> None:
    """
    Inert placeholder for the actual voice-streaming capability.

    Reachable ONLY through `run_voice_through_runtime_contract()` (which
    receives it as `stream_fn` and calls it exactly once, and only after
    governance approves a single start). Never invoked directly anywhere in
    production -- a structural test enforces that. Stage 6 replaces this body
    with real streaming; doing so must not route around the seam above.
    """
    print("Voice stream started")


def start_stream(db_path: str = None) -> None:
    """
    Public compatibility entry point for starting voice streaming.

    Delegates exclusively to the governed Runtime Contract seam. Preserves the
    historical contract: returns ``None`` in all cases. When any governance
    gate (parking brake, Identity Policy, or device consent) denies the single
    start attempt, the (inert) capability never runs; when the start is
    approved, the capability runs exactly once.

    Args:
        db_path: Optional database path for the brake check / reflection sink.

    Returns:
        None.
    """
    from bartholomew.kernel.runtime_contract import run_voice_through_runtime_contract

    asyncio.run(
        run_voice_through_runtime_contract(db_path=db_path, stream_fn=_perform_stream),
    )
    return None
