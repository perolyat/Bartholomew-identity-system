"""
Persistence layer for scheduler state and activity.

Uses wal_db() context manager with busy_timeout for reliable WAL access.
Reuses canonical schema from MemoryStore and extends with scheduler tables.
"""

import json
from typing import Any

# Import wal_db context manager
from bartholomew.kernel.db_ctx import wal_db

# Import canonical schema from MemoryStore
from bartholomew.kernel.memory_store import SCHEMA as MEMORY_STORE_SCHEMA

from . import containment

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

-- WP-A1 queue containment (B-F001 / NUDGE-F001, safety gate S1).
-- Added here, alongside the existing scheduler-owned nudges columns above,
-- because containment is a scheduler concept: MemoryStore.SCHEMA remains the
-- one authority for the nudges *table*, this file remains the one authority
-- for the scheduler's extensions to it.
--   dedup_key          deterministic equivalence key (see containment.py);
--                      NULL for anything the policy does not cover.
--   occurrence_count   how many times this equivalent item has been emitted,
--                      including the suppressed repeats -- so containment
--                      reduces the queue without hiding the frequency.
--   last_occurrence_ts most recent emission, suppressed or not.
ALTER TABLE nudges ADD COLUMN dedup_key TEXT;
ALTER TABLE nudges ADD COLUMN occurrence_count INTEGER;
ALTER TABLE nudges ADD COLUMN last_occurrence_ts INTEGER;
"""

# Statements that must run after the ALTERs above have added their columns.
# Kept separate from SCHEDULER_SCHEMA so a "duplicate column" tolerance loop
# can never skip an index whose column was added by an earlier run.
SCHEDULER_SCHEMA_POST = """
-- The invariant behind NUDGE-F001, enforced by SQLite rather than by
-- application logic: at most one *unresolved* item per equivalence key.
-- Partial, so resolving (acked/dismissed) an item frees its key for a later
-- genuinely-eligible occurrence, and so unkeyed rows are unaffected.
CREATE UNIQUE INDEX IF NOT EXISTS idx_nudges_dedup_pending
    ON nudges(dedup_key)
    WHERE dedup_key IS NOT NULL AND status = 'pending';

-- Machine-inspectable evidence for every containment decision that changed
-- what would otherwise have been written (WP-A1 requirement D). Append-only:
-- nothing in this codebase updates or deletes rows here.
CREATE TABLE IF NOT EXISTS nudge_containment_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    reason TEXT,
    outcome TEXT NOT NULL,
    existing_nudge_id INTEGER,
    detail TEXT,
    created_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nudge_containment_events_key
    ON nudge_containment_events(dedup_key, created_ts DESC);
