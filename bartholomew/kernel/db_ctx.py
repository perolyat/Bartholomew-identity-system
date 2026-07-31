"""
SQLite connection context managers and WAL cleanup utilities.

This module provides connection management and checkpoint helpers that ensure
reliable WAL file cleanup on Windows. The key pattern is to checkpoint with a
fresh connection after closing all active connections, followed by a brief
delay to allow Windows to release file handles.

This is the single shared connection policy for the whole application
(Phase B, stage B1). `bartholomew_api_bridge_v0_1/services/api/db_ctx.py`
re-exports these functions rather than reimplementing them.
"""

import gc
import logging
import sqlite3
import threading
import time
from collections.abc import Iterable
from contextlib import contextmanager

_checkpoint_log = logging.getLogger("bartholomew.kernel.db_ctx.checkpoint")
_VALID_CHECKPOINT_MODES = {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}


def _windows_release_handles(delay: float = 0.05) -> None:
    """
    Force garbage collection and brief pause to help Windows release
    file handles.

    Call this after closing database connections to give Windows time
    to release file locks before attempting cleanup operations.

    Args:
        delay: Time to sleep in seconds after garbage collection
               (default: 0.05)
    """
    gc.collect()
    time.sleep(delay)


def set_wal_pragmas(conn: sqlite3.Connection) -> None:
    """
    Configure a connection for WAL mode with standard settings.

    Enables:
    - WAL (Write-Ahead Logging) mode for better concurrency
    - NORMAL synchronous mode (balance of safety and performance)
    - Foreign key constraints
    - Busy timeout for reliable concurrent access

    Args:
        conn: SQLite connection to configure

    Example:
        >>> conn = sqlite3.connect("data.db")
        >>> set_wal_pragmas(conn)
    """
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")


def connect(
    db_path_or_uri: str,
    *,
    uri: bool = False,
    timeout: float = 30.0,
    check_same_thread: bool = False,
) -> sqlite3.Connection:
    """
    Create a SQLite connection with standard settings.

    Thin wrapper around sqlite3.connect with sensible defaults for
    the Bartholomew application.

    Args:
        db_path_or_uri: Database file path or URI
        uri: Whether the path is a URI (default: False)
        timeout: Lock timeout in seconds (default: 30.0)
        check_same_thread: Allow cross-thread usage (default: False)

    Returns:
        SQLite connection object

    Example:
        >>> conn = connect("data.db")
        >>> conn = connect("file:data.db?mode=ro", uri=True)
    """
    return sqlite3.connect(
        db_path_or_uri,
        uri=uri,
        timeout=timeout,
        check_same_thread=check_same_thread,
    )


def wal_checkpoint(
    db_path_or_uri: str,
    *,
    uri: bool = False,
    timeout: float = 30.0,
    mode: str = "TRUNCATE",
    label: str = "",
) -> tuple[int, int, int] | None:
    """
    Run PRAGMA wal_checkpoint(<mode>) with a fresh, short-lived connection.

    mode:
      - "PASSIVE": non-blocking, best-effort. Never waits on other
        connections; may checkpoint fewer frames than requested. Safe to
        call from a hot path (e.g. scheduled diagnostics), but still not
        the routine default -- see wal_db().
      - "FULL" / "RESTART": accepted for completeness; unused today.
      - "TRUNCATE" (default for direct calls, matching this function's
        previous name/behavior as wal_checkpoint_truncate): blocks until
        it can get an exclusive lock and physically truncate the WAL
        file. Reserve for controlled maintenance or shutdown, once
        active readers/writers have been drained -- see
        MemoryStore.close() and the API bridge's atexit hook.

    TEMPORARY instrumentation (see DECISIONS.md's scheduler-persistence
    entry): logs start time, duration, thread, mode, label, the
    checkpoint's own result row (busy, log_frames, checkpointed_frames),
    and this connection's in_transaction state, at DEBUG level only --
    inert unless something explicitly raises this logger's level. Kept
    to help diagnose the open question there (why an earlier TRUNCATE
    checkpoint call exceeded its own busy-timeout in CI); remove once
    that's resolved.

    Args:
        db_path_or_uri: Database file path or URI
        uri: Whether the path is a URI (default: False)
        timeout: Lock timeout in seconds (default: 30.0)
        mode: Checkpoint mode (default: "TRUNCATE")
        label: Optional caller-supplied tag for the instrumentation log

    Returns:
        The PRAGMA's own result row (busy, log_frames, checkpointed_frames),
        or None if the connection could not be opened.

    Example:
        >>> wal_checkpoint("data.db", mode="PASSIVE")
    """
    if mode not in _VALID_CHECKPOINT_MODES:
        raise ValueError(
            f"wal_checkpoint: unknown mode {mode!r}, expected one of {_VALID_CHECKPOINT_MODES}",
        )

    checkpoint_conn = None
    row = None
    started = time.monotonic()
    try:
        checkpoint_conn = connect(db_path_or_uri, uri=uri, timeout=timeout)
        cur = checkpoint_conn.execute(f"PRAGMA wal_checkpoint({mode})")
        row = cur.fetchone()
        return row
    finally:
        duration_ms = (time.monotonic() - started) * 1000
        _checkpoint_log.debug(
            "wal_checkpoint db=%s mode=%s label=%s thread=%s duration_ms=%.1f "
            "result=%s in_transaction=%s",
            db_path_or_uri,
            mode,
            label,
            threading.current_thread().name,
            duration_ms,
            row,
            checkpoint_conn.in_transaction if checkpoint_conn else None,
        )
        if checkpoint_conn:
            try:
                checkpoint_conn.close()
            except sqlite3.Error:
                pass
        # Allow Windows to release file handles
        _windows_release_handles(delay=0.05)


