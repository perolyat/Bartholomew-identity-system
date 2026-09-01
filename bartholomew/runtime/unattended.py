"""The harness side of an unattended run: identity, sampling, cleanup, freeze.

`bartholomew.runtime.evidence` is what the *service process* writes. This is
what the thing conducting the run uses -- an integration test today, and the
same code path a human would drive for a real unattended test period. Kept out
of `tests/` deliberately: a test that could only be run by running the tests
is not a procedure anybody can follow with a real deployment.

What it does, and nothing else:

* **Names the run**, once, and hands that name to every process it starts via
  `BARTH_UNATTENDED_RUN_ID`. One run, many process incarnations.
* **Samples the runtime's existing health surface** and appends what it got,
  verbatim, to the run's observations. It does not compute health, re-derive
  it, or decide what a healthy reading is -- `/api/health` already answers
  that, and a second opinion would be a second authority.
* **Owns the processes it started**, so a run that fails part-way does not
  leave a service holding a lock on a database that the next run needs. An
  orphaned `bartholomew serve` is the failure mode that makes the *following*
  run's evidence untrustworthy, so cleanup is part of evidence integrity
  rather than tidiness.
* **Freezes the report** at the end, through `evidence_report`.

It is not a supervisor: it never restarts anything on its own. A restart
during a run is something the operator or the scenario does deliberately, and
it is recorded as such.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any

from bartholomew.runtime.evidence import RUN_ID_ENV, EvidenceStore, validate_run_id
from bartholomew.runtime.evidence_report import write_frozen_report

#: How long to wait for a registered process to honour SIGTERM before it is
#: killed. Comfortably above `serve.SHUTDOWN_BUDGET_SECONDS` (30s), because a
#: process still inside its own graceful shutdown must not be killed by the
#: harness -- that would manufacture the very unclean shutdown the run is
#: trying to observe honestly.
TERMINATE_GRACE_SECONDS = 45.0


def new_run_id(prefix: str = "run") -> str:
    """A run id that sorts by start time and cannot collide.

    Time-prefixed so a directory of frozen reports reads in order, and
    suffixed with random hex so two runs started in the same second on the
    same machine are still distinguishable.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return validate_run_id(f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}")