"""

# Containment outcomes recorded in `nudge_containment_events.outcome` and
# returned by `insert_nudge_contained()`.
OUTCOME_INSERTED = "inserted"
OUTCOME_SUPPRESSED_DUPLICATE = "suppressed_duplicate"
OUTCOME_INSERTED_NOT_ELIGIBLE = "inserted_not_eligible"


def ensure_schema(db_path: str) -> None:
    """
    Ensure all required tables exist.

    Uses canonical MemoryStore schema plus scheduler extensions.
    Sets busy_timeout for reliable concurrent access.

    Args:
        db_path: Path to SQLite database
    """
    with wal_db(db_path, timeout=30.0, label="ensure_schema") as conn:
        # Set busy timeout for concurrent access
        conn.execute("PRAGMA busy_timeout = 3000")

        # Execute canonical MemoryStore schema
        conn.executescript(MEMORY_STORE_SCHEMA)

        # Execute scheduler schema extensions
        # Use try/except for ALTER TABLE since columns may exist
        for raw_stmt in SCHEDULER_SCHEMA.split(";"):
            stmt = raw_stmt.strip()
            if not stmt:
                continue
            try:
                conn.execute(stmt)
            except Exception as e:
                # Ignore "duplicate column" errors
                if "duplicate column" not in str(e).lower():
                    raise

        # Statements depending on the columns the ALTERs above guarantee.
        # Unconditional (no duplicate-column tolerance): every statement here
        # is IF NOT EXISTS, so a failure is a real failure and must surface --
        # a missing idx_nudges_dedup_pending would silently turn the
        # NUDGE-F001 invariant back off.
        conn.executescript(SCHEDULER_SCHEMA_POST)

        conn.commit()


def upsert_scheduled_tasks(db_path: str, tasks: dict[str, dict[str, Any]]) -> None:
    """
    Upsert scheduled tasks from registry.

    Args:
        db_path: Path to SQLite database
        tasks: Dict mapping task_id to {cadence, ...}
    """
    with wal_db(db_path, timeout=30.0, label="upsert_scheduled_tasks") as conn:
        conn.execute("PRAGMA busy_timeout = 3000")

        for task_id, config in tasks.items():
            cadence = config["cadence"]

            # Check if task exists
            cur = conn.execute("SELECT id FROM scheduled_tasks WHERE id = ?", (task_id,))
            exists = cur.fetchone() is not None

            if not exists:
                # Insert new task with next_run = now
                import time

                now_ts = int(time.time())
                conn.execute(
                    """INSERT INTO scheduled_tasks
                       (id, cadence, next_run_ts)
                       VALUES (?, ?, ?)""",
                    (task_id, cadence, now_ts),
                )

        conn.commit()


def next_due_task(db_path: str, now_ts: int) -> dict[str, Any] | None:
    """
    Get the next task that is due to run.

    Args:
        db_path: Path to SQLite database
        now_ts: Current time (UTC epoch seconds)

    Returns:
        Dict with task details, or None if no tasks due
    """
    with wal_db(db_path, timeout=30.0, label="next_due_task") as conn:
        conn.execute("PRAGMA busy_timeout = 3000")

        cur = conn.execute(
            """SELECT id, cadence, next_run_ts, last_run_ts, window_state
               FROM scheduled_tasks
               WHERE next_run_ts <= ?
               ORDER BY next_run_ts ASC
               LIMIT 1""",
            (now_ts,),
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


def tick_exists(db_path: str, idempotency_key: str) -> bool:
    """
    Check whether a tick with this idempotency key has already been recorded.

    Factored out of scheduler/loop.py's inline restart-protection check so
    that call, too, goes through SchedulerStore rather than touching
    wal_db()/sqlite3 directly from the scheduler's async loop.

    Args:
        db_path: Path to SQLite database
        idempotency_key: Unique key identifying the tick

    Returns:
        True if a tick with this idempotency key already exists
    """
    with wal_db(db_path, timeout=5.0, label="tick_exists") as conn:
        conn.execute("PRAGMA busy_timeout = 3000")
        cur = conn.execute(
            "SELECT id FROM ticks WHERE idempotency_key = ?",
            (idempotency_key,),
        )
        return cur.fetchone() is not None


def insert_tick(
    db_path: str,
    task_id: str,
    started_ts: int,
    finished_ts: int | None,
    success: int,
    idempotency_key: str,
    result_meta: dict[str, Any] | None = None,
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

    with wal_db(db_path, timeout=30.0, label="insert_tick") as conn:
        conn.execute("PRAGMA busy_timeout = 3000")

        cur = conn.execute(
            """INSERT INTO ticks
               (task_id, started_ts, finished_ts, success,
                idempotency_key, result_meta)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (task_id, started_ts, finished_ts, success, idempotency_key, result_json),
        )
        conn.commit()
        return cur.lastrowid


