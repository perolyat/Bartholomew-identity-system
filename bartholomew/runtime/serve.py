"""`python -m bartholomew serve` — the non-interactive service entry point.

Before this, the only ways to start Bartholomew were a foreground shell script
running `uvicorn --reload` and a Docker Compose file. Both require somebody to
have opened a terminal, and neither survives a reboot. That is what made
Bartholomew a program Taylor had to remember to run rather than a service.

This module is deliberately thin. It is not a supervisor: restarting after a
crash and starting at boot are the operating system's job (systemd, Windows
service manager, Docker's restart policy), and Bartholomew inventing its own
supervisor would mean two of them disagreeing. What this provides is a stable,
argument-free thing for those supervisors to launch, and a set of refusals
that stop a supervised process from violating the runtime's own invariants.

Three invariants it enforces, each of which is a real bug if left to
configuration:

* **One writer.** The kernel's SQLite persistence is single-writer, and
  `KernelDaemon` takes a `ProcessLock` on the database file at startup. Two
  uvicorn workers means two daemons against one database, of which all but one
  die on the lock. `--workers > 1` is therefore refused with the reason, not
  silently downgraded.
* **No autoreload.** `--reload` spawns and replaces child processes; against a
  held process lock that is a crash loop, and under a supervisor it is an
  invisible one. Refused for the same reason.
* **A lock conflict is not a crash.** A second service instance against the
  same database exits with a distinct code and an operator-readable message,
  so a supervisor's restart loop doesn't hammer an unwinnable start.

Graceful shutdown is uvicorn's existing SIGTERM/SIGINT handling, which runs
the FastAPI shutdown hook, which runs `KernelDaemon.stop()` -- admission
close, drain, background-task cancellation, WAL checkpoint, lock release. That
path already existed and is not reimplemented here; what matters for
supervision is that a stop budget exists and that unit files allow for it.
"""

from __future__ import annotations

import logging
import os
import sys

#: Exit code for "another Bartholomew already owns this database". Distinct
#: from a generic failure so a supervisor (and an operator reading `systemctl
#: status`) can tell a configuration conflict from a crash.
EXIT_LOCK_HELD = 3

#: Exit code for a refused configuration (workers/reload). Also distinct: it
#: will never succeed on retry, and a restart loop should not pretend it might.
EXIT_BAD_CONFIG = 4

#: Longest `KernelDaemon.stop()` is expected to take: admission drain plus
#: background-task cancellation plus a WAL checkpoint. A supervisor's stop
#: timeout must exceed this or it will SIGKILL a daemon mid-checkpoint --
#: which is exactly the unclean shutdown the startup integrity checks then
#: have to recover from. `deploy/bartholomew.service` sets TimeoutStopSec
#: above this.
SHUTDOWN_BUDGET_SECONDS = 30

logger = logging.getLogger(__name__)


def resolve_port() -> int:
    """The port to listen on. `BARTH_API_PORT`, else 5173."""
    raw = (os.getenv("BARTH_API_PORT") or "5173").strip()
    try:
        port = int(raw)
    except ValueError as e:
        raise ValueError(f"BARTH_API_PORT={raw!r} is not an integer") from e
    if not (1 <= port <= 65535):
        raise ValueError(f"BARTH_API_PORT={port} is out of range")
    return port


def check_supervision_config(*, workers: int, reload: bool) -> None:
    """Refuse configurations that would run two daemons against one database.

    Raises `ValueError` with the reason. Called before anything binds a socket
    or touches the database, so a misconfigured unit file fails immediately and
    legibly rather than as a lock error several seconds later.
    """
    if workers != 1:
        raise ValueError(
            f"--workers={workers} is refused. Bartholomew's persistence is "
            "single-writer and the kernel takes an exclusive process lock on "
            "its database at startup, so each worker beyond the first would "
            "fail to start. Run one process per runtime; scale by running "
            "separate runtimes against separate databases, not by adding "
            "workers to one.",
        )
    if reload:
        raise ValueError(
            "--reload is refused. Autoreload replaces the server process while "
            "the old one may still hold the kernel's process lock, which turns "
            "a normal edit into a crash loop -- and under a service supervisor, "
            "an invisible one. Use it from a development shell "
            "(`uvicorn app:app --reload`) if you want it; a supervised service "
            "must not.",
        )


def serve(
    *,
    host: str | None = None,
    port: int | None = None,
    workers: int = 1,
    reload: bool = False,
    log_level: str = "info",
) -> int:
    """Run Bartholomew as a service. Returns a process exit code.

    Returns rather than exits, so the CLI owns the process's exit and this
    stays callable from a test.
    """
    import uvicorn

    from bartholomew.kernel.process_lock import ProcessLockHeldError

    try:
        check_supervision_config(workers=workers, reload=reload)
    except ValueError as e:
        print(f"[bartholomew serve] {e}", file=sys.stderr)
        return EXIT_BAD_CONFIG

    # Imported here, not at module scope: importing the API application
    # constructs the orchestrator and reads config files, which should happen
    # when we are actually about to serve rather than when the CLI is merely
    # listing its commands.
    from bartholomew_api_bridge_v0_1.services.api.app import app, resolve_bind_host

    try:
        bind_host = host or resolve_bind_host()
        bind_port = port if port is not None else resolve_port()
    except (RuntimeError, ValueError) as e:
        # `resolve_bind_host()` raises when a non-loopback bind was requested
        # without the deliberate opt-in. That refusal is the existing network
        # boundary and is preserved exactly: `serve` does not widen it.
        print(f"[bartholomew serve] {e}", file=sys.stderr)
        return EXIT_BAD_CONFIG

    print(
        f"[bartholomew serve] Starting on http://{bind_host}:{bind_port} "
        f"(stop budget {SHUTDOWN_BUDGET_SECONDS}s)",
        file=sys.stderr,
    )

    try:
        uvicorn.run(
            app,
            host=bind_host,
            port=bind_port,
            log_level=log_level,
            # Explicit rather than defaulted, so the invariant is visible at
            # the call site and not only in the check above.
            workers=1,
            reload=False,
            timeout_graceful_shutdown=SHUTDOWN_BUDGET_SECONDS,
        )
    except ProcessLockHeldError as e:
        # Raised out of KernelDaemon.start() through uvicorn's startup.
        print(
            f"[bartholomew serve] {e}\n"
            "[bartholomew serve] Another Bartholomew service is already using "
            "this database. Stop it before starting a second one, or point "
            "this instance at a different BARTH_DB_PATH.",
            file=sys.stderr,
        )
        return EXIT_LOCK_HELD

    return 0
