"""
Test SQLite WAL auxiliary file cleanup behavior.

This test validates that .db-wal and .db-shm files are properly removed
after all connections are closed and a TRUNCATE checkpoint is performed.
This is critical for Windows where lingering files can cause permission errors.
"""

import gc
import sqlite3
import time
from pathlib import Path

import pytest


@pytest.mark.windows_quirk
@pytest.mark.database
def test_wal_aux_files_removed_after_checkpoint(temp_dir):
    """
    Test that WAL auxiliary files are removed after checkpoint(TRUNCATE).
    
    Steps:
    1. Create a temp database with multiple connections using WAL mode
    2. Write data to generate -wal and -shm files
    3. Verify auxiliary files exist during activity
    4. Close all connections and run checkpoint(TRUNCATE)
    5. Assert that -wal and -shm files are removed
       (with Windows-friendly polling)
    
    This test addresses the Windows file-locking issue where SQLite
    auxiliary files can persist and cause PermissionError on cleanup
    if not properly checkpointed and closed.
    """
    db_path = Path(temp_dir) / "cleanup.db"
    uri = f"file:{db_path}?cache=shared&mode=rwc"
    
    # Open multiple connections with WAL mode
    conns = []
    for _ in range(2):
        conn = sqlite3.connect(
            uri,
            uri=True,
            timeout=3.0,
            check_same_thread=False
        )
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conns.append(conn)
    
    # Write some data to trigger WAL file creation
    conns[0].execute("CREATE TABLE IF NOT EXISTS t(id INTEGER);")
    conns[0].execute("INSERT INTO t VALUES (1);")
    conns[0].commit()
    
    wal_path = Path(str(db_path) + "-wal")
    shm_path = Path(str(db_path) + "-shm")
    
    # WAL file should exist after writes in WAL mode
    assert wal_path.exists(), "WAL file should exist after writes"
    
    # Close all connections
    for conn in conns:
        conn.close()
    
    # Force checkpoint and truncate the WAL
    checkpoint_conn = sqlite3.connect(uri, uri=True, timeout=3.0)
    checkpoint_conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    checkpoint_conn.close()
    
    # Allow Windows to release file handles
    gc.collect()
    time.sleep(0.1)
    
    # Helper to wait for file removal (Windows-friendly)
    def wait_for_removal(path: Path, timeout=2.0, step=0.05):
        """Poll until file is removed or timeout occurs."""
        deadline = time.time() + timeout
        while path.exists() and time.time() < deadline:
            gc.collect()  # Force garbage collection to release handles
            time.sleep(step)
        return not path.exists()
    
    # Assert auxiliary files are removed
    assert wait_for_removal(wal_path), \
        f"WAL file {wal_path} not removed after checkpoint(TRUNCATE) and close"
    assert wait_for_removal(shm_path), \
        f"SHM file {shm_path} not removed after checkpoint(TRUNCATE) and close"
    
    # Verify the database itself still exists and is valid
    assert db_path.exists(), "Main database file should still exist"
    with sqlite3.connect(str(db_path)) as verify_conn:
        result = verify_conn.execute("SELECT id FROM t").fetchone()
        assert result[0] == 1, "Data should be preserved after checkpoint"


@pytest.mark.windows_quirk
@pytest.mark.database
def test_wal_cleanup_with_context_manager_pattern(temp_dir):
    """
    Test WAL cleanup using the app's context manager pattern.
    
    This mirrors how the actual db.py module manages connections,
    ensuring the checkpoint happens in the finally block.
    """
    import contextlib
    
    db_path = Path(temp_dir) / "ctx_cleanup.db"
    
    @contextlib.contextmanager
    def managed_connection():
        """Mimic the get_conn() pattern from db.py."""
        conn = None
        try:
            conn = sqlite3.connect(
                str(db_path),
                check_same_thread=False,
                timeout=30.0
            )
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            yield conn
        finally:
            if conn:
                try:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    conn.close()
                except sqlite3.Error:
                    pass
    
    # Use the context manager to create and write data
    with managed_connection() as conn:
        conn.execute("CREATE TABLE test_ctx (id INTEGER);")
        conn.execute("INSERT INTO test_ctx VALUES (42);")
        conn.commit()
    
    wal_path = Path(str(db_path) + "-wal")
    shm_path = Path(str(db_path) + "-shm")
    
    # Allow brief time for OS to release file handles
    time.sleep(0.1)
    gc.collect()
    
    # Auxiliary files should be cleaned up after context manager exit
    assert not wal_path.exists(), \
        "WAL file should be removed after context manager cleanup"
    assert not shm_path.exists(), \
        "SHM file should be removed after context manager cleanup"
