
import os
import threading
from contextlib import contextmanager

from . import db_ctx

DB_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "data", "barth.db"
)
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# Thread-local storage for connection pooling
_local = threading.local()


@contextmanager
def get_conn():
    """
    Context manager for database connections with proper cleanup.
    
    Uses the wal_db context manager which ensures:
    - WAL mode is enabled with proper pragmas
    - Connection is closed properly
    - Checkpoint(TRUNCATE) runs with a fresh connection
    - Windows file handles are released
    
    This pattern ensures reliable cleanup of WAL auxiliary files
    on Windows where file locking can cause permission errors.
    """
    with db_ctx.wal_db(DB_PATH, timeout=30.0) as conn:
        # Create table if not exists
        conn.execute(
            """CREATE TABLE IF NOT EXISTS water_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                ml INTEGER NOT NULL
            )"""
        )
        yield conn


def init_db():
    """Initialize the database with required tables."""
    with get_conn():
        # Table creation is handled in get_conn()
        pass
