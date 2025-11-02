# conftest.py
"""
Pytest configuration and fixtures for Bartholomew AI Identity System tests.

This module provides test fixtures with proper cleanup, especially for Windows
compatibility where file deletion can fail due to lingering file handles.
"""

import gc
import tempfile
import time
from pathlib import Path

import pytest


@pytest.fixture
def anyio_backend():
    """Configure anyio to use only asyncio backend (no trio)."""
    return "asyncio"


@pytest.fixture
def temp_db_path(tmp_path: Path):
    """
    Create a temporary database file path with WAL cleanup.

    Uses pytest's tmp_path for proper isolation and automatically cleans up
    WAL auxiliary files (-wal/-shm) after each test. This ensures reliable
    teardown on Windows where file handles can cause PermissionError.
    """
    # Import here to avoid circular dependencies
    from bartholomew_api_bridge_v0_1.services.api import db_ctx, fs_helpers

    p = tmp_path / "test.db"
    try:
        yield p
    finally:
        # Ensure WAL cleanup even if test crashed mid-connection
        try:
            db_ctx.wal_checkpoint_truncate(str(p))
        except Exception:
            pass  # DB may not exist yet

        # Remove WAL auxiliary files
        for aux in fs_helpers.wal_aux_paths(p):
            fs_helpers.robust_unlink(aux)


@pytest.fixture
def db_conn(temp_db_path):
    """
    Create a database connection with proper WAL cleanup.

    Uses the wal_db context manager which ensures:
    - WAL mode is enabled with proper pragmas
    - Connection is closed properly
    - Checkpoint(TRUNCATE) runs with a fresh connection
    """
    from bartholomew_api_bridge_v0_1.services.api import db_ctx

    with db_ctx.wal_db(str(temp_db_path)) as conn:
        yield conn


@pytest.fixture
def temp_dir():
    """
    Create a temporary directory with Windows-compatible cleanup.

    Handles Windows file locking issues where SQLite databases
    may remain locked briefly after connection closure.
    """
    import shutil

    tmpdir = tempfile.mkdtemp()
    try:
        yield tmpdir
    finally:
        # Windows-specific cleanup with retry logic
        for attempt in range(10):
            try:
                shutil.rmtree(tmpdir)
                break
            except PermissionError:
                gc.collect()  # Force garbage collection
                time.sleep(0.1 * (attempt + 1))  # Increasing delay
            except FileNotFoundError:
                # Directory already removed
                break
        else:
            # Final attempt with ignore_errors=True
            try:
                shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass  # Give up gracefully


@pytest.fixture
def isolated_memory_manager(temp_dir):
    """
    Create a MemoryManager instance in an isolated temporary directory.

    This ensures each test gets a fresh database without interference
    from other tests or previous runs.
    """
    from identity_interpreter import load_identity, normalize_identity
    from identity_interpreter.adapters.memory_manager import MemoryManager

    identity = load_identity("Identity.yaml")
    identity = normalize_identity(identity)

    mm = MemoryManager(identity, data_dir=temp_dir)

    try:
        yield mm
    finally:
        # Ensure any open connections are closed
        if hasattr(mm, "_conn") and mm._conn:
            mm._conn.close()
        del mm
        gc.collect()


@pytest.fixture(autouse=True)
def ensure_cleanup():
    """
    Auto-run fixture to ensure proper cleanup after each test.

    Forces garbage collection and brief pause to help Windows
    release file handles before the next test runs.
    """
    yield
    # Post-test cleanup
    gc.collect()
    time.sleep(0.05)  # Brief pause for system cleanup


# pytest configuration
def pytest_configure(config):
    """Configure pytest with custom markers and settings."""
    config.addinivalue_line(
        "markers",
        "integration: marks tests as integration tests (may be slower)",
    )
    config.addinivalue_line("markers", "database: marks tests that use database connections")
    config.addinivalue_line(
        "markers",
        "windows_quirk: marks tests that handle Windows-specific file issues",
    )


def pytest_collection_modifyitems(config, items):
    """Add markers to tests based on their names and content."""
    for item in items:
        # Mark database tests
        if "db" in item.name.lower() or "database" in item.name.lower():
            item.add_marker(pytest.mark.database)

        # Mark integration tests
        if "integration" in item.name.lower() or "cold_boot" in item.name.lower():
            item.add_marker(pytest.mark.integration)

        # Mark tests that handle Windows file quirks
        if "cleanup" in item.name.lower() or "teardown" in item.name.lower():
            item.add_marker(pytest.mark.windows_quirk)
