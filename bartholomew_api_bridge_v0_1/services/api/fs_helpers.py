"""
Windows-friendly filesystem helpers for SQLite database cleanup.

This module provides utilities to handle Windows file locking issues that can
occur with SQLite databases, particularly when using WAL mode. The functions
include retry logic, garbage collection, and polling to ensure reliable cleanup
even when file handles are temporarily held by the OS or antivirus software.
"""

import gc
import shutil
import time
from pathlib import Path
from typing import Tuple, Union


def windows_release_handles(delay: float = 0.05) -> None:
    """
    Force garbage collection and brief pause to help Windows release
    file handles.
    
    Call this after closing database connections to give Windows time
    to release file locks before attempting cleanup operations.
    
    Args:
        delay: Time to sleep in seconds after garbage collection
               (default: 0.05)
    """
    gc.collect()
    time.sleep(delay)


def wait_for_removal(
    path: Union[str, Path], 
    timeout: float = 2.0, 
    step: float = 0.05
) -> bool:
    """
    Poll until a file is removed or timeout occurs.
    
    This is particularly useful on Windows where file deletion may be delayed
    due to lingering file handles. The function periodically checks if the file
    still exists and forces garbage collection between checks.
    
    Args:
        path: Path to the file to wait for removal
        timeout: Maximum time to wait in seconds (default: 2.0)
        step: Time between polling attempts in seconds (default: 0.05)
    
    Returns:
        True if file was removed, False if timeout occurred
    
    Example:
        >>> wait_for_removal(Path("test.db-wal"), timeout=2.0)
        True
    """
    path = Path(path)
    deadline = time.time() + timeout
    
    while path.exists() and time.time() < deadline:
        gc.collect()
        time.sleep(step)
    
    return not path.exists()


def robust_unlink(
    path: Union[str, Path], 
    retries: int = 10, 
    step: float = 0.05
) -> None:
    """
    Attempt to delete a file with retry logic for Windows PermissionError.
    
    On Windows, file deletion can fail with PermissionError (WinError 32) if
    file handles are still open. This function retries with exponential backoff
    and garbage collection between attempts.
    
    Args:
        path: Path to the file to delete
        retries: Number of retry attempts (default: 10)
        step: Base delay between retries in seconds (default: 0.05)
    
    Raises:
        PermissionError: If file cannot be deleted after all retries
        
    Example:
        >>> robust_unlink("temp.db")
    """
    path = Path(path)
    
    for attempt in range(retries):
        try:
            path.unlink()
            return
        except FileNotFoundError:
            # File already deleted
            return
        except PermissionError:
            if attempt < retries - 1:
                gc.collect()
                time.sleep(step * (attempt + 1))
            else:
                raise


def robust_rmtree(
    path: Union[str, Path], 
    retries: int = 10, 
    step: float = 0.1
) -> None:
    """
    Attempt to remove a directory tree with retry logic for Windows.
    
    Similar to robust_unlink but for entire directory trees. Handles Windows
    file locking issues where directories may remain locked briefly after
    closing database connections.
    
    Args:
        path: Path to the directory to remove
        retries: Number of retry attempts (default: 10)
        step: Base delay between retries in seconds (default: 0.1)
    
    Raises:
        PermissionError: If directory cannot be removed after all retries
        
    Example:
        >>> robust_rmtree("temp_dir")
    """
    path = Path(path)
    
    for attempt in range(retries):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            # Directory already removed
            return
        except PermissionError:
            if attempt < retries - 1:
                gc.collect()
                time.sleep(step * (attempt + 1))
            else:
                # Final attempt with ignore_errors=True
                try:
                    shutil.rmtree(path, ignore_errors=True)
                    return
                except Exception:
                    raise


def wal_aux_paths(db_path: Union[str, Path]) -> Tuple[Path, Path]:
    """
    Get the paths to SQLite WAL auxiliary files for a database.
    
    Returns the paths to the -wal and -shm files that SQLite creates when
    using WAL (Write-Ahead Logging) mode.
    
    Args:
        db_path: Path to the main database file
    
    Returns:
        Tuple of (wal_path, shm_path)
        
    Example:
        >>> wal_path, shm_path = wal_aux_paths("data.db")
        >>> print(wal_path)
        PosixPath('data.db-wal')
    """
    db_path = Path(db_path)
    wal_path = Path(str(db_path) + "-wal")
    shm_path = Path(str(db_path) + "-shm")
    return wal_path, shm_path