def wal_checkpoint_truncate(
    db_path_or_uri: str,
    *,
    uri: bool = False,
    timeout: float = 30.0,
    label: str = "",
) -> None:
    """
    Run PRAGMA wal_checkpoint(TRUNCATE) with a fresh connection.

    Unchanged public API and behavior -- a thin wrapper over
    wal_checkpoint(mode="TRUNCATE"). This is the key pattern for reliable
    WAL cleanup on Windows: open a fresh short-lived connection, run the
    checkpoint, close it immediately, then give Windows a moment to
    release file handles. Reserve for controlled maintenance or shutdown
    once active readers/writers have been drained.

    Args:
        db_path_or_uri: Database file path or URI
        uri: Whether the path is a URI (default: False)
        timeout: Lock timeout in seconds (default: 30.0)
        label: Optional caller-supplied tag for the instrumentation log

    Example:
        >>> wal_checkpoint_truncate("data.db")
    """
    wal_checkpoint(db_path_or_uri, uri=uri, timeout=timeout, mode="TRUNCATE", label=label)


def close_quietly(conn: sqlite3.Connection | None) -> None:
    """
    Close a connection, suppressing any errors.

    Useful in finally blocks where you want to ensure cleanup
    even if the connection is already closed or in an error state.

    Args:
        conn: Connection to close, or None

    Example:
        >>> close_quietly(conn)
    """
    if conn:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def close_all_and_checkpoint(
    conns: Iterable[sqlite3.Connection],
    db_path_or_uri: str,
    *,
    uri: bool = False,
) -> None:
    """
    Close all connections and run checkpoint with a fresh connection.

    Useful when you have multiple connections that you need to clean up
    before running a final checkpoint operation.

    Args:
        conns: Iterable of connections to close
        db_path_or_uri: Database file path or URI for checkpoint
        uri: Whether the path is a URI (default: False)

    Example:
        >>> close_all_and_checkpoint([conn1, conn2], "data.db")
    """
    for conn in conns:
        close_quietly(conn)

    wal_checkpoint_truncate(db_path_or_uri, uri=uri)


@contextmanager
def wal_db(
    db_path_or_uri: str,
    *,
    uri: bool = False,
    timeout: float = 30.0,
    checkpoint: str | None = None,
    label: str = "",
):
    """
    Context manager for SQLite connections with WAL cleanup.

    This ensures:
    1. Connection is opened with standard settings
    2. WAL mode and pragmas are configured
    3. Connection is closed properly in finally block
    4. Optional checkpoint is run with a fresh connection (see `checkpoint`)
    5. Brief delay allows Windows to release file handles

    Usage pattern:
        with wal_db("data.db") as conn:
            conn.execute("INSERT INTO table VALUES (?)", (value,))
            conn.commit()

    The cleanup happens automatically when exiting the context, even on
    errors.

    Args:
        db_path_or_uri: Database file path or URI
        uri: Whether the path is a URI (default: False)
        timeout: Lock timeout in seconds (default: 30.0)
        checkpoint: What to run on exit, after the working connection is
            closed. Default None -- no explicit checkpoint at all;
            SQLite's own automatic WAL checkpoint (~every 1000 WAL
            pages) is the standard mechanism for routine reads/writes,
            and is the correct default for any hot path. WAL mode
            already guarantees readers see committed writes regardless
            of checkpoint timing, so this is a disk-layout/performance
            knob, not a correctness one. Pass "PASSIVE" for an explicit
            non-blocking best-effort checkpoint. Pass "TRUNCATE" only
            for controlled maintenance/shutdown -- prefer calling
            wal_checkpoint_truncate() directly instead in that case,
            since that's what it's for.
        label: Optional caller-supplied tag for the instrumentation log
            (only used when `checkpoint` is set)

    Yields:
        SQLite connection configured for WAL mode

    Example:
        >>> with wal_db("data.db") as conn:
        ...     conn.execute("CREATE TABLE IF NOT EXISTS t(x)")
        ...     conn.commit()
    """
    conn = None
    try:
        conn = connect(db_path_or_uri, uri=uri, timeout=timeout)
        set_wal_pragmas(conn)
        yield conn
    finally:
        # Close the working connection first
        close_quietly(conn)

        # Then checkpoint with a fresh connection, if requested
        if checkpoint:
            wal_checkpoint(db_path_or_uri, uri=uri, timeout=timeout, mode=checkpoint, label=label)
