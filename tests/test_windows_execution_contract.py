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
    (tmp_path / "controller.jsonl").write_text(
        "\n".join(
            [
                line(event="report_received", t=10.1, nodeid="t.py::a", when="setup", worker="gw0"),
                line(event="report_received", t=600.1, nodeid="t.py::a", when="setup", worker="gw1"),
            ],
        )
        + "\n",
        encoding="utf-8",
    )

    summarise(tmp_path)
    printed = capsys.readouterr().out

    assert "largest worker-to-controller delay" in printed
    delay_line = next(ln for ln in printed.splitlines() if "largest worker-to-controller" in ln)
    assert "0.1s" in delay_line, f"each report matched its own worker: {delay_line}"
    assert "590" not in delay_line, "no delay was invented across the two workers"
