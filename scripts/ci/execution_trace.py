"""A time-accounted trace of the parallel test run, controller and workers.

Why this exists. The Windows Merge Candidate job has been cancelled at its
40-minute cap on roughly half of all attempts, and every diagnosis attempted
from the controller's console output has had to stop at the same sentence:
the log cannot distinguish a test whose call phase ran until the cap from a
test that finished long before and whose report did not arrive until the cap.
Both produce exactly the same two lines, a ``logstart`` and a ``PASSED``
twenty minutes apart, and the 120-second per-test timeout ends neither.

The discriminator is trivial once both ends are timestamped. The worker
writes its own record of every phase boundary *before* the report is
serialised and sent; the controller writes its record when the report
arrives. Subtracting the two gives the transport delay directly. This
module writes both records, and the deciding table is produced by
``scripts/ci/summarise_trace.py``.

What it records, per process, as JSON lines:

* ``session_start`` / ``session_finish`` / ``process_exit`` -- so a worker
  that reached its last test but never reached shutdown is visible as such.
* ``logstart`` and one ``phase`` event per setup/call/teardown, with wall
  clock, monotonic duration, outcome, and the number of SQLite connections
  the phase opened and the time it spent opening them.
* ``node_created`` / ``node_ready`` / ``node_collected`` / ``node_down``
  on the controller, with the error text of a lost worker.
* ``report_received`` on the controller for every report, so the worker's
  ``phase`` event and the controller's ``report_received`` event bracket
  the transport.
* ``stall`` events from a watchdog in every process, carrying a full
  all-thread traceback (worker) or the scheduler's queue and per-node
  outstanding work (controller).

The watchdogs also give the run an ending it can be read from. A run that
is cancelled from outside at the job cap produces no junit and no summary,
so the only evidence is whatever happened to be on the console; the
controller watchdog ends the session itself, first, with the state written
down. That is the opposite of raising the cap: it makes the failure arrive
sooner and say what it was.

Tracing is off unless ``BARTHO_EXEC_TRACE`` is set, and costs nothing when
off. It is enabled for the Windows jobs in every tier.

Environment:

``BARTHO_EXEC_TRACE``        set to ``1`` to enable.
``BARTHO_EXEC_TRACE_DIR``    directory for the JSONL files (default ``exec-trace``).
``BARTHO_EXEC_STALL_WARN_S`` seconds of no progress before a stall event and a
                             stack dump (default 180).
``BARTHO_EXEC_STALL_ABORT_S`` seconds of no progress before the controller ends
                             the run itself (default 900; 0 disables).
"""

from __future__ import annotations

import atexit
import contextlib
import faulthandler
import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

TRACE_PLUGIN_NAME = "bartholomew-execution-trace"

#: The real clocks, bound once at import. Several tests in this suite
#: monkeypatch `time.time` and `time.perf_counter` (freezegun, and
#: `tests/unit/kernel/test_time_utils.py` directly), and a trace that reads
#: the patched ones records a phase that took 1.7 billion seconds and a
#: transport delay to match -- which is exactly the measurement this module
#: exists to make trustworthy. Binding the function objects here keeps the
#: trace on the real clock whatever a test does to the module attributes.
_wall_clock = time.time
_monotonic = time.perf_counter

_DEFAULT_WARN_S = 180.0
_DEFAULT_ABORT_S = 900.0
#: Bound on stack dumps per stalled phase, so a genuinely wedged worker
#: cannot fill the runner's disk while nobody is watching.
_MAX_DUMPS_PER_STALL = 8


def enabled() -> bool:
    return os.environ.get("BARTHO_EXEC_TRACE", "").strip() not in ("", "0", "false", "no")


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


class _SqliteCounter:
    """Count connections opened and the wall time spent opening them.

    Installed once per process. ``sqlite3.connect`` is the single door
    every storage path in this repository goes through (``db_ctx.connect``
    wraps it, ``aiosqlite`` calls it on its worker thread), so this is
    where per-operation connection cost becomes measurable per test rather
    than per suite.
    """

    def __init__(self) -> None:
        self.count = 0
        self.seconds = 0.0
        self._lock = threading.Lock()
        self._original = sqlite3.connect

    def install(self) -> None:
        original = self._original

        def counting_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
            started = _monotonic()
            try:
                return original(*args, **kwargs)
            finally:
                elapsed = _monotonic() - started
                with self._lock:
                    self.count += 1
                    self.seconds += elapsed

        sqlite3.connect = counting_connect  # type: ignore[assignment]

    def snapshot(self) -> tuple[int, float]:
        with self._lock:
            return self.count, self.seconds


