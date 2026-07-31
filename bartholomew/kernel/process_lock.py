"""
Cross-platform advisory process lock.

Phase B, stage B6. Prevents two KernelDaemon instances from running against
the same database at once, and lets CLI/maintenance tools detect (not
necessarily block on) whether a live daemon currently owns a database --
"External Governance control and CLI safety" per docs/PHASE_B_OVERVIEW.md.

Advisory, not a substitute for SQLite's own concurrency handling: WAL mode
already makes concurrent readers/writers against the same file safe at the
storage-engine level (see bartholomew/kernel/db_ctx.py's own docstring).
This lock exists for the failure mode SQLite's concurrency model does NOT
cover -- two KernelDaemon *processes* independently believing they own the
one scheduler loop, one set of background tasks, one in-process Governance
check path -- so a second daemon fails fast with a clear message instead of
silently racing the first one's assumptions.

One lock file per database, at "<db_path>.lock" (sibling to the "-wal"/
"-shm" files SQLite itself creates), holding a non-blocking exclusive OS
lock (fcntl.flock on POSIX, msvcrt.locking on Windows) for the life of the
holding process -- released explicitly via release(), or implicitly if the
process exits (the OS reclaims the lock either way, which is exactly the
"crash-safe" property this needs: an unclean daemon exit must not leave the
database permanently unusable by the next start()).
"""

from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger(__name__)


class ProcessLock:
    def __init__(self, lock_path: str):
        self.lock_path = lock_path
        self._fd: int | None = None

    def try_acquire(self) -> bool:
        """
        Non-blocking. Returns True if this instance now holds the lock
        (either newly acquired, or already held by this same instance --
        idempotent), False if another process currently holds it.
        """
        if self._fd is not None:
            return True

        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            if not _lock_fd_nonblocking(fd):
                os.close(fd)
                return False
        except Exception:
            os.close(fd)
            raise

        # Best-effort: record our PID for operator-facing diagnostics
        # (owner_pid() below). Never used for correctness decisions -- if
        # this write fails, the lock is still validly held.
        try:
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()}\n".encode())
        except OSError:
            logger.debug("ProcessLock(%s): failed to record owner pid", self.lock_path)

        self._fd = fd
        return True

    def release(self) -> None:
        """Idempotent: safe to call whether or not this instance holds the
        lock, and safe to call more than once."""
        if self._fd is None:
            return
        fd = self._fd
        self._fd = None  # release-then-cleanup ordering: never leave a
        # partially-released instance that a concurrent caller could see
        # as "still held" if unlock or close below raises.
        try:
            _unlock_fd(fd)
        except OSError:
            logger.debug("ProcessLock(%s): unlock raised on release", self.lock_path)
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    def is_held_by_other(self) -> bool:
        """
        Non-destructive probe: True if some OTHER process currently holds
        the lock. If this instance already holds it, returns False (we are
        not an "other" process). Does not change this instance's own
        held/not-held state.
        """
        if self._fd is not None:
            return False

        probe = ProcessLock(self.lock_path)
        acquired = probe.try_acquire()
        if acquired:
            probe.release()
            return False
        return True

    def owner_pid(self) -> int | None:
        """
        Best-effort: the PID recorded in the lock file, without acquiring
        it. None if the file is absent, empty, or unreadable. Purely
        informational (operator-facing messages) -- a stale PID (the
        recording process has since exited without cleaning up, e.g. after
        a crash) is possible and expected; never treat a non-None return as
        proof the process is still alive.
        """
        try:
            with open(self.lock_path, encoding="utf-8") as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return None


def _lock_fd_nonblocking(fd: int) -> bool:
    """True if the exclusive lock was acquired, False if already held
    elsewhere. Raises for any other OS-level failure."""
    if sys.platform == "win32":
        import msvcrt

        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock_fd(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(fd, fcntl.LOCK_UN)
