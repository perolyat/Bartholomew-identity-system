"""
SQLite connection context managers and WAL cleanup utilities for kernel.

This module provides connection management and checkpoint helpers that ensure
reliable WAL file cleanup on Windows. The key pattern is to checkpoint with a
fresh connection after closing all active connections, followed by a brief
delay to allow Windows to release file handles.

This is a kernel-local copy to avoid coupling to the API layer.
"""

import gc
import logging
import os
import sqlite3
import threading
import time
from collections.abc import Iterable, Iterator
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


#: How long `set_wal_pragmas()` keeps retrying the WAL conversion of a fresh
#: database file when SQLite refuses to wait for it (see `_enable_wal`).
_WAL_CONVERSION_RETRY_S = 5.0


def _enable_wal(conn: sqlite3.Connection) -> None:
    """
    Put the connection's database into WAL mode, tolerating the one case in
    which SQLite will not wait for the lock that needs.

    On a database that is already in WAL mode this pragma is answered from a
    read transaction and never contends. On a database that is NOT yet in WAL
    mode -- a file that did not exist a moment ago -- the pragma has to
    rewrite the file header, which it does by escalating its own read lock to
    a write lock. When two connections race to convert the same fresh file,
    SQLite deliberately does not invoke the busy handler for the loser of that
    escalation (the documented deadlock-avoidance rule: a reader waiting to
    become a writer while another writer waits for that reader to leave would
    wait forever), and returns SQLITE_BUSY immediately, whatever
    `busy_timeout` or the connection's `timeout=` says. The caller sees
    `sqlite3.OperationalError: database is locked` on the very first pragma
    of a brand-new database.

    Reproduced with two spawned processes opening one fresh file through
    `wal_db()`: 19 of 40 barrier-synchronised attempts failed on Linux (the
    mechanism behind `tests/test_sqlite_wal_concurrent_processes.py`'s
    intermittent "Worker 0 failed: database is locked"). The winner's
    conversion takes milliseconds, after which the pragma is a read again, so
    the correct response is to retry briefly -- not to widen every timeout.

    The retry is scoped to that pending conversion: after a refused attempt
    the journal mode is read (a pure read), and only a file that is still not
    in WAL mode is retried, for at most `_WAL_CONVERSION_RETRY_S`. A refusal
    with any other cause is raised as before.
    """
    deadline = time.monotonic() + _WAL_CONVERSION_RETRY_S
    delay = 0.005
    while True:
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            if _journal_mode(conn) == "wal":
                # The other connection finished the conversion; the pragma
                # would be a plain read now and there is nothing to retry.
                return
            if time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.1)


def _journal_mode(conn: sqlite3.Connection) -> str:
    """The database's current journal mode, read-only; '' if even that is refused."""
    try:
        row = conn.execute("PRAGMA journal_mode").fetchone()
    except sqlite3.OperationalError:
        return ""
    return str(row[0]).lower() if row else ""


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
    _enable_wal(conn)
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


# ---------------------------------------------------------------------------
# Bounded connection scopes (db_session)
# ---------------------------------------------------------------------------
#
# Why this exists -- the measured defect, not a guess.
#
# Every persistence helper in this repository takes a `db_path` and owns a
# whole connection lifecycle: `wal_db()` opens a connection, the helper does
# one small thing, and the connection is closed again. When that connection is
# the *only* connection to the database -- which it always is, between calls --
# closing it is not a cheap handle release. SQLite runs a checkpoint of the
# entire WAL and then deletes the `-wal` and `-shm` files; the next call has to
# recreate them. That is the cost that was previously attributed to "close",
# and it is paid once per tiny operation.
#
# Measured on Linux, 300 single-row writes through `wal_db()` (ms per
# operation, connect / commit / close):
#
#     sole connection            0.407 / 1.199 / 1.150
#     another connection open    0.156 / 0.021 / 0.022
#
# The second row is the same code doing the same work; the only difference is
# that closing is no longer the *last* close, so no checkpoint-and-unlink
# happens. On Windows, where creating and deleting files is far more
# expensive, the same term was measured at roughly 70 ms per close.
#
# The repair is an explicit, bounded ownership boundary rather than a pool:
# a caller that is about to do several database operations as one unit of work
# declares that scope with `db_session()`. Inside the scope, and only on the
# thread that opened it, `wal_db()` borrows the session's connection instead of
# opening its own. When the scope ends the connection is closed, so every
# handle is released exactly as before -- once per unit of work instead of once
# per statement.
#
# What this deliberately is NOT:
#   * not a process-wide pool -- nothing is cached, nothing is reused across
#     scopes, and there is no global registry of live connections;
#   * not a long-lived connection -- a session lasts exactly as long as its
#     `with` block and is closed in `finally`;
#   * not shared between threads -- the binding is thread-local, so a second
#     thread touching the same file gets its own connection, and SQLite objects
#     never cross a thread boundary;
#   * not a change to transaction semantics -- see `wal_db()` below.


class _SessionState(threading.local):
    """Per-thread map of database key -> active session.

    `threading.local` rather than a `contextvars.ContextVar`: the value being
    scoped is a `sqlite3.Connection`, which is bound to the thread that is
    allowed to use it. A context variable would follow an `await` onto another
    thread's executor and hand that thread a connection it must not touch.
    """

    def __init__(self) -> None:
        self.scopes: dict[str, _Session] = {}


class _Session:
    """One bounded connection scope. Internal; obtained via `db_session()`."""

    __slots__ = ("conn", "depth", "key", "label")

    def __init__(self, key: str, conn: sqlite3.Connection, label: str) -> None:
        self.key = key
        self.conn = conn
        self.label = label
        self.depth = 0


_sessions = _SessionState()


