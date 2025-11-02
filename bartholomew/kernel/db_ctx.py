"""
SQLite connection context managers and WAL cleanup utilities for kernel.

This module provides connection management and checkpoint helpers that ensure
reliable WAL file cleanup on Windows. The key pattern is to checkpoint with a
fresh connection after closing all active connections, followed by a brief
delay to allow Windows to release file handles.

This is a kernel-local copy to avoid coupling to the API layer.
"""

import gc
import sqlite3
import time
from collections.abc import Iterable
from contextlib import contextmanager


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


def wal_checkpoint_truncate(
    db_path_or_uri: str,
    *,
    uri: bool = False,
    timeout: float = 30.0,
) -> None:
    """
    Run PRAGMA wal_checkpoint(TRUNCATE) with a fresh connection.

    This is the key pattern for reliable WAL cleanup on Windows:
    1. Open a fresh short-lived connection
    2. Run PRAGMA wal_checkpoint(TRUNCATE)
    3. Close the connection immediately
    4. Force garbage collection and brief sleep

    This ensures the checkpoint operation doesn't conflict with lingering
    handles from recently-closed connections, and gives Windows time to
    release file locks.

    Args:
        db_path_or_uri: Database file path or URI
        uri: Whether the path is a URI (default: False)
        timeout: Lock timeout in seconds (default: 30.0)

    Example:
        >>> wal_checkpoint_truncate("data.db")
    """
    checkpoint_conn = None
    try:
        checkpoint_conn = connect(db_path_or_uri, uri=uri, timeout=timeout)
        checkpoint_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        if checkpoint_conn:
            try:
                checkpoint_conn.close()
            except sqlite3.Error:
                pass
        # Allow Windows to release file handles
        _windows_release_handles(delay=0.05)


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
def wal_db(db_path_or_uri: str, *, uri: bool = False, timeout: float = 30.0):
    """
    Context manager for SQLite connections with WAL cleanup.

    This ensures:
    1. Connection is opened with standard settings
    2. WAL mode and pragmas are configured
    3. Connection is closed properly in finally block
    4. Checkpoint(TRUNCATE) is run with a fresh connection
    5. Brief delay allows Windows to release file handles

    Usage pattern:
        with wal_db("data.db") as conn:
            conn.execute("INSERT INTO table VALUES (?)", (value,))
            conn.commit()

    The checkpoint and cleanup happen automatically when exiting the
    context, even on errors.

    Args:
        db_path_or_uri: Database file path or URI
        uri: Whether the path is a URI (default: False)
        timeout: Lock timeout in seconds (default: 30.0)

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

        # Then checkpoint with a fresh connection
        wal_checkpoint_truncate(db_path_or_uri, uri=uri, timeout=timeout)
