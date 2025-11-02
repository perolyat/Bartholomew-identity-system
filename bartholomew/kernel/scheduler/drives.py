"""
Drive functions and registry.

Each drive is a lightweight async function that performs a specific
autonomy task and optionally emits a Nudge.
"""

import time
from collections.abc import Awaitable, Callable
from typing import Any

from .health import check_drift, get_system_metrics
from .models import Nudge


# Drive function signature
DriveFn = Callable[[Any], Awaitable[Nudge | None]]


async def drive_self_check(ctx: Any) -> Nudge | None:
    """
    Self-check drive: monitor system health and emit nudge if drift.

    Checks:
    - Database accessibility
    - Pending nudges accumulation
    - Stale daily reflections

    Args:
        ctx: Context object (typically KernelDaemon instance)

    Returns:
        Nudge if system drift detected, None otherwise
    """
    db_path = ctx.mem.db_path
    metrics = get_system_metrics(db_path)
    drift = check_drift(metrics)

    if drift:
        return Nudge(
            kind="system_health",
            message=f"System drift detected: {drift}",
            actions=[],
            reason="self_check_drift",
            created_ts=int(time.time()),
        )

    return None


async def drive_curiosity_probe(ctx: Any) -> Nudge | None:
    """
    Curiosity probe drive: occasionally prompt reflection or exploration.

    Emits gentle nudges to encourage user engagement with memory/journal.

    Args:
        ctx: Context object (typically KernelDaemon instance)

    Returns:
        Nudge with curiosity prompt, or None
    """
    # Simple curiosity nudge every so often
    # In a more advanced implementation, could analyze recent activity
    # and tailor the question

    prompts = [
        "What's one thing you learned today?",
        "How are you feeling right now?",
        "Any highlights from today worth remembering?",
    ]

    # For now, just cycle through prompts based on time
    prompt_idx = (int(time.time()) // 3600) % len(prompts)

    return Nudge(
        kind="curiosity",
        message=prompts[prompt_idx],
        actions=[{"label": "Reflect", "cmd": "open_journal"}, {"label": "Later", "cmd": "dismiss"}],
        reason="curiosity_probe",
        created_ts=int(time.time()),
    )


async def drive_reflection_micro(ctx: Any) -> Nudge | None:
    """
    Micro-reflection drive: insert small reflective moments.

    Creates lightweight reflection entries to track system state over time.
    Does not emit nudges by default.

    Args:
        ctx: Context object (typically KernelDaemon instance)

    Returns:
        None (reflections are inserted directly, no nudge needed)
    """
    # Get system metrics for reflection content
    db_path = ctx.mem.db_path
    metrics = get_system_metrics(db_path)

    # Build micro-reflection content
    content = f"""# Micro-Reflection

System health snapshot:
- Database: {"OK" if metrics["db_ok"] else "Error"}
- Pending nudges: {metrics["pending_nudges"]}
- Last daily reflection: {metrics["last_daily_reflection_ts"] or "None"}

Status: Autonomy loop active
"""

    # Insert reflection via MemoryStore
    try:
        await ctx.mem.insert_reflection(
            kind="micro_reflection",
            content=content,
            meta=metrics,
            ts=str(int(time.time())),  # MemoryStore expects string
            pinned=False,
        )
    except Exception as e:
        print(f"[Scheduler] Error inserting micro-reflection: {e}")

    # No nudge emitted for micro-reflections
    return None


async def drive_fts_optimize(ctx: Any) -> Nudge | None:
    """
    FTS optimize drive: run weekly FTS index optimization.

    Runs INSERT INTO memory_fts(memory_fts) VALUES('optimize') to merge
    FTS segments and reduce fragmentation for better search performance.

    Args:
        ctx: Context object (typically KernelDaemon instance)

    Returns:
        None (optimization runs silently)
    """
    db_path = ctx.mem.db_path

    try:
        from bartholomew.kernel.fts_client import FTSClient

        fts = FTSClient(db_path)
        fts.optimize()
        print("[Scheduler] FTS index optimized")
    except Exception as e:
        print(f"[Scheduler] Error optimizing FTS index: {e}")

    # No nudge emitted for maintenance tasks
    return None


# Drive registry with default cadences
REGISTRY: dict[str, dict[str, Any]] = {
    "self_check": {
        "fn": drive_self_check,
        "cadence": "every:900",  # Every 15 minutes
    },
    "curiosity_probe": {
        "fn": drive_curiosity_probe,
        "cadence": "window:3600:2",  # 2 times per hour
    },
    "reflection_micro": {
        "fn": drive_reflection_micro,
        "cadence": "every:7200",  # Every 2 hours
    },
    "fts_optimize": {
        "fn": drive_fts_optimize,
        "cadence": "every:604800",  # Every 7 days (weekly)
    },
}
