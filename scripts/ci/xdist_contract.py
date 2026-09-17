"""Worker ownership, replacement and work accounting for the parallel test run.

This module owns the parts of the Windows test-execution contract that are
about *work never being lost*, as opposed to the parts that are about
*seeing what happened* (`scripts.ci.execution_trace`). It is always active,
on every platform, in every tier: a defect that silently drops tests is not
something to switch on only when someone is looking.

Two defects are corrected here, both in the controller.

**1. A replacement worker that dies before it has collected crashes the
controller.**

`pytest-xdist`'s load-scope schedulers (``--dist loadscope`` and the
``--dist loadfile`` this repository uses) keep two per-node maps:
``assigned_work``, populated by ``add_node()`` the moment a worker reports
ready, and ``registered_collections``, populated later by
``add_node_collection()`` when that worker finishes collecting. Between
those two moments a node is in ``assigned_work`` but not in
``registered_collections``.

``remove_node()`` -- called when *any* worker dies -- re-queues the dead
worker's unfinished work and then calls ``_reschedule()`` on every node in
``assigned_work``. For a replacement worker still in that window,
``_reschedule()`` sees no pending work, calls ``_assign_work_unit()``, and
that method's first statement is ``self.registered_collections[node]``.
The result is an unhandled ``KeyError: <WorkerController gwN>`` raised
inside the controller's event loop, which pytest reports as
``INTERNALERROR`` and which ends the entire session -- every test not yet
run is simply never run.

This is exactly the shape observed on the 16 Sep re-run of Merge Candidate
34037740445: four workers died inside one minute, and the controller
crashed rescheduling a replacement at 61 % of the suite. It needs two
deaths close together -- the first to create a replacement, the second to
trigger a reschedule while that replacement is still collecting -- which is
why it took a heavy-burst run to surface it.

The correction is to make ``_reschedule()`` skip a node that has not
registered a collection yet. Such a node cannot be given work in any case;
it will be rescheduled by ``add_node_collection()`` the moment it is ready,
which is the path a healthy replacement already takes.

**2. Work owned by a lost worker can end the run without ever being
reported.**

``remove_node()`` puts the dead worker's unfinished work unit back on the
queue, but nothing verifies that it is ever taken off again. If the
remaining workers are shutting down, or the run ends for any other reason,
those tests are neither run nor reported -- and the session summary counts
only what *did* report, so the run can print a green-looking line while
having silently skipped a whole file. Merge Candidate 34866265459 ended
``1 failed, 4983 passed, 79 skipped`` with 17 of the 5080 selected tests
never run and nothing in the summary saying so.

The correction is an explicit reconciliation at session end: every nodeid
the workers collected must have produced a terminal report. Any that did
not are named, and the session fails. This does not weaken, skip or reorder
anything -- it refuses to call a run complete that is not.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:  # pragma: no cover - typing only
    from xdist.workermanage import WorkerController

#: Nodeids that reported a terminal outcome, and the collection the workers
#: agreed on. Kept on the plugin instance, not module state.
CONTRACT_PLUGIN_NAME = "bartholomew-xdist-contract"
REDRIVE_PLUGIN_NAME = "bartholomew-xdist-redrive"


def make_safe_scheduler(config: pytest.Config, log: Any = None) -> Any:
    """Return a load-scope scheduler that cannot crash on a replacement worker.

    Returns ``None`` for any distribution mode this correction does not
    apply to, which tells pytest-xdist to fall back to its own choice.
    """
    dist = getattr(config.option, "dist", "no")
    if dist not in ("loadscope", "loadfile"):
        return None

    from xdist.scheduler.loadfile import LoadFileScheduling
    from xdist.scheduler.loadscope import LoadScopeScheduling

    base = LoadFileScheduling if dist == "loadfile" else LoadScopeScheduling

    class CollectionAwareScheduling(base):  # type: ignore[valid-type, misc]
        """``_reschedule`` that tolerates a node which has not collected yet.

        The only behavioural difference from the base class is the guard
        below. A node with no registered collection is skipped rather than
        being handed a work unit it cannot be told about; it is picked up
        again by ``add_node_collection()``, the ordinary path for a node
        that has just become useful.
        """

        def _reschedule(self, node: WorkerController) -> None:
            if node not in self.registered_collections:
                # Ready, but still collecting. It owns no work, it can be
                # given none, and asking would raise KeyError inside the
                # controller's event loop.
                return
            super()._reschedule(node)

    return CollectionAwareScheduling(config, log)


class WorkAccounting:
    """Reconcile what was collected against what was reported.

    Installed on the controller only (a worker has no view of the run as a
    whole). ``node_collection`` is what the workers agreed they had
    collected; ``reported`` is every nodeid that produced a terminal
    report, whether it passed, failed, errored or was skipped.
    """

    def __init__(self) -> None:
        self.expected: set[str] = set()
        self.reported: set[str] = set()
        self.lost_workers: list[str] = []
        self.summary: str | None = None

    # -- xdist controller hooks -------------------------------------------

    def pytest_xdist_node_collection_finished(
        self,
        node: WorkerController,
        ids: list[str],
    ) -> None:
        # Every worker collects the same set (xdist refuses to start if they
        # disagree), so a union is that set; it is built as a union rather
        # than taken from the first worker so that a replacement joining
        # later cannot narrow it.
        self.expected.update(ids)

    def pytest_testnodedown(self, node: WorkerController, error: object | None) -> None:
        if error is not None:
            self.lost_workers.append(f"{node.gateway.id}: {error}")

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        # A test is accounted for once it has produced any terminal
        # outcome: a teardown report (the normal end of a passing or
        # failing test), or a non-passing setup/call report (a skip at
        # setup, or a crash that never reached teardown).
        if report.when == "teardown" or not report.passed:
            self.reported.add(report.nodeid)

    # -- reconciliation ----------------------------------------------------

    @property
    def unreported(self) -> list[str]:
        return sorted(self.expected - self.reported)

    @pytest.hookimpl(tryfirst=True)
    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        # Only meaningful for a run that was allowed to finish its work. A
        # run stopped on purpose (-x, --maxfail, a keyboard interrupt, an
        # internal error) has tests left over by definition, and saying so
        # would bury the real reason it stopped.
        if session.shouldstop or session.shouldfail:
            return
        if exitstatus not in (
            pytest.ExitCode.OK,
            pytest.ExitCode.TESTS_FAILED,
        ):
            return
        missing = self.unreported
        if not missing:
            return

        detail = "\n".join(f"  {nodeid}" for nodeid in missing[:50])
        if len(missing) > 50:
            detail += f"\n  ... and {len(missing) - 50} more"
        context = ""
        if self.lost_workers:
            context = "\nWorkers lost during the run:\n" + "\n".join(
                f"  {line}" for line in self.lost_workers
            )
        # Turn the session red. exitstatus is returned by value up the
        # stack, so the visible failure is raised through the terminal
        # summary hook below plus a non-zero exit code set here.
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
        self.summary = (
            f"{len(missing)} collected test(s) were never reported -- work was "
            f"lost, not run:\n{detail}{context}"
        )

    def pytest_terminal_summary(self, terminalreporter: Any) -> None:
        if self.summary:
            terminalreporter.write_sep("=", "work accounting: TESTS LOST", red=True, bold=True)
            terminalreporter.write_line(self.summary)


def install(config: pytest.Config) -> None:
    """Register the contract plugin on the controller.

    A worker process (one with ``workerinput``) gets nothing: both
    corrections are controller-side.
    """
    if config.pluginmanager.hasplugin(CONTRACT_PLUGIN_NAME):
        return
    if hasattr(config, "workerinput"):
        return
    if os.environ.get("BARTHO_XDIST_CONTRACT") == "0":
        return
    config.pluginmanager.register(WorkAccounting(), CONTRACT_PLUGIN_NAME)
    # Only meaningful with a controller that distributes work; a serial run
    # has no scheduler to re-drive.
    if getattr(config.option, "dist", "no") != "no":
        config.pluginmanager.register(SchedulerRedrive(), REDRIVE_PLUGIN_NAME)


# ---------------------------------------------------------------------------
# Defect 3: the controller stops asking its own scheduler for work.
# ---------------------------------------------------------------------------


class SchedulerRedrive:
    """Re-ask the scheduler for work when a live run has stopped moving.

    **The defect, measured.** Merge Candidate 35090997379's Windows job
    recorded, three minutes into a stall and again three minutes after that::

        controller STALL after 184.9s; queued_units=47  event_queue_depth=0
          shutting_down={'gw1': False, 'gw2': False, 'gw3': False, 'gw5': False}

    Forty-seven work units queued. Four workers alive, none shutting down,
    each blocked in ``TestQueue.get()`` waiting to be given work. The
    controller's own event queue empty. Simultaneous stacks confirm all of
    it: every worker's main thread in ``xdist/remote.py`` line 214, the
    controller's in ``queue.get`` inside ``dsession.loop_once``.

    Nothing is broken and nothing is stuck. ``_reschedule`` would assign
    immediately if anything called it, and nothing does.

    **Why the run can reach that state.** ``WorkerInteractor.run_one_test``
    fetches the index *after* the one it is about to run, so that
    ``pytest_runtest_protocol`` can be handed a correct ``nextitem``::

        self.item_index = self.nextitem_index
        self.nextitem_index = self.torun.get()          # blocks
        ...
        self.sendevent("runtest_protocol_complete", ...)  # wakes the controller

    A ``--dist loadfile`` work unit is one file, sent as a single batch, so
    a worker always blocks before the last test of its unit and can only
    proceed once another unit is assigned. The controller assigns one only
    while handling a completion event. The wake-up and the work are
    mutually dependent, and losing one event anywhere in that loop stops it
    permanently.

    ``DSession.loop_once`` does wake every two seconds -- it is a
    ``queue.get(timeout=2.0)`` in a ``while 1`` -- but it only checks
    whether every node has died. The schedule is never re-examined.

    **The correction.** A controller-side thread watches for *test
    completions*, not events. When none has arrived for ``idle_after``
    seconds while the scheduler still holds queued work and at least one
    node is alive and not shutting down, it posts one event onto the
    controller's own queue. ``DSession.loop_once`` dispatches it on the
    main thread, which is where pytest-xdist does all of its scheduling and
    all of its channel sends, and the handler simply calls ``_reschedule``
    on every eligible node.

    This is not a retry and not a sleep. The condition is a measured fact
    about the scheduler's own state -- work queued, node able, controller
    idle -- and the action is the one call the controller failed to make.
    A node that is genuinely busy has ``_reschedule`` return immediately on
    its own pending count, so a re-drive that was not needed changes
    nothing.

    **Every re-drive is a defect, and is reported as one.** The count is
    written to the trace and printed in the terminal summary. A run that
    needed re-driving completed, but it completed over a bug, and the log
    says so rather than quietly looking green.
    """

    #: Seconds without a completed test before the scheduler is re-asked.
    #:
    #: Measured, not guessed. Merge Candidate 35171239428 was the first
    #: Windows run ever to reach its summary, and it needed **thirteen**
    #: re-drives to get there: the queue fell 50 -> 46 -> 42 -> ... -> 2, so
    #: the deadlock recurs at roughly every work-unit boundary rather than
    #: being a rare event. At a 60-second bound that cost about fifteen
    #: minutes of dead time and the run took 33m46s of its 40-minute budget.
    #:
    #: A short bound is safe because a re-drive that was not needed is a
    #: no-op: `_reschedule` returns on the node's own pending count, so
    #: firing while a worker is legitimately busy changes nothing. The bound
    #: is therefore set by how much dead time is acceptable, not by fear of
    #: false positives.
    DEFAULT_IDLE_AFTER_S = 8.0
    #: How often the watcher looks. Cheap: it reads three attributes.
    POLL_S = 1.0
    #: If this many re-drives do not restore progress, the run is failing
    #: for some other reason and the execution trace's abort should own the
    #: ending rather than this class hiding it behind an endless nudge.
    MAX_REDRIVES = 200

    EVENT_NAME = "bartholomew_scheduler_redrive"

    def __init__(self, idle_after_s: float | None = None) -> None:
        self.idle_after_s = idle_after_s if idle_after_s is not None else _redrive_idle_after()
        self.redrives = 0
        self.redrive_log: list[str] = []
        self._completions = 0
        self._last_progress = time.monotonic()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._config: pytest.Config | None = None
        self._installed_handler = False

    # -- progress ----------------------------------------------------------

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        # Progress means a test finished, not that some message arrived. An
        # earlier version of this watch counted any event, and a replacement
        # worker's startup chatter was enough to make a deadlocked run look
        # alive for the rest of its 40 minutes.
        if report.when != "teardown" and report.passed:
            return
        with self._lock:
            self._completions += 1
            self._last_progress = time.monotonic()

    # -- lifecycle ---------------------------------------------------------

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        self._config = session.config
        with self._lock:
            self._last_progress = time.monotonic()
        self._thread = threading.Thread(
            target=self._watch,
            name="xdist-scheduler-redrive",
            daemon=True,
        )
        self._thread.start()

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        self._stop.set()

    # -- the watch ---------------------------------------------------------

    def _dsession(self) -> Any:
        if self._config is None:
            return None
        try:
            return self._config.pluginmanager.getplugin("dsession")
        except Exception:  # pragma: no cover - defensive
            return None

    def _eligible_nodes(self, sched: Any) -> list[Any]:
        """Nodes that could be given work right now, best effort.

        Read from a watcher thread while the controller's own loop may be
        mutating the same structures, so every access is defensive: a read
        that fails means "do not act", never an exception.
        """
        try:
            return [
                node for node in list(getattr(sched, "assigned_work", {})) if not node.shutting_down
            ]
        except Exception:  # pragma: no cover - defensive
            return []

    def _should_redrive(self) -> tuple[bool, str]:
        dsession = self._dsession()
        if dsession is None:
            return False, "no dsession"
        sched = getattr(dsession, "sched", None)
        if sched is None:
            return False, "no scheduler yet"
        if getattr(dsession, "shuttingdown", False):
            return False, "already shutting down"
        try:
            queued = len(getattr(sched, "workqueue", ()) or ())
        except Exception:  # pragma: no cover - defensive
            return False, "queue unreadable"
        if not queued:
            # Nothing to hand out. A run with no queued work that is not
            # progressing is a different problem, and the execution trace's
            # abort owns it.
            return False, "no queued work"
        if not self._eligible_nodes(sched):
            return False, "no node can take work"
        try:
            if dsession.queue.qsize() > 0:
                # Events are waiting to be drained: the controller is busy,
                # not idle. Posting another would be noise.
                return False, "controller has events pending"
        except Exception:  # pragma: no cover - defensive
            pass
        return True, f"{queued} unit(s) queued and a node able to take them"

    def _watch(self) -> None:
        while not self._stop.wait(self.POLL_S):
            with self._lock:
                idle = time.monotonic() - self._last_progress
            if idle < self.idle_after_s:
                continue
            if self.redrives >= self.MAX_REDRIVES:
                continue
            ok, why = self._should_redrive()
            if not ok:
                continue
            self._post_redrive(idle, why)

    def _post_redrive(self, idle: float, why: str) -> None:
        dsession = self._dsession()
        if dsession is None:  # pragma: no cover - defensive
            return
        if not self._installed_handler:
            # DSession dispatches an event by calling `worker_<name>` on
            # itself. Binding the handler here, rather than subclassing
            # DSession, keeps this correction to one attribute on one
            # object and leaves pytest-xdist's own class untouched.
            setattr(dsession, f"worker_{self.EVENT_NAME}", self._handle_redrive)
            self._installed_handler = True
        self.redrives += 1
        note = (
            f"scheduler re-drive #{self.redrives}: no test completed for "
            f"{idle:.0f}s while {why}"
        )
        self.redrive_log.append(note)
        print(f"xdist-contract: {note}", file=sys.stderr, flush=True)
        try:
            dsession.queue.put((self.EVENT_NAME, {}))
        except Exception as exc:  # pragma: no cover - defensive
            print(f"xdist-contract: could not post re-drive: {exc!r}", file=sys.stderr)

    # -- runs on the controller's main thread ------------------------------

    def _handle_redrive(self) -> None:
        """Ask the scheduler to assign work, from the thread that may.

        Reached only through ``DSession.loop_once``, so the scheduler
        mutation and the ``channel.send`` inside ``_assign_work_unit``
        happen exactly where pytest-xdist performs them itself.
        """
        dsession = self._dsession()
        sched = getattr(dsession, "sched", None) if dsession else None
        if sched is None:  # pragma: no cover - defensive
            return
        for node in self._eligible_nodes(sched):
            try:
                sched._reschedule(node)
            except Exception as exc:  # pragma: no cover - defensive
                print(
                    f"xdist-contract: re-drive of {node.gateway.id} failed: {exc!r}",
                    file=sys.stderr,
                )

    # -- reporting ---------------------------------------------------------

    def pytest_terminal_summary(self, terminalreporter: Any) -> None:
        if not self.redrives:
            return
        terminalreporter.write_sep(
            "=",
            f"xdist contract: the scheduler stalled and was re-driven {self.redrives} time(s)",
            yellow=True,
            bold=True,
        )
        terminalreporter.write_line(
            "The run completed, but it completed over a pytest-xdist defect: the "
            "controller stopped assigning queued work to idle workers. See "
            "docs/WINDOWS_TEST_EXECUTION_CONTRACT.md clause W13.",
        )
        for note in self.redrive_log[:20]:
            terminalreporter.write_line(f"  {note}")
        if len(self.redrive_log) > 20:
            terminalreporter.write_line(f"  ... and {len(self.redrive_log) - 20} more")


def _redrive_idle_after() -> float:
    raw = os.environ.get("BARTHO_XDIST_REDRIVE_IDLE_S")
    if not raw:
        return SchedulerRedrive.DEFAULT_IDLE_AFTER_S
    try:
        return float(raw)
    except ValueError:
        return SchedulerRedrive.DEFAULT_IDLE_AFTER_S
