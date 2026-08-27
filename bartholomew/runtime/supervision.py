"""Turning an unrecoverable runtime failure into a restart.

A degraded field in a JSON health document is a *report*, not a recovery. If
the autonomy loop dies and the HTTP process keeps serving, then `systemd`'s
`Restart=on-failure` never fires, Docker's restart policy never fires, and
Bartholomew sits there answering requests while having quietly stopped being
proactive -- indefinitely, because nothing about that state ever becomes a
process exit. Health said "degraded" and no one and nothing acted on it.

This module closes that gap, and is careful about what it is:

* **It is failure propagation, not a supervisor.** Nothing here restarts
  anything, decides a backoff, or counts failures. It converts one internal
  fault into the only signal an external supervisor actually listens to --
  a graceful shutdown followed by a non-zero exit status -- and then gets out
  of the way. The restart decision stays entirely with systemd/Docker, which
  is where the approved topology puts it.
* **It is not a kill.** The recorded failure asks the server to stop
  gracefully, so the existing shutdown path still runs in full:
  admission close, drain, background-task cancellation, WAL checkpoint,
  process-lock release. A supervised restart must not be paid for with the
  unclean shutdown the next startup then has to recover from.
* **It never fires on a normal stop.** Cancellation during shutdown is the
  autonomy loop working as designed. Only an exit the daemon did not ask for,
  while it believes itself to be RUNNING, is a fault (see
  `KernelDaemon._on_scheduler_task_done`).

The kernel does not import this module's escalation *policy*; it calls a hook
that `bartholomew.runtime.serve` installs. That keeps the layering the one the
architecture already has: the kernel reports what happened to its own
background work, and the service layer decides what a process should do about
it.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

#: Exit code for "a component this process cannot run without has failed".
#:
#: Distinct from the configuration codes (`serve.EXIT_LOCK_HELD`,
#: `serve.EXIT_BAD_CONFIG`), and deliberately **not** listed in the unit
#: file's `RestartPreventExitStatus`: a refused configuration will never
#: succeed on retry, whereas this is exactly the case a restart is for.
EXIT_RUNTIME_FAILURE = 5


@dataclass(frozen=True)
class FatalRuntimeFailure:
    """What failed, and when. Reported at exit so the reason is not only in a log."""

    component: str
    reason: str
    at: str


class _FatalFailureRecorder:
    """Records the first fatal failure and asks the server to stop.

    First one wins: a cascading failure (the scheduler dies, and its death
    takes something else with it) should be reported by its cause, not by
    whichever consequence happened to be recorded last.

    Thread-safe because the recording can arrive from an asyncio callback
    while the shutdown is being observed from the serving thread.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failure: FatalRuntimeFailure | None = None
        self._stop_server = None

    def bind_server(self, server) -> None:
        """Attach the running uvicorn server this recorder may ask to stop."""
        with self._lock:
            self._stop_server = server

    def reset(self) -> None:
        with self._lock:
            self._failure = None
            self._stop_server = None

    @property
    def failure(self) -> FatalRuntimeFailure | None:
        with self._lock:
            return self._failure

    def record(self, component: str, reason: str) -> None:
        """Record a fatal failure and begin a graceful shutdown.

        Safe to call when no server is bound (a test, or a daemon run outside
        `serve`): the failure is still recorded, and nothing is terminated --
        this module never takes down a process it was not asked to supervise.
        """
        with self._lock:
            if self._failure is not None:
                logger.error(
                    "Additional fatal failure in %s (%s); already stopping for %s",
                    component,
                    reason,
                    self._failure.component,
                )
                return
            self._failure = FatalRuntimeFailure(
                component=component,
                reason=reason,
                at=datetime.now(timezone.utc).isoformat(),
            )
            server = self._stop_server

        logger.critical(
            "FATAL: %s failed (%s). Shutting down gracefully so the service "
            "supervisor can restart this process.",
            component,
            reason,
        )
        if server is None:
            logger.warning(
                "No server is bound, so nothing will be terminated. The failure "
                "is recorded; this process is not under this module's supervision.",
            )
            return
        # uvicorn's own graceful-shutdown flag: the serving loop notices it,
        # stops accepting, drains in-flight requests and runs the lifespan
        # shutdown -- which is what stops the kernel cleanly. Deliberately not
        # os._exit(), not SIGKILL, not sys.exit() from a callback thread.
        server.should_exit = True


#: One recorder per process, because there is one server per process.
_recorder = _FatalFailureRecorder()


def get_recorder() -> _FatalFailureRecorder:
    return _recorder


def record_fatal_failure(component: str, reason: str) -> None:
    """Report that `component` has failed unrecoverably (see the class above)."""
    _recorder.record(component, reason)