class UnattendedRun:
    """One unattended test run against one database.

    Use as a context manager: leaving the block terminates every process this
    run started, whether the run succeeded, failed, or raised.
    """

    def __init__(self, db_path: str, *, run_id: str | None = None):
        self.db_path = str(db_path)
        self.run_id = run_id or new_run_id()
        validate_run_id(self.run_id)
        self.store = EvidenceStore(self.db_path)
        self.store.ensure_schema()
        self._processes: list[subprocess.Popen] = []

    # -- context -----------------------------------------------------------

    def __enter__(self) -> UnattendedRun:
        self.observe(
            kind="run_started",
            source="harness",
            payload={"db_path": self.db_path},
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        # Recorded before cleanup, so a run that blew up says so even if
        # cleanup then has its own trouble.
        self.observe(
            kind="run_finished",
            source="harness",
            payload={
                "error": None if exc is None else f"{exc_type.__name__}: {exc}",
            },
        )
        self.terminate_all()
        return False

    # -- evidence ----------------------------------------------------------

    def observe(self, *, kind: str, source: str, payload: dict[str, Any]) -> int:
        return self.store.record_observation(
            self.run_id,
            kind=kind,
            source=source,
            payload=payload,
        )

    def env(self, base: dict[str, str] | None = None) -> dict[str, str]:
        """The environment a process in this run must be started with."""
        env = dict(os.environ if base is None else base)
        env[RUN_ID_ENV] = self.run_id
        return env

    def sample_health(self, port: int, *, host: str = "127.0.0.1", timeout: float = 5.0) -> dict:
        """Read `/api/health` once and record exactly what came back.

        A failed read is recorded as a failed read. That is a real observation
        about an unattended run -- arguably the most important kind -- and
        dropping it would leave a silent hole where "the service stopped
        answering" should be.
        """
        url = f"http://{host}:{port}/api/health"
        try:
            with urllib.request.urlopen(
                url,
                timeout=timeout,
            ) as r:  # noqa: S310 - caller-fixed host
                body = json.loads(r.read().decode())
            payload = {"url": url, "reachable": True, "status_code": r.status, "health": body}
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
            payload = {"url": url, "reachable": False, "error": f"{type(e).__name__}: {e}"}
        self.observe(kind="health_sample", source="api:/api/health", payload=payload)
        return payload

    def freeze(self, out_path: str, *, item_limit: int = 200) -> dict:
        """Write the sealed end-of-run evidence document."""
        envelope = write_frozen_report(
            self.db_path,
            self.run_id,
            out_path,
            item_limit=item_limit,
        )
        return envelope

    # -- process ownership -------------------------------------------------

    def spawn(self, argv: list[str], *, cwd: str | None = None, **popen_kwargs) -> subprocess.Popen:
        """Start a process that belongs to this run, and remember it."""
        env = self.env(popen_kwargs.pop("env", None))
        proc = subprocess.Popen(argv, cwd=cwd, env=env, **popen_kwargs)  # noqa: S603
        self._processes.append(proc)
        self.observe(
            kind="process_spawned",
            source="harness",
            payload={"pid": proc.pid, "argv": list(argv)},
        )
        return proc

    def register(self, proc: subprocess.Popen) -> subprocess.Popen:
        """Adopt an already-started process so cleanup covers it too."""
        if proc not in self._processes:
            self._processes.append(proc)
        return proc

    def stop(self, proc: subprocess.Popen, *, grace: float = TERMINATE_GRACE_SECONDS) -> int:
        """Stop one process the way a service manager does, and record it.

        SIGTERM (or `terminate()` on Windows, which is what a service stop
        does there), then wait out the full grace period. `kill()` only after
        that, and it is recorded as such -- a killed process is exactly the
        case whose evidence must not later read as a clean stop.
        """
        if proc.poll() is not None:
            return proc.returncode
        killed = False
        if os.name == "nt":  # pragma: no cover - exercised on the Windows job
            proc.terminate()
        else:
            proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            killed = True
            proc.kill()
            proc.wait(timeout=10)
        self.observe(
            kind="process_stopped",
            source="harness",
            payload={
                "pid": proc.pid,
                "exit_code": proc.returncode,
                "killed_after_grace": killed,
                "signal": "SIGTERM" if not killed else "SIGTERM then SIGKILL",
            },
        )
        return proc.returncode

    def kill(self, proc: subprocess.Popen) -> int:
        """Kill a process outright, to stand in for a crash or a power cut.

        Recorded as an uncontrolled end so the resulting ``lost`` incarnation
        is legible as deliberate rather than as an unexplained gap.
        """
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
        self.observe(
            kind="process_killed",
            source="harness",
            payload={
                "pid": proc.pid,
                "exit_code": proc.returncode,
                "note": "Uncontrolled termination: the process had no chance to "
                "record its own shutdown.",
            },
        )
        return proc.returncode

    def terminate_all(self) -> None:
        """Leave no process of this run behind.

        Called on the way out of the context manager, including on failure.
        A leftover `serve` holds the kernel's process lock, so an orphan does
        not merely waste a process -- it makes the next run unable to start,
        and a run that cannot start produces no evidence.
        """
        leftovers = [p for p in self._processes if p.poll() is None]
        for proc in leftovers:
            try:
                self.stop(proc)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        if leftovers:
            self.observe(
                kind="orphans_cleaned",
                source="harness",
                payload={"pids": [p.pid for p in leftovers]},
            )
        self._processes = [p for p in self._processes if p.poll() is None]

    def wait_for_health(
        self,
        port: int,
        *,
        proc: subprocess.Popen | None = None,
        timeout: float = 90.0,
        host: str = "127.0.0.1",
    ) -> dict:
        """Block until the runtime reports itself running, or raise.

        Polls the existing health endpoint rather than sleeping a guessed
        interval, and fails immediately if the process it is waiting for has
        already exited -- waiting the full timeout on a process that is gone
        turns a clear failure into a slow, vague one.
        """
        deadline = time.monotonic() + timeout
        last: Any = None
        while time.monotonic() < deadline:
            if proc is not None and proc.poll() is not None:
                raise RuntimeError(
                    f"the process exited with code {proc.returncode} before it became healthy",
                )
            sample = self.sample_health(port, host=host)
            if sample.get("reachable"):
                runtime = sample["health"].get("components", {}).get("runtime", {})
                if runtime.get("state") == "running":
                    return sample
            last = sample
            time.sleep(1.0)
        raise TimeoutError(f"the runtime never reported itself running. Last sample: {last}")


__all__ = ["TERMINATE_GRACE_SECONDS", "UnattendedRun", "new_run_id"]
