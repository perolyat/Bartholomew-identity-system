import os
import threading
from contextlib import contextmanager
from pathlib import Path

from . import db_ctx


def _find_project_root() -> Path:
    """Locate project root by walking up for pyproject.toml."""
    p = Path(__file__).resolve()
    for parent in [p.parent, *p.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


DEFAULT_DB_PATH = str(_find_project_root() / "data" / "barth.db")
DB_PATH = os.getenv("BARTH_DB_PATH", DEFAULT_DB_PATH)
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
        yield conn


def init_db():
    """Initialize the database with required tables."""
    with get_conn():
        pass
