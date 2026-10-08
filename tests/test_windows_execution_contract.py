"""Acceptance suite for the Windows test-execution contract.

The contract is documented in `docs/WINDOWS_TEST_EXECUTION_CONTRACT.md`; the
implementation is `scripts/ci/xdist_contract.py` and
`scripts/ci/execution_trace.py`. These tests drive the two of them the way
the Merge Candidate does -- through a real pytest run, in a real xdist
controller -- rather than asserting on internals, so the clauses survive a
replacement of either module.

Every test here is deterministic and platform-independent. The failures
they pin were only ever *observed* on Windows, because Windows is where
tests are slow enough to trip a timeout and workers are therefore lost; the
defects themselves are in the controller and are reproducible anywhere once
a worker is made to die on purpose.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

xdist = pytest.importorskip("xdist")


# ---------------------------------------------------------------------------
# Clause: worker replacement. A worker that dies while a replacement for an
# earlier death is still collecting must not crash the controller.
# ---------------------------------------------------------------------------


class _FakeGateway:
    def __init__(self, ident: str) -> None:
        self.id = ident


class _FakeNode:
    """The two attributes the load-scope scheduler touches on a node."""

    def __init__(self, ident: str) -> None:
        self.gateway = _FakeGateway(ident)
        self.shutting_down = False
        self.sent: list[list[int]] = []

    def send_runtest_some(self, indices: list[int]) -> None:
        self.sent.append(list(indices))

    def shutdown(self) -> None:
        self.shutting_down = True

    def __repr__(self) -> str:  # pragma: no cover - only for failure messages
        return f"<WorkerController {self.gateway.id}>"


def _schedule_with_a_still_collecting_replacement(scheduler):
    """Drive a scheduler into the exact state the controller crashed in.

    ``gw0`` has collected and owns a file's worth of work. ``gw1`` is a
    replacement that has reported ready -- so the scheduler knows about it
    -- but has not finished collecting. ``gw0`` then dies. ``remove_node``
    re-queues gw0's work and reschedules every node it knows about,
    including the replacement.
    """
    collection = [
        "tests/test_alpha.py::test_one",
        "tests/test_alpha.py::test_two",
        "tests/test_beta.py::test_three",
    ]
    gw0 = _FakeNode("gw0")
    scheduler.add_node(gw0)
    scheduler.add_node_collection(gw0, collection)
    scheduler.schedule()

    # The replacement: ready (so add_node has run) but still collecting
    # (so add_node_collection has not).
    gw1 = _FakeNode("gw1")
    scheduler.add_node(gw1)

    return scheduler.remove_node(gw0)


def _make_config(pytester: pytest.Pytester, dist: str = "loadfile") -> pytest.Config:
    pytester.makepyfile(test_alpha="def test_one(): pass\ndef test_two(): pass\n")
    # `-n` is translated into `--tx` by xdist's command-line hook, which
    # parseconfigure does not run; the tx spec is what the scheduler reads.
    return pytester.parseconfigure("--tx", "popen", "--dist", dist)


def test_stock_xdist_scheduler_crashes_on_a_still_collecting_replacement(pytester):
    """The defect, pinned as a defect.

    If pytest-xdist ever fixes this upstream, this test fails and the
    override in `scripts/ci/xdist_contract.py` can be retired -- which is
    the only way anyone would find out.
    """
    from xdist.scheduler.loadfile import LoadFileScheduling

    config = _make_config(pytester)
    with pytest.raises(KeyError):
        _schedule_with_a_still_collecting_replacement(LoadFileScheduling(config))


def test_the_contract_scheduler_survives_a_still_collecting_replacement(pytester):
    from scripts.ci.xdist_contract import make_safe_scheduler

    config = _make_config(pytester)
    scheduler = make_safe_scheduler(config)
    assert scheduler is not None

    crashitem = _schedule_with_a_still_collecting_replacement(scheduler)

    # The dead worker's in-flight test is still identified, and its file is
    # back on the queue for whoever can take it.
    assert crashitem == "tests/test_alpha.py::test_one"
    assert len(scheduler.workqueue) >= 1


def test_the_replacement_gets_the_requeued_work_once_it_has_collected(pytester):
    """Replacement succeeds: the work a lost worker owned is run, not dropped."""
    from scripts.ci.xdist_contract import make_safe_scheduler

    config = _make_config(pytester)
    scheduler = make_safe_scheduler(config)
    _schedule_with_a_still_collecting_replacement(scheduler)

    replacement = next(node for node in scheduler.assigned_work if node.gateway.id == "gw1")
    # What DSession.worker_collectionfinish does when a node finishes
    # collecting: register the collection, then schedule across every node.
    scheduler.add_node_collection(
        replacement,
        [
            "tests/test_alpha.py::test_one",
            "tests/test_alpha.py::test_two",
            "tests/test_beta.py::test_three",
        ],
    )
    scheduler.schedule()

    assert replacement.sent, "the replacement was never given the re-queued work"
    assigned = [nodeid for unit in scheduler.assigned_work[replacement].values() for nodeid in unit]
    assert "tests/test_alpha.py::test_one" in assigned


def test_a_scheduler_override_is_not_imposed_on_other_distribution_modes(pytester):
    from scripts.ci.xdist_contract import make_safe_scheduler

    assert make_safe_scheduler(_make_config(pytester, dist="load")) is None
    assert make_safe_scheduler(_make_config(pytester, dist="each")) is None


# ---------------------------------------------------------------------------
# Clause: no silent loss of work. Every collected test produces a report, or
# the run says which ones did not and fails.
# ---------------------------------------------------------------------------


def test_work_accounting_is_silent_when_every_test_reported():
    from scripts.ci.xdist_contract import WorkAccounting

    accounting = WorkAccounting()
    accounting.expected = {"a::t1", "a::t2"}
    accounting.reported = {"a::t1", "a::t2"}

    assert accounting.unreported == []


def test_work_accounting_names_every_test_that_never_reported():
    from scripts.ci.xdist_contract import WorkAccounting

    accounting = WorkAccounting()
    accounting.expected = {"a::t1", "a::t2", "b::t3"}
    accounting.reported = {"a::t1"}

    assert accounting.unreported == ["a::t2", "b::t3"]


def test_a_worker_that_dies_mid_file_cannot_end_the_run_green(pytester):
    """The whole clause, through a real parallel run.

    One test kills its own worker outright, the way pytest-timeout's thread
    method does when a test overruns on a slow runner. The tests that
    follow it in the same file are the work that worker owned. The run must
    not end reporting only what happened to survive.
    """
    pytester.makeconftest(
        f"""
        import sys
        sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})

        def pytest_xdist_make_scheduler(config, log):
            from scripts.ci.xdist_contract import make_safe_scheduler
            return make_safe_scheduler(config, log)

        def pytest_configure(config):
            from scripts.ci import xdist_contract
            xdist_contract.install(config)
        """,
    )
    pytester.makepyfile(
        test_victim="""
        import os

        def test_kills_its_own_worker():
            # Exactly what pytest-timeout's thread method does on overrun.
            os._exit(1)

        def test_never_runs_a(): pass
        def test_never_runs_b(): pass
        """,
        test_bystander="""
        def test_unaffected(): pass
        """,
    )

    result = pytester.runpytest_subprocess(
        "-n",
        "2",
        "--dist",
        "loadfile",
        "-p",
        "no:cacheprovider",
    )

    assert result.ret != 0, "a run that lost work reported success"
    result.stdout.fnmatch_lines(["*work accounting: TESTS LOST*"])
    result.stdout.fnmatch_lines(["*test_never_runs_*"])


def test_an_intentional_early_stop_is_not_reported_as_lost_work(pytester):
    """`-x` leaves tests unrun by design; saying so would bury the real reason."""
    pytester.makeconftest(
        f"""
        import sys
        sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})

        def pytest_configure(config):
            from scripts.ci import xdist_contract
            xdist_contract.install(config)
        """,
    )
    pytester.makepyfile(
        test_stops="""
        def test_fails(): assert False
        def test_would_have_run(): pass
        """,
    )

    result = pytester.runpytest_subprocess("-x", "-p", "no:cacheprovider")

    assert result.ret != 0
    assert "work accounting: TESTS LOST" not in result.stdout.str()


# ---------------------------------------------------------------------------
# Clause: diagnostics. A stall must be attributable without inference from a
# CI cancellation, and the trace must distinguish slow work from slow
# transport.
# ---------------------------------------------------------------------------


def _trace_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _run_traced(pytester: pytest.Pytester, trace_dir: Path, *args: str):
    pytester.makeconftest(
        f"""
        import sys
        sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})

        def pytest_xdist_make_scheduler(config, log):
            from scripts.ci.xdist_contract import make_safe_scheduler
            return make_safe_scheduler(config, log)

        def pytest_configure(config):
            from scripts.ci import execution_trace, xdist_contract
            xdist_contract.install(config)
            execution_trace.install(config)
        """,
    )
    return pytester.runpytest_subprocess("-p", "no:cacheprovider", *args)


def test_the_trace_brackets_every_report_at_both_ends(pytester, tmp_path, monkeypatch):
    """The measurement the controller log could never make.

    A worker's `phase` event is written before the report is sent; the
    controller's `report_received` when it arrives. With both, a twenty-
    minute gap is attributable to the test or to the transport, which is
    the question every previous diagnosis had to leave open.
    """
    trace_dir = tmp_path / "trace"
    monkeypatch.setenv("BARTHO_EXEC_TRACE", "1")
    monkeypatch.setenv("BARTHO_EXEC_TRACE_DIR", str(trace_dir))
    pytester.makepyfile(test_traced="def test_one(): pass\n")

    result = _run_traced(pytester, trace_dir, "-n", "1", "--dist", "loadfile")
    assert result.ret == 0

    controller = _trace_events(trace_dir / "controller.jsonl")
    worker = _trace_events(trace_dir / "gw0.jsonl")

    assert [e["event"] for e in controller][0] == "session_start"
    assert {"node_created", "node_ready", "node_collected"} <= {e["event"] for e in controller}

    call_phase = next(e for e in worker if e["event"] == "phase" and e["when"] == "call")
    arrival = next(
        e
        for e in controller
        if e["event"] == "report_received"
        and e["when"] == "call"
        and e["nodeid"].endswith("test_one")
    )
    assert call_phase["t"] <= arrival["t"], "the worker recorded a report after it arrived"
    assert "sqlite_connections" in call_phase


def test_a_lost_worker_is_recorded_as_lost_and_not_as_a_slow_test(pytester, tmp_path, monkeypatch):
    trace_dir = tmp_path / "trace"
    monkeypatch.setenv("BARTHO_EXEC_TRACE", "1")
    monkeypatch.setenv("BARTHO_EXEC_TRACE_DIR", str(trace_dir))
    pytester.makepyfile(
        test_dies="""
        import os
        def test_dies(): os._exit(1)
        """,
        test_ok="def test_ok(): pass\n",
    )

    _run_traced(pytester, trace_dir, "-n", "2", "--dist", "loadfile")

    controller = _trace_events(trace_dir / "controller.jsonl")
    downs = [e for e in controller if e["event"] == "node_down" and e["crashed"]]
    assert downs, "a worker that stopped existing left no node_down record"
    assert downs[0]["error"], "a lost worker was recorded without its error text"
    # A replacement is created, and that is visible too.
    created = [e["gateway"] for e in controller if e["event"] == "node_created"]
    assert len(created) > 2, "no replacement worker was created for the lost one"


def test_the_controller_ends_a_stalled_run_itself_rather_than_waiting_for_the_cap(
    pytester,
    tmp_path,
    monkeypatch,
):
    """Gate 11: a stall must produce its own attributable ending.

    The watchdog's abort bound is set to a few seconds here and the test
    body sleeps past it. The run must end with the contract's banner and
    an `abort` event naming the outstanding work -- not with a bare
    cancellation that says nothing.
    """
    trace_dir = tmp_path / "trace"
    monkeypatch.setenv("BARTHO_EXEC_TRACE", "1")
    monkeypatch.setenv("BARTHO_EXEC_TRACE_DIR", str(trace_dir))
    pytester.makeconftest(
        f"""
        import sys
        sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})

        def pytest_xdist_make_scheduler(config, log):
            from scripts.ci.xdist_contract import make_safe_scheduler
            return make_safe_scheduler(config, log)

        def pytest_configure(config):
            from scripts.ci import execution_trace, xdist_contract
            xdist_contract.install(config)
            execution_trace.install(config)
        """,
    )
    pytester.makepyfile(
        test_stalls="""
        import time
        def test_stalls(): time.sleep(120)
        """,
    )

    monkeypatch.setenv("BARTHO_EXEC_STALL_WARN_S", "3")
    monkeypatch.setenv("BARTHO_EXEC_STALL_ABORT_S", "10")
    # The point of the exercise is a stall the per-test timeout does not end,
    # which is what the Windows tail has always been.
    monkeypatch.setenv("PYTEST_TIMEOUT", "0")

    result = pytester.runpytest_subprocess(
        "-n",
        "1",
        "--dist",
        "loadfile",
        "-p",
        "no:cacheprovider",
    )

    assert result.ret == 3, f"expected the watchdog's own exit code, got {result.ret}"
    assert "EXECUTION CONTRACT VIOLATED" in result.stderr.str()

    controller = _trace_events(trace_dir / "controller.jsonl")
    aborts = [e for e in controller if e["event"] == "abort"]
    assert aborts, "the run ended without recording why"
    assert aborts[0]["idle_s"] >= 10
    assert aborts[0]["outstanding"], "the abort record does not name the outstanding work"
    # The pair that tells a blocked controller loop from a lost wakeup, which
    # need opposite corrections. Both must be present for the abort record to
    # be worth anything.
    assert isinstance(aborts[0].get("event_queue_depth"), int)
    assert isinstance(aborts[0].get("shutting_down"), dict)

    stalls = [e for e in controller if e["event"] == "stall"]
    assert stalls, "no stall was reported before the abort"

    # The controller's own threads are part of the evidence: a report that
    # left a worker and never arrived is sitting in an execnet receiver
    # thread, and only a stack says which one.
    controller_stacks = trace_dir / "controller.stacks.txt"
    assert controller_stacks.exists(), "the controller stalled without dumping its own threads"
    assert "stalled" in controller_stacks.read_text(encoding="utf-8")


def test_the_trace_is_immune_to_a_test_that_patches_the_clock(pytester, tmp_path, monkeypatch):
    """W9 has to be trustworthy, and this suite patches time in two places.

    `freezegun` and `tests/unit/kernel/test_time_utils.py` replace
    `time.time` and `time.perf_counter` on the module. A trace that reads
    them records a phase of 1.7 billion seconds and a transport delay to
    match, which would make the one measurement this module exists for
    worthless. The clocks are bound at import instead.
    """
    trace_dir = tmp_path / "trace"
    monkeypatch.setenv("BARTHO_EXEC_TRACE", "1")
    monkeypatch.setenv("BARTHO_EXEC_TRACE_DIR", str(trace_dir))
    pytester.makepyfile(
        test_patches_the_clock="""
        import time

        def test_moves_the_clock(monkeypatch):
            monkeypatch.setattr(time, "time", lambda: 1.0)
            monkeypatch.setattr(time, "perf_counter", lambda: 1.0)
            assert time.time() == 1.0
        """,
    )

    result = _run_traced(pytester, trace_dir, "-n", "1", "--dist", "loadfile")
    assert result.ret == 0

    worker = _trace_events(trace_dir / "gw0.jsonl")
    controller = _trace_events(trace_dir / "controller.jsonl")
    call_phase = next(e for e in worker if e["event"] == "phase" and e["when"] == "call")
    arrival = next(e for e in controller if e["event"] == "report_received" and e["when"] == "call")

    assert 0 <= call_phase["wall_s"] < 60, f"phase duration read a patched clock: {call_phase}"
    assert 0 <= arrival["t"] - call_phase["t"] < 60, "transport delay read a patched clock"


def test_a_worker_wedged_in_a_c_call_still_produces_a_stack(pytester, tmp_path, monkeypatch):
    """The one stall no Python-level instrument can see.

    A thread inside a C call that never releases the GIL stops every other
    Python thread in the process: pytest-timeout's `threading.Timer` never
    fires, and neither does this module's own watchdog. The process looks
    frozen and nothing says why -- which is a candidate explanation for a
    Windows tail the 120 s per-test timeout demonstrably fails to end.

    faulthandler's own timer runs on a thread it owns in C and writes
    without the GIL, so it is the only instrument that survives. The test
    holds the GIL for longer than the stall bound and asserts a stack was
    written anyway.
    """
    trace_dir = tmp_path / "trace"
    monkeypatch.setenv("BARTHO_EXEC_TRACE", "1")
    monkeypatch.setenv("BARTHO_EXEC_TRACE_DIR", str(trace_dir))
    monkeypatch.setenv("BARTHO_EXEC_STALL_WARN_S", "3")
    monkeypatch.setenv("BARTHO_EXEC_STALL_ABORT_S", "0")
    monkeypatch.setenv("PYTEST_TIMEOUT", "0")
    pytester.makeconftest(
        f"""
        import sys
        sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})

        def pytest_configure(config):
            from scripts.ci import execution_trace
            execution_trace.install(config)
        """,
    )
    pytester.makepyfile(
        test_holds_the_gil="""
        import sys
        import time

        def test_holds_the_gil():
            # sys.setswitchinterval keeps the interpreter from handing the
            # GIL to another Python thread for the duration of this loop,
            # which is the observable behaviour of a C call that does not
            # release it. time.monotonic() does not release it either, so
            # the loop can bound itself on the wall clock and still starve
            # every other Python thread.
            #
            # A wall-clock bound, not an iteration count: an earlier version
            # spun a fixed number of times, passed on a slow machine and
            # failed on a fast one -- precisely the timing dependence this
            # package exists to stop shipping.
            sys.setswitchinterval(1000)
            try:
                deadline = time.monotonic() + 8.0
                while time.monotonic() < deadline:
                    pass
            finally:
                sys.setswitchinterval(0.005)
        """,
    )

    pytester.runpytest_subprocess("-n", "1", "--dist", "loadfile", "-p", "no:cacheprovider")

    gil_dump = trace_dir / "gw0.gil.txt"
    assert gil_dump.exists(), "a worker that held the GIL produced no stack at all"
    dumped = gil_dump.read_text(encoding="utf-8")
    assert dumped.strip(), "the GIL-free dump file was created but never written to"
    assert "test_holds_the_gil" in dumped


@pytest.mark.skipif(sys.platform == "win32", reason="signal-free stack dump path differs")
def test_a_stalled_worker_writes_its_own_stacks(pytester, tmp_path, monkeypatch):
    """Requirement 11: tell a product deadlock from slow work.

    The stacks of every thread in the stalled worker are what separates
    "blocked on a lock" from "computing". They are written by the worker
    itself, so they survive the controller ending the run.
    """
    trace_dir = tmp_path / "trace"
    pytester.makeconftest(
        f"""
        import sys
        sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})

        def pytest_configure(config):
            from scripts.ci import execution_trace
            execution_trace.install(config)
        """,
    )
    pytester.makepyfile(
        test_slow="""
        import time
        def test_slow(): time.sleep(8)
        """,
    )

    monkeypatch.setenv("BARTHO_EXEC_TRACE", "1")
    monkeypatch.setenv("BARTHO_EXEC_TRACE_DIR", str(trace_dir))
    monkeypatch.setenv("BARTHO_EXEC_STALL_WARN_S", "3")
    monkeypatch.setenv("BARTHO_EXEC_STALL_ABORT_S", "0")
    monkeypatch.setenv("PYTEST_TIMEOUT", "0")

    pytester.runpytest_subprocess("-n", "1", "--dist", "loadfile", "-p", "no:cacheprovider")

    stacks = trace_dir / "gw0.stacks.txt"
    assert stacks.exists(), "a stalled worker produced no stack dump"
    text = stacks.read_text(encoding="utf-8")
    assert "stalled" in text
    assert "test_slow" in text

    worker = _trace_events(trace_dir / "gw0.jsonl")
    stall_events = [e for e in worker if e["event"] == "stall"]
    assert stall_events
    assert stall_events[0]["nodeid"].endswith("test_slow")


# ---------------------------------------------------------------------------
# Clause W13: the controller must not stop asking its own scheduler for work.
# ---------------------------------------------------------------------------


class _FakeQueue:
    """Just enough of `queue.Queue` for the re-drive path."""

    def __init__(self) -> None:
        self.items: list = []

    def put(self, item) -> None:
        self.items.append(item)

    def qsize(self) -> int:
        return len(self.items)


class _FakeDSession:
    def __init__(self, sched) -> None:
        self.sched = sched
        self.queue = _FakeQueue()
        self.shuttingdown = False


def _deadlocked_scheduler(pytester):
    """The exact state Merge Candidate 35090997379 recorded.

    A node that is alive, not shutting down, and holding one unfinished
    item; work still on the queue; and nothing left to trigger
    ``mark_test_complete``. Stock pytest-xdist leaves this untouched
    forever, because the only thing that calls ``_reschedule`` is an event
    that is never coming.
    """
    from scripts.ci.xdist_contract import make_safe_scheduler

    config = _make_config(pytester)
    sched = make_safe_scheduler(config)

    node = _FakeNode("gw0")
    sched.add_node(node)
    sched.add_node_collection(
        node,
        [
            "tests/test_alpha.py::test_one",
            "tests/test_alpha.py::test_two",
            "tests/test_beta.py::test_three",
            "tests/test_gamma.py::test_four",
        ],
    )
    sched.schedule()
    node.sent.clear()

    # Everything the node was given except its final item is done. This is
    # the moment a real worker blocks in TestQueue.get(): pending is 1, the
    # queue still holds units, and no further completion event will arrive.
    for unit in sched.assigned_work[node].values():
        nodeids = list(unit)
        for nodeid in nodeids[:-1]:
            unit[nodeid] = True
    return sched, node


def test_the_deadlock_is_real_and_nothing_in_xdist_breaks_it(pytester):
    """The defect, pinned as a defect.

    Work queued, a node able to take it, and no event to trigger the
    assignment. Nothing moves. If pytest-xdist ever grows a heartbeat that
    re-examines the schedule, this test fails and clause W13 can be retired.
    """
    sched, node = _deadlocked_scheduler(pytester)

    assert len(sched.workqueue) >= 1, "the queue should still hold work"
    assert not node.shutting_down
    assert not node.sent, "nothing assigns work without an event to prompt it"


def test_the_redrive_assigns_queued_work_to_an_idle_node(pytester):
    """The correction: one call the controller failed to make."""
    from scripts.ci.xdist_contract import SchedulerRedrive

    sched, node = _deadlocked_scheduler(pytester)
    redrive = SchedulerRedrive(idle_after_s=0.0)
    dsession = _FakeDSession(sched)
    redrive._dsession = lambda: dsession  # type: ignore[method-assign]

    redrive._handle_redrive()

    assert node.sent, "the re-drive did not hand the idle node its queued work"


def test_the_redrive_fires_only_when_work_is_queued_and_a_node_can_take_it(pytester):
    """It must not nudge a healthy run, or one that is ending."""
    from scripts.ci.xdist_contract import SchedulerRedrive

    sched, node = _deadlocked_scheduler(pytester)
    redrive = SchedulerRedrive(idle_after_s=0.0)
    dsession = _FakeDSession(sched)
    redrive._dsession = lambda: dsession  # type: ignore[method-assign]

    ok, _ = redrive._should_redrive()
    assert ok, "the deadlocked state should qualify"

    # A controller with events pending is busy, not stalled.
    dsession.queue.put(("something", {}))
    assert not redrive._should_redrive()[0]
    dsession.queue.items.clear()

    # A run that is shutting down is not stalled either.
    dsession.shuttingdown = True
    assert not redrive._should_redrive()[0]
    dsession.shuttingdown = False

    # Neither is a node that has been told to shut down.
    node.shutting_down = True
    assert not redrive._should_redrive()[0]
    node.shutting_down = False

    # Nor an empty queue: nothing to hand out is a different problem, and
    # the execution trace's abort owns it.
    sched.workqueue.clear()
    assert not redrive._should_redrive()[0]


def test_a_slow_test_is_not_a_stalled_scheduler(pytester):
    """A node still deep in its work unit is busy, not stalled.

    Eligibility asks the question ``_reschedule`` asks before it tops a
    node up. Without that, any long test anywhere in the run looked like a
    stall: Merge Candidate 35185611629 reported 64 re-drives where earlier
    runs of the same correction reported 7, because every eight-second
    stretch with a slow test running counted as one. A count that inflates
    with test duration cannot be used to tell a real defect from a slow
    suite, which is the only thing the count is for.
    """
    from scripts.ci.xdist_contract import SchedulerRedrive

    sched, node = _deadlocked_scheduler(pytester)
    redrive = SchedulerRedrive(idle_after_s=0.0)
    dsession = _FakeDSession(sched)
    redrive._dsession = lambda: dsession  # type: ignore[method-assign]

    assert redrive._should_redrive()[0], "one unfinished item is the deadlock"

    workload = sched.assigned_work[node]
    workload["tests/test_busy.py"] = {
        "tests/test_busy.py::test_a": False,
        "tests/test_busy.py::test_b": False,
        "tests/test_busy.py::test_c": False,
    }
    assert sched._pending_of(workload) > 2, "the node now has work left to do"
    assert not redrive._should_redrive()[0], "a busy node is not a stalled one"

    # And it becomes eligible again the moment it is nearly depleted, which
    # is the state the deadlock strands it in.
    del workload["tests/test_busy.py"]
    assert redrive._should_redrive()[0], "a nearly depleted node still qualifies"


def test_only_a_finished_test_counts_as_progress():
    """The defect that stopped the 600-second abort from ever firing.

    In Merge Candidate 35090997379 a replacement worker's startup chatter
    kept resetting the stall clock, so a run that had been deadlocked for
    half an hour never reached its own bound and was cancelled at the job
    cap instead. Progress has to mean a test finished.
    """
    from scripts.ci.xdist_contract import SchedulerRedrive

    redrive = SchedulerRedrive(idle_after_s=60.0)
    before_stamp = redrive._last_progress
    before_count = redrive._completions

    class _Report:
        when = "call"
        passed = True
        nodeid = "a::t1"

    redrive.pytest_runtest_logreport(_Report())  # type: ignore[arg-type]
    assert redrive._completions == before_count, "a passing call report is not a completion"
    assert redrive._last_progress == before_stamp

    class _Teardown:
        when = "teardown"
        passed = True
        nodeid = "a::t1"

    redrive.pytest_runtest_logreport(_Teardown())  # type: ignore[arg-type]
    # The completion counter, not the timestamp. Windows resolves
    # time.monotonic() to about 15.6 ms, so two calls inside one tick return
    # the same value and a strict `>` fails -- which is exactly how this
    # assertion failed on the Windows runner, and exactly the defect
    # RISKS.md already records against
    # test_the_scheduler_loop_beats_even_when_no_drive_is_due
    # (`assert 701.25 > 701.25`). The counter is resolution-independent and
    # is what the watcher actually means by progress.
    assert redrive._completions == before_count + 1, "a finished test is progress"
    assert redrive._last_progress >= before_stamp


def test_a_run_that_needed_redriving_says_so(pytester):
    """A re-driven run completed over a defect, and must not look green."""
    from scripts.ci.xdist_contract import SchedulerRedrive

    redrive = SchedulerRedrive(idle_after_s=0.0)
    redrive.redrives = 3
    redrive.redrive_log = ["re-drive #1: ...", "re-drive #2: ...", "re-drive #3: ..."]

    written: list[str] = []

    class _Reporter:
        def write_sep(self, sep, title, **kwargs):
            written.append(title)

        def write_line(self, line, **kwargs):
            written.append(line)

    redrive.pytest_terminal_summary(_Reporter())
    blob = "\n".join(written)
    assert "re-driven 3 time(s)" in blob
    assert "pytest-xdist defect" in blob

    quiet = SchedulerRedrive(idle_after_s=0.0)
    silent: list[str] = []

    class _Quiet:
        def write_sep(self, sep, title, **kwargs):
            silent.append(title)

        def write_line(self, line, **kwargs):
            silent.append(line)

    quiet.pytest_terminal_summary(_Quiet())
    assert not silent, "a run that never stalled must say nothing"


def test_the_redrive_reaches_the_controller_through_a_real_xdist_run(pytester, monkeypatch):
    """The plumbing, end to end, in a real controller.

    The tests above prove the decision and the assignment against fakes.
    This one proves the risky part: that the event posted from a watcher
    thread is dispatched by `DSession.loop_once` on the controller's main
    thread, that the handler bound with `setattr` is found and called, and
    that `_reschedule` from inside that dispatch does not disturb a healthy
    run.

    The bound is set to a second so the re-drive fires while one slow test
    holds the only worker and four units sit queued. A re-drive that was
    not needed is harmless by construction -- `_reschedule` returns on the
    node's own pending count -- so the run must still finish green, and
    must still say that it was re-driven.
    """
    monkeypatch.setenv("BARTHO_XDIST_REDRIVE_IDLE_S", "1")
    pytester.makeconftest(
        f"""
        import sys
        sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})

        def pytest_xdist_make_scheduler(config, log):
            from scripts.ci.xdist_contract import make_safe_scheduler
            return make_safe_scheduler(config, log)

        def pytest_configure(config):
            from scripts.ci import xdist_contract
            xdist_contract.install(config)
        """,
    )
    pytester.makepyfile(
        test_slow_one="import time\ndef test_slow(): time.sleep(9)\n",
        test_two="def test_a(): pass\n",
        test_three="def test_b(): pass\n",
        test_four="def test_c(): pass\n",
        test_five="def test_d(): pass\n",
    )

    result = pytester.runpytest_subprocess(
        "-n",
        "1",
        "--dist",
        "loadfile",
        "-p",
        "no:cacheprovider",
        "-p",
        "no:randomly",
    )

    assert result.ret == 0, "a re-drive disturbed an otherwise healthy run"
    result.assert_outcomes(passed=5)
    combined = result.stdout.str() + result.stderr.str()
    assert "scheduler re-drive #1" in combined, "the re-drive never reached the controller"
    assert "re-driven" in combined, "a re-driven run did not report that it was"


def test_the_trace_times_the_write_path_and_not_only_the_open(tmp_path):
    """Connection opens were measured at 0.4 ms and acquitted as the cause.

    That left the storage cost of the heaviest tests unexplained rather
    than explained, so the trace now times the commit and the close too.
    A measurement that silently records zero is worse than none.
    """
    from scripts.ci.execution_trace import _SqliteCounter

    counter = _SqliteCounter()
    original = sqlite3.connect
    counter.install()
    try:
        conn = sqlite3.connect(tmp_path / "written.db")
        conn.execute("create table t (a)")
        conn.execute("insert into t values (1)")
        conn.commit()
        conn.close()
    finally:
        sqlite3.connect = original

    opens, _ = counter.snapshot()
    commits, commit_s, close_s = counter.write_snapshot()
    assert opens == 1
    assert commits == 1, "the commit was counted"
    assert commit_s > 0.0, "the commit was timed"
    assert close_s > 0.0, "the close was timed"


def test_timing_the_write_path_keeps_a_caller_supplied_connection_class(tmp_path):
    """The timing is installed by wrapping the connection factory.

    A caller that supplies its own Connection subclass must still get it,
    or the instrument changes the behaviour it is there to observe.
    """
    from scripts.ci.execution_trace import _SqliteCounter

    class Mine(sqlite3.Connection):
        def marker(self) -> str:
            return "mine"

    counter = _SqliteCounter()
    original = sqlite3.connect
    counter.install()
    try:
        conn = sqlite3.connect(tmp_path / "factory.db", factory=Mine)
        assert isinstance(conn, Mine), "the caller's class survived"
        assert conn.marker() == "mine"
        conn.commit()
        conn.close()
    finally:
        sqlite3.connect = original

    assert counter.write_snapshot()[0] == 1


def test_a_requeued_test_does_not_invent_a_transport_delay(tmp_path, capsys):
    """A lost worker's work is requeued, so one test is reported twice.

    Matching the controller's receive to a worker's emit by test alone let
    the second worker's receive be subtracted from the first worker's emit.
    That invented worker-to-controller delays of 173 s and 585 s in runs
    35185611629 and 35189705193 and pointed at the harness, when the real
    defect was the test exceeding its per-test timeout. A diagnostic that
    manufactures the symptom it is meant to detect is worse than none.
    """
    from scripts.ci.summarise_trace import summarise

    def line(**fields):
        return json.dumps(fields)

    # gw0 emits setup at t=10 and is then lost; gw1 re-runs the same test
    # far later, emitting at t=600 and reported at t=600.1.
    (tmp_path / "gw0.jsonl").write_text(
        line(event="phase", t=10.0, nodeid="t.py::a", when="setup", outcome="passed", wall_s=0.2)
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "gw1.jsonl").write_text(
        line(event="phase", t=600.0, nodeid="t.py::a", when="setup", outcome="passed", wall_s=0.2)
        + "\n",
        encoding="utf-8",
    )

    def received(at, worker):
        return line(event="report_received", t=at, nodeid="t.py::a", when="setup", worker=worker)

    (tmp_path / "controller.jsonl").write_text(
        "\n".join([received(10.1, "gw0"), received(600.1, "gw1")]) + "\n",
        encoding="utf-8",
    )

    summarise(tmp_path)
    printed = capsys.readouterr().out

    assert "largest worker-to-controller delay" in printed
    delay_line = next(ln for ln in printed.splitlines() if "largest worker-to-controller" in ln)
    assert "0.1s" in delay_line, f"each report matched its own worker: {delay_line}"
    assert "590" not in delay_line, "no delay was invented across the two workers"


def test_a_worker_that_has_not_collected_yet_is_not_a_stall(pytester):
    """Startup is not a stall, and must not be counted as one.

    A node enters ``assigned_work`` when it reports ready and
    ``registered_collections`` only when it has finished collecting. In
    between it holds no work, so a pending-count test alone calls it idle.
    On Windows that window -- collection plus worker spin-up -- is longer
    than the idle bound, so every run began by reporting re-drives that
    `_reschedule` then skipped anyway. Run 35185611629's first twenty were
    exactly this, and they made the count useless as evidence.
    """
    from scripts.ci.xdist_contract import SchedulerRedrive

    sched, node = _deadlocked_scheduler(pytester)
    redrive = SchedulerRedrive(idle_after_s=0.0)
    dsession = _FakeDSession(sched)
    redrive._dsession = lambda: dsession  # type: ignore[method-assign]

    assert redrive._should_redrive()[0], "a collected, depleted node is the deadlock"

    collection = sched.registered_collections.pop(node)
    assert not redrive._should_redrive()[0], "a still-collecting node is not a stall"

    sched.registered_collections[node] = collection
    assert redrive._should_redrive()[0], "and it counts again once it has collected"


def test_the_redrive_says_so_when_it_stops_trying(pytester, capsys):
    """Past its bound the re-drive goes quiet; it must not go silent.

    The bound exists because past it the run is failing for a reason
    re-driving cannot fix, and the execution trace's abort is meant to own
    the ending. But that trace is opt-in, so on a run without it nothing
    else would ever speak, and a stranded run that prints nothing is the
    precise failure this package exists to remove.
    """
    from scripts.ci.xdist_contract import SchedulerRedrive

    sched, _node = _deadlocked_scheduler(pytester)
    redrive = SchedulerRedrive(idle_after_s=0.0)
    dsession = _FakeDSession(sched)
    redrive._dsession = lambda: dsession  # type: ignore[method-assign]
    redrive.redrives = redrive.MAX_REDRIVES

    redrive._announce_give_up()
    first = capsys.readouterr().err
    assert "giving up" in first
    assert str(redrive.MAX_REDRIVES) in first
    assert "BARTHO_EXEC_TRACE" in first, "it names the way to get the stacks"

    redrive._announce_give_up()
    assert capsys.readouterr().err == "", "said once, not once per poll"


# ---------------------------------------------------------------------------
# Clause W13: a re-drive is counted by the controller, not by the watcher.
#
# The watcher reads the scheduler from its own thread while the controller's
# main thread may be half-way through changing it. Integration run
# 36519235007 (2026-09-29) and Merge Candidate 37703362526 (2026-10-07, gate 8
# Run 1/3) each failed W13 on a re-drive counted from a snapshot taken inside
# xdist's initial schedule() -- "251" and "252 unit(s) queued and a node able
# to take them" -- on runs with nothing else wrong. What the watcher sees is a
# proposal; only what the controller does with it on its own thread, with the
# schedule consistent, is a re-drive.
#
# Note on the fixtures above: `_deadlocked_scheduler` leaves its node holding
# two pending tests, which a real worker runs as "one running, the next
# fetched" -- busy, not stranded. The tests below that need a genuinely
# stranded worker (one pending test, no next item to fetch) build one.
# ---------------------------------------------------------------------------


def _real_shape_collection() -> list[str]:
    """5742 nodeids in 253 files, the four largest holding 403, 140, 136 and
    121 tests: the shape of the default suite on `54c851b`, so the start-up
    windows below queue exactly 252, 251 and 250 units, as the two failed
    runs reported."""
    sizes = [403, 140, 136, 121] + [20] * 247 + [1, 1]
    return [
        f"tests/test_f{index:03d}.py::test_{test}"
        for index, size in enumerate(sizes)
        for test in range(size)
    ]


#: Units of seven to one tests: once one node has the first, it is busy.
_SMALL_UNITS = {"a": 7, "b": 6, "c": 5, "d": 4, "e": 3, "f": 2, "g": 1}


def _small_collection() -> list[str]:
    return [
        f"tests/test_{name}.py::test_{index}"
        for name, size in _SMALL_UNITS.items()
        for index in range(size)
    ]


class _ProbingNode(_FakeNode):
    """A node that runs ``probe`` once, as its first unit is sent.

    ``send_runtest_some`` is the last statement of ``_assign_work_unit``, so
    the probe sees the scheduler exactly where a watcher poll landing in the
    middle of ``schedule()`` would: this node's unit popped and recorded, the
    nodes after it still holding nothing.
    """

    def __init__(self, ident: str) -> None:
        super().__init__(ident)
        self.probe = None

    def send_runtest_some(self, indices: list[int]) -> None:
        super().send_runtest_some(indices)
        probe, self.probe = self.probe, None
        if probe is not None:
            probe()


def _redrive_on(sched):
    from scripts.ci.xdist_contract import SchedulerRedrive

    redrive = SchedulerRedrive(idle_after_s=0.0)
    dsession = _FakeDSession(sched)
    redrive._dsession = lambda: dsession  # type: ignore[method-assign]
    return redrive, dsession


def _watcher_poll(redrive, idle: float) -> tuple[bool, str]:
    """One poll of the watcher, as ``_watch`` makes it."""
    completions = redrive._completions
    ok, why = redrive._should_redrive()
    if ok:
        redrive._post_redrive(idle, why, completions)
    return ok, why


def _dispatch(dsession) -> None:
    """Deliver queued events the way ``DSession.loop_once`` does."""
    while dsession.queue.items:
        name, kwargs = dsession.queue.items.pop(0)
        getattr(dsession, f"worker_{name}")(**kwargs)


def _report_of(redrive, tmp_path, monkeypatch) -> tuple[dict, bool, str]:
    """The report the controller writes at session end, and the W13 verdict."""
    from scripts.ci.xdist_contract import check_contract_report

    report = tmp_path / "xdist-contract.json"
    monkeypatch.setenv("BARTHO_XDIST_CONTRACT_REPORT", str(report))
    redrive.pytest_sessionfinish(None, 0)  # type: ignore[arg-type]
    ok, message = check_contract_report(report)
    return json.loads(report.read_text(encoding="utf-8")), ok, message


def _stranded_scheduler(pytester):
    """A worker genuinely stranded: one pending test, work queued, no event coming.

    The node has completed everything it was given but one test. A real
    worker in that position holds that test and blocks fetching the item after
    it, which only the controller can send.
    """
    sched, node = _deadlocked_scheduler(pytester)
    sched.assigned_work[node]["tests/test_alpha.py"]["tests/test_alpha.py::test_two"] = True
    assert sched._pending_of(sched.assigned_work[node]) == 1 and sched.workqueue
    return sched, node


@pytest.mark.parametrize(("assigned_so_far", "queued"), [(1, 252), (2, 251), (3, 250)])
def test_a_snapshot_taken_inside_the_initial_distribution_is_not_counted(
    pytester,
    tmp_path,
    monkeypatch,
    assigned_so_far,
    queued,
):
    """Gate 8 Run 1/3's false positive (252) and Integration #227's (251).

    Inside ``schedule()``, once ``assigned_so_far`` of the four nodes have
    their first unit, the others are registered and hold nothing, so the
    watcher's snapshot reads "work queued and a node able to take it". The
    poll is taken there for real and does propose. Once ``schedule()``
    returns every node holds more than two tests and none can be given work,
    so the controller declines: not counted, not a W13 failure, and on the
    record as declined.
    """
    from scripts.ci.xdist_contract import make_safe_scheduler

    sched = make_safe_scheduler(pytester.parseconfigure("--tx", "4*popen", "--dist", "loadfile"))
    redrive, dsession = _redrive_on(sched)
    seen: dict = {}

    def poll() -> None:
        seen["ok"], seen["why"] = _watcher_poll(redrive, idle=18.0)

    nodes = [_ProbingNode(f"gw{index}") for index in range(4)]
    nodes[assigned_so_far - 1].probe = poll
    collection = _real_shape_collection()
    for node in nodes:
        sched.add_node(node)
    for node in nodes:
        sched.add_node_collection(node, collection)
    sched.schedule()

    # The torn state was really reached, and the watcher really proposed.
    assert seen.get("ok"), f"the poll never saw the mid-schedule state: {seen}"
    assert seen["why"] == f"{queued} unit(s) queued and a node able to take them"
    assert redrive.proposed == 1
    assert redrive.redrives == 0, "the watcher's proposal was counted as a re-drive"

    _dispatch(dsession)

    assert redrive.redrives == 0, "the controller counted a state it was not in"
    assert redrive.declined == 1, "the proposal vanished instead of being recorded"
    assert f"saw {queued} unit(s) queued" in redrive.declined_log[0]
    assert [len(node.sent) for node in nodes] == [1, 1, 1, 1], "a declined proposal assigned work"
    assert len(sched.workqueue) == 249

    report, ok, message = _report_of(redrive, tmp_path, monkeypatch)
    assert ok, message
    assert "declined" in message
    assert (report["redrives"], report["declined"], report["undispatched"]) == (0, 1, 0)


def test_a_snapshot_taken_while_a_replacement_registers_is_not_counted(pytester):
    """Not a start-up special case: the same torn read, after a worker loss.

    A replacement that has registered its collection holds nothing until
    ``schedule()``'s second call gives it work, and the dead worker's work is
    queued. A poll landing between the two -- inside the controller's
    ``worker_collectionfinish`` handler -- sees an idle, able node. By the
    time the controller looks, the replacement has its work.
    """
    from scripts.ci.xdist_contract import make_safe_scheduler

    sched = make_safe_scheduler(_make_config(pytester))
    redrive, dsession = _redrive_on(sched)
    collection = _small_collection()
    lost = _FakeNode("gw0")
    sched.add_node(lost)
    sched.add_node_collection(lost, collection)
    sched.schedule()
    assert sched.remove_node(lost) is not None, "the lost worker had work in flight"

    replacement = _FakeNode("gw1")
    sched.add_node(replacement)
    sched.add_node_collection(replacement, collection)
    ok, why = _watcher_poll(redrive, idle=12.0)  # lands inside worker_collectionfinish
    assert ok, why
    sched.schedule()  # what worker_collectionfinish does next
    assert replacement.sent, "the replacement was given its work"

    _dispatch(dsession)

    assert redrive.redrives == 0
    assert redrive.declined == 1
    assert len(replacement.sent) == 1, "a declined proposal assigned work"


def test_a_proposal_made_before_a_test_completed_is_declined_without_acting(pytester):
    """A stale proposal: the run was idle when the watcher looked, not when the
    controller did. Nothing is re-driven and nothing is counted."""
    sched, node = _stranded_scheduler(pytester)
    redrive, dsession = _redrive_on(sched)
    ok, _ = _watcher_poll(redrive, idle=9.0)
    assert ok

    class _Teardown:
        when = "teardown"
        passed = True
        nodeid = "tests/test_alpha.py::test_one"

    redrive.pytest_runtest_logreport(_Teardown())  # type: ignore[arg-type]
    _dispatch(dsession)

    assert redrive.redrives == 0
    assert redrive.declined == 1
    assert "a test completed after the proposal" in redrive.declined_log[0]
    assert not node.sent, "a stale proposal re-drove the scheduler"


def test_a_stranded_node_is_counted_by_the_controller_and_fails_w13(
    pytester,
    tmp_path,
    monkeypatch,
):
    """A genuine stall still counts, and it is the controller that counts it.

    One pending test, work queued, nothing coming: the defect W13 exists for.
    The watcher's proposal alone changes no count; the re-drive is counted
    when the controller hands the node its queued work, and the run is then
    not clean W13 evidence.
    """
    sched, node = _stranded_scheduler(pytester)
    redrive, dsession = _redrive_on(sched)
    queued = len(sched.workqueue)

    ok, why = _watcher_poll(redrive, idle=9.0)
    assert ok, why
    assert redrive.redrives == 0 and not redrive.redrive_log, "the watcher decided the count"
    assert not node.sent

    _dispatch(dsession)

    assert node.sent, "the re-drive did not hand the stranded node its queued work"
    assert len(sched.workqueue) == queued - 1
    assert (redrive.redrives, redrive.declined) == (1, 0)
    note = redrive.redrive_log[0]
    assert note.startswith("scheduler re-drive #1: no test completed for 9s")
    assert "gw0 (pending 1 -> 2: 1 unit(s), 1 test(s))" in note

    report, ok, message = _report_of(redrive, tmp_path, monkeypatch)
    assert not ok
    assert "not clean evidence" in message
    assert report["redrives"] == 1


def test_a_collected_node_left_holding_nothing_is_still_counted(pytester):
    """The count turns on what the controller did, not on the node's shape.

    A node that has collected, holds nothing and is not given the queued work
    is the controller failing to assign -- the defect W13 exists for -- even
    though its workload looks exactly like a node mid-``schedule()``. Excluding
    empty workloads would have silenced Run 1/3's false positive and this
    real one with it.
    """
    from scripts.ci.xdist_contract import make_safe_scheduler

    sched = make_safe_scheduler(_make_config(pytester))
    collection = _small_collection()
    first = _FakeNode("gw0")
    sched.add_node(first)
    sched.add_node_collection(first, collection)
    sched.schedule()
    # A replacement that has collected, but whose reschedule never came.
    idle = _FakeNode("gw1")
    sched.add_node(idle)
    sched.add_node_collection(idle, collection)
    assert sched.assigned_work[idle] == {} and sched.workqueue

    redrive, dsession = _redrive_on(sched)
    ok, why = _watcher_poll(redrive, idle=8.0)
    assert ok, why
    _dispatch(dsession)

    assert idle.sent, "the idle node was not given the queued work"
    assert redrive.redrives == 1
    assert "gw1 (pending 0 -> 6" in redrive.redrive_log[0]


def test_a_hand_off_that_raises_is_counted_and_does_not_end_the_session(pytester):
    """The unit left the queue, so the controller did hand it out; a send that
    then fails is named in the note, and nothing escapes into ``loop_once``,
    where an exception would end the run."""
    sched, node = _stranded_scheduler(pytester)

    def refuse(indices: list[int]) -> None:
        raise OSError("channel closed")

    node.send_runtest_some = refuse  # type: ignore[method-assign]
    redrive, dsession = _redrive_on(sched)
    assert _watcher_poll(redrive, idle=9.0)[0]

    _dispatch(dsession)

    assert redrive.redrives == 1
    assert "OSError" in redrive.redrive_log[0]


def test_a_proposal_the_controller_never_handled_fails_w13_closed(
    pytester,
    tmp_path,
    monkeypatch,
):
    """Neither confirmed nor declined is unknown, and unknown is not clean."""
    sched, _node = _stranded_scheduler(pytester)
    redrive, _dsession = _redrive_on(sched)
    assert _watcher_poll(redrive, idle=9.0)[0]
    # The session ends before loop_once dispatches the proposal.

    report, ok, message = _report_of(redrive, tmp_path, monkeypatch)
    assert not ok
    assert "never handled" in message
    assert (report["redrives"], report["declined"], report["undispatched"]) == (0, 0, 1)

    written: list[str] = []

    class _Reporter:
        def write_sep(self, sep, title, **kwargs):
            written.append(title)

        def write_line(self, line, **kwargs):
            written.append(line)

    redrive.pytest_terminal_summary(_Reporter())
    assert "never handled by the controller" in "\n".join(written)


def test_a_declined_proposal_is_said_but_does_not_read_as_a_redrive(pytester):
    """Declined proposals are on the record and never wear the defect banner."""
    sched, node = _deadlocked_scheduler(pytester)
    # Give the node work, so the schedule is consistent and nothing is owed.
    sched.assigned_work[node]["tests/test_busy.py"] = {
        "tests/test_busy.py::test_a": False,
        "tests/test_busy.py::test_b": False,
        "tests/test_busy.py::test_c": False,
    }
    redrive, dsession = _redrive_on(sched)
    assert not redrive._should_redrive()[0], "a consistent busy schedule is not a stall"

    # A proposal from a snapshot the controller does not share.
    redrive._post_redrive(18.0, "252 unit(s) queued and a node able to take them", 0)
    _dispatch(dsession)
    assert (redrive.redrives, redrive.declined) == (0, 1)
    assert not node.sent

    written: list[str] = []

    class _Reporter:
        def write_sep(self, sep, title, **kwargs):
            written.append(title)

        def write_line(self, line, **kwargs):
            written.append(line)

    redrive.pytest_terminal_summary(_Reporter())
    blob = "\n".join(written)
    assert "1 re-drive proposal(s) declined" in blob
    assert "252 unit(s) queued" in blob, "what the watcher saw is kept as evidence"
    assert "gw0 pending 5" in blob, "and so is what the controller found"
    assert "re-driven" not in blob and "pytest-xdist defect" not in blob


_LOST_WAKEUP_CONFTEST = """
import sys
sys.path.insert(0, {root!r})

