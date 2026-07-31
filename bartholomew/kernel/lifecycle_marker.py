"""
Daemon lifecycle clean-shutdown marker.

Phase B, stage B5. Persists whether the previous KernelDaemon run reached a
confirmed clean stop() -- so the next start() can detect and log (not
silently ignore) that the process previously exited without one
(crash, kill -9, unhandled exception outside start()/stop()'s own
try/except coverage). Conservative by design (per
docs/PHASE_B_OVERVIEW.md's B5 scope, "conservative unclean-start
recovery"): detection and logging only, no automatic remediation, no
blocked startup -- an aggressive automatic recovery action would itself be
a new, unrequested risk surface.

Stored in system_flags (the same generic key-value table
bartholomew/kernel/memory_store.py's schema already creates) under key
"daemon_lifecycle", reusing the existing table rather than adding a new one
for a single flag.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass

_FLAG_KEY = "daemon_lifecycle"


@dataclass(frozen=True)
class LifecycleMarker:
    instance_id: str
    state: str  # "running" | "clean_shutdown"
    started_at: int
    stopped_at: int | None


def read_marker(db_path: str) -> LifecycleMarker | None:
    """Read the current marker, or None if absent (fresh database, or a
    database older than this stage). Runs on whatever thread calls it --
    callers on the event loop should route this through a
    DedicatedDbExecutor (Phase B, stage B2), matching daemon.py's usage."""
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT value FROM system_flags WHERE key = ?",
                (_FLAG_KEY,),
            ).fetchone()
    except sqlite3.OperationalError:
        # system_flags doesn't exist yet (mem.init() hasn't run) -- nothing
        # to read, not an error.
        return None

    if row is None:
        return None

    try:
        data = json.loads(row[0])
        return LifecycleMarker(
            instance_id=data["instance_id"],
            state=data["state"],
            started_at=data["started_at"],
            stopped_at=data.get("stopped_at"),
        )
    except (ValueError, KeyError, TypeError):
        # Malformed marker: treat as absent rather than raising -- this is
        # a diagnostic aid, not a correctness-critical value (unlike the
        # Parking Brake's own fail-closed handling of a malformed legacy
        # value in bartholomew/kernel/governance/schema.py).
        return None


def write_marker(
    db_path: str,
    *,
    instance_id: str,
    state: str,
    started_at: int,
    stopped_at: int | None = None,
) -> None:
    """Upsert the marker. Runs on whatever thread calls it -- see
    read_marker()'s docstring."""
    payload = json.dumps(
        {
            "instance_id": instance_id,
            "state": state,
            "started_at": started_at,
            "stopped_at": stopped_at,
        },
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO system_flags(key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (_FLAG_KEY, payload, str(started_at if stopped_at is None else stopped_at)),
        )
        conn.commit()


def new_instance_id() -> str:
    return uuid.uuid4().hex