def insert_nudge(
    db_path: str,
    kind: str,
    message: str,
    actions: list[dict[str, Any]],
    reason: str,
    created_ts: int,
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

    created_iso = datetime.fromtimestamp(created_ts, tz=timezone.utc).isoformat()

    with wal_db(db_path, timeout=30.0, label="insert_nudge") as conn:
        conn.execute("PRAGMA busy_timeout = 3000")

        cur = conn.execute(
            """INSERT INTO nudges
               (kind, message, actions, reason, created_ts,
                created_ts_s, status)
               VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
            (kind, message, actions_json, reason, created_iso, created_ts),
        )
        conn.commit()
        return cur.lastrowid


def insert_nudge_contained(
    db_path: str,
    kind: str,
    message: str,
    actions: list[dict[str, Any]],
    reason: str,
    created_ts: int,
    escalation: str | None = None,
) -> dict[str, Any]:
    """
    Insert a nudge under WP-A1 queue containment (B-F001 / NUDGE-F001, S1).

    This is `insert_nudge()` plus the containment decision, executed as ONE
    atomic statement against the partial UNIQUE index
    `idx_nudges_dedup_pending` rather than as an application-level
    "does an equivalent one already exist?" read followed by a later write.
    That distinction is the point: a read-then-write would race two writers
    and, worse, would have to *interpret* a failed read -- and interpreting
    a read failure as "already represented" is exactly the silent
    obligation loss D2 forbids. Here there is no such branch. Either SQLite
    accepted the row or it rejected it because an equivalent unresolved row
    demonstrably exists.

    Any other error (locked database, missing table, disk failure) is
    raised, never swallowed. The caller is responsible for making that
    visible; see `scheduler/loop.py`.

    Nothing here deletes, resolves, expires or sheds an existing row.
    On suppression the surviving row's occurrence counter is incremented
    and an append-only `nudge_containment_events` row is written in the
    same transaction, so the queue is smaller but the emission frequency
    and the reason for every suppression stay fully inspectable.

    Args:
        db_path: Path to SQLite database
        kind: Nudge kind (e.g. "system_health", "curiosity")
        message: Nudge message
        actions: List of action dicts
        reason: Reason for nudge -- also the containment-policy allowlist key
        created_ts: Creation time (UTC epoch seconds)
        escalation: Optional explicit escalation label. When set it is part
            of the equivalence key, so an explicitly distinct escalation is
            always a distinct unresolved item.

    Returns:
        Dict with:
          outcome: one of OUTCOME_INSERTED, OUTCOME_INSERTED_NOT_ELIGIBLE,
                   OUTCOME_SUPPRESSED_DUPLICATE
          nudge_id: id of the row written, or None when suppressed
          existing_nudge_id: id of the surviving equivalent row when
                   suppressed, else None
          dedup_key: the equivalence key, or None when not policy-eligible
    """
    dedup_key = containment.dedup_key_for(kind, message, reason, escalation)
    actions_json = json.dumps(actions)

    # Convert epoch seconds to ISO string for legacy column
    from datetime import datetime, timezone

    created_iso = datetime.fromtimestamp(created_ts, tz=timezone.utc).isoformat()

    with wal_db(db_path, timeout=30.0, label="insert_nudge_contained") as conn:
        conn.execute("PRAGMA busy_timeout = 3000")

        if dedup_key is None:
            # Not policy-eligible: byte-for-byte the pre-WP-A1 behaviour.
            cur = conn.execute(
                """INSERT INTO nudges
                   (kind, message, actions, reason, created_ts,
                    created_ts_s, status)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
                (kind, message, actions_json, reason, created_iso, created_ts),
            )
            conn.commit()
            return {
                "outcome": OUTCOME_INSERTED_NOT_ELIGIBLE,
                "nudge_id": cur.lastrowid,
                "existing_nudge_id": None,
                "dedup_key": None,
            }

        # BEGIN IMMEDIATE: take the write lock up front so the insert
        # attempt and the evidence/counter write that may follow it are one
        # indivisible unit, and so a concurrent writer contends here (and is
        # retried by busy_timeout) rather than interleaving between them.
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.execute(
                """INSERT INTO nudges
                   (kind, message, actions, reason, created_ts,
                    created_ts_s, status, dedup_key,
                    occurrence_count, last_occurrence_ts)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, 1, ?)
                   ON CONFLICT(dedup_key)
                       WHERE dedup_key IS NOT NULL AND status = 'pending'
                   DO NOTHING""",
                (
                    kind,
                    message,
                    actions_json,
                    reason,
                    created_iso,
                    created_ts,
                    dedup_key,
                    created_ts,
                ),
            )

            if cur.rowcount == 1:
                nudge_id = cur.lastrowid
                conn.commit()
                return {
                    "outcome": OUTCOME_INSERTED,
                    "nudge_id": nudge_id,
                    "existing_nudge_id": None,
                    "dedup_key": dedup_key,
                }

            # Rejected by the partial unique index: an equivalent
            # UNRESOLVED item provably exists right now, in this
            # transaction. Record why, and make the repeat countable.
            existing = conn.execute(
                """SELECT id, occurrence_count FROM nudges
                   WHERE dedup_key = ? AND status = 'pending'""",
                (dedup_key,),
            ).fetchone()
            existing_id = existing[0] if existing else None
            prior_count = (existing[1] or 1) if existing else None

            if existing_id is not None:
                conn.execute(
                    """UPDATE nudges
                       SET occurrence_count = ?, last_occurrence_ts = ?
                       WHERE id = ?""",
                    (prior_count + 1, created_ts, existing_id),
                )

            conn.execute(
                """INSERT INTO nudge_containment_events
                   (dedup_key, kind, reason, outcome, existing_nudge_id,
                    detail, created_ts)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    dedup_key,
                    kind,
                    reason,
                    OUTCOME_SUPPRESSED_DUPLICATE,
                    existing_id,
                    json.dumps(
                        {
                            "suppressed_message": message,
                            "escalation": escalation,
                            "occurrence_count": (
                                prior_count + 1 if prior_count is not None else None
                            ),
                        },
                    ),
                    created_ts,
                ),
            )
            conn.commit()
            return {
                "outcome": OUTCOME_SUPPRESSED_DUPLICATE,
                "nudge_id": None,
                "existing_nudge_id": existing_id,
                "dedup_key": dedup_key,
            }
        except Exception:
            conn.rollback()
            raise


def list_containment_events(
    db_path: str,
    limit: int = 100,
    dedup_key: str | None = None,
) -> list[dict[str, Any]]:
    """
    Read the append-only containment evidence log (WP-A1 requirement D).

    Args:
        db_path: Path to SQLite database
        limit: Maximum rows to return, newest first
        dedup_key: Optional filter to one equivalence key

    Returns:
        List of event dicts.
    """
    sql = """SELECT id, dedup_key, kind, reason, outcome, existing_nudge_id,
                    detail, created_ts
             FROM nudge_containment_events"""
    params: list[Any] = []
    if dedup_key is not None:
        sql += " WHERE dedup_key = ?"
        params.append(dedup_key)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    with wal_db(db_path, timeout=5.0, label="list_containment_events") as conn:
        conn.execute("PRAGMA busy_timeout = 3000")
        rows = conn.execute(sql, tuple(params)).fetchall()

    return [
        {
            "id": r[0],
            "dedup_key": r[1],
            "kind": r[2],
            "reason": r[3],
            "outcome": r[4],
            "existing_nudge_id": r[5],
            "detail": json.loads(r[6]) if r[6] else None,
            "created_ts": r[7],
        }
        for r in rows
    ]


def insert_reflection(
    db_path: str,
    kind: str,
    content: str,
    meta: dict[str, Any] | None,
    ts: int,
    pinned: bool = False,
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

    ts_iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

    with wal_db(db_path, timeout=30.0, label="insert_reflection") as conn:
        conn.execute("PRAGMA busy_timeout = 3000")

        cur = conn.execute(
            """INSERT INTO reflections
               (kind, content, meta, ts, ts_s, pinned)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (kind, content, meta_json, ts_iso, ts, 1 if pinned else 0),
        )
        conn.commit()
        return cur.lastrowid


def update_next_run(
    db_path: str,
    task_id: str,
    next_run_ts: int,
    last_run_ts: int,
    window_state: str | None = None,
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
    with wal_db(db_path, timeout=30.0, label="update_next_run") as conn:
        conn.execute("PRAGMA busy_timeout = 3000")

        conn.execute(
            """UPDATE scheduled_tasks
               SET next_run_ts = ?, last_run_ts = ?, window_state = ?
               WHERE id = ?""",
            (next_run_ts, last_run_ts, window_state, task_id),
        )
        conn.commit()