def pytest_xdist_make_scheduler(config, log):
    from scripts.ci.xdist_contract import make_safe_scheduler

    sched = make_safe_scheduler(config, log)
    reschedule = sched._reschedule
    lost = []

    def mark_test_complete(node, item_index, duration=0):
        nodeid = sched.registered_collections[node][item_index]
        sched.assigned_work[node][sched._split_scope(nodeid)][nodeid] = True
        # The defect, injected: the controller loses the wake-up that should
        # top this node up -- twice, so the worker is left holding its last
        # test with no next item to fetch, and stops.
        if len(lost) < 2 and sched.workqueue and sched._pending_of(sched.assigned_work[node]) <= 2:
            lost.append(nodeid)
            return
        reschedule(node)

    sched.mark_test_complete = mark_test_complete
    return sched

def pytest_configure(config):
    from scripts.ci import xdist_contract
    xdist_contract.install(config)
"""


def test_a_lost_wakeup_in_a_real_xdist_run_is_counted_and_fails_w13(
    pytester,
    tmp_path,
    monkeypatch,
):
    """End to end, with a genuinely stranded worker: the controller loses the
    wake-ups that would have topped it up, the watcher proposes, the
    controller confirms and counts, the run completes, and W13 refuses it.

    (The two older end-to-end tests above re-drive a node that holds two
    tests, one running and the next fetched: they prove the plumbing and
    that a counted re-drive fails W13, not that a stranded worker is found.)
    """
    from scripts.ci.xdist_contract import check_contract_report

    report = tmp_path / "xdist-contract.json"
    monkeypatch.setenv("BARTHO_XDIST_CONTRACT_REPORT", str(report))
    # Three seconds, not one: a node passes through two pending tests on its
    # way to being stranded, and on a slow runner a trivial test taking a
    # second there would add a (correctly counted) busy-node re-drive first.
    monkeypatch.setenv("BARTHO_XDIST_REDRIVE_IDLE_S", "3")
    pytester.makeconftest(
        _LOST_WAKEUP_CONFTEST.format(root=str(Path(__file__).resolve().parents[1])),
    )
    pytester.makepyfile(
        test_alpha="def test_1(): pass\ndef test_2(): pass\ndef test_3(): pass\n"
        "def test_4(): pass\n",
        test_beta="def test_b(): pass\n",
        test_gamma="def test_c(): pass\n",
        test_delta="def test_d(): pass\n",
    )

    result = pytester.runpytest_subprocess(
        "-n",
        "1",
        "--dist",
        "loadfile",
        "-p",
        "no:cacheprovider",
        "-p",
        "no:randomly",
    )

    result.assert_outcomes(passed=7)
    recorded = json.loads(report.read_text(encoding="utf-8"))
    assert recorded["redrives"] >= 1, recorded
    assert recorded["undispatched"] == 0, recorded
    assert any("gw0 (pending 1 -> 2" in note for note in recorded["notes"]), recorded
    combined = result.stdout.str() + result.stderr.str()
    assert "re-drive proposal #1" in combined
    ok, message = check_contract_report(report)
    assert not ok and "not clean evidence" in message


@pytest.mark.parametrize("declined", [3, 0, "3", True, -1, None, [1]])
def test_declined_proposals_never_change_the_w13_verdict(tmp_path, declined):
    """Evidence, never a re-drive: whatever ``declined`` holds, a clean run
    stays clean and a re-driven one stays refused."""
    from scripts.ci.xdist_contract import check_contract_report

    clean = _contract_report_file(tmp_path, "clean.json", declined=declined)
    assert check_contract_report(clean)[0] is True
    redriven = _contract_report_file(tmp_path, "redriven.json", redrives=1, declined=declined)
    assert check_contract_report(redriven)[0] is False


@pytest.mark.parametrize(
    ("undispatched", "clean"),
    [(1, False), (0, True), ("1", True), (True, True), (-1, True), (None, True)],
)
def test_only_a_real_count_of_unhandled_proposals_refuses_the_run(
    tmp_path,
    undispatched,
    clean,
):
    from scripts.ci.xdist_contract import check_contract_report

    report = _contract_report_file(tmp_path, "report.json", undispatched=undispatched)
    assert check_contract_report(report)[0] is clean


def test_timing_the_write_path_survives_a_positional_factory(tmp_path):
    """``factory`` is sqlite3.connect's sixth positional parameter.

    Injecting it as a keyword alongside a positional one raises "got
    multiple values for argument 'factory'". Nothing in this repository
    passes it positionally today, but the counter wraps every sqlite3 open
    in the process, dependencies included. An instrument that breaks the
    thing it measures is worse than no instrument.
    """
    from scripts.ci.execution_trace import _SqliteCounter

    class Mine(sqlite3.Connection):
        pass

    counter = _SqliteCounter()
    original = sqlite3.connect
    counter.install()
    try:
        # timeout, detect_types, isolation_level, check_same_thread, factory
        conn = sqlite3.connect(str(tmp_path / "positional.db"), 5.0, 0, None, True, Mine)
        assert isinstance(conn, Mine), "the caller's class survived"
        conn.close()
    finally:
        sqlite3.connect = original

    assert counter.snapshot()[0] == 1, "the open was still counted"


def test_the_escape_hatch_covers_every_correction(pytester, monkeypatch):
    """`BARTHO_XDIST_CONTRACT=0` is documented as the way back to stock.

    It is there so that a defect in this module cannot block a release. A
    hatch that disables two of the three corrections and leaves the third
    installed would not help if the third were the defective one.
    """
    from scripts.ci import xdist_contract

    config = _make_config(pytester)

    monkeypatch.delenv("BARTHO_XDIST_CONTRACT", raising=False)
    assert xdist_contract.make_safe_scheduler(config) is not None

    monkeypatch.setenv("BARTHO_XDIST_CONTRACT", "0")
    assert xdist_contract.make_safe_scheduler(config) is None, "stock scheduler is restored"

    xdist_contract.install(config)
    assert not config.pluginmanager.hasplugin(xdist_contract.CONTRACT_PLUGIN_NAME)
    assert not config.pluginmanager.hasplugin(xdist_contract.REDRIVE_PLUGIN_NAME)


def test_the_summary_names_what_failed_last_of_all(tmp_path, capsys):
    """A CI log is read from the end, and log APIs return the tail.

    On run 35193584212 this summary was long enough to push pytest's own
    "short test summary info" out of that window: the job was red and the
    name of the failing test could not be reached without downloading an
    artifact. A diagnostic that displaces the line a reader needs is a
    defect in the diagnostic, so the failures are printed last.
    """
    from scripts.ci.summarise_trace import summarise

    def event(**fields):
        return json.dumps(fields)

    (tmp_path / "gw0.jsonl").write_text(
        "\n".join(
            [
                event(event="phase", t=1.0, nodeid="t.py::ok", when="call", outcome="passed"),
                event(event="phase", t=2.0, nodeid="t.py::bad", when="call", outcome="failed"),
                event(event="logstart", t=3.0, nodeid="t.py::died_here"),
            ],
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "controller.jsonl").write_text(
        event(
            event="node_down",
            t=4.0,
            gateway="gw0",
            crashed=True,
            error="Not properly terminated",
        )
        + "\n",
        encoding="utf-8",
    )

    summarise(tmp_path)
    printed = capsys.readouterr().out

    assert "-- what failed" in printed
    assert "t.py::bad" in printed, "the failing test is named"
    assert "WORKER LOST  gw0" in printed
    assert "died on t.py::died_here" in printed, "a lost worker names the test it died on"
    assert "t.py::ok" not in printed.split("-- what failed")[1], "passing tests are not listed"

    tail = printed.strip().splitlines()
    assert "t.py::bad" in "\n".join(tail[-6:]), "and it is at the end, where the tail is read"


def test_failures_are_annotated_so_they_survive_the_log_tail(tmp_path, capsys, monkeypatch):
    """Printing the failure is not enough on a Windows runner.

    The post-job cleanup alone fills the log tail a log API returns -- its
    git command lines are enormous -- so anything a step prints can be
    unreachable no matter where in the job that step runs. Moving this
    summary last (f2470c8) helped and still did not make it reachable. A
    workflow annotation is attached to the check run rather than the log,
    so it survives, and it appears at the top of the job in the UI.
    """
    from scripts.ci.summarise_trace import summarise

    (tmp_path / "gw0.jsonl").write_text(
        json.dumps(
            {
                "event": "phase",
                "t": 2.0,
                "nodeid": "t.py::bad",
                "when": "call",
                "outcome": "failed",
            },
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "controller.jsonl").write_text("", encoding="utf-8")

    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    summarise(tmp_path)
    assert "::error" not in capsys.readouterr().out, "no annotations outside Actions"

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    summarise(tmp_path)
    printed = capsys.readouterr().out
    assert "::error title=Windows execution::t.py::bad reported gw0/call" in printed
    for line in printed.splitlines():
        if line.startswith("::error"):
            assert "%0A" not in line or "\n" not in line


# ---------------------------------------------------------------------------
# Clause W15: a per-test timeout leaves evidence that survives the kill.
#
# pytest-timeout ends a worker with os._exit(1) and prints its stack dump to
# the worker's terminal, which xdist discards; the trace's own stall dumps are
# armed per phase at BARTHO_EXEC_STALL_WARN_S (180 s in CI) and so can never
# fire before a 120 s kill. Merge Candidate 35971892908 lost gw0 and gw2 that
# way with nothing to show for either. The thread method is forced here
# because it is the one Windows uses.
#
# The evidence fires min(10 s, a tenth of the timeout) before the kill. A
# killed test here uses a 20 s timeout, so that lead is 2 s: a 4-5 s timeout
# left it 0.4-0.5 s, and on a loaded Windows runner (Merge Candidate
# 36149262520) the kill landed between the `timeout_imminent` event and the
# stack file. Production's lead is 10 s. The inner run allows no worker
# restart, so the killed test runs once, not once per replacement worker.
# ---------------------------------------------------------------------------


def _run_with_timeout(pytester, trace_dir, monkeypatch, timeout_s: str, *args: str):
    monkeypatch.setenv("BARTHO_EXEC_TRACE", "1")
    monkeypatch.setenv("BARTHO_EXEC_TRACE_DIR", str(trace_dir))
    monkeypatch.setenv("BARTHO_EXEC_STALL_ABORT_S", "0")
    return _run_traced(
        pytester,
        trace_dir,
        "-n",
        "1",
        "--dist",
        "loadfile",
        "--timeout",
        timeout_s,
        "--max-worker-restart",
        "0",
        "-o",
        "timeout_method=thread",
        *args,
    )


def _stacks(trace_dir) -> str:
    """The pre-kill stack file -- or, if there is none, why not.

    Its absence has two explanations the trace can tell apart: the kill landed
    between the `timeout_imminent` event and the file (no later event), or the
    file could not be written (`timeout_dump_failed`)."""
    path = trace_dir / "gw0.timeout.txt"
    if not path.exists():
        worker = _trace_events(trace_dir / "gw0.jsonl")
        tail = [
            {k: e.get(k) for k in ("t", "event", "phase", "elapsed_s", "error")}
            for e in worker
            if e["event"] in ("timeout_imminent", "timeout_dump_failed")
        ]
        down = [
            e.get("t")
            for e in _trace_events(trace_dir / "controller.jsonl")
            if e["event"] == "node_down"
        ]
        pytest.fail(f"no {path.name}; evidence events {tail}; controller node_down at {down}")
    return path.read_text(encoding="utf-8")


def _summary(trace_dir, capsys) -> str:
    from scripts.ci.summarise_trace import summarise

    capsys.readouterr()
    summarise(trace_dir)
    return capsys.readouterr().out


def test_a_timeout_killed_worker_leaves_its_stacks_phase_and_elapsed_time(
    pytester,
    tmp_path,
    monkeypatch,
    capsys,
):
    trace_dir = tmp_path / "trace"
    pytester.makepyfile(
        test_setup_hangs="""
        import time
        import pytest

        @pytest.fixture
        def a_setup_that_never_finishes():
            time.sleep(60)

        def test_waits_in_setup(a_setup_that_never_finishes):
            pass
        """,
    )

    result = _run_with_timeout(pytester, trace_dir, monkeypatch, "20")

    # The timeout still does exactly what it did: the worker is lost, the
    # test fails, the run fails. Nothing here prevents, delays or retries it.
    assert result.ret != 0
    controller = _trace_events(trace_dir / "controller.jsonl")
    assert any(e["event"] == "node_down" and e["crashed"] for e in controller)

    worker = _trace_events(trace_dir / "gw0.jsonl")
    (imminent,) = [e for e in worker if e["event"] == "timeout_imminent"]
    assert imminent["nodeid"].endswith("test_waits_in_setup")
    assert imminent["phase"] == "setup"
    assert imminent["timeout_s"] == 20.0
    assert 17.0 <= imminent["elapsed_s"] < 20.0
    assert "MainThread" in imminent["threads"]

    stacks = _stacks(trace_dir)
    assert "per-test timeout imminent" in stacks
    assert "a_setup_that_never_finishes" in stacks, "the stack does not show where it waited"

    out = _summary(trace_dir, capsys)
    assert "per-test timeout (20s) imminent in setup" in out
    assert "is not excluded" in out
    assert "a_setup_that_never_finishes" in out, "the pre-kill stacks were not put in the log"


def test_the_evidence_spans_setup_and_call_as_the_timeout_does(pytester, tmp_path, monkeypatch):
    """gw0's shape: a slow setup, then a call that takes the rest of the budget.
    Neither phase alone reaches the timeout; together they do."""
    trace_dir = tmp_path / "trace"
    pytester.makepyfile(
        test_split="""
        import time
        import pytest

        @pytest.fixture
        def a_slow_setup():
            time.sleep(10)

        def test_then_a_call_that_hangs(a_slow_setup):
            time.sleep(60)
        """,
    )

    _run_with_timeout(pytester, trace_dir, monkeypatch, "20")

    worker = _trace_events(trace_dir / "gw0.jsonl")
    (imminent,) = [e for e in worker if e["event"] == "timeout_imminent"]
    assert imminent["phase"] == "call"
    # Had the clock restarted at the call, it would have fired 28 s in -- after
    # the kill -- and there would be no event at all.
    assert imminent["elapsed_s"] >= 17.0, "the evidence clock restarted at the phase boundary"
    assert imminent["phase_elapsed_s"] < imminent["elapsed_s"]
    stacks = _stacks(trace_dir)
    assert "test_then_a_call_that_hangs" in stacks


def test_a_test_that_finishes_leaves_no_timeout_evidence(pytester, tmp_path, monkeypatch):
    trace_dir = tmp_path / "trace"
    pytester.makepyfile(
        test_fine="""
        import time
        def test_quick(): pass
        def test_slow_but_inside_its_budget(): time.sleep(1.5)
        def test_quick_again(): pass
        """,
    )

    result = _run_with_timeout(pytester, trace_dir, monkeypatch, "4")

    assert result.ret == 0
    worker = _trace_events(trace_dir / "gw0.jsonl")
    assert not [e for e in worker if e["event"] == "timeout_imminent"]
    assert not (trace_dir / "gw0.timeout.txt").exists()
    # One evidence thread for the whole worker, not one per test.
    live = [e["live_threads"] for e in worker if e["event"] == "phase"]
    assert max(live) == min(live)


def test_a_worker_lost_without_a_timeout_is_not_called_a_timeout(
    pytester,
    tmp_path,
    monkeypatch,
    capsys,
):
    trace_dir = tmp_path / "trace"
    pytester.makepyfile(
        test_dies="""
        import os
        def test_dies(): os._exit(1)
        """,
    )

    _run_with_timeout(pytester, trace_dir, monkeypatch, "30")

    out = _summary(trace_dir, capsys)
    assert "WORKER LOST" in out
    assert "no per-test-timeout evidence for this test" in out
    assert "per-test timeout (" not in out


def test_evidence_of_an_imminent_timeout_is_not_reported_as_an_expired_one(
    pytester,
    tmp_path,
    monkeypatch,
    capsys,
):
    """The evidence proves the deadline was near, not that it was reached. A
    worker that dies for another reason after the evidence and before the
    deadline must not be reported as killed by the timeout."""
    trace_dir = tmp_path / "trace"
    pytester.makepyfile(
        test_dies_late="""
        import os
        import pathlib
        import time

        def test_dies_after_the_evidence_but_before_the_deadline():
            dump = pathlib.Path(os.environ["BARTHO_EXEC_TRACE_DIR"]) / "gw0.timeout.txt"
            while not (dump.exists() and dump.stat().st_size):
                time.sleep(0.02)
            os._exit(1)
        """,
    )

    # Evidence at 18 s, deadline at 20 s: the worker dies up to 2 s early.
    _run_with_timeout(pytester, trace_dir, monkeypatch, "20")

    worker = _trace_events(trace_dir / "gw0.jsonl")
    (imminent,) = [e for e in worker if e["event"] == "timeout_imminent"]
    assert imminent["elapsed_s"] < imminent["timeout_s"]
    out = _summary(trace_dir, capsys)
    assert "WORKER LOST" in out
    assert "per-test timeout (20s) imminent in call" in out
    assert "a different death in the last" in out
    assert "timeout (20s) expired" not in out, "an imminent timeout was reported as one that fired"


# ---------------------------------------------------------------------------
# Clause W16: a `database is locked` failure can be set against the slow
# SQLite operations in flight on the same worker -- as correlation only.
# ---------------------------------------------------------------------------


def test_a_locked_failure_is_listed_with_overlapping_slow_sqlite_operations(
    pytester,
    tmp_path,
    monkeypatch,
    capsys,
):
    trace_dir = tmp_path / "trace"
    # Every commit and close counts as "slow", so the overlap is deterministic.
    monkeypatch.setenv("BARTHO_EXEC_SLOW_SQLITE_S", "0")
    pytester.makepyfile(
        test_locked="""
        import sqlite3

        def test_refused(tmp_path):
            conn = sqlite3.connect(str(tmp_path / "held.db"))
            conn.execute("CREATE TABLE t(x)")
            conn.commit()
            conn.close()
            raise sqlite3.OperationalError("database is locked")
        """,
    )

    _run_with_timeout(pytester, trace_dir, monkeypatch, "30")

    worker = _trace_events(trace_dir / "gw0.jsonl")
    (failure,) = [e for e in worker if e["event"] == "sqlite_locked_failure"]
    assert failure["nodeid"].endswith("test_refused") and failure["when"] == "call"
    slow = [e for e in worker if e["event"] == "sqlite_slow"]
    assert {e["op"] for e in slow} >= {"commit", "close"}
    assert all(e["database"].endswith("held.db") for e in slow)

    out = _summary(trace_dir, capsys)
    assert "test_refused" in out
    assert "held.db" in out
    assert "correlation, not proof" in out


def test_ordinary_operations_are_not_reported_as_slow(pytester, tmp_path, monkeypatch):
    trace_dir = tmp_path / "trace"
    pytester.makepyfile(
        test_quick_db="""
        import sqlite3

        def test_quick(tmp_path):
            conn = sqlite3.connect(str(tmp_path / "q.db"))
            conn.execute("CREATE TABLE t(x)")
            conn.commit()
            conn.close()
        """,
    )

    result = _run_with_timeout(pytester, trace_dir, monkeypatch, "30")

    assert result.ret == 0
    worker = _trace_events(trace_dir / "gw0.jsonl")
    assert not [e for e in worker if e["event"] in ("sqlite_slow", "sqlite_locked_failure")]


# ---------------------------------------------------------------------------
# Clause W13, enforced: a run that needed a re-drive is not clean evidence.
#
# The contract always said so ("completed over a defect and does not read as
# green"); the implementation only printed a yellow banner, the job concluded
# `success`, and Merge Qualification -- which reads job conclusions -- could
# count it. Merge Candidate 35971892908 attempt 1 was re-driven three times.
# ---------------------------------------------------------------------------

_W13_CONFTEST = """
import sys
sys.path.insert(0, {root!r})

