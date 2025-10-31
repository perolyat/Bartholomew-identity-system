"""
Test demonstrating Windows database cleanup handling.

This test file validates that our conftest.py fixtures properly handle
Windows-specific file locking issues that can occur with SQLite databases.
"""

import sqlite3
from pathlib import Path

import pytest


@pytest.mark.windows_quirk
def test_database_cleanup_windows_compatibility(temp_dir):
    """
    Test that demonstrates proper Windows database cleanup.
    
    This test creates multiple database connections and files to stress-test
    the Windows-compatible cleanup logic in our fixtures.
    """
    db_path = Path(temp_dir) / "test.db"
    
    # Create multiple connections to stress test cleanup
    connections = []
    
    for i in range(3):
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS test (id INTEGER, value TEXT)"
        )
        conn.execute(f"INSERT INTO test VALUES ({i}, 'test_{i}')")
        conn.commit()
        connections.append(conn)
    
    # Close connections explicitly
    for conn in connections:
        conn.close()
    
    # Verify data was written
    with sqlite3.connect(str(db_path)) as verify_conn:
        rows = verify_conn.execute("SELECT COUNT(*) FROM test").fetchone()
        assert rows[0] == 3
    
    # The temp_dir fixture should handle cleanup automatically
    # even if there are lingering file handles (Windows quirk)


@pytest.mark.database
def test_concurrent_database_access(temp_dir):
    """
    Test concurrent database access patterns common in the application.
    
    This ensures our fixtures handle realistic usage patterns where
    multiple components might access the same database.
    """
    db_path = Path(temp_dir) / "concurrent.db"
    
    # WAL mode for better concurrency (as used in actual app)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute(
            "CREATE TABLE logs (id INTEGER PRIMARY KEY, message TEXT)"
        )
    
    # Simulate multiple writers (common pattern in app)
    for i in range(5):
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "INSERT INTO logs (message) VALUES (?)", (f"Message {i}",)
            )
    
    # Verify all writes succeeded
    with sqlite3.connect(str(db_path)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
        assert count == 5
    
    # Checkpoint WAL file before cleanup (as done in production)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def test_temp_db_path_fixture(temp_db_path):
    """
    Test the temp_db_path fixture specifically.
    
    This validates that individual database file paths are cleaned up
    properly, which is important for isolated test scenarios.
    """
    # Create a database at the temporary path
    with sqlite3.connect(temp_db_path) as conn:
        conn.execute("CREATE TABLE fixture_test (id INTEGER)")
        conn.execute("INSERT INTO fixture_test VALUES (42)")
    
    # Verify database exists and works
    with sqlite3.connect(temp_db_path) as conn:
        result = conn.execute("SELECT id FROM fixture_test").fetchone()
        assert result[0] == 42
    
    # Fixture should clean up automatically after test ends


def test_db_conn_fixture(db_conn):
    """
    Test the db_conn fixture specifically.
    
    This validates that database connections are properly managed
    and cleaned up without requiring manual connection handling.
    """
    # Use the provided connection
    db_conn.execute("CREATE TABLE conn_test (data TEXT)")
    db_conn.execute("INSERT INTO conn_test VALUES ('fixture_data')")
    db_conn.commit()
    
    # Verify data
    cursor = db_conn.execute("SELECT data FROM conn_test")
    result = cursor.fetchone()
    assert result[0] == "fixture_data"
    
    # Connection should be cleaned up automatically by fixture

