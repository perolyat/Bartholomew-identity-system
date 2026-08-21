"""
System health metrics interface for scheduler drives.

Provides lightweight metrics snapshots for drives like self_check.

WP-A1 / B-F001 note. `check_drift()` reports a high pending-nudge queue by
causing `drive_self_check` to emit a nudge -- which lands in that same queue.
In Test #1 that closed the loop: the warning incremented its own trigger, so
once the threshold was crossed the condition could never clear on its own and
each self-check made it worse. The fix is accounting, not cadence and not a
higher threshold: the drift check counts only the pending items a self-check
warning is not itself responsible for (`pending_nudges_countable`), so a
queue-size warning can never contribute to the count that produced it.

The true total is still measured and still reported as `pending_nudges`, and
still appears in the emitted warning, so the underlying condition remains
fully observable -- what is contained is the feedback loop, not the signal.
"""

import os
import sys
from typing import Any

# `reason` written by drive_self_check's drift nudge. Named here (rather than
# duplicated as a literal) because health accounting and the containment
# policy must agree on exactly which rows are self-check output.
SELF_CHECK_DRIFT_REASON = "self_check_drift"

# Countable pending items above which the queue is reported as drifting.
PENDING_NUDGE_DRIFT_THRESHOLD = 20


def get_system_metrics(db_path: str) -> dict[str, Any]:
    """
    Get a snapshot of system health metrics.

    Args:
        db_path: Path to the database file

    Returns:
        Dictionary with health metrics:
        - db_ok: Whether DB is accessible
        - db_size_bytes: Size of DB file
        - pending_nudges: Count of ALL pending nudges (the true total, for
          observability -- see this module's docstring)
        - pending_nudges_self_generated: Pending nudges created by
          self-check drift warnings themselves
        - pending_nudges_countable: pending_nudges minus
          pending_nudges_self_generated -- the only count check_drift()
          compares against the threshold (B-F001)
        - last_daily_reflection_ts: Timestamp of last daily reflection
        - python_version: Python version info
        - metrics_error: present only when the metrics read failed, in
          which case db_ok is False rather than silently optimistic
    """
    metrics: dict[str, Any] = {
        "db_ok": False,
        "db_size_bytes": 0,
        "pending_nudges": 0,
        "pending_nudges_self_generated": 0,
        "pending_nudges_countable": 0,
        "last_daily_reflection_ts": None,
        "python_version": sys.version,
    }

    # Check DB file exists and get size
    try:
        if os.path.exists(db_path):
            metrics["db_size_bytes"] = os.path.getsize(db_path)
            metrics["db_ok"] = True
    except Exception:
        pass

    # Query pending nudges count
    try:
        from bartholomew.kernel.db_ctx import wal_db

        with wal_db(db_path, timeout=5.0, label="get_system_metrics") as conn:
            cur = conn.execute("SELECT COUNT(*) FROM nudges WHERE status='pending'")
            row = cur.fetchone()
            metrics["pending_nudges"] = int(row[0] if row else 0)

            # Pending items that a self-check drift warning itself created.
            # Counted separately -- and excluded from the drift denominator
            # below -- so the warning cannot inflate its own trigger (B-F001).
            cur = conn.execute(
                "SELECT COUNT(*) FROM nudges WHERE status='pending' AND reason=?",
                (SELF_CHECK_DRIFT_REASON,),
            )
            row = cur.fetchone()
            metrics["pending_nudges_self_generated"] = int(row[0] if row else 0)
            metrics["pending_nudges_countable"] = max(
                0,
                metrics["pending_nudges"] - metrics["pending_nudges_self_generated"],
            )

            # Get last daily reflection timestamp
            cur = conn.execute(
                """SELECT ts FROM reflections
                   WHERE kind='daily_journal'
                   ORDER BY ts DESC LIMIT 1""",
            )
            row = cur.fetchone()
            if row:
                metrics["last_daily_reflection_ts"] = row[0]
    except Exception as e:
        # WP-A1 requirement E: uncertainty must be visible and safe. This
        # used to swallow the failure and leave pending_nudges at 0, so an
        # unreadable database reported as a perfectly healthy queue --
        # silently assuming the safe-looking answer. Record what went wrong
        # and drop db_ok, so check_drift() reports "database_unreachable"
        # (an existing drift class) instead of "no drift".
        metrics["db_ok"] = False
        metrics["metrics_error"] = f"{type(e).__name__}: {e}"

    return metrics


def check_drift(metrics: dict[str, Any]) -> str | None:
    """
    Check for system drift conditions.

    Args:
        metrics: System metrics from get_system_metrics()

    Returns:
        String describing drift condition, or None if healthy

    Examples of drift:
        - Many pending nudges accumulating
        - No daily reflection in >36 hours
        - DB not accessible
    """
    if not metrics.get("db_ok"):
        return "database_unreachable"

    # Deliberately NOT metrics["pending_nudges"]: see this module's docstring
    # and B-F001. Falls back to the raw total only for callers that predate
    # the countable metric (hand-built metric dicts in existing tests), which
    # preserves their behaviour exactly.
    pending = metrics.get("pending_nudges_countable")
    if pending is None:
        pending = metrics.get("pending_nudges", 0)
    if pending > PENDING_NUDGE_DRIFT_THRESHOLD:
        return f"high_pending_nudges:{pending}"

    # Check for stale daily reflection (>36 hours)
    last_daily = metrics.get("last_daily_reflection_ts")
    if last_daily:
        try:
            # If last_daily is ISO string, parse it
            if isinstance(last_daily, str):
                from datetime import datetime, timezone

                last_dt = datetime.fromisoformat(last_daily.replace("Z", "+00:00"))
                now_dt = datetime.now(timezone.utc)
                hours_since = (now_dt - last_dt).total_seconds() / 3600
                if hours_since > 36:
                    return f"stale_daily_reflection:{int(hours_since)}h"
        except Exception:
            pass

    return None