class _TraceWriter:
    """Append-only JSON lines, flushed on every write.

    Flushing every line is the point: the process this is recording may be
    ended by ``os._exit`` from a timeout handler, or killed outright when
    the job is cancelled. Anything still in a buffer at that moment is
    exactly the part that would have explained it.
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("a", encoding="utf-8", buffering=1)
        self._lock = threading.Lock()
        self.path = path

    def write(self, event: str, **fields: Any) -> None:
        record = {"t": round(_wall_clock(), 4), "event": event, **fields}
        line = json.dumps(record, default=str)
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()

    def close(self) -> None:
        with self._lock:
            try:
                self._fh.flush()
                self._fh.close()
            except (OSError, ValueError):
                pass


class _Watchdog:
    """Notice that nothing has happened for too long, and say what is true.

    ``on_stall`` is called once per ``warn_s`` of continued silence, up to
    ``_MAX_DUMPS_PER_STALL`` times for one stall. ``on_abort``, if given,
    is called once when the silence reaches ``abort_s``.
    """

    def __init__(
        self,
        *,
        warn_s: float,
        abort_s: float,
        on_stall: Any,
        on_abort: Any = None,
        poll_s: float = 5.0,
    ) -> None:
        self._warn_s = warn_s
        self._abort_s = abort_s
        self._on_stall = on_stall
        self._on_abort = on_abort
        self._poll_s = poll_s
        self._last = _monotonic()
        self._label = "startup"
        self._dumps = 0
        self._aborted = False
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="exec-trace-watchdog", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def beat(self, label: str) -> None:
        """Record progress. ``label`` names what is happening now."""
        with self._lock:
            self._last = _monotonic()
            self._label = label
            self._dumps = 0

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self._poll_s):
            with self._lock:
                idle = _monotonic() - self._last
                label = self._label
                dumps = self._dumps
            if self._abort_s and idle >= self._abort_s and not self._aborted:
                self._aborted = True
                if self._on_abort is not None:
                    self._on_abort(label, idle)
                return
            if idle >= self._warn_s * (dumps + 1) and dumps < _MAX_DUMPS_PER_STALL:
                with self._lock:
                    self._dumps += 1
                try:
                    self._on_stall(label, idle)
                except Exception as exc:  # pragma: no cover - diagnostics must not raise
                    print(f"exec-trace: stall handler failed: {exc!r}", file=sys.stderr)


class _BaseTrace:
    role = "process"

    def __init__(self, config: pytest.Config, trace_dir: Path, role: str) -> None:
        self.config = config
        self.role = role
        self.writer = _TraceWriter(trace_dir / f"{role}.jsonl")
        self.warn_s = _float_env("BARTHO_EXEC_STALL_WARN_S", _DEFAULT_WARN_S)
        self.abort_s = _float_env("BARTHO_EXEC_STALL_ABORT_S", _DEFAULT_ABORT_S)
        self.watchdog: _Watchdog | None = None

    def _note_exit(self) -> None:
        self.writer.write("process_exit", pid=os.getpid())
        self.writer.close()

    def dump_stacks(self, label: str, idle: float) -> None:
        """Write every thread's stack next to this process's trace.

        Next to it rather than into it: faulthandler writes its own format
        to a file descriptor, and these are meant to be read by a person.
        On the controller they name the execnet receiver thread for each
        worker, which is the only place a report that left a worker and
        never arrived can be sitting.
        """
        dump_path = self.writer.path.with_suffix(".stacks.txt")
        try:
            with dump_path.open("a", encoding="utf-8") as fh:
                fh.write(f"\n===== {self.role} stalled {idle:.0f}s in {label} =====\n")
                fh.flush()
                faulthandler.dump_traceback(file=fh, all_threads=True)
        except Exception as exc:  # pragma: no cover - diagnostics must not raise
            self.writer.write("stall_dump_failed", error=repr(exc))


class WorkerTrace(_BaseTrace):
    """The per-worker half: phase boundaries, SQLite cost, and stacks on a stall.

    Every event here is written *before* the corresponding report leaves
    the process, so a gap between a worker's ``phase`` event and the
    controller's ``report_received`` for the same nodeid is transport, not
    test time. That is the measurement the controller log could never make.
    """

    def __init__(self, config: pytest.Config, trace_dir: Path, workerid: str) -> None:
        super().__init__(config, trace_dir, workerid)
        self.workerid = workerid
        self.sqlite = _SqliteCounter()
        self.sqlite.install()
        self._phase_start = _monotonic()
        self._phase_sqlite = self.sqlite.snapshot()
        self._current = "<none>"
        self._gil_free_dump: Any = None

    # -- lifecycle ---------------------------------------------------------

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        self.writer.write(
            "session_start",
            role=self.workerid,
            pid=os.getpid(),
            executable=sys.executable,
            platform=sys.platform,
            cpu_count=os.cpu_count(),
        )
        self.watchdog = _Watchdog(
            warn_s=self.warn_s,
            # A worker never ends the run; only the controller does. It
            # dumps its stacks and keeps going, so the controller's own
            # abort finds the evidence already written.
            abort_s=0.0,
            on_stall=self._on_stall,
        )
        self.watchdog.start()
        self._arm_gil_free_dump()
        atexit.register(self._note_exit)

    def _arm_gil_free_dump(self) -> None:
        """Arm faulthandler's own timer, which does not need the GIL.

        The Python watchdog above cannot see the one case that would
        explain a stall the per-test timeout also fails to end: a thread
        inside a C call that never releases the GIL. No other Python
        thread runs then -- not pytest-timeout's `threading.Timer`, and
        not this module's watchdog either -- so the process looks frozen
        and nothing reports it.

        `dump_traceback_later` runs on a thread faulthandler owns in C and
        writes without the GIL, so it is the only instrument that survives
        that case. It is re-armed at every phase boundary and cancelled
        with the session, so a healthy run never writes a dump.
        """
        try:
            handle = (self.writer.path.with_suffix(".gil.txt")).open("a", encoding="utf-8")
        except OSError as exc:  # pragma: no cover - diagnostics must not raise
            self.writer.write("gil_dump_unavailable", error=repr(exc))
            return
        self._gil_free_dump = handle
        self._rearm_gil_free_dump()

    def _rearm_gil_free_dump(self) -> None:
        if self._gil_free_dump is None:
            return
        try:
            faulthandler.cancel_dump_traceback_later()
            faulthandler.dump_traceback_later(
                self.warn_s,
                repeat=True,
                file=self._gil_free_dump,
                exit=False,
            )
        except (RuntimeError, ValueError):  # pragma: no cover - defensive
            self._gil_free_dump = None

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        if self.watchdog is not None:
            self.watchdog.beat("session_finish")
        if self._gil_free_dump is not None:
            with contextlib.suppress(RuntimeError, ValueError):
                faulthandler.cancel_dump_traceback_later()
        count, seconds = self.sqlite.snapshot()
        self.writer.write(
            "session_finish",
            exitstatus=int(exitstatus),
            sqlite_connections=count,
            sqlite_connect_seconds=round(seconds, 3),
        )

    # -- per-test ----------------------------------------------------------

    def pytest_runtest_logstart(self, nodeid: str, location: Any) -> None:
        self._current = nodeid
        self._begin_phase(f"{nodeid}::setup")
        self.writer.write("logstart", nodeid=nodeid)

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        count, seconds = self.sqlite.snapshot()
        opened = count - self._phase_sqlite[0]
        connect_s = seconds - self._phase_sqlite[1]
        self.writer.write(
            "phase",
            nodeid=report.nodeid,
            when=report.when,
            outcome=report.outcome,
            duration_s=round(report.duration, 4),
            wall_s=round(_monotonic() - self._phase_start, 4),
            sqlite_connections=opened,
            sqlite_connect_s=round(connect_s, 4),
            # Live threads in this worker. A worker whose last tests run
            # far slower than its first is the shape a test that leaks a
            # daemon, a portal or a connection pool produces; the count
            # climbing across the run is what says so.
            #
            # Named live_threads, not threads: a stall event carries a
            # `threads` list of names, and one summary that mixed the two
            # crashed trying to sort a list against an int.
            live_threads=threading.active_count(),
        )
        self._begin_phase(f"{report.nodeid}::after-{report.when}")

    def _begin_phase(self, label: str) -> None:
        self._phase_start = _monotonic()
        self._phase_sqlite = self.sqlite.snapshot()
        if self.watchdog is not None:
            self.watchdog.beat(label)
        self._rearm_gil_free_dump()

    # -- stall -------------------------------------------------------------

    def _on_stall(self, label: str, idle: float) -> None:
        self.writer.write(
            "stall",
            where=label,
            nodeid=self._current,
            idle_s=round(idle, 1),
            threads=[t.name for t in threading.enumerate()],
        )
        self.dump_stacks(label, idle)
        # Also to stderr, which xdist forwards, so a run whose artifacts are
        # never uploaded still says something.
        print(
            f"exec-trace: {self.workerid} has made no progress for {idle:.0f}s "
            f"in {label} (test {self._current})",
            file=sys.stderr,
            flush=True,
        )


class ControllerTrace(_BaseTrace):
    """The controller half: worker lifecycle, report arrival, and the ending.

    The abort path is the part that changes what a failing run is worth. A
    job cancelled at its cap writes no junit and leaves the console as the
    only record; ending the session here, at a bound the run itself owns,
    means the queue state and every worker's stacks are on disk and the
    step fails for a stated reason.
    """

    def __init__(self, config: pytest.Config, trace_dir: Path) -> None:
        super().__init__(config, trace_dir, "controller")
        self.trace_dir = trace_dir
        self.aborted_reason: str | None = None

    # -- lifecycle ---------------------------------------------------------

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        self.writer.write(
            "session_start",
            role="controller",
            pid=os.getpid(),
            platform=sys.platform,
            cpu_count=os.cpu_count(),
            numprocesses=getattr(self.config.option, "numprocesses", None),
            dist=getattr(self.config.option, "dist", None),
        )
        self.watchdog = _Watchdog(
            warn_s=self.warn_s,
            abort_s=self.abort_s,
            on_stall=self._on_stall,
            on_abort=self._on_abort,
        )
        self.watchdog.start()
        atexit.register(self._note_exit)

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        if self.watchdog is not None:
            self.watchdog.stop()
        self.writer.write("session_finish", exitstatus=int(exitstatus))

    # -- worker lifecycle --------------------------------------------------

    def pytest_xdist_newgateway(self, gateway: Any) -> None:
        self.writer.write("node_created", gateway=gateway.id)
        self._beat(f"node_created:{gateway.id}")

    def pytest_testnodeready(self, node: Any) -> None:
        self.writer.write("node_ready", gateway=node.gateway.id)
        self._beat(f"node_ready:{node.gateway.id}")

    def pytest_xdist_node_collection_finished(self, node: Any, ids: list[str]) -> None:
        self.writer.write("node_collected", gateway=node.gateway.id, collected=len(ids))
        self._beat(f"node_collected:{node.gateway.id}")

    def pytest_testnodedown(self, node: Any, error: object | None) -> None:
        self.writer.write(
            "node_down",
            gateway=node.gateway.id,
            error=None if error is None else str(error),
            crashed=error is not None,
        )
        self._beat(f"node_down:{node.gateway.id}")

    # -- reports -----------------------------------------------------------

    def pytest_runtest_logstart(self, nodeid: str, location: Any) -> None:
        self.writer.write("logstart_received", nodeid=nodeid)
        self._beat(f"logstart:{nodeid}")

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        self.writer.write(
            "report_received",
            nodeid=report.nodeid,
            when=report.when,
            outcome=report.outcome,
            worker=getattr(report, "node", None) and report.node.gateway.id,
            duration_s=round(report.duration, 4),
        )
        self._beat(f"report:{report.nodeid}:{report.when}")

    def _beat(self, label: str) -> None:
        if self.watchdog is not None:
            self.watchdog.beat(label)

    # -- stall and abort ---------------------------------------------------

    def _scheduler_state(self) -> dict[str, Any]:
        """Queue and per-node outstanding work, best effort.

        Read from a watchdog thread while the controller's own loop is
        free to mutate it, so every access is defensive and a failure to
        read is itself recorded rather than raised.
        """
        state: dict[str, Any] = {}
        try:
            dsession = self.config.pluginmanager.getplugin("dsession")
        except Exception:  # pragma: no cover - defensive
            dsession = None
        if dsession is None:
            return {"scheduler": "unavailable"}
        sched = getattr(dsession, "sched", None)
        state["shuttingdown"] = getattr(dsession, "shuttingdown", None)
        state["failed_nodes"] = getattr(dsession, "_failed_nodes_count", None)
        try:
            active = list(getattr(dsession, "_active_nodes", []) or [])
            state["active_nodes"] = [n.gateway.id for n in active]
        except Exception as exc:
            state["active_nodes_error"] = repr(exc)
        if sched is None:
            return state
        try:
            state["workqueue_units"] = len(getattr(sched, "workqueue", ()) or ())
        except Exception as exc:
            state["workqueue_error"] = repr(exc)
        outstanding: dict[str, list[str]] = {}
        try:
            for node, workload in list(getattr(sched, "assigned_work", {}).items()):
                pending = [
                    nodeid
                    for unit in list(workload.values())
                    for nodeid, done in list(unit.items())
                    if not done
                ]
                outstanding[node.gateway.id] = pending[:20]
        except Exception as exc:
            state["assigned_work_error"] = repr(exc)
        state["outstanding"] = outstanding
        state["collections_registered"] = len(getattr(sched, "registered_collections", {}) or {})
        return state

    def _on_stall(self, label: str, idle: float) -> None:
        state = self._scheduler_state()
        self.writer.write("stall", where=label, idle_s=round(idle, 1), **state)
        # The controller's own threads matter as much as a worker's: a
        # report that left a worker and has not arrived is sitting in an
        # execnet receiver thread, and only a stack says which one.
        self.dump_stacks(label, idle)
        print(
            f"exec-trace: controller has received nothing for {idle:.0f}s "
            f"(last: {label}); outstanding={state.get('outstanding')} "
            f"queued_units={state.get('workqueue_units')}",
            file=sys.stderr,
            flush=True,
        )

    def _on_abort(self, label: str, idle: float) -> None:
        state = self._scheduler_state()
        self.aborted_reason = (
            f"no test report reached the controller for {idle:.0f}s "
            f"(bound: {self.abort_s:.0f}s); last event was {label}"
        )
        self.writer.write("abort", where=label, idle_s=round(idle, 1), **state)
        self.dump_stacks(label, idle)
        banner = [
            "",
            "=" * 78,
            "EXECUTION CONTRACT VIOLATED: the run made no progress and was ended by",
            "its own watchdog rather than by the job timeout.",
            f"  {self.aborted_reason}",
            f"  outstanding work per worker: {state.get('outstanding')}",
            f"  work units still queued:     {state.get('workqueue_units')}",
            f"  active workers:              {state.get('active_nodes')}",
            f"  workers lost so far:         {state.get('failed_nodes')}",
            f"  per-worker stacks and trace: {self.trace_dir}",
            "=" * 78,
            "",
        ]
        print("\n".join(banner), file=sys.stderr, flush=True)
        self.writer.close()
        sys.stderr.flush()
        sys.stdout.flush()
        # The controller's main thread is, by construction, blocked waiting
        # for a report that is not coming; there is no cooperative way to
        # end the session from here. Exiting is the honest outcome, and
        # everything that explains it is already on disk.
        os._exit(3)


def install(config: pytest.Config) -> None:
    """Register the trace plugin for this process, if tracing is enabled."""
    if not enabled():
        return
    if config.pluginmanager.hasplugin(TRACE_PLUGIN_NAME):
        return
    trace_dir = Path(os.environ.get("BARTHO_EXEC_TRACE_DIR", "exec-trace")).resolve()
    workerinput = getattr(config, "workerinput", None)
    plugin: _BaseTrace
    if workerinput is not None:
        plugin = WorkerTrace(config, trace_dir, str(workerinput.get("workerid", "gw?")))
    else:
        plugin = ControllerTrace(config, trace_dir)
    config.pluginmanager.register(plugin, TRACE_PLUGIN_NAME)
