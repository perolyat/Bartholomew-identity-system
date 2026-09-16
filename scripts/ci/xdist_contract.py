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
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:  # pragma: no cover - typing only
    from xdist.workermanage import WorkerController

#: Nodeids that reported a terminal outcome, and the collection the workers
#: agreed on. Kept on the plugin instance, not module state.
CONTRACT_PLUGIN_NAME = "bartholomew-xdist-contract"


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
