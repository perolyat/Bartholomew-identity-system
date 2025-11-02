"""
Test SQLite WAL defaults are properly configured.

Validates that the DB init path sets:
- PRAGMA journal_mode=WAL
- PRAGMA busy_timeout >= 5000
- PRAGMA synchronous=NORMAL or FULL
"""

import sqlite3

from bartholomew.kernel.db_ctx import set_wal_pragmas, wal_db


def test_wal_pragmas_applied_via_set_wal_pragmas(tmp_path):
    """Verify set_wal_pragmas applies all required settings."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    
    # Apply pragmas using the canonical function
    set_wal_pragmas(conn)
    
    # Check journal_mode
    mode = conn.execute("PRAGMA journal_mode;").fetchone()[0].lower()
    assert mode == "wal", f"Expected journal_mode=wal, got {mode}"
    
    # Check busy_timeout
    busy = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
    assert int(busy) >= 5000, f"Expected busy_timeout >= 5000, got {busy}"
    
    # Check synchronous (0=OFF, 1=NORMAL, 2=FULL, 3=EXTRA)
    sync = str(conn.execute("PRAGMA synchronous;").fetchone()[0])
    expected = "Expected synchronous=NORMAL(1) or FULL(2)"
    assert sync in ("1", "2"), f"{expected}, got {sync}"
    
    # Check foreign_keys
    fk = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
    assert fk == 1, f"Expected foreign_keys=ON, got {fk}"
    
    conn.close()


def test_wal_pragmas_applied_via_context_manager(tmp_path):
    """Verify wal_db context manager applies all required settings."""
    db_path = tmp_path / "test_ctx.db"
    
    with wal_db(str(db_path)) as conn:
        # Check journal_mode
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0].lower()
        assert mode == "wal", f"Expected journal_mode=wal, got {mode}"
        
        # Check busy_timeout
        busy = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
        assert int(busy) >= 5000, f"Expected busy_timeout >= 5000, got {busy}"
        
        # Check synchronous (0=OFF, 1=NORMAL, 2=FULL, 3=EXTRA)
        sync = str(conn.execute("PRAGMA synchronous;").fetchone()[0])
        expected = "Expected synchronous=NORMAL(1) or FULL(2)"
        assert sync in ("1", "2"), f"{expected}, got {sync}"
        
        # Check foreign_keys
        fk = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
        assert fk == 1, f"Expected foreign_keys=ON, got {fk}"


def test_wal_mode_persists_across_connections(tmp_path):
    """Verify WAL mode persists after initial setup."""
    db_path = tmp_path / "test_persist.db"
    
    # Set up with WAL mode
    with wal_db(str(db_path)) as conn:
        conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY)")
        conn.commit()
    
    # Open new connection and verify WAL is still active
    conn2 = sqlite3.connect(str(db_path))
    mode = conn2.execute("PRAGMA journal_mode;").fetchone()[0].lower()
    assert mode == "wal", f"Expected journal_mode=wal to persist, got {mode}"
    conn2.close()


def test_busy_timeout_allows_concurrent_access(tmp_path):
    """Verify busy_timeout is set to allow concurrent operations."""
    db_path = tmp_path / "test_concurrent.db"
    
    # Create database with our standard settings
    with wal_db(str(db_path)) as conn:
        conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO test (value) VALUES (?)", ("initial",))
        conn.commit()
    
    # Open two connections and verify busy_timeout is configured
    conn1 = sqlite3.connect(str(db_path))
    set_wal_pragmas(conn1)
    
    conn2 = sqlite3.connect(str(db_path))
    set_wal_pragmas(conn2)
    
    # Both should have busy_timeout >= 5000
    busy1 = conn1.execute("PRAGMA busy_timeout;").fetchone()[0]
    busy2 = conn2.execute("PRAGMA busy_timeout;").fetchone()[0]
    
    msg1 = f"conn1 busy_timeout should be >= 5000, got {busy1}"
    assert int(busy1) >= 5000, msg1
    msg2 = f"conn2 busy_timeout should be >= 5000, got {busy2}"
    assert int(busy2) >= 5000, msg2
    
    conn1.close()
    conn2.close()
