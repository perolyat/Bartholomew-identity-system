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

import json
import os
import sys
import threading
import time
from pathlib import Path
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
    if os.environ.get("BARTHO_XDIST_CONTRACT") == "0":
        # The same escape hatch as install(). It is documented as the way
        # to get stock behaviour back if a correction in this module is
        # itself defective, so it has to cover all three corrections --
        # including this one -- or it does not do what it is for.
        return None
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

    **What counts as a re-drive, and which thread decides (2026-10-08).**
    The watcher reads the scheduler from its own thread, without a lock,
    while the controller's main thread may be half-way through changing it.
    What the watcher sees is therefore a *proposal*, never a fact. Inside
    xdist's initial ``schedule()``, between the first unit's assignment and
    the last, it sees work queued and nodes that hold nothing yet -- a state
    that exists only for those milliseconds and that ``_reschedule`` would
    not act on once ``schedule()`` returns. Counting that snapshot failed
    the W13 step of Integration run 36519235007 (2026-09-29, "251 unit(s)
    queued") and of Merge Candidate 37703362526 (2026-10-07, "252 unit(s)
    queued") on runs that had nothing wrong with them.

    So the count is decided where the schedule is consistent: in the
    handler, on the controller's main thread, by what ``_reschedule``
    actually did. A re-drive is counted only when, with no test completed
    since the watcher saw the run idle for the bound, it handed a queued
    unit to a node at or below xdist's own top-up threshold. A proposal the
    controller cannot confirm is not a re-drive: it is recorded as
    *declined*, printed and written to the report, and never counted. A
    proposal the controller never got to is reported too, and fails the W13
    check, because a run that cannot say is not clean. Nothing the watcher
    sees is dropped silently, and nothing it sees is counted on its say-so.

    One false-count class remains, and it fails closed: a node holding
    exactly two pending tests is usually busy (one running, the next
    already fetched), yet it is at the top-up threshold and a re-drive that
    tops it up is counted. In this suite that state needs a worker loss or
    a node's first units totalling two tests or fewer; see
    ``docs/WINDOWS_TEST_EXECUTION_CONTRACT.md`` clause W13.
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
    #: xdist's own heuristic: it tops a node up once its pending work
    #: drops to this. Mirrored so eligibility matches _reschedule's.
    DEPLETED_AT = 2
    #: How many proposals and declined proposals are printed, and how many
    #: declined ones are kept as notes. All are counted; only text is bounded.
    MAX_PROPOSAL_NOTES = 20

    def __init__(self, idle_after_s: float | None = None) -> None:
        self.idle_after_s = idle_after_s if idle_after_s is not None else _redrive_idle_after()
        # `redrives` and `declined` are written only on the controller's main
        # thread, in _handle_redrive; `proposed` only on the watcher thread.
        self.redrives = 0
        self.redrive_log: list[str] = []
        self.declined = 0
        self.declined_log: list[str] = []
        self.proposed = 0
        self._completions = 0
        self._last_progress = time.monotonic()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._config: pytest.Config | None = None
        self._installed_handler = False
        self._gave_up = False

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
        if self._thread is not None:
            # A poll already past its wait could still post; let it finish,
            # so `proposed` is final before undispatched is worked out.
            self._thread.join(timeout=self.POLL_S + 1.0)
        report_path = os.environ.get(CONTRACT_REPORT_ENV)
        if report_path:
            write_contract_report(
                report_path,
                redrives=self.redrives,
                gave_up=self._gave_up,
                notes=self.redrive_log,
                exitstatus=int(exitstatus),
                declined=self.declined,
                declined_notes=self.declined_log,
                undispatched=self._undispatched(),
            )

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
        that fails means "do not act", never an exception. The handler asks
        the same question on the controller's main thread, where the answer
        is consistent; only that answer can lead to a counted re-drive.
        """
        try:
            assigned = dict(getattr(sched, "assigned_work", {}) or {})
        except Exception:  # pragma: no cover - defensive
            return []
        pending_of = getattr(sched, "_pending_of", None)
        registered = getattr(sched, "registered_collections", None)
        eligible = []
        for node, workload in assigned.items():
            try:
                if node.shutting_down:
                    continue
                # Ready but still collecting. `_reschedule` skips such a
                # node (see make_safe_scheduler), so counting it as able to
                # take work makes the whole startup window -- collection
                # plus worker spin-up, which exceeds the idle bound on
                # Windows -- look like a stall before the first test has
                # even finished. That is where run 35185611629's first
                # twenty "re-drives" came from, all of them no-ops.
                if registered is not None and node not in registered:
                    continue
                # A node deep in a work unit is busy, not stalled. Ask the
                # same question the scheduler asks before it hands out more
                # work, so "a node able to take them" means what it says: a
                # node _reschedule would actually give a unit to. Without
                # this, any long test anywhere in the run looks like a stall
                # and the re-drive count stops meaning anything.
                if pending_of is not None and pending_of(workload) > self.DEPLETED_AT:
                    continue
            except Exception:  # pragma: no cover - defensive
                continue
            eligible.append(node)
        return eligible

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
            self._poll_once()

    def _poll_once(self) -> tuple[bool, str]:
        """One look at the run; propose a re-drive if it reads as stalled.

        The idle reading and the completion count it was taken against are
        read together, so the controller can tell whether a test completed
        between this look and its own.
        """
        with self._lock:
            idle = time.monotonic() - self._last_progress
            completions = self._completions
        if idle < self.idle_after_s:
            return False, "not idle"
        if self.redrives >= self.MAX_REDRIVES:
            self._announce_give_up()
            return False, "gave up"
        ok, why = self._should_redrive()
        if ok:
            self._post_redrive(idle, why, completions)
        return ok, why

    def _announce_give_up(self) -> None:
        """Say so, once, when the re-drive stops trying.

        Past this bound the run is failing for a reason this correction
        cannot reach, and the execution trace's abort is meant to own the
        ending -- but that trace is opt-in, so on a run without it nothing
        else would ever speak. A stranded run that prints nothing is the
        exact failure this whole package exists to remove.
        """
        if self._gave_up:
            return
        self._gave_up = True
        print(
            f"xdist-contract: giving up after {self.MAX_REDRIVES} re-drives. The run is "
            "stalled for a reason re-driving the scheduler does not fix. Set "
            "BARTHO_EXEC_TRACE=1 to capture stacks and have the watchdog end it.",
            file=sys.stderr,
            flush=True,
        )

    def _post_redrive(self, idle: float, why: str, completions: int | None = None) -> None:
        """Propose a re-drive to the controller. Counts nothing.

        Runs on the watcher thread, whose view of the scheduler may be torn
        (see the class docstring). Whether this was a re-drive is decided by
        ``_handle_redrive`` on the controller's main thread. ``completions``
        is the completion count the idle reading was taken against, so the
        controller can tell whether the run was still idle when it looked.
        """
        dsession = self._dsession()
        if dsession is None or self._stop.is_set():  # pragma: no cover - defensive
            return
        if not self._installed_handler:
            # DSession dispatches an event by calling `worker_<name>` on
            # itself. Binding the handler here, rather than subclassing
            # DSession, keeps this correction to one attribute on one
            # object and leaves pytest-xdist's own class untouched.
            setattr(dsession, f"worker_{self.EVENT_NAME}", self._handle_redrive)
            self._installed_handler = True
        self.proposed += 1
        if self.proposed <= self.MAX_PROPOSAL_NOTES:
            # Said even though it is not a re-drive: if the controller never
            # handles it, this line is the only trace of what was seen.
            _say(
                f"xdist-contract: re-drive proposal #{self.proposed}: no test completed for "
                f"{idle:.0f}s while {why}; awaiting the controller",
            )
        try:
            dsession.queue.put(
                (self.EVENT_NAME, {"idle": idle, "why": why, "completions": completions}),
            )
        except Exception as exc:  # pragma: no cover - defensive
            print(f"xdist-contract: could not post re-drive: {exc!r}", file=sys.stderr)

    # -- runs on the controller's main thread ------------------------------

    def _handle_redrive(
        self,
        idle: float | None = None,
        why: str = "",
        completions: int | None = None,
    ) -> None:
        """Ask the scheduler to assign work, from the thread that may, and
        count a re-drive only if it did.

        Reached only through ``DSession.loop_once``, so the scheduler
        mutation and the ``channel.send`` inside ``_assign_work_unit``
        happen exactly where pytest-xdist performs them itself -- and the
        schedule read here is consistent, which the watcher's never is.

        A node was re-driven when its ``_reschedule`` took a unit off the
        queue: the one thing a stall withholds. A unit that turns out to
        hold no test still counts -- after a worker loss, handing out such
        units is what drains the queue -- and the note says how many tests
        went with it. If no node took a unit, or a test completed after the
        proposal (so the run was not idle when the controller looked), the
        proposal described a state the controller was not in: it is
        recorded as declined and is not a re-drive.

        Nothing here may raise into ``loop_once``, which would end the
        session; the counts are taken before any text is built.
        """
        dsession = self._dsession()
        sched = getattr(dsession, "sched", None) if dsession else None
        if sched is None:  # pragma: no cover - defensive
            return
        with self._lock:
            completed_since = completions is not None and self._completions != completions
            if idle is None:
                idle = time.monotonic() - self._last_progress
        queued = _queued_units(sched)
        if completed_since:
            self._decline(
                idle,
                why,
                "a test completed after the proposal, so the run was not idle",
                sched,
                queued,
            )
            return
        handed: list[str] = []
        failures: list[str] = []
        for node in self._eligible_nodes(sched):
            units_before = _queued_units(sched)
            pending_before = _pending(sched, node)
            failure = ""
            try:
                sched._reschedule(node)
            except Exception as exc:
                failure = f"; the hand-off raised {exc!r}"
                failures.append(f"{_node_id(node)} raised {exc!r}")
                _say(f"xdist-contract: re-drive of {_node_id(node)} failed: {exc!r}")
            units = units_before - _queued_units(sched)
            if units > 0:
                pending_after = _pending(sched, node)
                handed.append(
                    f"{_node_id(node)} (pending {pending_before} -> {pending_after}: "
                    f"{units} unit(s), {max(pending_after - pending_before, 0)} test(s)"
                    f"{failure})",
                )
        if not handed:
            reason = "at the controller no node could be given queued work"
            if failures:
                reason += f" ({'; '.join(failures)})"
            self._decline(idle, why, reason, sched, queued)
            return
        self.redrives += 1
        try:
            note = (
                f"scheduler re-drive #{self.redrives}: no test completed for {idle:.0f}s; "
                f"the controller handed queued work to {', '.join(handed)} "
                f"({queued} unit(s) were queued)"
            )
        except Exception:  # pragma: no cover - defensive: the count stands
            note = f"scheduler re-drive #{self.redrives}"
        self.redrive_log.append(note)
        _say(f"xdist-contract: {note}")

    def _decline(self, idle: float, why: str, reason: str, sched: Any, queued: int) -> None:
        """Record a proposal the controller could not confirm. Not a re-drive."""
        self.declined += 1
        if self.declined > self.MAX_PROPOSAL_NOTES:
            return
        try:
            nodes = ", ".join(
                f"{_node_id(node)} pending {_pending(sched, node)}"
                for node in list(getattr(sched, "assigned_work", {}) or {})[:8]
            )
            note = (
                f"re-drive proposal declined (#{self.declined}): the watcher saw "
                f"{why or 'a stall'} after {idle:.0f}s, but {reason} "
                f"({queued} unit(s) queued; {nodes or 'no nodes'}); not a re-drive"
            )
        except Exception:  # pragma: no cover - defensive: the count stands
            note = f"re-drive proposal declined (#{self.declined})"
        self.declined_log.append(note)
        _say(f"xdist-contract: {note}")

    def _undispatched(self) -> int:
        """Proposals the controller neither confirmed nor declined."""
        return max(self.proposed - self.redrives - self.declined, 0)

    # -- reporting ---------------------------------------------------------

    def pytest_terminal_summary(self, terminalreporter: Any) -> None:
        if self.declined:
            # Not a defect and not a failure: said so that what the watcher
            # saw is on the record next to what the controller found.
            terminalreporter.write_line(
                f"xdist contract: {self.declined} re-drive proposal(s) declined -- the "
                "controller could not confirm a stall, so none was a re-drive (clause W13)",
            )
            for note in self.declined_log:
                terminalreporter.write_line(f"  {note}")
            if self.declined > len(self.declined_log):
                terminalreporter.write_line(
                    f"  ... and {self.declined - len(self.declined_log)} more",
                )
        undispatched = self._undispatched()
        if undispatched:
            terminalreporter.write_line(
                f"xdist contract: {undispatched} re-drive proposal(s) were never handled by "
                "the controller, so whether the run stalled is unknown (clause W13)",
                red=True,
            )
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
        if self._gave_up:
            terminalreporter.write_line(
                f"The re-drive gave up at its bound of {self.MAX_REDRIVES}. Anything after "
                "that point was not re-driven, so this run's ending is not explained by "
                "this correction.",
            )


def _queued_units(sched: Any) -> int:
    """How many work units the scheduler holds unassigned; 0 if unreadable."""
    try:
        return len(getattr(sched, "workqueue", ()) or ())
    except Exception:  # pragma: no cover - defensive
        return 0


def _pending(sched: Any, node: Any) -> int:
    """Tests assigned to ``node`` and not yet complete; -1 if unreadable."""
    try:
        return int(sched._pending_of((getattr(sched, "assigned_work", {}) or {}).get(node, {})))
    except Exception:  # pragma: no cover - defensive
        return -1


def _node_id(node: Any) -> str:
    try:
        return str(node.gateway.id)
    except Exception:  # pragma: no cover - defensive
        return repr(node)


def _say(text: str) -> None:
    """Write a line to stderr; never raise, because the caller may be
    running inside ``DSession.loop_once``."""
    try:
        print(text, file=sys.stderr, flush=True)
    except Exception:  # pragma: no cover - defensive
        pass


def _redrive_idle_after() -> float:
    raw = os.environ.get("BARTHO_XDIST_REDRIVE_IDLE_S")
    if not raw:
        return SchedulerRedrive.DEFAULT_IDLE_AFTER_S
    try:
        return float(raw)
    except ValueError:
        return SchedulerRedrive.DEFAULT_IDLE_AFTER_S


# ---------------------------------------------------------------------------
# Clause W13, enforced: a re-driven run is not clean evidence.
#
# The re-drive recovers the run, and pytest's exit status stays what the
# tests made it -- a developer's local run, or a diagnosis, still gets its
# answer. What must not happen is a run that needed recovering being counted
# as a clean pass by Merge Qualification, which reads exactly one thing: each
# required job's conclusion. So the enforcement point is that conclusion. The
# controller writes this report; a named step after the test step in every
# required xdist job reads it and fails the job if the run was re-driven, or
# if there is no report at all (a run that could not say is not clean). The
# job goes red for a stated W13 reason, and the tests' own junit stays true.
#
# What is counted (since 2026-10-08): a re-drive the controller confirmed, on
# its own thread, by handing a queued unit to a node at or below xdist's
# top-up threshold, with no test completed since the watcher saw the run idle.
# A proposal it could not confirm is written to the report as `declined` and
# does not fail W13; one it never handled is `undispatched` and does.
#
# Known limitation (recorded in docs/WINDOWS_TEST_EXECUTION_CONTRACT.md): the
# threshold is xdist's own, two pending tests, and a node holding exactly two
# is usually busy -- one running, the next already fetched -- not stalled. A
# re-drive that tops such a node up is still counted. In a healthy run of
# this suite that state does not arise: each top-up leaves a node above the
# threshold while work is queued. It needs a node's first units to total two
# tests or fewer (the idle clock includes collection, so no slow test is
# needed), or a worker loss. Such a re-drive's note gives the node's pending
# count before and after, so it can be told apart.
# ---------------------------------------------------------------------------

CONTRACT_REPORT_ENV = "BARTHO_XDIST_CONTRACT_REPORT"
CONTRACT_REPORT_SCHEMA = 1


def write_contract_report(
    path: str | os.PathLike[str],
    *,
    redrives: int,
    gave_up: bool,
    notes: list[str],
    exitstatus: int,
    declined: int = 0,
    declined_notes: list[str] | None = None,
    undispatched: int = 0,
) -> None:
    report = {
        "schema": CONTRACT_REPORT_SCHEMA,
        "clause": "W13",
        "redrives": int(redrives),
        "gave_up": bool(gave_up),
        "exitstatus": int(exitstatus),
        "notes": list(notes)[:50],
        # Watcher proposals the controller could not confirm: evidence, not
        # re-drives. The check below never counts them.
        "declined": int(declined),
        "declined_notes": list(declined_notes or [])[:50],
        # Proposals the controller neither confirmed nor declined. Unknown,
        # so the check below fails closed on any.
        "undispatched": int(undispatched),
    }
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    except OSError as exc:  # pragma: no cover - reported by the check as "no report"
        print(f"xdist-contract: could not write {target}: {exc!r}", file=sys.stderr)


def check_contract_report(path: str | os.PathLike[str]) -> tuple[bool, str]:
    """Whether the run that wrote ``path`` is clean W13 evidence, and why.

    Fails closed unless the file is this contract's own report: a JSON object
    whose ``schema`` is CONTRACT_REPORT_SCHEMA, whose ``clause`` is "W13" and
    whose ``redrives`` is a non-negative integer. A JSON boolean is not an
    integer here, and nothing is coerced. Any other content, however it came
    to be at ``path``, establishes nothing about this run and is refused with
    the mismatch named. ``declined`` and ``undispatched`` (added 2026-10-08)
    are optional, read as zero when absent, and held to the same standard
    when present. A report that records proposals the controller never
    handled is refused: whether that run stalled is unknown. Declined
    proposals are evidence, never re-drives, and never change the verdict.
    """
    target = Path(path)
    if not target.is_file():
        return False, (
            f"no W13 contract report at {target}: the run did not record whether it "
            f"needed a scheduler re-drive ({CONTRACT_REPORT_ENV} unset, the contract "
            "disabled, or the session never finished), so it cannot count as clean"
        )
    try:
        report = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return False, f"unreadable W13 contract report at {target}: {exc!r}"
    problem = _contract_report_problem(report)
    if problem is not None:
        return False, (
            f"W13 contract report at {target} is not this contract's report ({problem}), "
            "so it establishes nothing about this run and cannot count as clean"
        )
    redrives = report["redrives"]
    if redrives:
        raw_notes = report.get("notes")
        notes = (
            "; ".join(str(note) for note in raw_notes[:5]) if isinstance(raw_notes, list) else ""
        )
        return False, (
            f"the run completed only after {redrives} scheduler re-drive(s) (clause W13): "
            "it completed over a pytest-xdist defect and is not clean evidence. "
            f"{notes}"
        )
    undispatched = report.get("undispatched")
    if _positive_int(undispatched):
        return False, (
            f"{undispatched} scheduler re-drive proposal(s) were never handled by the "
            "controller, so whether the run stalled is unknown (clause W13); it cannot "
            "count as clean"
        )
    declined = report.get("declined")
    if _positive_int(declined):
        return True, (
            "no scheduler re-drive was needed (clause W13); "
            f"{declined} re-drive proposal(s) were declined by the controller, which could "
            "not confirm a stall, so none was a re-drive"
        )
    return True, "no scheduler re-drive was needed (clause W13)"


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _contract_report_problem(report: object) -> str | None:
    """Why ``report`` is not a report this contract wrote, or None if it is.

    ``schema`` and ``redrives`` must be real integers: ``bool`` is a subclass of
    ``int`` in Python and ``True == 1``, so it is excluded explicitly, and a
    float such as ``1.0`` is refused rather than compared equal.
    """
    if not isinstance(report, dict):
        return f"top level is {type(report).__name__}, not an object"
    schema = report.get("schema")
    if isinstance(schema, bool) or not isinstance(schema, int) or schema != CONTRACT_REPORT_SCHEMA:
        return f"schema {schema!r}, expected {CONTRACT_REPORT_SCHEMA}"
    clause = report.get("clause")
    if clause != "W13":
        return f"clause {clause!r}, expected 'W13'"
    redrives = report.get("redrives")
    if isinstance(redrives, bool) or not isinstance(redrives, int) or redrives < 0:
        return f"redrives {redrives!r} is not a non-negative integer"
    # Added 2026-10-08. Absent means a report written before then, read as
    # zero; present, they are held to the same standard as `redrives`.
    for field in ("declined", "undispatched"):
        if field in report:
            value = report[field]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return f"{field} {value!r} is not a non-negative integer"
    return None


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] != "check":
        print("usage: python -m scripts.ci.xdist_contract check <report.json>", file=sys.stderr)
        return 2
    ok, message = check_contract_report(argv[2])
    if ok:
        print(f"W13 clean-run contract: {message}")
        return 0
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::error title=W13 clean-run contract::{message}")
    print(f"W13 clean-run contract NOT met: {message}")
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv))
