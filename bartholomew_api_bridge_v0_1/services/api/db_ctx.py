"""
SQLite connection context managers and WAL cleanup utilities for the API layer.

Re-exports bartholomew.kernel.db_ctx. This module's own near-duplicate
implementation -- whose wal_db() unconditionally ran a blocking TRUNCATE
checkpoint on every call, including on read-only liveness GET routes -- was
retired as Phase B stage B1's resolution of that duplicate-connection-policy
problem (see docs/B0_PERSISTENCE_BASELINE.md Sec 4 and ROADMAP.md's B1 row).
Kept at this import path (rather than updated at each call site) because
conftest.py and tests/test_sqlite_wal_concurrent_processes.py import
`db_ctx` alongside this package's own `fs_helpers` module, which is
unrelated to this consolidation and is not touched here.
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
