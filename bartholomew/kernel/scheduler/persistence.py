"""
Persistence layer for scheduler state and activity.

Uses wal_db() context manager with busy_timeout for reliable WAL access.
Reuses canonical schema from MemoryStore and extends with scheduler tables.
"""

import os
import sys
import json
from typing import Optional, Dict, Any, List

# Import wal_db context manager
from bartholomew_api_bridge_v0_1.services.api.db_ctx import wal_db

# Import canonical schema from MemoryStore
sys.path.insert(  # noqa: E402
    0,
    os.path.join(os.path.dirname(__file__), "..", "..")
)
from kernel.memory_store import SCHEMA as MEMORY_STORE_SCHEMA


# Scheduler-specific schema extensions
SCHEDULER_SCHEMA = """
-- Scheduled tasks with cadence tracking
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id TEXT PRIMARY KEY,
    cadence TEXT NOT NULL,
    next_run_ts INTEGER NOT NULL,
    last_run_ts INTEGER,
    window_state TEXT
);

-- Tick records for drive executions
CREATE TABLE IF NOT EXISTS ticks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    started_ts INTEGER NOT NULL,
    finished_ts INTEGER,
    success INTEGER NOT NULL DEFAULT 0,
    idempotency_key TEXT UNIQUE,
    result_meta TEXT
);
CREATE INDEX IF NOT EXISTS idx_ticks_task_started 
    ON ticks(task_id, started_ts DESC);

-- Add integer timestamp columns to existing tables for scheduler use
-- These supplement the existing ISO text timestamps
ALTER TABLE nudges ADD COLUMN created_ts_s INTEGER;
ALTER TABLE nudges ADD COLUMN acted_ts_s INTEGER;
ALTER TABLE reflections ADD COLUMN ts_s INTEGER;
"""


def ensure_schema(db_path: str) -> None:
    """
    Ensure all required tables exist.
    
    Uses canonical MemoryStore schema plus scheduler extensions.
    Sets busy_timeout for reliable concurrent access.
    
    Args:
        db_path: Path to SQLite database
    """
    with wal_db(db_path, timeout=30.0) as conn:
        # Set busy timeout for concurrent access
        conn.execute("PRAGMA busy_timeout = 3000")
        
        # Execute canonical MemoryStore schema
        conn.executescript(MEMORY_STORE_SCHEMA)
        
        # Execute scheduler schema extensions
        # Use try/except for ALTER TABLE since columns may exist
        for stmt in SCHEDULER_SCHEMA.split(";"):
            stmt = stmt.strip()
            if not stmt:
                continue
            try:
                conn.execute(stmt)
            except Exception as e:
                # Ignore "duplicate column" errors
                if "duplicate column" not in str(e).lower():
                    raise
        
        conn.commit()


def upsert_scheduled_tasks(
    db_path: str,
    tasks: Dict[str, Dict[str, Any]]
) -> None:
    """
    Upsert scheduled tasks from registry.
    
    Args:
        db_path: Path to SQLite database
        tasks: Dict mapping task_id to {cadence, ...}
    """
    with wal_db(db_path, timeout=30.0) as conn:
        conn.execute("PRAGMA busy_timeout = 3000")
        
        for task_id, config in tasks.items():
            cadence = config["cadence"]
            
            # Check if task exists
            cur = conn.execute(
                "SELECT id FROM scheduled_tasks WHERE id = ?",
                (task_id,)
            )
            exists = cur.fetchone() is not None
            
            if not exists:
                # Insert new task with next_run = now
                import time
                now_ts = int(time.time())
                conn.execute(
                    """INSERT INTO scheduled_tasks 
                       (id, cadence, next_run_ts) 
                       VALUES (?, ?, ?)""",
                    (task_id, cadence, now_ts)
                )
        
        conn.commit()


def next_due_task(
    db_path: str,
    now_ts: int
) -> Optional[Dict[str, Any]]:
    """
    Get the next task that is due to run.
    
    Args:
        db_path: Path to SQLite database
        now_ts: Current time (UTC epoch seconds)
    
    Returns:
        Dict with task details, or None if no tasks due
    """
    with wal_db(db_path, timeout=30.0) as conn:
        conn.execute("PRAGMA busy_timeout = 3000")
        
        cur = conn.execute(
            """SELECT id, cadence, next_run_ts, last_run_ts, window_state
               FROM scheduled_tasks 
               WHERE next_run_ts <= ?
               ORDER BY next_run_ts ASC 
               LIMIT 1""",
            (now_ts,)
        )
        row = cur.fetchone()
        
        if not row:
            return None
        
        return {
            "id": row[0],
            "cadence": row[1],
            "next_run_ts": row[2],
            "last_run_ts": row[3],
            "window_state": row[4],
        }


