"""
System health metrics interface for scheduler drives.

Provides lightweight metrics snapshots for drives like self_check.
"""

import os
import sys
from typing import Any


def get_system_metrics(db_path: str) -> dict[str, Any]:
    """
    Get a snapshot of system health metrics.

    Args:
        db_path: Path to the database file

    Returns:
        Dictionary with health metrics:
        - db_ok: Whether DB is accessible
        - db_size_bytes: Size of DB file
        - pending_nudges: Count of pending nudges
        - last_daily_reflection_ts: Timestamp of last daily reflection
        - python_version: Python version info
    """
    metrics: dict[str, Any] = {
        "db_ok": False,
        "db_size_bytes": 0,
        "pending_nudges": 0,
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
        # Import here to avoid circular dependency
        sys.path.insert(
            0,
            os.path.join(
                os.path.dirname(__file__),
                "..",
                "..",
                "..",
                "bartholomew_api_bridge_v0_1",
                "services",
                "api",
            ),
        )
        from db_ctx import wal_db

        with wal_db(db_path, timeout=5.0) as conn:
            cur = conn.execute("SELECT COUNT(*) FROM nudges WHERE status='pending'")
            row = cur.fetchone()
            metrics["pending_nudges"] = int(row[0] if row else 0)

            # Get last daily reflection timestamp
            cur = conn.execute(
                """SELECT ts FROM reflections
                   WHERE kind='daily_journal'
                   ORDER BY ts DESC LIMIT 1""",
            )
            row = cur.fetchone()
            if row:
                metrics["last_daily_reflection_ts"] = row[0]
    except Exception:
        # Best effort - don't fail if DB query fails
        pass

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

    pending = metrics.get("pending_nudges", 0)
    if pending > 20:
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