def session_key(db_path_or_uri: str, *, uri: bool = False) -> str:
    """The identity a session is keyed on: one key per database file.

    Two callers naming the same file by different paths must share one scope,
    or the scope would silently not apply. URIs are keyed verbatim (their
    query string can change what is opened -- `mode=ro`, `cache=shared` -- so
    two URIs are the same database only when they are the same string), and
    `:memory:` is keyed verbatim because each such connection is its own
    private database and must never be shared.
    """
    if uri or db_path_or_uri == ":memory:" or db_path_or_uri.startswith("file:"):
        return db_path_or_uri
    return os.path.realpath(db_path_or_uri)


def active_session_connection(
    db_path_or_uri: str,
    *,
    uri: bool = False,
) -> sqlite3.Connection | None:
    """The connection bound to this thread for `db_path_or_uri`, or None.

    Exposed for tests and diagnostics. A `None` here is the normal state:
    outside a `db_session()` scope every call owns its own connection.
    """
    session = _sessions.scopes.get(session_key(db_path_or_uri, uri=uri))
    return session.conn if session else None


def in_session(db_path_or_uri: str, *, uri: bool = False) -> bool:
    """Whether this thread currently holds a `db_session()` for this database."""
    return session_key(db_path_or_uri, uri=uri) in _sessions.scopes


@contextmanager
def db_session(
    db_path_or_uri: str,
    *,
    uri: bool = False,
    timeout: float = 30.0,
    label: str = "",
) -> Iterator[sqlite3.Connection]:
    """Bind one WAL connection to this thread for the duration of a scope.

    Use it around a *bounded unit of work* -- one scheduler tick, one request,
    one burst of related writes -- where opening and closing a connection per
    statement is the dominant cost. Every `wal_db()` call made on this thread,
    for this database, inside the scope borrows this connection.

    Contract:
      * The connection is closed when the scope exits, on the success path and
        on the exception path alike. Nothing survives the `with`.
      * Any transaction still open at scope exit is rolled back before the
        close -- which is what closing the connection would have done anyway.
        A unit of work that does not commit does not persist.
      * Scopes nest: an inner `db_session()` for the same database on the same
        thread reuses the outer connection and does not close it. The
        outermost scope owns the close.
      * The binding is thread-local. Another thread inside this scope is
        unaffected and opens its own connections as usual.
      * It is not a cache: leaving the scope and re-entering it opens a new
        connection.

    Yields:
        The bound connection, for callers that want it directly.
    """
    key = session_key(db_path_or_uri, uri=uri)
    existing = _sessions.scopes.get(key)
    if existing is not None:
        # Re-entrant: the outer scope owns the connection and its close.
        existing.depth += 1
        try:
            yield existing.conn
        finally:
            existing.depth -= 1
        return

    conn = connect(db_path_or_uri, uri=uri, timeout=timeout)
    session = _Session(key, conn, label)
    _sessions.scopes[key] = session
    try:
        set_wal_pragmas(conn)
        yield conn
    finally:
        # Unbind before releasing, so nothing can borrow a closing connection.
        _sessions.scopes.pop(key, None)
        _rollback_quietly(conn)
        close_quietly(conn)


def _rollback_quietly(conn: sqlite3.Connection) -> None:
    """Roll back an open transaction, ignoring a connection already gone."""
    try:
        if conn.in_transaction:
            conn.rollback()
    except sqlite3.Error:
        pass


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

    Inside an enclosing `db_session()` for the same database, on the same
    thread, steps 1, 2 and 3 do not happen: the session's already-configured
    connection is borrowed and the session's exit closes it. The observable
    per-call semantics are unchanged -- an uncommitted transaction is still
    discarded when the call returns -- but the open/close (and the WAL
    checkpoint-and-unlink that the last close performs) is paid once per
    session instead of once per call. See the `db_session()` block above.

    Yields:
        SQLite connection configured for WAL mode

    Example:
        >>> with wal_db("data.db") as conn:
        ...     conn.execute("CREATE TABLE IF NOT EXISTS t(x)")
        ...     conn.commit()
    """
    session = _sessions.scopes.get(session_key(db_path_or_uri, uri=uri))
    if session is not None:
        # Borrowed from an enclosing db_session() on this thread. The scope,
        # not this call, owns the connection and its close.
        try:
            yield session.conn
        finally:
            # Exactly what closing our own connection would have done: an
            # uncommitted transaction does not survive the operation. Without
            # this, a caller that raised part-way through a write would leave
            # a dirty transaction for the *next* borrower to commit.
            _rollback_quietly(session.conn)
            if checkpoint:
                # Run it on the session's own connection: the fresh-connection
                # dance exists to work around a *closed* connection's handles,
                # and there is no close here to work around.
                _checkpoint_on(session.conn, checkpoint, db_path_or_uri, label)
        return

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


def _checkpoint_on(
    conn: sqlite3.Connection,
    mode: str,
    db_path_or_uri: str,
    label: str,
) -> tuple[int, int, int] | None:
    """Checkpoint through an already-open connection (session path)."""
    if mode not in _VALID_CHECKPOINT_MODES:
        raise ValueError(
            f"wal_db: unknown checkpoint mode {mode!r}, expected one of {_VALID_CHECKPOINT_MODES}",
        )
    started = time.monotonic()
    row = None
    try:
        row = conn.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
        return row
    finally:
        _checkpoint_log.debug(
            "wal_checkpoint db=%s mode=%s label=%s thread=%s duration_ms=%.1f "
            "result=%s in_transaction=%s scope=session",
            db_path_or_uri,
            mode,
            label,
            threading.current_thread().name,
            (time.monotonic() - started) * 1000,
            row,
            conn.in_transaction,
        )
