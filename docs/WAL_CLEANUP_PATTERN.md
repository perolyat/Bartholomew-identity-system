# SQLite WAL Cleanup Pattern for Windows

## Overview

This document describes the WAL (Write-Ahead Logging) cleanup pattern implemented in Bartholomew to ensure reliable database teardown on Windows, where file locking can cause permission errors.

## The Problem

When using SQLite in WAL mode on Windows:
- The database creates auxiliary files: `*.db-wal` and `*.db-shm`
- Windows file locking can keep these files locked briefly after connections close
- Antivirus/indexing software may temporarily lock temp files
- Without proper cleanup, these files can cause `PermissionError` during teardown
- This leads to flaky tests, stuck REPLs, and failed CI builds

## The Solution

The key pattern is **checkpoint with a fresh connection after closing all active connections**:

1. Close all active database connections
2. Open a new short-lived connection
3. Run `PRAGMA wal_checkpoint(TRUNCATE)` on this fresh connection
4. Close the checkpoint connection immediately
5. Force garbage collection and brief sleep to allow Windows to release handles

## Implementation

### Helper Modules

#### `fs_helpers.py`
Windows-friendly filesystem utilities:
- `windows_release_handles(delay=0.05)` - Force GC and sleep
- `wait_for_removal(path, timeout=2.0)` - Poll for file disappearance
- `robust_unlink(path, retries=10)` - Retry file deletion with backoff
- `robust_rmtree(path, retries=10)` - Retry directory removal
- `wal_aux_paths(db_path)` - Get WAL/SHM file paths

#### `db_ctx.py`
SQLite connection management with WAL cleanup:
- `set_wal_pragmas(conn)` - Configure WAL mode, synchronous, foreign keys
- `connect(db_path_or_uri, ...)` - Standard connection wrapper
- `wal_checkpoint_truncate(db_path_or_uri)` - Checkpoint with fresh connection
- `close_quietly(conn)` - Safe connection close with error suppression
- `close_all_and_checkpoint(conns, db_path)` - Bulk close + checkpoint
- `wal_db(db_path)` - Context manager with automatic cleanup

### Usage Pattern

#### Basic Usage with Context Manager

```python
from bartholomew_api_bridge_v0_1.services.api import db_ctx

# Simple case: single connection with auto-cleanup
with db_ctx.wal_db("data.db") as conn:
    conn.execute("INSERT INTO table VALUES (?)", (value,))
    conn.commit()
# WAL files are automatically cleaned up when context exits
```

#### Manual Checkpoint

```python
from bartholomew_api_bridge_v0_1.services.api import db_ctx

# If managing connections manually
conn = db_ctx.connect("data.db")
db_ctx.set_wal_pragmas(conn)

try:
    conn.execute("CREATE TABLE t(x)")
    conn.commit()
finally:
    db_ctx.close_quietly(conn)
    # Checkpoint with a fresh connection
    db_ctx.wal_checkpoint_truncate("data.db")
```

#### Multiple Connections

```python
from bartholomew_api_bridge_v0_1.services.api import db_ctx

# When you have multiple connections
conns = [
    db_ctx.connect("data.db"),
    db_ctx.connect("data.db"),
]

for conn in conns:
    db_ctx.set_wal_pragmas(conn)
    # ... do work ...

# Close all and checkpoint
db_ctx.close_all_and_checkpoint(conns, "data.db")
```

## Testing

The pattern is validated by tests in:
- `test_sqlite_wal_cleanup.py` - Tests the core checkpoint pattern
- `test_fixtures_windows.py` - Tests fixture cleanup behavior
- `conftest.py` - Provides Windows-friendly test fixtures

All tests include Windows-specific polling and retry logic to handle file locking delays.

## Current Usage in Bartholomew

### API Bridge DB Layer

`bartholomew_api_bridge_v0_1/services/api/db.py` uses `wal_db` context manager:

```python
from . import db_ctx

@contextmanager
def get_conn():
    with db_ctx.wal_db(DB_PATH, timeout=30.0) as conn:
        # Table setup
        conn.execute("""CREATE TABLE IF NOT EXISTS water_logs (...)""")
        yield conn
    # Automatic cleanup when context exits
```

### Memory Manager (Future Enhancement)

`identity_interpreter/adapters/memory_manager.py` currently manages connections directly. Consider refactoring to use `wal_db` for consistency:

```python
# Current pattern (manual)
with sqlite3.connect(self.db_path) as conn:
    conn.execute("PRAGMA journal_mode = WAL;")
    # ... work ...

# Recommended pattern
from bartholomew_api_bridge_v0_1.services.api import db_ctx

with db_ctx.wal_db(self.db_path) as conn:
    # ... work ...
# Automatic WAL cleanup
```

## Performance Considerations

The pattern adds minimal overhead:
- Fresh connection open/close: ~1-5ms
- `gc.collect()`: <1ms typically
- Sleep delay: 50ms (configurable via `delay` parameter)
- Total overhead per operation: ~50-60ms

This is acceptable for most use cases and ensures reliable cleanup.

## Tuning for CI/Production

If you encounter sporadic lock issues on CI:

1. **Increase delay**: Use `windows_release_handles(delay=0.1)` or higher
2. **Exclude from AV scanning**: Configure antivirus to skip temp directories
3. **Use stable temp paths**: Consider `C:\temp` instead of `%TEMP%`
4. **Increase timeouts**: Use longer `timeout` values in `wal_db()`

## Common Symptoms and Fixes

When working with SQLite on Windows, you may encounter these issues:

| Symptom | Root Cause | Fix |
|---------|-----------|-----|
| `PermissionError (WinError 32)` on teardown | File handles still open when attempting deletion | Close all connections, call `wal_checkpoint_truncate()`, then `robust_unlink()` for -wal/-shm files |
| Stale -wal/-shm files after crash | Process terminated before cleanup | Add `atexit` hook with `wal_checkpoint_truncate(DB_PATH)`, or run cleanup on next startup |
| Flaky file deletion on Windows | Race condition with OS or antivirus | Use `robust_unlink()` with retry logic and `windows_release_handles()` for grace period |
| Cross-process writes stall/timeout | Improper WAL mode configuration | Use `db_ctx.wal_db()` which sets WAL + NORMAL sync; keep write connections short-lived |
| Tests leave temp files | Missing cleanup in fixtures | Use `temp_db_path` fixture from conftest.py which auto-cleans WAL files |

### Quick Recovery Commands

If you encounter stuck files during development:

```python
# Force cleanup of a database
from bartholomew_api_bridge_v0_1.services.api import db_ctx, fs_helpers
from pathlib import Path

db_path = Path("data/stuck.db")

# Checkpoint and remove auxiliary files
db_ctx.wal_checkpoint_truncate(str(db_path))
for aux in fs_helpers.wal_aux_paths(db_path):
    fs_helpers.robust_unlink(aux)
```

## Definition of Done ✓

- [x] WAL/shm files removed reliably after checkpoint on Windows
- [x] Repeatable green tests (no teardown flakiness)
- [x] Pattern codified in `db_ctx.py` and `fs_helpers.py`
- [x] Existing DB code refactored to use helpers
- [x] Documentation created
- [x] Symptoms → fixes table added
- [x] Atexit hook added to API bootstrap
- [x] Multi-process test added
- [x] Guard rail test added

## References

- SQLite WAL Mode: https://www.sqlite.org/wal.html
- WAL Checkpoint: https://www.sqlite.org/pragma.html#pragma_wal_checkpoint
- Windows File Locking: https://learn.microsoft.com/en-us/windows/win32/fileio/locking-and-unlocking-byte-ranges-in-files