def pytest_xdist_make_scheduler(config, log):
    from scripts.ci.xdist_contract import make_safe_scheduler
    return make_safe_scheduler(config, log)

def pytest_configure(config):
    from scripts.ci import xdist_contract
    xdist_contract.install(config)
"""


def _run_with_contract_report(pytester, report, *files: tuple[str, str]):
    pytester.makeconftest(_W13_CONFTEST.format(root=str(Path(__file__).resolve().parents[1])))
    pytester.makepyfile(**dict(files))
    return pytester.runpytest_subprocess(
        "-n",
        "1",
        "--dist",
        "loadfile",
        "-p",
        "no:cacheprovider",
        "-p",
        "no:randomly",
    )


def test_a_clean_run_satisfies_the_w13_check(pytester, tmp_path, monkeypatch):
    from scripts.ci.xdist_contract import check_contract_report

    report = tmp_path / "xdist-contract.json"
    monkeypatch.setenv("BARTHO_XDIST_CONTRACT_REPORT", str(report))
    result = _run_with_contract_report(
        pytester,
        report,
        ("test_one", "def test_a(): pass\n"),
        ("test_two", "def test_b(): pass\n"),
    )

    assert result.ret == 0
    assert json.loads(report.read_text(encoding="utf-8"))["redrives"] == 0
    ok, message = check_contract_report(report)
    assert ok, message


def test_a_redriven_run_still_recovers_but_cannot_satisfy_the_w13_check(
    pytester,
    tmp_path,
    monkeypatch,
):
    """The re-drive is not disabled to make this pass: it fires, it recovers
    the run, every test passes -- and the run is still not clean evidence."""
    from scripts.ci.xdist_contract import check_contract_report, main

    report = tmp_path / "xdist-contract.json"
    monkeypatch.setenv("BARTHO_XDIST_CONTRACT_REPORT", str(report))
    monkeypatch.setenv("BARTHO_XDIST_REDRIVE_IDLE_S", "1")
    result = _run_with_contract_report(
        pytester,
        report,
        ("test_slow_one", "import time\ndef test_slow(): time.sleep(9)\n"),
        ("test_two", "def test_a(): pass\n"),
        ("test_three", "def test_b(): pass\n"),
        ("test_four", "def test_c(): pass\n"),
        ("test_five", "def test_d(): pass\n"),
    )

    # The mechanism is still there and still works...
    assert result.ret == 0
    result.assert_outcomes(passed=5)
    assert "scheduler re-drive #1" in result.stdout.str() + result.stderr.str()
    # ...and the run it rescued does not qualify as clean.
    recorded = json.loads(report.read_text(encoding="utf-8"))
    assert recorded["redrives"] >= 1
    ok, message = check_contract_report(report)
    assert not ok
    assert "W13" in message and "not clean evidence" in message
    assert main(["xdist_contract", "check", str(report)]) == 1


def test_the_w13_check_fails_closed(tmp_path, pytester, monkeypatch):
    from scripts.ci.xdist_contract import check_contract_report

    ok, message = check_contract_report(tmp_path / "never-written.json")
    assert not ok and "cannot count as clean" in message

    garbled = tmp_path / "garbled.json"
    garbled.write_text("{not json", encoding="utf-8")
    assert check_contract_report(garbled)[0] is False

    # Switching the contract off does not produce clean evidence either.
    report = tmp_path / "disabled.json"
    monkeypatch.setenv("BARTHO_XDIST_CONTRACT_REPORT", str(report))
    monkeypatch.setenv("BARTHO_XDIST_CONTRACT", "0")
    result = _run_with_contract_report(pytester, report, ("test_one", "def test_a(): pass\n"))
    assert result.ret == 0
    assert check_contract_report(report)[0] is False


# ---------------------------------------------------------------------------
# The W13 check reads a report. It must be THIS contract's report.
#
# Codex finding review_comment:4180458481 on 03c5f2d: the check read only
# `redrives`, coerced it with int(), and never looked at `schema` or `clause`,
# so any JSON object carrying `"redrives": 0` -- a report from another schema,
# an incomplete one, or a boolean -- would have counted as clean evidence. The
# check now fails closed unless the report identifies itself as the current
# contract's and carries a real integer count. The writer is unchanged.
# ---------------------------------------------------------------------------

_ABSENT = object()


def _contract_report_file(tmp_path: Path, name: str, **overrides) -> Path:
    """A report shaped exactly like the writer's, with one field altered."""
    from scripts.ci.xdist_contract import CONTRACT_REPORT_SCHEMA

    report = {
        "schema": CONTRACT_REPORT_SCHEMA,
        "clause": "W13",
        "redrives": 0,
        "gave_up": False,
        "exitstatus": 0,
        "notes": [],
    }
    for key, value in overrides.items():
        if value is _ABSENT:
            report.pop(key, None)
        else:
            report[key] = value
    path = tmp_path / name
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("schema", 2, id="wrong-schema"),
        pytest.param("schema", _ABSENT, id="missing-schema"),
        pytest.param("schema", True, id="schema-is-the-boolean-that-equals-1"),
        pytest.param("schema", 1.0, id="schema-is-a-float"),
        pytest.param("schema", "1", id="schema-is-a-string"),
        pytest.param("clause", "W14", id="wrong-clause"),
        pytest.param("clause", _ABSENT, id="missing-clause"),
        pytest.param("redrives", _ABSENT, id="missing-redrives"),
        pytest.param("redrives", "0", id="redrives-is-a-string"),
        pytest.param("redrives", 0.0, id="redrives-is-a-float"),
        pytest.param("redrives", False, id="redrives-is-the-boolean-that-equals-0"),
        pytest.param("redrives", True, id="redrives-is-the-boolean-that-equals-1"),
        pytest.param("redrives", None, id="redrives-is-null"),
        pytest.param("redrives", -1, id="redrives-is-negative"),
    ],
)
def test_a_report_that_is_not_this_contracts_is_refused(tmp_path, field, value):
    from scripts.ci.xdist_contract import check_contract_report, main

    path = _contract_report_file(tmp_path, "report.json", **{field: value})
    ok, message = check_contract_report(path)
    assert not ok
    assert field in message, message
    assert "W13" in message and "cannot count as clean" in message
    assert main(["xdist_contract", "check", str(path)]) == 1