def insert_tick(
    db_path: str,
    task_id: str,
    started_ts: int,
    finished_ts: Optional[int],
    success: int,
    idempotency_key: str,
    result_meta: Optional[Dict[str, Any]] = None
) -> int:
    """
    Insert a tick record.
    
    Args:
        db_path: Path to SQLite database
        task_id: ID of the task
        started_ts: Start time (UTC epoch seconds)
        finished_ts: Finish time (UTC epoch seconds), or None
        success: 0 or 1
        idempotency_key: Unique key to prevent duplicates
        result_meta: Optional metadata dict
    
    Returns:
        ID of inserted tick
    """
    result_json = json.dumps(result_meta) if result_meta else None
    
    with wal_db(db_path, timeout=30.0) as conn:
        conn.execute("PRAGMA busy_timeout = 3000")
        
        cur = conn.execute(
            """INSERT INTO ticks 
               (task_id, started_ts, finished_ts, success, 
                idempotency_key, result_meta)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (task_id, started_ts, finished_ts, success,
             idempotency_key, result_json)
        )
        conn.commit()
        return cur.lastrowid


def insert_nudge(
    db_path: str,
    kind: str,
    message: str,
    actions: List[Dict[str, Any]],
    reason: str,
    created_ts: int
) -> int:
    """
    Insert a nudge record.
    
    Args:
        db_path: Path to SQLite database
        kind: Nudge kind (e.g., "system_health", "curiosity")
        message: Nudge message
        actions: List of action dicts
        reason: Reason for nudge
        created_ts: Creation time (UTC epoch seconds)
    
    Returns:
        ID of inserted nudge
    """
    actions_json = json.dumps(actions)
    
    # Convert epoch seconds to ISO string for legacy column
    from datetime import datetime, timezone
    created_iso = datetime.fromtimestamp(
        created_ts, tz=timezone.utc
    ).isoformat()
    
    with wal_db(db_path, timeout=30.0) as conn:
        conn.execute("PRAGMA busy_timeout = 3000")
        
        cur = conn.execute(
            """INSERT INTO nudges 
               (kind, message, actions, reason, created_ts, 
                created_ts_s, status)
               VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
            (kind, message, actions_json, reason, created_iso, created_ts)
        )
        conn.commit()
        return cur.lastrowid


def insert_reflection(
    db_path: str,
    kind: str,
    content: str,
    meta: Optional[Dict[str, Any]],
    ts: int,
    pinned: bool = False
) -> int:
    """
    Insert a reflection record.
    
    Args:
        db_path: Path to SQLite database
        kind: Reflection kind (e.g., "micro_reflection")
        content: Reflection content
        meta: Optional metadata dict
        ts: Timestamp (UTC epoch seconds)
        pinned: Whether to pin this reflection
    
    Returns:
        ID of inserted reflection
    """
    meta_json = json.dumps(meta) if meta else None
    
    # Convert epoch seconds to ISO string for legacy column
    from datetime import datetime, timezone
    ts_iso = datetime.fromtimestamp(
        ts, tz=timezone.utc
    ).isoformat()
    
    with wal_db(db_path, timeout=30.0) as conn:
        conn.execute("PRAGMA busy_timeout = 3000")
        
        cur = conn.execute(
            """INSERT INTO reflections 
               (kind, content, meta, ts, ts_s, pinned)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (kind, content, meta_json, ts_iso, ts, 1 if pinned else 0)
        )
        conn.commit()
        return cur.lastrowid


def update_next_run(
    db_path: str,
    task_id: str,
    next_run_ts: int,
    last_run_ts: int,
    window_state: Optional[str] = None
) -> None:
    """
    Update task's next run time after execution.
    
    Args:
        db_path: Path to SQLite database
        task_id: ID of the task
        next_run_ts: Next run time (UTC epoch seconds)
        last_run_ts: Last run time (UTC epoch seconds)
        window_state: Optional window state JSON
    """
    with wal_db(db_path, timeout=30.0) as conn:
        conn.execute("PRAGMA busy_timeout = 3000")
        
        conn.execute(
            """UPDATE scheduled_tasks 
               SET next_run_ts = ?, last_run_ts = ?, window_state = ?
               WHERE id = ?""",
            (next_run_ts, last_run_ts, window_state, task_id)
        )
        conn.commit()
