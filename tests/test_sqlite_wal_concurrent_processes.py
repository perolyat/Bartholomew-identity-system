"""
Test concurrent process access to SQLite WAL database.

This test verifies that:
1. Multiple processes can write to the same database concurrently
2. WAL cleanup removes auxiliary files after all processes close
3. No file handles are leaked on Windows
"""

import multiprocessing as mp
import sys
from pathlib import Path

import pytest

from bartholomew_api_bridge_v0_1.services.api import db_ctx, fs_helpers


def _worker(db_path_str: str, worker_id: int, n_writes: int):
    """
    Worker function that writes to the database.

    Args:
        db_path_str: Database file path
        worker_id: Unique ID for this worker
        n_writes: Number of rows to write
    """
    try:
        with db_ctx.wal_db(db_path_str) as conn:
            # Create table if not exists
            conn.execute(
                "CREATE TABLE IF NOT EXISTS concurrent_test "
                "(worker_id INTEGER, write_id INTEGER)",
            )

            # Write rows
            for i in range(n_writes):
                conn.execute(
                    "INSERT INTO concurrent_test(worker_id, write_id) VALUES (?, ?)",
                    (worker_id, i),
                )

            conn.commit()

        # Exit cleanly
        sys.exit(0)
    except Exception as e:
        print(f"Worker {worker_id} failed: {e}")
        sys.exit(1)


@pytest.mark.database
def test_wal_cleanup_concurrent_processes(temp_db_path):
    """
    Test that WAL files are cleaned up after concurrent process writes.

    This simulates real-world API usage where multiple requests might
    write to the database concurrently.
    """
    db_path = str(temp_db_path)

    # Use spawn context for Windows compatibility
    ctx = mp.get_context("spawn")

    # Launch two workers that write concurrently
    workers = []
    for worker_id in range(2):
        p = ctx.Process(target=_worker, args=(db_path, worker_id, 50))
        workers.append(p)
        p.start()

    # Wait for all workers to complete
    for p in workers:
        p.join(timeout=10.0)
        assert p.exitcode == 0, f"Worker process failed with code {p.exitcode}"

    # Verify data was written
    with db_ctx.wal_db(db_path) as conn:
        cursor = conn.execute("SELECT COUNT(*) FROM concurrent_test")
        count = cursor.fetchone()[0]
        assert count == 100, f"Expected 100 rows, got {count}"

    # Final checkpoint and cleanup
    db_ctx.close_all_and_checkpoint([], db_path)

    # Give Windows a brief grace to release handles
    fs_helpers.windows_release_handles(delay=0.05)

    # Verify no WAL auxiliary files remain
    for aux in fs_helpers.wal_aux_paths(Path(db_path)):
        assert not aux.exists(), f"Aux file still present: {aux}"


@pytest.mark.database
def test_wal_cleanup_after_uncommitted_write(temp_db_path):
    """
    Test that WAL cleanup works after uncommitted writes are abandoned.

    This simulates a crash where a connection is closed without committing,
    which is the realistic post-crash scenario.
    """
    db_path = str(temp_db_path)

    # Write some data successfully
    with db_ctx.wal_db(db_path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS crash_test(x INTEGER)")
        conn.execute("INSERT INTO crash_test(x) VALUES (42)")
        conn.commit()

    # Simulate crash: open connection, write without commit, then close
    # This is what happens when a process terminates mid-transaction
    conn = db_ctx.connect(db_path)
    db_ctx.set_wal_pragmas(conn)
    conn.execute("INSERT INTO crash_test(x) VALUES (99)")
    # Don't commit, just close (simulates process termination)
    conn.close()

    # Force cleanup (this would happen on next startup or via atexit)
    db_ctx.wal_checkpoint_truncate(db_path)
    fs_helpers.windows_release_handles(delay=0.1)

    # Verify we can still read only the committed data
    with db_ctx.wal_db(db_path) as verify_conn:
        cursor = verify_conn.execute("SELECT x FROM crash_test ORDER BY x")
        rows = cursor.fetchall()
        # Only the committed row should be there (99 was not committed)
        assert rows == [(42,)], f"Expected [(42,)], got {rows}"

    # Verify cleanup succeeded - WAL files should be removed
    for aux in fs_helpers.wal_aux_paths(Path(db_path)):
        assert not aux.exists(), f"Aux file still present after cleanup: {aux}"


@pytest.mark.database
def test_two_connections_converting_a_fresh_database_to_wal_both_succeed(tmp_path):
    """The mechanism behind this file's intermittent ``database is locked``.

    On a database that is not yet in WAL mode -- a file that did not exist a
    moment ago -- ``PRAGMA journal_mode = WAL`` has to rewrite the header, which
    it does by escalating its own read lock to a write lock. When two
    connections race to convert the same fresh file, SQLite deliberately does
    not invoke the busy handler for the loser of that escalation (its
    deadlock-avoidance rule) and returns SQLITE_BUSY at once, whatever
    ``busy_timeout`` says. Two spawned processes hit it on 19 of 40
    synchronised attempts on Linux; two threads hit it on 9 of 60.

    `db_ctx.set_wal_pragmas` now retries that one pragma briefly, because the
    winner's conversion takes milliseconds and afterwards the pragma is a
    read. Forty synchronised attempts make the pre-repair behaviour fail here
    with overwhelming probability while costing well under a second.
    """
    import threading

    failures = []
    for attempt in range(40):
        path = str(tmp_path / f"fresh-{attempt}.db")
        barrier = threading.Barrier(2)
        errors: list[str] = []

        def convert(path=path, barrier=barrier, errors=errors) -> None:
            conn = db_ctx.connect(path)
            try:
                barrier.wait(timeout=5)
                db_ctx.set_wal_pragmas(conn)
                conn.execute("CREATE TABLE IF NOT EXISTS t(x INTEGER)")
                conn.execute("INSERT INTO t(x) VALUES (1)")
                conn.commit()
            except Exception as exc:  # the failure text is the evidence
                errors.append(f"{type(exc).__name__}: {exc}")
            finally:
                conn.close()

        threads = [threading.Thread(target=convert) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
        if errors:
            failures.append((attempt, errors))
        else:
            with db_ctx.wal_db(path) as conn:
                assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
                assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 2

    assert not failures, f"fresh-database WAL conversion lost the race: {failures}"