def test_a_bare_redrives_object_is_refused(tmp_path):
    """The exact shape the finding described: nothing but a zero count."""
    from scripts.ci.xdist_contract import check_contract_report

    bare = tmp_path / "bare.json"
    bare.write_text('{"redrives": 0}', encoding="utf-8")
    ok, message = check_contract_report(bare)
    assert not ok and "schema" in message


@pytest.mark.parametrize("text", ["[0]", "0", "null", '"W13"', "true"])
def test_a_report_whose_top_level_is_not_an_object_is_refused(tmp_path, text):
    from scripts.ci.xdist_contract import check_contract_report

    path = tmp_path / "shape.json"
    path.write_text(text, encoding="utf-8")
    ok, message = check_contract_report(path)
    assert not ok and "not an object" in message


def test_the_writers_own_report_is_accepted_and_its_count_is_honoured(tmp_path):
    """The repair tightens the reader only: what the writer produces passes,
    and a writer-produced re-drive count still fails, with the same words."""
    from scripts.ci.xdist_contract import check_contract_report, write_contract_report

    clean = tmp_path / "clean.json"
    write_contract_report(clean, redrives=0, gave_up=False, notes=[], exitstatus=0)
    ok, message = check_contract_report(clean)
    assert ok, message
    assert "no scheduler re-drive was needed (clause W13)" == message

    redriven = tmp_path / "redriven.json"
    write_contract_report(
        redriven,
        redrives=2,
        gave_up=False,
        notes=["scheduler re-drive #1: no test completed for 33s"],
        exitstatus=0,
    )
    ok, message = check_contract_report(redriven)
    assert not ok
    assert "2 scheduler re-drive(s) (clause W13)" in message
    assert "not clean evidence" in message
    assert "scheduler re-drive #1" in message


