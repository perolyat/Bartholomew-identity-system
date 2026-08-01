"""
Cross-platform advisory process lock (Phase B stage B6).

Prevents a CLI maintenance operation from racing a running KernelDaemon
against the same database file, and prevents two daemon processes from
starting against the same db_path. Not a Governance concern (unlike the
write fence in governance_store.py) -- this is a plain OS-level mutual
exclusion primitive, deliberately kept separate: the write fence governs
*Governance writes specifically* during a single daemon's own startup/
shutdown window, while this lock governs *whole-process* exclusivity for
operations that assume they own the database file outright (daemon
startup itself, and CLI operations like `embeddings rebuild-vss` that
aren't designed to tolerate a concurrently-running daemon).

`brake on`/`brake off`/`brake status` deliberately do NOT take this lock
-- they are the intended mechanism for controlling a *running* daemon,
and GovernanceStore's own write fence (Phase B stage B5) plus
revision-guarded disengage() already protect that path. Repository
evidence (docs/PHASE_B_RISK_MAP.md's B6 rows) does not show a real
conflict path for those commands; adding lock-based exclusion there
would just make the kill switch unusable against a live daemon.

POSIX: fcntl.flock(LOCK_EX | LOCK_NB) on the lock file's fd -- a lock
held by an open file description, released when every fd referencing it
closes (including on process crash/exit, the OS-level safety net this
module's holders still explicitly release via close()/context-manager
exit rather than relying on).

Windows: msvcrt.locking() has no exclusive/non-blocking flag combination
directly equivalent to flock(LOCK_EX | LOCK_NB); LK_NBLCK is used,
locking a fixed 1-byte region (byte 0) of the file, matching the
"fixed-byte seeking" pattern the risk map calls for -- msvcrt.locking()
requires an explicit region size and offset, so this module always seeks
to 0 first.
"""

from __future__ import annotations

import os
import sys

_LOCK_BYTE_REGION = 1


class ProcessLockHeldError(RuntimeError):
    """Raised when acquire() finds the lock already held by another
    process (or, on POSIX, another open file description in this
    process) -- an actionable, operator-facing message, not a bare OS
    error."""


def _lock_path_for(db_path: str) -> str:
    """Derive the lock file's path deterministically from db_path -- one
    lock per database file, sitting alongside it rather than in a
    separate registry that could itself go stale."""
    return f"{db_path}.lock"


class ProcessLock:
    """
    A single-attempt, non-blocking advisory lock scoped to one database
    file. Not reentrant: a second acquire() from the same ProcessLock
    instance (or a second instance pointed at the same db_path within
    the same process) is refused exactly like a foreign process would
    be, since two independent holders sharing "ownership" of an
    exclusivity primitive would defeat its purpose.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.lock_path = _lock_path_for(db_path)
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self) -> None:
        if self._fd is not None:
            raise ProcessLockHeldError(
                f"Process lock for {self.db_path!r} is already held by this "
                "ProcessLock instance.",
            )

        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            if sys.platform == "win32":
                self._acquire_windows(fd)
            else:
                self._acquire_posix(fd)
        except OSError as e:
            os.close(fd)
            raise ProcessLockHeldError(
                f"Could not acquire process lock for {self.db_path!r} "
                f"(lock file: {self.lock_path!r}) -- another process (the "
                "daemon, or another maintenance command) is already using "
                "this database. Stop it first, or wait for it to finish, "
                "then retry.",
            ) from e
        self._fd = fd

    def _acquire_posix(self, fd: int) -> None:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _acquire_windows(self, fd: int) -> None:
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, _LOCK_BYTE_REGION)

    def release(self) -> None:
        if self._fd is None:
            return
        fd = self._fd
        self._fd = None
        try:
            if sys.platform == "win32":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, _LOCK_BYTE_REGION)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def __enter__(self) -> ProcessLock:
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()
