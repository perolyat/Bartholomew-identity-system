"""
SQLite test helpers
Provides consistent connection setup for test isolation
"""

import sqlite3


def connect_test_db(path: str) -> sqlite3.Connection:
    """
    Create a test database connection with proper settings

    Args:
        path: Path to database file (can be ":memory:")

    Returns:
        Configured SQLite connection
    """
    conn = sqlite3.connect(path)

    # Enable safety and consistency features
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")

    return conn


def create_minimal_memories_table(conn: sqlite3.Connection) -> None:
    """
    Create minimal memories table for FK satisfaction in unit tests

    Args:
        conn: SQLite connection
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            summary TEXT,
            ts TEXT NOT NULL
        )
    """,
    )
    conn.commit()


def insert_test_memory(
    conn: sqlite3.Connection,
    kind: str = "test",
    key: str = "test_key",
    value: str = "test content",
    summary: str | None = None,
    ts: str = "2024-01-01T00:00:00Z",
) -> int:
    """
    Insert a test memory and return its ID

    Args:
        conn: SQLite connection
        kind: Memory kind
        key: Memory key
        value: Memory value/content
        summary: Optional summary
        ts: Timestamp

    Returns:
        Inserted memory ID
    """
    cursor = conn.execute(
        "INSERT INTO memories(kind, key, value, summary, ts) VALUES(?, ?, ?, ?, ?)",
        (kind, key, value, summary, ts),
    )
    conn.commit()
    return cursor.lastrowid