def _required_xdist_jobs() -> list[tuple[str, str, dict]]:
    """Every (workflow, job) Merge Qualification requires that runs the suite
    under xdist, with the job's definition. Matrix names are expanded the way
    the forge renders them."""
    import itertools

    import yaml

    root = Path(__file__).resolve().parents[1]
    config = yaml.safe_load(
        (root / ".github" / "merge-qualification" / "config.yml").read_text(encoding="utf-8"),
    )
    required = {(t["workflow"], job) for t in config["required_tiers"] for job in t["jobs"]}

    found = []
    for workflow_file in sorted((root / ".github" / "workflows").glob("*.yml")):
        workflow = yaml.safe_load(workflow_file.read_text(encoding="utf-8"))
        for job in (workflow.get("jobs") or {}).values():
            matrix = (job.get("strategy") or {}).get("matrix") or {}
            axes = {k: v for k, v in matrix.items() if isinstance(v, list)}
            combos = [
                dict(zip(axes, values, strict=True)) for values in itertools.product(*axes.values())
            ]
            for combo in combos or [{}]:
                name = job.get("name", "")
                for key, value in combo.items():
                    name = name.replace(f"${{{{ matrix.{key} }}}}", str(value))
                runs_xdist = any(
                    "pytest" in str(step.get("run", "")) and " -n " in f" {step.get('run', '')} "
                    for step in job.get("steps", [])
                )
                if (workflow["name"], name) in required and runs_xdist:
                    found.append((workflow["name"], name, job))
    return found


def test_every_required_xdist_job_enforces_w13():
    jobs = _required_xdist_jobs()
    # Not vacuous: PR Fast, Integration coverage, both Merge Candidate
    # coverage legs and the Windows full suite.
    assert len(jobs) >= 5, [f"{w} / {j}" for w, j, _ in jobs]
    for workflow, name, job in jobs:
        steps = job["steps"]
        xdist_at = [
            i
            for i, step in enumerate(steps)
            if "pytest" in str(step.get("run", "")) and " -n " in f" {step.get('run', '')} "
        ]
        check_at = [
            i
            for i, step in enumerate(steps)
            if "scripts.ci.xdist_contract check xdist-contract.json" in str(step.get("run", ""))
        ]
        where = f"{workflow} / {name}"
        for i in xdist_at:
            env = steps[i].get("env") or {}
            assert (
                env.get("BARTHO_XDIST_CONTRACT_REPORT") == "xdist-contract.json"
            ), f"{where}: the xdist step does not ask for a W13 report"
        assert check_at and min(check_at) > max(
            xdist_at,
        ), f"{where}: no W13 clean-run check after its xdist test step"
        assert (
            "if" not in steps[min(check_at)]
        ), f"{where}: the W13 check must run on the default success() condition only"
