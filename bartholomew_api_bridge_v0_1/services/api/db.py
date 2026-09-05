import threading
from contextlib import contextmanager
from pathlib import Path

from bartholomew.kernel.db_paths import (
    default_kernel_db_path,
    find_project_root,
    resolve_kernel_db_path,
)

from . import db_ctx


def _find_project_root() -> Path:
    """Locate project root by walking up for pyproject.toml.

    Delegates to `bartholomew.kernel.db_paths`; kept under this name for the
    call sites that import it.
    """
    return find_project_root()


DEFAULT_DB_PATH = default_kernel_db_path()


def resolve_db_path() -> str:
    """
    Resolve the database path from BARTH_DB_PATH (or DEFAULT_DB_PATH),
    read fresh on every call rather than cached once at import time.

    Since the Windows companion completion this delegates to
    `bartholomew.kernel.db_paths.resolve_kernel_db_path`, the same resolver
    the kernel daemon and the operator CLI use, so that the brake an operator
    engages from a shell is the brake this server reads.

    Found investigating a CI flake on PR #38 (S1.4/S1.6): DB_PATH used to
    be *only* a module-level constant, resolved once when this module was
    first imported. Because Python caches modules, every later `from .db
    import DB_PATH` anywhere in the process reused that one frozen value
    -- including in test files that set os.environ["BARTH_DB_PATH"] to
    their own private tempdir *before* importing app.py, each expecting
    an isolated database. Whichever test file's import chain reached this
    module first (in whatever order pytest happened to collect files)
    silently won that race for the entire test session; every other such
    file's KernelDaemon ended up sharing that one physical SQLite file
    instead of its own -- real cross-file test contamination (each
    file's background scheduler and foreground HTTP-triggered writes
    genuinely racing an unrelated file's), not the per-file isolation
    each test's own code intended.

    app.py's startup() now calls this fresh at KernelDaemon-construction
    time instead of importing the frozen DB_PATH constant, so each test
    file's own env var assignment -- still in effect for its own
    duration -- actually determines its own daemon's database again.
    Identical behavior for a real single-process deployment, where
    BARTH_DB_PATH is set once before the process starts and never
    changes afterward.
    """
    return resolve_kernel_db_path(create_parent=True)


# Backward-compatible frozen constant, still resolved once at this
# module's first import, for call sites that read it as a plain module
# attribute (e.g. routes/liveness.py) rather than calling
# resolve_db_path(). See that function's docstring for why app.py's
# startup() -- the call site actually implicated in the CI flake --
# no longer uses this constant.
DB_PATH = resolve_db_path()

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
