"""
SQLite connection context managers and WAL cleanup utilities.

Re-exports `bartholomew.kernel.db_ctx`, the single shared connection policy
(Phase B, stage B1). Previously this module held its own independent copy of
the same functions, which had drifted from the kernel copy in two ways: it
had no generic `wal_checkpoint(mode=...)` (only a hardcoded TRUNCATE), and
its `wal_db()` unconditionally ran a blocking `TRUNCATE` checkpoint on every
call rather than defaulting to SQLite's own automatic WAL checkpoint. Kept as
its own importable module, at its existing path, so callers (`db.py`,
`routes/liveness.py`, `app.py`) don't need any changes.
"""

from bartholomew.kernel.db_ctx import (
    close_all_and_checkpoint,
    close_quietly,
    connect,
    set_wal_pragmas,
    wal_checkpoint,
    wal_checkpoint_truncate,
    wal_db,
)

__all__ = [
    "close_all_and_checkpoint",
    "close_quietly",
    "connect",
    "set_wal_pragmas",
    "wal_checkpoint",
    "wal_checkpoint_truncate",
    "wal_db",
]
